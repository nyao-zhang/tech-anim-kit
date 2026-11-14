# -*- coding: utf-8 -*-
"""诊断探针：分类切换耗时 / 弹窗来源 / 工具界面是否回落成体检报告。

在 Maya GUI 里跑（-command python("exec(open(...).read())")），结果写 JSON 后强退。
只做观测，不改任何用户配置。
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
        or r"C:/Users/NYAO/WorkBuddy/2026-11-12-23-27-35")
OUT = os.path.join(ROOT, "validation", "qt_category_probe_result.json")


def write(state):
    try:
        with io.open(OUT, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write(u"\n")
    except OSError:
        pass


def top_widgets(app):
    out = []
    try:
        for w in app.topLevelWidgets():
            try:
                if not w.isVisible():
                    continue
                out.append({
                    "name": str(w.objectName() or ""),
                    "title": str(w.windowTitle() or ""),
                    "class": type(w).__name__,
                    "modal": bool(getattr(w, "isModal", lambda: False)()),
                })
            except Exception:
                continue
    except Exception as exc:
        out.append({"error": str(exc)})
    return out


class Capture(object):
    """把 print 到 stdout/stderr 的内容抓下来（Maya 里这些不会回控制台）。"""

    def __init__(self):
        self.items = []
        self._out = None
        self._err = None

    def __enter__(self):
        self._out, self._err = sys.stdout, sys.stderr
        sys.stdout = self
        sys.stderr = self
        return self

    def write(self, text):
        self.items.append(str(text))
        return len(text)

    def flush(self):
        return None

    def __exit__(self, *_args):
        sys.stdout, sys.stderr = self._out, self._err
        return False

    def text(self):
        return "".join(self.items)[-1500:]


def main():
    state = {"ok": False}
    try:
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)

        from tak.compat.qt import QtWidgets
        app = QtWidgets.QApplication.instance()
        if app is None:
            app = QtWidgets.QApplication([])

        from tak.ui.qt import ToolMainWindow
        from tak.ui.qt import launcher
        from tak.core import runner

        window = ToolMainWindow(dcc="maya")
        state["before_widgets"] = top_widgets(app)

        # ---- 1) 分类切换：每次耗时 + 顶层窗口变化 + 控制台输出 ----
        switch = []
        for row in range(window.category_list.count()):
            label = window.category_list.item(row).text()
            before = top_widgets(app)
            with Capture() as cap:
                started = time.perf_counter()
                window.category_list.setCurrentRow(row)
                app.processEvents()
                elapsed = (time.perf_counter() - started) * 1000.0
            after = top_widgets(app)
            added = [w for w in after if w not in before]
            removed = [w for w in before if w not in after]
            switch.append({
                "category": label,
                "ms": round(elapsed, 1),
                "tools": window.tool_list.count(),
                "selected": window.detail_panel.tool.get("id"),
                "added_windows": added,
                "removed_windows": removed,
                "stdout": cap.text(),
            })
        state["category_switch"] = switch

        # ---- 2) 每个可见工具：走真实 open_tool 路径 ----
        # 切回「全部」，保证覆盖所有可见工具
        window.category_list.setCurrentRow(0)
        tool_ids = []
        for row in range(window.tool_list.count()):
            tool_ids.append(str(window.tool_list.item(row).data(0x0100) or ""))
        state["visible_tools"] = tool_ids

        opened = []
        for tid in tool_ids:
            with Capture() as cap:
                started = time.perf_counter()
                try:
                    result = launcher.open_tool(tid, dcc="maya")
                except Exception as exc:
                    result = None
                    cap.write("EXC %s: %s" % (type(exc).__name__, exc))
                elapsed = (time.perf_counter() - started) * 1000.0
            payload = {"id": tid, "ms": round(elapsed, 1), "stdout": cap.text()}
            if result is None:
                payload["result"] = "None"
            else:
                try:
                    payload["result"] = type(result).__name__
                    if hasattr(result, "windowTitle"):
                        payload["title"] = str(result.windowTitle())
                        payload["visible"] = bool(result.isVisible())
                        payload["object_name"] = str(result.objectName())
                        payload["is_report"] = "报告" in str(result.windowTitle())
                    else:
                        payload["title"] = "-"
                        payload["is_report"] = False
                except Exception as exc:
                    payload["inspect_error"] = str(exc)
            # GC 之后还在不在？弱引用缓存还在不在？
            gc.collect()
            app.processEvents()
            payload["weak_cache_alive"] = launcher._TOOL_WINDOWS.get(tid) is not None
            try:
                payload["still_visible"] = bool(result.isVisible()) if hasattr(result, "isVisible") else None
            except Exception:
                payload["still_visible"] = "gone"
            opened.append(payload)
        state["open_tool"] = opened
        state["after_widgets"] = top_widgets(app)
        state["ok"] = True
    except Exception as exc:
        state["reason"] = "%s: %s" % (type(exc).__name__, exc)
        state["traceback"] = traceback.format_exc()[-2000:]
    return state


try:
    _state = main()
finally:
    write(_state if "_state" in globals() else {"ok": False, "reason": "脚本异常退出"})
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(0)
