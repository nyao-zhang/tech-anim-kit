# -*- coding: utf-8 -*-
"""TAK Qt 组件库（可选层）。

无 Qt 时整个包可安全 import：``HAS_QT`` 为 False，所有工厂函数返回 None，
``launch`` / ``open_tool`` / ``open_settings`` / ``show_report`` 打印一行说明后返回 None。
"""
from __future__ import annotations

from .components import (
    HAS_QT,
    THEME_FILES,
    DiffPreview,
    LogConsole,
    ResultTable,
    StatusBar,
    ToolCard,
    ToolMainWindow,
    _theme_path,
    create_diff_preview,
    create_log_console,
    create_main_window,
    create_result_table,
    create_status_bar,
    create_tool_card,
)
from .detail import ToolDetailPanel
from .launcher import launch, last_open_error, open_settings, open_tool, show_report


def theme_path(name: str = "dark") -> str:
    """公开的 QSS 主题路径入口；插件不该依赖带下划线的私有名。"""
    return _theme_path(name)


__all__ = [
    "HAS_QT",
    "THEME_FILES",
    "theme_path",
    "_theme_path",
    "DiffPreview",
    "LogConsole",
    "ResultTable",
    "StatusBar",
    "ToolCard",
    "ToolDetailPanel",
    "ToolMainWindow",
    "create_diff_preview",
    "create_log_console",
    "create_main_window",
    "create_result_table",
    "create_status_bar",
    "create_tool_card",
    "launch",
    "last_open_error",
    "open_settings",
    "open_tool",
    "show_report",
]
