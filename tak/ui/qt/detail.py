# -*- coding: utf-8 -*-
"""工具详情面板：选中工具后右侧展示「作用 / 功能 / 怎么用 / 输入输出」。

这个组件解决的问题很具体：老面板只有一张卡片写一行 description，
用户点开任何一个工具看到的都是同一张结果表，分不清每个工具到底做什么。
现在面板从 manifest 的自描述字段（``summary`` / ``features`` / ``usage`` /
``inputs`` / ``outputs``）渲染结构化说明，插件作者写清楚，用户就能直接看懂。

约束与 ``components.py`` 一致：颜色只来自 QSS 的属性选择器（``takBadge`` /
``takSection`` / ``takDisplay`` / ``takMuted`` / ``takHint``），Python 里不写色值；
无 Qt 环境可安全 import，实例化为空壳。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from tak.compat.qt import QtCore, QtGui, QtWidgets

HAS_QT = (QtWidgets is not None and QtCore is not None and QtGui is not None)

if HAS_QT:
    _FrameBase = QtWidgets.QFrame
else:  # pragma: no cover - 无 Qt 只要求可实例化
    class _DummyBase(object):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    _FrameBase = _DummyBase

_NO_FRAME = getattr(getattr(QtWidgets, "QFrame", None), "NoFrame", 0) if HAS_QT else 0


def _clear_layout(layout: Any) -> None:
    """递归清空布局并 deleteLater，避免反复切换工具时残留控件。

    **不要** ``child.setParent(None)``：Qt 里把 QWidget 的 parent 置空会把它
    提升成一个顶层窗口。详情区每次切换要回收几十个 QLabel，于是每切一次
    分类就凭空冒出几十个临时窗口（闪一下又被 deleteLater 销毁）—— 这就是
    「切分类卡几秒 + 一堆弹窗自己消失」的来源，在 Maya 这类宿主里尤其慢。
    正确做法是先 hide 再 deleteLater，让控件作为原子控件正常回收。
    """
    if layout is None:
        return
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        child = item.widget()
        sub = item.layout()
        if child is not None:
            try:
                child.hide()
            except Exception:
                pass
            try:
                child.deleteLater()
            except Exception:
                pass
        elif sub is not None:
            _clear_layout(sub)
            try:
                sub.deleteLater()
            except (AttributeError, RuntimeError):
                pass


class ToolDetailPanel(_FrameBase):
    """右侧详情区：工具名 + 分类徽章 + 作用 / 功能 / 用法 / 输入输出 + 底部动作按钮。

    ``on_open(tool_id)`` 打开该工具自己的界面；``on_run(tool_id)`` 走通用运行契约。
    两个回调都由主窗口注入，面板本身不 import runner，保持组件可单独测试。
    """

    def __init__(self, on_open: Optional[Any] = None,
                 on_run: Optional[Any] = None,
                 parent: Optional[Any] = None) -> None:
        super().__init__(parent)
        if not HAS_QT:
            self._tool: Dict[str, Any] = {}
            return
        self._on_open = on_open
        self._on_run = on_run
        self._tool: Dict[str, Any] = {}
        self._category_names: Dict[str, str] = {}
        self.setProperty("takCard", "true")
        self.setObjectName("takToolDetail")

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.scroll = QtWidgets.QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(_NO_FRAME)
        outer.addWidget(self.scroll, 1)

        body = QtWidgets.QWidget()
        self.scroll.setWidget(body)
        self._body = QtWidgets.QVBoxLayout(body)
        self._body.setContentsMargins(20, 18, 20, 12)
        self._body.setSpacing(10)
        self._body.addStretch(1)

        footer = QtWidgets.QWidget(self)
        footer_layout = QtWidgets.QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 0, 20, 16)
        footer_layout.setSpacing(8)
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setProperty("takHint", "true")
        self.status_label.setWordWrap(True)
        self.open_button = QtWidgets.QPushButton("打开工具界面")
        self.open_button.setProperty("takPrimary", "true")
        self.open_button.setMinimumHeight(34)
        self.open_button.setMinimumWidth(132)
        self.open_button.setToolTip("打开这个工具自己的操作窗口（不是体检报告）")
        self.open_button.clicked.connect(self._emit_open)
        self.run_button = QtWidgets.QPushButton("运行出报告")
        self.run_button.setProperty("takGhost", "true")
        self.run_button.setMinimumHeight(34)
        self.run_button.setMinimumWidth(96)
        self.run_button.setToolTip("不打开工具窗口，按通用契约跑一遍并把结论填进「结果」页")
        self.run_button.clicked.connect(self._emit_run)
        footer_layout.addWidget(self.status_label, 1)
        footer_layout.addWidget(self.run_button, 0)
        footer_layout.addWidget(self.open_button, 0)
        outer.addWidget(footer, 0)

        self._show_placeholder()

    # ------------------------------------------------------------ 对外接口
    @property
    def tool(self) -> Dict[str, Any]:
        return dict(self._tool)

    def set_category_names(self, mapping: Dict[str, str]) -> "ToolDetailPanel":
        self._category_names = dict(mapping or {})
        return self

    def set_tool(self, tool: Optional[Dict[str, Any]]) -> "ToolDetailPanel":
        """渲染一个工具；传 None 显示空态。"""
        self._tool = dict(tool or {})
        if not HAS_QT:
            return self
        _clear_layout(self._body)
        if not self._tool:
            self._show_placeholder()
            return self
        self._build_detail(self._tool)
        self._body.addStretch(1)
        self.scroll.verticalScrollBar().setValue(0)
        has_ui = str(self._tool.get("ui") or "qt") != "none"
        self.open_button.setVisible(has_ui)
        self.open_button.setEnabled(has_ui)
        self.run_button.setEnabled(True)
        self.run_button.setProperty("takPrimary", "false" if has_ui else "true")
        self._repolish(self.run_button)
        self.status_label.setText("")
        return self

    def set_status(self, text: str, severity: str = "") -> "ToolDetailPanel":
        if not HAS_QT:
            return self
        self.status_label.setText(str(text or ""))
        self.status_label.setProperty("takSeverity", severity or "")
        self._repolish(self.status_label)
        return self

    # ------------------------------------------------------------ 渲染
    def _repolish(self, widget: Any) -> None:
        try:
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        except Exception:
            pass

    def _label(self, text: str, prop: str = "", wrap: bool = True) -> Any:
        label = QtWidgets.QLabel(str(text or ""))
        if prop:
            label.setProperty(prop, "true")
        label.setWordWrap(wrap)
        label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        return label

    def _badge(self, text: str) -> Any:
        label = QtWidgets.QLabel(str(text or ""))
        label.setProperty("takBadge", "true")
        return label

    def _show_placeholder(self) -> None:
        if not HAS_QT:
            return
        _clear_layout(self._body)
        self._body.addStretch(1)
        title = self._label("未选择工具", "takMuted")
        title.setAlignment(QtCore.Qt.AlignCenter)
        hint = self._label("在左侧选择一个工具，这里会显示它的作用、功能与用法。", "takHint")
        hint.setAlignment(QtCore.Qt.AlignCenter)
        self._body.addWidget(title)
        self._body.addWidget(hint)
        self._body.addStretch(1)
        self.open_button.setVisible(False)
        self.run_button.setVisible(False)

    def _build_detail(self, tool: Dict[str, Any]) -> None:
        self.open_button.setVisible(True)
        self.run_button.setVisible(True)

        # 标题行：名称 + 分类徽章
        title = self._label(str(tool.get("name") or tool.get("id") or ""), "takDisplay")
        title.setWordWrap(True)
        self._body.addWidget(title)

        badges = QtWidgets.QHBoxLayout()
        badges.setSpacing(6)
        for cat in tool.get("categories") or []:
            badges.addWidget(self._badge(self._category_names.get(cat, cat)))
        for perm in tool.get("permissions") or []:
            badges.addWidget(self._badge(str(perm)))
        badges.addStretch(1)
        self._body.addLayout(badges)

        summary = str(tool.get("summary") or "").strip()
        if summary:
            self._body.addWidget(self._label(summary, "takMuted"))

        description = str(tool.get("description") or "").strip()
        if description and description != summary:
            self._body.addWidget(self._label(description, "takHint"))

        self._add_list_section("这个工具做什么", tool.get("features") or [], numbered=False)
        self._add_list_section("怎么用", tool.get("usage") or [], numbered=True)
        self._add_io_section(tool)

        tags = [str(t) for t in (tool.get("tags") or []) if t]
        if tags:
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(6)
            for tag in tags:
                row.addWidget(self._badge("#" + tag))
            row.addStretch(1)
            self._body.addLayout(row)

    def _add_list_section(self, heading: str, items: Sequence[Any],
                          numbered: bool = False) -> None:
        cleaned = [str(i).strip() for i in (items or []) if str(i).strip()]
        if not cleaned:
            return
        self._body.addWidget(self._label(heading, "takSection"))
        for index, text in enumerate(cleaned, 1):
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(8)
            mark = ("%d." % index) if numbered else "·"
            marker = QtWidgets.QLabel(mark)
            marker.setProperty("takHint", "true")
            marker.setFixedWidth(18 if numbered else 10)
            marker.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
            label = self._label(text)
            row.addWidget(marker, 0)
            row.addWidget(label, 1)
            self._body.addLayout(row)

    def _add_io_section(self, tool: Dict[str, Any]) -> None:
        inputs: List[str] = [str(i) for i in (tool.get("inputs") or []) if str(i).strip()]
        outputs: List[str] = [str(i) for i in (tool.get("outputs") or []) if str(i).strip()]
        if not inputs and not outputs:
            return
        self._body.addWidget(self._label("输入 / 输出", "takSection"))
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        if inputs:
            grid.addWidget(self._label("输入", "takHint"), 0, 0)
            grid.addWidget(self._label("、".join(inputs)), 0, 1)
        if outputs:
            grid.addWidget(self._label("输出", "takHint"), 1, 0)
            grid.addWidget(self._label("、".join(outputs)), 1, 1)
        grid.setColumnStretch(1, 1)
        self._body.addLayout(grid)

    # ------------------------------------------------------------ 动作
    def _current_id(self) -> str:
        return str(self._tool.get("id") or "")

    def _emit_open(self) -> None:
        if not HAS_QT or not callable(self._on_open):
            return
        try:
            self._on_open(self._current_id())
        except Exception as exc:
            self.set_status("打开界面失败：%s: %s" % (type(exc).__name__, exc), "error")

    def _emit_run(self) -> None:
        if not HAS_QT or not callable(self._on_run):
            return
        try:
            self._on_run(self._current_id())
        except Exception as exc:
            self.set_status("运行失败：%s: %s" % (type(exc).__name__, exc), "error")
