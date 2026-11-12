# -*- coding: utf-8 -*-
"""
pose_delta.py  --  Pose Delta Tool  (Maya 2024 / Python 3)
==========================================================

骨骼（或任意 transform）的「差值记录 / 叠加」工具。

工作流程
--------
1. 摆好起始姿势 -> 点击【记录初始值 (A)】
2. 摆好目标姿势 -> 点击【记录目标值 (B)】   ->  自动计算  DELTA = B - A
3. 摆到任意新姿势（或换一套同名的骨骼）后，点击【添加差值到当前值】
       新值 = 当前值 + DELTA x 倍率
   该按钮可以反复点击：每次都在「最新当前值」的基础上再叠加一次差值，
   等价于 N 次点击 = 当前值 + N x DELTA x 倍率。

安装
----
把本文件拷贝到 Maya 的 scripts 目录，例如：
    C:/Users/<you>/Documents/maya/2024/scripts/pose_delta.py

然后在 Script Editor (Python) 或 Shelf 按钮中执行：
    import pose_delta
    pose_delta.show()

可选：执行 pose_delta.install_shelf_button() 在当前工具架上创建一个启动按钮。

依赖：PySide2（Maya 2024 自带，无需额外安装）。
"""

from __future__ import print_function

import json
import math
import os

import maya.cmds as cmds
import maya.mel as mel

try:
    import maya.api.OpenMaya as om
except Exception:  # pragma: no cover
    om = None

try:
    from PySide2 import QtCore, QtWidgets, QtGui
    from shiboken2 import wrapInstance
except Exception:  # pragma: no cover - Maya 2025+ 走 PySide6
    from PySide6 import QtCore, QtWidgets, QtGui
    from shiboken6 import wrapInstance

try:
    from maya import OpenMayaUI as omui
except Exception:  # pragma: no cover
    omui = None


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
TRANSLATE_ATTRS = ('translateX', 'translateY', 'translateZ')
ROTATE_ATTRS = ('rotateX', 'rotateY', 'rotateZ')
SCALE_ATTRS = ('scaleX', 'scaleY', 'scaleZ')
CORE_ATTRS = TRANSLATE_ATTRS + ROTATE_ATTRS

ROTATE_ORDER_NAMES = ('xyz', 'yzx', 'zxy', 'xzy', 'yxz', 'zyx')

WINDOW_OBJECT_NAME = 'PoseDeltaToolWindow'


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------
def maya_main_window():
    """返回 Maya 主窗口的 QWidget（用于给 UI 当父级）。"""
    if omui is None:
        return None
    ptr = omui.MQtUtil.mainWindow()
    if ptr is None:
        return None
    try:
        return wrapInstance(int(ptr), QtWidgets.QWidget)
    except Exception:
        try:
            return wrapInstance(long(ptr), QtWidgets.QWidget)  # noqa: F821
        except Exception:
            return None


class undo_chunk(object):
    """把所有操作打包成一次 Ctrl+Z 可撤销。"""

    def __init__(self, name='poseDelta'):
        self.name = name

    def __enter__(self):
        try:
            cmds.undoInfo(openChunk=True, chunkName=self.name)
        except Exception:
            pass
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            cmds.undoInfo(closeChunk=True)
        except Exception:
            pass
        return False


def is_transform(node):
    try:
        return 'transform' in cmds.nodeType(node, inherited=True)
    except Exception:
        return False


def strip_namespace(name):
    """去掉 DAG 路径里每一段的命名空间：|rig:root|rig:spine -> |root|spine"""
    return '|'.join(seg.rsplit(':', 1)[-1] for seg in name.split('|'))


def match_key(name, ignore_ns):
    return strip_namespace(name) if ignore_ns else name


def basename_key(name, ignore_ns):
    return match_key(name.split('|')[-1], ignore_ns)


def expand_children(objects):
    """把每个对象的所有后代 transform/joint 一起展开，去重并保持顺序。"""
    result = []
    seen = set()
    stack = list(objects)
    while stack:
        obj = stack.pop(0)
        if obj in seen:
            continue
        seen.add(obj)
        result.append(obj)
        kids = cmds.listRelatives(obj, c=True, f=True) or []
        for k in kids:
            if is_transform(k):
                stack.append(k)
    return result


def attr_exists(obj, attr):
    return cmds.objExists('%s.%s' % (obj, attr))


def attr_is_numeric(obj, attr):
    plug = '%s.%s' % (obj, attr)
    try:
        value = cmds.getAttr(plug)
    except Exception:
        return False
    if isinstance(value, (list, tuple)):
        value = value[0]
    return isinstance(value, (int, float))


def normalize_angle(value):
    """把角度差规整到 -180 ~ 180（旋转的最短路径）。"""
    return (value + 180.0) % 360.0 - 180.0


def channelbox_attrs():
    """返回通道盒里当前高亮的属性名列表；没有高亮则返回 None。"""
    for cb in ('mainChannelBox', 'MainChannelBox'):
        if not cmds.channelBox(cb, exists=True):
            continue
        try:
            sel = cmds.channelBox(cb, q=True, selectedMainAttributes=True) or []
        except Exception:
            sel = []
        if sel:
            return [str(a) for a in sel]
    return None


# ---------------------------------------------------------------------------
# 世界矩阵模式需要的数学工具
# ---------------------------------------------------------------------------
def _euler_order_constant(order_name):
    return getattr(om.MEulerRotation, 'k' + order_name.upper())


def _euler_to_quaternion(values_deg, order_name='xyz'):
    e = om.MEulerRotation(
        math.radians(values_deg[0]),
        math.radians(values_deg[1]),
        math.radians(values_deg[2]),
        order=_euler_order_constant(order_name),
    )
    return e.asQuaternion()


def get_world_matrix(obj):
    return om.MMatrix(cmds.getAttr('%s.worldMatrix[0]' % obj))


def get_parent_inverse_matrix(obj):
    parents = cmds.listRelatives(obj, parent=True, f=True) or []
    if not parents:
        return om.MMatrix()
    return om.MMatrix(cmds.getAttr('%s.worldMatrix[0]' % parents[0])).inverse()


def decompose_and_set_local(obj, local_matrix, do_translate=True,
                            do_rotate=True, do_scale=False):
    """把一个局部矩阵拆成 translate / rotate / scale 写回属性。

    对 joint 会补偿 jointOrient 与 rotateAxis（M = T * R * RA * JO，
    因此 R = M * JO^-1 * RA^-1）。
    """
    tm = om.MTransformationMatrix(local_matrix)

    if do_translate:
        t = tm.translation(om.MSpace.kTransform)
        cmds.setAttr('%s.translate' % obj, t.x, t.y, t.z)

    if do_rotate:
        quat = tm.rotation(asQuaternion=True)

        jo = (0.0, 0.0, 0.0)
        ra = (0.0, 0.0, 0.0)
        if attr_exists(obj, 'jointOrient'):
            jo = cmds.getAttr('%s.jointOrient' % obj)[0]
        if attr_exists(obj, 'rotateAxis'):
            ra = cmds.getAttr('%s.rotateAxis' % obj)[0]

        if any(abs(v) > 1e-9 for v in jo) or any(abs(v) > 1e-9 for v in ra):
            quat = quat * _euler_to_quaternion(jo).inverse()
            quat = quat * _euler_to_quaternion(ra).inverse()

        order_index = int(cmds.getAttr('%s.rotateOrder' % obj))
        euler = om.MEulerRotation.decompose(
            quat, _euler_order_constant(ROTATE_ORDER_NAMES[order_index]))
        cmds.setAttr('%s.rotate' % obj,
                     math.degrees(euler.x),
                     math.degrees(euler.y),
                     math.degrees(euler.z))

    if do_scale:
        s = tm.scale(om.MSpace.kTransform)
        cmds.setAttr('%s.scale' % obj, s[0], s[1], s[2])


def set_world_matrix(obj, world_matrix, do_scale=False):
    """用世界矩阵驱动对象（父空间安全）。"""
    local = world_matrix * get_parent_inverse_matrix(obj)
    decompose_and_set_local(obj, local, do_translate=True,
                            do_rotate=True, do_scale=do_scale)


# ---------------------------------------------------------------------------
# 采集 / 差值 / 应用
# ---------------------------------------------------------------------------
def collect_channels(obj, want_translate, want_rotate, want_scale,
                     want_user_defined, channelbox_filter):
    """返回该对象需要采集的属性名列表。"""
    if channelbox_filter:
        return [a for a in channelbox_filter
                if attr_exists(obj, a) and attr_is_numeric(obj, a)]

    attrs = []
    if want_translate:
        attrs += list(TRANSLATE_ATTRS)
    if want_rotate:
        attrs += list(ROTATE_ATTRS)
    if want_scale:
        attrs += list(SCALE_ATTRS)
    if want_user_defined:
        try:
            ud = cmds.listAttr(obj, ud=True) or []
        except Exception:
            ud = []
        attrs += [a for a in ud if attr_is_numeric(obj, a)]

    out = []
    for a in attrs:
        if a in out:
            continue
        if attr_exists(obj, a) and attr_is_numeric(obj, a):
            out.append(a)
    return out


def record_pose(objects, space, want_translate=True, want_rotate=True,
                want_scale=False, want_user_defined=False,
                channelbox_filter=None):
    """采集一套姿势。返回 {'space':..., 'objects': {...}}"""
    pose = {'space': space, 'objects': {}}
    for obj in objects:
        if not cmds.objExists(obj):
            continue
        if space == 'world':
            pose['objects'][obj] = list(cmds.getAttr('%s.worldMatrix[0]' % obj))
        else:
            attrs = collect_channels(obj, want_translate, want_rotate,
                                     want_scale, want_user_defined,
                                     channelbox_filter)
            if not attrs:
                continue
            pose['objects'][obj] = dict(
                (a, float(cmds.getAttr('%s.%s' % (obj, a)))) for a in attrs)
    return pose


def compute_delta(initial, target, shortest_rotation=True):
    """DELTA = 目标值 - 初始值。"""
    if not initial or not target:
        return None
    if initial['space'] != target['space']:
        cmds.warning(u'初始值和目标值的记录空间不一致，请重新记录。')
        return None

    space = initial['space']
    delta = {'space': space, 'objects': {}}

    if space == 'world':
        for obj, wa in initial['objects'].items():
            if obj not in target['objects']:
                continue
            wb = target['objects'][obj]
            m_a = om.MMatrix(wa)
            m_b = om.MMatrix(wb)
            delta['objects'][obj] = list(m_b * m_a.inverse())
        return delta

    for obj, a_values in initial['objects'].items():
        if obj not in target['objects']:
            continue
        b_values = target['objects'][obj]
        per_attr = {}
        for attr, a_val in a_values.items():
            if attr not in b_values:
                continue
            d = float(b_values[attr]) - float(a_val)
            if shortest_rotation and attr.startswith('rotate'):
                d = normalize_angle(d)
            if abs(d) < 1e-10:
                continue
            per_attr[attr] = d
        if per_attr:
            delta['objects'][obj] = per_attr
    return delta


def build_match_index(delta_objects, ignore_ns):
    """构建 全名索引 与 短名索引，便于换 rig / 换命名空间后仍能匹配。"""
    full = {}
    short = {}
    for obj in delta_objects:
        full.setdefault(match_key(obj, ignore_ns), obj)
        short.setdefault(basename_key(obj, ignore_ns), obj)
    return full, short


def match_recorded(name, full_index, short_index, ignore_ns):
    key = match_key(name, ignore_ns)
    if key in full_index:
        return full_index[key]
    base = basename_key(name, ignore_ns)
    if base in short_index:
        return short_index[base]
    return None


def apply_delta(delta, objects, multiplier=1.0, ignore_ns=False,
                do_scale=False, channelbox_filter=None):
    """把差值叠加到「当前值」上。返回 (成功写入的属性数, 未匹配到的对象列表)。"""
    if not delta or not delta.get('objects'):
        cmds.warning(u'还没有可用的差值，请先记录初始值 (A) 和 目标值 (B)。')
        return 0, []

    full_index, short_index = build_match_index(delta['objects'].keys(), ignore_ns)
    written = 0
    unmatched = []
    failed = []

    space = delta.get('space', 'local')

    for cur in objects:
        rec = match_recorded(cur, full_index, short_index, ignore_ns)
        if rec is None:
            unmatched.append(cur)
            continue

        if space == 'world':
            m_delta = om.MMatrix(delta['objects'][rec])
            if multiplier < 0:
                m_delta = m_delta.inverse()
            try:
                set_world_matrix(cur, m_delta * get_world_matrix(cur), do_scale)
                written += 1
            except Exception as err:
                failed.append((cur, str(err)))
            continue

        for attr, d in delta['objects'][rec].items():
            if channelbox_filter and attr not in channelbox_filter:
                continue
            plug = '%s.%s' % (cur, attr)
            if not attr_exists(cur, attr):
                failed.append((cur, attr))
                continue
            try:
                value = float(cmds.getAttr(plug)) + d * multiplier
                cmds.setAttr(plug, value)
                written += 1
            except Exception as err:
                failed.append((cur, '%s (%s)' % (attr, err)))

    if failed:
        cmds.warning(u'有 %d 个属性写入失败（可能被锁定、被连接或是引用节点）。' % len(failed))
    return written, unmatched


def pose_channel_count(pose):
    if not pose:
        return 0, 0
    if pose['space'] == 'world':
        return len(pose['objects']), len(pose['objects']) * 16
    total = 0
    for values in pose['objects'].values():
        total += len(values)
    return len(pose['objects']), total


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
class PoseDeltaTool(QtWidgets.QDialog):
    """Pose Delta 主窗口。"""

    def __init__(self, parent=None):
        super(PoseDeltaTool, self).__init__(parent)
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle(u'Pose Delta — 骨骼差值记录 / 叠加')
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Window)
        self.setMinimumWidth(380)

        self.initial_pose = None   # A
        self.target_pose = None    # B
        self.delta = None          # B - A

        self._build_ui()
        self._refresh_status()

    # ---------------------------------------------------------------- UI 构建
    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        root.addWidget(self._build_scope_group())
        root.addWidget(self._build_channel_group())
        root.addWidget(self._build_record_group())
        root.addWidget(self._build_apply_group())

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels([u'对象 / 通道', u'差值'])
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setMaximumHeight(220)
        self.tree.header().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeToContents)
        root.addWidget(self.tree)

        io_row = QtWidgets.QHBoxLayout()
        self.btn_save = QtWidgets.QPushButton(u'保存差值 (JSON)')
        self.btn_load = QtWidgets.QPushButton(u'加载差值 (JSON)')
        self.btn_save.clicked.connect(self._save_delta)
        self.btn_load.clicked.connect(self._load_delta)
        io_row.addWidget(self.btn_save)
        io_row.addWidget(self.btn_load)
        root.addLayout(io_row)

        self.lbl_status = QtWidgets.QLabel(u'')
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

        tip = QtWidgets.QLabel(
            u'用法：摆好起始姿势点 A，摆好目标姿势点 B，'
            u'之后每次点【添加差值】都会在当前值上再叠一次差值。')
        tip.setWordWrap(True)
        tip.setStyleSheet('color: #9a9a9a; font-size: 11px;')
        root.addWidget(tip)

    def _build_scope_group(self):
        box = QtWidgets.QGroupBox(u'采集范围')
        v = QtWidgets.QVBoxLayout(box)

        self.rb_selection = QtWidgets.QRadioButton(u'当前选择')
        self.rb_all_joints = QtWidgets.QRadioButton(u'场景中所有骨骼 (joint)')
        self.rb_recorded = QtWidgets.QRadioButton(u'已记录的骨骼集合')
        self.rb_selection.setChecked(True)

        self.scope_group = QtWidgets.QButtonGroup(box)
        for rb in (self.rb_selection, self.rb_all_joints, self.rb_recorded):
            self.scope_group.addButton(rb)
            v.addWidget(rb)

        self.chk_children = QtWidgets.QCheckBox(u'包含所选对象的所有子层级')
        self.chk_children.setChecked(True)
        self.chk_same_set = QtWidgets.QCheckBox(u'记录目标值时强制使用与初始值相同的骨骼')
        self.chk_same_set.setChecked(True)
        self.chk_ignore_ns = QtWidgets.QCheckBox(u'匹配时忽略命名空间 (可跨 rig 复用)')
        self.chk_ignore_ns.setChecked(True)

        v.addWidget(self.chk_children)
        v.addWidget(self.chk_same_set)
        v.addWidget(self.chk_ignore_ns)
        return box

    def _build_channel_group(self):
        box = QtWidgets.QGroupBox(u'通道')
        v = QtWidgets.QVBoxLayout(box)

        row1 = QtWidgets.QHBoxLayout()
        self.chk_translate = QtWidgets.QCheckBox(u'位移')
        self.chk_rotate = QtWidgets.QCheckBox(u'旋转')
        self.chk_scale = QtWidgets.QCheckBox(u'缩放')
        self.chk_translate.setChecked(True)
        self.chk_rotate.setChecked(True)
        self.chk_scale.setChecked(False)
        row1.addWidget(self.chk_translate)
        row1.addWidget(self.chk_rotate)
        row1.addWidget(self.chk_scale)
        row1.addStretch()
        v.addLayout(row1)

        row2 = QtWidgets.QHBoxLayout()
        self.chk_user_defined = QtWidgets.QCheckBox(u'自定义属性')
        self.chk_channelbox = QtWidgets.QCheckBox(u'只用通道盒高亮的属性')
        self.chk_shortest = QtWidgets.QCheckBox(u'旋转走最短路径 (-180~180)')
        self.chk_shortest.setChecked(True)
        row2.addWidget(self.chk_user_defined)
        row2.addWidget(self.chk_channelbox)
        v.addLayout(row2)
        v.addWidget(self.chk_shortest)

        row3 = QtWidgets.QHBoxLayout()
        row3.addWidget(QtWidgets.QLabel(u'记录空间：'))
        self.cmb_space = QtWidgets.QComboBox()
        self.cmb_space.addItem(u'局部属性 (推荐)', 'local')
        self.cmb_space.addItem(u'世界矩阵 (整体偏移)', 'world')
        row3.addWidget(self.cmb_space, 1)
        v.addLayout(row3)
        return box

    def _build_record_group(self):
        box = QtWidgets.QGroupBox(u'1 / 2 - 记录')
        v = QtWidgets.QVBoxLayout(box)

        row = QtWidgets.QHBoxLayout()
        self.btn_record_a = QtWidgets.QPushButton(u'记录初始值 (A)')
        self.btn_record_b = QtWidgets.QPushButton(u'记录目标值 (B)')
        self.btn_record_a.clicked.connect(self._record_initial)
        self.btn_record_b.clicked.connect(self._record_target)
        row.addWidget(self.btn_record_a)
        row.addWidget(self.btn_record_b)
        v.addLayout(row)

        row2 = QtWidgets.QHBoxLayout()
        self.btn_recompute = QtWidgets.QPushButton(u'重新计算差值')
        self.btn_clear = QtWidgets.QPushButton(u'清空')
        self.btn_recompute.clicked.connect(self._compute)
        self.btn_clear.clicked.connect(self._clear)
        row2.addWidget(self.btn_recompute)
        row2.addWidget(self.btn_clear)
        v.addLayout(row2)
        return box

    def _build_apply_group(self):
        box = QtWidgets.QGroupBox(u'3 - 叠加')
        v = QtWidgets.QVBoxLayout(box)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel(u'倍率：'))
        self.spn_multiplier = QtWidgets.QDoubleSpinBox()
        self.spn_multiplier.setRange(-20.0, 20.0)
        self.spn_multiplier.setSingleStep(0.1)
        self.spn_multiplier.setDecimals(3)
        self.spn_multiplier.setValue(1.0)
        row.addWidget(self.spn_multiplier)
        self.chk_apply_selection = QtWidgets.QCheckBox(u'应用到当前选择')
        self.chk_apply_selection.setChecked(True)
        self.chk_apply_selection.setToolTip(
            u'勾选时：对当前选中的骨骼叠加差值（没选则用已记录的骨骼集合）。')
        row.addWidget(self.chk_apply_selection)
        v.addLayout(row)

        self.btn_add = QtWidgets.QPushButton(u'添加差值到当前值  ( Add Delta )')
        self.btn_add.setMinimumHeight(38)
        self.btn_add.setStyleSheet('font-weight: bold;')
        self.btn_add.clicked.connect(lambda: self._apply(1.0))
        v.addWidget(self.btn_add)

        row2 = QtWidgets.QHBoxLayout()
        self.btn_sub = QtWidgets.QPushButton(u'减去差值 (x -1)')
        self.btn_sub.clicked.connect(lambda: self._apply(-1.0))
        row2.addWidget(self.btn_sub)
        v.addLayout(row2)
        return box

    # ---------------------------------------------------------------- 逻辑
    def _space(self):
        return str(self.cmb_space.currentData())

    def _channelbox_filter(self):
        return channelbox_attrs() if self.chk_channelbox.isChecked() else None

    def _recorded_objects(self):
        for pose in (self.initial_pose, self.target_pose, self.delta):
            if pose and pose.get('objects'):
                return [o for o in pose['objects'].keys() if cmds.objExists(o)]
        return []

    def _scope_objects(self, force_same_as_initial=False):
        """根据界面设置返回要处理的对象列表。"""
        if force_same_as_initial and self.chk_same_set.isChecked():
            objs = [o for o in (self.initial_pose or {}).get('objects', {})
                    if cmds.objExists(o)]
            if objs:
                return expand_children(objs) if self.chk_children.isChecked() else objs

        if self.rb_selection.isChecked():
            objs = cmds.ls(sl=True, l=True) or []
            objs = [o for o in objs if is_transform(o)]
        elif self.rb_all_joints.isChecked():
            objs = cmds.ls(type='joint', l=True) or []
        else:
            objs = self._recorded_objects()

        if self.chk_children.isChecked():
            objs = expand_children(objs)
        return objs

    def _record_initial(self):
        objs = self._scope_objects()
        if not objs:
            cmds.warning(u'没有可记录的对象：请先选择骨骼，或改用「场景中所有骨骼」。')
            return
        self.initial_pose = record_pose(
            objs, self._space(),
            self.chk_translate.isChecked(),
            self.chk_rotate.isChecked(),
            self.chk_scale.isChecked(),
            self.chk_user_defined.isChecked(),
            self._channelbox_filter(),
        )
        n_obj, n_ch = pose_channel_count(self.initial_pose)
        print('[PoseDelta] 初始值 A：%d 个对象 / %d 个通道' % (n_obj, n_ch))
        self._refresh_status()

    def _record_target(self):
        if not self.initial_pose:
            cmds.warning(u'请先记录初始值 (A)。')
            return
        objs = self._scope_objects(force_same_as_initial=True)
        if not objs:
            cmds.warning(u'没有可记录的对象。')
            return
        self.target_pose = record_pose(
            objs, self._space(),
            self.chk_translate.isChecked(),
            self.chk_rotate.isChecked(),
            self.chk_scale.isChecked(),
            self.chk_user_defined.isChecked(),
            self._channelbox_filter(),
        )
        self._compute()

    def _compute(self):
        if not self.initial_pose or not self.target_pose:
            cmds.warning(u'需要同时具备初始值 (A) 和 目标值 (B)。')
            return
        self.delta = compute_delta(self.initial_pose, self.target_pose,
                                   self.chk_shortest.isChecked())
        if self.delta is not None:
            n_obj, n_ch = pose_channel_count(self.delta)
            print('[PoseDelta] 差值已计算：%d 个对象 / %d 个通道' % (n_obj, n_ch))
        self._refresh_status()

    def _apply(self, sign):
        if not self.delta or not self.delta.get('objects'):
            cmds.warning(u'还没有差值：请先记录 A 和 B。')
            return

        if self.chk_apply_selection.isChecked():
            sel = [o for o in (cmds.ls(sl=True, l=True) or []) if is_transform(o)]
            objects = expand_children(sel) if (sel and self.chk_children.isChecked()) else sel
            if not objects:
                objects = self._recorded_objects()
                print(u'[PoseDelta] 没有选择对象，改为对已记录的 %d 个骨骼叠加。' % len(objects))
        else:
            objects = self._recorded_objects()

        if not objects:
            cmds.warning(u'没有可应用的对象。')
            return

        multiplier = self.spn_multiplier.value() * sign
        cbf = self._channelbox_filter()

        with undo_chunk('poseDeltaAdd'):
            written, unmatched = apply_delta(
                self.delta, objects,
                multiplier=multiplier,
                ignore_ns=self.chk_ignore_ns.isChecked(),
                do_scale=self.chk_scale.isChecked(),
                channelbox_filter=cbf,
            )

        print('[PoseDelta] 已叠加 %d 个通道（倍率 x%g）。' % (written, multiplier))
        if unmatched:
            cmds.warning(u'以下对象在差值记录里找不到同名骨骼，已跳过：\n  %s'
                         % '\n  '.join(unmatched[:20]))
        self._refresh_status()

    def _clear(self):
        self.initial_pose = None
        self.target_pose = None
        self.delta = None
        self._refresh_status()
        print('[PoseDelta] 已清空。')

    def _save_delta(self):
        if not self.delta:
            cmds.warning(u'没有差值可保存。')
            return
        start = cmds.workspace(q=True, dir=True) or os.path.expanduser('~')
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, u'保存差值', os.path.join(start, 'pose_delta.json'),
            'JSON (*.json)')
        if not path:
            return
        data = {
            'tool': 'pose_delta',
            'version': 1,
            'delta': self.delta,
        }
        try:
            with open(path, 'w') as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            print('[PoseDelta] 已保存：%s' % path)
        except Exception as err:
            cmds.warning(u'保存失败：%s' % err)

    def _load_delta(self):
        start = cmds.workspace(q=True, dir=True) or os.path.expanduser('~')
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, u'加载差值', start, 'JSON (*.json)')
        if not path:
            return
        try:
            with open(path, 'r') as fh:
                data = json.load(fh)
        except Exception as err:
            cmds.warning(u'读取失败：%s' % err)
            return
        delta = data.get('delta') if isinstance(data, dict) else None
        if not delta or 'objects' not in delta:
            cmds.warning(u'文件里没有有效的差值数据。')
            return
        self.delta = delta
        self.initial_pose = None
        self.target_pose = None
        print(u'[PoseDelta] 已加载差值：%d 个对象。' % len(delta['objects']))
        self._refresh_status()

    # ---------------------------------------------------------------- 状态
    def _refresh_status(self):
        n_a_obj, n_a_ch = pose_channel_count(self.initial_pose)
        n_b_obj, n_b_ch = pose_channel_count(self.target_pose)
        n_d_obj, n_d_ch = pose_channel_count(self.delta)

        self.lbl_status.setText(
            u'A 初始值：%d 对象 / %d 通道\n'
            u'B 目标值：%d 对象 / %d 通道\n'
            u'Δ 差值　：%d 对象 / %d 通道'
            % (n_a_obj, n_a_ch, n_b_obj, n_b_ch, n_d_obj, n_d_ch))

        self.btn_add.setEnabled(bool(self.delta and self.delta.get('objects')))
        self.btn_sub.setEnabled(self.btn_add.isEnabled())
        self.btn_save.setEnabled(self.btn_add.isEnabled())

        self._update_tree()

    def _update_tree(self):
        self.tree.clear()
        if not self.delta or not self.delta.get('objects'):
            return
        world_mode = self.delta.get('space') == 'world'
        for obj in sorted(self.delta['objects'].keys()):
            values = self.delta['objects'][obj]
            short = obj.split('|')[-1]
            item = QtWidgets.QTreeWidgetItem(self.tree, [short, ''])
            item.setToolTip(0, obj)
            if world_mode:
                child = QtWidgets.QTreeWidgetItem(item, ['worldMatrix', u'(矩阵)'])
                item.addChild(child)
            else:
                for attr in sorted(values.keys()):
                    item.addChild(QtWidgets.QTreeWidgetItem(
                        item, [attr, '%.4f' % values[attr]]))
            item.setText(1, u'%d 个通道' % len(values))
            item.setExpanded(False)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def show():
    """显示工具窗口（单实例，重复调用会刷新窗口）。"""
    for widget in QtWidgets.QApplication.allWidgets():
        if widget.objectName() == WINDOW_OBJECT_NAME:
            try:
                widget.close()
                widget.deleteLater()
            except Exception:
                pass

    win = PoseDeltaTool(maya_main_window())
    win.show()
    win.raise_()
    return win


def install_shelf_button():
    """在当前工具架上创建一个启动按钮（可选）。"""
    try:
        shelf = mel.eval('global string $gShelfTopLevel; $tmp = $gShelfTopLevel;')
        cmds.shelfButton(
            parent=shelf,
            label='PoseDelta',
            annotation=u'骨骼差值记录 / 叠加',
            image1='pythonFamily.png',
            command='import pose_delta; pose_delta.show()',
            sourceType='python',
        )
        print('[PoseDelta] 工具架按钮已创建。')
    except Exception as err:
        cmds.warning(u'创建工具架按钮失败：%s' % err)


if __name__ == '__main__':
    show()
