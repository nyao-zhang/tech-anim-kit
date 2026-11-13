# -*- coding: utf-8 -*-
"""Maya 2023+ 适配器：把 maya.cmds / maya.OpenMaya 翻译成 TAK 的跨 DCC 契约。

设计约束
--------
* 本模块顶层**不导入** maya，所有宿主调用都在函数体内延迟导入，
  因此纯 Python / CI / headless 下 ``import tak.adapters.maya`` 永远安全。
* 所有宿主调用都包在 try/except 内：失败返回可预测的空结构（0 / [] / {}），
  异常不允许冒到 UI 或规则引擎。
* 快照字段与 ``tak.core.diff.snapshot.SkeletonSnapshot`` 完全对齐，
  可直接交给 ``tak.core.runner.evaluate`` 与 ``diff_snapshots``。
"""
from __future__ import annotations

import importlib
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from tak.adapters.base import Capability, DCC, DCCAdapter

IDENTITY_MATRIX: List[float] = [1.0, 0, 0, 0, 0, 1.0, 0, 0,
                                0, 0, 1.0, 0, 0, 0, 0, 1.0]
DEFAULT_FRAME_RANGE: List[float] = [1.0, 120.0]

# 大数据量保护：蒙皮采样与网格遍历都要有硬上限，避免大场景卡死 UI。
_MAX_SAMPLE_VERTICES = 512
_MAX_MESHES = 64
_MAX_PARENT_DEPTH = 128
_WEIGHT_EPSILON = 1e-5

# 规范里允许的常见变形器类型；skinCluster 由独立的簇查询补齐。
_DEFORMER_TYPES = frozenset({
    "skinCluster", "blendShape", "cluster", "wrap", "nonLinear",
    "wire", "softMod", "deltaMush", "tweak", "jiggle", "ffd",
})

_SUPPORTED_CAPABILITIES = frozenset({
    Capability.SCENE_QUERY,
    Capability.SKIN_QUERY,
    Capability.SKELETON_SNAPSHOT,
    Capability.FBX_EXPORT,
    Capability.UNDO_SCOPE,
    Capability.UI_HOST,
    # P4.4：写方法已落地，DSL 动作不再只是安全 no-op。
    Capability.NODE_WRITE,
    Capability.WEIGHT_WRITE,
})


# --------------------------------------------------------------------------- #
# 延迟导入与安全调用
# --------------------------------------------------------------------------- #
def _cmds_module() -> Any:
    """延迟导入 ``maya.cmds``；宿主不存在时抛 ImportError 由调用方兜底。"""
    return importlib.import_module("maya.cmds")


def is_available() -> bool:
    """检测 Maya Python 环境是否可导入。

    只做 ``import maya.cmds``，**不会**触发 ``maya.standalone.initialize()``，
    因此在普通 Python / mayapy / Maya GUI 内都可安全调用。
    """
    try:
        return _cmds_module() is not None
    except Exception:
        return False


def _safe(default: Any, func: Any, *args: Any, **kwargs: Any) -> Any:
    """调用宿主 API，任何异常都退回 default。"""
    try:
        value = func(*args, **kwargs)
    except Exception:
        return default
    return default if value is None else value


def _get(cmds: Any, name: str, default: Any = None, *args: Any, **kwargs: Any) -> Any:
    """按名字安全调用 ``cmds.<name>``：属性缺失、调用失败都退回 default。

    直接写 ``_safe(default, cmds.xform, ...)`` 时，``cmds.xform`` 的属性查找
    发生在函数调用之前，属性不存在会抛 AttributeError；本函数把查找也包进兜底。
    """
    func = getattr(cmds, name, None)
    if not callable(func):
        return default
    try:
        value = func(*args, **kwargs)
    except Exception:
        return default
    return default if value is None else value


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _floats(value: Any, size: int, default: Sequence[float]) -> List[float]:
    """把宿主返回值规整为定长 float 列表。"""
    if isinstance(value, (list, tuple)) and len(value) == 1 \
            and isinstance(value[0], (list, tuple)):
        value = value[0]
    if not isinstance(value, (list, tuple)) or len(value) != size:
        return [float(item) for item in default]
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return [float(item) for item in default]


def _short_name(name: Any) -> str:
    """去掉 DAG 长路径前缀，保留 namespace（``ns:joint``）。"""
    text = str(name or "")
    return text.rsplit("|", 1)[-1]


def _namespace_of(name: str) -> str:
    return name.rpartition(":")[0] if ":" in name else ""


def _unique(items: Sequence[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


# --------------------------------------------------------------------------- #
# 适配器
# --------------------------------------------------------------------------- #
class MayaAdapter(DCCAdapter):
    """Maya 2023+ 的 DCCAdapter 实现（cmds 优先，OpenMaya 仅用于日志）。"""

    dcc = DCC.MAYA
    name = "maya"
    max_sample_vertices: int = _MAX_SAMPLE_VERTICES

    def __init__(self, version: str = "", python_version: str = "",
                 data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> None:
        super().__init__(version, python_version)
        self.version = str(self.version or "")
        self.python_version = str(self.python_version or "")
        self.available = is_available()
        self._offline = _extract_snapshot(data)
        if not self.version and self.available:
            self.version = str(_safe("", lambda: _cmds_module().about(version=True)))
        if not self.version:
            self.version = str((self._offline or {}).get("version") or "")
        if not self.python_version:
            self.python_version = "%d.%d" % (sys.version_info[0], sys.version_info[1])
        self._undo_depth = 0

    # ---------------------------------------------------------------- 基础
    def supports(self, capability: Capability) -> bool:
        return capability in _SUPPORTED_CAPABILITIES

    def describe(self) -> Dict[str, Any]:
        info = super().describe()
        info.update({"available": bool(self.available),
                     "offline": bool(self._offline),
                     "max_sample_vertices": self.max_sample_vertices})
        return info

    def _cmds(self) -> Optional[Any]:
        if not self.available:
            return None
        try:
            return _cmds_module()
        except Exception:
            return None

    # ---------------------------------------------------------------- 节点查询
    def list_nodes(self, node_type: str = "joint") -> List[str]:
        cmds = self._cmds()
        if cmds is None:
            if node_type == "joint":
                return sorted((self._offline or {}).get("joints", {}))
            return []
        try:
            names = cmds.ls(type=node_type) or []
        except Exception:
            return []
        return sorted(_unique([_short_name(name) for name in names if name]))

    def get_node_attrs(self, node: str) -> Dict[str, Any]:
        """返回 transform 线索：translate / rotate / scale / visibility / namespace / frozen。"""
        name = _short_name(node)
        if not name:
            return {}
        cmds = self._cmds()
        if cmds is None:
            return {}
        try:
            if not bool(_get(cmds, "objExists", True, name)):
                return {}
            node_type = str(_get(cmds, "nodeType", "", name))
            if not node_type:
                return {}
            translate = self._triple(cmds, name, "translate", [0.0, 0.0, 0.0])
            rotate = self._triple(cmds, name, "rotate", [0.0, 0.0, 0.0])
            scale = self._triple(cmds, name, "scale", [1.0, 1.0, 1.0])
            visibility = bool(_get(cmds, "getAttr", True, "%s.visibility" % name))
            parent = self._parent_of(cmds, name)
            return {
                "node": name,
                "type": node_type,
                "parent": parent,
                "translate": translate,
                "rotate": rotate,
                "scale": scale,
                "visibility": visibility,
                "namespace": _namespace_of(name),
                "frozen": _is_identity(translate, rotate, scale),
            }
        except Exception:
            return {}

    # ---------------------------------------------------------------- 蒙皮查询
    def get_skin_summary(self, mesh: str) -> Dict[str, Any]:
        """返回 skinCluster 摘要；无蒙皮 / 查询失败时返回可预测的零值结构。"""
        name = _short_name(mesh)
        empty = _empty_skin(name)
        if not name:
            return empty
        cmds = self._cmds()
        if cmds is None:
            return empty
        try:
            # 兼容 tak.core.mocks 的桥接：mock 的 skinCluster(mesh) 直接返回摘要 dict。
            if getattr(cmds, "tak_scene", None) is not None:
                probe = _get(cmds, "skinCluster", None, name)
                if isinstance(probe, dict):
                    return _normalize_skin_dict(name, probe)

            clusters = self._skin_clusters(cmds, name)
            deformers = self._deformers(cmds, name)
            transform = self._transform_of(cmds, name)
            influences: List[str] = []
            if clusters:
                raw = _get(cmds, "skinCluster", [], clusters[0], q=True, influence=True)
                influences = [_short_name(item) for item in raw if item]

            vertex_count = self._vertex_count(cmds, transform or name)
            sampled, max_influences, max_deviation, weights_error = \
                self._sample_weights(cmds, clusters[0] if clusters else "", transform or name,
                                     vertex_count)
            result = _empty_skin(name)
            result.update({
                "skin_clusters": list(clusters),
                "influences": influences,
                "max_influences_found": max_influences,
                "weight_sum_deviation": max_deviation,
                "deformers": sorted(set(deformers) | ({"skinCluster"} if clusters else set())),
                "vertex_count": vertex_count,
                "sampled_vertices": sampled,
                "truncated": bool(vertex_count > sampled),
            })
            if weights_error:
                result["sampling_error"] = weights_error
            return result
        except Exception as exc:
            empty["error"] = "%s: %s" % (type(exc).__name__, exc)
            return empty

    # ---------------------------------------------------------------- 骨架快照
    def get_skeleton_snapshot(self, root: Optional[str] = None) -> Dict[str, Any]:
        """产出 SkeletonSnapshot 兼容字典；任何失败都退化为空快照。"""
        cmds = self._cmds()
        if cmds is None:
            offline = dict(self._offline or {})
            if offline:
                snapshot = _normalize_snapshot(offline)
                snapshot["metadata"].setdefault("offline", True)
                return _filter_root(snapshot, root)
            snapshot = _empty_snapshot("maya 环境不可用")
            snapshot["version"] = self.version
            return snapshot
        try:
            snapshot = self._capture(cmds, root)
            return snapshot
        except Exception as exc:
            snapshot = _empty_snapshot("%s: %s" % (type(exc).__name__, exc))
            snapshot["version"] = self.version
            return snapshot

    # ---------------------------------------------------------------- 写操作
    # P4.4：spec_enforcer 的 DSL 动作通过 getattr 调用下面四个方法。它们只在
    # ACTION_REGISTRY.execute 开启的 undo chunk 内被调用，真机异常必须冒泡，
    # 由执行器整体 rollback；宿主不可用 / 节点不存在才返回可预测的 skipped。
    def rename_node(self, node: str, name: str) -> Dict[str, Any]:
        """重命名节点（返回实际新名，Maya 可能自动追加序号避免重名）。"""
        old, target = _short_name(node), _short_name(name)
        if not old or not target:
            return _write_result("rename_node", "skipped", reason="节点名或目标名为空")
        cmds = self._cmds()
        if cmds is None:
            return _write_result("rename_node", "skipped", reason="maya 环境不可用")
        scene = getattr(cmds, "tak_scene", None)
        if scene is not None:
            self._mock_write(scene, "rename_node", node=old, name=target)
            return _write_result("rename_node", "done", node=old, name=target, mock=True)
        if old == target:
            return _write_result("rename_node", "skipped", reason="目标名与当前名相同")
        if not bool(_get(cmds, "objExists", False, old)):
            return _write_result("rename_node", "skipped", reason="节点不存在")
        actual = _short_name(cmds.rename(old, target) or target)
        self.log("rename_node %s -> %s 完成" % (old, actual))
        return _write_result("rename_node", "done", node=old, name=actual)

    def set_control_color(self, node: str, color: int) -> Dict[str, Any]:
        """设置控制器 override color（Maya 颜色索引 0-31）。"""
        old = _short_name(node)
        index = max(0, min(31, _as_int(color, 0)))
        cmds = self._cmds()
        if cmds is None:
            return _write_result("set_control_color", "skipped", reason="maya 环境不可用")
        scene = getattr(cmds, "tak_scene", None)
        if scene is not None:
            self._mock_write(scene, "set_control_color", node=old, color=index)
            return _write_result("set_control_color", "done", node=old, color=index, mock=True)
        if not bool(_get(cmds, "objExists", False, old)):
            return _write_result("set_control_color", "skipped", reason="节点不存在")
        cmds.setAttr("%s.overrideEnabled" % old, True)
        cmds.setAttr("%s.overrideColor" % old, index)
        self.log("set_control_color %s -> %d 完成" % (old, index))
        return _write_result("set_control_color", "done", node=old, color=index)

    def normalize_weights(self, max_influences: Optional[int] = None,
                          tolerance: Optional[float] = None) -> Dict[str, Any]:
        """归一化全部 skinCluster 的采样顶点权重，可先裁剪到影响骨上限。

        真机逐顶点写回 ``skinPercent -tv``；任何写入异常都冒泡给 DSL 执行器，
        由外层 undo chunk 整体回滚。采样上限沿用 ``max_sample_vertices``。
        """
        limit = max(0, _as_int(max_influences, 0))
        epsilon = _as_float(tolerance, _WEIGHT_EPSILON) if tolerance is not None \
            else _WEIGHT_EPSILON
        cmds = self._cmds()
        if cmds is None:
            return _write_result("normalize_weights", "skipped", reason="maya 环境不可用")
        scene = getattr(cmds, "tak_scene", None)
        if scene is not None:
            self._mock_write(scene, "normalize_weights", max_influences=limit,
                             tolerance=epsilon)
            return _write_result("normalize_weights", "done", changed_vertices=0,
                                 clusters=0, mock=True)
        clusters = [_short_name(item) for item in
                    (_get(cmds, "ls", [], type="skinCluster") or []) if item]
        changed = 0
        for cluster in clusters[:_MAX_MESHES]:
            influences = [_short_name(item) for item in
                          (_get(cmds, "skinCluster", [], cluster, q=True,
                                influence=True) or []) if item]
            geometries = [str(item) for item in
                          (_get(cmds, "skinCluster", [], cluster, q=True,
                                geometry=True) or []) if item]
            if not influences:
                continue
            for geometry in geometries[:_MAX_MESHES]:
                count = self._vertex_count(cmds, geometry)
                for index in range(min(count, int(self.max_sample_vertices))):
                    vertex = "%s.vtx[%d]" % (_short_name(geometry).rsplit("|", 1)[-1], index)
                    values = _get(cmds, "skinPercent", [], cluster, vertex,
                                  q=True, value=True)
                    if not values or len(values) != len(influences):
                        continue
                    pairs = [(bone, _as_float(weight))
                             for bone, weight in zip(influences, values)
                             if _as_float(weight) > epsilon]
                    if limit:
                        pairs = sorted(pairs, key=lambda item: item[1], reverse=True)[:limit]
                    total = sum(weight for _, weight in pairs)
                    if total <= 0:
                        continue
                    cmds.skinPercent(cluster, vertex,
                                     tv=[(bone, weight / total) for bone, weight in pairs],
                                     normalize=True)
                    changed += 1
        self.log("normalize_weights 完成：%d 个顶点 / %d 个 skinCluster"
                 % (changed, len(clusters)))
        return _write_result("normalize_weights", "done", changed_vertices=changed,
                             clusters=len(clusters))

    def set_namespace(self, node: str, namespace: str) -> Dict[str, Any]:
        """把节点移入命名空间；空字符串表示移回根命名空间。"""
        old = _short_name(node)
        target_ns = str(namespace or "").strip().strip(":")
        cmds = self._cmds()
        if cmds is None:
            return _write_result("set_namespace", "skipped", reason="maya 环境不可用")
        scene = getattr(cmds, "tak_scene", None)
        if scene is not None:
            self._mock_write(scene, "set_namespace", node=old, namespace=target_ns)
            return _write_result("set_namespace", "done", node=old,
                                 namespace=target_ns, mock=True)
        if not bool(_get(cmds, "objExists", False, old)):
            return _write_result("set_namespace", "skipped", reason="节点不存在")
        if target_ns and not bool(_get(cmds, "namespace", False, exists=target_ns)):
            cmds.namespace(add=target_ns)
        bare = old.rsplit(":", 1)[-1]
        target = ("%s:%s" % (target_ns, bare)) if target_ns else bare
        if target != old:
            actual = _short_name(cmds.rename(old, target) or target)
        else:
            actual = old
        self.log("set_namespace %s -> %s 完成" % (old, actual))
        return _write_result("set_namespace", "done", node=old, namespace=target_ns,
                             name=actual)

    @staticmethod
    def _mock_write(scene: Any, action: str, **payload: Any) -> None:
        """离线 mock 桥接：把写入记进场景日志，便于测试与四端一致性回归。"""
        try:
            metadata = getattr(scene, "metadata", None)
            if isinstance(metadata, dict):
                metadata.setdefault("write_log", []).append(
                    dict(action=action, **payload))
        except Exception:
            pass

    # ---------------------------------------------------------------- 撤销
    def begin_undo(self, label: str = "TAK") -> str:
        cmds = self._cmds()
        if cmds is None:
            return ""
        try:
            cmds.undoInfo(openChunk=True, chunkName=str(label or "TAK"))
        except Exception:
            return ""
        self._undo_depth += 1
        return "maya:%d:%s" % (self._undo_depth, label or "TAK")

    def commit_undo(self, token: str) -> None:
        cmds = self._cmds()
        if cmds is None or not token:
            return
        try:
            cmds.undoInfo(closeChunk=True)
        except Exception:
            pass
        finally:
            self._undo_depth = max(0, self._undo_depth - 1)

    def rollback(self, token: str) -> None:
        cmds = self._cmds()
        if cmds is None or not token:
            return
        try:
            cmds.undoInfo(closeChunk=True)
        except Exception:
            pass
        try:
            cmds.undo()
        except Exception:
            pass
        finally:
            self._undo_depth = max(0, self._undo_depth - 1)

    # ---------------------------------------------------------------- FBX
    def export_fbx(self, path: str, spec: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """导出当前选择为 FBX，并在同目录写一份 sidecar 快照 JSON。"""
        target = str(path or "")
        result: Dict[str, Any] = {"ok": False, "path": target, "sidecar": "",
                                  "selected": 0, "bytes": 0, "error": ""}
        if not target:
            result["error"] = "导出路径为空"
            return result
        cmds = self._cmds()
        if cmds is None:
            result["error"] = "maya 环境不可用"
            return result
        try:
            self._load_fbx_plugin(cmds)
            self._apply_fbx_settings(cmds, dict(spec or {}))
            selected = self._select_for_export(cmds, dict(spec or {}))
            result["selected"] = selected
            cmds.file(target, force=True, type="FBX export", exportSelected=True)
            from .snapshot import write_snapshot
            sidecar = target + ".snapshot.json"
            write_snapshot(sidecar, self.get_skeleton_snapshot())
            result.update({
                "ok": True,
                "sidecar": sidecar,
                "bytes": os.path.getsize(target) if os.path.isfile(target) else 0,
            })
        except Exception as exc:
            result["error"] = "%s: %s" % (type(exc).__name__, exc)
        return result

    # ---------------------------------------------------------------- UI / 日志
    def ui_parent(self) -> Any:
        try:
            from tak.compat.qt import host_main_window
            return host_main_window("maya")
        except Exception:
            return None

    def log(self, message: str, level: str = "info") -> None:
        text = "[TAK][%s] %s" % (level, message)
        try:
            from maya import OpenMaya  # type: ignore
            if level in ("error", "critical"):
                OpenMaya.MGlobal.displayError(text)
            elif level in ("warning", "warn"):
                OpenMaya.MGlobal.displayWarning(text)
            else:
                OpenMaya.MGlobal.displayInfo(text)
            return
        except Exception:
            pass
        print(text)

    # ---------------------------------------------------------------- 内部实现
    def _triple(self, cmds: Any, node: str, attr: str,
                default: Sequence[float]) -> List[float]:
        value = _get(cmds, "getAttr", None, "%s.%s" % (node, attr))
        return _floats(value, 3, default)

    def _parent_of(self, cmds: Any, node: str) -> Optional[str]:
        parents = _get(cmds, "listRelatives", [], node, parent=True)
        if not parents:
            return None
        parent = _short_name(parents[0])
        return parent or None

    def _skin_clusters(self, cmds: Any, mesh: str) -> List[str]:
        history = _get(cmds, "listHistory", [], mesh, pruneDagObjects=True)
        clusters = []
        for item in history or []:
            if str(_get(cmds, "nodeType", "", item)) == "skinCluster":
                clusters.append(_short_name(item))
        return _unique(clusters)

    def _deformers(self, cmds: Any, mesh: str) -> List[str]:
        history = _get(cmds, "listHistory", [], mesh, pruneDagObjects=True)
        found = set()
        for item in history or []:
            node_type = str(_get(cmds, "nodeType", "", item))
            if node_type and (node_type in _DEFORMER_TYPES or node_type.endswith("Deformer")):
                found.add(node_type)
        return sorted(found)

    def _transform_of(self, cmds: Any, mesh: str) -> str:
        try:
            if str(_get(cmds, "nodeType", "", mesh)) == "mesh":
                parents = _get(cmds, "listRelatives", [], mesh, parent=True, fullPath=False)
                if parents:
                    return _short_name(parents[0])
        except Exception:
            pass
        return mesh

    def _vertex_count(self, cmds: Any, node: str) -> int:
        for target in (node, self._transform_of(cmds, node)):
            count = _get(cmds, "polyEvaluate", -1, target, vertex=True)
            value = _as_int(count, -1)
            if value >= 0:
                return value
        return 0

    def _sample_weights(self, cmds: Any, cluster: str, transform: str,
                        vertex_count: int) -> Tuple[int, int, float, str]:
        """按上限采样顶点权重，返回 (采样数, 最大影响数, 最大权重偏差, 错误)。"""
        if not cluster or vertex_count <= 0:
            return 0, 0, 0.0, ""
        limit = min(vertex_count, max(1, int(self.max_sample_vertices)))
        max_influences = 0
        max_deviation = 0.0
        sampled = 0
        error = ""
        for index in range(limit):
            vertex = "%s.vtx[%d]" % (transform or cluster, index)
            try:
                values = cmds.skinPercent(cluster, vertex, q=True, value=True)
            except Exception as exc:
                error = "%s: %s" % (type(exc).__name__, exc)
                break
            weights = [abs(_as_float(item)) for item in (values or [])]
            influences = sum(1 for weight in weights if weight > _WEIGHT_EPSILON)
            max_influences = max(max_influences, influences)
            max_deviation = max(max_deviation, abs(1.0 - sum(weights)))
            sampled += 1
        return sampled, max_influences, round(max_deviation, 6), error

    def _capture(self, cmds: Any, root: Optional[str]) -> Dict[str, Any]:
        names = [_short_name(item) for item in (_get(cmds, "ls", [], type="joint") or []) if item]
        names = _unique(names)
        # mock / 离线桥接场景：真实 Maya 节点名自带 namespace、单位与帧范围，
        # 但假宿主把这三类信息单独存在 MockScene 上，因此在真机路径读不到时
        # 优先用桥接值补齐；无 tak_scene 时行为与批次 2 完全一致。
        scene = getattr(cmds, "tak_scene", None)
        # 两遍扫描：先建完整 parent 表，再算 depth，避免 ls 顺序影响层级深度。
        parents: Dict[str, Optional[str]] = {}
        for name in names:
            parent = self._parent_of(cmds, name)
            parents[name] = parent if parent in names else None
        joints: Dict[str, Dict[str, Any]] = {}
        for name in names:
            scene_joint = _bridge_joint(scene, name)
            depth = _depth_of(name, parents)
            namespace = _namespace_of(name)
            if scene_joint is not None:
                depth = _as_int(getattr(scene_joint, "depth", depth), depth)
                namespace = namespace or str(getattr(scene_joint, "namespace", "") or "")
            joints[name] = {
                "parent": parents[name],
                "matrix": self._world_matrix(cmds, name),
                "depth": depth,
                "scale": self._triple(cmds, name, "scale", [1.0, 1.0, 1.0]),
                "rotate": self._triple(cmds, name, "rotate", [0.0, 0.0, 0.0]),
                "namespace": namespace,
            }

        meshes = [_short_name(item) for item in (_get(cmds, "ls", [], type="mesh") or []) if item]
        meshes = _unique(meshes)[:_MAX_MESHES]
        skin = self._aggregate_skin(cmds, meshes)

        version = str(self.version or getattr(scene, "version", "")
                      or _get(cmds, "about", "", version=True))
        unit = str(getattr(scene, "unit", "") or _unit_of(cmds))
        up_axis = str(getattr(scene, "up_axis", "") or _up_axis_of(cmds))
        frame_range = getattr(scene, "frame_range", None) if scene is not None else None
        if not isinstance(frame_range, (list, tuple)) or len(frame_range) != 2:
            frame_range = _frame_range(cmds)

        snapshot = {
            "dcc": "maya",
            "version": version,
            "unit": unit,
            "up_axis": up_axis,
            "joints": joints,
            "frame_range": [float(frame_range[0]), float(frame_range[1])],
            "skin": skin,
            "metadata": {
                "source": "tak.adapters.maya",
                "maya_version": str(self.version),
                "python_version": str(self.python_version),
                "scene": str(_get(cmds, "file", "", q=True, sceneName=True) or ""),
                "joint_count": len(joints),
                "mesh_count": len(meshes),
                "skin_mesh_count": len(skin.get("per_mesh", {}) or {}),
                "generated_by": "tak",
            },
        }
        return _filter_root(snapshot, root)

    def _world_matrix(self, cmds: Any, node: str) -> List[float]:
        value = _get(cmds, "xform", None, node, q=True, ws=True, matrix=True)
        matrix = _floats(value, 16, IDENTITY_MATRIX)
        if matrix != IDENTITY_MATRIX:
            return matrix
        value = _get(cmds, "getAttr", None, "%s.worldMatrix" % node)
        return _floats(value, 16, IDENTITY_MATRIX)

    def _aggregate_skin(self, cmds: Any, meshes: Sequence[str]) -> Dict[str, Any]:
        per_mesh: Dict[str, Dict[str, Any]] = {}
        deformers = set()
        max_influences = 0
        max_deviation = 0.0
        sampled_total = 0
        for mesh in meshes:
            summary = self.get_skin_summary(mesh)
            if not (summary.get("skin_clusters") or summary.get("deformers")):
                continue
            max_influences = max(max_influences, _as_int(summary.get("max_influences_found")))
            max_deviation = max(max_deviation, _as_float(summary.get("weight_sum_deviation")))
            sampled_total += _as_int(summary.get("sampled_vertices"))
            deformers.update(summary.get("deformers", []) or [])
            per_mesh[mesh] = {
                "max_influences_found": _as_int(summary.get("max_influences_found")),
                "weight_sum_deviation": _as_float(summary.get("weight_sum_deviation")),
                "deformers": list(summary.get("deformers", []) or []),
                "vertex_count": _as_int(summary.get("vertex_count")),
                "sampled_vertices": _as_int(summary.get("sampled_vertices")),
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

    def _load_fbx_plugin(self, cmds: Any) -> None:
        _get(cmds, "loadPlugin", None, "fbxmaya", quiet=True)

    def _apply_fbx_settings(self, cmds: Any, spec: Dict[str, Any]) -> None:
        """先重置再逐项设置 FBX 导出参数；单个参数失败不影响整体导出。"""
        _get(cmds, "FBXResetExport")
        settings = {
            "Export|IncludeGrp|Geometry": True,
            "Export|IncludeGrp|Animation": True,
            "Export|IncludeGrp|Animation|BakeAnimation": True,
            "Export|IncludeGrp|Animation|DeformedModels": True,
            "Export|IncludeGrp|Animation|Skin": True,
            "Export|IncludeGrp|Animation|Constraints": False,
            "Export|IncludeGrp|Animation|Cameras": False,
            "Export|IncludeGrp|Animation|Lights": False,
            "Export|Advanced|Axis": 1,
        }
        fbx_spec = spec.get("fbx") if isinstance(spec.get("fbx"), dict) else {}
        settings["Export|IncludeGrp|Animation|Constraints"] = \
            bool(fbx_spec.get("include_constraints", False))
        settings["Export|IncludeGrp|Geometry"] = \
            bool(fbx_spec.get("include_meshes", True))
        settings["Export|IncludeGrp|Animation"] = \
            bool(fbx_spec.get("bake_animation", True))
        for prop, value in settings.items():
            _get(cmds, "FBXProperty", None, prop, "-v", 1 if value else 0)

    def _select_for_export(self, cmds: Any, spec: Dict[str, Any]) -> int:
        selection = spec.get("selection") or spec.get("selected") or []
        if isinstance(selection, str):
            selection = [selection]
        if selection:
            _get(cmds, "select", None, [_short_name(item) for item in selection], replace=True)
        return len(_get(cmds, "ls", [], selection=True) or [])


# --------------------------------------------------------------------------- #
# 模块级工厂（runner.adapter_for 的唯一入口，签名固定为 **kwargs）
# --------------------------------------------------------------------------- #
def create_adapter(**kwargs: Any) -> MayaAdapter:
    """按 ``tak.core.runner.adapter_for("maya")`` 的约定创建适配器。

    可接受 ``version`` / ``python_version`` / ``data``；``data`` 为离线快照时，
    即使宿主不可用也能产出与 fixtures 一致的读数。
    """
    return MayaAdapter(version=str(kwargs.get("version") or ""),
                       python_version=str(kwargs.get("python_version") or ""),
                       data=kwargs.get("data"))


# --------------------------------------------------------------------------- #
# 纯函数工具
# --------------------------------------------------------------------------- #
def _bridge_joint(scene: Any, name: str) -> Any:
    """取 mock 桥接场景中的关节对象；真机无 tak_scene 时返回 None。"""
    if scene is None:
        return None
    joints = getattr(scene, "joints", None)
    if not isinstance(joints, dict):
        return None
    return joints.get(name)


def _write_result(action: str, status: str, **extra: Any) -> Dict[str, Any]:
    """写方法的统一返回结构：DSL 动作会把 dict 里的标量透传到日志。"""
    result: Dict[str, Any] = {"status": status, "action": action}
    result.update(extra)
    return result


def _empty_skin(mesh: str = "") -> Dict[str, Any]:
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


def _normalize_skin_dict(mesh: str, data: Dict[str, Any]) -> Dict[str, Any]:
    result = _empty_skin(mesh)
    result.update({
        "max_influences_found": _as_int(data.get("max_influences_found")),
        "weight_sum_deviation": round(_as_float(data.get("weight_sum_deviation")), 6),
        "deformers": sorted({str(item) for item in (data.get("deformers") or [])}),
        "skin_clusters": [str(item) for item in (data.get("skin_clusters") or [])],
        "vertex_count": _as_int(data.get("vertex_count")),
        "sampled_vertices": _as_int(data.get("sampled_vertices")),
    })
    return result


def _is_identity(translate: Sequence[float], rotate: Sequence[float],
                 scale: Sequence[float]) -> bool:
    return (all(abs(_as_float(value)) < 1e-6 for value in translate)
            and all(abs(_as_float(value)) < 1e-6 for value in rotate)
            and all(abs(_as_float(value) - 1.0) < 1e-6 for value in scale))


def _depth_of(name: str, parents: Dict[str, Optional[str]]) -> int:
    depth = 0
    seen = {name}
    current = parents.get(name)
    while current is not None and current not in seen and depth < _MAX_PARENT_DEPTH:
        seen.add(current)
        depth += 1
        current = parents.get(current)
    return depth


def _unit_of(cmds: Any) -> str:
    unit = _get(cmds, "currentUnit", "", q=True, linear=True)
    return str(unit or "cm")


def _up_axis_of(cmds: Any) -> str:
    axis = str(_get(cmds, "upAxis", "Y", q=True, axis=True) or "Y").upper()
    return axis[:1] or "Y"


def _frame_range(cmds: Any) -> List[float]:
    start = _get(cmds, "playbackOptions", None, q=True, minTime=True)
    end = _get(cmds, "playbackOptions", None, q=True, maxTime=True)
    if start is None or end is None:
        return list(DEFAULT_FRAME_RANGE)
    low, high = _as_float(start, 1.0), _as_float(end, 120.0)
    return [min(low, high), max(low, high)]


def _empty_snapshot(reason: str = "") -> Dict[str, Any]:
    metadata: Dict[str, Any] = {"source": "tak.adapters.maya", "generated_by": "tak"}
    if reason:
        metadata["degraded"] = reason
    return {
        "dcc": "maya",
        "version": "",
        "unit": "cm",
        "up_axis": "Y",
        "joints": {},
        "frame_range": list(DEFAULT_FRAME_RANGE),
        "skin": {"max_influences_found": 0, "weight_sum_deviation": 0.0,
                 "deformers": []},
        "metadata": metadata,
    }


def _extract_snapshot(data: Any) -> Dict[str, Any]:
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


def _filter_root(snapshot: Dict[str, Any], root: Optional[str]) -> Dict[str, Any]:
    """只保留 root 及其后代；root 不存在时原样返回。"""
    target = _short_name(root) if root else ""
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
