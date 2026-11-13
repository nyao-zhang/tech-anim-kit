# -*- coding: utf-8 -*-
"""UE Import Guard 的 Qt 界面（可选层）。

* ``tak.ui.qt`` / ``tak.compat.qt`` 的 import 全部在 ``show()`` 内部，
  无 Qt 的 CI / UE Python / 纯 Python 环境仍可安全 import；
* 无 Qt 或无 QApplication 时 ``show()`` 返回 ``None``，绝不抛异常；
* 颜色与间距只来自 ``tak/ui/theme.qss``，Python 侧不写任何颜色值；
* 判定逻辑全部在 ``core.py`` / ``rules.py``，UI 只调用
  ``tak.core.runner.run_tool`` 并渲染 Report。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
_THEME = os.path.join(_REPO_ROOT, "tak", "ui", "theme.qss")
TOOL_ID = "ue_import_guard"


def show(dcc: str = "unreal", omui: Any = None) -> Optional[Any]:
    """打开 UE 导入校验窗口；无 Qt 或无 QApplication 时返回 None。"""
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
        return _build_window(QtWidgets, ResultTable, LogConsole, StatusBar,
                             apply_theme, dcc=dcc, omui=omui)
    except Exception as exc:  # UI 构建失败不能拖垮宿主
        print("[TAK][UEImportGuard] 界面创建失败: %s: %s"
              % (type(exc).__name__, exc))
        return None


def _build_window(QtWidgets: Any, ResultTable: Any, LogConsole: Any,
                  StatusBar: Any, apply_theme: Any, dcc: str,
                  omui: Any) -> Any:
    window = QtWidgets.QMainWindow()
    window.setObjectName("tak_" + TOOL_ID)
    window.setWindowTitle("TAK · UE Import Guard 导入校验 · %s" % dcc)
    window.resize(1080, 720)

    central = QtWidgets.QWidget(window)
    layout = QtWidgets.QVBoxLayout(central)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)

    title = QtWidgets.QLabel("UE Import Guard 导入校验")
    title_font = title.font()
    title_font.setBold(True)
    title_font.setPointSize(title_font.pointSize() + 4)
    title.setFont(title_font)
    subtitle = QtWidgets.QLabel("只读校验 · sidecar 对比 · 不修改任何 UE 资产")
    subtitle.setProperty("takMuted", "true")

    toolbar = QtWidgets.QHBoxLayout()
    toolbar.setSpacing(8)
    run_button = QtWidgets.QPushButton("运行校验")
    run_button.setProperty("takPrimary", "true")
    run_button.setMinimumHeight(34)
    toolbar.addWidget(run_button, 0)
    toolbar.addStretch(1)

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
        status.set_status("正在校验 ...")
        status.busy(True)
        log_console.append("开始运行 %s（dcc=%s）" % (TOOL_ID, dcc), "info")
        try:
            from tak.core.runner import run_tool

            result = run_tool(TOOL_ID, dcc=dcc)
        except Exception as exc:
            status.busy(False)
            status.set_status("校验失败")
            status.set_elapsed(None)
            log_console.error("运行失败: %s: %s" % (type(exc).__name__, exc))
            return
        state["result"] = result
        window._last_result = result
        status.busy(False)
        result_table.set_report(result.report)
        counts = result.report.counts()
        conclusion = "通过" if result.passed else "不通过"
        status.set_status("结论：%s · blocker %d / error %d"
                          % (conclusion, counts.get("blocker", 0),
                             counts.get("error", 0)))
        status.set_elapsed(getattr(result, "duration_ms", 0.0))
        _log_summary(log_console, result)

    run_button.clicked.connect(_run)

    if apply_theme:
        try:
            apply_theme(window, _THEME)
        except Exception:
            pass

    window._run_import_check = _run
    window._last_result = None
    window.show()
    return window


def _log_summary(log_console: Any, result: Any) -> None:
    """把校验摘要与 api_notes 写进日志台（只读展示，不影响结论）。"""
    metadata = getattr(result.report, "metadata", None) or {}
    validate = metadata.get("validate") if isinstance(metadata, dict) else None
    if isinstance(validate, dict):
        log_console.append("导入校验：%s" % (validate.get("reason") or "未执行"),
                           "info")
    notes: List[Dict[str, Any]] = []
    if isinstance(metadata, dict):
        raw = metadata.get("api_notes")
        notes = [item for item in (raw or []) if isinstance(item, dict)]
    if notes:
        log_console.append("UE Python API 缺口 %d 条（不影响判定）" % len(notes),
                           "info")
