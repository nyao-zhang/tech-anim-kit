# -*- coding: utf-8 -*-
"""MotionBuilder 侧的宿主入口。

``import tak.adapters.mobu.init_mobu`` **零副作用**：不注册菜单、不 import
pyfbsdk、不打印。只有显式调用 ``initialize()`` 才会去注册菜单并打印说明。
"""
from __future__ import annotations

from typing import Any, Dict


def initialize() -> Dict[str, Any]:
    """注册 TAK 菜单并打印使用说明；返回 register_menu 的结果字典。

    无宿主时 register_menu 会安全返回 ``{"ok": False, "reason": ...}``，
    本函数不会因此抛异常，方便在 CI / headless 下直接调用。
    """
    from . import menu

    result = menu.register_menu()
    if result.get("ok"):
        print("[TAK] MotionBuilder 菜单注册成功：%s" % menu.MENU_LABEL)
    else:
        print("[TAK] MotionBuilder 菜单未注册（%s）" % result.get("reason"))
    print("[TAK] 如需手动加载，请在 MoBu Python 控制台执行：")
    print(menu.startup_script())
    return result
