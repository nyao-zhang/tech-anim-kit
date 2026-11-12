# -*- coding: utf-8 -*-
"""假 MotionBuilder 模块：pyfbsdk。

对齐 2023+ 的典型用法：``FBSystem().Scene.Models`` / ``FBFindModelByName``，
骨架模型是 ``FBModelSkeleton``，网格是 ``FBModelStatic``。
"""
from __future__ import annotations

from types import ModuleType
from typing import Dict, List, Optional

from ._common import attach_bridge, new_module
from .scene import IDENTITY_MATRIX, MockScene


class FBVector3d(list):
    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0):
        super().__init__([float(x), float(y), float(z)])

    @property
    def x(self) -> float:
        return self[0]

    @property
    def y(self) -> float:
        return self[1]

    @property
    def z(self) -> float:
        return self[2]


class FBModel:
    model_type = "FBModel"

    def __init__(self, scene: MockScene, name: str, registry: Dict[str, "FBModel"]):
        self._scene = scene
        self._name = name
        self._registry = registry

    @property
    def Name(self) -> str:
        return self._name

    @property
    def LongName(self) -> str:
        return self._name

    @property
    def Parent(self) -> Optional["FBModel"]:
        joint = self._scene.joints.get(self._name)
        if joint is None or joint.parent is None:
            return None
        return self._registry.get(joint.parent)

    @property
    def Children(self) -> List["FBModel"]:
        return [model for model in self._registry.values() if model.Parent is self]

    @property
    def Translation(self) -> FBVector3d:
        joint = self._scene.joints.get(self._name)
        return FBVector3d(*(joint.translation if joint is not None else [0.0, 0.0, 0.0]))

    @property
    def Rotation(self) -> FBVector3d:
        joint = self._scene.joints.get(self._name)
        return FBVector3d(*(joint.rotate if joint is not None else [0.0, 0.0, 0.0]))

    @property
    def Scaling(self) -> FBVector3d:
        joint = self._scene.joints.get(self._name)
        return FBVector3d(*(joint.scale if joint is not None else [1.0, 1.0, 1.0]))

    @property
    def Matrix(self) -> List[float]:
        joint = self._scene.joints.get(self._name)
        return list(joint.matrix) if joint is not None else list(IDENTITY_MATRIX)

    @property
    def Pickable(self) -> bool:
        return True

    def __repr__(self) -> str:
        return "<%s %s>" % (self.model_type, self._name)


class FBModelSkeleton(FBModel):
    model_type = "FBModelSkeleton"


class FBModelStatic(FBModel):
    model_type = "FBModelStatic"


class FBModelList(list):
    def __init__(self, items: Optional[List[FBModel]] = None):
        super().__init__(items or [])


class FBScene:
    def __init__(self, models: List[FBModel], root: Optional[FBModel]):
        self.Models = models
        self.RootModel = root

    def __repr__(self) -> str:
        return "<FBScene %d models>" % len(self.Models)


class FBSystem:
    """子类在 build 时注入场景；直接实例化的基类返回空场景。"""

    Version = "2023"

    def __init__(self, scene: Optional[MockScene] = None,
                 models: Optional[List[FBModel]] = None,
                 root: Optional[FBModel] = None):
        self.Scene = FBScene(models or [], root)
        self.CurrentTime = 0
        self.Version = str(scene.version) if scene is not None else self.Version


def build(scene: MockScene) -> Dict[str, ModuleType]:
    pyfbsdk = new_module("pyfbsdk")

    registry: Dict[str, FBModel] = {}
    for name in scene.joints:
        registry[name] = FBModelSkeleton(scene, name, registry)
    for name in scene.meshes:
        registry[name] = FBModelStatic(scene, name, registry)

    models = [registry[name] for name in scene.node_names()]
    roots = [model for model in models if model.Parent is None]

    class _System(FBSystem):
        def __init__(self):
            super().__init__(scene, models, roots[0] if roots else None)

    def FBFindModelByName(name: str) -> Optional[FBModel]:
        return registry.get(name)

    pyfbsdk.FBSystem = _System
    pyfbsdk.FBScene = FBScene
    pyfbsdk.FBModel = FBModel
    pyfbsdk.FBModelSkeleton = FBModelSkeleton
    pyfbsdk.FBModelStatic = FBModelStatic
    pyfbsdk.FBModelList = FBModelList
    pyfbsdk.FBVector3d = FBVector3d
    pyfbsdk.FBFindModelByName = FBFindModelByName

    attach_bridge(pyfbsdk, scene)
    return {"pyfbsdk": pyfbsdk}
