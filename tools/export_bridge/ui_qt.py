# -*- coding: utf-8 -*-
"""Export Bridge 的 Qt 界面（可选层）。

只做展示：选择导出前 / 导出后两份快照 JSON，调用
``tak.core.runner.run_tool`` 得到校验结果，用 ``DiffPreview`` 展示差异、
用阻断状态标签说明是否允许继续导出。

``tak.ui.qt`` / ``tak.compat.qt`` 的 import 全部在 ``show()`` 内，
无 Qt 时 ``show()`` 返回 None；颜色只来自 ``tak/ui/theme.qss``。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
_THEME = os.path.join(_REPO_ROOT, "tak", "ui", "theme.qss")
TOOL_ID = "export_bridge"


def show(dcc: str = "maya", omui: Any = None) -> Optional[Any]:
    """打开导出前后对比窗口；无 Qt 或无 QApplication 时返回 None。"""
    try:
        from tak.ui.qt import HAS_QT, DiffPreview, LogConsole, ResultTable, StatusBar
    except Exception:
        return None
    if not HAS_QT:
        return None
    try:
        from tak.compat.qt import QtWidgets, apply_theme

        if QtWidgets.QApplication.instance() is None:
            return None
        return _build_window(QtWidgets, ResultTable, DiffPreview, LogConsole,
                            StatusBar, apply_theme, dcc=dcc, omui=omui)
    except Exception as exc:
        print("[TAK][ExportBridge] 界面创建失败: %s: %s"
              % (type(exc).__name__, exc))
        return None


def _diff_rows(diffs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把 verify 的 diff 项映射成 DiffPreview 的四列。"""
    rows: List[Dict[str, Any]] = []
    for item in diffs or []:
        if not isinstance(item, dict):
            continue
        rows.append({
            "field": str(item.get("type") or item.get("field") or ""),
            "joint": str(item.get("joint") or ""),
            "before": _cell(item.get("before")),
            "after": _cell(item.get("after")),
        })
    return rows


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _build_window(QtWidgets: Any, ResultTable: Any, DiffPreview: Any,
                  LogConsole: Any, StatusBar: Any, apply_theme: Any,
                  dcc: str, omui: Any) -> Any:
    window = QtWidgets.QMainWindow()
    window.setObjectName("tak_" + TOOL_ID)
    window.setWindowTitle("TAK · Export Bridge 导出前后校验 · %s" % dcc)
    window.resize(1120, 760)

    central = QtWidgets.QWidget(window)
    layout = QtWidgets.QVBoxLayout(central)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)

    title = QtWidgets.QLabel("Export Bridge 导出前后校验")
    title_font = title.font()
    title_font.setBold(True)
    title_font.setPointSize(title_font.pointSize() + 4)
    title.setFont(title_font)
    subtitle = QtWidgets.QLabel("导出前 sidecar 快照 · 导出后对比 · 阻断性偏差拦下导出")
    subtitle.setProperty("takMuted", "true")

    before_row = QtWidgets.QHBoxLayout()
    before_label = QtWidgets.QLabel("未选择导出前快照")
    before_label.setProperty("takMuted", "true")
    before_button = QtWidgets.QPushButton("选择 before ...")
    before_row.addWidget(before_button, 0)
    before_row.addWidget(before_label, 1)

    after_row = QtWidgets.QHBoxLayout()
    after_label = QtWidgets.QLabel("未选择导出后快照")
    after_label.setProperty("takMuted", "true")
    after_button = QtWidgets.QPushButton("选择 after ...")
    after_row.addWidget(after_button, 0)
    after_row.addWidget(after_label, 1)

    toolbar = QtWidgets.QHBoxLayout()
    toolbar.setSpacing(8)
    verify_button = QtWidgets.QPushButton("开始对比")
    verify_button.setProperty("takPrimary", "true")
    verify_button.setMinimumHeight(34)
    verdict_label = QtWidgets.QLabel("尚未校验")
    verdict_label.setProperty("takMuted", "true")
    toolbar.addWidget(verify_button, 0)
    toolbar.addWidget(verdict_label, 1)

    result_table = ResultTable(central)
    diff_preview = DiffPreview(central)
    diff_preview.setMinimumHeight(220)
    log_console = LogConsole(central)
    log_console.setMinimumHeight(100)

    layout.addWidget(title)
    layout.addWidget(subtitle)
    layout.addLayout(before_row)
    layout.addLayout(after_row)
    layout.addLayout(toolbar)
    layout.addWidget(diff_preview, 3)
    layout.addWidget(result_table, 2)
    layout.addWidget(log_console, 1)
    window.setCentralWidget(central)

    status = StatusBar(window)
    window.setStatusBar(status)

    state: Dict[str, Any] = {"before_path": "", "after_path": "", "verify": None}

    def _pick(key: str, label: Any) -> None:
        path, _selected = QtWidgets.QFileDialog.getOpenFileName(
            window, "选择 %s 快照" % key, "", "快照 JSON (*.json)")
        if not path:
            return
        state[key] = path
        label.setText(os.path.basename(path))
        log_console.append("已选择 %s 快照：%s" % (key.replace("_path", ""), path),
                           "info")

    def _verify() -> None:
        if not state["before_path"] or not state["after_path"]:
            log_console.warning("请先选择 before 与 after 两份快照 JSON。")
            return
        status.set_status("正在对比导出前后快照 ...")
        status.busy(True)
        try:
            from tak.core.runner import run_tool

            result = run_tool(TOOL_ID, dcc=dcc, options={
                "before_path": state["before_path"],
                "after_path": state["after_path"],
            })
        except Exception as exc:
            status.busy(False)
            status.set_status("对比失败")
            status.set_elapsed(None)
            log_console.error("对比失败: %s: %s" % (type(exc).__name__, exc))
            return

        status.busy(False)
        verify = (result.report.metadata or {}).get("verify") or {}
        state["verify"] = verify
        window._last_result = result
        window._verify_state = verify

        diffs = verify.get("diffs") or []
        diff_preview.set_diffs(_diff_rows(diffs))
        result_table.set_report(result.report)

        blocking = verify.get("blocking") or []
        if blocking:
            verdict_label.setText("阻断导出：%d 项阻断性偏差" % len(blocking))
            status.set_status("阻断导出 · 共 %d 项偏差" % len(diffs))
            log_console.error("检测到 %d 项阻断性偏差：%s"
                              % (len(blocking), verify.get("reason", "")))
        else:
            verdict_label.setText("允许继续：无阻断性偏差（%d 项提示）" % len(diffs))
            status.set_status("允许继续 · 共 %d 项提示" % len(diffs))
            log_console.append("无阻断性偏差：%s" % verify.get("reason", ""), "info")
        status.set_elapsed(getattr(result, "duration_ms", 0.0))

    before_button.clicked.connect(lambda: _pick("before_path", before_label))
    after_button.clicked.connect(lambda: _pick("after_path", after_label))
    verify_button.clicked.connect(_verify)

    if apply_theme:
        try:
            apply_theme(window, _THEME)
        except Exception:
            pass

    window._verify_pair = _verify
    window._last_result = None
    window._verify_state = None
    window.show()
    return window
