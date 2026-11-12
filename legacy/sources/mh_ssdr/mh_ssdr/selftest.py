# -*- coding: utf-8 -*-
"""
mh_ssdr.selftest
================
在真实场景里做端到端自检（不依赖雕刻，几秒钟出结果）。

它做的事：
  1. 读取当前选中的蒙皮网格，自动校准"蒙皮矩阵公式"（v0~v3），
     这一步能立刻判断本工具读到的 A_j 是否与 Maya 实际变形一致；
  2. 人为制造一个"假雕刻"：随机给几根骨骼加上偏移，算出对应的顶点位移；
  3. 用 SSDR 反求这些偏移，再把结果写回骨骼，读回真实网格顶点，
     与步骤 2 的目标比对 —— 误差接近 0 说明整条链路（矩阵约定 / 写回）正确；
  4. 还原骨骼，场景回到调用前的状态。

用法（Script Editor，Python）::

    import sys; sys.path.insert(0, r"<你的路径>/MH_SSDR_Tool")
    from mh_ssdr import selftest
    selftest.run()                 # 或 selftest.run(angle=8.0, n_bones=10)
"""

from __future__ import unicode_literals

import numpy as np

from maya import cmds

from . import maya_bridge as mb
from . import ssdr_core as core


def _pick_rig():
    for node in cmds.ls(selection=True, long=False) or []:
        try:
            return mb.SkinRig(node)
        except Exception:
            continue
    for mesh in cmds.ls(type="mesh", noIntermediate=True) or []:
        try:
            return mb.SkinRig(mesh)
        except Exception:
            continue
    raise RuntimeError("场景里找不到已蒙皮的网格，请先选中头部网格")


def _random_rigid(rng, angle_deg, trans):
    axis = rng.normal(size=3)
    axis /= np.linalg.norm(axis)
    sign = 1.0 if rng.rand() > 0.5 else -1.0
    ang = np.radians(rng.uniform(0.6, 1.0) * angle_deg * sign)
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    R = np.eye(3) + np.sin(ang) * K + (1 - np.cos(ang)) * (K @ K)
    A = np.eye(4)
    A[:3, :3] = R
    A[:3, 3] = rng.uniform(-trans, trans, 3)
    return A


def run(n_bones=8, angle=6.0, iterations=45, seed=0, out=None):
    say = out or print
    rig = _pick_rig()
    say("=" * 66)
    say("MH-SSDR 自检：%s（顶点 %d / 骨骼 %d）" % (rig.mesh, rig.num_vertices, len(rig.bones)))

    variant, cal = rig.calibrate()
    diag = mb.mesh_diagonal(rig.rest_points)
    say("1) 蒙皮公式校验: 公式 %s，与当前网格变形的吻合误差 %.3e（网格对角线 %.2f）"
        % (variant, cal, diag))
    if cal > 1e-3 * diag:
        say("   ⚠ 误差偏大：请确认 skinCluster.envelope = 1、且没有其它变形器串联")

    rest = rig.rest_points
    current = rig.skinned_transforms()
    counts = rig.bone_vertex_counts()
    candidates = [j for j in range(len(rig.bones)) if counts[j] > 0]
    if not candidates:
        raise RuntimeError("skinCluster 里没有任何有效权重")
    rng = np.random.RandomState(seed)
    picked = list(np.sort(rng.choice(candidates, min(n_bones, len(candidates)), replace=False)))

    snapped = mb.snapshot_channels([rig.bones[j] for j in picked])
    try:
        # --- 2) 造一个已知的"假雕刻" ---------------------------------
        A_new = current.copy()
        for j in picked:
            A_new[j] = current[j] @ _random_rigid(rng, angle, 0.02 * diag)
        target = np.zeros_like(rest)
        for j in range(A_new.shape[0]):
            target += rig.weights[:, j:j + 1] * core.transform_points(A_new[j], rest)
        moved = np.linalg.norm(target - rest, axis=1)
        say("2) 构造假雕刻：%d 根骨骼，顶点最大位移 %.4f / 平均 %.4f"
            % (len(picked), float(moved.max()), float(moved.mean())))

        # --- 3) 反求 ---------------------------------------------------
        solver = core.SkinningSolver(rest, rig.weights, current, target,
                                     bone_names=rig.bones, active=picked)
        rep = solver.solve(iterations=iterations)
        say("3) SSDR 反求: RMS %.5f → %.5f（迭代 %d 次）"
            % (rep["rms_before"], rep["rms"], rep["iterations"]))

        # --- 4) 写回骨骼并读回真实网格 ---------------------------------
        worlds = rig.world_from_skinning(solver.transforms)
        targets = {rig.bones[j]: worlds[rig.bones[j]] for j in picked}
        werr, wworst = mb.apply_world_matrices(targets)
        actual = mb.read_points(rig.shape)
        d = np.linalg.norm(actual - target, axis=1)
        mesh_err = float(np.sqrt(np.mean(d * d)))
        say("4) 骨骼写回误差 %.3e（%s）｜ 真实网格 vs 目标 RMS %.5f"
            % (werr, wworst, mesh_err))

        ok = mesh_err < max(1e-3 * diag, 1e-4)
        say("-" * 66)
        if ok:
            say("✓ 自检通过：矩阵约定与骨骼写回链路正确，可以开始雕刻工作流。")
        else:
            say("✗ 自检未通过：真实网格与目标不符（RMS %.5f）。可能是：" % mesh_err)
            say("   · 骨骼上有关键帧/约束，通道值不是当前帧的最终值")
            say("   · translate/rotate 通道被锁定")
            say("   · skinCluster 前后还有变形器串联，或 envelope ≠ 1")
        say("=" * 66)
        return ok
    finally:
        mb.restore_channels(snapped)
        mb.force_update([rig.shape])
        say("已还原骨骼通道。")
