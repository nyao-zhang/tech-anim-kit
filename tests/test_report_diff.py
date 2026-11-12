# -*- coding: utf-8 -*-
"""报告过滤/转义与骨架快照 diff 的数值和空值边界。"""
import json
import math
import re

import pytest

from tak.core.diff import Joint, SkeletonSnapshot, diff_snapshots
from tak.core.report import Report
from tak.core.rules import Finding, Severity


def _report():
    report = Report("体检", "maya")
    report.add(Finding("info.rule", Severity.INFO, "信息", node="root"))
    report.add(Finding("warning.rule", Severity.WARNING, "警告", node="spine"))
    report.add(Finding("error.rule", Severity.ERROR, "错误", node="head"))
    return report


def _snapshot(name="root", matrix=None):
    joint = Joint(name)
    if matrix is not None:
        joint.matrix = matrix
    return SkeletonSnapshot(joints={name: joint})


def test_severity_filter_counts_and_summary():
    report = _report()
    assert [item.rule_id for item in report.filter_by_severity(Severity.ERROR)] == [
        "error.rule",
    ]
    assert report.counts("error")["error"] == 1
    assert report.counts(["info", "warning"])["warning"] == 1
    summary = report.summary(["info", "warning"])
    assert summary["total"] == 2
    assert summary["passed"] is True
    assert report.summary()["passed"] is False
    with pytest.raises(ValueError, match="未知 severity"):
        report.filter_by_severity("fatal")


def test_renderers_apply_severity_filter_without_mutating_report():
    report = _report()
    html = report.to_html(severity="error")
    markdown = report.to_markdown(severity=Severity.INFO)

    assert "error.rule" in html
    assert "info.rule" not in html
    assert "info.rule" in markdown
    assert "error.rule" not in markdown
    payload = json.loads(report.to_json(severity="warning"))
    assert [item["rule_id"] for item in payload["findings"]] == ["warning.rule"]
    assert payload["passed"] is True
    assert len(report.findings) == 3
    assert report.passed is False


def test_markdown_escapes_special_none_newline_and_long_text():
    long_text = "A" * 10000 + " `*_[]()#+-.!|\\ <script>&\nnext"
    report = Report(None, None)
    report.add(Finding(None, Severity.INFO, long_text, node=None, fix_hint=None))

    text = report.to_markdown()
    assert "None" not in text
    assert "A" * 10000 in text
    assert "\\`\\*\\_" in text
    assert "&lt;script&gt;&amp;" in text
    assert "<script>" not in text
    assert "next" in text


def test_html_escapes_all_fields_and_uses_theme_without_hardcoded_colors():
    report = Report('</title><script>alert("x")</script>', 'maya" onload="attack')
    report.add(Finding(
        '<img src=x onerror="attack">', Severity.ERROR,
        '<svg onload="attack">&', node="node'\"",
        fix_hint='</span><script>alert("hint")</script>',
    ))

    html = report.to_html('theme.css" onload="attack')
    assert "<script>" not in html
    assert "<img" not in html
    assert "<svg" not in html
    assert 'onload="attack' not in html
    assert "&lt;script&gt;" in html
    assert "tak-panel" in html
    assert "tak-sev-error" in html
    assert re.search(r"#[0-9a-fA-F]{3,8}\b", html) is None


def test_matrix_comparison_uses_absolute_tolerance():
    before = _snapshot()
    after = _snapshot(matrix=list(before.joints["root"].matrix))
    after.joints["root"].matrix[0] += 0.00005
    assert diff_snapshots(before, after, matrix_tolerance=0.0001) == []

    after.joints["root"].matrix[0] += 0.01
    assert [item["field"] for item in diff_snapshots(before, after)] == ["bind_matrix"]


@pytest.mark.parametrize("bad_value", [float("nan"), None])
def test_matrix_nan_and_none_are_differences_but_remain_json_safe(bad_value):
    before = _snapshot()
    matrix = list(before.joints["root"].matrix)
    matrix[0] = bad_value
    after = _snapshot(matrix=matrix)

    diffs = diff_snapshots(before, after)
    assert [item["field"] for item in diffs] == ["bind_matrix"]
    json.dumps(diffs, allow_nan=False)


def test_matrix_none_and_length_mismatch_do_not_crash():
    none_matrix = _snapshot(matrix=[None] * 16)
    assert diff_snapshots(none_matrix, _snapshot(matrix=[None] * 16)) == []
    assert diff_snapshots(_snapshot(), _snapshot(matrix=[1.0]))[0]["field"] == "bind_matrix"


def test_rename_detection_replaces_missing_and_added_classification():
    before = _snapshot("root")
    matrix = list(before.joints["root"].matrix)
    matrix[0] += 0.00001
    after = _snapshot("root_renamed", matrix=matrix)

    diffs = diff_snapshots(before, after)
    assert diffs == [{"field": "joint_renamed", "before": "root", "after": "root_renamed"}]


def test_changed_joint_is_missing_and_added_not_rename():
    before = _snapshot("root")
    after = _snapshot("other", matrix=[2.0] * 16)

    fields = {item["field"] for item in diff_snapshots(before, after)}
    assert fields == {"joint_missing", "joint_added"}


def test_unit_axis_and_non_finite_frame_range_are_classified_and_serializable():
    before = SkeletonSnapshot(unit="cm", up_axis="Y", frame_range=[1.0, 120.0])
    after = SkeletonSnapshot(unit="m", up_axis="Z", frame_range=[1.0, math.nan])

    diffs = diff_snapshots(before, after)
    assert {item["field"] for item in diffs} == {"unit", "up_axis", "frame_range"}
    payload = json.dumps(diffs, allow_nan=False)
    assert "NaN" not in payload
    assert "nan" in payload


def test_empty_none_and_dict_snapshots_are_robust():
    empty = SkeletonSnapshot()
    assert diff_snapshots(None, None) == []
    assert diff_snapshots(empty, SkeletonSnapshot()) == []
    assert diff_snapshots(None, {}) == []
    assert SkeletonSnapshot.from_dict(None).joints == {}


@pytest.mark.parametrize("tolerance", [-1, float("nan"), float("inf"), None, "0.1", True])
def test_invalid_matrix_tolerance_is_rejected(tolerance):
    with pytest.raises(ValueError, match="matrix_tolerance"):
        diff_snapshots(None, None, matrix_tolerance=tolerance)
