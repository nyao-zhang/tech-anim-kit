# -*- coding: utf-8 -*-
"""内置规则集与执行引擎回归：good 漏报 0 / 误报 0，bad 全部命中。"""
import copy
import json
import os

import pytest

from tak.adapters.base import NullAdapter
from tak.core.diff import SkeletonSnapshot
from tak.core.report import Report
from tak.core.rules import (RuleContext, Severity, evaluate_snapshot,
                            list_rule_ids, list_rules, rule, run_rules)
from tak.core.rules.base import RuleContext as BaseRuleContext
from tak.core.rules.base import RULE_REGISTRY
from tak.core.spec import load_spec

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FIXTURES = os.path.join(_ROOT, "fixtures")
_BUILTIN = "builtin"
_BUILTIN_IDS = [item["id"] for item in list_rules() if item["plugin_id"] == _BUILTIN]
_BLOCKING = (Severity.ERROR, Severity.BLOCKER)


def _load(folder):
    items = []
    path = os.path.join(_FIXTURES, folder)
    for name in sorted(os.listdir(path)):
        if name.endswith(".json"):
            with open(os.path.join(path, name), "r", encoding="utf-8") as fh:
                items.append(json.load(fh))
    return items


def _evaluate(fixture, dcc="maya"):
    return evaluate_snapshot(fixture["snapshot"], spec=load_spec(), dcc=dcc,
                             rule_ids=_BUILTIN_IDS)


def _errors(report):
    return [f for f in report.findings if f.severity in _BLOCKING]


def test_builtin_rule_count_and_ids_match_fixture_vocabulary():
    ids = set(_BUILTIN_IDS)
    assert len(ids) >= 10
    assert len(ids) == len(_BUILTIN_IDS)
    expected = {kind for fixture in _load("bad") for kind in fixture["expected_violations"]}
    assert expected <= ids, "缺少规则: %s" % sorted(expected - ids)
    assert ids <= set(list_rule_ids()), "list_rule_ids 与 list_rules 不一致"


@pytest.mark.parametrize("fixture", _load("good"), ids=lambda f: f["id"])
def test_good_fixtures_have_no_error(fixture):
    report = _evaluate(fixture)
    assert _errors(report) == [], "%s: %s" % (
        fixture["id"], [(f.rule_id, f.node) for f in _errors(report)])


@pytest.mark.parametrize("fixture", _load("bad"), ids=lambda f: f["id"])
def test_bad_fixtures_hit_declared_violations(fixture):
    report = _evaluate(fixture)
    error_ids = {f.rule_id for f in _errors(report)}
    assert error_ids, "%s 未命中任何 ERROR" % fixture["id"]
    missing = set(fixture["expected_violations"]) - error_ids
    assert not missing, "%s 漏报 %s，实际 %s" % (fixture["id"], sorted(missing),
                                             sorted(error_ids))


def test_fixture_regression_counts_are_zero():
    missed, false_alarm = 0, 0
    for fixture in _load("bad"):
        error_ids = {f.rule_id for f in _errors(_evaluate(fixture))}
        missed += len(set(fixture["expected_violations"]) - error_ids)
    for fixture in _load("good"):
        false_alarm += len(_errors(_evaluate(fixture)))
    assert (missed, false_alarm) == (0, 0)


def test_rules_return_report_compatible_structure():
    report = evaluate_snapshot(_load("bad")[0]["snapshot"], dcc="maya",
                               rule_ids=["naming.joint_pattern"])
    assert isinstance(report, Report)
    payload = report.to_dict()
    assert set(["title", "dcc", "passed", "counts", "findings"]) <= set(payload)
    assert payload["passed"] is False
    assert payload["counts"]["error"] == len(report.findings)
    assert set(report.to_dict()["findings"][0]) == {
        "rule_id", "severity", "message", "node", "fix_hint", "data"}
    assert isinstance(report.to_json(), str)


def test_severity_sorting_is_stable_and_descending():
    fixture = _load("bad")[3]  # 缺 foot_R：ERROR + side_tokens WARNING
    report = _evaluate(fixture)
    order = {"blocker": 0, "error": 1, "warning": 2, "info": 3}
    ranks = [order[f.severity.value] for f in report.findings]
    assert len(set(ranks)) > 1
    assert ranks == sorted(ranks)
    again = _evaluate(fixture)
    assert [f.rule_id for f in report.findings] == [f.rule_id for f in again.findings]


def test_rule_id_filter_keeps_only_requested_rule():
    report = _evaluate(_load("bad")[7], dcc="maya")  # skin.max_influences
    assert report.findings
    filtered = evaluate_snapshot(_load("bad")[7]["snapshot"], spec=load_spec(),
                                 rule_ids="skin.max_influences")
    assert {f.rule_id for f in filtered.findings} == {"skin.max_influences"}
    assert filtered.metadata["filters"]["rule_ids"] == ["skin.max_influences"]


def test_dcc_filter_skips_rules_not_supporting_dcc():
    fixture = _load("bad")[7]  # skin.max_influences，仅创作端 DCC 生效
    authoring = _evaluate(fixture, dcc="maya")
    assert any(f.rule_id == "skin.max_influences" for f in authoring.findings)
    unreal = _evaluate(fixture, dcc="unreal")
    assert not any(f.rule_id == "skin.max_influences" for f in unreal.findings)
    assert all("unreal" in item["dcc"] for item in list_rules("unreal"))
    assert len(list_rules("unreal")) < len(list_rules())


def test_plugin_filter_isolates_builtin_rules():
    report = run_rules(RuleContext(snapshot=_load("good")[0]["snapshot"],
                                   spec=load_spec()), plugin_id=_BUILTIN)
    assert report.findings == []
    assert report.metadata["filters"]["plugin_id"] == _BUILTIN
    assert _BUILTIN in {item["plugin_id"] for item in list_rules()}
    assert all(item["plugin_id"] == _BUILTIN for item in list_rules()
               if item["id"] in _BUILTIN_IDS)


def test_rule_catalog_entries_are_complete():
    for item in list_rules():
        assert isinstance(item["id"], str) and item["id"]
        assert item["severity"] in {s.value for s in Severity}
        assert isinstance(item["description"], str) and item["description"]
        assert isinstance(item["dcc"], list) and item["dcc"]
        assert isinstance(item["fix_available"], bool)
        assert isinstance(item["plugin_id"], str)
    fixable = {item["id"] for item in list_rules() if item["fix_available"]}
    assert {"naming.joint_pattern", "naming.namespace",
            "skeleton.missing_required"} <= fixable


def test_fix_hint_is_structured_and_never_mutates_snapshot():
    fixture = _load("bad")[0]
    before = copy.deepcopy(fixture["snapshot"])
    report = _evaluate(fixture)
    assert fixture["snapshot"] == before, "规则不得修改场景快照"
    fixable = [f for f in report.findings if f.fix_hint]
    assert fixable
    for finding in fixable:
        assert isinstance(finding.fix_hint, str) and finding.fix_hint
        if "fix" in finding.data:
            assert finding.data["fix"]["op"]


def test_empty_snapshot_is_handled_deterministically():
    report = evaluate_snapshot({}, spec=load_spec(), rule_ids=_BUILTIN_IDS)
    assert all(f.severity in Severity for f in report.findings)
    assert report.passed is False  # 空场景必然缺必需关节
    assert report.counts()["error"] == len(
        [f for f in report.findings if f.severity is Severity.ERROR])
    again = evaluate_snapshot({}, spec=load_spec(), rule_ids=_BUILTIN_IDS)
    assert [(f.rule_id, f.node) for f in report.findings] == [
        (f.rule_id, f.node) for f in again.findings]


@pytest.mark.parametrize("snapshot", [
    {"joints": None},
    {"joints": []},
    {"joints": {"root": {}}},
    {"joints": {"root": {"parent": "ghost", "matrix": [1, 0, 0], "scale": "big"}}},
    {"joints": {"root": {"depth": "deep", "rotate": None, "namespace": 3}}},
    {"skin": "nope", "export": {"fbx": []}},
    {"unit": None, "up_axis": None, "frame_range": [1]},
    {"joints": {"A_L": {"parent": "A_R"}, "A_R": {"parent": "A_L"}}},
])
def test_malformed_snapshot_does_not_raise(snapshot):
    report = evaluate_snapshot(snapshot, spec=load_spec(), rule_ids=_BUILTIN_IDS)
    assert isinstance(report, Report)
    assert isinstance(report.to_dict()["counts"], dict)


def test_cycle_detection_reports_error():
    snapshot = {"joints": {"A_L": {"parent": "A_R", "depth": 0},
                           "A_R": {"parent": "A_L", "depth": 0}}}
    report = evaluate_snapshot(snapshot, spec=load_spec(), rule_ids=_BUILTIN_IDS)
    assert any(f.rule_id == "skeleton.parent_integrity" for f in _errors(report))


def test_custom_spec_tightens_thresholds():
    spec = copy.deepcopy(load_spec())
    spec["skin"]["max_influences"] = 6
    fixture = _load("bad")[7]
    assert _errors(_evaluate(fixture))
    assert evaluate_snapshot(fixture["snapshot"], spec=spec,
                             rule_ids=_BUILTIN_IDS).findings == []
    spec["skeleton"]["allow_negative_scale"] = True
    assert evaluate_snapshot(_load("bad")[5]["snapshot"], spec=spec,
                             rule_ids=_BUILTIN_IDS).findings == []


def test_snapshot_object_and_base_context_are_accepted():
    fixture = _load("good")[0]
    snapshot_obj = SkeletonSnapshot.from_dict(fixture["snapshot"])
    assert evaluate_snapshot(snapshot_obj, spec=load_spec(),
                             rule_ids=_BUILTIN_IDS).findings == []

    ctx = BaseRuleContext(spec=load_spec(), adapter=NullAdapter(),
                          snapshot=fixture["snapshot"])
    report = run_rules(ctx, rule_ids=_BUILTIN_IDS)
    assert report.dcc == "maya"
    assert report.findings == []


def test_context_spec_defaults_to_repo_default_spec():
    report = run_rules(RuleContext(snapshot=_load("good")[0]["snapshot"]),
                       rule_ids=["naming.joint_pattern"])
    assert report.spec_version == load_spec()["version"]
    assert report.findings == []


def test_rule_run_records_duration_metadata():
    fixture = _load("bad")[0]
    report = _evaluate(fixture)
    assert report.findings
    assert all("duration_ms" in f.data for f in report.findings)
    assert all(f.data["duration_ms"] >= 0 for f in report.findings)


def test_engine_survives_crashing_rule():
    seen = {}

    def boom(context):
        raise RuntimeError("kaboom")

    registered = rule("test.crash_probe", severity=Severity.WARNING,
                      description="临时崩溃规则", dcc=["maya"],
                      plugin_id="test_probe")(boom)
    try:
        report = run_rules(RuleContext(snapshot={}, spec=load_spec()),
                           rule_ids=["test.crash_probe"])
        assert [f.rule_id for f in report.findings] == ["test.crash_probe"]
        assert report.findings[0].severity is Severity.ERROR
        assert "kaboom" in report.findings[0].message
    finally:
        RULE_REGISTRY[:] = [r for r in RULE_REGISTRY if r.fn is not boom]
        assert registered is boom
        assert all(r.id != "test.crash_probe" for r in RULE_REGISTRY)


def test_report_markdown_renders_findings():
    report = _evaluate(_load("bad")[0])
    text = report.to_markdown()
    assert "naming.joint_pattern" in text
    assert "不通过" in text
