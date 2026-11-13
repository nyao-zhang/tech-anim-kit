# -*- coding: utf-8 -*-
"""场景检查（qa_inspector）的「没查」与「通过」必须能区分。

这组测试守的是一条具体的假通过：场景卫生里 ``orphan_nodes`` /
``duplicate_shape`` / ``legacy_reference`` / ``control_uncolored`` 四类检查的
数据只来自 ``snapshot["metadata"]["health"]``。快照没采集到这些字段时，规则会
安静地返回 0 条结论 —— 结果列表里看着和「检查通过」一模一样。
"""
from __future__ import annotations

from tak.adapters.base import NullAdapter
from tak.core import runner

PARTIAL_ID = "qa_inspector.health.partial"


def _snapshot(**extra):
    data = {"dcc": "maya", "version": "2026", "unit": "cm", "up_axis": "y",
            "joints": {"root": {"parent": None, "scale": [1.0, 1.0, 1.0],
                                "rotate": [0.0, 0.0, 0.0]}},
            "frame_range": [1.0, 120.0], "skin": {}, "metadata": {}}
    data.update(extra)
    return data


def _run(checks, snapshot=None):
    return runner.run_tool("qa_inspector", snapshot or _snapshot(), dcc="maya",
                           adapter=NullAdapter(), options={"checks": checks})


def test_missing_health_surfaces_partial_notice():
    report = _run(["rig_health_guard.orphan_nodes",
                   "rig_health_guard.control_uncolored"]).report
    ids = [item.rule_id for item in report.findings]
    assert PARTIAL_ID in ids
    gaps = report.metadata["health_gaps"]
    assert {item["health_key"] for item in gaps} == {"orphan_nodes",
                                                     "control_colors"}


def test_partial_notice_names_the_checks():
    report = _run(["rig_health_guard.orphan_nodes"]).report
    notice = [item for item in report.findings if item.rule_id == PARTIAL_ID][0]
    assert "orphan_nodes" in notice.message
    assert notice.severity.value == "info"


def test_health_present_means_no_partial_notice():
    """采集到的空列表表示「查过了，没问题」，与「没查」是两回事。"""
    snapshot = _snapshot()
    snapshot["metadata"]["health"] = {
        "orphan_nodes": [], "duplicate_shapes": [],
        "references": [], "control_colors": {},
    }
    report = _run(["rig_health_guard.orphan_nodes",
                   "rig_health_guard.duplicate_shape",
                   "rig_health_guard.legacy_reference",
                   "rig_health_guard.control_uncolored"],
                  snapshot).report
    assert PARTIAL_ID not in [item.rule_id for item in report.findings]
    assert report.metadata["health_gaps"] == []


def test_gaps_only_cover_selected_checks():
    """没勾的检查项不算缺口，否则每次都会提示四条。"""
    report = _run(["rig_health_guard.negative_scale"]).report
    assert report.metadata["health_gaps"] == []
    assert PARTIAL_ID not in [item.rule_id for item in report.findings]


def test_whole_group_without_health_is_flagged_by_health_guard():
    """rig_health_guard 自己的入口也要发声，它还有独立调用的路径。"""
    result = runner.run_tool("rig_health_guard", _snapshot(), dcc="maya",
                             adapter=NullAdapter())
    ids = [item.rule_id for item in result.report.findings]
    assert "rig_health_guard.datasource.missing" in ids


def test_partial_collection_errors_are_flagged_by_health_guard():
    snapshot = _snapshot()
    snapshot["metadata"]["health"] = {
        "orphan_nodes": [],
        "_meta": {"collected": ["orphan_nodes"],
                  "errors": {"references": "RuntimeError: boom"}},
    }
    result = runner.run_tool("rig_health_guard", snapshot, dcc="maya",
                             adapter=NullAdapter())
    ids = [item.rule_id for item in result.report.findings]
    assert "rig_health_guard.datasource.partial" in ids


def test_health_meta_is_not_mistaken_for_a_health_key():
    snapshot = _snapshot()
    snapshot["metadata"]["health"] = {"_meta": {"collected": []}}
    report = _run(["rig_health_guard.orphan_nodes"], snapshot).report
    gaps = report.metadata["health_gaps"]
    assert [item["health_key"] for item in gaps] == ["orphan_nodes"]
