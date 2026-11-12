# -*- coding: utf-8 -*-
"""受限 DSL 与安全执行器（P1 会补齐内置动作集，P0 先交付可运行的安全底座）。

硬约束：LLM 只能输出参数化 JSON（tool + params），不得自由生成 DCC 脚本。
执行前必经：白名单校验 -> 权限校验 -> dry-run diff 预览 -> 人工确认
            -> adapter.begin_undo -> 逐条执行 -> 任一失败整体回滚。
"""
from __future__ import annotations

import copy
import json
import math
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
        return {"tool": self.tool, "params": copy.deepcopy(self.params)}


@dataclass
class ExecutionPlan:
    actions: List[Action] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"actions": [a.to_dict() for a in self.actions]}

    def to_json(self, indent: int = 2) -> str:
        """序列化为严格 JSON；NaN、Infinity 等值不作为合法计划输出。"""
        try:
            return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent,
                              allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise DSLError("plan 无法序列化为 JSON: %s" % exc)

    @classmethod
    def from_dict(cls, payload: Any) -> "ExecutionPlan":
        if not isinstance(payload, dict) or not isinstance(payload.get("actions"), list):
            raise DSLError("payload 必须是 {'actions': [...]}")
        actions: List[Action] = []
        for index, raw in enumerate(payload["actions"]):
            if not isinstance(raw, dict) or "tool" not in raw:
                raise DSLError("actions[%d] 缺少 tool" % index)
            params = raw.get("params", {})
            if not isinstance(params, dict):
                raise DSLError("actions[%d].params 必须是对象" % index)
            actions.append(Action(raw["tool"], copy.deepcopy(params)))
        return cls(actions)

    @classmethod
    def from_json(cls, payload: str) -> "ExecutionPlan":
        try:
            data = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise DSLError("payload 不是合法 JSON: %s" % exc)
        return cls.from_dict(data)


@dataclass
class ActionSpec:
    name: str
    fn: Callable[..., Any]
    permissions: List[str] = field(default_factory=list)
    description: str = ""
    parameters: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class ActionRegistry:
    """函数白名单。未注册的动作一律拒绝执行。"""

    def __init__(self):
        self._specs: Dict[str, ActionSpec] = {}

    def register(self, name: str, fn: Callable[..., Any],
                 permissions: Optional[List[str]] = None, description: str = "",
                 parameters: Optional[Dict[str, Any]] = None):
        """注册动作及其参数声明。

        参数声明示例：``{"value": {"type": float, "required": True,
        "min": 0.0, "max": 1.0}}``。声明非空时，未声明的参数也会被拒绝。
        """
        if name in self._specs:
            raise DSLError("动作 %s 已注册" % name)
        self._specs[name] = ActionSpec(
            name, fn, list(permissions or []), description, copy.deepcopy(parameters or {}))

    @property
    def names(self) -> List[str]:
        return sorted(self._specs)

    def build_plan(self, payload: Any) -> ExecutionPlan:
        """把 LLM 输出解析成执行计划；任何越界都在此处被拒绝。"""
        if isinstance(payload, str):
            plan = ExecutionPlan.from_json(payload)
        else:
            plan = ExecutionPlan.from_dict(payload)
        self._validate_plan(plan)
        return plan

    def _validate_plan(self, plan: ExecutionPlan) -> None:
        if not isinstance(plan, ExecutionPlan) or not isinstance(plan.actions, list):
            raise DSLError("plan 必须是 ExecutionPlan")
        for index, action in enumerate(plan.actions):
            if not isinstance(action, Action):
                raise DSLError("actions[%d] 必须是 Action" % index)
            spec = self._specs.get(action.tool)
            if spec is None:
                raise DSLError("actions[%d]: 未注册的动作 %r" % (index, action.tool))
            if not isinstance(action.params, dict):
                raise DSLError("actions[%d].params 必须是对象" % index)
            try:
                encoded = json.dumps(action.params, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise DSLError("actions[%d].params 不是严格 JSON: %s" % (index, exc))
            if json.loads(encoded) != action.params:
                raise DSLError("actions[%d].params 必须使用 JSON 原生类型与字符串键" % index)
            declarations = spec.parameters
            if declarations:
                unknown = sorted(set(action.params) - set(declarations))
                if unknown:
                    raise DSLError("actions[%d] 含未声明参数: %s" % (index, unknown))
            for name, raw_rule in declarations.items():
                rule = raw_rule if isinstance(raw_rule, dict) else {"type": raw_rule}
                if rule.get("required", False) and name not in action.params:
                    raise DSLError("actions[%d].params.%s 为必填参数" % (index, name))
                if name in action.params:
                    _validate_parameter(index, name, action.params[name], rule)

    def preview(self, plan: ExecutionPlan) -> List[Dict[str, Any]]:
        """dry-run diff：列出将要发生的动作，不产生任何副作用。"""
        self._validate_plan(plan)
        return [
            {"index": i, "tool": a.tool, "params": copy.deepcopy(a.params),
             "permissions": list(self._specs[a.tool].permissions), "status": "pending"}
            for i, a in enumerate(plan.actions)
        ]

    def execute(self, plan: ExecutionPlan, adapter: DCCAdapter,
                granted: Optional[List[str]] = None, dry_run: bool = True,
                confirm: bool = False) -> Dict[str, Any]:
        # 在执行任何动作前校验整个批次，避免部分计划已执行才发现后续参数无效。
        self._validate_plan(plan)
        required = sorted({p for a in plan.actions
                           for p in self._specs[a.tool].permissions})
        changes = self.preview(plan)
        if dry_run:
            # 不调用动作或 adapter，连只读宿主查询也不会发生。
            return self._result(True, False, changes, required)
        granted = set(granted or [])
        missing = [p for p in required if p not in granted]
        if missing:
            raise DSLError("缺少权限: %s" % missing)
        if not confirm:
            raise DSLError("非 dry-run 执行必须显式 confirm=True（人工确认）")

        try:
            token = adapter.begin_undo("TAK DSL: %d actions" % len(plan.actions))
        except Exception as exc:
            return self._result(
                False, False, changes, required,
                error="开启 undo 失败: %s: %s" % (type(exc).__name__, exc))

        try:
            for index, action in enumerate(plan.actions):
                self._specs[action.tool].fn(adapter, **action.params)
                changes[index]["status"] = "done"
            adapter.commit_undo(token)
        except Exception as exc:
            original = "%s: %s" % (type(exc).__name__, exc)
            try:
                adapter.rollback(token)
            except Exception as rollback_exc:
                rollback_error = "%s: %s" % (type(rollback_exc).__name__, rollback_exc)
                changes = [dict(c, status="rollback_failed") for c in changes]
                return self._result(
                    False, False, changes, required,
                    error="执行失败: %s；回滚失败: %s" % (original, rollback_error),
                    rollback_error=rollback_error, rollback_failed=True)
            changes = [dict(c, status="rolled_back") for c in changes]
            return self._result(False, False, changes, required,
                                error=original, rollback_failed=False)
        return self._result(False, True, changes, required)

    @staticmethod
    def _result(dry_run: bool, committed: bool, changes: List[Dict[str, Any]],
                required: List[str], **extra: Any) -> Dict[str, Any]:
        result = {"dry_run": dry_run, "committed": committed,
                  "required_permissions": required, "changes": changes,
                  "log": copy.deepcopy(changes)}
        result.update(extra)
        return result


def _validate_parameter(index: int, name: str, value: Any,
                        rule: Dict[str, Any]) -> None:
    label = "actions[%d].params.%s" % (index, name)
    expected = rule.get("type")
    if expected is not None and not _matches_type(value, expected):
        raise DSLError("%s 类型应为 %s，实际为 %s" % (
            label, expected, type(value).__name__))
    is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(value, float) and not math.isfinite(value):
        raise DSLError("%s 必须是有限数值" % label)
    for keys, comparison, description in (
            (("min", "minimum"), lambda a, b: a < b, "小于下限"),
            (("max", "maximum"), lambda a, b: a > b, "超过上限")):
        bound = next((rule[key] for key in keys if key in rule), None)
        if bound is None:
            continue
        if not is_number:
            raise DSLError("%s 声明了范围，必须提供数值" % label)
        try:
            outside = comparison(value, bound)
        except (TypeError, ValueError) as exc:
            raise DSLError("%s 的范围声明无效: %s" % (label, exc))
        if outside:
            raise DSLError("%s %s %r" % (label, description, bound))


def _matches_type(value: Any, expected: Any) -> bool:
    if isinstance(expected, (tuple, list)):
        return any(_matches_type(value, item) for item in expected)
    type_names = {
        "str": str, "string": str, "int": int, "integer": int,
        "bool": bool, "boolean": bool, "dict": dict, "object": dict,
        "list": list, "array": list, "null": type(None),
    }
    if isinstance(expected, str):
        if expected in ("float", "number"):
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected not in type_names:
            raise DSLError("未知参数类型声明 %r" % expected)
        expected = type_names[expected]
    if not isinstance(expected, type):
        raise DSLError("非法参数类型声明 %r" % (expected,))
    if expected in (int, float) and isinstance(value, bool):
        return False
    return isinstance(value, expected)


ACTION_REGISTRY = ActionRegistry()
