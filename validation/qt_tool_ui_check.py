# -*- coding: utf-8 -*-
"""真 Qt 回归检查：每个工具点「打开工具界面」都必须打开自己的窗口。

用 Maya 自带的 mayapy + PySide6 offscreen 平台跑，不需要启动 Maya GUI、不会
留下僵死进程；CI / 开发机上没有 PySide 时直接退出并说明。

跑法（仓库根目录，Windows）::

    set QT_QPA_PLATFORM=offscreen
    "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" validation/qt_tool_ui_check.py

产出的 JSON 写到 validation/qt_tool_ui_check_result.json。
"""
from __future__ import annotations

import gc
import io
import json
import os
import sys
import time
import traceback

ROOT = (os.environ.get("TAK_REPO_ROOT")
        or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "validation", "qt_tool_ui_check_result.json")


def write(state):
    with io.open(OUT, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write(u"\n")


def main():
    state = {"ok": False}
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)

    from tak.compat.qt import QT_BINDING, QtWidgets
    state["qt_binding"] = QT_BINDING
    if QtWidgets is None:
        state["reason"] = "没有 Qt 绑定"
        return state
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    import tak.ui.qt as ui
    from tak.ui.qt import launcher
    from tak.core import runner

    state["has_qt"] = ui.HAS_QT
    if not ui.HAS_QT:
        state["reason"] = "HAS_QT=False"
        return state

    window = ui.ToolMainWindow(dcc="maya")
    state["categories"] = window.category_list.count()
    state["visible_tools"] = window.tool_list.count()

    # 分类切换耗时；顺带确认没有新建顶层窗口
    switches = []
    for row in range(window.category_list.count()):
        before = len(app.topLevelWidgets())
        started = time.perf_counter()
        window.category_list.setCurrentRow(row)
        app.processEvents()
        elapsed = (time.perf_counter() - started) * 1000.0
        switches.append({
            "category": window.category_list.item(row).text(),
            "ms": round(elapsed, 1),
            "tools": window.tool_list.count(),
            "new_windows": len(app.topLevelWidgets()) - before,
        })
    state["category_switch"] = switches

    window.category_list.setCurrentRow(0)
    tool_ids = [str(window.tool_list.item(row).data(0x0100) or "")
                for row in range(window.tool_list.count())]

    opened = []
    for tid in tool_ids:
        launcher._TOOL_KEEPALIVE.pop(tid, None)
        result = launcher.open_tool(tid, dcc="maya")
        error = launcher.last_open_error(tid)
        payload = {
            "id": tid,
            "error": error,
            "result": type(result).__name__,
        }
        if hasattr(result, "windowTitle"):
            payload["title"] = str(result.windowTitle())
            payload["is_tool_window"] = True
            payload["is_report_window"] = "TAK 报告" in str(result.windowTitle())
        else:
            payload["is_tool_window"] = False
            payload["is_report_window"] = False
        gc.collect()
        app.processEvents()
        payload["kept_alive"] = launcher._TOOL_WINDOWS.get(tid) is not None
        try:
            # wrapper 若已被 Qt 销毁，调用任何方法都会抛 RuntimeError；
            # 不能用 `bool(...) or True` 这种恒真式糊过去。
            result.objectName()
            payload["still_alive"] = True
        except Exception:
            payload["still_alive"] = False
        opened.append(payload)
    state["open_tool"] = opened

    state["ok"] = all([
        state["visible_tools"] >= 5,
        all(item["new_windows"] == 0 for item in switches),
        all(item["is_tool_window"] and not item["is_report_window"]
            and not item["error"] and item["kept_alive"]
            and item["still_alive"] for item in opened),
    ])
    return state


if __name__ == "__main__":
    try:
        _state = main()
    except Exception as exc:
        _state = {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc),
                  "traceback": traceback.format_exc()[-2000:]}
    write(_state)
    print(json.dumps(_state, ensure_ascii=False, indent=2))
