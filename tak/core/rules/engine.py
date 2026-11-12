# -*- coding: utf-8 -*-
"""规则执行引擎。

职责：把「快照 + 规范 + DCC」变成一份与 Report 完全兼容的体检结果。
引擎本身不含任何判定逻辑——判定全部来自 RULE_REGISTRY 中的 @rule。

示例::

    report = evaluate_snapshot(snapshot, dcc="maya")
    if not report.passed:
        print(report.to_markdown())
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from . import builtin as _builtin  # noqa: F401  # 导入即注册内置规则
from .base import RULE_REGISTRY, Finding, Rule, Severity

_SEVERITY_ORDER = {
    Severity.BLOCKER.value: 0,
    Severity.ERROR.value: 1,
    Severity.WARNING.value: 2,
    Severity.INFO.value: 3,
}
_DEFAULT_TITLE = "TAK 规则体检"


@dataclass
class RuleContext:
    """引擎侧上下文：只依赖纯数据，可在无 DCC 环境下构造。"""

    snapshot: Any = field(default_factory=dict)
    spec: Optional[Dict[str, Any]] = None
    dcc: str = "maya"
    options: Dict[str, Any] = field(default_factory=dict)


def _coerce(context: Any, dcc: Optional[str] = None) -> RuleContext:
    """兼容 base.RuleContext 与纯 dict 快照。"""
    if isinstance(context, RuleContext):
        if dcc:
            context.dcc = dcc
        return context
    snapshot = getattr(context, "snapshot", None)
    if snapshot is None and isinstance(context, dict):
        snapshot = context
    spec = getattr(context, "spec", None)
    if spec is None and isinstance(context, dict):
        spec = context.get("spec")
    dcc_name = dcc or getattr(context, "dcc", None) or "maya"
    options = getattr(context, "options", None)
    return RuleContext(snapshot=snapshot or {},
                       spec=spec if isinstance(spec, dict) else None,
                       dcc=str(dcc_name),
                       options=dict(options) if isinstance(options, dict) else {})


def _report_class():
    """延迟导入，避免 tak.core.report ↔ tak.core.rules 的包级循环依赖。"""
    from tak.core.report import Report
    return Report


def _default_spec() -> Dict[str, Any]:
    from tak.core.spec import load_spec
    return load_spec()


def _severity_value(severity: Any) -> str:
    return severity.value if isinstance(severity, Severity) else str(severity)


def _ids(rule_ids: Union[None, str, Iterable[str]]) -> Optional[set]:
    if rule_ids is None:
        return None
    if isinstance(rule_ids, str):
        return {rule_ids}
    return {str(item) for item in rule_ids}


def _select(dcc: str, rule_ids: Optional[set],
            plugin_id: Optional[str]) -> List[Rule]:
    selected = []
    for item in RULE_REGISTRY:
        if dcc not in item.dcc:
            continue
        if rule_ids is not None and item.id not in rule_ids:
            continue
        if plugin_id and item.plugin_id != plugin_id:
            continue
        selected.append(item)
    return selected


def _sort_key(finding: Any) -> tuple:
    return (_SEVERITY_ORDER.get(_severity_value(finding.severity), 9),
            str(finding.rule_id), str(finding.node), str(finding.message))


def run_rules(context: Any, dcc: Optional[str] = None,
              rule_ids: Union[None, str, Sequence[str]] = None,
              plugin_id: Optional[str] = None,
              title: str = _DEFAULT_TITLE) -> Report:
    """执行规则并返回 Report。

    - ``dcc``: 仅运行声明支持该 DCC 的规则，默认取上下文里的 dcc。
    - ``rule_ids``: 只跑指定规则（字符串或列表），用于单测与定点排查。
    - ``plugin_id``: 只跑某个插件的规则；内置规则 plugin_id 为 ``builtin``。
    结果按 blocker > error > warning > info 排序，同级按 rule_id / node 稳定排序。
    """
    ctx = _coerce(context, dcc)
    if not ctx.spec:
        ctx.spec = _default_spec()

    selected = _select(ctx.dcc, _ids(rule_ids), plugin_id)
    findings: List[Any] = []
    for item in selected:
        try:
            findings.extend(item.run(ctx))
        except Exception as exc:  # 规则崩溃不能静默，按 ERROR 上报以保证体检完整性
            findings.append(Finding(
                rule_id=item.id, severity=Severity.ERROR,
                message="规则执行失败: %s: %s" % (type(exc).__name__, exc),
                data={"error": "%s: %s" % (type(exc).__name__, exc)}))
    findings.sort(key=_sort_key)

    report = _report_class()(title=title, dcc=ctx.dcc, findings=findings,
                             spec_version=str((ctx.spec or {}).get("version", "1.0.0")))
    report.metadata.update({
        "rules_available": len(selected),
        "rule_ids": [item.id for item in selected],
        "filters": {
            "dcc": ctx.dcc,
            "rule_ids": sorted(_ids(rule_ids) or []),
            "plugin_id": plugin_id or "",
        },
    })
    return report


def evaluate_snapshot(snapshot: Any, spec: Optional[Dict[str, Any]] = None,
                      dcc: str = "maya",
                      rule_ids: Union[None, str, Sequence[str]] = None) -> Report:
    """便捷入口：快照 dict / SkeletonSnapshot → Report。"""
    if hasattr(snapshot, "to_dict") and not isinstance(snapshot, dict):
        snapshot = snapshot.to_dict()
    return run_rules(RuleContext(snapshot=snapshot or {}, spec=spec, dcc=str(dcc)),
                     rule_ids=rule_ids)


def list_rules(dcc: Optional[str] = None) -> List[Dict[str, Any]]:
    """规则清单（供 UI 与插件指南使用），按 id 排序。"""
    items = [item for item in RULE_REGISTRY if dcc is None or dcc in item.dcc]
    return [{
        "id": item.id,
        "severity": _severity_value(item.severity),
        "description": item.description,
        "dcc": list(item.dcc),
        "fix_available": bool(item.fix_available),
        "plugin_id": item.plugin_id,
    } for item in sorted(items, key=lambda r: r.id)]


def list_rule_ids(dcc: Optional[str] = None) -> List[str]:
    """只返回规则 id，便于插件指南与过滤白名单。"""
    return [item["id"] for item in list_rules(dcc)]
