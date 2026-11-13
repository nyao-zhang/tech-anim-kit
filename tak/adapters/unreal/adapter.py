# -*- coding: utf-8 -*-
"""Unreal Engine 5.8 / 5.9 适配器：把 unreal 的资产层翻译成 TAK 的跨 DCC 契约。

语义映射
--------
============================  ================================================
TAK 概念                       UE 对应物
============================  ================================================
joint / bone                  Skeleton 的骨骼名（ReferenceSkeleton / BoneTree）
anim 节点                     AnimSequence 资产路径
mesh 蒙皮                     SkeletalMesh 的 Skinning / MorphTarget
deformer                      Skin / MorphTarget / MeshDeformer（UE 侧命名）
============================  ================================================

设计约束
--------
* 顶层**不导入** unreal；``import tak.adapters.unreal`` 在纯 Python / CI /
  本机开发机上永远成功，真实 ``import unreal`` 只发生在函数体内。
* 5.8 → 5.9 的 API 差异一律用 ``hasattr`` / ``get_editor_property`` 降级，
  不做版本号分支。真机结论：``EditorDialogLibrary`` 在 5.7/5.8 均不存在，
  必须走 ``EditorDialog``；``MLDeformerAsset`` / ``NeuralMorphModel`` /
  ``DeformerGraphAsset`` 在 Python 侧未暴露，缺失只记录在
  ``describe()["api_missing"]``，不抛异常。
* 快照输出 TAK 规范空间（Y 轴向上、厘米）：UE 原生 Z 轴向上在
  :func:`_to_tak_matrix` 里做基变换，原生轴向记录在
  ``metadata["native_up_axis"]``，便于与 Maya / Max 的 sidecar 直接 diff。
* UE 不暴露逐顶点权重，``get_skin_summary`` 的权重偏差在取不到时返回 0.0
  并标记 ``weight_sum_source="unavailable"``，绝不臆造数值。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from tak.adapters.base import Capability, DCC, DCCAdapter
from tak.compat.unreal import (API_PROBE_NAMES, api_probe, asset_registry,
                               engine_version, safe_attr)
from tak.core.diff.snapshot import SkeletonSnapshot, diff_snapshots

IDENTITY_MATRIX: List[float] = [1.0, 0, 0, 0, 0, 1.0, 0, 0,
                                0, 0, 1.0, 0, 0, 0, 0, 1.0]
DEFAULT_FRAME_RANGE: List[float] = [1.0, 120.0]

CANONICAL_UNIT = "cm"
CANONICAL_UP_AXIS = "Y"
NATIVE_UP_AXIS = "Z"

# UE 是左手 Z-up：v_ue = C · v_tak。C 按列主序展开成 4x4（平移在 [12:15]）。
_TAK_TO_UE: List[float] = [1.0, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1]
_UE_TO_TAK: List[float] = [1.0, 0, 0, 0, 0, 0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1]

# 资产遍历与层级回溯的硬上限，避免内容库很大时卡死编辑器。
_MAX_ASSETS = 64
_MAX_BONES = 8192
_MAX_PARENT_DEPTH = 128
_MAX_NOTES = 32

_SUPPORTED_CAPABILITIES = frozenset({
    Capability.SCENE_QUERY,
    Capability.SKIN_QUERY,
    Capability.SKELETON_SNAPSHOT,
    Capability.IMPORT_VALIDATE,
})

_BONE_TYPES = frozenset({"joint", "joints", "bone", "bones"})
_ANIM_TYPES = frozenset({"anim", "anims", "animation", "animations",
                         "anim_sequence", "animsequence", "anim_sequences"})
_MESH_TYPES = frozenset({"mesh", "meshes", "skeletal_mesh", "skeletalmesh"})
_SKELETON_TYPES = frozenset({"skeleton", "skeletons", "skeleton_asset"})


# --------------------------------------------------------------------------- #
# 延迟导入与安全调用
# --------------------------------------------------------------------------- #
def _unreal_module() -> Any:
    """延迟导入 ``unreal``；不在编辑器内时抛 ImportError。"""
    import importlib
    return importlib.import_module("unreal")


def is_available() -> bool:
    """检测是否身处 UE Python 环境。

    只做一次 ``import unreal``（引擎内置模块，无副作用，**不会**初始化编辑器
    或 GEditor），因此纯 Python / commandlet / 编辑器内都可安全调用。
    """
    try:
        return _unreal_module() is not None
    except Exception:
        return False


def _call(obj: Any, name: str, *args: Any) -> Any:
    """按名字安全调用宿主方法：属性缺失、调用失败都返回 None。"""
    func = safe_attr(obj, name)
    if not callable(func):
        return None
    try:
        return func(*args)
    except Exception:
        return None


def _editor_prop(obj: Any, names: Sequence[str], default: Any = None) -> Any:
    """多候选资产属性读取：``get_editor_property`` 优先，其次普通属性。

    5.8 → 5.9 属性改名是常态（例如 ``compression_scheme`` 与
    ``compression_settings``），因此永远传候选名列表而不是写版本分支。
    """
    getter = safe_attr(obj, "get_editor_property")
    for name in names:
        if callable(getter):
            try:
                value = getter(name)
            except Exception:
                value = None
            if value is not None:
                return value
        value = safe_attr(obj, name)
        if value is not None:
            return value
    return default


def _as_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _numbers(value: Any, size: int) -> Optional[List[float]]:
    """把宿主返回的 Vector / tuple / list 规整为定长 float 列表。"""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        items: Iterable[Any] = value
    elif hasattr(value, "__iter__") and not isinstance(value, (str, bytes)):
        try:
            items = list(value)
        except Exception:
            return None
    else:
        return None
    items = list(items)
    if len(items) == 1 and isinstance(items[0], (list, tuple)):
        items = list(items[0])
    if len(items) != size:
        return None
    try:
        return [float(item) for item in items]
    except (TypeError, ValueError):
        return None


def _mat_multiply(a: Sequence[float], b: Sequence[float]) -> List[float]:
    """列主序 4x4 矩阵乘法（与 ``SkeletonSnapshot.Joint.matrix`` 的约定一致）。"""
    out = [0.0] * 16
    for col in range(4):
        for row in range(4):
            total = 0.0
            for k in range(4):
                total += a[k * 4 + row] * b[col * 4 + k]
            out[col * 4 + row] = total
    return out


def _mat_transpose(m: Sequence[float]) -> List[float]:
    out = [0.0] * 16
    for col in range(4):
        for row in range(4):
            out[col * 4 + row] = m[row * 4 + col]
    return out


def _to_tak_matrix(matrix: Optional[Sequence[float]]) -> List[float]:
    """UE 空间矩阵 → TAK 规范空间矩阵（``C⁻¹ · M · C``）。"""
    if matrix is None or len(matrix) != 16:
        return list(IDENTITY_MATRIX)
    return _mat_multiply(_mat_multiply(_UE_TO_TAK, list(matrix)), _TAK_TO_UE)


def _matrix_from_transform(transform: Any) -> Optional[List[float]]:
    """从 UE 的 Transform / Matrix 读 4x4；读不到返回 None（不做猜测）。"""
    if transform is None:
        return None
    converted = _call(transform, "to_matrix")
    matrix = _numbers(converted, 16)
    if matrix is not None:
        return matrix
    for name in ("matrix", "local_matrix", "transform"):
        matrix = _numbers(safe_attr(transform, name), 16)
        if matrix is not None:
            return matrix
    location = safe_attr(transform, "translation")
    if location is None:
        location = safe_attr(transform, "location")
    scale = safe_attr(transform, "scale3d")
    if scale is None:
        scale = safe_attr(transform, "scale")
    loc = _numbers(location, 3)
    scl = _numbers(scale, 3)
    if loc is None and scl is None:
        return None
    matrix = list(IDENTITY_MATRIX)
    if scl is not None:
        matrix[0], matrix[5], matrix[10] = scl[0], scl[1], scl[2]
    if loc is not None:
        matrix[12], matrix[13], matrix[14] = loc[0], loc[1], loc[2]
    return matrix


def _frame_rate(value: Any) -> Optional[float]:
    """FrameRate 结构 / 数字 / 字符串统一成 float fps。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    numerator = _as_float(safe_attr(value, "numerator"), 0.0)
    denominator = _as_float(safe_attr(value, "denominator"), 0.0)
    if numerator > 0 and denominator > 0:
        return round(numerator / denominator, 6)
    try:
        return round(float(str(value)), 6)
    except (TypeError, ValueError):
        return None


def _length_of(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return len(value)
    except TypeError:
        return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_json_safe(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _asset_path(asset: Any) -> str:
    """资产 → 包路径字符串；兼容 AssetData / UObject / 字符串。"""
    if asset is None:
        return ""
    if isinstance(asset, str):
        return asset
    for name in ("package_name", "get_path_name", "get_name"):
        value = safe_attr(asset, name)
        if callable(value):
            text = _call(asset, name)
            if text:
                return str(text)
        elif value:
            return str(value)
    return ""


def _depth_of(name: str, parents: Dict[str, Optional[str]]) -> int:
    depth = 0
    seen = {name}
    current = parents.get(name)
    while current is not None and current not in seen and depth < _MAX_PARENT_DEPTH:
        seen.add(current)
        depth += 1
        current = parents.get(current)
    return depth


def _empty_skin(mesh: str = "") -> Dict[str, Any]:
    return {
        "mesh": mesh,
        "max_influences_found": 0,
        "weight_sum_deviation": 0.0,
        "deformers": [],
        "influences": [],
        "vertex_count": 0,
        "morph_target_count": 0,
        "weight_sum_source": "unavailable",
        "source": "none",
    }


def _empty_snapshot(reason: str = "") -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "source": "tak.adapters.unreal",
        "generated_by": "tak",
        "native_up_axis": NATIVE_UP_AXIS,
        "joint_count": 0,
        "matrix_source": "none",
    }
    if reason:
        metadata["degraded"] = [reason]
    return {
        "dcc": "unreal",
        "version": "",
        "unit": CANONICAL_UNIT,
        "up_axis": CANONICAL_UP_AXIS,
        "joints": {},
        "frame_range": list(DEFAULT_FRAME_RANGE),
        "skin": {"max_influences_found": 0, "weight_sum_deviation": 0.0,
                 "deformers": []},
        "metadata": metadata,
    }


def _extract_snapshot(data: Any) -> Dict[str, Any]:
    """从注入数据里挑出快照：支持裸快照与 ``{"snapshot": {...}}`` 包装。"""
    if not isinstance(data, dict):
        return {}
    if isinstance(data.get("joints"), dict):
        return data
    nested = data.get("snapshot")
    return nested if isinstance(nested, dict) else {}


def _normalize_snapshot(raw: Dict[str, Any]) -> Dict[str, Any]:
    joints: Dict[str, Dict[str, Any]] = {}
    for name, info in (raw.get("joints") or {}).items():
        info = info if isinstance(info, dict) else {}
        matrix = _numbers(info.get("matrix"), 16) or list(IDENTITY_MATRIX)
        joints[str(name)] = {
            "parent": info.get("parent"),
            "matrix": matrix,
            "depth": _as_int(info.get("depth")),
            "scale": _numbers(info.get("scale"), 3) or [1.0, 1.0, 1.0],
            "rotate": _numbers(info.get("rotate"), 3) or [0.0, 0.0, 0.0],
            "namespace": str(info.get("namespace") or ""),
        }
    skin_raw = raw.get("skin") if isinstance(raw.get("skin"), dict) else {}
    skin = dict(skin_raw)
    skin["max_influences_found"] = _as_int(skin.get("max_influences_found"))
    skin["weight_sum_deviation"] = round(_as_float(skin.get("weight_sum_deviation")), 6)
    skin["deformers"] = sorted({str(item) for item in (skin.get("deformers") or [])})
    metadata = dict(raw.get("metadata") or {})
    metadata.setdefault("source", "tak.adapters.unreal")
    return {
        "dcc": "unreal",
        "version": str(raw.get("version") or ""),
        "unit": str(raw.get("unit") or CANONICAL_UNIT),
        "up_axis": str(raw.get("up_axis") or CANONICAL_UP_AXIS),
        "joints": joints,
        "frame_range": _numbers(raw.get("frame_range"), 2) or list(DEFAULT_FRAME_RANGE),
        "skin": skin,
        "metadata": metadata,
    }


def _filter_root(snapshot: Dict[str, Any], root: Optional[str]) -> Dict[str, Any]:
    """只保留 root 及其后代；root 不存在时原样返回。"""
    target = str(root or "").rsplit("|", 1)[-1]
    joints = snapshot.get("joints") or {}
    if not target or target not in joints:
        return snapshot
    children: Dict[str, List[str]] = {}
    for name, info in joints.items():
        parent = (info or {}).get("parent")
        if parent:
            children.setdefault(parent, []).append(name)
    keep = set()
    queue = [target]
    while queue:
        current = queue.pop()
        if current in keep:
            continue
        keep.add(current)
        queue.extend(children.get(current, []))
    filtered = {name: info for name, info in joints.items() if name in keep}
    result = dict(snapshot)
    result["joints"] = filtered
    metadata = dict(snapshot.get("metadata") or {})
    metadata["root"] = target
    metadata["joint_count"] = len(filtered)
    result["metadata"] = metadata
    return result


def _resolve_sidecar(path: Any) -> Tuple[str, str]:
    """把「FBX / 资产路径 / sidecar」统一解析到真实存在的 JSON 文件。"""
    text = str(path or "").strip()
    if not text:
        return "", "未提供导入校验路径"
    candidates = [text]
    if not text.lower().endswith(".json"):
        candidates.append(text + ".snapshot.json")
        stem = os.path.splitext(text)[0]
        if stem and stem != text:
            candidates.append(stem + ".snapshot.json")
    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.abspath(candidate), ""
    return "", "找不到 sidecar 快照（尝试过 %s）" % "、".join(candidates)


# --------------------------------------------------------------------------- #
# 适配器
# --------------------------------------------------------------------------- #
class UnrealAdapter(DCCAdapter):
    """UE 5.8 / 5.9 的 DCCAdapter 实现（资产层语义，全部调用延迟导入 + 降级）。"""

    dcc = DCC.UNREAL
    name = "unreal"

    def __init__(self, version: str = "", python_version: str = "",
                 data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> None:
        super().__init__(version, python_version)
        self.available = is_available()
        self._u = self._unreal() if self.available else None
        self.engine_version = engine_version(self._u) if self._u is not None else ""
        self.offline_data = dict(data) if isinstance(data, dict) else {}
        self._offline_snapshot = _extract_snapshot(self.offline_data)
        self.degraded: List[str] = []
        self.api: Dict[str, bool] = {}
        self._snapshot_cache: Optional[Dict[str, Any]] = None

        self.version = str(self.version or "")
        if not self.version:
            self.version = self.engine_version or str(
                (self._offline_snapshot or {}).get("version")
                or self.offline_data.get("version") or "")
        if not self.python_version:
            self.python_version = "%d.%d" % (sys.version_info[0], sys.version_info[1])
        self._refresh_api_probe()

    # ---------------------------------------------------------------- 基础
    def _unreal(self) -> Optional[Any]:
        try:
            return _unreal_module()
        except Exception:
            return None

    def _note(self, text: str) -> None:
        """记录一次降级原因（去重 + 上限），供 describe() 与探测脚本取证。"""
        if text and text not in self.degraded and len(self.degraded) < _MAX_NOTES:
            self.degraded.append(text)

    def _refresh_api_probe(self) -> None:
        module = self._u
        if module is None:
            self.api = {name: False for name in API_PROBE_NAMES}
            self._note("unreal 模块不可导入：适配器以离线/降级模式运行")
            return
        try:
            self.api = dict(api_probe(module))
        except Exception as exc:
            self.api = {name: False for name in API_PROBE_NAMES}
            self._note("api_probe 失败: %s: %s" % (type(exc).__name__, exc))
        for name in ("MLDeformerAsset", "NeuralMorphModel", "VertexDeltaModel",
                     "MLDeformerComponent", "DeformerGraphAsset"):
            if self.api.get(name) is False:
                self._note("%s 在 Python 侧未暴露，相关能力降级" % name)

    def supports(self, capability: Capability) -> bool:
        return capability in _SUPPORTED_CAPABILITIES

    def describe(self) -> Dict[str, Any]:
        info = super().describe()
        info.update({
            "available": bool(self.available),
            "offline": bool(self._offline_snapshot or self.offline_data),
            "engine_version": self.engine_version,
            "api_probe": dict(self.api),
            "api_missing": sorted(name for name, ok in self.api.items() if not ok),
            "native_up_axis": NATIVE_UP_AXIS,
            "degraded": list(self.degraded),
        })
        return _json_safe(info)

    # ---------------------------------------------------------------- 节点查询
    def list_nodes(self, node_type: str = "joint") -> List[str]:
        kind = str(node_type or "joint").strip().lower()
        if kind in _ANIM_TYPES:
            return self._offline_list("anims") or self._asset_paths("AnimSequence")
        if kind in _MESH_TYPES:
            return self._asset_paths("SkeletalMesh")
        if kind in _SKELETON_TYPES:
            return self._asset_paths("Skeleton")
        if kind in ("asset", "assets"):
            return sorted(set(self._asset_paths("Skeleton"))
                          | set(self._asset_paths("SkeletalMesh"))
                          | set(self._asset_paths("AnimSequence")))
        return self._bone_names()

    def _offline_list(self, key: str) -> List[str]:
        value = self.offline_data.get(key)
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value]
        return []

    def _bone_names(self) -> List[str]:
        offline = self._offline_list("nodes") or self._offline_list("joints")
        if offline:
            return sorted(set(offline))
        snapshot = self.get_skeleton_snapshot()
        return sorted((snapshot.get("joints") or {}))

    def get_node_attrs(self, node: str) -> Dict[str, Any]:
        """骨骼返回层级线索，资产路径返回资产元数据（全部 hasattr 降级）。"""
        name = str(node or "").strip()
        if not name:
            return {}
        attrs = self.offline_data.get("attrs")
        if isinstance(attrs, dict) and isinstance(attrs.get(name), dict):
            return dict(attrs[name])
        joint = (self.get_skeleton_snapshot().get("joints") or {}).get(name)
        if isinstance(joint, dict):
            return {
                "node": name,
                "type": "bone",
                "parent": joint.get("parent"),
                "depth": _as_int(joint.get("depth")),
                "matrix": list(joint.get("matrix") or IDENTITY_MATRIX),
                "scale": list(joint.get("scale") or [1.0, 1.0, 1.0]),
                "rotate": list(joint.get("rotate") or [0.0, 0.0, 0.0]),
                "namespace": "",
            }
        asset = self._load_asset(name)
        if asset is None:
            return {}
        return self._asset_attrs(name, asset)

    def _asset_attrs(self, path: str, asset: Any) -> Dict[str, Any]:
        skeleton = self._skeleton_of(asset)
        curves = _editor_prop(asset, ("curve_data", "compressed_curve_data",
                                      "float_curve_names"))
        materials = _editor_prop(asset, ("materials", "material_slots"))
        morphs = _editor_prop(asset, ("morph_targets", "morph_targets_data"))
        return _json_safe({
            "node": path,
            "type": "asset",
            "asset_class": self._asset_class_name(asset),
            "skeleton": _asset_path(skeleton) if skeleton is not None else None,
            "frame_rate": _frame_rate(_editor_prop(
                asset, ("sampling_frame_rate", "frame_rate", "display_rate"))),
            "compression": _editor_prop(
                asset, ("compression_scheme", "compression_settings", "compression")),
            "root_motion": _editor_prop(
                asset, ("enable_root_motion", "root_motion", "bake_root_motion")),
            "curve_count": _as_int(_length_of(curves), 0),
            "material_slots": _as_int(_length_of(materials), 0),
            "morph_target_count": _as_int(_length_of(morphs), 0),
        })

    # ---------------------------------------------------------------- 蒙皮
    def get_skin_summary(self, mesh: str) -> Dict[str, Any]:
        """SkeletalMesh 的蒙皮摘要；读不到的字段返回可预测零值并标注来源。"""
        name = str(mesh or "").strip()
        result = _empty_skin(name)
        if not name:
            return result
        offline = self.offline_data.get("skin")
        if isinstance(offline, dict) and isinstance(offline.get(name), dict):
            return self._merge_skin(name, offline[name], "offline")

        asset = self._load_asset(name)
        if asset is None:
            result["source"] = "missing_asset"
            self._note("资产 %s 未加载到，蒙皮摘要返回零值" % name)
            return result

        summary = _editor_prop(asset, ("skin_summary", "tak_skin_summary"))
        if isinstance(summary, dict):
            return self._merge_skin(name, summary, "asset")

        morphs = _editor_prop(asset, ("morph_targets", "morph_targets_data"))
        morph_count = _as_int(_length_of(morphs), 0)
        deformers = ["Skin"] if self._skeleton_of(asset) is not None else []
        if morph_count:
            deformers.append("MorphTarget")
        if self._has_mesh_deformer(asset):
            deformers.append("MeshDeformer")

        influences = _as_int(_editor_prop(
            asset, ("max_influences", "max_num_influences", "max_influences_found")), 0)
        result.update({
            "max_influences_found": influences,
            "deformers": sorted(deformers),
            "morph_target_count": morph_count,
            "vertex_count": _as_int(_editor_prop(
                asset, ("num_vertices", "vertex_count")), 0),
            "source": "asset",
        })
        return result

    def _merge_skin(self, name: str, data: Dict[str, Any], source: str) -> Dict[str, Any]:
        result = _empty_skin(name)
        result.update({
            "max_influences_found": _as_int(data.get("max_influences_found")),
            "weight_sum_deviation": round(_as_float(data.get("weight_sum_deviation")), 6),
            "deformers": sorted({str(item) for item in (data.get("deformers") or [])}),
            "influences": [str(item) for item in (data.get("influences") or [])],
            "vertex_count": _as_int(data.get("vertex_count")),
            "morph_target_count": _as_int(data.get("morph_target_count")),
            "weight_sum_source": str(data.get("weight_sum_source") or source),
            "source": source,
        })
        return result

    def _has_mesh_deformer(self, asset: Any) -> bool:
        value = _editor_prop(asset, ("deformer_graph", "mesh_deformer",
                                     "deformers", "mesh_deformer_collection"))
        return value is not None

    # ---------------------------------------------------------------- 快照
    def get_skeleton_snapshot(self, root: Optional[str] = None) -> Dict[str, Any]:
        """产出 SkeletonSnapshot 兼容字典（TAK 规范空间：Y-up / cm）。"""
        if self._offline_snapshot:
            snapshot = _normalize_snapshot(self._offline_snapshot)
            metadata = snapshot["metadata"]
            metadata.setdefault("offline", True)
            metadata.setdefault("native_up_axis", NATIVE_UP_AXIS)
            metadata.setdefault("matrix_source", "offline")
            metadata["joint_count"] = len(snapshot["joints"])
            self._snapshot_cache = snapshot if root is None else self._snapshot_cache
            return _filter_root(snapshot, root)

        if self._u is None:
            snapshot = _empty_snapshot("unreal 模块不可导入")
            snapshot["version"] = self.version
            self._snapshot_cache = snapshot if root is None else self._snapshot_cache
            return snapshot

        try:
            snapshot = self._capture(root)
        except Exception as exc:
            snapshot = _empty_snapshot("%s: %s" % (type(exc).__name__, exc))
            snapshot["version"] = self.version
            self._note("快照采集失败: %s: %s" % (type(exc).__name__, exc))
        if root is None:
            self._snapshot_cache = snapshot
        return snapshot

    def _capture(self, root: Optional[str]) -> Dict[str, Any]:
        skeleton = self._find_skeleton(root)
        names, parents, transforms, source = self._bone_records(skeleton)
        # 只有真的读到变换才算「矩阵可比」，否则退化为单位阵并标记 none。
        matrix_source = source if transforms else "none"
        joints: Dict[str, Dict[str, Any]] = {}
        for name in names:
            matrix = _to_tak_matrix(transforms.get(name))
            joints[name] = {
                "parent": parents.get(name),
                "matrix": matrix,
                "depth": _depth_of(name, parents),
                "scale": [matrix[0], matrix[5], matrix[10]],
                "rotate": [0.0, 0.0, 0.0],
                "namespace": "",
            }
        meshes = self._asset_paths("SkeletalMesh")
        skin = self._aggregate_skin(meshes)
        snapshot = {
            "dcc": "unreal",
            "version": str(self.version or self.engine_version),
            "unit": CANONICAL_UNIT,
            "up_axis": CANONICAL_UP_AXIS,
            "joints": joints,
            "frame_range": list(DEFAULT_FRAME_RANGE),
            "skin": skin,
            "metadata": {
                "source": "tak.adapters.unreal",
                "engine_version": self.engine_version,
                "python_version": self.python_version,
                "skeleton": _asset_path(skeleton),
                "joint_count": len(joints),
                "mesh_count": len(meshes),
                "skin_mesh_count": len(skin.get("per_mesh") or {}),
                "matrix_source": matrix_source,
                "native_up_axis": NATIVE_UP_AXIS,
                "frame_range_source": "default",
                "generated_by": "tak",
            },
        }
        if not joints:
            snapshot["metadata"]["degraded"] = [
                "未找到 Skeleton / SkeletalMesh 资产（空探测项目属正常）"]
        if matrix_source == "none":
            snapshot["metadata"]["degraded"] = list(
                snapshot["metadata"].get("degraded") or []) + [
                "UE Python 未暴露骨骼静止变换，bind_matrix 以单位阵占位"]
            self._note("UE Python 未暴露骨骼静止变换，bind_matrix 以单位阵占位")
        return snapshot

    def _find_skeleton(self, root: Optional[str]) -> Optional[Any]:
        if root:
            asset = self._load_asset(str(root))
            if asset is not None:
                if self._is_skeleton(asset):
                    return asset
                found = self._skeleton_of(asset)
                if found is not None:
                    return found

        for asset in self._selected_assets():
            if self._is_skeleton(asset):
                return asset
            found = self._skeleton_of(asset)
            if found is not None:
                return found

        for path in self._asset_paths("Skeleton")[:_MAX_ASSETS]:
            asset = self._load_asset(path)
            if asset is not None:
                return asset

        for path in self._asset_paths("SkeletalMesh")[:_MAX_ASSETS]:
            asset = self._load_asset(path)
            found = self._skeleton_of(asset) if asset is not None else None
            if found is not None:
                return found
        return None

    def _bone_records(self, skeleton: Any) -> Tuple[List[str], Dict[str, Optional[str]],
                                                   Dict[str, List[float]], str]:
        """按 ReferenceSkeleton → BoneTree → get_bone_names 的顺序降级读取。"""
        if skeleton is None:
            return [], {}, {}, "none"

        reference = _call(skeleton, "get_reference_skeleton")
        records = self._records_from_reference(reference)
        if records[0]:
            return records[0], records[1], records[2], "reference_skeleton"

        tree = _editor_prop(skeleton, ("bone_tree", "bone_tree_data"))
        records = self._records_from_tree(tree)
        if records[0]:
            return records[0], records[1], records[2], "bone_tree"

        names = _call(skeleton, "get_bone_names") or safe_attr(skeleton, "bone_names")
        if isinstance(names, (list, tuple)) and names:
            ordered = [str(item) for item in list(names)[:_MAX_BONES]]
            return ordered, {name: None for name in ordered}, {}, "bone_names"
        return [], {}, {}, "none"

    def _records_from_reference(self, reference: Any) -> Tuple[List[str],
                                                                Dict[str, Optional[str]],
                                                                Dict[str, List[float]]]:
        total = _as_int(_call(reference, "get_num"), 0)
        if total <= 0:
            return [], {}, {}
        total = min(total, _MAX_BONES)
        names: List[str] = []
        parent_index: List[int] = []
        transforms: Dict[str, List[float]] = {}
        for index in range(total):
            name = _call(reference, "get_bone_name", index)
            if name is None:
                name = safe_attr(reference, "bone_names")
                if isinstance(name, (list, tuple)) and index < len(name):
                    name = name[index]
            text = str(name or "")
            if not text:
                continue
            names.append(text)
            parent_index.append(_as_int(_call(reference, "get_parent_index", index), -1))
            matrix = _matrix_from_transform(
                _call(reference, "get_bone_transform", index)
                or _call(reference, "get_ref_bone_pose", index))
            if matrix is not None:
                transforms[text] = matrix
        parents: Dict[str, Optional[str]] = {}
        for index, name in enumerate(names):
            parent = parent_index[index] if index < len(parent_index) else -1
            parents[name] = names[parent] if 0 <= parent < len(names) else None
        return names, parents, transforms

    def _records_from_tree(self, tree: Any) -> Tuple[List[str],
                                                     Dict[str, Optional[str]],
                                                     Dict[str, List[float]]]:
        if tree is None:
            return [], {}, {}
        items = tree
        if not isinstance(items, (list, tuple)):
            items = _editor_prop(tree, ("bones", "elements"), None)
        if not isinstance(items, (list, tuple)):
            return [], {}, {}
        names: List[str] = []
        parents: Dict[str, Optional[str]] = {}
        transforms: Dict[str, List[float]] = {}
        for item in list(items)[:_MAX_BONES]:
            name = _editor_prop(item, ("bone_name", "name"))
            text = str(name or "")
            if not text:
                continue
            names.append(text)
            parent = _editor_prop(item, ("parent_name", "parent"))
            parents[text] = str(parent) if parent else None
            matrix = _matrix_from_transform(_editor_prop(item, ("transform", "local_transform")))
            if matrix is not None:
                transforms[text] = matrix
        return names, parents, transforms

    def _aggregate_skin(self, meshes: Sequence[str]) -> Dict[str, Any]:
        per_mesh: Dict[str, Dict[str, Any]] = {}
        deformers = set()
        max_influences = 0
        max_deviation = 0.0
        for path in list(meshes)[:_MAX_ASSETS]:
            summary = self.get_skin_summary(path)
            if summary.get("source") in ("none", "missing_asset"):
                continue
            max_influences = max(max_influences, _as_int(summary.get("max_influences_found")))
            max_deviation = max(max_deviation, _as_float(summary.get("weight_sum_deviation")))
            deformers.update(summary.get("deformers") or [])
            per_mesh[path] = {
                "max_influences_found": _as_int(summary.get("max_influences_found")),
                "weight_sum_deviation": _as_float(summary.get("weight_sum_deviation")),
                "deformers": list(summary.get("deformers") or []),
                "vertex_count": _as_int(summary.get("vertex_count")),
            }
        result: Dict[str, Any] = {
            "max_influences_found": max_influences,
            "weight_sum_deviation": round(max_deviation, 6),
            "deformers": sorted(deformers),
            "mesh_count": len(per_mesh),
        }
        if per_mesh:
            result["per_mesh"] = per_mesh
        return result

    # ---------------------------------------------------------------- 导入校验
    def import_validate(self, path: Any, spec: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """把 Export Bridge 写出的 sidecar 快照与 UE 侧 Skeleton 做 diff。

        ``spec["ue"]`` 可覆盖三个阈值（都可省略）：

        * ``import_max_diffs``          允许的偏差条数，默认 0（有偏差即阻断）；
        * ``import_matrix_tolerance``   矩阵容差，默认 1e-4；
        * ``import_check_bind_matrices`` 是否比对 bind_matrix；默认仅在 UE 侧
          真的读到骨骼变换时比对（否则跳过并在 reason 里注明）。

        返回 ``{"passed": bool, "diffs": [...], "reason": str}``。
        """
        spec = spec if isinstance(spec, dict) else {}
        ue_spec = spec.get("ue") if isinstance(spec.get("ue"), dict) else {}
        result: Dict[str, Any] = {"passed": False, "diffs": [], "reason": ""}

        target, error = _resolve_sidecar(path)
        if not target:
            result["reason"] = error
            return result
        try:
            with open(target, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError) as exc:
            result["reason"] = "无法读取 sidecar 快照 %s: %s: %s" % (
                target, type(exc).__name__, exc)
            return result

        before = raw
        if isinstance(raw, dict) and "joints" not in raw and isinstance(raw.get("snapshot"), dict):
            before = raw["snapshot"]
        if not isinstance(before, dict) or not isinstance(before.get("joints"), dict):
            result["reason"] = "sidecar 快照缺少 joints 对象: %s" % target
            return result

        root = ue_spec.get("skeleton_asset") or spec.get("root")
        after = self.get_skeleton_snapshot(root=root)
        tolerance = _as_float(ue_spec.get("import_matrix_tolerance"), 0.0001)
        if tolerance < 0:
            tolerance = 0.0001
        try:
            diffs = diff_snapshots(SkeletonSnapshot.from_dict(before),
                                   SkeletonSnapshot.from_dict(after),
                                   matrix_tolerance=tolerance)
        except (TypeError, ValueError) as exc:
            result["reason"] = "快照 diff 失败: %s: %s" % (type(exc).__name__, exc)
            return result

        check_matrices = ue_spec.get("import_check_bind_matrices")
        if check_matrices is None:
            check_matrices = self._matrix_verified(after)
        skipped = 0
        if not check_matrices:
            kept = [item for item in diffs if item.get("field") != "bind_matrix"]
            skipped = len(diffs) - len(kept)
            diffs = kept

        limit = max(0, _as_int(ue_spec.get("import_max_diffs"), 0))
        result["diffs"] = [dict(item) for item in diffs]
        result["passed"] = len(diffs) <= limit
        joint_count = len(before.get("joints") or {})
        if result["passed"]:
            result["reason"] = "校验通过：%d 个关节与 UE Skeleton 一致（阈值 %d 处偏差）" % (
                joint_count, limit)
        else:
            fields = sorted({str(item.get("field")) for item in diffs})
            result["reason"] = "校验未通过：%d 处偏差超过阈值 %d（%s）" % (
                len(diffs), limit, "、".join(fields))
        if skipped:
            result["reason"] += "；UE 侧未暴露骨骼静止变换，已跳过 %d 处 bind_matrix 偏差" % skipped
        return result

    def _matrix_verified(self, snapshot: Dict[str, Any]) -> bool:
        metadata = snapshot.get("metadata") or {}
        return (str(metadata.get("matrix_source") or "none") != "none"
                and bool(snapshot.get("joints")))

    # ---------------------------------------------------------------- UI / 日志
    def ui_parent(self) -> Any:
        """UE 引擎内没有 Qt，返回 None；TAK 面板在 UE 侧走 Web（P3.2）。"""
        return None

    def log(self, message: str, level: str = "info") -> None:
        text = "[TAK][%s] %s" % (level, message)
        module = self._u
        if module is not None:
            name = {"error": "log_error", "critical": "log_error",
                    "warning": "log_warning", "warn": "log_warning"}.get(
                        str(level or "info").lower(), "log")
            func = safe_attr(module, name) or safe_attr(module, "log")
            if callable(func):
                try:
                    func(text)
                    return
                except Exception:
                    pass
        print(text)

    # ---------------------------------------------------------------- 资产读取
    def _load_asset(self, path: str) -> Optional[Any]:
        module = self._u
        if module is None or not path:
            return None
        for func in (safe_attr(module, "load_asset"),
                     safe_attr(safe_attr(module, "EditorAssetLibrary", None), "load_asset")):
            if callable(func):
                try:
                    asset = func(path)
                except Exception:
                    asset = None
                if asset is not None:
                    return asset
        return None

    def _asset_paths(self, class_name: str) -> List[str]:
        module = self._u
        if module is None:
            return []
        registry = asset_registry(module)
        getter = safe_attr(registry, "get_assets_by_class")
        if not callable(getter):
            return []
        try:
            assets = getter(class_name, True)
        except Exception:
            try:
                assets = getter(class_name)
            except Exception:
                return []
        paths = []
        for item in list(assets or [])[:_MAX_ASSETS]:
            path = _asset_path(item)
            if path:
                paths.append(path)
        return sorted(set(paths))

    def _selected_assets(self) -> List[Any]:
        module = self._u
        if module is None:
            return []
        library = safe_attr(module, "EditorUtilityLibrary")
        assets = _call(library, "get_selected_assets")
        return list(assets or [])

    def _asset_class_name(self, asset: Any) -> str:
        asset_class = safe_attr(asset, "asset_class")
        if asset_class:
            return str(asset_class)
        cls = _call(asset, "get_class")
        name = _call(cls, "get_name") if cls is not None else None
        if name:
            return str(name)
        return type(asset).__name__

    def _is_skeleton(self, asset: Any) -> bool:
        if asset is None:
            return False
        if "Skeleton" in self._asset_class_name(asset):
            return True
        return (callable(safe_attr(asset, "get_reference_skeleton"))
                or safe_attr(asset, "bone_tree") is not None)

    def _skeleton_of(self, asset: Any) -> Optional[Any]:
        if asset is None:
            return None
        skeleton = _call(asset, "get_skeleton")
        if skeleton is not None:
            return skeleton
        return _editor_prop(asset, ("skeleton", "skeletal_mesh_skeleton"))


# --------------------------------------------------------------------------- #
# 模块级工厂（runner.adapter_for 的唯一入口，签名固定为 **kwargs）
# --------------------------------------------------------------------------- #
def create_adapter(**kwargs: Any) -> UnrealAdapter:
    """按 ``tak.core.runner.adapter_for("unreal")`` 的约定创建适配器。

    可接受 ``version`` / ``python_version`` / ``data``：``data`` 为离线快照
    （裸快照或 ``{"snapshot": {...}}``）或 ``{"nodes"/"anims"/"attrs"/"skin"}``
    注入表，供无引擎环境下的单测与 CI 使用。
    """
    return UnrealAdapter(version=str(kwargs.get("version") or ""),
                         python_version=str(kwargs.get("python_version") or ""),
                         data=kwargs.get("data"))
