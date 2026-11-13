# -*- coding: utf-8 -*-
"""ML Deformer 数据工厂的 Qt 界面（可选层）。

* ``tak.ui.qt`` / ``tak.compat.qt`` 的 import 全部在 ``show()`` 内部，
  无 Qt 的 CI / mayapy / 纯 Python 环境仍可安全 import；
* 无 Qt 或无 ``QApplication`` 时 ``show()`` 返回 ``None``，绝不抛异常；
* 颜色与间距只来自 ``tak/ui/theme.qss``，Python 侧不写任何颜色值；
* 采样与判定全部在 ``core.py`` / ``tak.core.mldeformer``，UI 只调用
  ``tak.core.runner.run_tool`` 并渲染 Report，绝不写场景、不调训练入口。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
_THEME = os.path.join(_REPO_ROOT, "tak", "ui", "theme.qss")
TOOL_ID = "mldeformer_factory"


def show(dcc: str = "maya", omui: Any = None) -> Optional[Any]:
    """打开数据工厂窗口；无 Qt 或无 QApplication 时返回 None。"""
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
    except Exception as exc:  # 界面构建失败不能拖垮宿主
        print("[TAK][MLDeformerFactory] 界面创建失败: %s: %s"
              % (type(exc).__name__, exc))
        return None


def _build_window(QtWidgets: Any, ResultTable: Any, LogConsole: Any,
                  StatusBar: Any, apply_theme: Any, dcc: str,
                  omui: Any) -> Any:
    window = QtWidgets.QMainWindow()
    window.setObjectName("tak_" + TOOL_ID)
    window.setWindowTitle("TAK · ML Deformer 数据工厂 · %s" % dcc)
    window.resize(1080, 720)

    central = QtWidgets.QWidget(window)
    layout = QtWidgets.QVBoxLayout(central)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)

    title = QtWidgets.QLabel("ML Deformer 数据工厂")
    title_font = title.font()
    title_font.setBold(True)
    title_font.setPointSize(title_font.pointSize() + 4)
    title.setFont(title_font)
    subtitle = QtWidgets.QLabel(
        "只读采样计划 · 内容寻址清单 · 质量门槛；不写场景、不调用训练入口"
        "（mldeformer.train = false）")
    subtitle.setProperty("takMuted", "true")
    subtitle.setWordWrap(True)

    toolbar = QtWidgets.QHBoxLayout()
    toolbar.setSpacing(8)
    run_button = QtWidgets.QPushButton("生成数据集清单")
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
        status.set_status("正在生成数据集清单 ...")
        status.busy(True)
        log_console.append("开始运行 %s（dcc=%s）" % (TOOL_ID, dcc), "info")
        try:
            from tak.core.runner import run_tool

            result = run_tool(TOOL_ID, dcc=dcc)
        except Exception as exc:
            status.busy(False)
            status.set_status("生成失败")
            status.set_elapsed(None)
            log_console.error("运行失败: %s: %s" % (type(exc).__name__, exc))
            return
        state["result"] = result
        window._last_result = result
        status.busy(False)
        result_table.set_report(result.report)
        counts = result.report.counts()
        conclusion = "通过" if result.passed else "未通过"
        status.set_status("结论：%s · error %d / warning %d"
                          % (conclusion, counts.get("error", 0),
                             counts.get("warning", 0)))
        status.set_elapsed(getattr(result, "duration_ms", 0.0))
        _log_summary(log_console, result)

    run_button.clicked.connect(_run)

    if apply_theme:
        try:
            apply_theme(window, _THEME)
        except Exception:
            pass

    window._run_manifest = _run
    window._last_result = None
    window.show()
    return window


def _log_summary(log_console: Any, result: Any) -> None:
    """把数据集摘要与门槛计数写进日志台（只读展示，不影响结论）。"""
    metadata = getattr(result.report, "metadata", None) or {}
    if not isinstance(metadata, dict):
        return
    manifest = metadata.get("manifest")
    manifest = manifest if isinstance(manifest, dict) else {}
    if manifest:
        log_console.append(
            "数据集 %s · pose %d · 关节 %d · 重复率 %s"
            % (manifest.get("dataset_id", "-"), manifest.get("pose_count", 0),
               manifest.get("joint_count", 0), manifest.get("duplicate_ratio", 0.0)),
            "info")
        categories = manifest.get("categories")
        if isinstance(categories, dict) and categories:
            log_console.append(
                "类别：" + "、".join("%s %d" % (name, categories[name])
                                    for name in sorted(categories)), "info")
    safety = metadata.get("safety")
    safety = safety if isinstance(safety, dict) else {}
    notes: List[str] = []
    if safety:
        notes.append("训练入口：%s" % ("未调用" if not safety.get("training_api")
                                      else "已调用"))
        notes.append("写场景：%s" % ("否" if not safety.get("scene_write") else "是"))
    if notes:
        log_console.append(" · ".join(notes), "info")
