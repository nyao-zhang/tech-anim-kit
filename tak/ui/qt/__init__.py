# -*- coding: utf-8 -*-
"""TAK Qt 组件库（可选层）。

无 Qt 时整个包可安全 import：``HAS_QT`` 为 False，所有工厂函数返回 None，
``launch`` / ``show_report`` 打印一行说明后返回 None。
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
    create_diff_preview,
    create_log_console,
    create_main_window,
    create_result_table,
    create_status_bar,
    create_tool_card,
)
from .launcher import launch, show_report

__all__ = [
    "HAS_QT",
    "THEME_FILES",
    "DiffPreview",
    "LogConsole",
    "ResultTable",
    "StatusBar",
    "ToolCard",
    "ToolMainWindow",
    "create_diff_preview",
    "create_log_console",
    "create_main_window",
    "create_result_table",
    "create_status_bar",
    "create_tool_card",
    "launch",
    "show_report",
]
