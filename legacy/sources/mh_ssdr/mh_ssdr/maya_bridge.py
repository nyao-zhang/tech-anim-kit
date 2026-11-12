# -*- coding: utf-8 -*-
"""
mh_ssdr.maya_bridge
===================
Maya 数据层：把场景里的"蒙皮网格 + 骨骼"翻译成 ssdr_core 需要的数组，
再把解算出的蒙皮矩阵写回骨骼的 TRS。

矩阵约定
--------
* Maya 原生：**行向量约定**，p' = p @ M，平移在 M[3, :3]；
  cmds.getAttr / xform 的 16 个浮点即按此行优先顺序排列。
* ssdr_core：**列向量约定**，p' = M @ p，平移在 M[:3, 3]。
* 两者互为转置，用 col_to_row() / row_to_col() 转换（内部都是 .T）。

蒙皮公式（Maya skinCluster，行向量约定）
--------------------------------------
    p_world = sum_j  w_j * ( v_local @ G @ bindPre_j @ P_j )
其中 G = skinCluster.geomMatrix（绑定时的几何世界矩阵），
bindPre_j = 绑定姿态世界矩阵的逆，P_j = 骨骼当前世界矩阵。
转换到物体空间（右乘网格当前世界矩阵的逆 W^-1）得到：
    A_j = G @ bindPre_j @ P_j @ W^-1
反过来，由解算出的 A'_j 求骨骼应有的世界矩阵：
    P'_j = bindPre_j^-1 @ G^-1 @ A'_j @ W

注意 bindPre_j 与 P_j 的先后顺序不能交换：绑定姿态带旋转时二者不可交换，
而在静止姿态下所有写法都等于单位矩阵，写反了完全看不出来、一动就全错。
上面这个顺序是在 Maya 2024 里用 24 种排列对实际变形做拟合验证出来的。
"""

from __future__ import unicode_literals

import json

import numpy as np

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma

from . import ssdr_core as core

STATE_NODE = "mhSSDRState"
STATE_ATTR = "data"
STATE_VERSION = 1

FACIAL_PREFIX = "FACIAL_"
# MetaHuman 面部根/颈/头骨：改动它们会整体位移，默认不参与计算
MH_DEFAULT_EXCLUDE = ("FACIAL_C_FacialRoot", "FACIAL_C_Neck1", "FACIAL_C_Neck2")


# ----------------------------------------------------------------------------
# 撤销块
# ----------------------------------------------------------------------------
class undo_chunk(object):
    """把一组操作合成一个 Ctrl+Z 步骤"""

    def __init__(self, name="MH_SSDR"):
        self.name = name

    def __enter__(self):
        try:
            cmds.undoInfo(openChunk=True, chunkName=self.name)
        except Exception:
            pass
        return self

    def __exit__(self, *args):
        try:
            cmds.undoInfo(closeChunk=True)
        except Exception:
            pass
        return False


# ----------------------------------------------------------------------------
# 矩阵约定转换
# ----------------------------------------------------------------------------
def col_to_row(A):
    """列向量约定 <-> 行向量约定：只交换最后两个轴。

    注意：不能用 ``A.T``——对 (B,4,4) 的矩阵栈 ``.T`` 会把轴顺序整体反转成
    (4,4,B)，之后 A[j] 拿到的是"第 j 行"而不是"第 j 个矩阵"，整条链路全错。
    """
    return np.swapaxes(np.asarray(A, dtype=np.float64), -1, -2).copy()


def row_to_col(A):
    return np.swapaxes(np.asarray(A, dtype=np.float64), -1, -2).copy()


def mat_to_list(A_row):
    return np.asarray(A_row, dtype=np.float64).ravel().tolist()


def list_to_mat(values):
    return np.asarray(values, dtype=np.float64).reshape(4, 4)


def identity_row():
    return np.eye(4)


# ----------------------------------------------------------------------------
# 节点解析
# ----------------------------------------------------------------------------
def as_transform(node):
    """把任意节点/组件/网格名解析为 transform 名"""
    if not node:
        raise RuntimeError("未指定节点")
    name = node.split(".")[0]
    if cmds.nodeType(name) == "mesh":
        parents = cmds.listRelatives(name, parent=True, fullPath=True) or []
        if not parents:
            raise RuntimeError("网格 %s 没有 transform" % name)
        name = parents[0]
    if cmds.nodeType(name) == "transform" or cmds.nodeType(name) == "joint":
        return name
    shapes = cmds.listRelatives(name, shapes=True, type="mesh", noIntermediate=True) or []
    if shapes:
        return name
    raise RuntimeError("%s 不是网格节点" % node)


def shape_of(transform):
    shapes = cmds.listRelatives(transform, shapes=True, type="mesh", noIntermediate=True,
                                fullPath=True) or []
    if not shapes:
        raise RuntimeError("%s 上没有可用的 mesh 形状节点" % transform)
    return shapes[0]


def find_skin_cluster(shape):
    scs = cmds.ls(cmds.listHistory(shape, pruneDagObjects=True) or [], type="skinCluster")
    short = shape.split("|")[-1]
    for sc in scs:
        geo = cmds.skinCluster(sc, query=True, geometry=True) or []
        if short in [g.split("|")[-1] for g in geo]:
            return sc
    return scs[0] if scs else None


def world_matrix(node):
    """返回行向量约定的 4x4 世界矩阵"""
    vals = cmds.xform(node, query=True, worldSpace=True, matrix=True)
    return list_to_mat(vals)


def matrix_attr(node, attr):
    vals = cmds.getAttr("%s.%s" % (node, attr))
    if isinstance(vals, list) and len(vals) == 1 and isinstance(vals[0], (list, tuple)):
        vals = vals[0]
    return list_to_mat(vals)


def has_attr(node, attr):
    return bool(cmds.attributeQuery(attr, node=node, exists=True))


def force_update(nodes=None):
    """让 DG 立即求值。

    ``cmds.dgEvaluate`` 并不存在于 maya.cmds（那个名字只在 MEL 里有），批处理
    模式下也没有 ``refresh``，所以这里两手都试一遍，失败就静默跳过——Maya 本身
    在读取属性时会按需求值。
    """
    if nodes:
        try:
            cmds.dgdirty(list(nodes))
        except Exception:
            pass
    try:
        cmds.refresh(force=True)
    except Exception:
        pass


def dag_depth(node):
    parents = cmds.listRelatives(node, allParents=True, fullPath=True) or []
    return len(parents)


# ----------------------------------------------------------------------------
# 读取网格 / 权重
# ----------------------------------------------------------------------------
def read_points(shape):
    """读取顶点（物体空间），返回 (N,3) float64"""
    sel = om.MSelectionList()
    sel.add(shape)
    fn = om.MFnMesh(sel.getDagPath(0))
    pts = fn.getPoints(om.MSpace.kObject)
    out = np.empty((len(pts), 3), dtype=np.float64)
    for i, p in enumerate(pts):
        out[i, 0] = p.x
        out[i, 1] = p.y
        out[i, 2] = p.z
    return out


def write_points(shape, points):
    sel = om.MSelectionList()
    sel.add(shape)
    fn = om.MFnMesh(sel.getDagPath(0))
    pts = om.MPointArray()
    for p in np.asarray(points, dtype=np.float64):
        pts.append(om.MPoint(float(p[0]), float(p[1]), float(p[2])))
    fn.setPoints(pts, om.MSpace.kObject)


def num_vertices(shape):
    sel = om.MSelectionList()
    sel.add(shape)
    return om.MFnMesh(sel.getDagPath(0)).numVertices


def num_polygons(shape):
    sel = om.MSelectionList()
    sel.add(shape)
    return om.MFnMesh(sel.getDagPath(0)).numPolygons


def influence_names(fn_skin, skin_cluster=None):
    """MFnSkinCluster 的影响骨骼名（顺序与权重索引一致）"""
    names = []
    try:
        objects = fn_skin.getInfluenceObjects()
    except Exception:
        objects = None
    if objects is not None:
        for item in objects:
            if isinstance(item, om.MDagPath):
                names.append(item.partialPathName())
            else:                                   # MPlug
                node = item.node()
                try:
                    names.append(node.absoluteName())
                except Exception:
                    names.append(str(item).split(".")[0])
    if not names and skin_cluster:
        names = cmds.ls(cmds.skinCluster(skin_cluster, query=True, influence=True) or [],
                        long=False)
    return names


# ----------------------------------------------------------------------------
class SkinRig(object):
    """一个蒙皮网格的完整快照数据，供 ssdr_core 使用。"""

    def __init__(self, mesh):
        self.mesh = as_transform(mesh)
        self.shape = shape_of(self.mesh)
        self.skin_cluster = find_skin_cluster(self.shape)
        if not self.skin_cluster:
            raise RuntimeError("网格 %s 上没有找到 skinCluster（请选择已蒙皮的模型）" % self.mesh)

        sel = om.MSelectionList()
        sel.add(self.skin_cluster)
        self.fn_skin = oma.MFnSkinCluster(sel.getDependNode(0))

        self.bones = influence_names(self.fn_skin, self.skin_cluster)
        self.num_vertices = num_vertices(self.shape)
        self.num_polygons = num_polygons(self.shape)
        if not self.bones:
            raise RuntimeError("skinCluster 没有影响骨骼")

        # --- 权重 -------------------------------------------------------
        shape_sel = om.MSelectionList()
        shape_sel.add(self.shape)
        self.shape_dag = shape_sel.getDagPath(0)
        comp_fn = om.MFnSingleIndexedComponent()
        comp = comp_fn.create(om.MFn.kMeshVertComponent)
        comp_fn.addElements(list(range(self.num_vertices)))
        weights, influence_count = self.fn_skin.getWeights(self.shape_dag, comp)
        W = np.asarray(weights, dtype=np.float64).reshape(self.num_vertices, influence_count)
        if influence_count != len(self.bones):
            raise RuntimeError("权重数量(%d)与影响骨骼(%d)不一致" % (influence_count, len(self.bones)))
        row = W.sum(axis=1, keepdims=True)
        bad = (row < 1e-6).ravel()
        self.zero_weight_vertices = int(bad.sum())
        row[row < 1e-6] = 1.0
        self.weights = W / row

        # --- 矩阵 -------------------------------------------------------
        self.bind_pre = []
        for i in range(len(self.bones)):
            self.bind_pre.append(matrix_attr(self.skin_cluster, "bindPreMatrix[%d]" % i))
        self.geom_matrix = (matrix_attr(self.skin_cluster, "geomMatrix")
                            if has_attr(self.skin_cluster, "geomMatrix") else identity_row())
        self.mesh_world = world_matrix(self.mesh)
        self.inv_mesh_world = np.linalg.inv(self.mesh_world)
        self.envelope = self._safe_attr("envelope", 1.0)
        self.normalize_weights = self._safe_attr("normalizeWeights", 1)

        self._rest = None
        self._deformed = None
        self._variant = None
        self._calibration_rms = None

    # -- 顶点 ---------------------------------------------------------------
    def _safe_attr(self, attr, default):
        try:
            return cmds.getAttr(self.skin_cluster + "." + attr)
        except Exception:
            return default

    @property
    def rest_points(self):
        """绑定姿态（未蒙皮）的物体空间顶点"""
        if self._rest is None:
            self._rest = self._read_rest_points()
        return self._rest

    def _read_rest_points(self):
        """读取绑定姿态（未蒙皮）的物体空间顶点。

        注意一个很隐蔽的坑：当网格没有构建历史时，skinCluster 的
        ``input[0].inputGeometry`` 会**连回网格自己的形状节点**，从它读到的其实
        是"变形后"的顶点。它看起来像是 rest（差别只有几个单位），但会让后面所有
        矩阵反解系统性地偏掉。所以必须优先用中间形状（``*Orig``）并跳过自连接。
        """
        self.rest_source = ""
        longs = cmds.ls(self.shape, long=True) or [self.shape]
        shape_full = longs[0]

        # 策略 1：临时把 envelope 设为 0。
        # 输出 = skinCluster 的输入几何，也= 上游所有形变（tweak/混合变形/雕刻历史）
        # 之后的结果 —— 这才是蒙皮真正用的 rest 几何。比翻连接和找中间形状都可靠：
        #   * 网格无历史时 inputGeometry 会连回自身（读到的是变形后的点）；
        #   * 中间形状（*Orig）在"蒙皮后又改过点"的情况下会丢掉那些改动。
        env = None
        try:
            env = cmds.getAttr(self.skin_cluster + ".envelope")
        except Exception:
            env = None
        if env is not None:
            try:
                if not cmds.getAttr(self.skin_cluster + ".envelope", lock=True):
                    cmds.setAttr(self.skin_cluster + ".envelope", 0.0)
                    pts = read_points(self.shape)
                    if pts.shape[0] == self.num_vertices:
                        self.rest_source = "envelope=0"
                        return pts
            except Exception:
                pass
            finally:
                try:
                    cmds.setAttr(self.skin_cluster + ".envelope", env)
                except Exception:
                    pass

        # 策略 2：skinCluster 的输入几何（跳过连回自身的自连接）
        for idx in range(4):
            attr = "%s.input[%d].inputGeometry" % (self.skin_cluster, idx)
            try:
                srcs = cmds.listConnections(attr, source=True, destination=False) or []
            except Exception:
                continue
            for src in srcs:
                long_src = (cmds.ls(src, long=True) or [src])[0]
                if long_src == shape_full or long_src.startswith(shape_full + "."):
                    continue                       # 自连接：读到的是变形后的点
                try:
                    node = long_src if cmds.nodeType(long_src) == "mesh" else shape_of(long_src)
                    pts = read_points(node)
                except Exception:
                    continue
                if pts.shape[0] == self.num_vertices:
                    self.rest_source = "skinCluster.input(%s)" % node.split("|")[-1]
                    return pts

        # 策略 3：中间形状（可能是更早的几何，缺少蒙皮后做的点编辑）
        parents = cmds.listRelatives(self.shape, parent=True, fullPath=True) or []
        if parents:
            for s in cmds.listRelatives(parents[0], shapes=True, type="mesh",
                                        noIntermediate=False, fullPath=True) or []:
                if s == shape_full:
                    continue
                try:
                    if not cmds.getAttr(s + ".intermediateObject"):
                        continue
                    pts = read_points(s)
                except Exception:
                    continue
                if pts.shape[0] == self.num_vertices:
                    self.rest_source = "intermediate(%s)" % s.split("|")[-1]
                    return pts

        self.rest_source = "current(⚠ 未能找到原始几何)"
        return read_points(self.shape)

    @property
    def deformed_points(self):
        if self._deformed is None:
            self._deformed = read_points(self.shape)
        return self._deformed

    # -- 蒙皮矩阵 -----------------------------------------------------------
    #: 候选公式。第 1 个（GBPW）是在 Maya 2024 里实测验证过的正确顺序：
    #: 建蒙皮网格 -> 摆姿态 -> 用 24 种乘积顺序去拟合 Maya 的实际变形，
    #: 只有 G·B·P·W⁻¹ 能把误差压到 1e-7，其余都在 0.1~3.0 量级。
    FORMULAS = ("GBPW", "GPBW", "PBGW")

    def skinning_matrices(self, formula=None, geom_matrix=None, mesh_world=None,
                          worlds=None):
        """返回 (B,4,4) 行向量约定的物体空间蒙皮矩阵 A（p' = p · A）。

        正确顺序::

            A = geomMatrix · bindPreMatrix · jointWorld · meshWorld⁻¹

        这四个矩阵**绝对不能随便换序**：绑定姿态带旋转时 bindPre 与 jointWorld
        不可交换，而写反了在静止姿态下所有写法都恰好等于单位矩阵——完全看不出来，
        一动起来整条链就全错。

        :param worlds: 可选，{骨骼名: 世界矩阵}。给定时用它代替"从场景里读"，
            这样可以在不写场景的前提下算出一组假想姿态的蒙皮矩阵（镜像要用）。
        """
        if formula is None:
            formula = self.calibrate()[0]
        G = np.asarray(self.geom_matrix if geom_matrix is None else geom_matrix,
                       dtype=np.float64)
        W = np.asarray(self.mesh_world if mesh_world is None else mesh_world,
                       dtype=np.float64)
        invW = np.linalg.inv(W)
        out = np.empty((len(self.bones), 4, 4), dtype=np.float64)
        for j, bone in enumerate(self.bones):
            if worlds is None:
                P = world_matrix(bone)
            else:
                P = np.asarray(worlds.get(bone, np.eye(4)), dtype=np.float64)
            bp = np.asarray(self.bind_pre[j], dtype=np.float64)
            if formula == "GPBW":
                out[j] = G @ P @ bp @ invW
            elif formula == "PBGW":
                out[j] = P @ bp @ G @ invW
            else:                                   # GBPW（默认，实测正确）
                out[j] = G @ bp @ P @ invW
        return out

    def calibrate(self, force=False):
        """用「公式预测的变形 vs 网格实际变形」校验公式是否成立（结果缓存）。

        返回 ``(formula, rms)``。

        静止姿态下所有候选公式都等于单位矩阵，无法区分——所以这里的职责不是
        "猜公式"，而是**发现异常**（额外形变器、权重被改、geomMatrix 缺失……）。
        默认始终用 GBPW，只有当它明显不成立、且有别的公式明显更好时才切换。
        """
        if self._variant is not None and not force:
            return self._variant, self._calibration_rms

        rest = self.rest_points
        target = self.deformed_points
        weights = self.weights
        diag = max(mesh_diagonal(rest), 1e-9)
        errs = {}
        for name in self.FORMULAS:
            try:
                errs[name] = core.rms_of(row_to_col(self.skinning_matrices(name)),
                                         rest, weights, target)
            except Exception:
                continue

        base = errs.get("GBPW", float("inf"))
        best_name, best_err = "GBPW", base
        if base > 0.01 * diag:                      # GBPW 明显不成立
            for name, err in errs.items():
                if err < best_err * 0.25:
                    best_name, best_err = name, err
        self._variant, self._calibration_rms = best_name, best_err
        return self._variant, self._calibration_rms

    def skinned_transforms(self):
        """当前姿态下所有影响骨骼的物体空间蒙皮矩阵，列向量约定（给 core 用）"""
        return row_to_col(self.skinning_matrices(self.calibrate()[0]))

    def world_from_skinning(self, A_col):
        """由物体空间蒙皮矩阵(列向量约定)反求骨骼世界矩阵(行向量约定)。

        GBPW: A = G·B·P·W⁻¹  ->  P = B⁻¹·G⁻¹·A·W
        GPBW: A = G·P·B·W⁻¹  ->  P = G⁻¹·A·W·B⁻¹
        PBGW: A = P·B·G·W⁻¹  ->  P = A·W·G⁻¹·B⁻¹
        """
        formula = self.calibrate()[0]
        G = np.asarray(self.geom_matrix, dtype=np.float64)
        W = np.asarray(self.mesh_world, dtype=np.float64)
        invG = np.linalg.inv(G)
        A_row = col_to_row(A_col)
        out = {}
        for j, bone in enumerate(self.bones):
            invB = np.linalg.inv(self.bind_pre[j])
            A = A_row[j]
            if formula == "GPBW":
                P = invG @ A @ W @ invB
            elif formula == "PBGW":
                P = A @ W @ invG @ invB
            else:                                   # GBPW
                P = invB @ invG @ A @ W
            out[bone] = P
        return out

    def skinning_from_world(self, worlds):
        """world_from_skinning 的逆：由骨骼世界矩阵(行向量)算回蒙皮矩阵(列向量)。

        用于"先在**世界空间**里算出镜像后的骨骼矩阵，再换算回求解器用的
        物体空间蒙皮矩阵"——这样滑块仍然能控制这些骨骼，镜像结果不会在下一
        次拖动滑块时被冲掉。
        """
        return row_to_col(self.skinning_matrices(worlds=worlds))

    # -- 其他 ---------------------------------------------------------------
    def bone_pivots(self):
        """每根影响骨骼在**绑定姿态**下的物体空间位置 (B,3)。

        来源：``bindPre`` 把物体空间映射到骨骼的绑定空间，所以它的逆作用在骨骼
        原点 (0,0,0) 上就得到骨骼在物体空间里的绑定位置。行向量约定下
        ``[0,0,0,1] · M = M[3, :]``，也就是逆矩阵的最后一行。
        用这个位置做"骨骼离雕刻区域多远"的判据（见 core.bones_within_reach）。
        """
        out = []
        for bp in self.bind_pre:
            M = np.asarray(bp, dtype=np.float64).reshape(4, 4)
            try:
                inv = np.linalg.inv(M)
            except np.linalg.LinAlgError:
                out.append(np.zeros(3))
                continue
            out.append(inv[3, :3].copy())
        return np.asarray(out, dtype=np.float64).reshape(-1, 3)

    def bone_vertex_counts(self, threshold=0.001):
        return (self.weights > threshold).sum(axis=0)

    def facial_bones(self):
        out = []
        for b in self.bones:
            short = b.split("|")[-1]
            if short.startswith(FACIAL_PREFIX) and short not in MH_DEFAULT_EXCLUDE:
                out.append(b)
        return out


# ----------------------------------------------------------------------------
# 骨骼写回
# ----------------------------------------------------------------------------
def _local_channels(node):
    out = {}
    for key, attr in _CH_ATTR.items():
        try:
            out[key] = cmds.getAttr(node + "." + attr)
        except Exception:
            pass
    return out


def snapshot_channels(nodes):
    return {n: _local_channels(n) for n in nodes if cmds.objExists(n)}


def restore_channels(snap):
    for node, ch in snap.items():
        if not cmds.objExists(node):
            continue
        for k, v in ch.items():
            attr = node + "." + _CH_ATTR[k]
            try:
                if cmds.getAttr(attr, lock=True):
                    continue
                cmds.setAttr(attr, v)
            except Exception:
                pass


_CH_ATTR = {"tx": "translateX", "ty": "translateY", "tz": "translateZ",
            "rx": "rotateX", "ry": "rotateY", "rz": "rotateZ",
            "sx": "scaleX", "sy": "scaleY", "sz": "scaleZ"}


def apply_world_matrices(targets, key=False, verify=True):
    """把一组骨骼的世界矩阵写回场景。

    因为带 jointOrient 的关节在被写入矩阵时，Maya 会先扣除 jointOrient 再分解出
    旋转通道，所以这里采用"写回 -> 读回 -> 用残差补偿"的定点迭代，最多 3 轮，
    可以保证最终世界矩阵与目标一致（对 jointOrient / 锁定通道也会给出提示）。

    :param targets: {bone_name: 行向量约定 4x4}
    :return: (max_error, worst_bone) —— 写回后的实测误差（世界单位）
    """
    if not targets:
        return 0.0, None
    ordered = sorted(targets.keys(), key=dag_depth)      # 父级优先
    scale = 1.0
    for m in targets.values():
        scale = max(scale, float(np.abs(m).max()))
    tol = 1e-6 * scale

    cur = {n: np.asarray(targets[n], dtype=np.float64).copy() for n in targets}
    for node in ordered:
        if cmds.objExists(node):
            cmds.xform(node, worldSpace=True, matrix=mat_to_list(cur[node]))

    worst, worst_bone = 0.0, None
    if verify:
        for _round in range(3):
            force_update(ordered)
            worst, worst_bone = 0.0, None
            fix = {}
            for node in ordered:
                if not cmds.objExists(node):
                    continue
                target = np.asarray(targets[node], dtype=np.float64)
                actual = world_matrix(node)
                err = float(np.abs(actual - target).max())
                if err > worst:
                    worst, worst_bone = err, node
                if err > tol:
                    # actual = cur @ E  ->  cur' = cur @ E^-1
                    try:
                        E = np.linalg.inv(cur[node]) @ actual
                        fix[node] = cur[node] @ np.linalg.inv(E)
                    except np.linalg.LinAlgError:
                        pass
            if not fix or worst <= tol:
                break
            for node, m in fix.items():
                cur[node] = m
                cmds.xform(node, worldSpace=True, matrix=mat_to_list(m))

    if key:
        for node in ordered:
            if not cmds.objExists(node):
                continue
            try:
                cmds.setKeyframe(node, attribute=["translateX", "translateY", "translateZ",
                                                  "rotateX", "rotateY", "rotateZ"])
            except Exception:
                pass
    return worst, worst_bone


# ----------------------------------------------------------------------------
# 雕刻网格的提取 / 重置
# ----------------------------------------------------------------------------
def delete_if_exists(node):
    if node and cmds.objExists(node):
        cmds.delete(node)


def duplicate_static(mesh, new_name):
    """复制网格并把当前变形"烘焙"成静态形状（去掉历史，不再受骨骼驱动）"""
    src = as_transform(mesh)
    src_shape = shape_of(src)
    dup = cmds.duplicate(src, renameChildren=True)[0]
    dup = cmds.rename(dup, new_name)
    dup_shape = shape_of(dup)

    # 安全检查：若复制体与原网格共享历史节点，说明 duplicate 没有复制输入图，
    # 直接删历史会破坏原始 rig —— 这种情况下只删除该复制体自己的 skinCluster。
    src_hist = set(cmds.ls(cmds.listHistory(src_shape, pruneDagObjects=True) or []))
    dup_hist = set(cmds.ls(cmds.listHistory(dup, pruneDagObjects=True) or []))
    shared = src_hist & dup_hist

    if not shared:
        try:
            cmds.delete(dup, constructionHistory=True)    # 烘焙当前形状
            return dup
        except Exception:
            pass

    for sc in cmds.ls(list(dup_hist), type="skinCluster"):
        geo = [g.split("|")[-1] for g in (cmds.skinCluster(sc, query=True, geometry=True) or [])]
        if dup_shape.split("|")[-1] in geo:
            try:
                cmds.delete(sc)
            except Exception:
                pass
    return dup


def extract_sculpt(rig, hide_source=True):
    """创建 (形状备份, 雕刻网格)，返回 (backup_name, sculpt_name)

    两者都是"提取那一刻"的静态副本：
      * backup  —— 隐藏，只用于「重置雕刻」时还原
      * sculpt  —— 给美术雕刻的网格
    求解用的 rest 顶点不依赖快照，始终从 skinCluster 的输入几何实时读取，
    因此骨骼位姿变化后仍然正确。
    """
    base = rig.mesh.split("|")[-1]
    backup_name = base + "_SSDR_Backup"
    sculpt_name = base + "_SSDR_Sculpt"

    delete_if_exists(backup_name)
    delete_if_exists(sculpt_name)

    backup = duplicate_static(rig.mesh, backup_name)
    if num_vertices(shape_of(backup)) != rig.num_vertices:
        raise RuntimeError("备份网格顶点数(%d)与源网格(%d)不一致" %
                           (num_vertices(shape_of(backup)), rig.num_vertices))
    cmds.setAttr(backup + ".visibility", 0)
    if has_attr(backup, "hiddenInOutliner"):
        cmds.setAttr(backup + ".hiddenInOutliner", 1)

    sculpt = duplicate_static(rig.mesh, sculpt_name)
    sculpt_shape = shape_of(sculpt)
    if num_vertices(sculpt_shape) != rig.num_vertices:
        raise RuntimeError("雕刻网格顶点数(%d)与源网格(%d)不一致" %
                           (num_vertices(sculpt_shape), rig.num_vertices))

    if hide_source:
        cmds.setAttr(rig.mesh + ".visibility", 0)

    try:
        cmds.select(sculpt, replace=True)
    except Exception:
        pass
    return backup, sculpt


def reset_sculpt(sculpt_mesh, backup_mesh):
    """把雕刻网格恢复成提取时的形状"""
    s_shape = shape_of(as_transform(sculpt_mesh))
    b_shape = shape_of(as_transform(backup_mesh))
    pts = read_points(b_shape)
    if num_vertices(s_shape) != pts.shape[0]:
        raise RuntimeError("雕刻网格与备份网格顶点数不一致，无法重置")
    write_points(s_shape, pts)
    force_update([s_shape])


def check_topology(rig, sculpt_mesh):
    """校验拓扑未改变（顶点数 / 面数）"""
    s_shape = shape_of(as_transform(sculpt_mesh))
    nv, nf = num_vertices(s_shape), num_polygons(s_shape)
    msgs = []
    if nv != rig.num_vertices:
        msgs.append("顶点数 %d ≠ 源网格 %d" % (nv, rig.num_vertices))
    if nf != rig.num_polygons:
        msgs.append("面数 %d ≠ 源网格 %d" % (nf, rig.num_polygons))
    return (len(msgs) == 0), "；".join(msgs)


# ----------------------------------------------------------------------------
# 场景状态（保存到网络节点，随场景存盘）
# ----------------------------------------------------------------------------
def _state_node(create=False):
    if cmds.objExists(STATE_NODE):
        return STATE_NODE
    if not create:
        return None
    node = cmds.createNode("network", name=STATE_NODE)
    cmds.addAttr(node, longName=STATE_ATTR, dataType="string")
    return node


def save_state(data):
    node = _state_node(create=True)
    data = dict(data)
    data["version"] = STATE_VERSION
    cmds.setAttr(node + "." + STATE_ATTR, json.dumps(data, ensure_ascii=False), type="string")


def load_state():
    node = _state_node(create=False)
    if not node:
        return {}
    if not cmds.attributeQuery(STATE_ATTR, node=node, exists=True):
        return {}
    raw = cmds.getAttr(node + "." + STATE_ATTR) or ""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def clear_state():
    node = _state_node(create=False)
    if node:
        cmds.setAttr(node + "." + STATE_ATTR, "", type="string")


# ----------------------------------------------------------------------------
def mesh_diagonal(points):
    p = np.asarray(points, dtype=np.float64)
    if p.size == 0:
        return 1.0
    return float(np.linalg.norm(p.max(axis=0) - p.min(axis=0))) or 1.0
