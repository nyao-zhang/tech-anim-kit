# -*- coding: utf-8 -*-
"""Agent Console 的 Qt 界面（可选层，可简化）。

设计要点
--------
* ``tak.ui.qt`` / ``tak.compat.qt`` / 组件库 import 全部放在 ``show()`` 内部，
  无 Qt 的 CI / mayapy / 纯 Python 环境可安全 import，``show()`` 返回 None；
* 无 Qt 或无 QApplication 时返回 None，不抛异常；
* 输入框 + 候选/报告视图 + 日志台，只调用插件 core 的公开函数，
  UI 不复制任何判定逻辑，也不直接执行任何 DSL 动作；
* 颜色、圆角、间距只来自 ``tak/ui/theme.qss``，Python 代码不写颜色值。
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
_THEME = os.path.join(_REPO_ROOT, "tak", "ui", "theme.qss")
TOOL_ID = "agent_console"


def show(dcc: str = "maya", omui: Any = None) -> Optional[Any]:
    """打开对话控制台窗口；无 Qt 或无 QApplication 时返回 None。"""
    try:
        from tak.compat.qt import QtWidgets, apply_theme
        from tak.ui.qt import HAS_QT, LogConsole, ResultTable, StatusBar
    except Exception:
        return None
    if not HAS_QT or QtWidgets is None:
        return None
    try:
        if QtWidgets.QApplication.instance() is None:
            return None
        return _build_window(QtWidgets, ResultTable, LogConsole, StatusBar,
                             apply_theme, dcc=dcc, omui=omui)
    except Exception as exc:  # 界面构建失败不能拖垮宿主
        print("[TAK][AgentConsole] 界面创建失败: %s: %s"
              % (type(exc).__name__, exc))
        return None


def _plugin_core() -> Any:
    """按文件路径取插件 core，避免插件目录不在 sys.path 时的导入问题。"""
    from tak.core import registry, runner

    return registry.load_plugin_module(runner.find_plugin(TOOL_ID), "core")


def _build_window(QtWidgets: Any, ResultTable: Any, LogConsole: Any,
                  StatusBar: Any, apply_theme: Any,
                  dcc: str, omui: Any) -> Any:
    from tak.core.rules.base import Finding, Severity

    window = QtWidgets.QMainWindow()
    window.setObjectName("tak_" + TOOL_ID)
    window.setWindowTitle("TAK · Agent Console 对话式控制台 · %s" % dcc)
    window.resize(1000, 720)

    central = QtWidgets.QWidget(window)
    layout = QtWidgets.QVBoxLayout(central)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)

    title = QtWidgets.QLabel("Agent Console 对话式控制台")
    title_font = title.font()
    title_font.setBold(True)
    title_font.setPointSize(title_font.pointSize() + 4)
    title.setFont(title_font)
    subtitle = QtWidgets.QLabel(
        "意图/判定走规则 · AI 只解释与候选 · chat 绝不写场景 · "
        "写入需白名单 + dry-run + 权限 + 人工确认 + undo")
    subtitle.setProperty("takMuted", "true")
    subtitle.setWordWrap(True)

    # 输入框
    input_row = QtWidgets.QHBoxLayout()
    input_row.setSpacing(8)
    line_edit = QtWidgets.QLineEdit()
    line_edit.setPlaceholderText("例如：解释一下问题 / 生成修复计划 / 查看状态")
    send_button = QtWidgets.QPushButton("发送")
    send_button.setProperty("takPrimary", "true")
    send_button.setMinimumHeight(34)
    input_row.addWidget(line_edit, 1)
    input_row.addWidget(send_button, 0)

    # 候选 / 报告视图 + 对话日志
    result_table = ResultTable(central)
    log_console = LogConsole(central)
    log_console.setMinimumHeight(180)

    layout.addWidget(title)
    layout.addWidget(subtitle)
    layout.addLayout(input_row)
    layout.addWidget(result_table, 2)
    layout.addWidget(log_console, 1)
    window.setCentralWidget(central)

    status = StatusBar(window)
    window.setStatusBar(status)
    status.set_status("就绪")

    core = _plugin_core()
    state = {"snapshot": {}, "last": None}

    def _on_send() -> None:
        text = line_edit.text().strip()
        if not text:
            return
        line_edit.clear()
        log_console.append("你：%s" % text, "info")
        try:
            reply = core.chat(text, snapshot=state.get("snapshot") or {})
        except Exception as exc:
            status.set_status("对话失败")
            log_console.error("对话失败: %s: %s" % (type(exc).__name__, exc))
            return
        state["last"] = reply
        log_console.append("助手：%s" % reply.get("reply", ""), "info")
        status.set_status("状态：%s · 候选 %d 项" % (
            reply.get("status", ""), len(reply.get("candidates") or [])))
        rows = []
        for row in (reply.get("preview") or []):
            rows.append(Finding(
                rule_id=str(row.get("tool") or "agent_console.candidate"),
                severity=Severity.INFO,
                message="dry-run：%s %s" % (
                    row.get("tool"),
                    json.dumps(row.get("params") or {}, ensure_ascii=False,
                               sort_keys=True)),
                node=str((row.get("params") or {}).get("node", "")),
                fix_hint="需 confirm(...) 二次确认并授权后才会写场景。",
            ))
        result_table.set_findings(rows)

    send_button.clicked.connect(_on_send)
    line_edit.returnPressed.connect(_on_send)

    if apply_theme:
        try:
            apply_theme(window, _THEME)
        except Exception:
            pass

    # 暴露给回归测试 / 宿主做无 GUI 断言
    window._agent_send = _on_send
    window._agent_input = line_edit
    window._agent_core = core
    window.show()
    return window
