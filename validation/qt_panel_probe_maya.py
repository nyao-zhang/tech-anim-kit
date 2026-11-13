# -*- coding: utf-8 -*-
"""Qt 面板真机探测：在 Maya 环境里构造主窗口，验证分类 / 工具 / 详情三条链路。

为什么需要它
------------
pytest 环境没有 PySide，`HAS_QT` 恒为 False，单测只能覆盖「无 Qt 降级分支」。
面板重构（分类导航、详情渲染、独立界面调度）全部写在 `if HAS_QT:` 里面，
**只有真 Maya 能验证**。这个脚本用 mayabatch 拉起真实 PySide2，把窗口构造出来
但不 `show()`，逐项读出控件内容，把证据落成 JSON。

跑法（在仓库根目录）::

    "/c/Program Files/Autodesk/Maya2026/bin/mayabatch.exe" \
        -command "python(\"exec(open(r'C:/.../validation/qt_panel_probe_maya.py', encoding='utf-8').read())\")"

普通 Python 直接运行时只会打印一句提示（没有 Maya，拿不到 PySide2）。
"""
from __future__ import annotations

import io
import json
import os
import sys
import traceback

try:
    _SELF = os.path.abspath(__file__)
except NameError:  # mayabatch -command "python(exec(open(...)))" 没有 __file__
    _SELF = ""

ROOT = (os.environ.get("TAK_REPO_ROOT")
        or (os.path.dirname(os.path.dirname(_SELF)) if _SELF else os.getcwd()))
OUT = os.path.join(ROOT, "validation", "qt_panel_probe_result.json")


def _record(state, key, value):
    state[key] = value
    print("[probe] %s = %r" % (key, value))


def main():
    state = {"ok": False, "python": sys.version.split()[0]}
    try:
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)

        import tak
        _record(state, "version", tak.__version__)

        from tak.compat.qt import QT_BINDING, QtWidgets
        _record(state, "qt_binding", QT_BINDING)
        if QtWidgets is None:
            state["reason"] = "当前解释器没有 PySide2/PySide6"
            return state

        app = QtWidgets.QApplication.instance()
        if app is None:
            app = QtWidgets.QApplication([])
        _record(state, "qapp", app is not None)

        from tak.ui.qt import HAS_QT, ToolDetailPanel, ToolMainWindow
        _record(state, "has_qt", HAS_QT)
        if not HAS_QT:
            state["reason"] = "tak.ui.qt 判定 HAS_QT=False"
            return state

        # ---- 1. 主窗口构造 ----
        window = ToolMainWindow(dcc="maya")
        _record(state, "window_created", True)
        state["window_size"] = [window.width(), window.height()]

        # ---- 2. 分类列表 ----
        categories = [window.category_list.item(i).text()
                      for i in range(window.category_list.count())]
        _record(state, "categories", categories)

        # ---- 3. 工具列表 ----
        tools = [window.tool_list.item(i).text()
                 for i in range(window.tool_list.count())]
        _record(state, "tools", tools)

        # ---- 4. 逐项选中，检查详情渲染 ----
        details = []
        for row in range(window.tool_list.count()):
            window.tool_list.setCurrentRow(row)
            tool = window.detail_panel.tool
            details.append({
                "id": tool.get("id"),
                "name": tool.get("name"),
                "summary": bool(tool.get("summary")),
                "features": len(tool.get("features") or []),
                "usage": len(tool.get("usage") or []),
                "categories": tool.get("categories") or [],
                "open_button": window.detail_panel.open_button.isVisibleTo(window.detail_panel)
                               if hasattr(window.detail_panel, "open_button") else None,
                "run_button": window.detail_panel.run_button.isVisibleTo(window.detail_panel)
                              if hasattr(window.detail_panel, "run_button") else None,
            })
        _record(state, "details", details)

        # ---- 5. 分类切换：每个分类下的工具数 ----
        per_category = {}
        for index in range(window.category_list.count()):
            window.category_list.setCurrentRow(index)
            label = window.category_list.item(index).text()
            per_category[label] = window.tool_list.count()
        _record(state, "tools_per_category", per_category)

        # ---- 6. 工具自带界面模块能否加载（不 show） ----
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
                ui_modules[tid] = "show" if callable(getattr(module, "show", None)) else "no show()"
            except Exception as exc:
                ui_modules[tid] = "%s: %s" % (type(exc).__name__, exc)
        _record(state, "tool_ui_modules", ui_modules)

        # ---- 7. 详情面板空态 ----
        window.detail_panel.set_tool(None)
        _record(state, "empty_state_ok", window.detail_panel.tool == {})

        state["ok"] = True
    except Exception as exc:
        state["reason"] = "%s: %s" % (type(exc).__name__, exc)
        state["traceback"] = traceback.format_exc()
        print("[probe] FAILED: %s" % state["reason"])
    return state


def _write(state):
    try:
        with io.open(OUT, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write(u"\n")
        print("[probe] 证据写入 %s" % OUT)
    except OSError as exc:
        print("[probe] 写证据失败: %s" % exc)


if __name__ == "__main__":
    if "maya" not in sys.modules and "maya.cmds" not in sys.modules:
        try:
            import maya.cmds  # noqa: F401
        except ImportError:
            print("[probe] 这不是 Maya 环境；请用 mayabatch 运行本脚本。")
            sys.exit(2)
    result = main()
    if result.get("ok"):
        _write(result)
    else:
        _write(result)
        sys.exit(1)
