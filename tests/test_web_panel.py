# -*- coding: utf-8 -*-
"""Web 面板（tak.ui.web）回归测试。

覆盖点
------
* ``handle_api`` 纯函数：health / tools / evaluate / run 的正常与异常路径；
* golden fixtures：good 快照 ``passed=True``、bad 快照 ``passed=False``；
* 面板 HTML：主题类名、theme.css 引用、无硬编码颜色、无外部 URL；
* 真实端口只用 ``port=0`` 随机端口，并在 finally 里 shutdown。

刻意不改动任何既有文件，也不往仓库 ``tools/`` 里写真插件，
外部插件一律落在 tmp_path 下（与 test_runner.py 一致的做法）。
"""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.request

import pytest

from tak.core import runner
from tak.ui.web import server as web_server

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FIXTURES = os.path.join(_ROOT, "fixtures")
_WEB_DIR = os.path.join(_ROOT, "tak", "ui", "web")

# 颜色字面量：十六进制、颜色函数、常见具名颜色。
# 具名颜色后面接 "-" 时不算（white-space / border-color 这类属性名）。
_NAMED_COLORS = (r"white|black|red|blue|green|gray|grey|orange|purple|pink|"
                 r"yellow|cyan|magenta|silver|maroon|navy|teal|olive|lime|aqua|"
                 r"fuchsia|transparent|currentcolor")
_COLOR_PATTERNS = (
    re.compile(r"#[0-9a-fA-F]{3,8}\b"),
    re.compile(r"\brgba?\s*\("),
    re.compile(r"\bhsla?\s*\("),
    re.compile(r"\b(?:%s)(?![-\w])" % _NAMED_COLORS, re.IGNORECASE),
)
_EXTERNAL_URL = re.compile(r"https?://", re.IGNORECASE)

_FAKE_MANIFEST = {
    "id": "web_probe", "name": "面板探针", "dcc": ["maya", "unreal"],
    "min_version": {"maya": "2023", "unreal": "5.8"},
    "entry": "web_probe", "ui": "both", "permissions": ["scene.read"],
    "description": "Web 面板单测用的假工具",
}
_FAKE_CORE = '''# -*- coding: utf-8 -*-
from tak.core.rules.base import Finding, Severity
from tak.core.report import Report


def run_report(ctx):
    report = Report(title="面板探针", dcc=ctx.dcc)
    joints = (ctx.snapshot or {}).get("joints", {})
    report.add(Finding(rule_id="web_probe.joint_count", severity=Severity.INFO,
                        message="检测到 %d 根骨骼" % len(joints)))
    return report
'''


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _fixture(folder, name):
    return _read_json(os.path.join(_FIXTURES, folder, name))


@pytest.fixture
def fake_plugin(tmp_path, monkeypatch):
    """在临时目录放一个假插件并挂进插件搜索路径。"""
    root = tmp_path / "plugins"
    target = root / "web_probe"
    target.mkdir(parents=True)
    (target / "manifest.json").write_text(json.dumps(_FAKE_MANIFEST), encoding="utf-8")
    (target / "core.py").write_text(_FAKE_CORE, encoding="utf-8")
    monkeypatch.setenv("TAK_PLUGIN_PATH", str(root))
    runner.clear_cache()
    yield target
    runner.clear_cache()


# --------------------------------------------------------------------------- #
# handle_api：健康检查与工具列表
# --------------------------------------------------------------------------- #
def test_health_returns_ok_and_version():
    status, payload = web_server.handle_api("GET", "/api/health")
    assert status == 200
    assert payload["ok"] is True
    assert payload["version"]
    assert "dcc" in payload
    assert payload["dccs"] == ["maya", "max", "mobu", "unreal"]


def test_health_rejects_post():
    status, payload = web_server.handle_api("POST", "/api/health", body={})
    assert status == 405
    assert payload["ok"] is False
    assert payload["error"]


def test_tools_list_is_empty_without_plugins():
    status, payload = web_server.handle_api("GET", "/api/tools")
    assert status == 200
    assert payload["ok"] is True
    assert isinstance(payload["tools"], list)
    assert payload["count"] == len(payload["tools"])


def test_tools_list_filters_by_dcc(fake_plugin):
    status, payload = web_server.handle_api("GET", "/api/tools?dcc=maya")
    assert status == 200
    ids = [item["id"] for item in payload["tools"]]
    assert "web_probe" in ids
    entry = [item for item in payload["tools"] if item["id"] == "web_probe"][0]
    assert entry["name"] == "面板探针"
    assert entry["dcc"] == ["maya", "unreal"]
    assert entry["description"]


def test_tools_list_filters_out_other_dcc(fake_plugin):
    status, payload = web_server.handle_api("GET", "/api/tools?dcc=mobu")
    assert status == 200
    assert all("web_probe" not in item["id"] for item in payload["tools"])


# --------------------------------------------------------------------------- #
# handle_api：规范化验（golden fixtures）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["good_001.json", "good_007.json", "good_020.json"])
def test_evaluate_good_fixture_passes(name):
    fixture = _fixture("good", name)
    status, payload = web_server.handle_api(
        "POST", "/api/evaluate", body={"snapshot": fixture["snapshot"], "dcc": "maya"})
    assert status == 200
    assert payload["ok"] is True
    assert payload["passed"] is True
    assert payload["findings"] == []
    assert payload["counts"]["error"] == 0


@pytest.mark.parametrize("name", ["bad_001.json", "bad_010.json", "bad_020.json"])
def test_evaluate_bad_fixture_fails(name):
    fixture = _fixture("bad", name)
    status, payload = web_server.handle_api(
        "POST", "/api/evaluate", body={"snapshot": fixture["snapshot"], "dcc": "maya"})
    assert status == 200
    assert payload["ok"] is True
    assert payload["passed"] is False
    assert payload["counts"]["error"] + payload["counts"]["blocker"] > 0
    assert payload["findings"]


def test_evaluate_accepts_raw_json_body():
    fixture = _fixture("good", "good_001.json")
    body = json.dumps({"snapshot": fixture["snapshot"], "dcc": "maya"}, ensure_ascii=False)
    status, payload = web_server.handle_api("POST", "/api/evaluate", body=body)
    assert status == 200
    assert payload["passed"] is True


def test_evaluate_rejects_non_object_snapshot():
    status, payload = web_server.handle_api(
        "POST", "/api/evaluate", body={"snapshot": [1, 2, 3]})
    assert status == 400
    assert payload["ok"] is False
    assert "snapshot" in payload["error"]


# --------------------------------------------------------------------------- #
# handle_api：运行工具
# --------------------------------------------------------------------------- #
def test_run_tool_with_snapshot(fake_plugin):
    fixture = _fixture("good", "good_001.json")
    status, payload = web_server.handle_api("POST", "/api/run", body={
        "tool_id": "web_probe", "dcc": "maya", "snapshot": fixture["snapshot"]})
    assert status == 200
    assert payload["ok"] is True
    assert payload["tool_id"] == "web_probe"
    assert payload["plugin_id"] == "web_probe"
    assert payload["passed"] is True
    assert payload["duration_ms"] >= 0
    # 快照确实传到了插件内部（插件把骨骼数写进 finding 文本）。
    assert payload["findings"]
    assert "根骨骼" in payload["findings"][0]["message"]


def test_run_missing_tool_id_reports_error():
    status, payload = web_server.handle_api("POST", "/api/run", body={"dcc": "maya"})
    assert status == 400
    assert payload["ok"] is False
    assert "tool_id" in payload["error"]


def test_run_unknown_tool_returns_404():
    status, payload = web_server.handle_api("POST", "/api/run", body={"tool_id": "not_exist"})
    assert status == 404
    assert payload["ok"] is False
    assert "not_exist" in payload["error"]


def test_run_invalid_json_returns_400():
    status, payload = web_server.handle_api("POST", "/api/run", body="{not json")
    assert status == 400
    assert payload["ok"] is False
    assert "JSON" in payload["error"]


def test_unknown_api_route_returns_404():
    status, payload = web_server.handle_api("GET", "/api/nope")
    assert status == 404
    assert payload["ok"] is False


def test_report_route_returns_html_and_markdown(fake_plugin):
    status, payload = web_server.handle_api("GET", "/api/report?tool_id=web_probe&dcc=maya")
    assert status == 200
    assert payload["ok"] is True
    assert "tak-panel" in payload["html"]
    assert "# " in payload["markdown"]


# --------------------------------------------------------------------------- #
# 面板 HTML：类名、主题引用、离线自检
# --------------------------------------------------------------------------- #
def _read(name):
    with open(os.path.join(_WEB_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


def test_panel_html_uses_theme_classes():
    html = _read("panel.html")
    for token in ("tak-panel", "tak-header", "tak-meta", "tak-table",
                  "tak-badge", "tak-state", "tak-hint",
                  "tak-state-pass", "tak-state-fail"):
        assert token in html, "panel.html 缺少主题类名 %s" % token


def test_panel_html_references_theme_css_and_has_theme_toggle():
    html = _read("panel.html")
    assert "/theme.css" in html
    assert 'id="themeToggle"' in html
    assert 'data-theme="light"' in html


def test_panel_html_uses_result_row_classes():
    app_js = _read("app.js")
    assert "tak-row" in app_js
    assert "tak-sev-" in app_js


def test_panel_html_has_no_hardcoded_colors():
    for name in ("panel.html", "app.css"):
        text = _read(name)
        for pattern in _COLOR_PATTERNS:
            match = pattern.search(text)
            assert match is None, "%s 出现硬编码颜色: %s" % (name, match.group(0))


def test_web_assets_have_no_external_urls():
    for name in ("panel.html", "app.css", "app.js"):
        text = _read(name)
        assert _EXTERNAL_URL.search(text) is None, "%s 含外部 URL" % name


def test_panel_html_has_no_external_dependencies():
    html = _read("panel.html")
    assert "<script src=\"http" not in html
    assert "fonts.googleapis" not in html.lower()
    assert "unpkg" not in html.lower()
    # 只允许同源相对引用：href/src 必须是 / 开头的本站路径。
    for match in re.finditer(r'(?:href|src)="([^"]+)"', html):
        value = match.group(1)
        assert value.startswith("/") or value.startswith("#"), "外部引用: %s" % value


def test_server_serves_panel_and_theme_over_http():
    httpd = web_server.create_server(port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        for path, expected in (("/", "text/html"), ("/theme.css", "text/css"),
                               ("/app.js", "javascript"), ("/app.css", "text/css")):
            url = "http://127.0.0.1:%d%s" % (port, path)
            with urllib.request.urlopen(url, timeout=10) as resp:
                assert resp.status == 200
                assert expected in resp.headers.get("Content-Type", "")
                assert resp.read()
        with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % port, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        assert data["ok"] is True
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_server_unknown_path_returns_json_404():
    httpd = web_server.create_server(port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/nope" % port, timeout=10)
            raise AssertionError("未知路径应当返回 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
            data = json.loads(exc.read().decode("utf-8"))
        assert data["ok"] is False
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_package_exports_public_api():
    import tak.ui.web as pkg
    for name in ("create_server", "serve", "main", "PANEL_PATH", "THEME_CSS_PATH"):
        assert hasattr(pkg, name), "tak.ui.web 缺少导出 %s" % name
    assert os.path.isfile(pkg.PANEL_PATH)
    assert os.path.isfile(pkg.THEME_CSS_PATH)
