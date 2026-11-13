# -*- coding: utf-8 -*-
"""ML Deformer 数据工厂的 Web 面板 HTML 片段。

约束
----
* 只使用 ``tak/ui/theme.css`` 的 ``tak-*`` 类名，颜色 / 间距一律取自
  ``var(--tak-*)`` 设计 token；本文件不出现任何字面颜色；
* 不引用 CDN / 外链（无 ``<link>`` / ``<script>`` / ``href`` / ``src`` /
  ``http(s)://``）；
* 所有动态文本经 ``html.escape`` 转义，URL 方案在转义前先打散。

``render_panel_html`` 接受 ``core.manifest()`` / ``core.plan()`` 的 dict、
``ToolResult``、``Report`` 及各自的 ``to_dict()``；``render_report_html``
复用同一片段。
"""
from __future__ import annotations

import html
from typing import Any, Dict, List

TOOL_ID = "mldeformer_factory"
TITLE = "ML Deformer 数据工厂"

_SAFETY_NOTE = ("仅产出数据集清单与质量门槛报告；UE 侧 mldeformer.train 能力位为 "
                "false，本工具不调用任何训练入口。")


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _escape(value: Any) -> str:
    raw = _text(value).replace("https://", "https:").replace("http://", "http:")
    return html.escape(raw, quote=True)


def _to_dict(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        return dict(result)
    if hasattr(result, "to_dict"):
        try:
            value = result.to_dict()
            if isinstance(value, dict):
                return dict(value)
        except Exception:
            pass
    return {}


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _view(result: Any) -> Dict[str, Any]:
    data = _to_dict(result)
    metadata = data.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}

    def pick(key: str, default: Any = None) -> Any:
        for source in (data, metadata):
            value = source.get(key)
            if value is not None:
                return value
        return default

    manifest = pick("manifest")
    if not isinstance(manifest, dict):
        manifest = data if "dataset_id" in data else {}
    validation = pick("validation")
    validation = validation if isinstance(validation, dict) else {}
    summary = pick("summary")
    summary = summary if isinstance(summary, dict) else {}
    issues = validation.get("issues")
    issues = issues if isinstance(issues, list) else []
    if not issues:
        raw = pick("issues")
        issues = raw if isinstance(raw, list) else []
    counts = validation.get("counts")
    counts = counts if isinstance(counts, dict) else {}
    gates = validation.get("gates")
    gates = gates if isinstance(gates, dict) else {}
    categories = manifest.get("categories")
    categories = categories if isinstance(categories, dict) else {}
    coverage = manifest.get("coverage")
    coverage = coverage if isinstance(coverage, dict) else {}
    safety = pick("safety")
    safety = safety if isinstance(safety, dict) else {}
    ok = bool(validation.get("ok", False))
    return {
        "title": _text(pick("title") or TITLE),
        "dcc": _text(pick("dcc") or "maya"),
        "ok": ok,
        "manifest": manifest,
        "validation": validation,
        "summary": summary,
        "issues": issues,
        "counts": counts,
        "gates": gates,
        "categories": categories,
        "coverage": coverage,
        "safety": safety,
    }


def _meta_section(view: Dict[str, Any]) -> str:
    manifest = view["manifest"]
    summary = view["summary"]
    pose_count = _int(manifest.get("pose_count", summary.get("pose_count")))
    joint_count = _int(manifest.get("joint_count", summary.get("joint_count")))
    return (
        '<p class="tak-meta">数据集 <code>%s</code> · 契约 %s · pose %d · 关节 %d'
        ' · 类别 %d · 重复率 %s · 种子 %s · DCC %s</p>' % (
            _escape(manifest.get("dataset_id") or "（未生成）"),
            _escape(manifest.get("contract") or "-"), pose_count, joint_count,
            len(view["categories"]), _escape(manifest.get("duplicate_ratio", 0.0)),
            _escape(manifest.get("seed", 0)), _escape(view["dcc"])))


def _categories_section(view: Dict[str, Any]) -> str:
    categories = view["categories"]
    rows: List[str] = []
    for name in sorted(categories):
        rows.append(
            '<tr class="tak-row tak-sev-info">'
            '<td><code>%s</code></td>'
            '<td class="tak-badge">%d</td>'
            '</tr>' % (_escape(name), _int(categories[name])))
    body = "".join(rows) or (
        '<tr class="tak-row tak-sev-warning"><td colspan="2">'
        '无类别数据：快照为空或采样类别被裁剪</td></tr>')
    return (
        '<section><h2>类别分布</h2><table class="tak-table"><thead><tr>'
        '<th>类别</th><th>条数</th></tr></thead><tbody>%s</tbody></table></section>'
        % body)


def _coverage_section(view: Dict[str, Any]) -> str:
    coverage = view["coverage"]
    ranked = sorted(coverage.items(), key=lambda item: (-_int(item[1]), str(item[0])))
    rows: List[str] = []
    for joint, number in ranked[:12]:
        rows.append(
            '<tr class="tak-row tak-sev-info">'
            '<td><code>%s</code></td><td class="tak-badge">%d</td></tr>'
            % (_escape(joint), _int(number)))
    body = "".join(rows) or (
        '<tr class="tak-row tak-sev-warning"><td colspan="2">无关节覆盖数据</td></tr>')
    return (
        '<section><h2>关节覆盖（Top 12）</h2><table class="tak-table"><thead><tr>'
        '<th>关节</th><th>条数</th></tr></thead><tbody>%s</tbody></table></section>'
        % body)


def _gates_section(view: Dict[str, Any]) -> str:
    gates = view["gates"]
    keys = ("min_total_poses", "max_total_poses", "min_poses_per_joint",
            "max_duplicate_ratio", "require_categories", "require_contract")
    rows: List[str] = []
    for key in keys:
        value = gates.get(key)
        if isinstance(value, (list, tuple)):
            value = "、".join(str(item) for item in value)
        if value is None:
            continue
        rows.append(
            '<tr class="tak-row tak-sev-info"><td><code>%s</code></td>'
            '<td>%s</td></tr>' % (_escape(key), _escape(value)))
    body = "".join(rows) or (
        '<tr class="tak-row tak-sev-info"><td colspan="2">'
        '未携带门槛（仅展示清单）</td></tr>')
    return (
        '<section><h2>质量门槛</h2><table class="tak-table"><thead><tr>'
        '<th>门槛</th><th>阈值</th></tr></thead><tbody>%s</tbody></table></section>'
        % body)


def _severity_class(severity: Any) -> str:
    value = _text(severity).lower()
    if value in ("error", "blocker"):
        return "tak-sev-error"
    if value == "warning":
        return "tak-sev-warning"
    return "tak-sev-info"


def _issues_section(view: Dict[str, Any]) -> str:
    rows: List[str] = []
    for issue in view["issues"]:
        if not isinstance(issue, dict):
            continue
        rows.append(
            '<tr class="tak-row %s">'
            '<td class="tak-badge">%s</td>'
            '<td><code>%s</code></td>'
            '<td>%s</td>'
            '</tr>' % (_severity_class(issue.get("severity")),
                       _escape(issue.get("severity")),
                       _escape(issue.get("code")), _escape(issue.get("message"))))
    body = "".join(rows) or (
        '<tr class="tak-row tak-sev-info"><td colspan="3">'
        '门槛全部通过，无 issue</td></tr>')
    return (
        '<section><h2>门槛明细</h2><table class="tak-table"><thead><tr>'
        '<th>严重度</th><th>code</th><th>说明</th></tr></thead><tbody>%s</tbody>'
        '</table></section>' % body)


def _safety_section(view: Dict[str, Any]) -> str:
    return (
        '<section><h2>只读边界</h2>'
        '<p class="tak-hint">%s 写入场景：%s · 联网：%s · 训练入口：%s</p></section>'
        % (_escape(view["safety"].get("note") or _SAFETY_NOTE),
           _escape("否" if not view["safety"].get("scene_write") else "是"),
           _escape("否" if not view["safety"].get("network") else "是"),
           _escape("否" if not view["safety"].get("training_api") else "是")))


def render_panel_html(result: Any) -> str:
    """渲染数据集摘要、类别 / 覆盖 / 门槛与只读边界，返回无外链 HTML 片段。"""
    view = _view(result)
    counts = view["counts"]
    state_class = "tak-state-pass" if view["ok"] else "tak-state-fail"
    state_text = "门槛通过" if view["ok"] else "门槛未通过"
    return "".join([
        '<section class="tak-panel" data-tool="%s">' % _escape(TOOL_ID),
        '<header class="tak-header"><h1>%s</h1><span class="tak-state %s">%s</span>'
        '</header>' % (_escape(view["title"]), state_class, _escape(state_text)),
        _meta_section(view),
        '<p class="tak-meta">error %d · warning %d · 极值提示：数据集必须含 '
        'extreme 类别才能学可动范围</p>' % (
            _int(counts.get("error")), _int(counts.get("warning"))),
        _categories_section(view),
        _coverage_section(view),
        _gates_section(view),
        _issues_section(view),
        _safety_section(view),
        '</section>',
    ])


def render_report_html(result: Any) -> str:
    """与 Report 面板保持同一入口；完整面板同样只返回 HTML 片段。"""
    return render_panel_html(result)
