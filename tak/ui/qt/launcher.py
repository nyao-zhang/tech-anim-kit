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
#: 工具窗口必须同时留一份强引用。只用弱引用时 Python wrapper 一被回收，
#: 缓存立刻失效 —— 用户每点一次「打开工具界面」就多造一个窗口，旧窗口还
#: 叠在后面，看起来就是「弹一堆窗又自己消失」。关闭时由 destroyed 清理。
_TOOL_KEEPALIVE: Dict[str, Any] = {}
_REPORTS: List[Any] = []
#: 上一次 open_tool 的失败原因，供主窗口写进日志台/详情区；
#: 不让「界面加载失败」静默退化成一张体检报告。
_LAST_OPEN_ERROR: Dict[str, str] = {}


def _remember(key: str, window: Any) -> None:
    """弱引用用于查重；强引用只用于防止顶层窗口被 GC，关闭后自动清理。"""
    _WINDOWS[key] = window
    _KEEPALIVE[key] = window
    try:
        window.destroyed.connect(lambda *_: _KEEPALIVE.pop(key, None))
    except Exception:
        pass


def _remember_tool(key: str, window: Any) -> None:
    """工具窗口 / 设置面板的查重 + 保活。"""
    try:
        _TOOL_WINDOWS[key] = window
    except TypeError:
        return
    _TOOL_KEEPALIVE[key] = window

    def _forget(*_args: Any) -> None:
        _TOOL_KEEPALIVE.pop(key, None)
        try:
            _TOOL_WINDOWS.pop(key, None)
        except Exception:
            pass

    try:
        window.destroyed.connect(_forget)
    except Exception:
        pass


def last_open_error(tool_id: str = "") -> str:
    """返回最近一次打开工具界面的失败原因；空串代表没有错误。

    主窗口用它把错误显示到详情区/日志台，避免「界面挂了却弹一张体检报告」。
    """
    return _LAST_OPEN_ERROR.get(str(tool_id or ""), "")


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


def show_report(report: Any, dcc: str = "maya",
                tool_name: str = "") -> Optional[Any]:
    """独立打开一份报告窗口；无 Qt 时返回 None。

    ``tool_name`` 让「快速运行」的报告写清楚是哪个工具，而不是一律叫
    「体检结果」—— 用户分不清工具之间的差异，多半就是被这个标题误导的。
    """
    if not HAS_QT:
        print("[TAK] Qt 不可用，无法显示报告窗口（dcc=%s）。" % dcc)
        return None
    if not _qt_ready():
        print("[TAK] Qt 已导入但缺少 QApplication，无法显示报告窗口。")
        return None
    parent = host_main_window(str(dcc or "maya"))
    try:
        label = str(tool_name or "").strip()
        heading = ("%s · 运行结果" % label) if label else "运行结果"
        window = QtWidgets.QMainWindow(parent)
        window.setObjectName("takReportWindow")
        window.setWindowTitle("TAK 报告 · %s" % heading)
        window.resize(1020, 700)
        central = QtWidgets.QWidget(window)
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        subtitle = QtWidgets.QLabel("DCC %s · Qt %s · 规范 %s"
                                    % (getattr(report, "dcc", dcc), QT_BINDING,
                                       getattr(report, "spec_version", "")))
        subtitle.setProperty("takMuted", "true")
        table = ResultTable(central, title=heading)
        table.set_report(report)
        layout.addWidget(subtitle)
        layout.addWidget(table, 1)
        window.setCentralWidget(central)
        apply_theme(window, _theme_path("dark"))
        window.show()
        # 报告窗口也要保活。原来只留最近 8 个、超了就 pop(0)，在 Python
        # wrapper 被回收时窗口会「自己消失」；改成由 destroyed 精确清理。
        _REPORTS.append(window)

        def _forget_report(*_args: Any) -> None:
            try:
                _REPORTS.remove(window)
            except (ValueError, RuntimeError):
                pass

        try:
            window.destroyed.connect(_forget_report)
        except Exception:
            pass
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
    2. **确实没有** ``ui_qt.py`` → 才回退到通用契约：取快照 → ``run_tool``
       → 报告窗口，并在标题里写明是哪个工具。

    v0.6.1 起，自带界面加载/构造失败**不再静默回退**。以前 ``show()`` 抛异常
    时会被吞掉并顺手跑一遍体检，用户看到的就是「这个工具打开是体检界面」，
    而且完全不知道是界面挂了。现在错误写进 ``last_open_error()``，由主窗口
    显示到详情区与日志台。
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
    _LAST_OPEN_ERROR.pop(target, None)

    existing = _TOOL_WINDOWS.get(target)
    if existing is not None:
        try:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return existing
        except (RuntimeError, AttributeError):
            # 窗口已被销毁但弱引用还没清理，继续走重建流程。
            _TOOL_KEEPALIVE.pop(target, None)

    plugin = None
    discover_error = ""
    try:
        from tak.core.runner import find_plugin
        plugin = find_plugin(target, dcc=dcc)
    except Exception as exc:
        discover_error = "未找到工具插件：%s: %s" % (type(exc).__name__, exc)
        print("[TAK] %s %s" % (target, discover_error))

    has_own_ui = (plugin is not None and
                  os.path.isfile(os.path.join(getattr(plugin, "path", ""),
                                              "ui_qt.py")))
    if has_own_ui:
        window, error = _call_tool_show(plugin, dcc, omui)
        if window is not None:
            _remember_tool(target, window)
            return window
        _LAST_OPEN_ERROR[target] = error or "工具界面的 show() 未返回窗口"
        print("[TAK] 工具 %s 界面打开失败：%s" % (target, _LAST_OPEN_ERROR[target]))
        return None

    if discover_error:
        _LAST_OPEN_ERROR[target] = discover_error
        return None
    return _fallback_run(target, dcc)


def _call_tool_show(plugin: Any, dcc: str, omui: Any) -> Any:
    """加载 ``ui_qt.py`` 并调 ``show()``；返回 ``(window, error)``。"""
    path = os.path.join(getattr(plugin, "path", ""), "ui_qt.py")
    if not os.path.isfile(path):
        return None, ""
    try:
        from tak.core.registry import load_plugin_module
        module = load_plugin_module(plugin, "ui_qt")
    except Exception as exc:
        return None, "界面模块加载失败：%s: %s" % (type(exc).__name__, exc)
    show = getattr(module, "show", None)
    if not callable(show):
        return None, "ui_qt.py 缺少 show()"
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
    # 很多工具的 show() 自己包了 except 并 print 一行原因（Maya 里这行会掉进
    # 脚本编辑器，用户看不到）。这里把 print 抓下来并进错误信息，避免出现
    # 「界面挂了、只弹一张体检报告、还查不到原因」。
    import contextlib
    import io as _io
    import sys as _sys

    captured = _io.StringIO()
    window = None
    failure = ""
    try:
        with contextlib.redirect_stdout(captured):
            window = show(**kwargs)
    except Exception as exc:
        failure = "界面构造失败：%s: %s" % (type(exc).__name__, exc)
    printed = captured.getvalue()
    if printed:
        try:
            _sys.stdout.write(printed)   # 原有日志行为不变
        except Exception:
            pass
    if window is None:
        detail = printed.strip()[-300:]
        message = failure or "show() 返回 None（Qt 不可用或工具主动放弃创建窗口）"
        if detail and detail not in message:
            message = "%s；工具输出：%s" % (message, detail)
        return None, message
    return window, ""


def _fallback_run(tool_id: str, dcc: str) -> Any:
    """通用回退（仅限没有自带界面的插件）：取快照 → 运行工具 → 报告窗口。"""
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
            show_report(report, dcc=dcc, tool_name=tool_id)
        return result
    except Exception as exc:
        message = "运行工具 %s 失败：%s: %s" % (tool_id, type(exc).__name__, exc)
        _LAST_OPEN_ERROR[tool_id] = message
        print("[TAK] %s" % message)
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
        _remember_tool(key, window)
        return window
    except Exception as exc:
        print("[TAK] 设置面板打开失败: %s: %s" % (type(exc).__name__, exc))
        return None
