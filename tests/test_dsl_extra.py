# -*- coding: utf-8 -*-
"""DSL 参数声明、计划序列化、原子性与失败补偿的边界测试。"""
import json

import pytest

from tak.adapters import NullAdapter
from tak.core.dsl import Action, ActionRegistry, DSLError, ExecutionPlan


class RecordingAdapter(NullAdapter):
    def __init__(self, fail_begin=False, fail_commit=False, fail_rollback=False):
        super().__init__()
        self.events = []
        self.fail_begin = fail_begin
        self.fail_commit = fail_commit
        self.fail_rollback = fail_rollback

    def begin_undo(self, label):
        self.events.append("begin")
        if self.fail_begin:
            raise RuntimeError("begin exploded")
        return "token"

    def commit_undo(self, token):
        self.events.append("commit")
        if self.fail_commit:
            raise RuntimeError("commit exploded")

    def rollback(self, token):
        self.events.append("rollback")
        if self.fail_rollback:
            raise RuntimeError("rollback exploded")


def _numeric_registry(calls=None):
    reg = ActionRegistry()
    reg.register(
        "set_number",
        lambda adapter, value: calls.append(value) if calls is not None else None,
        parameters={"value": {"type": "number", "required": True, "min": 0, "max": 1}},
    )
    return reg


def test_plan_json_roundtrip_is_lossless_and_detached():
    reg = ActionRegistry()
    reg.register("query", lambda adapter, **params: None)
    plan = reg.build_plan({"actions": [{
        "tool": "query", "params": {"names": ["root", "hand_L"], "limit": 2},
    }]})

    restored = ExecutionPlan.from_json(plan.to_json())
    assert restored.to_dict() == plan.to_dict()
    exported = plan.to_dict()
    exported["actions"][0]["params"]["names"].append("extra")
    assert plan.actions[0].params["names"] == ["root", "hand_L"]


@pytest.mark.parametrize("params,message", [
    ({}, "必填"),
    ({"value": "0.5"}, "类型"),
    ({"value": -0.01}, "下限"),
    ({"value": 1.01}, "上限"),
    ({"value": 0.5, "extra": True}, "未声明"),
])
def test_declared_parameter_validation(params, message):
    with pytest.raises(DSLError, match=message):
        _numeric_registry().build_plan({"actions": [{"tool": "set_number", "params": params}]})


@pytest.mark.parametrize("value", [True, float("nan"), float("inf")])
def test_non_boolean_and_non_finite_number_boundaries(value):
    with pytest.raises(DSLError):
        _numeric_registry().build_plan({"actions": [{
            "tool": "set_number", "params": {"value": value},
        }]})


def test_invalid_later_action_prevents_whole_batch_from_starting():
    calls = []
    adapter = RecordingAdapter()
    reg = _numeric_registry()
    reg.register("first", lambda adapter: calls.append("first"))
    plan = ExecutionPlan([
        Action("first"),
        Action("set_number", {"value": 99}),
    ])

    with pytest.raises(DSLError, match="上限"):
        reg.execute(plan, adapter, dry_run=False, confirm=True)
    assert calls == []
    assert adapter.events == []


def test_dry_run_does_not_call_action_or_adapter():
    calls = []
    adapter = RecordingAdapter()
    reg = ActionRegistry()
    reg.register("read_scene", lambda adapter: calls.append("read"))
    plan = reg.build_plan({"actions": [{"tool": "read_scene"}]})

    result = reg.execute(plan, adapter, dry_run=True)
    assert result["required_permissions"] == []
    assert result["log"] == result["changes"]
    assert calls == []
    assert adapter.events == []
    json.dumps(result, allow_nan=False)


def test_read_only_action_needs_no_write_permission():
    reg = ActionRegistry()
    reg.register("read_scene", lambda adapter: "ok")
    plan = reg.build_plan({"actions": [{"tool": "read_scene"}]})

    result = reg.execute(plan, RecordingAdapter(), dry_run=False, confirm=True)
    assert result["committed"] is True
    assert result["required_permissions"] == []


def test_rollback_failure_reports_both_errors_and_is_serializable():
    reg = ActionRegistry()

    def fail(adapter):
        raise RuntimeError("write exploded")

    reg.register("write", fail, permissions=["node.write"])
    plan = reg.build_plan({"actions": [{"tool": "write"}]})
    result = reg.execute(
        plan, RecordingAdapter(fail_rollback=True), granted=["node.write"],
        dry_run=False, confirm=True,
    )

    assert result["committed"] is False
    assert result["rollback_failed"] is True
    assert "write exploded" in result["error"]
    assert "rollback exploded" in result["rollback_error"]
    assert {item["status"] for item in result["changes"]} == {"rollback_failed"}
    json.dumps(result, allow_nan=False)


def test_commit_failure_is_compensated_by_rollback():
    calls = []
    adapter = RecordingAdapter(fail_commit=True)
    reg = ActionRegistry()
    reg.register("write", lambda adapter: calls.append("write"))
    result = reg.execute(reg.build_plan({"actions": [{"tool": "write"}]}),
                         adapter, dry_run=False, confirm=True)

    assert result["committed"] is False
    assert "commit exploded" in result["error"]
    assert adapter.events == ["begin", "commit", "rollback"]
    assert result["changes"][0]["status"] == "rolled_back"


def test_begin_undo_failure_never_executes_action():
    calls = []
    adapter = RecordingAdapter(fail_begin=True)
    reg = ActionRegistry()
    reg.register("write", lambda adapter: calls.append("write"))
    result = reg.execute(reg.build_plan({"actions": [{"tool": "write"}]}),
                         adapter, dry_run=False, confirm=True)

    assert result["committed"] is False
    assert "开启 undo 失败" in result["error"]
    assert calls == []
    assert adapter.events == ["begin"]


def test_plan_rejects_non_json_native_containers_and_non_string_keys():
    reg = ActionRegistry()
    reg.register("query", lambda adapter, **params: None)
    for params in ({"names": ("root",)}, {1: "root"}):
        with pytest.raises(DSLError, match="JSON"):
            reg.build_plan({"actions": [{"tool": "query", "params": params}]})
