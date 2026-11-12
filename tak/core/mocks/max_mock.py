# -*- coding: utf-8 -*-
"""假 3ds Max 模块：pymxs / pymxs.runtime。

对齐 2023+ 的 pymxs 用法：``rt.objects`` / ``rt.getNodeByName`` /
``rt.classOf``，节点用 ``pos`` / ``transform`` / ``parent`` 取值。
"""
from __future__ import annotations

from types import ModuleType
from typing import Any, Dict, Iterator, List, Optional

from ._common import attach_bridge, new_module
from .scene import IDENTITY_MATRIX, MockScene


class Point3:
    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __iter__(self):
        return iter((self.x, self.y, self.z))

    def __getitem__(self, index: int) -> float:
        return (self.x, self.y, self.z)[index]

    def __repr__(self) -> str:
        return "Point3(%s, %s, %s)" % (self.x, self.y, self.z)


class MaxNode:
    """关节用 BoneGeometry，网格用 Editable_Mesh。"""

    max_class = "BoneGeometry"

    def __init__(self, scene: MockScene, name: str, registry: Dict[str, "MaxNode"]):
        self._scene = scene
        self._name = name
        self._registry = registry

    @property
    def name(self) -> str:
        return self._name

    @property
    def parent(self) -> Optional["MaxNode"]:
        joint = self._scene.joints.get(self._name)
        if joint is None or joint.parent is None:
            return None
        return self._registry.get(joint.parent)

    @property
    def children(self) -> List["MaxNode"]:
        return [node for node in self._registry.values() if node.parent is self]

    @property
    def pos(self) -> Point3:
        joint = self._scene.joints.get(self._name)
        values = joint.translation if joint is not None else [0.0, 0.0, 0.0]
        return Point3(*values)

    @property
    def transform(self) -> List[float]:
        joint = self._scene.joints.get(self._name)
        return list(joint.matrix) if joint is not None else list(IDENTITY_MATRIX)

    @property
    def scale(self) -> List[float]:
        joint = self._scene.joints.get(self._name)
        return list(joint.scale) if joint is not None else [1.0, 1.0, 1.0]

    @property
    def rotation(self) -> List[float]:
        joint = self._scene.joints.get(self._name)
        return list(joint.rotate) if joint is not None else [0.0, 0.0, 0.0]

    @property
    def isHidden(self) -> bool:
        joint = self._scene.joints.get(self._name)
        return not (joint.visibility if joint is not None else True)

    def __repr__(self) -> str:
        return "<%s %s>" % (self.max_class, self._name)


class MeshNode(MaxNode):
    max_class = "Editable_Mesh"


class NodeCollection:
    def __init__(self, nodes: List[MaxNode]):
        self._nodes = list(nodes)

    def __iter__(self) -> Iterator[MaxNode]:
        return iter(self._nodes)

    def __len__(self) -> int:
        return len(self._nodes)

    def __getitem__(self, index: int) -> MaxNode:
        return self._nodes[index]

    def count(self) -> int:
        return len(self._nodes)


def build(scene: MockScene) -> Dict[str, ModuleType]:
    pymxs = new_module("pymxs", package=True)
    runtime = new_module("pymxs.runtime")

    registry: Dict[str, MaxNode] = {}
    for name in scene.joints:
        registry[name] = MaxNode(scene, name, registry)
    for name in scene.meshes:
        registry[name] = MeshNode(scene, name, registry)

    collection = NodeCollection([registry[name] for name in scene.node_names()])

    def getNodeByName(name: str) -> Optional[MaxNode]:
        return registry.get(name)

    def classOf(node: Any) -> str:
        return getattr(node, "max_class", type(node).__name__)

    runtime.objects = collection
    runtime.getNodeByName = getNodeByName
    runtime.classOf = classOf
    runtime.Point3 = Point3
    runtime.rootNode = MeshNode(scene, "rootNode", registry)

    attach_bridge(runtime, scene)
    pymxs.runtime = runtime  # type: ignore[attr-defined]
    return {"pymxs": pymxs, "pymxs.runtime": runtime}
