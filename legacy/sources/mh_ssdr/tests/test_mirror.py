# -*- coding: utf-8 -*-
"""
tests/test_mirror.py
====================
mh_ssdr.mirror 的离线单测（不需要 Maya）。

    python tests/test_mirror.py
"""

from __future__ import print_function, unicode_literals

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mh_ssdr import mirror as mir           # noqa: E402
from mh_ssdr import ssdr_core as core       # noqa: E402

_fails = []


def check(name, cond, detail=""):
    if cond:
        print(u"  ✓ %s" % name)
    else:
        print(u"  ✗ %s   %s" % (name, detail))
        _fails.append(name)


def norm(v):
    return float(np.linalg.norm(v))


# ---------------------------------------------------------------------------
def test_names():
    print(u"[1] 左右命名识别")
    cases = [
        ("FACIAL_L_EyeUpper", "FACIAL_R_EyeUpper"),
        ("FACIAL_R_Brow", "FACIAL_L_Brow"),
        ("FACIAL_L_MouthCorner", "FACIAL_R_MouthCorner"),
        ("Brow.L", "Brow.R"),
        ("Brow.R", "Brow.L"),
        ("eye_left", "eye_right"),
        ("eye_right", "eye_left"),
        ("hand_LEFT", "hand_RIGHT"),
        ("|rig|grp|FACIAL_L_Lip", "|rig|grp|FACIAL_R_Lip"),
        ("arm_l_02", "arm_r_02"),
    ]
    for src, want in cases:
        got = mir.opposite_name(src)
        check(u"%s → %s" % (src, want), got == want, u"得到 %r" % (got,))

    # 中线骨骼不应该有对侧
    for name in ("FACIAL_C_Jaw", "FACIAL_C_FacialRoot", "spine", "root",
                 "FACIAL_C_LipUpper", "joint1"):
        check(u"%s 无对侧" % name, mir.opposite_name(name) is None,
              u"却得到 %r" % (mir.opposite_name(name),))

    # side_of
    check(u"side_of(FACIAL_L_Eye) == L", mir.side_of("FACIAL_L_Eye") == mir.SIDE_L)
    check(u"side_of(FACIAL_R_Eye) == R", mir.side_of("FACIAL_R_Eye") == mir.SIDE_R)
    check(u"side_of(FACIAL_C_Jaw) is None", mir.side_of("FACIAL_C_Jaw") is None)

    # 存在性校验：候选里没有真实骨骼时要继续往下找，而不是乱猜
    avail = set(["FACIAL_L_Brow", "FACIAL_R_Brow_01"])
    check(u"用存在性挑出真实名字",
          mir.opposite_name("FACIAL_L_Brow", avail) is None,
          u"不该返回不存在的 FACIAL_R_Brow")

    avail = set(["FACIAL_L_Brow", "FACIAL_R_Brow"])
    check(u"存在时正常返回",
          mir.opposite_name("FACIAL_L_Brow", avail) == "FACIAL_R_Brow")


def test_pairs():
    print(u"[2] 配对列表")
    bones = ["FACIAL_L_Eye", "FACIAL_R_Eye", "FACIAL_L_Brow", "FACIAL_R_Brow",
             "FACIAL_C_Jaw", "FACIAL_C_FacialRoot", "FACIAL_R_Cheek"]
    pairs = mir.build_pairs(bones, mir.SIDE_L)
    check(u"L→R 得到 2 对", len(pairs) == 2, u"%r" % (pairs,))
    check(u"配对内容正确",
          set(pairs) == {("FACIAL_L_Eye", "FACIAL_R_Eye"),
                         ("FACIAL_L_Brow", "FACIAL_R_Brow")}, u"%r" % (pairs,))
    rev = mir.build_pairs(bones, mir.SIDE_R)
    check(u"R→L 得到 2 对（Cheek 没有左侧对应，跳过）", len(rev) == 2, u"%r" % (rev,))
    check(u"反向配对指向同一对骨骼",
          ("FACIAL_R_Eye", "FACIAL_L_Eye") in rev, u"%r" % (rev,))


def test_reflection():
    print(u"[3] 反射矩阵与共轭")
    R = mir.reflection_row(0, 0.0)
    p = np.array([3.0, 2.0, -1.0])
    q = mir.reflect_point(p, 0, 0.0)
    check(u"点反射 (3,2,-1) → (-3,2,-1)", np.allclose(q, [-3, 2, -1]))

    # 带偏移的平面
    R2 = mir.reflection_row(0, 5.0)
    check(u"平面 x=5 的反射", np.allclose(mir.reflect_point([3.0, 0, 0], 0, 5.0), [7.0, 0, 0]))

    # 纯平移的镜像
    P = np.eye(4)
    P[3, :3] = (1.5, 0.0, 0.0)    # 行向量：p·P = p + t
    M = mir.conjugate(P, R)
    check(u"平移被镜像", np.allclose(M[3, :3], [-1.5, 0.0, 0.0]), u"%r" % (M[3, :3],))

    # 绕 Y 旋转 → 镜像后反向
    th = np.radians(30.0)
    P = np.eye(4)
    P[:3, :3] = core.quat_to_mat(np.array([np.cos(th / 2), 0, np.sin(th / 2), 0]))
    M = mir.conjugate(P, R)
    ang_p = np.degrees(core.rotation_angle(P[:3, :3]))
    ang_m = np.degrees(core.rotation_angle(M[:3, :3]))
    check(u"旋转角大小不变 (%.3f°)" % ang_p, abs(ang_p - ang_m) < 1e-9)
    check(u"旋转轴反号（Y 分量）", M[1, 2] * P[1, 2] < 0 or abs(P[1, 2]) < 1e-9)
    check(u"共轭后仍是刚体 (det=+1)",
          abs(np.linalg.det(M[:3, :3]) - 1.0) < 1e-9)

    # 镜像两次回到原点
    P = np.eye(4)
    P[3, :3] = (0.3, -0.2, 0.7)
    P[:3, :3] = core.quat_to_mat(np.array([0.9, 0.1, 0.3, 0.2]))
    check(u"共轭两次 = 原变换",
          np.allclose(mir.conjugate(mir.conjugate(P, R), R), P, atol=1e-12))


def test_plane():
    print(u"[4] 镜像面自动判断")
    rng = np.random.RandomState(3)
    src = np.stack([rng.uniform(1, 5, 12), rng.uniform(-3, 3, 12),
                    rng.uniform(-2, 2, 12)], axis=1)
    dst = src.copy()
    dst[:, 0] *= -1.0                              # 关于 x=0 对称
    axis, coord, res = mir.estimate_plane(src, dst)
    check(u"轴 = X", axis == 0, u"axis=%r" % (axis,))
    check(u"平面 x ≈ 0 (%.2e)" % coord, abs(coord) < 1e-12)
    check(u"残差 ≈ 0 (%.2e)" % res, res < 1e-12)

    # 平面平移
    src2 = src + np.array([0.0, 0.0, 0.0])
    dst2 = dst + np.array([4.0, 0.0, 0.0])         # 对称面在 x=2
    axis, coord, res = mir.estimate_plane(src2, dst2)
    check(u"平面 x ≈ 2 (%.3f)" % coord, abs(coord - 2.0) < 1e-9, u"%r" % (coord,))

    # 左右轴是 Y 的 rig
    src3 = np.stack([rng.uniform(-3, 3, 8), rng.uniform(1, 5, 8),
                     rng.uniform(-1, 1, 8)], axis=1)
    dst3 = src3.copy()
    dst3[:, 1] *= -1.0
    axis, _c, res = mir.estimate_plane(src3, dst3)
    check(u"能识别出 Y 轴", axis == 1, u"axis=%r" % (axis,))

    # 强制指定轴
    axis, _c, _res = mir.estimate_plane(src3, dst3, axis=0)
    check(u"可强制指定轴", axis == 0)


def test_mirror_math():
    print(u"[5] 镜像偏移的几何意义（行向量约定）")
    R = mir.reflection_row(0, 0.0)
    # 左右骨骼的绑定姿态：互为镜像
    base_l = np.eye(4)
    base_l[3, :3] = (3.0, 0.0, 0.0)
    base_r = mir.conjugate(base_l, R)
    check(u"构造的绑定姿态确实对称",
          np.allclose(base_r[3, :3], (-3.0, 0.0, 0.0)), u"%r" % (base_r[3, :3],))

    # 左侧骨骼做了 20° 旋转 + 0.5 平移
    tgt_l = np.eye(4)
    th = np.radians(20.0)
    tgt_l[:3, :3] = core.quat_to_mat(np.array([np.cos(th / 2), np.sin(th / 2), 0, 0]))
    tgt_l[3, :3] = base_l[3, :3] + np.array([0.0, 0.5, 0.2])

    D_l = mir.relative(base_l, tgt_l)
    D_m = mir.conjugate(D_l, R)
    tgt_r = base_r @ D_m
    check(u"镜像后 = 目标姿态的镜像",
          np.allclose(tgt_r, mir.conjugate(tgt_l, R), atol=1e-12),
          u"maxerr=%.2e" % np.abs(tgt_r - mir.conjugate(tgt_l, R)).max())

    # 叠加模式：原有偏移上再叠加镜像量
    base_r2 = base_r @ np.eye(4)
    own = np.eye(4)
    own[3, :3] = (0.0, -0.3, 0.0)
    D_own = mir.relative(base_r2, base_r2 @ own)
    add = base_r2 @ (D_own @ D_m)
    check(u"叠加 = 自有偏移 · 镜像偏移",
          np.allclose(add, base_r2 @ D_own @ D_m, atol=1e-12))


def main():
    test_names()
    test_pairs()
    test_reflection()
    test_plane()
    test_mirror_math()
    print("")
    if _fails:
        print(u"结果: %d 项失败 / %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print(u"结果: 全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
