# -*- coding: utf-8 -*-
"""Maya 2023+ 适配器真机探针。

用法::

    "/c/Program Files/Autodesk/Maya2023/bin/mayapy.exe" validation/maya_adapter_probe.py
    python validation/maya_adapter_probe.py        # 无 Maya 时输出 degraded

行为
----
1. 尝试 ``maya.standalone.initialize()``（license 不可用时降级为只读检查）；
2. 建 4 根 joint + 一个 polyCube + skinCluster；
3. 调用 ``MayaAdapter.get_skeleton_snapshot`` / ``get_skin_summary``；
4. 调用 ``tak.core.runner.evaluate`` 跑全部内置规则；
5. 把结构化 JSON 打印到 stdout，绝不因宿主异常而崩溃。
"""
from __future__ import annotations

import json
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

JOINT_CHAIN = ("root", "pelvis", "spine_01", "head")


def _blank_report() -> dict:
    return {
        "script": "maya_adapter_probe",
        "status": "degraded",
        "reason": "",
        "python": sys.version.split()[0],
        "maya": {
            "importable": False,
            "version": "",
            "standalone": "not_attempted",
            "license": "unknown",
            "error": "",
        },
        "scene": {"built": False, "joints": 0, "mesh": "", "skin_cluster": "",
                  "undo_rollback": "not_tested"},
        "snapshot": {},
        "skin": {},
        "evaluate": {},
        "limits": [],
    }


def _maya_info(cmds) -> dict:
    info = {"version": str(_safe(cmds, "about", "", version=True)),
            "api": str(_safe(cmds, "about", "", apiVersion=True))}
    license_info = _safe(cmds, "about", "", license=True)
    info["license"] = str(license_info) if license_info else "unknown"
    return info


def _safe(cmds, name, default=None, *args, **kwargs):
    func = getattr(cmds, name, None)
    if not callable(func):
        return default
    try:
        value = func(*args, **kwargs)
    except Exception:
        return default
    return default if value is None else value


def _build_scene(cmds) -> dict:
    """建最小可蒙皮场景；返回构建信息，失败时尽量保留已完成部分。"""
    info = {"built": False, "joints": 0, "mesh": "", "skin_cluster": "",
            "undo_rollback": "not_tested"}
    cmds.file(new=True, force=True)
    cmds.select(clear=True)
    joints = []
    for index, name in enumerate(JOINT_CHAIN):
        joints.append(cmds.joint(name=name, position=(0.0, float(index) * 10.0, 0.0)))
    mesh = cmds.polyCube(name="tak_probe_body", width=10.0, height=30.0, depth=10.0)[0]
    cluster = cmds.skinCluster(joints, mesh, toSelectedBones=True, normalizeWeights=1)
    info.update({"built": True, "joints": len(joints), "mesh": str(mesh),
                 "skin_cluster": str(cluster[0] if isinstance(cluster, (list, tuple)) else cluster)})

    # 撤销三段验证：开块 -> 建 locator -> 回滚，locator 必须消失。
    from tak.adapters.maya import create_adapter as _create
    adapter = _create()
    token = adapter.begin_undo("tak_probe_undo")
    locator = str(cmds.spaceLocator(name="tak_probe_locator")[0])
    adapter.rollback(token)
    info["undo_rollback"] = "ok" if not cmds.objExists(locator) else "failed"
    return info


def _run(cmds) -> None:
    report = _blank_report()
    report["maya"]["standalone"] = "initialized"
    report["maya"].update(_maya_info(cmds))
    report["maya"]["importable"] = True
    if report["maya"].get("license") in ("", "unknown"):
        # standalone 能初始化即代表 license 获取成功；about -license 在各版本返回不一致。
        report["maya"]["license"] = "ok (standalone initialized)"

    from tak.adapters.maya import create_adapter, is_available
    report["maya"]["available"] = bool(is_available())
    adapter = create_adapter()

    try:
        report["scene"] = _build_scene(cmds)
    except Exception as exc:
        report["reason"] = "建场景失败: %s: %s" % (type(exc).__name__, exc)
        report["scene"]["error"] = traceback.format_exc(limit=3)

    try:
        snapshot = adapter.get_skeleton_snapshot()
        report["snapshot"] = {
            "keys": sorted(snapshot),
            "joint_count": len(snapshot.get("joints") or {}),
            "version": snapshot.get("version"),
            "unit": snapshot.get("unit"),
            "up_axis": snapshot.get("up_axis"),
            "frame_range": snapshot.get("frame_range"),
            "skin": snapshot.get("skin"),
            "metadata": snapshot.get("metadata"),
            "joint_names": sorted(snapshot.get("joints") or {})[:12],
        }
    except Exception as exc:
        snapshot = {}
        report["reason"] = report["reason"] or "快照失败: %s: %s" % (type(exc).__name__, exc)

    try:
        report["skin"] = adapter.get_skin_summary(report["scene"].get("mesh") or "")
    except Exception as exc:
        report["skin"] = {"error": "%s: %s" % (type(exc).__name__, exc)}

    try:
        from tak.core.runner import evaluate
        result = evaluate(snapshot, dcc="maya")
        report["evaluate"] = {
            "passed": result.passed,
            "counts": result.counts(),
            "findings": [finding.to_dict() for finding in result.findings][:24],
            "rules_available": result.metadata.get("rules_available"),
        }
    except Exception as exc:
        report["evaluate"] = {"error": "%s: %s" % (type(exc).__name__, exc)}

    # 限制与降级原因
    if not report["scene"].get("built"):
        report["limits"].append("场景未完整构建，快照可能为空或只读")
    if not report["snapshot"].get("joint_count"):
        report["limits"].append("快照无关节：检查 license / standalone 初始化")
    if report["skin"] and not report["skin"].get("skin_clusters"):
        report["limits"].append("未找到 skinCluster，蒙皮摘要为零值兜底")
    if report["maya"].get("standalone") != "initialized":
        report["limits"].append("standalone 未初始化：%s"
                                % (report["maya"].get("error") or "未知原因"))

    if report["scene"].get("built") and report["snapshot"].get("joint_count"):
        report["status"] = "ok"
        report["reason"] = report["reason"] or ""
    else:
        report["status"] = "degraded"
    print("RESULT_BEGIN")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print("RESULT_END")


def main() -> int:
    try:
        import maya.cmds as cmds  # type: ignore
        import maya.standalone  # type: ignore
    except Exception as exc:
        report = _blank_report()
        report["reason"] = "maya 不可导入: %s: %s" % (type(exc).__name__, exc)
        report["limits"].append("纯 Python 环境：仅验证 degraded 输出，不建场景")
        print("RESULT_BEGIN")
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        print("RESULT_END")
        return 0

    initialized = False
    try:
        maya.standalone.initialize(name="python")
        initialized = True
    except Exception as exc:
        # license / 环境不允许时仍尝试只读空场景检查，不让脚本直接失败。
        print("[TAK] standalone.initialize 失败: %s: %s" % (type(exc).__name__, exc),
              file=sys.stderr)
        try:
            report = _blank_report()
            report["maya"]["importable"] = True
            report["maya"]["standalone"] = "failed"
            report["maya"]["license"] = "unavailable (standalone initialize failed)"
            report["maya"]["error"] = "%s: %s" % (type(exc).__name__, exc)
            report["reason"] = "standalone 初始化失败，降级为只读空场景检查"
            report["limits"].append("license 或环境不允许 standalone，场景构建已跳过")
            from tak.adapters.maya import create_adapter
            adapter = create_adapter()
            snapshot = adapter.get_skeleton_snapshot()
            report["snapshot"] = {
                "keys": sorted(snapshot),
                "joint_count": len(snapshot.get("joints") or {}),
                "metadata": snapshot.get("metadata"),
            }
            try:
                from tak.core.runner import evaluate
                result = evaluate(snapshot, dcc="maya")
                report["evaluate"] = {"passed": result.passed, "counts": result.counts(),
                                      "findings": [item.to_dict()
                                                   for item in result.findings][:24]}
            except Exception as inner:
                report["evaluate"] = {"error": "%s: %s" % (type(inner).__name__, inner)}
            print("RESULT_BEGIN")
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            print("RESULT_END")
        except Exception:
            report = _blank_report()
            report["reason"] = "降级检查也失败: %s" % traceback.format_exc(limit=2)
            print("RESULT_BEGIN")
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            print("RESULT_END")
        finally:
            if initialized:
                _safe_uninitialize()
        return 0

    try:
        _run(cmds)
    except Exception:
        report = _blank_report()
        report["maya"]["standalone"] = "initialized"
        report["reason"] = "探针异常: %s" % traceback.format_exc(limit=4)
        report["status"] = "error"
        print("RESULT_BEGIN")
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        print("RESULT_END")
    finally:
        _safe_uninitialize()
    return 0


def _safe_uninitialize() -> None:
    try:
        import maya.standalone  # type: ignore
        maya.standalone.uninitialize()
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
