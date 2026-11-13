# -*- coding: utf-8 -*-
"""P6.2 ML Deformer 数据工厂：离线采样计划 + 数据清单 + 质量门槛。

为什么只有「数据」没有「训练」
==============================
UE 侧 ``MLDeformerAsset`` / ``NeuralMorphModel`` / ``VertexDeltaModel`` /
``MLDeformerComponent`` / ``DeformerGraphAsset`` 在 5.7 与 5.8 的 Python 侧
均未暴露（``docs/VALIDATION.md`` B2、``docs/WEB_PANEL.md`` W3 / W30），能力位
``mldeformer.train`` 恒为 ``False``。因此本包只回答三件事：

1. ``build_plan``        该拍哪些姿势（关节 / 轴 / 角度 / 类别）；
2. ``build_manifest``    这批数据是什么（内容寻址的 id + 覆盖统计）；
3. ``validate_manifest`` 这批数据够不够（门槛 issue 列表）。

本包不 import 任何 DCC（maya / pymxs / pyfbsdk / unreal），零第三方依赖，
纯 Python 3.9+；随机采样用局部 ``random.Random(seed)``，同一输入两次运行
产出的清单逐字节相同。
"""
from __future__ import annotations

from .dataset import (DEFAULT_GATES, GENERATED_BY, build_manifest, gate_codes,
                      manifest_digest, pose_key_summary, quality_gates,
                      validate_manifest)
from .sampling import (CATEGORY_ORDER, DATASET_CONTRACT, MAX_POSES_HARD_LIMIT,
                       PoseSamplePlan, build_plan, category_order,
                       iter_pose_keys, joint_angle_range, sample_summary)

__all__ = [
    "CATEGORY_ORDER",
    "DATASET_CONTRACT",
    "DEFAULT_GATES",
    "GENERATED_BY",
    "MAX_POSES_HARD_LIMIT",
    "PoseSamplePlan",
    "build_manifest",
    "build_plan",
    "category_order",
    "gate_codes",
    "iter_pose_keys",
    "joint_angle_range",
    "manifest_digest",
    "pose_key_summary",
    "quality_gates",
    "sample_summary",
    "validate_manifest",
]
