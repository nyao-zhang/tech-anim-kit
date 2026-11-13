# -*- coding: utf-8 -*-
"""TAK 设置面板：分类管理、工具归类、隐藏工具、主题与配置路径。

菜单里的「设置」和主面板左下角的「设置」按钮都打开这个对话框。它只做三件事：

1. **改分类** —— 增 / 删 / 改名，内置的「通用 / 绑定 / 动画 / 管线 / AI」只是初值；
2. **改归类** —— 每个工具属于哪些分类，直接写文本（分类名或 id 都认，逗号分隔）；
3. **改默认** —— 主题、隐藏工具、配置目录；
4. **导入工具** —— 把第三方 .py 脚本装进面板，可选让 AI 读脚本补全说明
   （见 ``tak.core.importer``；导入只做 ast 只读分析，不执行脚本）。

所有改动落 ``~/.tak/config.json``，由 ``tak.core.config`` 原子写回。
插件目录里的 ``manifest.json`` 不被修改 —— 那是插件作者的事实源，用户的偏好
单独存一份，重装/升级插件不会丢配置。
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from tak.core import config as tak_config
from tak.core import importer, registry
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
        self.tabs.addTab(self._build_import_tab(), "导入工具")
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

    # ------------------------------------------------------------ 导入工具
    def _build_import_tab(self) -> Any:
        """导入第三方脚本：选文件 → 看分析结果 → 可改说明 → 装进面板。

        这一页刻意把「TAK 不会替你执行脚本」写在最上面：导入只做 ast 解析与
        文件复制，脚本真正的运行发生在用户点「快速运行」或「打开工具界面」时。
        """
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        intro = QtWidgets.QLabel(
            "导入 .py 脚本或包含 .py 的目录。导入时只读分析（ast），不会执行脚本；"
            "安装位置 %s，跨项目可用。" % registry.user_plugin_dir())
        intro.setProperty("takHint", "true")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        pick_row = QtWidgets.QHBoxLayout()
        self.import_path = QtWidgets.QLineEdit()
        self.import_path.setPlaceholderText("脚本路径（.py）或目录")
        browse_file = QtWidgets.QPushButton("选脚本")
        browse_dir = QtWidgets.QPushButton("选目录")
        analyse_button = QtWidgets.QPushButton("分析")
        analyse_button.setProperty("takPrimary", "true")
        pick_row.addWidget(self.import_path, 1)
        pick_row.addWidget(browse_file, 0)
        pick_row.addWidget(browse_dir, 0)
        pick_row.addWidget(analyse_button, 0)
        layout.addLayout(pick_row)

        form = QtWidgets.QFormLayout()
        self.import_id = QtWidgets.QLineEdit()
        self.import_name = QtWidgets.QLineEdit()
        self.import_summary = QtWidgets.QLineEdit()
        self.import_category = QtWidgets.QLineEdit()
        self.import_category.setPlaceholderText("general, rigging（逗号分隔）")
        form.addRow("工具 id", self.import_id)
        form.addRow("显示名", self.import_name)
        form.addRow("一句话说明", self.import_summary)
        form.addRow("分类", self.import_category)
        layout.addLayout(form)

        self.import_report = QtWidgets.QPlainTextEdit()
        self.import_report.setReadOnly(True)
        self.import_report.setMinimumHeight(150)
        self.import_report.setPlainText(
            "选好脚本后点「分析」，这里会显示：识别到的宿主、会改场景的函数、"
            "有没有自己的界面、有没有 TAK 运行契约。")
        layout.addWidget(self.import_report, 1)

        action_row = QtWidgets.QHBoxLayout()
        self.import_ai = QtWidgets.QCheckBox("用 AI 补全说明与用法")
        self.import_ai.setChecked(True)
        self.import_overwrite = QtWidgets.QCheckBox("覆盖同名工具")
        install_button = QtWidgets.QPushButton("安装到面板")
        install_button.setProperty("takPrimary", "true")
        action_row.addWidget(self.import_ai, 0)
        action_row.addWidget(self.import_overwrite, 0)
        action_row.addStretch(1)
        action_row.addWidget(install_button, 0)
        layout.addLayout(action_row)

        layout.addWidget(QtWidgets.QLabel("已导入的工具"))
        self.import_list = QtWidgets.QTableWidget(0, 4)
        self.import_list.setHorizontalHeaderLabels(["工具", "来源", "界面/契约", "导入时间"])
        self.import_list.verticalHeader().setVisible(False)
        self.import_list.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectRows)
        self.import_list.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers)
        header = self.import_list.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.import_list, 1)

        list_row = QtWidgets.QHBoxLayout()
        remove_button = QtWidgets.QPushButton("移除选中")
        remove_button.setProperty("takGhost", "true")
        refresh_button = QtWidgets.QPushButton("刷新列表")
        refresh_button.setProperty("takGhost", "true")
        list_row.addStretch(1)
        list_row.addWidget(refresh_button, 0)
        list_row.addWidget(remove_button, 0)
        layout.addLayout(list_row)

        self._import_state: Dict[str, Any] = {"analysis": {}, "manifest": {}}

        def _browse(single_file: bool) -> None:
            if single_file:
                path, _ = QtWidgets.QFileDialog.getOpenFileName(
                    page, "选择脚本", "", "Python 脚本 (*.py)")
            else:
                path = QtWidgets.QFileDialog.getExistingDirectory(
                    page, "选择包含脚本的目录")
            if path:
                self.import_path.setText(path)
                _analyse()

        def _analyse() -> None:
            source = self.import_path.text().strip()
            if not source:
                self.import_report.setPlainText("先选一个脚本或目录。")
                return
            analysis = importer.analyze(source)
            self._import_state["analysis"] = analysis
            if not analysis.get("ok"):
                self.import_report.setPlainText(
                    "分析失败：\n" + "\n".join(analysis.get("errors") or []))
                self._import_state["manifest"] = {}
                return
            manifest = importer.draft_manifest(analysis)
            self._import_state["manifest"] = manifest
            self.import_id.setText(str(manifest.get("id") or ""))
            self.import_name.setText(str(manifest.get("name") or ""))
            self.import_summary.setText(str(manifest.get("summary") or ""))
            self.import_category.setText(", ".join(manifest.get("category") or []))
            self._render_import_report(analysis, manifest)

        def _install() -> None:
            analysis = self._import_state.get("analysis") or {}
            manifest = dict(self._import_state.get("manifest") or {})
            if not analysis.get("ok") or not manifest:
                self.hint.setText("先点「分析」拿到可用信息，再安装。")
                return
            manifest["id"] = self.import_id.text().strip() or manifest.get("id")
            manifest["name"] = self.import_name.text().strip() or manifest.get("name")
            summary = self.import_summary.text().strip()
            if summary:
                manifest["summary"] = summary
            categories = [item.strip() for item in
                          self.import_category.text().replace("，", ",").split(",")
                          if item.strip()]
            if categories:
                manifest["category"] = categories

            ai_fields: List[str] = []
            if self.import_ai.isChecked():
                enriched = importer.enrich(manifest, analysis)
                manifest = enriched.get("manifest") or manifest
                ai_fields = list(enriched.get("fields") or [])
                self.hint.setText(
                    enriched.get("note") or "已跳过 AI 补全"
                    if not enriched.get("ai")
                    else "AI 已补全：%s" % "、".join(ai_fields))

            result = importer.install(
                self.import_path.text().strip(), manifest=manifest,
                analysis=analysis, overwrite=self.import_overwrite.isChecked(),
                ai_fields=ai_fields)
            if result.get("ok"):
                self.hint.setText(
                    "已安装 %s 到 %s；保存后返回主面板即可看到。"
                    % (result["tool_id"], result["dir"]))
                self._load_imports()
                self._notify_tools_changed()
            else:
                self.hint.setText("安装失败：%s" % result.get("error"))

        def _remove() -> None:
            row = self.import_list.currentRow()
            item = self.import_list.item(row, 0) if row >= 0 else None
            if item is None:
                self.hint.setText("先在列表里选一个已导入的工具。")
                return
            tool_id = item.text()
            result = importer.uninstall(tool_id)
            self.hint.setText(
                "已移除 %s" % tool_id if result.get("ok")
                else "移除失败：%s" % result.get("error"))
            self._load_imports()
            self._notify_tools_changed()

        browse_file.clicked.connect(lambda: _browse(True))
        browse_dir.clicked.connect(lambda: _browse(False))
        analyse_button.clicked.connect(_analyse)
        install_button.clicked.connect(_install)
        remove_button.clicked.connect(_remove)
        refresh_button.clicked.connect(self._load_imports)
        self._load_imports()
        return page

    def _render_import_report(self, analysis: Dict[str, Any],
                              manifest: Dict[str, Any]) -> None:
        lines = [
            "宿主：%s" % "、".join(analysis.get("dcc") or []),
            "入口：%s" % os.path.basename(str(analysis.get("entry") or "")),
            "自带界面 show()：%s" % ("是" if analysis.get("has_show") else "否"),
            "TAK 运行契约：%s" % ("有（可进结果表）"
                                if analysis.get("has_report_contract")
                                else "没有（只能原样执行，不会产判定）"),
            "推断权限：%s" % "、".join(manifest.get("permissions") or []),
            "会调用的宿主函数：%s" % ("、".join(
                (analysis.get("host_calls") or [])[:12]) or "（未识别）"),
            "",
            "识别到的功能（来自 docstring / 函数注释）：",
        ]
        lines.extend("  · " + item for item in (manifest.get("features") or ["（无）"]))
        if analysis.get("warnings"):
            lines.append("")
            lines.append("注意：")
            lines.extend("  · " + item for item in analysis["warnings"])
        if analysis.get("errors"):
            lines.append("")
            lines.append("解析错误：")
            lines.extend("  · " + item for item in analysis["errors"])
        self.import_report.setPlainText("\n".join(lines))

    def _notify_tools_changed(self) -> None:
        """导入/移除后通知主面板刷新；保存前也生效，不必等用户点保存。"""
        if callable(self._on_saved):
            try:
                self._on_saved()
            except Exception:
                pass
        self._load_tools()

    def _load_imports(self) -> None:
        items = importer.list_imported()
        self.import_list.setRowCount(len(items))
        for row, item in enumerate(items):
            flags = []
            flags.append("界面" if item.get("has_show") else "无界面")
            flags.append("契约" if item.get("has_report_contract") else "无契约")
            cells = [item.get("tool_id", ""), item.get("source", ""),
                     " / ".join(flags), item.get("imported_at", "")]
            for column, text in enumerate(cells):
                self.import_list.setItem(row, column,
                                         QtWidgets.QTableWidgetItem(str(text)))

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
