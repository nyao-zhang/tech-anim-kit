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


# --------------------------------------------------------------------------- #
# 把「回主线程」的能力交给桥
#
# 桥（tak.core.bridge）只在自己的线程里发 HTTP，宿主 API 必须切回 Maya 主线程，
# 否则 cmds 调用会报错甚至崩进程。core 里不能 import maya（架构不变量），
# 所以由本包在导入时注册一个执行器 —— 谁碰宿主，谁提供线程切换。
# --------------------------------------------------------------------------- #
def _maya_executor(func, *args, **kwargs):
    """在 Maya 主线程执行 func 并返回结果。

    拿不到 ``maya.utils`` 说明当前不在 Maya 进程里（纯 Python / CI 用假适配器
    调桥做测试），此时没有主线程之争，直接执行即可 —— 不要让「不在 Maya 里」
    变成一次 500。
    """
    try:
        from maya.utils import executeInMainThreadWithResult
    except Exception:
        return func(*args, **kwargs)
    return executeInMainThreadWithResult(func, *args, **kwargs)


def _register_bridge_executor() -> None:
    try:
        from tak.core import bridge
        bridge.register_executor("maya", _maya_executor,
                                 note="宿主调用经 maya.utils 回到主线程")
    except Exception:
        # 注册失败不影响适配器本身：桥会退回直调并自报 thread_safe=False。
        pass


_register_bridge_executor()


__all__ = [
    "MayaAdapter",
    "capture_snapshot",
    "create_adapter",
    "is_available",
    "load_fixture_snapshot",
    "write_snapshot",
]
