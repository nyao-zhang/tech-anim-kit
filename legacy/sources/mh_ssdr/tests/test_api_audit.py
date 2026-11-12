# -*- coding: utf-8 -*-
"""
test_api_audit.py —— 静态审计：源码里用到的 Maya / Qt API 是否真的存在

用法::

    "C:/Program Files/Autodesk/Maya2024/bin/mayapy.exe" tests/test_api_audit.py

为什么需要它：像 ``cmds.dgEvaluate`` 这种名字在 MEL 里有、在 maya.cmds 里**没有**。
写错了在批处理下可能被 try/except 吞掉，或者只在点按钮时才抛异常，
静态审计能在几秒内把这类"只在特定路径下才炸"的 API 全揪出来。
"""

from __future__ import print_function

import os
import re
import sys

import maya.standalone

maya.standalone.initialize(name="python")

import maya.cmds as cmds                                    # noqa: E402
import maya.api.OpenMaya as om                              # noqa: E402
import maya.api.OpenMayaAnim as oma                         # noqa: E402
from PySide2 import QtCore, QtGui, QtWidgets                # noqa: E402

TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES = {"cmds": cmds, "om": om, "oma": oma,
           "QtWidgets": QtWidgets, "QtCore": QtCore, "QtGui": QtGui}

# 只出现在注释/文档里的名字不算
DOC_ONLY = {("cmds", "dgEvaluate")}


def sources():
    for folder in (TOOL_DIR, os.path.join(TOOL_DIR, "mh_ssdr")):
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.endswith(".py"):
                path = os.path.join(folder, name)
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    yield name, f.read()


def main():
    bad = []
    checked = 0
    for filename, text in sources():
        # 去掉注释与文档字符串，避免把说明文字当成调用
        code = re.sub(r'"""(?:.|\n)*?"""', "", text)
        code = re.sub(r"'''(?:.|\n)*?'''", "", code)
        code = re.sub(r"#[^\n]*", "", code)
        for alias, module in MODULES.items():
            for name in set(re.findall(r"\b%s\.([A-Za-z_]\w*)" % re.escape(alias), code)):
                checked += 1
                if (alias, name) in DOC_ONLY:
                    continue
                if not hasattr(module, name):
                    bad.append((filename, alias, name))

    print("共检查 %d 处 API 引用" % checked)
    if bad:
        print(u"❌ 以下 API 在 Maya 2024 里不存在：")
        for filename, alias, name in sorted(set(bad)):
            print("   %-16s %s.%s" % (filename, alias, name))
        return 1
    print(u"✅ 全部存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())
