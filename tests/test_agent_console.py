# -*- coding: utf-8 -*-
"""Agent Console（批次 3 / P5.1）回归测试。

覆盖面
------
* 意图分类中英文、返回结构与 UNKNOWN；
* local_candidates 只产出已注册动作、确定性 / 可复现、无信号时空候选；
* explain_findings 结构、limit、可序列化、离线可跑；
* chat PLAN 产出 plan_id 与 dry-run preview、零副作用、绝不调用 DSL execute；
* 未注册动作被 rejected（propose 直传 + monkeypatch 候选）；
* confirm 默认 / confirm+dry_run 不执行、完整链执行、缺权限拒绝、未知 plan_id；
* history / audit 可复现且单调递增、reset 清空、describe 可序列化；
* SEARCH / STATUS / RUN / UNKNOWN 分支；
* 插件 manifest 可被 discover、run_report 返回 Report 且 metadata 含 safety；
* Web 薄封装 create_session / chat / confirm（含 dict body 容错）；
* ui_web 转义 / 无外链 / 无硬编码颜色、ui_qt 无 Qt 优雅降级；
* 新增 .py 通过 ast.parse(feature_version=(3,9)) 且无真实 DCC import。

全部离线：只用 NullAdapter / RecordingAdapter 与手写快照，不接触真实 DCC。
"""
from __future__ import annotations

import ast
import copy
import json
import os
import re

import pytest

from tak.adapters.base import NullAdapter
from tak.core import registry, runner
from tak.core.agent import (
    EXPLAIN, PLAN, RUN, SEARCH, STATUS, UNKNOWN, SAFETY_NOTES,
    AgentSession, classify, explain_findings, local_candidates,
)
from tak.core.agent import intents as intents_mod
from tak.core.dsl import ACTION_REGISTRY
from tak.core.report import Report

TOOL_ID = "agent_console"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SNAP = {
    "skin": {"weight_sum_deviation": 0.2, "max_influences_found": 7},
    "joints": {
        "CTRL:root": {"namespace": "CTRL", "scale": [1.0, 1.0, 1.0]},
        "upperarm_L": {"namespace": "", "scale": [-1.0, 1.0, 1.0]},
    },
}
SPEC = {
    "skin": {"weight_sum_tolerance": 0.001, "max_influences": 4},
    "naming": {"joint_pattern": "^(root|upperarm_[LR])$"},
}

_NEW_PY = (
    os.path.join(_ROOT, "tak", "core", "agent", "__init__.py"),
    os.path.join(_ROOT, "tak", "core", "agent", "intents.py"),
    os.path.join(_ROOT, "tak", "core", "agent", "session.py"),
    os.path.join(_ROOT, "tools", TOOL_ID, "core.py"),
    os.path.join(_ROOT, "tools", TOOL_ID, "ui_web.py"),
    os.path.join(_ROOT, "tools", TOOL_ID, "ui_qt.py"),
)


# --------------------------------------------------------------------------- #
# 工具 / fixture
# --------------------------------------------------------------------------- #
def _plugin():
    return runner.find_plugin(TOOL_ID)


def _core():
    # 每次重新 exec，得到干净的模块级 _SESSIONS，测试间互不污染。
    return registry.load_plugin_module(_plugin(), "core")


def _ui_web():
    return registry.load_plugin_module(_plugin(), "ui_web")


def _ui_qt():
    return registry.load_plugin_module(_plugin(), "ui_qt")


class RecordingAdapter(NullAdapter):
    """记录一切可能写场景的交互，用于断言零副作用 / 执行链。"""

    def __init__(self):
        super().__init__()
        self.events = []

    def begin_undo(self, label="TAK"):
        self.events.append("begin")
        return "token"

    def commit_undo(self, token):
        self.events.append("commit")

    def rollback(self, token):
        self.events.append("rollback")

    def log(self, message, level="info"):
        self.events.append("log")

    def set_namespace(self, node, namespace):
        self.events.append("set_namespace")

    def set_control_color(self, node, color):
        self.events.append("set_control_color")

    def normalize_weights(self, **kwargs):
        self.events.append("normalize_weights")

    def rename_node(self, node, name):
        self.events.append("rename_node")


@pytest.fixture(autouse=True)
def _ensure_actions():
    """保证 spec_enforcer 的既有 4 个动作已注册（幂等，不注册新动作）。"""
    intents_mod.ensure_actions_registered()
    yield


def _session(**kwargs):
    kwargs.setdefault("adapter", RecordingAdapter())
    kwargs.setdefault("spec", copy.deepcopy(SPEC))
    return AgentSession(**kwargs)


def _jsonable(value):
    json.dumps(value, ensure_ascii=False)
    return True


# --------------------------------------------------------------------------- #
# 意图分类
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("message,expected", [
    ("解释一下这个问题", EXPLAIN),
    ("为什么权重会错", EXPLAIN),
    ("生成修复计划", PLAN),
    ("给个方案和建议", PLAN),
    ("执行计划", RUN),
    ("应用修复", RUN),
    ("搜索文档", SEARCH),
    ("查询知识库", SEARCH),
    ("查看状态", STATUS),
    ("显示历史记录", STATUS),
])
def test_classify_chinese(message, expected):
    result = classify(message)
    assert result["intent"] == expected
    assert 0.0 < result["confidence"] <= 0.99
    assert result["matched"]


@pytest.mark.parametrize("message,expected", [
    ("explain this issue", EXPLAIN),
    ("please describe the reason", EXPLAIN),
    ("give me a plan", PLAN),
    ("propose a fix preview", PLAN),
    ("apply the plan", RUN),
    ("execute it", RUN),
    ("search the docs", SEARCH),
    ("find knowledge", SEARCH),
    ("show status", STATUS),
    ("print history and help", STATUS),
])
def test_classify_english(message, expected):
    result = classify(message)
    assert result["intent"] == expected


def test_classify_shape_and_unknown():
    result = classify("今天天气真不错啊")
    assert result["intent"] == UNKNOWN
    assert result["confidence"] == 0.0
    assert result["matched"] == []
    assert set(("intent", "confidence", "args", "matched")).issubset(result)
    assert isinstance(result["args"], dict)
    assert _jsonable(result)


def test_classify_no_llm(monkeypatch):
    """意图判定绝不调用 LLM：把 get_provider 打爆也不影响 classify。"""
    import tak.core.ai as ai_pkg

    def _boom(*args, **kwargs):
        raise AssertionError("classify 不应调用 get_provider/LLM")

    monkeypatch.setattr(ai_pkg, "get_provider", _boom)
    assert classify("生成修复计划")["intent"] == PLAN


# --------------------------------------------------------------------------- #
# local_candidates / explain_findings
# --------------------------------------------------------------------------- #
def test_local_candidates_only_registered_actions():
    result = local_candidates(SNAP, spec=SPEC)
    assert result["source"] == "deterministic"
    assert isinstance(result["actions"], list)
    assert isinstance(result["rationale"], list)
    assert result["actions"], "含问题信号时应产出候选"
    names = set(ACTION_REGISTRY.names)
    for action in result["actions"]:
        assert set(("tool", "params")).issubset(action)
        assert action["tool"] in names
        assert isinstance(action["params"], dict)
    assert _jsonable(result)


def test_local_candidates_reproducible():
    first = local_candidates(SNAP, spec=SPEC)
    second = local_candidates(copy.deepcopy(SNAP), spec=copy.deepcopy(SPEC))
    assert first == second


def test_local_candidates_no_signal_empty():
    clean = {"joints": {}, "skin": {}}
    result = local_candidates(clean, spec=SPEC)
    assert result["actions"] == []
    assert result["source"] == "deterministic"


def test_local_candidates_never_emits_unregistered(monkeypatch):
    """即使底层注册表被清空，也绝不产出未注册动作名。"""
    monkeypatch.setattr(intents_mod, "ensure_actions_registered", lambda: [])
    monkeypatch.setattr(ACTION_REGISTRY, "_specs", {})
    result = local_candidates(SNAP, spec=SPEC)
    assert result["actions"] == []


def test_explain_findings_structure_and_limit():
    items = explain_findings(SNAP, spec=SPEC, limit=2)
    assert isinstance(items, list) and len(items) <= 2
    for item in items:
        assert set(("rule_id", "severity", "node", "message",
                    "explanation", "fix_hint")).issubset(item)
        assert item["explanation"]
    assert _jsonable(items)
    assert explain_findings(SNAP, spec=SPEC, limit=2) == items  # 可复现


def test_explain_findings_offline_and_clean_snapshot():
    """无信号 + 空 spec 也能给出确定性 INFO 条目，且不抛异常。"""
    items = explain_findings({}, spec={}, limit=5)
    assert isinstance(items, list)
    assert _jsonable(items)
    assert all(_jsonable(item) for item in items)


# --------------------------------------------------------------------------- #
# chat：PLAN 路径 / 零副作用 / 绝不调用 execute
# --------------------------------------------------------------------------- #
def test_chat_plan_produces_plan_id_and_preview():
    session = _session()
    result = session.chat("生成修复计划", snapshot=SNAP)
    required = ("session_id", "turn", "intent", "reply", "candidates",
                "plan", "plan_id", "status", "citations", "safety")
    assert set(required).issubset(result)
    assert result["status"] == "plan_ready"
    assert result["plan_id"] == "plan-1"
    assert isinstance(result["plan"], dict)
    assert result["preview"], "应返回 preview(plan) 的 dry-run diff"
    for row in result["preview"]:
        assert set(("index", "tool", "params", "permissions", "status")).issubset(row)
        assert row["status"] == "pending"
    assert result["safety"]["chat_writes_scene"] is False
    assert _jsonable(result)


def test_chat_plan_zero_side_effects(monkeypatch):
    calls = []
    real_execute = ACTION_REGISTRY.execute

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real_execute(*args, **kwargs)

    monkeypatch.setattr(ACTION_REGISTRY, "execute", _spy)
    adapter = RecordingAdapter()
    session = AgentSession(adapter=adapter, spec=copy.deepcopy(SPEC))
    before = copy.deepcopy(SNAP)
    result = session.chat("生成修复计划", snapshot=SNAP)
    assert calls == [], "chat 绝不调用 DSL execute"
    assert adapter.events == [], "chat 不触碰 adapter"
    assert SNAP == before, "chat 不修改快照"
    assert result["status"] == "plan_ready"


def test_chat_never_calls_dsl_execute_any_intent(monkeypatch):
    calls = []

    def _boom(*args, **kwargs):
        calls.append(True)
        raise AssertionError("chat 不应调用 DSL execute")

    monkeypatch.setattr(ACTION_REGISTRY, "execute", _boom)
    session = _session()
    for message in ("解释问题", "生成计划", "执行计划", "搜索文档",
                    "查看状态", "随便说点什么"):
        session.chat(message, snapshot=SNAP)
    assert calls == []


def test_chat_plan_ids_are_stable_and_increasing():
    session = _session()
    a = session.chat("生成修复计划", snapshot=SNAP)
    b = session.chat("再给个方案", snapshot=SNAP)
    assert a["plan_id"] == "plan-1"
    assert b["plan_id"] == "plan-2"
    assert len(session.pending_plans()) == 2


def test_unregistered_action_rejected_via_propose():
    session = _session()
    result = session.propose({"actions": [
        {"tool": "evil.delete_everything", "params": {}}]})
    assert result["ok"] is False
    assert result["status"] == "rejected"
    assert result["plan_id"] is None
    assert "白名单" in result["reason"] or "未注册" in result["reason"]


def test_unregistered_action_rejected_via_chat(monkeypatch):
    def _bad(snapshot, spec=None, intent="plan"):
        return {"actions": [{"tool": "evil.wipe_scene", "params": {}}],
                "rationale": [], "source": "deterministic"}

    monkeypatch.setattr(intents_mod, "local_candidates", _bad)
    session = _session()
    result = session.chat("生成修复计划", snapshot=SNAP)
    assert result["status"] == "rejected"
    assert result["plan_id"] is None
    assert result["plan"] is None
    assert session.pending_plans() == []


# --------------------------------------------------------------------------- #
# chat：其他意图分支
# --------------------------------------------------------------------------- #
def test_chat_explain_status_and_search():
    session = _session()
    assert session.chat("解释一下", snapshot=SNAP)["status"] == "explained"
    assert session.chat("查看状态", snapshot=SNAP)["status"] == "status"
    searched = session.chat("搜索蒙皮规范", snapshot=SNAP)
    assert searched["status"] == "searched"
    assert isinstance(searched["citations"], list)
    assert _jsonable(searched)
    run = session.chat("执行计划", snapshot=SNAP)
    assert run["status"] in ("explained", "plan_ready")
    unknown = session.chat("啦啦啦", snapshot=SNAP)
    assert unknown["status"] == "explained"


# --------------------------------------------------------------------------- #
# confirm：唯一写入口
# --------------------------------------------------------------------------- #
def _planned_session():
    session = _session()
    result = session.chat("生成修复计划", snapshot=SNAP)
    return session, result["plan_id"]


def test_confirm_default_does_not_execute():
    session, plan_id = _planned_session()
    out = session.confirm(plan_id)
    assert out["committed"] is False
    assert out["dry_run"] is True
    assert out["ok"] is True
    assert session.adapter.events == []


def test_confirm_true_dry_run_still_no_execute():
    session, plan_id = _planned_session()
    out = session.confirm(plan_id, confirm=True, dry_run=True)
    assert out["committed"] is False
    assert out["dry_run"] is True
    assert session.adapter.events == []


def test_confirm_full_chain_executes():
    session, plan_id = _planned_session()
    out = session.confirm(plan_id, confirm=True, dry_run=False,
                          granted=["node.write"])
    assert out["ok"] is True
    assert out["committed"] is True
    assert out["dry_run"] is False
    assert "begin" in session.adapter.events
    assert "commit" in session.adapter.events
    assert _jsonable(out)


def test_confirm_missing_permission_rejected():
    session, plan_id = _planned_session()
    out = session.confirm(plan_id, confirm=True, dry_run=False,
                          granted=["scene.read"])
    assert out["ok"] is False
    assert "权限" in out["error"]
    assert out["committed"] is False
    assert "begin" not in session.adapter.events


def test_confirm_unknown_plan_id():
    session = _session()
    out = session.confirm("plan-999")
    assert out["ok"] is False
    assert out["committed"] is False


# --------------------------------------------------------------------------- #
# history / audit / reset / describe
# --------------------------------------------------------------------------- #
def test_history_audit_reproducible_and_monotonic():
    session, plan_id = _planned_session()
    session.confirm(plan_id)
    session.chat("查看状态", snapshot=SNAP)
    history = session.history()
    audit = session.audit()
    assert history == session.history()
    assert audit == session.audit()
    assert [h["seq"] for h in history] == sorted(h["seq"] for h in history)
    assert [a["seq"] for a in audit] == sorted(a["seq"] for a in audit)
    assert all(a["event"] == "confirm" for a in audit)
    assert _jsonable(history) and _jsonable(audit)


def test_reset_clears_state():
    session, plan_id = _planned_session()
    session.confirm(plan_id)
    assert session.history() and session.audit() and session.pending_plans()
    session.reset()
    assert session.history() == []
    assert session.audit() == []
    assert session.pending_plans() == []
    assert session.describe()["turn"] == 0


def test_describe_is_serializable():
    described = _session().describe()
    assert described["session_id"]
    assert "registered_actions" in described
    assert described["safety"] == list(SAFETY_NOTES)
    assert _jsonable(described)


# --------------------------------------------------------------------------- #
# 插件 manifest / run_report / Web 封装
# --------------------------------------------------------------------------- #
def test_manifest_discoverable_and_valid():
    plugins = registry.discover()
    assert TOOL_ID in [plugin.id for plugin in plugins]
    plugin = runner.find_plugin(TOOL_ID)
    assert plugin.name == "对话控制台"
    assert set(plugin.dcc) == {"maya", "max", "mobu", "unreal"}
    assert plugin.min_version == {"maya": "2023", "max": "2023",
                                  "mobu": "2023", "unreal": "5.8"}
    assert plugin.ui == "both"
    assert plugin.permissions == ["scene.read", "node.write"]
    with open(os.path.join(_plugin().path, "manifest.json"), encoding="utf-8") as fh:
        assert registry.validate_manifest(json.load(fh)) == []


def test_run_report_returns_report_with_safety(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("run_report 绝不写场景 / 调用 DSL execute")

    monkeypatch.setattr(ACTION_REGISTRY, "execute", _boom)
    ctx = runner.ToolContext(dcc="maya", snapshot=copy.deepcopy(SNAP),
                             spec=copy.deepcopy(SPEC), adapter=NullAdapter(),
                             tool_id=TOOL_ID, plugin=_plugin())
    report = _core().run_report(ctx)
    assert isinstance(report, Report)
    assert report.findings
    assert report.passed is True  # 只产出 INFO / WARNING
    metadata = report.metadata
    assert "safety" in metadata and metadata["safety"]
    assert "candidates" in metadata
    assert "session" in metadata and metadata["session"]["session_id"] == "report"
    assert "capabilities" in metadata
    assert metadata["dry_run_only"] is True
    assert _jsonable(report.to_dict())


def test_run_tool_integration():
    result = runner.run_tool(TOOL_ID, snapshot=copy.deepcopy(SNAP), dcc="maya",
                             adapter=NullAdapter(), spec=copy.deepcopy(SPEC))
    assert isinstance(result.report, Report)
    assert "safety" in result.report.metadata
    assert _jsonable(result.to_dict())


def test_web_wrappers_and_dict_body():
    core = _core()
    created = core.create_session(session_id="web-1")
    assert created["session_id"] == "web-1"
    assert _jsonable(created)
    chat_res = core.chat({"message": "生成修复计划", "snapshot": SNAP,
                          "session_id": "web-1"})
    assert chat_res["status"] == "plan_ready"
    preview = core.confirm({"plan_id": chat_res["plan_id"],
                            "session_id": "web-1", "dry_run": True})
    assert preview["committed"] is False
    missing = core.confirm("no-such", session_id="web-1")
    assert missing["ok"] is False
    unknown_session = core.confirm("plan-1", session_id="absent",
                                   confirm=True, dry_run=False,
                                   granted=["node.write"])
    assert unknown_session["ok"] is False


# --------------------------------------------------------------------------- #
# ui_web / ui_qt
# --------------------------------------------------------------------------- #
def test_ui_web_escaping_and_no_external():
    ui = _ui_web()
    payload = _session().chat("生成修复计划", snapshot=SNAP)
    payload["reply"] = '<script>alert("x")</script> & 中文'
    payload["candidates"] = [{"tool": "a<b>", "params": {"k": "</td><img>"}}]
    html = ui.render_panel_html(payload)
    assert "tak-panel" in html
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert "&lt;/td&gt;&lt;img&gt;" in html
    assert "http://" not in html and "https://" not in html
    assert "src=" not in html and "href=" not in html
    assert "cdn" not in html.lower() and "unpkg" not in html.lower()
    for pattern in (re.compile(r"#[0-9a-fA-F]{3,8}\b"),
                    re.compile(r"\brgba?\s*\("),
                    re.compile(r"\bhsla?\s*\(")):
        assert pattern.search(html) is None
    assert _jsonable(payload)


def test_ui_web_report_html():
    ui = _ui_web()
    ctx = runner.ToolContext(dcc="maya", snapshot=copy.deepcopy(SNAP),
                             spec=copy.deepcopy(SPEC), adapter=NullAdapter(),
                             tool_id=TOOL_ID, plugin=_plugin())
    report = _core().run_report(ctx)
    html = ui.render_report_html(report)
    assert "tak-panel" in html
    assert "安全边界" in html
    assert "http://" not in html


def test_ui_qt_import_safe_without_qt():
    ui = _ui_qt()
    assert callable(ui.show)
    assert ui.show(dcc="maya", omui=None) is None


# --------------------------------------------------------------------------- #
# 静态：Python 3.9 语法 + 零 DCC import
# --------------------------------------------------------------------------- #
def test_new_sources_parse_as_python39_and_no_dcc_imports():
    forbidden = ("maya", "pymxs", "pyfbsdk", "unreal")
    for path in _NEW_PY:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=path, feature_version=(3, 9))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in forbidden
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                assert node.module.split(".")[0] not in forbidden


def test_core_agent_has_no_thirdparty_top_level_imports():
    """tak/core/agent 顶层只允许标准库与 tak.* 自身。"""
    allowed_std = {"__future__", "re", "math", "copy", "typing", "ast",
                   "json", "os", "html", "time", "enum", "dataclasses",
                   "abc", "importlib"}
    for path in _NEW_PY[:3]:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path, feature_version=(3, 9))
        for node in tree.body:  # 仅检查模块顶层
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root in allowed_std or root == "tak"
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                root = node.module.split(".")[0]
                assert root in allowed_std or root == "tak"
