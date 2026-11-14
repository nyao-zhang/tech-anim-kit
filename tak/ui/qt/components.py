# -*- coding: utf-8 -*-
"""TAK 通用 Qt 组件库（Maya 2023+ / PySide2 & PySide6）。

设计原则
--------
* 只从 ``tak.compat.qt`` 取 Qt 绑定，不写任何 PySide 版本判断；
* **Python 代码里不出现颜色值**：卡片、主按钮、弱化文字、严重级别颜色
  全部通过 ``tak/ui/theme.qss`` 的属性选择器驱动，深浅主题靠切换 QSS；
* 无 Qt（纯 Python / mayapy / CI）时模块可安全 import，所有工厂函数返回 None；
* 组件对异常零容忍：运行工具失败只进日志台，绝不让 UI 崩溃。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence, Set

from tak.compat.qt import QT_BINDING, QtCore, QtGui, QtWidgets, apply_theme

HAS_QT = (QtWidgets is not None and QtCore is not None and QtGui is not None)
_UI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THEME_FILES = {"dark": "theme.qss", "light": "theme_light.qss"}

_SEVERITY_ORDER = {"blocker": 0, "error": 1, "warning": 2, "info": 3}
_SEVERITY_LABELS = ("blocker", "error", "warning", "info")
_LEVEL_PREFIX = {"debug": "[DEBUG]", "info": "[INFO]", "warning": "[WARN]",
                 "warn": "[WARN]", "error": "[ERROR]", "critical": "[CRIT]"}


# --------------------------------------------------------------------------- #
# Qt 基类与降级占位
# --------------------------------------------------------------------------- #
if HAS_QT:
    _FrameBase = QtWidgets.QFrame
    _WidgetBase = QtWidgets.QWidget
    _PlainTextBase = QtWidgets.QPlainTextEdit
    _StatusBarBase = QtWidgets.QStatusBar
    _MainWindowBase = QtWidgets.QMainWindow
else:  # pragma: no cover - 无 Qt 环境只要求可 import、可实例化为空壳
    class _DummyBase(object):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    _FrameBase = _WidgetBase = _PlainTextBase = _StatusBarBase = _MainWindowBase = _DummyBase


def _qt_ready() -> bool:
    """Qt 绑定可用且宿主已创建 QApplication 时才允许造控件。"""
    if not HAS_QT:
        return False
    try:
        return QtWidgets.QApplication.instance() is not None
    except Exception:
        return False


def _severity_of(finding: Any) -> str:
    value = getattr(finding, "severity", "info")
    value = value.value if hasattr(value, "value") else value
    text = str(value or "info").lower()
    return text if text in _SEVERITY_ORDER else "info"


def _severity_set(value: Any) -> Optional[Set[str]]:
    if value is None:
        return None
    if isinstance(value, str):
        values = {value.lower()}
    else:
        try:
            values = {str(item.value if hasattr(item, "value") else item).lower()
                      for item in value}
        except TypeError:
            values = {str(value).lower()}
    return {item for item in values if item in _SEVERITY_ORDER} or None


def _text(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, dict, set)):
        try:
            return json.dumps(value if not isinstance(value, set) else sorted(value),
                              ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _theme_path(name: str) -> str:
    return os.path.join(_UI_DIR, THEME_FILES.get(str(name or "dark").lower(),
                                                THEME_FILES["dark"]))


# --------------------------------------------------------------------------- #
# 工具卡片
# --------------------------------------------------------------------------- #
class ToolCard(_FrameBase):
    """卡片式工具入口：标题 + 说明 + 主运行按钮。"""

    def __init__(self, title: str = "", description: str = "",
                 on_run: Any = None, parent: Any = None) -> None:
        super().__init__(parent)
        if not HAS_QT:
            return
        self._on_run = on_run
        self._tool_id = ""
        self.setProperty("takCard", "true")
        self.setObjectName("takToolCard")
        self.setMinimumHeight(150)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)
        self.title_label = QtWidgets.QLabel(title or "未选择工具")
        title_font = self.title_label.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 3)
        self.title_label.setFont(title_font)
        self.title_label.setWordWrap(True)
        self.badge_label = QtWidgets.QLabel("")
        self.badge_label.setProperty("takHint", "true")
        header.addWidget(self.title_label, 1)
        header.addWidget(self.badge_label, 0)

        self.description_label = QtWidgets.QLabel(description or "从左侧选择一个工具插件。")
        self.description_label.setProperty("takMuted", "true")
        self.description_label.setWordWrap(True)

        footer = QtWidgets.QHBoxLayout()
        footer.setSpacing(8)
        self.hint_label = QtWidgets.QLabel("")
        self.hint_label.setProperty("takHint", "true")
        self.hint_label.setWordWrap(True)
        self.run_button = QtWidgets.QPushButton("运行")
        self.run_button.setProperty("takPrimary", "true")
        self.run_button.setMinimumHeight(34)
        self.run_button.setMinimumWidth(96)
        self.run_button.clicked.connect(self.run)
        footer.addWidget(self.hint_label, 1)
        footer.addWidget(self.run_button, 0)

        layout.addLayout(header)
        layout.addWidget(self.description_label, 1)
        layout.addLayout(footer)

    def set_tool(self, name: str, description: str = "", hint: str = "",
                 tool_id: str = "") -> "ToolCard":
        """更新卡片内容；tool_id 会回传给 on_run 回调。"""
        self._tool_id = str(tool_id or "")
        if not HAS_QT:
            return self
        self.title_label.setText(name or "未选择工具")
        self.description_label.setText(description or "从左侧选择一个工具插件。")
        self.hint_label.setText(hint)
        self.badge_label.setText("就绪" if tool_id else "")
        self.run_button.setVisible(bool(tool_id))
        self.run_button.setEnabled(bool(tool_id))
        return self

    def set_runnable(self, enabled: bool) -> "ToolCard":
        if HAS_QT:
            self.run_button.setEnabled(bool(enabled))
        return self

    def run(self) -> None:
        """点击运行；回调异常只记录到标准错误，不让 UI 崩溃。"""
        if not HAS_QT or not callable(self._on_run):
            return
        try:
            self._on_run(self._tool_id)
        except Exception as exc:  # 回调应由宿主兜底，这里再保一层
            print("[TAK][error] 工具卡片回调失败: %s: %s" % (type(exc).__name__, exc))


# --------------------------------------------------------------------------- #
# 结果表
# --------------------------------------------------------------------------- #
class ResultTable(_FrameBase):
    """Report.findings 表格：级别 badge / 规则 / 节点 / 说明 / 修复建议。"""

    COLUMNS = ("级别", "规则", "节点", "说明", "修复建议")

    def __init__(self, parent: Any = None, title: str = "运行结果") -> None:
        super().__init__(parent)
        if not HAS_QT:
            return
        self._findings: List[Any] = []
        self._filter: Optional[Set[str]] = None
        self.setProperty("takCard", "true")
        self.setObjectName("takResultTable")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        head = QtWidgets.QHBoxLayout()
        head.setSpacing(8)
        # 标题由调用方决定：以前硬编码「体检结果」，导致导出、ML 数据集、
        # 缺陷检查这些工具的界面看起来全都是同一个体检窗口。
        self.heading_label = QtWidgets.QLabel(str(title or "运行结果"))
        title_font = self.heading_label.font()
        title_font.setBold(True)
        self.heading_label.setFont(title_font)
        self.summary_label = QtWidgets.QLabel("")
        self.summary_label.setProperty("takMuted", "true")
        self.filter_box = QtWidgets.QComboBox()
        self.filter_box.addItem("全部级别", "")
        for severity in _SEVERITY_LABELS:
            self.filter_box.addItem(severity.upper(), severity)
        self.filter_box.currentIndexChanged.connect(self._on_filter_changed)
        head.addWidget(self.heading_label, 0)
        head.addWidget(self.summary_label, 1)
        head.addWidget(self.filter_box, 0)

        self.empty_label = QtWidgets.QLabel("暂无结果，运行工具后在此查看。")
        self.empty_label.setProperty("takMuted", "true")
        self.empty_label.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_label.setMinimumHeight(140)

        self.table = QtWidgets.QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(list(self.COLUMNS))
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        for column, width in enumerate((80, 200, 160)):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.Fixed)
            self.table.setColumnWidth(column, width)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.Fixed)
        self.table.setColumnWidth(4, 240)

        layout.addLayout(head)
        layout.addWidget(self.empty_label, 1)
        layout.addWidget(self.table, 1)
        self._refresh()

    # ------------------------------------------------------------ 数据入口
    def set_title(self, text: str) -> "ResultTable":
        """改表头标题；工具可以给自己的结果区起名字。"""
        if HAS_QT and hasattr(self, "heading_label"):
            self.heading_label.setText(str(text or "运行结果"))
        return self

    def set_report(self, report: Any) -> "ResultTable":
        findings = list(getattr(report, "findings", []) or [])
        return self.set_findings(findings)

    def set_findings(self, findings: Sequence[Any]) -> "ResultTable":
        self._findings = list(findings or [])
        if HAS_QT:
            self._refresh()
        return self

    def set_severity_filter(self, severity: Any = None) -> "ResultTable":
        """按 severity 过滤；支持 None / 单个字符串 / 字符串集合。"""
        self._filter = _severity_set(severity)
        if HAS_QT:
            self.filter_box.blockSignals(True)
            index = 0
            if self._filter and len(self._filter) == 1:
                found = self.filter_box.findData(next(iter(self._filter)))
                index = found if found >= 0 else 0
            self.filter_box.setCurrentIndex(index)
            self.filter_box.blockSignals(False)
            self._refresh()
        return self

    def visible_findings(self) -> List[Any]:
        if self._filter is None:
            return list(self._findings)
        return [item for item in self._findings if _severity_of(item) in self._filter]

    # ------------------------------------------------------------ 内部渲染
    def _on_filter_changed(self, index: int) -> None:
        data = self.filter_box.itemData(index) if HAS_QT else ""
        self._filter = {data} if data else None
        self._refresh()

    def _refresh(self) -> None:
        if not HAS_QT:
            return
        rows = self.visible_findings()
        counts = {name: 0 for name in _SEVERITY_LABELS}
        for item in self._findings:
            counts[_severity_of(item)] = counts.get(_severity_of(item), 0) + 1
        self.summary_label.setText("共 %d 条 · blocker %d / error %d / warning %d / info %d"
                                   % (len(self._findings), counts["blocker"], counts["error"],
                                      counts["warning"], counts["info"]))
        self.table.setRowCount(0)
        for row, finding in enumerate(rows):
            self.table.insertRow(row)
            self.table.setRowHeight(row, 32)
            severity = _severity_of(finding)
            badge = QtWidgets.QLabel(severity.upper())
            badge.setProperty("takSeverity", severity)
            badge.setAlignment(QtCore.Qt.AlignCenter)
            badge.setMinimumWidth(64)
            self.table.setCellWidget(row, 0, badge)
            values = (getattr(finding, "rule_id", ""), getattr(finding, "node", ""),
                      getattr(finding, "message", ""), getattr(finding, "fix_hint", ""))
            for column, value in enumerate(values, start=1):
                cell = QtWidgets.QTableWidgetItem(str(value or ""))
                cell.setToolTip(str(value or ""))
                self.table.setItem(row, column, cell)
        has_rows = bool(rows)
        self.table.setVisible(has_rows)
        self.empty_label.setVisible(not has_rows)
        self.empty_label.setText("当前过滤条件下无记录。" if self._findings else
                                 "暂无结果，运行工具后在此查看。")


# --------------------------------------------------------------------------- #
# Diff 预览
# --------------------------------------------------------------------------- #
class DiffPreview(_FrameBase):
    """diff_snapshots 结果预览：field / joint / before / after。"""

    COLUMNS = ("字段", "关节", "变更前", "变更后")

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        if not HAS_QT:
            return
        self.setProperty("takCard", "true")
        self.setObjectName("takDiffPreview")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        head = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("快照差异")
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        self.count_label = QtWidgets.QLabel("")
        self.count_label.setProperty("takMuted", "true")
        head.addWidget(title, 0)
        head.addWidget(self.count_label, 1)

        self.empty_label = QtWidgets.QLabel("没有差异：导入前后快照一致。")
        self.empty_label.setProperty("takMuted", "true")
        self.empty_label.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_label.setMinimumHeight(140)

        self.table = QtWidgets.QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(list(self.COLUMNS))
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
        self.table.setColumnWidth(0, 150)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Fixed)
        self.table.setColumnWidth(1, 180)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)

        layout.addLayout(head)
        layout.addWidget(self.empty_label, 1)
        layout.addWidget(self.table, 1)
        self.set_diffs([])

    def set_diffs(self, diffs: Sequence[Dict[str, Any]]) -> "DiffPreview":
        if not HAS_QT:
            return self
        items = [item for item in (diffs or []) if isinstance(item, dict)]
        self.table.setRowCount(0)
        for row, item in enumerate(items):
            self.table.insertRow(row)
            self.table.setRowHeight(row, 32)
            for column, key in enumerate(("field", "joint", "before", "after")):
                value = _text(item.get(key))
                cell = QtWidgets.QTableWidgetItem(value)
                cell.setToolTip(value)
                self.table.setItem(row, column, cell)
        self.count_label.setText("共 %d 项" % len(items))
        self.table.setVisible(bool(items))
        self.empty_label.setVisible(not items)
        return self


# --------------------------------------------------------------------------- #
# 日志台
# --------------------------------------------------------------------------- #
class LogConsole(_PlainTextBase):
    """只读日志台；级别用 ``[INFO]`` / ``[WARN]`` / ``[ERROR]`` 前缀区分。"""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        if not HAS_QT:
            return
        self.setReadOnly(True)
        self.setMaximumBlockCount(4000)
        self.setPlaceholderText("工具运行日志会显示在这里。")
        self.setObjectName("takLogConsole")

    def append(self, message: Any, level: str = "info") -> None:  # noqa: A003
        if not HAS_QT:
            return
        prefix = _LEVEL_PREFIX.get(str(level or "info").lower(), "[INFO]")
        text = str(message if message is not None else "")
        for line in text.splitlines() or [""]:
            self.appendPlainText("%s %s" % (prefix, line))
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def info(self, message: Any) -> None:
        self.append(message, "info")

    def warning(self, message: Any) -> None:
        self.append(message, "warning")

    def error(self, message: Any) -> None:
        self.append(message, "error")


# --------------------------------------------------------------------------- #
# 状态栏
# --------------------------------------------------------------------------- #
class StatusBar(_StatusBarBase):
    """底部状态栏：状态文本 + 不确定进度条 + 耗时。"""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        if not HAS_QT:
            return
        self.setSizeGripEnabled(False)
        self.setContentsMargins(8, 4, 8, 4)
        self.status_label = QtWidgets.QLabel("就绪")
        self.progress = QtWidgets.QProgressBar()
        self.progress.setFixedWidth(180)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.elapsed_label = QtWidgets.QLabel("")
        self.elapsed_label.setProperty("takMuted", "true")
        self.addWidget(self.status_label, 1)
        self.addPermanentWidget(self.progress, 0)
        self.addPermanentWidget(self.elapsed_label, 0)

    def set_status(self, text: str) -> "StatusBar":
        if HAS_QT:
            self.status_label.setText(str(text or ""))
        return self

    def set_elapsed(self, milliseconds: Any) -> "StatusBar":
        if not HAS_QT:
            return self
        if milliseconds is None:
            self.elapsed_label.setText("")
        else:
            try:
                self.elapsed_label.setText("耗时 %.1f ms" % float(milliseconds))
            except (TypeError, ValueError):
                self.elapsed_label.setText("")
        return self

    def set_progress(self, value: Optional[int], maximum: int = 100) -> "StatusBar":
        if not HAS_QT:
            return self
        if value is None:
            self.progress.setRange(0, 0)
            self.progress.setVisible(True)
        else:
            top = max(1, int(maximum))
            current = max(0, min(int(value), top))
            self.progress.setRange(0, top)
            self.progress.setValue(current)
            self.progress.setVisible(current > 0)
        return self

    def busy(self, active: bool = True) -> "StatusBar":
        return self.set_progress(None if active else 0)


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #
class ToolMainWindow(_MainWindowBase):
    """TAK 主窗口：左侧「分类 → 工具」两级导航 + 右侧详情 / 结果 / 日志。

    与 v0.3 的区别（这是用户反馈里最关键的一条）：老版左侧平铺全部插件、
    右侧只有一张卡片加一个「运行」按钮，任何工具点下去都是同一张体检结果表，
    用户无法分辨工具之间的差异。现在：

    * 左栏先按分类过滤（全部 / 通用 / 绑定 / 动画 / 管线 / AI，分类可由用户增删改）；
    * 选中工具后右侧渲染 manifest 自描述的「作用 / 功能 / 怎么用 / 输入输出」；
    * 「打开工具界面」调该工具自己的 ``ui_qt.show()``，「快速运行」走通用契约。
    """

    def __init__(self, dcc: str = "maya", parent: Any = None,
                 theme: str = "dark") -> None:
        super().__init__(parent)
        if not HAS_QT:
            return
        from tak.core import runner  # 延迟导入，避免 UI 包拖慢 core 启动

        self._runner = runner
        self.dcc = str(dcc or "maya")
        self._theme = "dark"
        self._tools: List[Dict[str, Any]] = []
        self._categories: List[Dict[str, Any]] = []
        self._current_tool_id = ""
        self._current_category = "all"

        self.setObjectName("takMainWindow")
        self.setWindowTitle("TAK 技术动画工具集 · %s" % self.dcc)
        self.resize(1260, 820)
        self.setMinimumSize(980, 640)
        self._build_ui()
        self.set_theme(theme)
        self.reload_tools()

    # ------------------------------------------------------------ 构建
    def _build_ui(self) -> None:
        central = QtWidgets.QWidget(self)
        outer = QtWidgets.QHBoxLayout(central)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(14)

        # ---------------- 左栏：分类 + 工具 ----------------
        sidebar = QtWidgets.QFrame()
        sidebar.setProperty("takCard", "true")
        sidebar.setFixedWidth(248)
        side_layout = QtWidgets.QVBoxLayout(sidebar)
        side_layout.setContentsMargins(12, 12, 12, 12)
        side_layout.setSpacing(6)

        cat_title = QtWidgets.QLabel("分类")
        cat_font = cat_title.font()
        cat_font.setBold(True)
        cat_title.setFont(cat_font)
        self.category_list = QtWidgets.QListWidget()
        self.category_list.setObjectName("takCategoryList")
        self.category_list.setSpacing(1)
        self.category_list.setMaximumHeight(196)
        self.category_list.currentRowChanged.connect(self._on_category_selected)

        tool_title = QtWidgets.QLabel("工具")
        tool_title.setFont(cat_font)
        self.tool_list = QtWidgets.QListWidget()
        self.tool_list.setSpacing(2)
        self.tool_list.currentRowChanged.connect(self._on_tool_selected)

        side_hint = QtWidgets.QLabel("在设置面板里可以改分类、导入第三方工具")
        side_hint.setProperty("takHint", "true")
        side_hint.setWordWrap(True)
        self.settings_button = QtWidgets.QPushButton("设置")
        self.settings_button.setProperty("takGhost", "true")
        self.settings_button.clicked.connect(self.open_settings)

        side_layout.addWidget(cat_title)
        side_layout.addWidget(self.category_list, 0)
        side_layout.addSpacing(4)
        side_layout.addWidget(tool_title)
        side_layout.addWidget(self.tool_list, 1)
        side_layout.addWidget(side_hint)
        side_layout.addWidget(self.settings_button, 0)

        # ---------------- 右栏：标题 + 页签 ----------------
        main = QtWidgets.QVBoxLayout()
        main.setSpacing(10)
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)
        title_box = QtWidgets.QVBoxLayout()
        title_box.setSpacing(2)
        main_title = QtWidgets.QLabel("TAK 技术动画工具集")
        main_font = main_title.font()
        main_font.setBold(True)
        main_font.setPointSize(main_font.pointSize() + 4)
        main_title.setFont(main_font)
        subtitle = QtWidgets.QLabel("DCC %s · Qt %s · 统一快照 / 规则 / 报告"
                                    % (self.dcc, QT_BINDING))
        subtitle.setProperty("takMuted", "true")
        title_box.addWidget(main_title)
        title_box.addWidget(subtitle)
        self.theme_button = QtWidgets.QPushButton("切换主题")
        self.theme_button.setProperty("takGhost", "true")
        self.theme_button.clicked.connect(self.toggle_theme)
        header.addLayout(title_box, 1)
        header.addWidget(self.theme_button, 0)

        from .detail import ToolDetailPanel
        self.detail_panel = ToolDetailPanel(on_open=self.open_tool,
                                            on_run=self.run_tool, parent=self)
        self.result_table = ResultTable(self)
        self.diff_preview = DiffPreview(self)
        self.log_console = LogConsole(self)
        self.log_page = QtWidgets.QWidget()
        log_layout = QtWidgets.QVBoxLayout(self.log_page)
        log_layout.setContentsMargins(12, 12, 12, 12)
        log_layout.setSpacing(0)
        log_layout.addWidget(self.log_console)
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.detail_panel, "详情")
        self.tabs.addTab(self.result_table, "结果")
        self.tabs.addTab(self.log_page, "日志")
        # 原生 QTabBar 的选中页由系统样式绘制，会和 QSS 主题撞色；
        # 这里隐藏原生 tab bar，用受 QSS 控制的按钮做页签，tab 内容仍由 QTabWidget 管理。
        self.tabs.tabBar().setVisible(False)
        self.tab_buttons: List[Any] = []
        tab_nav = QtWidgets.QWidget()
        tab_nav_layout = QtWidgets.QHBoxLayout(tab_nav)
        tab_nav_layout.setContentsMargins(0, 0, 0, 0)
        tab_nav_layout.setSpacing(6)
        for index, label in enumerate(("详情", "结果", "日志")):
            button = QtWidgets.QPushButton(label)
            button.setMinimumWidth(86)
            button.clicked.connect(lambda _checked=False, i=index: self._switch_tab(i))
            tab_nav_layout.addWidget(button, 0)
            self.tab_buttons.append(button)
        tab_nav_layout.addStretch(1)

        main.addLayout(header)
        main.addWidget(tab_nav, 0)
        main.addWidget(self.tabs, 1)
        outer.addWidget(sidebar, 0)
        outer.addLayout(main, 1)
        self.setCentralWidget(central)

        self.status_bar = StatusBar(self)
        self.setStatusBar(self.status_bar)
        self._switch_tab(0)

    def _switch_tab(self, index: int) -> int:
        """切换 QTabWidget 页并同步按钮的 takPrimary 状态。"""
        if not HAS_QT or not self.tab_buttons:
            return 0
        index = max(0, min(int(index), len(self.tab_buttons) - 1))
        self.tabs.setCurrentIndex(index)
        for position, button in enumerate(self.tab_buttons):
            button.setProperty("takPrimary", "true" if position == index else "false")
            try:
                button.style().unpolish(button)
                button.style().polish(button)
            except Exception:
                pass
        return index

    # ------------------------------------------------------------ 主题
    @property
    def theme(self) -> str:
        return self._theme

    def set_theme(self, name: str) -> bool:
        """切换 QSS 主题；颜色只来自 tak/ui/theme*.qss。"""
        if not HAS_QT:
            return False
        target = str(name or "dark").lower()
        if target not in THEME_FILES:
            target = "dark"
        applied = apply_theme(self, _theme_path(target))
        if applied:
            self._theme = target
        return applied

    def toggle_theme(self) -> str:
        new_theme = "light" if self._theme == "dark" else "dark"
        self.set_theme(new_theme)
        self.log_console.append("主题切换为 %s" % self._theme, "info")
        return self._theme

    # ------------------------------------------------------------ 分类与工具列表
    def reload_tools(self) -> List[Dict[str, Any]]:
        """重新读分类与工具；保留当前分类选择（不存在则回落到「全部」）。"""
        if not HAS_QT:
            return []
        try:
            self._categories = list(self._runner.list_categories(dcc=self.dcc) or [])
        except Exception as exc:
            self._categories = []
            self.log_console.append("分类读取失败: %s: %s"
                                    % (type(exc).__name__, exc), "error")

        previous = self._current_category
        self.category_list.blockSignals(True)
        self.category_list.clear()
        target_row = 0
        for row, cat in enumerate(self._categories):
            item = QtWidgets.QListWidgetItem("%s · %d" % (cat.get("name") or cat.get("id"),
                                                          int(cat.get("count") or 0)))
            item.setToolTip(str(cat.get("description") or ""))
            item.setData(QtCore.Qt.UserRole, str(cat.get("id") or ""))
            self.category_list.addItem(item)
            if str(cat.get("id")) == previous:
                target_row = row
        self.category_list.blockSignals(False)
        if self._categories:
            self.category_list.setCurrentRow(target_row)
            self._current_category = str(
                self._categories[target_row].get("id") or "all")
        else:
            self._current_category = "all"

        self._refresh_tool_list()
        return list(self._tools)

    def _on_category_selected(self, row: int) -> None:
        if not HAS_QT or row < 0 or row >= len(self._categories):
            return
        self._current_category = str(self._categories[row].get("id") or "all")
        self._refresh_tool_list()

    def _refresh_tool_list(self) -> None:
        """按当前分类拉取工具并重填列表。"""
        if not HAS_QT:
            return
        try:
            self._tools = list(self._runner.list_tools(
                dcc=self.dcc, category=self._current_category) or [])
        except Exception as exc:
            self._tools = []
            self.log_console.append("插件发现失败: %s: %s"
                                    % (type(exc).__name__, exc), "error")
        # 批量重建时先停掉重绘：Maya 里每新建一个 QLabel 都会触发一次 QSS
        # 重算与重绘，连续切分类时用户会明显感到「卡住几秒」。
        self.tool_list.setUpdatesEnabled(False)
        self.detail_panel.setUpdatesEnabled(False)
        try:
            self.tool_list.blockSignals(True)
            self.tool_list.clear()
            for tool in self._tools:
                item = QtWidgets.QListWidgetItem(
                    str(tool.get("name") or tool.get("id") or "未命名"))
                tip = str(tool.get("summary") or tool.get("description") or "")
                item.setToolTip(tip)
                item.setData(QtCore.Qt.UserRole, str(tool.get("id") or ""))
                self.tool_list.addItem(item)
            self.tool_list.blockSignals(False)

            try:
                names = {c["id"]: c["name"] for c in self._categories}
            except Exception:
                names = {}
            self.detail_panel.set_category_names(names)

            if self._tools:
                self.tool_list.setCurrentRow(0)
                # setCurrentRow 在信号未屏蔽时会自己触发 _on_tool_selected，
                # 以前这里又显式调一次，等于每次切分类把详情重建两遍。
                first_id = str(self._tools[0].get("id") or "")
                if self._current_tool_id != first_id:
                    self._on_tool_selected(0)
            else:
                self._current_tool_id = ""
                self.detail_panel.set_tool(None)
                self.log_console.append("当前分类下没有可用工具。", "warning")
        finally:
            self.tool_list.setUpdatesEnabled(True)
            self.detail_panel.setUpdatesEnabled(True)

    def select_tool(self, tool_id: str) -> bool:
        """选中指定工具；不在当前分类时先切回「全部」。"""
        target = str(tool_id or "")
        for row, tool in enumerate(self._tools):
            if str(tool.get("id")) == target:
                self.tool_list.setCurrentRow(row)
                return True
        # 目标工具不在当前分类下：切到「全部」再找，保证外部调用（菜单、启动器）
        # 传 tool_id 时一定能定位到，而不是静默失败。
        if self._categories:
            self.category_list.setCurrentRow(0)
            self._current_category = "all"
            self._refresh_tool_list()
            for row, tool in enumerate(self._tools):
                if str(tool.get("id")) == target:
                    self.tool_list.setCurrentRow(row)
                    return True
        return False

    def _on_tool_selected(self, row: int) -> None:
        if not HAS_QT or row < 0 or row >= len(self._tools):
            return
        tool = self._tools[row]
        self._current_tool_id = str(tool.get("id") or "")
        self.detail_panel.set_tool(tool)
        self.status_bar.set_status("已选择 %s" % self._current_tool_id)

    # ------------------------------------------------------------ 打开工具界面
    def open_tool(self, tool_id: Optional[str] = None) -> Any:
        """打开该工具自己的界面；没有自带界面时回退到通用运行。

        这是「每个工具有一个单独 UI」的落地点：面板不再把所有工具渲染成
        同一张结果表，而是把控制权交回插件自己的 ``ui_qt.show()``。
        """
        if not HAS_QT:
            return None
        target = str(tool_id or self._current_tool_id or "")
        if not target:
            self.log_console.append("没有选中任何工具。", "warning")
            return None
        from .launcher import last_open_error, open_tool as _open_tool
        self.detail_panel.set_status("正在打开 %s ..." % target)
        window = _open_tool(target, dcc=self.dcc)
        error = last_open_error(target)
        if error:
            # 界面加载/构造失败：如实报错，绝不偷偷跑一遍体检冒充工具界面。
            # Qt 槽里的异常不会自己冒到界面上，必须显式写进详情区与日志台。
            self.detail_panel.set_status("打不开 %s 的界面：%s" % (target, error),
                                         "error")
            self.log_console.append("工具 %s 界面打开失败：%s" % (target, error),
                                    "error")
            self.status_bar.set_status("界面打开失败：%s" % target)
            self._switch_tab(0)
        elif window is None:
            self.detail_panel.set_status("该工具没有可用界面，已尝试通用运行。", "warning")
            self.log_console.append("工具 %s 没有独立界面，已回退通用运行。" % target,
                                    "warning")
        elif hasattr(window, "windowTitle"):
            self.detail_panel.set_status("已打开 %s" % target)
            self.log_console.append("打开工具界面：%s" % target, "info")
        else:
            # 回退分支返回 ToolResult，不是窗口；说清楚发生了什么，别让用户
            # 以为自己打开的是这个工具的专用界面。
            note = "该工具没有独立界面，已按通用契约运行并另开结果报告。"
            self.detail_panel.set_status(note, "warning")
            self.log_console.append("%s：%s" % (target, note), "warning")
        return window

    def open_settings(self) -> Any:
        """打开设置面板（分类管理 / 第三方工具导入 / 配置路径）。"""
        if not HAS_QT:
            return None
        from .launcher import open_settings as _open_settings
        return _open_settings(self.dcc, on_saved=self.reload_tools)

    # ------------------------------------------------------------ 运行
    def _on_run_requested(self, tool_id: str) -> None:
        self.run_tool(tool_id)

    def run_tool(self, tool_id: Optional[str] = None) -> Any:
        """运行插件并把 Report / 耗时 / 异常分别送到表格、状态栏、日志台。"""
        if not HAS_QT:
            return None
        target = str(tool_id or self._current_tool_id or "")
        if not target:
            self.log_console.append("没有选中任何工具。", "warning")
            self.status_bar.set_status("未选择工具")
            return None
        self.status_bar.set_status("正在运行 %s ..." % target)
        self.status_bar.set_elapsed(None)
        self.status_bar.busy(True)
        self.log_console.append("开始运行 %s（dcc=%s）" % (target, self.dcc), "info")
        try:
            result = self._runner.run_tool(target, dcc=self.dcc)
        except Exception as exc:
            self.status_bar.busy(False)
            self.status_bar.set_status("运行失败：%s" % target)
            self.log_console.append("运行 %s 失败: %s: %s"
                                    % (target, type(exc).__name__, exc), "error")
            self._switch_tab(2)
            return None
        finally:
            self.status_bar.busy(False)

        report = getattr(result, "report", None)
        self.result_table.set_title("%s · 运行结果" % target)
        self.result_table.set_report(report)
        counts = getattr(report, "counts", None)
        summary = ""
        if callable(counts):
            try:
                data = counts()
                summary = "blocker %d / error %d / warning %d / info %d" % (
                    data.get("blocker", 0), data.get("error", 0),
                    data.get("warning", 0), data.get("info", 0))
            except Exception:
                summary = ""
        self.status_bar.set_status("完成：%s%s" % (target, (" · " + summary) if summary else ""))
        self.status_bar.set_elapsed(getattr(result, "duration_ms", 0.0))
        self._switch_tab(1)
        self.detail_panel.set_status("上次运行 %s：%s" % (target, summary or "无异常"))
        self.log_console.append("运行 %s 完成，%s" % (target, summary or "无异常"), "info")
        return result


# --------------------------------------------------------------------------- #
# 工厂函数：无 Qt / 无 QApplication 时统一返回 None，调用方无需判断
# --------------------------------------------------------------------------- #
def create_tool_card(title: str = "", description: str = "", on_run: Any = None,
                     parent: Any = None) -> Optional[ToolCard]:
    """创建工具卡片；Qt 不可用时返回 None。"""
    if not _qt_ready():
        return None
    return ToolCard(title=title, description=description, on_run=on_run, parent=parent)


def create_result_table(parent: Any = None) -> Optional[ResultTable]:
    """创建结果表；Qt 不可用时返回 None。"""
    return ResultTable(parent) if _qt_ready() else None


def create_diff_preview(parent: Any = None) -> Optional[DiffPreview]:
    """创建 diff 预览；Qt 不可用时返回 None。"""
    return DiffPreview(parent) if _qt_ready() else None


def create_log_console(parent: Any = None) -> Optional[LogConsole]:
    """创建日志台；Qt 不可用时返回 None。"""
    return LogConsole(parent) if _qt_ready() else None


def create_status_bar(parent: Any = None) -> Optional[StatusBar]:
    """创建状态栏；Qt 不可用时返回 None。"""
    return StatusBar(parent) if _qt_ready() else None


def create_main_window(dcc: str = "maya", parent: Any = None,
                       theme: str = "dark") -> Optional[ToolMainWindow]:
    """创建主窗口；Qt 不可用时返回 None。"""
    if not _qt_ready():
        return None
    return ToolMainWindow(dcc=dcc, parent=parent, theme=theme)
