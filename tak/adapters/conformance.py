# -*- coding: utf-8 -*-
"""四端一致性契约（批次 3 · P4.3 的框架侧基线）。

四个 DCC 的宿主 API 互不相通，但工具链只认一份快照。本模块定义并校验
这份契约，让「同一具骨架从 Maya / Max / MoBu / UE 读出来应得到同一份数据」
成为**可执行的断言**，而不是文档里的一句话。

对外接口
--------
``validate_snapshot(snapshot)``
    结构体检，返回 ``{"ok", "errors", "warnings", ...}``。
``compare_snapshots(before, after, ...)``
    忽略 ``dcc`` / ``version`` / ``metadata`` 后逐字段 diff，数值带容差。
``check_adapter(adapter)``
    对某个适配器实例取一次快照并检查能力声明、DCC 标识与结构。
``capability_matrix(adapters)``
    把若干适配器的能力声明汇总为可序列化的矩阵。

纯 Python 3.9+，顶层零 DCC import；所有入参都按不可信数据处理。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from tak.adapters.base import Capability, DCCAdapter
from tak.adapters.snapshot_utils import (JOINT_KEYS, TOP_LEVEL_KEYS,
                                         as_float, short_name)

CONTRACT_VERSION = "1.0"
REQUIRED_CAPABILITIES = (
    Capability.SCENE_QUERY,
    Capability.SKIN_QUERY,
    Capability.SKELETON_SNAPSHOT,
)
# 比对时忽略的字段：宿主版本、来源与采集元数据天然不同。
IGNORED_FIELDS = ("dcc", "version", "metadata")

SNAPSHOT_CONTRACT: Dict[str, Any] = {
    "version": CONTRACT_VERSION,
    "top_level": list(TOP_LEVEL_KEYS),
    "joint": list(JOINT_KEYS),
    "matrix_size": 16,
    "vector3_size": 3,
    "frame_range_size": 2,
    "ignored_for_compare": list(IGNORED_FIELDS),
}

__all__ = ["CONTRACT_VERSION", "SNAPSHOT_CONTRACT", "validate_snapshot",
           "compare_snapshots", "check_adapter", "capability_matrix"]


# --------------------------------------------------------------------------- #
# 结构校验
# --------------------------------------------------------------------------- #
def validate_snapshot(snapshot: Any) -> Dict[str, Any]:
    """检查快照是否符合四端契约；任何异常都转成 error 项，不抛出。"""
    errors: List[str] = []
    warnings: List[str] = []
    if not isinstance(snapshot, dict):
        return _result(False, ["快照必须是 dict，实际为 %s" % type(snapshot).__name__],
                       warnings, 0, "")

    dcc = str(snapshot.get("dcc") or "")
    for key in TOP_LEVEL_KEYS:
        if key not in snapshot:
            errors.append("缺少顶层字段 %s" % key)
    if dcc not in ("maya", "max", "mobu", "unreal"):
        errors.append("dcc 必须是 maya/max/mobu/unreal，实际为 %r" % dcc)

    joints = snapshot.get("joints")
    if joints is None:
        joints = {}
    if not isinstance(joints, dict):
        errors.append("joints 必须是对象")
        joints = {}
    for name, info in joints.items():
        label = "joints.%s" % name
        if not isinstance(info, dict):
            errors.append("%s 必须是对象" % label)
            continue
        for key in JOINT_KEYS:
            if key not in info:
                errors.append("%s 缺少 %s" % (label, key))
        matrix = info.get("matrix")
        if matrix is not None and (not isinstance(matrix, (list, tuple)) or len(matrix) != 16):
            errors.append("%s.matrix 必须含 16 个数" % label)
        for key in ("scale", "rotate"):
            value = info.get(key)
            if value is not None and (not isinstance(value, (list, tuple)) or len(value) != 3):
                errors.append("%s.%s 必须含 3 个数" % (label, key))
        parent = info.get("parent")
        if parent is not None and parent not in joints:
            errors.append("%s.parent 指向不存在的关节 %r" % (label, parent))
        info_depth = info.get("depth")
        if info_depth is not None and (not isinstance(info_depth, int) or info_depth < 0):
            errors.append("%s.depth 必须是非负整数" % label)
    if not joints:
        warnings.append("快照不含关节（空场景或宿主不可用）")

    frame_range = snapshot.get("frame_range")
    if frame_range is not None and (not isinstance(frame_range, (list, tuple))
                                    or len(frame_range) != 2):
        errors.append("frame_range 必须含 2 个数")
    skin = snapshot.get("skin")
    if skin is not None and not isinstance(skin, dict):
        errors.append("skin 必须是对象")
    if snapshot.get("metadata") is not None and not isinstance(snapshot.get("metadata"), dict):
        errors.append("metadata 必须是对象")
    if str(snapshot.get("unit") or "") == "":
        errors.append("unit 不能为空")
    if str(snapshot.get("up_axis") or "") not in ("X", "Y", "Z"):
        errors.append("up_axis 必须是 X/Y/Z")

    return _result(not errors, errors, warnings, len(joints), dcc)


def _result(ok: bool, errors: List[str], warnings: List[str],
            joint_count: int, dcc: str) -> Dict[str, Any]:
    return {
        "ok": bool(ok),
        "contract": CONTRACT_VERSION,
        "errors": list(errors),
        "warnings": list(warnings),
        "joint_count": int(joint_count),
        "dcc": dcc,
    }


# --------------------------------------------------------------------------- #
# 内容比对
# --------------------------------------------------------------------------- #
def _close(left: Any, right: Any, tolerance: float) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return left == right


def _diff(path: str, before: Any, after: Any,
          out: List[Dict[str, Any]]) -> None:
    out.append({"path": path, "before": before, "after": after})


def compare_snapshots(before: Any, after: Any,
                      matrix_tolerance: float = 0.0001,
                      numeric_tolerance: float = 0.000001,
                      ignore: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """逐字段比对两份快照，返回机器可读的差异列表。

    忽略 ``dcc`` / ``version`` / ``metadata``（以及调用方追加的字段），
    因为宿主标识与采集元数据本就不同；其余字段一律要求一致。
    """
    ignored = set(IGNORED_FIELDS)
    ignored.update(str(item) for item in (ignore or []))
    differences: List[Dict[str, Any]] = []
    if not isinstance(before, dict) or not isinstance(after, dict):
        return {"ok": False, "contract": CONTRACT_VERSION,
                "differences": [{"path": "snapshot", "before": type(before).__name__,
                                 "after": type(after).__name__}],
                "ignored": sorted(ignored), "joint_count_before": 0,
                "joint_count_after": 0}

    for key in ("unit", "up_axis"):
        if key in ignored:
            continue
        if before.get(key) != after.get(key):
            _diff(key, before.get(key), after.get(key), differences)
    for key in ("frame_range",):
        if key in ignored:
            continue
        left = list(before.get(key) or [])
        right = list(after.get(key) or [])
        if len(left) != len(right) or any(
                not _close(a, b, numeric_tolerance) for a, b in zip(left, right)):
            _diff(key, left, right, differences)

    skin_before = before.get("skin") or {}
    skin_after = after.get("skin") or {}
    for key in ("max_influences_found", "weight_sum_deviation"):
        if "skin.%s" % key in ignored:
            continue
        left, right = skin_before.get(key, 0), skin_after.get(key, 0)
        if not _close(left, right, numeric_tolerance):
            _diff("skin.%s" % key, left, right, differences)
    if "skin.deformers" not in ignored:
        left = sorted(str(item) for item in (skin_before.get("deformers") or []))
        right = sorted(str(item) for item in (skin_after.get("deformers") or []))
        if left != right:
            _diff("skin.deformers", left, right, differences)

    joints_before = before.get("joints") or {}
    joints_after = after.get("joints") or {}
    if not isinstance(joints_before, dict):
        joints_before = {}
    if not isinstance(joints_after, dict):
        joints_after = {}
    for name in sorted(set(joints_before) - set(joints_after)):
        if "joints.%s" % name not in ignored:
            _diff("joints.%s" % name, "present", "missing", differences)
    for name in sorted(set(joints_after) - set(joints_before)):
        if "joints.%s" % name not in ignored:
            _diff("joints.%s" % name, "missing", "present", differences)
    for name in sorted(set(joints_before) & set(joints_after)):
        if "joints.%s" % name in ignored:
            continue
        left = joints_before.get(name) if isinstance(joints_before.get(name), dict) else {}
        right = joints_after.get(name) if isinstance(joints_after.get(name), dict) else {}
        for key in ("parent", "namespace"):
            if "joints.%s.%s" % (name, key) in ignored:
                continue
            if left.get(key) != right.get(key):
                _diff("joints.%s.%s" % (name, key), left.get(key), right.get(key),
                      differences)
        for key in ("depth",):
            if "joints.%s.%s" % (name, key) in ignored:
                continue
            if not _close(left.get(key, 0), right.get(key, 0), numeric_tolerance):
                _diff("joints.%s.%s" % (name, key), left.get(key), right.get(key),
                      differences)
        for key, tolerance in (("matrix", matrix_tolerance),
                               ("scale", numeric_tolerance),
                               ("rotate", numeric_tolerance)):
            if "joints.%s.%s" % (name, key) in ignored:
                continue
            a = list(left.get(key) or [])
            b = list(right.get(key) or [])
            if len(a) != len(b) or any(
                    not _close(x, y, tolerance) for x, y in zip(a, b)):
                _diff("joints.%s.%s" % (name, key), a, b, differences)

    return {
        "ok": not differences,
        "contract": CONTRACT_VERSION,
        "differences": differences,
        "ignored": sorted(ignored),
        "joint_count_before": len(joints_before),
        "joint_count_after": len(joints_after),
        "matrix_tolerance": float(matrix_tolerance),
    }


# --------------------------------------------------------------------------- #
# 适配器侧
# --------------------------------------------------------------------------- #
def capability_matrix(adapters: Dict[str, Any]) -> Dict[str, Any]:
    """汇总四端能力声明；不触碰真实宿主，只调用 ``supports`` / ``describe``。"""
    matrix: Dict[str, Any] = {"contract": CONTRACT_VERSION, "dccs": {}}
    for dcc, adapter in (adapters or {}).items():
        capabilities: Dict[str, bool] = {}
        for capability in Capability:
            try:
                capabilities[capability.value] = bool(adapter.supports(capability))
            except Exception:
                capabilities[capability.value] = False
        missing = [item.value for item in REQUIRED_CAPABILITIES
                   if not capabilities.get(item.value, False)]
        matrix["dccs"][str(dcc)] = {
            "name": str(getattr(adapter, "name", dcc)),
            "capabilities": capabilities,
            "missing_required": missing,
            "conformant": not missing,
        }
    return matrix


def check_adapter(adapter: Any, root: Optional[str] = None,
                  strict_dcc: bool = True) -> Dict[str, Any]:
    """取一次快照做端到端检查：能力、DCC 标识、结构、错误降级。"""
    result: Dict[str, Any] = {
        "ok": False,
        "contract": CONTRACT_VERSION,
        "adapter": "",
        "requested_root": short_name(root) if root else "",
        "errors": [],
        "warnings": [],
        "capability": {},
    }
    if not isinstance(adapter, DCCAdapter):
        result["errors"].append("对象不是 DCCAdapter")
        return result
    try:
        described = adapter.describe()
    except Exception as exc:
        described = {"dcc": str(getattr(adapter, "dcc", "")),
                     "error": "%s: %s" % (type(exc).__name__, exc)}
    result["adapter"] = str(described.get("name") or getattr(adapter, "name", ""))
    dcc_value = getattr(adapter, "dcc", "maya")
    dcc_name = str(getattr(dcc_value, "value", dcc_value)).lower()
    info = capability_matrix({dcc_name: adapter})
    block = list(info["dccs"].values())
    result["capability"] = block[0] if block else {}
    result["warnings"].extend("缺少必需能力 %s" % item
                              for item in result["capability"].get("missing_required", []))

    try:
        snapshot = adapter.get_skeleton_snapshot(root)
    except Exception as exc:
        result["errors"].append("get_skeleton_snapshot 抛异常: %s: %s"
                                % (type(exc).__name__, exc))
        return result
    validation = validate_snapshot(snapshot)
    result["validation"] = validation
    result["errors"].extend(validation["errors"])
    result["warnings"].extend(validation["warnings"])
    expected = dcc_name
    actual = str(snapshot.get("dcc") or "")
    if strict_dcc and expected and actual and expected not in actual:
        result["errors"].append("快照 dcc=%r 与适配器 dcc=%r 不一致" % (actual, expected))
    if (snapshot.get("metadata") or {}).get("degraded"):
        result["warnings"].append("宿主不可用，快照为降级空结构: %s"
                                  % snapshot["metadata"]["degraded"])
    result["joint_count"] = validation["joint_count"]
    result["ok"] = not result["errors"]
    return result
