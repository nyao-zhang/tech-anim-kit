# -*- coding: utf-8 -*-
"""Qt 兼容层：提炼自 MH_SSDR_Tool / vot_ui 的 PySide2→PySide6 写法。

Maya 2023/2024、3ds Max 2023、MotionBuilder 2023 自带 PySide2；
Maya 2025+ 与部分 UE 外挂环境为 PySide6。全部插件只用本模块，
不要各自写 try/except，避免版本判断散落。

在无 Qt 的纯 Python / headless 环境中，名称会被置为 None 而不抛异常，
因此 tak 包在 mayapy / UnrealEditor-Cmd 中也可安全 import。
"""
from __future__ import annotations

from typing import Any, Optional

QtCore = None
QtGui = None
QtWidgets = None
wrapInstance = None
QT_BINDING = "none"

try:
    from PySide2 import QtCore, QtGui, QtWidgets  # noqa: F811
    from shiboken2 import wrapInstance  # noqa: F811
    QT_BINDING = "PySide2"
except ImportError:
    try:
        from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F811
        from shiboken6 import wrapInstance  # noqa: F811
        QT_BINDING = "PySide6"
    except ImportError:
        pass


def host_main_window(dcc: str, omui: Any = None) -> Optional[Any]:
    """返回 DCC 主窗口 QWidget；失败返回 None（工具退化为独立窗口）。"""
    if QtWidgets is None or wrapInstance is None:
        return None
    try:
        if dcc == "maya":
            if omui is None:
                import maya.OpenMayaUI as omui  # type: ignore
            ptr = omui.MQtUtil.mainWindow()
            return wrapInstance(int(ptr), QtWidgets.QWidget) if ptr else None
        # Max / MoBu 的主窗口句柄由各自 adapter 提供，统一在此收口。
        if dcc == "max":
            from pymxs import runtime as rt  # type: ignore
            ptr = rt.windows.getMAXHWND()
            return wrapInstance(int(ptr), QtWidgets.QWidget) if ptr else None
    except Exception:
        return None
    return None


def apply_theme(widget: Any, qss_path: str) -> bool:
    """给窗口套用 tak/ui 生成的 QSS；无 Qt 时安全返回 False。"""
    if QtWidgets is None or widget is None:
        return False
    try:
        with open(qss_path, "r", encoding="utf-8") as fh:
            widget.setStyleSheet(fh.read())
        return True
    except (OSError, AttributeError):
        return False
