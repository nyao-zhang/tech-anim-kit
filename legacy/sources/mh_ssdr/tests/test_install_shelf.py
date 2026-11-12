# -*- coding: utf-8 -*-
"""
安装器 Shelf 逻辑测试（不需要 Maya 界面）。

为什么需要
----------
Maya 在批处理模式下**不执行也不校验 UI 命令**（全部返回 False），
所以 ``add_shelf_button`` 这条路径用 mayapy 是测不出来的 ——
``cmds.shelfButton(name=...)`` 那个 bug 就是这样漏到用户机器上的。

这里用一个极小的 ``cmds`` 替身（stub）把 Shelf 的真实结构（顶层布局 ->
标签页 -> 按钮）模拟出来，然后验证安装逻辑本身：

* 第一次安装会新建按钮
* 重复安装**不会**产生重复按钮（按 docTag 认亲）
* 不会误删别人的按钮
* 没有 Shelf 时优雅跳过、不抛异常
* 装饰性参数不被支持时能退化重试

跑法::

    mayapy tests/test_install_shelf.py
"""

import importlib.util
import os
import sys

TOOL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL_PATH = os.path.join(TOOL, "install.py")


# ---------------------------------------------------------------------------
# cmds 替身
# ---------------------------------------------------------------------------
class FakeCmds(object):
    def __init__(self, tabs=("Custom", "Animation"), current="Custom"):
        self.top = "shelfTopLevel1"
        self.tabs = list(tabs)                 # 每个标签页是 layout 名
        self.children = {t: [] for t in self.tabs}
        self.tags = {}                         # 控件名 -> docTag
        self.deleted = []
        self._seq = 0
        self._current = current
        self.reject_style = False              # True 时带样式的创建会抛 TypeError

    # -- 内部 ---------------------------------------------------------------
    def _new_name(self, prefix):
        self._seq += 1
        return "%s%d" % (prefix, self._seq)

    # -- 查询 ---------------------------------------------------------------
    def shelfTabLayout(self, node, query=False, childArray=False, **kw):
        assert query and childArray, "测试只支持 query childArray"
        assert node == self.top, "意外的顶层布局: %s" % node
        return list(self.tabs)

    def tabLayout(self, node, query=False, selectTab=None, edit=False, **kw):
        if edit:
            self._current = selectTab
            return None
        return self._current

    def shelfLayout(self, node, query=False, childArray=False, parent=None, **kw):
        if query:
            assert childArray
            return list(self.children.get(node, []))
        # 新建标签页
        self.tabs.append(node)
        self.children[node] = []
        return node

    def shelfButton(self, node=None, query=False, docTag=None, parent=None, **kw):
        if node is not None:                       # query 模式
            assert query and docTag is True, "只支持查询 docTag"
            if node not in self.tags:
                raise RuntimeError("不是本工具创建的 shelfButton: %s" % node)
            return self.tags[node]
        # 创建模式
        if self.reject_style and ("image" in kw or "annotation" in kw):
            raise TypeError(u"标志“image”无效")
        if parent not in self.children:
            raise RuntimeError("parent 不存在: %s" % parent)
        name = self._new_name("shelfButton")
        self.children[parent].append(name)
        # docTag 可能被具名参数接收，也可能留在 **kw 里
        self.tags[name] = docTag if docTag is not None else kw.get("docTag")
        return name

    def deleteUI(self, node):
        self.deleted.append(node)
        self.tags.pop(node, None)
        for kids in self.children.values():
            if node in kids:
                kids.remove(node)

    # 其他命令一律当成功
    def __getattr__(self, item):
        def _noop(*a, **k):
            return None
        return _noop


class FakeMel(object):
    def __init__(self, top="shelfTopLevel1"):
        self.top = top

    def eval(self, code):
        return self.top


# ---------------------------------------------------------------------------
def load_install(cmds_stub=None, mel_stub=None):
    """加载 install.py 并把 cmds / mel 换成替身"""
    import types
    pkg_cmds = types.ModuleType("maya.cmds")
    pkg_mel = types.ModuleType("maya.mel")
    if cmds_stub is not None:
        pkg_cmds = cmds_stub
    if mel_stub is not None:
        pkg_mel = mel_stub
    maya_pkg = sys.modules.get("maya") or types.ModuleType("maya")
    sys.modules["maya"] = maya_pkg
    sys.modules["maya.cmds"] = pkg_cmds
    sys.modules["maya.mel"] = pkg_mel

    spec = importlib.util.spec_from_file_location("mh_ssdr_install_ut", INSTALL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if cmds_stub is not None:
        mod.cmds = cmds_stub
    if mel_stub is not None:
        # install 里的 shelf_top 用 catch 包着 mel.eval
        mod.shelf_top = lambda: mel_stub.eval("$tmpVar=$gShelfTopLevel") or None
    return mod


def _buttons(mod, fake, tab):
    return [c for c in fake.children[tab] if fake.tags.get(c) == mod.SHELF_TAG]


def test_first_install():
    fake = FakeCmds()
    mod = load_install(fake, FakeMel())
    btn = mod.add_shelf_button(TOOL)
    assert btn, "应创建按钮"
    assert len(_buttons(mod, fake, "Custom")) == 1, fake.children
    assert fake.tags[btn] == mod.SHELF_TAG
    print(u"  ✓ 首次安装创建按钮（标签页 Custom）")


def test_idempotent():
    fake = FakeCmds()
    mod = load_install(fake, FakeMel())
    for _ in range(3):
        mod.add_shelf_button(TOOL)
    n = len(_buttons(mod, fake, "Custom"))
    assert n == 1, u"重复安装后按钮数应为 1，实际 %d" % n
    print(u"  ✓ 重复安装 3 次仍只有 1 个按钮")


def test_keeps_foreign_buttons():
    fake = FakeCmds()
    fake.children["Custom"].append("someUserButton")
    fake.tags["someUserButton"] = "userTag"
    fake.children["Custom"].append("otherToolBtn")
    fake.tags["otherToolBtn"] = None
    mod = load_install(fake, FakeMel())
    mod.add_shelf_button(TOOL)
    assert "someUserButton" not in fake.deleted, u"误删了别人的按钮"
    assert "otherToolBtn" not in fake.deleted, u"误删了别人的按钮"
    assert "someUserButton" in fake.children["Custom"]
    print(u"  ✓ 不触碰其他人的 Shelf 按钮")


def test_moves_between_tabs():
    """按钮在旧标签页时，重新安装应搬走，而不是留下两份"""
    fake = FakeCmds(tabs=("Animation",), current="Animation")
    mod = load_install(fake, FakeMel())
    mod.add_shelf_button(TOOL)
    assert len(_buttons(mod, fake, "Animation")) == 1
    # 用户切到 Custom 再装一次（Custom 这时还不存在 -> 走新建标签页分支）
    fake._current = "Custom"
    mod.add_shelf_button(TOOL)
    assert len(_buttons(mod, fake, "Animation")) == 0, u"旧标签页应被清理"
    total = sum(len(_buttons(mod, fake, t)) for t in fake.children)
    assert total == 1, u"全局应只有 1 个按钮，实际 %d" % total
    print(u"  ✓ 换标签页后只保留一份按钮")


def test_no_shelf():
    fake = FakeCmds()
    mod = load_install(fake, FakeMel(top=""))     # 批处理：没有 $gShelfTopLevel
    assert mod.add_shelf_button(TOOL) is None, u"没有 Shelf 时应返回 None"
    print(u"  ✓ 没有 Shelf 时优雅跳过")


def test_style_fallback():
    fake = FakeCmds()
    fake.reject_style = True
    mod = load_install(fake, FakeMel())
    btn = mod.add_shelf_button(TOOL)
    assert btn, u"样式参数失败后应退化重试并成功"
    assert len(_buttons(mod, fake, "Custom")) == 1
    print(u"  ✓ 装饰参数不被支持时退化重试")


def test_uninstall():
    fake = FakeCmds()
    fake.children["Custom"].append("userBtn")
    fake.tags["userBtn"] = "userTag"
    mod = load_install(fake, FakeMel())
    mod.add_shelf_button(TOOL)
    n = mod.remove_shelf_buttons()
    assert n == 1, u"应删除 1 个按钮，实际 %d" % n
    assert "userBtn" in fake.children["Custom"], u"误删了别人的按钮"
    print(u"  ✓ 卸载只删自己的按钮")


def main():
    print(u"== 安装器 Shelf 逻辑测试（cmds 替身）==")
    tests = [test_first_install, test_idempotent, test_keeps_foreign_buttons,
             test_moves_between_tabs, test_no_shelf, test_style_fallback,
             test_uninstall]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as exc:
            failed += 1
            print(u"  ✗ %s: %s" % (t.__name__, exc))
        except Exception as exc:
            failed += 1
            print(u"  ✗ %s: %s: %s" % (t.__name__, type(exc).__name__, exc))
    print(u"结果: %d/%d 通过" % (len(tests) - failed, len(tests)))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
