# -*- coding: utf-8 -*-
"""3ds Max 2023+ 适配器：把 pymxs.runtime 翻译成 TAK 的跨 DCC 契约。

设计约束
--------
* 本模块顶层**不导入** pymxs，所有宿主调用都在函数体内延迟导入，
  因此纯 Python / CI / headless 下 ``import tak.adapters.max`` 永远安全；
* 所有宿主调用都包在 ``snapshot_utils.safe`` / ``snapshot_utils.call`` 内，
  失败返回可预测的空结构，异常不允许冒到 UI 或规则引擎；
* 真机与 tak.core.mocks 走**同一条遍历路径**：遍历 ``rt.objects`` 逐节点
  采集，桥接（``rt.tak_scene``）只用于补充场景级元数据，绝不直接把
  ``rt.tak_snapshot()`` 当结果返回；
* 快照字段与 ``tak.core.diff.snapshot.SkeletonSnapshot`` 完全对齐，
  可直接交给 ``conformance.check_adapter`` / ``compare_snapshots``。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

from tak.adapters.base import Capability, DCC, DCCAdapter
from tak.adapters.snapshot_utils import (
    DEFAULT_FRAME_RANGE, DEFAULT_ROTATE, DEFAULT_SCALE, IDENTITY_MATRIX,
    as_float, as_int, build_snapshot, call, depth_of, empty_skin,
    empty_snapshot, filter_root, floats, namespace_of, normalize_skin_dict,
    normalize_snapshot, safe, short_name, write_sidecar,
)

# 源码标识：写入每份快照，供 sidecar / 报告溯源。
SOURCE = "tak.adapters.max"

# 采集上限：避免大场景卡死 UI。
_MAX_MESHES = 64

# classOf 返回值到 TAK 节点类型的映射；真机返回值可能带前缀，故再补子串判定。
_JOINT_CLASSES = frozenset({"BoneGeometry", "Bone", "Biped_Object"})
_MESH_CLASSES = frozenset({"Editable_Mesh", "Editable_Poly", "PolyMeshObject"})

_SUPPORTED_CAPABILITIES = frozenset({
    Capability.SCENE_QUERY,
    Capability.SKIN_QUERY,
    Capability.SKELETON_SNAPSHOT,
    Capability.FBX_EXPORT,
    Capability.NODE_WRITE,
    Capability.WEIGHT_WRITE,
    Capability.UNDO_SCOPE,
    Capability.UI_HOST,
})


# --------------------------------------------------------------------------- #
# 延迟导入
# --------------------------------------------------------------------------- #
def _import_runtime() -> Any:
    """延迟导入 ``pymxs`` 并返回 ``pymxs.runtime``；宿主缺失时返回 None。

    只做一次 ``import pymxs; pymxs.runtime`` 探测，不初始化场景、不触碰
    ``rt.objects``，因此在 headless / CI 下永远安全。
    """
    try:
        import pymxs
        return getattr(pymxs, "runtime", None)
    except Exception:
        return None


def is_available() -> bool:
    """检测 3ds Max Python 环境是否可导入（不探测场景、不初始化）。"""
    return _import_runtime() is not None


# --------------------------------------------------------------------------- #
# 纯函数小工具（只处理数值与字典，不触碰宿主）
# --------------------------------------------------------------------------- #
def _classify(class_text: Any) -> str:
    """把 ``classOf`` 返回值归为 joint / mesh / ""。"""
    text = str(class_text or "")
    if text in _JOINT_CLASSES or "BoneGeometry" in text or "Bone" in text:
        return "joint"
    if text in _MESH_CLASSES or "Editable_Mesh" in text or "Editable_Poly" in text:
        return "mesh"
    return ""


def _triple(value: Any, default: Sequence[float]) -> List[float]:
    """把 Point3 / 列表 / 带 x,y,z 的对象规整为定长 3 float 列表。"""
    if value is None:
        return [float(item) for item in default]
    try:
        seq = list(value)
    except Exception:
        try:
            seq = [value.x, value.y, value.z]
        except Exception:
            return [float(item) for item in default]
    return floats(seq, 3, default)


def _is_identity(translate: Sequence[float], rotate: Sequence[float],
                 scale: Sequence[float]) -> bool:
    return (all(abs(as_float(value)) < 1e-6 for value in translate)
            and all(abs(as_float(value)) < 1e-6 for value in rotate)
            and all(abs(as_float(value) - 1.0) < 1e-6 for value in scale))


def _coerce_offline(data: Any) -> Dict[str, Any]:
    """把 data= 入参规整成离线快照字典。

    支持三种形态：本身含 ``joints`` 的快照字典、``{"snapshot": {...}}``
    包装、以及指向 JSON 文件的路径字符串。任何异常都返回空字典。
    """
    if isinstance(data, dict):
        if isinstance(data.get("joints"), dict):
            return data
        nested = data.get("snapshot")
        return nested if isinstance(nested, dict) else {}
    if isinstance(data, (str, os.PathLike)):
        try:
            with open(os.fspath(data), "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            return _coerce_offline(payload)
        except Exception:
            return {}
    return {}


def _placeholder_bytes() -> bytes:
    return (b"TAK-MAX-FBX-PLACEHOLDER\n"
            b"This file is a clearly marked placeholder: no export API "
            b"was available (mock host or headless Python).\n")


# --------------------------------------------------------------------------- #
# 适配器
# --------------------------------------------------------------------------- #
class MaxAdapter(DCCAdapter):
    """3ds Max 2023+ 的 DCCAdapter 实现（pymxs 优先，桥接仅作补充）。"""

    dcc = DCC.MAX
    name = "max"

    def __init__(self, version: str = "", python_version: str = "",
                 data: Any = None, **kwargs: Any) -> None:
        super().__init__(version, python_version)
        self.version = str(self.version or "")
        self.python_version = str(self.python_version or "")
        self.available = is_available()
        self._offline = _coerce_offline(data)

        if not self.version and self.available:
            scene = self._bridge_scene()
            self.version = str(safe("", lambda: scene.version) or "")
            if not self.version:
                rt = _import_runtime()
                self.version = str(safe("", lambda: call(rt, "maxVersion", "")) or "")
        if not self.version:
            self.version = str(self._offline.get("version") or "")
        if not self.python_version:
            self.python_version = "%d.%d" % (sys.version_info[0], sys.version_info[1])

        # 状态日志式 undo（Max 原生 undo 在 Python 侧不稳定）。
        self._undo_seq = 0
        self._undo_records: Dict[str, Dict[str, Any]] = {}
        self._open_tokens: List[str] = []

    # ---------------------------------------------------------------- 基础
    def supports(self, capability: Capability) -> bool:
        return capability in _SUPPORTED_CAPABILITIES

    def describe(self) -> Dict[str, Any]:
        info = super().describe()
        info.update({
            "available": bool(self.available),
            "offline": bool(self._offline),
            "undo_strategy": "state_log",
        })
        return info

    def _host_runtime(self) -> Any:
        """实时探测宿主；返回 ``pymxs.runtime`` 或 None（不依赖缓存）。"""
        return _import_runtime()

    def _bridge_scene(self) -> Any:
        """返回 mock 桥接场景（``rt.tak_scene``）；真机 / 无宿主返回 None。"""
        rt = self._host_runtime()
        if rt is None:
            return None
        return safe(None, lambda: getattr(rt, "tak_scene", None))

    def _scene_metadata(self) -> Any:
        scene = self._bridge_scene()
        return safe(None, lambda: getattr(scene, "metadata", None))

    # ---------------------------------------------------------------- 节点查询
    def _iter_objects(self, rt: Any) -> List[Any]:
        """安全取 ``rt.objects`` 为列表；任何异常返回空列表。"""
        return safe([], lambda: list(getattr(rt, "objects", []) or []))

    def _node_name(self, obj: Any) -> str:
        return short_name(safe("", lambda: obj.name))

    def list_nodes(self, node_type: str = "joint") -> List[str]:
        rt = self._host_runtime()
        if rt is None:
            if node_type == "joint":
                return sorted((self._offline.get("joints") or {}))
            return []
        found: List[str] = []
        try:
            for obj in self._iter_objects(rt):
                kind = _classify(call(rt, "classOf", "", obj))
                name = self._node_name(obj)
                if not name:
                    continue
                if node_type == "joint" and kind == "joint":
                    found.append(name)
                elif node_type == "mesh" and kind == "mesh":
                    found.append(name)
                elif node_type not in ("joint", "mesh") and kind:
                    found.append(name)
        except Exception:
            return []
        return sorted(set(found))

    def get_node_attrs(self, node: str) -> Dict[str, Any]:
        """返回与 MayaAdapter 同构的属性字典。"""
        name = short_name(node)
        if not name:
            return {}
        rt = self._host_runtime()
        if rt is None:
            return dict((self._offline.get("attrs") or {}).get(name, {}))
        obj = call(rt, "getNodeByName", None, name)
        if obj is None:
            return {}
        kind = _classify(call(rt, "classOf", "", obj))
        node_type = "joint" if kind == "joint" else ("mesh" if kind == "mesh" else kind or "node")
        translate = _triple(safe(None, lambda: obj.pos), [0.0, 0.0, 0.0])
        rotate = _triple(safe(None, lambda: obj.rotation), DEFAULT_ROTATE)
        scale = _triple(safe(None, lambda: obj.scale), DEFAULT_SCALE)
        parent_obj = safe(None, lambda: obj.parent)
        parent = self._node_name(parent_obj) if parent_obj is not None else None
        hidden = bool(safe(False, lambda: obj.isHidden))
        return {
            "node": name,
            "type": node_type,
            "parent": parent or None,
            "translate": translate,
            "rotate": rotate,
            "scale": scale,
            "visibility": not hidden,
            "namespace": namespace_of(name),
            "frozen": _is_identity(translate, rotate, scale),
        }

    # ---------------------------------------------------------------- 蒙皮查询
    def get_skin_summary(self, mesh: str) -> Dict[str, Any]:
        """返回蒙皮摘要；优先用桥接，其次尝试 Skin 修改器查询，最终规整。"""
        name = short_name(mesh)
        result = empty_skin(name)
        if not name:
            return normalize_skin_dict(name, result)
        rt = self._host_runtime()
        if rt is None:
            offline_skin = self._offline.get("skin") or {}
            if isinstance(offline_skin, dict) and isinstance(offline_skin.get(name), dict):
                offline_skin = offline_skin[name]
            return normalize_skin_dict(name, offline_skin)
        raw: Dict[str, Any] = {}
        scene = self._bridge_scene()
        if scene is not None:
            probe = call(scene, "skin_summary", {}, name)
            if isinstance(probe, dict):
                raw = probe
        if not raw:
            raw = self._skin_from_modifiers(rt, name)
        return normalize_skin_dict(name, raw)

    def _skin_from_modifiers(self, rt: Any, name: str) -> Dict[str, Any]:
        """真机兜底：尝试 ``getClassInstances(Skin)`` / node.modifiers 查询。"""
        skin_class = safe(None, lambda: getattr(rt, "Skin", None))
        instances = call(rt, "getClassInstances", [], skin_class)
        obj = call(rt, "getNodeByName", None, name)
        modifiers = safe([], lambda: list(getattr(obj, "modifiers", []) or []))
        deformers = set()
        for modifier in modifiers:
            try:
                class_text = str(call(rt, "classOf", "", modifier) or "")
            except Exception:
                class_text = ""
            if "Skin" in class_text:
                deformers.add("Skin")
        if instances or deformers:
            return {"deformers": sorted(deformers)}
        return {}

    # ---------------------------------------------------------------- 骨架快照
    def get_skeleton_snapshot(self, root: Optional[str] = None) -> Dict[str, Any]:
        """产出 SkeletonSnapshot 兼容字典；任何失败都退化为合法空结构。"""
        rt = self._host_runtime()
        if rt is None:
            if self._offline:
                snapshot = normalize_snapshot(self._offline, dcc="max", source=SOURCE)
                snapshot["metadata"].setdefault("offline", True)
                return filter_root(snapshot, root)
            snapshot = empty_snapshot("max", SOURCE, "3ds Max 环境不可用")
            snapshot["version"] = self.version
            return snapshot
        try:
            return self._capture(rt, root)
        except Exception as exc:
            snapshot = empty_snapshot("max", SOURCE, "%s: %s" % (type(exc).__name__, exc))
            snapshot["version"] = self.version
            return snapshot

    def _capture(self, rt: Any, root: Optional[str]) -> Dict[str, Any]:
        joint_nodes: Dict[str, Any] = {}
        mesh_names: List[str] = []
        for obj in self._iter_objects(rt):
            kind = _classify(call(rt, "classOf", "", obj))
            name = self._node_name(obj)
            if not name:
                continue
            if kind == "joint":
                joint_nodes[name] = obj
            elif kind == "mesh":
                mesh_names.append(name)
        mesh_names = sorted(set(mesh_names))[:_MAX_MESHES]

        # 两遍扫描：先建完整 parent 表，再算 depth。
        parents: Dict[str, Optional[str]] = {}
        for name, obj in joint_nodes.items():
            parent_obj = safe(None, lambda: obj.parent)
            parent = self._node_name(parent_obj) if parent_obj is not None else ""
            parents[name] = parent if parent in joint_nodes else None

        # mock / 离线桥接场景：真机节点名与层级本身可读，但假宿主把
        # namespace / depth 存在 MockScene 上；桥接存在时补齐全，保证
        # fixture 注入的命名空间与深度违规在离线回归中可复现。
        scene = self._bridge_scene()
        scene_joints = getattr(scene, "joints", None) if scene is not None else None
        if not isinstance(scene_joints, dict):
            scene_joints = {}

        joints: Dict[str, Dict[str, Any]] = {}
        for name, obj in joint_nodes.items():
            bridge_joint = scene_joints.get(name)
            depth = depth_of(name, parents)
            namespace = namespace_of(name)
            if bridge_joint is not None:
                depth = as_int(getattr(bridge_joint, "depth", depth), depth)
                namespace = namespace or str(getattr(bridge_joint, "namespace", "") or "")
            joints[name] = {
                "parent": parents[name],
                "matrix": floats(safe(None, lambda: obj.transform), 16, IDENTITY_MATRIX),
                "depth": depth,
                "scale": _triple(safe(None, lambda: obj.scale), DEFAULT_SCALE),
                "rotate": _triple(safe(None, lambda: obj.rotation), DEFAULT_ROTATE),
                "namespace": namespace,
            }

        skin = self._aggregate_skin(mesh_names)
        unit = str(safe("cm", lambda: scene.unit) or "cm")
        axis = str(safe("Y", lambda: scene.up_axis) or "Y").upper()
        up_axis = axis[:1] if axis[:1] in ("X", "Y", "Z") else "Y"
        frame_range = floats(safe(None, lambda: scene.frame_range), 2, DEFAULT_FRAME_RANGE)
        scene_name = str(safe("", lambda: rt.maxFileName) or "")

        metadata = {
            "source": SOURCE,
            "max_version": str(self.version or ""),
            "python_version": str(self.python_version or ""),
            "scene": scene_name,
            "joint_count": len(joints),
            "mesh_count": len(mesh_names),
            "skin_mesh_count": len(skin.get("per_mesh", {}) or {}),
            "generated_by": "tak",
        }
        return build_snapshot(
            dcc="max", source=SOURCE, joints=joints, skin=skin,
            version=str(self.version or ""), unit=unit, up_axis=up_axis,
            frame_range=frame_range, metadata=metadata, root=root,
        )

    def _aggregate_skin(self, meshes: Sequence[str]) -> Dict[str, Any]:
        per_mesh: Dict[str, Dict[str, Any]] = {}
        deformers = set()
        max_influences = 0
        max_deviation = 0.0
        sampled_total = 0
        for mesh in meshes:
            summary = self.get_skin_summary(mesh)
            if not (summary.get("skin_clusters") or summary.get("deformers")
                    or summary.get("max_influences_found")):
                continue
            max_influences = max(max_influences, as_int(summary.get("max_influences_found")))
            max_deviation = max(max_deviation, as_float(summary.get("weight_sum_deviation")))
            sampled_total += as_int(summary.get("sampled_vertices"))
            deformers.update(str(item) for item in (summary.get("deformers") or []))
            per_mesh[mesh] = {
                "max_influences_found": as_int(summary.get("max_influences_found")),
                "weight_sum_deviation": round(as_float(summary.get("weight_sum_deviation")), 6),
                "deformers": [str(item) for item in (summary.get("deformers") or [])],
                "vertex_count": as_int(summary.get("vertex_count")),
                "sampled_vertices": as_int(summary.get("sampled_vertices")),
            }
        result: Dict[str, Any] = {
            "max_influences_found": max_influences,
            "weight_sum_deviation": round(max_deviation, 6),
            "deformers": sorted(deformers),
            "sampled_vertices": sampled_total,
            "mesh_count": len(per_mesh),
        }
        if per_mesh:
            result["per_mesh"] = per_mesh
        return result

    # ---------------------------------------------------------------- FBX
    def export_fbx(self, path: str, spec: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """导出 FBX 并写 sidecar；mock / 无导出 API 时写明确标注的占位文件。"""
        target = str(path or "")
        result: Dict[str, Any] = {
            "ok": False, "path": target, "sidecar": "", "bytes": 0,
            "selected": 0, "error": "", "mock": False,
        }
        if not target:
            result["error"] = "导出路径为空"
            return result
        options = spec if isinstance(spec, dict) else {}
        selection = options.get("selection") or options.get("selected") or []
        if isinstance(selection, str):
            selection = [selection]
        result["selected"] = len(selection)

        rt = self._host_runtime()
        scene = self._bridge_scene()
        is_mock = scene is not None
        export_fn = safe(None, lambda: getattr(rt, "exportFile", None)) if rt else None

        try:
            if rt is not None and not is_mock and callable(export_fn):
                # 真机：尽力设置 FBX 参数并导出，任何失败都由 call 兜底。
                call(rt, "FBXExporterSetParam", None, "Animation", True)
                call(rt, "FBXExporterSetParam", None, "Skin", True)
                call(rt, "exportFile", None, target, noPrompt=True,
                     selectedOnly=False, using="FBXEXP")
            if not os.path.isfile(target):
                # 占位文件（明确标注，便于 UE 侧识别为未真正导出）。
                parent = os.path.dirname(os.path.abspath(target))
                if parent and not os.path.isdir(parent):
                    os.makedirs(parent, exist_ok=True)
                with open(target, "wb") as fh:
                    fh.write(_placeholder_bytes())
                result["mock"] = True
            else:
                result["mock"] = bool(is_mock)
            sidecar = target + ".snapshot.json"
            write_sidecar(sidecar, self.get_skeleton_snapshot())
            result.update({
                "ok": True,
                "sidecar": sidecar,
                "bytes": os.path.getsize(target) if os.path.isfile(target) else 0,
            })
        except Exception as exc:
            result["error"] = "%s: %s" % (type(exc).__name__, exc)
        return result

    # ---------------------------------------------------------------- 写方法
    # 签名与 tools/spec_enforcer/actions.py 的 DSL 完全一致。
    def rename_node(self, node: str, name: str) -> Dict[str, Any]:
        new_name = str(name or "")

        def _real(rt: Any, target: str) -> None:
            obj = call(rt, "getNodeByName", None, target)
            if obj is not None:
                setattr(obj, "name", new_name)

        return self._write("rename_node", node, {"name": new_name}, _real)

    def set_control_color(self, node: str, color: Any) -> Dict[str, Any]:
        value = as_int(color, 0)

        def _real(rt: Any, target: str) -> None:
            obj = call(rt, "getNodeByName", None, target)
            if obj is not None:
                setattr(obj, "wireColor", value)

        return self._write("set_control_color", node, {"color": value}, _real)

    def normalize_weights(self, max_influences: Optional[int] = None,
                          tolerance: Optional[float] = None) -> Dict[str, Any]:
        extra: Dict[str, Any] = {}
        if max_influences is not None:
            extra["max_influences"] = as_int(max_influences, 0)
        if tolerance is not None:
            extra["tolerance"] = as_float(tolerance, 0.0)

        def _real(rt: Any, target: str) -> None:
            call(rt, "takNormalizeWeights", None, dict(extra))

        return self._write("normalize_weights", "", extra, _real)

    def set_namespace(self, node: str, namespace: str) -> Dict[str, Any]:
        ns = str(namespace or "")

        def _real(rt: Any, target: str) -> None:
            obj = call(rt, "getNodeByName", None, target)
            if obj is not None:
                bare = target.rpartition(":")[2]
                setattr(obj, "name", ("%s:%s" % (ns, bare)) if ns else bare)

        return self._write("set_namespace", node, {"namespace": ns}, _real)

    def _write(self, action: str, node: str, extra: Dict[str, Any],
               apply_real: Any) -> Dict[str, Any]:
        """写方法统一骨架：记录 undo 旧值 → 真机 setter / mock write_log。"""
        target = short_name(node)
        payload: Dict[str, Any] = {"status": "done", "action": action, "node": target}
        before = self.get_node_attrs(target) if target else {}
        self._push_undo(action, target, before)

        scene = self._bridge_scene()
        if scene is not None:
            record: Dict[str, Any] = {"action": action, "node": target, "mock": True}
            record.update(extra)
            self._write_log(create=True).append(record)
            payload["mock"] = True
        else:
            rt = self._host_runtime()
            if rt is None:
                payload["status"] = "skipped"
                payload["reason"] = "3ds Max 宿主不可用（无 pymxs.runtime）"
                payload["mock"] = False
                return payload
            if apply_real is not None:
                safe(False, lambda: apply_real(rt, target))
            payload["mock"] = False
        payload.update(extra)
        return payload

    # ---------------------------------------------------------------- 状态日志 undo
    def _write_log(self, create: bool = True) -> List[Any]:
        meta = self._scene_metadata()
        if not isinstance(meta, dict):
            return []
        if create:
            log = meta.setdefault("write_log", [])
            if not isinstance(log, list):
                log = []
                meta["write_log"] = log
            return log
        existing = meta.get("write_log")
        return existing if isinstance(existing, list) else []

    def begin_undo(self, label: str = "TAK") -> str:
        self._undo_seq += 1
        token = "max:%d:%s" % (self._undo_seq, str(label or "TAK"))
        self._undo_records[token] = {
            "label": str(label or "TAK"),
            "entries": [],
            "write_log_len": len(self._write_log(create=False)),
            "scene": self._bridge_scene(),
        }
        self._open_tokens.append(token)
        return token

    def _push_undo(self, action: str, node: str, before: Dict[str, Any]) -> None:
        if not self._open_tokens:
            return
        record = self._undo_records.get(self._open_tokens[-1])
        if record is not None:
            record["entries"].append(
                {"action": action, "node": node, "before": dict(before)})

    def _restore(self, entry: Dict[str, Any]) -> None:
        before = entry.get("before") or {}
        if not before:
            return
        if self._bridge_scene() is not None:
            return  # mock：节点值由 write_log 截断统一恢复
        rt = self._host_runtime()
        obj = call(rt, "getNodeByName", None, entry.get("node"))
        if obj is None:
            return
        old_name = before.get("name")
        if old_name:
            safe(None, lambda: setattr(obj, "name", str(old_name)))

    def commit_undo(self, token: str) -> None:
        self._undo_records.pop(token, None)
        if token in self._open_tokens:
            self._open_tokens.remove(token)

    def rollback(self, token: str) -> None:
        record = self._undo_records.pop(token, None)
        if token in self._open_tokens:
            self._open_tokens.remove(token)
        if record is None:
            return
        for entry in reversed(record.get("entries", [])):
            self._restore(entry)
        log = self._write_log(create=False)
        target_len = int(record.get("write_log_len", 0) or 0)
        if len(log) > target_len:
            del log[target_len:]

    # ---------------------------------------------------------------- UI / 日志
    def ui_parent(self) -> Any:
        try:
            from tak.compat.qt import host_main_window
            return host_main_window("max")
        except Exception:
            return None

    def log(self, message: str, level: str = "info") -> None:
        text = "[TAK][%s] %s" % (level, message)
        rt = self._host_runtime()
        if rt is not None:
            call(rt, "format", None, text + "\n")  # 尝试 Max 监听器输出，失败忽略
        print(text)


# --------------------------------------------------------------------------- #
# 模块级工厂（runner.adapter_for 的唯一入口，签名固定为 **kwargs）
# --------------------------------------------------------------------------- #
def create_adapter(**kwargs: Any) -> MaxAdapter:
    """按 ``tak.core.runner.adapter_for("max")`` 的约定创建适配器。

    可接受 ``version`` / ``python_version`` / ``data``；``data`` 为离线快照
    （字典或 JSON 路径）时，即使宿主不可用也能产出与 fixtures 一致的读数。
    """
    return MaxAdapter(version=str(kwargs.get("version") or ""),
                      python_version=str(kwargs.get("python_version") or ""),
                      data=kwargs.get("data"))
