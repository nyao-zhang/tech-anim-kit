# -*- coding: utf-8 -*-
"""受限 DSL：未注册动作、权限、dry-run、确认与整体回滚。"""
import pytest

from tak.adapters import NullAdapter
from tak.core.dsl import ActionRegistry, DSLError

PAYLOAD = {"actions": [{"tool": "set_name", "params": {"value": "CTRL_root"}}]}


def _registry(fn):
    reg = ActionRegistry()
    reg.register("set_name", fn, permissions=["node.write"], description="示例写操作")
    return reg


class RecordingAdapter(NullAdapter):
    def __init__(self, fail=False):
        super().__init__()
        self.events = []
        self.fail = fail

    def begin_undo(self, label):
        self.events.append("begin")
        return "token"

    def commit_undo(self, token):
        self.events.append("commit")

    def rollback(self, token):
        self.events.append("rollback")


def test_unregistered_action_is_rejected():
    reg = ActionRegistry()
    with pytest.raises(DSLError, match="未注册"):
        reg.build_plan(PAYLOAD)
    with pytest.raises(DSLError):
        reg.build_plan("not json at all")


def test_malformed_payload_is_rejected():
    reg = _registry(lambda adapter, value: None)
    for bad in ({"actions": "oops"}, {"actions": [{"params": {}}]}, []):
        with pytest.raises(DSLError):
            reg.build_plan(bad)


def test_dry_run_has_no_side_effects():
    calls = []
    reg = _registry(lambda adapter, value: calls.append(value))
    plan = reg.build_plan(PAYLOAD)
    result = reg.execute(plan, NullAdapter(), dry_run=True)
    assert result["dry_run"] is True
    assert result["committed"] is False
    assert result["changes"][0]["status"] == "pending"
    assert calls == []


def test_permission_required():
    reg = _registry(lambda adapter, value: None)
    plan = reg.build_plan(PAYLOAD)
    with pytest.raises(DSLError, match="缺少权限"):
        reg.execute(plan, NullAdapter(), dry_run=False, confirm=True)


def test_human_confirmation_required():
    reg = _registry(lambda adapter, value: None)
    plan = reg.build_plan(PAYLOAD)
    with pytest.raises(DSLError, match="confirm"):
        reg.execute(plan, NullAdapter(), granted=["node.write"], dry_run=False)


def test_commit_on_success():
    calls = []
    adapter = RecordingAdapter()
    reg = _registry(lambda adapter, value: calls.append(value))
    result = reg.execute(reg.build_plan(PAYLOAD), adapter,
                         granted=["node.write"], dry_run=False, confirm=True)
    assert result["committed"] is True
    assert calls == ["CTRL_root"]
    assert adapter.events == ["begin", "commit"]


def test_whole_plan_rolls_back_on_failure():
    adapter = RecordingAdapter()

    def boom(adapter, value):
        raise RuntimeError("host rejected")

    reg = _registry(boom)
    result = reg.execute(reg.build_plan(PAYLOAD), adapter,
                         granted=["node.write"], dry_run=False, confirm=True)
    assert result["committed"] is False
    assert "host rejected" in result["error"]
    assert adapter.events == ["begin", "rollback"]
    assert all(c["status"] == "rolled_back" for c in result["changes"])


def test_payload_can_be_json_text():
    import json
    reg = _registry(lambda adapter, value: None)
    plan = reg.build_plan(json.dumps(PAYLOAD))
    assert plan.actions[0].params["value"] == "CTRL_root"
