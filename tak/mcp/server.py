# -*- coding: utf-8 -*-
"""TAK 的 MCP server：让外部 AI 客户端真的能查 / 改 Maya 场景。

协议与拓扑
----------
* 传输：**stdio + 每行一个 JSON-RPC 2.0 对象**（MCP stdio transport），
  日志一律写 stderr，stdout 只准出现协议消息；
* 本进程**不 import 任何 DCC**，只是个 HTTP 客户端：
  ``MCP 客户端 -> stdio -> tak.mcp -> 127.0.0.1 桥 -> Maya 主线程 -> maya.cmds``。
  所以 ``command`` 用系统 Python 就行，不需要 Maya 的 python 解释器；
* ``tools/list`` 优先问桥要当前 DCC 的能力表（含写开关状态），桥没开时
  回落到 core 的静态声明，AI 至少能看到「有哪些能力」并得到明确的
  「桥未启动」提示；
* ``tools/call`` 把 tool 名映射回受限动作，参数先经 core 白名单校验，
  再经桥的写开关与两阶段确认，最后才落到宿主。

**为什么写操作要两步**：AI 第一次调用只拿到 dry-run diff 和 ``confirm_token``，
把它展示给人；人同意后第二次调用带上 token 才真写。这样「模型自己决定写」
不会发生，token 还绑定了动作与参数，改参数即失效。

零第三方依赖：只用标准库，Python 3.9+。
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional, Tuple

from tak.core import bridge as tak_bridge
from tak.core import config as tak_config
from tak.core import scene_actions as sa
from tak.version import __version__

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "tak-scene-bridge"

#: 不属于动作参数、只由 MCP 层消费的控制字段。
_CONTROL_KEYS = ("confirm", "confirm_token", "dry_run")

_META_TOOLS: Tuple[Dict[str, Any], ...] = (
    {
        "name": "tak_status",
        "description": "查询 TAK 桥接状态：是否启动、当前 DCC、地址、是否允许外部写入、"
                       "可用动作数。开始工作前先调这个。",
        "inputSchema": {"type": "object", "properties": {},
                        "additionalProperties": False},
    },
    {
        "name": "tak_list_actions",
        "description": "列出所有可用的场景动作及其参数、权限、是否已实现。"
                       "不确定该调哪个工具时先看这个。",
        "inputSchema": {"type": "object", "properties": {},
                        "additionalProperties": False},
    },
    {
        "name": "tak_undo",
        "description": "撤销最近一次由 TAK 执行的写操作（仅当宿主最近一次操作确实是 TAK 时生效）",
        "inputSchema": {"type": "object",
                        "properties": {
                            "confirm_token": {"type": "string",
                                             "description": "首次调用返回的确认 token"},
                        },
                        "additionalProperties": False},
    },
)


def _stderr(message: str) -> None:
    try:
        sys.stderr.write("[tak.mcp] %s\n" % message)
        sys.stderr.flush()
    except Exception:
        pass


def _text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _tool_result(payload: Any, is_error: bool = False) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": _text(payload)}],
            "isError": bool(is_error)}


def bridge_tools() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """问桥要能力表；桥没开时用 core 的静态声明兜底。"""
    status = tak_bridge.bridge_status()
    if status.get("running"):
        # 桥在但宿主主线程卡住时不能让 tools/list 拖到客户端超时。
        try:
            answer = tak_bridge.call("/bridge/actions", timeout=4.0)
        except Exception as exc:  # pragma: no cover - 宿主相关
            _stderr("/bridge/actions 探测失败，回落静态能力表: %s" % exc)
            answer = {}
        if answer.get("ok") and answer.get("mcp_tools"):
            tools = list(answer["mcp_tools"])
            for index, tool in enumerate(tools):
                action = sa.REGISTRY.tool_to_action(tool["name"])
                spec = sa.REGISTRY.get(action)
                if spec is not None and not spec.read_only:
                    tools[index] = _with_confirm_fields(tool)
            return tools + list(_META_TOOLS), status
    static = []
    for tool in sa.REGISTRY.mcp_tools():
        action = sa.REGISTRY.tool_to_action(tool["name"])
        spec = sa.REGISTRY.get(action)
        static.append(tool if spec is None or spec.read_only
                      else _with_confirm_fields(tool))
    return static + list(_META_TOOLS), status


def _with_confirm_fields(tool: Dict[str, Any]) -> Dict[str, Any]:
    """给写动作的 schema 注入 confirm / confirm_token，否则客户端会拒传。"""
    item = dict(tool)
    schema = dict(item.get("inputSchema") or {"type": "object"})
    properties = dict(schema.get("properties") or {})
    properties["confirm_token"] = {
        "type": "string",
        "description": "第二次调用时填第一次返回的 confirm_token；填了即表示人已确认，"
                       "动作会真正执行（还需 DCC 面板打开「允许 MCP 写入」）",
    }
    properties["confirm"] = {
        "type": "boolean",
        "description": "与 confirm_token 一起为 true 时才会写场景；缺省只做 dry-run",
    }
    schema["properties"] = properties
    schema["additionalProperties"] = False
    item["inputSchema"] = schema
    return item


def _status_payload() -> Dict[str, Any]:
    status = tak_bridge.bridge_status()
    return {
        "ok": True,
        "running": bool(status.get("running")),
        "dcc": status.get("dcc") or "",
        "url": status.get("url") or "",
        "owner": status.get("owner") or "",
        "allow_write": bool(status.get("allow_write")),
        "action_count": len(sa.REGISTRY.names()),
        "hint": ("桥已就绪，可以直接调用 tak_* 工具查询场景。" if status.get("running")
                 else "桥未启动：在 Maya 的 TAK 面板 → 对话控制台 → 点「启动桥接」。"),
    }


def _call_action(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """把一个 MCP tool call 转成一次桥的 /bridge/act 调用。"""
    if not tak_bridge.bridge_status().get("running"):
        return {"ok": False,
                "error": "TAK 桥未启动：在 Maya 的 TAK 面板 → 对话控制台 → "
                         "「启动桥接」。MCP server 只是客户端，不会自己启动 Maya。",
                "hint": "也可以先调 tak_status 看当前状态。"}

    args = dict(arguments or {})
    token = str(args.pop("confirm_token", "") or "")
    explicit_confirm = bool(args.pop("confirm", False))
    args.pop("dry_run", None)  # 不允许客户端绕过两阶段

    if name == "tak_undo":
        name, args = "scene.undo_tak", {}
    action = sa.REGISTRY.tool_to_action(name)
    if not action:
        return {"ok": False, "error": "未知 MCP 工具：%s" % name,
                "hint": "调 tak_list_actions 查看可用工具"}

    spec = sa.REGISTRY.get(action)
    commit = bool(token and explicit_confirm and spec and not spec.read_only)
    body: Dict[str, Any] = {
        "action": action,
        "params": args,
        "confirm": commit,
        "dry_run": not commit,
    }
    if token:
        body["confirm_token"] = token
    answer = tak_bridge.call("/bridge/act", "POST", body, timeout=40.0)
    # 把 HTTP 状态 / 业务状态分开，别让 AI 误读。
    if not answer.get("ok"):
        answer["action"] = action
    return answer


def _handle_tools_call(params: Dict[str, Any]) -> Dict[str, Any]:
    name = str(params.get("name") or "")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _tool_result({"ok": False, "error": "arguments 必须是对象"}, True)
    if name == "tak_status":
        return _tool_result(_status_payload())
    if name == "tak_list_actions":
        return _tool_result({
            "ok": True,
            "actions": sa.REGISTRY.describe(),
            "write_requires": "先 dry-run 拿 confirm_token → 带 token 且 confirm=true "
                              "再次调用；DCC 面板的「允许 MCP 写入」必须打开",
        })
    payload = _call_action(name, arguments)
    return _tool_result(payload, not payload.get("ok", False))


def handle(request: Any) -> Optional[Dict[str, Any]]:
    """处理一条 JSON-RPC 请求；notification 返回 None（不发响应）。

    单独暴露成纯函数，便于在没有 stdio 的单元测试里跑完整协议往返。
    """
    if not isinstance(request, dict):
        return _error(None, -32600, "Invalid Request")
    request_id = request.get("id")
    method = str(request.get("method") or "")
    params = request.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    # 没有 id 的是 notification（如 notifications/initialized），不能回消息。
    notification = "id" not in request
    try:
        if method == "initialize":
            requested = str(params.get("protocolVersion") or PROTOCOL_VERSION)
            result = {
                "protocolVersion": requested if requested.startswith("202") else PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
                "instructions": "先用 tak_status 确认桥已启动；查询类工具可直接调用；"
                                "写入类工具第一次返回 dry-run 预览与 confirm_token，"
                                "把预览展示给用户确认后再带 token 调用。",
            }
        elif method in ("notifications/initialized", "notifications/cancelled",
                        "initialized"):
            return None
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            tools, status = bridge_tools()
            result = {"tools": tools, "bridge_running": bool(status.get("running")),
                      "allow_write": bool(status.get("allow_write"))}
        elif method == "tools/call":
            return _response(request_id, _handle_tools_call(params)) \
                if not notification else None
        elif method in ("resources/list", "prompts/list"):
            key = "resources" if method.startswith("resources") else "prompts"
            result = {key: []}
        elif method == "shutdown":
            result = {}
        else:
            if notification:
                return None
            return _error(request_id, -32601, "Method not found: %s" % method)
    except Exception as exc:  # 单条消息失败不能杀掉整个 stdio 循环
        _stderr("dispatch error: %s\n%s" % (exc, traceback.format_exc()[-800:]))
        if notification:
            return None
        return _error(request_id, -32603, "%s: %s" % (type(exc).__name__, exc))
    if notification:
        return None
    return _response(request_id, result)


def _response(request_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


def _write(sink: Any, payload: Any) -> bool:
    """写一条协议消息，返回 False 表示管道已断应退出循环。

    输出强制 ``ensure_ascii=True``：Windows 下 stdout 可能是 GBK，中文或特殊
    字符会抛 UnicodeEncodeError；一旦抛在写入处，客户端就永远等不到这一帧，
    表现和「initialize 超时」一模一样。JSON 转义后仍是合法 UTF-8 文本，
    客户端解析完照常显示中文。
    """
    try:
        sink.write(json.dumps(payload, ensure_ascii=True, default=str) + "\n")
        sink.flush()
        return True
    except (BrokenPipeError, OSError, UnicodeError, ValueError, TypeError):
        return False


def serve(stdin: Any = None, stdout: Any = None) -> int:
    """stdio 主循环。每行一个 JSON 对象，直到 EOF。

    启动阶段的任何异常都不能让进程静默退出 —— 客户端只会看到 initialize 超时。
    """
    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout
    try:
        url = (tak_bridge.bridge_status() or {}).get("url") or "not running"
    except Exception as exc:  # 发现文件损坏等，不影响协议本身
        url = "unknown"
        _stderr("bridge_status 失败（不影响协议）: %s" % exc)
    _stderr("TAK MCP server %s ready (bridge=%s)" % (__version__, url))
    while True:
        try:
            line = source.readline()
        except (KeyboardInterrupt, OSError, UnicodeError):
            break
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            if not _write(sink, _error(None, -32700, "Parse error")):
                break
            continue
        try:
            answer = handle(request)
        except Exception as exc:  # 单条消息失败必须继续服务下一条
            _stderr("handle 异常: %s\n%s" % (exc, traceback.format_exc()[-800:]))
            answer = _error(None, -32603, "Internal error: %s" % type(exc).__name__)
        if answer is None:
            continue
        if not _write(sink, answer):
            break
    return 0


#: 能执行 ``-m tak.mcp`` 的解释器文件名前缀；其余一律视为 DCC 宿主。
_PYTHON_EXE_PREFIXES = ("python", "pypy", "mayapy")
#: 显式排除的宿主进程，避免名字碰巧带 python 的宿主被误判。
_DCC_EXE_NAMES = frozenset({
    "maya.exe", "3dsmax.exe", "motionbuilder.exe", "unrealeditor.exe",
    "unrealeditor-cmd.exe", "blender.exe", "houdini.exe",
})


def _is_python_exe(path: str) -> bool:
    name = os.path.basename(str(path or "")).lower()
    if not name or name in _DCC_EXE_NAMES:
        return False
    return any(name.startswith(prefix) for prefix in _PYTHON_EXE_PREFIXES)


def python_executable() -> Tuple[str, str]:
    """返回能启动 MCP server 的 Python 解释器，以及它的来源说明。

    **为什么不能直接用 ``sys.executable``**：面板里点「MCP 配置」时这段代码
    跑在 Maya 进程内，``sys.executable`` 是 ``maya.exe``。把它写进 mcpServers
    会让客户端去启动 Maya GUI，永远等不到 ``initialize`` 响应 —— 表现为
    「MCP initialize timed out」。DCC 宿主一律跳过，按可用性逐级探测。

    探测顺序：显式 ``TAK_MCP_PYTHON`` → 当前解释器 → ``sys._base_executable``
    → PATH → Windows ``py -3`` → DCC 自带解释器（mayapy，较慢，仅兜底）
    → 裸 ``python``（交给客户端 PATH 解析）。只做路径/名称检查，不启动子进程，
    点一下配置按钮不该卡住 DCC 界面。
    """
    override = os.environ.get("TAK_MCP_PYTHON", "").strip().strip('"')
    if override and os.path.isfile(override):
        return override, "TAK_MCP_PYTHON"

    current = str(sys.executable or "")
    if _is_python_exe(current) and os.path.isfile(current):
        return current, "当前解释器"

    base = str(getattr(sys, "_base_executable", "") or "")
    if _is_python_exe(base) and os.path.isfile(base):
        return base, "sys._base_executable"

    import shutil
    for name in ("python", "python3"):
        found = shutil.which(name) or ""
        if _is_python_exe(found) and os.path.isfile(found):
            return found, "PATH: %s" % name

    launcher = shutil.which("py") or ""
    if launcher:
        try:
            import subprocess
            out = subprocess.run(
                [launcher, "-3", "-c", "import sys; print(sys.executable)"],
                capture_output=True, text=True, timeout=5)
            lines = [line.strip() for line in (out.stdout or "").splitlines()
                     if line.strip()]
            found = lines[-1] if out.returncode == 0 and lines else ""
            if _is_python_exe(found) and os.path.isfile(found):
                return found, "py -3"
        except Exception as exc:  # pragma: no cover - 平台相关
            _stderr("py launcher 探测失败: %s" % exc)

    folder = os.path.dirname(current)
    for name in ("mayapy.exe", "mayapy", "python.exe", "python"):
        sibling = os.path.join(folder, name)
        if _is_python_exe(sibling) and os.path.isfile(sibling):
            return sibling, "DCC 自带解释器（启动较慢）"

    return "python", "未探测到绝对路径，交给客户端 PATH 解析"


def repo_root() -> str:
    """仓库根目录（``tak/mcp/server.py`` 往上三级）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def entry_script() -> str:
    """自包含启动器的绝对路径；文件缺失时返回空串。

    v0.7.2 起优先用 ``scripts/tak_mcp_entry.py`` 而不是 ``-m tak.mcp``：
    启动器会在 import tak 之前补齐 ``PYTHONPATH`` / ``USERPROFILE`` /
    ``TAK_HOME``，所以即使客户端把 ``env`` 全量替换成空对象，或配置被
    手工改坏、重新 Trust 时带出旧值，server 仍能起来并连上桥。
    """
    path = os.path.join(repo_root(), "scripts", "tak_mcp_entry.py")
    return path if os.path.isfile(path) else ""


def config_snippet() -> Dict[str, Any]:
    """给 WorkBuddy / Claude Desktop 的 ``mcpServers`` 片段。

    ``command`` 由 :func:`python_executable` 决定，**绝不能是 DCC 宿主进程**。
    ``args`` 优先指向 :func:`entry_script`（自举 env），文件不存在才退回
    ``-m tak.mcp``。``env`` 三键是"正常路径"的显式声明，启动器还会再兜一次底，
    因此这几个键被删掉或写错也不会退化成「桥未启动」。
    """
    root = repo_root()
    exe, _source = python_executable()
    entry = entry_script()
    command_args = [entry] if entry else ["-m", "tak.mcp"]
    return {
        "mcpServers": {
            "tak-scene": {
                "command": exe,
                "args": command_args,
                "env": {
                    "PYTHONPATH": root,
                    "PYTHONIOENCODING": "utf-8",
                    "TAK_HOME": tak_config.config_home(),
                },
            }
        }
    }


def diagnose_config(entry: Dict[str, Any]) -> List[str]:
    """体检一条 mcpServers 配置，返回人类可读的问题列表（空列表 = 没问题）。

    给一键安装脚本、自检脚本和面板用：把「initialize 超时」和「桥未启动」
    这两类现象直接对应到具体字段上，而不是让人去翻客户端日志。
    """
    problems: List[str] = []
    command = str(entry.get("command") or "")
    if not command:
        problems.append("command 为空")
    elif not _is_python_exe(command):
        problems.append(
            "command=%r 是 DCC 宿主或不是 Python 解释器，客户端会一直等不到 "
            "initialize；必须换成普通 python.exe" % command)
    elif not os.path.isfile(command):
        problems.append("command 指向的文件不存在：%s" % command)

    args = [str(item) for item in (entry.get("args") or [])]
    if not args:
        problems.append("args 为空，server 不会进入协议循环")
    elif not any(item == "-m" or item.endswith("tak_mcp_entry.py") for item in args):
        problems.append("args 既没有 -m tak.mcp 也没有 tak_mcp_entry.py：%r" % args)

    use_entry = any(item.endswith("tak_mcp_entry.py") for item in args)
    if use_entry:
        for item in args:
            if item.endswith("tak_mcp_entry.py") and not os.path.isfile(item):
                problems.append("启动器文件不存在：%s" % item)
    else:
        for item in args:
            if item.endswith(".py") and not os.path.isfile(item):
                problems.append("args 指向的脚本不存在：%s" % item)

    env = entry.get("env") or {}
    if not isinstance(env, dict):
        problems.append("env 不是对象")
        env = {}
    # 用了启动器时 env 全空也没关系：启动器会自己补 TAK_HOME / USERPROFILE。
    if not use_entry and not str(env.get("TAK_HOME") or "").strip():
        problems.append("env 缺 TAK_HOME；客户端若全量替换环境，"
                        "expanduser('~') 会退化成字面 '~'，工具一律回「桥未启动」")
    return problems


__all__ = ["PROTOCOL_VERSION", "bridge_tools", "config_snippet", "diagnose_config",
           "entry_script", "handle", "python_executable", "repo_root", "serve"]
