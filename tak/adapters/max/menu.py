# -*- coding: utf-8 -*-
"""3ds Max 菜单宿主入口。

用 ``pymxs`` 的 ``rt.menuMan`` 延迟导入注册 / 反注册 TAK 菜单。
设计约束：

* 顶层零 DCC import；``pymxs`` 只在函数体内导入；
* 任何失败（无宿主 / API 缺失 / 调用异常）都返回
  ``{"ok": False, "reason": ...}``，**绝不抛异常**到 UI 或启动脚本；
* ``menu_script()`` 返回可粘贴到 Max MAXScript 监听器的启动代码字符串，
  纯字符串生成，import 与调用都不需要宿主。
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from tak.adapters.snapshot_utils import call, safe

MENU_NAME = "TAK"
MENU_OWNER = "tak_max"

# (菜单项显示名, 触发的 python 语句标识)
MENU_ITEMS: Tuple[Tuple[str, str], ...] = (
    ("Run Health Guard", "tak_health_guard"),
    ("Open Web Panel", "tak_web_panel"),
    ("Validate Import", "tak_validate_import"),
)


def _menu_man() -> Tuple[Any, str]:
    """延迟导入并返回 ``rt.menuMan``；缺失时返回 (None, reason)。"""
    try:
        import pymxs
        rt = getattr(pymxs, "runtime", None)
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)
    if rt is None:
        return None, "pymxs.runtime 不可用"
    menu_man = safe(None, lambda: getattr(rt, "menuMan", None))
    if menu_man is None:
        return None, "rt.menuMan 不可用（非 3ds Max 宿主）"
    return menu_man, ""


def _do_register(menu_man: Any) -> bool:
    """尽力通过 menuMan 建立 TAK 子菜单；任何一步缺失都返回 False。"""
    main_bar = call(menu_man, "getMainMenuBar", None)
    if main_bar is None:
        return False
    existing = call(menu_man, "findMenu", None, MENU_NAME)
    if existing is not None:
        call(menu_man, "unRegisterMenu", None, existing)
    menu = call(menu_man, "createMenu", None, MENU_NAME)
    if menu is None:
        return False
    for label, action in MENU_ITEMS:
        item = call(menu_man, "createActionItem", None, action, MENU_NAME)
        if item is not None:
            call(menu, "addItem", None, item, -1)
        else:
            # 部分版本不提供 createActionItem，退化成仅记录标签的菜单项。
            call(menu, "addItem", None, label, -1)
    sub_item = call(menu_man, "createSubMenuItem", None, MENU_NAME, menu)
    if sub_item is not None:
        call(main_bar, "addItem", None, sub_item, -1)
    call(menu_man, "updateMenuBar", None)
    return True


def _do_unregister(menu_man: Any) -> bool:
    existing = call(menu_man, "findMenu", None, MENU_NAME)
    if existing is None:
        return True
    removed = call(menu_man, "unRegisterMenu", None, existing)
    call(menu_man, "updateMenuBar", None)
    return removed is not False


def register_menu() -> Dict[str, Any]:
    """注册 TAK 菜单；失败返回 ``{"ok": False, "reason": ...}``。"""
    menu_man, reason = _menu_man()
    if menu_man is None:
        return {"ok": False, "reason": reason, "menu": MENU_NAME}
    ok = safe(False, lambda: _do_register(menu_man))
    if not ok:
        return {"ok": False, "reason": "menuMan 注册失败（API 不可用或调用异常）",
                "menu": MENU_NAME}
    return {"ok": True, "menu": MENU_NAME,
            "items": [item[0] for item in MENU_ITEMS], "owner": MENU_OWNER}


def unregister_menu() -> Dict[str, Any]:
    """反注册 TAK 菜单；失败返回 ``{"ok": False, "reason": ...}``。"""
    menu_man, reason = _menu_man()
    if menu_man is None:
        return {"ok": False, "reason": reason, "menu": MENU_NAME}
    ok = safe(False, lambda: _do_unregister(menu_man))
    if not ok:
        return {"ok": False, "reason": "menuMan 反注册失败（API 不可用或调用异常）",
                "menu": MENU_NAME}
    return {"ok": True, "menu": MENU_NAME, "removed": True}


def menu_script() -> str:
    """返回可粘贴到 Max MAXScript 监听器的启动代码字符串（纯字符串生成）。"""
    lines = [
        "-- TAK · 3ds Max 2023+ 启动片段（粘贴到 MAXScript 监听器执行）",
        "python.Execute \"import tak.adapters.max.init_max as m; m.initialize()\"",
        "-- 仅注册菜单：",
        "python.Execute \"from tak.adapters.max.menu import register_menu; "
        "print(register_menu())\"",
        "-- 反注册菜单：",
        "python.Execute \"from tak.adapters.max.menu import unregister_menu; "
        "print(unregister_menu())\"",
    ]
    return "\n".join(lines)
