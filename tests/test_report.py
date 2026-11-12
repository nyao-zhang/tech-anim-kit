# -*- coding: utf-8 -*-
"""报告模型：三种渲染与阻断判定。"""
import json

from tak.core.report import Report
from tak.core.rules import Finding, Severity


def _report():
    report = Report("导出前体检", "maya")
    report.add(Finding("naming.joint_pattern", Severity.WARNING,
                       "骨骼命名不匹配", node="LeftUpArm", fix_hint="重命名为 upperarm_L"))
    report.add(Finding("skin.max_influences", Severity.ERROR,
                       "影响骨超过 4 根", node="head_M"))
    return report


def test_counts_and_passed():
    report = _report()
    assert report.counts()["warning"] == 1
    assert report.counts()["error"] == 1
    assert report.passed is False
    assert Report("空", "maya").passed is True


def test_json_roundtrip():
    payload = json.loads(_report().to_json())
    assert payload["passed"] is False
    assert payload["findings"][0]["node"] == "LeftUpArm"


def test_markdown_contains_fix_hint_and_conclusion():
    text = _report().to_markdown()
    assert "不通过" in text
    assert "修复建议" in text
    assert "skin.max_influences" in text


def test_html_uses_design_system_classes_and_escapes():
    html = _report().to_html()
    assert "tak-panel" in html
    assert "tak-state-fail" in html
    assert "tak-sev-error" in html
    report = Report("<script>", "maya")
    report.add(Finding("id", Severity.INFO, "<b>x</b>"))
    assert "<script>" not in report.to_html()
    assert "&lt;b&gt;" in report.to_html()
