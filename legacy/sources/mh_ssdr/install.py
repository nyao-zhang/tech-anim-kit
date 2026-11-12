# -*- coding: utf-8 -*-
"""
install.py —— MH-SSDR 工具安装器（Maya 2024 / Python 3）

用法（任选一种，推荐第 1 种）
----------------------------
1) 把本文件**直接拖进 Maya 视口**（或右键 >> 运行脚本）。安装完成后工具会自动弹出。
2) Maya 命令行 / Script Editor (Python) 里执行::

       import importlib.util as u
       s = u.spec_from_file_location("mh_ssdr_install", r"<本文件路径>")
       m = u.module_from_spec(s); s.loader.exec_module(m); m.main()

安装器只做三件事，全部可重复执行（幂等）
--------------------------------------
* 确认工具目录位置（工具已经放在 Maya 的 scripts 目录里时**不复制**，原地使用，
  这样你改代码立刻生效）
* 清掉早期版本遗留在 scripts/ 下的 ``mh_ssdr`` 包副本（它会抢在工具目录之前
  被 import，导致"改了代码却没变化"）
* 把工具目录注册进 sys.path（写入 ``scripts/userSetup.py`` 的一个受管区块，
  下次启动 Maya 也有效），并添加 Shelf 按钮 ``SSDR``

卸载：执行 ``uninstall()``
"""

import io
import os
import shutil
import sys

import maya.cmds as cmds

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
TOOL_DIR_NAME = "MH_SSDR_Tool"
PKG_NAME = "mh_ssdr"
# 注意：shelfButton 没有 -name 参数（Maya 2024 的 help 里确认过），
# 按钮名字由 Maya 自动生成，所以用 docTag 做身份标记，重复安装时按 tag 找到并删掉旧的。
SHELF_TAG = "mhSSDRButton"
SHELF_TAB = "MH_SSDR"
MARK_BEGIN = "# >>> MH-SSDR TOOL >>> 以下区块由 MH_SSDR_Tool/install.py 自动生成，重装会覆盖，请勿手改"
MARK_END = "# <<< MH-SSDR TOOL <<<"
NUMPY_VERSION = "1.26.4"


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def log(msg):
    print(u"[MH-SSDR] %s" % msg)


def tool_dir():
    """本文件所在目录（= 工具根目录，内含 mh_ssdr/）"""
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:                                  # 极端情况下 __file__ 不存在
        return os.getcwd()


def scripts_dir():
    try:
        d = cmds.internalVar(userScriptDir=True)
    except Exception:
        d = ""
    if not d:
        d = os.path.join(os.path.expanduser("~"), "Documents", "maya", "2024", "scripts")
    return os.path.normpath(d)


def _is_our_pkg(path):
    return os.path.isfile(os.path.join(path, "ssdr_core.py"))


def _same(a, b):
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 1. 定位 / 复制工具目录
# ---------------------------------------------------------------------------
def ensure_tool_dir():
    src = tool_dir()
    if not _is_our_pkg(os.path.join(src, PKG_NAME)):
        raise RuntimeError(u"安装器所在目录下找不到 %s 包：%s\n"
                           u"请把 install.py 和 mh_ssdr 文件夹放在同一个目录里。" % (PKG_NAME, src))

    target = os.path.join(scripts_dir(), TOOL_DIR_NAME)
    if _same(src, target):
        log(u"工具已在 Maya scripts 目录中，原地使用（改代码即时生效）：%s" % target)
        return target, False

    # 工具放在别处 —— 复制一份到 scripts 目录
    if os.path.isdir(target):
        if _is_our_pkg(os.path.join(target, PKG_NAME)):
            shutil.rmtree(target, ignore_errors=True)
        else:
            raise RuntimeError(u"目标目录已存在且不属于本工具，已中止：%s" % target)
    shutil.copytree(src, target,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".git"))
    log(u"已复制到：%s" % target)
    return target, True


# ---------------------------------------------------------------------------
# 2. 清理会"抢跑"的旧副本
# ---------------------------------------------------------------------------
def clean_legacy(target):
    """早期版本会把 mh_ssdr 直接复制到 scripts/ 下。scripts/ 常被 Maya 加入
    sys.path，那份副本会优先于工具目录被 import —— 必须删掉。"""
    legacy = os.path.join(scripts_dir(), PKG_NAME)
    if not os.path.isdir(legacy):
        return None
    if _same(legacy, os.path.join(target, PKG_NAME)):
        return None
    if not _is_our_pkg(legacy):
        log(u"⚠ scripts/%s 已存在但不属于本工具，跳过清理" % PKG_NAME)
        return None
    shutil.rmtree(legacy, ignore_errors=True)
    log(u"已清理旧版本副本：%s（它会遮蔽工具目录里的最新代码）" % legacy)
    return legacy


# ---------------------------------------------------------------------------
# 3. 注册 sys.path
# ---------------------------------------------------------------------------
def register_path(target):
    _purge_modules()
    p = os.path.normpath(target)
    # 放到最前面，确保优先于任何同名残留
    while p in sys.path:
        sys.path.remove(p)
    sys.path.insert(0, p)
    return p


def _purge_modules():
    for name in [k for k in list(sys.modules)
                 if k == PKG_NAME or k.startswith(PKG_NAME + ".")]:
        sys.modules.pop(name, None)


def write_user_setup(target):
    """写入/更新 scripts/userSetup.py 中的受管区块（幂等）"""
    path = os.path.join(scripts_dir(), "userSetup.py")
    block = (
        MARK_BEGIN + "\n"
        "try:\n"
        "    import sys as _mh_sys\n"
        "    _MH_SSDR_PATH = r\"%s\"\n"
        "    if _MH_SSDR_PATH not in _mh_sys.path:\n"
        "        _mh_sys.path.insert(0, _MH_SSDR_PATH)\n"
        "    del _mh_sys\n"
        "except Exception:\n"
        "    pass\n"
        "%s\n" % (os.path.normpath(target), MARK_END)
    )

    if os.path.isfile(path):
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        if MARK_BEGIN in text and MARK_END in text:
            i = text.index(MARK_BEGIN)
            j = text.index(MARK_END) + len(MARK_END)
            new_text = text[:i] + block.rstrip("\n") + text[j:]
        else:
            sep = "" if text.endswith("\n") else "\n"
            new_text = text + sep + "\n" + block
        if new_text == text:
            log(u"userSetup.py 已是最新：%s" % path)
            return path
        bak = path + ".mhssdr.bak"
        if not os.path.isfile(bak):
            shutil.copyfile(path, bak)
            log(u"已备份原 userSetup.py -> %s" % bak)
        with io.open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
        log(u"已更新 userSetup.py：%s" % path)
    else:
        with io.open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(u"# -*- coding: utf-8 -*-\n# Maya 用户启动脚本（由 MH-SSDR 安装器创建）\n\n" + block)
        log(u"已创建 userSetup.py：%s" % path)
    return path


# ---------------------------------------------------------------------------
# 4. 启动代码 & Shelf 按钮
# ---------------------------------------------------------------------------
def launch_code(target):
    return (
        "# MH-SSDR launcher\n"
        "import sys\n"
        "_MH_P = r\"%s\"\n"
        "while _MH_P in sys.path:\n"
        "    sys.path.remove(_MH_P)\n"
        "sys.path.insert(0, _MH_P)\n"
        "for _k in [k for k in list(sys.modules) if k == 'mh_ssdr' or k.startswith('mh_ssdr.')]:\n"
        "    sys.modules.pop(_k, None)\n"
        "try:\n"
        "    import numpy\n"
        "except ImportError:\n"
        "    import maya.cmds as _c, maya.mel as _m\n"
        "    _c.confirmDialog(title='MH-SSDR', message=u'Maya 的 Python 缺少 numpy。\\n\\n'\n"
        "        u'请在命令行执行：\\n'\n"
        "        u'mayapy -m pip install --user numpy==%s', button=['确定'])\n"
        "    raise\n"
        "import mh_ssdr.tool_ui as _mh_ui\n"
        "_mh_ui.show()\n" % (os.path.normpath(target), NUMPY_VERSION)
    )


def _short(node):
    return str(node).split("|")[-1]


def shelf_top():
    """当前 Maya 的 Shelf 顶层布局名；没有 Shelf（批处理 / 无界面）时返回 None"""
    try:
        import maya.mel as mel
        top = mel.eval("$tmpVar=$gShelfTopLevel")
    except Exception:
        return None
    return top or None


def shelf_tabs(top):
    try:
        return list(cmds.shelfTabLayout(top, query=True, childArray=True) or [])
    except Exception:
        return []


def _layout_children(layout):
    try:
        return list(cmds.shelfLayout(layout, query=True, childArray=True) or [])
    except Exception:
        return []


def _is_our_button(btn):
    """按 docTag 认自己的按钮（对非按钮子控件查询会报错，直接吞掉）"""
    try:
        return cmds.shelfButton(btn, query=True, docTag=True) == SHELF_TAG
    except Exception:
        return False


def remove_shelf_buttons(verbose=False):
    """删掉所有 Shelf 标签页里属于本工具的按钮，返回删除数量"""
    top = shelf_top()
    if not top:
        return 0
    removed = []
    for tab in shelf_tabs(top):
        for child in _layout_children(tab):
            if not _is_our_button(child):
                continue
            try:
                cmds.deleteUI(child)
                removed.append(child)
            except Exception:
                pass
    if removed and verbose:
        log(u"已移除旧的 Shelf 按钮 %d 个" % len(removed))
    return len(removed)


def add_shelf_button(target):
    top = shelf_top()
    if not top:
        log(u"⚠ 当前环境没有 Shelf（批处理 / 无界面模式），跳过 Shelf 按钮；"
            u"工具本身不受影响")
        return None

    tabs = shelf_tabs(top)
    remove_shelf_buttons(verbose=True)

    # 目标标签页：当前页 -> Custom -> 已有的 MH_SSDR -> 新建一个
    try:
        current = _short(cmds.tabLayout(top, query=True, selectTab=True) or "")
    except Exception:
        current = ""
    pick, created = None, False
    for cand in (current, "Custom", SHELF_TAB):
        if not cand:
            continue
        for t in tabs:
            if _short(t) == cand:
                pick = t
                break
        if pick:
            break
    if pick is None:
        try:
            pick = cmds.shelfLayout(SHELF_TAB, parent=top)
            created = True
        except Exception as exc:
            log(u"⚠ 新建 Shelf 标签页失败：%s" % exc)
            return None
        pick = pick or SHELF_TAB
        tabs = shelf_tabs(top)

    kwargs = dict(
        parent=pick,
        docTag=SHELF_TAG,
        label="SSDR",
        annotation=u"MH-SSDR：雕刻模型 -> 反求面部骨骼偏移\n（MetaHuman LOD0 面部骨骼）",
        image="pythonFamily.png",
        imageOverlayLabel="SSDR",
        overlayLabelColor=(1.0, 0.78, 0.25),
        overlayLabelBackColor=(0.0, 0.0, 0.0, 0.0),
        sourceType="python",
        command=launch_code(target),
        width=45,
        height=45,
    )
    btn = None
    try:
        btn = cmds.shelfButton(**kwargs)
    except TypeError as exc:
        # 某个装饰性参数在当前 Maya 版本不可用：退化成最朴素的按钮，别让安装失败
        log(u"⚠ 带样式的 Shelf 按钮创建失败（%s），改用简化样式重试" % exc)
        try:
            btn = cmds.shelfButton(parent=pick, docTag=SHELF_TAG, label="SSDR",
                                   sourceType="python", command=launch_code(target))
        except Exception as exc2:
            log(u"⚠ Shelf 按钮创建失败：%s" % exc2)
            return None

    log(u"已添加 Shelf 按钮 SSDR（标签页：%s）" % _short(pick))
    if created:
        try:
            cmds.tabLayout(top, edit=True, selectTab=_short(pick))
        except Exception:
            pass
    return btn


# ---------------------------------------------------------------------------
# 5. numpy 检查
# ---------------------------------------------------------------------------
def check_numpy():
    try:
        import numpy
        return numpy.__version__
    except ImportError:
        return None


def is_batch():
    """批处理/mayapy 里不能建 Qt 窗口（会自动启动工具时用到）"""
    try:
        return bool(cmds.about(batch=True))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main(auto_launch=True):
    log(u"安装器目录：%s" % tool_dir())

    version = check_numpy()
    if version is None:
        log(u"✗ 当前 Maya 的 Python 没有 numpy（本工具的核心算法依赖它）")
        log(u"  请关闭 Maya 后，在系统命令行执行：")
        log(u"  \"%s\" -m pip install --user numpy==%s"
            % (os.path.join(os.path.dirname(sys.executable), "mayapy.exe"), NUMPY_VERSION))
        log(u"  或直接在 Maya 的 Script Editor 执行：")
        log(u"  import subprocess, sys; subprocess.check_call([%r, '-m', 'pip', 'install', '--user', 'numpy==%s'])"
            % (sys.executable, NUMPY_VERSION))
        try:
            cmds.confirmDialog(
                title="MH-SSDR",
                message=u"Maya 的 Python 缺少 numpy，工具无法运行。\n\n"
                        u"在 Script Editor (Python) 里执行下面这行，装完重启 Maya：\n\n"
                        u"import subprocess, sys; subprocess.check_call([sys.executable, "
                        u"'-m', 'pip', 'install', '--user', 'numpy'])\n\n"
                        u"详情见 Script Editor 输出。",
                button=["确定"])
        except Exception:
            pass
    else:
        log(u"numpy %s ✓" % version)

    target, copied = ensure_tool_dir()

    # 每一步互相隔离：任何一步失败都只记警告，不让整个安装崩掉
    steps = [
        (u"清理旧版本副本", lambda: clean_legacy(target)),
        (u"注册 sys.path", lambda: register_path(target)),
        (u"写入 userSetup.py", lambda: write_user_setup(target)),
        (u"添加 Shelf 按钮", lambda: add_shelf_button(target)),
    ]
    for title, fn in steps:
        try:
            fn()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            log(u"⚠ %s 失败（不影响工具本身）：%s" % (title, exc))

    # 确认 import 真的指向工具目录（早期版本踩过"旧副本抢先接管"的坑）
    try:
        _purge_modules()
        import mh_ssdr
        got = os.path.dirname(os.path.abspath(mh_ssdr.__file__))
        if not _same(got, os.path.join(target, PKG_NAME)):
            log(u"⚠ 注意：import 到的 mh_ssdr 来自 %s，不是 %s" % (got, target))
    except Exception as exc:
        log(u"⚠ 导入检查失败：%s" % exc)

    log(u"安装完成。")
    log(u"工具目录：%s" % target)
    log(u"如果窗口没弹出，点 Shelf 上的 SSDR 按钮，或执行：")
    log(u"    from mh_ssdr.tool_ui import show; show()")

    if auto_launch and version is not None and not is_batch():
        try:
            import mh_ssdr.tool_ui as ui
            ui.show()
            log(u"工具窗口已打开。")
        except Exception as exc:
            import traceback
            traceback.print_exc()
            log(u"⚠ 启动失败：%s" % exc)

    try:
        cmds.inViewMessage(
            amg=u"<hl>MH-SSDR</hl> 安装完成" + (u"" if version else u"（缺 numpy，见 Script Editor）"),
            pos="midCenter", fade=True, fadeOutTime=2)
    except Exception:
        pass
    return target


def uninstall():
    """移除 Shelf 按钮、userSetup 区块与旧副本（不删除工具目录本身）"""
    try:
        n = remove_shelf_buttons(verbose=True)
        if n == 0:
            log(u"没有找到本工具的 Shelf 按钮")
    except Exception as exc:
        log(u"删除 Shelf 按钮失败：%s" % exc)

    path = os.path.join(scripts_dir(), "userSetup.py")
    if os.path.isfile(path):
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        if MARK_BEGIN in text and MARK_END in text:
            i = text.index(MARK_BEGIN)
            j = text.index(MARK_END) + len(MARK_END)
            with io.open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write((text[:i].rstrip("\n") + "\n" + text[j:].lstrip("\n")).strip("\n") + "\n")
            log(u"已从 userSetup.py 移除路径注册")

    clean_legacy(tool_dir())
    _purge_modules()
    log(u"卸载完成（工具目录仍在：%s）" % tool_dir())


# executeDroppedPythonFile 的约定入口（拖拽 .py 到视口时 Maya 调用它）
def onMayaDroppedPythonFile(*args):
    main()


if __name__ == "__main__":
    main()
