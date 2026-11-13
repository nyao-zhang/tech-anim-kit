# -*- coding: utf-8 -*-
"""在真 Maya 里验证适配器的 metadata.health 采集 + 场景卫生 9 条规则。

只做三件事：

1. 搭一个**故意有脏东西**的场景：一个空的孤立 transform、一个负缩放的镜像组、
   一条未上色的控制器曲线、一个完好的引用；
2. 调 ``MayaAdapter.get_skeleton_snapshot()`` 看 health 里到底采到了什么；
3. 用 ``runner.run_tool('qa_inspector', ...)`` 跑场景卫生那一组，看规则是否
   真的出了结论（而不是静默返回 0 条）。

结论写进 ``validation/maya_health_probe_result.json``：mayabatch 没有 stdout
回显到控制台的习惯，文件是唯一可靠的证据通道。

用法::

    mayabatch -command "exec(io.open(r'.../maya_health_probe.py', encoding='utf-8').read())"
"""
from __future__ import annotations

import io
import json
import os
import sys
import traceback

_RESULT: dict = {"ok": False, "steps": [], "error": ""}


def _repo_root() -> str:
    try:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    except NameError:  # exec(open(...).read()) 下没有 __file__
        return os.environ.get("TAK_REPO_ROOT") or os.getcwd()


def _step(name: str, **payload) -> None:
    entry = {"step": name}
    entry.update(payload)
    _RESULT["steps"].append(entry)


def _build_dirty_scene(cmds) -> dict:
    """造一个干净的骨架 + 几类脏东西，返回各自的节点名。

    另外生成一个临时 .ma 并 reference 进来，用来验证「完好的引用不报、
    文件丢了才报」这条分支。
    """
    repo = _repo_root() or os.getcwd()
    temp_dir = os.path.join(repo, "validation", "_tmp_health")
    if not os.path.isdir(temp_dir):
        os.makedirs(temp_dir)
    ref_src = os.path.join(temp_dir, "prop_src.ma")

    # 先做引用源文件
    cmds.file(new=True, force=True)
    cmds.polySphere(name="prop_geo")
    cmds.file(rename=ref_src)
    cmds.file(save=True, type="mayaAscii", force=True)

    cmds.file(new=True, force=True)
    made = {}

    # 干净骨架：root -> hips -> spine_01
    root = cmds.createNode("joint", name="root")
    hips = cmds.createNode("joint", name="hips")
    spine = cmds.createNode("joint", name="spine_01")
    cmds.parent(hips, root)
    cmds.parent(spine, hips)
    made["joints"] = [root, hips, spine]

    # 1) 孤立节点：无父级、无子级、无连接、变换仍是默认值
    made["orphan"] = cmds.createNode("transform", name="junk_orphan_grp")

    # 2) 负缩放的镜像组（非关节）—— 不该被算成孤立节点
    mirror = cmds.createNode("transform", name="mirror_grp")
    cmds.setAttr(mirror + ".scaleX", -1.0)
    made["mirror"] = mirror

    # 3) 未上色的控制器（按命名约定带 ctrl）
    curve = cmds.circle(name="l_arm_ctrl", normal=(0, 1, 0), radius=1)[0]
    made["control"] = curve

    # 一个已上色的控制器，用来确认「上色了就不会被报」
    colored = cmds.circle(name="r_arm_ctrl", normal=(0, 1, 0), radius=1)[0]
    cmds.setAttr(colored + ".overrideEnabled", True)
    cmds.setAttr(colored + ".overrideColor", 13)
    made["colored_control"] = colored

    # 一个普通网格，确认它不会被当成孤立节点
    made["mesh"] = cmds.polyCube(name="body_mesh")[0]

    # 完好的引用
    cmds.file(ref_src, reference=True, namespace="prop")
    made["ref_src"] = ref_src
    return made


def _probe_health(cmds, made: dict) -> None:
    from tak.adapters.maya.adapter import MayaAdapter

    adapter = MayaAdapter()
    snapshot = adapter.get_skeleton_snapshot()
    metadata = snapshot.get("metadata") or {}
    health = metadata.get("health")
    keys = sorted(str(k) for k in health) if isinstance(health, dict) else []
    _step("health_collected",
          present=isinstance(health, dict),
          keys=keys,
          orphan_nodes=(health or {}).get("orphan_nodes"),
          negative_scale=(health or {}).get("negative_scale"),
          control_colors=(health or {}).get("control_colors"),
          duplicate_shapes=(health or {}).get("duplicate_shapes"),
          references=(health or {}).get("references"),
          coverage=(health or {}).get("_meta"),
          health_error=metadata.get("health_error"))
    # 断言：孤立节点必须恰好抓到 junk_orphan_grp，且不能把 body_mesh / 关节 /
    # 负缩放组带上（后者由负缩放规则单独负责，不能重复报）。
    orphans = (health or {}).get("orphan_nodes") or []
    _step("orphan_assertion",
          caught_orphan=made["orphan"] in orphans,
          missed_mesh=made["mesh"] not in orphans,
          missed_joints=not any(name in orphans for name in made["joints"]),
          missed_mirror=made["mirror"] not in orphans,
          total=len(orphans))
    colors = (health or {}).get("control_colors") or {}
    _step("color_assertion",
          uncolored_caught=colors.get(made["control"]) is None,
          colored_has_index=colors.get(made["colored_control"]) == 13,
          entries=sorted(colors))

    # 引用分支：完好的引用不该报；文件被移走后必须报。
    healthy_refs = (health or {}).get("references") or []
    _step("reference_healthy", entries=healthy_refs,
          silent=not healthy_refs)
    backup = made["ref_src"] + ".bak"
    try:
        os.replace(made["ref_src"], backup)
        broken = MayaAdapter().get_skeleton_snapshot()
        broken_refs = ((broken.get("metadata") or {}).get("health") or {}) \
            .get("references") or []
        _step("reference_missing",
              caught=any(item.get("missing") for item in broken_refs),
              entries=broken_refs)
    finally:
        if os.path.isfile(backup):
            os.replace(backup, made["ref_src"])


def _probe_rules() -> None:
    from tak.core.runner import run_tool

    scene_checks = [
        "rig_health_guard.orphan_nodes",
        "rig_health_guard.negative_scale",
        "rig_health_guard.unfrozen_transform",
        "rig_health_guard.namespace_pollution",
        "rig_health_guard.duplicate_shape",
        "rig_health_guard.skin_influence_over_limit",
        "rig_health_guard.skin_weight_sum",
        "rig_health_guard.legacy_reference",
        "rig_health_guard.control_uncolored",
    ]
    result = run_tool("qa_inspector", dcc="maya",
                      options={"checks": scene_checks})
    report = result.report
    findings = []
    for item in report.findings:
        severity = getattr(item.severity, "value", str(item.severity))
        findings.append({"rule_id": item.rule_id, "severity": severity,
                         "node": getattr(item, "node", "")})
    rule_ids = sorted({item["rule_id"] for item in findings})
    _step("rules_ran",
          passed=bool(getattr(report, "passed", None)),
          finding_count=len(findings),
          rule_ids=rule_ids,
          health_gaps=(report.metadata or {}).get("health_gaps"),
          pseudo_pass=not any(item["rule_id"].endswith(".health.partial")
                              for item in findings))
    _step("rules_detail", findings=findings[:40])


def _probe_partial_gap() -> None:
    """没有 health 的快照必须被点名，而不是显示通过。"""
    from tak.core.runner import run_tool

    snapshot = {"dcc": "maya", "version": "2026", "joints": {},
                "frame_range": [1.0, 120.0], "skin": {}, "metadata": {}}
    result = run_tool("qa_inspector", dcc="maya", snapshot=snapshot,
                      options={"checks": ["rig_health_guard.orphan_nodes",
                                          "rig_health_guard.control_uncolored"]})
    ids = [item.rule_id for item in result.report.findings]
    _step("partial_gap",
          has_partial_notice="qa_inspector.health.partial" in ids,
          rule_ids=ids)


def main() -> None:
    try:
        import maya.cmds as cmds  # noqa: F401
    except Exception as exc:
        _RESULT["error"] = "not in maya: %s" % exc
        _write()
        return

    root = _repo_root()
    if root and root not in sys.path:
        sys.path.insert(0, root)
    _step("bootstrap", repo_root=root)

    try:
        made = _build_dirty_scene(cmds)
        _step("scene_built", nodes=made)
        _probe_health(cmds, made)
        _probe_rules()
        _probe_partial_gap()
        _RESULT["ok"] = all(
            entry.get("caught_orphan", True) and entry.get("missed_mesh", True)
            and entry.get("missed_joints", True)
            and entry.get("missed_mirror", True)
            and entry.get("has_partial_notice", True)
            and entry.get("silent", True) and entry.get("caught", True)
            for entry in _RESULT["steps"])
    except Exception:
        _RESULT["error"] = traceback.format_exc()
    _write()


def _write() -> None:
    target = os.path.join(_repo_root() or ".", "validation",
                          "maya_health_probe_result.json")
    with io.open(target, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(_RESULT, ensure_ascii=False, indent=2))
    try:
        import maya.cmds as cmds
        cmds.quit(force=True)
    except Exception:
        pass


main()
