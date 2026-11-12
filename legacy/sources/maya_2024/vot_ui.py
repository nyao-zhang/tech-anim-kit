# -*- coding: utf-8 -*-
"""
vot_ui.py  --  Vertex Order Transfer 的 Maya 界面 (Maya 2024 / PySide2)

    import vot_ui
    vot_ui.show()

交互主体是「选一条循环边 / 一段边界边」，而不是选点：
    A 上双击一条边（Maya 原生会选中整条循环边）
    Shift + 双击 B 上对应的那条边
    点「从选择添加」
"""

from __future__ import print_function

import maya.cmds as cmds

try:
    from PySide2 import QtWidgets, QtCore
    from shiboken2 import wrapInstance
except Exception:  # pragma: no cover
    try:
        from PySide6 import QtWidgets, QtCore
        from shiboken6 import wrapInstance
    except Exception:
        QtWidgets = None
        wrapInstance = None

try:
    import maya.OpenMayaUI as omui
except Exception:  # pragma: no cover
    omui = None

import vot_core as vot

WIN_NAME = 'votMainWindow'
TITLE = u'顶点顺序传递  Vertex Order Transfer'


def _maya_main_window():
    if omui is None or wrapInstance is None:
        return None
    try:
        ptr = omui.MQtUtil.mainWindow()
        if ptr is None:
            return None
        return wrapInstance(int(ptr), QtWidgets.QWidget)
    except Exception:
        return None


def _shape_of(node):
    try:
        _, path = vot.get_mesh_fn(node)
        return path.partialPathName()
    except Exception:
        return None


class VOWindow(QtWidgets.QMainWindow if QtWidgets else object):

    def __init__(self, parent=None):
        super(VOWindow, self).__init__(parent)
        self.setWindowTitle(TITLE)
        self.setObjectName(WIN_NAME)
        self.resize(600, 800)

        self.A = None
        self.B = None
        self.mapping = {}
        self.partial_info = None  # 部分匹配的统计（approx / dup / dropped）
        self.candidates = []      # 候选种子 dict 列表（来自循环边/边界边）
        self.seeds = []           # 备用：单点种子

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        root.addWidget(self._build_objects())
        root.addWidget(self._build_loops())
        root.addWidget(self._build_points())
        root.addWidget(self._build_options())
        root.addLayout(self._build_actions())

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        try:
            f = self.log.font()
            f.setFamilies(['Consolas', 'Courier New', 'monospace'])
            self.log.setFont(f)
        except Exception:
            pass
        self.log.setMinimumHeight(190)
        root.addWidget(self.log, 1)

        vot.set_logger(self._log)
        self._on_partial_changed()

    # ---------------------------------------------------------------- 构建
    def _build_objects(self):
        box = QtWidgets.QGroupBox(u'1. 选择两个模型')
        lay = QtWidgets.QGridLayout(box)
        lay.setColumnMinimumWidth(1, 270)

        lay.addWidget(QtWidgets.QLabel(u'源模型 A（点序参考）'), 0, 0)
        self.edA = QtWidgets.QLineEdit()
        self.edA.setPlaceholderText(u'选中 A 后点右侧按钮')
        lay.addWidget(self.edA, 0, 1)
        btnA = QtWidgets.QPushButton(u'<< 从选择')
        btnA.clicked.connect(self.pick_a)
        lay.addWidget(btnA, 0, 2)

        lay.addWidget(QtWidgets.QLabel(u'目标模型 B（要改点序）'), 1, 0)
        self.edB = QtWidgets.QLineEdit()
        self.edB.setPlaceholderText(u'选中 B 后点右侧按钮')
        lay.addWidget(self.edB, 1, 1)
        btnB = QtWidgets.QPushButton(u'<< 从选择')
        btnB.clicked.connect(self.pick_b)
        lay.addWidget(btnB, 1, 2)

        lab = QtWidgets.QLabel(u'A 只提供「点序 + 面表」，形状仍然用 B 自己的。')
        lab.setWordWrap(True)
        lay.addWidget(lab, 2, 0, 1, 3)
        return box

    def _build_loops(self):
        box = QtWidgets.QGroupBox(u'2. 对应循环边 / 边界边  ← 主要用法')
        lay = QtWidgets.QVBoxLayout(box)

        self.loopList = QtWidgets.QListWidget()
        self.loopList.setMaximumHeight(80)
        lay.addWidget(self.loopList)

        row1 = QtWidgets.QHBoxLayout()
        b1 = QtWidgets.QPushButton(u'从选择添加')
        b1.setToolTip(u'A 上双击一条边选中整条循环边，Shift+双击 B 上对应那条，再点这里')
        b1.clicked.connect(self.add_loop)
        row1.addWidget(b1)

        b2 = QtWidgets.QPushButton(u'一键：用边界边')
        b2.setToolTip(u'自动取两个模型的开口边界边（无需手动选择）')
        b2.clicked.connect(self.add_border)
        row1.addWidget(b2)

        b3 = QtWidgets.QPushButton(u'清空')
        b3.clicked.connect(self.clear_candidates)
        row1.addWidget(b3)
        lay.addLayout(row1)

        row2 = QtWidgets.QHBoxLayout()
        b4 = QtWidgets.QPushButton(u'选中A边界边')
        b4.clicked.connect(lambda: self.show_border('A'))
        row2.addWidget(b4)

        b5 = QtWidgets.QPushButton(u'选中B边界边')
        b5.clicked.connect(lambda: self.show_border('B'))
        row2.addWidget(b5)

        b6 = QtWidgets.QPushButton(u'扩展成整条循环边')
        b6.setToolTip(u'只选了 1 条边时，点这个把它扩展成完整循环边')
        b6.clicked.connect(self.expand_loop)
        row2.addWidget(b6)
        lay.addLayout(row2)

        tip = QtWidgets.QLabel(u'用法：在 Maya 里<b>双击</b>一条边即可选中整条循环边；'
                               u'再按住 Shift 双击 B 上对应的那条边；点「从选择添加」。\n'
                               u'开放网格也可以直接点「一键：用边界边」，完全不用选。\n'
                               u'选一条边 = 一次性给出几十上百组对应点，朝向由程序自动判定。')
        tip.setWordWrap(True)
        lay.addWidget(tip)
        return box

    def _build_points(self):
        box = QtWidgets.QGroupBox(u'2b. 备用：单点种子（循环边不好选时才用）')
        lay = QtWidgets.QVBoxLayout(box)

        self.seedList = QtWidgets.QListWidget()
        self.seedList.setMaximumHeight(64)
        lay.addWidget(self.seedList)

        row = QtWidgets.QHBoxLayout()
        b1 = QtWidgets.QPushButton(u'从选择添加 1 组')
        b1.clicked.connect(self.add_seed)
        row.addWidget(b1)
        b2 = QtWidgets.QPushButton(u'删除')
        b2.clicked.connect(self.del_seed)
        row.addWidget(b2)
        b3 = QtWidgets.QPushButton(u'清空')
        b3.clicked.connect(self.clear_seeds)
        row.addWidget(b3)
        lay.addLayout(row)
        return box

    def _build_options(self):
        box = QtWidgets.QGroupBox(u'3. 选项')
        lay = QtWidgets.QVBoxLayout(box)

        self.ckPos = QtWidgets.QCheckBox(u'自动坐标匹配（形状完全重合时秒解）')
        self.ckPos.setChecked(True)
        lay.addWidget(self.ckPos)

        sep = QtWidgets.QFrame()
        try:
            sep.setFrameShape(QtWidgets.QFrame.HLine)
        except Exception:
            pass
        lay.addWidget(sep)

        self.ckPartial = QtWidgets.QCheckBox(
            u'允许部分匹配：个别点对不上时忽略它们，先把能对上的对上')
        self.ckPartial.setToolTip(
            u'勾选后，求解失败不再整体放弃：\n'
            u'  · 删掉造成「断边」的冲突点，保证剩余部分拓扑一致\n'
            u'  · 剩下的点用最近的 B 顶点近似填充，并在结果里选中它们供检查\n'
            u'适用于「大部分一样、个别点/个别连接不一样」的模型。')
        self.ckPartial.setChecked(False)
        self.ckPartial.stateChanged.connect(self._on_partial_changed)
        lay.addWidget(self.ckPartial)

        rowf = QtWidgets.QHBoxLayout()
        rowf.addSpacing(20)
        self.ckFill = QtWidgets.QCheckBox(u'对不上的点用最近点近似填充')
        self.ckFill.setToolTip(u'取消勾选则只输出能确定的对应点，'
                               u'未匹配的点在重建时沿用 A 的坐标')
        self.ckFill.setChecked(True)
        self.ckFill.stateChanged.connect(self._on_partial_changed)
        rowf.addWidget(self.ckFill)
        rowf.addStretch(1)
        lay.addLayout(rowf)

        self.ckPrune = QtWidgets.QCheckBox(u'删掉造成断边的点（保证留下的部分严格一致）')
        self.ckPrune.setToolTip(
            u'勾选：删掉冲突点，剩下的每一条边都能在 B 上找到，被删的点用最近点回填。\n'
            u'取消：保留全部已确定的对应关系，只是把对不上的连接报给你 ——\n'
            u'  两模型姿态/形状并不完全重合时，取消更安全。')
        self.ckPrune.setChecked(True)
        lay.addWidget(self.ckPrune)

        rowr = QtWidgets.QHBoxLayout()
        rowr.addSpacing(20)
        rowr.addWidget(QtWidgets.QLabel(u'近似搜索半径（相对包围盒对角线 %）'))
        self.spRadius = QtWidgets.QDoubleSpinBox()
        self.spRadius.setRange(0.001, 50.0)
        self.spRadius.setDecimals(3)
        self.spRadius.setSingleStep(0.05)
        self.spRadius.setValue(0.1)          # 0.1% of diag
        self.spRadius.setMinimumWidth(90)
        rowr.addWidget(self.spRadius)
        rowr.addStretch(1)
        lay.addLayout(rowr)

        self.ckUV = QtWidgets.QCheckBox(u'复制所有 UV 集')
        self.ckUV.setChecked(True)
        lay.addWidget(self.ckUV)

        self.ckEdge = QtWidgets.QCheckBox(u'复制硬边 / 软边')
        self.ckEdge.setChecked(True)
        lay.addWidget(self.ckEdge)

        self.ckColor = QtWidgets.QCheckBox(u'复制顶点色（大场景较慢）')
        self.ckColor.setChecked(False)
        lay.addWidget(self.ckColor)

        self.ckSkin = QtWidgets.QCheckBox(u'重建 skinCluster 蒙皮权重（实验）')
        self.ckSkin.setChecked(False)
        lay.addWidget(self.ckSkin)

        self.ckKeep = QtWidgets.QCheckBox(u'保留原模型（取消则删除 B，新模型沿用其名）')
        self.ckKeep.setChecked(True)
        lay.addWidget(self.ckKeep)

        self.ckSrcPos = QtWidgets.QCheckBox(u'用 A 的坐标（会把 B 变成 A 的形状，一般不要勾）')
        self.ckSrcPos.setChecked(False)
        lay.addWidget(self.ckSrcPos)
        return box

    def _build_actions(self):
        lay = QtWidgets.QHBoxLayout()
        self.btnSolve = QtWidgets.QPushButton(u'① 计算映射 / 校验')
        self.btnSolve.setMinimumHeight(34)
        self.btnSolve.clicked.connect(self.solve)
        lay.addWidget(self.btnSolve)

        self.btnApply = QtWidgets.QPushButton(u'② 应用（生成新模型）')
        self.btnApply.setMinimumHeight(34)
        self.btnApply.setEnabled(False)
        self.btnApply.clicked.connect(self.apply)
        lay.addWidget(self.btnApply)

        self.btnMark = QtWidgets.QPushButton(u'选中「对不上的点」')
        self.btnMark.setMinimumHeight(34)
        self.btnMark.setToolTip(u'在 A 上选中被忽略 / 近似填充的顶点，方便人工核对')
        self.btnMark.setEnabled(False)
        self.btnMark.clicked.connect(self.mark_bad)
        lay.addWidget(self.btnMark)

        self.btnReload = QtWidgets.QPushButton(u'重载')
        self.btnReload.setMinimumHeight(34)
        self.btnReload.setMaximumWidth(58)
        self.btnReload.setToolTip(u'重新加载 vot_core —— 改过 .py 之后点这个，不用重启 Maya')
        self.btnReload.clicked.connect(self.reload_modules)
        lay.addWidget(self.btnReload)
        return lay

    # ---------------------------------------------------------------- 日志
    def _log(self, msg):
        try:
            self.log.appendPlainText(str(msg))
            QtWidgets.QApplication.processEvents()
        except Exception:
            print(str(msg))

    def _warn(self, msg):
        try:
            QtWidgets.QMessageBox.warning(self, TITLE, str(msg))
        except Exception:
            pass

    def _on_partial_changed(self, *_a):
        on = self.ckPartial.isChecked()
        self.ckFill.setEnabled(on)
        self.ckPrune.setEnabled(on)
        self.spRadius.setEnabled(on and self.ckFill.isChecked())

    def _reset(self):
        self.mapping = {}
        self.partial_info = None
        self.btnApply.setEnabled(False)
        self.btnMark.setEnabled(False)

    # ---------------------------------------------------------------- 模型
    def _pick(self, edit):
        sel = cmds.ls(sl=True, long=False) or []
        if not sel:
            self._warn(u'请先选择一个模型')
            return None
        node = sel[0]
        if _shape_of(node) is None:
            self._warn(u'选中的不是多边形网格')
            return None
        edit.setText(node)
        return node

    def pick_a(self):
        if self._pick(self.edA):
            self.A = None
            self._reset()
            self._log(u'源 A = %s' % self.edA.text())

    def pick_b(self):
        if self._pick(self.edB):
            self.B = None
            self._reset()
            self._log(u'目标 B = %s' % self.edB.text())

    def _graphs(self):
        if not self.edA.text() or not self.edB.text():
            raise RuntimeError(u'请先指定 A 和 B')
        if self.A is None:
            self.A = vot.MeshGraph(self.edA.text())
        if self.B is None:
            self.B = vot.MeshGraph(self.edB.text())
        return self.A, self.B

    # ---------------------------------------------------------------- 循环边
    def add_loop(self):
        try:
            A, B = self._graphs()
            cands = vot.candidates_from_selection(A, B)
        except Exception as e:
            self._warn(str(e))
            return
        self.candidates.extend(cands)
        self.loopList.addItem(u'循环边 #%d：%d 组对应点，%d 种朝向（自动判定）'
                              % (self.loopList.count() + 1,
                                 len(cands[0]) if cands else 0, len(cands)))
        self._reset()

    def add_border(self):
        try:
            A, B = self._graphs()
            cands = vot.border_candidates(A, B)
        except Exception as e:
            self._warn(str(e))
            return
        if not cands:
            self._warn(u'两模型没有可用的边界边（都是封闭网格），请改用手动选择循环边')
            return
        self.candidates.extend(cands)
        self.loopList.addItem(u'边界边 #%d：%d 组对应点，%d 种朝向（自动判定）'
                              % (self.loopList.count() + 1,
                                 len(cands[0]) if cands else 0, len(cands)))
        self._reset()

    def clear_candidates(self):
        self.candidates = []
        self.loopList.clear()
        self._reset()

    def show_border(self, which):
        try:
            A, B = self._graphs()
        except Exception as e:
            self._warn(str(e))
            return
        G = A if which == 'A' else B
        if not G.border_edges:
            self._warn(u'%s 没有边界边（是封闭网格）' % which)
            return
        try:
            cmds.select('%s.e[%d]' % (G.transform, G.border_edges[0]), replace=True)
            for eid in G.border_edges[1:]:
                cmds.select('%s.e[%d]' % (G.transform, eid), add=True)
        except Exception as e:
            self._warn(str(e))
            return
        self._log(u'%s 的边界边：%d 条（已在视口选中）' % (which, len(G.border_edges)))

    def expand_loop(self):
        """只选了 1 条边时，把它扩展成完整循环边"""
        sel = vot.selected_edges()
        if len(sel) != 1:
            self._warn(u'请先只选 1 条边')
            return
        try:
            A, B = self._graphs()
        except Exception as e:
            self._warn(str(e))
            return
        obj, eid = vot.parse_edge(sel[0])
        s = _shape_of(obj)
        if s == A.shape:
            G, name = A, 'A'
        elif s == B.shape:
            G, name = B, 'B'
        else:
            self._warn(u'选中的边不属于 A 也不属于 B')
            return
        ids = G.expand_edge_loop(eid)
        if not ids:
            self._warn(u'扩展失败，请手动双击边选中循环边')
            return
        try:
            cmds.select('%s.e[%d]' % (G.transform, ids[0]), replace=True)
            for i in ids[1:]:
                cmds.select('%s.e[%d]' % (G.transform, i), add=True)
        except Exception as e:
            self._warn(str(e))
            return
        self._log(u'%s：已扩展为 %d 条边的循环边' % (name, len(ids)))

    # ---------------------------------------------------------------- 单点
    def add_seed(self):
        try:
            A, B = self._graphs()
        except Exception as e:
            self._warn(str(e))
            return
        vtxs = vot.selected_vertices()
        if len(vtxs) != 2:
            self._warn(u'请选中恰好 2 个顶点：A 上一个 + B 上对应的那一个')
            return
        found = {}
        for v in vtxs:
            obj, idx = vot.parse_vertex(v)
            s = _shape_of(obj)
            if s == A.shape:
                found['A'] = idx
            elif s == B.shape:
                found['B'] = idx
        if 'A' not in found or 'B' not in found:
            self._warn(u'两个顶点必须一个来自 A、一个来自 B')
            return
        pair = (found['A'], found['B'])
        if pair in self.seeds:
            return
        self.seeds.append(pair)
        self.seedList.addItem(u'A.vtx[%d]   <->   B.vtx[%d]' % pair)
        self._log(u'种子：A.vtx[%d] <-> B.vtx[%d]' % pair)
        self._reset()

    def del_seed(self):
        row = self.seedList.currentRow()
        if row < 0:
            return
        self.seedList.takeItem(row)
        del self.seeds[row]
        self._reset()

    def clear_seeds(self):
        self.seedList.clear()
        self.seeds = []
        self._reset()

    # ---------------------------------------------------------------- 求解
    def solve(self):
        self.log.clear()
        self.mapping = {}
        self.partial_info = None
        self.btnApply.setEnabled(False)
        self.btnMark.setEnabled(False)
        self.A = self.B = None
        try:
            A, B = self._graphs()
        except Exception as e:
            self._warn(str(e))
            return

        self._log(u'A: %s  (%d 顶点 / %d 边 / %d 面，边界边 %d 条)'
                  % (A.transform, A.nV, len(A.edges), A.nP, len(A.border_edges)))
        self._log(u'B: %s  (%d 顶点 / %d 边 / %d 面，边界边 %d 条)'
                  % (B.transform, B.nV, len(B.edges), B.nP, len(B.border_edges)))

        radius = None
        if self.spRadius.isEnabled():
            try:
                radius = max(A.diag, B.diag) * (self.spRadius.value() / 100.0)
            except Exception:
                radius = None

        kwargs = dict(seeds=self.seeds,
                      seed_candidates=self.candidates,
                      use_position=self.ckPos.isChecked())
        if self.ckPartial.isChecked():
            # 旧版 vot_core 没有部分匹配：给出明确提示，而不是抛一个看不懂的 TypeError
            if not hasattr(vot, 'repair_partial'):
                self._warn(u'当前加载的 vot_core 是旧版，不支持部分匹配。\n\n'
                           u'请重新加载后再试：\n'
                           u'    import importlib, vot_ui\n'
                           u'    importlib.reload(vot_ui)\n'
                           u'    vot_ui.show()')
                return
            kwargs.update(allow_partial=True,
                          partial_fill=self.ckFill.isChecked(),
                          prune_conflicts=self.ckPrune.isChecked(),
                          partial_radius=radius)

        try:
            cmds.refresh(force=False)
            rep = vot.build_mapping(A, B, **kwargs)
        except TypeError as e:
            self._warn(u'求解失败：%s\n\n多半是 vot_core 版本与 vot_ui 不一致。'
                       u'请重新加载：\n'
                       u'    import importlib, vot_ui\n'
                       u'    importlib.reload(vot_ui)\n'
                       u'    vot_ui.show()' % e)
            return
        except Exception as e:
            self._warn(u'求解失败：%s' % e)
            return

        self.mapping = rep['mapping']
        self.partial_info = rep.get('partial_info')
        if rep['ok']:
            tag = u'⚠ 部分匹配 ' if rep.get('partial') else u'✓ '
            self._log(tag + rep['reason'])
            self.btnApply.setEnabled(True)
            if self.partial_info:
                bad = (list(self.partial_info.get('approx') or [])
                       + list(self.partial_info.get('dup') or [])
                       + list(self.partial_info.get('dropped') or []))
                self.btnMark.setEnabled(bool(bad))
                self._log(u'→ 点「选中『对不上的点』」可在 A 上定位这些顶点')
            if rep.get('partial'):
                self._warn(u'只完成了部分匹配（详见日志）。\n'
                           u'被忽略 / 近似填充的点已在日志中列出，应用后会被自动选中。')
            return

        self._log(u'✗ ' + rep['reason'])
        if not self.ckPartial.isChecked() and len(rep['mapping']) > 0:
            self._log(u'→ 提示：若只有个别点对不上，可勾选「允许部分匹配」先出结果')
        if rep['unseeded']:
            self._log(u'→ 有 %d 个独立壳完全没有对应点，需要再给一条循环边覆盖它'
                      % len(rep['unseeded']))
            try:
                vot.select_vertices(A.transform, [c[0] for c in rep['unseeded'] if c])
                self._log(u'→ 已在 A 上选中这些壳的第一个点，去 B 上找对应点后加种子')
            except Exception:
                pass
        if rep['ambiguous']:
            self._log(u'→ %d 个顶点无法确定（对称/歧义区）' % len(rep['ambiguous']))
            try:
                vot.select_vertices(A.transform, rep['ambiguous'][:60])
                self._log(u'→ 已在 A 上选中待定点；更推荐再选一条经过该区域的循环边')
            except Exception:
                pass
        self._warn(u'映射未通过校验，请看日志补充对应循环边；'
                   u'或勾选「允许部分匹配」跳过对不上的点')

    def _bad_vertices(self):
        info = self.partial_info or {}
        out = set(info.get('approx') or []) | set(info.get('dup') or [])
        out |= set(info.get('dropped') or [])
        return sorted(out)

    def reload_modules(self):
        import importlib
        try:
            importlib.reload(vot)
        except Exception as e:
            self._warn(u'重载失败：%s' % e)
            return
        ver = getattr(vot, '__version__', None)
        self._log(u'vot_core 已重新加载：%s' % (ver or u'（旧版，无版本号）'))
        self._reset()

    def mark_bad(self):
        bad = self._bad_vertices()
        if not bad:
            self._warn(u'没有需要检查的点')
            return
        try:
            A, B = self._graphs()
        except Exception as e:
            self._warn(str(e))
            return
        try:
            vot.select_vertices(A.transform, bad[:2000])
            self._log(u'已在 A 上选中 %d 个「被忽略 / 近似填充」的顶点' % len(bad))
        except Exception as e:
            self._warn(str(e))

    def apply(self):
        if not self.mapping:
            self._warn(u'请先成功计算映射')
            return
        try:
            A, B = self._graphs()
        except Exception as e:
            self._warn(str(e))
            return
        try:
            new = vot.apply_mapping(
                A, B, self.mapping,
                name=None,
                keep_original=self.ckKeep.isChecked(),
                use_source_positions=self.ckSrcPos.isChecked(),
                copy_uv=self.ckUV.isChecked(),
                copy_smoothing=self.ckEdge.isChecked(),
                copy_colors=self.ckColor.isChecked(),
                copy_skin=self.ckSkin.isChecked(),
                highlight=self._bad_vertices() or None,
            )
        except Exception as e:
            self._warn(u'应用失败：%s' % e)
            return
        try:
            cmds.select(new, replace=True)
        except Exception:
            pass
        self._log(u'新模型：%s' % new)
        self.btnApply.setEnabled(False)
        self.btnMark.setEnabled(False)
        self.mapping = {}


def reload_core(verbose=True):
    """
    强制重新加载 vot_core。
    改过 vot_core.py 之后**必须**走这一步：Python 的 import 不会重读已加载的模块，
    否则会出现 'build_mapping() got an unexpected keyword argument ...' 这类
    「磁盘上是新版、内存里是旧版」的报错。
    """
    import importlib
    try:
        importlib.reload(vot)
    except Exception as e:
        if verbose:
            print(u'[vot] reload vot_core 失败：%s' % e)
        return None
    ver = getattr(vot, '__version__', None)
    if verbose:
        print(u'[vot] vot_core %s 已重新加载' % (ver or u'(旧版，无版本号)'))
    return ver


def show():
    if QtWidgets is None:
        raise RuntimeError(u'当前环境没有 PySide2 / PySide6')
    reload_core(verbose=True)
    for w in QtWidgets.QApplication.allWidgets():
        try:
            if w.objectName() == WIN_NAME:
                w.close()
                w.deleteLater()
        except Exception:
            pass
    win = VOWindow(_maya_main_window())
    win.show()
    ver = getattr(vot, '__version__', None)
    if ver:
        win._log(u'vot_core %s 已加载' % ver)
    else:
        win._log(u'警告：vot_core 缺少版本号，可能是旧版 —— 部分匹配不可用。'
                 u'请确认 vot_core.py 已更新到最新，然后重新执行 show()')
    return win


if __name__ == '__main__':
    show()
