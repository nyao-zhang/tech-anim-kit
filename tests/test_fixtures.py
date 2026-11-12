# -*- coding: utf-8 -*-
"""Golden fixtures 回归：20 好 + 20 坏，判定器必须漏报 0 / 误报 0。"""
import json
import os

import pytest

from tak.core.diff import SkeletonSnapshot, diff_snapshots, validate_snapshot
from tak.core.spec import load_spec

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FIXTURES = os.path.join(_ROOT, "fixtures")


def _load(folder):
    items = []
    path = os.path.join(_FIXTURES, folder)
    for name in sorted(os.listdir(path)):
        if name.endswith(".json"):
            with open(os.path.join(path, name), "r", encoding="utf-8") as fh:
                items.append(json.load(fh))
    return items


@pytest.mark.parametrize("folder,expected_count", [("good", 20), ("bad", 20)])
def test_fixture_counts(folder, expected_count):
    assert len(_load(folder)) == expected_count


@pytest.mark.parametrize("fixture", _load("good"), ids=lambda f: f["id"])
def test_good_fixtures_have_no_violations(fixture):
    spec = load_spec()
    snapshot = SkeletonSnapshot.from_dict(fixture["snapshot"])
    assert validate_snapshot(snapshot, spec) == [], fixture["id"]


@pytest.mark.parametrize("fixture", _load("bad"), ids=lambda f: f["id"])
def test_bad_fixtures_match_declared_violations(fixture):
    spec = load_spec()
    snapshot = SkeletonSnapshot.from_dict(fixture["snapshot"])
    detected = validate_snapshot(snapshot, spec)
    assert detected == fixture["expected_violations"], (
        "%s: 期望 %s，实际 %s" % (fixture["id"], fixture["expected_violations"], detected))


def test_diff_detects_joint_and_axis_changes():
    before = SkeletonSnapshot.from_dict(_load("good")[0]["snapshot"])
    after = SkeletonSnapshot.from_dict(_load("good")[0]["snapshot"])
    assert diff_snapshots(before, after) == []
    after.joints.pop("foot_R")
    after.up_axis = "Z"
    fields = {d["field"] for d in diff_snapshots(before, after)}
    assert {"joint_count", "joint_missing", "up_axis"} <= fields


def test_generator_is_reproducible():
    from fixtures.generate_fixtures import build
    first, second = build(), build()
    assert first == second
    assert len(first["good"]) + len(first["bad"]) == 40
