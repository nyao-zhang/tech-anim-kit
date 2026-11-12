# -*- coding: utf-8 -*-
"""绑定 / 导出规范的加载与校验。"""
from .schema import ValidationError, load_schema, load_spec, validate, validate_file

__all__ = ["ValidationError", "load_schema", "load_spec", "validate", "validate_file"]
