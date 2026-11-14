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
        # v0.5.0：设置里多了「导入工具」页，四个页签的顺序也要对得上
        state["settings_tabs"] = [dialog.tabs.tabText(i)
                                  for i in range(dialog.tabs.count())]
        state["import_widgets"] = {
            "path": hasattr(dialog, "import_path"),
            "table": hasattr(dialog, "import_list"),
            "report": hasattr(dialog, "import_report"),
            "ai_checkbox": hasattr(dialog, "import_ai"),
        }
        dialog.deleteLater()

        # 对话控制台的「连接」行：桥接与 AI 两条通道各自可开
        from tools.agent_console import ui_qt as console_ui
        core = console_ui._plugin_core()
        state["console_capabilities"] = core.info().get("capabilities")

        # 桥接状态与 AI 连接描述都必须可读（哪怕没配密钥也要给原因）
        from tak.core import bridge as tak_bridge
        from tak.core.ai import connection
        state["bridge_status"] = {
            "running": tak_bridge.bridge_status().get("running"),
            "endpoints": list(tak_bridge.ENDPOINTS),
        }
        describe = connection.describe()
        state["ai_connection"] = {
            "ready": describe.get("ready"),
            "base_url_source": describe.get("base_url_source"),
            "api_key_env": describe.get("api_key_env"),
            "hint": describe.get("hint"),
        }

        # 导入器端到端：造一个真脚本 → 安装 → 出现在工具列表 → 运行 → 卸载
        state["importer"] = _probe_importer()

        # v0.6.0：桥接改成一行开关，AI 连接改成三步向导 —— 两者都要在真 Maya 里
        # 点一遍，否则「面板上有个按钮」和「点了有用」是两回事。
        state["bridge_toggle"] = _probe_bridge_toggle()
        state["ai_wizard"] = _probe_ai_wizard()

        window.deleteLater()
        state["ok"] = True
    except Exception as exc:
        state["reason"] = "%s: %s" % (type(exc).__name__, exc)
        state["traceback"] = traceback.format_exc()
    return state


def _probe_importer():
    """在真 Maya 里验证导入器：导入的脚本能用 cmds，且运行结果如实回报。"""
    import tempfile
    from tak.core import importer, runner

    # 逐行拼装：转义写坏过一次，逐行最稳
    lines = [
        "# -*- coding: utf-8 -*-",
        '"""临时探针脚本：统计场景里的关节数量。',
        "",
        "- 列出全部关节",
        "- 报告数量",
        '"""',
        "import maya.cmds as cmds",
        "",
        "",
        "def count_joints():",
        '    """返回场景中的关节数量。"""',
        '    return len(cmds.ls(type="joint") or [])',
        "",
        "",
        "def main():",
        '    print("joints", count_joints())',
        "",
    ]
    temp_dir = tempfile.mkdtemp(prefix="tak_probe_")
    path = os.path.join(temp_dir, "probe_joint_counter.py")
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write(chr(10).join(lines))
    try:
        analysis = importer.analyze(path)
        draft = importer.draft_manifest(analysis)
        result = importer.install(path, manifest=draft, analysis=analysis,
                                  overwrite=True)
        if not result.get("ok"):
            return {"ok": False, "stage": "install", "error": result.get("error")}
        tool_id = result["tool_id"]
        discovered = tool_id in {item["id"] for item in runner.list_tools(dcc="maya")}
        run = runner.run_tool(tool_id, None, dcc="maya")
        payload = {
            "ok": True,
            "tool_id": tool_id,
            "discovered": discovered,
            "permissions": draft.get("permissions"),
            "dcc": draft.get("dcc"),
            "has_report_contract": analysis.get("has_report_contract"),
            "run_passed": run.report.passed,
            "finding_ids": [item.rule_id for item in run.report.findings],
            "stdout_tail": str(run.report.metadata.get("stdout") or "")[-120:],
        }
        importer.uninstall(tool_id)
        payload["uninstalled"] = not importer.is_imported(tool_id)
        return payload
    except Exception as exc:
        return {"ok": False, "stage": "exception",
                "error": "%s: %s" % (type(exc).__name__, exc),
                "traceback": traceback.format_exc()[-800:]}


# --------------------------------------------------------------------------- #
# v0.6.0：桥接一行开关（真点一次）
# --------------------------------------------------------------------------- #
def _probe_bridge_toggle():
    """点一下开关就该起，再点一下就该停 —— 而不是「弹个框告诉你状态」。

    面板上有个按钮 ≠ 按钮有用。这里用真实的 ``click()`` 走完整路径。
    """
    from tak.core import bridge
    from tools.agent_console import ui_qt as console_ui

    payload = {"ok": False}
    window = None
    try:
        if bridge.bridge_status().get("running"):
            bridge.stop_bridge()          # 前置条件：从未运行开始测

        window = console_ui.show(dcc="maya")
        if window is None:
            return {"ok": False, "error": "console_ui.show 返回 None"}

        button = window._agent_bridge_toggle
        label = window._agent_bridge_label
        payload["text_before"] = button.text()
        payload["label_before"] = label.text()
        payload["running_before"] = bridge.bridge_status().get("running")

        button.click()                    # → 启动
        payload["text_after_start"] = button.text()
        payload["label_after_start"] = label.text()
        started = bridge.bridge_status()
        payload["running_after_start"] = started.get("running")
        payload["url"] = started.get("url")
        payload["thread_safe"] = started.get("thread_safe")

        button.click()                    # → 停止
        payload["text_after_stop"] = button.text()
        payload["running_after_stop"] = bridge.bridge_status().get("running")
        payload["error_detail"] = window._agent_bridge_error()

        payload["ok"] = all([
            payload["running_before"] is False,
            payload["running_after_start"] is True,
            payload["text_after_start"] == "停止",
            "http://" in str(payload["label_after_start"]),
            payload["running_after_stop"] is False,
            payload["text_after_stop"] == "启动桥接",
            payload["error_detail"] == "",
        ])
    except Exception as exc:
        payload["error"] = "%s: %s" % (type(exc).__name__, exc)
        payload["traceback"] = traceback.format_exc()[-700:]
    finally:
        try:
            bridge.stop_bridge()
        except Exception:
            pass
        try:
            if window is not None:
                window.close()
        except Exception:
            pass
    return payload


# --------------------------------------------------------------------------- #
# v0.6.0：AI 连接向导（选预设 → 粘密钥 → 测试 → 保存 → 清除）
# --------------------------------------------------------------------------- #
def _probe_ai_wizard():
    """用一个本地的假 OpenAI 端点走完整流程，不碰真实配置目录。

    关键是证明「粘一个 key 就能用」：不设环境变量、不改系统设置、不重启 Maya。
    """
    import shutil
    import tempfile
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from tak.compat.qt import QtWidgets
    from tak.core.ai import connection, credential
    from tak.ui.qt import LogConsole, StatusBar
    from tools.agent_console.ui_qt import _ai_dialog

    class _Fake(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # noqa: N802
            return

        def _send(self, payload):
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):  # noqa: N802
            self._send({"data": [{"id": "probe-model"}]})

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            self._send({"choices": [{"message": {"content": "pong"}}]})

    payload = {"ok": False}
    tmp_home = tempfile.mkdtemp(prefix="tak_ai_probe_")
    previous_home = os.environ.get("TAK_HOME")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d/v1" % server.server_address[1]
    dialog = None
    try:
        # 隔离配置目录：探针写的东西不能留在用户真实的 ~/.tak 里
        os.environ["TAK_HOME"] = tmp_home

        dialog = _ai_dialog(QtWidgets, None, LogConsole(), StatusBar())
        payload["presets_in_ui"] = dialog._preset_box.count()
        payload["presets_expected"] = len(connection.PRESETS)
        payload["has_widgets"] = all([
            dialog._base_edit is not None, dialog._model_box is not None,
            dialog._key_edit is not None, dialog._output is not None,
            dialog._remember is not None])

        # 1) 选预设应自动填地址与模型
        index = dialog._preset_box.findData("deepseek")
        dialog._preset_box.setCurrentIndex(index)
        payload["preset_base"] = dialog._base_edit.text()
        payload["preset_model"] = dialog._model_box.currentText()

        # 2) 换成探针端点 + 临时密钥，先测（不保存）
        dialog._base_edit.setText(base)
        dialog._model_box.setCurrentText("probe-model")
        dialog._key_edit.setText("sk-probe-123456")
        dialog._test()
        text = dialog._output.toPlainText()
        payload["test_ok"] = u"就绪：是" in text
        payload["test_output"] = text[-200:]
        payload["saved_before_click"] = credential.describe()["present"]

        # 3) 保存：密钥进凭据文件，不进 config.json
        dialog._save()
        described = credential.describe()
        payload["saved_after_click"] = described["present"]
        payload["saved_tail"] = described["tail"]
        payload["key_field_cleared"] = dialog._key_edit.text() == ""

        from tak.core import config as tak_config
        ai = tak_config.load()["ai"]
        payload["config_base_url"] = ai.get("base_url")
        payload["config_model"] = ai.get("model")
        payload["config_leaks_secret"] = "sk-probe" in json.dumps(ai)

        # 4) 保存后再探一次：这次密钥来自文件而不是输入框
        dialog._key_edit.clear()
        dialog._test()
        payload["test_from_file"] = u"本机凭据文件" in dialog._output.toPlainText()

        # 5) 清除
        dialog._forget()
        payload["cleared"] = credential.describe()["present"] is False

        payload["ok"] = all([
            payload["presets_in_ui"] == payload["presets_expected"],
            payload["has_widgets"],
            payload["preset_base"] == "https://api.deepseek.com",
            payload["preset_model"] == "deepseek-v4-flash",
            payload["test_ok"],
            payload["saved_before_click"] is False,
            payload["saved_after_click"] is True,
            payload["key_field_cleared"] is True,
            payload["config_leaks_secret"] is False,
            payload["test_from_file"] is True,
            payload["cleared"] is True,
        ])
    except Exception as exc:
        payload["error"] = "%s: %s" % (type(exc).__name__, exc)
        payload["traceback"] = traceback.format_exc()[-900:]
    finally:
        try:
            if dialog is not None:
                dialog.close()
        except Exception:
            pass
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass
        if previous_home is None:
            os.environ.pop("TAK_HOME", None)
        else:
            os.environ["TAK_HOME"] = previous_home
        shutil.rmtree(tmp_home, ignore_errors=True)
    return payload


try:
    _state = main()
finally:
    _write(_state if "_state" in globals() else {"ok": False, "reason": "脚本异常退出"})
    # 为什么直接强退
    # --------------
    # ``cmds.quit(force=True)`` 在装了第三方插件的环境里会卡在退出流程里不返回；
    # 换 Python 线程做看门狗也不行 —— 主线程卡在 C 扩展里时不释放 GIL，看门狗
    # 线程拿不到执行权（实测两次都等到 ``timeout`` 才被杀，留下 700MB+ 的残留
    # 进程，taskkill 还报「没有此任务的实例在运行」）。
    # 探针的全部价值就是那份 JSON，它已经写盘了，所以直接结束进程最可靠。
    try:
        import sys as _sys
        _sys.stdout.flush()
        _sys.stderr.flush()
    except Exception:
        pass
    os._exit(0)
