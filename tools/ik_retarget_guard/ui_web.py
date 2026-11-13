# -*- coding: utf-8 -*-
"""IK Retarget Guard 的 Web 面板 HTML 片段。

仅使用 ``tak/ui/theme.css`` 的 tak-* 类名；不写行内样式、字面颜色或外链。
``render_panel_html`` 接受 ToolResult、Report、ToolResult.to_dict() 以及
``inspect_retarget`` 返回字典。所有动态文本均 HTML 转义。
"""
from __future__ import annotations

import html
from typing import Any, Dict, List, Tuple

TOOL_ID = "ik_retarget_guard"
_SEVERITIES = ("blocker", "error", "warning", "info")


def _get(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else getattr(value, key, None)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _escape(value: Any) -> str:
    return html.escape(_text(value), quote=True)


def _split(result: Any) -> Tuple[Any, Dict[str, Any]]:
    if isinstance(result, dict):
        return result, result
    report = getattr(result, "report", None)
    view: Dict[str, Any] = {}
    if hasattr(result, "to_dict"):
        try:
            value = result.to_dict()
            view = value if isinstance(value, dict) else {}
        except Exception:
            view = {}
    return report if report is not None else result, view


def _metadata(report: Any, view: Dict[str, Any]) -> Dict[str, Any]:
    metadata = view.get("metadata")
    if isinstance(metadata, dict):
        return metadata
    if isinstance(report, dict) and isinstance(report.get("mapping"), dict):
        return report  # inspect_retarget 的直接返回值
    metadata = _get(report, "metadata")
    return metadata if isinstance(metadata, dict) else {}


def _findings(report: Any, view: Dict[str, Any]) -> List[Any]:
    findings = view.get("findings")
    if isinstance(findings, list):
        return findings
    findings = _get(report, "findings")
    return list(findings) if isinstance(findings, list) else []


def _severity(finding: Any) -> str:
    value = _get(finding, "severity")
    value = value.value if hasattr(value, "value") else str(value or "info")
    return value if value in _SEVERITIES else "info"


def _counts(findings: List[Any]) -> Dict[str, int]:
    counts = {name: 0 for name in _SEVERITIES}
    for item in findings:
        counts[_severity(item)] += 1
    return counts


def _passed(report: Any, view: Dict[str, Any], findings: List[Any]) -> bool:
    value = view.get("passed")
    if isinstance(value, bool):
        return value
    value = _get(report, "passed")
    if isinstance(value, bool):
        return value
    return not any(_severity(item) in {"blocker", "error"} for item in findings)


def _chain_section(rows: Any) -> str:
    if not isinstance(rows, list):
        rows = []
    body: List[str] = []
    labels = {"mapped": "已映射", "missing_mapping": "缺少映射",
              "unknown_source": "未知源链"}
    for item in rows:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "missing_mapping")
        severity = "tak-sev-info" if status == "mapped" else "tak-sev-error"
        body.append(
            '<tr class="tak-row %s"><td><code>%s</code></td><td><code>%s</code></td>'
            '<td class="tak-badge">%s</td></tr>' % (
                severity, _escape(item.get("source_chain")),
                _escape(item.get("target_chain") or "—"),
                _escape(labels.get(status, status))))
    if not body:
        body.append('<tr class="tak-row tak-sev-info"><td colspan="3">未提供链映射数据，已跳过</td></tr>')
    return (
        '<section><h2>IK 链映射</h2><table class="tak-table"><thead><tr>'
        '<th>源链</th><th>目标链</th><th>状态</th></tr></thead><tbody>%s</tbody>'
        '</table></section>' % "".join(body))


def _mapping_section(mapping: Any) -> str:
    data = mapping if isinstance(mapping, dict) else {}
    entries = data.get("entries") if isinstance(data.get("entries"), list) else []
    rows: List[str] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        try:
            confidence = "%.2f" % float(item.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = "0.00"
        rows.append(
            '<tr class="tak-row tak-sev-info"><td><code>%s</code></td>'
            '<td><code>%s</code></td><td class="tak-badge">%s</td><td>%s</td></tr>' % (
                _escape(item.get("source")), _escape(item.get("target")),
                confidence, _escape(item.get("method"))))
    if not rows:
        rows.append('<tr class="tak-row tak-sev-info"><td colspan="4">无自动候选；请查看未映射项或冲突</td></tr>')
    unmapped_source = "、".join(_text(item) for item in (data.get("unmapped_source") or [])) or "无"
    unmapped_target = "、".join(_text(item) for item in (data.get("unmapped_target") or [])) or "无"
    return (
        '<section><h2>骨骼候选映射</h2><table class="tak-table"><thead><tr>'
        '<th>Source</th><th>Target</th><th>置信度</th><th>方法</th></tr></thead>'
        '<tbody>%s</tbody></table><p class="tak-hint">未映射 Source：%s<br>'
        '未映射 Target：%s</p></section>' % (
            "".join(rows), _escape(unmapped_source), _escape(unmapped_target)))


def _conflicts_section(mapping: Any) -> str:
    data = mapping if isinstance(mapping, dict) else {}
    conflicts = data.get("conflicts") if isinstance(data.get("conflicts"), list) else []
    rows: List[str] = []
    for item in conflicts:
        if not isinstance(item, dict):
            continue
        sources = item.get("sources") or ([item.get("source")] if item.get("source") else [])
        targets = item.get("targets") or ([item.get("target")] if item.get("target") else [])
        rows.append(
            '<tr class="tak-row tak-sev-error"><td class="tak-badge">CONFLICT</td>'
            '<td><code>%s</code></td><td><code>%s</code></td><td>%s</td></tr>' % (
                _escape("、".join(_text(value) for value in sources)),
                _escape("、".join(_text(value) for value in targets)),
                _escape(item.get("message") or item.get("type"))))
    if not rows:
        return '<section><h2>映射冲突</h2><p class="tak-meta">无冲突</p></section>'
    return (
        '<section><h2>映射冲突</h2><table class="tak-table"><thead><tr><th>级别</th>'
        '<th>Source</th><th>Target</th><th>说明</th></tr></thead><tbody>%s</tbody>'
        '</table></section>' % "".join(rows))


def _findings_section(findings: List[Any]) -> str:
    rows: List[str] = []
    for item in findings:
        severity = _severity(item)
        hint = _text(_get(item, "fix_hint"))
        rows.append(
            '<tr class="tak-row tak-sev-%s"><td class="tak-badge">%s</td>'
            '<td><code>%s</code></td><td><code>%s</code></td><td>%s%s</td></tr>' % (
                severity, severity.upper(), _escape(_get(item, "rule_id")),
                _escape(_get(item, "node")), _escape(_get(item, "message")),
                ('<span class="tak-hint">%s</span>' % _escape(hint)) if hint else ""))
    body = "".join(rows) or '<tr class="tak-row tak-sev-info"><td colspan="4">无异常</td></tr>'
    return (
        '<section><h2>规则结果</h2><table class="tak-table"><thead><tr><th>级别</th>'
        '<th>规则</th><th>节点</th><th>说明</th></tr></thead><tbody>%s</tbody>'
        '</table></section>' % body)


def render_panel_html(result: Any) -> str:
    """渲染链映射、候选置信度、冲突及规则结果，返回无外链的 HTML 片段。"""
    report, view = _split(result)
    metadata = _metadata(report, view)
    findings = _findings(report, view)
    counts = _counts(findings)
    passed = _passed(report, view, findings)
    title = _text(_get(report, "title")) or "IK Retarget Guard 重定向映射校验"
    dcc = _text(_get(report, "dcc")) or "unreal"
    state = "tak-state-pass" if passed else "tak-state-fail"
    label = "通过" if passed else "不通过"
    summary = " · ".join("%s=%d" % (name, counts[name]) for name in _SEVERITIES)
    handoff = metadata.get("handoff")
    handoff_text = handoff.get("note") if isinstance(handoff, dict) else \
        "mapping asset 可交由 P5.2 Skeleton Mapper 消费"
    parts = [
        '<section class="tak-panel" data-tool="%s">' % TOOL_ID,
        '<header class="tak-header"><h1>%s</h1><span class="tak-state %s">%s</span>'
        '</header><p class="tak-meta">DCC %s · 规则 %d · %s</p>' % (
            _escape(title), state, label, _escape(dcc),
            int(metadata.get("rules_count") or 0), summary),
        _chain_section(metadata.get("chain_mapping")),
        _mapping_section(metadata.get("mapping")),
        _conflicts_section(metadata.get("mapping")),
        _findings_section(findings),
        '<p class="tak-hint">P5.2 交接：%s</p>' % _escape(handoff_text),
        '</section>',
    ]
    return "".join(parts)


def render_report_html(result: Any) -> str:
    """保留与 Report 面板一致的入口；完整面板同样只返回片段。"""
    return render_panel_html(result)
