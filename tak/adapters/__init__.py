# -*- coding: utf-8 -*-
"""DCC 适配器层。core 与工具只依赖本包的抽象接口。

具体宿主实现放在子包（maya / unreal / max / mobu），按需延迟导入：
``create_adapter("maya")`` 在没有安装 Maya 的机器上会返回 NullAdapter，
不会抛异常，也不影响 CI。
"""
from .base import Capability, DCC, DCCAdapter, NullAdapter


def create_adapter(dcc: str, **kwargs):
    """按 dcc 名创建适配器；宿主不可用自动降级（见 tak.core.runner.adapter_for）。"""
    from tak.core.runner import adapter_for  # 延迟导入，避免与 runner 形成包级循环
    return adapter_for(dcc, **kwargs)


def available_adapters() -> dict:
    """返回各端 ``is_available()`` 结果，供启动自检与文档使用。"""
    import importlib
    result = {}
    for name in ("maya", "unreal", "max", "mobu"):
        try:
            module = importlib.import_module("tak.adapters.%s" % name)
            result[name] = bool(module.is_available())
        except Exception:
            result[name] = False
    return result


__all__ = ["Capability", "DCC", "DCCAdapter", "NullAdapter",
           "create_adapter", "available_adapters"]
