# -*- coding: utf-8 -*-
"""IK Retarget Guard 的六条确定性规则（全部只读、离线）。

数据从 ``ctx.options["retarget"]`` 或 ``ctx.snapshot["metadata"]["retarget"]``
读取，options 优先。插件专属数据不存在时每条规则立即返回 ``[]``；专属字段
缺失时只跳过对应检查，绝不产生 ERROR/BLOCKER。因此不含 retarget 字段的 40 个
golden fixtures 不会受到本插件影响。

所有规则显式声明 ``plugin_id="ik_retarget_guard"`` 与 ``dcc=["unreal"]``。
``fix_hint`` 提供人工可执行建议，``Finding.data["fix"]`` 提供严格 JSON 结构；
本文件不执行修复，也不直接调用 Unreal Python API。
"""
from __future__ import annotations

import math
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from tak.core.rules import Finding, Severity, rule

TOOL_ID = "ik_retarget_guard"
_DCC = ["unreal"]
_DEFAULT_OFFSET_TOLERANCE = 0.01
_DEFAULT_POSE_TOLERANCE = 0.01
_DEFAULT_REQUIRED_CHAINS = ("Spine", "LeftArm", "RightArm", "LeftLeg", "RightLeg")


# --------------------------------------------------------------------------
# 数据读取与共享助手
# --------------------------------------------------------------------------
def _snapshot(ctx: Any) -> Dict[str, Any]:
    snapshot = getattr(ctx, "snapshot", None)
    if hasattr(snapshot, "to_dict") and not isinstance(snapshot, dict):
        try:
            snapshot = snapshot.to_dict()
        except Exception:
            snapshot = {}
    return snapshot if isinstance(snapshot, dict) else {}


def _retarget(ctx: Any) -> Dict[str, Any]:
    options = getattr(ctx, "options", None)
    if isinstance(options, dict) and isinstance(options.get("retarget"), dict):
        return options["retarget"]
    metadata = _snapshot(ctx).get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("retarget"), dict):
        return metadata["retarget"]
    return {}


def _section(data: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    value = data.get(key)
    return value if isinstance(value, dict) else None


def _ue_spec(ctx: Any) -> Dict[str, Any]:
    spec = getattr(ctx, "spec", None)
    value = spec.get("ue") if isinstance(spec, dict) else None
    return value if isinstance(value, dict) else {}


def _strings(value: Any) -> List[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _vector(value: Any) -> Optional[List[float]]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    result = [_number(item) for item in value]
    if any(item is None for item in result):
        return None
    return [float(item) for item in result if item is not None]


def _finding(rule_id: str, message: str, node: str = "",
             severity: Severity = Severity.ERROR, fix_hint: str = "",
             fix: Optional[Dict[str, Any]] = None, **extra: Any) -> Finding:
    data = dict(extra)
    if fix is not None:
        data["fix"] = fix
    return Finding(rule_id=rule_id, severity=severity, message=message,
                   node=node, fix_hint=fix_hint, data=data)


def _mapping_module() -> Any:
    """按插件文件路径加载 mapping.py，不使用相对导入或修改 sys.path。"""
    name = "tak_plugin_%s_mapping" % TOOL_ID
    module = sys.modules.get(name)
    if module is not None:
        return module
    from tak.core import registry, runner
    return registry.load_plugin_module(runner.find_plugin(TOOL_ID), "mapping")


def _chain_entries(value: Any) -> List[Tuple[str, str]]:
    """读取 dict 或保留重复项的 entry 列表，供完整性与冲突规则共用。"""
    entries: List[Tuple[str, str]] = []
    if isinstance(value, dict):
        for source, target in value.items():
            if isinstance(source, str) and source.strip():
                entries.append((source.strip(), target.strip()
                                if isinstance(target, str) else ""))
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, dict):
                source, target = item.get("source"), item.get("target")
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                source, target = item
            else:
                continue
            if isinstance(source, str) and source.strip():
                entries.append((source.strip(), target.strip()
                                if isinstance(target, str) else ""))
    return entries


def _chain_names(rig: Optional[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    if rig is None or not isinstance(rig.get("chains"), (list, tuple)):
        return False, []
    names: List[str] = []
    for item in rig.get("chains") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return True, names


def _threshold(ctx: Any, data: Dict[str, Any], name: str, default: float) -> float:
    """阈值优先级：data.thresholds / data 直配 > spec.ue > 非负默认值。"""
    thresholds = data.get("thresholds")
    candidates: List[Any] = []
    if isinstance(thresholds, dict):
        candidates.append(thresholds.get(name))
    candidates.append(data.get(name + "_max"))
    candidates.append(_ue_spec(ctx).get("retarget_%s_tolerance" % name))
    for value in candidates:
        number = _number(value)
        if number is not None and number >= 0:
            return number
    return default


def _chain_key(name: str) -> str:
    compact = re.sub(r"[^a-z0-9]", "", name.lower())
    aliases = {
        "spine": "spine", "spine01": "spine", "spine1": "spine",
        "leftarm": "left_arm", "leftuparm": "left_arm", "larm": "left_arm",
        "rightarm": "right_arm", "rightuparm": "right_arm", "rarm": "right_arm",
        "leftleg": "left_leg", "leftupleg": "left_leg", "lleg": "left_leg",
        "rightleg": "right_leg", "rightupleg": "right_leg", "rleg": "right_leg",
    }
    return aliases.get(compact, re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"))


# --------------------------------------------------------------------------
# 1. IK Rig 链与 Retargeter 映射完整性（双向）
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".chain_mapping_completeness", severity=Severity.ERROR,
      description="IK Rig 链与 IK Retargeter chain_mapping 必须双向完整对应",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def chain_mapping_completeness(ctx: Any) -> List[Finding]:
    """有链无映射会断链，有映射无对应链会引用不存在的 Rig 链。"""
    data = _retarget(ctx)
    rig = _section(data, "ik_rig")
    retargeter = _section(data, "retargeter")
    chains_present, chain_names = _chain_names(rig)
    raw_mapping = retargeter.get("chain_mapping") if retargeter is not None else None
    if not chains_present and raw_mapping is None:
        return []
    entries = _chain_entries(raw_mapping)
    mapped_sources = {source for source, target in entries if target}
    known_chains = set(chain_names)
    findings: List[Finding] = []
    for name in sorted(set(chain_names)):
        if name in mapped_sources:
            continue
        findings.append(_finding(
            TOOL_ID + ".chain_mapping_completeness",
            "IK Rig 链 %r 缺少有效的 Retargeter chain_mapping" % name, node=name,
            fix_hint="在 IK Retargeter 中为该源链选择对应目标链，再重跑姿态校验",
            fix={"op": "set_chain_mapping",
                 "params": {"source_chain": name, "target_chain": name}},
            source_chain=name, issue="missing_mapping"))
    for source, target in entries:
        if source not in known_chains:
            findings.append(_finding(
                TOOL_ID + ".chain_mapping_completeness",
                "chain_mapping 引用了 IK Rig 中不存在的源链 %r" % source, node=source,
                fix_hint="移除失效映射，或在 IK Rig 中补建同名链后重新校验",
                fix={"op": "remove_chain_mapping", "params": {"source_chain": source}},
                source_chain=source, target_chain=target, issue="unknown_source_chain"))
    return findings


# --------------------------------------------------------------------------
# 2. Source / Target 骨骼命名集合差异
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".bone_naming_mismatch", severity=Severity.ERROR,
      description="source_bones 与 target_bones 集合差异必须有明确映射候选",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def bone_naming_mismatch(ctx: Any) -> List[Finding]:
    """命名体系不同可给候选；词典无法对齐的骨骼必须人工处理。"""
    data = _retarget(ctx)
    if not isinstance(data.get("source_bones"), (list, tuple)) \
            or not isinstance(data.get("target_bones"), (list, tuple)):
        return []
    sources = set(_strings(data.get("source_bones")))
    targets = set(_strings(data.get("target_bones")))
    source_only = sorted(sources - targets)
    target_only = sorted(targets - sources)
    if not source_only and not target_only:
        return []
    mapping = _mapping_module().suggest_mapping(sorted(sources), sorted(targets))
    candidates = {item["source"]: item for item in mapping["entries"]}
    findings: List[Finding] = []
    for source in source_only:
        candidate = candidates.get(source)
        if candidate is not None and candidate["target"] in target_only:
            findings.append(_finding(
                TOOL_ID + ".bone_naming_mismatch",
                "骨骼命名不一致：%r 可映射到 %r（置信度 %.2f）" % (
                    source, candidate["target"], candidate["confidence"]),
                node=source, severity=Severity.WARNING,
                fix_hint="由人工确认候选后写入 Skeleton Mapper / IK Retargeter，不要直接重命名资产",
                fix={"op": "set_bone_mapping", "params": dict(candidate)},
                source=source, candidate=dict(candidate)))
        else:
            findings.append(_finding(
                TOOL_ID + ".bone_naming_mismatch",
                "源骨骼 %r 在目标骨架中没有可靠的词典映射候选" % source,
                node=source,
                fix_hint="人工指定目标骨骼；不要根据模糊字符串自动绑定",
                fix={"op": "review_bone_mapping", "params": {"source": source}},
                source=source))
    mapped_targets = {item["target"] for item in candidates.values()}
    for target in target_only:
        if target in mapped_targets:
            continue
        findings.append(_finding(
            TOOL_ID + ".bone_naming_mismatch",
            "目标骨骼 %r 没有对应的源骨骼映射" % target, node=target,
            severity=Severity.WARNING,
            fix_hint="确认该骨骼是否为辅助骨；若参与重定向，请人工补充 source 映射",
            fix={"op": "review_bone_mapping", "params": {"target": target}},
            target=target))
    return findings


# --------------------------------------------------------------------------
# 3. Root / Pelvis 偏移阈值
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".root_pelvis_offset", severity=Severity.ERROR,
      description="Root 与 Pelvis 重定向偏移向量长度不得超过配置阈值",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def root_pelvis_offset(ctx: Any) -> List[Finding]:
    """根骨或骨盆偏移过大通常意味着比例、单位或 Retarget Root 配置错误。"""
    data = _retarget(ctx)
    retargeter = _section(data, "retargeter")
    if retargeter is None:
        return []
    findings: List[Finding] = []
    for field in ("root_offset", "pelvis_offset"):
        if field not in retargeter:
            continue
        vector = _vector(retargeter.get(field))
        if vector is None:
            continue
        magnitude = math.sqrt(sum(item * item for item in vector))
        tolerance = _threshold(ctx, data, field, _DEFAULT_OFFSET_TOLERANCE)
        if magnitude <= tolerance:
            continue
        findings.append(_finding(
            TOOL_ID + ".root_pelvis_offset",
            "%s 的向量长度 %.6g 超过阈值 %.6g" % (field, magnitude, tolerance),
            node="root" if field == "root_offset" else "pelvis",
            fix_hint="检查单位、骨架比例与 Retarget Root；将补偿偏移重置后重新烘焙预览",
            fix={"op": "set_retarget_offset",
                 "params": {"field": field, "value": [0.0, 0.0, 0.0]}},
            field=field, offset=vector, magnitude=magnitude, tolerance=tolerance))
    return findings


# --------------------------------------------------------------------------
# 4. 重定向后姿态偏差
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".pose_deviation", severity=Severity.ERROR,
      description="重定向后全局与逐骨骼姿态偏差不得超过配置容差",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def pose_deviation(ctx: Any) -> List[Finding]:
    """max 是全局最大实测偏差；per_bone 是各骨骼实测偏差而非阈值。"""
    data = _retarget(ctx)
    pose = _section(data, "pose_deviation")
    if pose is None:
        return []
    tolerance = _threshold(ctx, data, "pose_deviation", _DEFAULT_POSE_TOLERANCE)
    findings: List[Finding] = []
    maximum = _number(pose.get("max"))
    if maximum is not None and abs(maximum) > tolerance:
        findings.append(_finding(
            TOOL_ID + ".pose_deviation",
            "重定向后全局姿态偏差 %.6g 超过容差 %.6g" % (abs(maximum), tolerance),
            fix_hint="重新匹配参考姿态与链朝向，烘焙后重新采样 pose_deviation.max",
            fix={"op": "recheck_retarget_pose", "params": {"scope": "global"}},
            scope="global", deviation=abs(maximum), tolerance=tolerance))

    per_bone = pose.get("per_bone")
    if isinstance(per_bone, dict):
        data_thresholds = data.get("thresholds")
        data_per_bone = data_thresholds.get("pose_per_bone") \
            if isinstance(data_thresholds, dict) else None
        override_section = data.get("pose_deviation_thresholds")
        override_per_bone = override_section.get("per_bone") \
            if isinstance(override_section, dict) else None
        spec_per_bone = _ue_spec(ctx).get("retarget_pose_per_bone_tolerances")
        for bone in sorted(per_bone):
            deviation = _number(per_bone[bone])
            if deviation is None:
                continue
            bone_tolerance = tolerance
            for overrides in (data_per_bone, override_per_bone, spec_per_bone):
                if not isinstance(overrides, dict) or bone not in overrides:
                    continue
                candidate = _number(overrides.get(bone))
                if candidate is not None and candidate >= 0:
                    bone_tolerance = candidate
                    break
            if abs(deviation) <= bone_tolerance:
                continue
            findings.append(_finding(
                TOOL_ID + ".pose_deviation",
                "骨骼 %r 的重定向姿态偏差 %.6g 超过容差 %.6g" % (
                    bone, abs(deviation), bone_tolerance),
                node=bone,
                fix_hint="核对该骨骼的链映射、局部旋转与参考姿态，再重跑偏差采样",
                fix={"op": "recheck_retarget_pose",
                     "params": {"scope": "bone", "bone": bone}},
                scope="bone", bone=bone, deviation=abs(deviation),
                tolerance=bone_tolerance))
    return findings


# --------------------------------------------------------------------------
# 5. 必需 IK 链
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".required_chains", severity=Severity.ERROR,
      description="IK Rig 必须包含脊柱、左右臂与左右腿等必需链",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def required_chains(ctx: Any) -> List[Finding]:
    """默认可由 data.required_chains 或 spec.ue.retarget_required_chains 覆盖。"""
    data = _retarget(ctx)
    rig = _section(data, "ik_rig")
    present, names = _chain_names(rig)
    if not present:
        return []
    configured = data.get("required_chains")
    if not isinstance(configured, (list, tuple)):
        configured = _ue_spec(ctx).get("retarget_required_chains")
    if not isinstance(configured, (list, tuple)):
        configured = list(_DEFAULT_REQUIRED_CHAINS)
    required = _strings(configured)
    actual = {_chain_key(name) for name in names}
    findings: List[Finding] = []
    for name in required:
        if _chain_key(name) in actual:
            continue
        findings.append(_finding(
            TOOL_ID + ".required_chains", "IK Rig 缺少必需链 %r" % name, node=name,
            fix_hint="在 IK Rig 中补建该链并指定起止骨骼，再配置 Retargeter 链映射",
            fix={"op": "create_ik_chain", "params": {"chain": name}},
            required_chain=name, present_chains=sorted(set(names))))
    return findings


# --------------------------------------------------------------------------
# 6. 重复 / 冲突映射
# --------------------------------------------------------------------------
@rule(TOOL_ID + ".mapping_conflicts", severity=Severity.ERROR,
      description="链映射不得重复或共用目标，骨骼候选冲突必须显式复核",
      dcc=_DCC, fix_available=True, plugin_id=TOOL_ID)
def mapping_conflicts(ctx: Any) -> List[Finding]:
    """冲突项不能静默覆盖；dict 无法表达的重复 source 可用 entry 列表采集。"""
    data = _retarget(ctx)
    retargeter = _section(data, "retargeter")
    raw_mapping = retargeter.get("chain_mapping") if retargeter is not None else None
    entries = _chain_entries(raw_mapping)
    findings: List[Finding] = []
    by_source: Dict[str, List[str]] = {}
    by_target: Dict[str, List[str]] = {}
    for source, target in entries:
        by_source.setdefault(source, []).append(target)
        if target:
            by_target.setdefault(target, []).append(source)
    for source in sorted(by_source):
        targets = by_source[source]
        if len(targets) <= 1:
            continue
        findings.append(_finding(
            TOOL_ID + ".mapping_conflicts",
            "源链 %r 存在 %d 条重复/冲突映射" % (source, len(targets)), node=source,
            fix_hint="只保留一条经人工确认的 source→target 链映射",
            fix={"op": "resolve_chain_conflict", "params": {"source_chain": source}},
            conflict_type="duplicate_source", source_chain=source, targets=targets))
    for target in sorted(by_target):
        sources = sorted(set(by_target[target]))
        if len(by_target[target]) <= 1 and len(sources) <= 1:
            continue
        if len(sources) <= 1:
            continue  # 同一 source 的重复已由上一循环报告
        findings.append(_finding(
            TOOL_ID + ".mapping_conflicts",
            "目标链 %r 同时被多个源链占用：%s" % (target, "、".join(sources)), node=target,
            fix_hint="为每个源链选择唯一目标链，避免后写入的映射覆盖先写入项",
            fix={"op": "resolve_chain_conflict", "params": {"target_chain": target}},
            conflict_type="shared_target", target_chain=target, sources=sources))

    if isinstance(data.get("source_bones"), (list, tuple)) \
            and isinstance(data.get("target_bones"), (list, tuple)):
        suggestion = _mapping_module().suggest_mapping(
            data.get("source_bones"), data.get("target_bones"))
        for conflict in suggestion["conflicts"]:
            findings.append(_finding(
                TOOL_ID + ".mapping_conflicts",
                "骨骼候选映射冲突：%s" % conflict["message"],
                node=str(conflict.get("source") or conflict.get("target") or ""),
                fix_hint="在 mapping asset 的 conflicts 中人工选择唯一目标，未解决前不得自动写入",
                fix={"op": "resolve_bone_conflict",
                     "params": {"canonical": conflict.get("canonical", "")}},
                conflict_type=conflict.get("type"), conflict=dict(conflict)))
    return findings


RULE_IDS = (
    TOOL_ID + ".chain_mapping_completeness",
    TOOL_ID + ".bone_naming_mismatch",
    TOOL_ID + ".root_pelvis_offset",
    TOOL_ID + ".pose_deviation",
    TOOL_ID + ".required_chains",
    TOOL_ID + ".mapping_conflicts",
)
