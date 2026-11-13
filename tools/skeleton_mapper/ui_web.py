# -*- coding: utf-8 -*-
"""Skeleton Mapper 的 Web 面板 HTML 片段。

仅使用 ``tak/ui/theme.css`` 的 tak-* 类名；不写行内样式、字面颜色、CDN 或
任何外链。``render_panel_html`` 可接受 ``map_skeleton`` 的返回字典、Report、
ToolResult 及二者的 ``to_dict()`` 形态。所有动态文本均经 html.escape 转义。

面板展示映射表（源 / 目标 / 语义 / 置信度 / 来源）、冲突、未解析项，并明确
提示：冲突与低置信度项绝不自动选择，必须人工确认后才能写入下游。
"""
from __future__ import annotations

import html
from typing import Any, Dict, List

TOOL_ID = "skeleton_mapper"


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _escape(value: Any) -> str:
    return html.escape(_text(value), quote=True)


def _view(result: Any) -> Dict[str, Any]:
    """从多种 result 形态里提取出含 entries / conflicts / unresolved 的映射字典。"""
    candidates: List[Any] = []
    if isinstance(result, dict):
        if "entries" in result and ("conflicts" in result or "unresolved" in result):
            return result
        candidates = [result, result.get("metadata")]
    else:
        if hasattr(result, "to_dict"):
            try:
                candidates.append(result.to_dict())
            except Exception:
                pass
        report = getattr(result, "report", None)
        if report is not None:
            candidates.append(getattr(report, "metadata", None))
        candidates.append(getattr(result, "metadata", None))
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if "entries" in candidate and ("conflicts" in candidate
                                       or "unresolved" in candidate):
            return candidate
        metadata = candidate.get("metadata")
        if isinstance(metadata, dict):
            mapping = metadata.get("mapping")
            if isinstance(mapping, dict) and "entries" in mapping:
                return mapping
    return {}


def _origin_label(item: Dict[str, Any]) -> str:
    origin = _text(item.get("origin") or item.get("method") or "dictionary")
    provider = item.get("provider")
    if origin == "ai" and provider:
        return "ai (%s)" % _text(provider)
    return origin


def _confidence_label(item: Dict[str, Any]) -> str:
    try:
        return "%.2f" % float(item.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return "0.00"


def _mapping_section(data: Dict[str, Any]) -> str:
    entries = data.get("entries")
    entries = entries if isinstance(entries, list) else []
    rows: List[str] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        status = _text(item.get("status") or "auto")
        severity = "tak-sev-info" if status == "auto" else "tak-sev-warning"
        rows.append(
            '<tr class="tak-row %s"><td><code>%s</code></td>'
            '<td><code>%s</code></td><td class="tak-badge">%s</td><td>%s</td>'
            '<td>%s</td></tr>' % (
                severity, _escape(item.get("source")), _escape(item.get("target")),
                _escape(item.get("semantic") or "—"),
                _escape(_confidence_label(item)),
                _escape(_origin_label(item))))
    if not rows:
        rows.append('<tr class="tak-row tak-sev-info"><td colspan="5">'
                    '无自动候选；请查看冲突与未解析项</td></tr>')
    return (
        '<section><h2>骨骼语义映射表</h2><table class="tak-table"><thead><tr>'
        '<th>源骨骼</th><th>目标骨骼</th><th>语义</th><th>置信度</th>'
        '<th>来源</th></tr></thead><tbody>%s</tbody></table></section>'
        % "".join(rows))


def _conflicts_section(data: Dict[str, Any]) -> str:
    conflicts = data.get("conflicts")
    conflicts = conflicts if isinstance(conflicts, list) else []
    rows: List[str] = []
    for item in conflicts:
        if not isinstance(item, dict):
            continue
        sources = item.get("sources") or ([item.get("source")]
                                          if item.get("source") else [])
        targets = item.get("targets") or ([item.get("target")]
                                          if item.get("target") else [])
        source_text = "、".join(_text(value) for value in sources) or \
            _text(item.get("canonical") or "—")
        target_text = "、".join(_text(value) for value in targets) or "—"
        rows.append(
            '<tr class="tak-row tak-sev-error"><td class="tak-badge">CONFLICT</td>'
            '<td><code>%s</code></td><td><code>%s</code></td>'
            '<td><code>%s</code></td><td>%s</td></tr>' % (
                _escape(item.get("type") or "conflict"),
                _escape(source_text), _escape(target_text),
                _escape(item.get("message") or "")))
    if not rows:
        return '<section><h2>映射冲突</h2><p class="tak-meta">无冲突</p></section>'
    return (
        '<section><h2>映射冲突（不自动裁决）</h2><table class="tak-table">'
        '<thead><tr><th>级别</th><th>类型</th><th>源</th><th>目标</th>'
        '<th>说明</th></tr></thead><tbody>%s</tbody></table></section>'
        % "".join(rows))


def _unresolved_section(data: Dict[str, Any]) -> str:
    unresolved = data.get("unresolved")
    unresolved = unresolved if isinstance(unresolved, list) else []
    rows: List[str] = []
    for item in unresolved:
        if not isinstance(item, dict):
            continue
        rows.append(
            '<tr class="tak-row tak-sev-warning"><td><code>%s</code></td>'
            '<td class="tak-badge">%s</td><td>%s</td><td>%s</td></tr>' % (
                _escape(item.get("bone")), _escape(item.get("side") or "—"),
                _escape(item.get("semantic") or "—"),
                _escape(item.get("reason") or "unmapped")))
    if not rows:
        return '<section><h2>未解析 / 待确认项</h2><p class="tak-meta">无</p></section>'
    return (
        '<section><h2>未解析 / 待确认项</h2><table class="tak-table">'
        '<thead><tr><th>骨骼</th><th>侧别</th><th>语义</th><th>原因</th>'
        '</tr></thead><tbody>%s</tbody></table></section>' % "".join(rows))


def _warnings_section(data: Dict[str, Any]) -> str:
    warnings = data.get("warnings")
    warnings = warnings if isinstance(warnings, list) else []
    if not warnings:
        return ""
    items = "".join('<li>%s</li>' % _escape(item) for item in warnings)
    return '<section><h2>提示</h2><ul class="tak-list">%s</ul></section>' % items


def _handoff_section(data: Dict[str, Any]) -> str:
    metadata = data.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    handoff = metadata.get("handoff")
    note = handoff.get("note") if isinstance(handoff, dict) else ""
    hint = note or "冲突与低置信度项不会自动写入，请在 DCC 内人工确认后再应用到 IK Retargeter。"
    return (
        '<p class="tak-hint">人工确认：%s</p>'
        '<p class="tak-hint">P5.2 交接：%s</p>' % (
            _escape("冲突绝不自动选择；AI 候选只作建议，须人工确认后才写入下游。"),
            _escape(hint)))


def render_panel_html(result: Any) -> str:
    """渲染映射表、冲突、未解析项与人工确认提示，返回无外链的 HTML 片段。"""
    data = _view(result)
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    consumers = metadata.get("consumers") or []
    stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
    summary = " · ".join(
        "%s=%s" % (_escape(key), _escape(value))
        for key, value in sorted(stats.items())
        if isinstance(key, str) and not isinstance(value, (dict, list)))
    parts = [
        '<section class="tak-panel" data-tool="%s">' % TOOL_ID,
        '<header class="tak-header"><h1>%s</h1></header>' % _escape(
            "Skeleton Mapper 骨骼语义映射"),
        '<p class="tak-meta">消费方 %s · %s</p>' % (
            _escape("、".join(_text(value) for value in consumers) or "—"),
            summary or "—"),
        _mapping_section(data),
        _conflicts_section(data),
        _unresolved_section(data),
        _warnings_section(data),
        _handoff_section(data),
        '</section>',
    ]
    return "".join(parts)


def render_report_html(result: Any) -> str:
    """保留与 Report 面板一致的入口；完整面板同样只返回片段。"""
    return render_panel_html(result)
