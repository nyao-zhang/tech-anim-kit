# -*- coding: utf-8 -*-
"""Unreal Python 兼容层：提炼自 acc_unreal_api.py 的 hasattr 降级模式。

UE 5.8 → 5.9 存在 API 弃用（如 EditorLevelLibrary → EditorActorSubsystem、
EditorDialogLibrary → EditorDialog），所有调用统一走这里，避免插件里散落版本判断。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def safe_attr(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default)


def engine_version(unreal: Any) -> str:
    """返回引擎版本字符串，兼容多种降级路径。"""
    try:
        return str(unreal.SystemLibrary.get_engine_version())
    except Exception:
        pass
    try:
        return str(unreal.SystemLibrary.get_engine_version_string())
    except Exception:
        return "unknown"


def asset_registry(unreal: Any) -> Optional[Any]:
    try:
        return unreal.AssetRegistryHelpers.get_asset_registry()
    except Exception:
        return None


def editor_actor_subsystem(unreal: Any) -> Optional[Any]:
    """5.x 推荐路径优先，缺失时退回旧版 EditorLevelLibrary。"""
    try:
        return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    except Exception:
        return safe_attr(unreal, "EditorLevelLibrary")


def dialog(unreal: Any, title: str, message: str) -> bool:
    """弹出 OK 对话框；无 GUI（commandlet）时静默返回 False。"""
    try:
        unreal.EditorDialog.show_message(title=title, message=message,
                                         message_type=unreal.AppMsgType.OK)
        return True
    except Exception:
        try:
            unreal.EditorDialogLibrary.show_message(title, message)
            return True
        except Exception:
            return False


API_PROBE_NAMES = [
    "SystemLibrary", "Paths", "EditorAssetLibrary", "EditorUtilityLibrary",
    "AssetRegistryHelpers", "EditorDialog", "EditorDialogLibrary",
    "EditorActorSubsystem", "EditorLevelLibrary", "ToolMenus", "ToolMenuEntryScript",
    "Skeleton", "SkeletalMesh", "AnimSequence", "IKRigDefinition", "IKRetargeter",
    "MLDeformerAsset", "NeuralMorphModel", "VertexDeltaModel", "MLDeformerComponent",
    "DeformerGraphAsset", "MeshDeformerCollection", "ControlRig", "AnimBlueprint",
    "EditorUtilityWidget", "PythonScriptLibrary", "InternationalizationLibrary",
]


def api_probe(unreal: Any, names: Optional[List[str]] = None) -> Dict[str, bool]:
    """真机 API 存在性探测，供 validation/ue_probe.py 与 CI 使用。"""
    return {name: hasattr(unreal, name) for name in (names or API_PROBE_NAMES)}
