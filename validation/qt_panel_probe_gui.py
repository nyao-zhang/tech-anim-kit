# -*- coding: utf-8 -*-
"""Qt 面板真机探测（GUI 模式）。

为什么要 GUI
------------
`mayabatch` 没有窗口系统：创建 `QMainWindow` 会直接让进程静默退出（无 traceback）。
因此本脚本必须用 `maya.exe` 启动，并把结果写进 JSON —— GUI 子系统程序的 stdout
不回控制台，只能靠文件取证据。

跑法（仓库根目录）::

    "C:/Program Files/Autodesk/Maya2026/bin/maya.exe" \
      -command "python(\"exec(open(r'C:/.../qt_panel_probe_gui.py', encoding='utf-8').read())\")"

脚本最后会 ``cmds.quit(force=True)``，不需要手动关 Maya。
"""
from __future__ import annotations

import io
import json
import os
import sys
import traceback

try:
    _SELF = os.path.abspath(__file__)
except NameError:
    _SELF = ""

ROOT = (os.environ.get("TAK_REPO_ROOT")
        or (os.path.dirname(os.path.dirname(_SELF)) if _SELF else os.getcwd()))
OUT = os.path.join(ROOT, "validation", "qt_panel_probe_result.json")


def _write(state):
    try:
        with io.open(OUT, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write(u"\n")
    except OSError:
        pass


def main():
    state = {"ok": False, "mode": "gui", "python": sys.version.split()[0]}
    try:
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)

        import tak
        state["version"] = tak.__version__

        from tak.compat.qt import QT_BINDING, QtWidgets
        state["qt_binding"] = QT_BINDING
        if QtWidgets is None:
            state["reason"] = "没有可用的 Qt 绑定"
            return state

        app = QtWidgets.QApplication.instance()
        if app is None:
            app = QtWidgets.QApplication([])
        state["qapp"] = app is not None

        from tak.ui.qt import HAS_QT, ToolMainWindow
        state["has_qt"] = HAS_QT
        if not HAS_QT:
            state["reason"] = "tak.ui.qt 判定 HAS_QT=False"
            return state

        window = ToolMainWindow(dcc="maya")
        state["window_created"] = True
        state["categories"] = [window.category_list.item(i).text()
                               for i in range(window.category_list.count())]
        state["tools"] = [window.tool_list.item(i).text()
                          for i in range(window.tool_list.count())]

        details = []
        for row in range(window.tool_list.count()):
            window.tool_list.setCurrentRow(row)
            tool = window.detail_panel.tool
            details.append({
                "id": tool.get("id"),
                "name": tool.get("name"),
                "has_summary": bool(tool.get("summary")),
                "features": len(tool.get("features") or []),
                "usage": len(tool.get("usage") or []),
                "categories": tool.get("categories") or [],
            })
        state["details"] = details

        per_category = {}
        for index in range(window.category_list.count()):
            window.category_list.setCurrentRow(index)
            label = window.category_list.item(index).text()
            per_category[label] = window.tool_list.count()
        state["tools_per_category"] = per_category

        # 工具自带界面模块能否加载（只加载，不 show —— show 会开真窗口）
        from tak.core import runner
        from tak.core.registry import load_plugin_module
        ui_modules = {}
        for tool in runner.list_tools(dcc="maya"):
            tid = tool["id"]
            path = os.path.join(tool["path"], "ui_qt.py")
            if not os.path.isfile(path):
                ui_modules[tid] = "missing ui_qt.py"
                continue
            try:
                module = load_plugin_module(runner.find_plugin(tid), "ui_qt")
                ui_modules[tid] = ("show" if callable(getattr(module, "show", None))
                                   else "no show()")
            except Exception as exc:
                ui_modules[tid] = "%s: %s" % (type(exc).__name__, exc)
        state["tool_ui_modules"] = ui_modules

        # 详情面板空态
        window.detail_panel.set_tool(None)
        state["empty_state_ok"] = window.detail_panel.tool == {}

        # 设置面板能否构造（不 show）
        from tak.ui.qt.settings import SettingsDialog
        dialog = SettingsDialog(dcc="maya")
        state["settings_created"] = True
        state["settings_rows"] = dialog.tool_table.rowCount()
        state["settings_categories"] = dialog.category_list.count()
        dialog.deleteLater()

        window.deleteLater()
        state["ok"] = True
    except Exception as exc:
        state["reason"] = "%s: %s" % (type(exc).__name__, exc)
        state["traceback"] = traceback.format_exc()
    return state


try:
    _state = main()
finally:
    _write(_state if "_state" in globals() else {"ok": False, "reason": "脚本异常退出"})
    try:
        import maya.cmds as _cmds
        _cmds.quit(force=True)
    except Exception:
        pass
