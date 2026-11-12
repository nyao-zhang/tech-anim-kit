# -*- coding: utf-8 -*-
"""
界面自引用审计：找出 ``self.xxx`` 里指向不存在成员的引用。

为什么需要它
------------
Maya 的批处理模式（mayapy）**连一个普通的 ``QWidget()`` 都会让进程静默退出**，
所以窗口没法在无界面环境里构造 —— 也就是说 ``SSDRWindow()`` 里任何拼错的
控价名（``self.lblSourc``）、写错的方法名（``self._on_extrat``）、
连到不存在的槽函数，都只会在**用户第一次打开窗口时**才炸。

这个测试纯静态：把每个 ``self.<name>`` 读出来，跟「赋值过的属性 + 类上的方法
+ 基类（Qt）成员」比对，剩下对不上的就是可疑引用。

跑法::

    mayapy tests/test_ui_refs.py
"""

import ast
import io
import os
import sys

import maya.standalone
maya.standalone.initialize(name="python")

TOOL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOL)

import mh_ssdr.tool_ui as ui          # noqa: E402

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(TOOL, "mh_ssdr", "tool_ui.py")
TARGETS = ["SSDRController", "SSDRWindow"]


def _self_attrs(node):
    """遍历某方法体，收集 (self 上的赋值名集合, self 上读取的 (名字, 行号) 列表)"""
    assigned, reads = set(), []
    for sub in ast.walk(node):
        # 赋值目标：self.x = ...
        targets = []
        if isinstance(sub, ast.Assign):
            targets = sub.targets
        elif isinstance(sub, (ast.AugAssign, ast.AnnAssign)):
            targets = [sub.target]
        for t in targets:
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) \
                    and t.value.id == "self":
                assigned.add(t.attr)
        # 读取：self.x
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) \
                and sub.value.id == "self":
            reads.append((sub.attr, getattr(sub, "lineno", 0)))
    return assigned, reads


def audit_class(path, cls_name):
    with io.open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)

    cls_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            cls_node = node
            break
    if cls_node is None:
        return None, [(0, u"找不到类 %s" % cls_name)]

    assigned, reads = set(), []
    for item in cls_node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a, r = _self_attrs(item)
            assigned |= a
            reads.extend(r)

    cls_obj = getattr(ui, cls_name, None)
    problems = []
    for name, lineno in reads:
        if name in assigned:
            continue
        if cls_obj is not None and hasattr(cls_obj, name):
            continue                       # 方法 / 基类（Qt）成员
        problems.append((lineno, u"self.%s 从未赋值，也不在类或基类上" % name))

    # 去重（同一名字可能在多处出现）
    seen, uniq = set(), []
    for lineno, msg in problems:
        if msg in seen:
            continue
        seen.add(msg)
        uniq.append((lineno, msg))
    return cls_node, uniq


def main():
    print(u"== 界面自引用审计：self.xxx 是否都真实存在 ==")
    total = 0
    for cls_name in TARGETS:
        node, problems = audit_class(SRC, cls_name)
        n_methods = len([b for b in (node.body if node else [])
                         if isinstance(b, ast.FunctionDef)])
        if problems:
            total += len(problems)
            print(u"  ✗ %s（%d 个方法）" % (cls_name, n_methods))
            for lineno, msg in problems[:20]:
                print(u"      第 %d 行  %s" % (lineno, msg))
        else:
            print(u"  ✓ %s（%d 个方法）全部引用有效" % (cls_name, n_methods))
    if total:
        print(u"结果: %d 处可疑引用 ✗" % total)
        return 1
    print(u"结果: 全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
