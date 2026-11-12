# -*- coding: utf-8 -*-
"""
离线测试：病态骨骼（点集近共线）既不能解出假偏移，也不能被"稳定化"压死不拟合。
    python tests/test_stability.py

背景（对应 MetaHuman 里 FACIAL_*_12IPV_ForeheadSkin* 这类补皮骨）：

  1. 骨骼只影响额头一条很窄的带 —— 加权点集近共线（秩 1）。**绕这条带主轴的
     自旋**在数据上不产生任何位移，目标函数在这个自由度上是平的，SVD 于是给出
     由数值噪声决定的任意旋转。实测：秩 1 点集上会解出 180.000° + 4.0 平移，
     而 RMS 依然只有 0.0052（"拟合完美但一动就废"）。
     注意**共面不受影响**（平面内的对应关系足以定出完整旋转），只有共线丢自由度。
  2. 权重小 —— 子问题目标 u = r/w 被放大 1/w 倍，平移对残差极其敏感。
  3. 残差守恒 —— 取消勾选 A 之后残差转嫁给 B，B 又超限。所以"让用户自己取消
     勾选"这条路是走不通的。

v1.3 的解法是**软先验**（weighted_procrustes(prior=..., prior_frac=...)）：
在同一个二次型里加一个极小权重的先验点集，把"数据没说的方向"唯一地定为不动。
它不会像 v1.2 的"事后硬裁剪"那样把数据说清楚的方向也一起压掉——那正是
"开启稳定模式就几乎没有拟合"的原因。本文件两头都锁：
  * 退化方向不得爆（180° → ~0°）；
  * 真实旋转不得被削弱（12° 必须还是 12°）。
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mh_ssdr import ssdr_core as core  # noqa: E402


# ---------------------------------------------------------------------------
def make_cloud(n=2000, seed=2):
    rng = np.random.RandomState(seed)
    return rng.uniform([-8.0, -6.0, 0.0], [8.0, 6.0, 4.0], size=(n, 3))


def make_collinear_rig(n=2000, seed=2, n_line=60, w_line=(0.85, 0.15)):
    """一条严格共线的顶点带，带内两根骨骼分摊；其余顶点由主骨控制。

    :return: V, W, A0, T, 诊断用的关键下标
    """
    rng = np.random.RandomState(seed)
    V = make_cloud(n, seed)
    B = 3
    W = np.zeros((n, B))
    W[:, 0] = 1.0
    line = np.argsort(np.abs(V[:, 1]) + np.abs(V[:, 2] - 2.0))[:n_line]
    V[line, 1] = 0.0                      # 拉到 x 轴上 -> sigma = (L, 0, 0)
    V[line, 2] = 2.0
    W[line, 0] = 0.0
    W[line, 1] = w_line[0]
    W[line, 2] = w_line[1]
    W /= W.sum(axis=1, keepdims=True)
    A0 = np.repeat(np.eye(4)[None], B, axis=0)
    T = V.copy()
    T[line, 2] += 0.5 + rng.normal(scale=0.03, size=len(line))
    return V, W, A0, T, line


def make_band_rig(n=4000, seed=7):
    """近共线窄带（真实 rig 的形态）+ 雕刻噪声，窄带骨被逼着吃残差。"""
    rng = np.random.RandomState(seed)
    V = make_cloud(n, seed)
    B = 4
    W = np.zeros((n, B))
    main = (np.abs(V[:, 0]) < 6) & (np.abs(V[:, 1]) < 5)
    W[main, 0] = 1.0
    band = np.exp(-((V[:, 1] - 3.0) ** 2) / (2 * 0.25 ** 2)) * (np.abs(V[:, 2] - 3.0) < 0.6)
    W[:, 1] = 0.15 * band
    W[main, 0] *= (1 - 0.15 * band[main])
    W[:, 2] = 0.02                                    # 极小权重铺满
    W[:, 3] = 0.12 * np.exp(-((V[:, 0] - 4.0) ** 2) / (2 * 0.25 ** 2))
    W /= np.maximum(W.sum(axis=1, keepdims=True), 1e-9)
    A0 = np.repeat(np.eye(4)[None], B, axis=0)
    T = V.copy()
    T[:, 2] += 1.2 * np.exp(-((V[:, 1] - 3.0) ** 2) / (2 * 1.2 ** 2))
    T += rng.normal(scale=0.01, size=T.shape)
    return V, W, A0, T


def make_strip_rig(n=3000, seed=3, halo=0.015):
    """细长窄带骨：核心权重 1.0（点集共线），周围一圈低权重"晕"。

    halo 取 0.015 < 2% × 1.0，所以晕上的顶点**不进**求解用的点集 mask，
    但它们在 LBS 里确实受这根骨骼驱动 —— 这正是"骨骼值乱飞会把网格拽坏"的地方。

    :return: V, W, A0, T, 真值矩阵
    """
    rng = np.random.RandomState(seed)
    V = make_cloud(n, seed)
    B = 3
    W = np.zeros((n, B))
    W[:, 0] = 1.0
    line = np.argsort(np.abs(V[:, 1] - 2.0) + np.abs(V[:, 2] - 2.5))[:200]
    V[line, 1] = 2.0                       # 严格共线
    V[line, 2] = 2.5
    W[line, 0] = 0.0
    W[line, 1] = 1.0
    near = (np.abs(V[:, 1] - 2.0) < 0.9) & (np.abs(V[:, 2] - 2.5) < 0.9)
    near[line] = False
    W[near, 0] = 1.0 - halo
    W[near, 1] = halo
    W /= W.sum(axis=1, keepdims=True)

    A0 = np.repeat(np.eye(4)[None], B, axis=0)
    A_true = A0.copy()
    c = np.array([0.0, 2.0, 2.5])
    ang = np.radians(12.0)
    K = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 0.0]])
    R = np.eye(3) + np.sin(ang) * K + (1 - np.cos(ang)) * (K @ K)
    A_true[1][:3, :3] = R
    A_true[1][:3, 3] = c - R @ c + np.array([0.0, 0.0, 0.4])
    T = np.einsum("nb,bni->ni", W, core.batch_transform(A_true, V))
    return V, W, A0, T, A_true


def solve(V, W, A0, T, active=None, **kw):
    s = core.SkinningSolver(V, W, A0, T, active=active)
    rep = s.solve(iterations=45, **kw)
    return s, rep


# ---------------------------------------------------------------------------
def test_identifiability_metric():
    """共线点集的可辨识度必须是 0；成面/成体的点集必须接近 1。"""
    rng = np.random.RandomState(4)
    line = np.zeros((40, 3))
    line[:, 0] = np.linspace(-5, 5, 40)
    c, sig, axes = core.weighted_shape(line, np.ones(40))
    rel_line = core.rotation_identifiability(sig)[0]

    plane = np.stack([rng.uniform(-5, 5, 40), rng.uniform(-5, 5, 40), np.zeros(40)], axis=1)
    c, sig, axes = core.weighted_shape(plane, np.ones(40))
    rel_plane = core.rotation_identifiability(sig)[0]

    blob = rng.normal(size=(40, 3)) * 3.0
    c, sig, axes = core.weighted_shape(blob, np.ones(40))
    rel_blob = core.rotation_identifiability(sig)[0]

    print("     可辨识度: 共线 %.3f ｜ 共面 %.3f ｜ 成体 %.3f" %
          (rel_line, rel_plane, rel_blob))
    assert rel_line < 1e-6, "严格共线必须判为 0"
    assert rel_plane > 0.5, "共面点集本身足以定出完整旋转，不该被判为退化"
    assert rel_blob > 0.7
    print("[OK] 可辨识度判据：共线=0，共面不受影响")


def test_collinear_bone_no_longer_explodes():
    """秩 1 点集：不加先验会解出 180°，加了必须回到 ~0°，且拟合精度不变。"""
    V, W, A0, T, line = make_collinear_rig()
    _, raw = solve(V, W, A0, T, stabilize=False)
    _, fix = solve(V, W, A0, T, stabilize=True)

    bad = raw["deltas"][2]
    good = fix["deltas"][2]
    print("     原始:   旋转 %8.2f°  平移 %7.3f" % (bad["angle"], bad["translate"]))
    print("     先验:   旋转 %8.2f°  平移 %7.3f   ｜ RMS %.7f → %.7f" %
          (good["angle"], good["translate"], raw["rms"], fix["rms"]))
    assert bad["angle"] > 90.0, "这个场景本来就该复现出假的大旋转（测试前提）"
    assert good["angle"] < 1.0, "退化骨骼仍然解出 %.1f°" % good["angle"]
    assert good["translate"] < 0.3, "退化骨骼仍然解出 %.3f 平移" % good["translate"]
    assert fix["rms"] <= raw["rms"] * 1.001 + 1e-9, "先验不该牺牲拟合精度"
    print("[OK] 秩 1（共线）骨骼：180°/4.0 → 0°，拟合精度不变")


def test_degenerate_but_real_rotation_survives():
    """关键回归：点集严格共线、但真值确实需要转 12° —— 不得被压成"不转"。

    v1.2 的硬裁剪在可辨识度 < 0.06 时把整个旋转归零，这类骨骼就完全不拟合了
    （这就是"开启稳定模式几乎没有拟合"的根因）。软先验只压掉绕带轴的自旋，
    带方向的改变（数据能定的部分）必须原样保留。
    """
    V, W, A0, T, A_true = make_strip_rig()
    s0 = core.SkinningSolver(V, W, A0, T, active=[0, 1, 2])
    rel = s0.stats[1]["rel_rot"]

    axis_true = A_true[1][:3, :3] @ np.array([1.0, 0.0, 0.0])
    rows = []
    for flag in (False, True):
        s, rep = solve(V, W, A0, T, active=[0, 1, 2], stabilize=flag)
        axis_got = s.transforms[1][:3, :3] @ np.array([1.0, 0.0, 0.0])
        err = float(np.degrees(np.arccos(min(1.0, abs(float(axis_true @ axis_got))))))
        rows.append((flag, rep, err))
        print("     stabilize=%-5s RMS %.3e ｜ 带轴方向误差 %.4f° ｜ 旋转 %.2f° 平移 %.3f" %
              (flag, rep["rms"], err, rep["deltas"][1]["angle"], rep["deltas"][1]["translate"]))
    print("     该骨骼可辨识度 rel_rot = %.5f（严格共线 -> 0）" % rel)

    raw, fix = rows[0][1], rows[1][1]
    assert rel < 1e-3, "测试前提：这条带必须是真的共线（rel_rot=%.4f）" % rel
    assert 11.0 < fix["deltas"][1]["angle"] < 13.5, \
        "真实旋转被改动：解出 %.2f°（应为 ~12°）" % fix["deltas"][1]["angle"]
    assert rows[1][2] < 0.5, "带轴方向错了 %.3f°" % rows[1][2]
    # 不可判定方向上的"旋转/平移配比"本身无法从数据恢复，先验按最小偏移选一个；
    # 因此允许它在网格上留下极小的差别（这里 1e-4 级，区域位移是 0.6 级）。
    assert fix["rms"] < 5e-3, "先验把退化场景的拟合弄坏了：RMS %.3e" % fix["rms"]
    print("[OK] 退化点集上的真实旋转被完整保留（A 的不可判定分量按最小偏移取）")


def test_narrow_band_bone_keeps_its_fit():
    """近共线窄带（真实 rig 形态）：先验不得改变解，RMS 不得变差。"""
    V, W, A0, T = make_band_rig()
    s_raw, raw = solve(V, W, A0, T, stabilize=False)
    s_fix, fix = solve(V, W, A0, T, stabilize=True)

    for name, rep in (("原始", raw), ("先验", fix)):
        rows = ["b%d %.1f°/%.3f" % (j, rep["deltas"][j]["angle"], rep["deltas"][j]["translate"])
                for j in range(len(rep["deltas"]))]
        print("     %s: %s" % (name, "  ".join(rows)))
    dang = max(float(np.degrees(core.rotation_angle(
        s_fix.transforms[j][:3, :3] @ s_raw.transforms[j][:3, :3].T)))
        for j in range(W.shape[1]))
    print("     RMS %.7f → %.7f ｜ 最大角度差 %.2e°" % (raw["rms"], fix["rms"], dang))
    assert abs(fix["rms"] - raw["rms"]) <= 1e-5 + raw["rms"] * 1e-4, "先验改变了拟合"
    # 这条窄带的可辨识度只有 0.06，属于"弱病态"：剩下的那点差别是数据本身
    # 定不死的方向，先验按最小偏移做了一个更保守的选择。
    assert dang < 0.5, "先验把解挪动了 %.3f°" % dang
    print("[OK] 正常（弱病态）数据上的解与不加先验时一致（差 %.3f°）" % dang)


def test_no_drift_over_many_iterations():
    """退化方向上不能逐轮累积漂移（v1.2 曾出现 0.9°→3.2°→…→56°）。"""
    rng = np.random.RandomState(5)
    V = make_cloud(2000, 5)
    B = 3
    W = np.zeros((V.shape[0], B))
    W[:, 0] = 1.0
    line = np.argsort(np.abs(V[:, 1]) + np.abs(V[:, 2] - 2.0))[:60]
    V[line, 1] = 0.0
    V[line, 2] = 2.0
    W[line, 0] = 0.0
    W[line, 1] = 0.85
    W[line, 2] = 0.15
    W /= W.sum(axis=1, keepdims=True)
    A0 = np.repeat(np.eye(4)[None], B, axis=0)
    T = V.copy()
    T[line, 2] += 0.5 + rng.normal(scale=0.05, size=len(line))

    s = core.SkinningSolver(V, W, A0, T, active=[0, 1, 2])
    rep = s.solve(iterations=200, tolerance=1e-9, stabilize=True)
    angs = [rep["deltas"][j]["angle"] for j in (1, 2)]
    print("     200 轮后：b1 %.2f°/%.3f ｜ b2 %.2f°/%.3f ｜ RMS %.6f" %
          (angs[0], rep["deltas"][1]["translate"],
           angs[1], rep["deltas"][2]["translate"], rep["rms"]))
    assert max(angs) < 8.0, "退化方向上漂移成 %.1f°" % max(angs)
    print("[OK] 200 轮迭代无漂移")


def test_accuracy_preserved():
    """数据充分时，先验必须几乎不改变解（偏差只有 prior_frac 量级）。"""
    rng = np.random.RandomState(11)
    V = make_cloud(3000, 11)
    B = 8
    lo, hi = V.min(axis=0), V.max(axis=0)
    centers = lo + rng.uniform(0.1, 0.9, size=(B, 3)) * (hi - lo)
    d = np.linalg.norm(V[:, None, :] - centers[None, :, :], axis=2)
    W = np.exp(-(d * d) / (2.0 * 1.5 ** 2))
    cut = np.partition(W, -3, axis=1)[:, -3][:, None]
    W[W < cut] = 0.0
    W /= W.sum(axis=1, keepdims=True)

    A0 = np.repeat(np.eye(4)[None], B, axis=0)
    A_true = A0.copy()
    for j in range(B):
        axis = rng.normal(size=3)
        axis /= np.linalg.norm(axis)
        ang = np.radians(rng.uniform(5.0, 25.0))
        K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
        A_true[j][:3, :3] = np.eye(3) + np.sin(ang) * K + (1 - np.cos(ang)) * (K @ K)
        A_true[j][:3, 3] = rng.uniform(-0.6, 0.6, 3)
    T = np.einsum("nb,bni->ni", W, core.batch_transform(A_true, V))

    s_raw, raw = solve(V, W, A0, T, active=list(range(B)), stabilize=False)
    s_fix, fix = solve(V, W, A0, T, active=list(range(B)), stabilize=True)
    dang = max(float(np.degrees(core.rotation_angle(
        s_fix.transforms[j][:3, :3] @ s_raw.transforms[j][:3, :3].T)))
        for j in range(B))
    print("     RMS 原始 %.8f ｜ 先验 %.8f ｜ 最大角度差 %.2e°" % (raw["rms"], fix["rms"], dang))
    assert dang < 0.05, "先验改变了正常数据的解（%.4f°）" % dang
    # 基线本身已经是机器精度，用绝对量判（网格对角线约 20 单位）
    assert fix["rms"] - raw["rms"] < 1e-4, "先验让拟合变差了 %.2e" % (fix["rms"] - raw["rms"])
    print("[OK] 正常数据上的反求结果几乎完全一致（不误伤）")


def test_identity_untouched():
    """没有雕刻位移时，必须先验后仍精确等于原姿态。"""
    V = make_cloud(1500)
    W = np.zeros((V.shape[0], 3))
    W[:, 0] = 1.0
    A0 = np.repeat(np.eye(4)[None], 3, axis=0)
    s, rep = solve(V, W, A0, V, stabilize=True)
    assert np.allclose(s.transforms, A0, atol=1e-7)
    assert rep["rms"] < 1e-9
    print("[OK] 无位移时保持原姿态 (rms=%.1e)" % rep["rms"])


def test_bones_within_reach():
    """「骨骼最大距离」：离雕刻区域太远的骨骼不参与计算。"""
    V = make_cloud(1200, 9)
    # 只把原点附近的顶点往上抬
    near = np.linalg.norm(V - np.array([0.0, 0.0, 2.0]), axis=1) < 3.0
    T = V.copy()
    T[near, 2] += 0.4

    pivots = np.array([[0.0, 0.0, 2.0],      # 就在雕刻区里
                       [6.0, 3.0, 2.0],      # 约 6.7 远
                       [40.0, 0.0, 2.0],     # 很远
                       [0.0, 0.0, -30.0]])   # 很远
    keep0, drop0, _ = core.bones_within_reach(pivots, V, T, max_dist=0.0)
    assert keep0 == [0, 1, 2, 3] and not drop0, "max_dist=0 必须不过滤"

    keep, drop, detail = core.bones_within_reach(pivots, V, T, max_dist=8.0)
    dists = dict((j, round(d, 2)) for j, d in detail)
    print("     距离: %s ｜ 保留 %s ｜ 剔除 %s" % (dists, keep, drop))
    assert keep == [0, 1] and drop == [2, 3], "距离过滤结果不对: %s / %s" % (keep, drop)

    # 候选子集
    keep2, drop2, _ = core.bones_within_reach(pivots, V, T, candidates=[0, 2, 3], max_dist=8.0)
    assert keep2 == [0] and drop2 == [2, 3], "候选过滤结果不对: %s / %s" % (keep2, drop2)

    # 全被剔除时返回空（上层据此报错，而不是静默什么都不算）
    keep3, drop3, _ = core.bones_within_reach(pivots, V, T, max_dist=0.5)
    assert not keep3 and drop3, "全超距时应返回空保留集"
    print("[OK] 最大距离过滤：按骨骼到被雕刻顶点的最近距离筛选")


if __name__ == "__main__":
    test_identifiability_metric()
    test_collinear_bone_no_longer_explodes()
    test_degenerate_but_real_rotation_survives()
    test_narrow_band_bone_keeps_its_fit()
    test_no_drift_over_many_iterations()
    test_accuracy_preserved()
    test_identity_untouched()
    test_bones_within_reach()
    print("\n全部稳定性测试通过")
