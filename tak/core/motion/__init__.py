# -*- coding: utf-8 -*-
"""动作资产语义检索（P6.1）：纯 Python 3.9、零第三方依赖、零 DCC import。

对外只有六个名字：

* :class:`MotionClip`     动作片段数据模型；
* :class:`MotionLibrary`  确定性 BM25 + 类型 + 过滤 + 特征相似度检索器；
* :func:`clip_from_dict`  容错解析（缺字段 / 类型错都兜底，绝不抛异常）；
* :func:`extract_features` 数值特征提取（有采样真实算，无采样保守估计）；
* :data:`MOTION_TYPES`    12 类动作（英文 key → 中文显示名）；
* :data:`CONTRACT_VERSION` 索引契约版本（``motion_index.json`` 的 ``contract``）。

本包不解析 FBX 二进制、不访问网络、不写场景、不写缓存文件；同样的输入
两次调用结果逐字段相同。
"""
from __future__ import annotations

from .features import MOTION_TYPES, extract_features
from .library import CONTRACT_VERSION, MotionLibrary
from .model import MotionClip, clip_from_dict

__all__ = [
    "CONTRACT_VERSION",
    "MOTION_TYPES",
    "MotionClip",
    "MotionLibrary",
    "clip_from_dict",
    "extract_features",
]
