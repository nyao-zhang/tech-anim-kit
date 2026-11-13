# -*- coding: utf-8 -*-
"""训练数据集清单：把采样计划固化成可复现、可校验的 JSON 清单。

边界
====
同 ``sampling``：本模块只算「清单 + 摘要 + 质量门槛」，**不训练、不连引擎、
不写场景**。UE 侧 ``MLDeformerAsset`` 等类型在 5.7 / 5.8 Python 侧均未暴露
（``docs/VALIDATION.md`` B2），能力位 ``mldeformer.train`` 恒为 False，
所以清单里刻意没有、也不允许出现训练任务 ID / 引擎资产路径之类字段。

确定性
======
清单里的 ``dataset_id`` / ``version`` 是**规范化 JSON**（``sort_keys=True``、
``separators=(",", ":")``、无时间戳、无绝对路径）的 ``sha256`` 前 16 位；
``pose_digest`` 只对 pose 列表求值。因此同一输入两次构建逐字节一致，
改一个角度就必然换 id —— 这条是数据工厂可追溯性的地基。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .sampling import (CATEGORY_ORDER, DATASET_CONTRACT, MAX_POSES_HARD_LIMIT,
                       _as_options, _finite)

GENERATED_BY = "tak.core.mldeformer"
DIGEST_LENGTH = 16
POSE_FIELDS = ("pose_id", "category", "joint", "axis", "angle", "label", "tags")

#: 门槛缺省值；逐条在 ``quality_gates`` 的 docstring 里解释来由。
DEFAULT_GATES: Dict[str, Any] = {
    "min_total_poses": 24,
    "max_total_poses": MAX_POSES_HARD_LIMIT,
    "min_poses_per_joint": 4,
    "max_duplicate_ratio": 0.1,
    "require_categories": ["extreme", "mid"],
    "require_contract": DATASET_CONTRACT,
}
_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


# --------------------------------------------------------------------------- #
# 规范化与摘要
# --------------------------------------------------------------------------- #
def _canonical(value: Any) -> str:
    """规范化 JSON 文本：键序固定、无空格、禁止 NaN，保证 digest 可复现。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:DIGEST_LENGTH]


def _normalize_angle(value: Any) -> float:
    number = _finite(value)
    number = 0.0 if number is None else number
    rounded = round(number, 3)
    return 0.0 if rounded == 0 else rounded


def _normalize_pose(pose: Any) -> Optional[Dict[str, Any]]:
    """把 pose 压成固定字段集，字段名 / 类型统一，供 digest 与下游消费。"""
    if not isinstance(pose, dict):
        return None
    joint = pose.get("joint")
    axis = pose.get("axis")
    if not isinstance(joint, str) or not isinstance(axis, str):
        return None
    tags = pose.get("tags")
    tag_list = sorted({str(tag) for tag in tags
                       if isinstance(tag, (str, int, float)) and str(tag)}) \
        if isinstance(tags, (list, tuple, set, frozenset)) else []
    category = pose.get("category")
    category = category if isinstance(category, str) else "unknown"
    pose_id = pose.get("pose_id")
    angle = _normalize_angle(pose.get("angle"))
    return {
        "pose_id": str(pose_id) if isinstance(pose_id, str) and pose_id else
                    "%s:%s:%s:%s" % (category, joint, axis, angle),
        "category": category,
        "joint": joint,
        "axis": axis,
        "angle": angle,
        "label": str(pose.get("label") or ""),
        "tags": tag_list,
    }


def _plan_dict(plan: Any) -> Dict[str, Any]:
    if plan is None:
        return {}
    if isinstance(plan, dict):
        return dict(plan)
    if hasattr(plan, "to_dict"):
        try:
            data = plan.to_dict()
            return dict(data) if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    number = _finite(value)
    return default if number is None else float(number)


# --------------------------------------------------------------------------- #
# 门槛
# --------------------------------------------------------------------------- #
def quality_gates(defaults: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """返回一份完整的质量门槛（缺省值 + 覆盖值），非法覆盖静默回落缺省。

    每条门槛为什么这么定
    --------------------
    * ``min_total_poses = 24``
      ML Deformer 的形变残差需要「多关节 × 多方向」组合才能辨识。24 是
      6 个 pose × 4 个关节的下限，低于它连一整套极值 / 半极值都拍不完，
      清单没有进入拍摄环节的意义。
    * ``max_total_poses = 2000``
      与采样计划的硬上限对齐：再多人工摆姿势与校验成本都会失控，而且
      2000 条已远超常见 20–30 关节骨架所需的千级样本量。
    * ``min_poses_per_joint = 4``
      每个关节至少要有正负极值与正负半极值（4 条），否则该关节的
      形变方向不全，训练时会退化成「只有一侧有数据」。缺失 4 条→警告；
      关节在清单里完全没有→错误（数据整块缺失，不是稀疏）。
    * ``max_duplicate_ratio = 0.1``
      采样器已按 ``(joint, axis, angle)`` 去重，正常产出重复率应为 0；
      留 10% 是给「左右对称骨架镜像合并」这类语义重复的余量，超过
      10% 说明采样或合并逻辑出了问题，必须阻断。
    * ``require_categories = ["extreme", "mid"]``
      极值决定可动范围，半极值决定中间插值质量，两者缺一不可；
      ``grid`` / ``random`` 只是补充密度，因此不设为必需。
    * ``require_contract = "1.0"``
      清单契约一旦变更，下游读取方必须同步升级，禁止静默混用。
    """
    gates = dict(DEFAULT_GATES)
    gates["require_categories"] = list(DEFAULT_GATES["require_categories"])
    overrides = _as_options(defaults)
    if "min_total_poses" in overrides:
        value = _as_int(overrides.get("min_total_poses"), -1)
        if value >= 0:
            gates["min_total_poses"] = value
    if "max_total_poses" in overrides:
        value = _as_int(overrides.get("max_total_poses"), -1)
        if value > 0:
            gates["max_total_poses"] = value
    if "min_poses_per_joint" in overrides:
        value = _as_int(overrides.get("min_poses_per_joint"), -1)
        if value >= 0:
            gates["min_poses_per_joint"] = value
    if "max_duplicate_ratio" in overrides:
        value = _as_float(overrides.get("max_duplicate_ratio"), -1.0)
        if 0.0 <= value <= 1.0:
            gates["max_duplicate_ratio"] = value
    if "require_categories" in overrides:
        raw = overrides.get("require_categories")
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, (list, tuple, set, frozenset)):
            picked = [str(item).strip().lower() for item in raw
                      if isinstance(item, str) and item.strip()]
            gates["require_categories"] = sorted(set(picked))
    if "require_contract" in overrides:
        value = overrides.get("require_contract")
        if isinstance(value, str) and value.strip():
            gates["require_contract"] = value.strip()
    if gates["max_total_poses"] < gates["min_total_poses"]:
        gates["max_total_poses"] = gates["min_total_poses"]
    return gates


# --------------------------------------------------------------------------- #
# 清单构建
# --------------------------------------------------------------------------- #
def build_manifest(plan: Any, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把采样计划固化成数据清单（纯数据、可复现、可 JSON 序列化）。

    输出字段
    --------
    ``contract`` / ``dataset_id`` / ``version`` / ``pose_count`` /
    ``joint_count`` / ``categories`` / ``coverage`` / ``joints`` /
    ``pose_digest`` / ``seed`` / ``duplicates`` / ``duplicate_ratio`` /
    ``gates`` / ``generated_by`` / ``notes``

    ``dataset_id`` 与 ``version`` 都是规范化 JSON 的 sha256 前 16 位（内容寻址，
    同输入同 id）；``pose_digest`` 只对 pose 列表求值，改一个角度即变。
    """
    data = _plan_dict(plan)
    options = _as_options(options)
    gates = quality_gates(options)

    raw_poses = data.get("poses")
    raw_poses = raw_poses if isinstance(raw_poses, (list, tuple)) else []
    poses: List[Dict[str, Any]] = []
    for item in raw_poses:
        normalized = _normalize_pose(item)
        if normalized is not None:
            poses.append(normalized)

    coverage: Dict[str, int] = {}
    for pose in poses:
        coverage[pose["joint"]] = coverage.get(pose["joint"], 0) + 1
    coverage = {joint: coverage[joint] for joint in sorted(coverage)}

    categories: Dict[str, int] = {}
    counted: Dict[str, int] = {}
    for pose in poses:
        counted[pose["category"]] = counted.get(pose["category"], 0) + 1
    for name in CATEGORY_ORDER:
        if counted.get(name):
            categories[name] = counted[name]
    for name in sorted(set(counted) - set(categories)):
        categories[name] = counted[name]

    keys = [(pose["joint"], pose["axis"], pose["angle"]) for pose in poses]
    duplicates = len(keys) - len(set(keys))
    pose_count = len(poses)
    duplicate_ratio = round(duplicates / float(pose_count), 4) if pose_count else 0.0

    joints = sorted(coverage)
    joint_count = _as_int(data.get("joint_count"), len(joints))
    if joint_count < len(joints):
        joint_count = len(joints)
    seed = _as_int(data.get("seed"), 0)
    contract = data.get("contract")
    contract = contract if isinstance(contract, str) and contract else DATASET_CONTRACT

    payload = {
        "contract": contract,
        "seed": seed,
        "joint_count": joint_count,
        "pose_count": pose_count,
        "categories": categories,
        "coverage": coverage,
        "joints": joints,
        "poses": poses,
    }
    digest = _digest(payload)

    notes: List[str] = []
    raw_notes = data.get("notes")
    if isinstance(raw_notes, (list, tuple)):
        notes.extend(str(note) for note in raw_notes if isinstance(note, str))
    notes.append("清单为内容寻址：dataset_id / version 由规范化 JSON 派生，"
                 "同一输入两次构建结果逐字节相同。")
    notes.append("本清单只描述采样数据与质量门槛，不含训练任务、引擎资产路径或"
                 "任何训练入口调用（UE 侧 mldeformer.train 能力位为 false）。")

    return {
        "contract": contract,
        "dataset_id": digest,
        "version": digest,
        "pose_count": pose_count,
        "joint_count": joint_count,
        "categories": categories,
        "coverage": coverage,
        "joints": joints,
        "pose_digest": _digest(poses),
        "seed": seed,
        "duplicates": duplicates,
        "duplicate_ratio": duplicate_ratio,
        "gates": gates,
        "generated_by": GENERATED_BY,
        "notes": notes,
    }


# --------------------------------------------------------------------------- #
# 清单校验
# --------------------------------------------------------------------------- #
def _issue(code: str, severity: str, message: str,
           data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"code": code, "severity": severity, "message": message,
            "data": dict(data or {})}


def _manifest_poses(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = manifest.get("poses")
    if not isinstance(raw, (list, tuple)):
        return []
    poses = []
    for item in raw:
        normalized = _normalize_pose(item)
        if normalized is not None:
            poses.append(normalized)
    return poses


def _duplicate_ratio(manifest: Dict[str, Any]) -> float:
    poses = _manifest_poses(manifest)
    if poses:
        keys = [(pose["joint"], pose["axis"], pose["angle"]) for pose in poses]
        return (len(keys) - len(set(keys))) / float(len(keys))
    ratio = _finite(manifest.get("duplicate_ratio"))
    if ratio is not None and ratio >= 0:
        return float(ratio)
    pose_count = _as_int(manifest.get("pose_count"), 0)
    if pose_count > 0:
        duplicates = max(0, _as_int(manifest.get("duplicates"), 0))
        return min(1.0, duplicates / float(pose_count))
    return 0.0


def validate_manifest(manifest: Any, gates: Optional[Dict[str, Any]] = None
                      ) -> Dict[str, Any]:
    """按质量门槛校验清单，返回确定性 issue 列表。

    返回 ``{"ok", "contract", "issues", "counts", "gates"}``：
    ``issues`` 每项 ``{"code", "severity", "message", "data"}``，``code`` 稳定
    可被 CI / UI 直接断言；``ok`` 仅在无 error 时为 True（warning 不阻断）。

    畸形输入（``None`` / 字符串 / 列表）不会抛异常，返回一条
    ``mld.manifest.invalid`` 错误。
    """
    gate_map = quality_gates(gates)
    issues: List[Dict[str, Any]] = []
    if not isinstance(manifest, dict):
        issues.append(_issue("mld.manifest.invalid", "error",
                             "清单不是对象，无法校验",
                             {"type": type(manifest).__name__}))
        return {"ok": False, "contract": "", "issues": issues,
                "counts": {"error": 1, "warning": 0, "info": 0},
                "gates": gate_map}

    contract = manifest.get("contract")
    contract = contract if isinstance(contract, str) else ""
    if contract != gate_map["require_contract"]:
        issues.append(_issue(
            "mld.contract.mismatch", "error",
            "清单契约 %r 与要求 %r 不一致" % (contract, gate_map["require_contract"]),
            {"contract": contract, "expected": gate_map["require_contract"]}))

    pose_count = _as_int(manifest.get("pose_count"), 0)
    if pose_count < gate_map["min_total_poses"]:
        issues.append(_issue(
            "mld.pose_count.too_low", "error",
            "pose 总数 %d 低于下限 %d" % (pose_count, gate_map["min_total_poses"]),
            {"pose_count": pose_count, "min_total_poses": gate_map["min_total_poses"]}))
    if pose_count > gate_map["max_total_poses"]:
        issues.append(_issue(
            "mld.pose_count.over_budget", "error",
            "pose 总数 %d 超过预算 %d" % (pose_count, gate_map["max_total_poses"]),
            {"pose_count": pose_count,
             "max_total_poses": gate_map["max_total_poses"]}))

    covered_raw = manifest.get("coverage")
    covered = covered_raw if isinstance(covered_raw, dict) else {}
    joint_count = _as_int(manifest.get("joint_count"), len(covered))
    declared = manifest.get("joints")
    declared = [str(joint) for joint in declared
                if isinstance(joint, str)] if isinstance(declared, (list, tuple)) else []
    if declared:
        for joint in sorted(set(declared)):
            if joint not in covered:
                issues.append(_issue(
                    "mld.coverage.missing_joint", "error",
                    "关节 %s 完全没有采样数据" % joint,
                    {"joint": joint}))
    elif joint_count > len(covered):
        issues.append(_issue(
            "mld.coverage.missing_joint", "error",
            "joint_count=%d 但只有 %d 个关节有采样数据"
            % (joint_count, len(covered)),
            {"joint_count": joint_count, "covered": sorted(covered)}))

    for joint in sorted(covered):
        number = _as_int(covered[joint], 0)
        if number < gate_map["min_poses_per_joint"]:
            issues.append(_issue(
                "mld.coverage.low", "warning",
                "关节 %s 仅 %d 条 pose，低于建议值 %d"
                % (joint, number, gate_map["min_poses_per_joint"]),
                {"joint": joint, "pose_count": number,
                 "min_poses_per_joint": gate_map["min_poses_per_joint"]}))

    ratio = _duplicate_ratio(manifest)
    if ratio > gate_map["max_duplicate_ratio"]:
        issues.append(_issue(
            "mld.duplicate.too_high", "error",
            "重复率 %.4f 超过上限 %.4f" % (ratio, gate_map["max_duplicate_ratio"]),
            {"duplicate_ratio": round(ratio, 4),
             "max_duplicate_ratio": gate_map["max_duplicate_ratio"]}))

    categories = manifest.get("categories")
    categories = categories if isinstance(categories, dict) else {}
    present = {str(name) for name, value in categories.items() if _as_int(value, 0) > 0}
    for category in gate_map["require_categories"]:
        if category not in present:
            issues.append(_issue(
                "mld.category.missing", "warning",
                "缺少必需类别 %s" % category,
                {"category": category,
                 "present": sorted(present)}))
    if "extreme" not in present:
        issues.append(_issue(
            "mld.extreme.missing", "warning",
            "没有 extreme 类别 pose：ML Deformer 依赖极值样本学习可动范围",
            {"present": sorted(present)}))

    issues.sort(key=lambda item: (_SEVERITY_RANK.get(item["severity"], 9),
                                  item["code"],
                                  _canonical(item["data"])))
    counts = {"error": 0, "warning": 0, "info": 0}
    for item in issues:
        counts[item["severity"]] = counts.get(item["severity"], 0) + 1
    return {
        "ok": counts["error"] == 0,
        "contract": contract,
        "issues": issues,
        "counts": counts,
        "gates": gate_map,
    }


def gate_codes() -> List[str]:
    """返回全部门槛 issue code，供文档与测试对齐。"""
    return [
        "mld.contract.mismatch",
        "mld.pose_count.too_low",
        "mld.pose_count.over_budget",
        "mld.coverage.missing_joint",
        "mld.coverage.low",
        "mld.duplicate.too_high",
        "mld.category.missing",
        "mld.extreme.missing",
        "mld.manifest.invalid",
    ]


def manifest_digest(manifest: Dict[str, Any]) -> str:
    """重算清单的内容摘要（排除自身摘要字段），用于外部核对是否被篡改。"""
    data = dict(manifest or {})
    for key in ("dataset_id", "version", "pose_digest"):
        data.pop(key, None)
    return _digest(data)


def pose_key_summary(poses: Sequence[Any]) -> Dict[str, int]:
    """统计 pose 列表里的去重键数量与重复数（供 UI 显示）。"""
    keys: List[Tuple[str, str, float]] = []
    for pose in poses or []:
        normalized = _normalize_pose(pose)
        if normalized is not None:
            keys.append((normalized["joint"], normalized["axis"],
                         normalized["angle"]))
    return {"total": len(keys), "unique": len(set(keys)),
            "duplicates": len(keys) - len(set(keys))}


__all__ = ["DEFAULT_GATES", "GENERATED_BY", "build_manifest", "gate_codes",
           "manifest_digest", "pose_key_summary", "quality_gates",
           "validate_manifest"]
