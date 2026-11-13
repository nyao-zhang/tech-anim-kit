# -*- coding: utf-8 -*-
"""MotionBuilder 菜单注册与启动脚本。

约束
----
* 顶层**不导入** pyfbsdk / PySide；FBMenuManager / FBMenu 一律在函数体内
  延迟导入，失败统一返回 ``{"ok": False, "reason": ...}``，绝不抛异常。
* 没有 MotionBuilder 宿主（含只装了 mock 的场景）时，所有函数都安全退化；
  ``startup_script()`` 只返回纯文本，不触碰任何宿主 API。
* MoBu 的菜单 API 随版本差异较大，这里用 ``call`` / ``safe`` 尝试若干常见
  方法名，任一失败都不影响调用方。
"""
from __future__ import annotations

import importlib
import os
from typing import Any, Dict, Optional

from tak.adapters.snapshot_utils import call, safe

MENU_ID = "TAK_Tools"
MENU_LABEL = "&TAK / Tech Anim Kit"

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))


def _import_host() -> Any:
    try:
        return importlib.import_module("pyfbsdk")
    except Exception:
        return None


def _menu_manager(host: Any) -> Optional[Any]:
    cls = getattr(host, "FBMenuManager", None)
    return safe(None, cls) if callable(cls) else None


def _unavailable(reason: str) -> Dict[str, Any]:
    return {"ok": False, "reason": reason, "menu": MENU_ID}


def register_menu() -> Dict[str, Any]:
    """注册 TAK 菜单；宿主不可用 / API 缺失 / 调用失败时给出明确 reason。"""
    host = _import_host()
    if host is None:
        return _unavailable("pyfbsdk 不可用（无 MotionBuilder 宿主）")
    manager = _menu_manager(host)
    if manager is None:
        return _unavailable("当前 pyfbsdk 未提供 FBMenuManager")
    try:
        # 尝试若干版本常见的插入接口；call 会吞掉属性缺失与调用异常。
        inserted = call(manager, "InsertMenu", None, MENU_ID, MENU_LABEL)
        if inserted is None:
            inserted = call(manager, "InsertFirst", None, MENU_ID, MENU_LABEL)
        if inserted is None:
            inserted = call(manager, "AddMenu", None, MENU_ID, MENU_LABEL)
        if inserted is None:
            return _unavailable("FBMenuManager 未接受任何菜单插入调用")
        return {"ok": True, "menu": MENU_ID, "label": MENU_LABEL}
    except Exception as exc:
        return _unavailable("%s: %s" % (type(exc).__name__, exc))


def unregister_menu() -> Dict[str, Any]:
    """注销 TAK 菜单；同样在无宿主时安全返回。"""
    host = _import_host()
    if host is None:
        return _unavailable("pyfbsdk 不可用（无 MotionBuilder 宿主）")
    manager = _menu_manager(host)
    if manager is None:
        return _unavailable("当前 pyfbsdk 未提供 FBMenuManager")
    try:
        removed = call(manager, "DeleteMenu", None, MENU_ID)
        if removed is None:
            removed = call(manager, "RemoveMenu", None, MENU_ID)
        if removed is None:
            return _unavailable("FBMenuManager 未接受任何菜单删除调用")
        return {"ok": True, "menu": MENU_ID}
    except Exception as exc:
        return _unavailable("%s: %s" % (type(exc).__name__, exc))


def startup_script() -> str:
    """返回可粘贴到 MoBu Python 控制台的启动代码（纯字符串，无副作用）。"""
    lines = [
        "# -*- coding: utf-8 -*-",
        "# TAK / Tech Anim Kit - MotionBuilder 2023+ 启动脚本",
        "# 粘贴到 MoBu 的 Python Console / Python Editor 执行即可。",
        "import sys",
        "_TAK_ROOT = r\"%s\"" % _REPO_ROOT,
        "if _TAK_ROOT not in sys.path:",
        "    sys.path.insert(0, _TAK_ROOT)",
        "from tak.adapters.mobu import init_mobu",
        "init_mobu.initialize()",
        "print(\"[TAK] MotionBuilder 适配器已加载\")",
    ]
    return "\n".join(lines)
