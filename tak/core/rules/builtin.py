# -*- coding: utf-8 -*-
"""内置确定性规则集。

全部规则只读快照与规范，绝不修改场景；可修复项只输出结构化 fix hint
（``Finding.data["fix"]``），由上层在用户确认后调用 adapter 执行。

规则 id 与 ``fixtures/bad`` 中声明的注入类型一一对应，可直接作为
golden fixtures 的判定基准。
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Sequence

from .base import Finding, RuleContext, Severity, rule

PLUGIN_ID = "builtin"
_ALL_DCC = ["maya", "max", "mobu", "unreal"]
_AUTHORING_DCC = ["maya", "max", "mobu"]

# 常见但未列入规范 side_tokens 的写法，用于给出清晰报错
_KNOWN_SIDE_SUFFIXES = ("_l", "_r", "_left", "_right", "_lt", "_rt", "_lf", "_rf", "_c")


# --------------------------------------------------------------------------
# 取数与容错
# --------------------------------------------------------------------------
def _snapshot(ctx: Any) -> Dict[str, Any]:
    snapshot = getattr(ctx, "snapshot", None)
    if hasattr(snapshot, "to_dict"):
        snapshot = snapshot.to_dict()
    return snapshot if isinstance(snapshot, dict) else {}


def _spec(ctx: Any) -> Dict[str, Any]:
    spec = getattr(ctx, "spec", None)
    return spec if isinstance(spec, dict) else {}


def _section(ctx: Any, key: str) -> Dict[str, Any]:
    value = _spec(ctx).get(key)
    return value if isinstance(value, dict) else {}


def _joints(ctx: Any) -> Dict[str, Dict[str, Any]]:
    raw = _snapshot(ctx).get("joints")
    if not isinstance(raw, dict):
        return {}
    return {str(name): (data if isinstance(data, dict) else {}) for name, data in raw.items()}


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


def _version_key(text: Any) -> Optional[tuple]:
    if not isinstance(text, str):
        return None
    parts = re.findall(r"\d+", text)
    return tuple(int(p) for p in parts) if parts else None


def _finding(rule_id: str, severity: Severity, message: str, node: str = "",
             fix_hint: str = "", fix: Optional[Dict[str, Any]] = None,
             **extra: Any) -> Finding:
    data: Dict[str, Any] = dict(extra)
    if fix:
        data["fix"] = fix
    return Finding(rule_id=rule_id, severity=severity, message=message,
                   node=node, fix_hint=fix_hint, data=data)


# --------------------------------------------------------------------------
# 命名规则
# --------------------------------------------------------------------------
@rule("naming.joint_pattern", severity=Severity.ERROR,
      description="骨骼名必须匹配规范 naming.joint_pattern", dcc=_ALL_DCC,
      fix_available=True, plugin_id=PLUGIN_ID)
def joint_pattern(ctx: RuleContext) -> List[Finding]:
    joints = _joints(ctx)
    pattern = _section(ctx, "naming").get("joint_pattern")
    if not isinstance(pattern, str) or not pattern:
        return []
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return [_finding("naming.joint_pattern", Severity.ERROR,
                         "规范中的 joint_pattern 不是合法正则: %s" % exc,
                         fix_hint="修正 schema/rig_spec.default.json 的 naming.joint_pattern",
                         pattern=pattern)]
    findings: List[Finding] = []
    for name in joints:
        if rx.match(name) is None:
            findings.append(_finding(
                "naming.joint_pattern", Severity.ERROR,
                "骨骼名 %r 不匹配命名规范" % name, node=name,
                fix_hint="按规范重命名，例如 upperarm_L / spine_01 / FACIAL_jaw",
                fix={"op": "rename_joint", "node": name, "pattern": pattern},
                pattern=pattern))
    return findings


@rule("naming.forbidden_token", severity=Severity.ERROR,
      description="骨骼名不得包含禁用的分隔符、空格或拷贝后缀", dcc=_ALL_DCC,
      fix_available=True, plugin_id=PLUGIN_ID)
def forbidden_token(ctx: RuleContext) -> List[Finding]:
    tokens = _section(ctx, "naming").get("forbidden_tokens")
    if not isinstance(tokens, (list, tuple)):
        return []
    tokens = [str(t) for t in tokens if str(t)]
    findings: List[Finding] = []
    for name in _joints(ctx):
        hits = [t for t in tokens if t in name]
        if hits:
            findings.append(_finding(
                "naming.forbidden_token", Severity.ERROR,
                "骨骼名 %r 含禁用 token %s" % (name, ", ".join(repr(t) for t in hits)), node=name,
                fix_hint="去掉空格 / 分隔符 / copy 等后缀后重新命名",
                fix={"op": "rename_joint", "node": name, "remove_tokens": hits},
                tokens=hits))
    return findings


@rule("naming.namespace", severity=Severity.ERROR,
      description="骨架关节不得带 DCC 命名空间前缀", dcc=_ALL_DCC,
      fix_available=True, plugin_id=PLUGIN_ID)
def namespace(ctx: RuleContext) -> List[Finding]:
    findings: List[Finding] = []
    for name, data in _joints(ctx).items():
        ns = data.get("namespace")
        if (isinstance(ns, str) and ns) or ":" in name:
            findings.append(_finding(
                "naming.namespace", Severity.ERROR,
                "关节 %r 带命名空间 %r，导出后骨骼路径会不一致" % (name, ns or name.split(":")[0]),
                node=name,
                fix_hint="将参考骨架移到根命名空间（namespace = \"\"）后再导出",
                fix={"op": "clear_namespace", "node": name},
                namespace=ns if isinstance(ns, str) else ""))
    return findings


@rule("naming.side_tokens", severity=Severity.WARNING,
      description="左右侧标记必须是规范 side_tokens 且左右成对", dcc=_ALL_DCC,
      plugin_id=PLUGIN_ID)
def side_tokens(ctx: RuleContext) -> List[Finding]:
    joints = _joints(ctx)
    tokens = _section(ctx, "naming").get("side_tokens")
    tokens = [str(t) for t in tokens] if isinstance(tokens, (list, tuple)) else []
    pair = {}
    for token in tokens:
        for other in tokens:
            if other != token:
                pair[token] = other
                break
    findings: List[Finding] = []
    for name in joints:
        lowered = name.lower()
        suffix = next((s for s in _KNOWN_SIDE_SUFFIXES if lowered.endswith(s)), None)
        if suffix is None:
            continue
        actual = name[-len(suffix):]
        if tokens and actual not in tokens:
            findings.append(_finding(
                "naming.side_tokens", Severity.WARNING,
                "关节 %r 的侧标记 %r 不在规范 %s 中" % (name, actual, tokens), node=name,
                fix_hint="使用规范侧标记重命名，例如 %s%s" % (name[:-len(suffix)], tokens[0]),
                suffix=actual, allowed=tokens))
            continue
        other = pair.get(actual)
        if other:
            mirror = name[:-len(actual)] + other
            if mirror not in joints:
                findings.append(_finding(
                    "naming.side_tokens", Severity.WARNING,
                    "关节 %r 缺少镜像关节 %r" % (name, mirror), node=name,
                    fix_hint="补齐左右对称关节，或确认该骨骼本就单侧存在",
                    suffix=actual, missing_mirror=mirror))
    return findings


@rule("naming.case_collision", severity=Severity.WARNING,
      description="规范区分大小写，关节名不得仅靠大小写区分", dcc=_ALL_DCC,
      plugin_id=PLUGIN_ID)
def case_collision(ctx: RuleContext) -> List[Finding]:
    groups: Dict[str, List[str]] = {}
    for name in _joints(ctx):
        groups.setdefault(name.lower(), []).append(name)
    findings: List[Finding] = []
    for names in groups.values():
        if len(names) > 1:
            findings.append(_finding(
                "naming.case_collision", Severity.WARNING,
                "关节名仅靠大小写区分: %s" % ", ".join(sorted(names)), node=names[0],
                fix_hint="删除重复项或改用规范命名", joints=sorted(names)))
    return findings


@rule("naming.control_namespace", severity=Severity.WARNING,
      description="带命名空间的节点必须位于规范 control_namespace 下", dcc=_ALL_DCC,
      fix_available=True, plugin_id=PLUGIN_ID)
def control_namespace(ctx: RuleContext) -> List[Finding]:
    expected = _section(ctx, "naming").get("control_namespace")
    if not isinstance(expected, str) or not expected:
        return []
    findings: List[Finding] = []
    for name, data in _joints(ctx).items():
        ns = data.get("namespace")
        if not isinstance(ns, str) or not ns:
            continue
        stripped = ns.rstrip(":")
        if stripped and stripped != expected:
            findings.append(_finding(
                "naming.control_namespace", Severity.WARNING,
                "关节 %r 的命名空间 %r 不是控制命名空间 %r" % (name, stripped, expected),
                node=name,
                fix_hint="控制器统一放入 %s: 命名空间，骨架节点保持无命名空间" % expected,
                fix={"op": "rename_namespace", "node": name, "from": stripped,
                     "to": expected},
                namespace=stripped, expected=expected))
    return findings


# --------------------------------------------------------------------------
# 骨架规则
# --------------------------------------------------------------------------
@rule("skeleton.missing_required", severity=Severity.ERROR,
      description="必须包含规范要求的全部关节", dcc=_ALL_DCC,
      fix_available=True, plugin_id=PLUGIN_ID)
def missing_required(ctx: RuleContext) -> List[Finding]:
    required = _section(ctx, "skeleton").get("required_joints")
    if not isinstance(required, (list, tuple)):
        return []
    joints = _joints(ctx)
    findings: List[Finding] = []
    for name in required:
        if str(name) not in joints:
            findings.append(_finding(
                "skeleton.missing_required", Severity.ERROR,
                "缺少必需关节 %r" % name, node=str(name),
                fix_hint="按规范补齐该关节并重建蒙皮权重",
                fix={"op": "create_joint", "name": str(name)},
                joint=str(name)))
    return findings


@rule("skeleton.max_depth", severity=Severity.ERROR,
      description="骨骼层级深度不得超过规范上限", dcc=_ALL_DCC,
      plugin_id=PLUGIN_ID)
def max_depth(ctx: RuleContext) -> List[Finding]:
    limit = _number(_section(ctx, "skeleton").get("max_depth"))
    if limit is None:
        return []
    deepest_name, deepest = "", -1
    for name, data in _joints(ctx).items():
        depth = _number(data.get("depth"))
        if depth is not None and depth > deepest:
            deepest_name, deepest = name, depth
    if deepest > limit:
        return [_finding(
            "skeleton.max_depth", Severity.ERROR,
            "层级深度 %g 超过上限 %g（节点 %r）" % (deepest, limit, deepest_name),
            node=deepest_name,
            fix_hint="压平中间辅助骨骼或合并末端关节",
            depth=deepest, max_depth=limit)]
    return []


@rule("skeleton.negative_scale", severity=Severity.ERROR,
      description="规范禁止负缩放，导出会产生镜像翻转", dcc=_ALL_DCC,
      fix_available=True, plugin_id=PLUGIN_ID)
def negative_scale(ctx: RuleContext) -> List[Finding]:
    skeleton = _section(ctx, "skeleton")
    if skeleton.get("allow_negative_scale", True):
        return []
    findings: List[Finding] = []
    for name, data in _joints(ctx).items():
        scale = _numbers(data.get("scale"))
        if any(value < 0 for value in scale):
            findings.append(_finding(
                "skeleton.negative_scale", Severity.ERROR,
                "关节 %r 存在负缩放 %s" % (name, scale), node=name,
                fix_hint="冻结变换并把缩放归正到 1，再检查镜像关节",
                fix={"op": "reset_scale", "node": name, "scale": [abs(v) for v in scale]},
                scale=scale))
    return findings


@rule("skeleton.unfrozen_transform", severity=Severity.ERROR,
      description="规范要求导出前冻结旋转等通道", dcc=_ALL_DCC,
      fix_available=True, plugin_id=PLUGIN_ID)
def unfrozen_transform(ctx: RuleContext) -> List[Finding]:
    if not _section(ctx, "skeleton").get("allow_frozen_transforms_only", False):
        return []
    findings: List[Finding] = []
    for name, data in _joints(ctx).items():
        rotate = _numbers(data.get("rotate"))
        if any(abs(value) > 0.0001 for value in rotate):
            findings.append(_finding(
                "skeleton.unfrozen_transform", Severity.ERROR,
                "关节 %r 旋转通道未冻结: %s" % (name, rotate), node=name,
                fix_hint="对骨架执行 freeze transformations 后再导出",
                fix={"op": "freeze_transforms", "node": name},
                rotate=rotate))
    return findings


@rule("skeleton.joint_orient", severity=Severity.WARNING,
      description="关节朝向矩阵需正交归一，偏差不得超过 joint_orient_tolerance_deg",
      dcc=_ALL_DCC, plugin_id=PLUGIN_ID)
def joint_orient(ctx: RuleContext) -> List[Finding]:
    tolerance_deg = _number(_section(ctx, "skeleton").get("joint_orient_tolerance_deg"))
    tolerance = max(math.sin(math.radians(tolerance_deg)), 1e-3) if tolerance_deg else 1e-3
    findings: List[Finding] = []
    for name, data in _joints(ctx).items():
        matrix = _numbers(data.get("matrix"))
        if len(matrix) != 16:
            continue
        axes = [matrix[0:3], matrix[4:7], matrix[8:11]]
        deviation = 0.0
        for axis in axes:
            deviation = max(deviation, abs(math.sqrt(sum(v * v for v in axis)) - 1.0))
        for i in range(3):
            for k in range(i + 1, 3):
                deviation = max(deviation, abs(sum(a * b for a, b in zip(axes[i], axes[k]))))
        if deviation > tolerance:
            findings.append(_finding(
                "skeleton.joint_orient", Severity.WARNING,
                "关节 %r 朝向矩阵偏差 %.5f 超出容差 %.5f" % (name, deviation, tolerance),
                node=name,
                fix_hint="重新 orient joint，确保轴向正交且无缩放残留",
                deviation=round(deviation, 6), tolerance=round(tolerance, 6),
                tolerance_deg=tolerance_deg or 0.0))
    return findings


@rule("skeleton.parent_integrity", severity=Severity.ERROR,
      description="关节父级必须存在、无环，且 depth 与父级一致", dcc=_ALL_DCC,
      plugin_id=PLUGIN_ID)
def parent_integrity(ctx: RuleContext) -> List[Finding]:
    joints = _joints(ctx)
    findings: List[Finding] = []
    for name, data in joints.items():
        parent = data.get("parent")
        if parent is None:
            continue
        parent = str(parent)
        if parent not in joints:
            findings.append(_finding(
                "skeleton.parent_integrity", Severity.ERROR,
                "关节 %r 的父级 %r 不在骨架中" % (name, parent), node=name,
                fix_hint="修正 parent 或补回被误删的父关节",
                parent=parent))
            continue
        child_depth = _number(data.get("depth"))
        parent_depth = _number(joints[parent].get("depth"))
        if child_depth is not None and parent_depth is not None \
                and child_depth != parent_depth + 1:
            findings.append(_finding(
                "skeleton.parent_integrity", Severity.ERROR,
                "关节 %r 的 depth=%g 与父级 %r 的 depth=%g 不连续"
                % (name, child_depth, parent, parent_depth), node=name,
                fix_hint="按实际层级重算 depth，或修正 parent 指向",
                depth=child_depth, parent_depth=parent_depth, parent=parent))
    for name in joints:
        seen, current = set(), name
        while current in joints:
            if current in seen:
                findings.append(_finding(
                    "skeleton.parent_integrity", Severity.ERROR,
                    "关节 %r 存在父子环" % name, node=name,
                    fix_hint="断开循环的 parent 引用"))
                break
            seen.add(current)
            parent = joints[current].get("parent")
            current = str(parent) if parent is not None else ""
    return findings


# --------------------------------------------------------------------------
# 蒙皮规则
# --------------------------------------------------------------------------
@rule("skin.max_influences", severity=Severity.ERROR,
      description="单顶点影响骨数量不得超过规范上限", dcc=_AUTHORING_DCC,
      fix_available=True, plugin_id=PLUGIN_ID)
def max_influences(ctx: RuleContext) -> List[Finding]:
    limit = _number(_section(ctx, "skin").get("max_influences"))
    skin = _snapshot(ctx).get("skin")
    found = _number(skin.get("max_influences_found")) if isinstance(skin, dict) else None
    if limit is None or found is None or found <= limit:
        return []
    return [_finding(
        "skin.max_influences", Severity.ERROR,
        "单顶点最大影响骨数 %g 超过上限 %g" % (found, limit),
        fix_hint="对蒙皮执行 limit influences 到 %g 根并重新归一" % limit,
        fix={"op": "limit_influences", "max": limit, "found": found},
        found=found, max_influences=limit)]


@rule("skin.weight_sum", severity=Severity.ERROR,
      description="顶点权重和偏差必须落在规范容差内", dcc=_AUTHORING_DCC,
      fix_available=True, plugin_id=PLUGIN_ID)
def weight_sum(ctx: RuleContext) -> List[Finding]:
    tolerance = _number(_section(ctx, "skin").get("weight_sum_tolerance"))
    skin = _snapshot(ctx).get("skin")
    deviation = _number(skin.get("weight_sum_deviation")) if isinstance(skin, dict) else None
    if tolerance is None or deviation is None or abs(deviation) <= tolerance:
        return []
    return [_finding(
        "skin.weight_sum", Severity.ERROR,
        "权重和偏差 %g 超出容差 %g" % (deviation, tolerance),
        fix_hint="执行 normalize weights，确保每顶点权重和为 1",
        fix={"op": "normalize_weights", "tolerance": tolerance},
        deviation=deviation, tolerance=tolerance)]


@rule("skin.unknown_deformer", severity=Severity.ERROR,
      description="只允许规范白名单内的 deformer 类型", dcc=_AUTHORING_DCC,
      plugin_id=PLUGIN_ID)
def unknown_deformer(ctx: RuleContext) -> List[Finding]:
    allowed = _section(ctx, "skin").get("allowed_deformers")
    if not isinstance(allowed, (list, tuple)):
        return []
    allowed = [str(item) for item in allowed]
    skin = _snapshot(ctx).get("skin")
    deformers = skin.get("deformers") if isinstance(skin, dict) else None
    if not isinstance(deformers, (list, tuple)):
        return []
    findings: List[Finding] = []
    for name in deformers:
        if str(name) not in allowed:
            findings.append(_finding(
                "skin.unknown_deformer", Severity.ERROR,
                "deformer %r 不在允许列表 %s 中" % (name, allowed), node=str(name),
                fix_hint="确认该 deformer 是否必要；必要时用 blendShape / skinCluster 替代",
                deformer=str(name), allowed=allowed))
    return findings


# --------------------------------------------------------------------------
# 导出规则
# --------------------------------------------------------------------------
def _fbx_spec(ctx: Any) -> Dict[str, Any]:
    export = _section(ctx, "export")
    fbx = export.get("fbx")
    return fbx if isinstance(fbx, dict) else {}


@rule("export.unit_mismatch", severity=Severity.ERROR,
      description="场景单位必须与导出规范一致", dcc=_ALL_DCC,
      fix_available=True, plugin_id=PLUGIN_ID)
def unit_mismatch(ctx: RuleContext) -> List[Finding]:
    expected = _fbx_spec(ctx).get("unit")
    actual = _snapshot(ctx).get("unit")
    if not isinstance(expected, str) or not isinstance(actual, str) or actual == expected:
        return []
    return [_finding(
        "export.unit_mismatch", Severity.ERROR,
        "场景单位 %r 与导出规范 %r 不一致" % (actual, expected),
        fix_hint="把场景 working unit 改为 %s 或调整导出缩放" % expected,
        fix={"op": "set_scene_unit", "value": expected},
        unit=actual, expected=expected)]


@rule("export.up_axis_mismatch", severity=Severity.ERROR,
      description="场景 up_axis 必须与导出规范一致", dcc=_ALL_DCC,
      fix_available=True, plugin_id=PLUGIN_ID)
def up_axis_mismatch(ctx: RuleContext) -> List[Finding]:
    expected = _fbx_spec(ctx).get("up_axis")
    actual = _snapshot(ctx).get("up_axis")
    if not isinstance(expected, str) or not isinstance(actual, str) or actual == expected:
        return []
    return [_finding(
        "export.up_axis_mismatch", Severity.ERROR,
        "场景 up_axis %r 与导出规范 %r 不一致" % (actual, expected),
        fix_hint="导出前把 up axis 切换为 %s" % expected,
        fix={"op": "set_up_axis", "value": expected},
        up_axis=actual, expected=expected)]


@rule("export.frame_range_mismatch", severity=Severity.ERROR,
      description="导出帧范围必须与规范一致", dcc=_ALL_DCC,
      fix_available=True, plugin_id=PLUGIN_ID)
def frame_range_mismatch(ctx: RuleContext) -> List[Finding]:
    expected = _numbers(_fbx_spec(ctx).get("frame_range"))
    actual = _numbers(_snapshot(ctx).get("frame_range"))
    if len(expected) != 2 or len(actual) != 2:
        return []
    if all(abs(a - b) <= 1e-6 for a, b in zip(actual, expected)):
        return []
    return [_finding(
        "export.frame_range_mismatch", Severity.ERROR,
        "导出帧范围 %s 与规范 %s 不一致" % (actual, expected),
        fix_hint="把时间轴范围设置为 %s 后再导出" % expected,
        fix={"op": "set_frame_range", "value": expected},
        frame_range=actual, expected=expected)]


@rule("dcc.version_unsupported", severity=Severity.WARNING,
      description="DCC 版本不得低于规范的最低版本", dcc=_ALL_DCC,
      plugin_id=PLUGIN_ID)
def version_unsupported(ctx: RuleContext) -> List[Finding]:
    minimums = _section(ctx, "dcc").get("min_versions")
    if not isinstance(minimums, dict):
        return []
    snapshot = _snapshot(ctx)
    dcc = snapshot.get("dcc") or getattr(ctx, "dcc", "")
    minimum = minimums.get(dcc)
    actual = snapshot.get("version")
    actual_key = _version_key(actual)
    minimum_key = _version_key(minimum)
    if actual_key is None or minimum_key is None or actual_key >= minimum_key:
        return []
    return [_finding(
        "dcc.version_unsupported", Severity.WARNING,
        "%s %s 低于规范要求的最低版本 %s" % (dcc, actual, minimum),
        fix_hint="升级宿主版本，或在低版本上限制使用相关功能",
        dcc=dcc, version=str(actual), minimum=str(minimum))]


BUILTIN_RULE_IDS: Sequence[str] = (
    "naming.joint_pattern", "naming.forbidden_token", "naming.namespace",
    "naming.side_tokens", "naming.case_collision", "naming.control_namespace",
    "skeleton.missing_required", "skeleton.max_depth", "skeleton.negative_scale",
    "skeleton.unfrozen_transform", "skeleton.joint_orient", "skeleton.parent_integrity",
    "skin.max_influences", "skin.weight_sum", "skin.unknown_deformer",
    "export.unit_mismatch", "export.up_axis_mismatch", "export.frame_range_mismatch",
    "dcc.version_unsupported",
)
