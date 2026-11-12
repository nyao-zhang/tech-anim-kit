# -*- coding: utf-8 -*-
"""把四套 mock 接到 DCCAdapter 抽象接口上。

适配器不缓存宿主对象，每次调用都经由 sys.modules 读取当前已安装的假模块，
因此 uninstall 之后再调用会给出明确错误，而不会读到陈旧数据。
"""
from __future__ import annotations

import sys
from typing import Any, Callable, Dict, List, Optional

from tak.adapters.base import Capability, DCC, DCCAdapter

from ._common import depth_map
from .scene import MockScene

_CAPABILITIES = (
    Capability.SCENE_QUERY,
    Capability.SKIN_QUERY,
    Capability.SKELETON_SNAPSHOT,
    Capability.NODE_WRITE,
    Capability.UNDO_SCOPE,
)


def _require(name: str) -> Any:
    module = sys.modules.get(name)
    if module is None:
        raise RuntimeError("mock 模块 %s 未安装，请先调用 tak.core.mocks.install_mock()" % name)
    return module


def _records(names: List[str], parents: Dict[str, Optional[str]],
             scene: MockScene) -> List[Dict[str, Any]]:
    depths = depth_map(parents)
    records: List[Dict[str, Any]] = []
    for name in names:
        joint = scene.joints[name]
        records.append({
            "name": name,
            "parent": parents.get(name),
            "matrix": list(joint.matrix),
            "depth": depths.get(name, joint.depth),
            "scale": list(joint.scale),
            "rotate": list(joint.rotate),
            "namespace": joint.namespace,
        })
    return records


def _read_maya(scene: MockScene) -> List[Dict[str, Any]]:
    cmds = _require("maya.cmds")
    names = list(cmds.ls(type="joint"))
    parents = {name: (cmds.listRelatives(name, parent=True) or [None])[0] for name in names}
    depths = depth_map(parents)
    records: List[Dict[str, Any]] = []
    for name in names:
        records.append({
            "name": name,
            "parent": parents[name],
            "matrix": list(cmds.getAttr(name + ".matrix")),
            "depth": depths.get(name, 0),
            "scale": [cmds.getAttr(name + ".scaleX"), cmds.getAttr(name + ".scaleY"),
                      cmds.getAttr(name + ".scaleZ")],
            "rotate": [cmds.getAttr(name + ".rotateX"), cmds.getAttr(name + ".rotateY"),
                       cmds.getAttr(name + ".rotateZ")],
            "namespace": scene.joints[name].namespace,
        })
    return records


def _read_max(scene: MockScene) -> List[Dict[str, Any]]:
    runtime = _require("pymxs.runtime")
    nodes = [node for node in runtime.objects if runtime.classOf(node) == "BoneGeometry"]
    parents = {node.name: (node.parent.name if node.parent is not None else None) for node in nodes}
    depths = depth_map(parents)
    records: List[Dict[str, Any]] = []
    for node in nodes:
        records.append({
            "name": node.name,
            "parent": parents[node.name],
            "matrix": list(node.transform),
            "depth": depths.get(node.name, 0),
            "scale": list(node.scale),
            "rotate": list(node.rotation),
            "namespace": scene.joints[node.name].namespace,
        })
    return records


def _read_mobu(scene: MockScene) -> List[Dict[str, Any]]:
    pyfbsdk = _require("pyfbsdk")
    models = [model for model in pyfbsdk.FBSystem().Scene.Models
              if isinstance(model, pyfbsdk.FBModelSkeleton)]
    parents = {model.Name: (model.Parent.Name if model.Parent is not None else None)
               for model in models}
    depths = depth_map(parents)
    records: List[Dict[str, Any]] = []
    for model in models:
        records.append({
            "name": model.Name,
            "parent": parents[model.Name],
            "matrix": list(model.Matrix),
            "depth": depths.get(model.Name, 0),
            "scale": list(model.Scaling),
            "rotate": list(model.Rotation),
            "namespace": scene.joints[model.Name].namespace,
        })
    return records


def _read_unreal(scene: MockScene) -> List[Dict[str, Any]]:
    unreal = _require("unreal")
    skeleton = unreal.load_asset("/Game/TAK/SK_Mannequin_Skeleton")
    if skeleton is None:
        raise RuntimeError("mock Unreal 场景缺少骨架资产")
    reference = skeleton.get_reference_skeleton()
    names = [reference.get_bone_name(index) for index in range(reference.get_num())]
    parents = {
        name: (None if reference.get_parent_index(index) < 0
               else reference.get_bone_name(reference.get_parent_index(index)))
        for index, name in enumerate(names)
    }
    return _records(names, parents, scene)


_READERS: Dict[str, Callable[[MockScene], List[Dict[str, Any]]]] = {
    "maya": _read_maya,
    "max": _read_max,
    "mobu": _read_mobu,
    "unreal": _read_unreal,
}


class MockAdapter(DCCAdapter):
    """四套 mock 共用的适配器实现。"""

    def __init__(self, dcc: str, scene: MockScene, version: str = "",
                 python_version: str = "3.9"):
        super().__init__(version or scene.version, python_version)
        self.dcc = DCC(dcc)
        self.name = "mock-%s" % self.dcc.value
        self._scene = scene

    @property
    def scene(self) -> MockScene:
        return self._scene

    def supports(self, capability: Capability) -> bool:
        return capability in _CAPABILITIES

    def list_nodes(self, node_type: str = "joint") -> List[str]:
        return self._scene.node_names(node_type)

    def get_node_attrs(self, node: str) -> Dict[str, Any]:
        return self._scene.attrs(node)

    def get_skin_summary(self, mesh: str) -> Dict[str, Any]:
        return self._scene.skin_summary(mesh)

    def get_skeleton_snapshot(self, root: Optional[str] = None) -> Dict[str, Any]:
        reader = _READERS.get(self.dcc.value)
        if reader is None:
            raise RuntimeError("mock 适配器不支持 DCC：%s" % self.dcc.value)
        records = reader(self._scene)
        if root is not None:
            keep = _subtree({record["name"]: record["parent"] for record in records}, root)
            records = [record for record in records if record["name"] in keep]
        joints = {
            record["name"]: {
                "parent": record["parent"],
                "matrix": record["matrix"],
                "depth": record["depth"],
                "scale": record["scale"],
                "rotate": record["rotate"],
                "namespace": record["namespace"],
            }
            for record in records
        }
        return {
            "dcc": self.dcc.value,
            "version": str(self.version),
            "unit": self._scene.unit,
            "up_axis": self._scene.up_axis,
            "joints": joints,
            "frame_range": list(self._scene.frame_range),
            "skin": dict(self._scene.skin),
            "metadata": dict(self._scene.metadata),
        }


def _subtree(parents: Dict[str, Optional[str]], root: str) -> set:
    if root not in parents:
        raise KeyError("骨架中不存在根节点：%s" % root)
    keep = {root}
    changed = True
    while changed:
        changed = False
        for name, parent in parents.items():
            if name not in keep and parent in keep:
                keep.add(name)
                changed = True
    return keep
