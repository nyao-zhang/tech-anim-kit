# -*- coding: utf-8 -*-
"""v0.7.2：MCP 自包含启动器的回归护栏。

覆盖两件真实踩过坑的事：

* 客户端 UI 重新 Trust 时把 v0.7.0 的旧 ``maya.exe`` 配置写回 mcp.json；
* 客户端把 ``env`` 全量替换成空对象，``-m tak.mcp`` 找不到 tak / ``~`` 解析不出来。
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional, Tuple

import pytest

from tak.mcp import config_snippet, diagnose_config, entry_script, repo_root

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = repo_root()
ENTRY = os.path.join(ROOT, "scripts", "tak_mcp_entry.py")
INSTALL = os.path.join(ROOT, "scripts", "install_mcp_config.py")


def _legacy_config() -> Dict[str, Any]:
    """v0.7.0 在 Maya 面板里生成的错误片段。"""
    return {
        "command": r"C:\Program Files\Autodesk\Maya2026\bin\maya.exe",
        "args": ["-m", "tak.mcp"],
        "env": {"PYTHONPATH": ROOT},
    }


def _load_entry():
    spec = importlib.util.spec_from_file_location("tak_mcp_entry_under_test", ENTRY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# 配置生成 / 诊断
# --------------------------------------------------------------------------- #
def test_config_snippet_uses_entry_script_and_passes_diagnose():
    assert os.path.isfile(ENTRY)
    assert entry_script() == ENTRY
    server = config_snippet()["mcpServers"]["tak-scene"]
    assert "python" in os.path.basename(server["command"]).lower()
    assert "maya.exe" not in server["command"].lower()
    assert server["args"] == [ENTRY]
    assert os.path.isabs(server["env"]["TAK_HOME"])
    assert diagnose_config(server) == []


def test_diagnose_catches_legacy_maya_exe_and_missing_tak_home():
    problems = diagnose_config(_legacy_config())
    assert any("不是 Python 解释器" in item for item in problems)

    # 没用启动器时，env 缺 TAK_HOME 必须报出来
    plain = {"command": sys.executable, "args": ["-m", "tak.mcp"],
             "env": {"PYTHONPATH": ROOT}}
    assert any("TAK_HOME" in item for item in diagnose_config(plain))

    # 用了启动器，env 整段删掉也不该报错
    minimal = {"command": sys.executable, "args": [ENTRY], "env": {}}
    assert diagnose_config(minimal) == []


def test_diagnose_catches_missing_entry_file(tmp_path):
    missing = str(tmp_path / "nope" / "tak_mcp_entry.py")
    problems = diagnose_config({"command": sys.executable, "args": [missing],
                                "env": {}})
    assert any("不存在" in item for item in problems)


# --------------------------------------------------------------------------- #
# 启动器自举
# --------------------------------------------------------------------------- #
def test_bootstrap_fills_env_without_inheriting_userprofile(monkeypatch):
    for key in ("USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA",
                "LOCALAPPDATA", "TAK_HOME", "PYTHONPATH", "PYTHONIOENCODING",
                "TEMP", "TMP"):
        monkeypatch.delenv(key, raising=False)
    before = list(sys.path)
    try:
        module = _load_entry()
        module.bootstrap()
        assert ROOT in sys.path
        assert os.environ["PYTHONPATH"]
        assert os.environ["USERPROFILE"]
        home = os.environ["TAK_HOME"]
        assert os.path.isabs(home) and not home.startswith("~")
        assert home.rstrip("\\/").endswith(".tak")
    finally:
        sys.path[:] = before


def _spawn(command, args, env):
    return subprocess.Popen(command + args, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, cwd=HERE)


def _read_line(proc, request, timeout=20.0):
    proc.stdin.write((json.dumps(request, ensure_ascii=True) + "\n").encode("ascii"))
    proc.stdin.flush()
    box: Dict[str, Any] = {}

    def worker():
        try:
            box["line"] = proc.stdout.readline()
        except Exception as exc:  # pragma: no cover - 管道异常
            box["error"] = str(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout)
    assert "line" in box, "%.0f 秒内没有 initialize 响应" % timeout
    assert box["line"], "进程提前退出: %s" % proc.stderr.read().decode(
        "utf-8", "replace")[:400]
    return json.loads(box["line"].decode("ascii"))


def _win_minimal_env() -> Dict[str, str]:
    env = {}
    for key in ("SystemRoot", "SYSTEMROOT"):
        if os.environ.get(key):
            env[key] = os.environ[key]
            break
    env.setdefault("SystemRoot", r"C:\Windows")
    env["PATH"] = os.environ.get("PATH", "")
    return env


def test_entry_initializes_with_empty_env():
    """客户端把 env 全量替换成空：启动器仍要能 import tak 并回 initialize。"""
    proc = _spawn([sys.executable], [ENTRY], _win_minimal_env())
    try:
        answer = _read_line(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "pytest", "version": "1"}},
        })
        assert answer["result"]["serverInfo"]["name"] == "tak-scene-bridge"
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_entry_print_config_has_absolute_tak_home_with_empty_env():
    proc = subprocess.run([sys.executable, ENTRY, "--print-config"],
                          env=_win_minimal_env(), cwd=HERE,
                          capture_output=True, timeout=20)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[:400]
    snippet = json.loads(proc.stdout.decode("utf-8", "replace"))
    home = snippet["mcpServers"]["tak-scene"]["env"]["TAK_HOME"]
    assert os.path.isabs(home) and not home.startswith("~")
    assert home.rstrip("\\/").endswith(".tak")


# --------------------------------------------------------------------------- #
# 一键修复脚本
# --------------------------------------------------------------------------- #
def test_install_script_repairs_legacy_config_and_keeps_other_servers(tmp_path):
    target = tmp_path / "mcp.json"
    original = {"mcpServers": {
        "tak-scene": _legacy_config(),
        "another-server": {"command": "node", "args": ["x.js"]},
    }}
    with io.open(str(target), "w", encoding="utf-8") as handle:
        handle.write(json.dumps(original, ensure_ascii=False, indent=2))

    check = subprocess.run([sys.executable, INSTALL, "--check",
                            "--config", str(target)],
                           cwd=ROOT, capture_output=True, timeout=30)
    assert check.returncode == 1
    assert "maya.exe" in check.stdout.decode("utf-8", "replace")

    fixed = subprocess.run([sys.executable, INSTALL, "--no-handshake",
                            "--config", str(target)],
                           cwd=ROOT, capture_output=True, timeout=30)
    assert fixed.returncode == 0, fixed.stdout.decode("utf-8", "replace")[-600:]

    with io.open(str(target), "r", encoding="utf-8") as handle:
        data = json.load(handle)
    assert data["mcpServers"]["another-server"] == original["mcpServers"]["another-server"]
    server = data["mcpServers"]["tak-scene"]
    assert "maya.exe" not in server["command"]
    assert server["args"] == [ENTRY]
    assert diagnose_config(server) == []
    assert list(tmp_path.glob("mcp.json.bak-*")), "必须留下备份"


def test_install_module_diagnose_then_repair(tmp_path):
    from tak.mcp import install as mcp_install

    target = tmp_path / "mcp.json"
    with io.open(str(target), "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"mcpServers": {
            "tak-scene": _legacy_config(), "keep-me": {"command": "node"}}}))

    report = mcp_install.diagnose(str(target))
    assert not report["ok"] and "maya.exe" in str(report["problems"])
    assert report["recommendation"]

    fixed = mcp_install.repair(str(target))
    assert fixed["ok"] and fixed["backup"]
    assert fixed["before"] and not fixed["after"]
    assert os.path.isfile(fixed["backup"])
    assert mcp_install.load(str(target))["mcpServers"]["keep-me"] == {"command": "node"}
    assert mcp_install.diagnose(str(target))["ok"]


def test_agent_console_core_repair_uses_same_module(tmp_path, monkeypatch):
    from tools.agent_console import core

    target = tmp_path / "mcp.json"
    with io.open(str(target), "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"mcpServers": {"tak-scene": _legacy_config()}}))
    monkeypatch.setenv("TAK_MCP_CONFIG", str(target))

    assert not core.mcp_diagnose()["ok"]
    assert core.mcp_repair()["ok"]
    assert core.mcp_diagnose()["ok"]
