# -*- coding: utf-8 -*-
"""UE 无头批量入口：在 UnrealEditor-Cmd 里跑 TAK 规则并落一份 JSON 报告。

两种用法：

* 编辑器内 / 探测脚本：``from tak.adapters.unreal.commandlet import run_headless``
  然后 ``run_headless()``，快照从当前 UE 场景采集；
* CI / 本机无引擎：``python -m tak.adapters.unreal.commandlet --snapshot x.json --out r.json``，
  直接读本地 sidecar 快照跑离线规则，不需要 unreal。

约定：返回字典永远可 JSON 序列化；失败不抛异常，错误写进 ``errors``。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from .adapter import create_adapter, is_available

DEFAULT_OUT_NAME = "tak_headless_report.json"


def _load_snapshot(path: str) -> Tuple[Dict[str, Any], str]:
    """读 sidecar / 快照 JSON；自动拆 ``{"snapshot": {...}}`` 包装。"""
    target = os.path.abspath(str(path))
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return {}, "无法读取快照 %s: %s: %s" % (target, type(exc).__name__, exc)
    if isinstance(data, dict) and "joints" not in data and isinstance(data.get("snapshot"), dict):
        data = data["snapshot"]
    if not isinstance(data, dict) or not isinstance(data.get("joints"), dict):
        return {}, "快照 %s 缺少 joints 对象" % target
    return data, ""


def _resolve_out(out_path: Optional[str]) -> str:
    target = str(out_path or os.environ.get("TAK_COMMANDLET_OUT") or "").strip()
    if not target:
        target = os.path.join(os.getcwd(), DEFAULT_OUT_NAME)
    target = os.path.abspath(target)
    parent = os.path.dirname(target)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            pass
    return target


def run_headless(snapshot_path: Optional[str] = None,
                 out_path: Optional[str] = None,
                 dcc: str = "unreal",
                 tool_id: Optional[str] = None,
                 spec: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """跑一次无头体检，返回 ``{"ok", "report", "out", "source", "errors"}``。

    - ``snapshot_path`` 为空时由 adapter 从当前 UE 场景采集（无引擎则为空快照）；
    - ``tool_id`` 非空时走 ``runner.run_tool``，否则走 ``runner.evaluate``；
    - 报告无论通过与否都会落盘，便于 CI 归档。
    """
    from tak.core.runner import evaluate, run_tool

    result: Dict[str, Any] = {
        "ok": False,
        "dcc": str(dcc or "unreal"),
        "tool_id": str(tool_id or ""),
        "source": "",
        "snapshot_path": os.path.abspath(snapshot_path) if snapshot_path else "",
        "out": "",
        "engine_available": bool(is_available()),
        "errors": [],
    }

    snapshot: Dict[str, Any] = {}
    if snapshot_path:
        snapshot, error = _load_snapshot(snapshot_path)
        if error:
            result["errors"].append(error)
        else:
            result["source"] = "snapshot"

    adapter = create_adapter()
    if not snapshot:
        snapshot = adapter.get_skeleton_snapshot() or {}
        result["source"] = "adapter" if is_available() else "offline_adapter"

    try:
        if tool_id:
            tool_result = run_tool(str(tool_id), snapshot=snapshot, dcc=result["dcc"],
                                   adapter=adapter, spec=spec)
            report = tool_result.report
            data = tool_result.to_dict()
        else:
            report = evaluate(snapshot=snapshot, spec=spec,
                              dcc=result["dcc"], adapter=adapter,
                              title="TAK UE Headless")
            data = report.to_dict()
    except Exception as exc:
        message = "规则执行失败: %s: %s" % (type(exc).__name__, exc)
        result["errors"].append(message)
        return result

    out = _resolve_out(out_path)
    try:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        result["out"] = out
    except OSError as exc:
        result["errors"].append("报告写入失败 %s: %s" % (out, exc))

    result["ok"] = bool(report.passed) and not result["errors"]
    result["passed"] = bool(report.passed)
    result["joint_count"] = len(snapshot.get("joints") or {})
    result["counts"] = report.counts()
    result["findings"] = [finding.to_dict() for finding in report.findings]
    result["report"] = data
    return result


def _parse_args(argv: List[str]) -> Dict[str, Any]:
    """同时接受 ``--snapshot x`` 与 UE 风格 ``-snapshot=x``。"""
    options: Dict[str, Any] = {"snapshot": None, "out": None, "dcc": "unreal",
                               "tool": None}
    index = 0
    while index < len(argv):
        token = str(argv[index])
        if not token.startswith("-"):
            index += 1
            continue
        body = token.lstrip("-")
        if "=" in body:
            key, _, value = body.partition("=")
        else:
            key = body
            value = ""
            if index + 1 < len(argv) and not str(argv[index + 1]).startswith("-"):
                value = str(argv[index + 1])
                index += 1
        key = key.replace("-", "_").lower()
        if key in ("snapshot", "snapshot_path"):
            options["snapshot"] = value
        elif key in ("out", "out_path"):
            options["out"] = value
        elif key in ("dcc",):
            options["dcc"] = value or "unreal"
        elif key in ("tool", "tool_id"):
            options["tool"] = value or None
        index += 1
    return options


def main(argv: Optional[List[str]] = None) -> int:
    """命令行入口：``python -m tak.adapters.unreal.commandlet --snapshot ...``。"""
    options = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    result = run_headless(snapshot_path=options["snapshot"], out_path=options["out"],
                          dcc=str(options["dcc"] or "unreal"),
                          tool_id=options["tool"])
    print("[TAK] headless ok=%s passed=%s joints=%s out=%s" % (
        result["ok"], result.get("passed"), result.get("joint_count"), result.get("out")))
    for error in result.get("errors") or []:
        print("[TAK] error: %s" % error)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
