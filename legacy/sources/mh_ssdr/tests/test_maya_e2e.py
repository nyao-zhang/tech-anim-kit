# -*- coding: utf-8 -*-
"""
test_maya_e2e.py —— 在真实 Maya 环境里跑一遍完整链路（回归测试）

用法（不需要打开 Maya 界面，但需要 Maya 的 Python）::

    "C:/Program Files/Autodesk/Maya2024/bin/mayapy.exe" tests/test_maya_e2e.py

它会在一个临时场景里：建平面 + 关节 + skinCluster -> 提取雕刻网格 ->
用「已知的骨骼偏移」造一个目标形状 -> 反求 -> 写回 -> 比对顶点误差，
顺便校验蒙皮矩阵公式、rest 顶点来源、滑块单调性、临时节点清理。

这套检查是用来防"约定写反"的：蒙皮矩阵的乘积顺序、rest 顶点取法只要错一处，
烘焙结果就会整体偏掉，而这种错在静止姿态下完全看不出来。
"""

from __future__ import print_function

import os
import sys

import numpy as np

import maya.standalone

maya.standalone.initialize(name="python")

import maya.cmds as cmds                                    # noqa: E402

TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOL_DIR not in sys.path:
    sys.path.insert(0, TOOL_DIR)

from mh_ssdr import maya_bridge as mb                        # noqa: E402
from mh_ssdr.tool_ui import SSDRController                   # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(u"  [%s] %s%s" % ("OK" if ok else "FAIL", name, (u"  " + detail) if detail else u""))
    if not ok:
        FAILS.append(name)


def build_scene():
    """平面 + 25 根关节 + skinCluster，并在蒙皮前先改一点顶点（模拟已有雕刻历史）"""
    cmds.file(new=True, force=True)
    mesh = cmds.rename(cmds.polyPlane(w=20, h=12, sx=20, sy=12, ch=False)[0], "head")

    # 先动顶点：这样历史里会留下 tweak 节点，用来校验 rest 顶点取法是否可靠
    cmds.select(mesh + ".vtx[*]")
    cmds.move(0, 0, 0.4, relative=True)
    cmds.select(cl=True)

    root = cmds.joint(n="FACIAL_C_FacialRoot", p=(0, 0, -1.5))
    joints = []
    for iy in range(4):
        for ix in range(6):
            cmds.select(root)
            joints.append(cmds.joint(n="FACIAL_%02d" % len(joints),
                                     p=(-8 + 3.2 * ix, -4.5 + 3.0 * iy, -1.5)))
    cmds.select(cl=True)
    all_joints = [root] + joints
    cmds.skinCluster(root, *joints + [mesh], toSelectedBones=True, maximumInfluences=4)
    return mesh, all_joints


def main():
    print("=" * 70)
    print("MH-SSDR Maya 端回归测试")
    print("=" * 70)

    mesh, all_joints = build_scene()
    shape = mb.shape_of(mesh)
    rest_true = mb.read_points(shape)          # 绑定姿态 = 真实 rest
    base = mb.snapshot_channels(all_joints)

    cmds.select(mesh, replace=True)
    ctl = SSDRController(logger=lambda m: None)
    ctl.set_source_from_selection()
    ctl.extract()
    rig = ctl.rig

    # -- 1. rest 顶点 --------------------------------------------------
    print("\n1) rest 顶点")
    check(u"取自 envelope=0（= skinCluster 真正的输入几何）",
          rig.rest_source == "envelope=0", rig.rest_source)
    check(u"与绑定姿态顶点一致", np.abs(rig.rest_points - rest_true).max() < 1e-5,
          "max=%.2e" % np.abs(rig.rest_points - rest_true).max())

    # -- 2. 蒙皮矩阵公式 -----------------------------------------------
    print("\n2) 蒙皮矩阵公式")
    formula, cal = rig.calibrate()
    diag = mb.mesh_diagonal(rest_true)
    check(u"公式为 GBPW", formula == "GBPW", formula)
    check(u"预测变形与实际变形吻合", cal < 1e-4 * diag,
          "error=%.2e (对角线 %.2f)" % (cal, diag))

    # -- 2b. 骨骼绑定位姿坐标（「骨骼最大距离」用它）---------------------
    pivots = rig.bone_pivots()
    inv_w = np.linalg.inv(np.asarray(rig.mesh_world))
    worst_p = 0.0
    for j, bone in enumerate(rig.bones):
        w = cmds.xform(bone, query=True, worldSpace=True, translation=True)
        obj = inv_w @ np.array([w[0], w[1], w[2], 1.0])
        worst_p = max(worst_p, float(np.linalg.norm(obj[:3] - pivots[j])))
    check(u"bone_pivots() 等于关节的真实位置", worst_p < 1e-6, "max=%.2e" % worst_p)

    # -- 3. 反解世界矩阵 -----------------------------------------------
    print("\n3) 蒙皮矩阵 <-> 世界矩阵 往返")
    cmds.rotate(0, 0, 9, "FACIAL_06", relative=True)
    cmds.rotate(6, 0, 0, "FACIAL_10", relative=True)
    cmds.xform("FACIAL_16", translation=(0.5, -0.4, 0.7), relative=True)
    cmds.rotate(3, 2, 0, "FACIAL_C_FacialRoot", relative=True)
    mb.force_update([shape])

    rig2 = mb.SkinRig(mesh)
    A = rig2.skinned_transforms()
    worlds = rig2.world_from_skinning(A)
    worst = max(np.abs(mb.world_matrix(b) - worlds[b]).max() for b in rig2.bones)
    check(u"由 A 反求出的世界矩阵等于真实关节矩阵", worst < 1e-6, "max=%.2e" % worst)

    # -- 4. 端到端烘焙 ---------------------------------------------------
    print("\n4) 端到端：已知骨骼偏移 -> 雕刻目标 -> 反求 -> 烘焙")
    target = mb.read_points(shape)
    moved = float(np.max(np.linalg.norm(target - rest_true, axis=1)))
    mb.restore_channels(base)
    mb.force_update([shape])

    mb.write_points(mb.shape_of(ctl.sculpt_mesh()), target)
    mb.force_update([mb.shape_of(ctl.sculpt_mesh())])

    rep = ctl.solve(rig.bones, iterations=60)
    err_write, _ = ctl.apply_weight(1.0)
    got = mb.read_points(shape)
    d = np.linalg.norm(got - target, axis=1)
    pct = float(d.max() / diag * 100.0)

    check(u"骨骼世界矩阵写回精确", err_write < 1e-6, "max=%.2e" % err_write)
    check(u"烘焙后贴合雕刻目标（< 1% 对角线）", pct < 1.0,
          "max=%.4f / mean=%.4f / %.3f%% of %.2f（目标位移 %.3f）"
          % (d.max(), d.mean(), pct, diag, moved))
    big = [d["name"] for d in rep["deltas"]
           if d["active"] and (d["angle"] > 60.0 or d["translate"] > 0.05 * diag)]
    check(u"没有骨骼被解出离谱偏移", not big, u", ".join(big) or u"无")

    # -- 4b. 骨骼最大距离过滤 --------------------------------------------
    print("\n4b) 骨骼最大距离：只改局部时远处的骨骼不参与")
    # polyPlane 落在 XZ 平面（法线 +Y），所以局部鼓包加在 Y 上
    local = rest_true.copy()
    near = np.hypot(rest_true[:, 0] + 7.0, rest_true[:, 2] + 4.0) < 2.5
    local[near, 1] += 0.6
    mb.write_points(mb.shape_of(ctl.sculpt_mesh()), local)
    mb.force_update([mb.shape_of(ctl.sculpt_mesh())])
    ctl.solve(rig.bones, iterations=8, max_dist=6.0)
    n_active = len(ctl.last_active)
    check(u"按距离剔除了远处的骨骼", 0 < n_active < len(rig.bones),
          u"参与 %d / %d 根" % (n_active, len(rig.bones)))
    moved_pts = rest_true[local[:, 1] > rest_true[:, 1] + 1e-3]
    worst_d = 0.0
    for j in ctl.last_active:
        d_j = np.linalg.norm(moved_pts - rig.bone_pivots()[j], axis=1).min()
        worst_d = max(worst_d, float(d_j))
    check(u"保留下来的骨骼都在距离内", worst_d <= 6.0 + 1e-6, "最远 %.3f" % worst_d)

    # -- 4c. 只算雕刻区附近的骨骼（影响权重自动圈定）---------------------
    print("\n4c) 只算雕刻区附近的骨骼：影响权重自动圈定 + 重要度过滤")
    rep_sc = ctl.solve(rig.bones, iterations=10, max_dist=0.0,
                       scope=True, min_share=0.01, prune=False)
    n_sc = len(ctl.last_active)
    check(u"影响权重把不相干的骨骼排除了", 0 < n_sc < len(rig.bones),
          u"参与 %d / %d 根" % (n_sc, len(rig.bones)))
    piv = rig.bone_pivots()
    worst_sc = 0.0
    for j in ctl.last_active:
        worst_sc = max(worst_sc, float(np.linalg.norm(moved_pts - piv[j], axis=1).min()))
    check(u"参与的都是离雕刻区近的骨骼", worst_sc <= 6.0,
          u"最远 %.3f（局部鼓包半径 2.5）" % worst_sc)
    frozen_delta = 0.0
    for j in range(len(rig.bones)):
        if j in ctl.last_active:
            continue
        frozen_delta = max(frozen_delta, float(np.abs(ctl.solver.transforms[j]
                                                      - ctl.solver.A0[j]).max()))
    check(u"未参与的骨骼严格保持原姿态", frozen_delta < 1e-12, "max=%.2e" % frozen_delta)

    rep_pr = ctl.solve(rig.bones, iterations=10, max_dist=0.0,
                       scope=True, min_share=0.01, prune=True,
                       prune_translate=0.02, prune_rotate=0.2)
    check(u"重要度过滤不误伤真正出力的骨骼", rep_pr["rms"] - rep_sc["rms"] < 1e-4 * diag,
          "冻结 %d 根 ｜ RMS %.6f -> %.6f"
          % (len(rep_pr.get("frozen") or []), rep_sc["rms"], rep_pr["rms"]))

    # 阈值设到很大时，骨骼必须严格退回原姿态（验证"退回"是精确的，不是缩一点）
    rep_big = ctl.solve(rig.bones, iterations=10, max_dist=0.0,
                        scope=False, min_share=0.0, prune=True,
                        prune_translate=100.0, prune_rotate=89.0)
    worst = max([float(np.abs(ctl.solver.transforms[j] - ctl.solver.A0[j]).max())
                 for j in range(len(rig.bones))] or [0.0])
    check(u"阈值过大时所有骨骼精确回到原姿态",
          int(rep_big.get("active_count", -1)) == 0 and worst < 1e-12,
          u"冻结 %d 根，残留 %.2e" % (len(rep_big.get("frozen") or []), worst))

    # 恢复成完整的端到端目标，后面滑块用的是这一份。
    # 故意**不**把骨骼还原：此刻骨骼停在上一次的 100% 上，求解器应当自动
    # 以"提取时的姿态"作为 0% 基准（见 _reference_transforms）。
    mb.write_points(mb.shape_of(ctl.sculpt_mesh()), target)
    mb.force_update([mb.shape_of(ctl.sculpt_mesh())])
    ctl.solve(rig.bones, iterations=60)

    # -- 5. 滑块 ---------------------------------------------------------
    print("\n5) 权重滑块")
    ctl.apply_weight(0.0)
    back = np.linalg.norm(mb.read_points(shape) - rest_true, axis=1).max()
    check(u"0% 精确回到原姿态", back < 1e-5, "max=%.2e" % back)

    seq = []
    for t in (0.0, 0.25, 0.5, 0.75, 1.0):
        ctl.apply_weight(t)
        seq.append(float(np.linalg.norm(mb.read_points(shape) - rest_true, axis=1).max()))
    mono = all(b >= a - 1e-6 for a, b in zip(seq, seq[1:]))
    check(u"随滑块单调逼近 100%", mono, " -> ".join("%.3f" % v for v in seq))

    # -- 6. 清理 ---------------------------------------------------------
    print("\n6) 收尾")
    ctl.apply_weight(1.0)
    ctl.finish()
    left = [n for n in cmds.ls(type="transform") or [] if "SSDR" in n]
    check(u"临时网格已清理", not left, ", ".join(left) or u"无")
    check(u"源网格恢复显示", bool(cmds.getAttr(mesh + ".visibility")))

    print("\n" + "=" * 70)
    if FAILS:
        print(u"❌ 失败 %d 项：%s" % (len(FAILS), "; ".join(FAILS)))
        return 1
    print(u"✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
