# -*- coding: utf-8 -*-
"""规范校验器。

DCC 内置 Python 环境通常没有 jsonschema 第三方包，因此这里实现一个覆盖本仓库
schema 所需关键字的轻量校验器，保证 Maya / Max / MoBu / UE 内可直接 import。

支持：type / required / properties / additionalProperties / items / enum /
      pattern / minimum / maximum / minItems / maxItems / uniqueItems。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_SCHEMA_PATH = os.path.join(_ROOT, "schema", "rig_spec.schema.json")
_SPEC_PATH = os.path.join(_ROOT, "schema", "rig_spec.default.json")

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


class ValidationError(ValueError):
    """规范实例不符合 schema 时抛出，message 中列出全部问题。"""

    def __init__(self, errors: List[str]):
        self.errors = list(errors)
        super().__init__("规范校验失败:\n  - " + "\n  - ".join(self.errors))


def load_schema(path: Optional[str] = None) -> Dict[str, Any]:
    """加载 JSON Schema。默认使用仓库内 schema/rig_spec.schema.json。"""
    with open(path or _SCHEMA_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_spec(path: Optional[str] = None, validate_spec: bool = True) -> Dict[str, Any]:
    """加载规范实例，并在返回前校验其合法性。"""
    with open(path or _SPEC_PATH, "r", encoding="utf-8") as fh:
        spec = json.load(fh)
    if validate_spec:
        errors = validate(spec)
        if errors:
            raise ValidationError(errors)
    return spec


def validate_file(path: str, schema: Optional[Dict[str, Any]] = None) -> List[str]:
    """校验磁盘上的规范文件，返回错误列表（空列表表示通过）。"""
    with open(path, "r", encoding="utf-8") as fh:
        return validate(json.load(fh), schema)


def validate(instance: Any, schema: Optional[Dict[str, Any]] = None) -> List[str]:
    """校验实例，返回人类可读的错误列表；不抛异常，便于批量报告。"""
    schema = schema if schema is not None else load_schema()
    errors: List[str] = []
    _check(instance, schema, "$", errors)
    return errors


def _check(value: Any, schema: Dict[str, Any], path: str, errors: List[str]) -> None:
    if "type" in schema:
        expected = schema["type"]
        py_type = _TYPES.get(expected)
        if py_type is None:
            errors.append("%s: 未知类型 %r" % (path, expected))
            return
        if expected == "integer" and isinstance(value, bool):
            errors.append("%s: 期望 integer，实际 bool" % path)
            return
        if expected in ("number", "integer") and isinstance(value, bool):
            errors.append("%s: 期望 %s，实际 bool" % (path, expected))
            return
        if not isinstance(value, py_type):
            errors.append("%s: 期望 %s，实际 %s" % (path, expected, type(value).__name__))
            return

    if "enum" in schema and value not in schema["enum"]:
        errors.append("%s: %r 不在允许值 %r 中" % (path, value, schema["enum"]))

    if isinstance(value, str) and "pattern" in schema:
        if re.search(schema["pattern"], value) is None:
            errors.append("%s: %r 不匹配正则 %s" % (path, value, schema["pattern"]))

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append("%s: %s 小于最小值 %s" % (path, value, schema["minimum"]))
        if "maximum" in schema and value > schema["maximum"]:
            errors.append("%s: %s 大于最大值 %s" % (path, value, schema["maximum"]))

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append("%s: 缺少必填字段 %r" % (path, key))
        properties = schema.get("properties", {})
        for key, child in value.items():
            if key in properties:
                _check(child, properties[key], "%s.%s" % (path, key), errors)
            elif schema.get("additionalProperties") is False:
                errors.append("%s: 不允许的字段 %r" % (path, key))

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append("%s: 元素少于 %s 个" % (path, schema["minItems"]))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append("%s: 元素多于 %s 个" % (path, schema["maxItems"]))
        if schema.get("uniqueItems"):
            try:
                if len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
                    errors.append("%s: 存在重复元素" % path)
            except TypeError:
                pass
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                _check(item, item_schema, "%s[%d]" % (path, index), errors)
