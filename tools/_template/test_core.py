# -*- coding: utf-8 -*-
"""插件自带单测模板。复制到插件目录后由 pytest 收集。"""
from __future__ import annotations

from tak.adapters import NullAdapter
from tak.core.rules.base import RuleContext
from tak.core.spec import load_spec

from core import TOOL_ID, info, run
from rules import scene_has_joints


def test_info():
    assert info()["id"] == TOOL_ID


def test_empty_scene_reports_error():
    context = RuleContext(spec=load_spec(), adapter=NullAdapter(), snapshot={"joints": {}})
    findings = scene_has_joints(context)
    assert findings and findings[0].severity.value == "error"


def test_run_collects_findings():
    context = RuleContext(spec=load_spec(), adapter=NullAdapter(), snapshot={"joints": {}})
    assert run(context)
