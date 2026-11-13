# -*- coding: utf-8 -*-
"""P5.3 VLM Defect Inspector 抽帧缺陷诊断回归测试。

覆盖 tak/core/vision 纯函数层、插件 core/manifest、Web HTML 片段与可复现性。
全程离线（autouse fixture 固定 TAK_AI_OFFLINE=1），不联网、不接触真实 DCC，
不注册全局 @rule，因此不污染 40 个 golden fixtures 的全规则回归。
"""
from __future__ import annotations

import ast
import json
import os

import pytest

from tak.core import registry, runner, vision
from tak.core.report import Report
from tak.core.rules import Finding, Severity
from tak.core.runner import ToolContext
from tak.core.vision import (
    DEFECT_CATEGORIES, MAX_FRAMES, MAX_IMAGE_CHARS,
    build_prompt, diagnose, fallback_candidates, inspect_frames,
    normalize_frames, parse_vision_response, validate_candidates,
)

TOOL_ID = "defect_inspector"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VISION_DIR = os.path.join(_ROOT, "tak", "core", "vision")
_PLUGIN_DIR = os.path.join(_ROOT, "tools", "defect_inspector")


@pytest.fixture(autouse=True)
def _force_offline(monkeypatch):
    """固定离线 provider，保证结果可复现、不发起网络请求。"""
    monkeypatch.setenv("TAK_AI_OFFLINE", "1")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _load(submodule):
    return registry.load_plugin_module(runner.find_plugin(TOOL_ID), submodule)


def _frames(count=3, start=1, step=1, bones=None):
    out = []
    for i in range(count):
        out.append({
            "frame": start + i * step,
            "image": "data:image/png;base64,AAAA%d" % i,
            "bones": list(bones) if bones is not None else ["root", "foot_L"],
            "note": "",
        })
    return out


def _cand(frame=1, category="popping", bone="", confidence=0.8, **kw):
    item = {
        "frame": frame, "category": category, "bone": bone,
        "confidence": confidence, "evidence": "可见信号",
        "source": "vision_model",
    }
    item.update(kw)
    return item


class _JsonProvider:
    """返回合法 JSON 候选的假 provider，用于验证非降级路径。"""

    model = "fake-vision"

    def vision(self, images, prompt, **kwargs):
        return json.dumps({"candidates": [
            {"frame": 1, "category": "popping", "bone": "root",
             "confidence": 0.9, "evidence": "可见跳变"},
        ]})


class _BoomProvider:
    """调用即抛异常的假 provider，用于验证降级路径。"""

    model = "boom"

    def vision(self, images, prompt, **kwargs):
        raise RuntimeError("network down")


# --------------------------------------------------------------------------- #
# normalize_frames
# --------------------------------------------------------------------------- #
def test_normalize_frames_valid():
    result = normalize_frames(_frames(3))
    assert result["ok"] is True
    assert len(result["frames"]) == 3
    for item in result["frames"]:
        assert set(item.keys()) == {"frame", "image", "bones", "note"}
        assert isinstance(item["frame"], int)
        assert isinstance(item["image"], str)
        assert isinstance(item["bones"], list)
        assert isinstance(item["note"], str)
    assert result["errors"] == []
    assert result["truncated"] is False


def test_normalize_frames_frame_number_alias_and_url_image():
    frames = [{"frame_number": "7", "image": "https://cdn.example/x.png"}]
    result = normalize_frames(frames)
    assert result["frames"][0]["frame"] == 7
    assert result["frames"][0]["image"].startswith("https://")


def test_normalize_frames_timestamp_goes_to_note():
    frames = [{"frame": 5, "image": "data:image/png;base64,AA",
               "timestamp": 0.5}]
    result = normalize_frames(frames)
    assert "timestamp" in result["frames"][0]["note"]


def test_normalize_frames_invalid_items_do_not_raise():
    frames = [
        {"frame": 1, "image": "data:image/png;base64,AA"},
        {"frame": 2, "image": "C:\\local\\a.png"},
        {"frame": "abc", "image": "data:image/png;base64,AA"},
        {"frame": 4, "image": 123},
        "not a dict",
    ]
    result = normalize_frames(frames)
    assert len(result["frames"]) == 1
    reasons = [error["reason"] for error in result["errors"]]
    assert "invalid_image" in reasons
    assert "invalid_frame" in reasons
    assert "not_object" in reasons


def test_normalize_frames_truncates_over_limit():
    result = normalize_frames(_frames(MAX_FRAMES + 5))
    assert result["truncated"] is True
    assert len(result["frames"]) == MAX_FRAMES
    assert result["stats"]["truncated"] == 5


def test_normalize_frames_deduplicates_frame_numbers():
    frames = _frames(2) + [{"frame": 1, "image": "data:image/png;base64,ZZ"}]
    result = normalize_frames(frames)
    assert len(result["frames"]) == 2
    assert result["stats"]["duplicates"] == 1
    assert any(error["reason"] == "duplicate_frame" for error in result["errors"])


def test_normalize_frames_rejects_overlong_image():
    big = "data:image/png;base64," + "A" * (MAX_IMAGE_CHARS + 10)
    result = normalize_frames([{"frame": 1, "image": big}])
    assert result["frames"] == []
    assert any(error["reason"] == "image_too_long" for error in result["errors"])


def test_normalize_frames_non_list_input():
    result = normalize_frames("nope")
    assert result["ok"] is False
    assert result["frames"] == []
    assert result["errors"][0]["reason"] == "frames_not_list"


# --------------------------------------------------------------------------- #
# validate_candidates：确定性过滤
# --------------------------------------------------------------------------- #
def test_validate_rejects_frame_out_of_range():
    result = validate_candidates([_cand(frame=99)], _frames(3))
    assert result["valid"] == []
    assert result["rejected"][0]["reason"] == "frame_out_of_range"


def test_validate_rejects_unknown_category():
    result = validate_candidates([_cand(category="explode")], _frames(3))
    assert result["rejected"][0]["reason"] == "unknown_category"


def test_validate_rejects_invalid_confidence():
    bad_values = (-0.1, 1.5, float("nan"), float("inf"), "abc", None, True)
    for value in bad_values:
        result = validate_candidates([_cand(confidence=value)], _frames(3))
        assert result["valid"] == [], value
        assert result["rejected"][0]["reason"] == "invalid_confidence", value


def test_validate_unknown_bone_degrades_but_keeps_candidate():
    snapshot = {"joints": {"root": {}, "foot_L": {}}}
    result = validate_candidates(
        [_cand(bone="ghost_bone", confidence=0.9)], _frames(3),
        snapshot=snapshot)
    assert len(result["valid"]) == 1
    candidate = result["valid"][0]
    assert candidate["bone_known"] is False
    assert candidate["confidence"] <= 0.3
    assert any(w["type"] == "unknown_bone" for w in result["warnings"])


def test_validate_known_bone_passes_without_warning():
    snapshot = {"joints": {"root": {}}}
    result = validate_candidates([_cand(bone="root")], _frames(3),
                                 snapshot=snapshot)
    assert result["valid"][0]["bone_known"] is True
    assert result["warnings"] == []


def test_validate_bone_not_checked_when_snapshot_has_no_joints():
    result = validate_candidates([_cand(bone="any_bone")], _frames(3))
    assert result["valid"][0]["bone_known"] is True
    assert result["warnings"] == []


def test_validate_dedup_and_stable_sort():
    frames = _frames(4)
    candidates = [
        _cand(frame=3, category="jitter"),
        _cand(frame=1, category="popping"),
        _cand(frame=3, category="jitter"),
        _cand(frame=1, category="foot_slide"),
        _cand(frame=2, category="popping"),
    ]
    result = validate_candidates(candidates, frames)
    keys = [(c["frame"], c["category"], c["bone"]) for c in result["valid"]]
    assert keys == sorted(keys)
    assert len(keys) == 4
    assert result["stats"]["duplicates"] == 1
    assert set(result.keys()) == {"ok", "valid", "rejected", "warnings", "stats"}


# --------------------------------------------------------------------------- #
# parse_vision_response
# --------------------------------------------------------------------------- #
def test_parse_json_code_block():
    text = "```json\n" + json.dumps({"candidates": [_cand()]}) + "\n```"
    result = parse_vision_response(text)
    assert result["error"] == ""
    assert len(result["candidates"]) == 1


def test_parse_candidates_object():
    text = json.dumps({"candidates": [_cand(frame=1), _cand(frame=2)]})
    result = parse_vision_response(text)
    assert result["error"] == ""
    assert len(result["candidates"]) == 2


def test_parse_plain_array():
    text = json.dumps([_cand(frame=1), _cand(frame=2)])
    result = parse_vision_response(text)
    assert result["error"] == ""
    assert len(result["candidates"]) == 2


def test_parse_garbage_returns_error_without_raising():
    for text in ("not json at all", "[mock] 3 张图，提示词 12 字符", "",
                 None, 123, "```json {oops} ```"):
        result = parse_vision_response(text)
        assert result["candidates"] == []
        assert result["error"]


# --------------------------------------------------------------------------- #
# fallback_candidates
# --------------------------------------------------------------------------- #
def test_fallback_reproducible_and_tagged():
    frames = _frames(4)
    first = fallback_candidates(frames)
    second = fallback_candidates(frames)
    assert first == second
    assert first
    for candidate in first:
        assert candidate["source"] == "deterministic_fallback"
        assert candidate["category"] in DEFECT_CATEGORIES
        assert "需人工复核" in candidate["evidence"]
        assert 0.0 <= candidate["confidence"] <= 1.0
        assert set(candidate.keys()) >= {
            "frame", "category", "bone", "confidence", "evidence", "source"}


def test_fallback_never_invents_visual_defect():
    for candidate in fallback_candidates(_frames(5)):
        assert candidate["category"] == "unknown"


def test_fallback_empty_frames_returns_empty():
    assert fallback_candidates([]) == []


# --------------------------------------------------------------------------- #
# inspect_frames：离线全链路 / 异常降级 / 模型路径 / 可复现
# --------------------------------------------------------------------------- #
def test_inspect_frames_offline_full_chain():
    result = inspect_frames(_frames(4))
    assert result["ok"] is True
    assert result["source"] == "deterministic_fallback"
    assert result["deterministic"] is True
    assert result["frames_checked"] == 4
    assert result["candidates"]
    assert result["provider"]
    assert result["model"]
    assert set(result.keys()) == {
        "ok", "candidates", "rejected", "warnings", "provider", "model",
        "source", "frames_checked", "stats", "deterministic"}
    assert any(w["type"] == "deterministic_fallback" for w in result["warnings"])


def test_inspect_frames_provider_exception_falls_back():
    result = inspect_frames(_frames(3), provider=_BoomProvider())
    assert result["ok"] is True
    assert result["source"] == "deterministic_fallback"
    assert any(w["type"] == "provider_error" for w in result["warnings"])


def test_inspect_frames_uses_model_candidates_when_valid():
    result = inspect_frames(_frames(3), provider=_JsonProvider())
    assert result["source"] == "vision_model"
    assert result["deterministic"] is False
    assert any(c["category"] == "popping" for c in result["candidates"])


def test_inspect_frames_is_reproducible():
    assert inspect_frames(_frames(4)) == inspect_frames(_frames(4))


def test_inspect_frames_bad_provider_output_still_offline_fallback():
    class _BadProvider:
        model = "bad"

        def vision(self, images, prompt, **kwargs):
            return 42  # 非字符串响应

    result = inspect_frames(_frames(3), provider=_BadProvider())
    assert result["ok"] is True
    assert result["source"] == "deterministic_fallback"
    assert any(w["type"] == "provider_error" for w in result["warnings"])


def test_diagnose_alias_matches_inspect_frames():
    assert diagnose(_frames(3)) == inspect_frames(_frames(3))


# --------------------------------------------------------------------------- #
# build_prompt / 顶层导出
# --------------------------------------------------------------------------- #
def test_build_prompt_contains_json_constraints_and_whitelists():
    prompt = build_prompt(_frames(3), snapshot={"joints": {"root": {}}})
    assert "JSON" in prompt
    assert "candidates" in prompt
    assert "帧号白名单" in prompt
    assert "类别白名单" in prompt
    assert "字段白名单" in prompt
    assert "confidence" in prompt
    assert "[1, 2, 3]" in prompt
    assert "root" in prompt
    for category in DEFECT_CATEGORIES:
        assert category in prompt
    assert "自由文本" in prompt and "脚本" in prompt


def test_vision_package_exports_public_api():
    for name in ("DEFECT_CATEGORIES", "MAX_FRAMES", "MAX_IMAGE_CHARS",
                 "normalize_frames", "validate_candidates", "parse_vision_response",
                 "fallback_candidates", "build_prompt", "inspect_frames", "diagnose"):
        assert hasattr(vision, name), name
    assert isinstance(vision.DEFECT_CATEGORIES, frozenset)
    required = {"popping", "jitter", "foot_slide", "penetration",
                "weight_artifact", "twist", "ik_snap", "unknown"}
    assert required <= set(vision.DEFECT_CATEGORIES)
    assert vision.MAX_FRAMES == 24
    assert vision.MAX_IMAGE_CHARS == 2_000_000


# --------------------------------------------------------------------------- #
# manifest / 发现 / new_tool --list
# --------------------------------------------------------------------------- #
def test_manifest_discoverable_by_registry():
    ids = [plugin.id for plugin in registry.discover()]
    assert TOOL_ID in ids
    plugin = runner.find_plugin(TOOL_ID)
    assert plugin.ui == "web"
    assert set(plugin.dcc) == {"maya", "max", "mobu", "unreal"}
    assert plugin.permissions == ["scene.read"]
    assert "候选" in plugin.description
    assert "人工复核" in plugin.description
    assert "离线可跑" in plugin.description


def test_new_tool_list_includes_plugin(capsys):
    from tools import new_tool
    assert new_tool.main(["--list"]) == 0
    assert TOOL_ID in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# 插件 core.py
# --------------------------------------------------------------------------- #
def _ctx(frames=None, snapshot=None):
    return ToolContext(
        dcc="unreal",
        options={"frames": frames if frames is not None else _frames(3)},
        snapshot=snapshot if snapshot is not None else {"joints": {"root": {}}},
        spec={},
        tool_id=TOOL_ID,
    )


def test_core_info():
    info = _load("core").info()
    assert info["id"] == TOOL_ID
    assert info["permissions"] == ["scene.read"]
    assert set(info["dcc"]) == {"maya", "max", "mobu", "unreal"}
    assert "候选" in info["description"]


def test_core_inspect_wrapper():
    core = _load("core")
    result = core.inspect(_frames(3), snapshot={"joints": {"root": {}}})
    assert result["ok"] is True
    assert result["source"] == "deterministic_fallback"
    # 支持 inspect(**body) 且忽略多余键
    result2 = core.inspect(**{"frames": _frames(3), "dcc": "unreal", "extra": 1})
    assert result2["ok"] is True


def test_run_report_returns_report_with_safety_metadata():
    report = _load("core").run_report(_ctx())
    assert isinstance(report, Report)
    assert "safety" in report.metadata
    safety = report.metadata["safety"]
    assert safety["writes_scene"] is False
    assert safety["needs_human_review"] is True
    assert safety["ai_role"] == "candidates_only"
    assert isinstance(report.metadata["candidates"], list)
    assert "stats" in report.metadata
    assert "source" in report.metadata
    assert "provider" in report.metadata
    assert report.metadata["source"] in ("vision_model", "deterministic_fallback")


def test_run_report_only_info_or_warning_and_never_blocks():
    report = _load("core").run_report(_ctx())
    for finding in report.findings:
        assert isinstance(finding, Finding)
        assert finding.severity in (Severity.INFO, Severity.WARNING)
        assert "需人工复核" in finding.fix_hint
    assert report.passed is True


def test_run_report_is_reproducible():
    core = _load("core")
    assert core.run_report(_ctx()).to_dict() == core.run_report(_ctx()).to_dict()


# --------------------------------------------------------------------------- #
# ui_web.py：转义、字段展示、无外链
# --------------------------------------------------------------------------- #
def test_ui_web_escapes_dynamic_text_and_shows_fields():
    ui = _load("ui_web")
    result = inspect_frames(_frames(3))
    result["candidates"].append({
        "frame": 1, "category": "unknown", "bone": "<b>&\"'</b>",
        "confidence": 0.42,
        "evidence": "<script>alert(1)</script> & 'x'",
        "source": "deterministic_fallback",
    })
    out = ui.render_panel_html(result)
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out
    assert "&amp;" in out
    assert "tak-panel" in out
    assert TOOL_ID in out
    assert "需人工复核" in out
    assert "deterministic_fallback" in out
    assert "帧号" in out and "置信度" in out and "证据" in out and "来源" in out


def test_ui_web_report_html_entrypoint():
    ui = _load("ui_web")
    assert "tak-panel" in ui.render_report_html(inspect_frames(_frames(3)))


def test_ui_web_shows_rejected_and_warnings():
    ui = _load("ui_web")
    result = inspect_frames(_frames(3))
    result["rejected"] = [{"index": 0, "candidate": {"frame": 1},
                           "reason": "unknown_category"}]
    result["warnings"] = [{"type": "parse_error", "message": "模型输出无法解析"}]
    out = ui.render_panel_html(result)
    assert "被拒绝项" in out
    assert "unknown_category" in out
    assert "警告" in out
    assert "parse_error" in out
    assert "模型输出无法解析" in out


def test_ui_web_has_no_external_links_or_scripts():
    ui = _load("ui_web")
    result = inspect_frames(_frames(3))
    for renderer in (ui.render_panel_html, ui.render_report_html):
        out = renderer(result).lower()
        assert "<script" not in out
        assert "<link" not in out
        assert "http://" not in out
        assert "https://" not in out
        assert "fonts.googleapis" not in out
    with open(os.path.join(_PLUGIN_DIR, "ui_web.py"), "r", encoding="utf-8") as fh:
        source = fh.read().lower()
    for banned in ("http://", "https://", "fonts.googleapis", "unpkg", "cdnjs"):
        assert banned not in source


# --------------------------------------------------------------------------- #
# Python 3.9 语法 / 零 DCC 与第三方依赖
# --------------------------------------------------------------------------- #
def test_new_python_files_parse_as_python39():
    files = [
        os.path.join(_VISION_DIR, "__init__.py"),
        os.path.join(_VISION_DIR, "frames.py"),
        os.path.join(_PLUGIN_DIR, "core.py"),
        os.path.join(_PLUGIN_DIR, "ui_web.py"),
        os.path.abspath(__file__),
    ]
    for path in files:
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        assert ast.parse(source, filename=path, feature_version=(3, 9)) is not None


def test_vision_layer_has_no_dcc_or_third_party_imports():
    banned = ("import maya", "import pymxs", "import pyfbsdk", "import unreal",
              "import numpy", "import cv2", "from PIL", "import PIL",
              "import subprocess", "import ffmpeg")
    for name in ("__init__.py", "frames.py"):
        with open(os.path.join(_VISION_DIR, name), "r", encoding="utf-8") as fh:
            source = fh.read()
        for token in banned:
            assert token not in source, "%s 含禁用 import: %s" % (name, token)
