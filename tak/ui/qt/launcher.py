# -*- coding: utf-8 -*-
"""TAK Qt 启动器：把主窗口挂到 DCC 宿主上，并避免重复开窗。

* ``launch``      —— 主窗口入口，模块级弱引用缓存去重；
* ``open_tool``   —— 打开单个工具**自己的**界面（无自带界面时回退通用运行）；
* ``open_settings`` —— 设置面板；
* ``show_report`` —— 独立打开一份报告窗口（不依赖工具列表）。

无 Qt 或缺少 QApplication 时返回 None，只打印一行说明，不抛异常。
"""
from __future__ import annotations

import inspect
import os
import weakref
from typing import Any, Dict, List, Optional

from tak.compat.qt import QT_BINDING, QtCore, QtWidgets, apply_theme, host_main_window

from .components import HAS_QT, ResultTable, ToolMainWindow, _qt_ready, _theme_path

_WINDOWS: "weakref.WeakValueDictionary[str, Any]" = weakref.WeakValueDictionary()
_KEEPALIVE: Dict[str, Any] = {}
_TOOL_WINDOWS: "weakref.WeakValueDictionary[str, Any]" = weakref.WeakValueDictionary()
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


# --------------------------------------------------------------------------- #
# 单工具界面
# --------------------------------------------------------------------------- #
def open_tool(tool_id: str, dcc: str = "maya",
              omui: Any = None) -> Any:
    """打开某个工具自己的界面。

    顺序：
    1. 工具目录下的 ``ui_qt.py`` 提供 ``show()`` → 调它（每个工具一个独立窗口）；
    2. 没有 ``ui_qt.py`` / 没有 ``show()`` / ``show()`` 返回 None
       → 回退到通用契约：取快照 → ``run_tool`` → 报告窗口。

    回退分支是刻意保留的：第三方插件可能只提供 ``core.py``，不该因为没写
    界面就点不动。返回 ``None`` 只在 Qt 完全不可用时发生。
    """
    if not HAS_QT:
        print("[TAK] Qt 不可用，无法打开工具界面（%s）。" % tool_id)
        return None
    if not _qt_ready():
        print("[TAK] Qt 已导入但缺少 QApplication，无法打开工具界面（%s）。" % tool_id)
        return None

    target = str(tool_id or "")
    if not target:
        return None

    existing = _TOOL_WINDOWS.get(target)
    if existing is not None:
        try:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return existing
        except Exception:
            # 窗口已被销毁但弱引用还没清理，继续走重建流程。
            pass

    plugin = None
    try:
        from tak.core.runner import find_plugin
        plugin = find_plugin(target, dcc=dcc)
    except Exception as exc:
        print("[TAK] 未找到工具 %s：%s: %s" % (target, type(exc).__name__, exc))

    if plugin is not None:
        window = _call_tool_show(plugin, dcc, omui)
        if window is not None:
            try:
                _TOOL_WINDOWS[target] = window
            except TypeError:
                pass
            return window
    return _fallback_run(target, dcc)


def _call_tool_show(plugin: Any, dcc: str, omui: Any) -> Any:
    """加载 ``ui_qt.py`` 并调 ``show()``；任何异常都打印后返回 None 交给回退。"""
    path = os.path.join(getattr(plugin, "path", ""), "ui_qt.py")
    if not os.path.isfile(path):
        return None
    try:
        from tak.core.registry import load_plugin_module
        module = load_plugin_module(plugin, "ui_qt")
    except Exception as exc:
        print("[TAK] 工具 %s 的界面模块加载失败，回退通用界面：%s: %s"
              % (getattr(plugin, "id", "?"), type(exc).__name__, exc))
        return None
    show = getattr(module, "show", None)
    if not callable(show):
        return None
    # 各工具 show() 的签名不完全一致（有的只收 dcc，有的收 dcc+omui），
    # 用签名探测而不是 try/except TypeError —— 后者会掩盖 show 内部的真实 TypeError。
    try:
        params = inspect.signature(show).parameters
    except (TypeError, ValueError):
        params = {}
    kwargs: Dict[str, Any] = {}
    if "dcc" in params or not params:
        kwargs["dcc"] = dcc
    if "omui" in params:
        kwargs["omui"] = omui
    try:
        return show(**kwargs)
    except Exception as exc:
        print("[TAK] 工具 %s 的界面打开失败，回退通用界面：%s: %s"
              % (getattr(plugin, "id", "?"), type(exc).__name__, exc))
        return None


def _fallback_run(tool_id: str, dcc: str) -> Any:
    """通用回退：取快照 → 运行工具 → 报告窗口。"""
    try:
        from tak.core.runner import adapter_for, run_tool
        adapter = adapter_for(dcc)
        snapshot = None
        if hasattr(adapter, "get_skeleton_snapshot"):
            try:
                snapshot = adapter.get_skeleton_snapshot()
            except Exception:
                snapshot = None
        result = run_tool(tool_id, snapshot, dcc=dcc, adapter=adapter)
        report = getattr(result, "report", None)
        if report is not None:
            show_report(report, dcc=dcc)
        return result
    except Exception as exc:
        print("[TAK] 运行工具 %s 失败：%s: %s" % (tool_id, type(exc).__name__, exc))
        return None


# --------------------------------------------------------------------------- #
# 设置面板
# --------------------------------------------------------------------------- #
def open_settings(dcc: str = "maya", omui: Any = None,
                  on_saved: Any = None) -> Any:
    """打开设置面板（分类管理 / 工具归类 / 插件导入 / 配置路径）。"""
    if not HAS_QT:
        print("[TAK] Qt 不可用，无法打开设置面板。")
        return None
    if not _qt_ready():
        print("[TAK] Qt 已导入但缺少 QApplication，无法打开设置面板。")
        return None
    key = "__settings__"
    existing = _TOOL_WINDOWS.get(key)
    if existing is not None:
        try:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return existing
        except Exception:
            pass
    try:
        from .settings import SettingsDialog
        parent = host_main_window(str(dcc or "maya"), omui)
        window = SettingsDialog(dcc=dcc, parent=parent, on_saved=on_saved)
        window.show()
        _TOOL_WINDOWS[key] = window
        return window
    except Exception as exc:
        print("[TAK] 设置面板打开失败: %s: %s" % (type(exc).__name__, exc))
        return None
