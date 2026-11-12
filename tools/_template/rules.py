# -*- coding: utf-8 -*-
"""插件自带的确定性规则。导入时自动注册到全局 RULE_REGISTRY。"""
from __future__ import annotations

from typing import List

from tak.core.rules.base import Finding, RuleContext, Severity, rule

TOOL_ID = "__TEMPLATE_ID__"


@rule(TOOL_ID + ".scene_has_joints", severity=Severity.ERROR,
      description="场景必须包含至少一根骨骼", plugin_id=TOOL_ID)
def scene_has_joints(context: RuleContext) -> List[Finding]:
    snapshot = context.snapshot or {}
    joints = snapshot.get("joints", {})
    if joints:
        return []
    return [Finding(
        rule_id=TOOL_ID + ".scene_has_joints",
        severity=Severity.ERROR,
        message="场景中没有任何骨骼，无法进行绑定体检",
        fix_hint="先在 DCC 中建立骨架，或检查 adapter.get_skeleton_snapshot 的实现",
        data={"joint_count": 0},
    )]
