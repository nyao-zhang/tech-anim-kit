# -*- coding: utf-8 -*-
"""报告模型：同一份结果渲染为 JSON / Markdown / HTML。

HTML 使用 tak/ui/theme.css 的类名，与 Qt 端共用一套设计 token。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List

from tak.core.rules.base import Finding, Severity
from tak.version import __version__

_BLOCKING = {Severity.ERROR.value, Severity.BLOCKER.value}


@dataclass
class Report:
    title: str
    dcc: str
    findings: List[Finding] = field(default_factory=list)
    spec_version: str = "1.0.0"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add(self, finding: Finding) -> "Report":
        self.findings.append(finding)
        return self

    def extend(self, findings: List[Finding]) -> "Report":
        self.findings.extend(findings)
        return self

    def counts(self) -> Dict[str, int]:
        result = {s.value: 0 for s in Severity}
        for item in self.findings:
            result[item.severity.value] += 1
        return result

    @property
    def passed(self) -> bool:
        return all(f.severity.value not in _BLOCKING for f in self.findings)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "dcc": self.dcc,
            "toolkit_version": __version__,
            "spec_version": self.spec_version,
            "passed": self.passed,
            "counts": self.counts(),
            "metadata": self.metadata,
            "findings": [f.to_dict() for f in self.findings],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_markdown(self) -> str:
        lines = ["# %s" % self.title, "",
                 "- DCC: `%s`" % self.dcc,
                 "- 规范版本: `%s`" % self.spec_version,
                 "- 结论: **%s**" % ("通过" if self.passed else "不通过"),
                 "- 统计: %s" % ", ".join("%s=%d" % kv for kv in self.counts().items()), ""]
        if not self.findings:
            lines.append("无异常。")
        for item in self.findings:
            node = (" `%s`" % item.node) if item.node else ""
            lines.append("- **[%s]**%s `%s` — %s%s" % (
                item.severity.value.upper(), node, item.rule_id, item.message,
                ("（修复建议：%s）" % item.fix_hint) if item.fix_hint else ""))
        return "\n".join(lines) + "\n"

    def to_html(self, theme_href: str = "tak/ui/theme.css") -> str:
        rows = []
        for item in self.findings:
            hint = ('<span class="tak-hint">%s</span>' % _escape(item.fix_hint)) if item.fix_hint else ""
            rows.append(
                '<tr class="tak-row tak-sev-%s"><td class="tak-badge">%s</td>'
                '<td><code>%s</code></td><td><code>%s</code></td><td>%s%s</td></tr>' % (
                    item.severity.value, item.severity.value.upper(),
                    _escape(item.rule_id), _escape(item.node),
                    _escape(item.message), hint))
        state = "tak-state-pass" if self.passed else "tak-state-fail"
        label = "通过" if self.passed else "不通过"
        return (
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<link rel="stylesheet" href="%s"><title>%s</title></head><body>'
            '<main class="tak-panel"><header class="tak-header"><h1>%s</h1>'
            '<span class="tak-state %s">%s</span></header>'
            '<section class="tak-meta">DCC %s · 规范 %s · %s</section>'
            '<table class="tak-table"><thead><tr><th>级别</th><th>规则</th>'
            '<th>节点</th><th>说明</th></tr></thead><tbody>%s</tbody></table>'
            '</main></body></html>' % (
                theme_href, _escape(self.title), _escape(self.title), state, label,
                _escape(self.dcc), _escape(self.spec_version),
                ", ".join("%s=%d" % kv for kv in self.counts().items()),
                "".join(rows) or '<tr><td colspan="4">无异常</td></tr>'))


def _escape(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
