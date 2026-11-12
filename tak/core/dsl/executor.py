# -*- coding: utf-8 -*-
"""受限 DSL 与安全执行器（P1 会补齐内置动作集，P0 先交付可运行的安全底座）。

硬约束：LLM 只能输出参数化 JSON（tool + params），不得自由生成 DCC 脚本。
执行前必经：白名单校验 -> 权限校验 -> dry-run diff 预览 -> 人工确认
            -> adapter.begin_undo -> 逐条执行 -> 任一失败整体回滚。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from tak.adapters.base import DCCAdapter


class DSLError(ValueError):
    """payload 不合法或动作不被允许。"""


@dataclass
class Action:
    tool: str
    params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"tool": self.tool, "params": dict(self.params)}


@dataclass
class ExecutionPlan:
    actions: List[Action] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"actions": [a.to_dict() for a in self.actions]}


@dataclass
class ActionSpec:
    name: str
    fn: Callable[..., Any]
    permissions: List[str] = field(default_factory=list)
    description: str = ""


class ActionRegistry:
    """函数白名单。未注册的动作一律拒绝执行。"""

    def __init__(self):
        self._specs: Dict[str, ActionSpec] = {}

    def register(self, name: str, fn: Callable[..., Any],
                 permissions: Optional[List[str]] = None, description: str = ""):
        if name in self._specs:
            raise DSLError("动作 %s 已注册" % name)
        self._specs[name] = ActionSpec(name, fn, list(permissions or []), description)

    @property
    def names(self) -> List[str]:
        return sorted(self._specs)

    def build_plan(self, payload: Any) -> ExecutionPlan:
        """把 LLM 输出解析成执行计划；任何越界都在此处被拒绝。"""
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError as exc:
                raise DSLError("payload 不是合法 JSON: %s" % exc)
        if not isinstance(payload, dict) or not isinstance(payload.get("actions"), list):
            raise DSLError("payload 必须是 {'actions': [...]}")
        actions: List[Action] = []
        for index, raw in enumerate(payload["actions"]):
            if not isinstance(raw, dict) or "tool" not in raw:
                raise DSLError("actions[%d] 缺少 tool" % index)
            tool = raw["tool"]
            if tool not in self._specs:
                raise DSLError("actions[%d]: 未注册的动作 %r" % (index, tool))
            params = raw.get("params", {})
            if not isinstance(params, dict):
                raise DSLError("actions[%d].params 必须是对象" % index)
            actions.append(Action(tool, dict(params)))
        return ExecutionPlan(actions)

    def preview(self, plan: ExecutionPlan) -> List[Dict[str, Any]]:
        """dry-run diff：列出将要发生的动作，不产生任何副作用。"""
        return [
            {"index": i, "tool": a.tool, "params": dict(a.params),
             "permissions": list(self._specs[a.tool].permissions), "status": "pending"}
            for i, a in enumerate(plan.actions)
        ]

    def execute(self, plan: ExecutionPlan, adapter: DCCAdapter,
                granted: Optional[List[str]] = None, dry_run: bool = True,
                confirm: bool = False) -> Dict[str, Any]:
        required = sorted({p for a in plan.actions
                           for p in self._specs[a.tool].permissions})
        if dry_run:
            # 预览不产生副作用，因此不要求已授权；把所需权限返回给 UI 提前展示。
            return {"dry_run": True, "committed": False,
                    "required_permissions": required, "changes": self.preview(plan)}
        granted = set(granted or [])
        missing = [p for p in required if p not in granted]
        if missing:
            raise DSLError("缺少权限: %s" % missing)
        if not confirm:
            raise DSLError("非 dry-run 执行必须显式 confirm=True（人工确认）")

        token = adapter.begin_undo("TAK DSL: %d actions" % len(plan.actions))
        changes = self.preview(plan)
        try:
            for index, action in enumerate(plan.actions):
                self._specs[action.tool].fn(adapter, **action.params)
                changes[index]["status"] = "done"
        except Exception as exc:
            adapter.rollback(token)
            changes = [dict(c, status="rolled_back") for c in changes]
            return {"dry_run": False, "committed": False,
                    "error": "%s: %s" % (type(exc).__name__, exc), "changes": changes}
        adapter.commit_undo(token)
        return {"dry_run": False, "committed": True, "changes": changes}


ACTION_REGISTRY = ActionRegistry()
