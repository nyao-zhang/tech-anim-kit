# -*- coding: utf-8 -*-
"""离线骨骼命名归一化、候选映射与映射资产读写。

本模块不调用 AI、不访问网络，也不导入任何 DCC API。``suggest_mapping`` 只依据
内置词典和确定性的归一化规则给出候选；冲突项绝不自动选择，留给人工或 P5.2
Skeleton Mapper 复核。

映射资产契约（版本 1.0）
========================
``write_mapping_asset`` 写出 UTF-8 JSON，顶层字段固定为::

    {
      "version": "1.0",
      "generated_by": "ik_retarget_guard",
      "entries": [
        {"source": "upperarm_L", "target": "LeftUpArm",
         "confidence": 0.85, "method": "dictionary"}
      ],
      "conflicts": [
        {"type": "one_source_many_targets", "canonical": "upperarm_l",
         "sources": ["upperarm_L"], "targets": ["LeftUpArm", "upperarm_l"],
         "message": "..."}
      ],
      "metadata": {
        "unmapped_source": [], "unmapped_target": [],
        "consumers": ["P5.2 Skeleton Mapper", "UE IK Retargeter",
                      "MotionBuilder Characterization", "3ds Max Biped"]
      }
    }

资产生成不写时间戳，因此同一输入产生逐字节一致的 JSON。P5.2 可直接读取
``entries`` 写入 UE IK Retargeter，也可翻译到 MoBu Characterization 或 Max
Biped；``conflicts`` 与 ``metadata.unmapped_*`` 是必须保留的人工复核队列。
"""
from __future__ import annotations

import json
import math
import os
import re
from typing import Any, Dict, List, Tuple

TOOL_ID = "ik_retarget_guard"
ASSET_VERSION = "1.0"
GENERATED_BY = TOOL_ID

_PREFIXES = {
    "armature", "bip", "bip01", "bip001", "biped", "mixamorig",
    "rig", "skeleton", "sk",
}
_LEFT = {"l", "left", "lt", "lf"}
_RIGHT = {"r", "right", "rt", "rf"}

# 无侧别的基础名 -> TAK 规范基础名。覆盖 UE、Mixamo、Biped 与常见 DCC 别名。
_BASE_ALIASES = {
    "root": "root", "root_bone": "root", "reference": "root",
    "pelvis": "pelvis", "hips": "pelvis", "hip": "pelvis",
    "spine": "spine_01", "spine_01": "spine_01", "spine_02": "spine_02",
    "spine_03": "spine_03", "spine_04": "spine_04", "spine_05": "spine_05",
    "neck": "neck", "neck_01": "neck", "head": "head", "head_01": "head",
    "clavicle": "clavicle", "shoulder": "clavicle", "clavicle_bone": "clavicle",
    "upperarm": "upperarm", "upper_arm": "upperarm", "up_arm": "upperarm",
    "uparm": "upperarm", "arm": "upperarm", "humerus": "upperarm",
    "lowerarm": "lowerarm", "lower_arm": "lowerarm", "forearm": "lowerarm",
    "fore_arm": "lowerarm", "lowarm": "lowerarm", "radius": "lowerarm",
    "hand": "hand", "wrist": "hand", "palm": "hand",
    "thigh": "thigh", "upper_leg": "thigh", "up_leg": "thigh",
    "upleg": "thigh", "femur": "thigh",
    "calf": "calf", "lower_leg": "calf", "low_leg": "calf",
    "leg": "calf", "shin": "calf", "tibia": "calf",
    "foot": "foot", "ankle": "foot", "foot_bone": "foot",
    "toe": "toe", "toes": "toe", "ball": "toe", "toe_base": "toe",
    "thumb": "thumb_01", "thumb_01": "thumb_01", "thumb_02": "thumb_02",
    "thumb_03": "thumb_03", "index": "index_01", "index_01": "index_01",
    "index_02": "index_02", "index_03": "index_03",
    "middle": "middle_01", "middle_01": "middle_01", "middle_02": "middle_02",
    "middle_03": "middle_03", "ring": "ring_01", "ring_01": "ring_01",
    "ring_02": "ring_02", "ring_03": "ring_03",
    "pinky": "pinky_01", "pinky_01": "pinky_01", "pinky_02": "pinky_02",
    "pinky_03": "pinky_03", "little": "pinky_01",
}


def _tokens(name: Any) -> List[str]:
    """拆分命名空间、分隔符与驼峰，返回小写 token。"""
    if not isinstance(name, str) or not name.strip():
        return []
    text = name.strip()
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return [item.lower() for item in re.split(r"[^A-Za-z0-9]+", text) if item]


def _strip_prefixes(tokens: List[str]) -> List[str]:
    result = list(tokens)
    while result:
        if len(result) >= 2 and result[0] == "bip" and result[1] in {"01", "001"}:
            result = result[2:]
            continue
        if result[0] in _PREFIXES:
            result = result[1:]
            continue
        break
    return result


def _canonical_base(tokens: List[str]) -> str:
    base = "_".join(tokens)
    if not base:
        return ""
    # UE 的 spine_01 与 Mixamo 的 Spine 对齐；Mixamo/Biped 的 Spine1 是第二节。
    match = re.match(r"^spine_(\d+)$", base)
    if match:
        return "spine_%02d" % int(match.group(1))
    match = re.match(r"^spine(\d+)$", base)
    if match:
        return "spine_%02d" % (int(match.group(1)) + 1)
    match = re.match(r"^(thumb|index|middle|ring|pinky)_?(\d+)$", base)
    if match:
        return "%s_%02d" % (match.group(1), int(match.group(2)))
    return _BASE_ALIASES.get(base, base)


def normalize_bone_name(name: Any) -> str:
    """归一化常见骨骼名，返回小写规范名（侧别统一为 ``_l`` / ``_r``）。

    规则包括：去 ``Bip01`` / ``mixamorig`` / ``Armature`` 等前缀、拆分下划线与
    驼峰、统一 ``Left`` / ``L`` / ``_l`` 等侧别，并应用离线语义词典。例如
    ``upperarm_L``、``LeftUpArm``、``Bip01 L UpperArm`` 都归一为 ``upperarm_l``。
    非字符串或空字符串返回 ``""``；无法识别的基础名保留归一化后的原样，不臆造。
    """
    tokens = _strip_prefixes(_tokens(name))
    if not tokens:
        return ""
    side = ""
    if tokens[0] in _LEFT | _RIGHT:
        side = "l" if tokens.pop(0) in _LEFT else "r"
    if tokens and tokens[-1] in _LEFT | _RIGHT:
        token_side = "l" if tokens[-1] in _LEFT else "r"
        tokens.pop()
        side = side or token_side
    # 兼容 upperarmL / upperarmR 这类无分隔符且全大写的写法；只有去掉末字符后
    # 是词典中的已知骨骼时才认定侧别，避免误伤 spinal 等正常单词。
    if not side and tokens:
        last = tokens[-1]
        if len(last) > 2 and last[-1:] in {"l", "r"}:
            stem = last[:-1]
            candidate = _canonical_base(tokens[:-1] + [stem])
            if candidate != "_".join(tokens[:-1] + [stem]) or stem in _BASE_ALIASES:
                side = last[-1:]
                tokens[-1] = stem
    canonical = _canonical_base(tokens)
    return canonical + ("_" + side if canonical and side else "")


def _names(value: Any) -> List[str]:
    """过滤非法项、去重并排序，保证结果与输入顺序无关。"""
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    return sorted({item.strip() for item in value
                   if isinstance(item, str) and item.strip()})


def _surface_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _match_method(source: str, target: str) -> Tuple[float, str]:
    if source == target:
        return 1.0, "exact"
    if source.lower() == target.lower() or _surface_key(source) == _surface_key(target):
        return 0.95, "normalized"
    return 0.85, "dictionary"


def suggest_mapping(source_bones: Any, target_bones: Any) -> Dict[str, Any]:
    """用离线词典生成一对一骨骼候选映射，并显式列出未映射项与冲突。

    输出包含 ``entries`` / ``unmapped_source`` / ``unmapped_target`` / ``conflicts``。
    一个 source 命中多个 target、多个 source 命中同一 target，以及多对多碰撞都会
    进入 ``conflicts``，相关骨骼不会被擅自写入 ``entries``。相同输入始终得到完全
    相同的字典；集合顺序、字典插入顺序均不影响结果。
    """
    sources = _names(source_bones)
    targets = _names(target_bones)
    source_groups: Dict[str, List[str]] = {}
    target_groups: Dict[str, List[str]] = {}
    for name in sources:
        canonical = normalize_bone_name(name)
        if canonical:
            source_groups.setdefault(canonical, []).append(name)
    for name in targets:
        canonical = normalize_bone_name(name)
        if canonical:
            target_groups.setdefault(canonical, []).append(name)

    entries: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    mapped_sources = set()
    mapped_targets = set()
    for canonical in sorted(set(source_groups) & set(target_groups)):
        left = sorted(source_groups[canonical])
        right = sorted(target_groups[canonical])
        if len(left) == 1 and len(right) == 1:
            confidence, method = _match_method(left[0], right[0])
            entries.append({"source": left[0], "target": right[0],
                            "confidence": confidence, "method": method})
            mapped_sources.add(left[0])
            mapped_targets.add(right[0])
            continue
        if len(left) == 1:
            kind = "one_source_many_targets"
            message = "源骨骼 %r 同时命中多个目标骨骼" % left[0]
        elif len(right) == 1:
            kind = "many_sources_one_target"
            message = "多个源骨骼同时命中目标骨骼 %r" % right[0]
        else:
            kind = "many_to_many"
            message = "规范名 %r 产生多对多映射冲突" % canonical
        conflicts.append({
            "type": kind, "canonical": canonical,
            "source": left[0] if len(left) == 1 else "",
            "sources": left,
            "target": right[0] if len(right) == 1 else "",
            "targets": right, "message": message,
        })

    entries.sort(key=lambda item: (item["source"], item["target"]))
    conflicts.sort(key=lambda item: (item["canonical"], item["type"],
                                     item["sources"], item["targets"]))
    return {
        "entries": entries,
        "unmapped_source": [name for name in sources if name not in mapped_sources],
        "unmapped_target": [name for name in targets if name not in mapped_targets],
        "conflicts": conflicts,
    }


def _asset_entries(mapping: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = mapping.get("entries")
    if not isinstance(raw, (list, tuple)):
        raise ValueError("mapping.entries 必须是列表")
    entries: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("mapping.entries 的每一项都必须是对象")
        source, target = item.get("source"), item.get("target")
        if not isinstance(source, str) or not source or not isinstance(target, str) or not target:
            raise ValueError("映射 entry 必须含非空 source / target")
        confidence = item.get("confidence", 0.0)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("映射 confidence 必须是有限数值")
        confidence = float(confidence)
        if not math.isfinite(confidence):
            raise ValueError("映射 confidence 必须是有限数值")
        entries.append({"source": source, "target": target,
                        "confidence": confidence,
                        "method": str(item.get("method") or "dictionary")})
    return entries


def _asset_payload(mapping: Any) -> Dict[str, Any]:
    if not isinstance(mapping, dict):
        raise ValueError("mapping 必须是 suggest_mapping 返回的字典")
    metadata = mapping.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    metadata["unmapped_source"] = list(mapping.get("unmapped_source") or [])
    metadata["unmapped_target"] = list(mapping.get("unmapped_target") or [])
    metadata.setdefault("consumers", [
        "P5.2 Skeleton Mapper", "UE IK Retargeter",
        "MotionBuilder Characterization", "3ds Max Biped",
    ])
    metadata["offline"] = True
    conflicts = mapping.get("conflicts") or []
    if not isinstance(conflicts, (list, tuple)):
        raise ValueError("mapping.conflicts 必须是列表")
    return {
        "version": ASSET_VERSION,
        "generated_by": GENERATED_BY,
        "entries": _asset_entries(mapping),
        "conflicts": [dict(item) for item in conflicts if isinstance(item, dict)],
        "metadata": metadata,
    }


def write_mapping_asset(mapping: Any, path: Any) -> str:
    """把候选映射写为版本化 JSON 资产，返回传入路径字符串。

    只写调用方指定的文件，不触碰 UE 资产或场景；父目录不存在时会创建。
    序列化使用排序键、固定缩进且禁止 NaN，便于版本管理与 P5.2 可复现消费。
    """
    filename = os.fspath(path)
    if not filename:
        raise ValueError("path 不能为空")
    payload = _asset_payload(mapping)
    parent = os.path.dirname(os.path.abspath(filename))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(filename, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2,
                  sort_keys=True, allow_nan=False)
        handle.write("\n")
    return str(filename)


def load_mapping_asset(path: Any) -> Dict[str, Any]:
    """读取并校验版本 1.x 映射资产；返回与文件内容一致的字典。

    文件缺字段、JSON 损坏或主版本不兼容时抛出 ``ValueError``，不会静默修补，
    避免把不完整映射写入 IK Retargeter / Characterization / Biped。
    """
    filename = os.fspath(path)
    try:
        with open(filename, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("无法读取映射资产 %s: %s" % (filename, exc))
    if not isinstance(payload, dict):
        raise ValueError("映射资产顶层必须是对象")
    required = {"version", "generated_by", "entries", "conflicts", "metadata"}
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError("映射资产缺少字段: %s" % ", ".join(missing))
    version = str(payload.get("version") or "")
    if not version.startswith("1."):
        raise ValueError("不支持的映射资产版本: %r" % version)
    if not isinstance(payload["entries"], list) or not isinstance(payload["conflicts"], list):
        raise ValueError("entries / conflicts 必须是列表")
    if not isinstance(payload["metadata"], dict):
        raise ValueError("metadata 必须是对象")
    return payload
