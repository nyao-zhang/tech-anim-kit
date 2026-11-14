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
        "直接说需求：查场景 / 列节点 / 看骨架与蒙皮 / 创建资产 / 改名删除。"
        "动作走白名单 + 参数校验，写入必须先预览再由你点「执行」；同一套动作经 MCP "
        "暴露给外部 AI（桥接开关在下面）。")
    subtitle.setProperty("takMuted", "true")
    subtitle.setWordWrap(True)

    # 输入框
    input_row = QtWidgets.QHBoxLayout()
    input_row.setSpacing(8)
    line_edit = QtWidgets.QLineEdit()
    line_edit.setPlaceholderText(
        "例如：场景里有多少关节 / 列出所有控制器 / 查 body 的蒙皮 / 创建一个 locator 叫 foot_ctrl")
    send_button = QtWidgets.QPushButton("发送")
    send_button.setProperty("takPrimary", "true")
    send_button.setMinimumHeight(34)
    input_row.addWidget(line_edit, 1)
    input_row.addWidget(send_button, 0)

    # 连接区：桥接（WorkBuddy → DCC）与 AI 端点（DCC → WorkBuddy）是两条独立通道，
    # 分开摆在同一行，避免用户以为「连不上 AI 就不能让外部调工具」。
    #
    # 桥接做成**一行开关**：点一下就起/停，不用先进对话框。对话框只留「详情」，
    # 因为用户要的是「我要开这个端口」，而不是「先读一段说明再找按钮」。
    connect_row = QtWidgets.QHBoxLayout()
    connect_row.setSpacing(8)
    bridge_label = QtWidgets.QLabel("桥接：未启动")
    bridge_label.setProperty("takMuted", "true")
    bridge_toggle = QtWidgets.QPushButton("启动桥接")
    bridge_toggle.setProperty("takPrimary", "true")
    bridge_detail = QtWidgets.QPushButton("详情")
    ai_button = QtWidgets.QPushButton("AI 连接")
    connect_row.addWidget(bridge_label, 1)
    connect_row.addWidget(bridge_toggle, 0)
    connect_row.addWidget(bridge_detail, 0)
    connect_row.addWidget(ai_button, 0)

    # 动作区：写操作永远先出预览，人点「执行」才落地；开关默认关。
    action_row = QtWidgets.QHBoxLayout()
    action_row.setSpacing(8)
    write_box = QtWidgets.QCheckBox("允许写入（面板 + MCP）")
    write_box.setToolTip("关闭时外部 AI 与面板都只能查询 / 预览，不能改场景。"
                         "打开后写操作仍需带 confirm_token 二次提交。")
    execute_button = QtWidgets.QPushButton("执行")
    execute_button.setProperty("takPrimary", "true")
    execute_button.setToolTip("执行上一次预览的写操作（需先打开「允许写入」）")
    execute_button.setEnabled(False)
    undo_button = QtWidgets.QPushButton("撤销上一次")
    undo_button.setToolTip("撤销最近一次由 TAK 执行的写操作")
    mcp_button = QtWidgets.QPushButton("MCP 配置")
    mcp_button.setToolTip("显示可直接粘进 WorkBuddy / Claude Desktop 的 mcpServers 配置")
    action_row.addWidget(write_box, 1)
    action_row.addWidget(execute_button, 0)
    action_row.addWidget(undo_button, 0)
    action_row.addWidget(mcp_button, 0)

    # 候选 / 报告视图 + 对话日志
    result_table = ResultTable(central)
    result_table.set_title("动作结果")
    log_console = LogConsole(central)
    log_console.setMinimumHeight(180)

    layout.addWidget(title)
    layout.addWidget(subtitle)
    layout.addLayout(input_row)
    layout.addLayout(connect_row)
    layout.addLayout(action_row)
    layout.addWidget(result_table, 2)
    layout.addWidget(log_console, 1)
    window.setCentralWidget(central)

    status = StatusBar(window)
    window.setStatusBar(status)
    status.set_status("就绪")

    core = _plugin_core()
    state: dict = {"snapshot": {}, "last": None, "bridge_error": "",
                   "pending_calls": [], "write_enabled": False}

    def _format(item: dict) -> str:
        try:
            from tak.core.ai.commands import format_result
            return format_result(item)
        except Exception as exc:
            return "%s: %s" % (item.get("action"), item.get("error") or exc)

    def _show_results(reply: dict) -> None:
        rows = []
        for item in (reply.get("results") or []):
            level = (Severity.WARNING if item.get("status") == "dry_run"
                     else Severity.ERROR if not item.get("ok")
                     else Severity.INFO)
            rows.append(Finding(
                rule_id="agent_console.action.%s" % item.get("action", "?"),
                severity=level,
                message=_format(item),
                node=str((item.get("params") or {}).get("node")
                         or (item.get("params") or {}).get("name") or ""),
                fix_hint=("点「执行」并打开「允许写入」才会改场景"
                          if item.get("status") == "dry_run" else ""),
            ))
        result_table.set_findings(rows)

    def _on_send() -> None:
        text = line_edit.text().strip()
        if not text:
            return
        line_edit.clear()
        log_console.append("你：%s" % text, "info")
        status.set_status("正在解析指令…")
        try:
            reply = core.command(text, dcc=dcc,
                                 allow_write=bool(state["write_enabled"]))
        except Exception as exc:  # 槽里的异常 Qt 会吞掉，必须自己报出来
            log_console.error("指令解析失败: %s: %s" % (type(exc).__name__, exc))
            status.set_status("指令解析失败")
            return
        state["last"] = reply
        if reply.get("model_error"):
            log_console.append("AI 端点不可用，已用本地解析：%s"
                               % str(reply["model_error"])[:140], "warning")
        if reply.get("reply"):
            log_console.append("助手：%s" % str(reply["reply"])[:600], "info")
        results = reply.get("results") or []
        if not results:
            # 没解析出动作时退回原来的规则问答，输入框不至于像坏了。
            try:
                old = core.chat(text, snapshot=state.get("snapshot") or {})
                log_console.append("规则问答：%s" % str(old.get("reply") or "")[:600],
                                   "info")
            except Exception as exc:
                log_console.error("规则问答也失败: %s: %s" % (type(exc).__name__, exc))
            if reply.get("hint"):
                log_console.append("提示：%s" % str(reply["hint"])[:240], "warning")
            result_table.set_findings([])
            state["pending_calls"] = []
            execute_button.setEnabled(False)
            status.set_status("未解析出场景动作")
            return
        for item in results:
            level = ("error" if not item.get("ok") else
                     "warning" if item.get("status") == "dry_run" else "info")
            log_console.append(_format(item), level)
        _show_results(reply)
        state["pending_calls"] = list(reply.get("calls") or [])
        needs = bool(reply.get("need_confirm"))
        execute_button.setEnabled(needs)
        if needs and not state["write_enabled"]:
            log_console.append("要执行这条写操作，请先勾选「允许写入（面板 + MCP）」",
                               "warning")
        status.set_status("%d 项动作 · %s" % (
            len(results), "待确认" if needs else "已完成"))

    def _on_execute() -> None:
        try:
            if not state["pending_calls"]:
                log_console.append("没有待确认的动作，先在上面提一句需求", "warning")
                return
            if not state["write_enabled"]:
                log_console.error("写入开关未打开：先勾选「允许写入（面板 + MCP）」")
                return
            reply = core.command(calls=state["pending_calls"], dcc=dcc,
                                 allow_write=True, execute=True)
            for item in (reply.get("results") or []):
                log_console.append(_format(item),
                                   "info" if item.get("ok") else "error")
            _show_results(reply)
            committed = any(item.get("committed") for item in
                            (reply.get("results") or []))
            if committed:
                state["pending_calls"] = []
                execute_button.setEnabled(False)
                status.set_status("已执行 · 可用「撤销上一次」回退")
            else:
                status.set_status("未执行（检查写入开关或错误原因）")
        except Exception as exc:
            log_console.error("执行失败: %s: %s" % (type(exc).__name__, exc))
            status.set_status("执行失败")

    def _on_undo() -> None:
        try:
            payload = core.undo(dcc=dcc)
            if payload.get("ok"):
                log_console.append("已撤销：%s" % payload.get("undone"), "info")
                status.set_status("已撤销上一次写入")
            else:
                log_console.append("撤销未生效：%s" % payload.get("error"), "warning")
        except Exception as exc:
            log_console.error("撤销失败: %s: %s" % (type(exc).__name__, exc))

    def _on_mcp_config() -> None:
        try:
            snippet = json.dumps(core.mcp_config(), ensure_ascii=False, indent=2)
            log_console.append("把下面这段加进 WorkBuddy / Claude Desktop 的 "
                               "mcpServers 配置（普通 Python 即可，无需 Maya 解释器）：",
                               "info")
            log_console.append(snippet, "info")
            try:
                app = QtWidgets.QApplication.instance()
                if app is not None and app.clipboard() is not None:
                    app.clipboard().setText(snippet)
                    log_console.append("已复制到剪贴板", "info")
            except Exception:
                pass
            status.set_status("MCP 配置已输出到日志台")
        except Exception as exc:
            log_console.error("MCP 配置读取失败: %s: %s" % (type(exc).__name__, exc))

    def _on_write_toggled(checked: bool) -> None:
        try:
            payload = core.set_write_enabled(bool(checked))
            state["write_enabled"] = bool(payload.get("allow_write"))
            log_console.append(
                "外部/面板写入：%s（%s）" % (
                    "已开启" if state["write_enabled"] else "已关闭",
                    "写操作仍需每次预览并点执行" if state["write_enabled"]
                    else "只能查询与预览"),
                "warning" if state["write_enabled"] else "info")
        except Exception as exc:
            log_console.error("写入开关设置失败: %s: %s" % (type(exc).__name__, exc))
            write_box.blockSignals(True)
            write_box.setChecked(False)
            write_box.blockSignals(False)

    try:
        state["write_enabled"] = bool(core.write_enabled())
        write_box.setChecked(state["write_enabled"])
    except Exception:
        pass

    send_button.clicked.connect(_on_send)
    line_edit.returnPressed.connect(_on_send)
    execute_button.clicked.connect(_on_execute)
    undo_button.clicked.connect(_on_undo)
    mcp_button.clicked.connect(_on_mcp_config)
    write_box.toggled.connect(_on_write_toggled)

    # ------------------------------------------------------------ 连接区逻辑
    def _refresh_bridge_label() -> None:
        try:
            from tak.core import bridge
            status = bridge.bridge_status()
        except Exception as exc:
            bridge_label.setText("桥接：状态读取失败（%s）" % type(exc).__name__)
            bridge_toggle.setText("启动桥接")
            bridge_toggle.setEnabled(False)
            return
        bridge_toggle.setEnabled(True)
        if status.get("running"):
            bridge_label.setText("桥接：%s" % (status.get("url") or "运行中"))
            bridge_toggle.setText("停止")
        else:
            bridge_label.setText("桥接：未启动（外部调不进来）")
            bridge_toggle.setText("启动桥接")

    def _toggle_bridge() -> None:
        """一行开关：运行中就停，没运行就起。失败原因写进日志台。

        注意变量命名：这里**不能**用 ``status`` 承接 ``bridge_status()`` 的结果，
        它会把外层状态栏对象 ``status`` 遮蔽掉，于是 ``status.set_status(...)``
        打在 dict 上抛 AttributeError —— 而 Qt 会把槽函数里的异常吞掉，界面上
        表现为「点了没反应」。真机探针第一次就是这么抓到它的。
        """
        try:
            from tak.core import bridge
        except Exception as exc:
            log_console.error("桥接模块不可用: %s: %s" % (type(exc).__name__, exc))
            return
        try:
            current = bridge.bridge_status()
            if current.get("running"):
                payload = bridge.stop_bridge()
                if payload.get("ok"):
                    log_console.append("桥接已停止（端口 %s）" % payload.get("port"),
                                       "info")
                    status.set_status("桥接已停止")
                else:
                    log_console.error("桥接停止失败：%s" % payload.get("error"))
                _refresh_bridge_label()
                return

            payload = bridge.start_bridge(dcc=dcc)
            if payload.get("ok"):
                log_console.append("桥接已启动：%s" % payload.get("url"), "info")
                status.set_status("桥接运行中 · %s" % payload.get("url"))
                if not payload.get("thread_safe"):
                    # 不致命，但会让外部调用在高负载时超时，必须说出来。
                    log_console.append(
                        "提示：%s" % (payload.get("thread_note")
                                      or "宿主未注册主线程执行器，外部调用可能不稳定"),
                        "warn")
            else:
                log_console.error("桥接启动失败：%s" % payload.get("error"))
                status.set_status("桥接启动失败")
        except Exception as exc:
            # 槽函数里的异常绝不能只留给 Qt 吞 —— 用户会以为按钮坏了。
            state["bridge_error"] = "%s: %s" % (type(exc).__name__, exc)
            log_console.error("桥接操作失败：%s" % state["bridge_error"])
            status.set_status("桥接操作失败")
        _refresh_bridge_label()

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

    bridge_toggle.clicked.connect(_toggle_bridge)
    bridge_detail.clicked.connect(_open_bridge)
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
    window._agent_toggle_bridge = _toggle_bridge
    window._agent_open_ai = _open_ai
    window._agent_open_bridge = _open_bridge
    window._agent_bridge_label = bridge_label
    window._agent_bridge_toggle = bridge_toggle
    window._agent_bridge_error = lambda: state["bridge_error"]
    # v0.7.0 动作链路的测试钩子（GUI 探针 / mayapy offscreen 可无鼠标断言）
    window._agent_execute = _on_execute
    window._agent_undo = _on_undo
    window._agent_mcp_config = _on_mcp_config
    window._agent_write_box = write_box
    window._agent_execute_button = execute_button
    window._agent_result_table = result_table
    window._agent_state = state
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
# AI 连接向导
# --------------------------------------------------------------------------- #
def _ai_dialog(QtWidgets: Any, parent: Any, log_console: Any,
               status_bar: Any) -> Any:
    """AI 端点向导：选服务商 → 粘贴密钥 → 测试。三步之内能用。

    设计取舍：旧版让人填 ``base_url`` / 模型 / **密钥环境变量名**，等于要求
    用户先去系统属性里建环境变量、再重启 DCC —— 实际结果是没人配得动。
    这一版把密钥直接收在这里，存进 ``~/.tak/credential.json``（权限收到当前
    用户），不用改系统设置、不用重启。
    """
    from tak.core import config as tak_config
    from tak.core.ai import connection, credential

    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("TAK · AI 连接（DCC 侧走哪个模型端点）")
    dialog.resize(760, 620)
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)

    intro = QtWidgets.QLabel(
        "只有「解释 / 生成修复计划」这类步骤需要它，所有判定仍走确定性规则。\n"
        "密钥存进本机凭据文件（不进 config.json、不进 git）；系统环境变量优先级更高。")
    intro.setWordWrap(True)
    intro.setProperty("takMuted", "true")

    # ---------------------------------------------------------------- 服务商
    preset_row = QtWidgets.QHBoxLayout()
    preset_row.setSpacing(8)
    preset_box = QtWidgets.QComboBox()
    for item in connection.PRESETS:
        preset_box.addItem(item["name"], item["id"])
    key_page_button = QtWidgets.QPushButton("去申请密钥")
    preset_row.addWidget(QtWidgets.QLabel("服务商"))
    preset_row.addWidget(preset_box, 1)
    preset_row.addWidget(key_page_button, 0)

    note_label = QtWidgets.QLabel("")
    note_label.setWordWrap(True)
    note_label.setProperty("takMuted", "true")

    form = QtWidgets.QFormLayout()
    base_edit = QtWidgets.QLineEdit()
    base_edit.setPlaceholderText("https://api.deepseek.com")
    model_box = QtWidgets.QComboBox()
    model_box.setEditable(True)
    model_box.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
    model_box.lineEdit().setPlaceholderText("留空 = 让端点自己给一个")
    key_edit = QtWidgets.QLineEdit()
    key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
    key_edit.setPlaceholderText("粘贴 API key（sk-…）；本地 Ollama 随便填")
    form.addRow("端点 base_url", base_edit)
    form.addRow("模型", model_box)
    form.addRow("密钥", key_edit)

    key_state = QtWidgets.QLabel("")
    key_state.setWordWrap(True)
    key_state.setProperty("takMuted", "true")
    remember = QtWidgets.QCheckBox("保存到本机凭据文件（推荐：不用改系统环境变量）")
    remember.setChecked(True)

    output = QtWidgets.QPlainTextEdit()
    output.setReadOnly(True)
    output.setMinimumHeight(160)

    test_button = QtWidgets.QPushButton("测试连接")
    test_button.setProperty("takPrimary", "true")
    save_button = QtWidgets.QPushButton("保存")
    fetch_button = QtWidgets.QPushButton("拉取模型列表")
    forget_button = QtWidgets.QPushButton("清除已存密钥")
    close_button = QtWidgets.QPushButton("关闭")
    buttons = QtWidgets.QHBoxLayout()
    buttons.addWidget(test_button)
    buttons.addWidget(save_button)
    buttons.addWidget(fetch_button)
    buttons.addWidget(forget_button)
    buttons.addStretch(1)
    buttons.addWidget(close_button)

    state = {"key_env": connection.DEFAULT_KEY_ENV, "key_url": ""}

    # ---------------------------------------------------------------- 内部
    def _print(payload: dict) -> None:
        source = str(payload.get("api_key_source") or "none")
        source_text = {
            "env": "环境变量 %s" % (payload.get("api_key_env_used")
                                   or payload.get("api_key_env")),
            "file": "本机凭据文件",
            "override": "本次输入（尚未保存）",
            "none": "无",
        }.get(source, source)
        # probe() 的结果里没有 ready 字段：一次成功的最小请求就等于「能用」。
        ready = payload.get("ready")
        if ready is None:
            ready = payload.get("ok")
        lines = [
            "就绪：%s" % ("是" if ready else "否"),
            "端点：%s（来源 %s）" % (payload.get("base_url"),
                                  payload.get("base_url_source")),
            "模型：%s" % (payload.get("model") or "(按任务路由)"),
            "密钥来源：%s" % source_text,
            "提示：%s" % payload.get("hint"),
        ]
        for key, label in (("latency_ms", "耗时(ms)"), ("status", "HTTP"),
                           ("model_source", "模型来源"), ("reply", "回复"),
                           ("error", "错误")):
            if payload.get(key) not in (None, "", 0):
                lines.append("%s：%s" % (label, payload[key]))
        available = payload.get("available_models") or []
        if available:
            lines.append("")
            lines.append("这个端点可用的模型：")
            lines.extend("  " + name for name in available[:20])
        output.setPlainText("\n".join(lines))

    def _refresh_key_state() -> None:
        described = credential.describe()
        found = connection.detected_env_keys()
        parts = []
        if found:
            parts.append("环境变量里已发现：%s" % "、".join(
                "%s(%s)" % (item["name"], item["tail"]) for item in found))
        if described.get("present"):
            parts.append("本机凭据文件已存：%s（%s）" % (
                described.get("tail"), described.get("saved_at")))
        if not parts:
            parts.append("还没有可用密钥 —— 在上面粘贴一个，再点「测试连接」")
        key_state.setText(" · ".join(parts))
        forget_button.setEnabled(bool(described.get("present")))

    def _apply_preset(preset_id: str, fill_fields: bool = True) -> None:
        item = connection.preset(preset_id)
        state["key_env"] = item.get("key_env") or connection.DEFAULT_KEY_ENV
        state["key_url"] = item.get("key_url") or ""
        note_label.setText(item.get("note") or "")
        key_page_button.setEnabled(bool(state["key_url"]))
        if fill_fields:
            base_edit.setText(item.get("base_url") or "")
            model_box.setCurrentText(item.get("model") or "")

    def _load() -> None:
        data = tak_config.load()
        ai = data.get("ai") or {}
        base = str(ai.get("base_url") or "")
        base_edit.setText(base)
        model_box.setCurrentText(str(ai.get("model") or ""))
        preset_id = connection.detect_provider(base) or "deepseek"
        index = preset_box.findData(preset_id)
        if index >= 0:
            preset_box.setCurrentIndex(index)
        # 已经配过地址就别用预设覆盖它 —— 用户改过的值优先。
        _apply_preset(preset_id, fill_fields=not bool(base))
        _refresh_key_state()
        _print(connection.describe(data))

    def _overrides() -> dict:
        overrides = {"base_url": base_edit.text().strip(),
                     "model": model_box.currentText().strip()}
        typed = key_edit.text().strip()
        if typed:
            overrides["api_key"] = typed
        return overrides

    def _test() -> None:
        status_bar.set_status("正在测试 AI 连接…")
        payload = connection.probe(timeout=12, overrides=_overrides())
        _print(payload)
        if payload.get("ok"):
            log_console.append("AI 连接正常（%s ms，模型 %s）" % (
                payload.get("latency_ms"), payload.get("model")), "info")
            status_bar.set_status("AI 连接正常 · %s" % payload.get("model"))
        else:
            log_console.error("AI 连接失败：%s" % payload.get("error"))
            status_bar.set_status("AI 连接失败")

    def _save() -> None:
        result = connection.save_endpoint(
            base_edit.text().strip(), model_box.currentText().strip(),
            api_key_env=state["key_env"])
        if not result.get("ok"):
            log_console.error("端点保存失败：%s" % result.get("error"))
            return
        typed = key_edit.text().strip()
        if typed and remember.isChecked():
            saved = credential.save(typed, provider=preset_box.currentData() or "")
            if not saved.get("ok"):
                log_console.error("密钥保存失败：%s" % saved.get("error"))
            else:
                key_edit.clear()
                if saved.get("short"):
                    log_console.append(
                        "注意：密钥长度偏短，若端点要求正式 key 请再确认一次。", "warn")
        log_console.append("AI 连接设置已保存（%s）" % tak_config.config_path(),
                           "info")
        status_bar.set_status("AI 连接设置已保存")
        _refresh_key_state()
        _print(connection.describe())

    def _fetch_models() -> None:
        status_bar.set_status("正在向端点询问模型列表…")
        payload = connection.list_models(
            base_url=base_edit.text().strip(),
            api_key=key_edit.text().strip() or None, timeout=12)
        if not payload.get("ok"):
            output.setPlainText("拉取失败：%s\nHTTP：%s" % (
                payload.get("error"), payload.get("status")))
            status_bar.set_status("拉取模型列表失败")
            return
        names = payload.get("models") or []
        current = model_box.currentText()
        model_box.clear()
        model_box.addItems(names)
        if current:
            model_box.setCurrentText(current)
        output.setPlainText("端点返回 %d 个模型：\n%s" % (
            len(names), "\n".join("  " + name for name in names[:60])))
        status_bar.set_status("拉取到 %d 个模型，可在「模型」下拉里选" % len(names))

    def _forget() -> None:
        payload = credential.clear()
        if payload.get("ok"):
            log_console.append("已清除本机凭据文件里的密钥", "info")
            status_bar.set_status("已清除已保存的密钥")
        else:
            log_console.error("清除失败：%s" % payload.get("error"))
        _refresh_key_state()

    def _open_key_page() -> None:
        if not state["key_url"]:
            return
        try:
            from tak.compat.qt import QtGui
            QtGui.QDesktopServices.openUrl(QtGui.QUrl(state["key_url"]))
        except Exception as exc:
            log_console.append("打开浏览器失败，请手动访问：%s（%s）" % (
                state["key_url"], type(exc).__name__), "warn")

    preset_box.currentIndexChanged.connect(
        lambda index: _apply_preset(preset_box.itemData(index) or "custom"))
    test_button.clicked.connect(_test)
    save_button.clicked.connect(_save)
    fetch_button.clicked.connect(_fetch_models)
    forget_button.clicked.connect(_forget)
    key_page_button.clicked.connect(_open_key_page)
    close_button.clicked.connect(dialog.accept)

    layout.addWidget(intro)
    layout.addLayout(preset_row)
    layout.addWidget(note_label)
    layout.addLayout(form)
    layout.addWidget(key_state)
    layout.addWidget(remember)
    layout.addWidget(output, 1)
    layout.addLayout(buttons)

    # 暴露给回归测试（不点按钮也能断言控件状态）
    dialog._refresh = _load
    dialog._test = _test
    dialog._save = _save
    dialog._forget = _forget
    dialog._fetch = _fetch_models
    dialog._preset_box = preset_box
    dialog._base_edit = base_edit
    dialog._model_box = model_box
    dialog._key_edit = key_edit
    dialog._remember = remember
    dialog._output = output
    _load()
    dialog.show()
    return dialog
