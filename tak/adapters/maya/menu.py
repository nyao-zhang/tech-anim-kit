# -*- coding: utf-8 -*-
"""Maya 菜单宿主入口。

用 ``maya.cmds`` + ``maya.mel`` 注册 / 反注册 TAK 菜单。设计约束与 Max / MoBu
两端保持一致：

* 顶层**零 DCC import**：``maya.cmds`` / ``maya.mel`` 一律在函数体内延迟导入；
* 任何失败（无宿主 / API 缺失 / 调用异常）都返回
  ``{"ok": False, "reason": ...}``，**绝不抛异常**到 Maya 启动流程 —— 一个菜单
  注册失败不该让整个 ``userSetup.py`` 崩掉，否则用户的 Maya 会静默失去所有
  自定义启动项；
* 注册是**幂等**的：已存在同名菜单先删再建，避免反复执行 ``userSetup`` 产生
  重复菜单项（Maya 的 ``cmds.menu`` 不会自己查重）；
* ``menu_script()`` 只生成字符串，import 与调用都不需要宿主，便于塞进
  ``userSetup.py`` 或 shelf 按钮。

为什么 Maya 端单独写一个模块
---------------------------
``tak.install`` 的 Maya 启动片段会 ``from tak.adapters.maya.menu import
register_menu``。早期版本这一层缺失，导致「安装成功、Maya 里却没有菜单」，
且安装块里的 ``except`` 是静默的 —— 用户只能看到什么都没发生。现在这个模块
是该导入路径的唯一实现，缺失会直接暴露为 ``ImportError``。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

from tak.adapters.snapshot_utils import call, safe

MENU_NAME = "TAK"
MENU_LABEL = "TAK / Tech Anim Kit"
MENU_OWNER = "TAK_MainMenu"
SHELF_NAME = "TAK"

# (菜单项显示名, 动作标识, 中文说明)。动作标识由 ``_dispatch`` 解析，
# 不直接内联代码：菜单项只传标识，避免把执行逻辑塞进 Maya 的 command 字符串。
MENU_ITEMS: Tuple[Tuple[str, str, str], ...] = (
    ("打开面板", "tak_launch_qt", "打开 TAK Qt 主面板"),
    ("场景体检", "tak_health_guard", "运行 Rig Health Guard"),
    ("规范校验", "tak_spec_enforcer", "运行 Spec Enforcer"),
    ("导出校验", "tak_export_bridge", "运行 Export Bridge 预检"),
    ("动作检索", "tak_motion_library", "打开 Web 动作检索面板"),
    ("Web 面板", "tak_web_panel", "启动本地 Web 面板"),
    (None, "tak_separator", ""),
    ("关于 TAK", "tak_about", "版本与仓库路径"),
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))


# --------------------------------------------------------------------------- #
# 宿主探测
# --------------------------------------------------------------------------- #
def _import_cmds() -> Tuple[Any, str]:
    """延迟导入 ``maya.cmds``；缺失时返回 ``(None, reason)``。"""
    try:
        from maya import cmds  # noqa: F401  (延迟导入，顶层不持有)
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)
    return cmds, ""


def _unavailable(reason: str) -> Dict[str, Any]:
    return {"ok": False, "reason": reason, "menu": MENU_NAME}


def _main_window(cmds: Any) -> str:
    """返回 Maya 主窗口名；老版本用 ``MayaWindow``，2022+ 走 ``mayaMainWindow``。"""
    value = call(cmds, "about", None, "mayaMainWindow", q=True)
    if isinstance(value, str) and value:
        return value
    return "MayaWindow"


# --------------------------------------------------------------------------- #
# 菜单注册 / 反注册
# --------------------------------------------------------------------------- #
def register_menu(menu_name: str = MENU_NAME,
                  label: str = MENU_LABEL) -> Dict[str, Any]:
    """注册 TAK 菜单；幂等，宿主不可用时返回明确 reason。

    :returns: 成功 ``{"ok": True, "menu": ..., "label": ..., "items": n}``；
              失败 ``{"ok": False, "reason": ..., "menu": ...}``。
    """
    cmds, reason = _import_cmds()
    if cmds is None:
        return _unavailable("maya.cmds 不可用（无 Maya 宿主）：%s" % reason)

    try:
        window = _main_window(cmds)
        # 幂等：先删后建。删除失败不算错误（可能本来就没有）。
        _delete_menu(cmds, menu_name)
        menu = call(cmds, "menu", None, menu_name, parent=window,
                    label=label, tearOff=True, allowOptionBoxes=True)
        if menu is None:
            return _unavailable("cmds.menu 未能创建菜单（主窗口 %s）" % window)

        created = 0
        for item_label, action, _hint in MENU_ITEMS:
            if item_label is None:
                call(cmds, "menuItem", None, divider=True, parent=menu_name)
                continue
            command = _command_for(action)
            item = call(cmds, "menuItem", None, item_label, parent=menu_name,
                        command=command)
            if item is not None:
                created += 1
        return {"ok": True, "menu": menu_name, "label": label,
                "items": created}
    except Exception as exc:
        return _unavailable("%s: %s" % (type(exc).__name__, exc))


def unregister_menu(menu_name: str = MENU_NAME) -> Dict[str, Any]:
    """注销 TAK 菜单；没有宿主或本来就不存在时同样安全返回。"""
    cmds, reason = _import_cmds()
    if cmds is None:
        return _unavailable("maya.cmds 不可用（无 Maya 宿主）：%s" % reason)
    try:
        existed = bool(call(cmds, "menu", None, menu_name, exists=True))
        if not existed:
            return {"ok": True, "menu": menu_name, "removed": False}
        _delete_menu(cmds, menu_name)
        return {"ok": True, "menu": menu_name, "removed": True}
    except Exception as exc:
        return _unavailable("%s: %s" % (type(exc).__name__, exc))


def _delete_menu(cmds: Any, menu_name: str) -> None:
    """尽力删除同名菜单；任何缺失 / 异常都吞掉（幂等注册的前置步骤）。"""
    try:
        if call(cmds, "menu", None, menu_name, exists=True):
            call(cmds, "deleteUI", None, menu_name)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 菜单项动作
# --------------------------------------------------------------------------- #
def _command_for(action: str) -> str:
    """返回菜单项要执行的 Python 语句（纯字符串，Maya 侧 ``exec`` 执行）。

    刻意只写一行 ``from ... import ...; f()`` 形式：Maya 的 ``menuItem
    -command`` 拿到的是字符串，多行代码在 MEL 里转义麻烦且难排查。
    """
    return ("import tak.adapters.maya.menu as _tak_menu; "
            "_tak_menu.dispatch(%r)" % action)


def dispatch(action: str) -> Any:
    """菜单项动作分发；无宿主 / 缺工具时打印一行说明而不是抛异常。

    这里是唯一会把用户操作接进工具链的地方，因此每一步都单独兜底：
    打开面板失败不该阻止用户运行体检，反之亦然。
    """
    action = str(action or "")
    if action == "tak_launch_qt":
        return _safe_call(_launch_panel, action)
    if action == "tak_health_guard":
        return _safe_call(lambda: _run_tool("rig_health_guard"), action)
    if action == "tak_spec_enforcer":
        return _safe_call(lambda: _run_tool("spec_enforcer"), action)
    if action == "tak_export_bridge":
        return _safe_call(lambda: _run_tool("export_bridge"), action)
    if action == "tak_motion_library":
        return _safe_call(_open_web_panel, action, "motion")
    if action == "tak_web_panel":
        return _safe_call(_open_web_panel, action)
    if action == "tak_about":
        return _safe_call(_about, action)
    return {"ok": False, "action": action, "reason": "未知菜单动作"}


def _safe_call(func: Any, action: str, *args: Any) -> Dict[str, Any]:
    try:
        result = func(*args)
    except Exception as exc:
        message = "%s: %s" % (type(exc).__name__, exc)
        print("[TAK] 菜单动作 %s 失败：%s" % (action, message))
        return {"ok": False, "action": action, "reason": message}
    if isinstance(result, dict):
        payload = dict(result)
        payload.setdefault("action", action)
        return payload
    return {"ok": True, "action": action, "result": result}


def _launch_panel(dcc: str = "maya") -> Any:
    from tak.ui.qt import launch
    return launch(dcc)


def _run_tool(tool_id: str) -> Any:
    """在 Maya 内跑一个工具：取当前场景快照 → 走统一运行契约 → 弹面板。"""
    from tak.core.runner import adapter_for, run_tool
    from tak.ui.qt import show_report

    adapter = adapter_for("maya")
    snapshot = adapter.get_skeleton_snapshot()
    result = run_tool(tool_id, snapshot, dcc="maya", adapter=adapter)
    show_report(result)
    return {"ok": bool(getattr(result, "passed", False)),
            "tool_id": tool_id,
            "findings": len(getattr(result, "report", None).findings
                            if getattr(result, "report", None) else [])}


def _open_web_panel(tab: str = "") -> Any:
    """启动本地 Web 面板（阻塞式服务器，放在后台线程里跑）。"""
    import threading
    from tak.ui.web.server import serve_in_thread

    return serve_in_thread(tab=tab)


def _about() -> Dict[str, Any]:
    from tak import __version__
    return {"ok": True, "version": __version__, "repo_root": _REPO_ROOT,
            "menu": MENU_NAME}


# --------------------------------------------------------------------------- #
# 启动脚本
# --------------------------------------------------------------------------- #
def menu_script() -> str:
    """返回可粘贴到 Maya 脚本编辑器 / ``userSetup.py`` 的启动代码。"""
    lines = [
        "# -*- coding: utf-8 -*-",
        "# TAK / Tech Anim Kit - Maya 2023+ 菜单启动脚本",
        "# 粘贴到 Maya 脚本编辑器执行，或整段放进 userSetup.py。",
        "import sys",
        "_TAK_ROOT = r\"%s\"" % _REPO_ROOT,
        "if _TAK_ROOT not in sys.path:",
        "    sys.path.insert(0, _TAK_ROOT)",
        "try:",
        "    from tak.adapters.maya.menu import register_menu",
        "    _tak_result = register_menu()",
        "    if not _tak_result.get('ok'):",
        "        print('[TAK] 菜单未注册：%s' % _tak_result.get('reason'))",
        "except Exception as _tak_exc:",
        "    print('[TAK] 菜单注册异常：%s: %s' % (type(_tak_exc).__name__, _tak_exc))",
    ]
    return "\n".join(lines)


def menu_items() -> List[Dict[str, str]]:
    """返回菜单结构（供测试与 Qt「关于」页展示），不触碰宿主。"""
    return [{"label": label or "---", "action": action, "hint": hint}
            for label, action, hint in MENU_ITEMS]
