# -*- coding: utf-8 -*-
"""Qt 组件库回归测试。

CI / 开发机通常没有 PySide，本文件重点保证：
* ``tak.ui.qt`` 在无 Qt 时可安全 import；
* 所有工厂函数、launcher 入口安全返回 None；
* 组件源码不出现硬编码颜色与 PySide 版本判断；
* 主题文件路径真实存在，深浅主题一一对应。
"""
from __future__ import annotations

import ast
import glob
import os
import re

import pytest

import tak.ui.qt as ui
from tak.ui.qt import components, launcher
from tak.ui.qt.components import THEME_FILES, _severity_set, _text, _theme_path


def test_package_imports_without_qt():
    assert hasattr(ui, "HAS_QT")
    for name in ("ToolCard", "ResultTable", "DiffPreview", "LogConsole",
                 "StatusBar", "ToolMainWindow", "launch", "show_report"):
        assert hasattr(ui, name)
    assert callable(ui.launch)
    assert callable(ui.show_report)


@pytest.mark.skipif(ui.HAS_QT, reason="本机已安装 Qt，无 Qt 降级分支不适用")
def test_all_factories_return_none_without_qt():
    assert ui.HAS_QT is False
    assert ui.create_tool_card("t", "d") is None
    assert ui.create_result_table() is None
    assert ui.create_diff_preview() is None
    assert ui.create_log_console() is None
    assert ui.create_status_bar() is None
    assert ui.create_main_window() is None


@pytest.mark.skipif(ui.HAS_QT, reason="本机已安装 Qt，无 Qt 降级分支不适用")
def test_launcher_returns_none_and_explains(capsys):
    assert ui.launch(dcc="maya") is None
    assert ui.launch(dcc="maya", tool_id="x") is None
    assert ui.show_report(None) is None
    output = capsys.readouterr().out
    assert "Qt 不可用" in output


@pytest.mark.skipif(ui.HAS_QT, reason="本机已安装 Qt，无 Qt 降级分支不适用")
def test_classes_are_safe_shells_without_qt():
    # 直接实例化也不应抛异常；只是没有真实控件。
    assert components.ToolCard("t") is not None
    assert components.ResultTable() is not None
    assert components.ToolMainWindow(dcc="maya") is not None
    assert launcher._WINDOWS is not None
    assert launcher._KEEPALIVE == {}


def test_theme_files_exist():
    for name, filename in THEME_FILES.items():
        path = _theme_path(name)
        assert os.path.isfile(path), path
        assert os.path.basename(path) == filename
    assert _theme_path("light").endswith("theme_light.qss")
    assert _theme_path("unknown").endswith("theme.qss")


def test_pure_helpers():
    assert _severity_set(None) is None
    assert _severity_set("error") == {"error"}
    assert _severity_set(["ERROR", "Blocking"]) == {"error"}
    assert _severity_set([]) is None
    assert _text(None) == "—"
    assert _text(True) == "true"
    assert _text([1, 2]) == "[1, 2]"


def test_no_hardcoded_colors_or_binding_checks():
    source = open(components.__file__, "r", encoding="utf-8").read()
    assert re.search(r"#[0-9A-Fa-f]{6}\b", source) is None
    assert "from PySide2" not in source and "from PySide6" not in source
    assert "import PySide2" not in source and "import PySide6" not in source
    assert "from tak.compat.qt import" in source


def test_launcher_uses_compat_host_window():
    source = open(launcher.__file__, "r", encoding="utf-8").read()
    assert "host_main_window" in source
    assert "from tak.compat.qt import" in source
    assert "from PySide2" not in source and "from PySide6" not in source
    assert "import PySide2" not in source and "import PySide6" not in source


# --------------------------------------------------------------------------- #
# v0.6.1：工具界面「打开却变成体检报告」的回归防线
# --------------------------------------------------------------------------- #
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_every_tool_ui_import_is_actually_exported():
    """工具 ui_qt.py 里 ``from tak.ui.qt import X`` 的名字必须真的存在。

    0.6.0 有三个工具导入了未导出的 ``_theme_path``，ImportError 被 show()
    自己的 except 吞掉 → 启动器静默回退到通用体检报告。用户看到的就是
    「很多工具打开还是体检界面」。CI 没有 PySide，只有静态检查能拦住它。
    """
    paths = sorted(glob.glob(os.path.join(_REPO_ROOT, "tools", "*", "ui_qt.py")))
    assert paths, "没有扫到任何工具界面模块"
    checked = 0
    for path in paths:
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module != "tak.ui.qt":
                continue
            for alias in node.names:
                checked += 1
                assert hasattr(ui, alias.name), (
                    "%s 导入了 tak.ui.qt 中不存在的 %s" % (path, alias.name))
    assert checked >= 5


def test_public_theme_path_and_result_table_title():
    assert os.path.isfile(ui.theme_path("dark"))
    assert ui._theme_path("dark") == ui.theme_path("dark")
    table = components.ResultTable(title="导出检查 · 运行结果")
    assert table.set_title("换个标题") is table


def test_open_error_is_reported_and_cleared():
    launcher._LAST_OPEN_ERROR["demo"] = "界面构造失败：ImportError"
    assert "ImportError" in launcher.last_open_error("demo")
    assert launcher.last_open_error("other") == ""


def test_tool_window_is_kept_alive_until_destroyed():
    class _FakeWindow(object):
        def __init__(self):
            self.callbacks = []

        def destroyed_connect(self, callback):
            self.callbacks.append(callback)

        @property
        def destroyed(self):
            return self

        def connect(self, callback):
            self.callbacks.append(callback)

    window = _FakeWindow()
    launcher._remember_tool("__probe__", window)
    assert launcher._TOOL_KEEPALIVE.get("__probe__") is window
    assert launcher._TOOL_WINDOWS.get("__probe__") is window
    for callback in list(window.callbacks):
        callback()
    assert "__probe__" not in launcher._TOOL_KEEPALIVE


def test_detail_clear_layout_does_not_create_toplevel_windows():
    """清空详情区时禁止把子控件 setParent(None)（会变成临时顶层窗口）。"""
    from tak.ui.qt import detail

    source = open(detail.__file__, "r", encoding="utf-8").read()
    tree = ast.parse(source, filename=detail.__file__)
    # 用 AST 而不是字符串查找：注释里正好写了这个反例，字符串会误报。
    offenders = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "setParent"]
    assert offenders == [], "清空布局时不得把 QWidget 提升成顶层窗口"
    body = source[source.index("def _clear_layout"):source.index("class ToolDetailPanel")]
    assert "child.hide()" in body
    assert "child.deleteLater()" in body


def test_own_ui_failure_never_silently_falls_back_to_report():
    """自带界面失败时不得再跑一遍通用体检冒充工具界面。"""
    source = open(launcher.__file__, "r", encoding="utf-8").read()
    assert "has_own_ui" in source
    assert "last_open_error" in source
    # 有自带界面但 show() 失败 → 记错误后直接返回 None，且不碰 _fallback_run
    start = source.index("_LAST_OPEN_ERROR[target] = error")
    block = source[start:source.index("if discover_error", start)]
    assert "return None" in block
    assert "_fallback_run" not in block
