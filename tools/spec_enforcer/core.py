# -*- coding: utf-8 -*-
"""Spec Enforcer 的编排层（无 DCC / 无 UI 依赖）。

职责边界
--------
1. ``run_report(ctx)``：跑本插件自己的确定性规则，产出 ``Report``，并在
   ``metadata`` 里附上**参数化 JSON 修复计划**（``fix_plan`` / ``plan_count``）；
2. ``plan_fixes(ctx)``：只把 findings 里的 ``data["fix"]`` 翻译成
   ``ExecutionPlan``，**绝不执行**；
3. ``preview(ctx)``：返回 ``ACTION_REGISTRY.preview(plan)`` 的 dry-run diff；
4. ``apply_plan(...)``：``ACTION_REGISTRY.execute`` 的薄封装，默认 dry-run，
   ``confirm=False`` 时绝不写场景。

所有写入都发生在 DSL 执行器内部（白名单 → dry-run → 权限 → 人工确认 →
begin_undo → 逐条 → 失败整体 rollback），本文件不 import 任何宿主 API。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

TOOL_ID = "spec_enforcer"
TITLE = "Spec Enforcer 规范校验"
VERSION = "0.1.0"

_SCOPE_KEY = "spec_enforcer"

# op -> 已注册的 DSL 动作名；未列出的 op 视为「需人工处理」，不进入计划。
_OP_ACTIONS = {
    "rename_joint": TOOL_ID + ".rename_joint",
    "set_control_color": TOOL_ID + ".set_control_color",
    "normalize_weights": TOOL_ID + ".normalize_weights",
    "set_namespace": TOOL_ID + ".set_namespace",
}

_LEGEND = {
    "dry_run": "plan_fixes / preview 只产出参数化计划，不触碰场景。",
    "confirm": "真正写入必须 apply_plan(..., confirm=True) 且授予 node.write 权限。",
    "manual": "create_joint / reparent_joint 等无 DSL 动作的问题只在 skipped_fixes 中提示。",
}


def info() -> Dict[str, Any]:
    """插件自描述，供工具面板与菜单展示。"""
    return {
        "id": TOOL_ID,
        "name": "Spec Enforcer 规范校验与修复",
        "version": VERSION,
        "dcc": ["maya", "max", "mobu"],
        "capabilities": ["scene.read", "node.write", "dsl.plan", "report.render"],
        "description": "按 rig_spec 校验骨架并生成 dry-run 修复计划，写入需人工二次确认。",
    }


# --------------------------------------------------------------------------
# 规则执行
# --------------------------------------------------------------------------
def _load_plugin_modules() -> None:
    """确保 rules / actions 已注册（直接 import core 时也能用）。"""
    try:
        from tak.core import runner

        runner.load_tool_modules(runner.find_plugin(TOOL_ID))
    except Exception:
        # 单文件单测/离线场景下允许失败：此时通常只缺规则，不影响纯函数。
        pass


def _run_rules(ctx: Any) -> Any:
    from tak.core.rules.engine import run_rules

    _load_plugin_modules()
    return run_rules(ctx.rule_context(), title=TITLE, plugin_id=TOOL_ID)


def _scope_enabled(ctx: Any) -> bool:
    """与 rules.py 的作用域判定保持一致（此处只关心是否开启）。"""
    options = getattr(ctx, "options", None)
    if isinstance(options, dict):
        value = options.get(_SCOPE_KEY)
        if value is True:
            return True
        if isinstance(value, dict) and value.get("enabled", True) is not False:
            return True
    snapshot = getattr(ctx, "snapshot", None)
    if hasattr(snapshot, "to_dict"):
        snapshot = snapshot.to_dict()
    metadata = snapshot.get("metadata") if isinstance(snapshot, dict) else None
    block = metadata.get(_SCOPE_KEY) if isinstance(metadata, dict) else None
    return isinstance(block, dict) and block.get("enabled", True) is not False


# --------------------------------------------------------------------------
# 计划生成（只产出，不执行）
# --------------------------------------------------------------------------
def _fix_of(finding: Any) -> Optional[Dict[str, Any]]:
    data = getattr(finding, "data", None)
    fix = data.get("fix") if isinstance(data, dict) else None
    return fix if isinstance(fix, dict) else None


def _build_plan(findings: List[Any]) -> Tuple[Any, List[Dict[str, Any]]]:
    """把 findings 的 fix hint 翻译成 ExecutionPlan，返回 (plan, skipped)。"""
    from tak.core.dsl import ACTION_REGISTRY, Action, DSLError, ExecutionPlan

    actions: List[Any] = []
    skipped: List[Dict[str, Any]] = []
    seen = set()
    for finding in findings:
        fix = _fix_of(finding)
        if not fix:
            continue
        op = str(fix.get("op") or "")
        raw_params = fix.get("params")
        params = dict(raw_params) if isinstance(raw_params, dict) else {}
        rule_id = str(getattr(finding, "rule_id", ""))
        node = str(getattr(finding, "node", "") or "")
        action_name = _OP_ACTIONS.get(op)
        if action_name is None:
            skipped.append({"rule_id": rule_id, "node": node, "op": op,
                            "params": params,
                            "reason": "没有对应的 DSL 动作，需人工在 DCC 内处理"})
            continue
        key = None
        try:
            key = (action_name, json.dumps(params, ensure_ascii=False,
                                           sort_keys=True, allow_nan=False))
        except (TypeError, ValueError) as exc:
            skipped.append({"rule_id": rule_id, "node": node, "op": op,
                            "params": {}, "reason": "参数无法序列化为严格 JSON: %s" % exc})
            continue
        if key in seen:
            continue
        candidate = Action(action_name, params)
        try:
            # 借 preview 做一次完整校验（未声明参数 / 类型 / 范围 / 严格 JSON）。
            ACTION_REGISTRY.preview(ExecutionPlan([candidate]))
        except DSLError as exc:
            skipped.append({"rule_id": rule_id, "node": node, "op": op,
                            "params": params,
                            "reason": "参数未通过动作声明校验: %s" % exc})
            continue
        seen.add(key)
        actions.append(candidate)
    return ExecutionPlan(actions), skipped


def plan_fixes(ctx: Any) -> Any:
    """根据规则结果生成修复计划（只产出 JSON 参数，绝不执行）。"""
    report = _run_rules(ctx)
    plan, _skipped = _build_plan(list(report.findings))
    return plan


def preview(ctx: Any) -> List[Dict[str, Any]]:
    """dry-run diff：列出计划里将要发生的每一步，不产生任何副作用。"""
    from tak.core.dsl import ACTION_REGISTRY

    return ACTION_REGISTRY.preview(plan_fixes(ctx))


def apply_plan(plan: Any, adapter: Any, granted: Optional[List[str]] = None,
               confirm: bool = False, dry_run: Optional[bool] = None) -> Dict[str, Any]:
    """执行计划（薄封装）。

    - ``confirm=False``（默认）：强制 dry-run，**绝不写场景**；
    - ``confirm=True`` 且 ``granted`` 覆盖 ``node.write`` 时才会真正执行，
      由执行器负责 begin_undo / 逐条 / 失败整体 rollback；
    - 显式传 ``dry_run`` 可覆盖默认推导。
    """
    from tak.core.dsl import ACTION_REGISTRY

    if dry_run is None:
        dry_run = not bool(confirm)
    return ACTION_REGISTRY.execute(plan, adapter, granted=list(granted or []),
                                   dry_run=bool(dry_run), confirm=bool(confirm))


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------
def run_report(ctx: Any) -> Any:
    """运行规则并返回 Report；修复计划只写进 metadata，不执行。"""
    report = _run_rules(ctx)
    plan, skipped = _build_plan(list(report.findings))
    report.metadata["rules_count"] = len(report.metadata.get("rule_ids", []))
    report.metadata["fix_plan"] = plan.to_dict()
    report.metadata["plan_count"] = len(plan.actions)
    report.metadata["skipped_fixes"] = skipped
    report.metadata["scope"] = {
        "enabled": _scope_enabled(ctx),
        "key": "metadata.spec_enforcer 或 options.spec_enforcer",
        "note": "未显式开启时规则整组跳过，保证对普通快照零误报。",
    }
    report.metadata["dry_run_only"] = True
    report.metadata["legend"] = dict(_LEGEND)
    return report
