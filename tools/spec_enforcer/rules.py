# -*- coding: utf-8 -*-
"""Spec Enforcer 的确定性规范规则（纯只读）。

作用域约定（关键）
------------------
规则只在「调用方显式要求执行规范校验」时才生效，满足任一条件即可：

* 快照带插件专属字段 ``metadata["spec_enforcer"]``，且是 dict、``enabled`` 不为 False；
* 调用方传入 ``ToolContext.options["spec_enforcer"]``，值为 True 或 dict。

golden fixtures 既没有该专属字段、也不会带这个 option，因此 40 份 fixtures
上本插件规则一律返回 ``[]``：既不产生 ERROR/BLOCKER，也不会影响内置规则的
全量回归。每条规则自身再检查一次所需字段，字段缺失同样立即返回 ``[]``。

修复契约
--------
每条 finding 的 ``data["fix"]`` 是严格 JSON::

    {"op": "rename_joint", "params": {"node": "LeftUpArm", "pattern": "..."}}

``core.plan_fixes`` 只把 ``op`` 翻译成已注册的 DSL 动作名并生成
``ExecutionPlan``，规则本身绝不改写场景。

规则 id 统一 ``spec_enforcer.`` 前缀，全部 ``dcc=["maya"]``（规范校验依赖
Maya 侧的关节命名与蒙皮采集），``plugin_id`` 显式传 ``TOOL_ID``。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from tak.core.rules import Finding, Severity, rule

TOOL_ID = "spec_enforcer"

_DCC = ["maya"]
_SCOPE_KEY = "spec_enforcer"
_EPS = 1e-4

# 控制器默认颜色（Maya override color 索引）：左侧红、右侧蓝、中线黄。
_SIDE_COLORS = (("_L", 13), ("_R", 6))
_DEFAULT_CONTROL_COLOR = 17


# --------------------------------------------------------------------------
# 取数与容错（兼容 engine.RuleContext 与 base.RuleContext）
# --------------------------------------------------------------------------
def _snapshot(ctx: Any) -> Dict[str, Any]:
    snapshot = getattr(ctx, "snapshot", None)
    if hasattr(snapshot, "to_dict"):
        snapshot = snapshot.to_dict()
    return snapshot if isinstance(snapshot, dict) else {}


def _spec_section(ctx: Any, key: str) -> Dict[str, Any]:
    spec = getattr(ctx, "spec", None)
    section = spec.get(key) if isinstance(spec, dict) else None
    return section if isinstance(section, dict) else {}


def _scope(ctx: Any) -> Optional[Dict[str, Any]]:
    """返回校验作用域配置；未显式开启时返回 None（整组规则跳过）。"""
    options = getattr(ctx, "options", None)
    if isinstance(options, dict):
        value = options.get(_SCOPE_KEY)
        if value is True:
            return {}
        if isinstance(value, dict) and value.get("enabled", True) is not False:
            return value
    metadata = _snapshot(ctx).get("metadata")
    if not isinstance(metadata, dict):
        return None
    block = metadata.get(_SCOPE_KEY)
    if isinstance(block, dict) and block.get("enabled", True) is not False:
        return block
    return None


def _joints(ctx: Any) -> Dict[str, Dict[str, Any]]:
    raw = _snapshot(ctx).get("joints")
    if not isinstance(raw, dict):
        return {}
    return {str(name): (data if isinstance(data, dict) else {})
            for name, data in raw.items()}


def _controls(scope: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = scope.get("controls")
    if not isinstance(raw, (list, tuple)):
        return []
    items: List[Dict[str, Any]] = []
    for entry in raw:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("node") or entry.get("control")
            if isinstance(name, str) and name:
                items.append(dict(entry, name=name))
        elif isinstance(entry, str) and entry:
            items.append({"name": entry})
    return items


def _skin(ctx: Any) -> Dict[str, Any]:
    skin = _snapshot(ctx).get("skin")
    return skin if isinstance(skin, dict) else {}


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


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


def _finding(rule_id: str, severity: Severity, message: str, node: str = "",
             fix_hint: str = "", fix: Optional[Dict[str, Any]] = None,
             **extra: Any) -> Finding:
    data: Dict[str, Any] = dict(extra)
    if fix:
        data["fix"] = fix
    return Finding(rule_id=rule_id, severity=severity, message=message,
                   node=node, fix_hint=fix_hint, data=data)


# --------------------------------------------------------------------------
# 1. 命名正则
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".naming_pattern", severity=Severity.ERROR,
      description="关节名必须匹配 spec 的 naming.joint_pattern",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def naming_pattern(ctx: Any) -> List[Finding]:
    """不匹配命名规范的关节无法稳定映射到引擎骨骼，直接阻断。"""
    scope = _scope(ctx)
    if scope is None:
        return []
    joints = _joints(ctx)
    if not joints:
        return []
    pattern = _text(scope.get("joint_pattern")) or _text(
        _spec_section(ctx, "naming").get("joint_pattern"))
    if not pattern:
        return []
    try:
        rx = re.compile(pattern)
    except re.error:
        # 规范本身非法不属于场景问题，交给 schema 校验器处理，这里不误报。
        return []
    findings: List[Finding] = []
    for name in sorted(joints):
        if rx.match(name) is not None:
            continue
        findings.append(_finding(
            TOOL_ID + ".naming_pattern", Severity.ERROR,
            "关节 %r 不匹配规范命名正则" % name, node=name,
            fix_hint="按 naming.joint_pattern 重命名；目标名需人工确认后再执行",
            fix={"op": "rename_joint", "params": {"node": name, "pattern": pattern}},
            pattern=pattern))
    return findings


# --------------------------------------------------------------------------
# 2. 必需关节
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".required_joints", severity=Severity.ERROR,
      description="骨架必须包含 spec 的 skeleton.required_joints",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def required_joints(ctx: Any) -> List[Finding]:
    """缺必需关节会让导出骨架与 UE 侧 Skeleton 资产对不上。"""
    scope = _scope(ctx)
    if scope is None:
        return []
    joints = _joints(ctx)
    if not joints:
        return []
    required = scope.get("required_joints")
    if not isinstance(required, (list, tuple)):
        required = _spec_section(ctx, "skeleton").get("required_joints")
    if not isinstance(required, (list, tuple)):
        return []
    findings: List[Finding] = []
    for name in required:
        key = str(name)
        if key in joints:
            continue
        findings.append(_finding(
            TOOL_ID + ".required_joints", Severity.ERROR,
            "缺少必需关节 %r" % key, node=key,
            fix_hint="按规范补齐该关节并重建蒙皮权重（创建动作需人工在 DCC 内完成）",
            fix={"op": "create_joint", "params": {"name": key}},
            joint=key))
    return findings


# --------------------------------------------------------------------------
# 3. 层级深度
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".max_depth", severity=Severity.ERROR,
      description="骨骼层级深度不得超过 spec 的 skeleton.max_depth",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def max_depth(ctx: Any) -> List[Finding]:
    """层级过深会放大动画误差，并让引擎侧骨骼链难以维护。"""
    scope = _scope(ctx)
    if scope is None:
        return []
    limit = _number(scope.get("max_depth"))
    if limit is None:
        limit = _number(_spec_section(ctx, "skeleton").get("max_depth"))
    if limit is None:
        return []
    findings: List[Finding] = []
    for name, data in sorted(_joints(ctx).items()):
        depth = _number(data.get("depth"))
        if depth is None or depth <= limit:
            continue
        findings.append(_finding(
            TOOL_ID + ".max_depth", Severity.ERROR,
            "关节 %r 的层级深度 %g 超过上限 %g" % (name, depth, limit), node=name,
            fix_hint="压平中间辅助骨骼，或把末端关节合并到父级",
            fix={"op": "reparent_joint",
                 "params": {"node": name, "depth": int(depth),
                            "max_depth": int(limit)}},
            depth=depth, max_depth=limit))
    return findings


# --------------------------------------------------------------------------
# 4. 权重归一化
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".weight_sum", severity=Severity.ERROR,
      description="顶点权重和偏差必须落在 spec 的 skin.weight_sum_tolerance 内",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def weight_sum(ctx: Any) -> List[Finding]:
    """权重和不为 1，蒙皮在引擎侧会整体偏胖或偏瘦。"""
    scope = _scope(ctx)
    if scope is None:
        return []
    skin = _skin(ctx)
    if not skin:
        return []
    deviation = _number(skin.get("weight_sum_deviation"))
    tolerance = _number(scope.get("weight_sum_tolerance"))
    if tolerance is None:
        tolerance = _number(_spec_section(ctx, "skin").get("weight_sum_tolerance"))
    if deviation is None or tolerance is None or abs(deviation) <= tolerance + _EPS:
        return []
    return [_finding(
        TOOL_ID + ".weight_sum", Severity.ERROR,
        "权重和偏差 %g 超出规范容差 %g" % (deviation, tolerance),
        fix_hint="执行 normalize weights，确保每顶点权重和为 1",
        fix={"op": "normalize_weights", "params": {"tolerance": tolerance}},
        deviation=deviation, tolerance=tolerance)]


# --------------------------------------------------------------------------
# 5. 影响骨上限
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".influence_limit", severity=Severity.ERROR,
      description="单顶点影响骨数量不得超过 spec 的 skin.max_influences",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def influence_limit(ctx: Any) -> List[Finding]:
    """超限的影响骨会被引擎截断，DCC 预览与运行时结果不一致。"""
    scope = _scope(ctx)
    if scope is None:
        return []
    skin = _skin(ctx)
    if not skin:
        return []
    found = _number(skin.get("max_influences_found"))
    limit = _number(scope.get("max_influences"))
    if limit is None:
        limit = _number(_spec_section(ctx, "skin").get("max_influences"))
    if found is None or limit is None or found <= limit:
        return []
    params: Dict[str, Any] = {"max_influences": int(limit)}
    tolerance = _number(scope.get("weight_sum_tolerance"))
    if tolerance is None:
        tolerance = _number(_spec_section(ctx, "skin").get("weight_sum_tolerance"))
    if tolerance is not None:
        params["tolerance"] = tolerance
    return [_finding(
        TOOL_ID + ".influence_limit", Severity.ERROR,
        "单顶点最大影响骨数 %g 超过规范上限 %g" % (found, limit),
        fix_hint="limit influences 到 %g 根，并重新 normalize weights" % limit,
        fix={"op": "normalize_weights", "params": params},
        found=found, max_influences=limit)]


# --------------------------------------------------------------------------
# 6. 控制器命名空间
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".control_namespace", severity=Severity.WARNING,
      description="控制器必须位于 spec 的 naming.control_namespace 下",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def control_namespace(ctx: Any) -> List[Finding]:
    """控制器散落在其他命名空间时，动画引用与导出节点路径都会不稳定。"""
    scope = _scope(ctx)
    if scope is None:
        return []
    expected = _text(scope.get("control_namespace")) or _text(
        _spec_section(ctx, "naming").get("control_namespace"))
    if not expected:
        return []
    findings: List[Finding] = []
    for entry in _controls(scope):
        node = entry["name"]
        namespace = _text(entry.get("namespace")).rstrip(":") or _text(
            entry.get("ns")).rstrip(":")
        if namespace == expected:
            continue
        findings.append(_finding(
            TOOL_ID + ".control_namespace", Severity.WARNING,
            "控制器 %r 的命名空间 %r 不是规范要求的 %r"
            % (node, namespace or "", expected), node=node,
            fix_hint="把控制器移入 %r 命名空间，骨架节点保持根命名空间" % expected,
            fix={"op": "set_namespace",
                 "params": {"node": node, "namespace": expected}},
            namespace=namespace, expected=expected))
    return findings


# --------------------------------------------------------------------------
# 7. 控制器颜色
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".control_color", severity=Severity.INFO,
      description="控制器建议按左右侧设置 override color，便于动画师识别",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def control_color(ctx: Any) -> List[Finding]:
    """未上色控制器不影响导出，但会降低动画制作效率。"""
    scope = _scope(ctx)
    if scope is None:
        return []
    findings: List[Finding] = []
    for entry in _controls(scope):
        node = entry["name"]
        if not _color_missing(entry.get("color")):
            continue
        color = _suggest_color(node)
        findings.append(_finding(
            TOOL_ID + ".control_color", Severity.INFO,
            "控制器 %r 未设置颜色" % node, node=node,
            fix_hint="按左右侧约定设置 override color（左红右蓝，中线黄）",
            fix={"op": "set_control_color", "params": {"node": node, "color": color}},
            color=color))
    return findings


def _color_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("", "default", "none")
    if isinstance(value, dict):
        if value.get("colored") is False:
            return True
        if "color" in value:
            return _color_missing(value.get("color"))
        return False
    return False


def _suggest_color(node: str) -> int:
    lowered = node.lower()
    for suffix, color in _SIDE_COLORS:
        if lowered.endswith(suffix.lower()):
            return color
    return _DEFAULT_CONTROL_COLOR


RULE_IDS = (
    TOOL_ID + ".naming_pattern",
    TOOL_ID + ".required_joints",
    TOOL_ID + ".max_depth",
    TOOL_ID + ".weight_sum",
    TOOL_ID + ".influence_limit",
    TOOL_ID + ".control_namespace",
    TOOL_ID + ".control_color",
)


def scope_enabled(ctx: Any) -> bool:
    """供 core / UI 判断当前上下文是否开启了规范校验。"""
    return _scope(ctx) is not None
