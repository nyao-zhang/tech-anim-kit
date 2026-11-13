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

    # 连接区：桥接（WorkBuddy → DCC）与 AI 端点（DCC → WorkBuddy）是两条独立通道，
    # 分开摆在同一行，避免用户以为「连不上 AI 就不能让外部调工具」。
    connect_row = QtWidgets.QHBoxLayout()
    connect_row.setSpacing(8)
    bridge_label = QtWidgets.QLabel("桥接：未启动")
    bridge_label.setProperty("takMuted", "true")
    bridge_button = QtWidgets.QPushButton("桥接设置")
    ai_button = QtWidgets.QPushButton("AI 连接")
    connect_row.addWidget(bridge_label, 1)
    connect_row.addWidget(bridge_button, 0)
    connect_row.addWidget(ai_button, 0)

    # 候选 / 报告视图 + 对话日志
    result_table = ResultTable(central)
    log_console = LogConsole(central)
    log_console.setMinimumHeight(180)

    layout.addWidget(title)
    layout.addWidget(subtitle)
    layout.addLayout(input_row)
    layout.addLayout(connect_row)
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

    # ------------------------------------------------------------ 连接区逻辑
    def _refresh_bridge_label() -> None:
        try:
            from tak.core import bridge
            status = bridge.bridge_status()
        except Exception as exc:
            bridge_label.setText("桥接：状态读取失败（%s）" % type(exc).__name__)
            return
        if status.get("running"):
            bridge_label.setText("桥接：%s · %s" % (
                status.get("url", ""), status.get("owner", "")))
        else:
            bridge_label.setText("桥接：未启动")

    def _open_bridge() -> None:
        try:
            _bridge_dialog(QtWidgets, window, dcc, log_console, status,
                           _refresh_bridge_label)
        except Exception as exc:
            log_console.error("桥接设置打开失败: %s: %s" % (type(exc).__name__, exc))

    def _open_ai() -> None:
        try:
            _ai_dialog(QtWidgets, window, log_console, status)
        except Exception as exc:
            log_console.error("AI 连接设置打开失败: %s: %s"
                              % (type(exc).__name__, exc))

    bridge_button.clicked.connect(_open_bridge)
    ai_button.clicked.connect(_open_ai)
    _refresh_bridge_label()

    if apply_theme:
        try:
            apply_theme(window, _THEME)
        except Exception:
            pass

    # 暴露给回归测试 / 宿主做无 GUI 断言
    window._agent_send = _on_send
    window._agent_input = line_edit
    window._agent_core = core
    window._agent_refresh_bridge = _refresh_bridge_label
    window.show()
    return window


# --------------------------------------------------------------------------- #
# 桥接设置
# --------------------------------------------------------------------------- #
def _bridge_dialog(QtWidgets: Any, parent: Any, dcc: str, log_console: Any,
                   status_bar: Any, on_change: Any) -> Any:
    """桥接开关 + 地址/token 显示 + 一行 curl 示例。

    token 只显示前 8 位：能确认「换没换过」，又不至于被截图带走。
    """
    from tak.core import bridge

    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("TAK · 桥接（外部 AI 调用 DCC）")
    dialog.resize(640, 360)
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)

    intro = QtWidgets.QLabel(
        "桥只监听 127.0.0.1，除 /bridge/health 外都要带 X-TAK-Token。\n"
        "外部（WorkBuddy / 脚本）用下面的地址查询场景、跑检查、对话，"
        "写场景仍需在面板内确认。")
    intro.setWordWrap(True)
    intro.setProperty("takMuted", "true")

    info = QtWidgets.QPlainTextEdit()
    info.setReadOnly(True)
    info.setMinimumHeight(180)

    state = {"running": False}

    def _render() -> None:
        payload = bridge.bridge_status()
        state["running"] = bool(payload.get("running"))
        settings = bridge.tak_config.load()
        token = str((settings.get("bridge") or {}).get("token") or "")
        masked = (token[:8] + "…" + token[-4:]) if len(token) > 16 else "(未生成)"
        lines = [
            "状态：%s" % ("运行中" if state["running"] else "未启动"),
            "地址：%s" % (payload.get("url") or "-"),
            "归属：%s" % (payload.get("owner") or "-"),
            "token：%s" % masked,
            "发现文件：%s" % bridge.discovery_path(),
            "",
            "可用接口：",
        ]
        lines.extend("  " + item for item in bridge.ENDPOINTS)
        lines.append("")
        lines.append("等价 curl：")
        lines.append('  curl -H "X-TAK-Token: <token>" %s/bridge/scene'
                     % (payload.get("url") or "http://127.0.0.1:PORT"))
        lines.append("")
        lines.append("线程：%s" % (
            "宿主调用已回到主线程" if payload.get("thread_safe")
            else (payload.get("thread_note") or "未知")))
        info.setPlainText("\n".join(lines))
        start_button.setEnabled(not state["running"])
        stop_button.setEnabled(state["running"])
        if on_change:
            on_change()

    start_button = QtWidgets.QPushButton("启动桥接")
    start_button.setProperty("takPrimary", "true")
    stop_button = QtWidgets.QPushButton("停止")
    refresh_button = QtWidgets.QPushButton("刷新")
    close_button = QtWidgets.QPushButton("关闭")

    buttons = QtWidgets.QHBoxLayout()
    buttons.addWidget(start_button)
    buttons.addWidget(stop_button)
    buttons.addWidget(refresh_button)
    buttons.addStretch(1)
    buttons.addWidget(close_button)

    def _start() -> None:
        payload = bridge.start_bridge(dcc=dcc)
        if payload.get("ok"):
            log_console.append("桥接已启动：%s" % payload.get("url"), "info")
            status_bar.set_status("桥接运行中 · %s" % payload.get("url"))
        else:
            log_console.error("桥接启动失败：%s" % payload.get("error"))
        _render()

    def _stop() -> None:
        payload = bridge.stop_bridge()
        log_console.append("桥接已停止（端口 %s）" % payload.get("port"), "info")
        status_bar.set_status("桥接已停止")
        _render()

    start_button.clicked.connect(_start)
    stop_button.clicked.connect(_stop)
    refresh_button.clicked.connect(_render)
    close_button.clicked.connect(dialog.accept)

    layout.addWidget(intro)
    layout.addWidget(info, 1)
    layout.addLayout(buttons)
    dialog._refresh = _render
    _render()
    dialog.show()
    return dialog


# --------------------------------------------------------------------------- #
# AI 连接设置
# --------------------------------------------------------------------------- #
def _ai_dialog(QtWidgets: Any, parent: Any, log_console: Any,
               status_bar: Any) -> Any:
    """AI 端点设置：base_url / 模型 / 密钥环境变量名，外加一次真实探测。"""
    from tak.core import config as tak_config
    from tak.core.ai import connection

    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("TAK · AI 连接（对话侧走哪个端点）")
    dialog.resize(660, 380)
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)

    intro = QtWidgets.QLabel(
        "填任何 OpenAI 兼容端点即可（WorkBuddy 网关 / 自建代理 / 官方 API）。\n"
        "密钥不写进配置文件：只填**环境变量名**，值放在系统环境变量里。")
    intro.setWordWrap(True)
    intro.setProperty("takMuted", "true")

    form = QtWidgets.QFormLayout()
    base_edit = QtWidgets.QLineEdit()
    base_edit.setPlaceholderText("https://your-gateway/v1")
    model_edit = QtWidgets.QLineEdit()
    model_edit.setPlaceholderText("DeepSeek-V4.1-Flash")
    key_env_edit = QtWidgets.QLineEdit()
    key_env_edit.setPlaceholderText("TAK_AI_API_KEY")
    form.addRow("端点 base_url", base_edit)
    form.addRow("模型", model_edit)
    form.addRow("密钥环境变量", key_env_edit)

    output = QtWidgets.QPlainTextEdit()
    output.setReadOnly(True)
    output.setMinimumHeight(120)

    save_button = QtWidgets.QPushButton("保存")
    save_button.setProperty("takPrimary", "true")
    test_button = QtWidgets.QPushButton("测试连接")
    close_button = QtWidgets.QPushButton("关闭")
    buttons = QtWidgets.QHBoxLayout()
    buttons.addWidget(save_button)
    buttons.addWidget(test_button)
    buttons.addStretch(1)
    buttons.addWidget(close_button)

    def _load() -> None:
        data = tak_config.load()
        ai = data.get("ai") or {}
        base_edit.setText(str(ai.get("base_url") or ""))
        model_edit.setText(str(ai.get("model") or ""))
        key_env_edit.setText(str(ai.get("api_key_env") or "TAK_AI_API_KEY"))
        _print(connection.describe(data))

    def _print(payload: dict) -> None:
        lines = ["就绪：%s" % ("是" if payload.get("ready") else "否"),
                 "端点：%s（来源 %s）" % (payload.get("base_url"),
                                       payload.get("base_url_source")),
                 "模型：%s" % payload.get("model"),
                 "密钥变量：%s（%s）" % (payload.get("api_key_env"),
                                     "已设置" if payload.get("api_key_present")
                                     else "未设置"),
                 "提示：%s" % payload.get("hint")]
        for key, label in (("latency_ms", "耗时(ms)"), ("status", "HTTP"),
                           ("reply", "回复"), ("error", "错误")):
            if payload.get(key) not in (None, "", 0):
                lines.append("%s：%s" % (label, payload[key]))
        output.setPlainText("\n".join(lines))

    def _save() -> None:
        data = tak_config.load()
        ai = dict(data.get("ai") or {})
        ai["base_url"] = base_edit.text().strip()
        ai["model"] = model_edit.text().strip()
        ai["api_key_env"] = key_env_edit.text().strip() or "TAK_AI_API_KEY"
        data["ai"] = ai
        tak_config.save(data)
        log_console.append("AI 连接设置已保存到 %s" % tak_config.config_path(),
                           "info")
        status_bar.set_status("AI 连接设置已保存")
        _print(connection.describe())

    def _test() -> None:
        status_bar.set_status("正在测试 AI 连接…")
        payload = connection.probe(timeout=10)
        _print(payload)
        if payload.get("ok"):
            log_console.append("AI 连接正常（%s ms）" % payload.get("latency_ms"),
                               "info")
            status_bar.set_status("AI 连接正常")
        else:
            log_console.error("AI 连接失败：%s" % payload.get("error"))
            status_bar.set_status("AI 连接失败")

    save_button.clicked.connect(_save)
    test_button.clicked.connect(_test)
    close_button.clicked.connect(dialog.accept)

    layout.addWidget(intro)
    layout.addLayout(form)
    layout.addWidget(output, 1)
    layout.addLayout(buttons)
    dialog._refresh = _load
    dialog._test = _test
    _load()
    dialog.show()
    return dialog
