# -*- coding: utf-8 -*-
"""场景检查的独立界面：左侧勾选检查项，右侧看结果。

这个窗口是「每个工具有一个单独 UI」的示范实现：工具自己的界面负责**配置输入**
（勾哪些检查项），结果仍然落在 ``ResultTable`` 上，与其它工具共用同一套渲染。

无 Qt / 无 QApplication 时 ``show()`` 返回 None，不抛异常。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

TOOL_ID = "qa_inspector"


def _core() -> Any:
    """取本插件的 core 模块（模块名带前缀，不能直接相对导入）。"""
    from tak.core import runner
    return runner.load_tool_modules(runner.find_plugin(TOOL_ID))


def show(dcc: str = "maya", omui: Any = None) -> Optional[Any]:
    """打开检查窗口；不可用时返回 None。"""
    try:
        from tak.ui.qt import HAS_QT
    except Exception:
        return None
    if not HAS_QT:
        return None
    try:
        from tak.compat.qt import QtWidgets
        if QtWidgets.QApplication.instance() is None:
            return None
        return _build(dcc=str(dcc or "maya"))
    except Exception as exc:
        print("[TAK][QA] 界面创建失败: %s: %s" % (type(exc).__name__, exc))
        return None


def _build(dcc: str) -> Any:
    from tak.compat.qt import QtCore, QtWidgets, apply_theme
    from tak.ui.qt import LogConsole, ResultTable, theme_path
    from tak.ui.qt.launcher import host_main_window

    core = _core()
    groups = core.groups(dcc=dcc)
    selection = set(core.resolve_selection(dcc=dcc))

    parent = host_main_window(dcc)
    window = QtWidgets.QMainWindow(parent)
    window.setObjectName("takQaInspector")
    window.setWindowTitle("场景检查 · %s" % dcc)
    window.resize(1100, 730)

    central = QtWidgets.QWidget(window)
    outer = QtWidgets.QVBoxLayout(central)
    outer.setContentsMargins(16, 16, 16, 12)
    outer.setSpacing(10)

    # ---- 顶部说明 ----
    header = QtWidgets.QHBoxLayout()
    title_box = QtWidgets.QVBoxLayout()
    title_box.setSpacing(2)
    title = QtWidgets.QLabel("场景检查")
    font = title.font()
    font.setBold(True)
    font.setPointSize(font.pointSize() + 5)
    title.setFont(font)
    subtitle = QtWidgets.QLabel(
        "共 %d 组 %d 项。只读检查，不改场景；未勾选的项不会执行。"
        % (len(groups), sum(len(g["checks"]) for g in groups)))
    subtitle.setProperty("takMuted", "true")
    title_box.addWidget(title)
    title_box.addWidget(subtitle)
    header.addLayout(title_box, 1)

    select_all = QtWidgets.QPushButton("全选")
    select_all.setProperty("takGhost", "true")
    select_none = QtWidgets.QPushButton("全不选")
    select_none.setProperty("takGhost", "true")
    save_button = QtWidgets.QPushButton("保存勾选")
    save_button.setProperty("takGhost", "true")
    run_button = QtWidgets.QPushButton("运行检查")
    run_button.setProperty("takPrimary", "true")
    run_button.setMinimumHeight(34)
    run_button.setMinimumWidth(110)
    for button in (select_all, select_none, save_button):
        header.addWidget(button, 0)
    header.addSpacing(8)
    header.addWidget(run_button, 0)
    outer.addLayout(header)

    # ---- 主体：左树 右结果 ----
    splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
    tree = QtWidgets.QTreeWidget()
    tree.setObjectName("takCheckTree")
    tree.setHeaderLabels(["检查项", "级别"])
    tree.setColumnWidth(0, 400)
    tree.setAlternatingRowColors(True)
    splitter.addWidget(tree)

    right = QtWidgets.QWidget()
    right_layout = QtWidgets.QVBoxLayout(right)
    right_layout.setContentsMargins(0, 0, 0, 0)
    right_layout.setSpacing(6)
    table = ResultTable(right)
    log = LogConsole(right)
    right_layout.addWidget(table, 1)
    right_layout.addWidget(log, 0)
    log.setMaximumHeight(96)
    splitter.addWidget(right)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    splitter.setSizes([420, 680])
    outer.addWidget(splitter, 1)

    # ---- 底部状态 ----
    footer = QtWidgets.QHBoxLayout()
    status = QtWidgets.QLabel("就绪")
    status.setProperty("takHint", "true")
    status.setWordWrap(True)
    footer.addWidget(status, 1)
    outer.addLayout(footer)

    window.setCentralWidget(central)
    apply_theme(window, theme_path("dark"))

    checkable = QtCore.Qt.ItemIsUserCheckable
    checked = QtCore.Qt.Checked
    unchecked = QtCore.Qt.Unchecked

    def fill_tree() -> None:
        tree.blockSignals(True)
        tree.clear()
        for group in groups:
            top = QtWidgets.QTreeWidgetItem(tree, [group["name"], ""])
            top.setFlags(top.flags() | checkable | QtCore.Qt.ItemIsAutoTristate)
            top.setToolTip(0, group.get("description") or "")
            top.setExpanded(True)
            for item in group["checks"]:
                child = QtWidgets.QTreeWidgetItem(top, [item["id"], item["severity"]])
                child.setFlags(child.flags() | checkable)
                child.setData(0, QtCore.Qt.UserRole, item["id"])
                child.setCheckState(0, checked if item["id"] in selection else unchecked)
                tooltip = item.get("description") or ""
                if item.get("fix_available"):
                    tooltip += "（可自动修复）"
                child.setToolTip(0, tooltip)
        tree.blockSignals(False)

    def selected_ids() -> List[str]:
        out: List[str] = []
        root = tree.invisibleRootItem()
        for index in range(root.childCount()):
            group_item = root.child(index)
            for child_index in range(group_item.childCount()):
                child = group_item.child(child_index)
                if child.checkState(0) == checked:
                    out.append(str(child.data(0, QtCore.Qt.UserRole) or ""))
        return [item for item in out if item]

    def set_all(state: Any) -> None:
        tree.blockSignals(True)
        root = tree.invisibleRootItem()
        for index in range(root.childCount()):
            group_item = root.child(index)
            for child_index in range(group_item.childCount()):
                group_item.child(child_index).setCheckState(0, state)
        tree.blockSignals(False)

    def on_run() -> None:
        ids = selected_ids()
        run_button.setEnabled(False)
        status.setText("正在运行 %d 项检查 ..." % len(ids))
        try:
            from tak.core.runner import adapter_for, run_tool
            adapter = adapter_for(dcc)
            snapshot = None
            try:
                snapshot = adapter.get_skeleton_snapshot()
            except Exception:
                snapshot = None
            result = run_tool(TOOL_ID, snapshot, dcc=dcc, adapter=adapter,
                              options={"checks": ids})
            report = getattr(result, "report", None)
            table.set_report(report)
            counts = report.counts() if hasattr(report, "counts") else {}
            status.setText("完成：blocker %d / error %d / warning %d / info %d"
                           % (counts.get("blocker", 0), counts.get("error", 0),
                              counts.get("warning", 0), counts.get("info", 0)))
            log.append("检查完成，共 %d 项结果" % len(getattr(report, "findings", [])),
                       "info")
        except Exception as exc:
            status.setText("运行失败：%s: %s" % (type(exc).__name__, exc))
            log.error("检查失败：%s: %s" % (type(exc).__name__, exc))
        finally:
            run_button.setEnabled(True)

    def on_save() -> None:
        ids = selected_ids()
        result = core.save_selection(ids)
        if result.get("ok"):
            status.setText("已保存 %d 项勾选到 %s" % (len(ids), result.get("path")))
            log.append("已保存勾选：%d 项" % len(ids), "info")
        else:
            status.setText("保存失败：%s" % result.get("reason"))
            log.error("保存勾选失败：%s" % result.get("reason"))

    select_all.clicked.connect(lambda: set_all(checked))
    select_none.clicked.connect(lambda: set_all(unchecked))
    save_button.clicked.connect(on_save)
    run_button.clicked.connect(on_run)

    fill_tree()
    window.show()
    return window
