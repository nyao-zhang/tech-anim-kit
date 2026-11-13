# -*- coding: utf-8 -*-
"""TAK 设置面板：分类管理、工具归类、隐藏工具、主题与配置路径。

菜单里的「设置」和主面板左下角的「设置」按钮都打开这个对话框。它只做三件事：

1. **改分类** —— 增 / 删 / 改名，内置的「通用 / 绑定 / 动画 / 管线 / AI」只是初值；
2. **改归类** —— 每个工具属于哪些分类，直接写文本（分类名或 id 都认，逗号分隔）；
3. **改默认** —— 主题、隐藏工具、配置目录。

所有改动落 ``~/.tak/config.json``，由 ``tak.core.config`` 原子写回。
插件目录里的 ``manifest.json`` 不被修改 —— 那是插件作者的事实源，用户的偏好
单独存一份，重装/升级插件不会丢配置。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from tak.core import config as tak_config
from tak.compat.qt import QtCore, QtWidgets

from .components import _theme_path
from tak.compat.qt import apply_theme

HAS_QT = (QtWidgets is not None and QtCore is not None)

if HAS_QT:
    _DialogBase = QtWidgets.QDialog
else:  # pragma: no cover - 无 Qt 只要求可实例化
    class _DialogBase(object):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    _DialogBase = _DialogBase

_CHECKED = QtCore.Qt.Checked if HAS_QT else None
_UNCHECKED = QtCore.Qt.Unchecked if HAS_QT else None
_SPLIT_RE = re.compile(r"[,，、/\s]+")


class SettingsDialog(_DialogBase):
    """设置对话框。``on_saved`` 在保存成功后调用（主面板用它刷新工具列表）。"""

    def __init__(self, dcc: str = "maya", parent: Any = None,
                 on_saved: Optional[Any] = None) -> None:
        super().__init__(parent)
        if not HAS_QT:
            return
        self.dcc = str(dcc or "maya")
        self._on_saved = on_saved
        self._cfg = tak_config.load()
        self._categories: List[Dict[str, str]] = [
            dict(c) for c in (self._cfg.get("categories") or [])]
        self._tool_ids: List[str] = []

        self.setObjectName("takSettings")
        self.setWindowTitle("TAK 设置 · %s" % self.dcc)
        self.resize(780, 580)
        self._build_ui()
        self._load_tools()
        self._refresh_categories()
        apply_theme(self, _theme_path(str(self._cfg.get("theme") or "dark")))

    # ------------------------------------------------------------ 构建
    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(10)

        title = QtWidgets.QLabel("设置")
        font = title.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 4)
        title.setFont(font)
        subtitle = QtWidgets.QLabel(
            "配置写入 %s。插件 manifest 不会被改动，升级插件不会覆盖你的偏好。"
            % tak_config.config_path())
        subtitle.setProperty("takHint", "true")
        subtitle.setWordWrap(True)
        outer.addWidget(title)
        outer.addWidget(subtitle)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_category_tab(), "分类")
        self.tabs.addTab(self._build_tool_tab(), "工具归类")
        self.tabs.addTab(self._build_general_tab(), "常规")
        outer.addWidget(self.tabs, 1)

        footer = QtWidgets.QHBoxLayout()
        footer.setSpacing(8)
        self.hint = QtWidgets.QLabel("")
        self.hint.setProperty("takHint", "true")
        self.hint.setWordWrap(True)
        save_button = QtWidgets.QPushButton("保存")
        save_button.setProperty("takPrimary", "true")
        save_button.setMinimumHeight(32)
        save_button.clicked.connect(self.save)
        cancel_button = QtWidgets.QPushButton("关闭")
        cancel_button.setProperty("takGhost", "true")
        cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.hint, 1)
        footer.addWidget(cancel_button, 0)
        footer.addWidget(save_button, 0)
        outer.addLayout(footer)

    def _build_category_tab(self) -> Any:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.category_list = QtWidgets.QListWidget()
        self.category_list.setObjectName("takCategoryList")
        layout.addWidget(self.category_list, 1)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        for label, slot in (("新增", self._add_category),
                            ("重命名", self._rename_category),
                            ("删除", self._remove_category)):
            button = QtWidgets.QPushButton(label)
            button.setProperty("takGhost", "true")
            button.clicked.connect(slot)
            row.addWidget(button, 0)
        row.addStretch(1)
        layout.addLayout(row)

        tip = QtWidgets.QLabel(
            "「全部」是内置视图，不在这里维护。分类删掉后，原本归在该分类下的工具"
            "会自动回落到「通用」，不会从列表里消失。")
        tip.setProperty("takHint", "true")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        return page

    def _build_tool_tab(self) -> Any:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.tool_table = QtWidgets.QTableWidget(0, 3)
        self.tool_table.setHorizontalHeaderLabels(["工具", "分类（逗号分隔）", "隐藏"])
        self.tool_table.verticalHeader().setVisible(False)
        self.tool_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectRows)
        header = self.tool_table.horizontalHeader()
        try:
            header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
            header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        except Exception:
            pass
        layout.addWidget(self.tool_table, 1)

        tip = QtWidgets.QLabel(
            "分类列可以填中文名或 id（例如「绑定」或 rigging），多个用逗号分隔。"
            "留空表示按插件 manifest 的默认归类。")
        tip.setProperty("takHint", "true")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        return page

    def _build_general_tab(self) -> Any:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        theme_row = QtWidgets.QHBoxLayout()
        theme_row.addWidget(QtWidgets.QLabel("主题"), 0)
        self.theme_combo = QtWidgets.QComboBox()
        self.theme_combo.addItems(["深色", "浅色"])
        self.theme_combo.setCurrentIndex(
            1 if str(self._cfg.get("theme") or "dark") == "light" else 0)
        theme_row.addWidget(self.theme_combo, 0)
        theme_row.addStretch(1)
        layout.addLayout(theme_row)

        path_row = QtWidgets.QHBoxLayout()
        path_label = QtWidgets.QLabel(tak_config.config_path())
        path_label.setProperty("takHint", "true")
        path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        open_button = QtWidgets.QPushButton("打开配置目录")
        open_button.setProperty("takGhost", "true")
        open_button.clicked.connect(tak_config.open_in_explorer)
        path_row.addWidget(path_label, 1)
        path_row.addWidget(open_button, 0)
        layout.addLayout(path_row)

        reset_button = QtWidgets.QPushButton("恢复默认设置")
        reset_button.setProperty("takGhost", "true")
        reset_button.clicked.connect(self._reset)
        layout.addWidget(reset_button, 0)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------ 数据
    def _load_tools(self) -> None:
        from tak.core.runner import list_tools
        try:
            # 设置面板要看到**全部**插件，包括 manifest 标了 internal 的规则提供者：
            # 它们虽然不在工具列表里，但用户可能想给它们归类或显式隐藏。
            tools = list_tools(dcc=self.dcc, include_hidden=True,
                               include_internal=True)
        except Exception:
            tools = []
        cfg = self._cfg
        self.tool_table.setRowCount(len(tools))
        self._tool_ids = []
        for row, tool in enumerate(tools):
            tid = str(tool.get("id") or "")
            self._tool_ids.append(tid)
            name_item = QtWidgets.QTableWidgetItem(str(tool.get("name") or tid))
            name_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            name_item.setData(QtCore.Qt.UserRole, tid)
            self.tool_table.setItem(row, 0, name_item)

            cats = tak_config.resolve_categories(
                tool.get("category") or [], tid, cfg)
            self.tool_table.setItem(row, 1, QtWidgets.QTableWidgetItem(
                "、".join(self._name_of(c, cfg) for c in cats)))

            check = QtWidgets.QTableWidgetItem("")
            check.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
            check.setCheckState(_CHECKED if tid in (cfg.get("hidden_tools") or [])
                                else _UNCHECKED)
            self.tool_table.setItem(row, 2, check)

    def _name_of(self, cat_id: str, cfg: Optional[Dict[str, Any]] = None) -> str:
        names = tak_config.category_names(cfg or self._cfg)
        return names.get(cat_id, cat_id)

    def _refresh_categories(self) -> None:
        self.category_list.clear()
        for cat in self._categories:
            item = QtWidgets.QListWidgetItem(str(cat.get("name") or cat.get("id")))
            item.setData(QtCore.Qt.UserRole, str(cat.get("id") or ""))
            item.setToolTip(str(cat.get("description") or ""))
            self.category_list.addItem(item)

    # ------------------------------------------------------------ 分类操作
    def _add_category(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "新增分类", "分类名：")
        if not ok or not str(name).strip():
            return
        name = str(name).strip()
        cat_id = self._unique_id(name)
        self._categories.append({"id": cat_id, "name": name, "description": ""})
        self._refresh_categories()
        self.hint.setText("已新增分类「%s」（id %s），记得点保存。" % (name, cat_id))

    def _unique_id(self, name: str) -> str:
        """由中文名生成一个合法 id；冲突时加数字后缀。"""
        base = re.sub(r"[^0-9A-Za-z_]", "", name) or "cat"
        if base[0].isdigit():
            base = "c" + base
        existing = {c["id"] for c in self._categories}
        candidate = base
        index = 2
        while candidate in existing:
            candidate = "%s%d" % (base, index)
            index += 1
        return candidate

    def _rename_category(self) -> None:
        row = self.category_list.currentRow()
        if row < 0 or row >= len(self._categories):
            return
        current = str(self._categories[row].get("name") or "")
        name, ok = QtWidgets.QInputDialog.getText(
            self, "重命名分类", "新名称：", text=current)
        if not ok or not str(name).strip():
            return
        self._categories[row]["name"] = str(name).strip()
        self._refresh_categories()
        self.category_list.setCurrentRow(row)

    def _remove_category(self) -> None:
        row = self.category_list.currentRow()
        if row < 0 or row >= len(self._categories):
            return
        cat = self._categories[row]
        answer = QtWidgets.QMessageBox.question(
            self, "删除分类",
            "删除分类「%s」？归在该分类下的工具会回落到「通用」。"
            % cat.get("name"),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self._categories.pop(row)
        self._refresh_categories()

    # ------------------------------------------------------------ 保存
    def _parse_cats(self, text: str) -> List[str]:
        """把「通用、绑定」这类输入解析成分类 id 列表。"""
        name_to_id = {c["name"]: c["id"] for c in self._categories}
        valid = {c["id"] for c in self._categories}
        out: List[str] = []
        for token in _SPLIT_RE.split(str(text or "")):
            token = token.strip()
            if not token:
                continue
            if token in valid:
                out.append(token)
            elif token in name_to_id:
                out.append(name_to_id[token])
        # 去重但保序
        return list(dict.fromkeys(out))

    def save(self) -> bool:
        if not HAS_QT:
            return False
        cfg = tak_config.load()
        cfg["categories"] = [dict(c) for c in self._categories]
        mapping: Dict[str, List[str]] = {}
        hidden: List[str] = []
        for row, tid in enumerate(self._tool_ids):
            item = self.tool_table.item(row, 1)
            cats = self._parse_cats(item.text() if item is not None else "")
            if cats:
                mapping[tid] = cats
            flag = self.tool_table.item(row, 2)
            if flag is not None and flag.checkState() == _CHECKED:
                hidden.append(tid)
        cfg["tool_categories"] = mapping
        cfg["hidden_tools"] = sorted(hidden)
        cfg["theme"] = "light" if self.theme_combo.currentIndex() == 1 else "dark"
        result = tak_config.save(cfg)
        if not result.get("ok"):
            self.hint.setText("保存失败：%s" % result.get("reason"))
            return False
        self._cfg = cfg
        self.hint.setText("已保存到 %s" % result.get("path"))
        if callable(self._on_saved):
            try:
                self._on_saved()
            except Exception:
                pass
        return True

    def _reset(self) -> None:
        answer = QtWidgets.QMessageBox.question(
            self, "恢复默认", "把所有分类、归类、隐藏、主题重置为默认值？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        tak_config.reset()
        self._cfg = tak_config.load()
        self._categories = [dict(c) for c in (self._cfg.get("categories") or [])]
        self._refresh_categories()
        self._load_tools()
        self.theme_combo.setCurrentIndex(0)
        self.hint.setText("已恢复默认设置。")
        if callable(self._on_saved):
            try:
                self._on_saved()
            except Exception:
                pass
