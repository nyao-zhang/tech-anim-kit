# -*- coding: utf-8 -*-
"""Unreal Engine 适配器包。

对外只暴露三个稳定入口：

* ``UnrealAdapter``    —— DCCAdapter 子类，所有 unreal 调用延迟导入 + hasattr 降级；
* ``create_adapter``   —— ``tak.core.runner.adapter_for("unreal")`` 的工厂，
  签名固定为 ``create_adapter(**kwargs)``，支持 ``data=`` 离线数据注入；
* ``is_available``     —— 只做一次 ``import unreal``，不触发编辑器初始化。

无 UE 时 ``import tak.adapters.unreal`` 仍然成功：``is_available()`` 返回
False，适配器退化为可预测的空结构。编辑器入口在 :mod:`menu` /
:mod:`init_unreal`，无头批量入口在 :mod:`commandlet`，三者都只在被显式
调用时才接触宿主，import 本身没有副作用。
"""
from __future__ import annotations

from .adapter import UnrealAdapter, create_adapter, is_available

__all__ = ["UnrealAdapter", "create_adapter", "is_available"]
