# -*- coding: utf-8 -*-
"""「只让受影响的骨骼参与计算」与「重要度阈值」的离线回归测试。

不依赖 Maya，纯 NumPy，用 mayapy 或普通 python 都能跑。

覆盖：
  1. 影响权重 share 能正确区分"被雕刻区驱动的骨骼"和"只是铺满全脸的骨骼"
  2. 自动圈定：只雕嘴角时，额头/眼睛附近的骨骼不参与
  3. 重要度阈值：解出来几乎没动的骨骼被退回原姿态（A == A0），且拟合不变差
  4. 真实运动不被误杀（该转 15° 的骨骼必须保留）
  5. 全被剔掉时兜底（至少留一根，不会算不出来）
  6. 距离过滤与影响过滤是两个独立判据，可叠加
  7. 全部骨骼都真的在动时，重要度过滤一根都不冻结（结果与不过滤一致）
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mh_ssdr import ssdr_core as core  # noqa: E402


# ---------------------------------------------------------------------------
# 造一个"像脸一样"的合成 rig：网格铺满，每根骨骼用高斯权重覆盖一片，
# 归一化之后每根骨骼在整张网上都有微小权重（这正是真实 rig 的行为，
# 也是"只雕嘴角、额头骨骼却动了"的土壤）。
# ---------------------------------------------------------------------------
def make_face(n=4000, seed=1, spacing=6.0):
    rng = np.random.RandomState(seed)
    V = rng.uniform([-8.0, -8.0, 0.0], [8.0, 8.0, 3.0], size=(n, 3))
    # 3x3 骨骼布点
    pivots = []
    for i in (-1, 0, 1):
        for k in (-1, 0, 1):
            pivots.append([i * spacing, k * spacing, 0.0])
    pivots = np.asarray(pivots, dtype=np.float64)
    d2 = ((V[:, None, :] - pivots[None, :, :]) ** 2).sum(axis=2)
    W = np.exp(-d2 / (2.0 * 2.2 ** 2))
    W /= np.maximum(W.sum(axis=1, keepdims=True), 1e-12)
    names = ["FACIAL_%d%d_Test" % (i, k) for i in (-1, 0, 1) for k in (-1, 0, 1)]
    return V, W, pivots, names


def rigid(angle_deg, axis, translate=(0.0, 0.0, 0.0), center=None):
    axis = np.asarray(axis, dtype=np.float64)
    axis = axis / np.linalg.norm(axis)
    a = np.radians(angle_deg)
    K = np.array([[0.0, -axis[2], axis[1]],
                  [axis[2], 0.0, -axis[0]],
                  [-axis[1], axis[0], 0.0]])
    R = np.eye(3) + np.sin(a) * K + (1.0 - np.cos(a)) * (K @ K)
    A = np.eye(4)
    A[:3, :3] = R
    if center is None:
        A[:3, 3] = translate
    else:
        c = np.asarray(center, dtype=np.float64)
        A[:3, 3] = c - R @ c + np.asarray(translate, dtype=np.float64)
    return A


def lbs(V, W, A):
    return np.einsum("nb,bni->ni", W, core.batch_transform(A, V))


def solve(V, W, A0, T, active, **kw):
    s = core.SkinningSolver(V, W, A0, T, bone_names=["b%d" % j for j in range(W.shape[1])],
                            active=active)
    rep = s.solve(**kw)
    return s, rep


def approx(a, b, tol):
    return abs(float(a) - float(b)) <= tol


# ---------------------------------------------------------------------------
def test_share_ranks_relevant_bones():
    print("1) 影响权重按'谁真被雕刻区需要'排序")
    V, W, pivots, names = make_face()
    # 只在 (0,-1) 那个骨骼附近鼓一个包
    center = pivots[3]
    dist = np.linalg.norm(V - center, axis=1)
    T = V.copy()
    T[:, 2] += 0.8 * np.exp(-(dist ** 2) / (2.0 * 1.5 ** 2))

    info = core.influence_share(V, W, T, pivots=pivots)
    share = info["share"]
    near = 3
    print("     share: " + "  ".join("%s=%.4f" % (names[j].split("_")[1], share[j])
                                     for j in range(len(names))))
    assert share[near] > 0.9, "中心骨骼的影响权重应当接近 1，实际 %.4f" % share[near]
    far = [j for j in range(len(names)) if np.linalg.norm(pivots[j] - center) > 10.0]
    assert max(share[j] for j in far) < 0.5, "远处骨骼的影响权重应当很小"
    assert info["reach"][near] <= info["reach"][far[0]], "reach 应当越近越小"
    print("[OK] 中心 %.3f ｜ 远处最大 %.3f" % (share[near], max(share[j] for j in far)))


def test_auto_scope_excludes_untouched_regions():
    print("\n2) 自动圈定：只雕一处时，别处的骨骼不参与")
    V, W, pivots, names = make_face()
    center = pivots[3]
    dist = np.linalg.norm(V - center, axis=1)
    T = V.copy()
    T[:, 2] += 0.8 * np.exp(-(dist ** 2) / (2.0 * 1.5 ** 2))

    kept, dropped, info = core.select_influential_bones(
        V, W, T, pivots=pivots, min_share=0.01)
    print("     保留 %d 根，剔除 %d 根" % (len(kept), len(dropped)))
    for j, why in dropped[:3]:
        print("       - %-16s %s" % (names[j], why))
    assert 3 in kept, "中心骨骼必须参与"
    assert 3 not in [j for j, _ in dropped]
    assert len(dropped) >= 5, "离得远、影响可忽略的骨骼应当被剔除"
    far = [j for j in range(len(names)) if np.linalg.norm(pivots[j] - center) > 10.0]
    for j in far:
        assert j not in kept, "远处骨骼 %s 不该参与" % names[j]
    print("[OK] 参与集合 %s" % [names[j] for j in kept])


def test_prune_freezes_noise_motion():
    print("\n3) 重要度阈值：几乎没动的骨骼被退回原姿态")
    V, W, pivots, names = make_face()
    center = pivots[3]
    dist = np.linalg.norm(V - center, axis=1)
    A_true = np.repeat(np.eye(4)[None], len(names), axis=0)
    A_true[3] = rigid(9.0, (0, 1, 0), (0.0, 0.0, 0.55), center=center)
    T = lbs(V, W, A_true)

    A0 = np.repeat(np.eye(4)[None], len(names), axis=0)
    s_raw, raw = solve(V, W, A0, T, list(range(len(names))),
                       iterations=45, prune_translate=0.0, prune_rotate=0.0)
    s_pr, pr = solve(V, W, A0, T, list(range(len(names))),
                     iterations=45, prune_translate=0.02, prune_rotate=0.2)
    moved_raw = [j for j in range(len(names))
                 if raw["deltas"][j]["angle"] > 1e-6 or raw["deltas"][j]["translate"] > 1e-7]
    print("     不过滤：%d 根有偏移 ｜ 冻结后：冻结 %d 根，保留 %d 根" %
          (len(moved_raw), len(pr["frozen"]), pr["active_count"]))
    print("     RMS %.7f -> %.7f" % (raw["rms"], pr["rms"]))
    assert len(pr["frozen"]) > 0, "应当有骨骼被冻结"
    for j in range(len(names)):
        if s_pr.bone_names[j] in pr["frozen"]:
            # 冻结 = 完全不动，不是"缩一点"
            assert np.abs(s_pr.transforms[j] - A0[j]).max() < 1e-12, \
                "被冻结的骨骼必须严格回到原姿态"
    diag = float(np.linalg.norm(V.max(axis=0) - V.min(axis=0)))
    assert pr["rms"] - raw["rms"] < 1e-4 * diag, \
        "冻结把拟合做坏了：%.3e -> %.3e" % (raw["rms"], pr["rms"])
    # 真正该动的骨骼必须留下
    assert s_pr.active[3], "中心骨骼被误冻结"
    print("[OK] 冻结的都是噪声量级的骨骼，拟合没有变差"
          "（RMS +%.2e，网格对角线 %.1f）" % (pr["rms"] - raw["rms"], diag))


def test_real_motion_survives():
    print("\n4) 真实运动不被误杀")
    V, W, pivots, names = make_face()
    center = pivots[3]
    A_true = np.repeat(np.eye(4)[None], len(names), axis=0)
    A_true[3] = rigid(15.0, (0, 1, 0), (0.05, 0.0, 0.9), center=center)
    A_true[4] = rigid(4.0, (1, 0, 0), (0.0, 0.02, 0.35), center=pivots[4])
    T = lbs(V, W, A_true)

    A0 = np.repeat(np.eye(4)[None], len(names), axis=0)
    s, rep = solve(V, W, A0, T, [3, 4], iterations=60,
                   prune_translate=0.02, prune_rotate=0.2)
    print("     骨骼 3：%.2f° / %.3f ｜ 骨骼 4：%.2f° / %.3f" %
          (rep["deltas"][3]["angle"], rep["deltas"][3]["translate"],
           rep["deltas"][4]["angle"], rep["deltas"][4]["translate"]))
    assert s.active[3] and s.active[4], "真实运动的骨骼不该被冻结"
    assert rep["deltas"][3]["angle"] > 12.0, "旋转被削掉了"
    assert rep["deltas"][4]["angle"] > 2.5
    print("[OK] %.2f° / %.2f° 都完整保留" %
          (rep["deltas"][3]["angle"], rep["deltas"][4]["angle"]))


def test_fallback_when_everything_dropped():
    print("\n5) 阈值设到极端时的兜底")
    V, W, pivots, names = make_face()
    center = pivots[3]
    dist = np.linalg.norm(V - center, axis=1)
    T = V.copy()
    T[:, 2] += 0.6 * np.exp(-(dist ** 2) / (2.0 * 1.5 ** 2))
    kept, dropped, info = core.select_influential_bones(
        V, W, T, pivots=pivots, min_share=1.5)          # 比 1 还大 -> 全被剔
    assert info["fallback"] is True, "应当标记为兜底"
    assert len(kept) == 1 and kept[0] == 3, "兜底留下影响最大的那根"
    print("     兜底保留：%s（影响 %.3f）" % (names[kept[0]], info["share"][kept[0]]))
    print("[OK] 极端阈值不会让工具无法工作")


def test_distance_and_share_are_independent():
    print("\n6) 距离与影响是两个独立判据")
    V, W, pivots, names = make_face()
    center = pivots[3]
    dist = np.linalg.norm(V - center, axis=1)
    T = V.copy()
    T[:, 2] += 0.8 * np.exp(-(dist ** 2) / (2.0 * 1.5 ** 2))

    kept_d, _, info = core.select_influential_bones(
        V, W, T, pivots=pivots, min_share=0.0, max_dist=4.0)
    kept_s, _, _ = core.select_influential_bones(
        V, W, T, pivots=pivots, min_share=0.01, max_dist=0.0)
    kept_b, _, _ = core.select_influential_bones(
        V, W, T, pivots=pivots, min_share=0.01, max_dist=4.0)
    print("     只看距离: %d 根 ｜ 只看影响: %d 根 ｜ 两个都用: %d 根" %
          (len(kept_d), len(kept_s), len(kept_b)))
    assert set(kept_b) <= set(kept_d) and set(kept_b) <= set(kept_s)
    # 距离 4.0 以内一定包含中心骨骼
    assert 3 in kept_d and 3 in kept_b
    print("[OK] 组合判据 = 两者交集")


def test_no_prune_when_everything_moves():
    print("\n7) 所有骨骼都真的在动时，一根都不冻结")
    V, W, pivots, names = make_face()
    A_true = np.repeat(np.eye(4)[None], len(names), axis=0)
    rng = np.random.RandomState(4)
    for j in range(len(names)):
        A_true[j] = rigid(rng.uniform(6.0, 18.0), rng.normal(size=3),
                          tuple(rng.uniform(-0.4, 0.4, 3)), center=pivots[j])
    T = lbs(V, W, A_true)
    A0 = np.repeat(np.eye(4)[None], len(names), axis=0)

    s_raw, raw = solve(V, W, A0, T, list(range(len(names))), iterations=50)
    s_pr, pr = solve(V, W, A0, T, list(range(len(names))), iterations=50,
                     prune_translate=0.02, prune_rotate=0.2)
    dang = max(rep["angle"] for rep in pr["deltas"])
    print("     冻结 %d 根 ｜ RMS %.7f -> %.7f ｜ 最大角度 %.1f°" %
          (len(pr["frozen"]), raw["rms"], pr["rms"], dang))
    assert not pr["frozen"], "正常数据不该冻结任何骨骼"
    assert approx(pr["rms"], raw["rms"], 1e-12), "结果必须与不过滤时一致"
    print("[OK] 不误伤正常结果")


def main():
    test_share_ranks_relevant_bones()
    test_auto_scope_excludes_untouched_regions()
    test_prune_freezes_noise_motion()
    test_real_motion_survives()
    test_fallback_when_everything_dropped()
    test_distance_and_share_are_independent()
    test_no_prune_when_everything_moves()
    print("\n全部 7 项通过")


if __name__ == "__main__":
    main()
