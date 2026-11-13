# -*- coding: utf-8 -*-
"""P5.2 Skeleton Mapper 的骨骼语义纯函数层。

本模块只依赖 Python 标准库，不导入任何 DCC API、不导入任何第三方包，也不访问
网络、时间或随机数。所有判定都是确定性的：相同输入永远得到逐字节一致的输出。

职责边界
========
- ``normalize_bone`` / ``SEMANTIC_GROUPS`` / ``semantic_of`` / ``confidence_of``：
  命名归一化与语义词典。
- ``deterministic_pairs``：按规范名分组给出一对一候选，并把一对多 / 多对一
  / 多对多碰撞显式标为 ``status="conflict"``，绝不自动挑选。
- ``validate_mapping``：确定性校验（源/目标存在性、目标唯一、语义一致、
  置信度范围、必填链 root/pelvis 覆盖），返回可复现的结构化结果。
- ``merge_candidates``：合并 AI 候选，只允许补词典未覆盖项；AI 与词典重叠
  一律进人工复核队列，不覆盖高置信词典结果。
- ``parse_ai_candidates``：容错解析 AI 文本，非法输出返回空列表并保留 error
  说明，绝不抛异常。

字段约定
========
每条候选 entry 是一个字典，固定包含::

    {
      "source": "upperarm_L",       # 源骨骼名（与 mapping asset v1.0 一致）
      "target": "LeftUpArm",        # 目标骨骼名（与 mapping asset v1.0 一致）
      "semantic": "arm",            # SEMANTIC_GROUPS 的语义组名
      "confidence": 0.95,           # 0..1 确定性评分
      "method": "normalized",       # v1.0 方法：exact/normalized/dictionary/ai/asset
      "origin": "dictionary",       # 来源类别：dictionary/ai/asset
      "provider": None,             # AI provider 名（非 AI 为 None）
      "status": "auto"              # auto/needs_review/conflict
    }

注意：``source`` 是映射对的源骨骼名；「来源」信息放在 ``origin`` / ``method``
/ ``provider`` 上，以便与 ``ik_retarget_guard`` 的映射资产 v1.0 格式保持兼容
（该格式的 entry 键正是 source/target/confidence/method）。
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Optional, Tuple

# 解析 AI 文本失败时的说明；每次调用 parse_ai_candidates* 都会刷新。
LAST_PARSE_ERROR: Optional[str] = None

# --------------------------------------------------------------------------- #
# 语义词典
# --------------------------------------------------------------------------- #
# 结构：{语义组名: {归一化别名: 规范基础名}}。
# 同一别名只属于一个语义组；规范基础名本身也必须作为键出现。
# 覆盖 UE Mannequin、Mixamo 与 3ds Max Biped 的常见命名。
SEMANTIC_GROUPS: Dict[str, Dict[str, str]] = {
    "root": {
        "root": "root", "reference": "root", "root_bone": "root",
        "rootbone": "root", "root_jnt": "root", "root_motion": "root",
        "master": "root", "world": "root", "global": "root",
    },
    "pelvis": {
        "pelvis": "pelvis", "hips": "pelvis", "hip": "pelvis",
        "pelvis_bone": "pelvis", "hip_bone": "pelvis", "pelvis_jnt": "pelvis",
        "hips_jnt": "pelvis", "hips_root": "pelvis",
    },
    "spine": {
        "spine_01": "spine_01", "spine": "spine_01",
        "spine_02": "spine_02", "spine_03": "spine_03",
        "spine_04": "spine_04", "spine_05": "spine_05",
        "spine_06": "spine_06",
        "spine_1": "spine_01", "spine_2": "spine_02",
        "spine_3": "spine_03", "spine_4": "spine_04",
        "spine_5": "spine_05", "spine_6": "spine_06",
        "lowerback": "spine_01", "lower_back": "spine_01",
        "spine_a": "spine_01", "spine_b": "spine_01",
    },
    "chest": {
        "chest": "chest", "upper_chest": "chest", "upperchest": "chest",
        "upper_chest_bone": "chest", "chest_bone": "chest",
        "ribcage": "chest", "thorax": "chest", "spine_chest": "chest",
    },
    "neck": {
        "neck": "neck", "neck_01": "neck", "neck_02": "neck",
        "neck_1": "neck", "neck_2": "neck", "neck_bone": "neck",
    },
    "head": {
        "head": "head", "head_01": "head", "head_02": "head",
        "head_1": "head", "head_2": "head", "skull": "head",
        "head_bone": "head", "head_jnt": "head",
    },
    "clavicle": {
        "clavicle": "clavicle", "shoulder": "clavicle",
        "clavicle_bone": "clavicle", "collar": "clavicle",
        "collarbone": "clavicle", "shoulder_bone": "clavicle",
    },
    "arm": {
        "upperarm": "upperarm", "upper_arm": "upperarm",
        "up_arm": "upperarm", "uparm": "upperarm", "arm": "upperarm",
        "humerus": "upperarm", "upperarm_bone": "upperarm",
        "upperarm_jnt": "upperarm", "upper_arm_bone": "upperarm",
    },
    "forearm": {
        "lowerarm": "lowerarm", "lower_arm": "lowerarm",
        "low_arm": "lowerarm", "lowarm": "lowerarm", "forearm": "lowerarm",
        "fore_arm": "lowerarm", "radius": "lowerarm", "ulna": "lowerarm",
        "lowerarm_bone": "lowerarm", "lowerarm_jnt": "lowerarm",
    },
    "hand": {
        "hand": "hand", "wrist": "hand", "palm": "hand",
        "hand_bone": "hand", "hand_jnt": "hand", "wrist_bone": "hand",
    },
    "leg": {
        "thigh": "thigh", "upper_leg": "thigh", "up_leg": "thigh",
        "upleg": "thigh", "upperleg": "thigh", "femur": "thigh",
        "thigh_bone": "thigh", "thigh_jnt": "thigh",
    },
    "calf": {
        "calf": "calf", "lower_leg": "calf", "low_leg": "calf",
        "lowleg": "calf", "lowerleg": "calf", "leg": "calf",
        "shin": "calf", "tibia": "calf", "calf_bone": "calf",
        "calf_jnt": "calf",
    },
    "foot": {
        "foot": "foot", "ankle": "foot", "foot_bone": "foot",
        "foot_jnt": "foot", "ankle_bone": "foot",
    },
    "toe": {
        "toe": "toe", "toes": "toe", "ball": "toe", "toe_base": "toe",
        "toebase": "toe", "toe_bone": "toe", "toe_01": "toe",
        "toe_02": "toe", "ball_bone": "toe",
    },
    "finger": {
        "index_01": "index_01", "index_02": "index_02", "index_03": "index_03",
        "index": "index_01", "index_1": "index_01", "index_2": "index_02",
        "index_3": "index_03",
        "middle_01": "middle_01", "middle_02": "middle_02",
        "middle_03": "middle_03", "middle": "middle_01",
        "middle_1": "middle_01", "middle_2": "middle_02",
        "middle_3": "middle_03",
        "ring_01": "ring_01", "ring_02": "ring_02", "ring_03": "ring_03",
        "ring": "ring_01", "ring_1": "ring_01", "ring_2": "ring_02",
        "ring_3": "ring_03",
        "pinky_01": "pinky_01", "pinky_02": "pinky_02",
        "pinky_03": "pinky_03", "pinky": "pinky_01", "little": "pinky_01",
        "pinky_1": "pinky_01", "pinky_2": "pinky_02", "pinky_3": "pinky_03",
        "thumb_01": "thumb_01", "thumb_02": "thumb_02", "thumb_03": "thumb_03",
        "thumb": "thumb_01", "thumb_1": "thumb_01", "thumb_2": "thumb_02",
        "thumb_3": "thumb_03",
    },
}

# 扁平反向索引：别名 -> (语义组, 规范基础名)。模块加载时构建一次，纯数据。
_ALIAS_TO_GROUP: Dict[str, str] = {}
_ALIAS_TO_CANON: Dict[str, str] = {}
for _group, _aliases in SEMANTIC_GROUPS.items():
    for _alias, _canonical in _aliases.items():
        _ALIAS_TO_GROUP[_alias] = _group
        _ALIAS_TO_CANON[_alias] = _canonical

# 命名空间 / 驼峰 / 侧别 / 前后缀词表。
_PREFIXES = {
    "armature", "bip", "bip01", "bip001", "biped", "mixamorig",
    "mixamorig1", "mixamo", "rig", "skeleton", "sk", "val", "char",
    "character", "def", "rootmotion",
}
_SUFFIXES = {"jnt", "joint", "bone", "bnd", "bind", "geo"}
_LEFT = {"l", "left", "lt", "lf", "lft"}
_RIGHT = {"r", "right", "rt", "rf", "rgt"}
_NUM_STEMS = ("spine", "neck", "head", "chest", "thumb", "index",
              "middle", "ring", "pinky", "toe")
_NUM_RE = "(%s)" % "|".join(_NUM_STEMS)
_SIDE_SUFFIXES = ("_left", "_right")


# --------------------------------------------------------------------------- #
# 命名归一化
# --------------------------------------------------------------------------- #
def _tokens(name: Any) -> List[str]:
    """拆分命名空间、分隔符与驼峰，返回小写 token 列表。"""
    if not isinstance(name, str) or not name.strip():
        return []
    text = name.strip()
    # 命名空间（mixamorig: / VAL: / CHAR_A: / |group|bone）取最后一段。
    if ":" in text:
        text = text.rsplit(":", 1)[-1]
    if "|" in text:
        text = text.rsplit("|", 1)[-1]
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return [item.lower() for item in re.split(r"[^A-Za-z0-9]+", text) if item]


def _strip_prefixes(tokens: List[str]) -> List[str]:
    result = list(tokens)
    while result:
        if len(result) >= 2 and result[0] == "bip" \
                and result[1] in {"01", "001", "1"}:
            result = result[2:]
            continue
        if result[0] in _PREFIXES:
            result = result[1:]
            continue
        break
    return result


def _strip_suffixes(tokens: List[str]) -> List[str]:
    result = list(tokens)
    while len(result) >= 2 and result[-1] in _SUFFIXES:
        result.pop()
    return result


def _normalize_numbered(base: str) -> str:
    """把 spine1 / spine_1 / thumb2 等收敛成 stem_%02d 规范形式。

    ``spine1``（Mixamo 无下划线写法）与 ``spine_02``（UE 第二节）对齐，
    因此对无下划线的 spine 数字额外 +1；其余带数字的骨保持原编号。
    """
    match = re.match(r"^%s_(\d+)$" % _NUM_RE, base)
    if match:
        return "%s_%02d" % (match.group(1), int(match.group(2)))
    match = re.match(r"^%s(\d+)$" % _NUM_RE, base)
    if match:
        number = int(match.group(2))
        if match.group(1) == "spine":
            number += 1
        return "%s_%02d" % (match.group(1), number)
    return base


def _canonical_base(tokens: List[str]) -> str:
    base = "_".join(tokens)
    if not base:
        return ""
    base = _normalize_numbered(base)
    # 未知基础名保留归一化后的原样，不臆造。
    return _ALIAS_TO_CANON.get(base, base)


def _extract_side(tokens: List[str]) -> Tuple[List[str], str]:
    """提取左右标记，统一为 left / right，返回 (剩余 token, 侧别)。"""
    result = list(tokens)
    side = ""
    if result and result[0] in _LEFT | _RIGHT:
        side = "left" if result[0] in _LEFT else "right"
        result = result[1:]
    if result and result[-1] in _LEFT | _RIGHT:
        token_side = "left" if result[-1] in _LEFT else "right"
        result = result[:-1]
        side = side or token_side
    # 兼容 upperarmL / upperarml 这类无分隔符写法：仅当去掉末字符后是词典中
    # 的已知骨时才认定侧别，避免误伤 spinal 等正常单词。
    if not side and result:
        last = result[-1]
        if len(last) > 2 and last[-1:] in {"l", "r"}:
            stem = last[:-1]
            trial = result[:-1] + [stem]
            if _canonical_base(trial) in _ALIAS_TO_GROUP:
                side = "left" if last[-1:] == "l" else "right"
                result = trial
    return result, side


def normalize_bone(name: Any) -> str:
    """归一化常见骨骼名，返回小写规范名（侧别统一为 ``_left`` / ``_right``）。

    处理大小写、``_`` / 空格 / 点 / 冒号 / 竖线分隔与驼峰，去掉 ``mixamorig:`` /
    ``Bip01`` / ``VAL:`` / ``Armature`` 等前后缀，并把 ``L`` / ``Left`` / ``_l``
    等左右标记统一为 ``left`` / ``right``。例如 ``upperarm_L``、``LeftUpArm``、
    ``Bip01 L UpperArm``、``mixamorig:LeftUpArm`` 都归一为 ``upperarm_left``。

    非字符串、空字符串返回 ``""``；无法识别的基础名保留原样，不臆造。
    """
    tokens = _strip_prefixes(_tokens(name))
    if not tokens:
        return ""
    tokens, side = _extract_side(tokens)
    tokens = _strip_suffixes(tokens)
    canonical = _canonical_base(tokens)
    if not canonical:
        return ""
    return canonical + ("_" + side if side else "")


def _strip_side(canonical: str) -> str:
    for suffix in _SIDE_SUFFIXES:
        if canonical.endswith(suffix):
            return canonical[: -len(suffix)]
    return canonical


def semantic_of(name: Any) -> str:
    """返回骨骼名所属的语义组（SEMANTIC_GROUPS 的键）；无法识别返回 ``""``。"""
    canonical = normalize_bone(name)
    if not canonical:
        return ""
    return _ALIAS_TO_GROUP.get(_strip_side(canonical), "")


def confidence_of(source: Any, target: Any) -> float:
    """确定性评分 0..1：比较源/目标骨名的归一化与语义一致性。

    - 原名完全相同 -> 1.0
    - 归一化后相同 -> 0.95
    - 同属一个语义组但规范名不同 -> 0.55（弱同源，仅作建议）
    - 其它 / 无法识别 -> 0.0
    """
    left = normalize_bone(source)
    right = normalize_bone(target)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0 if source == target else 0.95
    left_sem = semantic_of(source)
    right_sem = semantic_of(target)
    if left_sem and left_sem == right_sem:
        return 0.55
    return 0.0


# --------------------------------------------------------------------------- #
# 候选配对
# --------------------------------------------------------------------------- #
def _names(value: Any) -> List[str]:
    """过滤非法项、去重并排序，保证结果与输入顺序无关。"""
    if isinstance(value, dict):
        value = list(value.keys())
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    return sorted({item.strip() for item in value
                   if isinstance(item, str) and item.strip()})


def _method(source: str, target: str) -> str:
    if source == target:
        return "exact"
    if normalize_bone(source) == normalize_bone(target):
        return "normalized"
    return "dictionary"


def _entry(source: str, target: str, status: str, confidence: float) -> Dict[str, Any]:
    semantic = semantic_of(source) or semantic_of(target) or ""
    return {
        "source": source,
        "target": target,
        "semantic": semantic,
        "confidence": round(float(confidence), 4),
        "method": _method(source, target),
        "origin": "dictionary",
        "provider": None,
        "status": status,
    }


def deterministic_pairs(source_bones: Any, target_bones: Any) -> List[Dict[str, Any]]:
    """按规范名分组给出一对一候选，返回候选 entry 列表。

    一对多 / 多对一 / 多对多碰撞不会自动挑选：所有碰撞组合都作为
    ``status="conflict"`` 的 entry 保留（带 ``conflict_type``），供人工确认。
    相同输入始终得到完全相同的列表；集合顺序与字典插入顺序均不影响结果。
    """
    sources = _names(source_bones)
    targets = _names(target_bones)
    source_groups: Dict[str, List[str]] = {}
    target_groups: Dict[str, List[str]] = {}
    for name in sources:
        canonical = normalize_bone(name)
        if canonical:
            source_groups.setdefault(canonical, []).append(name)
    for name in targets:
        canonical = normalize_bone(name)
        if canonical:
            target_groups.setdefault(canonical, []).append(name)

    results: List[Dict[str, Any]] = []
    for canonical in sorted(set(source_groups) & set(target_groups)):
        left = sorted(source_groups[canonical])
        right = sorted(target_groups[canonical])
        if len(left) == 1 and len(right) == 1:
            results.append(_entry(left[0], right[0], "auto",
                                  confidence_of(left[0], right[0])))
            continue
        if len(left) == 1:
            kind = "one_source_many_targets"
        elif len(right) == 1:
            kind = "many_sources_one_target"
        else:
            kind = "many_to_many"
        for source in left:
            for target in right:
                item = _entry(source, target, "conflict",
                              confidence_of(source, target) * 0.5)
                item["conflict_type"] = kind
                item["canonical"] = canonical
                results.append(item)
    results.sort(key=lambda item: (item["source"], item["target"],
                                   item["semantic"], item["status"]))
    return results


# --------------------------------------------------------------------------- #
# 确定性校验
# --------------------------------------------------------------------------- #
def _finite_confidence(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    number = float(value)
    return math.isfinite(number) and 0.0 <= number <= 1.0


def validate_mapping(entries: Any, source_bones: Any,
                     target_bones: Any) -> Dict[str, Any]:
    """确定性校验候选映射，返回结构化结果。

    返回 ``{"ok", "errors", "warnings", "valid_entries", "conflicts", "stats"}``：

    - ``errors``：源/目标不存在、置信度非 0..1 有限数值、语义不一致等硬错误。
    - ``warnings``：低置信度、必填链 root/pelvis 未覆盖等软提示。
    - ``conflicts``：目标重复、源重复、显式 ``status="conflict"`` 的项；全部
      保留供人工确认，绝不自动裁决。
    - ``valid_entries``：通过硬校验且不属于任何冲突的干净一对一映射。
    """
    sources = set(_names(source_bones))
    targets = set(_names(target_bones))
    errors: List[str] = []
    warnings: List[str] = []
    conflicts: List[Dict[str, Any]] = []
    checked: List[Dict[str, Any]] = []

    for index, raw in enumerate(entries if isinstance(entries, (list, tuple)) else []):
        if not isinstance(raw, dict):
            errors.append("第 %d 条 entry 不是对象" % index)
            continue
        item = dict(raw)
        source, target = item.get("source"), item.get("target")
        invalid = False
        if not isinstance(source, str) or not source or source not in sources:
            errors.append("entry %r 的源骨骼不存在于 source_bones" % source)
            invalid = True
        if not isinstance(target, str) or not target or target not in targets:
            errors.append("entry %r 的目标骨骼不存在于 target_bones" % target)
            invalid = True
        confidence = item.get("confidence", 0.0)
        if not _finite_confidence(confidence):
            errors.append("entry %r -> %r 的 confidence 不是 0..1 有限数值"
                          % (source, target))
            invalid = True
            confidence = 0.0
        item["confidence"] = round(float(confidence), 4)
        semantic = item.get("semantic") or ""
        source_sem = semantic_of(source)
        target_sem = semantic_of(target)
        if not semantic:
            semantic = source_sem or target_sem or ""
        if semantic and (source_sem or target_sem) \
                and semantic not in (source_sem, target_sem):
            errors.append("entry %r -> %r 的语义 %r 与骨骼语义不一致"
                          % (source, target, semantic))
            invalid = True
        item["semantic"] = semantic
        item.setdefault("method", _method(str(source), str(target)))
        item.setdefault("origin", "dictionary")
        item.setdefault("provider", None)
        item.setdefault("status", "auto")
        item["_invalid"] = invalid
        checked.append(item)

    by_target: Dict[str, List[Dict[str, Any]]] = {}
    by_source: Dict[str, List[Dict[str, Any]]] = {}
    for item in checked:
        if item["_invalid"]:
            continue
        by_target.setdefault(item["target"], []).append(item)
        by_source.setdefault(item["source"], []).append(item)

    conflicted = set()
    for target in sorted(by_target):
        group = by_target[target]
        if len(group) > 1:
            conflicts.append({
                "type": "duplicate_target", "target": target,
                "sources": sorted(item["source"] for item in group),
                "message": "多个源骨骼同时命中目标骨骼 %r，需人工确认" % target,
            })
            conflicted.update(id(item) for item in group)
    for source in sorted(by_source):
        group = by_source[source]
        if len(group) > 1:
            conflicts.append({
                "type": "duplicate_source", "source": source,
                "targets": sorted(item["target"] for item in group),
                "message": "源骨骼 %r 同时命中多个目标骨骼，需人工确认" % source,
            })
            conflicted.update(id(item) for item in group)
    for item in checked:
        if item["status"] == "conflict" and not item["_invalid"] \
                and id(item) not in conflicted:
            conflicted.add(id(item))
            conflicts.append({
                "type": item.get("conflict_type") or "explicit_conflict",
                "canonical": item.get("canonical") or "",
                "source": item["source"], "target": item["target"],
                "message": "候选映射 %r -> %r 存在碰撞，需人工确认"
                           % (item["source"], item["target"]),
            })

    valid_entries: List[Dict[str, Any]] = []
    for item in checked:
        clean = {key: value for key, value in item.items()
                 if key not in ("_invalid", "conflict_type", "canonical")}
        if item["_invalid"] or id(item) in conflicted:
            continue
        if clean["confidence"] < 0.5:
            clean["status"] = "needs_review"
            warnings.append("候选 %r -> %r 置信度偏低，需人工确认"
                            % (clean["source"], clean["target"]))
        valid_entries.append(clean)
    valid_entries.sort(key=lambda item: (item["source"], item["target"],
                                         item["semantic"]))

    # 必填链 root / pelvis 覆盖检查：仅当骨架里确实存在该语义骨却未映射时提示。
    mapped_sources = {item["source"] for item in valid_entries}
    for required in ("root", "pelvis"):
        present = sorted(name for name in sources if semantic_of(name) == required)
        if present and not any(name in mapped_sources for name in present):
            warnings.append("必填链 %s 未覆盖（%s）"
                            % (required, ", ".join(present)))

    confidences = [item["confidence"] for item in valid_entries]
    stats = {
        "total": len(checked),
        "valid": len(valid_entries),
        "errors": len(errors),
        "warnings": len(warnings),
        "conflicts": len(conflicts),
        "average_confidence": (round(sum(confidences) / len(confidences), 4)
                               if confidences else 0.0),
        "covered_semantics": sorted({item["semantic"] for item in valid_entries
                                     if item["semantic"]}),
    }
    conflicts.sort(key=lambda item: (item["type"], str(item.get("canonical") or ""),
                                     str(item.get("source") or ""),
                                     str(item.get("target") or "")))
    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "valid_entries": valid_entries,
        "conflicts": conflicts,
        "stats": stats,
    }


# --------------------------------------------------------------------------- #
# 候选合并（AI 只补空位）
# --------------------------------------------------------------------------- #
def merge_candidates(dictionary_entries: Any, ai_entries: Any,
                     source_bones: Any, target_bones: Any) -> Dict[str, Any]:
    """合并词典与 AI 候选；AI 只能补词典未覆盖的项，冲突保留不自动裁决。

    返回 ``{"entries", "conflicts", "warnings"}``。每条结果都带 ``origin`` /
    ``method`` / ``provider`` 来源标注。AI 候选必须经确定性校验（源/目标存在、
    语义一致、置信度合法）才能进入 ``entries``；与词典高置信项重叠时保留词典
    结果并把分歧放进 ``conflicts`` 供人工确认，绝不覆盖、绝不自动选择。
    """
    sources = set(_names(source_bones))
    targets = set(_names(target_bones))
    result: List[Dict[str, Any]] = []
    warnings: List[str] = []
    conflicts: List[Dict[str, Any]] = []
    dictionary_sources = set()
    dictionary_targets = set()
    dictionary_by_source: Dict[str, str] = {}

    for raw in dictionary_entries if isinstance(dictionary_entries, (list, tuple)) else []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item.setdefault("origin", "dictionary")
        item.setdefault("method", _method(str(item.get("source") or ""),
                                          str(item.get("target") or "")))
        item.setdefault("provider", None)
        item.setdefault("status", "auto")
        result.append(item)
        source, target = item.get("source"), item.get("target")
        if isinstance(source, str):
            dictionary_sources.add(source)
            dictionary_by_source[source] = str(item.get("target") or "")
        if isinstance(target, str):
            dictionary_targets.add(target)

    for raw in ai_entries if isinstance(ai_entries, (list, tuple)) else []:
        if not isinstance(raw, dict):
            warnings.append("忽略一条非对象 AI 候选")
            continue
        item = dict(raw)
        source, target = item.get("source"), item.get("target")
        item["origin"] = "ai"
        item["method"] = "ai"
        item.setdefault("provider", None)
        item.setdefault("status", "needs_review")
        # 确定性校验：源/目标必须存在，语义必须一致，置信度必须落在 0..1。
        if source not in sources or target not in targets:
            warnings.append("AI 候选含未知骨骼，已丢弃: %r -> %r"
                            % (source, target))
            continue
        source_sem, target_sem = semantic_of(source), semantic_of(target)
        # 只有两端语义都已知且冲突时才丢弃；一端未知正是 AI 补空位的价值所在。
        if source_sem and target_sem and source_sem != target_sem:
            warnings.append("AI 候选语义冲突（%s != %s），已丢弃: %r -> %r"
                            % (source_sem, target_sem, source, target))
            continue
        confidence = item.get("confidence", 0.5)
        if not _finite_confidence(confidence):
            confidence = 0.5
        item["confidence"] = round(float(confidence), 4)
        item["semantic"] = item.get("semantic") or source_sem or target_sem or ""
        # 词典已覆盖的源：绝不覆盖高置信结果，分歧进人工复核队列。
        if source in dictionary_sources:
            conflicts.append({
                "type": "ai_overlaps_dictionary", "source": source,
                "dictionary_target": dictionary_by_source.get(source, ""),
                "ai_target": target,
                "message": "AI 候选与词典结果重叠，保留词典项，需人工确认",
            })
            continue
        if target in dictionary_targets:
            conflicts.append({
                "type": "ai_target_conflict", "source": source, "target": target,
                "message": "AI 候选目标已被词典占用，需人工确认",
            })
            continue
        result.append(item)

    result.sort(key=lambda item: (str(item.get("source") or ""),
                                  str(item.get("target") or "")))
    conflicts.sort(key=lambda item: (item["type"], str(item.get("source") or "")))
    return {"entries": result, "conflicts": conflicts, "warnings": warnings}


# --------------------------------------------------------------------------- #
# AI 文本容错解析
# --------------------------------------------------------------------------- #
def _items_from(data: Any) -> Optional[List[Any]]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("entries", "candidates", "pairs", "mapping"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return None


def _clean_ai_items(items: List[Any]) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        source, target = raw.get("source"), raw.get("target")
        if not (isinstance(source, str) and source.strip()
                and isinstance(target, str) and target.strip()):
            continue
        confidence = raw.get("confidence", 0.6)
        if not _finite_confidence(confidence):
            confidence = 0.6
        cleaned.append({
            "source": source.strip(), "target": target.strip(),
            "semantic": str(raw.get("semantic") or ""),
            "confidence": round(float(confidence), 4),
            "method": "ai", "origin": "ai",
            "provider": raw.get("provider"),
            "status": "needs_review",
        })
    cleaned.sort(key=lambda item: (item["source"], item["target"]))
    return cleaned


def parse_ai_candidates_with_error(text: Any) -> Dict[str, Any]:
    """容错解析 AI 文本，返回 ``{"entries": list, "error": str|None}``。

    支持 ```json 代码块、纯数组、``{"entries": [...]}``，以及 JSON 前后夹带
    说明文字的情况。任何非法输入都返回空列表 + error 说明，绝不抛异常。
    """
    global LAST_PARSE_ERROR
    LAST_PARSE_ERROR = None
    raw = text if isinstance(text, str) else ""
    candidates: List[str] = []
    for block in re.findall(r"```(?:json)?\s*(.*?)```", raw,
                            flags=re.IGNORECASE | re.DOTALL):
        candidates.append(block.strip())
    candidates.append(raw.strip())
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = raw.find(opener), raw.rfind(closer)
        if 0 <= start < end:
            candidates.append(raw[start:end + 1].strip())
    for candidate in candidates:
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        items = _items_from(data)
        if items is not None:
            return {"entries": _clean_ai_items(items), "error": None}
    LAST_PARSE_ERROR = "AI 输出无法解析为候选列表（非 JSON 或缺少 entries）"
    return {"entries": [], "error": LAST_PARSE_ERROR}


def parse_ai_candidates(text: Any) -> List[Dict[str, Any]]:
    """容错解析 AI 文本为候选 entry 列表；非法输出返回 ``[]``，绝不抛异常。

    error 说明可通过 ``LAST_PARSE_ERROR`` 或 ``parse_ai_candidates_with_error``
    读取，便于上层把降级原因写进 warnings。
    """
    return parse_ai_candidates_with_error(text)["entries"]
