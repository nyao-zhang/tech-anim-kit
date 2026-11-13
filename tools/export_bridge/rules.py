# -*- coding: utf-8 -*-
"""Export Bridge 的导出侧确定性规则（纯只读）。

只检查「导出设置是否与 spec 一致」：单位、轴向、帧范围。三者任一不一致，
导出结果在引擎侧就会出现缩放错误、朝向翻转或动画长度不符。

容错约定
--------
* 快照缺 ``unit`` / ``up_axis`` / ``frame_range``，或 spec 缺
  ``export.fbx`` 对应字段时，立即返回 ``[]``——绝不对缺失字段误报；
* 只看 Maya 侧（``dcc=["maya"]``），``plugin_id`` 显式声明，避免被内置规则
  或其它插件的结果污染；
* 本模块不提供 DSL 动作：导出设置在引擎侧还需同步调整，交给人工处理，
  fix hint 只写清「改成什么」。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from tak.core.rules import Finding, Severity, rule

TOOL_ID = "export_bridge"

_DCC = ["maya"]
_EPS = 1e-6


def _snapshot(ctx: Any) -> Dict[str, Any]:
    snapshot = getattr(ctx, "snapshot", None)
    if hasattr(snapshot, "to_dict"):
        snapshot = snapshot.to_dict()
    return snapshot if isinstance(snapshot, dict) else {}


def _fbx_spec(ctx: Any) -> Dict[str, Any]:
    spec = getattr(ctx, "spec", None)
    export = spec.get("export") if isinstance(spec, dict) else None
    fbx = export.get("fbx") if isinstance(export, dict) else None
    return fbx if isinstance(fbx, dict) else {}


def _numbers(value: Any) -> List[float]:
    if not isinstance(value, (list, tuple)):
        return []
    result: List[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return []
        result.append(float(item))
    return result


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _finding(rule_id: str, severity: Severity, message: str,
             fix_hint: str = "", fix: Optional[Dict[str, Any]] = None,
             **extra: Any) -> Finding:
    data: Dict[str, Any] = dict(extra)
    if fix:
        data["fix"] = fix
    return Finding(rule_id=rule_id, severity=severity, message=message,
                   fix_hint=fix_hint, data=data)


@rule(TOOL_ID + ".unit_axis_consistency", severity=Severity.ERROR,
      description="导出单位与 up_axis 必须与 spec 的 export.fbx 一致",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def unit_axis_consistency(ctx: Any) -> List[Finding]:
    """单位不一致会整体缩放错位，轴向不一致会让角色在引擎里躺倒。"""
    fbx = _fbx_spec(ctx)
    snapshot = _snapshot(ctx)
    expected_unit = _text(fbx.get("unit"))
    expected_axis = _text(fbx.get("up_axis"))
    if not expected_unit and not expected_axis:
        return []
    findings: List[Finding] = []
    actual_unit = _text(snapshot.get("unit"))
    if expected_unit and actual_unit and actual_unit != expected_unit:
        findings.append(_finding(
            TOOL_ID + ".unit_axis_consistency", Severity.ERROR,
            "场景单位 %r 与导出规范 %r 不一致" % (actual_unit, expected_unit),
            fix_hint="把场景 working unit 改为 %s，或按规范设置导出缩放" % expected_unit,
            fix={"op": "set_scene_unit", "params": {"unit": expected_unit}},
            field="unit", before=actual_unit, after=expected_unit))
    actual_axis = _text(snapshot.get("up_axis"))
    if expected_axis and actual_axis and actual_axis != expected_axis:
        findings.append(_finding(
            TOOL_ID + ".unit_axis_consistency", Severity.ERROR,
            "场景 up_axis %r 与导出规范 %r 不一致" % (actual_axis, expected_axis),
            fix_hint="导出前把 up axis 切换为 %s，并确认骨架朝向未被二次旋转"
                     % expected_axis,
            fix={"op": "set_up_axis", "params": {"up_axis": expected_axis}},
            field="up_axis", before=actual_axis, after=expected_axis))
    return findings


@rule(TOOL_ID + ".frame_range_consistency", severity=Severity.ERROR,
      description="导出帧范围必须与 spec 的 export.fbx.frame_range 一致",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def frame_range_consistency(ctx: Any) -> List[Finding]:
    """帧范围不一致会让烘焙出的动画长度与引擎侧动画资产对不上。"""
    expected = _numbers(_fbx_spec(ctx).get("frame_range"))
    actual = _numbers(_snapshot(ctx).get("frame_range"))
    if len(expected) != 2 or len(actual) != 2:
        return []
    if all(abs(a - b) <= _EPS for a, b in zip(actual, expected)):
        return []
    return [_finding(
        TOOL_ID + ".frame_range_consistency", Severity.ERROR,
        "导出帧范围 %s 与规范 %s 不一致" % (actual, expected),
        fix_hint="把时间轴起止帧设为 %s 后再烘焙/导出" % expected,
        fix={"op": "set_frame_range", "params": {"frame_range": list(expected)}},
        field="frame_range", before=actual, after=expected)]


RULE_IDS = (
    TOOL_ID + ".unit_axis_consistency",
    TOOL_ID + ".frame_range_consistency",
)
