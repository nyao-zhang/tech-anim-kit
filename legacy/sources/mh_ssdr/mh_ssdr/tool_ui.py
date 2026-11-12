# -*- coding: utf-8 -*-
"""
mh_ssdr.tool_ui
===============
MetaHuman 面部骨骼 SSDR 烘焙工具 —— 控制器 + 界面（Maya 2024 / Python 3 / PySide2）

工作流
------
1. 选中已蒙皮的头部网格 -> [使用当前选择]
2. [提取雕刻网格]：生成一份静态副本（原网格隐藏）
3. 用「雕刻几何体」工具改形（不要改变拓扑）
4. 勾选参与计算的骨骼（默认 FACIAL_* 全部）
5. [计算骨骼偏移]：SSDR 反求每根骨骼的新位置
   · 「只算雕刻区附近的骨骼」按影响权重自动收窄参与集合（不必手动挑骨骼）
   · 「自动稳定」只把数据无法判定的旋转方向按不动处理，不影响真实运动
   · 「重要度过滤」把解出来几乎没动的骨骼退回原姿态
6. （可选）[镜像到另一侧]：把一侧的结果按左右对称写过去
7. 拖动权重滑块 0%~100% 预览（松手才写骨骼），满意后点 [应用 100%]

命令入口：
    from mh_ssdr.tool_ui import show; show()
"""

from __future__ import unicode_literals

import traceback

import numpy as np

from maya import cmds, OpenMayaUI as omui

try:
    from PySide2 import QtCore, QtGui, QtWidgets
    from shiboken2 import wrapInstance
    _QT = "PySide2"
except ImportError:                                   # Maya 2025+
    from PySide6 import QtCore, QtGui, QtWidgets
    from shiboken6 import wrapInstance
    _QT = "PySide6"

from . import maya_bridge as mb
from . import mirror as mir
from . import ssdr_core as core

WINDOW_NAME = "mhSSDRWindow"
WINDOW_TITLE = "MetaHuman 面部骨骼 SSDR 烘焙工具  v1.4"

_window = None


# ============================================================================
# 控制器
# ============================================================================
class SSDRController(object):
    def __init__(self, logger=None):
        self._log = logger or (lambda msg: None)
        self.rig = None
        self.solver = None
        self.affine = False
        self.report = None
        self.last_active = []
        self.scoped = None           # 最近一次"按影响圈定"的结果（kept/share/dropped）
        self.state = mb.load_state() or {}

    # -- 基础 ---------------------------------------------------------------
    def log(self, msg):
        try:
            self._log(u"%s" % msg)
        except Exception:
            pass

    def _require_rig(self):
        if self.rig is None:
            self._auto_rig_from_state()
        if self.rig is None:
            raise RuntimeError("请先选择已蒙皮的模型并点击『使用当前选择』")
        return self.rig

    def _auto_rig_from_state(self):
        name = self.state.get("source_mesh")
        if name and cmds.objExists(name):
            try:
                self.rig = mb.SkinRig(name)
            except Exception:
                self.rig = None
        return self.rig

    # -- 源网格 -------------------------------------------------------------
    def set_source_from_selection(self):
        sel = cmds.ls(selection=True, long=False) or []
        if not sel:
            raise RuntimeError("请先在视口中选中一个已蒙皮的网格")
        tried = []
        for node in sel:
            try:
                rig = mb.SkinRig(node)
            except Exception as exc:
                tried.append("%s (%s)" % (node, exc))
                continue
            self.rig = rig
            self.state.update({
                "source_mesh": rig.mesh,
                "skin_cluster": rig.skin_cluster,
            })
            mb.save_state(self.state)

            variant, cal = rig.calibrate()
            diag = mb.mesh_diagonal(rig.rest_points)
            self.log("源网格: %s  顶点 %d  面 %d  影响骨骼 %d" %
                     (rig.mesh, rig.num_vertices, rig.num_polygons, len(rig.bones)))
            self.log("蒙皮公式校验: 公式 %s, 与当前网格变形吻合误差 %.3e (网格对角线 %.2f)" %
                     (variant, cal, diag))
            if cal > 1e-4 * max(diag, 1e-6):
                self.log("⚠ 自检误差偏大，说明该 skinCluster 的矩阵约定比较特殊，"
                         "烘焙结果可能不准，请先小范围试算验证。")
            if rig.zero_weight_vertices:
                self.log("⚠ 有 %d 个顶点权重为 0（不受骨骼控制），这些顶点无法被烘焙" %
                         rig.zero_weight_vertices)
            if abs(float(rig.envelope) - 1.0) > 1e-6:
                self.log("⚠ skinCluster.envelope = %s（不是 1），烘焙结果会与视图不一致" % rig.envelope)
            if str(rig.normalize_weights) in ("0", "0.0"):
                self.log("⚠ skinCluster.normalizeWeights = 0（权重未归一化），"
                         "工具会按行归一化处理，可能出现细小差异")
            return rig
        raise RuntimeError("选中的对象不是蒙皮网格：\n" + "\n".join(tried))

    def bone_rows(self):
        rig = self._require_rig()
        counts = rig.bone_vertex_counts()
        return [(name, int(c)) for name, c in zip(rig.bones, counts)]

    def default_checked_bones(self):
        rig = self.rig
        if rig is None:
            return []
        facial = rig.facial_bones()
        if facial:
            return facial
        counts = rig.bone_vertex_counts()
        order = np.argsort(-counts)[:30]
        return [rig.bones[i] for i in order if counts[i] > 0]

    # -- 提取 / 雕刻 --------------------------------------------------------
    def sculpt_mesh(self):
        name = self.state.get("sculpt_mesh")
        if name and cmds.objExists(name):
            return name
        return None

    def backup_mesh(self):
        name = self.state.get("backup_mesh")
        if name and cmds.objExists(name):
            return name
        return None

    def extract(self):
        rig = self._require_rig()
        for key in ("sculpt_mesh", "backup_mesh"):
            old = self.state.get(key)
            if old and cmds.objExists(old):
                try:
                    cmds.delete(old)
                except Exception:
                    pass
        with mb.undo_chunk(u"MH-SSDR 提取雕刻网格"):
            backup, sculpt = mb.extract_sculpt(rig, hide_source=True)
        self.state.update({
            "sculpt_mesh": sculpt,
            "backup_mesh": backup,
            # 提取这一刻的姿态就是滑块 0% 的基准（也是「还原骨骼」回到的位置）
            "base_channels": mb.snapshot_channels(rig.bones),
            "source_bbox": mb.mesh_diagonal(rig.rest_points),
        })
        mb.save_state(self.state)
        self.solver = None
        self.log("已生成雕刻网格: %s（源网格 %s 已隐藏）" % (sculpt, rig.mesh))
        self.log("现在请用「雕刻几何体」工具改形；注意：不要增删顶点、不要改拓扑。")
        return sculpt

    def reset_sculpt(self):
        sculpt, backup = self.sculpt_mesh(), self.backup_mesh()
        if not sculpt or not backup:
            raise RuntimeError("还没有提取过雕刻网格")
        with mb.undo_chunk(u"MH-SSDR 重置雕刻"):
            mb.reset_sculpt(sculpt, backup)
        self.log("雕刻网格已恢复为原始形状")

    def toggle_view(self):
        rig = self._require_rig()
        sculpt = self.sculpt_mesh()
        if not sculpt:
            raise RuntimeError("还没有提取过雕刻网格")
        src_vis = cmds.getAttr(rig.mesh + ".visibility")
        if src_vis:
            cmds.setAttr(rig.mesh + ".visibility", 0)
            cmds.setAttr(sculpt + ".visibility", 1)
            self.log("显示：雕刻网格")
        else:
            cmds.setAttr(rig.mesh + ".visibility", 1)
            cmds.setAttr(sculpt + ".visibility", 0)
            self.log("显示：源网格（骨骼驱动结果）")

    def show_source_only(self):
        rig = self._require_rig()
        sculpt = self.sculpt_mesh()
        cmds.setAttr(rig.mesh + ".visibility", 1)
        if sculpt:
            cmds.setAttr(sculpt + ".visibility", 0)

    def _sculpt_inputs(self):
        """取出反求需要的三样东西，并做前置校验（rest / target / 位移）。"""
        rig = self._require_rig()
        sculpt = self.sculpt_mesh()
        if not sculpt:
            raise RuntimeError("请先点击『① 提取雕刻网格』")
        ok, msg = mb.check_topology(rig, sculpt)
        if not ok:
            raise RuntimeError("雕刻网格的拓扑已被改变（%s），SSDR 无法反求。\n"
                               "请只做形变雕刻，不要增删顶点/分割面。" % msg)

        rest = rig.rest_points
        target = mb.read_points(mb.shape_of(sculpt))
        if target.shape[0] != rest.shape[0]:
            raise RuntimeError("顶点数不一致：雕刻 %d / 源 %d" % (target.shape[0], rest.shape[0]))

        delta = np.linalg.norm(target - rest, axis=1)
        if float(delta.max()) < 1e-7:
            raise RuntimeError("雕刻网格与原始网格完全一致，没有可反求的位移")
        return rig, rest, target, delta

    def influenced_bones(self, min_share=0.01):
        """按雕刻区域推断"应该参与计算"的骨骼（给『按雕刻区自动勾选』按钮用）。"""
        rig, rest, target, delta = self._sculpt_inputs()
        try:
            pivots = rig.bone_pivots()
        except Exception:
            pivots = None
        kept, dropped, info = core.select_influential_bones(
            rest, rig.weights, target, candidates=list(range(len(rig.bones))),
            pivots=pivots, min_share=float(min_share))
        self.log("按雕刻区域圈定骨骼：%d 个顶点被改动，保留 %d 根，排除 %d 根（影响 < %.2f%%）" %
                 (info["moved_n"], len(kept), len(dropped), 100.0 * float(min_share)))
        return [rig.bones[j] for j in kept]

    # -- 数据准备 -----------------------------------------------------------
    def _build_solver(self, bones, affine, max_dist=0.0, scope=False, min_share=0.0):
        rig, rest, target, delta = self._sculpt_inputs()

        current = self._reference_transforms(rig)
        checked = [rig.bones.index(b) for b in bones if b in rig.bones]
        if not checked:
            raise RuntimeError("没有勾选任何参与计算的骨骼")

        # ④-2 按"骨骼离被雕刻区域有多远"筛一遍（max_dist <= 0 表示不限制）
        idx = list(checked)
        max_dist = float(max_dist or 0.0)
        if max_dist > 0.0:
            idx = self._filter_by_distance(rig, rest, target, idx, max_dist)

        # ④-3 按"这次雕刻实际依赖谁"圈定（影响权重低于阈值的不参与）
        min_share = float(min_share or 0.0)
        self.scoped = None
        if scope and min_share > 0.0:
            idx = self._scope_by_influence(rig, rest, target, idx, min_share)

        self.last_active = list(idx)
        solver = core.SkinningSolver(rest, rig.weights, current, target,
                                     bone_names=rig.bones, active=idx)
        self.log("雕刻位移: 最大 %.4f / 平均 %.4f / 有变化顶点 %d（共 %d）" %
                 (float(delta.max()), float(delta.mean()), int((delta > 1e-5).sum()), delta.size))
        self.log("参与计算骨骼 %d 根（勾选 %d 根%s%s）" %
                 (len(idx), len(checked),
                  u"" if max_dist <= 0 else u"，距离限制 %.3f" % max_dist,
                  u"" if not (scope and min_share > 0.0)
                  else u"，影响阈值 %.2f%%" % (100.0 * min_share)))
        self._check_coverage(rig, delta, idx)
        return solver, list(idx)

    def _scope_by_influence(self, rig, rest, target, idx, min_share):
        """只让"这次雕刻真正需要"的骨骼参与计算。

        判据是影响权重 ``Σ_{被雕刻顶点} w_ij·|δ_i|``（见 ``core.influence_share``）：
        骨骼离雕刻区越远、或者在那些顶点上的权重越小，这个量越接近 0。它一次就能
        把"只改了嘴角，额头/眼睛附近骨骼却跟着动"整片排掉，比手动勾选可靠得多，
        也比「自动推荐」靠谱——后者按单根骨骼单独求解后的整体误差排序，覆盖大区域的
        主骨永远排前面，真正贴着雕刻区的小骨容易被挤出前 N 名。
        """
        try:
            pivots = rig.bone_pivots()
        except Exception as exc:                    # 骨架异常不要因此中断计算
            self.log("⚠ 无法读取骨骼位置（%s），本次不按影响圈定" % exc)
            return idx
        kept, dropped, info = core.select_influential_bones(
            rest, rig.weights, target, candidates=idx, pivots=pivots,
            min_share=min_share)
        self.scoped = {"kept": kept, "share": info["share"],
                       "moved_n": info["moved_n"],
                       "dropped": [j for j, _ in dropped]}
        if info.get("fallback"):
            self.log("⚠ 影响阈值 %.2f%% 把勾选的骨骼全部排除了，已只保留影响最大的一根。"
                     "请把阈值调小。" % (100.0 * min_share))
        if dropped:
            dropped.sort(key=lambda x: info["share"][x[0]])
            self.log("ℹ 雕刻只动到了 %d 个顶点，按影响权重排除 %d 根骨骼（< %.2f%%）：" %
                     (info["moved_n"], len(dropped), 100.0 * min_share))
            self.log("     " + u", ".join(
                u"%s(%.2f%%)" % (rig.bones[j].split("|")[-1], 100.0 * info["share"][j])
                for j, _ in dropped[:8]) + (u" …" if len(dropped) > 8 else u""))
        kept.sort(key=lambda j: -info["share"][j])
        self.log("     参与：%s%s" % (
            u", ".join(u"%s(%.1f%%)" % (rig.bones[j].split("|")[-1],
                                        100.0 * info["share"][j]) for j in kept[:8]),
            u" …" if len(kept) > 8 else u""))
        return kept

    def _check_coverage(self, rig, delta, idx):
        """诊断：被雕刻的顶点里，有多少几乎不受"参与计算的骨骼"控制。

        这种情况反求再准也贴不上（那部分顶点根本没法被驱动），是最常见的
        "怎么算都拟合不上"的原因——勾选少了、或者最大距离把控制它们的骨骼滤掉了。
        """
        mx = float(delta.max()) if delta.size else 0.0
        if mx <= 0.0 or not idx:
            return
        big = delta > max(0.02 * mx, 1e-6)
        n = int(big.sum())
        if n == 0:
            return
        w = np.asarray(rig.weights[np.nonzero(big)[0]][:, list(idx)], dtype=np.float64).sum(axis=1)
        dead = int((w < 0.3).sum())
        if dead > max(3, int(0.02 * n)):
            self.log("⚠ 雕刻区域有 %d 个顶点（%.1f%%）几乎不受勾选骨骼控制，"
                     "这些地方无论如何都贴不上：请勾上控制它们的骨骼，"
                     "或把『骨骼最大距离』调大。" % (dead, 100.0 * dead / n))

    def _reference_transforms(self, rig):
        """求解基准姿态（滑块 0% 对应的姿态）。

        优先用**提取时**（`base_channels`）的姿态，而不是"此刻场景里的姿态"。
        否则一旦执行过「应用 100%」再点一次计算，0% 就会悄悄变成上一次的 100%，
        滑块往回拖也回不到原形——很难察觉、也很难受。
        做法是临时把 base 通道装回去读一次矩阵，读完立刻还原当前姿态（场景不变）。
        """
        base = self.state.get("base_channels") or {}
        nodes = [n for n in base if cmds.objExists(n)]
        if not nodes:
            return rig.skinned_transforms()

        now = mb.snapshot_channels(nodes)
        moved = 0.0
        for n in nodes:
            for k, v in (base.get(n) or {}).items():
                cur = (now.get(n) or {}).get(k)
                if cur is not None:
                    moved = max(moved, abs(float(cur) - float(v)))
        if moved <= 1e-6:                       # 已经在提取时的姿态
            return rig.skinned_transforms()

        try:
            mb.restore_channels(base)
            mb.force_update(nodes)
            out = rig.skinned_transforms()
        finally:
            mb.restore_channels(now)
            mb.force_update(nodes)
        self.log("ℹ 骨骼当前不在提取时的姿态，本次以**提取时姿态**作为 0% 基准"
                 "（场景姿态没有改动）")
        return out

    def _filter_by_distance(self, rig, rest, target, idx, max_dist):
        """把离雕刻区域超过 max_dist 的骨骼剔出参与集合。"""
        try:
            pivots = rig.bone_pivots()
        except Exception as exc:                    # 骨架异常时不要因此中断计算
            self.log("⚠ 无法读取骨骼位置（%s），本次不做距离过滤" % exc)
            return idx
        kept, dropped, detail = core.bones_within_reach(
            pivots, rest, target, candidates=idx, max_dist=max_dist)
        if not kept:
            near = sorted(detail, key=lambda x: x[1])[:5]
            raise RuntimeError(
                "勾选的骨骼全都离雕刻区域超过 %.3f，没有骨骼可参与计算。\n"
                "最近的几根：\n  %s\n\n请调大『骨骼最大距离』，或设成 0 取消限制。" %
                (max_dist, u"\n  ".join(u"%s   距离 %.3f" % (rig.bones[j], d) for j, d in near)))
        if dropped:
            dropped.sort(key=lambda j: -dict(detail)[j])
            self.log("ℹ 离雕刻区域超过 %.3f，剔除 %d 根骨骼：%s%s" %
                     (max_dist, len(dropped),
                      u", ".join(rig.bones[j].split("|")[-1] for j in dropped[:6]),
                      u" …" if len(dropped) > 6 else u""))
        return kept

    def solve(self, bones, iterations=40, damping=1.0, affine=False, stabilize=True,
              max_dist=0.0, scope=True, min_share=0.01, prune=True,
              prune_translate=0.02, prune_rotate=0.2):
        solver, idx = self._build_solver(bones, affine, max_dist=max_dist,
                                        scope=scope, min_share=min_share)
        rep = solver.solve(iterations=iterations, damping=damping, allow_affine=affine,
                           stabilize=stabilize,
                           prune_translate=(float(prune_translate) if prune else 0.0),
                           prune_rotate=(float(prune_rotate) if prune else 0.0))
        self.solver = solver
        self.affine = affine
        self.report = rep

        if not self.state.get("base_channels"):
            self.state["base_channels"] = mb.snapshot_channels(self.rig.bones)
            mb.save_state(self.state)
        if not self.state.get("bones"):
            self.state["bones"] = [self.rig.bones[i] for i in idx]
            mb.save_state(self.state)

        diag = mb.mesh_diagonal(self.rig.rest_points)
        pct = rep["rms"] / max(diag, 1e-9) * 100.0
        moved = [d for d in rep["deltas"] if d["active"] and
                 (d["angle"] > 1e-4 or d["translate"] > 1e-6)]
        moved.sort(key=lambda d: d["angle"] * 10.0 + d["translate"], reverse=True)

        self.log("反求完成: RMS %.5f → %.5f（占网格对角线 %.3f%%），迭代 %d 次" %
                 (rep["rms_before"], rep["rms"], pct, rep["iterations"]))
        self.log("参与骨骼 %d 根，实际发生变化 %d 根；残差最大 %.5f" %
                 (len(idx), len(moved), rep["max"]))
        for d in moved[:8]:
            self.log("   %-34s 旋转 %7.3f°  平移 %.4f" %
                     (d["name"], d["angle"], d["translate"]))

        # 重要度过滤：解出来几乎没动的骨骼已退回原姿态
        if rep.get("frozen_detail"):
            self.log("ℹ 重要度过滤：%d 根骨骼解出来几乎没动（旋转与位移都低于阈值），"
                     "已保持不动（不再产生无意义的微小位移）：" % len(rep["frozen_detail"]))
            for name, note in rep["frozen_detail"][:8]:
                self.log("     %-32s %s" % (name.split("|")[-1], note))
            if len(rep["frozen_detail"]) > 8:
                self.log("     …另有 %d 根" % (len(rep["frozen_detail"]) - 8))

        # 被规则挡下来的骨骼：说明原因，而不是让用户去反复取消勾选
        if rep.get("low_support"):
            self.log("ℹ %d 根骨骼几乎不影响任何顶点，已保持不动：%s" %
                     (len(rep["low_support"]), ", ".join(rep["low_support"][:6])))
            for name in rep["low_support"][:6]:
                reason = (rep.get("locked_reasons") or {}).get(name)
                if reason:
                    self.log("     %-32s %s" % (name, reason))
        if rep.get("degenerate"):
            self.log("ℹ %d 根骨骼的影响顶点近似一条线（补皮骨常见），绕这条线的自旋"
                     "在数据上无法判定，已按『不转』处理（不影响其它方向）：%s" %
                     (len(rep["degenerate"]),
                      ", ".join(n.split("|")[-1] for n in rep["degenerate"][:6])))
        if pct > 3.0:
            self.log("⚠ 拟合误差偏大（占对角线 %.2f%%）：勾选的骨骼不足以表达这次雕刻。"
                     "可以增加参与计算的骨骼，或调大『骨骼最大距离』。" % pct)
        return rep

    # -- 应用 ---------------------------------------------------------------
    def apply_weight(self, weight, key=False):
        if self.solver is None:
            raise RuntimeError("请先点击『④ 计算骨骼偏移（SSDR 反求）』")
        rig = self._require_rig()
        A = self.solver.blended(weight, rigid=not self.affine)
        worlds = rig.world_from_skinning(A)
        active = np.nonzero(self.solver.active)[0]
        targets = {}
        for j in active:
            bone = rig.bones[j]
            if cmds.objExists(bone):
                targets[bone] = worlds[bone]
        with mb.undo_chunk(u"MH-SSDR 应用偏移 %d%%" % int(round(weight * 100))):
            err, worst = mb.apply_world_matrices(targets, key=key)
        scale = max(mb.mesh_diagonal(rig.rest_points), 1e-9)
        if err > 1e-3 * scale:
            self.log("⚠ 骨骼写回误差较大（%.5f，骨骼 %s）。该 rig 可能存在 "
                     "jointOrient/锁定通道，建议改用『旋转/位移』通道手动确认。" % (err, worst))
        return err, worst

    def revert(self):
        base = self.state.get("base_channels") or {}
        if not base:
            raise RuntimeError("没有基线数据（可能还没计算过）")
        with mb.undo_chunk(u"MH-SSDR 还原骨骼"):
            mb.restore_channels(base)
        self.log("已将 %d 根骨骼还原到提取时的状态" % len(base))

    # -- 镜像 ---------------------------------------------------------------
    def mirror_pairs(self, direction="L2R"):
        """按名称列出左右配对的骨骼 (源, 目标)。"""
        rig = self._require_rig()
        want = mir.SIDE_L if direction == "L2R" else mir.SIDE_R
        return mir.build_pairs(list(rig.bones), want, set(rig.bones))

    def mirror(self, direction="L2R", mode="overwrite", axis=None):
        """把反求出的偏移镜像到另一侧。

        :param direction: "L2R" 把左侧偏移写到右侧；"R2L" 反之
        :param mode: "overwrite" 覆盖绝对值（对侧最终 = 源侧的镜像）
                     "additive"  叠加偏移（对侧原有偏移之上再叠加镜像量）
        :param axis: None=自动判断镜像轴；0/1/2 = 强制世界 X/Y/Z

        写回的是**求解器的结果矩阵**而不是直接写场景，所以镜像完成后
        权重滑块依然统一控制两侧骨骼，拖动时不会把镜像结果冲掉。
        """
        if self.solver is None:
            raise RuntimeError("请先点击『④ 计算骨骼偏移（SSDR 反求）』")
        rig = self._require_rig()
        solver = self.solver

        pairs = self.mirror_pairs(direction)
        if not pairs:
            raise RuntimeError(
                "没有找到可镜像的骨骼对。\n\n"
                "工具按骨骼名里的左右标记配对（如 FACIAL_L_Eye / FACIAL_R_Eye、"
                "Brow.L / Brow.R、xxx_left / xxx_right），并且要求对侧骨骼也在"
                "本次参与计算的骨骼里。\n"
                "请检查命名，或先把两侧骨骼都勾选上再重新计算。")

        base = rig.world_from_skinning(solver.A0)
        full = rig.world_from_skinning(solver.A)

        # 用配对关节在绑定姿态下的位置估计镜像面
        src_pos = np.stack([base[s][3, :3] for s, _ in pairs])
        dst_pos = np.stack([base[d][3, :3] for _, d in pairs])
        axe, coord, resid = mir.estimate_plane(src_pos, dst_pos, axis)
        R = mir.reflection_row(axe, coord)
        diag = max(mb.mesh_diagonal(rig.rest_points), 1e-9)

        # 源侧几乎没动的骨骼跳过：覆盖模式下它们会把对侧清零，容易误伤
        live, idle = [], 0
        replaced = 0
        for s, d in pairs:
            D = mir.relative(base[s], full[s])
            ang = float(np.degrees(core.rotation_angle(D[:3, :3])))
            tr = float(np.linalg.norm(D[3, :3]))
            if ang <= 1e-3 and tr <= 1e-6 * diag:
                idle += 1
                continue
            if mode == "overwrite":
                Dd = mir.relative(base[d], full[d])
                if (float(np.degrees(core.rotation_angle(Dd[:3, :3]))) > 1e-3 or
                        float(np.linalg.norm(Dd[3, :3])) > 1e-6 * diag):
                    replaced += 1
            live.append((s, d, D))
        if not live:
            raise RuntimeError("源侧骨骼没有任何偏移，没有可镜像的内容。\n"
                               "请确认勾选的方向对（例：只雕了右侧就选『右 → 左』）。")

        index_of = dict((b, i) for i, b in enumerate(rig.bones))
        new_world = {}
        rows = []
        for s, d, D in live:
            Dm = mir.conjugate(D, R)                     # 关键：反射共轭
            if mode == "additive":
                Dn = mir.relative(base[d], full[d]) @ Dm
            else:
                Dn = Dm
            new_world[d] = base[d] @ Dn
            rows.append((s, d,
                         float(np.degrees(core.rotation_angle(Dn[:3, :3]))),
                         float(np.linalg.norm(Dn[3, :3]))))

        A_new = rig.skinning_from_world(new_world)
        for d in new_world:
            j = index_of[d]
            solver.set_bone_transform(j, A_new[j], refresh=False)
        added = solver.activate([index_of[d] for d in new_world])
        solver.refresh()

        angs = [r[2] for r in rows]
        trs = [r[3] for r in rows]
        self.log("镜像：%s ｜ %s ｜ 配对 %d 对（源侧无偏移跳过 %d 对）" %
                 (u"左 → 右" if direction == "L2R" else u"右 → 左",
                  u"覆盖绝对值" if mode == "overwrite" else u"叠加偏移",
                  len(pairs), idle))
        self.log("镜像面：世界 %s = %.4f ｜ 左右配对残差 %.2e（占网格对角线 %.4f%%）" %
                 (mir.AXIS_NAMES[axe], coord, resid, resid / diag * 100.0))
        if resid > 0.01 * diag:
            self.log("⚠ 配对残差偏大：可能镜像轴判断错了，或左右骨骼命名不是严格对称。"
                     "请核对下方配对明细，必要时手动指定镜像轴。")
        self.log("已写入 %d 根骨骼%s" % (len(rows),
                                      "（其中 %d 根原本未参与计算，已自动加入）" % len(added)
                                      if added else ""))
        self.log("  旋转 平均 %.3f° / 最大 %.3f° ｜ 平移 平均 %.4f / 最大 %.4f" %
                 (float(np.mean(angs)), float(np.max(angs)),
                  float(np.mean(trs)), float(np.max(trs))))
        if replaced:
            self.log("ℹ %d 根对侧骨骼原本也有自己的偏移，已按『覆盖绝对值』丢弃"
                     "（想保留请改用『叠加偏移』）" % replaced)
        for s, d, a, t in sorted(rows, key=lambda r: -r[2])[:5]:
            self.log("   %-32s → %-32s %7.3f°  %.4f" % (s.split("|")[-1], d.split("|")[-1], a, t))
        self.log("提示：镜像结果已并入求解结果，滑块 0%→100% 会同时控制两侧；"
                 "重新点『计算骨骼偏移』会覆盖镜像。")
        return {"pairs": len(pairs), "written": len(rows), "idle": idle,
                "axis": axe, "coord": coord, "residual": resid,
                "replaced": replaced, "activated": [rig.bones[j] for j in added]}

    def recommend(self, top_n=24):
        rig = self._require_rig()
        solver, idx = self._build_solver(rig.bones, False)
        ranked = solver.rank_bones()
        names = []
        for j, err in ranked:
            counts = int((rig.weights[:, j] > 0.001).sum())
            if counts <= 0:
                continue
            names.append(rig.bones[j])
            if len(names) >= top_n:
                break
        self.log("已按贡献度推荐 %d 根骨骼（单独求解后整体误差最小的优先）" % len(names))
        return names

    def finish(self, delete_temp=True):
        rig = self._require_rig()
        self.show_source_only()
        if delete_temp:
            for key in ("sculpt_mesh", "backup_mesh"):
                node = self.state.get(key)
                if node and cmds.objExists(node):
                    cmds.delete(node)
                self.state[key] = ""
            mb.save_state(self.state)
            self.log("已清理临时网格，源网格恢复显示")
        self.solver = None


# ============================================================================
# 界面
# ============================================================================
class SSDRWindow(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(SSDRWindow, self).__init__(parent)
        self.setObjectName(WINDOW_NAME)
        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        self.resize(640, 900)
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, False)

        self._check_state = {}
        self._bone_rows = []
        self._view_switched = False

        self.ctl = SSDRController(logger=self._append_log)
        self._build_ui()
        self._restore_from_scene()
        self._apply_style()

    # -- 样式 ---------------------------------------------------------------
    def _apply_style(self):
        pal = self.palette()
        mid = pal.color(QtGui.QPalette.Mid).name()
        self.setStyleSheet("""
            QGroupBox { border: 1px solid %s; border-radius: 6px; margin-top: 12px;
                        padding-top: 6px; font-weight: bold; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QPushButton { padding: 5px 8px; }
            QPushButton#step { font-weight: bold; padding: 7px 8px; }
            QPlainTextEdit { font-family: Consolas, monospace; font-size: 11px; }
        """ % mid)

    # -- 构建 ---------------------------------------------------------------
    def _build_ui(self):
        # 内容整体放进滚动区：控件多，笔记本/低分辨率下不至于被截掉下半截
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QtWidgets.QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        outer.addWidget(scroll)
        page = QtWidgets.QWidget()
        scroll.setWidget(page)

        root = QtWidgets.QVBoxLayout(page)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        # ---- 1. 源网格
        g1 = QtWidgets.QGroupBox(u"① 源网格（已蒙皮的头部）")
        l1 = QtWidgets.QVBoxLayout(g1)
        row = QtWidgets.QHBoxLayout()
        self.lblSource = QtWidgets.QLabel(u"未选择")
        self.lblSource.setWordWrap(True)
        self.btnUseSel = QtWidgets.QPushButton(u"使用当前选择")
        self.btnUseSel.clicked.connect(self._on_use_selection)
        self.btnSelfTest = QtWidgets.QPushButton(u"自检")
        self.btnSelfTest.setToolTip(u"在当前 rig 上做一次端到端验证（会临时动骨骼，随后自动还原）")
        self.btnSelfTest.clicked.connect(self._on_selftest)
        row.addWidget(self.lblSource, 1)
        row.addWidget(self.btnSelfTest)
        row.addWidget(self.btnUseSel)
        l1.addLayout(row)
        root.addWidget(g1)

        # ---- 2. 提取 / 雕刻
        g2 = QtWidgets.QGroupBox(u"② 提取雕刻网格 → 雕刻 → 重置")
        l2 = QtWidgets.QVBoxLayout(g2)
        r2 = QtWidgets.QHBoxLayout()
        self.btnExtract = QtWidgets.QPushButton(u"提取雕刻网格")
        self.btnExtract.setObjectName("step")
        self.btnExtract.clicked.connect(self._on_extract)
        self.btnResetSculpt = QtWidgets.QPushButton(u"重置雕刻")
        self.btnResetSculpt.clicked.connect(self._on_reset_sculpt)
        self.btnToggle = QtWidgets.QPushButton(u"切换显示")
        self.btnToggle.clicked.connect(self._on_toggle_view)
        r2.addWidget(self.btnExtract, 2)
        r2.addWidget(self.btnResetSculpt, 1)
        r2.addWidget(self.btnToggle, 1)
        l2.addLayout(r2)
        tip = QtWidgets.QLabel(u"提取后会生成 <I>*_SSDR_Sculpt</I> 静态网格（原网格自动隐藏）。"
                               u"请用「雕刻几何体 / 软选择」修改它，<b>不要改变拓扑</b>。")
        tip.setWordWrap(True)
        l2.addWidget(tip)
        root.addWidget(g2)

        # ---- 3. 骨骼
        g3 = QtWidgets.QGroupBox(u"③ 参与计算的骨骼")
        l3 = QtWidgets.QVBoxLayout(g3)
        r3 = QtWidgets.QHBoxLayout()
        self.editFilter = QtWidgets.QLineEdit()
        self.editFilter.setPlaceholderText(u"过滤骨骼名称，例如 lip / brow / jaw")
        self.editFilter.textChanged.connect(lambda *_: self._rebuild_bone_list())
        r3.addWidget(self.editFilter, 1)
        l3.addLayout(r3)

        self.boneList = QtWidgets.QListWidget()
        self.boneList.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.boneList.setMinimumHeight(140)
        self.boneList.itemChanged.connect(self._on_item_changed)
        l3.addWidget(self.boneList, 1)

        r4 = QtWidgets.QHBoxLayout()
        for text, slot in ((u"全选", self._check_all), (u"全不选", self._check_none),
                           (u"反选", self._invert_check), (u"视口选中", self._check_from_viewport)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(slot)
            r4.addWidget(b)
        l3.addLayout(r4)

        r4b = QtWidgets.QHBoxLayout()
        self.btnScope = QtWidgets.QPushButton(u"按雕刻区自动勾选")
        self.btnScope.setToolTip(
            u"按「这次雕刻实际依赖谁」重新勾选：影响权重（骨骼在被雕刻顶点上的\n"
            u"权重 × 位移）不到最强骨骼 1% 的，直接不勾。\n"
            u"这一步不是必须的——点『计算骨骼偏移』时也会自动收窄——\n"
            u"但它能让你先看清楚到底哪几根骨骼会被用到。")
        self.btnScope.clicked.connect(self._check_influenced)
        r4b.addWidget(self.btnScope, 2)
        btn = QtWidgets.QPushButton(u"按贡献度推荐")
        btn.setToolTip(u"旧方式：按「单根骨骼单独求解后整体误差最小」排序取前 24 根。\n"
                       u"它偏向覆盖大区域的主骨，容易漏掉真正贴着雕刻区的小骨，\n"
                       u"所以现在默认用左边的『按雕刻区自动勾选』。")
        btn.clicked.connect(self._auto_recommend)
        r4b.addWidget(btn, 1)
        l3.addLayout(r4b)
        self.lblCount = QtWidgets.QLabel(u"已选 0 根")
        l3.addWidget(self.lblCount)
        root.addWidget(g3, 1)

        # ---- 4. 反求参数
        g4 = QtWidgets.QGroupBox(u"④ SSDR 反求")
        l4 = QtWidgets.QVBoxLayout(g4)
        r5 = QtWidgets.QHBoxLayout()
        r5.addWidget(QtWidgets.QLabel(u"迭代次数"))
        self.spinIter = QtWidgets.QSpinBox()
        self.spinIter.setRange(1, 400)
        self.spinIter.setValue(40)
        r5.addWidget(self.spinIter)
        r5.addSpacing(10)
        r5.addWidget(QtWidgets.QLabel(u"阻尼"))
        self.spinDamp = QtWidgets.QDoubleSpinBox()
        self.spinDamp.setRange(0.1, 1.0)
        self.spinDamp.setSingleStep(0.1)
        self.spinDamp.setValue(1.0)
        self.spinDamp.setToolTip(u"1.0 = 直接解（推荐）；调小可让每步更保守")
        r5.addWidget(self.spinDamp)
        r5.addStretch(1)
        l4.addLayout(r5)

        r6 = QtWidgets.QHBoxLayout()
        r6.addWidget(QtWidgets.QLabel(u"求解类型"))
        self.comboMode = QtWidgets.QComboBox()
        self.comboMode.addItems([u"刚性（旋转 + 平移，推荐）", u"仿射（额外允许缩放/剪切）"])
        r6.addWidget(self.comboMode, 1)
        l4.addLayout(r6)

        r6b = QtWidgets.QHBoxLayout()
        r6b.addWidget(QtWidgets.QLabel(u"骨骼最大距离"))
        self.spinDist = QtWidgets.QDoubleSpinBox()
        self.spinDist.setRange(0.0, 10000.0)
        self.spinDist.setDecimals(2)
        self.spinDist.setSingleStep(0.5)
        self.spinDist.setValue(0.0)
        self.spinDist.setSuffix(u"  单位")
        self.spinDist.setToolTip(
            u"离**被雕刻顶点**超过这个距离的骨骼不参与计算（按骨骼在绑定姿态的位置量）。\n"
            u"0 = 不限制，用勾选列表里的全部骨骼。\n"
            u"雕刻只改了局部时，调小它可以避免远处的骨骼跟着乱动——例如只改额头时\n"
            u"设成额头到鼻子之间的距离，下颌相关骨骼就不会被拉进来。")
        self.spinDist.setKeyboardTracking(False)
        r6b.addWidget(self.spinDist)
        r6b.addWidget(QtWidgets.QLabel(u"（0 = 不限制）"))
        r6b.addStretch(1)
        l4.addLayout(r6b)

        # ---- 只算雕刻区附近的骨骼 ------------------------------------------
        r6c = QtWidgets.QHBoxLayout()
        self.chkScope = QtWidgets.QCheckBox(u"只算雕刻区附近的骨骼")
        self.chkScope.setChecked(True)
        self.chkScope.setToolTip(
            u"自动判断「这次雕刻需要哪些骨骼」：\n"
            u"  影响权重 = Σ(该骨骼在**被雕刻顶点**上的权重 × 那些顶点的位移量)\n"
            u"它不到最大那根骨骼的这个比例时，就不参与计算。\n"
            u"骨骼离雕刻区越远、或者在那些顶点上的权重越小，这个量越接近 0，\n"
            u"所以只改嘴角时，额头/眼睛附近那些骨骼会被整片排掉——不需要你\n"
            u"手动一根根勾选。（勾选列表仍然有效，这里只是在此之上再收窄。）")
        r6c.addWidget(self.chkScope)
        r6c.addSpacing(8)
        r6c.addWidget(QtWidgets.QLabel(u"影响阈值"))
        self.spinShare = QtWidgets.QDoubleSpinBox()
        self.spinShare.setRange(0.0, 100.0)
        self.spinShare.setDecimals(2)
        self.spinShare.setSingleStep(0.5)
        self.spinShare.setValue(1.0)
        self.spinShare.setSuffix(u"  %")
        self.spinShare.setKeyboardTracking(False)
        self.spinShare.setToolTip(
            u"影响权重低于该比例的骨骼不参与计算（相对最强的那根骨骼，1% 已很宽松）。\n"
            u"雕刻范围大、多根骨骼都在出力时它几乎不会排除任何骨骼；\n"
            u"只改一小块时它会把不相干的骨骼全部挡在外面。\n"
            u"如果日志提示「把勾选的骨骼全部排除了」，把它调小即可。")
        r6c.addWidget(self.spinShare)
        r6c.addStretch(1)
        l4.addLayout(r6c)

        # ---- 重要度阈值（解完的二次过滤） ----------------------------------
        r6d = QtWidgets.QHBoxLayout()
        self.chkPrune = QtWidgets.QCheckBox(u"重要度过滤：几乎没动的骨骼就当没动")
        self.chkPrune.setChecked(True)
        self.chkPrune.setToolTip(
            u"解完之后，**旋转和位移同时**低于下面两个值的骨骼会被退回原姿态。\n"
            u"它们本来就只挪了千分之几度，留着只会让「我没碰的地方也动了」这种\n"
            u"观感变差；退回之后腾出来的那点残差会交给其它骨骼重解一次，\n"
            u"所以拟合不会变差（日志会打印冻结前后的 RMS）。\n"
            u"真实需要的运动（例如 2° 以上、或者 0.05 单位以上）永远不会被它砍掉。")
        r6d.addWidget(self.chkPrune)
        l4.addLayout(r6d)

        r6e = QtWidgets.QHBoxLayout()
        r6e.addSpacing(18)
        r6e.addWidget(QtWidgets.QLabel(u"位移 <"))
        self.spinPruneT = QtWidgets.QDoubleSpinBox()
        self.spinPruneT.setRange(0.0, 100.0)
        self.spinPruneT.setDecimals(3)
        self.spinPruneT.setSingleStep(0.01)
        self.spinPruneT.setValue(0.02)
        self.spinPruneT.setSuffix(u"  单位")
        self.spinPruneT.setKeyboardTracking(False)
        r6e.addWidget(self.spinPruneT)
        r6e.addWidget(QtWidgets.QLabel(u"且 旋转 <"))
        self.spinPruneR = QtWidgets.QDoubleSpinBox()
        self.spinPruneR.setRange(0.0, 90.0)
        self.spinPruneR.setDecimals(2)
        self.spinPruneR.setSingleStep(0.1)
        self.spinPruneR.setValue(0.20)
        self.spinPruneR.setSuffix(u"  °")
        self.spinPruneR.setKeyboardTracking(False)
        self.spinPruneR.setToolTip(u"两个条件同时满足才会被冻结（只看其中一个会误伤）")
        r6e.addWidget(self.spinPruneR)
        r6e.addStretch(1)
        l4.addLayout(r6e)

        self.chkStable = QtWidgets.QCheckBox(u"自动稳定：不可判定的方向按「不动」处理")
        self.chkStable.setChecked(True)
        self.chkStable.setToolTip(
            u"某些补皮骨（如 12IPV_ForeheadSkin*）的影响顶点近似一条线，绕这条线的\n"
            u"自旋在数据上完全无法判定，硬解会得到 180° 这种纯噪声的旋转。\n"
            u"打开后用一个极小的先验把这个方向唯一地定为「不动」，而数据能确定的\n"
            u"旋转/位移**照常精确求解**（影响只有万分之一量级），所以不会削弱拟合。\n"
            u"只有当你怀疑工具压住了本该发生的旋转时才需要关掉它。")
        l4.addWidget(self.chkStable)

        self.btnSolve = QtWidgets.QPushButton(u"计算骨骼偏移（SSDR 反求）")
        self.btnSolve.setObjectName("step")
        self.btnSolve.clicked.connect(self._on_solve)
        l4.addWidget(self.btnSolve)

        self.lblResult = QtWidgets.QLabel(u"—")
        self.lblResult.setWordWrap(True)
        self.lblResult.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        l4.addWidget(self.lblResult)
        root.addWidget(g4)

        # ---- 5. 镜像
        g5 = QtWidgets.QGroupBox(u"⑤ 镜像到另一侧（可选）")
        l5 = QtWidgets.QVBoxLayout(g5)

        r7 = QtWidgets.QHBoxLayout()
        self.comboMirrorDir = QtWidgets.QComboBox()
        self.comboMirrorDir.addItems([u"左 → 右   (L → R)", u"右 → 左   (R → L)"])
        self.comboMirrorDir.setToolTip(u"把哪一侧的反求结果写到另一侧")
        self.comboMirrorDir.currentIndexChanged.connect(lambda *_: self._update_mirror_hint())
        self.comboMirrorMode = QtWidgets.QComboBox()
        self.comboMirrorMode.addItems([u"覆盖绝对值", u"叠加偏移"])
        self.comboMirrorMode.setToolTip(
            u"覆盖绝对值：对侧骨骼最终 = 源侧的镜像结果，对侧原有偏移被丢弃。\n"
            u"叠加偏移：在对侧原有偏移之上，再叠加一份镜像过来的偏移。")
        r7.addWidget(QtWidgets.QLabel(u"方向"))
        r7.addWidget(self.comboMirrorDir, 1)
        r7.addWidget(QtWidgets.QLabel(u"方式"))
        r7.addWidget(self.comboMirrorMode, 1)
        l5.addLayout(r7)

        r8 = QtWidgets.QHBoxLayout()
        self.comboMirrorAxis = QtWidgets.QComboBox()
        self.comboMirrorAxis.addItems([u"镜像面：自动判断", u"镜像面：世界 X",
                                       u"镜像面：世界 Y", u"镜像面：世界 Z"])
        self.comboMirrorAxis.setToolTip(
            u"面部/身体的左右镜像面通常垂直于世界 X 轴。\n"
            u"默认从左右配对骨骼的绑定位置自动判断，残差会在日志里给出。")
        self.btnMirror = QtWidgets.QPushButton(u"镜像偏移")
        self.btnMirror.setObjectName("step")
        self.btnMirror.clicked.connect(self._on_mirror)
        self.btnMirror.setEnabled(False)
        r8.addWidget(self.comboMirrorAxis, 2)
        r8.addWidget(self.btnMirror, 2)
        l5.addLayout(r8)

        self.lblMirror = QtWidgets.QLabel(u"镜像面自动判断｜尚未识别骨骼对")
        self.lblMirror.setWordWrap(True)
        l5.addWidget(self.lblMirror)
        root.addWidget(g5)

        # ---- 6. 权重滑块
        g6 = QtWidgets.QGroupBox(u"⑥ 偏移权重 → 应用")
        l6 = QtWidgets.QVBoxLayout(g6)
        r9 = QtWidgets.QHBoxLayout()
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(0)
        self.slider.setEnabled(False)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(5)
        self.slider.setTracking(True)
        self.spinWeight = QtWidgets.QSpinBox()
        self.spinWeight.setRange(0, 100)
        self.spinWeight.setSuffix(u" %")
        self.spinWeight.setValue(0)
        self.spinWeight.setEnabled(False)
        self.spinWeight.setKeyboardTracking(False)      # 键入时不要每敲一个数字就触发
        # 拖动过程中**不执行**写骨骼（每次写骨骼都要重算整张网格，拖起来会卡且抖），
        # 松手才提交一次。键盘/滚轮/数字框这类没有"拖动结束"事件的操作，
        # 用一个短延时兜住。
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.slider.valueChanged.connect(self._on_slider)
        self.spinWeight.valueChanged.connect(self._on_spin_weight)
        self.spinWeight.editingFinished.connect(self._on_spin_edited)
        r9.addWidget(self.slider, 1)
        r9.addWidget(self.spinWeight)
        l6.addLayout(r9)

        self.lblWeight = QtWidgets.QLabel(u"当前 0%")
        l6.addWidget(self.lblWeight)

        self._dragging = False
        self._applied = None
        self._commit_timer = QtCore.QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.setInterval(180)
        self._commit_timer.timeout.connect(self._on_commit_timer)

        r10 = QtWidgets.QHBoxLayout()
        self.btnApply = QtWidgets.QPushButton(u"应用 100%")
        self.btnApply.setObjectName("step")
        self.btnApply.clicked.connect(self._on_apply_full)
        self.btnApply.setEnabled(False)
        self.btnRevert = QtWidgets.QPushButton(u"还原骨骼")
        self.btnRevert.clicked.connect(self._on_revert)
        self.btnFinish = QtWidgets.QPushButton(u"完成并清理")
        self.btnFinish.clicked.connect(self._on_finish)
        r10.addWidget(self.btnApply, 2)
        r10.addWidget(self.btnRevert, 1)
        r10.addWidget(self.btnFinish, 1)
        l6.addLayout(r10)

        self.chkKey = QtWidgets.QCheckBox(u"应用时在当前帧打关键帧")
        l6.addWidget(self.chkKey)
        root.addWidget(g6)

        # ---- 日志
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(100)
        root.addWidget(self.log)

        # 关掉某个勾选时把它下面的数值框一起置灰
        self.chkScope.toggled.connect(lambda *_: self._sync_scope_ui())
        self.chkPrune.toggled.connect(lambda *_: self._sync_scope_ui())
        self._sync_scope_ui()

    # -- 日志 ---------------------------------------------------------------
    def _append_log(self, msg):
        self.log.appendPlainText(msg)
        QtWidgets.QApplication.processEvents()

    def _error(self, exc):
        self._append_log(u"✗ %s" % exc)
        QtWidgets.QMessageBox.warning(self, u"出错了", u"%s" % exc)

    def _busy(self, on):
        if on:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        else:
            QtWidgets.QApplication.restoreOverrideCursor()
        QtWidgets.QApplication.processEvents()

    # -- 场景恢复 -----------------------------------------------------------
    def _restore_from_scene(self):
        state = self.ctl.state
        src = state.get("source_mesh")
        if src and cmds.objExists(src):
            try:
                rig = mb.SkinRig(src)
                self.ctl.rig = rig
                self.lblSource.setText(u"%s   （%d 顶点 / %d 骨骼）" %
                                       (rig.mesh, rig.num_vertices, len(rig.bones)))
                self._refresh_bones()
            except Exception:
                pass
        if not self._bone_rows:
            self.lblSource.setText(u"未选择（先选中蒙皮网格，再点右侧按钮）")

    # -- 动作：源网格 -------------------------------------------------------
    def _on_use_selection(self):
        self._busy(True)
        try:
            rig = self.ctl.set_source_from_selection()
            self.lblSource.setText(u"%s   （%d 顶点 / %d 骨骼）" %
                                   (rig.mesh, rig.num_vertices, len(rig.bones)))
            # 换了源网格，上一次的解算结果就不再有意义了
            self.ctl.solver = None
            self._applied = None
            for w in (self.slider, self.spinWeight, self.btnApply, self.btnMirror):
                w.setEnabled(False)
            self._set_weight_ui(0)
            self.lblResult.setText(u"—")
            self._refresh_bones()
        except Exception as exc:
            self._error(exc)
        finally:
            self._busy(False)

    def _on_selftest(self):
        self._busy(True)
        try:
            from . import selftest
            self._append_log(u"———— 端到端自检 ————")
            selftest.run(out=self._append_log)
        except Exception as exc:
            self._error(exc)
            traceback.print_exc()
        finally:
            self._busy(False)
    def _refresh_bones(self):
        try:
            self._bone_rows = self.ctl.bone_rows()
        except Exception:
            self._bone_rows = []
        defaults = set(self.ctl.default_checked_bones())
        for name, _count in self._bone_rows:
            self._check_state.setdefault(name, name in defaults)
        self._rebuild_bone_list()
        self._update_mirror_hint()

    def _rebuild_bone_list(self):
        text = self.editFilter.text().strip().lower()
        self.boneList.blockSignals(True)
        self.boneList.clear()
        for name, count in self._bone_rows:
            if text and text not in name.lower():
                continue
            item = QtWidgets.QListWidgetItem(u"%s    (%d 顶点)" % (name, count))
            item.setData(QtCore.Qt.UserRole, name)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if self._check_state.get(name, False)
                               else QtCore.Qt.Unchecked)
            self.boneList.addItem(item)
        self.boneList.blockSignals(False)
        self._update_count()

    def _on_item_changed(self, item):
        name = item.data(QtCore.Qt.UserRole)
        self._check_state[name] = (item.checkState() == QtCore.Qt.Checked)
        self._update_count()

    def _update_count(self):
        total = len(self._bone_rows)
        picked = len(self.selected_bones())
        self.lblCount.setText(u"已勾选 %d / %d 根骨骼" % (picked, total))

    def selected_bones(self):
        return [name for name, _ in self._bone_rows if self._check_state.get(name, False)]

    def _set_all(self, value):
        for name, _ in self._bone_rows:
            self._check_state[name] = value
        self._rebuild_bone_list()

    def _check_all(self):
        self._set_all(True)

    def _check_none(self):
        self._set_all(False)

    def _invert_check(self):
        for name, _ in self._bone_rows:
            self._check_state[name] = not self._check_state.get(name, False)
        self._rebuild_bone_list()

    def _check_from_viewport(self):
        sel = cmds.ls(selection=True, long=False) or []
        bones = set()
        for node in sel:
            for part in node.split("|"):
                bones.add(part)
        hit = 0
        for name, _ in self._bone_rows:
            if name in bones or name.split("|")[-1] in bones:
                self._check_state[name] = True
                hit += 1
        self._rebuild_bone_list()
        self._append_log(u"从视口选择中勾选了 %d 根骨骼" % hit)

    def _check_influenced(self):
        self._busy(True)
        try:
            names = self.ctl.influenced_bones(self.spinShare.value() / 100.0)
            picked = set(names)
            for name, _ in self._bone_rows:
                self._check_state[name] = name in picked
            self._rebuild_bone_list()
            self._append_log(u"已按雕刻区勾选 %d 根骨骼（不点这个按钮也没关系，"
                             u"计算时会自动按同一规则收窄）" % len(picked))
        except Exception as exc:
            self._error(exc)
        finally:
            self._busy(False)

    def _auto_recommend(self):
        self._busy(True)
        try:
            names = self.ctl.recommend(24)
            for name, _ in self._bone_rows:
                self._check_state[name] = name in names
            self._rebuild_bone_list()
        except Exception as exc:
            self._error(exc)
        finally:
            self._busy(False)

    # -- 动作：提取 / 雕刻 --------------------------------------------------
    def _on_extract(self):
        self._busy(True)
        try:
            self.ctl.extract()
        except Exception as exc:
            self._error(exc)
        finally:
            self._busy(False)

    def _on_reset_sculpt(self):
        self._busy(True)
        try:
            self.ctl.reset_sculpt()
        except Exception as exc:
            self._error(exc)
        finally:
            self._busy(False)

    def _on_toggle_view(self):
        try:
            self.ctl.toggle_view()
        except Exception as exc:
            self._error(exc)

    # -- 动作：求解 ---------------------------------------------------------
    def _sync_scope_ui(self):
        """勾选框关掉时，把它的数值框一起置灰，避免误解。"""
        scope = self.chkScope.isChecked()
        self.spinShare.setEnabled(scope)
        prune = self.chkPrune.isChecked()
        self.spinPruneT.setEnabled(prune)
        self.spinPruneR.setEnabled(prune)

    def _on_solve(self):
        bones = self.selected_bones()
        if not bones:
            self._error(u"请至少勾选一根参与计算的骨骼")
            return
        self._busy(True)
        try:
            rep = self.ctl.solve(bones,
                                 iterations=self.spinIter.value(),
                                 damping=self.spinDamp.value(),
                                 affine=(self.comboMode.currentIndex() == 1),
                                 stabilize=self.chkStable.isChecked(),
                                 max_dist=self.spinDist.value(),
                                 scope=self.chkScope.isChecked(),
                                 min_share=self.spinShare.value() / 100.0,
                                 prune=self.chkPrune.isChecked(),
                                 prune_translate=self.spinPruneT.value(),
                                 prune_rotate=self.spinPruneR.value())
            diag = mb.mesh_diagonal(self.ctl.rig.rest_points)
            self.lblResult.setText(
                u"RMS  %.5f → <b>%.5f</b>   （占网格对角线 %.3f%%）<br>"
                u"最大残差 %.5f ｜ 迭代 %d 次 ｜ 参与 %d 根 ｜ 冻结 %d 根" %
                (rep["rms_before"], rep["rms"], rep["rms"] / max(diag, 1e-9) * 100.0,
                 rep["max"], rep["iterations"], int(rep.get("active_count", len(bones))),
                 len(rep.get("frozen") or [])))
            self.slider.setEnabled(True)
            self.spinWeight.setEnabled(True)
            self.btnApply.setEnabled(True)
            self.btnMirror.setEnabled(True)
            self._view_switched = False
            self._applied = None                 # 场景里的骨骼状态已变，别相信旧值
            self._set_weight_ui(0)
            self._update_mirror_hint()
        except Exception as exc:
            self._error(exc)
            traceback.print_exc()
        finally:
            self._busy(False)

    # -- 动作：镜像 ---------------------------------------------------------
    def _update_mirror_hint(self):
        direction = "L2R" if self.comboMirrorDir.currentIndex() == 0 else "R2L"
        try:
            pairs = self.ctl.mirror_pairs(direction)
        except Exception:
            pairs = []
        if not pairs:
            self.lblMirror.setText(u"⚠ 没有识别到左右配对的骨骼，请检查骨骼命名")
            return
        self.lblMirror.setText(u"已识别 <b>%d</b> 对左右骨骼，镜像面按配对位置自动判断"
                               % len(pairs))

    def _on_mirror(self):
        if self.ctl.solver is None:
            self._error(u"请先点击『计算骨骼偏移』")
            return
        direction = "L2R" if self.comboMirrorDir.currentIndex() == 0 else "R2L"
        mode = "overwrite" if self.comboMirrorMode.currentIndex() == 0 else "additive"
        axis = self.comboMirrorAxis.currentIndex()
        axis = None if axis == 0 else axis - 1
        self._commit_timer.stop()
        self._busy(True)
        ok = False
        try:
            rep = self.ctl.mirror(direction=direction, mode=mode, axis=axis)
            for name in rep.get("activated") or []:
                if name in self._check_state:
                    self._check_state[name] = True        # 让"重新计算"也包含它们
            self._rebuild_bone_list()
            self._update_mirror_hint()
            ok = True
        except Exception as exc:
            self._error(exc)
            traceback.print_exc()
        finally:
            self._busy(False)
        if not ok:
            return
        # 立刻把结果画出来，否则用户看不到任何变化
        if self.slider.value() == 0:
            self._set_weight_ui(100)
            self._append_log(u"已把权重设为 100% 以便查看镜像结果")
        self._commit_weight(force=True)

    # -- 动作：权重 ---------------------------------------------------------
    def _set_weight_ui(self, value):
        for w in (self.slider, self.spinWeight):
            w.blockSignals(True)
            w.setValue(int(value))
            w.blockSignals(False)
        self.lblWeight.setText(u"当前 %d%%" % int(value))

    def _on_slider_pressed(self):
        self._dragging = True
        self._commit_timer.stop()

    def _on_slider_released(self):
        # 拖动结束：只有这一次真正写骨骼
        self._dragging = False
        self._commit_timer.stop()
        self._commit_weight()

    def _on_slider(self, value):
        self.spinWeight.blockSignals(True)
        self.spinWeight.setValue(value)
        self.spinWeight.blockSignals(False)
        self.lblWeight.setText(u"当前 %d%%%s" % (value, u"（松开后应用）" if self._dragging else ""))
        if not self._dragging:                     # 键盘 / 滚轮 / 点击轨道
            self._commit_timer.start()

    def _on_spin_weight(self, value):
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        self.lblWeight.setText(u"当前 %d%%（停止输入后应用）" % value)
        if not self._dragging:
            self._commit_timer.start()

    def _on_spin_edited(self):
        self._commit_timer.stop()
        self._commit_weight()

    def _on_commit_timer(self):
        if self._dragging:                         # 还按着鼠标，别动
            return
        self._commit_weight()

    def _commit_weight(self, force=False):
        if self.ctl.solver is None:
            return
        t = self.slider.value() / 100.0
        if (not force and self._applied is not None
                and abs(t - self._applied) < 1e-9):
            self.lblWeight.setText(u"当前 %d%%（已应用）" % int(round(t * 100)))
            return
        self._apply_weight(t)

    def _apply_weight(self, t):
        if self.ctl.solver is None:
            return
        self._busy(True)
        try:
            self.ctl.apply_weight(t, key=self.chkKey.isChecked())
            self._applied = t
            if t > 0.0 and not self._view_switched:
                self.ctl.show_source_only()
                self._view_switched = True
            self.lblWeight.setText(u"当前 %d%%（已应用）" % int(round(t * 100)))
            self._append_log(u"权重 %d%% 已应用" % int(round(t * 100)))
        except Exception as exc:
            self._error(exc)
        finally:
            self._busy(False)

    def _on_apply_full(self):
        self._commit_timer.stop()
        self._busy(True)
        try:
            self._set_weight_ui(100)
            self.ctl.apply_weight(1.0, key=self.chkKey.isChecked())
            self._applied = 1.0
            self.ctl.show_source_only()
            self.lblWeight.setText(u"当前 100%（已应用）")
            self._append_log(u"已按 100% 权重烘焙到骨骼（可用『还原骨骼』撤销）")
        except Exception as exc:
            self._error(exc)
        finally:
            self._busy(False)

    def _on_revert(self):
        self._commit_timer.stop()
        self._busy(True)
        try:
            self.ctl.revert()
            self._set_weight_ui(0)
            self._applied = 0.0
        except Exception as exc:
            self._error(exc)
        finally:
            self._busy(False)

    def _on_finish(self):
        ret = QtWidgets.QMessageBox.question(
            self, u"完成",
            u"确定完成吗？\n\n将隐藏雕刻网格、删除临时副本（骨骼上的烘焙结果会保留）。",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if ret != QtWidgets.QMessageBox.Yes:
            return
        self._commit_timer.stop()
        self._busy(True)
        try:
            self.ctl.finish(delete_temp=True)
            self.slider.setEnabled(False)
            self.spinWeight.setEnabled(False)
            self.btnApply.setEnabled(False)
            self.btnMirror.setEnabled(False)
            self._applied = None
            self.lblResult.setText(u"—")
            self.lblWeight.setText(u"当前 —")
        except Exception as exc:
            self._error(exc)
        finally:
            self._busy(False)

    # -- 关闭 ---------------------------------------------------------------
    def closeEvent(self, event):
        global _window
        try:
            self._commit_timer.stop()
        except Exception:
            pass
        _window = None
        super(SSDRWindow, self).closeEvent(event)


# ============================================================================
def _maya_main_window():
    ptr = omui.MQtUtil.mainWindow()
    if ptr is None:
        return None
    return wrapInstance(int(ptr), QtWidgets.QWidget)


def show():
    global _window
    if _window is not None:
        try:
            _window.close()
            _window.deleteLater()
        except Exception:
            pass
        _window = None
    _window = SSDRWindow(_maya_main_window())
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window
