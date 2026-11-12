# -*- coding: utf-8 -*-
"""UE 编辑器内运行的真机探测脚本（由 UnrealEditor-Cmd -ExecutePythonScript 调用）。

目的：把「能在 commandlet 里客观测到」的事实写成 JSON，
不能测到的（GUI 交互、真实资产、5.9、Max2023+）留给 docs/VALIDATION.md。

注意：UE 执行脚本时 __file__ 不保证存在，因此全部使用绝对路径 / 环境变量。
"""
import json
import os
import platform
import sys
import traceback

REPO_ROOT = r"C:\Users\NYAO\WorkBuddy\2026-11-12-23-27-35"
LEGACY_PY_DIR = r"C:\Users\NYAO\Documents\UnrealEngine\Python"
OUT = os.environ.get("TAK_PROBE_OUT", os.path.join(REPO_ROOT, "validation", "ue_probe_result.json"))

result = {
    "ok": False,
    "python_version": sys.version,
    "python_executable": sys.executable,
    "platform": platform.platform(),
    "repo_root": REPO_ROOT,
    "engine_version": None,
    "tak_import": {},
    "api": {},
    "legacy_python_syntax": {},
    "errors": [],
}

try:
    import unreal
    result["engine_version"] = str(unreal.SystemLibrary.get_engine_version())
    result["unreal_module_file"] = str(getattr(unreal, "__file__", "built-in"))
except Exception:
    result["errors"].append("import unreal 失败:\n" + traceback.format_exc())

try:
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import tak
    from tak.compat.unreal import API_PROBE_NAMES, api_probe, engine_version
    from tak.core.spec import load_spec
    result["tak_import"] = {
        "ok": True,
        "version": tak.__version__,
        "spec_version": load_spec()["version"],
        "engine_version_via_compat": engine_version(unreal),
        "api_probe_count": len(API_PROBE_NAMES),
    }
    result["api"] = api_probe(unreal)
except Exception:
    result["tak_import"]["ok"] = False
    result["errors"].append("import tak 失败:\n" + traceback.format_exc())

# 用户既有 acc_*.py 在 UE5.8 (Python 3.11) 下的语法兼容性：只编译，不执行
for name in ("acc_menu.py", "acc_unreal_api.py", "acc_unreal_def.py",
             "acc_perf_audit_ue.py", "init_unreal.py"):
    path = os.path.join(LEGACY_PY_DIR, name)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            compile(fh.read(), path, "exec")
        result["legacy_python_syntax"][name] = "ok"
    except FileNotFoundError:
        result["legacy_python_syntax"][name] = "missing"
    except Exception as exc:
        result["legacy_python_syntax"][name] = "%s: %s" % (type(exc).__name__, exc)

result["ok"] = bool(result["engine_version"]) and bool(result["tak_import"].get("ok"))
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as fh:
    json.dump(result, fh, ensure_ascii=False, indent=2)
print("[TAK-PROBE] ok=%s engine=%s out=%s" % (result["ok"], result["engine_version"], OUT))
