# -*- coding: utf-8 -*-
"""VLM Defect Inspector 的 Web 面板 HTML 片段（P5.3）。

约束
----
* 只使用 tak-* 主题类名；不写任何颜色字面值、行内样式或外链；
  如需主题色一律由 ``var(--tak-*)`` 提供（本片段不直接书写颜色）；
* 无外部样式表、无脚本标签、无内容分发网络、无外部 URL；
* 所有动态文本经 ``html.escape(..., quote=True)`` 完整转义；
* 展示帧号/类别/骨骼/置信度/证据/来源、被拒绝项、warnings 与离线降级说明。

``render_panel_html`` / ``render_report_html`` 接收 ``inspect_frames`` 结果 dict、
``ToolResult`` / ``Report`` / ``ToolResult.to_dict()`` 等形态，返回 HTML 片段。
"""
from __future__ import annotations

import html
from typing import Any, Dict, List

TOOL_ID = "defect_inspector"
_DEGRADED_SOURCE = "deterministic_fallback"


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _esc(value: Any) -> str:
    return html.escape(_text(value), quote=True)


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _view(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        return result
    if hasattr(result, "to_dict"):
        try:
            data = result.to_dict()
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _metadata(result: Any, view: Dict[str, Any]) -> Dict[str, Any]:
    meta = view.get("metadata")
    if isinstance(meta, dict):
        return meta
    report = getattr(result, "report", result)
    meta = _get(report, "metadata")
    return meta if isinstance(meta, dict) else {}


def _pick(view: Dict[str, Any], meta: Dict[str, Any], key: str,
          default: Any = None) -> Any:
    if key in view:
        return view[key]
    if key in meta:
        return meta[key]
    return default


def _collect(result: Any) -> Dict[str, Any]:
    view = _view(result)
    meta = _metadata(result, view)

    candidates = _pick(view, meta, "candidates", [])
    if not isinstance(candidates, list):
        candidates = []
    rejected = _pick(view, meta, "rejected", [])
    if not isinstance(rejected, list):
        rejected = []
    warnings = _pick(view, meta, "warnings", [])
    if not isinstance(warnings, list):
        warnings = []
    stats = _pick(view, meta, "stats", {})
    if not isinstance(stats, dict):
        stats = {}
    source = _text(_pick(view, meta, "source", ""))
    provider = _text(_pick(view, meta, "provider", ""))
    model = _text(_pick(view, meta, "model", ""))
    deterministic = bool(_pick(view, meta, "deterministic", False))
    frames_checked = _pick(view, meta, "frames_checked", 0)
    try:
        frames_checked = int(frames_checked or 0)
    except (TypeError, ValueError):
        frames_checked = 0

    report = getattr(result, "report", result)
    title = _text(view.get("title") or _get(report, "title")) \
        or "VLM Defect Inspector 抽帧缺陷诊断"
    dcc = _text(view.get("dcc") or _get(report, "dcc")) or "unreal"
    return {
        "candidates": candidates, "rejected": rejected, "warnings": warnings,
        "stats": stats, "source": source, "provider": provider, "model": model,
        "deterministic": deterministic, "frames_checked": frames_checked,
        "title": title, "dcc": dcc,
        "degraded": deterministic or source == _DEGRADED_SOURCE,
    }


def _conf(value: Any) -> str:
    try:
        return "%.2f" % float(value)
    except (TypeError, ValueError):
        return "0.00"


def _summarize(preview: Any) -> str:
    if isinstance(preview, dict):
        return ", ".join("%s=%s" % (key, _text(value)) for key, value
                         in sorted(preview.items(), key=lambda kv: str(kv[0])))
    return _text(preview)[:200]


def _degrade_section(data: Dict[str, Any]) -> str:
    if data["degraded"]:
        note = ("当前来源：deterministic_fallback（离线确定性降级）。"
                "未获得有效的视觉模型候选，以下结果仅由确定性规则基于帧数、"
                "帧号间隔、骨骼信息生成，未做视觉缺陷识别，不代表模型识别结果，"
                "全部需人工复核。")
        return ('<section><h2>离线降级说明</h2><p class="tak-meta">%s</p>'
                '</section>' % _esc(note))
    note = ("当前来源：%s。AI 仅产出候选，已经确定性过滤；"
            "所有候选仍需人工复核，不作为确定性判定。"
            % (data["source"] or "vision_model"))
    return ('<section><h2>来源说明</h2><p class="tak-meta">%s</p>'
            '</section>' % _esc(note))


def _candidates_section(candidates: List[Any]) -> str:
    rows: List[str] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        rows.append(
            '<tr class="tak-row tak-sev-info"><td><code>%s</code></td>'
            '<td><code>%s</code></td><td><code>%s</code></td>'
            '<td class="tak-badge">%s</td><td>%s</td><td>%s</td></tr>' % (
                _esc(item.get("frame")), _esc(item.get("category")),
                _esc(item.get("bone") or "—"),
                _esc(_conf(item.get("confidence"))),
                _esc(item.get("evidence")),
                _esc(item.get("source"))))
    body = "".join(rows) or (
        '<tr class="tak-row tak-sev-info"><td colspan="6">无有效候选</td></tr>')
    return ('<section><h2>缺陷候选（需人工复核）</h2>'
            '<table class="tak-table"><thead><tr><th>帧号</th><th>类别</th>'
            '<th>骨骼</th><th>置信度</th><th>证据</th><th>来源</th></tr>'
            '</thead><tbody>%s</tbody></table></section>' % body)


def _rejected_section(rejected: List[Any]) -> str:
    rows: List[str] = []
    for item in rejected:
        if not isinstance(item, dict):
            continue
        rows.append(
            '<tr class="tak-row tak-sev-warning"><td><code>%s</code></td>'
            '<td class="tak-badge">%s</td><td>%s</td></tr>' % (
                _esc(item.get("index")), _esc(item.get("reason")),
                _esc(_summarize(item.get("candidate")))))
    if not rows:
        return '<section><h2>被拒绝项</h2><p class="tak-meta">无</p></section>'
    return ('<section><h2>被拒绝项</h2><table class="tak-table"><thead><tr>'
            '<th>序号</th><th>原因</th><th>候选摘要</th></tr></thead>'
            '<tbody>%s</tbody></table></section>' % "".join(rows))


def _warnings_section(warnings: List[Any]) -> str:
    rows: List[str] = []
    for item in warnings:
        if not isinstance(item, dict):
            rows.append('<tr class="tak-row tak-sev-info">'
                        '<td class="tak-badge">info</td><td>%s</td></tr>'
                        % _esc(_text(item)[:300]))
            continue
        rows.append(
            '<tr class="tak-row tak-sev-info"><td class="tak-badge">%s</td>'
            '<td>%s</td></tr>' % (
                _esc(item.get("type")), _esc(item.get("message"))))
    if not rows:
        return '<section><h2>警告</h2><p class="tak-meta">无</p></section>'
    return ('<section><h2>警告</h2><table class="tak-table"><thead><tr>'
            '<th>类型</th><th>说明</th></tr></thead><tbody>%s</tbody>'
            '</table></section>' % "".join(rows))


def render_panel_html(result: Any) -> str:
    """渲染完整面板片段：候选、被拒绝项、警告、离线降级说明。"""
    data = _collect(result)
    parts = [
        '<section class="tak-panel" data-tool="%s">' % _esc(TOOL_ID),
        '<header class="tak-header"><h1>%s</h1>'
        '<span class="tak-state tak-state-pass">候选 · 待复核</span></header>' % (
            _esc(data["title"]),),
        '<p class="tak-meta">DCC %s · 来源 %s · 模型 %s · 检查帧 %d · 候选 %d</p>' % (
            _esc(data["dcc"]), _esc(data["source"] or "—"),
            _esc(data["model"] or "—"), data["frames_checked"],
            len(data["candidates"])),
        _degrade_section(data),
        _candidates_section(data["candidates"]),
        _rejected_section(data["rejected"]),
        _warnings_section(data["warnings"]),
        '<p class="tak-hint">输出均为候选，非确定性判定；帧号/骨骼/置信度需人工复核'
        '（docs/VALIDATION.md B9）。离线可跑、不写场景。</p>',
        '</section>',
    ]
    return "".join(parts)


def render_report_html(result: Any) -> str:
    """与面板一致的完整片段入口（保留双入口约定）。"""
    return render_panel_html(result)
