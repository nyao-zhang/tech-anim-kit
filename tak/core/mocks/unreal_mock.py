# -*- coding: utf-8 -*-
"""假 Unreal 模块：unreal（UE 5.8 风格）。

只覆盖 mock 校验用到的入口：SystemLibrary / EditorAssetLibrary /
AssetRegistryHelpers / EditorActorSubsystem / 骨架与骨骼网格资产。
"""
from __future__ import annotations

from types import ModuleType
from typing import Any, Dict, List, Optional

from ._common import attach_bridge, new_module
from .scene import MockScene

SKELETON_PATH = "/Game/TAK/SK_Mannequin_Skeleton"
MESH_PATH = "/Game/TAK/SK_Mannequin"
ENGINE_VERSION = "++UE5+Release-5.8-CL-0-TAKMOCK"


class MockVector:
    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __iter__(self):
        return iter((self.x, self.y, self.z))

    def __repr__(self) -> str:
        return "Vector(%s, %s, %s)" % (self.x, self.y, self.z)


class MockTransform:
    def __init__(self, translation: List[float], scale: List[float], rotation: List[float]):
        self.translation = MockVector(*translation)
        self.scale3d = MockVector(*scale)
        self.rotation = MockVector(*rotation)


class MockReferenceSkeleton:
    def __init__(self, scene: MockScene):
        self._bones = list(scene.joints)
        self._index = {name: index for index, name in enumerate(self._bones)}
        self._parents = [scene.joints[name].parent for name in self._bones]

    def get_num(self) -> int:
        return len(self._bones)

    def get_bone_name(self, index: int) -> str:
        return self._bones[index]

    def get_parent_index(self, index: int) -> int:
        parent = self._parents[index]
        return -1 if parent is None else self._index[parent]

    def find_bone_index(self, name: str) -> int:
        return self._index.get(name, -1)


class MockSkeleton:
    asset_class = "Skeleton"
    asset_name = "SK_Mannequin_Skeleton"
    package_name = SKELETON_PATH
    package_path = SKELETON_PATH

    def __init__(self, scene: MockScene):
        self._reference = MockReferenceSkeleton(scene)
        self._scene = scene

    def get_reference_skeleton(self) -> MockReferenceSkeleton:
        return self._reference

    def get_name(self) -> str:
        return self.asset_name

    def get_editor_property(self, name: str) -> Any:
        if name in ("bone_tree", "reference_skeleton"):
            return self._reference
        return None


class MockSkeletalMesh:
    asset_class = "SkeletalMesh"
    asset_name = "SK_Mannequin"
    package_name = MESH_PATH
    package_path = MESH_PATH

    def __init__(self, scene: MockScene, skeleton: MockSkeleton):
        self._scene = scene
        self._skeleton = skeleton

    def get_skeleton(self) -> MockSkeleton:
        return self._skeleton

    def get_name(self) -> str:
        return self.asset_name

    def get_editor_property(self, name: str) -> Any:
        if name == "skeleton":
            return self._skeleton
        if name in ("skin_summary", "tak_skin_summary"):
            return dict(self._scene.skin)
        return None


class MockAssetData:
    def __init__(self, asset: Any):
        self.asset_name = asset.asset_name
        self.package_name = asset.package_name
        self.package_path = asset.package_name
        self.asset_class = asset.asset_class

    def __repr__(self) -> str:
        return "<AssetData %s>" % self.package_name


class MockAssetRegistry:
    def __init__(self, assets: List[Any]):
        self._assets = assets

    def get_assets_by_class(self, class_name: str, recursive: bool = True) -> List[MockAssetData]:
        return [MockAssetData(asset) for asset in self._assets if asset.asset_class == class_name]

    def get_assets_by_path(self, path: str, recursive: bool = True) -> List[MockAssetData]:
        return [MockAssetData(asset) for asset in self._assets
                if asset.package_name.startswith(path)]


class MockActor:
    def __init__(self, label: str, joint: Optional[str], scene: MockScene):
        self._label = label
        self._joint = joint
        self._scene = scene

    def get_actor_label(self) -> str:
        return self._label

    def get_name(self) -> str:
        return self._label

    def get_actor_transform(self) -> MockTransform:
        joint = self._scene.joints.get(self._joint or "")
        if joint is None:
            return MockTransform([0.0, 0.0, 0.0], [1.0, 1.0, 1.0], [0.0, 0.0, 0.0])
        return MockTransform(joint.translation, joint.scale, joint.rotate)


class EditorActorSubsystem:
    def __init__(self, actors: List[MockActor]):
        self._actors = actors

    def get_all_level_actors(self) -> List[MockActor]:
        return list(self._actors)


class EditorAssetLibrary:
    def __init__(self, assets: Dict[str, Any]):
        self._assets = assets

    def list_assets(self, directory_path: str, recursive: bool = True) -> List[str]:
        return [path for path in self._assets if path.startswith(directory_path)]

    def does_asset_exist(self, path: str) -> bool:
        return path in self._assets

    def load_asset(self, path: str) -> Any:
        return self._assets.get(path)


class EditorDialog:
    calls: List[Dict[str, Any]] = []

    @staticmethod
    def show_message(**kwargs: Any) -> None:
        EditorDialog.calls.append(dict(kwargs))


class EditorDialogLibrary:
    calls: List[Any] = []

    @staticmethod
    def show_message(*args: Any, **kwargs: Any) -> None:
        EditorDialogLibrary.calls.append((args, kwargs))


class AppMsgType:
    OK = "OK"
    YES = "YES"
    NO = "NO"


class SystemLibrary:
    @staticmethod
    def get_engine_version() -> str:
        return ENGINE_VERSION

    @staticmethod
    def get_engine_version_string() -> str:
        return ENGINE_VERSION


def build(scene: MockScene) -> Dict[str, ModuleType]:
    unreal = new_module("unreal")

    skeleton = MockSkeleton(scene)
    mesh = MockSkeletalMesh(scene, skeleton)
    assets = {SKELETON_PATH: skeleton, MESH_PATH: mesh}
    registry = MockAssetRegistry([skeleton, mesh])

    actors = [MockActor(name, name, scene) for name in scene.joints]
    actors.extend(MockActor(name, None, scene) for name in scene.meshes)
    subsystem = EditorActorSubsystem(actors)
    editor_asset_library = EditorAssetLibrary(assets)

    def load_asset(path: str) -> Any:
        return assets.get(path)

    def get_editor_subsystem(subsystem_type: Any) -> Any:
        if subsystem_type is EditorActorSubsystem:
            return subsystem
        return None

    unreal.SystemLibrary = SystemLibrary
    unreal.EditorAssetLibrary = editor_asset_library
    unreal.AssetRegistryHelpers = type(
        "AssetRegistryHelpers", (), {"get_asset_registry": staticmethod(lambda: registry)})
    unreal.EditorActorSubsystem = EditorActorSubsystem
    unreal.EditorDialog = EditorDialog
    unreal.EditorDialogLibrary = EditorDialogLibrary
    unreal.AppMsgType = AppMsgType
    unreal.Skeleton = MockSkeleton
    unreal.SkeletalMesh = MockSkeletalMesh
    unreal.load_asset = load_asset
    unreal.get_editor_subsystem = get_editor_subsystem

    attach_bridge(unreal, scene)
    return {"unreal": unreal}
