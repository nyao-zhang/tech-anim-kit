# -*- coding: utf-8 -*-
"""DCC 适配器层。core 与工具只依赖本包的抽象接口。"""
from .base import Capability, DCC, DCCAdapter, NullAdapter

__all__ = ["Capability", "DCC", "DCCAdapter", "NullAdapter"]
