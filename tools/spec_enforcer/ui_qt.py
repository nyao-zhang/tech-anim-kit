# -*- coding: utf-8 -*-
"""Spec Enforcer 的 Qt 界面（可选层）。

设计要点
--------
* ``tak.ui.qt`` / ``tak.compat.qt`` 的 import 全部放在 ``show()`` 内部，
  无 Qt 的 CI / mayapy / 纯 Python 环境可安全 import，``show()`` 返回 None；
* 颜色、圆角、间距只来自 ``tak/ui/theme.qss``，Python 代码不写颜色值；
* 校验与计划生成只调用 ``tak.core.runner.run_tool`` 与插件 core 的公开函数，
  UI 不复制任何判定逻辑；
* 「应用修复」按钮**必须**经过 QMessageBox 二次确认才以 ``confirm=True``
  执行；取消时保持零写入。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
_THEME = os.path.join(_REPO_ROOT, "tak", "ui", "theme.qss")
TOOL_ID = "spec_enforcer"
_SCOPE_KEY = "spec_enforcer"


def show(dcc: str = "maya", omui: Any = None) -> Optional[Any]:
    """打开规范校验窗口；无 Qt 或无 QApplication 时返回 None。"""
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
    except Exception as exc:  # UI 构建失败不能拖垮宿主
        print("[TAK][SpecEnforcer] 界面创建失败: %s: %s" % (type(exc).__name__, exc))
        return None


def _plugin_core() -> Any:
    """按文件路径取插件 core，避免插件目录不在 sys.path 时的导入问题。"""
    from tak.core import registry, runner

    return registry.load_plugin_module(runner.find_plugin(TOOL_ID), "core")


def _preview_rows(plan_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把 DSL 计划转成 DiffPreview 需要的四列数据。"""
    from tak.core.dsl import ACTION_REGISTRY, DSLError, ExecutionPlan

    try:
        plan = ExecutionPlan.from_dict(plan_payload or {"actions": []})
        if not plan.actions:
            return []
        rows = ACTION_REGISTRY.preview(plan)
    except (DSLError, TypeError, ValueError):
        return []
    items: List[Dict[str, Any]] = []
    for row in rows:
        params = row.get("params") or {}
        items.append({
            "field": row.get("tool", ""),
            "joint": str(params.get("node") or params.get("name") or ""),
            "before": "-",
            "after": json.dumps(params, ensure_ascii=False, sort_keys=True),
        })
    return items


def _build_window(QtWidgets: Any, ResultTable: Any, DiffPreview: Any,
                  LogConsole: Any, StatusBar: Any, apply_theme: Any,
                  dcc: str, omui: Any) -> Any:
    window = QtWidgets.QMainWindow()
    window.setObjectName("tak_" + TOOL_ID)
    window.setWindowTitle("TAK · Spec Enforcer 规范校验 · %s" % dcc)
    window.resize(1120, 780)

    central = QtWidgets.QWidget(window)
    layout = QtWidgets.QVBoxLayout(central)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)

    title = QtWidgets.QLabel("Spec Enforcer 规范校验与修复")
    title_font = title.font()
    title_font.setBold(True)
    title_font.setPointSize(title_font.pointSize() + 4)
    title.setFont(title_font)
    subtitle = QtWidgets.QLabel("校验只读 · 修复计划默认 dry-run · 必须二次确认才写入场景")
    subtitle.setProperty("takMuted", "true")

    toolbar = QtWidgets.QHBoxLayout()
    toolbar.setSpacing(8)
    run_button = QtWidgets.QPushButton("运行规范校验")
    run_button.setProperty("takPrimary", "true")
    run_button.setMinimumHeight(34)
    apply_button = QtWidgets.QPushButton("应用修复")
    apply_button.setMinimumHeight(34)
    apply_button.setEnabled(False)

    result_table = ResultTable(central)
    diff_preview = DiffPreview(central)
    diff_preview.setMinimumHeight(200)
    log_console = LogConsole(central)
    log_console.setMinimumHeight(110)

    toolbar.addWidget(run_button, 0)
    toolbar.addWidget(apply_button, 0)
    toolbar.addStretch(1)

    layout.addWidget(title)
    layout.addWidget(subtitle)
    layout.addLayout(toolbar)
    layout.addWidget(result_table, 2)
    layout.addWidget(diff_preview, 2)
    layout.addWidget(log_console, 1)
    window.setCentralWidget(central)

    status = StatusBar(window)
    window.setStatusBar(status)

    state: Dict[str, Any] = {"result": None, "plan": {"actions": []}}

    def _run() -> None:
        status.set_status("正在运行规范校验 ...")
        status.busy(True)
        log_console.append("开始校验（dcc=%s，scope=%s）" % (dcc, _SCOPE_KEY), "info")
        try:
            from tak.core.runner import run_tool

            result = run_tool(TOOL_ID, dcc=dcc, options={_SCOPE_KEY: True})
        except Exception as exc:
            status.busy(False)
            status.set_status("校验失败")
            status.set_elapsed(None)
            log_console.error("运行失败: %s: %s" % (type(exc).__name__, exc))
            return

        state["result"] = result
        metadata = result.report.metadata or {}
        state["plan"] = metadata.get("fix_plan") or {"actions": []}
        window._last_result = result
        window._plan = state["plan"]

        status.busy(False)
        result_table.set_report(result.report)
        rows = _preview_rows(state["plan"])
        diff_preview.set_diffs(rows)
        apply_button.setEnabled(bool(rows))

        counts = result.report.counts()
        conclusion = "通过" if result.passed else "不通过"
        status.set_status("结论：%s · 计划 %d 项 · error %d / blocker %d"
                          % (conclusion, len(rows), counts.get("error", 0),
                             counts.get("blocker", 0)))
        status.set_elapsed(getattr(result, "duration_ms", 0.0))
        log_console.append(
            "校验完成：%s（error %d / warning %d / info %d），修复计划 %d 项（dry-run）"
            % (conclusion, counts.get("error", 0), counts.get("warning", 0),
               counts.get("info", 0), len(rows)), "info")
        skipped = metadata.get("skipped_fixes") or []
        if skipped:
            log_console.append("有 %d 项问题没有 DSL 动作，需人工处理" % len(skipped),
                               "warning")

    def _apply() -> None:
        plan_payload = state.get("plan") or {"actions": []}
        if not plan_payload.get("actions"):
            log_console.warning("当前没有可执行的修复计划，请先运行校验。")
            return
        try:
            from tak.core.dsl import ACTION_REGISTRY, ExecutionPlan

            plan = ExecutionPlan.from_dict(plan_payload)
            rows = ACTION_REGISTRY.preview(plan)
        except Exception as exc:
            log_console.error("计划无效: %s: %s" % (type(exc).__name__, exc))
            return
        required = sorted({item for row in rows
                           for item in (row.get("permissions") or [])})
        answer = QtWidgets.QMessageBox.question(
            window, "确认应用修复",
            "即将执行 %d 个修复动作，会修改当前场景：\n\n权限：%s\n\n"
            "执行器会开启 undo 并在任一动作失败时整体回滚。是否继续？"
            % (len(rows), "、".join(required) or "无"),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if answer != QtWidgets.QMessageBox.Yes:
            log_console.append("已取消，未对场景做任何修改。", "info")
            return

        status.set_status("正在应用修复 ...")
        status.busy(True)
        try:
            from tak.core.runner import adapter_for

            result = _plugin_core().apply_plan(plan, adapter_for(dcc),
                                               granted=required, confirm=True)
        except Exception as exc:
            status.busy(False)
            status.set_status("应用失败")
            log_console.error("应用失败（执行器已尝试整体回滚）: %s: %s"
                              % (type(exc).__name__, exc))
            return
        status.busy(False)
        if result.get("committed"):
            status.set_status("已应用 %d 个修复动作" % len(rows))
            log_console.append("修复已提交（%d 个动作）。" % len(rows), "info")
        else:
            status.set_status("未提交")
            log_console.warning("修复未提交：%s" % result.get("error", "未知原因"))
        skipped = [item for item in (result.get("changes") or [])
                   if item.get("status") != "done"]
        if skipped:
            log_console.warning("%d 个动作被跳过（适配器未实现或缺少目标名）"
                                % len(skipped))

    run_button.clicked.connect(_run)
    apply_button.clicked.connect(_apply)

    if apply_theme:
        try:
            apply_theme(window, _THEME)
        except Exception:
            pass

    window._run_spec_check = _run
    window._apply_plan = _apply
    window._last_result = None
    window._plan = {"actions": []}
    window.show()
    return window
