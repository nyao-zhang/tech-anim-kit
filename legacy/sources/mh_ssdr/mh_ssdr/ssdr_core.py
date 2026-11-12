# -*- coding: utf-8 -*-
"""
mh_ssdr.ssdr_core
=================
SSDR(Smooth Skinning Decomposition with Rigid Bones)的"反求"核心。

与 Le & Deng 原始论文(SIGGRAPH Asia 2012)不同的是：论文用于
"从网格序列里同时求权重 + 骨骼变换"，而本工具的场景里
**蒙皮权重是已知的(来自 MetaHuman 的 skinCluster)**，只有骨骼变换未知，
因此我们只需要论文中的 "Bone transform update"(公式 6~10) 这一步：

    目标:  min_{A_j}  sum_i  ||  sum_j  w_ij * A_j * v_i  -  p_i^*  ||
           s.t.      A_j 为刚体变换(或仿射)

采用块坐标下降(Block Coordinate Descent)：固定除 j 以外的所有骨骼，
对骨骼 j 做一次"加权 Kabsch / 加权 Procrustes"求解，循环若干次即收敛。
每次迭代的目标函数单调下降，收敛稳定。

约定（非常重要）：
  * 所有矩阵一律 4x4，**列向量约定**(p' = M @ p)，平移在 M[:3, 3]。
  * 与 Maya 的"行向量约定"(p' = p @ M, 平移在 M[3, :3])的转置关系为
    M_maya = M_numpy.T，转换在 maya_bridge 里统一处理。
  * v_i 为**绑定姿态(rest)的物体空间坐标**；
    A_j 为物体空间的蒙皮矩阵(A_j = I 表示该骨骼处于绑定姿态)。
  * p_i^* 为雕刻后的目标顶点位置(同一个物体空间)。

本模块只依赖 NumPy，不依赖 Maya，可离线跑单元测试。
"""

from __future__ import unicode_literals

import numpy as np

EPS = 1e-12
NONZERO_W = 1e-6      # 小于该值的权重视为 0


# ----------------------------------------------------------------------------
# 基础矩阵 / 四元数工具
# ----------------------------------------------------------------------------
def as_mat4(m):
    """保证返回 4x4 float64。支持 (4,4) / (3,4) / 长度 16 的列表。"""
    m = np.asarray(m, dtype=np.float64)
    if m.shape == (4, 4):
        return m
    if m.shape == (3, 4):
        out = np.eye(4)
        out[:3, :4] = m
        return out
    if m.size == 16:
        return m.reshape(4, 4)
    raise ValueError("无法转换为 4x4 矩阵: shape=%s" % (m.shape,))


def to_row_major16(A):
    """列向量约定的 4x4 -> Maya 行向量约定的 16 个浮点（row-major）"""
    return np.asarray(A, dtype=np.float64).T.ravel().tolist()


def from_row_major16(values):
    """Maya 行向量约定的 16 个浮点 -> 列向量约定的 4x4"""
    return np.asarray(values, dtype=np.float64).reshape(4, 4).T.copy()


def transform_points(A, V):
    """(4,4) 作用于 (N,3) -> (N,3)"""
    A = np.asarray(A, dtype=np.float64)
    V = np.asarray(V, dtype=np.float64)
    if V.size == 0:
        return V.reshape(0, 3)
    return V @ A[:3, :3].T + A[:3, 3]


def batch_transform(A, V):
    """A: (B,4,4), V: (N,3) -> (B,N,3)"""
    A = np.asarray(A, dtype=np.float64)
    V = np.asarray(V, dtype=np.float64)
    return np.einsum("bij,nj->bni", A[:, :3, :3], V) + A[:, None, :3, 3]


def orthonormalize(m3):
    """把任意 3x3 正交化(极分解)，去掉缩放/剪切/数值漂移。"""
    m3 = np.asarray(m3, dtype=np.float64)
    U, _S, Vt = np.linalg.svd(m3)
    R = U @ Vt
    if np.linalg.det(R) < 0:
        U = U.copy()
        U[:, -1] *= -1.0
        R = U @ Vt
    return R


def rotation_angle(R):
    """旋转矩阵 -> 角度(弧度, [0, pi])"""
    c = (np.trace(np.asarray(R, dtype=np.float64)) - 1.0) * 0.5
    return float(np.arccos(max(-1.0, min(1.0, c))))


def mat_to_quat(R):
    """3x3 旋转矩阵 -> 四元数 (w, x, y, z)"""
    R = np.asarray(R, dtype=np.float64)
    t = R[0, 0] + R[1, 1] + R[2, 2]
    if t > 0.0:
        s = np.sqrt(t + 1.0) * 2.0
        q = [0.25 * s, (R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s]
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2.0
        q = [(R[2, 1] - R[1, 2]) / s, 0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s]
    elif R[1, 1] > R[2, 2]:
        s = np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2.0
        q = [(R[0, 2] - R[2, 0]) / s, (R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s]
    else:
        s = np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2.0
        q = [(R[1, 0] - R[0, 1]) / s, (R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s]
    q = np.asarray(q, dtype=np.float64)
    n = np.linalg.norm(q)
    return q / n if n > EPS else np.array([1.0, 0.0, 0.0, 0.0])


def quat_to_mat(q):
    """四元数 (w, x, y, z) -> 3x3 旋转矩阵"""
    q = np.asarray(q, dtype=np.float64)
    n = np.linalg.norm(q)
    if n < EPS:
        return np.eye(3)
    w, x, y, z = q / n
    return np.array([
        [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)],
        [2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)],
        [2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)],
    ])


def rotvec_from_mat(R):
    """3x3 旋转矩阵 -> 旋转向量(轴 * 角度)。用于"按轴分离"地收敛旋转幅度。"""
    q = mat_to_quat(orthonormalize(R))
    if q[0] < 0.0:                      # 取短弧，保证角度落在 [0, pi]
        q = -q
    w = max(-1.0, min(1.0, float(q[0])))
    ang = 2.0 * np.arccos(w)
    s = np.sqrt(max(0.0, 1.0 - w * w))
    if s < 1e-9:
        if ang < 0.5:                   # 零旋转
            return np.zeros(3)
        axis = q[1:]                    # 接近 pi，用虚部定轴
        n = np.linalg.norm(axis)
        return axis / n * ang if n > EPS else np.zeros(3)
    return q[1:] / s * ang


def mat_from_rotvec(v):
    """旋转向量 -> 3x3 旋转矩阵(Rodrigues)"""
    v = np.asarray(v, dtype=np.float64)
    ang = float(np.linalg.norm(v))
    if ang < 1e-12:
        return np.eye(3)
    k = v / ang
    K = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
    return np.eye(3) + np.sin(ang) * K + (1.0 - np.cos(ang)) * (K @ K)


def weighted_shape(src, w):
    """加权点集的形状统计：质心、三主轴上的加权 RMS 半径、主轴方向。

    这是判断"这根骨骼能不能被数据唯一定解"的唯一可靠依据：
    Procrustes 的旋转解在点集**近共线**时会失去一个自由度（绕共线轴的旋转
    在数据上完全不产生位移），SVD 于是给出一个由数值噪声决定的随机旋转。
    共面不受影响（平面内的对应关系足以定出完整旋转），所以判据用
    sqrt(sigma_j^2 + sigma_k^2) / sigma_max，而不是简单的 sigma_min/sigma_max。

    :return: (centroid(3), sigma(3) 降序, axes(3,3) 列为主轴) 或 None
    """
    src = np.asarray(src, dtype=np.float64)
    w = np.asarray(w, dtype=np.float64)
    sw2 = float(np.sum(w * w))
    if src.shape[0] < 3 or sw2 <= EPS:
        return None
    ww = w * w
    c = (src * ww[:, None]).sum(axis=0) / sw2
    d = src - c
    C = (d * ww[:, None]).T @ d
    vals, vecs = np.linalg.eigh(C)
    order = np.argsort(vals)[::-1]
    vals = np.clip(vals[order], 0.0, None)
    vecs = vecs[:, order]
    sigma = np.sqrt(vals / sw2)
    return c, sigma, vecs


def rotation_identifiability(sigma):
    """绕三个主轴的旋转各有多大程度上被数据约束（无量纲，1=完全可定）。

    绕 e_i 旋转角度 θ 时，各点位移的二阶量正比于 `sw2*(sigma_j^2+sigma_k^2)*θ^2`
    （j,k 是另外两轴），因此以 sigma_max 归一化后即为可辨识度。
    """
    s = np.asarray(sigma, dtype=np.float64)
    denom = max(float(s[0]), EPS)
    return np.array([
        np.hypot(s[1], s[2]) / denom,
        np.hypot(s[0], s[2]) / denom,
        np.hypot(s[0], s[1]) / denom,
    ])


def bones_within_reach(pivots, rest, target, candidates=None, max_dist=0.0,
                       move_ratio=1e-3, chunk=256):
    """按"骨骼离被雕刻区域有多远"来筛参与计算的骨骼。

    用户的语义：**超出偏移顶点一定距离的骨骼不参与计算**。这里的"距离"取
    "骨骼在绑定姿态的位置 → 距离它最近的那个被雕刻过的顶点"的欧氏距离：
    骨骼离雕刻区域越远，它在这块形变里能起的作用越小，硬让它参与反而会用
    一大坨平移去凑局部残差（典型症状：下巴附近的骨骼被解出 10 单位的位移）。

    :param pivots: (B,3) 骨骼在绑定姿态下的位置（物体空间）
    :param rest:   (N,3) 原始顶点
    :param target: (N,3) 雕刻后的顶点
    :param candidates: 候选骨骼下标；None = 全部
    :param max_dist: 距离上限；<= 0 表示不过滤
    :param move_ratio: "这个顶点算不算被雕刻过"的阈值 = 该比例 × 最大位移
    :param chunk: 分块大小（避免 B×N 的距离矩阵过大）
    :return: (kept, dropped, detail) —— detail 是 [(下标, 距离), ...]
    """
    B = int(np.asarray(pivots).shape[0])
    cand = list(range(B)) if candidates is None else [int(c) for c in candidates]
    if max_dist is None or float(max_dist) <= 0.0 or not cand:
        return cand, [], [(j, 0.0) for j in cand]

    rest = np.asarray(rest, dtype=np.float64).reshape(-1, 3)
    target = np.asarray(target, dtype=np.float64).reshape(-1, 3)
    pivots = np.asarray(pivots, dtype=np.float64).reshape(-1, 3)
    disp = np.linalg.norm(target - rest, axis=1)
    if disp.size == 0:
        return cand, [], [(j, 0.0) for j in cand]
    thr = max(float(move_ratio) * float(disp.max()), 1e-9)
    moved = rest[disp > thr]
    if moved.shape[0] == 0:
        return cand, [], [(j, 0.0) for j in cand]

    cand_arr = np.asarray(cand, dtype=int)
    nearest = np.full(cand_arr.size, np.inf, dtype=np.float64)
    for s in range(0, moved.shape[0], int(max(1, chunk))):
        block = moved[s:s + int(max(1, chunk))]
        d = np.sqrt(_sqdist(pivots[cand_arr], block))
        nearest = np.minimum(nearest, d.min(axis=1))

    kept, dropped, detail = [], [], []
    for k, j in enumerate(cand_arr):
        dist = float(nearest[k])
        detail.append((int(j), dist))
        if dist <= float(max_dist):
            kept.append(int(j))
        else:
            dropped.append(int(j))
    return kept, dropped, detail


def influence_share(rest, weights, target, candidates=None, pivots=None,
                    move_ratio=1e-3):
    """每根骨骼"这次雕刻有多依赖它"的度量。

    定义::

        drive_j = Σ_{i ∈ 被雕刻过的顶点} w_ij · |δ_i|

    含义很直接：**这根骨骼完全不动时，它负责的那片顶点最多还能被带动多少**。
    它同时照顾到两种"离得远"：

      * 骨骼位置离雕刻区很远 → 那些顶点上 w 基本为 0 → drive ≈ 0
      * 骨骼虽然覆盖整张脸，但被改的只是嘴角，它在那儿 w 只有 0.02 → drive 也小

    所以"只有我改的那片附近的骨骼才参与计算"可以完全自动化，不必让用户
    一根根勾选（自动推荐之所以会漏骨骼，是因为它按"单根骨骼单独求解后整体误差
    最小"排序，覆盖大区域的主骨永远排在前面，真正贴着雕刻区的小骨反而进不了前 N）。

    :param pivots: (B,3) 骨骼绑定位姿；给了就顺带算 `reach`（到最近被雕刻顶点的距离）
    :param move_ratio: "这个顶点算被雕刻过"的门槛 = 该比例 × 最大位移
    :return: dict
        - ``moved_n`` (int) 被雕刻顶点数
        - ``drive``  (B,) 每根骨骼的驱动量（未归一化）
        - ``share``  (B,) ``drive / max(drive)``，0~1，与骨骼数量/单位无关
        - ``reach``  (B,) 到最近被雕刻顶点的距离；没给 pivots 时为 None
    """
    rest = np.asarray(rest, dtype=np.float64).reshape(-1, 3)
    target = np.asarray(target, dtype=np.float64).reshape(-1, 3)
    W = np.asarray(weights, dtype=np.float64)
    B = W.shape[1]
    cand = list(range(B)) if candidates is None else [int(c) for c in candidates]
    out = {"moved_n": 0, "drive": np.zeros(B), "share": np.zeros(B),
           "reach": None, "candidates": cand}
    if not cand or rest.shape[0] == 0:
        return out

    disp = np.linalg.norm(target - rest, axis=1)
    thr = max(float(move_ratio) * float(disp.max()), 1e-12)
    moved = np.nonzero(disp > thr)[0]
    out["moved_n"] = int(moved.size)
    if moved.size == 0:
        return out

    cand_arr = np.asarray(cand, dtype=int)
    wm = W[moved][:, cand_arr]                          # (n_moved, n_cand)
    drive = (wm * disp[moved][:, None]).sum(axis=0)
    drive = np.maximum(drive, 0.0)
    out["drive"][cand_arr] = drive
    top = float(drive.max()) if drive.size else 0.0
    if top > 0.0:
        out["share"][cand_arr] = drive / top

    if pivots is not None:
        piv = np.asarray(pivots, dtype=np.float64).reshape(-1, 3)
        pts = rest[moved]
        reach = np.full(B, np.inf)
        for k, j in enumerate(cand_arr):
            d = pts - piv[j]
            reach[j] = float(np.sqrt((d * d).sum(axis=1)).min())
        out["reach"] = reach
    return out


def select_influential_bones(rest, weights, target, candidates=None, pivots=None,
                             min_share=0.005, max_dist=0.0, move_ratio=1e-3):
    """按"这次雕刻实际依赖谁"圈定参与计算的骨骼。

    :param min_share: 影响阈值（0~1）。``share`` 低于它的骨骼不参与——它们对这次
        雕刻的贡献不到主骨骼的千分之几，解出来只会是噪声（额头/眼睛附近那些
        "莫名其妙跟着动"的骨骼就是这一档）。
    :param max_dist: 距离上限，<=0 表示不按距离限制。距离与影响是**两个独立判据**
        （骨骼可能既远又确实驱动了那片顶点，例如下颌骨带动嘴角），任一超限就剔掉。
    :return: (kept, dropped, info)
        - ``kept``  参与计算的下标列表（按影响从大到小）
        - ``dropped`` [(下标, 原因), ...]
        - ``info``  ``influence_share`` 的返回，外加 ``fallback`` 标记
    """
    info = influence_share(rest, weights, target, candidates=candidates,
                           pivots=pivots, move_ratio=move_ratio)
    cand = info["candidates"]
    if not cand:
        return [], [], info

    share = info["share"]
    reach = info["reach"]
    min_share = float(min_share or 0.0)
    max_dist = float(max_dist or 0.0)

    kept, dropped = [], []
    for j in cand:
        reasons = []
        if share[j] < min_share:
            reasons.append(u"影响 %.3f%% < %.3f%%" % (100.0 * share[j], 100.0 * min_share))
        if reach is not None and max_dist > 0.0 and reach[j] > max_dist:
            reasons.append(u"距离 %.3f > %.3f" % (reach[j], max_dist))
        if reasons:
            dropped.append((int(j), u"，".join(reasons)))
        else:
            kept.append(int(j))

    # 全被剔掉时至少留下影响最大的那根：宁可算得粗，也不能让工具无法工作，
    # 而且要让用户看到"阈值设得太狠了"。
    info["fallback"] = False
    if not kept:
        best = int(np.argmax(share))
        if share[best] > 0.0:
            kept = [best]
            dropped = [d for d in dropped if d[0] != best]
            info["fallback"] = True
        else:
            kept = list(cand)               # 完全没信息，退回全部
            dropped = []
            info["fallback"] = True

    kept.sort(key=lambda j: (-share[j], j))
    return kept, dropped, info


def _sqdist(a, b):
    """(m,3) 与 (n,3) 的两两平方距离 -> (m,n)"""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    d = (a * a).sum(axis=1)[:, None] + (b * b).sum(axis=1)[None, :] - 2.0 * (a @ b.T)
    return np.maximum(d, 0.0)


def slerp(q0, q1, t):
    """球面线性插值"""
    q0 = np.asarray(q0, dtype=np.float64)
    q1 = np.asarray(q1, dtype=np.float64)
    d = float(np.dot(q0, q1))
    if d < 0.0:                     # 取短弧
        q1 = -q1
        d = -d
    if d > 0.9995:
        return _normalize(q0 + (q1 - q0) * t)
    theta = np.arccos(max(-1.0, min(1.0, d)))
    s = np.sin(theta)
    return _normalize(q0 * (np.sin((1.0 - t) * theta) / s) + q1 * (np.sin(t * theta) / s))


def _normalize(v):
    n = np.linalg.norm(v)
    return v / n if n > EPS else v


def blend_matrix(A0, A1, t, rigid=True):
    """在两个 4x4 变换之间插值。
    rigid=True 时旋转走 slerp、平移走线性插值（推荐，等价于骨骼的 TRS 插值）；
    rigid=False 时直接线性插值矩阵（仿射模式）。"""
    A0 = np.asarray(A0, dtype=np.float64)
    A1 = np.asarray(A1, dtype=np.float64)
    if t <= 0.0:
        return A0.copy()
    if t >= 1.0:
        return A1.copy()
    out = np.eye(4)
    if rigid:
        R0 = orthonormalize(A0[:3, :3])
        R1 = orthonormalize(A1[:3, :3])
        out[:3, :3] = quat_to_mat(slerp(mat_to_quat(R0), mat_to_quat(R1), t))
    else:
        out[:3, :3] = (1.0 - t) * A0[:3, :3] + t * A1[:3, :3]
    out[:3, 3] = (1.0 - t) * A0[:3, 3] + t * A1[:3, 3]
    return out


def blend_matrices(A0, A1, t, rigid=True):
    """批量版 blend_matrix。A0/A1: (B,4,4)"""
    A0 = np.asarray(A0, dtype=np.float64)
    A1 = np.asarray(A1, dtype=np.float64)
    out = np.empty_like(A0)
    for j in range(A0.shape[0]):
        out[j] = blend_matrix(A0[j], A1[j], t, rigid)
    return out


# ----------------------------------------------------------------------------
# 加权 Procrustes (Kabsch) —— SSDR 公式 (9)(10)
# ----------------------------------------------------------------------------
def prior_probe_points(src, w, sw=None):
    """生成用于"软先验"的探针点：质心 + 三根长度为 RMS 半径的正交臂。

    这些点**不参与顶点误差**，只是告诉求解器"数据没说的方向，就按先验来"。
    尺度取该区域的 RMS 半径，保证先验与数据的量纲一致——这样先验强度可以写成
    一个纯无量纲的比例，对确定方向的影响正比于该比例（极小），对不可判定方向
    则给出唯一解。返回 None 表示点集退化到无法定尺度。
    """
    src = np.asarray(src, dtype=np.float64).reshape(-1, 3)
    w = np.asarray(w, dtype=np.float64).ravel()
    sw = float(w.sum()) if sw is None else float(sw)
    if src.shape[0] < 3 or sw <= EPS:
        return None
    c = (src * w[:, None]).sum(axis=0) / sw
    h = float(np.sqrt(np.sum(w * np.sum((src - c) ** 2, axis=1)) / sw))
    if not np.isfinite(h) or h <= EPS:
        return None
    return c + np.array([[0.0, 0.0, 0.0],
                         [h, 0.0, 0.0],
                         [0.0, h, 0.0],
                         [0.0, 0.0, h]])


def weighted_procrustes(src, dst, w, allow_affine=False, reg=0.0,
                        prior=None, prior_frac=0.0):
    """求解 min_R,t  sum_i w_i * || R*src_i + t - dst_i ||^2

    这是一个"加权"最小二乘，正好对应线性混合蒙皮(LBS)的误差函数。
    allow_affine=True 允许缩放/剪切(仿射)，用于需要体积补偿的骨骼。

    ``prior`` / ``prior_frac``：**软先验**（正则化），解决"点集近共线时绕共线轴
    的自旋不可判定"这个病态问题。做法是把先验矩阵作用在几个探针点上（见
    prior_probe_points），以 ``prior_frac × 数据总权重`` 的权重混进同一次最小二乘。

    这比"解完再把可疑分量裁掉"严格得多：它是同一个二次型的最优解，
    数据能定的方向照样精确跟随数据（误差只有 prior_frac 量级；用默认的 1e-6
    实测角度偏差 2e-4°、RMS 变化 4e-6），数据定不了的方向唯一地退回先验（= 不动）。
    既不会出现 180° 假旋转，也不会把真实的旋转一并压死。

    :return: 4x4 变换; 若权重过小(无法求解)返回 None
    """
    src = np.asarray(src, dtype=np.float64).reshape(-1, 3)
    dst = np.asarray(dst, dtype=np.float64).reshape(-1, 3)
    w = np.asarray(w, dtype=np.float64).ravel()
    sw = float(w.sum())
    if sw <= NONZERO_W * 3 or src.shape[0] < 3:
        return None

    if prior is not None and float(prior_frac) > 0.0:
        probe = prior_probe_points(src, w, sw)
        if probe is not None:
            P = as_mat4(prior)
            src = np.vstack([src, probe])
            dst = np.vstack([dst, transform_points(P, probe)])
            w = np.concatenate([w, np.full(probe.shape[0], float(prior_frac) * sw)])
            sw = float(w.sum())

    src_bar = (src * w[:, None]).sum(axis=0) / sw
    dst_bar = (dst * w[:, None]).sum(axis=0) / sw
    s = (src - src_bar) * w[:, None]
    d = dst - dst_bar

    H = s.T @ d                      # 3x3, 加权协方差

    if allow_affine:
        Cvv = s.T @ (src - src_bar)  # 加权自相关
        if reg > 0.0:
            Cvv = Cvv + np.eye(3) * (reg * float(np.trace(Cvv)) / 3.0 + EPS)
        try:
            M = H @ np.linalg.inv(Cvv)
        except np.linalg.LinAlgError:
            return None
        out = np.eye(4)
        out[:3, :3] = M
        out[:3, 3] = dst_bar - M @ src_bar
        return out

    U, _S, Vt = np.linalg.svd(H)
    R = Vt.T @ U.T
    if np.linalg.det(R) < 0:
        Vt = Vt.copy()
        Vt[-1, :] *= -1.0
        R = Vt.T @ U.T
    out = np.eye(4)
    out[:3, :3] = R
    out[:3, 3] = dst_bar - R @ src_bar
    return out


# ----------------------------------------------------------------------------
# 求解器
# ----------------------------------------------------------------------------
class SkinningSolver(object):
    """已知权重、已知 rest 顶点、已知目标顶点 —— 反求骨骼变换。

    用法::

        solver = SkinningSolver(rest_pts, weights, cur_transforms, target_pts,
                                bone_names=names, active=selected_indices)
        rep = solver.solve(iterations=25)
        A_new = solver.transforms            # (B,4,4) 物体空间蒙皮矩阵
        blended = solver.blended(0.5)        # 与初始状态插值
    """

    def __init__(self, rest_points, weights, transforms, target_points,
                 bone_names=None, active=None):
        V = np.ascontiguousarray(np.asarray(rest_points, dtype=np.float64).reshape(-1, 3))
        W = np.ascontiguousarray(np.asarray(weights, dtype=np.float64))
        A = np.ascontiguousarray(np.asarray(transforms, dtype=np.float64))
        T = np.ascontiguousarray(np.asarray(target_points, dtype=np.float64).reshape(-1, 3))

        if W.ndim != 2:
            raise ValueError("weights 必须是 (顶点数, 骨骼数) 的二维数组")
        if W.shape[0] != V.shape[0]:
            raise ValueError("权重行数(%d)与顶点数(%d)不一致" % (W.shape[0], V.shape[0]))
        if A.shape != (W.shape[1], 4, 4):
            raise ValueError("transforms 形状应为 (%d,4,4)，实际 %s" % (W.shape[1], A.shape))
        if T.shape != V.shape:
            raise ValueError("目标顶点数与 rest 顶点数不一致: %s vs %s" % (T.shape, V.shape))

        self.V = V
        self.W = W
        self.A = A.copy()
        self.A0 = A.copy()           # 求解前的状态（滑块 0% 时的姿态）
        self.T = T
        self.N, self.B = V.shape[0], W.shape[1]
        self.bone_names = list(bone_names) if bone_names else ["bone_%d" % i for i in range(self.B)]

        if active is None:
            mask = np.ones(self.B, dtype=bool)
        else:
            mask = np.zeros(self.B, dtype=bool)
            for i in np.asarray(active, dtype=int).ravel():
                if 0 <= i < self.B:
                    mask[i] = True
        self.active = mask

        self._idx = []
        self._w = []
        for j in range(self.B):
            nz = np.nonzero(self.W[:, j] > NONZERO_W)[0]
            self._idx.append(nz)
            self._w.append(self.W[nz, j])

        # 权重支撑：该骨骼"等效控制了多少个顶点"（权重已归一化，所以就是加权和）。
        # 支撑极小的骨骼在数据上几乎不可约束——硬解它必然得到又大又假的偏移
        # （典型症状：贴在网格边缘、只勉强影响一个顶点的骨骼被解出 90° 旋转
        #  + 十几单位平移）。见 solve(min_support=...)。
        self.support = self.W.sum(axis=0)

        # 每根骨骼参与最小二乘的顶点掩码（与 _lsq_target 里的规则一致，
        # 预先算好，后面既用于求解也用于形状统计，避免两处口径不一致）。
        self._mask = []
        for j in range(self.B):
            w = self._w[j]
            if w.size == 0:
                self._mask.append(np.zeros(0, dtype=bool))
                continue
            thr = max(1e-3, 0.02 * float(w.max()))
            m = w >= thr
            if int(m.sum()) < 3:
                m = np.ones(w.size, dtype=bool)
            self._mask.append(m)

        # 可辨识度统计（决定"这根骨骼的解该信多少"）
        self._sculpt_disp = np.linalg.norm(self.T - self.V, axis=1)
        self.stats = [self._bone_stats(j) for j in range(self.B)]

        self._Q = None
        self._P = None
        self._reset_state()

    # -- 逐骨骼的可辨识度 / 约束强度 -----------------------------------------
    def _bone_stats(self, j):
        idx = self._idx[j]
        w = self._w[j]
        m = self._mask[j] if self._mask[j].size == w.size else np.ones(w.size, dtype=bool)
        idx2, w2 = idx[m], w[m]
        out = {"n": int(idx2.size), "support": float(self.support[j]),
               "eff_n": 0.0, "w_eff": 0.0, "sigma": None, "axes": None,
               "centroid": None, "rel_rot": 0.0, "conf_rot": 1.0, "disp_rms": 0.0}
        if idx2.size == 0 or w2.size == 0:
            return out
        sw = float(w2.sum())
        sw2 = float(np.sum(w2 * w2))
        # Kish 有效样本量：权重越不均匀，"等效有几个顶点"越少。小到一定程度时
        # 目标点 u = r/w 被放大得离谱，平移完全由残差噪声决定。
        out["eff_n"] = (sw * sw / sw2) if sw2 > EPS else 0.0
        # 该骨骼的平均权重（按 w 加权）：骨骼位移乘它才是它对顶点位移的贡献量级
        out["w_eff"] = (sw2 / sw) if sw > EPS else 0.0
        shape = weighted_shape(self.V[idx2], w2)
        if shape is None:
            return out
        c, sigma, axes = shape
        out["centroid"], out["sigma"], out["axes"] = c, sigma, axes
        rel = rotation_identifiability(sigma)
        # 只有绕主轴的旋转可能失去约束，另外两轴恒 >= 1
        out["rel_rot"] = float(rel[0])
        out["disp_rms"] = float(np.sqrt(np.sum(w2 * self._sculpt_disp[idx2] ** 2) / max(sw, EPS)))
        out["idx2"] = idx2
        out["w2"] = w2
        return out

    # -- 状态 ---------------------------------------------------------------
    def _reset_state(self):
        self._Q = [transform_points(self.A[j], self.V[self._idx[j]]) for j in range(self.B)]
        P = np.zeros((self.N, 3), dtype=np.float64)
        for j in range(self.B):
            if self._idx[j].size:
                P[self._idx[j]] += self._w[j][:, None] * self._Q[j]
        self._P = P

    def _refresh_P(self):
        P = np.zeros((self.N, 3), dtype=np.float64)
        for j in range(self.B):
            if self._idx[j].size:
                P[self._idx[j]] += self._w[j][:, None] * (self.V[self._idx[j]] @ self.A[j][:3, :3].T + self.A[j][:3, 3])
        self._P = P
        self._Q = [transform_points(self.A[j], self.V[self._idx[j]]) for j in range(self.B)]

    def set_transforms(self, A):
        self.A = np.array(A, dtype=np.float64).reshape(self.B, 4, 4)
        self._reset_state()

    def set_bone_transform(self, index, A, refresh=True):
        """直接改单根骨骼的结果矩阵（镜像功能用）。

        :param refresh: 是否立刻重算预测位置。批量改多根时传 False，
            最后调一次 refresh() 即可，避免每根都算一遍全网格。
        """
        self.A[int(index)] = np.asarray(A, dtype=np.float64).reshape(4, 4)
        if refresh:
            self._refresh_P()

    def activate(self, indices):
        """把骨骼加入"参与"集合（镜像写入对侧骨骼时用，否则不会被写回）。

        :return: 新加入的骨骼下标
        """
        added = []
        for j in np.asarray(indices, dtype=int).ravel():
            if 0 <= j < self.B and not self.active[j]:
                self.active[j] = True
                added.append(int(j))
        return added

    def refresh(self):
        """重算内部缓存的预测位置（直接改过 A 之后调用）。"""
        self._refresh_P()

    # -- 误差 ---------------------------------------------------------------
    def error(self):
        """返回 (rms, max) 顶点误差"""
        if self.N == 0:
            return 0.0, 0.0
        d = np.linalg.norm(self._P - self.T, axis=1)
        return float(np.sqrt(np.mean(d * d))), float(d.max())

    def predicted_points(self):
        return self._P.copy()

    # -- 单根骨骼的局部最小二乘(SSDR 公式 6~8) ------------------------------
    def _lsq_target(self, j):
        """给定其他骨骼不变，构造骨骼 j 的加权最小二乘问题。

        由  p_i = sum_k w_ik A_k v_i  可得
            w_ij * A_j v_i = p_i^* - sum_{k!=j} w_ik A_k v_i  =: r_i
        因此求解  min_{A_j} sum_i w_ij^2 * || A_j v_i - r_i / w_ij ||^2
        (权重取 w^2、目标取 r/w —— 与论文公式(8)等价，且恰好等价于
         最小化整体 LBS 重建误差)
        """
        idx = self._idx[j]
        w = self._w[j]
        if idx.size < 3:
            return None
        m = self._mask[j]
        if m.size != w.size:
            m = np.ones(w.size, dtype=bool)
        if int(m.sum()) < 3:
            m = np.ones(w.size, dtype=bool)
        idx2 = idx[m]
        w2 = w[m]
        q_old = self._Q[j][m]
        r = self.T[idx2] - (self._P[idx2] - w2[:, None] * q_old)
        return idx2, r / w2[:, None], w2 * w2

    # -- 主循环 -------------------------------------------------------------
    def solve(self, iterations=25, tolerance=1e-4, allow_affine=False,
              damping=1.0, reg=0.0, callback=None, min_support=2.0,
              stabilize=True, prior_frac=1e-6, min_eff_n=2.5, degenerate_rel=0.2,
              prune_translate=0.0, prune_rotate=0.0, prune_passes=3):
        """块坐标下降求解。返回报告 dict。

        :param min_support: 权重支撑下限（等效顶点数）。低于它的骨骼保持不动——
            这类骨骼在数据上根本无法被约束（只勉强影响一两个顶点），硬解只会
            得到巨大且毫无意义的偏移。
        :param stabilize: **软先验**（默认开）。点集近共线时绕共线轴的自旋在数据上
            不可判定，SVD 会返回由数值噪声决定的任意旋转——这就是"莫名 180°
            + 好几单位平移"的来历。打开后用一个极小权重的先验把这类方向唯一地
            定为"不动"，而数据能定的方向照样精确跟随数据（偏差 only prior_frac 量级）。
            它**不会**削弱真实的旋转/位移，所以不存在"开了稳定模式就不拟合"。
        :param prior_frac: 先验强度（相对数据总权重）。1e-6 表示对可判定方向的
            影响约为百万分之一（实测角度偏差 2e-4°、RMS 增加 3e-6），
            同时足以把严格不可判定的方向完全钉住（180° -> 0.01°）。
        :param min_eff_n: 有效顶点数（Kish）下限。权重极不均匀、等效只有两个顶点的
            骨骼，目标点会被 1/w 放大到失去意义，直接保持不动。
        :param degenerate_rel: 只用于**报告**：可辨识度低于此值的骨骼会在
            ``degenerate`` 里列出（点集近共线，绕带轴方向不可判定），供日志提示。
        :param prune_translate: **重要度阈值（位移）**。解完后，旋转与位移**同时**
            低于 ``prune_rotate`` / ``prune_translate`` 的骨骼被视为"这次雕刻不需要
            它动"，直接退回原姿态（A = A0），并把腾出来的那点残差交给其余骨骼
            重解一遍。0 = 不过滤。
        :param prune_rotate: 重要度阈值（旋转，度）。语义同上。
        :param prune_passes: 冻结-重解的最多轮数。冻结只会让解更"干净"，不会越解越乱。
        """
        order = np.nonzero(self.active)[0]
        rep = {"iterations": 0, "rms": 0.0, "max": 0.0, "rms_before": 0.0, "history": [],
               "solved_bones": [], "skipped_bones": [], "low_support": [],
               "stabilized": stabilize, "degenerate": [], "locked_reasons": {},
               "frozen": [], "frozen_detail": []}

        # 支撑过小的骨骼先剔掉（保持 A = A0，等于"这根骨骼我没动"）
        keep = []
        for j in order:
            st = self.stats[j]
            reason = None
            if st["n"] < 3:
                reason = u"影响顶点不足 3 个"
            elif self.support[j] < float(min_support):
                reason = u"权重支撑 %.2f < %.2f" % (self.support[j], float(min_support))
            elif st["eff_n"] < float(min_eff_n):
                reason = u"有效顶点数 %.1f < %.1f" % (st["eff_n"], float(min_eff_n))
            elif st["axes"] is None:
                reason = u"几何退化，无法定位"
            if reason:
                rep["low_support"].append(self.bone_names[j])
                rep["locked_reasons"][self.bone_names[j]] = reason
            else:
                keep.append(j)
        # 约束强的骨骼先解：先把残差里"说得清"的部分吃掉，弱约束骨骼轮到时
        # 残差已经很小，自然不会去抢。这也顺便去掉了对勾选顺序的依赖。
        keep.sort(key=lambda j: (-self.support[j], j))
        order = np.asarray(keep, dtype=int)
        rep["solved_bones"] = [self.bone_names[j] for j in order]

        if stabilize:
            for j in order:
                if self.stats[j]["rel_rot"] < float(degenerate_rel):
                    rep["degenerate"].append(self.bone_names[j])

        if order.size == 0 or self.N == 0:
            rep["rms"], rep["max"] = self.error()
            rep["rms_before"] = rep["rms"]
            rep["deltas"] = self.bone_deltas()
            return rep

        rms_before, _ = self.error()
        rep["rms_before"] = rms_before

        order = self._sweep(order, iterations, tolerance, allow_affine, damping, reg,
                            callback, stabilize, prior_frac, rep)

        # ---- 重要度过滤 ---------------------------------------------------
        # 解出来"几乎没动"的骨骼说明这次雕刻根本不需要它：典型就是只雕了嘴角，
        # 额头/眼睛附近的骨骼为了抵消邻接骨骼的溢出而挪了千分之几度。把它退回
        # 原姿态（等于"这根骨骼我没动"），再让真正需要的骨骼重解一次把残差吃掉。
        # 注意这一步只砍"已经很小"的量，不做任何按比例收缩，所以不会像 v1.2 那样
        # 把真实的旋转削弱掉。
        trans_thr = float(prune_translate or 0.0)
        rot_thr = float(prune_rotate or 0.0)
        if (trans_thr > 0.0 or rot_thr > 0.0) and order.size:
            refit = max(3, min(int(iterations), 12))
            for _ in range(max(1, int(prune_passes))):
                rest_order, detail = self._freeze_insignificant(order, trans_thr, rot_thr)
                if not detail:
                    break
                for j, ang, tr in detail:
                    name = self.bone_names[j]
                    self.active[j] = False
                    rep["frozen"].append(name)
                    rep["frozen_detail"].append(
                        (name, u"旋转 %.3f° < %.2f° 且 位移 %.4f < %.4f" %
                         (ang, rot_thr, tr, trans_thr)))
                order = rest_order
                if order.size == 0:
                    break
                order = self._sweep(order, refit, tolerance, allow_affine, damping, reg,
                                    None, stabilize, prior_frac, rep)

        rep["rms"], rep["max"] = self.error()
        rep["deltas"] = self.bone_deltas()
        rep["solved_bones"] = [self.bone_names[j] for j in order]
        rep["active_count"] = int(order.size)
        return rep

    def _sweep(self, order, iterations, tolerance, allow_affine, damping, reg,
               callback, stabilize, prior_frac, rep):
        """对给定骨骼序列跑一轮块坐标下降（可被"冻结后重解"复用）。"""
        if order.size == 0:
            return order
        prev = None
        for it in range(max(1, int(iterations))):
            for j in order:
                idx = self._idx[j]
                if idx.size < 3:
                    if self.bone_names[j] not in rep["skipped_bones"]:
                        rep["skipped_bones"].append(self.bone_names[j])
                    continue
                prob = self._lsq_target(j)
                if prob is None:
                    continue
                idx2, u, wt = prob
                new_A = weighted_procrustes(
                    self.V[idx2], u, wt, allow_affine=allow_affine, reg=reg,
                    prior=(self.A0[j] if stabilize else None),
                    prior_frac=(float(prior_frac) if stabilize else 0.0))
                if new_A is None:
                    continue
                if damping < 1.0:
                    new_A = blend_matrix(self.A[j], new_A, damping, rigid=not allow_affine)
                if not np.all(np.isfinite(new_A)):
                    continue
                w = self._w[j]
                q_old = self._Q[j]
                q_new = transform_points(new_A, self.V[idx])
                self.A[j] = new_A
                self._Q[j] = q_new
                self._P[idx] += w[:, None] * (q_new - q_old)
            # 每轮重算一次累加位置，避免浮点漂移
            self._refresh_P()
            rms, mx = self.error()
            rep["history"].append(rms)
            rep["iterations"] += 1
            if callback is not None:
                try:
                    callback(rep["iterations"], rms)
                except Exception:
                    pass
            if rms < 1e-9:
                break
            if prev is not None and prev > 0 and (prev - rms) <= tolerance * prev:
                break
            prev = rms
        return order

    def _freeze_insignificant(self, order, trans_thr, rot_thr):
        """把"旋转与位移同时低于阈值"的骨骼退回原姿态。

        :return: (保留的下标数组, [(下标, 角度, 平移), ...])
        """
        detail = []
        for j in order:
            R0 = orthonormalize(self.A0[j][:3, :3])
            R1 = orthonormalize(self.A[j][:3, :3])
            ang = float(np.degrees(rotation_angle(R1 @ R0.T)))
            tr = float(np.linalg.norm(self.A[j][:3, 3] - self.A0[j][:3, 3]))
            if ang <= float(rot_thr) and tr <= float(trans_thr):
                detail.append((int(j), ang, tr))
        if not detail:
            return order, detail
        dead = set(j for j, _, _ in detail)
        for j in dead:
            self.A[j] = self.A0[j].copy()
        self._refresh_P()
        keep = np.asarray([j for j in order if j not in dead], dtype=int)
        return keep, detail

    # -- 结果 ---------------------------------------------------------------
    @property
    def transforms(self):
        return self.A.copy()

    def blended(self, t, rigid=True):
        """把求解结果与求解前状态按 t∈[0,1] 插值。"""
        return blend_matrices(self.A0, self.A, float(t), rigid=rigid)

    def bone_deltas(self):
        """每根骨骼相对求解前的变化量（角度 / 平移）。"""
        out = []
        for j in range(self.B):
            R0 = orthonormalize(self.A0[j][:3, :3])
            R1 = orthonormalize(self.A[j][:3, :3])
            ang = np.degrees(rotation_angle(R1 @ R0.T))
            trans = float(np.linalg.norm(self.A[j][:3, 3] - self.A0[j][:3, 3]))
            out.append({"name": self.bone_names[j], "angle": ang, "translate": trans,
                        "active": bool(self.active[j])})
        return out

    def rank_bones(self, candidates=None):
        """为每根候选骨骼单独求解一次，返回其单独求解后的整体 RMS。
        用来"按贡献度自动推荐"参与计算的骨骼。"""
        if candidates is None:
            candidates = range(self.B)
        candidates = [int(c) for c in candidates]
        base_rms, _ = self.error()
        out = []
        for j in candidates:
            prob = self._lsq_target(j)
            if prob is None:
                out.append((j, base_rms))
                continue
            idx2, u, wt = prob
            new_A = weighted_procrustes(self.V[idx2], u, wt)
            if new_A is None:
                out.append((j, base_rms))
                continue
            idx = self._idx[j]
            q_new = transform_points(new_A, self.V[idx])
            Pn = self._P.copy()
            Pn[idx] += self._w[j][:, None] * (q_new - self._Q[j])
            d = np.linalg.norm(Pn - self.T, axis=1)
            out.append((j, float(np.sqrt(np.mean(d * d)))))
        out.sort(key=lambda x: x[1])
        return out


def rms_of(A, rest, weights, target):
    """给定蒙皮矩阵快速评估 RMS（不建 solver，用于自检）。
    按骨骼逐根累加，避免一次性生成 (B,N,3) 的大数组。"""
    A = np.asarray(A, dtype=np.float64)
    rest = np.asarray(rest, dtype=np.float64)
    weights = np.asarray(weights, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    P = np.zeros_like(rest)
    for j in range(A.shape[0]):
        q = rest @ A[j][:3, :3].T + A[j][:3, 3]
        P += weights[:, j:j + 1] * q
    d = np.linalg.norm(P - target, axis=1)
    return float(np.sqrt(np.mean(d * d)))
