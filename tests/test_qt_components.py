# -*- coding: utf-8 -*-
"""Qt 组件库回归测试。

CI / 开发机通常没有 PySide，本文件重点保证：
* ``tak.ui.qt`` 在无 Qt 时可安全 import；
* 所有工厂函数、launcher 入口安全返回 None；
* 组件源码不出现硬编码颜色与 PySide 版本判断；
* 主题文件路径真实存在，深浅主题一一对应。
"""
from __future__ import annotations

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
