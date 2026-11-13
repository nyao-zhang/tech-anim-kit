# -*- coding: utf-8 -*-
"""3ds Max 适配器包。

对外只暴露三个稳定入口：

* ``MaxAdapter``       —— DCCAdapter 子类，宿主调用全部延迟导入；
* ``create_adapter``   —— ``tak.core.runner.adapter_for("max")`` 的工厂；
* ``is_available``     —— 纯检测，只探测 ``pymxs.runtime``，不触碰场景。

无 3ds Max 时 ``import tak.adapters.max`` 仍然成功，此时 ``is_available()``
返回 False，适配器退化为可预测的空结构或 ``data=`` 离线快照。
"""
from __future__ import annotations

from .adapter import MaxAdapter, create_adapter, is_available

__all__ = [
    "MaxAdapter",
    "create_adapter",
    "is_available",
]
