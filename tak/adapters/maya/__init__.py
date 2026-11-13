# -*- coding: utf-8 -*-
"""Maya 适配器包。

对外只暴露三个稳定入口：

* ``MayaAdapter``       —— DCCAdapter 子类，宿主调用全部延迟导入；
* ``create_adapter``    —— ``tak.core.runner.adapter_for("maya")`` 的工厂；
* ``is_available``      —— 纯检测，不触发 standalone initialize。

无 Maya 时 ``import tak.adapters.maya`` 仍然成功，此时 ``is_available()``
返回 False，适配器退化为可预测的空结构或 golden fixture 离线场景。
"""
from __future__ import annotations

from .adapter import MayaAdapter, create_adapter, is_available
from .snapshot import capture_snapshot, load_fixture_snapshot, write_snapshot

__all__ = [
    "MayaAdapter",
    "capture_snapshot",
    "create_adapter",
    "is_available",
    "load_fixture_snapshot",
    "write_snapshot",
]
