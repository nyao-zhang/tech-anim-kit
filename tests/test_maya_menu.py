# -*- coding: utf-8 -*-
"""Maya 菜单入口回归（P6 补漏）。

背景：``tak.install`` 的 Maya 启动片段会
``from tak.adapters.maya.menu import register_menu``。P6 之前这一层**根本不存在**
（Max / MoBu / UE 都有 ``menu.py``，只有 Maya 没有），而安装块里的 ``except``
是静默的 —— 结果就是「安装成功、Maya 里却没有菜单，而且没有任何提示」。

这些用例锁三件事：
1. 无宿主时不抛异常、给出可读 reason；
2. 有宿主时菜单真的建出来、且**幂等**（反复执行 ``userSetup`` 不会堆积菜单）；
3. 菜单项分发逐级兜底，单个动作失败不影响其它动作。
"""
from __future__ import annotations

import ast
import os
import sys
from types import ModuleType

import pytest

import tak
from tak.adapters.maya import menu

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------- #
# 假 maya.cmds
# --------------------------------------------------------------------------- #
class FakeCmds:
    """记录调用的最小 cmds 替身；只实现 menu.py 真正用到的那几个函数。

    ``ui=False`` 复现 batch / 无界面启动下的真实行为：``cmds.menu`` 与
    ``cmds.window`` **不抛异常，直接返回 False**，窗口也不存在。老实现只判
    ``menu is None``，在这种宿主里会报 ``ok=True`` 却什么都没建。
    """

    def __init__(self, window="MayaWindow", ui=True):
        self.window = window
        self.ui = ui
        self.calls = []
        self.menus = {}
        self.items = []
        #: False 时 ``menu()`` 返回名字但**不记录**，模拟"看起来成功其实没建"
        self.honor_menus = True

    # -- 被 menu.py 调用的接口 ------------------------------------------- #
    def window(self, name=None, q=False, exists=False, **kwargs):
        self.calls.append(("window", name, dict(kwargs, q=q, exists=exists)))
        if exists:
            return bool(self.ui) and name == self.window
        return name

    def lsUI(self, window=False, **kwargs):
        self.calls.append(("lsUI", None, dict(kwargs, window=window)))
        return [self.window] if self.ui else []

    def menu(self, name=None, q=False, exists=False, **kwargs):
        self.calls.append(("menu", name, dict(kwargs, q=q, exists=exists)))
        if exists:
            return name in self.menus
        if q:
            return self.menus.get(name)
        if not self.honor_menus:
            return name
        self.menus[name] = dict(kwargs)
        return name

    def deleteUI(self, name=None, **kwargs):
        self.calls.append(("deleteUI", name))
        self.menus.pop(name, None)

    def menuItem(self, label=None, **kwargs):
        self.calls.append(("menuItem", label, dict(kwargs)))
        if kwargs.get("divider"):
            self.items.append(("---", kwargs.get("parent")))
            return "divider1"
        self.items.append((label, kwargs.get("parent")))
        return "item%d" % len(self.items)


@pytest.fixture()
def fake_host(monkeypatch):
    """注入一个假的 ``maya`` 包；返回 FakeCmds 供断言。"""
    cmds = FakeCmds()
    package = ModuleType("maya")
    package.cmds = cmds
    monkeypatch.setitem(sys.modules, "maya", package)
    monkeypatch.setitem(sys.modules, "maya.cmds", cmds)
    return cmds


@pytest.fixture()
def headless_host(monkeypatch):
    """无界面宿主（batch）：有 ``maya.cmds``，但没有主窗口。"""
    cmds = FakeCmds(ui=False)
    package = ModuleType("maya")
    package.cmds = cmds
    monkeypatch.setitem(sys.modules, "maya", package)
    monkeypatch.setitem(sys.modules, "maya.cmds", cmds)
    return cmds


# --------------------------------------------------------------------------- #
# 无宿主：安全降级
# --------------------------------------------------------------------------- #
def test_register_without_host_returns_reason(monkeypatch):
    monkeypatch.setitem(sys.modules, "maya", None)
    result = menu.register_menu()
    assert result["ok"] is False
    assert result["menu"] == menu.MENU_NAME
    assert "maya.cmds 不可用" in result["reason"]


def test_unregister_without_host_returns_reason(monkeypatch):
    monkeypatch.setitem(sys.modules, "maya", None)
    result = menu.unregister_menu()
    assert result["ok"] is False
    assert "无 Maya 宿主" in result["reason"]


def test_dispatch_unknown_action():
    result = menu.dispatch("tak_not_a_real_action")
    assert result["ok"] is False
    assert "未知菜单动作" in result["reason"]


def test_dispatch_about_reports_version():
    result = menu.dispatch("tak_about")
    assert result["ok"] is True
    # _safe_call 对 dict 返回值做扁平合并（补 action 字段），不是嵌套 result
    assert result["version"] == tak.__version__
    assert result["menu"] == menu.MENU_NAME
    assert result["action"] == "tak_about"


def test_dispatch_swallows_action_failure(monkeypatch, capsys):
    def boom():
        raise RuntimeError("面板炸了")

    monkeypatch.setattr(menu, "_launch_panel", boom)
    result = menu.dispatch("tak_launch_qt")
    assert result["ok"] is False
    assert "RuntimeError" in result["reason"]
    captured = capsys.readouterr()
    assert "[TAK] 菜单动作 tak_launch_qt 失败" in captured.out


# --------------------------------------------------------------------------- #
# 有宿主：菜单结构正确且幂等
# --------------------------------------------------------------------------- #
def test_register_builds_menu_and_items(fake_host):
    result = menu.register_menu()
    assert result["ok"] is True
    assert result["menu"] == menu.MENU_NAME
    assert result["items"] == sum(1 for label, _a, _h in menu.MENU_ITEMS
                                  if label is not None)
    # 菜单建在主窗口下，且带 tearOff
    created = fake_host.menus[menu.MENU_NAME]
    assert created["parent"] == "MayaWindow"
    assert created["tearOff"] is True
    # 分隔符真的插入了
    assert ("---", menu.MENU_NAME) in fake_host.items


def test_register_falls_back_to_lsui_window(fake_host):
    """主窗口不叫 MayaWindow/mayaMainWindow 时，用 lsUI(window=True) 兜底。"""
    fake_host.window = "MayaWindow_2026"
    assert menu.register_menu()["ok"] is True
    assert fake_host.menus[menu.MENU_NAME]["parent"] == "MayaWindow_2026"


def test_main_window_never_uses_about_flag(fake_host):
    """``cmds.about(mayaMainWindow=True)`` 在 Maya 2026 是非法标志，不许再调。"""
    menu.register_menu()
    assert all(call[0] != "about" for call in fake_host.calls)


def test_register_fails_loudly_when_no_main_window(headless_host):
    """batch / 无界面：必须 ok=False 且说明原因，不能假成功。

    真实证据：``mayabatch -script`` 里 ``cmds.menu(...)`` 返回 False 不抛异常，
    ``cmds.window("MayaWindow", q=True, exists=True)`` 也是 False。老实现只判
    ``menu is None``，于是报 ``ok=True, items=7`` —— 菜单其实一个都没建。
    """
    result = menu.register_menu()
    assert result["ok"] is False
    assert "找不到 Maya 主窗口" in result["reason"]
    # 没有真建菜单，就不能留下"已注册"的痕迹
    assert headless_host.menus == {}


def test_register_verifies_menu_actually_exists(fake_host):
    """宿主返回了菜单名却没真建（静默 no-op）时，不许报成功。"""
    fake_host.honor_menus = False
    result = menu.register_menu()
    assert result["ok"] is False
    assert "未真正创建菜单" in result["reason"]


def test_register_is_idempotent(fake_host):
    menu.register_menu()
    first_items = len(fake_host.items)
    menu.register_menu()
    # 第二次注册会先删两次（第一次不存在也算一次尝试），但**不会**让菜单项翻倍
    delete_calls = [c for c in fake_host.calls if c[0] == "deleteUI"]
    assert len(delete_calls) >= 1
    assert fake_host.menus[menu.MENU_NAME]["label"] == menu.MENU_LABEL
    # 每次注册都重建同样数量的菜单项，不会累积
    assert len(fake_host.items) == first_items * 2
    labels = [label for label, _parent in fake_host.items]
    assert labels.count(menu.MENU_ITEMS[0][0]) == 2


def test_unregister_removes_existing_menu(fake_host):
    menu.register_menu()
    result = menu.unregister_menu()
    assert result["ok"] is True
    assert result["removed"] is True
    assert menu.MENU_NAME not in fake_host.menus


def test_unregister_missing_menu_is_safe(fake_host):
    result = menu.unregister_menu()
    assert result["ok"] is True
    assert result["removed"] is False


def test_menu_item_commands_route_through_dispatch(fake_host):
    menu.register_menu()
    commands = [kwargs["command"] for name, label, kwargs in fake_host.calls
                if name == "menuItem" and "command" in kwargs]
    assert commands
    for command in commands:
        assert "_tak_menu.dispatch(" in command
        # 菜单字符串必须是一行合法 Python（Maya 的 -command 只吃字符串）
        ast.parse(command, feature_version=(3, 9))


# --------------------------------------------------------------------------- #
# 结构与脚本
# --------------------------------------------------------------------------- #
def test_menu_items_have_unique_actions_and_one_divider():
    labels = [label for label, _a, _h in menu.MENU_ITEMS]
    actions = [action for _l, action, _h in menu.MENU_ITEMS]
    assert sum(1 for label in labels if label is None) == 1
    assert len(set(actions)) == len(actions)
    assert all(action.startswith("tak_") for action in actions)
    for label, _action, hint in menu.MENU_ITEMS:
        if label is not None:
            assert label and hint


def test_menu_script_is_valid_and_self_contained():
    script = menu.menu_script()
    ast.parse(script, feature_version=(3, 9))
    assert "register_menu" in script
    assert menu._REPO_ROOT in script
    assert "sys.path" in script
    # 脚本本身不 import maya（它在 Maya 里执行时才需要宿主）
    assert "import maya" not in script


def test_menu_module_has_no_top_level_dcc_import():
    path = os.path.join(ROOT, "tak", "adapters", "maya", "menu.py")
    source = open(path, "r", encoding="utf-8").read()
    tree = ast.parse(source, feature_version=(3, 9))
    for node in tree.body:                      # 只看模块顶层
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] != "maya"
                       for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "maya"


def test_menu_items_view_matches_constant():
    view = menu.menu_items()
    assert len(view) == len(menu.MENU_ITEMS)
    assert view[0]["action"] == menu.MENU_ITEMS[0][1]
    assert any(item["label"] == "---" for item in view)
