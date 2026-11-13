# -*- coding: utf-8 -*-
"""P2.4：mayapy headless 全量回归。

    "/c/Program Files/Autodesk/Maya2023/bin/mayapy.exe" validation/maya_full_regression.py
    python validation/maya_full_regression.py            # 无 Maya 时离线降级

做四件事：
1. 40 个 golden fixtures（20 好 / 20 坏）跑内置规则，统计漏报 / 误报；
2. 同一批 fixtures 跑完 5 个工具插件（Maya 三个 + UE 两个），确认零回归；
3. 验证受限 DSL 的 dry-run 无副作用、缺权限被拒、失败整体回滚；
4. 若 mayapy 可用，初始化 standalone、建骨架与 skinCluster，抓一次
   **真机** MayaAdapter 快照并跑规则，作为 adapter 层的端到端证据。

结果写到 validation/maya_full_regression_result.json。
"""
from __future__ import annotations

import json
import os
import platform
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

OUT = os.environ.get("TAK_REGRESSION_OUT",
                     os.path.join(_HERE, "maya_full_regression_result.json"))


def _fixtures():
    items = []
    for kind, expected in (("good", True), ("bad", False)):
        folder = os.path.join(_ROOT, "fixtures", kind)
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                data = json.load(fh)
            items.append((name, data, expected))
    return items


def _fixture_rules():
    """返回 (漏报数, 误报数, 逐项明细)。"""
    from tak.adapters.base import NullAdapter
    from tak.core.runner import evaluate

    false_negative = 0
    false_positive = 0
    details = []
    for name, fixture, expected in _fixtures():
        report = evaluate(fixture["snapshot"], dcc="maya", adapter=NullAdapter())
        actual = report.passed
        if expected and not actual:
            false_positive += 1
        elif not expected and actual:
            false_negative += 1
        details.append({
            "id": fixture.get("id", name), "expected_pass": expected,
            "actual_pass": actual,
            "errors": report.counts().get("error", 0),
            "violations": fixture.get("expected_violations", []),
        })
    return false_negative, false_positive, details


def _plugin_matrix():
    """五个插件在 40 份 fixtures 上跑通，统计异常与阻断级 finding 数。"""
    from tak.adapters.base import NullAdapter
    from tak.core.runner import run_tool

    tools = [
        ("rig_health_guard", "maya", {"include_builtin": True}),
        ("spec_enforcer", "maya", {}),
        ("export_bridge", "maya", {}),
        ("ue_import_guard", "unreal", {}),
        ("ik_retarget_guard", "unreal", {}),
    ]
    summary = {}
    for tool_id, dcc, options in tools:
        runs, errors, blocking = 0, [], 0
        for name, fixture, _expected in _fixtures():
            try:
                result = run_tool(tool_id, fixture["snapshot"], dcc=dcc,
                                  adapter=NullAdapter(), options=dict(options))
                runs += 1
                blocking += result.report.counts().get("error", 0)
            except Exception as exc:
                errors.append("%s: %s: %s" % (name, type(exc).__name__, exc))
        summary[tool_id] = {"runs": runs, "errors": errors,
                            "error_findings_total": blocking,
                            "ok": not errors and runs == 40}
    return summary


def _dsl_regression():
    """受限 DSL：dry-run 零副作用、缺权限被拒、失败整体回滚。"""
    from tak.adapters.base import Capability, DCCAdapter
    from tak.core.dsl import Action, ActionRegistry, ExecutionPlan

    class SpyAdapter(DCCAdapter):
        name = "spy"

        def __init__(self, fail=False):
            self.events = []
            self.fail = fail

        def supports(self, capability):
            return capability in (Capability.NODE_WRITE, Capability.UNDO_SCOPE)

        def list_nodes(self, node_type="joint"):
            return []

        def get_node_attrs(self, node):
            return {}

        def get_skin_summary(self, mesh):
            return {}

        def get_skeleton_snapshot(self, root=None):
            return {}

        def begin_undo(self, label):
            self.events.append("begin")
            return "token"

        def commit_undo(self, token):
            self.events.append("commit")

        def rollback(self, token):
            self.events.append("rollback")

    def ok_action(adapter, value):
        adapter.events.append("do:%s" % value)

    def bad_action(adapter, value):
        adapter.events.append("do:%s" % value)
        raise RuntimeError("模拟失败")

    registry = ActionRegistry()
    registry.register("reg.ok", ok_action, permissions=["node.write"],
                      parameters={"value": {"type": str, "required": True}})
    registry.register("reg.bad", bad_action, permissions=["node.write"],
                      parameters={"value": {"type": str, "required": True}})

    dry_adapter = SpyAdapter()
    plan = ExecutionPlan([Action("reg.ok", {"value": "a"})])
    dry = registry.execute(plan, dry_adapter, dry_run=True)
    assert dry["dry_run"] is True and not dry["committed"]
    assert dry_adapter.events == [], "dry-run 产生了副作用"

    try:
        registry.execute(plan, SpyAdapter(), dry_run=False, confirm=True)
        raise AssertionError("缺权限应被拒")
    except Exception as exc:
        assert "权限" in str(exc)

    rolled = SpyAdapter()
    failed = registry.execute(ExecutionPlan([Action("reg.bad", {"value": "b"})]),
                              rolled, granted=["node.write"],
                              dry_run=False, confirm=True)
    assert not failed["committed"] and "rollback" in rolled.events
    assert "commit" not in rolled.events

    committed = SpyAdapter()
    done = registry.execute(plan, committed, granted=["node.write"],
                            dry_run=False, confirm=True)
    assert done["committed"] and committed.events == ["begin", "do:a", "commit"]
    return {"dry_run_no_side_effects": True, "permission_enforced": True,
            "rollback_on_failure": True, "commit_on_success": True}


def _maya_live():
    """mayapy 真机：standalone → 骨架 + skinCluster → MayaAdapter 快照 → 规则。"""
    result = {"available": False, "reason": ""}
    try:
        import maya.standalone
        maya.standalone.initialize(name="python")
        import maya.cmds as cmds
    except Exception as exc:
        result["reason"] = "Maya standalone 不可用: %s: %s" % (type(exc).__name__, exc)
        return result

    result["available"] = True
    try:
        cmds.file(new=True, force=True)
        cmds.select(clear=True)
        names = ["root", "pelvis", "spine_01", "head"]
        # 连续 cmds.joint 会自动把新关节挂到上一根关节下，形成链；
        # 不要再显式 parent，否则在部分版本会重复挂载报错。
        joints = []
        for index, name in enumerate(names):
            joints.append(cmds.joint(name=name, position=(0, index * 10.0, 0)))
        cube = cmds.polyCube(name="probe_mesh")[0]
        # 显式传骨骼与 mesh，确保 skinCluster 真的分配了权重（供采样验证）。
        cmds.skinCluster(joints, cube, name="probe_skin",
                         toSelectedBones=True, normalizeWeights=1)

        from tak.adapters.maya import create_adapter
        from tak.core.runner import evaluate

        adapter = create_adapter()
        snapshot = adapter.get_skeleton_snapshot()
        # 采样必须在任何 undo/rollback 之前做：Maya 的 cmds.undo() 会撤销
        # 最近的操作，若先回滚会把刚建的 skinCluster 一起撤掉。
        skin = adapter.get_skin_summary(cube)
        report = evaluate(snapshot, dcc="maya", adapter=adapter)

        # 在 undo chunk 内造一个可撤销节点，验证 rollback 真的生效。
        token = adapter.begin_undo("regression")
        cmds.createNode("transform", name="tak_undo_probe")
        adapter.rollback(token)
        undo_ok = not bool(cmds.objExists("tak_undo_probe"))
        result.update({
            "maya_version": str(cmds.about(version=True)),
            "joint_count": len(snapshot.get("joints", {})),
            "unit": snapshot.get("unit"), "up_axis": snapshot.get("up_axis"),
            "skin": skin,
            "rule_count": report.metadata.get("rules_available"),
            "report_passed": report.passed,
            "undo_rollback": "ok" if undo_ok else "FAILED",
        })
    except Exception as exc:
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        result["traceback"] = traceback.format_exc()[-1200:]
    return result


def main() -> int:
    payload = {
        "ok": False,
        "probe": "maya_full_regression",
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "repo_root": _ROOT,
        "fixture_count": len(_fixtures()),
        "false_positives": None,
        "false_negatives": None,
        "errors": [],
    }
    try:
        fn, fp, details = _fixture_rules()
        payload["false_negatives"] = fn
        payload["false_positives"] = fp
        payload["fixture_details"] = details
        payload["plugins"] = _plugin_matrix()
        payload["dsl"] = _dsl_regression()
        payload["maya_live"] = _maya_live()
        payload["ok"] = (fn == 0 and fp == 0
                         and not payload["errors"]
                         and all(item["ok"] for item in payload["plugins"].values()))
    except Exception as exc:
        payload["errors"].append("%s: %s" % (type(exc).__name__, exc))
        payload["traceback"] = traceback.format_exc()[-2000:]

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print("TAK_MAYA_REGRESSION ok=%s fn=%s fp=%s fixtures=%s plugins=%s out=%s"
          % (payload["ok"], payload["false_negatives"],
             payload["false_positives"], payload["fixture_count"],
             len(payload.get("plugins", {})), OUT))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
