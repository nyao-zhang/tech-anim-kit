# -*- coding: utf-8 -*-
"""Rig Health Guard 的 Maya 侧确定性规则。

设计约束
--------
* **纯只读**：只读取快照与 ``metadata["health"]``，绝不修改场景；
  可修复项只在 ``Finding.fix_hint`` / ``Finding.data["fix"]`` 里给出
  dry-run 建议，由上层在用户确认后执行。
* **零误报**：``metadata["health"]`` 不存在或不是 dict 时，整组规则直接
  返回 ``[]``。golden fixtures 不含该插件专属字段，因此不会污染内置规则
  的全量回归。
* 规则只声明 ``dcc=["maya"]``：健康字段由 Maya 适配器采集，其他宿主
  即使加载了插件也不会误判。

规则 id 统一以 ``rig_health_guard.`` 开头，``plugin_id`` 显式传入
``TOOL_ID``，避免依赖模块名推断。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from tak.core.rules import Finding, Severity, rule

TOOL_ID = "rig_health_guard"

_DCC = ["maya"]
_EPS = 1e-4
_DEFAULT_MAX_INFLUENCES = 4
_DEFAULT_WEIGHT_TOLERANCE = 0.001


# --------------------------------------------------------------------------
# 取数与容错（兼容 engine.RuleContext 与 base.RuleContext）
# --------------------------------------------------------------------------
def _snapshot(ctx: Any) -> Dict[str, Any]:
    snapshot = getattr(ctx, "snapshot", None)
    if hasattr(snapshot, "to_dict"):
        snapshot = snapshot.to_dict()
    return snapshot if isinstance(snapshot, dict) else {}


def _health(ctx: Any) -> Optional[Dict[str, Any]]:
    """插件专属健康字段；缺失或类型不对时返回 None（整组规则跳过）。"""
    metadata = _snapshot(ctx).get("metadata")
    if not isinstance(metadata, dict):
        return None
    health = metadata.get("health")
    return health if isinstance(health, dict) else None


def _spec_section(ctx: Any, key: str) -> Dict[str, Any]:
    spec = getattr(ctx, "spec", None)
    section = spec.get(key) if isinstance(spec, dict) else None
    return section if isinstance(section, dict) else {}


def _joints(ctx: Any) -> Dict[str, Dict[str, Any]]:
    raw = _snapshot(ctx).get("joints")
    if not isinstance(raw, dict):
        return {}
    return {str(name): (data if isinstance(data, dict) else {})
            for name, data in raw.items()}


def _numbers(value: Any) -> List[float]:
    if not isinstance(value, (list, tuple)):
        return []
    result: List[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return []
        result.append(float(item))
    return result


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _entry_name(item: Any, keys=("node", "name", "shape", "file")) -> str:
    if isinstance(item, dict):
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
        return ""
    if isinstance(item, str):
        return item
    return ""


def _finding(rule_id: str, severity: Severity, message: str, node: str = "",
             fix_hint: str = "", fix: Optional[Dict[str, Any]] = None,
             **extra: Any) -> Finding:
    data: Dict[str, Any] = dict(extra)
    if fix:
        data["fix"] = fix
    return Finding(rule_id=rule_id, severity=severity, message=message,
                   node=node, fix_hint=fix_hint, data=data)


# --------------------------------------------------------------------------
# 场景清理
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".orphan_nodes", severity=Severity.WARNING,
      description="场景中不应残留无父级、无引用的孤立节点",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def orphan_nodes(ctx: Any) -> List[Finding]:
    """孤立节点会让场景变脏，导出时可能被误带进引擎。"""
    health = _health(ctx)
    if health is None:
        return []
    entries = health.get("orphan_nodes")
    if not isinstance(entries, (list, tuple)):
        return []
    findings: List[Finding] = []
    for item in entries:
        node = _entry_name(item)
        if not node:
            continue
        findings.append(_finding(
            TOOL_ID + ".orphan_nodes", Severity.WARNING,
            "节点 %r 既没有父级也没有蒙皮/约束引用，属于孤立节点" % node,
            node=node,
            fix_hint="确认无动画或材质依赖后删除；如需保留请挂回骨架层级",
            fix={"op": "delete_node", "node": node, "dry_run": True},
            orphan=node))
    return findings


@rule(TOOL_ID + ".negative_scale", severity=Severity.ERROR,
      description="骨骼不得存在负缩放，否则导出会镜像翻转",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def negative_scale(ctx: Any) -> List[Finding]:
    """负缩放破坏关节朝向与蒙皮，是导出级错误。"""
    health = _health(ctx)
    if health is None:
        return []
    findings: List[Finding] = []
    seen = set()
    for name, data in _joints(ctx).items():
        scale = _numbers(data.get("scale"))
        if any(value < -_EPS for value in scale):
            seen.add(name)
            findings.append(_finding(
                TOOL_ID + ".negative_scale", Severity.ERROR,
                "关节 %r 存在负缩放 %s，导出后会出现镜像翻转" % (name, scale),
                node=name,
                fix_hint="把缩放归正到 1 并重新冻结变换，再检查镜像侧关节",
                fix={"op": "reset_scale", "node": name,
                     "scale": [abs(value) for value in scale], "dry_run": True},
                scale=scale))
    # 适配器也可以仅上报节点名（例如约束/组上的负缩放）。
    for item in _list(health.get("negative_scale")):
        node = _entry_name(item)
        if node and node not in seen:
            seen.add(node)
            findings.append(_finding(
                TOOL_ID + ".negative_scale", Severity.ERROR,
                "节点 %r 存在负缩放" % node, node=node,
                fix_hint="归正缩放并冻结变换后再导出",
                fix={"op": "reset_scale", "node": node, "dry_run": True},
                scale=[]))
    return findings


@rule(TOOL_ID + ".unfrozen_transform", severity=Severity.WARNING,
      description="骨架节点应冻结变换，避免残留缩放/旋转影响绑定",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def unfrozen_transform(ctx: Any) -> List[Finding]:
    """未冻结的缩放/旋转会把误差带进动画与导出。"""
    health = _health(ctx)
    if health is None:
        return []
    explicit = health.get("unfrozen_transforms")
    findings: List[Finding] = []
    if isinstance(explicit, (list, tuple)):
        for item in explicit:
            node = _entry_name(item)
            if not node:
                continue
            findings.append(_finding(
                TOOL_ID + ".unfrozen_transform", Severity.WARNING,
                "节点 %r 的变换通道未冻结" % node, node=node,
                fix_hint="对骨架执行 freeze transformations 后再保存/导出",
                fix={"op": "freeze_transforms", "node": node, "dry_run": True}))
        # 该字段存在时以适配器上报为准，不再从 joints 二次推断。
        return findings
    for name, data in _joints(ctx).items():
        scale = _numbers(data.get("scale"))
        rotate = _numbers(data.get("rotate"))
        dirty_scale = any(abs(value - 1.0) > _EPS for value in scale)
        dirty_rotate = any(abs(value) > _EPS for value in rotate)
        if dirty_scale or dirty_rotate:
            findings.append(_finding(
                TOOL_ID + ".unfrozen_transform", Severity.WARNING,
                "关节 %r 变换未冻结（scale=%s, rotate=%s）" % (
                    name, scale or "缺失", rotate or "缺失"),
                node=name,
                fix_hint="对骨架执行 freeze transformations；确认关节朝向仍正确",
                fix={"op": "freeze_transforms", "node": name, "dry_run": True},
                scale=scale, rotate=rotate))
    return findings


@rule(TOOL_ID + ".namespace_pollution", severity=Severity.WARNING,
      description="骨架关节不应带 DCC 命名空间前缀",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def namespace_pollution(ctx: Any) -> List[Finding]:
    """带命名空间的关节在导出/引用后会变成不一致的节点路径。"""
    health = _health(ctx)
    if health is None:
        return []
    findings: List[Finding] = []
    for name, data in _joints(ctx).items():
        namespace = data.get("namespace")
        if not isinstance(namespace, str):
            namespace = ""
        if namespace or ":" in name:
            findings.append(_finding(
                TOOL_ID + ".namespace_pollution", Severity.WARNING,
                "关节 %r 带命名空间前缀 %r" % (name, namespace or name.split(":")[0]),
                node=name,
                fix_hint="把骨架移回根命名空间，并清理节点名里的冒号前缀",
                fix={"op": "clear_namespace", "node": name, "dry_run": True},
                namespace=namespace))
    return findings


@rule(TOOL_ID + ".duplicate_shape", severity=Severity.WARNING,
      description="多个 transform 不应共享同一个 shape 节点",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def duplicate_shape(ctx: Any) -> List[Finding]:
    """共享 shape 会导致替换模型时互相串改。"""
    health = _health(ctx)
    if health is None:
        return []
    entries = health.get("duplicate_shapes")
    if not isinstance(entries, (list, tuple)):
        return []
    findings: List[Finding] = []
    for item in entries:
        if isinstance(item, dict):
            shape = str(item.get("shape") or item.get("name") or "")
            nodes = [str(node) for node in _list(item.get("nodes")) if str(node)]
        elif isinstance(item, (list, tuple)):
            shape = ""
            nodes = [str(node) for node in item if str(node)]
        else:
            shape = str(item or "")
            nodes = [shape] if shape else []
        if not shape and not nodes:
            continue
        label = shape or "未命名 shape"
        findings.append(_finding(
            TOOL_ID + ".duplicate_shape", Severity.WARNING,
            "shape %r 被多个节点共享: %s" % (label, ", ".join(nodes) or "未知"),
            node=nodes[0] if nodes else label,
            fix_hint="保留一个节点，其余删除或复制为独立 shape 后再改名",
            fix={"op": "resolve_duplicate_shape", "shape": label,
                 "nodes": nodes, "dry_run": True},
            shape=label, nodes=nodes))
    return findings


# --------------------------------------------------------------------------
# 蒙皮
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".skin_influence_over_limit", severity=Severity.ERROR,
      description="单顶点影响骨数量不得超过上限，否则引擎会截断权重",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def skin_influence_over_limit(ctx: Any) -> List[Finding]:
    """影响骨超限会在引擎侧被硬截断，导致蒙皮结果与 DCC 不一致。"""
    health = _health(ctx)
    if health is None:
        return []
    skin = _snapshot(ctx).get("skin")
    if not isinstance(skin, dict):
        return []
    found = _number(skin.get("max_influences_found"))
    limit = _number(health.get("max_influences"))
    if limit is None:
        limit = _number(_spec_section(ctx, "skin").get("max_influences"))
    if limit is None:
        limit = float(_DEFAULT_MAX_INFLUENCES)
    if found is None or found <= limit:
        return []
    return [_finding(
        TOOL_ID + ".skin_influence_over_limit", Severity.ERROR,
        "单顶点最大影响骨数 %g 超过上限 %g，引擎会截断权重" % (found, limit),
        fix_hint="执行 limit influences 到 %g 根，并重新 normalize weights" % limit,
        fix={"op": "limit_influences", "max": limit, "found": found, "dry_run": True},
        found=found, max_influences=limit)]


@rule(TOOL_ID + ".skin_weight_sum", severity=Severity.ERROR,
      description="顶点权重和偏差必须落在容差内",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def skin_weight_sum(ctx: Any) -> List[Finding]:
    """权重和不为 1 会让蒙皮在引擎里整体偏胖或偏瘦。"""
    health = _health(ctx)
    if health is None:
        return []
    skin = _snapshot(ctx).get("skin")
    if not isinstance(skin, dict):
        return []
    deviation = _number(skin.get("weight_sum_deviation"))
    tolerance = _number(_spec_section(ctx, "skin").get("weight_sum_tolerance"))
    if tolerance is None:
        tolerance = _DEFAULT_WEIGHT_TOLERANCE
    if deviation is None or abs(deviation) <= tolerance:
        return []
    return [_finding(
        TOOL_ID + ".skin_weight_sum", Severity.ERROR,
        "权重和偏差 %g 超出容差 %g" % (deviation, tolerance),
        fix_hint="执行 normalize weights，确保每顶点权重和为 1",
        fix={"op": "normalize_weights", "tolerance": tolerance, "dry_run": True},
        deviation=deviation, tolerance=tolerance)]


# --------------------------------------------------------------------------
# 引用与控制器
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".legacy_reference", severity=Severity.WARNING,
      description="场景不应残留旧版 reference 节点",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def legacy_reference(ctx: Any) -> List[Finding]:
    """遗留 reference 会带来隐藏命名空间与重复节点。"""
    health = _health(ctx)
    if health is None:
        return []
    entries = health.get("references")
    if not isinstance(entries, (list, tuple)):
        return []
    findings: List[Finding] = []
    for item in entries:
        node = _entry_name(item, keys=("node", "name", "file", "reference"))
        if not node:
            continue
        loaded = item.get("loaded") if isinstance(item, dict) else None
        state = "仍处于加载状态" if loaded is True else (
            "已卸载但节点仍保留" if loaded is False else "仍在场景中")
        findings.append(_finding(
            TOOL_ID + ".legacy_reference", Severity.WARNING,
            "reference %r %s，可能带来隐藏命名空间与重复节点" % (node, state),
            node=node,
            fix_hint="确认无依赖后 remove reference，或把内容导入并清理命名空间",
            fix={"op": "remove_reference", "reference": node,
                 "dry_run": True},
            loaded=loaded))
    return findings


@rule(TOOL_ID + ".control_uncolored", severity=Severity.INFO,
      description="控制器建议按左右侧上色，便于动画师识别",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def control_uncolored(ctx: Any) -> List[Finding]:
    """未上色控制器不影响导出，但会降低动画制作效率。"""
    health = _health(ctx)
    if health is None:
        return []
    colors = health.get("control_colors")
    findings: List[Finding] = []
    if isinstance(colors, dict):
        for name, value in colors.items():
            node = str(name)
            if not node:
                continue
            if _color_missing(value):
                findings.append(_uncolored(node))
    elif isinstance(colors, (list, tuple)):
        for item in colors:
            node = _entry_name(item, keys=("node", "name", "control"))
            if node:
                findings.append(_uncolored(node))
    return findings


def _color_missing(value: Any) -> bool:
    """判断控制器颜色是否缺失；只认明确的空值与 colored=False。"""
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


def _uncolored(node: str) -> Finding:
    return _finding(
        TOOL_ID + ".control_uncolored", Severity.INFO,
        "控制器 %r 未上色" % node, node=node,
        fix_hint="按左右侧约定设置 override color（左红右蓝或项目规范）",
        fix={"op": "set_control_color", "node": node, "dry_run": True})


RULE_IDS = (
    TOOL_ID + ".orphan_nodes",
    TOOL_ID + ".negative_scale",
    TOOL_ID + ".unfrozen_transform",
    TOOL_ID + ".namespace_pollution",
    TOOL_ID + ".duplicate_shape",
    TOOL_ID + ".skin_influence_over_limit",
    TOOL_ID + ".skin_weight_sum",
    TOOL_ID + ".legacy_reference",
    TOOL_ID + ".control_uncolored",
)
