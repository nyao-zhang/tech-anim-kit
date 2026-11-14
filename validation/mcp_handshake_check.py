# -*- coding: utf-8 -*-
"""按客户端**实际** mcpServers 配置起一次 stdio MCP server，检查握手。

这个脚本是给「`MCP initialize timed out` / 所有工具都报桥未启动」这类问题用的：
单元测试里的 ``handle()`` 只验证协议逻辑，验证不到客户端真正拉起的子进程、
``command`` 指向的到底是哪个 exe、以及 ``env`` 被全量替换后 ``~`` 还能不能解析。

用法::

    python validation/mcp_handshake_check.py
    python validation/mcp_handshake_check.py --config <mcp.json> --server tak-scene
    python validation/mcp_handshake_check.py --out <结果.json>

默认读 ``~/.workbuddy/mcp.json``，结果写 ``validation/mcp_handshake_result.json``。
只发只读请求（initialize / tools/list / tak_status / tak_scene_summary），不改场景。
零第三方依赖、不 import 任何 DCC 模块；退出码 0 = 全通。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "mcp_handshake_result.json")
DCC_EXE_NAMES = frozenset({
    "maya.exe", "3dsmax.exe", "motionbuilder.exe", "unrealeditor.exe",
    "unrealeditor-cmd.exe", "blender.exe", "houdini.exe",
})
PYTHON_PREFIXES = ("python", "pypy", "mayapy")


def _looks_like_python(path: str) -> bool:
    name = os.path.basename(str(path or "")).lower()
    return bool(name) and name not in DCC_EXE_NAMES \
        and any(name.startswith(p) for p in PYTHON_PREFIXES)


def _rpc(proc: Any, request: Dict[str, Any],
         timeout: float = 20.0) -> Tuple[Optional[Dict[str, Any]], float, str]:
    start = time.perf_counter()
    try:
        proc.stdin.write((json.dumps(request, ensure_ascii=True) + "\n").encode("ascii"))
        proc.stdin.flush()
    except (BrokenPipeError, OSError) as exc:
        return None, (time.perf_counter() - start) * 1000, "写入 stdin 失败: %s" % exc
    box: Dict[str, Any] = {}

    def _read() -> None:
        try:
            box["line"] = proc.stdout.readline()
        except Exception as exc:  # pragma: no cover - 管道异常
            box["error"] = str(exc)

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    reader.join(timeout)
    elapsed = (time.perf_counter() - start) * 1000
    if "line" not in box:
        return None, elapsed, "%.0f 秒内没有响应" % timeout
    line = box["line"]
    if not line:
        return None, elapsed, "进程提前退出（stdout EOF），通常是 command 配错或 import 失败"
    try:
        answer = json.loads(line.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        return None, elapsed, "响应不是合法 ASCII JSON: %s" % exc
    if "error" in answer:
        return answer, elapsed, "JSON-RPC error: %s" % answer["error"]
    return answer, elapsed, ""


def _step(steps: List[Dict[str, Any]], name: str, answer: Any,
          ms: float, error: str, detail: Any = None) -> bool:
    ok = answer is not None and not error
    steps.append({"name": name, "ok": ok, "ms": round(ms, 1),
                  "error": error, "detail": detail})
    return ok


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="TAK MCP stdio 握手自检")
    parser.add_argument("--config", default=os.path.join(
        os.path.expanduser("~"), ".workbuddy", "mcp.json"))
    parser.add_argument("--server", default="tak-scene")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--minimal-env", action="store_true",
                        help="完全忽略配置里的 env（只留 SystemRoot/PATH），"
                             "验证启动器能否自举；模拟最狠的客户端")
    args = parser.parse_args(argv)

    result: Dict[str, Any] = {"ok": False, "config": args.config,
                              "server": args.server, "steps": [],
                              "warnings": []}
    proc = None
    try:
        with io.open(args.config, "r", encoding="utf-8") as handle:
            cfg = json.load(handle)
        entry = (cfg.get("mcpServers") or {})[args.server]
        command = str(entry.get("command") or "")
        command_args = [str(item) for item in (entry.get("args") or [])]
        env_cfg = {str(k): str(v) for k, v in (entry.get("env") or {}).items()}
        result["command"] = command
        result["args"] = command_args
        result["env"] = env_cfg
        result["minimal_env"] = bool(args.minimal_env)

        if not _looks_like_python(command):
            result["warnings"].append(
                "command=%r 不是 Python 解释器（很像 DCC 宿主进程）；"
                "这正是 initialize 超时的典型原因。运行 "
                "python scripts/install_mcp_config.py 一键修复。" % command)
            result["fatal"] = ("command 不是 Python 解释器（客户端会去启动 DCC GUI，"
                               "30 秒收不到 initialize 响应）")
            return 1
        uses_entry = any(item.endswith("tak_mcp_entry.py") for item in command_args)
        if (not uses_entry and not env_cfg.get("TAK_HOME")
                and not args.minimal_env):
            result["warnings"].append(
                "env 没有 TAK_HOME；客户端若全量替换环境，桥发现文件会找不到，"
                "工具一律回「桥未启动」。用 tak_mcp_entry.py 启动器可免此依赖。")
        if not os.path.isfile(command):
            result["fatal"] = "command 指向的文件不存在：%s" % command
            return 1
        for item in command_args:
            if item.endswith(".py") and not os.path.isfile(item):
                result["fatal"] = "args 指向的启动脚本不存在：%s" % item
                return 1
        if not any(item == "-m" or item.endswith("tak_mcp_entry.py")
                   for item in command_args):
            result["fatal"] = ("args 既没有 -m tak.mcp 也没有 tak_mcp_entry.py，"
                               "server 不会进入协议循环：%r" % command_args)
            return 1

        # 模拟最严苛的客户端：只给配置里的 env + 最小系统变量，不继承 USERPROFILE。
        # --minimal-env 时连配置 env 都丢掉，专门验证启动器自举能力。
        env = {} if args.minimal_env else dict(env_cfg)
        env.setdefault("SystemRoot", os.environ.get("SystemRoot", r"C:\Windows"))
        env.setdefault("PATH", os.environ.get("PATH", ""))
        proc = subprocess.Popen([command] + command_args,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env, cwd=HERE)
        init, ms, err = _rpc(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "tak-handshake-check", "version": "1"}},
        })
        if not _step(result["steps"], "initialize", init, ms, err,
                     (init or {}).get("result", {}).get("serverInfo")):
            result["fatal"] = err
            return 1
        proc.stdin.write((
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
            + "\n").encode("ascii"))
        proc.stdin.flush()

        listing, ms, err = _rpc(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        payload = (listing or {}).get("result", {}) if listing else {}
        tools = payload.get("tools") or []
        listing_ok = _step(result["steps"], "tools/list", listing, ms, err,
                           {"tools": len(tools),
                            "bridge_running": payload.get("bridge_running"),
                            "allow_write": payload.get("allow_write")})
        result["tool_count"] = len(tools)
        result["bridge_running"] = bool(payload.get("bridge_running"))
        if not listing_ok:
            result["fatal"] = err
            return 1

        status, ms, err = _rpc(proc, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "tak_status", "arguments": {}}})
        status_text = ""
        if status:
            try:
                status_text = status["result"]["content"][0]["text"]
                result["bridge_url"] = json.loads(status_text).get("url", "")
            except (KeyError, IndexError, ValueError, TypeError):
                pass
        _step(result["steps"], "tak_status", status, ms, err,
              result.get("bridge_url") or status_text[:120])

        if result["bridge_running"]:
            scene, ms, err = _rpc(proc, {
                "jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "tak_scene_summary", "arguments": {}}}, timeout=25.0)
            detail = ""
            if scene:
                try:
                    data = json.loads(scene["result"]["content"][0]["text"])
                    detail = {"ok": data.get("ok"), "dcc": (data.get("result") or {}).get("dcc"),
                              "frame_range": (data.get("result") or {}).get("frame_range")}
                except (KeyError, IndexError, ValueError, TypeError):
                    pass
            _step(result["steps"], "tak_scene_summary", scene, ms, err, detail)
        else:
            result["warnings"].append(
                "桥没在跑，跳过 tak_scene_summary；在 DCC 面板点「启动桥接」后重跑可覆盖真机读取。")

        result["ok"] = all(step["ok"] for step in result["steps"])
    except Exception as exc:
        result["fatal"] = "%s: %s" % (type(exc).__name__, exc)
        result["ok"] = False
    finally:
        if proc is not None:
            try:
                proc.kill()
                proc.wait(timeout=5)
                result["stderr"] = proc.stderr.read().decode("utf-8", "replace")[:400]
            except Exception:
                pass
        try:
            with io.open(args.out, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        except OSError as exc:
            print("结果写入失败: %s" % exc)

    print("TAK MCP 握手自检: %s" % ("OK" if result["ok"] else "FAILED"))
    print("  command = %s" % result.get("command", "-"))
    for step in result["steps"]:
        print("  %-20s %-5s %7.1f ms  %s" % (
            step["name"], "ok" if step["ok"] else "FAIL", step["ms"],
            json.dumps(step["detail"], ensure_ascii=False)[:120]
            if step["detail"] is not None else (step["error"] or "")))
    for warning in result["warnings"]:
        print("  [warn] %s" % warning)
    if result.get("fatal"):
        print("  [fatal] %s" % result["fatal"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
