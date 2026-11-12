# -*- coding: utf-8 -*-
"""
============================================================
 Weight Joints Tool  -  获取模型的所有权重骨骼
 适用: Maya 2024 (Python 3 / OpenMaya API 2.0)
------------------------------------------------------------
 用法 1 (UI, 推荐):
     A. 把本 .py 文件直接拖进 Maya 脚本编辑器窗口 -> 按 Ctrl+Enter(小键盘 Enter) 执行,
        会自动弹出工具窗口。(这就是本文件末尾那段判断的作用)
     B. 或者放到  C:/Users/<你>/Documents/maya/2024/scripts/  后在
        Maya 脚本编辑器 (Python) 执行:
             import maya_weight_joints_tool as w
             w.show()

 用法 2 (命令行):
     import maya_weight_joints_tool as w
     shape, skin, rows = w.get_weight_joints('pSphere1')
     for name, maxw in rows:
         print(name, maxw)

 功能:
     - 列出 skinCluster 中全部 influence (可只看 joint)
     - 精确模式: 扫描全部顶点权重, 统计每个骨骼的最大权重
     - 过滤零权重骨骼 / 清理无用 influence
     - 一键选择骨骼、复制名称、导出 TXT / JSON
============================================================
"""

import json
import os

import maya.cmds as cmds
from maya.api import OpenMaya as om
from maya.api import OpenMayaAnim as oma

WIN_NAME = "wbWeightJointsToolWin"
_ZERO_TOL = 1e-5
_GROUP_TAG = "### "          # 列表中的分组行前缀
_STATE = {"mesh": None, "skin": None, "rows": []}


# ------------------------------------------------------------------
#  底层查询
# ------------------------------------------------------------------
def _short(node):
    """返回最短可读名, 长名不可用时回退。"""
    try:
        return cmds.ls(node, shortNames=True)[0] if node else node
    except Exception:
        return node


def _node_type(node):
    try:
        return cmds.nodeType(node)
    except Exception:
        # 名称可能带命名空间或已被重命名, 用长名再试
        try:
            full = cmds.ls(node, long=True)
            return cmds.nodeType(full[0]) if full else ""
        except Exception:
            return ""


def find_skin_cluster(mesh):
    """返回 (shape, skinCluster)。没蒙皮时 skin 为 None。"""
    if not cmds.objExists(mesh):
        raise RuntimeError(u"对象不存在: %s" % mesh)

    if cmds.nodeType(mesh) == "mesh":
        shape = mesh
    else:
        shapes = cmds.listRelatives(mesh, s=True, ni=True, type="mesh", f=True) or []
        shape = shapes[0] if shapes else None

    if not shape:
        return None, None

    skin = None
    try:
        skin = cmds.findRelatedSkinCluster(shape)
    except Exception:
        skin = None

    if not skin:
        hist = cmds.listHistory(shape, pruneDagObjects=True, interestLevel=2) or []
        skins = cmds.ls(hist, type="skinCluster") or []
        skin = skins[-1] if skins else None

    return shape, skin


def _influence_names(skin):
    """通过 API 取 influence 列表, 并按权重索引排序。"""
    sel = om.MSelectionList()
    sel.add(skin)
    fn = oma.MFnSkinCluster(sel.getDependNode(0))

    idx_to_name = {}
    for path in fn.influenceObjects():
        try:
            idx = fn.indexForInfluenceObject(path)
        except Exception:
            idx = len(idx_to_name)
        idx_to_name[idx] = path.partialPathName()

    return [idx_to_name[i] for i in sorted(idx_to_name)]


def _max_weights(shape, skin, names):
    """扫描整个 mesh 的权重, 返回每个 influence 的最大权重。"""
    sel = om.MSelectionList()
    sel.add(skin)
    fn = oma.MFnSkinCluster(sel.getDependNode(0))

    sel2 = om.MSelectionList()
    sel2.add(shape)
    dag = sel2.getDagPath(0)

    weights, count = fn.getWeights(dag, om.MObject())
    wl = list(weights)
    n = len(names)

    maxs = []
    for i in range(n):
        col = wl[i::n]
        maxs.append(max(col) if col else 0.0)
    return maxs


def get_weight_joints(mesh, joints_only=True, skip_zero=True, precise=True):
    """
    获取 mesh 的权重骨骼。

    :param mesh:        transform 或 shape 名
    :param joints_only: True 时只保留 joint 类型的 influence
    :param skip_zero:   True 时剔除最大权重为 0 的骨骼(需 precise)
    :param precise:     True 时扫描顶点权重统计最大权重, False 时只列名(快)
    :return: (shape, skin, [(name, maxWeight or None), ...])
    """
    shape, skin = find_skin_cluster(mesh)
    if not shape:
        raise RuntimeError(u"%s 上没有找到 mesh" % mesh)
    if not skin:
        raise RuntimeError(u"%s 没有蒙皮 (找不到 skinCluster)" % mesh)

    names = _influence_names(skin)
    if precise and names:
        try:
            maxs = _max_weights(shape, skin, names)
        except Exception:
            maxs = [None] * len(names)
    else:
        maxs = [None] * len(names)

    rows = []
    for name, mw in zip(names, maxs):
        if joints_only and _node_type(name) != "joint":
            continue
        if skip_zero and precise and mw is not None and mw <= _ZERO_TOL:
            continue
        rows.append((name, mw))

    return shape, skin, rows


def targets_from_selection():
    """从当前选择解析出 mesh shape 列表(支持组件选择)。"""
    sel = cmds.ls(sl=True, l=True) or []
    out = set()
    for item in sel:
        if "." in item:
            item = item.split(".")[0]
        if cmds.nodeType(item) == "mesh":
            out.add(item)
            continue
        shapes = cmds.listRelatives(item, s=True, ni=True, type="mesh", f=True) or []
        out.update(shapes)
    return sorted(out)


# ------------------------------------------------------------------
#  UI
# ------------------------------------------------------------------
def _fmt_row(index, name, mw):
    left = u"%3d  %s" % (index, name)
    if mw is None:
        return left
    return u"%s  max=%0.4f" % (left, mw)


def _group_line(shape, skin, total):
    return u"%s%s   [%s]   joints: %d" % (_GROUP_TAG, _short(shape), _short(skin), total)


def refresh(*args):
    """刷新列表。"""
    if not cmds.textScrollList("wbWJ_list", exists=True):
        return

    meshes = targets_from_selection()
    if not meshes:
        cmds.textScrollList("wbWJ_list", e=True, removeAll=True)
        cmds.text("wbWJ_status", e=True, label=u"请先在场景中选中模型(或模型上的点/线/面)")
        _STATE.update({"mesh": None, "skin": None, "rows": [], "items": []})
        return

    joints_only = cmds.checkBox("wbWJ_opt_jointOnly", q=True, v=True)
    skip_zero = cmds.checkBox("wbWJ_opt_skipZero", q=True, v=True)
    precise = cmds.checkBox("wbWJ_opt_precise", q=True, v=True)

    items = []
    rows_out = []
    total_all = 0
    _STATE["skin"] = None

    for mesh in meshes:
        try:
            shape, skin, rows = get_weight_joints(
                mesh, joints_only=joints_only, skip_zero=skip_zero, precise=precise
            )
        except RuntimeError as err:
            items.append(u"%s< %s >" % (_GROUP_TAG, err))
            items.append(u" ")
            continue

        if _STATE.get("skin") in (None, ""):
            _STATE["skin"] = skin

        items.append(_group_line(shape, skin, len(rows)))
        for i, (name, mw) in enumerate(rows, 1):
            items.append(_fmt_row(i, name, mw))
            rows_out.append(name)
        items.append(u" ")
        total_all += len(rows)

    _STATE["mesh"] = meshes[0]
    _STATE["rows"] = rows_out
    _STATE["items"] = items

    cmds.textScrollList("wbWJ_list", e=True, removeAll=True, append=items)
    cmds.text("wbWJ_status", e=True,
              label=u"模型 %d 个  |  骨骼 %d 个" % (len(meshes), total_all))


def select_joints(*args):
    """在场景中选中列表里的骨骼。"""
    joints = _STATE.get("rows") or []
    if not joints:
        cmds.warning(u"列表为空")
        return
    exist = [j for j in joints if cmds.objExists(j)]
    missing = len(joints) - len(exist)
    if not exist:
        cmds.warning(u"列表中的骨骼在场景中都不存在")
        return
    cmds.select(exist, r=True)
    if missing:
        cmds.warning(u"有 %d 个骨骼在场景中找不到, 已跳过" % missing)


def _clipboard(text):
    """复制到系统剪贴板, 失败时退化为 MEL 全局变量。"""
    for mod in ("PySide6", "PySide2"):
        try:
            qt = __import__(mod + ".QtWidgets", fromlist=["QApplication"])
            qt.QApplication.clipboard().setText(text)
            return True
        except Exception:
            continue
    # 退化为 MEL 全局变量, 可在脚本编辑器里用 $gWbClipboard 取出
    try:
        import maya.mel as mel
        mel.eval('global string $gWbClipboard; $gWbClipboard = "%s";'
                 % text.replace('"', '\\"').replace("\n", "\\n"))
        return True
    except Exception:
        pass
    return False


def copy_selected(*args):
    """复制: 有选中项则复制选中项, 否则复制全部。"""
    # 列表行文本 -> 骨骼名 的映射(按生成顺序一一对应)
    mapping = {}
    row_iter = iter(_STATE.get("rows") or [])
    for line in (_STATE.get("items") or []):
        if line.startswith(_GROUP_TAG) or not line.strip():
            continue
        try:
            mapping[line] = next(row_iter)
        except StopIteration:
            break

    sel = cmds.textScrollList("wbWJ_list", q=True, si=True) or []
    names = [mapping[s] for s in sel if s in mapping]
    if not names:
        names = list(_STATE.get("rows") or [])
    if not names:
        cmds.warning(u"没有可复制的内容")
        return
    _clipboard("\n".join(names))
    cmds.text("wbWJ_status", e=True, label=u"已复制 %d 个骨骼名到剪贴板" % len(names))


def copy_pylist(*args):
    names = list(_STATE.get("rows") or [])
    if not names:
        cmds.warning(u"列表为空")
        return
    _clipboard(json.dumps(names, ensure_ascii=False, indent=4))
    cmds.text("wbWJ_status", e=True, label=u"已复制 Python 列表 (%d 项)" % len(names))


def export_data(fmt="txt"):
    """导出当前结果。"""
    rows = _STATE.get("rows") or []
    if not rows:
        cmds.warning(u"列表为空, 没有可导出的内容")
        return

    flt = "Text Files (*.txt)" if fmt == "txt" else "JSON Files (*.json)"
    paths = cmds.fileDialog2(fileFilter=flt, dialogStyle=2, fileMode=0)
    if not paths:
        return
    path = paths[0]

    if fmt == "txt":
        with open(path, "w", encoding="utf-8") as f:
            f.write(u"# mesh: %s\n# skinCluster: %s\n# count: %d\n\n"
                    % (_STATE.get("mesh"), _STATE.get("skin"), len(rows)))
            for name in rows:
                f.write(name + "\n")
    else:
        data = {
            "mesh": _STATE.get("mesh"),
            "skinCluster": _STATE.get("skin"),
            "count": len(rows),
            "joints": rows,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    cmds.text("wbWJ_status", e=True, label=u"已导出: %s" % os.path.basename(path))


def export_txt(*args):
    export_data("txt")


def export_json(*args):
    export_data("json")


def remove_zero_influences(*args):
    """清理 skinCluster 中权重全为 0 的 influence。"""
    skin = _STATE.get("skin")
    if not skin or not cmds.objExists(skin):
        cmds.warning(u"没有有效的 skinCluster")
        return
    ok = cmds.confirmDialog(
        title=u"清理零权重影响",
        message=u"将从 %s 移除所有权重为 0 的影响物体。\n此操作会修改蒙皮, 建议先存盘。" % skin,
        button=[u"确定", u"取消"], defaultButton=u"取消",
        cancelButton=u"取消", dismissString=u"取消")
    if ok != u"确定":
        return
    cmds.skinCluster(skin, e=True, removeUnusedInfluence=True)
    refresh()
    cmds.text("wbWJ_status", e=True, label=u"已清理 %s 的零权重影响" % skin)


def _snippet(*args):
    lines = [
        u"命令行用法:",
        u"    import maya_weight_joints_tool as w",
        u"    shape, skin, rows = w.get_weight_joints('pSphere1')",
        u"    for name, maxw in rows:",
        u"        print(name, maxw)",
        u"",
        u"参数:",
        u"    joints_only=True   只看 joint, 排除 mesh / 空物体等影响物",
        u"    skip_zero=True     剔除最大权重为 0 的骨骼",
        u"    precise=True       扫描顶点权重统计最大值(关掉只列名, 更快)",
        u"",
        u"导出: 选中结果行后点【复制名称】, 或【导出 TXT / JSON】",
    ]
    cmds.textScrollList("wbWJ_list", e=True, removeAll=True, append=lines)
    _STATE.update({"mesh": None, "skin": None, "rows": [], "items": lines})
    cmds.text("wbWJ_status", e=True, label=u"命令行用法示例  (想回到列表点【刷新】)")


def show():
    """显示工具窗口。"""
    if cmds.window(WIN_NAME, exists=True):
        cmds.deleteUI(WIN_NAME)
    if cmds.windowPref(WIN_NAME, exists=True):
        cmds.windowPref(WIN_NAME, remove=True)

    win = cmds.window(WIN_NAME, title=u"权重骨骼提取工具  ·  Maya 2024",
                      widthHeight=(520, 640), sizeable=True, minimizeButton=False)
    root = cmds.columnLayout(adj=True, rs=6, cal="left")

    cmds.text(label=u"  选中模型 → 点击【刷新】, 列出 skinCluster 中所有权重骨骼",
              al="left", h=22)
    cmds.separator(h=6, style="in")

    cmds.checkBox("wbWJ_opt_jointOnly", label=u"只显示 joint 骨骼(排除 mesh / 空物体等影响物)",
                  v=True, cc=refresh)
    cmds.checkBox("wbWJ_opt_skipZero", label=u"过滤最大权重为 0 的骨骼", v=True, cc=refresh)
    cmds.checkBox("wbWJ_opt_precise", label=u"精确模式: 扫描全部顶点权重并统计最大值(关闭则仅列名, 更快)",
                  v=True, cc=refresh)
    cmds.separator(h=6, style="in")

    # 注意: -font 只接受 Maya 内置字体名(如 fixedWidthFont), 不能写 "Courier New"
    cmds.textScrollList("wbWJ_list", h=340, allowMultiSelection=True,
                        dcc=select_joints, font="fixedWidthFont")
    cmds.separator(h=4, style="in")

    cmds.rowLayout(nc=4, cw4=(128, 128, 128, 128), ct4=("both",) * 4, adj=1)
    cmds.button(label=u"刷新", c=refresh, h=28)
    cmds.button(label=u"选择骨骼", c=select_joints, h=28)
    cmds.button(label=u"复制名称", c=copy_selected, h=28)
    cmds.button(label=u"复制为列表", c=copy_pylist, h=28)
    cmds.setParent("..")

    cmds.rowLayout(nc=4, cw4=(128, 128, 128, 128), ct4=("both",) * 4, adj=1)
    cmds.button(label=u"导出 TXT", c=export_txt, h=28)
    cmds.button(label=u"导出 JSON", c=export_json, h=28)
    cmds.button(label=u"清理零权重影响", c=remove_zero_influences, h=28)
    cmds.button(label=u"用法", c=_snippet, h=28)
    cmds.setParent("..")

    cmds.separator(h=6, style="none")
    cmds.text("wbWJ_status", label=u"就绪", al="left", h=20)
    cmds.setParent("..")

    cmds.showWindow(win)
    cmds.window(win, e=True, widthHeight=(520, 640))
    refresh()
    return win


# ------------------------------------------------------------------
#  直接把 .py 文件拖进 Maya 脚本编辑器 / 粘贴执行时自动弹窗;
#  被 import 时(__name__ == 模块名)不弹窗, 只提供函数接口。
# ------------------------------------------------------------------
_self_file = globals().get("__file__", "") or ""
if __name__ in ("__main__", "__builtin__", "builtins") or (
        _self_file and __name__.endswith(os.path.basename(_self_file))):
    show()
