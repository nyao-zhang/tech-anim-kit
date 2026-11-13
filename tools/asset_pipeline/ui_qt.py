# -*- coding: utf-8 -*-
"""导出 / 导入的独立界面。

上半部分是「导什么、导到哪」的表单，下半部分是计划预览与结果表。
表单状态可以存成命名 profile，导出端和导入端读同一份 —— 跨 DCC 交付时
不需要再口头同步「上次是怎么导的」。

无 Qt / 无 QApplication 时 ``show()`` 返回 None。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

TOOL_ID = "asset_pipeline"


def _core() -> Any:
    from tak.core import runner
    return runner.load_tool_modules(runner.find_plugin(TOOL_ID))


def show(dcc: str = "maya", omui: Any = None) -> Optional[Any]:
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
        return _build(str(dcc or "maya"))
    except Exception as exc:
        print("[TAK][Pipeline] 界面创建失败: %s: %s" % (type(exc).__name__, exc))
        return None


def _build(dcc: str) -> Any:
    from tak.compat.qt import QtCore, QtWidgets, apply_theme
    from tak.ui.qt import LogConsole, ResultTable, _theme_path
    from tak.ui.qt.launcher import host_main_window

    core = _core()
    parent = host_main_window(dcc)
    window = QtWidgets.QMainWindow(parent)
    window.setObjectName("takAssetPipeline")
    window.setWindowTitle("导出 / 导入 · %s" % dcc)
    window.resize(1060, 760)

    central = QtWidgets.QWidget(window)
    outer = QtWidgets.QVBoxLayout(central)
    outer.setContentsMargins(16, 16, 16, 12)
    outer.setSpacing(10)

    title = QtWidgets.QLabel("导出 / 导入")
    font = title.font()
    font.setBold(True)
    font.setPointSize(font.pointSize() + 5)
    title.setFont(font)
    subtitle = QtWidgets.QLabel(
        "先定内容与目标，再预检，最后导出。缓存位用于跨 DCC 中转：在这里导出，在另一个 DCC 里导入校验。")
    subtitle.setProperty("takMuted", "true")
    subtitle.setWordWrap(True)
    outer.addWidget(title)
    outer.addWidget(subtitle)

    # ---------------- 表单 ----------------
    form_box = QtWidgets.QFrame()
    form_box.setProperty("takCard", "true")
    form = QtWidgets.QGridLayout(form_box)
    form.setContentsMargins(14, 12, 14, 12)
    form.setHorizontalSpacing(10)
    form.setVerticalSpacing(8)

    preset_combo = QtWidgets.QComboBox()
    for item in core.PRESETS:
        preset_combo.addItem(item["name"], item["id"])
    target_combo = QtWidgets.QComboBox()
    for item in core.TARGETS:
        target_combo.addItem(item["name"], item["id"])
    format_combo = QtWidgets.QComboBox()

    form.addWidget(QtWidgets.QLabel("预设"), 0, 0)
    form.addWidget(preset_combo, 0, 1)
    form.addWidget(QtWidgets.QLabel("目标"), 0, 2)
    form.addWidget(target_combo, 0, 3)
    form.addWidget(QtWidgets.QLabel("格式"), 0, 4)
    form.addWidget(format_combo, 0, 5)

    content_box = QtWidgets.QWidget()
    content_layout = QtWidgets.QHBoxLayout(content_box)
    content_layout.setContentsMargins(0, 0, 0, 0)
    content_layout.setSpacing(12)
    content_checks: Dict[str, Any] = {}
    for item in core.CONTENT_ITEMS:
        box = QtWidgets.QCheckBox(item["name"])
        box.setToolTip(item["description"])
        content_checks[item["id"]] = box
        content_layout.addWidget(box)
    content_layout.addStretch(1)
    form.addWidget(QtWidgets.QLabel("内容"), 1, 0)
    form.addWidget(content_box, 1, 1, 1, 5)

    path_edit = QtWidgets.QLineEdit()
    path_edit.setPlaceholderText("导出文件路径，留空则用缓存位")
    browse_button = QtWidgets.QPushButton("浏览…")
    browse_button.setProperty("takGhost", "true")
    cache_box = QtWidgets.QCheckBox("导出到缓存位")
    cache_box.setToolTip("缓存位：%s" % core.cache_dir())
    path_row = QtWidgets.QHBoxLayout()
    path_row.setSpacing(8)
    path_row.addWidget(path_edit, 1)
    path_row.addWidget(browse_button, 0)
    path_row.addWidget(cache_box, 0)
    path_widget = QtWidgets.QWidget()
    path_widget.setLayout(path_row)
    form.addWidget(QtWidgets.QLabel("路径"), 2, 0)
    form.addWidget(path_widget, 2, 1, 1, 5)

    profile_combo = QtWidgets.QComboBox()
    profile_combo.setMinimumWidth(180)
    save_profile_button = QtWidgets.QPushButton("存为 profile")
    save_profile_button.setProperty("takGhost", "true")
    load_profile_button = QtWidgets.QPushButton("载入")
    load_profile_button.setProperty("takGhost", "true")
    delete_profile_button = QtWidgets.QPushButton("删除")
    delete_profile_button.setProperty("takGhost", "true")
    profile_row = QtWidgets.QHBoxLayout()
    profile_row.setSpacing(8)
    profile_row.addWidget(profile_combo, 1)
    profile_row.addWidget(load_profile_button, 0)
    profile_row.addWidget(save_profile_button, 0)
    profile_row.addWidget(delete_profile_button, 0)
    profile_widget = QtWidgets.QWidget()
    profile_widget.setLayout(profile_row)
    form.addWidget(QtWidgets.QLabel("Profile"), 3, 0)
    form.addWidget(profile_widget, 3, 1, 1, 5)

    outer.addWidget(form_box, 0)

    # ---------------- 计划预览 + 结果 ----------------
    preview = QtWidgets.QPlainTextEdit()
    preview.setReadOnly(True)
    preview.setMaximumHeight(96)
    preview.setPlaceholderText("计划预览：选好内容与目标后点「预检」。")
    outer.addWidget(preview, 0)

    action_row = QtWidgets.QHBoxLayout()
    action_row.setSpacing(8)
    status = QtWidgets.QLabel("就绪")
    status.setProperty("takHint", "true")
    status.setWordWrap(True)
    preflight_button = QtWidgets.QPushButton("预检")
    preflight_button.setProperty("takGhost", "true")
    export_button = QtWidgets.QPushButton("导出")
    export_button.setProperty("takPrimary", "true")
    export_button.setMinimumHeight(34)
    export_button.setMinimumWidth(96)
    import_button = QtWidgets.QPushButton("导入校验")
    import_button.setProperty("takGhost", "true")
    action_row.addWidget(status, 1)
    action_row.addWidget(preflight_button, 0)
    action_row.addWidget(import_button, 0)
    action_row.addWidget(export_button, 0)
    outer.addLayout(action_row)

    table = ResultTable(central)
    log = LogConsole(central)
    log.setMaximumHeight(88)
    outer.addWidget(table, 1)
    outer.addWidget(log, 0)

    window.setCentralWidget(central)
    apply_theme(window, _theme_path("dark"))

    # ---------------- 状态 ----------------
    def current_profile() -> Dict[str, Any]:
        target_id = str(target_combo.currentData() or "unreal")
        preset_id = str(preset_combo.currentData() or "full_rig")
        return core.normalize_profile({
            "preset": preset_id,
            "content": [cid for cid, box in content_checks.items() if box.isChecked()],
            "target": target_id,
            "format": str(format_combo.currentData() or ""),
            "path": path_edit.text().strip(),
            "use_cache_dir": cache_box.isChecked(),
        })

    def refresh_formats() -> None:
        target_id = str(target_combo.currentData() or "unreal")
        spec = next((t for t in core.TARGETS if t["id"] == target_id), core.TARGETS[0])
        current = format_combo.currentData()
        format_combo.blockSignals(True)
        format_combo.clear()
        for fmt in spec["formats"]:
            format_combo.addItem(fmt.upper(), fmt)
        index = format_combo.findData(current)
        format_combo.setCurrentIndex(index if index >= 0 else 0)
        format_combo.blockSignals(False)

    def apply_preset() -> None:
        preset_id = str(preset_combo.currentData() or "")
        preset = next((p for p in core.PRESETS if p["id"] == preset_id), None)
        if not preset:
            return
        for cid, box in content_checks.items():
            box.setChecked(cid in preset["content"])

    def apply_profile(profile: Dict[str, Any]) -> None:
        index = target_combo.findData(profile.get("target"))
        if index >= 0:
            target_combo.setCurrentIndex(index)
        refresh_formats()
        fmt_index = format_combo.findData(profile.get("format"))
        if fmt_index >= 0:
            format_combo.setCurrentIndex(fmt_index)
        for cid, box in content_checks.items():
            box.setChecked(cid in (profile.get("content") or []))
        path_edit.setText(str(profile.get("path") or ""))
        cache_box.setChecked(bool(profile.get("use_cache_dir")))
        preset_index = preset_combo.findData(profile.get("preset"))
        if preset_index >= 0:
            preset_combo.blockSignals(True)
            preset_combo.setCurrentIndex(preset_index)
            preset_combo.blockSignals(False)

    def refresh_profiles() -> None:
        current = profile_combo.currentText()
        profile_combo.clear()
        for name in core.tak_config.export_profiles():
            profile_combo.addItem(name, name)
        index = profile_combo.findText(current)
        if index >= 0:
            profile_combo.setCurrentIndex(index)

    def snapshot() -> Any:
        try:
            from tak.core.runner import adapter_for
            adapter = adapter_for(dcc)
            return adapter, adapter.get_skeleton_snapshot()
        except Exception:
            return None, {}

    def run_preflight() -> Any:
        profile = current_profile()
        adapter, snap = snapshot()
        plan = core.build_plan(snap or {}, profile, profile.get("path") or "asset")
        preview.setPlainText("\n".join(core.describe_plan(plan)))
        from tak.core.runner import run_tool
        result = run_tool(TOOL_ID, snap, dcc=dcc, adapter=adapter,
                          options={"profile": profile})
        table.set_report(getattr(result, "report", None))
        counts = getattr(result.report, "counts", None)
        stats = counts() if callable(counts) else {}
        status.setText("预检完成：blocker %d / error %d / warning %d / info %d"
                       % (stats.get("blocker", 0), stats.get("error", 0),
                          stats.get("warning", 0), stats.get("info", 0)))
        return result

    def on_preflight() -> None:
        try:
            run_preflight()
        except Exception as exc:
            status.setText("预检失败：%s: %s" % (type(exc).__name__, exc))
            log.error("预检失败：%s: %s" % (type(exc).__name__, exc))

    def on_export() -> None:
        profile = current_profile()
        adapter, snap = snapshot()
        plan = core.build_plan(snap or {}, profile, profile.get("path") or "asset")
        preview.setPlainText("\n".join(core.describe_plan(plan)))
        from types import SimpleNamespace
        probe = SimpleNamespace(snapshot=snap or {}, dcc=dcc, spec={})
        blockers = [f for f in core.preflight(probe, profile)
                    if str(getattr(f.severity, "value", f.severity)) in ("blocker", "error")]
        if blockers:
            status.setText("预检未通过（%d 个 error/blocker），已取消导出。" % len(blockers))
            log.error("导出已取消：存在 %d 个阻断项" % len(blockers))
            table.set_findings(blockers)
            return
        result = core.execute(plan, adapter)
        if result.get("ok"):
            status.setText("已导出：%s" % result.get("path"))
            log.append("导出成功：%s" % result.get("path"), "info")
        else:
            status.setText("导出失败：%s" % result.get("reason"))
            log.error("导出失败：%s" % result.get("reason"))

    def on_import() -> None:
        path = path_edit.text().strip()
        if not path:
            status.setText("请先在路径里选择要校验的导出文件 / sidecar。")
            return
        adapter, snap = snapshot()
        result = core.import_check(path, snap or {}, current_profile())
        status.setText("导入校验：%s" % ("通过" if result.get("ok") else "存在问题"))
        log.append("导入校验完成：%s" % path, "info")
        findings = result.get("findings") or []
        table.set_findings(findings)

    def on_browse() -> None:
        start = path_edit.text().strip() or core.cache_dir()
        fmt = str(format_combo.currentData() or "fbx")
        chosen, _filter = QtWidgets.QFileDialog.getSaveFileName(
            window, "导出到", start, "%s (*.%s);;所有文件 (*)" % (fmt.upper(), fmt))
        if chosen:
            path_edit.setText(chosen)
            cache_box.setChecked(False)

    def on_save_profile() -> None:
        name, ok = QtWidgets.QInputDialog.getText(window, "保存 profile", "名称：")
        if not ok or not str(name).strip():
            return
        result = core.save_profile(str(name).strip(), current_profile())
        if result.get("ok"):
            refresh_profiles()
            status.setText("已保存 profile：%s" % name)
        else:
            status.setText("保存失败：%s" % result.get("reason"))

    def on_load_profile() -> None:
        name = str(profile_combo.currentData() or "")
        if not name:
            return
        profile = core.load_profile(name)
        if profile:
            apply_profile(profile)
            status.setText("已载入 profile：%s" % name)

    def on_delete_profile() -> None:
        name = str(profile_combo.currentData() or "")
        if not name:
            return
        core.delete_profile(name)
        refresh_profiles()
        status.setText("已删除 profile：%s" % name)

    target_combo.currentIndexChanged.connect(lambda _i: refresh_formats())
    preset_combo.currentIndexChanged.connect(lambda _i: apply_preset())
    browse_button.clicked.connect(on_browse)
    preflight_button.clicked.connect(on_preflight)
    export_button.clicked.connect(on_export)
    import_button.clicked.connect(on_import)
    save_profile_button.clicked.connect(on_save_profile)
    load_profile_button.clicked.connect(on_load_profile)
    delete_profile_button.clicked.connect(on_delete_profile)

    refresh_formats()
    refresh_profiles()
    apply_profile(core.default_profile(str(target_combo.currentData() or "unreal")))
    window.show()
    return window
