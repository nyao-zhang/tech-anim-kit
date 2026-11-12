# -*- coding: utf-8 -*-
"""DCC 适配器抽象接口（P0.1 核心产出）。

所有 tak.core / tools 代码禁止直接 import maya / pymxs / pyfbsdk / unreal。
每个 DCC 实现一个子类，把宿主 API 翻译成下面这组与宿主无关的操作。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional


class DCC(str, Enum):
    MAYA = "maya"
    MAX = "max"
    MOBU = "mobu"
    UNREAL = "unreal"


class Capability(str, Enum):
    SCENE_QUERY = "scene.query"
    SKIN_QUERY = "skin.query"
    SKELETON_SNAPSHOT = "skeleton.snapshot"
    WEIGHT_WRITE = "skin.write"
    NODE_WRITE = "node.write"
    FBX_EXPORT = "export.fbx"
    USD_EXPORT = "export.usd"
    IMPORT_VALIDATE = "import.validate"
    UI_HOST = "ui.host"
    UNDO_SCOPE = "undo.scope"
    PLAYBLAST = "capture.playblast"
    MLDEFORMER_TRAIN = "mldeformer.train"


class DCCAdapter(ABC):
    """适配器基类。子类只需实现 supports 中声明为 True 的能力。"""

    dcc: DCC = DCC.MAYA
    name: str = "base"
    version: str = ""
    python_version: str = ""

    def __init__(self, version: str = "", python_version: str = ""):
        self.version = version or self.version
        self.python_version = python_version or self.python_version

    @abstractmethod
    def supports(self, capability: Capability) -> bool:
        """宿主是否支持该能力；工具据此自动隐藏不可用的功能。"""

    @abstractmethod
    def list_nodes(self, node_type: str = "joint") -> List[str]:
        """返回场景中指定类型的节点名（长名优先由各端自行决定）。"""

    @abstractmethod
    def get_node_attrs(self, node: str) -> Dict[str, Any]:
        """返回节点的可校验属性字典（transform / scale / visibility / namespace 等）。"""

    @abstractmethod
    def get_skin_summary(self, mesh: str) -> Dict[str, Any]:
        """返回蒙皮摘要：influences 超限顶点数、权重和偏差、deformer 类型列表。"""

    @abstractmethod
    def get_skeleton_snapshot(self, root: Optional[str] = None) -> Dict[str, Any]:
        """返回与 tak.core.diff.snapshot.SkeletonSnapshot 兼容的字典。"""

    def export_fbx(self, path: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError("%s 不支持 FBX 导出" % self.name)

    def export_usd(self, path: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError("%s 不支持 USD 导出" % self.name)

    def import_validate(self, path: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError("%s 不支持导入校验" % self.name)

    def begin_undo(self, label: str) -> str:
        """开启一个可整体回滚的变更块，返回 token。"""
        return "noop"

    def commit_undo(self, token: str) -> None:
        """提交变更块。"""

    def rollback(self, token: str) -> None:
        """整体回滚变更块。"""

    def ui_parent(self) -> Any:
        """返回可用于 Qt 窗口挂靠的宿主主窗口指针/对象；无 UI 时返回 None。"""
        return None

    def log(self, message: str, level: str = "info") -> None:
        """默认输出到 stdout，DCC 子类应覆盖为宿主日志。"""
        print("[TAK][%s] %s" % (level, message))

    def describe(self) -> Dict[str, Any]:
        return {
            "dcc": self.dcc.value,
            "name": self.name,
            "version": self.version,
            "python_version": self.python_version,
            "capabilities": [c.value for c in Capability if self.supports(c)],
        }


class NullAdapter(DCCAdapter):
    """离线 / 单测用适配器：不触碰任何真实 DCC，返回可预测的空数据。"""

    dcc = DCC.MAYA
    name = "null"

    def __init__(self, version: str = "0", python_version: str = "3.9", data: Optional[Dict] = None):
        super().__init__(version, python_version)
        self.data = data or {}

    def supports(self, capability: Capability) -> bool:
        return capability in (
            Capability.SCENE_QUERY,
            Capability.SKIN_QUERY,
            Capability.SKELETON_SNAPSHOT,
            Capability.UNDO_SCOPE,
        )

    def list_nodes(self, node_type: str = "joint") -> List[str]:
        return list(self.data.get("nodes", []))

    def get_node_attrs(self, node: str) -> Dict[str, Any]:
        return dict(self.data.get("attrs", {}).get(node, {}))

    def get_skin_summary(self, mesh: str) -> Dict[str, Any]:
        return dict(self.data.get("skin", {}).get(mesh, {}))

    def get_skeleton_snapshot(self, root: Optional[str] = None) -> Dict[str, Any]:
        return dict(self.data.get("skeleton", {}))
