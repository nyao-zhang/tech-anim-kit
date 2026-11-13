# -*- coding: utf-8 -*-
"""DCC 侧本地桥：把「场景查询 / 工具运行 / 对话」暴露给外部 AI 调用。

为什么是 DCC 当服务端
--------------------
反向做不到：Maya / Max / MotionBuilder / UE 都是**被启动**的进程，外部 AI 没有
稳定的办法「进到」它们里面执行 Python。所以桥必须由 DCC 侧监听回环地址，外部
用 HTTP 调进来。这也是 DCC 插件生态里唯一稳定的做法（REST 插件、命令端口、
socket server 都是这个形状）。

安全边界（四条，缺一条就不该开这个端口）
--------------------------------------
1. **只绑回环**：``127.0.0.1``，不接受局域网访问，``host`` 不做成配置项；
2. **必须带 token**：首次启动随机生成 32 字节，落盘 ``~/.tak/bridge.json``，
   请求头 ``X-TAK-Token`` 校验；``/bridge/health`` 不需要 token 但**不返回任何
   场景数据**，只回版本号，方便探测「有没有开」；
3. **只读为主**：``/bridge/run`` 只跑工具插件，而插件契约里 ``run_report`` 阶段
   本身不写场景；写操作只能走 ``/bridge/chat`` 背后的 confirm 流程，那条路径
   仍然要白名单 + dry-run + 权限 + 人工确认；
4. **审计**：每次调用都记 ``method path status 耗时 client``，日志进
   ``~/.tak/bridge.log``（滚动到 512 KB 后清空重写）。

宿主线程约束
------------
``maya.cmds`` / ``pymxs`` / ``unreal`` 都**只能在宿主主线程调用**。HTTP 请求跑在
自己的线程里，直接调宿主 API 轻则报错重则崩进程。因此所有宿主访问都过
:meth:`BridgeServer._host_call`，而执行器由**适配器层**注册（Maya 用
``maya.utils.executeInMainThreadWithResult``）—— ``tak/core`` 保持零 DCC import。
没注册执行器的宿主退回直调，并在 :meth:`status` 里标 ``thread_safe=False``，
让调用方自己决定要不要用。
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple

from tak.core import config as tak_config

BRIDGE_VERSION = "1.0"
DEFAULT_HOST = "127.0.0.1"
DISCOVERY_FILE = "bridge.json"
LOG_FILE = "bridge.log"
_LOG_LIMIT = 512 * 1024
_TOKEN_BYTES = 32

class BridgeBusy(RuntimeError):
    """宿主主线程没有在超时内处理这次请求。"""


_SERVER: Optional["BridgeServer"] = None
_SERVER_LOCK = threading.RLock()

#: dcc -> 主线程执行器。由适配器层注册（见 :func:`register_executor`）。
_EXECUTORS: Dict[str, Callable[..., Any]] = {}
_EXECUTOR_NOTES: Dict[str, str] = {}


# --------------------------------------------------------------------------- #
# 发现文件与 token
# --------------------------------------------------------------------------- #
def discovery_path() -> str:
    return os.path.join(tak_config.config_home(), DISCOVERY_FILE)


def log_path() -> str:
    return os.path.join(tak_config.config_home(), LOG_FILE)


def ensure_token(data: Optional[Dict[str, Any]] = None) -> str:
    """取配置里的 token；没有就生成一个并写回配置（原子替换）。"""
    cfg = data if isinstance(data, dict) else tak_config.load()
    bridge = cfg.get("bridge") if isinstance(cfg.get("bridge"), dict) else {}
    token = str(bridge.get("token") or "").strip()
    if token:
        return token
    token = secrets.token_hex(_TOKEN_BYTES)
    bridge = dict(bridge)
    bridge["token"] = token
    cfg = dict(cfg)
    cfg["bridge"] = bridge
    tak_config.save(cfg)
    return token


def load_discovery() -> Dict[str, Any]:
    """读发现文件；不存在/坏 JSON 都返回空字典，调用方据此判断「没开」。"""
    path = discovery_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_discovery(payload: Dict[str, Any]) -> None:
    path = discovery_path()
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
    os.replace(temp, path)


def _clear_discovery() -> None:
    path = discovery_path()
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _log(line: str) -> None:
    """单行审计日志；超过上限直接清空重写，不引入第三方轮转库。"""
    try:
        path = log_path()
        if os.path.isfile(path) and os.path.getsize(path) > _LOG_LIMIT:
            open(path, "w", encoding="utf-8").close()
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 宿主主线程调度
# --------------------------------------------------------------------------- #
def register_executor(dcc: str, func: Callable[..., Any], note: str = "") -> None:
    """登记「把函数切回宿主主线程」的执行器。

    为什么不让桥自己去 ``import maya.utils``：``tak/core`` 的架构不变量是
    **零 DCC import**（``tests/test_py39_compat.py`` 在守这条）。执行器由适配器
    层在导入时注册 —— 谁碰宿主，谁提供线程切换。
    """
    _EXECUTORS[str(dcc or "maya")] = func
    _EXECUTOR_NOTES[str(dcc or "maya")] = str(note or "")


def _main_thread_executor(dcc: str) -> Tuple[Optional[Callable[..., Any]], str]:
    """返回 (执行器, 说明)。执行器为 None 表示只能直调。"""
    func = _EXECUTORS.get(str(dcc or "maya"))
    if func is None:
        return None, ("%s 未注册主线程执行器（宿主调用将直调，跨线程时不安全）"
                      % dcc)
    return func, _EXECUTOR_NOTES.get(str(dcc or "maya"), "")


# --------------------------------------------------------------------------- #
# 服务端
# --------------------------------------------------------------------------- #
class BridgeServer:
    """回环 HTTP 桥。生命周期由 :func:`start_bridge` / :func:`stop_bridge` 管理。"""

    def __init__(self, dcc: str = "maya", port: int = 0, token: str = "",
                 adapter: Any = None,
                 executor: Optional[Callable[..., Any]] = None,
                 call_timeout: float = 15.0) -> None:
        self.dcc = str(dcc or "maya")
        self.requested_port = int(port or 0)
        self.token = str(token or "")
        self.adapter = adapter
        self._executor = executor
        self.call_timeout = float(call_timeout or 15.0)
        self._thread_note = ""
        self._httpd: Any = None
        self._thread: Optional[threading.Thread] = None
        self.started_at = ""
        self.port = 0
        self.calls = 0
        self.last_error = ""

    # ------------------------------------------------------------ 生命周期
    def start(self) -> Dict[str, Any]:
        if not self.token:
            self.token = ensure_token()
        if self._executor is None:
            self._executor, self._thread_note = _main_thread_executor(self.dcc)
        global _ACTIVE
        _ACTIVE = self
        try:
            self._httpd = ThreadingHTTPServer((DEFAULT_HOST, self.requested_port),
                                              _BridgeHandler)
        except OSError as exc:
            self.last_error = "端口无法监听: %s" % exc
            return {"ok": False, "error": self.last_error}
        self._httpd.daemon_threads = True
        self.port = int(self._httpd.server_address[1])
        self.started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name="tak-bridge", daemon=True)
        self._thread.start()
        payload = self.status()
        payload["ok"] = True
        _write_discovery(payload)
        _log("START %s port=%d" % (self.dcc, self.port))
        return payload

    def stop(self) -> Dict[str, Any]:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception as exc:
                self.last_error = str(exc)
            self._httpd = None
        if self._thread is not None:
            self._thread = None
        _clear_discovery()
        _log("STOP %s port=%d" % (self.dcc, self.port))
        return {"ok": True, "stopped": True, "port": self.port}

    def status(self) -> Dict[str, Any]:
        running = self._httpd is not None
        return {
            "running": running,
            "dcc": self.dcc,
            "host": DEFAULT_HOST,
            "port": self.port,
            "url": self.url(),
            "endpoints": list(ENDPOINTS),
            "bridge_version": BRIDGE_VERSION,
            "started_at": self.started_at,
            "calls": self.calls,
            "call_timeout": self.call_timeout,
            "thread_safe": self._executor is not None,
            "thread_note": self._thread_note,
            "discovery": discovery_path(),
            "last_error": self.last_error,
        }

    def url(self) -> str:
        return "http://%s:%d" % (DEFAULT_HOST, self.port or self.requested_port)

    # ------------------------------------------------------------ 宿主调用
    def _host_call(self, func: Callable[[], Any]) -> Any:
        """把宿主访问切回主线程；没有执行器时直调（并在 status 里声明不可靠）。

        **为什么要带超时**：``executeInMainThreadWithResult`` 是把函数塞进宿主
        的 idle 队列，主线程必须回到事件循环才会执行它。如果主线程正卡在某个
        阻塞调用里（模态对话框、长计算，或者——真机踩过的——在 Maya 脚本编辑器
        里同步调本桥），队列永远不被处理，HTTP 请求就会一直挂着。挂住比报错
        更难排查，所以这里硬超时，返回一个能读懂的 503。

        超时后那次排队调用仍可能在主线程空下来时补跑一次；这些动作都是只读
        查询，补跑不产生副作用。
        """
        self.calls += 1
        if self._executor is None:
            return func()

        result: Dict[str, Any] = {}

        def _worker() -> None:
            try:
                result["value"] = self._executor(func)
            except Exception as exc:  # 执行器内部异常原样转述
                result["error"] = exc

        thread = threading.Thread(target=_worker, name="tak-bridge-host",
                                  daemon=True)
        thread.start()
        thread.join(self.call_timeout)
        if thread.is_alive():
            raise BridgeBusy(
                "宿主主线程 %g 秒内没有处理这次请求（可能被模态对话框或长计算"
                "占住；也可能是在宿主自己的脚本里同步调用了本桥）。"
                % self.call_timeout)
        if "error" in result:
            raise result["error"]
        return result.get("value")

    def _adapter(self) -> Any:
        if self.adapter is not None:
            return self.adapter
        from tak.core import runner
        return runner.adapter_for(self.dcc)

    # ------------------------------------------------------------ 业务动作
    def scene(self, full: bool = False) -> Dict[str, Any]:
        """场景摘要。默认不返回全部关节，避免一次请求把大骨架整个吐出去。"""
        def _capture() -> Dict[str, Any]:
            adapter = self._adapter()
            getter = getattr(adapter, "get_skeleton_snapshot", None)
            if not callable(getter):
                return {}
            value = getter()
            return dict(value) if isinstance(value, dict) else {}

        snapshot = self._host_call(_capture) or {}
        if not snapshot:
            return {"ok": False,
                    "error": "当前适配器没有返回快照（%s）" % self.dcc}
        joints = snapshot.get("joints") or {}
        metadata = snapshot.get("metadata") or {}
        names = sorted(str(name) for name in joints)
        payload: Dict[str, Any] = {
            "ok": True,
            "dcc": snapshot.get("dcc") or self.dcc,
            "version": snapshot.get("version") or "",
            "unit": snapshot.get("unit") or "",
            "up_axis": snapshot.get("up_axis") or "",
            "frame_range": snapshot.get("frame_range"),
            "joint_count": len(names),
            "mesh_count": (metadata or {}).get("mesh_count"),
            "skin_mesh_count": (metadata or {}).get("skin_mesh_count"),
            "scene": (metadata or {}).get("scene") or "",
            "degraded": (metadata or {}).get("degraded") or "",
            "health_keys": sorted(str(key) for key in (metadata.get("health") or {})
                                  if not str(key).startswith("_")),
            "joints_preview": names[:40],
            "joints_truncated": max(0, len(names) - 40),
        }
        if full:
            payload["joints"] = joints
            payload["skin"] = snapshot.get("skin")
            payload["metadata"] = metadata
        return payload

    def tools(self) -> Dict[str, Any]:
        from tak.core import runner
        items = runner.list_tools(dcc=self.dcc)
        return {"ok": True, "count": len(items),
                "tools": [{"id": item.get("id"), "name": item.get("name"),
                           "summary": item.get("summary")}
                          for item in items]}

    def run(self, tool_id: str, options: Optional[Dict[str, Any]] = None,
            snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """跑一个工具插件并回摘要。插件契约保证 run_report 阶段不写场景。"""
        from tak.core import runner
        tool_id = str(tool_id or "").strip()
        if not tool_id:
            return {"ok": False, "error": "缺少 tool_id"}
        visible = {item.get("id") for item in runner.list_tools(dcc=self.dcc)}
        if tool_id not in visible:
            return {"ok": False,
                    "error": "工具 %s 不在当前 DCC 的可见工具列表里" % tool_id,
                    "available": sorted(visible)}
        if snapshot is None:
            # 先在本线程拿到快照再交给 runner：run_tool 内部若自己取快照，
            # 宿主调用就发生在 HTTP 线程上了（Maya 里会直接报错）。
            snapshot = self._raw_snapshot()
        result = runner.run_tool(tool_id, snapshot, dcc=self.dcc,
                                 adapter=self._adapter(),
                                 options=dict(options or {}))
        report = result.report
        findings = []
        for item in list(report.findings)[:50]:
            severity = getattr(item.severity, "value", str(item.severity))
            findings.append({"rule_id": item.rule_id, "severity": severity,
                             "message": item.message,
                             "node": getattr(item, "node", "")})
        return {"ok": True, "tool_id": tool_id,
                "passed": bool(getattr(report, "passed", False)),
                "title": getattr(report, "title", ""),
                "finding_count": len(report.findings),
                "findings": findings,
                "metadata": {key: value for key, value in
                             (getattr(report, "metadata", {}) or {}).items()
                             if key in ("checks_selected", "health_gaps",
                                        "scope", "duration_ms")}}

    def _raw_snapshot(self) -> Optional[Dict[str, Any]]:
        def _capture() -> Dict[str, Any]:
            getter = getattr(self._adapter(), "get_skeleton_snapshot", None)
            if not callable(getter):
                return {}
            value = getter()
            return dict(value) if isinstance(value, dict) else {}
        return self._host_call(_capture) or None

    def chat(self, message: str, session_id: str = "bridge") -> Dict[str, Any]:
        """对话（只读）。写场景仍然只能在 DCC 里人工确认，桥不开放 confirm。"""
        message = str(message or "").strip()
        if not message:
            return {"ok": False, "error": "缺少 message"}
        from tak.core.agent.session import AgentSession
        snapshot = self._raw_snapshot() or {}
        session = AgentSession(session_id=session_id)
        turn = session.chat(message, snapshot=snapshot)
        return {"ok": True, "session_id": session_id,
                "turn": turn if isinstance(turn, dict) else str(turn),
                "note": "桥只提供只读对话；写场景请在 DCC 面板内确认执行。"}


ENDPOINTS: Tuple[str, ...] = (
    "GET  /bridge/health",
    "GET  /bridge/tools",
    "GET  /bridge/scene[?full=1]",
    "POST /bridge/run     {tool_id, options?}",
    "POST /bridge/chat    {message, session_id?}",
)


# --------------------------------------------------------------------------- #
# 请求处理
# --------------------------------------------------------------------------- #
_ACTIVE: Optional[BridgeServer] = None


def active() -> Optional[BridgeServer]:
    return _ACTIVE


# 说明：handler 直接继承 BaseHTTPRequestHandler。所有路由状态都从模块级
# ``_ACTIVE`` 取，handler 自己不留实例状态 —— 每个请求都是新实例。
class _BridgeHandler(BaseHTTPRequestHandler):
    server_version = "TAKBridge/" + BRIDGE_VERSION

    def log_message(self, fmt: str, *fmt_args: Any) -> None:  # noqa: N802
        return  # 审计走 _log，不要 stderr 噪声

    # ---------------------------------------------------------- 工具
    def _send(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        server = _ACTIVE
        if server is None or not server.token:
            return False
        supplied = self.headers.get("X-TAK-Token", "") or ""
        if not supplied:
            query = self.path.partition("?")[2]
            for part in query.split("&"):
                key, _, value = part.partition("=")
                if key == "token":
                    supplied = value
        return secrets.compare_digest(str(supplied), server.token)

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _dispatch(self, method: str) -> None:
        server = _ACTIVE
        if server is None:
            self._send(503, {"ok": False, "error": "桥未启动"})
            return
        route = self.path.partition("?")[0].rstrip("/") or "/"
        query = self.path.partition("?")[2]
        started = time.time()
        if route == "/bridge/health":
            self._send(200, {"ok": True, "bridge": BRIDGE_VERSION,
                             "dcc": server.dcc,
                             "running": server._httpd is not None,
                             "auth_required": True})
            _log("GET /bridge/health 200 %.0fms %s"
                 % ((time.time() - started) * 1000, self.client_address[0]))
            return
        if not self._authorized():
            self._send(401, {"ok": False,
                             "error": "缺少或错误的 X-TAK-Token"})
            _log("%s %s 401 %s" % (method, route, self.client_address[0]))
            return
        try:
            payload = self._route(server, method, route, query)
            status = 200 if payload.get("ok") else 400
        except BridgeBusy as exc:
            payload = {"ok": False, "error": str(exc), "retry_after_ms": 2000}
            status = 503
        except Exception as exc:  # 任何异常都不把 traceback 抛给调用方
            payload = {"ok": False,
                       "error": "%s: %s" % (type(exc).__name__, exc)}
            status = 500
        self._send(status, payload)
        _log("%s %s %d %.0fms %s" % (method, route, status,
                                     (time.time() - started) * 1000,
                                     self.client_address[0]))

    def _route(self, server: "BridgeServer", method: str, route: str,
               query: str) -> Dict[str, Any]:
        if route == "/bridge/tools" and method == "GET":
            return server.tools()
        if route == "/bridge/scene" and method == "GET":
            full = "full=1" in query or "full=true" in query
            return server.scene(full=full)
        if route == "/bridge/run" and method == "POST":
            body = self._body()
            return server.run(body.get("tool_id"),
                              body.get("options"),
                              body.get("snapshot"))
        if route == "/bridge/chat" and method == "POST":
            body = self._body()
            return server.chat(body.get("message"),
                               str(body.get("session_id") or "bridge"))
        return {"ok": False,
                "error": "未知路由 %s %s（可用：%s）"
                         % (method, route, ", ".join(ENDPOINTS))}

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")


# --------------------------------------------------------------------------- #
# 进程级开关
# --------------------------------------------------------------------------- #
def start_bridge(dcc: str = "maya", port: Optional[int] = None,
                 adapter: Any = None,
                 call_timeout: float = 15.0) -> Dict[str, Any]:
    """启动（或复用）桥。端口取配置 ``bridge.port``，0 表示自动选一个空闲端口。"""
    global _SERVER
    with _SERVER_LOCK:
        if _SERVER is not None and _SERVER._httpd is not None:
            payload = _SERVER.status()
            payload["ok"] = True
            payload["reused"] = True
            return payload
        cfg = tak_config.load()
        bridge_cfg = cfg.get("bridge") if isinstance(cfg.get("bridge"), dict) else {}
        selected_port = int(port if port is not None
                            else bridge_cfg.get("port") or 0)
        token = ensure_token(cfg)
        _SERVER = BridgeServer(dcc=dcc, port=selected_port, token=token,
                               adapter=adapter, call_timeout=call_timeout)
        payload = _SERVER.start()
        payload["reused"] = False
        return payload


def stop_bridge() -> Dict[str, Any]:
    global _SERVER
    with _SERVER_LOCK:
        if _SERVER is None:
            return {"ok": True, "stopped": False, "reason": "桥未启动"}
        payload = _SERVER.stop()
        _SERVER = None
        return payload


def bridge_status() -> Dict[str, Any]:
    if _SERVER is not None:
        payload = _SERVER.status()
        payload["owner"] = "当前进程"
        return payload
    discovery = load_discovery()
    if not discovery:
        return {"running": False, "owner": "", "dcc": "",
                "url": "", "endpoints": list(ENDPOINTS),
                "discovery": discovery_path(),
                "hint": "桥未启动。在 DCC 面板的「对话控制台 → 桥接」里启动，"
                        "或用 tak.start_bridge()。"}
    discovery = dict(discovery)
    discovery["owner"] = "其它进程"
    return discovery


def call(path: str, method: str = "GET", body: Optional[Dict[str, Any]] = None,
         timeout: float = 30.0, discovery: Optional[Dict[str, Any]] = None,
         token: Optional[str] = None) -> Dict[str, Any]:
    """客户端：按发现文件找到桥并调一次。供文档示例与 WorkBuddy 侧参考实现。

    ``token`` 显式传入时优先于配置文件 —— 排查「401」时可以直接换一个错的试试。
    """
    import urllib.error
    import urllib.request

    found = discovery if isinstance(discovery, dict) else load_discovery()
    base = str(found.get("url") or "").rstrip("/")
    if token is None:
        bridge_cfg = tak_config.load().get("bridge")
        token = str((bridge_cfg or {}).get("token") or "") \
            if isinstance(bridge_cfg, dict) else ""
    if not base:
        return {"ok": False, "error": "发现文件不存在，桥未启动"}
    if not token:
        return {"ok": False, "error": "配置里没有 bridge.token"}
    url = base + (path if path.startswith("/") else "/" + path)
    data = None
    headers = {"X-TAK-Token": token}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            payload.setdefault("ok", True)
            payload["status"] = response.status
            return payload
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"ok": False, "error": raw[:200]}
        payload["status"] = exc.code
        return payload
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def main(argv: Optional[List[str]] = None) -> int:
    """``python -m tak.core.bridge status|scene|tools|run|chat`` 便于手工排查。"""
    import sys
    args = list(argv if argv is not None else sys.argv[1:])
    action = args[0] if args else "status"
    if action == "status":
        payload = bridge_status()
    elif action == "scene":
        payload = call("/bridge/scene" + ("?full=1" if "full" in args else ""))
    elif action == "tools":
        payload = call("/bridge/tools")
    elif action == "run" and len(args) > 1:
        payload = call("/bridge/run", "POST", {"tool_id": args[1]})
    elif action == "chat" and len(args) > 1:
        payload = call("/bridge/chat", "POST", {"message": " ".join(args[1:])})
    else:
        payload = {"ok": False, "error": "用法: status|scene|tools|run <tool_id>|chat <文本>"}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    # status 是诊断命令：桥没开也算查到了状态，退出码应为 0，否则脚本里
    # `python -m tak.core.bridge status || echo 没开` 这种写法会误判。
    return 0 if payload.get("ok") or action == "status" else 1


if __name__ == "__main__":
    raise SystemExit(main())
