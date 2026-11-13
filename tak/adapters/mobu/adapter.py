# -*- coding: utf-8 -*-
"""MotionBuilder 2023+ 适配器：把 pyfbsdk 翻译成 TAK 的跨 DCC 契约。

设计约束
--------
* 本模块顶层**不导入** pyfbsdk（也不导入任何 PySide / Maya / Max / Unreal），
  所有宿主调用都在函数体内延迟导入，因此纯 Python / CI / headless 下
  ``import tak.adapters.mobu`` 永远安全。
* 所有宿主调用都经 ``snapshot_utils.safe`` / ``snapshot_utils.call`` 兜底，
  失败返回可预测的空结构（0 / [] / {}），异常不允许冒到 UI 或规则引擎。
* 快照字段与 ``tak.core.diff.snapshot.SkeletonSnapshot`` 完全对齐，且刻意
  与 ``tak.adapters.maya.MayaAdapter`` 同构：unit / up_axis / frame_range 用
  规范默认值、namespace 由节点名推导、skin 走聚合，从而四端互比可比。
* 采集逻辑真机与 mock 走**同一条遍历路径**（``FBSystem().Scene.Models``），
  绝不把桥接的 ``tak_snapshot()`` 直接当结果返回。
* MoBu Python 没有可靠的原生事务，撤销采用**状态日志式回滚**：写方法改前
  记录旧值，rollback 逆序恢复；mock 下至少把 ``write_log`` 恢复到改前长度。
"""
from __future__ import annotations

import importlib
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

from tak.adapters.base import Capability, DCC, DCCAdapter
from tak.adapters.snapshot_utils import (DEFAULT_FRAME_RANGE, DEFAULT_ROTATE,
                                         DEFAULT_SCALE, IDENTITY_MATRIX,
                                         as_float, as_int, build_snapshot,
                                         call, depth_of, empty_skin,
                                         empty_snapshot, filter_root, floats,
                                         namespace_of, normalize_skin_dict,
                                         normalize_snapshot, safe, short_name,
                                         write_sidecar)

_SOURCE = "tak.adapters.mobu"
_MAX_MESHES = 64
_WEIGHT_EPSILON = 1e-5

# 声明的能力。CAPTURE.PLAYBLAST 在 MoBu Python 侧没有稳定可靠的接口，
# 因此**不声明**，并在 describe() 的 notes 里说明。
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
# 延迟导入与安全调用
# --------------------------------------------------------------------------- #
def _import_host() -> Any:
    """延迟导入 pyfbsdk；宿主不存在或注入 None 时返回 None，不抛异常。"""
    try:
        return importlib.import_module("pyfbsdk")
    except Exception:
        return None


def is_available() -> bool:
    """检测 MotionBuilder Python 环境：只做一次 ``import pyfbsdk`` 探测。

    不会触发任何场景初始化，因此在普通 Python / MoBu GUI 内都可安全调用。
    """
    return _import_host() is not None


def _host_version() -> str:
    """尽量读取 FBSystem().Version 作为宿主版本；失败返回空串。"""
    host = _import_host()
    if host is None:
        return ""
    cls = getattr(host, "FBSystem", None)
    system = safe(None, cls) if callable(cls) else None
    if system is None:
        return ""
    return str(safe("", getattr, system, "Version") or "")


def _model_name(model: Any) -> str:
    """安全读出模型短名（优先 Name，其次 LongName）。"""
    value = safe("", getattr, model, "Name")
    if not value:
        value = safe("", getattr, model, "LongName")
    return short_name(value)


def _is_identity(translate: Sequence[float], rotate: Sequence[float],
                 scale: Sequence[float]) -> bool:
    """translate≈0 / rotate≈0 / scale≈1 视为未冻结变换（frozen=False 的反面）。"""
    return (all(abs(as_float(value)) < 1e-6 for value in translate)
            and all(abs(as_float(value)) < 1e-6 for value in rotate)
            and all(abs(as_float(value) - 1.0) < 1e-6 for value in scale))


def _extract_snapshot(data: Any) -> Dict[str, Any]:
    """从 data= 里抽取出一份离线快照（兼容 {"snapshot": {...}} 包装）。"""
    if not isinstance(data, dict):
        return {}
    if isinstance(data.get("joints"), dict):
        return data
    nested = data.get("snapshot")
    return nested if isinstance(nested, dict) else {}


# --------------------------------------------------------------------------- #
# 适配器
# --------------------------------------------------------------------------- #
class MobuAdapter(DCCAdapter):
    """MotionBuilder 2023+ 的 DCCAdapter 实现。"""

    dcc = DCC.MOBU
    name = "mobu"
    max_sample_vertices: int = 512

    def __init__(self, version: str = "", python_version: str = "",
                 data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> None:
        super().__init__(version, python_version)
        self.version = str(self.version or "")
        self.python_version = str(self.python_version or "")
        self.available = is_available()
        self._offline = _extract_snapshot(data)
        if not self.version and self.available:
            self.version = _host_version()
        if not self.version:
            self.version = str((self._offline or {}).get("version") or "")
        if not self.python_version:
            self.python_version = "%d.%d" % (sys.version_info[0], sys.version_info[1])
        # 状态日志式撤销：每个未提交的块持有若干「逆操作」记录。
        self._undo_stack: List[Dict[str, Any]] = []
        self._undo_depth = 0

    # ---------------------------------------------------------------- 基础
    def supports(self, capability: Capability) -> bool:
        return capability in _SUPPORTED_CAPABILITIES

    def describe(self) -> Dict[str, Any]:
        info = super().describe()
        info.update({
            "available": bool(self.available),
            "offline": bool(self._offline),
            "undo_strategy": "state_log",
            "notes": [
                "MoBu Python 原生事务不可靠，undo 采用状态日志式回滚",
                "CAPTURE.PLAYBLAST 未声明：MoBu Python 无稳定的视口截图/录屏接口",
            ],
        })
        return info

    def _host(self) -> Any:
        """每次动态读取宿主模块，能正确反映 mock 的安装/卸载。"""
        return _import_host()

    def _is_mock(self, host: Any) -> bool:
        return host is not None and getattr(host, "tak_scene", None) is not None

    # ---------------------------------------------------------------- 场景遍历
    def _scene_models(self, host: Any) -> List[Any]:
        """遍历 FBSystem().Scene.Models；任何失败都返回空列表。"""
        cls = getattr(host, "FBSystem", None)
        system = safe(None, cls) if callable(cls) else None
        if system is None:
            return []
        scene = safe(None, getattr, system, "Scene")
        if scene is None:
            return []
        models = safe([], getattr, scene, "Models")
        try:
            return list(models or [])
        except Exception:
            return []

    def _is_skeleton(self, host: Any, model: Any) -> bool:
        cls = getattr(host, "FBModelSkeleton", None)
        if isinstance(cls, type):
            try:
                if isinstance(model, cls):
                    return True
            except Exception:
                pass
        try:
            return str(getattr(model, "model_type", "")) in ("FBModelSkeleton", "FBSkeleton")
        except Exception:
            return False

    def _is_mesh(self, host: Any, model: Any) -> bool:
        cls = getattr(host, "FBModelStatic", None)
        if isinstance(cls, type):
            try:
                if isinstance(model, cls):
                    return True
            except Exception:
                pass
        try:
            return str(getattr(model, "model_type", "")) in ("FBModelStatic", "FBMesh")
        except Exception:
            return False

    def _find_model(self, host: Any, name: str) -> Any:
        """优先 FBFindModelByName，失败再遍历 Scene.Models。"""
        finder = getattr(host, "FBFindModelByName", None)
        if callable(finder):
            model = safe(None, finder, name)
            if model is not None:
                return model
        target = short_name(name)
        for model in self._scene_models(host):
            if _model_name(model) == target:
                return model
        return None

    def _parent_name(self, model: Any) -> Optional[str]:
        parent = safe(None, getattr, model, "Parent")
        if parent is None:
            return None
        return _model_name(parent) or None

    def _triple(self, model: Any, attr: str,
                default: Sequence[float]) -> List[float]:
        return floats(safe(None, getattr, model, attr), 3, default)

    # ---------------------------------------------------------------- 节点查询
    def list_nodes(self, node_type: str = "joint") -> List[str]:
        host = self._host()
        if host is None:
            if node_type == "joint":
                return sorted((self._offline or {}).get("joints", {}))
            return []
        models = self._scene_models(host)
        if node_type == "joint":
            picked = [model for model in models if self._is_skeleton(host, model)]
        elif node_type in ("mesh", "geometry", "static"):
            picked = [model for model in models if self._is_mesh(host, model)]
        else:
            picked = []
        names: List[str] = []
        seen = set()
        for model in picked:
            value = _model_name(model)
            if value and value not in seen:
                seen.add(value)
                names.append(value)
        return sorted(names)

    def get_node_attrs(self, node: str) -> Dict[str, Any]:
        """返回与 MayaAdapter 同构的 transform 线索。"""
        name = short_name(node)
        if not name:
            return {}
        host = self._host()
        if host is None:
            attrs = (self._offline or {}).get("attrs")
            if isinstance(attrs, dict):
                return dict(attrs.get(name, {}))
            return {}
        model = self._find_model(host, name)
        if model is None:
            return {}
        try:
            translate = self._triple(model, "Translation", [0.0, 0.0, 0.0])
            rotate = self._triple(model, "Rotation", [0.0, 0.0, 0.0])
            scale = self._triple(model, "Scaling", [1.0, 1.0, 1.0])
            visibility = bool(safe(True, getattr, model, "Pickable"))
            if self._is_skeleton(host, model):
                node_type = "joint"
            elif self._is_mesh(host, model):
                node_type = "mesh"
            else:
                node_type = "model"
            return {
                "node": name,
                "type": node_type,
                "parent": self._parent_name(model),
                "translate": translate,
                "rotate": rotate,
                "scale": scale,
                "visibility": visibility,
                "namespace": namespace_of(name),
                "frozen": _is_identity(translate, rotate, scale),
            }
        except Exception:
            return {}

    # ---------------------------------------------------------------- 蒙皮查询
    def get_skin_summary(self, mesh: str) -> Dict[str, Any]:
        """返回统一结构的蒙皮摘要；桥接存在时取 tak_scene.skin_summary。"""
        name = short_name(mesh)
        empty = empty_skin(name)
        if not name:
            return empty
        host = self._host()
        if host is None:
            raw = (self._offline or {}).get("skin")
            return normalize_skin_dict(name, raw if isinstance(raw, dict) else {})
        try:
            scene = getattr(host, "tak_scene", None)
            if scene is not None:
                probe = call(scene, "skin_summary", None, name)
                if isinstance(probe, dict):
                    return normalize_skin_dict(name, probe)
            # 真机降级路径：尝试模型的 SkinDeformer / Geometry 与权重查询。
            model = self._find_model(host, name)
            raw: Dict[str, Any] = {}
            if model is not None:
                deformer = safe(None, getattr, model, "SkinDeformer")
                if deformer is not None:
                    influence_count = call(deformer, "GetInfluenceCount", 0)
                    raw["max_influences_found"] = as_int(influence_count)
                    raw["deformers"] = ["FBSkin"]
                geometry = safe(None, getattr, model, "Geometry")
                if geometry is not None:
                    raw["vertex_count"] = as_int(call(geometry, "VertexCount", 0))
            return normalize_skin_dict(name, raw)
        except Exception as exc:
            empty["error"] = "%s: %s" % (type(exc).__name__, exc)
            return empty

    # ---------------------------------------------------------------- 骨架快照
    def get_skeleton_snapshot(self, root: Optional[str] = None) -> Dict[str, Any]:
        """产出 SkeletonSnapshot 兼容字典；任何失败都退化为空快照。"""
        host = self._host()
        if host is None:
            offline = dict(self._offline or {})
            if offline:
                snapshot = normalize_snapshot(offline, dcc="mobu", source=_SOURCE)
                snapshot["metadata"].setdefault("offline", True)
                return filter_root(snapshot, root)
            snapshot = empty_snapshot("mobu", _SOURCE, "mobu 环境不可用")
            snapshot["version"] = self.version
            return snapshot
        try:
            return self._capture(host, root)
        except Exception as exc:
            snapshot = empty_snapshot("mobu", _SOURCE, "%s: %s" % (type(exc).__name__, exc))
            snapshot["version"] = self.version
            return snapshot

    def _capture(self, host: Any, root: Optional[str]) -> Dict[str, Any]:
        models = self._scene_models(host)
        skeletons = [model for model in models if self._is_skeleton(host, model)]
        mesh_models = [model for model in models if self._is_mesh(host, model)]

        joint_names: List[str] = []
        model_by_name: Dict[str, Any] = {}
        for model in skeletons:
            value = _model_name(model)
            if value and value not in model_by_name:
                joint_names.append(value)
                model_by_name[value] = model
        name_set = set(joint_names)

        # mock / 离线桥接场景：真机模型名与层级可读，但假宿主把
        # namespace / depth 单独存在 MockScene 上；桥接存在时补齐全。
        scene = getattr(host, "tak_scene", None)
        scene_joints = getattr(scene, "joints", None) if scene is not None else None
        if not isinstance(scene_joints, dict):
            scene_joints = {}

        # 两遍扫描：先建完整 parent 表（父不在关节集合内则视为根），再算 depth。
        parents: Dict[str, Optional[str]] = {}
        for name in joint_names:
            parent = self._parent_name(model_by_name[name])
            parents[name] = parent if parent in name_set else None
        joints: Dict[str, Dict[str, Any]] = {}
        for name in joint_names:
            model = model_by_name[name]
            bridge_joint = scene_joints.get(name)
            depth = depth_of(name, parents)
            namespace = namespace_of(name)
            if bridge_joint is not None:
                depth = as_int(getattr(bridge_joint, "depth", depth), depth)
                namespace = namespace or str(getattr(bridge_joint, "namespace", "") or "")
            joints[name] = {
                "parent": parents[name],
                "matrix": floats(safe(None, getattr, model, "Matrix"), 16, IDENTITY_MATRIX),
                "depth": depth,
                "scale": self._triple(model, "Scaling", DEFAULT_SCALE),
                "rotate": self._triple(model, "Rotation", DEFAULT_ROTATE),
                "namespace": namespace,
            }

        meshes: List[str] = []
        seen = set()
        for model in mesh_models:
            value = _model_name(model)
            if value and value not in seen:
                seen.add(value)
                meshes.append(value)
        skin = self._aggregate_skin(host, meshes)

        frame_range = self._frame_range(host)
        if scene is not None:
            raw_range = getattr(scene, "frame_range", None)
            if isinstance(raw_range, (list, tuple)) and len(raw_range) == 2:
                frame_range = ([float(raw_range[0]), float(raw_range[1])], "bridge")
        unit = str(getattr(scene, "unit", "") or "cm")
        axis = str(getattr(scene, "up_axis", "") or "Y").upper()
        up_axis = axis[:1] if axis[:1] in ("X", "Y", "Z") else "Y"
        metadata: Dict[str, Any] = {
            "source": _SOURCE,
            "mobu_version": str(self.version),
            "python_version": str(self.python_version),
            "scene": self._scene_name(host),
            "joint_count": len(joints),
            "mesh_count": len(meshes),
            "skin_mesh_count": len(skin.get("per_mesh", {}) or {}),
            "frame_range_source": frame_range[1],
            "generated_by": "tak",
        }
        return build_snapshot(
            dcc="mobu", source=_SOURCE, joints=joints, skin=skin,
            version=self.version, unit=unit, up_axis=up_axis,
            frame_range=frame_range[0], metadata=metadata, root=root)

    def _aggregate_skin(self, host: Any, meshes: Sequence[str]) -> Dict[str, Any]:
        """按网格聚合蒙皮摘要（与 MayaAdapter._aggregate_skin 同构）。"""
        per_mesh: Dict[str, Dict[str, Any]] = {}
        deformers = set()
        max_influences = 0
        max_deviation = 0.0
        sampled_total = 0
        unique = list(dict.fromkeys(meshes))[:_MAX_MESHES]
        for mesh in unique:
            summary = self.get_skin_summary(mesh)
            if not (summary.get("skin_clusters") or summary.get("deformers")):
                continue
            max_influences = max(max_influences, as_int(summary.get("max_influences_found")))
            max_deviation = max(max_deviation, as_float(summary.get("weight_sum_deviation")))
            sampled_total += as_int(summary.get("sampled_vertices"))
            deformers.update(summary.get("deformers", []) or [])
            per_mesh[mesh] = {
                "max_influences_found": as_int(summary.get("max_influences_found")),
                "weight_sum_deviation": as_float(summary.get("weight_sum_deviation")),
                "deformers": list(summary.get("deformers", []) or []),
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

    def _frame_range(self, host: Any) -> Any:
        """帧范围及其来源；MoBu Python 无稳定接口，安全尝试后退回规范默认值。"""
        cls = getattr(host, "FBPlayerControl", None)
        player = safe(None, cls) if callable(cls) else None
        if player is not None:
            start = safe(None, getattr, player, "LoopStartTime")
            stop = safe(None, getattr, player, "LoopStopTime")
            get_start = getattr(start, "GetFrame", None) if start is not None else None
            get_stop = getattr(stop, "GetFrame", None) if stop is not None else None
            if callable(get_start) and callable(get_stop):
                low = as_float(safe(None, get_start))
                high = as_float(safe(None, get_stop))
                return [min(low, high), max(low, high)], "player_control"
        return list(DEFAULT_FRAME_RANGE), "default"

    def _scene_name(self, host: Any) -> str:
        cls = getattr(host, "FBApplication", None)
        app = safe(None, cls) if callable(cls) else None
        if app is None:
            return ""
        return str(safe("", getattr, app, "FBXFileName") or "")

    # ---------------------------------------------------------------- FBX 导出
    def export_fbx(self, path: str, spec: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """导出 FBX 并在同目录写一份 sidecar 快照 JSON；mock 下写占位文件。"""
        target = str(path or "")
        result: Dict[str, Any] = {"ok": False, "path": target, "sidecar": "",
                                  "bytes": 0, "selected": 0, "error": "", "mock": False}
        if not target:
            result["error"] = "导出路径为空"
            return result
        host = self._host()
        if host is None:
            result["error"] = "mobu 环境不可用"
            return result
        try:
            mock = self._is_mock(host)
            result["mock"] = mock
            options = dict(spec or {})
            selection = options.get("selection") or options.get("selected") or []
            if isinstance(selection, str):
                selection = [selection]
            result["selected"] = len(selection) if selection else len(
                [m for m in self._scene_models(host)
                 if self._is_skeleton(host, m) or self._is_mesh(host, m)])
            if mock:
                self._write_placeholder(target)
            else:
                self._native_export(host, target, options)
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

    @staticmethod
    def _write_placeholder(target: str) -> None:
        """写一份明确标注的占位文件，避免被误认为真实 FBX。"""
        parent = os.path.dirname(os.path.abspath(target))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(b"TAK MotionBuilder mock FBX placeholder - NOT a real FBX file\n")

    @staticmethod
    def _native_export(host: Any, target: str, spec: Dict[str, Any]) -> None:
        """真机：安全尝试 FBApplication().FileExport / FbxOptions。"""
        cls = getattr(host, "FBApplication", None)
        app = safe(None, cls) if callable(cls) else None
        if app is None:
            return
        opt_cls = getattr(host, "FbxOptions", None)
        options = safe(None, opt_cls) if callable(opt_cls) else None
        if options is not None:
            call(app, "FileExport", None, target, options)
        else:
            call(app, "FileExport", None, target)

    # ---------------------------------------------------------------- 状态日志撤销
    def begin_undo(self, label: str = "TAK") -> str:
        self._undo_depth += 1
        token = "mobu:%d:%s" % (self._undo_depth, label or "TAK")
        self._undo_stack.append({"token": token, "label": label, "records": []})
        return token

    def commit_undo(self, token: str) -> None:
        self._drop_block(token)

    def rollback(self, token: str) -> None:
        block = self._drop_block(token)
        if block is None:
            return
        for record in reversed(block["records"]):
            self._restore(record)

    def _drop_block(self, token: str) -> Optional[Dict[str, Any]]:
        for index in range(len(self._undo_stack) - 1, -1, -1):
            if self._undo_stack[index]["token"] == token:
                return self._undo_stack.pop(index)
        return None

    def _record(self, record: Any) -> None:
        """把一条「逆操作」记到当前所有未提交的块（支持嵌套）。"""
        for block in self._undo_stack:
            block["records"].append(record)

    @staticmethod
    def _restore(record: Any) -> None:
        kind = record[0]
        if kind == "log":
            # ("log", list_ref, length_before)：把 write_log 截断到改前长度。
            log, before = record[1], record[2]
            try:
                del log[before:]
            except Exception:
                pass
        elif kind == "attr":
            # ("attr", model, attr, old_value)：恢复宿主属性旧值。
            model, attr, old = record[1], record[2], record[3]
            safe(None, setattr, model, attr, old)

    def _write_log(self, host: Any) -> Any:
        scene = getattr(host, "tak_scene", None)
        if scene is None:
            return None
        metadata = getattr(scene, "metadata", None)
        if not isinstance(metadata, dict):
            return None
        log = metadata.get("write_log")
        if not isinstance(log, list):
            log = []
            metadata["write_log"] = log
        return log

    def _log_write(self, host: Any, entry: Dict[str, Any]) -> None:
        log = self._write_log(host)
        if log is None:
            return
        before = len(log)
        self._record(("log", log, before))
        log.append(dict(entry))

    # ---------------------------------------------------------------- 写方法
    def rename_node(self, node: str, name: str) -> Dict[str, Any]:
        target = short_name(node)
        new_name = str(name or "")
        result: Dict[str, Any] = {"ok": False, "op": "rename_node", "node": target,
                                  "name": new_name, "mock": False}
        host = self._host()
        if host is None:
            result["error"] = "mobu 环境不可用"
            return result
        if self._is_mock(host):
            self._log_write(host, {"op": "rename_node", "node": target, "name": new_name})
            result.update({"ok": True, "mock": True})
            return result
        model = self._find_model(host, target)
        if model is None:
            result["error"] = "找不到模型 %s" % target
            return result
        old = safe("", getattr, model, "Name")
        self._record(("attr", model, "Name", old))
        safe(None, setattr, model, "Name", new_name)
        result.update({"ok": True, "old": old})
        return result

    def set_control_color(self, node: str, color: Any) -> Dict[str, Any]:
        target = short_name(node)
        try:
            value = int(color)
        except (TypeError, ValueError):
            value = 0
        result: Dict[str, Any] = {"ok": False, "op": "set_control_color",
                                  "node": target, "color": value, "mock": False}
        host = self._host()
        if host is None:
            result["error"] = "mobu 环境不可用"
            return result
        if self._is_mock(host):
            self._log_write(host, {"op": "set_control_color", "node": target, "color": value})
            result.update({"ok": True, "mock": True})
            return result
        model = self._find_model(host, target)
        if model is None:
            result["error"] = "找不到模型 %s" % target
            return result
        old = safe(None, getattr, model, "Color")
        self._record(("attr", model, "Color", old))
        color_obj = self._make_color(host, value)
        safe(None, setattr, model, "Color", color_obj)
        result["ok"] = True
        return result

    @staticmethod
    def _make_color(host: Any, value: int) -> Any:
        cls = getattr(host, "FBColor", None)
        if callable(cls):
            obj = safe(None, cls, float(value), 0.0, 0.0)
            if obj is not None:
                return obj
        return value

    def normalize_weights(self, max_influences: Optional[int] = None,
                          tolerance: Optional[float] = None) -> Dict[str, Any]:
        result: Dict[str, Any] = {"ok": False, "op": "normalize_weights",
                                  "max_influences": as_int(max_influences) if max_influences is not None else None,
                                  "tolerance": as_float(tolerance) if tolerance is not None else None,
                                  "mock": False}
        host = self._host()
        if host is None:
            result["error"] = "mobu 环境不可用"
            return result
        if self._is_mock(host):
            self._log_write(host, {"op": "normalize_weights",
                                   "max_influences": result["max_influences"],
                                   "tolerance": result["tolerance"]})
            result.update({"ok": True, "mock": True})
            return result
        # 真机：安全尝试对网格的 SkinDeformer 做权重归一化。
        touched = 0
        for model in self._scene_models(host):
            if not self._is_mesh(host, model):
                continue
            deformer = safe(None, getattr, model, "SkinDeformer")
            if deformer is None:
                continue
            call(deformer, "NormalizeWeights", None)
            touched += 1
        result.update({"ok": True, "meshes": touched})
        return result

    def set_namespace(self, node: str, namespace: str) -> Dict[str, Any]:
        target = short_name(node)
        ns = str(namespace or "")
        result: Dict[str, Any] = {"ok": False, "op": "set_namespace",
                                  "node": target, "namespace": ns, "mock": False}
        host = self._host()
        if host is None:
            result["error"] = "mobu 环境不可用"
            return result
        if self._is_mock(host):
            self._log_write(host, {"op": "set_namespace", "node": target, "namespace": ns})
            result.update({"ok": True, "mock": True})
            return result
        model = self._find_model(host, target)
        if model is None:
            result["error"] = "找不到模型 %s" % target
            return result
        old = safe("", getattr, model, "Name")
        base = old.rpartition(":")[2] or old
        new_name = "%s:%s" % (ns, base) if ns else base
        self._record(("attr", model, "Name", old))
        safe(None, setattr, model, "Name", new_name)
        result.update({"ok": True, "old": old, "name": new_name})
        return result

    # ---------------------------------------------------------------- UI / 日志
    def ui_parent(self) -> Any:
        try:
            from tak.compat.qt import host_main_window
            return host_main_window("mobu")
        except Exception:
            return None

    def log(self, message: str, level: str = "info") -> None:
        text = "[TAK][%s] %s" % (level, message)
        host = self._host()
        tracer = getattr(host, "FBTrace", None) if host is not None else None
        if callable(tracer):
            if safe(None, tracer, text + "\n") is not None:
                return
        print(text)


# --------------------------------------------------------------------------- #
# 模块级工厂（runner.adapter_for("mobu") 的唯一入口，签名固定为 **kwargs）
# --------------------------------------------------------------------------- #
def create_adapter(**kwargs: Any) -> MobuAdapter:
    """按 ``tak.core.runner.adapter_for("mobu")`` 的约定创建适配器。

    可接受 ``version`` / ``python_version`` / ``data``；``data`` 为离线快照时，
    即使宿主不可用也能产出与 fixtures 一致的读数。
    """
    return MobuAdapter(version=str(kwargs.get("version") or ""),
                       python_version=str(kwargs.get("python_version") or ""),
                       data=kwargs.get("data"))
