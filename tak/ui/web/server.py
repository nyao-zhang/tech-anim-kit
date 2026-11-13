# -*- coding: utf-8 -*-
"""TAK Web 面板服务：给 UE 内嵌浏览器 / 本地浏览器用的单页工具面板。

设计约束
--------
* **只依赖标准库**：``http.server`` / ``urllib`` / ``json`` / ``argparse``，
  零第三方依赖，能直接跑在 UE 的内置 Python 里；
* **页面完全离线**：不含 CDN、外部字体、外部图片与第三方 JS 框架，
  颜色一律取自 ``tak/ui/theme.css`` 暴露的 ``var(--tak-*)``；
* **单一事实源**：所有判定都走 ``tak.core.runner``，Web 端不自己下结论；
* **异常不外泄**：任何异常都返回 ``{"ok": false, "error": "..."}``，
  traceback 只写服务端日志，绝不发给浏览器。

对外接口
--------
``create_server(host, port, repo_root)``
    只创建并绑定 ``ThreadingHTTPServer``（``port=0`` 表示随机端口），不进入
    消息循环，便于单测里 ``server_close()`` 直接释放。
``serve(host, port, ue)``
    创建服务并阻塞运行；``ue=True`` 时强制绑定 127.0.0.1 并打印可直接粘进
    UE 内嵌浏览器的地址。
``handle_api(method, path, body, query, repo_root)``
    **纯函数**版 API 处理器，返回 ``(status, payload)``，不占端口、不写
    socket，单测可直接调用。

命令行::

    python -m tak.ui.web.server --port 8765
    python -m tak.ui.web.server --ue --port 8765
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from tak.core import runner
from tak.version import __version__

# --------------------------------------------------------------------------- #
# 路径常量
# --------------------------------------------------------------------------- #
WEB_DIR = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.dirname(WEB_DIR)
PANEL_PATH = os.path.join(WEB_DIR, "panel.html")
APP_JS_PATH = os.path.join(WEB_DIR, "app.js")
APP_CSS_PATH = os.path.join(WEB_DIR, "app.css")
THEME_CSS_PATH = os.path.join(UI_DIR, "theme.css")

HTTP_SCHEME = "http"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DCC_LIST = ("maya", "max", "mobu", "unreal")
MAX_BODY_BYTES = 8 * 1024 * 1024

__all__ = [
    "create_server", "serve", "handle_api", "main",
    "PANEL_PATH", "APP_JS_PATH", "APP_CSS_PATH", "THEME_CSS_PATH",
    "WEB_DIR", "UI_DIR", "DCC_LIST",
]


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
class ApiError(Exception):
    """API 层错误：带 HTTP 状态码，message 直接返回给浏览器。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = int(status)
        self.message = str(message)


_ENV_LOCK = threading.Lock()


@contextlib.contextmanager
def _plugin_scope(repo_root: Optional[str]):
    """临时把 ``<repo_root>/tools`` 挂进插件搜索路径。

    ``tak.core.registry`` 通过环境变量 ``TAK_PLUGIN_PATH`` 发现外部插件目录；
    这里只在单次调用期间生效，并在退出时恢复原值，避免污染宿主进程。
    """
    if not repo_root:
        yield
        return
    extra = os.path.join(os.path.abspath(repo_root), "tools")
    if not os.path.isdir(extra):
        yield
        return
    with _ENV_LOCK:
        old = os.environ.get("TAK_PLUGIN_PATH", "")
        parts = [item for item in old.split(os.pathsep) if item]
        if extra not in parts:
            parts.insert(0, extra)
        os.environ["TAK_PLUGIN_PATH"] = os.pathsep.join(parts)
        try:
            yield
        finally:
            if old:
                os.environ["TAK_PLUGIN_PATH"] = old
            else:
                os.environ.pop("TAK_PLUGIN_PATH", None)


def _log(message: str) -> None:
    """服务端简短日志（只写 stderr，不污染 stdout 的数据输出）。"""
    try:
        sys.stderr.write("[TAK][web] %s\n" % message)
        sys.stderr.flush()
    except Exception:
        pass


def _first(value: Any) -> str:
    """取查询参数的首个值，兼容 ``str`` 与 ``list`` 两种形态。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return str(value)


def _parse_body(body: Any) -> Dict[str, Any]:
    """把请求体解析成 dict；非法 JSON 统一抛 400。"""
    if body is None:
        return {}
    if isinstance(body, dict):
        return dict(body)
    if isinstance(body, (list, tuple)):
        raise ApiError(400, "请求体必须是 JSON 对象")
    if isinstance(body, (bytes, bytearray)):
        raw = bytes(body).decode("utf-8", "replace")
    else:
        raw = str(body)
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ApiError(400, "请求体不是合法 JSON: %s" % exc)
    if not isinstance(data, dict):
        raise ApiError(400, "请求体必须是 JSON 对象")
    return data


def _snapshot_of(data: Dict[str, Any]) -> Dict[str, Any]:
    """取出 snapshot 字段并校验类型。"""
    snapshot = data.get("snapshot")
    if snapshot is None:
        return {}
    if not isinstance(snapshot, dict):
        raise ApiError(400, "snapshot 必须是 JSON 对象")
    return snapshot


def _detect_dcc() -> str:
    """猜测当前进程所处的 DCC；纯 Python 下返回 ``none``。"""
    for dcc, module in (("unreal", "unreal"), ("maya", "maya"),
                        ("max", "pymxs"), ("mobu", "pyfbsdk")):
        if module in sys.modules:
            return dcc
    return "none"


def _payload_of(result: Any) -> Dict[str, Any]:
    """把 ToolResult / Report 统一成可 JSON 序列化的 dict。"""
    if hasattr(result, "to_dict"):
        return dict(result.to_dict())
    if isinstance(result, dict):
        return dict(result)
    return {"result": str(result)}


# --------------------------------------------------------------------------- #
# API（纯函数）
# --------------------------------------------------------------------------- #
def _api_health() -> Dict[str, Any]:
    return {
        "ok": True,
        "version": __version__,
        "dcc": _detect_dcc(),
        "dccs": list(DCC_LIST),
        "service": "tak-web-panel",
    }


def _api_tools(query: Dict[str, Any], repo_root: Optional[str]) -> Dict[str, Any]:
    dcc = _first(query.get("dcc")).strip()
    with _plugin_scope(repo_root):
        tools = runner.list_tools(dcc=dcc or None)
    return {"ok": True, "dcc": dcc, "tools": tools, "count": len(tools)}


def _api_run(body: Any, repo_root: Optional[str]) -> Dict[str, Any]:
    data = _parse_body(body)
    tool_id = str(data.get("tool_id") or "").strip()
    if not tool_id:
        raise ApiError(400, "缺少 tool_id")
    dcc = str(data.get("dcc") or "maya").strip().lower() or "maya"
    snapshot = _snapshot_of(data)
    try:
        with _plugin_scope(repo_root):
            result = runner.run_tool(tool_id, snapshot, dcc=dcc)
    except runner.ToolNotFound as exc:
        raise ApiError(404, str(exc))
    except runner.ToolError as exc:
        raise ApiError(400, str(exc))
    payload = _payload_of(result)
    payload["ok"] = True
    return payload


def _api_evaluate(body: Any, repo_root: Optional[str]) -> Dict[str, Any]:
    data = _parse_body(body)
    dcc = str(data.get("dcc") or "maya").strip().lower() or "maya"
    snapshot = _snapshot_of(data)
    try:
        with _plugin_scope(repo_root):
            report = runner.evaluate(snapshot, dcc=dcc)
    except runner.ToolError as exc:
        raise ApiError(400, str(exc))
    payload = _payload_of(report)
    payload["ok"] = True
    return payload


def _api_report(query: Dict[str, Any], repo_root: Optional[str]) -> Dict[str, Any]:
    """附带 HTML / Markdown 渲染的完整报告。"""
    tool_id = _first(query.get("tool_id")).strip()
    dcc = _first(query.get("dcc")).strip().lower() or "maya"
    severity = _first(query.get("severity")).strip()
    with _plugin_scope(repo_root):
        if tool_id:
            try:
                result = runner.run_tool(tool_id, None, dcc=dcc)
            except runner.ToolNotFound as exc:
                raise ApiError(404, str(exc))
            except runner.ToolError as exc:
                raise ApiError(400, str(exc))
            report = result.report
        else:
            report = runner.evaluate(None, dcc=dcc)
    kwargs = {"severity": severity or None}
    payload = _payload_of(report)
    payload["ok"] = True
    payload["html"] = report.to_html(theme_href="/theme.css", **kwargs)
    payload["markdown"] = report.to_markdown(**kwargs)
    return payload


def handle_api(method: str, path: str, body: Any = None,
               query: Optional[Dict[str, Any]] = None,
               repo_root: Optional[str] = None) -> Tuple[int, Dict[str, Any]]:
    """处理 ``/api/*`` 请求，返回 ``(HTTP 状态码, JSON 负载)``。

    这是**纯函数**：不读写 socket、不打全局状态，单测可以随便调用。

    :param method: HTTP 方法（``GET`` / ``POST``）。
    :param path: 请求路径，可带查询串（``/api/tools?dcc=maya``）。
    :param body: 请求体；``dict`` 直接使用，``str`` / ``bytes`` 会被当 JSON 解析。
    :param query: 查询参数；``None`` 时从 ``path`` 里解析。
    :param repo_root: 仓库根目录，用于额外发现 ``<repo_root>/tools`` 插件。
    """
    method = str(method or "GET").upper()
    parsed = urlparse(str(path or "/"))
    route = parsed.path or "/"
    if query is None:
        query = parse_qs(parsed.query)
    try:
        if route == "/api/health":
            if method != "GET":
                raise ApiError(405, "该接口只接受 GET")
            return 200, _api_health()
        if route == "/api/tools":
            if method != "GET":
                raise ApiError(405, "该接口只接受 GET")
            return 200, _api_tools(query, repo_root)
        if route == "/api/run":
            if method != "POST":
                raise ApiError(405, "该接口只接受 POST")
            return 200, _api_run(body, repo_root)
        if route == "/api/evaluate":
            if method != "POST":
                raise ApiError(405, "该接口只接受 POST")
            return 200, _api_evaluate(body, repo_root)
        if route == "/api/report":
            if method != "GET":
                raise ApiError(405, "该接口只接受 GET")
            return 200, _api_report(query, repo_root)
        raise ApiError(404, "未知接口: %s" % route)
    except ApiError as exc:
        return exc.status, {"ok": False, "error": exc.message}
    except Exception as exc:  # 兜底：绝不把 traceback 暴露给浏览器
        _log("接口 %s %s 处理失败: %s: %s" % (method, route, type(exc).__name__, exc))
        return 500, {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


# --------------------------------------------------------------------------- #
# HTTP 服务
# --------------------------------------------------------------------------- #
_STATIC_FILES = {
    "/": ("text/html; charset=utf-8", PANEL_PATH),
    "/index.html": ("text/html; charset=utf-8", PANEL_PATH),
    "/theme.css": ("text/css; charset=utf-8", THEME_CSS_PATH),
    "/app.js": ("application/javascript; charset=utf-8", APP_JS_PATH),
    "/app.css": ("text/css; charset=utf-8", APP_CSS_PATH),
}


class _PanelHandler(BaseHTTPRequestHandler):
    """静态资源 + ``/api/*`` 的极简处理器。"""

    server_version = "TAKWebPanel/%s" % __version__
    protocol_version = "HTTP/1.1"
    repo_root: Optional[str] = None

    # -- 工具 ---------------------------------------------------------------
    def _send(self, status: int, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(payload)
            except Exception:
                pass

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", body)

    def _send_file(self, path: str, content_type: str) -> None:
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            _log("读取静态资源失败 %s: %s" % (path, exc))
            self._send_json(404, {"ok": False, "error": "资源不可用: %s" % os.path.basename(path)})
            return
        self._send(200, content_type, data)

    # -- 路由 ---------------------------------------------------------------
    def _handle_static(self, route: str) -> None:
        entry = _STATIC_FILES.get(route)
        if entry is None:
            # 不做目录浏览：未知路径一律 404 JSON。
            self._send_json(404, {"ok": False, "error": "未找到: %s" % route})
            return
        content_type, path = entry
        if not os.path.isfile(path):
            self._send_json(404, {"ok": False, "error": "资源缺失: %s" % os.path.basename(path)})
            return
        self._send_file(path, content_type)

    def do_GET(self) -> None:  # noqa: N802  (http.server 约定)
        parsed = urlparse(self.path)
        route = parsed.path or "/"
        if route.startswith("/api/"):
            status, payload = handle_api("GET", self.path, repo_root=self.repo_root)
            self._send_json(status, payload)
            return
        self._handle_static(route)

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path or "/"
        if not route.startswith("/api/"):
            self._send_json(404, {"ok": False, "error": "未找到: %s" % route})
            return
        raw = b""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length > MAX_BODY_BYTES:
            self._send_json(400, {"ok": False, "error": "请求体过大"})
            return
        if length > 0:
            try:
                raw = self.rfile.read(length)
            except Exception:
                raw = b""
        status, payload = handle_api("POST", self.path, body=raw,
                                     repo_root=self.repo_root)
        self._send_json(status, payload)

    def log_message(self, fmt: str, *args: Any) -> None:
        _log("%s %s" % (self.command, fmt % args))


def create_server(host: str = DEFAULT_HOST, port: int = 0,
                  repo_root: Optional[str] = None) -> ThreadingHTTPServer:
    """创建（并绑定）面板服务器，不进入消息循环。

    :param host: 监听地址，默认只监听本机。
    :param port: 监听端口，``0`` 表示由系统分配随机端口。
    :param repo_root: 仓库根目录，用于额外发现 ``<repo_root>/tools`` 插件。
    """
    handler = type("_BoundPanelHandler", (_PanelHandler,),
                   {"repo_root": os.path.abspath(repo_root) if repo_root else None})
    httpd = ThreadingHTTPServer((host, int(port)), handler)
    httpd.daemon_threads = True
    return httpd


def panel_url(host: str, port: int) -> str:
    """拼出面板地址（仅本机地址，用于打印到控制台）。"""
    if host in ("0.0.0.0", "::", ""):
        host = DEFAULT_HOST
    return "%s://%s:%d/" % (HTTP_SCHEME, host, int(port))


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
          ue: bool = False, repo_root: Optional[str] = None,
          open_browser: bool = False) -> None:
    """启动面板并阻塞运行，直到 Ctrl+C。

    :param ue: ``True`` 时强制绑定 127.0.0.1（UE 内嵌浏览器场景），
        并打印可直接粘贴的地址。
    """
    bind_host = DEFAULT_HOST if ue else (host or DEFAULT_HOST)
    httpd = create_server(host=bind_host, port=port, repo_root=repo_root)
    real_port = httpd.server_address[1]
    url = panel_url(bind_host, real_port)
    print("[TAK][web] 面板已启动: %s" % url)
    print("[TAK][web] 版本 %s · DCC %s" % (__version__, _detect_dcc()))
    if ue:
        print("[TAK][web] UE 内嵌浏览器地址: %s" % url)
        print("[TAK][web] 在 UE 里用 WebBrowserWidget 打开上面的地址即可。")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[TAK][web] 面板已停止。")
    finally:
        httpd.server_close()


def main(argv: Optional[Iterable[str]] = None) -> int:
    """命令行入口：``python -m tak.ui.web.server --port 8765``。"""
    parser = argparse.ArgumentParser(
        prog="python -m tak.ui.web.server",
        description="启动 TAK Web 工具面板（UE 内嵌浏览器 / 本地浏览器）")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help="监听地址（默认 %s，--ue 时强制本机）" % DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="监听端口（默认 %d，0 表示随机）" % DEFAULT_PORT)
    parser.add_argument("--ue", action="store_true",
                        help="UE 模式：只绑定 127.0.0.1 并打印内嵌浏览器地址")
    parser.add_argument("--no-browser", action="store_true",
                        help="启动后不自动打开本地浏览器")
    parser.add_argument("--repo-root", default=None,
                        help="仓库根目录，用于额外发现 tools/ 插件")
    args = parser.parse_args(list(argv) if argv is not None else None)
    serve(host=args.host, port=args.port, ue=args.ue,
          repo_root=args.repo_root, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    sys.exit(main())
