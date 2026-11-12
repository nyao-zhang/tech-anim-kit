# -*- coding: utf-8 -*-
"""
mh_ssdr_run.py —— MH-SSDR 直启脚本（**不安装**：不复制文件、不写 userSetup、不加 Shelf）

用法（三选一，推荐第 1 种）
--------------------------
1. 把这个文件**拖进 Maya 视口** → 窗口直接弹出。

2. Script Editor（Python）里粘贴这两行::

       import sys; sys.path.insert(0, r"C:/Users/NYAO/Documents/maya/2024/zh_CN/scripts/MH_SSDR_Tool")
       import mh_ssdr_run; mh_ssdr_run.launch()

   想做成 Shelf 按钮的话，就把这两行存成一个按钮的命令即可。

3. 已经在该目录下时::

       import mh_ssdr_run; mh_ssdr_run.launch()

要点
----
* Maya 用 ``import`` 加载模块并且会**缓存**：改完代码再跑，如果只是重新 import，
  执行的还是旧版本（会看到莫名其妙的旧行号报错）。``launch()`` 每次都会先把
  ``mh_ssdr*`` 从 ``sys.modules`` 里清掉，所以你看到的永远是最新代码。
* 工具目录以本文件所在目录为准；拖拽和 exec 都能拿到 ``__file__``，
  拿不到时退回下面写死的 ``TOOL_DIR``（工具挪位置就改这一行）。
"""

from __future__ import print_function

# 默认工具目录（当 __file__ 不可用时使用）
TOOL_DIR = r"C:/Users/NYAO/Documents/maya/2024/zh_CN/scripts/MH_SSDR_Tool"

PKG = "mh_ssdr"


def tool_dir():
    """本文件所在目录（= 工具根目录，里面应该有 mh_ssdr/）"""
    import os
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return TOOL_DIR


def purge_modules():
    """丢掉已缓存的 mh_ssdr*，让下次 import 重新读盘（改代码即时生效）"""
    import sys
    dropped = []
    for name in [k for k in list(sys.modules) if k == PKG or k.startswith(PKG + ".")]:
        sys.modules.pop(name, None)
        dropped.append(name)
    return dropped


def _warn_no_numpy():
    msg = (u"Maya 的 Python 缺少 numpy，工具无法运行。\n\n"
           u"请在系统命令行执行：\n\n"
           u'"C:/Program Files/Autodesk/Maya2024/bin/mayapy.exe" '
           u"-m pip install --user numpy==1.26.4\n\n"
           u"装完重启 Maya。")
    print("[MH-SSDR] " + msg.replace("\n", "\n[MH-SSDR] "))
    try:
        from maya import cmds
        cmds.confirmDialog(title="MH-SSDR", message=msg, button=[u"确定"])
    except Exception:
        pass


def launch(tool_dir_path=None, verbose=True):
    """打开工具窗口，返回窗口对象"""
    import os
    import sys

    here = tool_dir_path or tool_dir()
    if not os.path.isdir(os.path.join(here, PKG)):
        raise RuntimeError(u"在 %s 下找不到 %s 包。\n"
                           u"请把 mh_ssdr_run.py 里的 TOOL_DIR 改成工具实际所在目录。"
                           % (here, PKG))

    # 放到 sys.path 最前面，避免同名旧副本抢先
    if here in sys.path:
        sys.path.remove(here)
    sys.path.insert(0, here)
    purge_modules()

    try:
        import numpy  # noqa: F401
    except ImportError:
        _warn_no_numpy()
        raise

    import mh_ssdr.tool_ui as ui
    window = ui.show()

    if verbose:
        print(u"[MH-SSDR] 工具目录：%s" % here)
        print(u"[MH-SSDR] 代码来源：%s" % ui.__file__)
        print(u"[MH-SSDR] 窗口已打开（关掉重开即可加载最新代码）")
    return window


# Maya 把 .py 拖进视口时调用的入口
def onMayaDroppedPythonFile(*args):
    launch()


if __name__ == "__main__":
    launch()
