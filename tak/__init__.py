# -*- coding: utf-8 -*-
"""Tech Anim Kit: 跨 DCC 技术动画工具集。

设计约束（改动前请先读 docs/COMPATIBILITY.md）：
    1. tak.core 及本包顶层为纯 Python 3.9+，禁止 import maya / pymxs / pyfbsdk / unreal。
    2. 所有 DCC 调用必须经 tak.adapters 的抽象接口注入。
    3. 工具以 tools/<id>/manifest.json 动态发现，新增工具无需修改框架。
"""
from __future__ import annotations

from .version import __version__

__all__ = ["__version__"]
