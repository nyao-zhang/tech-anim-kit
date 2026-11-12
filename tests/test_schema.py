# -*- coding: utf-8 -*-
"""规范 schema 与轻量校验器的回归测试（不依赖 jsonschema 第三方包）。"""
import copy

import pytest

from tak.core.spec import ValidationError, load_schema, load_spec, validate


def test_default_spec_is_valid():
    spec = load_spec()
    assert spec["version"] == "1.0.0"
    assert validate(spec) == []


def test_schema_itself_has_expected_sections():
    schema = load_schema()
    for key in ("dcc", "naming", "skeleton", "skin", "export"):
        assert key in schema["properties"]


def test_missing_required_field():
    spec = copy.deepcopy(load_spec(validate_spec=False))
    del spec["skin"]
    assert any("缺少必填字段 'skin'" in e for e in validate(spec))


def test_rejects_unknown_field():
    spec = copy.deepcopy(load_spec(validate_spec=False))
    spec["unexpected"] = True
    assert any("不允许的字段" in e for e in validate(spec))


def test_rejects_bad_enum_and_version_pattern():
    spec = copy.deepcopy(load_spec(validate_spec=False))
    spec["export"]["fbx"]["up_axis"] = "X"
    spec["version"] = "v1"
    errors = validate(spec)
    assert any("不在允许值" in e for e in errors)
    assert any("不匹配正则" in e for e in errors)


def test_rejects_bad_types_and_out_of_range():
    spec = copy.deepcopy(load_spec(validate_spec=False))
    spec["skin"]["max_influences"] = 99
    spec["dcc"]["targets"] = ["maya", "blender"]
    errors = validate(spec)
    assert any("大于最大值" in e for e in errors)
    assert any("不在允许值" in e for e in errors)


def test_load_spec_raises_on_invalid_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": "1.0.0"}', encoding="utf-8")
    with pytest.raises(ValidationError):
        load_spec(str(bad))
