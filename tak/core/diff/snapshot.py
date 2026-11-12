# -*- coding: utf-8 -*-
"""骨架快照：导出前后各 dump 一份，diff 出偏差才允许进入引擎。

快照是纯 JSON，不依赖任何 DCC；四个 DCC 的 adapter 都产出同一结构，
因此 golden fixtures 可以跨 DCC 复用，回归测试无需启动 Maya / UE。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


_IDENTITY_MATRIX = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


@dataclass
class Joint:
    name: str
    parent: Optional[str] = None
    matrix: List[float] = field(default_factory=lambda: list(_IDENTITY_MATRIX))
    depth: int = 0
    scale: List[float] = field(default_factory=lambda: [1.0, 1.0, 1.0])
    rotate: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    namespace: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"parent": self.parent, "matrix": list(self.matrix), "depth": self.depth,
                "scale": list(self.scale), "rotate": list(self.rotate),
                "namespace": self.namespace}

    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> "Joint":
        data = data or {}
        return cls(name=name, parent=data.get("parent"),
                   matrix=list(data.get("matrix") or _IDENTITY_MATRIX),
                   depth=int(data.get("depth") or 0),
                   scale=list(data.get("scale") or [1.0, 1.0, 1.0]),
                   rotate=list(data.get("rotate") or [0.0, 0.0, 0.0]),
                   namespace=data.get("namespace") or "")


@dataclass
class SkeletonSnapshot:
    dcc: str = "maya"
    version: str = ""
    unit: str = "cm"
    up_axis: str = "Y"
    joints: Dict[str, Joint] = field(default_factory=dict)
    frame_range: List[float] = field(default_factory=lambda: [1.0, 120.0])
    skin: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"dcc": self.dcc, "version": self.version, "unit": self.unit,
                "up_axis": self.up_axis,
                "joints": {name: j.to_dict() for name, j in self.joints.items()},
                "frame_range": list(self.frame_range),
                "skin": dict(self.skin), "metadata": dict(self.metadata)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkeletonSnapshot":
        data = data or {}
        return cls(
            dcc=data.get("dcc", "maya"), version=str(data.get("version") or ""),
            unit=data.get("unit", "cm"), up_axis=data.get("up_axis", "Y"),
            joints={k: Joint.from_dict(k, v)
                    for k, v in (data.get("joints") or {}).items()},
            frame_range=list(data.get("frame_range") or [1.0, 120.0]),
            skin=dict(data.get("skin") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


def validate_snapshot(snapshot: SkeletonSnapshot, spec: Dict[str, Any]) -> List[str]:
    """按规范做确定性体检，返回 rule_id 列表（golden fixtures 的判定基准）。"""
    found: List[str] = []
    naming = spec.get("naming", {})
    skeleton = spec.get("skeleton", {})
    skin_spec = spec.get("skin", {})
    export = spec.get("export", {}).get("fbx", {})

    pattern = naming.get("joint_pattern")
    if pattern:
        try:
            rx = re.compile(pattern)
            if any(rx.match(name) is None for name in snapshot.joints):
                found.append("naming.joint_pattern")
        except re.error:
            found.append("spec.invalid_joint_pattern")
    for token in naming.get("forbidden_tokens", []):
        if any(token in name for name in snapshot.joints):
            found.append("naming.forbidden_token")
            break
    if snapshot.joints and any(j.namespace for j in snapshot.joints.values()):
        found.append("naming.namespace")

    missing = [j for j in skeleton.get("required_joints", []) if j not in snapshot.joints]
    if missing:
        found.append("skeleton.missing_required")
    if snapshot.joints and max((j.depth for j in snapshot.joints.values()), default=0) > skeleton.get("max_depth", 99):
        found.append("skeleton.max_depth")
    if not skeleton.get("allow_negative_scale", True):
        if any(any(v < 0 for v in j.scale) for j in snapshot.joints.values()):
            found.append("skeleton.negative_scale")
    if skeleton.get("allow_frozen_transforms_only", False):
        if any(any(abs(v) > 0.0001 for v in j.rotate) for j in snapshot.joints.values()):
            found.append("skeleton.unfrozen_transform")

    skin = snapshot.skin or {}
    if skin.get("max_influences_found", 0) > skin_spec.get("max_influences", 8):
        found.append("skin.max_influences")
    if skin.get("weight_sum_deviation", 0.0) > skin_spec.get("weight_sum_tolerance", 1.0):
        found.append("skin.weight_sum")
    allowed = skin_spec.get("allowed_deformers", [])
    if allowed and any(d not in allowed for d in skin.get("deformers", [])):
        found.append("skin.unknown_deformer")

    if export.get("unit") and snapshot.unit != export["unit"]:
        found.append("export.unit_mismatch")
    if export.get("up_axis") and snapshot.up_axis != export["up_axis"]:
        found.append("export.up_axis_mismatch")
    expected_range = export.get("frame_range")
    if expected_range and list(snapshot.frame_range) != list(expected_range):
        found.append("export.frame_range_mismatch")
    return sorted(set(found))


def diff_snapshots(before: SkeletonSnapshot, after: SkeletonSnapshot,
                   matrix_tolerance: float = 0.0001) -> List[Dict[str, Any]]:
    """对比两份快照；None 按默认空快照处理，返回值仅含严格 JSON 数据。"""
    before = _coerce_snapshot(before)
    after = _coerce_snapshot(after)
    if (isinstance(matrix_tolerance, bool)
            or not isinstance(matrix_tolerance, (int, float))
            or not math.isfinite(matrix_tolerance) or matrix_tolerance < 0):
        raise ValueError("matrix_tolerance 必须是非负有限数值")
    tolerance = float(matrix_tolerance)
    diffs: List[Dict[str, Any]] = []
    if before.unit != after.unit:
        diffs.append({"field": "unit", "before": _json_value(before.unit),
                      "after": _json_value(after.unit)})
    if before.up_axis != after.up_axis:
        diffs.append({"field": "up_axis", "before": _json_value(before.up_axis),
                      "after": _json_value(after.up_axis)})
    if len(before.joints) != len(after.joints):
        diffs.append({"field": "joint_count", "before": len(before.joints),
                      "after": len(after.joints)})

    missing = set(before.joints) - set(after.joints)
    added = set(after.joints) - set(before.joints)
    renamed = []
    for old_name in sorted(missing):
        for new_name in sorted(added):
            if _joints_equal(before.joints[old_name], after.joints[new_name], tolerance):
                renamed.append((old_name, new_name))
                added.remove(new_name)
                missing.remove(old_name)
                break
    for old_name, new_name in renamed:
        diffs.append({"field": "joint_renamed", "before": old_name, "after": new_name})
    for name in sorted(missing):
        diffs.append({"field": "joint_missing", "joint": name})
    for name in sorted(added):
        diffs.append({"field": "joint_added", "joint": name})
    for name in sorted(set(before.joints) & set(after.joints)):
        a, b = before.joints[name], after.joints[name]
        if a.parent != b.parent:
            diffs.append({"field": "parent", "joint": name,
                          "before": _json_value(a.parent), "after": _json_value(b.parent)})
        if not _numeric_sequences_equal(a.matrix, b.matrix, tolerance):
            diffs.append({"field": "bind_matrix", "joint": name})
    before_range = _json_value(list(before.frame_range or []))
    after_range = _json_value(list(after.frame_range or []))
    if before_range != after_range:
        diffs.append({"field": "frame_range", "before": before_range, "after": after_range})
    return diffs


def _coerce_snapshot(snapshot: Any) -> SkeletonSnapshot:
    if snapshot is None:
        return SkeletonSnapshot()
    if isinstance(snapshot, SkeletonSnapshot):
        return snapshot
    if isinstance(snapshot, dict):
        return SkeletonSnapshot.from_dict(snapshot)
    raise TypeError("snapshot 必须是 SkeletonSnapshot、字典或 None")


def _numeric_sequences_equal(before: Any, after: Any, tolerance: float) -> bool:
    if before is None or after is None:
        return before is after
    try:
        if len(before) != len(after):
            return False
    except TypeError:
        return before == after
    for left, right in zip(before, after):
        numbers = (isinstance(left, (int, float)) and not isinstance(left, bool)
                   and isinstance(right, (int, float)) and not isinstance(right, bool))
        if numbers:
            if not math.isclose(left, right, rel_tol=0.0, abs_tol=tolerance):
                return False
        elif left != right:
            return False
    return True


def _joints_equal(before: Joint, after: Joint, tolerance: float) -> bool:
    return (before.parent == after.parent and before.depth == after.depth
            and before.namespace == after.namespace
            and _numeric_sequences_equal(before.matrix, after.matrix, tolerance)
            and _numeric_sequences_equal(before.scale, after.scale, tolerance)
            and _numeric_sequences_equal(before.rotate, after.rotate, tolerance))


def _json_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)
