# -*- coding: utf-8 -*-
"""假 Maya 模块：maya / maya.cmds / maya.OpenMaya。

只实现 mock 工具需要的子集：节点枚举、层级、属性读写与少量 OpenMaya 数学类型。
"""
from __future__ import annotations

from types import ModuleType
from typing import Any, Dict, List, Optional

from ._common import attach_bridge, match_name, new_module
from .scene import MockScene

_AXIS = {"X": 0, "Y": 1, "Z": 2}


def build(scene: MockScene) -> Dict[str, ModuleType]:
    maya = new_module("maya", package=True)
    cmds = new_module("maya.cmds")
    open_maya = new_module("maya.OpenMaya")

    def _joint(name: str):
        return scene.joints.get(name)

    def ls(*patterns: Any, **kwargs: Any) -> List[str]:
        node_type = kwargs.get("type")
        names = scene.node_names(node_type)
        for pattern in patterns:
            if isinstance(pattern, str):
                names = [name for name in names if match_name(name, pattern)]
        return names

    def objExists(name: str) -> bool:
        return scene.node_type_of(name) != ""

    def listRelatives(node: str, **kwargs: Any) -> List[str]:
        if scene.node_type_of(node) == "":
            return []
        node_type = kwargs.get("type")
        if kwargs.get("parent"):
            joint = _joint(node)
            if joint is None or joint.parent is None:
                return []
            if node_type and scene.node_type_of(joint.parent) != node_type:
                return []
            return [joint.parent]
        if kwargs.get("children") or not kwargs.get("parent"):
            children = [name for name, joint in scene.joints.items() if joint.parent == node]
            if node_type:
                children = [name for name in children
                            if scene.node_type_of(name) == node_type]
            return children
        return []

    def getAttr(attr: str) -> Any:
        node_name, _, param = attr.partition(".")
        if not param:
            raise RuntimeError("属性名必须是 node.attribute：%s" % attr)
        joint = _joint(node_name)
        if joint is None:
            if node_name in scene.meshes:
                if param in ("visibility", "v"):
                    return True
                if param in ("vertexCount", "vertex_count"):
                    return scene.meshes[node_name]
            raise RuntimeError("No object matches name: %s" % node_name)
        if param in ("matrix", "worldMatrix"):
            return list(joint.matrix)
        if param in ("visibility", "v"):
            return bool(joint.visibility)
        if param in ("translate", "t"):
            return joint.translation
        if param in ("rotate", "r"):
            return list(joint.rotate)
        if param in ("scale", "s"):
            return list(joint.scale)
        for long_name, short_name, channel in (
            ("translate", "t", "translation"), ("rotate", "r", "rotate"),
            ("scale", "s", "scale"),
        ):
            if param.startswith(long_name) or (len(param) == 2 and param[0] == short_name):
                axis = param[-1:].upper()
                if axis in _AXIS:
                    values = joint.translation if channel == "translation" else getattr(joint, channel)
                    return values[_AXIS[axis]]
        raise RuntimeError("No attribute matches: %s" % attr)

    def setAttr(attr: str, *values: Any, **kwargs: Any) -> None:
        node_name, _, param = attr.partition(".")
        joint = _joint(node_name)
        if joint is None:
            raise RuntimeError("No object matches name: %s" % node_name)
        if param in ("visibility", "v"):
            joint.visibility = bool(values[0]) if values else True
            return
        if param in ("translate", "t"):
            joint.matrix = list(joint.matrix)
            for index, value in enumerate(values[:3]):
                joint.matrix[12 + index] = float(value)
            return
        for long_name, short_name, channel in (
            ("translate", "t", "translation"), ("rotate", "r", "rotate"),
            ("scale", "s", "scale"),
        ):
            matches = param.startswith(long_name) or (len(param) == 2 and param[0] == short_name)
            if not matches:
                continue
            axis = param[-1:].upper()
            if axis not in _AXIS:
                continue
            if not values:
                raise RuntimeError("setAttr 缺少值：%s" % attr)
            index = _AXIS[axis]
            if channel == "translation":
                joint.matrix = list(joint.matrix)
                joint.matrix[12 + index] = float(values[0])
            else:
                target = list(getattr(joint, channel))
                target[index] = float(values[0])
                setattr(joint, channel, target)
            return
        raise RuntimeError("No attribute matches: %s" % attr)

    def nodeType(node: str) -> str:
        kind = scene.node_type_of(node)
        if kind == "joint":
            return "joint"
        if kind == "mesh":
            return "mesh"
        return ""

    def about(**kwargs: Any) -> str:
        return str(scene.version) if kwargs.get("version") else "TAK mock Maya"

    def skinCluster(mesh: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        summary = scene.skin_summary(mesh)
        return summary or None

    cmds.ls = ls
    cmds.objExists = objExists
    cmds.listRelatives = listRelatives
    cmds.getAttr = getAttr
    cmds.setAttr = setAttr
    cmds.nodeType = nodeType
    cmds.about = about
    cmds.skinCluster = skinCluster

    class MPoint:
        kX, kY, kZ, kW = 0, 1, 2, 3

        def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0, w: float = 1.0):
            self.x, self.y, self.z, self.w = float(x), float(y), float(z), float(w)

        def __iter__(self):
            return iter((self.x, self.y, self.z, self.w))

        def __getitem__(self, index: int) -> float:
            return (self.x, self.y, self.z, self.w)[index]

        def __repr__(self) -> str:
            return "MPoint(%s, %s, %s, %s)" % (self.x, self.y, self.z, self.w)

    class MVector(MPoint):
        def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0):
            super().__init__(x, y, z, 0.0)

    class MMatrix:
        def __init__(self, values: Optional[List[float]] = None):
            self._values = list(values) if values else [1.0, 0, 0, 0, 0, 1.0, 0, 0,
                                                        0, 0, 1.0, 0, 0, 0, 0, 1.0]

        def getElement(self, row: int, column: int) -> float:
            return self._values[row * 4 + column]

        def __getitem__(self, index: int) -> float:
            return self._values[index]

        def __iter__(self):
            return iter(self._values)

    open_maya.MPoint = MPoint
    open_maya.MVector = MVector
    open_maya.MMatrix = MMatrix

    attach_bridge(cmds, scene)
    return {"maya": maya, "maya.cmds": cmds, "maya.OpenMaya": open_maya}
