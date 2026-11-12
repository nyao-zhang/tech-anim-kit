# -*- coding: utf-8 -*-
"""
tests/test_maya_mirror.py
=========================
镜像功能在**真实 Maya 环境**里的回归测试（mayapy 跑，不需要界面）。

    "C:/Program Files/Autodesk/Maya2024/bin/mayapy.exe" tests/test_maya_mirror.py

场景：造一个左右严格对称的蒙皮网格（FACIAL_L_* / FACIAL_R_* / FACIAL_C_Root），
只让左侧骨骼带动网格做出"雕刻"，然后 SSDR 反求 → 把结果镜像到右侧，
检查：

  * 左右骨骼配对与镜像面自动判断是否正确
  * 镜像后的骨骼世界矩阵是否满足 base_右 · (R·D·R)（反射共轭，逐根校验）
  * 烘焙后的网格是否真的左右对称
  * 镜像结果是否并入了求解结果（滑块 0% 回原、100% 到镜像位）
  * 叠加偏移模式是否等于「自有偏移 · 镜像偏移」
"""

from __future__ import print_function, unicode_literals

import os
import sys

import numpy as np

import maya.standalone
maya.standalone.initialize(name="python")

import maya.cmds as cmds                                    # noqa: E402

TOOL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOL)

from mh_ssdr import maya_bridge as mb                       # noqa: E402
from mh_ssdr import mirror as mir                           # noqa: E402
from mh_ssdr.tool_ui import SSDRController                  # noqa: E402

_fails = []


def check(name, cond, detail=""):
    if cond:
        print(u"  ✓ %s" % name)
    else:
        print(u"  ✗ %s   %s" % (name, detail))
        _fails.append(name)


def build_scene():
    """严格左右对称的平面 + 关节 + skinCluster"""
    cmds.file(new=True, force=True)
    mesh = cmds.rename(cmds.polyPlane(w=20, h=12, sx=24, sy=14, ch=False)[0], "head")
    cmds.select(cl=True)
    root = cmds.joint(n="FACIAL_C_Root", p=(0, 0, -1.5))
    names = []
    for i, (x, z) in enumerate([(3, -2.5), (3, 2.5), (7, -2.5), (7, 2.5)]):
        for side, sx in (("L", 1.0), ("R", -1.0)):
            cmds.select(root)
            n = cmds.joint(n="FACIAL_%s_%02d" % (side, i), p=(sx * x, 0, z))
            names.append(n)
    cmds.select(cl=True)
    cmds.skinCluster(root, *(names + [mesh]), toSelectedBones=True,
                     maximumInfluences=4, bindMethod=0)
    return mesh, root, names


def mirror_index(rest):
    """rest 顶点各自的"左右对称点"下标（按 rest 位置就近匹配）"""
    q = rest.copy()
    q[:, 0] *= -1.0
    d = ((q[:, None, :] - rest[None, :, :]) ** 2).sum(-1)
    return np.argmin(d, axis=1)


def main():
    print(u"== 镜像功能端到端（真实 Maya）==")
    mesh, root, bones = build_scene()
    rest = mb.read_points(mb.shape_of(mesh))
    diag = mb.mesh_diagonal(rest)
    base = mb.snapshot_channels([root] + bones)

    # -- 提取雕刻网格
    cmds.select(mesh, replace=True)
    logs = []
    ctl = SSDRController(logger=logs.append)
    ctl.set_source_from_selection()
    ctl.extract()
    rig = ctl.rig
    check(u"源网格与骨骼数", len(rig.bones) == 9, u"%d 根" % len(rig.bones))

    # -- 只动左侧骨骼，造一个"只雕了一半"的雕刻
    cmds.rotate(0, 0, 14, "FACIAL_L_00", relative=True)
    cmds.rotate(10, 0, 0, "FACIAL_L_01", relative=True)
    cmds.xform("FACIAL_L_02", translation=(0.6, -0.5, 0.8), relative=True)
    cmds.rotate(0, 0, 6, "FACIAL_L_03", relative=True)
    mb.force_update([mb.shape_of(mesh)])
    target = mb.read_points(mb.shape_of(mesh))
    moved = float(np.linalg.norm(target - rest, axis=1).max())
    check(u"左侧雕刻位移非零 (%.3f)" % moved, moved > 0.1)

    mb.restore_channels(base)
    mb.force_update([mb.shape_of(mesh)])
    sculpt = ctl.sculpt_mesh()
    mb.write_points(mb.shape_of(sculpt), target)
    mb.force_update([mb.shape_of(sculpt)])

    # 对称性基准：雕刻本身应该左右不对称
    mi = mirror_index(rest)
    mir_target = target.copy()
    mir_target[:, 0] *= -1.0
    asym_before = float(np.linalg.norm(mir_target - target[mi], axis=1).max())
    check(u"雕刻本身是左右不对称的 (%.3f)" % asym_before, asym_before > 0.05)

    # -- 反求
    rep = ctl.solve(list(rig.bones), iterations=60)
    print(u"    SSDR: RMS %.6f → %.6f，%d 次迭代" %
          (rep["rms_before"], rep["rms"], rep["iterations"]))
    check(u"反求收敛 (RMS < 1e-3 × 对角线)",
          rep["rms"] < 1e-3 * diag, u"rms=%.6f" % rep["rms"])

    pairs = ctl.mirror_pairs("L2R")
    check(u"识别到 4 对左右骨骼", len(pairs) == 4, u"%r" % (pairs,))
    check(u"配对不含中线骨骼",
          all(not s.endswith("Root") for s, _ in pairs))

    # 保存镜像前的状态，后面校验"叠加"语义
    A_before = ctl.solver.A.copy()
    base_w = rig.world_from_skinning(ctl.solver.A0)
    full_w = rig.world_from_skinning(A_before)

    # -- 镜像（覆盖绝对值）
    mrep = ctl.mirror("L2R", "overwrite")
    check(u"镜像面自动判断为 X 轴", mrep["axis"] == 0, u"axis=%d" % mrep["axis"])
    check(u"镜像面 x ≈ 0 (%.2e)" % mrep["coord"], abs(mrep["coord"]) < 1e-6)
    check(u"左右配对残差极小 (%.2e)" % mrep["residual"], mrep["residual"] < 1e-3)
    check(u"写入 4 根骨骼", mrep["written"] == 4, u"%d" % mrep["written"])

    R = mir.reflection_row(mrep["axis"], mrep["coord"])
    full_after = rig.world_from_skinning(ctl.solver.A)
    worst = 0.0
    for s, d in pairs:
        want = base_w[d] @ mir.conjugate(mir.relative(base_w[s], full_w[s]), R)
        worst = max(worst, float(np.abs(full_after[d] - want).max()))
    check(u"每根骨骼 = base_对侧 · R·D·R（最大偏差 %.2e）" % worst, worst < 1e-9)

    worst_sym = 0.0
    for s, d in pairs:
        worst_sym = max(worst_sym,
                        float(np.abs(full_after[d] - mir.conjugate(full_after[s], R)).max()))
    print(u"    对称 rig 上 右骨骼 vs 左骨骼镜像 最大差 %.3e（对角线 %.2f）" %
          (worst_sym, diag))

    check(u"镜像骨骼已并入参与集合",
          all(ctl.solver.active[rig.bones.index(d)] for _, d in pairs))

    # -- 滑块：0% 必须精确回到原形
    ctl.apply_weight(0.0)
    back = mb.read_points(mb.shape_of(mesh))
    check(u"0%% 回退到原形 (%.2e)" % np.linalg.norm(back - rest, axis=1).max(),
          np.linalg.norm(back - rest, axis=1).max() < 1e-6)

    # -- 100%：左侧仍贴合雕刻，整体变成左右对称
    ctl.apply_weight(1.0)
    got = mb.read_points(mb.shape_of(mesh))
    mir_got = got.copy()
    mir_got[:, 0] *= -1.0
    asym_after = float(np.linalg.norm(mir_got - got[mi], axis=1).max())

    # 左侧远端（远离中线、几乎不受右侧骨骼影响）必须保持雕刻形状；
    # 中线附近本来就同时受两侧骨骼影响，镜像后形状略有变化是"对称化"的必然结果
    far = rest[:, 0] >= 4.0
    d_left = float(np.linalg.norm(got[far] - target[far], axis=1).max())
    d_all = float(np.linalg.norm(got - target, axis=1).max())
    print(u"    烘焙: 左侧远端贴合 max=%.4f ｜ 全网格对雕刻 max=%.4f ｜ "
          u"左右不对称度 %.4f → %.4f" % (d_left, d_all, asym_before, asym_after))
    check(u"左侧远端仍贴合雕刻 (%.3f%% 对角线)" % (d_left / diag * 100),
          d_left < 0.01 * diag)
    check(u"镜像后网格接近左右对称 (%.3f%% 对角线)" % (asym_after / diag * 100),
          asym_after < 0.02 * diag)
    check(u"对称度显著改善", asym_after < 0.3 * asym_before,
          u"%.4f vs %.4f" % (asym_after, asym_before))

    # -- 右侧骨骼确实被滑块驱动
    ctl.apply_weight(0.0)
    w0 = dict((d, mb.world_matrix(d)) for _, d in pairs)
    ctl.apply_weight(1.0)
    w1 = dict((d, mb.world_matrix(d)) for _, d in pairs)
    deltas = [float(np.abs(w1[d] - w0[d]).max()) for _, d in pairs]
    check(u"4 根右侧骨骼都随滑块变化（最小 %.2e）" % min(deltas), min(deltas) > 1e-3)

    # -- 叠加偏移模式
    ctl.revert()
    ctl.solve(list(rig.bones), iterations=40)          # 从干净状态重新求解
    base_w = rig.world_from_skinning(ctl.solver.A0)
    src_w = rig.world_from_skinning(ctl.solver.A)      # 这次求解的源侧结果
    ctl.mirror("L2R", "overwrite")
    full_before = rig.world_from_skinning(ctl.solver.A)
    ctl.mirror("L2R", "additive")
    full_add = rig.world_from_skinning(ctl.solver.A)
    worst_add = 0.0
    for s, d in pairs:
        Dm = mir.conjugate(mir.relative(base_w[s], src_w[s]), R)
        want = base_w[d] @ (mir.relative(base_w[d], full_before[d]) @ Dm)
        worst_add = max(worst_add, float(np.abs(full_add[d] - want).max()))
    check(u"叠加模式 = 自有偏移 · 镜像偏移（偏差 %.2e）" % worst_add, worst_add < 1e-9)

    s0, d0 = pairs[0]
    a1 = float(np.degrees(_angle(mir.relative(base_w[d0], full_before[d0]))))
    a2 = float(np.degrees(_angle(mir.relative(base_w[d0], full_add[d0]))))
    print(u"    %s 的偏移量: 覆盖后 %.3f° → 叠加后 %.3f°" % (d0, a1, a2))
    check(u"叠加后偏移变大 (%.3f° → %.3f°)" % (a1, a2), a2 > a1 * 1.5)

    # -- 只勾选左侧骨骼时，镜像应自动把右侧骨骼纳入参与集合
    ctl.revert()
    left_only = [b for b in rig.bones if "_L_" in b.split("|")[-1]]
    ctl.solve(left_only, iterations=40)
    inactive = [d for _, d in pairs if not ctl.solver.active[rig.bones.index(d)]]
    check(u"只算左侧时右侧骨骼确实未参与", len(inactive) == 4, u"%d 根" % len(inactive))
    mrep2 = ctl.mirror("L2R", "overwrite")
    check(u"镜像自动加入 4 根对侧骨骼", len(mrep2["activated"]) == 4,
          u"%r" % (mrep2["activated"],))
    check(u"对侧骨骼已进入参与集合",
          all(ctl.solver.active[rig.bones.index(d)] for _, d in pairs))
    ctl.apply_weight(1.0)
    got2 = mb.read_points(mb.shape_of(mesh))
    right = rest[:, 0] <= -4.0
    moved_right = float(np.linalg.norm(got2[right] - rest[right], axis=1).max())
    check(u"右侧网格确实被带动 (%.3f)" % moved_right, moved_right > 0.1)
    mir2 = got2.copy()
    mir2[:, 0] *= -1.0
    asym2 = float(np.linalg.norm(mir2 - got2[mi], axis=1).max())
    check(u"只算左侧 + 镜像后仍然对称 (%.3f%% 对角线)" % (asym2 / diag * 100),
          asym2 < 0.02 * diag)

    ctl.finish()
    print("")
    if _fails:
        print(u"结果: %d 项失败 / %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print(u"结果: 全部通过 ✓")
    return 0


def _angle(M):
    """世界矩阵相对单位矩阵的旋转角（弧度）"""
    from mh_ssdr import ssdr_core as core
    return core.rotation_angle(core.orthonormalize(np.asarray(M)[:3, :3]))


if __name__ == "__main__":
    sys.exit(main())
