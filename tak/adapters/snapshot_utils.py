# -*- coding: utf-8 -*-
"""跨 DCC 适配器共享的纯函数工具（批次 3 · P4.0）。

存在意义：Maya / 3ds Max / MotionBuilder / UE 的宿主 API 完全不同，但
**快照必须逐字段同构**。把归一化、空值、层级深度、root 过滤与 sidecar
写入收敛到本模块，四端适配器就能共享同一份契约，避免出现「各自差一点」
导致 golden fixtures 判定漂移。

硬约束：

* 纯 Python 3.9+，**顶层零 DCC import**；本模块只处理字典与数值；
* 任何宿主异常都不在这里冒泡，适配器用 :func:`safe` / :func:`call` 兜底；
* 产出与 ``tak.core.diff.snapshot.SkeletonSnapshot`` 完全兼容。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence

IDENTITY_MATRIX: List[float] = [1.0, 0.0, 0.0, 0.0,
                                0.0, 1.0, 0.0, 0.0,
                                0.0, 0.0, 1.0, 0.0,
                                0.0, 0.0, 0.0, 1.0]
DEFAULT_FRAME_RANGE: List[float] = [1.0, 120.0]
DEFAULT_SCALE: List[float] = [1.0, 1.0, 1.0]
DEFAULT_ROTATE: List[float] = [0.0, 0.0, 0.0]
JOINT_KEYS = ("parent", "matrix", "depth", "scale", "rotate", "namespace")
TOP_LEVEL_KEYS = ("dcc", "version", "unit", "up_axis", "joints",
                  "frame_range", "skin", "metadata")
MAX_PARENT_DEPTH = 128

__all__ = [
    "IDENTITY_MATRIX", "DEFAULT_FRAME_RANGE", "JOINT_KEYS", "TOP_LEVEL_KEYS",
    "safe", "call", "as_float", "as_int", "floats", "short_name",
    "namespace_of", "depth_of", "empty_skin", "normalize_skin_dict",
    "empty_snapshot", "normalize_snapshot", "build_snapshot",
    "filter_root", "write_sidecar",
]


# --------------------------------------------------------------------------- #
# 宿主调用兜底
# --------------------------------------------------------------------------- #
def safe(default: Any, func: Any, *args: Any, **kwargs: Any) -> Any:
    """调用宿主函数，任何异常都退回 ``default``（含函数对象不存在的情况）。"""
    if not callable(func):
        return default
    try:
        value = func(*args, **kwargs)
    except Exception:
        return default
    return default if value is None else value


def call(host: Any, name: str, default: Any, *args: Any, **kwargs: Any) -> Any:
    """按名字安全调用 ``host.<name>``。

    属性查找本身也会抛异常（pymxs / pyfbsdk 的惰性属性尤其常见），
    因此查找与调用都必须在 try 内完成，不能用 ``safe(default, host.x)``。
    """
    func = getattr(host, name, None)
    if not callable(func):
        return default
    try:
        value = func(*args, **kwargs)
    except Exception:
        return default
    return default if value is None else value


# --------------------------------------------------------------------------- #
# 数值与名字
# --------------------------------------------------------------------------- #
def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def floats(value: Any, size: int, default: Sequence[float]) -> List[float]:
    """把宿主返回值规整为定长 float 列表；支持嵌套一层的返回值。"""
    if isinstance(value, (list, tuple)) and len(value) == 1 \
            and isinstance(value[0], (list, tuple)):
        value = value[0]
    if not isinstance(value, (list, tuple)) or len(value) != size:
        return [float(item) for item in default]
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return [float(item) for item in default]


def short_name(name: Any) -> str:
    """去掉 DAG 长路径前缀（Maya ``|`` / Max ``$`` 风格），保留 namespace。"""
    text = str(name or "")
    return text.replace("\\", "/").rsplit("|", 1)[-1].rsplit("/", 1)[-1].lstrip("$")


def namespace_of(name: str) -> str:
    return name.rpartition(":")[0] if ":" in name else ""


def depth_of(name: str, parents: Dict[str, Optional[str]],
             max_depth: int = MAX_PARENT_DEPTH) -> int:
    """按 parent 链计算深度，带环与超深保护。"""
    depth = 0
    seen = {name}
    current = parents.get(name)
    while current is not None and current not in seen and depth < max_depth:
        seen.add(current)
        depth += 1
        current = parents.get(current)
    return depth


# --------------------------------------------------------------------------- #
# 蒙皮摘要
# --------------------------------------------------------------------------- #
def empty_skin(mesh: str = "") -> Dict[str, Any]:
    return {
        "mesh": mesh,
        "skin_clusters": [],
        "influences": [],
        "max_influences_found": 0,
        "weight_sum_deviation": 0.0,
        "deformers": [],
        "vertex_count": 0,
        "sampled_vertices": 0,
        "truncated": False,
    }


def normalize_skin_dict(mesh: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """把宿主/桥接层给的蒙皮摘要收敛成统一字段与类型。"""
    raw = data if isinstance(data, dict) else {}
    result = empty_skin(mesh)
    result.update({
        "max_influences_found": as_int(raw.get("max_influences_found")),
        "weight_sum_deviation": round(as_float(raw.get("weight_sum_deviation")), 6),
        "deformers": sorted({str(item) for item in (raw.get("deformers") or [])}),
        "skin_clusters": [str(item) for item in (raw.get("skin_clusters") or [])],
        "influences": [str(item) for item in (raw.get("influences") or [])],
        "vertex_count": as_int(raw.get("vertex_count")),
        "sampled_vertices": as_int(raw.get("sampled_vertices")),
        "truncated": bool(raw.get("truncated", False)),
    })
    return result


# --------------------------------------------------------------------------- #
# 骨架快照
# --------------------------------------------------------------------------- #
def empty_snapshot(dcc: str, source: str, reason: str = "") -> Dict[str, Any]:
    """宿主不可用时的可预测空快照；结构与正常快照完全一致。"""
    metadata: Dict[str, Any] = {"source": source, "generated_by": "tak"}
    if reason:
        metadata["degraded"] = reason
    return {
        "dcc": str(dcc or "maya"),
        "version": "",
        "unit": "cm",
        "up_axis": "Y",
        "joints": {},
        "frame_range": list(DEFAULT_FRAME_RANGE),
        "skin": {"max_influences_found": 0, "weight_sum_deviation": 0.0,
                 "deformers": []},
        "metadata": metadata,
    }


def normalize_snapshot(raw: Dict[str, Any], dcc: str, source: str) -> Dict[str, Any]:
    """把任意离线快照 / 宿主采集结果规整成 ``SkeletonSnapshot`` 兼容字典。

    ``dcc`` 与 ``source`` 由调用方强制指定，避免宿主数据把报告误标成别的 DCC。
    """
    data = raw if isinstance(raw, dict) else {}
    joints: Dict[str, Dict[str, Any]] = {}
    for name, info in (data.get("joints") or {}).items():
        info = info if isinstance(info, dict) else {}
        parent = info.get("parent")
        joints[str(name)] = {
            "parent": None if parent is None else str(parent),
            "matrix": floats(info.get("matrix"), 16, IDENTITY_MATRIX),
            "depth": as_int(info.get("depth")),
            "scale": floats(info.get("scale"), 3, DEFAULT_SCALE),
            "rotate": floats(info.get("rotate"), 3, DEFAULT_ROTATE),
            "namespace": str(info.get("namespace") or ""),
        }
    skin_raw = data.get("skin") if isinstance(data.get("skin"), dict) else {}
    skin = dict(skin_raw)
    skin["max_influences_found"] = as_int(skin.get("max_influences_found"))
    skin["weight_sum_deviation"] = round(as_float(skin.get("weight_sum_deviation")), 6)
    skin["deformers"] = [str(item) for item in (skin.get("deformers") or [])]
    metadata = dict(data.get("metadata") or {})
    metadata.setdefault("source", source)
    metadata.setdefault("joint_count", len(joints))
    metadata.setdefault("generated_by", "tak")
    return {
        "dcc": str(dcc),
        "version": str(data.get("version") or ""),
        "unit": str(data.get("unit") or "cm"),
        "up_axis": str(data.get("up_axis") or "Y"),
        "joints": joints,
        "frame_range": floats(data.get("frame_range"), 2, DEFAULT_FRAME_RANGE),
        "skin": skin,
        "metadata": metadata,
    }


def build_snapshot(dcc: str, source: str, joints: Dict[str, Dict[str, Any]],
                   skin: Optional[Dict[str, Any]] = None, version: str = "",
                   unit: str = "cm", up_axis: str = "Y",
                   frame_range: Optional[Sequence[float]] = None,
                   metadata: Optional[Dict[str, Any]] = None,
                   root: Optional[str] = None) -> Dict[str, Any]:
    """四端采集器的统一出口：组装 → 归一化 → 可选 root 子树过滤。"""
    snapshot = normalize_snapshot({
        "version": version, "unit": unit, "up_axis": up_axis,
        "joints": joints or {}, "skin": skin or {},
        "frame_range": list(frame_range) if frame_range else list(DEFAULT_FRAME_RANGE),
        "metadata": metadata or {},
    }, dcc=dcc, source=source)
    return filter_root(snapshot, root)


def filter_root(snapshot: Dict[str, Any], root: Optional[str]) -> Dict[str, Any]:
    """只保留 ``root`` 及其后代；root 不存在时原样返回。"""
    target = short_name(root) if root else ""
    joints = snapshot.get("joints") or {}
    if not target or target not in joints:
        return snapshot
    children: Dict[str, List[str]] = {}
    for name, info in joints.items():
        parent = (info or {}).get("parent")
        if parent:
            children.setdefault(str(parent), []).append(name)
    keep = set()
    queue = [target]
    while queue:
        current = queue.pop()
        if current in keep:
            continue
        keep.add(current)
        queue.extend(children.get(current, []))
    result = dict(snapshot)
    result["joints"] = {name: info for name, info in joints.items() if name in keep}
    meta = dict(snapshot.get("metadata") or {})
    meta["root"] = target
    meta["joint_count"] = len(result["joints"])
    result["metadata"] = meta
    return result


def write_sidecar(path: str, snapshot: Dict[str, Any]) -> str:
    """写出可复现的 sidecar JSON（排序键、固定缩进、禁止 NaN）。

    导出桥与四端适配器共用：UE 侧 ``ue_import_guard`` 直接读取此文件。
    """
    target = str(path or "")
    parent = os.path.dirname(os.path.abspath(target))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(snapshot if isinstance(snapshot, dict) else {}, fh,
                  ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        fh.write("\n")
    return target
