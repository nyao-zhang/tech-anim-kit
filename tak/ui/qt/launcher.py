# -*- coding: utf-8 -*-
"""TAK Qt 启动器：把主窗口挂到 DCC 宿主上，并避免重复开窗。

* ``launch``      —— 主窗口入口，模块级弱引用缓存去重；
* ``show_report`` —— 独立打开一份报告窗口（不依赖工具列表）。

无 Qt 或缺少 QApplication 时返回 None，只打印一行说明，不抛异常。
"""
from __future__ import annotations

import weakref
from typing import Any, Dict, List, Optional

from tak.compat.qt import QT_BINDING, QtCore, QtWidgets, apply_theme, host_main_window

from .components import HAS_QT, ResultTable, ToolMainWindow, _qt_ready, _theme_path

_WINDOWS: "weakref.WeakValueDictionary[str, Any]" = weakref.WeakValueDictionary()
_KEEPALIVE: Dict[str, Any] = {}
_REPORTS: List[Any] = []


def _remember(key: str, window: Any) -> None:
    """弱引用用于查重；强引用只用于防止顶层窗口被 GC，关闭后自动清理。"""
    _WINDOWS[key] = window
    _KEEPALIVE[key] = window
    try:
        window.destroyed.connect(lambda *_: _KEEPALIVE.pop(key, None))
    except Exception:
        pass


def launch(dcc: str = "maya", tool_id: Optional[str] = None,
           omui: Any = None) -> Optional[Any]:
    """创建并显示 TAK 主窗口；重复调用复用同一窗口。

    :param dcc: ``maya`` / ``max`` / ``mobu`` / ``unreal``
    :param tool_id: 打开后自动选中的工具 id
    :param omui: 宿主 UI 模块（如 maya.OpenMayaUI），用于定位主窗口
    """
    if not HAS_QT:
        print("[TAK] Qt 不可用，跳过 UI 启动（dcc=%s）。" % dcc)
        return None
    if not _qt_ready():
        print("[TAK] Qt 已导入但缺少 QApplication，跳过 UI 启动（dcc=%s）。" % dcc)
        return None

    key = str(dcc or "maya")
    existing = _WINDOWS.get(key)
    if existing is not None:
        try:
            existing.show()
            existing.raise_()
            existing.activateWindow()
        except Exception:
            pass
        if tool_id:
            try:
                existing.select_tool(tool_id)
            except Exception:
                pass
        return existing

    parent = host_main_window(key, omui)
    try:
        window = ToolMainWindow(dcc=key, parent=parent)
    except Exception as exc:
        print("[TAK] 主窗口创建失败: %s: %s" % (type(exc).__name__, exc))
        return None

    if parent is not None:
        # 挂靠宿主时仍保持独立顶层窗口，避免被嵌进 DCC 的布局。
        try:
            window.setWindowFlags(QtCore.Qt.Window)
        except Exception:
            pass
    try:
        window.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
    except Exception:
        pass
    _remember(key, window)
    window.show()
    if tool_id:
        try:
            window.select_tool(tool_id)
        except Exception:
            pass
    return window


def show_report(report: Any, dcc: str = "maya") -> Optional[Any]:
    """独立打开一份报告窗口；无 Qt 时返回 None。"""
    if not HAS_QT:
        print("[TAK] Qt 不可用，无法显示报告窗口（dcc=%s）。" % dcc)
        return None
    if not _qt_ready():
        print("[TAK] Qt 已导入但缺少 QApplication，无法显示报告窗口。")
        return None
    parent = host_main_window(str(dcc or "maya"))
    try:
        window = QtWidgets.QMainWindow(parent)
        window.setObjectName("takReportWindow")
        window.setWindowTitle("TAK 报告 · %s" % (getattr(report, "title", "体检结果") or "体检结果"))
        window.resize(1020, 700)
        central = QtWidgets.QWidget(window)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        subtitle = QtWidgets.QLabel("DCC %s · Qt %s · 规范 %s"
                                    % (getattr(report, "dcc", dcc), QT_BINDING,
                                       getattr(report, "spec_version", "")))
        subtitle.setProperty("takMuted", "true")
        table = ResultTable(central)
        table.set_report(report)
        layout.addWidget(subtitle)
        layout.addWidget(table, 1)
        window.setCentralWidget(central)
        apply_theme(window, _theme_path("dark"))
        window.show()
        _REPORTS.append(window)
        if len(_REPORTS) > 8:
            _REPORTS.pop(0)
        return window
    except Exception as exc:
        print("[TAK] 报告窗口创建失败: %s: %s" % (type(exc).__name__, exc))
        return None
