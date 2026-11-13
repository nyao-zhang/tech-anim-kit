# -*- coding: utf-8 -*-
"""MotionBuilder 2023+ 适配器包。

对外暴露三个稳定入口：

* ``MobuAdapter``    —— DCCAdapter 子类，宿主调用全部延迟导入；
* ``create_adapter`` —— ``tak.core.runner.adapter_for("mobu")`` 的工厂；
* ``is_available``   —— 纯检测，只做一次 ``import pyfbsdk``。

无 MoBu 时 ``import tak.adapters.mobu`` 仍然成功，此时 ``is_available()``
返回 False，适配器退化为可预测的空结构或 ``data=`` 注入的离线快照。
``menu`` / ``init_mobu`` 是独立子模块，按需显式导入，import 零副作用。
"""
from __future__ import annotations

from .adapter import MobuAdapter, create_adapter, is_available

__all__ = ["MobuAdapter", "create_adapter", "is_available"]
