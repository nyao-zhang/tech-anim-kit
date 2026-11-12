# -*- coding: utf-8 -*-
"""
离线单元测试（不需要 Maya）：
    python tests/test_ssdr_core.py

测试覆盖：
  * Maya 行向量 / NumPy 列向量 约定转换
  * 四元数往返 + slerp
  * 无变形时解出单位矩阵
  * 全骨骼刚体反求（目标由已知刚体变换生成）
  * 部分骨骼参与计算（未选中的骨骼必须保持不动）
  * 自动推荐骨骼命中真实骨骼
  * 权重滑块 0% / 50% / 100% 的行为
  * 雕刻式(非刚体)位移场下的近似逼近
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mh_ssdr import ssdr_core as core  # noqa: E402


# ----------------------------------------------------------------------------
def make_mesh(nx=110, ny=50, width=18.0, height=6.0):
    """一片带起伏的面片，尺度接近人脸(cm 级)"""
    xs = np.linspace(0.0, width, nx)
    ys = np.linspace(-height * 0.5, height * 0.5, ny)
    gx, gy = np.meshgrid(xs, ys)
    V = np.stack([gx.ravel(), gy.ravel(),
                  0.6 * np.sin(gx.ravel() * 1.7) * np.cos(gy.ravel() * 1.1)], axis=1)
    return V


def make_weights(V, n_bones=40, sigma=0.9, top_k=4, seed=1):
    """模拟 MetaHuman 的局部化权重：每顶点最多 top_k 根骨骼、平滑衰减、行归一化"""
    rng = np.random.RandomState(seed)
    lo, hi = V.min(axis=0), V.max(axis=0)
    centers = lo + rng.uniform(0.02, 0.98, size=(n_bones, 3)) * (hi - lo)
    d = np.linalg.norm(V[:, None, :] - centers[None, :, :], axis=2)
    W = np.exp(-(d * d) / (2.0 * sigma * sigma))
    if top_k < n_bones:
        cut = np.partition(W, -top_k, axis=1)[:, -top_k][:, None]
        W[W < cut] = 0.0
    W /= W.sum(axis=1, keepdims=True)
    return W


def random_rigid(rng, angle_deg=12.0, trans=0.3):
    axis = rng.normal(size=3)
    axis /= np.linalg.norm(axis)
    ang = np.radians(rng.uniform(-angle_deg, angle_deg))
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    R = np.eye(3) + np.sin(ang) * K + (1 - np.cos(ang)) * (K @ K)
    A = np.eye(4)
    A[:3, :3] = R
    A[:3, 3] = rng.uniform(-trans, trans, 3)
    return A


def lbs(V, W, A):
    return np.einsum("nb,bni->ni", W, core.batch_transform(A, V))


# ----------------------------------------------------------------------------
def test_conventions():
    A = np.array([[0.0, 1.0, 0.0, 5.0],
                  [1.0, 0.0, 0.0, 6.0],
                  [0.0, 0.0, 1.0, 7.0],
                  [0.0, 0.0, 0.0, 1.0]])
    vals = core.to_row_major16(A)
    # 行向量约定下平移位于最后一行 -> 第 12/13/14 个浮点
    assert abs(vals[12] - 5.0) < 1e-12 and abs(vals[13] - 6.0) < 1e-12 and abs(vals[14] - 7.0) < 1e-12
    assert np.allclose(core.from_row_major16(vals), A)
    M = np.array(vals).reshape(4, 4)
    v = np.array([1.0, 2.0, 3.0])
    assert np.allclose((np.append(v, 1.0) @ M)[:3], core.transform_points(A, v[None])[0])
    print("[OK] Maya 行向量 / NumPy 列向量 约定转换正确")


def test_quat():
    rng = np.random.RandomState(1)
    for _ in range(20):
        R = random_rigid(rng, 170.0, 0.0)[:3, :3]
        assert np.allclose(R, core.quat_to_mat(core.mat_to_quat(R)), atol=1e-9)
    q0 = core.mat_to_quat(core.orthonormalize(random_rigid(rng)[:3, :3]))
    q1 = core.mat_to_quat(core.orthonormalize(random_rigid(rng)[:3, :3]))
    Rm = core.quat_to_mat(core.slerp(q0, q1, 0.5))
    assert np.allclose(Rm @ Rm.T, np.eye(3), atol=1e-9)
    print("[OK] 四元数 / slerp 往返一致")


def test_identity():
    V = make_mesh()
    W = make_weights(V)
    A0 = np.repeat(np.eye(4)[None], W.shape[1], axis=0)
    s = core.SkinningSolver(V, W, A0, V)
    rep = s.solve(iterations=15)
    assert rep["rms"] < 1e-9, rep["rms"]
    assert np.allclose(s.transforms, A0, atol=1e-6)
    print("[OK] 无变形时解出单位矩阵 (rms=%.1e)" % rep["rms"])


def test_full_recovery():
    """目标由已知刚体变换生成 -> 应能高精度反求回来"""
    rng = np.random.RandomState(11)
    V = make_mesh()
    W = make_weights(V)
    B = W.shape[1]
    moved = list(rng.choice(B, 12, replace=False))
    A_true = np.repeat(np.eye(4)[None], B, axis=0)
    for j in moved:
        A_true[j] = random_rigid(rng, 15.0, 0.4)
    target = lbs(V, W, A_true)

    A0 = np.repeat(np.eye(4)[None], B, axis=0)
    s = core.SkinningSolver(V, W, A0, target, bone_names=["b%d" % i for i in range(B)],
                            active=moved)
    rep = s.solve(iterations=40)
    print("     rms_before=%.4f -> rms=%.5f  iters=%d" % (rep["rms_before"], rep["rms"], rep["iterations"]))
    assert rep["rms"] < 5e-3, rep["rms"]
    # 旋转偏差
    worst = 0.0
    for j in moved:
        R1 = core.orthonormalize(s.transforms[j][:3, :3])
        R2 = core.orthonormalize(A_true[j][:3, :3])
        worst = max(worst, np.degrees(core.rotation_angle(R1 @ R2.T)))
    print("     最大旋转偏差 %.2f 度" % worst)
    assert worst < 2.0, worst
    print("[OK] 全骨骼刚体反求收敛")


def test_partial_selection():
    """只有部分骨骼位移，只选这几根骨骼求解，未选中的骨骼不得被改动"""
    rng = np.random.RandomState(7)
    V = make_mesh()
    W = make_weights(V)
    B = W.shape[1]
    A_true = np.repeat(np.eye(4)[None], B, axis=0)
    moved = [3, 17]
    for j in moved:
        A_true[j] = random_rigid(rng, 20.0, 0.6)
    target = lbs(V, W, A_true)

    A0 = np.repeat(np.eye(4)[None], B, axis=0)
    s = core.SkinningSolver(V, W, A0, target, active=moved)
    rep = s.solve(iterations=40)
    print("     rms_before=%.4f -> rms=%.5f" % (rep["rms_before"], rep["rms"]))
    assert rep["rms"] < 5e-3, rep["rms"]
    for j in range(B):
        if j not in moved:
            assert np.allclose(s.transforms[j], np.eye(4), atol=1e-12), "未选中骨骼 %d 被改动了" % j
    print("[OK] 部分骨骼参与计算，未选中骨骼保持不动")


def test_rank_bones():
    rng = np.random.RandomState(5)
    V = make_mesh()
    W = make_weights(V)
    B = W.shape[1]
    A_true = np.repeat(np.eye(4)[None], B, axis=0)
    A_true[9] = random_rigid(rng, 25.0, 0.8)
    target = lbs(V, W, A_true)

    A0 = np.repeat(np.eye(4)[None], B, axis=0)
    s = core.SkinningSolver(V, W, A0, target)
    ranked = s.rank_bones()
    print("     推荐顺序(前3):", [(j, round(e, 5)) for j, e in ranked[:3]])
    assert ranked[0][0] == 9, "应优先推荐真正发生位移的骨骼, got %s" % ranked[0][0]
    print("[OK] 自动推荐骨骼命中真实骨骼")


def test_blend():
    rng = np.random.RandomState(9)
    V = make_mesh()
    W = make_weights(V)
    B = W.shape[1]
    moved = list(rng.choice(B, 8, replace=False))
    A_true = np.repeat(np.eye(4)[None], B, axis=0)
    for j in moved:
        A_true[j] = random_rigid(rng, 8.0, 0.2)
    target = lbs(V, W, A_true)

    A0 = np.repeat(np.eye(4)[None], B, axis=0)
    s = core.SkinningSolver(V, W, A0, target, active=moved)
    s.solve(iterations=40)

    errs = [core.rms_of(s.blended(t), V, W, target) for t in (0.0, 0.25, 0.5, 0.75, 1.0)]
    print("     滑块 0/25/50/75/100%% 的 RMS:", ["%.4f" % e for e in errs])
    for a, b in zip(errs, errs[1:]):
        assert b <= a + 1e-9, errs
    assert np.allclose(s.blended(0.0), A0, atol=1e-12)
    assert np.allclose(s.blended(1.0), s.transforms, atol=1e-12)
    assert core.rms_of(s.blended(0.0), V, W, V) < 1e-9, "0% 权重必须完全等于原姿态"
    print("[OK] 权重滑块单调逼近，0% 回到原姿态")


def test_sculpt_field():
    """模拟真实雕刻：非刚体位移场，检查骨骼驱动结果能吃掉大部分位移"""
    rng = np.random.RandomState(21)
    V = make_mesh()
    W = make_weights(V)
    B = W.shape[1]
    center = V.mean(axis=0)
    A0 = np.repeat(np.eye(4)[None], B, axis=0)

    d = V - center
    falloff = np.exp(-(d[:, 0] ** 2 + d[:, 1] ** 2) / (2.0 * 2.0 ** 2))
    target = V.copy()
    target[:, 2] += 1.8 * falloff                      # 往前推 1.8 的鼓包
    target[:, 1] += 0.6 * falloff * d[:, 0] / 9.0      # 轻微侧向剪切

    s = core.SkinningSolver(V, W, A0, target, active=list(range(B)))
    rep = s.solve(iterations=45)
    ratio = rep["rms"] / max(rep["rms_before"], 1e-12)
    print("     雕刻位移场: rms %.4f -> %.4f (残差 %.1f%%)" %
          (rep["rms_before"], rep["rms"], ratio * 100.0))
    assert ratio < 0.2, "骨骼化后应吸收 80%% 以上位移, 实际残差 %.1f%%" % (ratio * 100.0)
    print("[OK] 非刚体雕刻位移场可被骨骼近似吸收")


if __name__ == "__main__":
    test_conventions()
    test_quat()
    test_identity()
    test_full_recovery()
    test_partial_selection()
    test_rank_bones()
    test_blend()
    test_sculpt_field()
    print("\n全部测试通过")
