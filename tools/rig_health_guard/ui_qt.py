# -*- coding: utf-8 -*-
"""Rig Health Guard 的 Qt 界面（可选层）。

设计要点
--------
* ``tak.ui.qt`` / ``tak.compat.qt`` 的 import 全部放在 ``show()`` 内部，
  因此无 Qt 的 CI、mayapy、纯 Python 环境下本模块仍可安全 import；
* 无 Qt 或无 QApplication 时 ``show()`` 返回 ``None``，不抛异常；
* 颜色、圆角、间距只来自 ``tak/ui/theme.qss``，Python 代码里不写颜色值；
* 运行、导出、过滤都只调用 ``tak.core.runner.run_tool`` 与 Report 渲染，
  不在 UI 里复制任何判定逻辑。
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
_THEME = os.path.join(_REPO_ROOT, "tak", "ui", "theme.qss")
TOOL_ID = "rig_health_guard"

_EXPORT_FORMATS = (
    ("JSON", "json", ".json", "JSON 报告 (*.json)"),
    ("Markdown", "md", ".md", "Markdown 报告 (*.md)"),
    ("HTML", "html", ".html", "HTML 报告 (*.html)"),
)


def show(dcc: str = "maya", omui: Any = None) -> Optional[Any]:
    """打开体检窗口；无 Qt 或无 QApplication 时返回 None。"""
    try:
        from tak.ui.qt import HAS_QT, LogConsole, ResultTable, StatusBar
    except Exception:
        return None
    if not HAS_QT:
        return None

    try:
        from tak.compat.qt import QtWidgets, apply_theme

        if QtWidgets.QApplication.instance() is None:
            return None
        window = _build_window(QtWidgets, ResultTable, LogConsole, StatusBar,
                               apply_theme, dcc=dcc, omui=omui)
        return window
    except Exception as exc:  # UI 构建失败也不能拖垮宿主
        print("[TAK][RigHealthGuard] 界面创建失败: %s: %s"
              % (type(exc).__name__, exc))
        return None


def _build_window(QtWidgets: Any, ResultTable: Any, LogConsole: Any,
                  StatusBar: Any, apply_theme: Any, dcc: str,
                  omui: Any) -> Any:
    window = QtWidgets.QMainWindow()
    window.setObjectName("tak_" + TOOL_ID)
    window.setWindowTitle("TAK · Rig Health Guard 场景体检 · %s" % dcc)
    window.resize(1080, 720)

    central = QtWidgets.QWidget(window)
    layout = QtWidgets.QVBoxLayout(central)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)

    title = QtWidgets.QLabel("Rig Health Guard 场景体检")
    title_font = title.font()
    title_font.setBold(True)
    title_font.setPointSize(title_font.pointSize() + 4)
    title.setFont(title_font)
    subtitle = QtWidgets.QLabel("只读检查 · dry-run 修复建议 · 不自动改写场景")
    subtitle.setProperty("takMuted", "true")

    toolbar = QtWidgets.QHBoxLayout()
    toolbar.setSpacing(8)

    run_button = QtWidgets.QPushButton("运行体检")
    run_button.setProperty("takPrimary", "true")
    run_button.setMinimumHeight(34)

    format_box = QtWidgets.QComboBox()
    for label, value, _suffix, _flt in _EXPORT_FORMATS:
        format_box.addItem(label, value)

    export_button = QtWidgets.QPushButton("导出报告")
    export_button.setMinimumHeight(34)

    filter_box = QtWidgets.QComboBox()
    filter_box.addItem("全部级别", "")
    for name in ("blocker", "error", "warning", "info"):
        filter_box.addItem(name.upper(), name)

    toolbar.addWidget(run_button, 0)
    toolbar.addWidget(format_box, 0)
    toolbar.addWidget(export_button, 0)
    toolbar.addStretch(1)
    toolbar.addWidget(QtWidgets.QLabel("严重度过滤"))
    toolbar.addWidget(filter_box, 0)

    result_table = ResultTable(central)
    log_console = LogConsole(central)
    log_console.setMinimumHeight(120)

    layout.addWidget(title)
    layout.addWidget(subtitle)
    layout.addLayout(toolbar)
    layout.addWidget(result_table, 3)
    layout.addWidget(log_console, 1)
    window.setCentralWidget(central)

    status = StatusBar(window)
    window.setStatusBar(status)

    state: Dict[str, Any] = {"result": None}

    def _run() -> None:
        status.set_status("正在体检 ...")
        status.busy(True)
        log_console.append("开始运行 %s（dcc=%s）" % (TOOL_ID, dcc), "info")
        try:
            from tak.core.runner import run_tool

            # 面板是完整体检入口：内置规范规则 + 插件专项规则一起跑。
            result = run_tool(TOOL_ID, dcc=dcc,
                              options={"include_builtin": True})
        except Exception as exc:
            status.busy(False)
            status.set_status("体检失败")
            status.set_elapsed(None)
            log_console.error("运行失败: %s: %s" % (type(exc).__name__, exc))
            return
        state["result"] = result
        window._last_result = result
        status.busy(False)
        result_table.set_report(result.report)
        counts = result.report.counts()
        conclusion = "通过" if result.passed else "不通过"
        status.set_status("结论：%s · error %d / blocker %d"
                          % (conclusion, counts.get("error", 0),
                             counts.get("blocker", 0)))
        status.set_elapsed(getattr(result, "duration_ms", 0.0))
        log_console.append(
            "体检完成：%s（blocker %d / error %d / warning %d / info %d）"
            % (conclusion, counts.get("blocker", 0), counts.get("error", 0),
               counts.get("warning", 0), counts.get("info", 0)), "info")
        explanations = result.report.metadata.get("ai_explanations") or []
        if explanations:
            log_console.append("AI 解释层生成 %d 条（不参与判定）" % len(explanations),
                               "info")

    def _export() -> None:
        result = state.get("result")
        if result is None:
            log_console.warning("还没有体检结果，请先点击「运行体检」。")
            return
        fmt = str(format_box.currentData() or "json")
        info = [item for item in _EXPORT_FORMATS if item[1] == fmt][0]
        default_name = "rig_health_report%s" % info[2]
        path, _selected = QtWidgets.QFileDialog.getSaveFileName(
            window, "导出体检报告", default_name, info[3])
        if not path:
            return
        try:
            text = result.render(fmt)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except Exception as exc:
            log_console.error("导出失败: %s: %s" % (type(exc).__name__, exc))
            return
        log_console.append("已导出 %s：%s" % (fmt.upper(), path), "info")

    def _on_filter(_index: int) -> None:
        value = str(filter_box.currentData() or "")
        result_table.set_severity_filter(value or None)

    run_button.clicked.connect(_run)
    export_button.clicked.connect(_export)
    filter_box.currentIndexChanged.connect(_on_filter)

    if apply_theme:
        try:
            apply_theme(window, _THEME)
        except Exception:
            pass

    window._run_health = _run
    window._export_report = _export
    window._last_result = None
    window.show()
    return window
