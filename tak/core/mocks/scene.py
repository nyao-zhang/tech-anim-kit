# -*- coding: utf-8 -*-
"""四套 DCC mock 共享的场景数据模型（P1.4）。

场景是纯 Python 数据结构，不触碰任何宿主 API：骨架、节点属性、权重表
都能序列化回 ``tak.core.diff.snapshot.SkeletonSnapshot`` 兼容的字典，
因此 Maya / Max / MoBu / Unreal 四套 mock 的读数是可比的。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

IDENTITY_MATRIX: List[float] = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
DEFAULT_MESH = "body"
DEFAULT_VERTEX_COUNT = 16
DEFAULT_SKIN: Dict[str, Any] = {
    "max_influences_found": 4,
    "weight_sum_deviation": 0.0,
    "deformers": ["skinCluster"],
}

_JOINT_KEYS = ("parent", "matrix", "depth", "scale", "rotate", "namespace")


@dataclass
class MockJoint:
    """一个关节；字段与 SkeletonSnapshot.Joint 一一对应。"""

    name: str
    parent: Optional[str] = None
    matrix: List[float] = field(default_factory=lambda: list(IDENTITY_MATRIX))
    depth: int = 0
    scale: List[float] = field(default_factory=lambda: [1.0, 1.0, 1.0])
    rotate: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    namespace: str = ""
    visibility: bool = True

    @property
    def translation(self) -> List[float]:
        return [float(self.matrix[12]), float(self.matrix[13]), float(self.matrix[14])]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "parent": self.parent,
            "matrix": list(self.matrix),
            "depth": self.depth,
            "scale": list(self.scale),
            "rotate": list(self.rotate),
            "namespace": self.namespace,
        }

    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any], depth: Optional[int] = None) -> "MockJoint":
        if not isinstance(data, dict):
            raise ValueError("关节 %s 的定义必须是对象" % name)
        matrix = list(data.get("matrix", IDENTITY_MATRIX))
        if len(matrix) != 16:
            raise ValueError("关节 %s 的 matrix 必须是 16 个数" % name)
        scale = list(data.get("scale", [1.0, 1.0, 1.0]))
        rotate = list(data.get("rotate", [0.0, 0.0, 0.0]))
        if len(scale) != 3 or len(rotate) != 3:
            raise ValueError("关节 %s 的 scale / rotate 必须是 3 个数" % name)
        if depth is None:
            depth = int(data.get("depth", 0))
        return cls(
            name=name,
            parent=data.get("parent"),
            matrix=matrix,
            depth=int(depth),
            scale=scale,
            rotate=rotate,
            namespace=str(data.get("namespace", "")),
        )


@dataclass
class MockScene:
    """统一的假场景。"""

    dcc: str = "maya"
    version: str = "2023"
    unit: str = "cm"
    up_axis: str = "Y"
    joints: Dict[str, MockJoint] = field(default_factory=dict)
    skin: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_SKIN))
    frame_range: List[float] = field(default_factory=lambda: [1.0, 120.0])
    metadata: Dict[str, Any] = field(default_factory=dict)
    meshes: Dict[str, int] = field(default_factory=dict)
    weights: Dict[str, List[Dict[str, float]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.meshes:
            self.meshes = {DEFAULT_MESH: DEFAULT_VERTEX_COUNT}
        self.meshes = {str(name): max(1, int(count)) for name, count in self.meshes.items()}
        if not self.weights:
            self.weights = self._build_weights()

    # ------------------------------------------------------------------ 构造
    @classmethod
    def from_snapshot(cls, data: Dict[str, Any], dcc: Optional[str] = None) -> "MockScene":
        """从 fixture / snapshot 字典构造；自动拆包 ``{"snapshot": {...}}``。"""
        raw: Any = data
        if isinstance(raw, dict) and "snapshot" in raw and "joints" not in raw:
            raw = raw["snapshot"]
        if not isinstance(raw, dict):
            raise TypeError("快照必须是 dict，实际为 %s" % type(raw).__name__)
        joints_data = raw.get("joints")
        if not isinstance(joints_data, dict) or not joints_data:
            raise ValueError("快照缺少非空的 joints 对象")

        parents: Dict[str, Optional[str]] = {}
        for name, info in joints_data.items():
            if not isinstance(info, dict):
                raise ValueError("关节 %s 的定义必须是对象" % name)
            parent = info.get("parent")
            if parent is not None and parent not in joints_data:
                raise ValueError("关节 %s 的 parent %s 不存在" % (name, parent))
            parents[name] = parent

        joints: Dict[str, MockJoint] = {}
        for name, info in joints_data.items():
            depth = info.get("depth")
            if depth is None:
                depth = _depth_of(name, parents)
            joints[name] = MockJoint.from_dict(name, info, depth=int(depth))

        meshes = _normalize_meshes(raw.get("meshes"))
        return cls(
            dcc=str(dcc or raw.get("dcc", "maya")),
            version=str(raw.get("version", "2023")) or "2023",
            unit=str(raw.get("unit", "cm")),
            up_axis=str(raw.get("up_axis", "Y")),
            joints=joints,
            skin=dict(raw.get("skin", DEFAULT_SKIN)),
            frame_range=list(raw.get("frame_range", [1.0, 120.0])),
            metadata=dict(raw.get("metadata", {})),
            meshes=meshes,
        )

    @classmethod
    def from_fixture(cls, path: str, dcc: Optional[str] = None) -> "MockScene":
        """从 fixtures/good 下的 JSON 快照文件构造场景。"""
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return cls.from_snapshot(data, dcc=dcc)

    @classmethod
    def default(cls, dcc: str = "maya") -> "MockScene":
        """无外部数据时的最小可用骨架（root → pelvis → spine_01 → head）。"""
        chain = [("root", None), ("pelvis", "root"), ("spine_01", "pelvis"), ("head", "spine_01")]
        joints = {
            name: MockJoint(name=name, parent=parent, depth=index)
            for index, (name, parent) in enumerate(chain)
        }
        return cls(dcc=dcc, joints=joints, skin=dict(DEFAULT_SKIN),
                   metadata={"source": "tak.mock.default"})

    # ------------------------------------------------------------------ 查询
    def node_names(self, node_type: Optional[str] = None) -> List[str]:
        joints = list(self.joints)
        meshes = list(self.meshes)
        if node_type == "joint":
            return joints
        if node_type == "mesh":
            return meshes
        if node_type in ("transform", "dag", None):
            return joints + meshes
        return []

    def node_type_of(self, node: str) -> str:
        if node in self.joints:
            return "joint"
        if node in self.meshes:
            return "mesh"
        return ""

    def attrs(self, node: str) -> Dict[str, Any]:
        joint = self.joints.get(node)
        if joint is not None:
            return {
                "type": "joint",
                "parent": joint.parent,
                "matrix": list(joint.matrix),
                "translation": joint.translation,
                "scale": list(joint.scale),
                "rotate": list(joint.rotate),
                "namespace": joint.namespace,
                "visibility": bool(joint.visibility),
            }
        if node in self.meshes:
            return {
                "type": "mesh",
                "parent": None,
                "translation": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
                "rotate": [0.0, 0.0, 0.0],
                "namespace": "",
                "visibility": True,
                "vertex_count": self.meshes[node],
            }
        return {}

    def skin_summary(self, mesh: str) -> Dict[str, Any]:
        if mesh not in self.meshes:
            return {}
        return dict(self.skin)

    def weights_for(self, mesh: str) -> List[Dict[str, float]]:
        return [dict(item) for item in self.weights.get(mesh, [])]

    def snapshot(self) -> Dict[str, Any]:
        return {
            "dcc": self.dcc,
            "version": str(self.version),
            "unit": self.unit,
            "up_axis": self.up_axis,
            "joints": {name: joint.to_dict() for name, joint in self.joints.items()},
            "frame_range": list(self.frame_range),
            "skin": dict(self.skin),
            "metadata": dict(self.metadata),
        }

    # ------------------------------------------------------------------ 权重
    def _build_weights(self) -> Dict[str, List[Dict[str, float]]]:
        """确定性地生成权重表：每个顶点固定 k 个影响、权重和恒为 1。"""
        names = list(self.joints)
        if not names:
            return {}
        raw_k = int(self.skin.get("max_influences_found", 4) or 1)
        count = max(1, min(raw_k, len(names)))
        share = 1.0 / count
        tables: Dict[str, List[Dict[str, float]]] = {}
        for mesh, vertex_count in self.meshes.items():
            table: List[Dict[str, float]] = []
            for vertex in range(vertex_count):
                table.append({names[(vertex + i) % len(names)]: share for i in range(count)})
            tables[mesh] = table
        return tables


def load_fixture(path: str, dcc: Optional[str] = None) -> MockScene:
    """便捷函数：``load_fixture("fixtures/good/good_001.json")``。"""
    return MockScene.from_fixture(path, dcc=dcc)


def _normalize_meshes(value: Any) -> Dict[str, int]:
    if value is None:
        return {DEFAULT_MESH: DEFAULT_VERTEX_COUNT}
    if isinstance(value, dict):
        return {str(name): int(count) for name, count in value.items()}
    if isinstance(value, (list, tuple)):
        return {str(name): DEFAULT_VERTEX_COUNT for name in value}
    raise ValueError("meshes 必须是对象或列表")


def _depth_of(name: str, parents: Dict[str, Optional[str]]) -> int:
    depth = 0
    seen = set()
    current = parents.get(name)
    while current is not None and depth <= len(parents):
        if current in seen:
            raise ValueError("关节层级存在环：%s" % name)
        seen.add(current)
        depth += 1
        current = parents.get(current)
    return depth


def joint_keys() -> Tuple[str, ...]:
    """与 SkeletonSnapshot.Joint.to_dict 对齐的字段名（供测试/文档使用）。"""
    return _JOINT_KEYS
