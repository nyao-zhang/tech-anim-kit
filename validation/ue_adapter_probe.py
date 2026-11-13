# -*- coding: utf-8 -*-
"""UE 真机探测：验证 tak.adapters.unreal 适配器（由 UnrealEditor-Cmd 调用）。

用法（由主 agent 统一执行，脚本自身**不启动**任何命令）::

    UnrealEditor-Cmd.exe validation/TakProbe.uproject -nullrhi -unattended -nosplash \
        -ExecutePythonScript=validation/ue_adapter_probe.py

输出路径由环境变量 ``TAK_PROBE_OUT`` 指定，默认
``validation/ue_adapter_probe_result.json``。

设计要点
--------
* 与 ``validation/ue_probe.py`` 一致：不依赖 ``__file__``（UE 执行脚本时不保证
  存在），仓库根走常量 / ``TAK_REPO_ROOT`` 环境变量。
* 纯 Python 下运行也能落一份 ``degraded`` 结果而不是崩溃，便于 CI 冒烟。
* 只做只读操作：不注册菜单、不写资产、不改场景。
"""
import json
import os
import platform
import sys
import traceback

REPO_ROOT = os.environ.get(
    "TAK_REPO_ROOT", r"C:\Users\NYAO\WorkBuddy\2026-11-12-23-27-35")
OUT = os.environ.get(
    "TAK_PROBE_OUT",
    os.path.join(REPO_ROOT, "validation", "ue_adapter_probe_result.json"))

result = {
    "ok": False,
    "probe": "ue_adapter",
    "python_version": sys.version,
    "python_executable": sys.executable,
    "platform": platform.platform(),
    "repo_root": REPO_ROOT,
    "engine_version": None,
    "available": False,
    "describe": {},
    "api_missing": [],
    "snapshot": {},
    "evaluate": {},
    "degraded": [],
    "errors": [],
}

try:
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)

    import tak
    from tak.adapters.base import Capability
    from tak.adapters.unreal import UnrealAdapter, create_adapter, is_available
    from tak.core.runner import evaluate

    result["tak_version"] = getattr(tak, "__version__", "")
    result["available"] = bool(is_available())
    if not result["available"]:
        result["degraded"].append(
            "unreal 模块不可导入：已跑通纯 Python 降级路径，结论不能代表编辑器内行为")

    adapter = create_adapter()
    info = adapter.describe()
    result["describe"] = info
    result["engine_version"] = info.get("engine_version") or None
    result["api_missing"] = list(info.get("api_missing") or [])
    result["degraded"].extend(info.get("degraded") or [])

    snapshot = adapter.get_skeleton_snapshot()
    result["snapshot"] = {
        "dcc": snapshot.get("dcc"),
        "unit": snapshot.get("unit"),
        "up_axis": snapshot.get("up_axis"),
        "native_up_axis": (snapshot.get("metadata") or {}).get("native_up_axis"),
        "matrix_source": (snapshot.get("metadata") or {}).get("matrix_source"),
        "joint_count": len(snapshot.get("joints") or {}),
        "joints": sorted(snapshot.get("joints") or {})[:20],
        "skin": snapshot.get("skin") or {},
        "degraded": (snapshot.get("metadata") or {}).get("degraded") or [],
    }

    report = evaluate(snapshot=snapshot, dcc="unreal", adapter=adapter,
                      title="TAK UE Adapter Probe")
    result["evaluate"] = {
        "passed": report.passed,
        "counts": report.counts(),
        "rules_available": (report.metadata or {}).get("rules_available"),
        "findings": [finding.to_dict() for finding in report.findings][:20],
    }

    # 适配器语义冒烟：排序稳定、类型正确即可，不写入任何资产。
    result["list_nodes"] = {
        "joint": adapter.list_nodes("joint")[:20],
        "anim_count": len(adapter.list_nodes("anim")),
        "skeletal_mesh_count": len(adapter.list_nodes("skeletal_mesh")),
    }
    result["ui_parent"] = adapter.ui_parent()
    result["supports"] = [capability.value for capability in Capability
                          if adapter.supports(capability)]
    result["adapter_is_unreal"] = isinstance(adapter, UnrealAdapter)
    result["ok"] = True
except Exception:
    result["errors"].append(traceback.format_exc())

result["passed"] = bool((result.get("evaluate") or {}).get("passed"))
os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
with open(OUT, "w", encoding="utf-8") as fh:
    json.dump(result, fh, ensure_ascii=False, indent=2)
print("[TAK-ADAPTER-PROBE] ok=%s available=%s engine=%s joints=%s out=%s" % (
    result["ok"], result["available"], result["engine_version"],
    (result.get("snapshot") or {}).get("joint_count"), OUT))
