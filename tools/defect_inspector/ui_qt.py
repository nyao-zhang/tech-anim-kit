# -*- coding: utf-8 -*-
"""VLM 缺陷检查的独立界面。

这个工具最容易被误解成「AI 自动修模型」。界面第一屏就把边界说清楚：

* 输入是**抽帧图片序列**（Playblast / 录屏按固定间隔导出）+ 同一时刻的场景快照；
* 输出是**候选清单**：帧号、类别、疑似骨骼、置信度；
* 它是**人工复核的起点**，不是判定，也不写场景。

无 Qt / 无 QApplication 时 ``show()`` 返回 None。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

TOOL_ID = "defect_inspector"
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


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
        print("[TAK][VLM] 界面创建失败: %s: %s" % (type(exc).__name__, exc))
        return None


def _frames_from_dir(folder: str) -> List[Dict[str, Any]]:
    """把图片目录整理成帧列表：帧号从文件名里的最后一串数字取。"""
    names = sorted(name for name in os.listdir(folder)
                   if os.path.splitext(name)[1].lower() in _IMAGE_EXT)
    frames: List[Dict[str, Any]] = []
    for index, name in enumerate(names, 1):
        numbers = re.findall(r"\d+", os.path.splitext(name)[0])
        frame = int(numbers[-1]) if numbers else index
        frames.append({"frame": frame, "image": os.path.join(folder, name),
                       "bones": [], "note": ""})
    return frames


def _frames_from_json(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        data = data.get("frames") or []
    return [item for item in data if isinstance(item, dict)]


def _build(dcc: str) -> Any:
    from tak.compat.qt import QtCore, QtWidgets, apply_theme
    from tak.ui.qt import LogConsole, ResultTable, _theme_path
    from tak.ui.qt.launcher import host_main_window

    core = _core()
    parent = host_main_window(dcc)
    window = QtWidgets.QMainWindow(parent)
    window.setObjectName("takDefectInspector")
    window.setWindowTitle("VLM 缺陷检查 · %s" % dcc)
    window.resize(1020, 720)

    central = QtWidgets.QWidget(window)
    outer = QtWidgets.QVBoxLayout(central)
    outer.setContentsMargins(16, 16, 16, 12)
    outer.setSpacing(10)

    title = QtWidgets.QLabel("VLM 缺陷检查")
    font = title.font()
    font.setBold(True)
    font.setPointSize(font.pointSize() + 5)
    title.setFont(font)
    what = QtWidgets.QLabel(
        "输入抽帧图片序列，输出「第几帧、什么类别、大概哪根骨骼、置信度多少」的候选清单。"
        "这是人工复核的起点：它不判定对错，也不改场景。")
    what.setProperty("takMuted", "true")
    what.setWordWrap(True)
    outer.addWidget(title)
    outer.addWidget(what)

    # ---------------- 输入 ----------------
    form_box = QtWidgets.QFrame()
    form_box.setProperty("takCard", "true")
    form = QtWidgets.QGridLayout(form_box)
    form.setContentsMargins(14, 12, 14, 12)
    form.setHorizontalSpacing(10)
    form.setVerticalSpacing(8)

    source_edit = QtWidgets.QLineEdit()
    source_edit.setPlaceholderText("图片目录，或一份帧列表 JSON")
    dir_button = QtWidgets.QPushButton("选目录")
    dir_button.setProperty("takGhost", "true")
    file_button = QtWidgets.QPushButton("选 JSON")
    file_button.setProperty("takGhost", "true")
    source_row = QtWidgets.QHBoxLayout()
    source_row.setSpacing(8)
    source_row.addWidget(source_edit, 1)
    source_row.addWidget(dir_button, 0)
    source_row.addWidget(file_button, 0)
    source_widget = QtWidgets.QWidget()
    source_widget.setLayout(source_row)
    form.addWidget(QtWidgets.QLabel("帧来源"), 0, 0)
    form.addWidget(source_widget, 0, 1, 1, 3)

    focus_edit = QtWidgets.QLineEdit()
    focus_edit.setPlaceholderText("可选：想重点看的部位或现象，例如「左肩」「腋下穿模」")
    form.addWidget(QtWidgets.QLabel("关注点"), 1, 0)
    form.addWidget(focus_edit, 1, 1, 1, 3)

    info_label = QtWidgets.QLabel("尚未载入帧。")
    info_label.setProperty("takHint", "true")
    info_label.setWordWrap(True)
    form.addWidget(info_label, 2, 0, 1, 4)
    outer.addWidget(form_box, 0)

    # ---------------- 动作 ----------------
    action_row = QtWidgets.QHBoxLayout()
    action_row.setSpacing(8)
    status = QtWidgets.QLabel("就绪")
    status.setProperty("takHint", "true")
    status.setWordWrap(True)
    preview_button = QtWidgets.QPushButton("载入预览")
    preview_button.setProperty("takGhost", "true")
    run_button = QtWidgets.QPushButton("开始诊断")
    run_button.setProperty("takPrimary", "true")
    run_button.setMinimumHeight(34)
    run_button.setMinimumWidth(110)
    action_row.addWidget(status, 1)
    action_row.addWidget(preview_button, 0)
    action_row.addWidget(run_button, 0)
    outer.addLayout(action_row)

    table = ResultTable(central)
    log = LogConsole(central)
    log.setMaximumHeight(88)
    outer.addWidget(table, 1)
    outer.addWidget(log, 0)

    window.setCentralWidget(central)
    apply_theme(window, _theme_path("dark"))

    frames: List[Dict[str, Any]] = []

    def load_frames() -> List[Dict[str, Any]]:
        raw = source_edit.text().strip()
        if not raw:
            status.setText("先选一个图片目录或帧列表 JSON。")
            return []
        try:
            if os.path.isdir(raw):
                items = _frames_from_dir(raw)
            elif os.path.isfile(raw):
                items = _frames_from_json(raw)
            else:
                status.setText("路径不存在：%s" % raw)
                return []
        except Exception as exc:
            status.setText("读取失败：%s: %s" % (type(exc).__name__, exc))
            log.error("读取帧来源失败：%s: %s" % (type(exc).__name__, exc))
            return []
        info_label.setText("已载入 %d 帧：%s" % (
            len(items), ", ".join(str(i.get("frame")) for i in items[:12])
            + (" …" if len(items) > 12 else "")))
        return items

    def on_preview() -> None:
        nonlocal frames
        frames = load_frames()
        if frames:
            status.setText("预览：%d 帧待诊断。" % len(frames))

    def on_run() -> None:
        nonlocal frames
        if not frames:
            frames = load_frames()
        if not frames:
            return
        run_button.setEnabled(False)
        status.setText("正在诊断 %d 帧 ..." % len(frames))
        try:
            from tak.core.runner import adapter_for, run_tool
            from types import SimpleNamespace
            adapter = adapter_for(dcc)
            try:
                snap = adapter.get_skeleton_snapshot()
            except Exception:
                snap = {}
            result = run_tool(TOOL_ID, snap, dcc=dcc, adapter=adapter,
                              options={"frames": frames,
                                       "focus": focus_edit.text().strip()})
            report = getattr(result, "report", None)
            table.set_report(report)
            counts = report.counts() if hasattr(report, "counts") else {}
            total = len(getattr(report, "findings", []))
            status.setText("完成：%d 条候选（warning %d / info %d）。全部需要人工复核。"
                           % (total, counts.get("warning", 0), counts.get("info", 0)))
            log.append("诊断完成：%d 帧 → %d 条候选" % (len(frames), total), "info")
        except Exception as exc:
            status.setText("诊断失败：%s: %s" % (type(exc).__name__, exc))
            log.error("诊断失败：%s: %s" % (type(exc).__name__, exc))
        finally:
            run_button.setEnabled(True)

    def on_pick_dir() -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            window, "选择抽帧图片目录", source_edit.text().strip() or "")
        if folder:
            source_edit.setText(folder)
            on_preview()

    def on_pick_file() -> None:
        path, _f = QtWidgets.QFileDialog.getOpenFileName(
            window, "选择帧列表 JSON", source_edit.text().strip() or "",
            "JSON (*.json);;所有文件 (*)")
        if path:
            source_edit.setText(path)
            on_preview()

    dir_button.clicked.connect(on_pick_dir)
    file_button.clicked.connect(on_pick_file)
    preview_button.clicked.connect(on_preview)
    run_button.clicked.connect(on_run)

    window.show()
    return window
