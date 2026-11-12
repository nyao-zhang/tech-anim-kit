# -*- coding: utf-8 -*-
"""插件界面（可选）。Qt 不可用时整体降级，保证 headless 环境可导入。"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from tak.compat.qt import QT_BINDING, QtWidgets, apply_theme, host_main_window

_HERE = os.path.dirname(os.path.abspath(__file__))
_THEME = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "tak", "ui", "theme.qss")


def _tool_id() -> str:
    """直接读 manifest，避免插件目录不是 Python 包时的相对导入问题。"""
    with open(os.path.join(_HERE, "manifest.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)["id"]


def show(dcc: str = "maya", omui: Any = None) -> Optional[Any]:
    """打开工具窗口；无 Qt 时返回 None（不影响命令行与 CI）。"""
    if QtWidgets is None:
        return None
    tool_id = _tool_id()
    parent = host_main_window(dcc, omui)
    window = QtWidgets.QWidget(parent)
    window.setObjectName("tak_" + tool_id)
    window.setWindowTitle(tool_id + " [QT: %s]" % QT_BINDING)
    apply_theme(window, _THEME)
    window.show()
    return window
