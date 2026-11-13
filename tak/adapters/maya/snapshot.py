# -*- coding: utf-8 -*-
"""Maya 快照采集 / 落盘 / 离线场景加载。

``capture_snapshot`` 在真实 Maya 内调用适配器；宿主不可用时自动退回
``fixtures/good`` 的 golden 快照，使 headless / CI 也能走完整链路。
快照是纯 JSON：不含 numpy、不含 Maya 类型，可直接用于 diff 与归档。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
DEFAULT_FIXTURE = os.path.join(_REPO_ROOT, "fixtures", "good", "good_001.json")

IDENTITY_MATRIX = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
DEFAULT_FRAME_RANGE = [1.0, 120.0]


def capture_snapshot(adapter: Optional[Any] = None) -> Dict[str, Any]:
    """采集一份 SkeletonSnapshot 兼容字典。

    - ``adapter`` 非空时优先使用它（可注入 mock / 已有实例）；
    - 否则在 Maya 可用时创建适配器实时采集；
    - 都不可用时加载 golden fixture，并在 metadata 标记 ``offline``。
    """
    if adapter is not None:
        try:
            data = adapter.get_skeleton_snapshot()
            if isinstance(data, dict) and data:
                return _normalize(data)
        except Exception:
            pass

    try:
        from .adapter import MayaAdapter, is_available
        if is_available():
            data = MayaAdapter().get_skeleton_snapshot()
            if isinstance(data, dict) and data:
                return _normalize(data)
    except Exception:
        pass

    snapshot = load_fixture_snapshot()
    snapshot["metadata"]["offline"] = True
    snapshot["metadata"].setdefault("degraded", "maya 环境不可用，使用 golden fixture")
    return snapshot


def load_fixture_snapshot(path: Optional[str] = None) -> Dict[str, Any]:
    """把 fixtures/good 的 JSON 读成「离线场景」快照。

    自动兼容 ``{"snapshot": {...}}`` 包装；文件缺失或损坏时返回空快照而非抛异常。
    """
    target = os.path.abspath(path or DEFAULT_FIXTURE)
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        snapshot = _empty_snapshot()
        snapshot["metadata"]["error"] = "%s: %s" % (type(exc).__name__, exc)
        return snapshot

    raw: Any = data
    if isinstance(raw, dict) and "snapshot" in raw and "joints" not in raw:
        raw = raw.get("snapshot")
    if not isinstance(raw, dict):
        snapshot = _empty_snapshot()
        snapshot["metadata"]["error"] = "fixture 缺少 snapshot 对象"
        return snapshot
    snapshot = _normalize(raw)
    snapshot["metadata"].setdefault("fixture", os.path.relpath(target, _REPO_ROOT)
                                    .replace(os.sep, "/"))
    return snapshot


def write_snapshot(path: str, snapshot: Any) -> str:
    """把快照写成 UTF-8 JSON；父目录不存在时自动创建，返回写入路径。"""
    target = os.path.abspath(path)
    parent = os.path.dirname(target)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    data = snapshot
    if hasattr(data, "to_dict") and not isinstance(data, dict):
        data = data.to_dict()
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(_json_safe(data), fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return target


def _normalize(raw: Dict[str, Any]) -> Dict[str, Any]:
    joints: Dict[str, Dict[str, Any]] = {}
    for name, info in (raw.get("joints") or {}).items():
        info = info if isinstance(info, dict) else {}
        joints[str(name)] = {
            "parent": info.get("parent"),
            "matrix": _floats(info.get("matrix"), 16, IDENTITY_MATRIX),
            "depth": _as_int(info.get("depth")),
            "scale": _floats(info.get("scale"), 3, [1.0, 1.0, 1.0]),
            "rotate": _floats(info.get("rotate"), 3, [0.0, 0.0, 0.0]),
            "namespace": str(info.get("namespace") or ""),
        }
    skin_raw = raw.get("skin") if isinstance(raw.get("skin"), dict) else {}
    skin = dict(skin_raw)
    skin["max_influences_found"] = _as_int(skin.get("max_influences_found"))
    skin["weight_sum_deviation"] = round(_as_float(skin.get("weight_sum_deviation")), 6)
    skin["deformers"] = [str(item) for item in (skin.get("deformers") or [])]
    return {
        "dcc": "maya",
        "version": str(raw.get("version") or ""),
        "unit": str(raw.get("unit") or "cm"),
        "up_axis": str(raw.get("up_axis") or "Y"),
        "joints": joints,
        "frame_range": _floats(raw.get("frame_range"), 2, DEFAULT_FRAME_RANGE),
        "skin": skin,
        "metadata": dict(raw.get("metadata") or {}),
    }


def _empty_snapshot() -> Dict[str, Any]:
    return {
        "dcc": "maya",
        "version": "",
        "unit": "cm",
        "up_axis": "Y",
        "joints": {},
        "frame_range": list(DEFAULT_FRAME_RANGE),
        "skin": {"max_influences_found": 0, "weight_sum_deviation": 0.0,
                 "deformers": []},
        "metadata": {"source": "tak.adapters.maya.snapshot", "offline": True},
    }


def _floats(value: Any, size: int, default: Any) -> list:
    if isinstance(value, (list, tuple)) and len(value) == 1 \
            and isinstance(value[0], (list, tuple)):
        value = value[0]
    if not isinstance(value, (list, tuple)) or len(value) != size:
        return [float(item) for item in default]
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return [float(item) for item in default]


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _json_safe(value: Any) -> Any:
    """递归把 tuple / set / 自定义对象转成可序列化结构。"""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_json_safe(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
