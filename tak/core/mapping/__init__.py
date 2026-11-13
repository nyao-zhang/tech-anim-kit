# -*- coding: utf-8 -*-
"""P5.2 Skeleton Mapper 骨骼语义映射（tak.core.mapping 包入口）。

本包把 ``semantics`` 的确定性纯函数编排成一条完整流水线：

1. 可选地把 ``ik_retarget_guard`` 的映射资产 v1.0 作为基线（已确认映射）。
2. 叠加确定性词典候选（``deterministic_pairs``）。
3. 可选地用 AI provider 生成候选，经容错解析与确定性校验后仅补空位。
4. 经 ``validate_mapping`` 输出干净的 entries 与人工复核队列。

硬约束
======
- ``tak.core.mapping`` 顶层零 DCC import、零第三方依赖；registry / runner /
  AI provider 均在函数内 lazy import，纯确定性路径完全不碰 AI 与插件。
- 冲突绝不自动选择；AI 候选只能作为建议，必须经确定性校验后才能进入结果，
  且标注 ``origin`` / ``method`` / ``provider``；冲突与低置信度项保留供人工确认。
- 输出可复现：所有集合排序、稳定 tie-break，不写时间戳 / 随机数。
- 生成的 mapping asset 与 ``ik_retarget_guard`` v1.0 格式兼容，可被
  ``load_mapping_asset`` 读回。

公开 API
========
语义纯函数（来自 ``semantics``）：``normalize_bone`` / ``SEMANTIC_GROUPS`` /
``semantic_of`` / ``confidence_of`` / ``deterministic_pairs`` /
``validate_mapping`` / ``merge_candidates`` / ``parse_ai_candidates``。
编排：``map_skeleton`` / ``write_mapping`` / ``load_mapping``。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from tak.core.mapping.semantics import (
    LAST_PARSE_ERROR,
    SEMANTIC_GROUPS,
    confidence_of,
    deterministic_pairs,
    merge_candidates,
    normalize_bone,
    parse_ai_candidates,
    parse_ai_candidates_with_error,
    semantic_of,
    validate_mapping,
)

__all__ = [
    "normalize_bone", "SEMANTIC_GROUPS", "semantic_of", "confidence_of",
    "deterministic_pairs", "validate_mapping", "merge_candidates",
    "parse_ai_candidates", "parse_ai_candidates_with_error", "LAST_PARSE_ERROR",
    "map_skeleton", "write_mapping", "load_mapping",
]

TOOL_ID = "skeleton_mapper"
ASSET_VERSION = "1.0"
IK_TOOL_ID = "ik_retarget_guard"
_IK_MODULE_NAME = "tak_plugin_%s_mapping" % IK_TOOL_ID
_CONSUMERS = ["P5.2 Skeleton Mapper", "UE IK Retargeter"]


# --------------------------------------------------------------------------- #
# ik_retarget_guard.mapping 模块加载（按文件路径，插件目录不在 sys.path）
# --------------------------------------------------------------------------- #
def _ik_mapping_module() -> Any:
    """通过注册表加载 ik_retarget_guard 的 mapping.py，并复用其 sys.modules 缓存。

    与 ``tools/ik_retarget_guard/core.py:_mapping_module`` 使用同一模块名，避免
    重复 exec；顶层不做 DCC / 第三方导入，只有在真正读写资产时才触发。
    """
    module = sys.modules.get(_IK_MODULE_NAME)
    if module is not None:
        return module
    from tak.core import registry, runner
    return registry.load_plugin_module(runner.find_plugin(IK_TOOL_ID), "mapping")


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
def _names(value: Any) -> List[str]:
    if isinstance(value, dict):
        value = list(value.keys())
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    return sorted({item.strip() for item in value
                   if isinstance(item, str) and item.strip()})


def _safe_confidence(value: Any, default: float = 0.9) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return default
    if not 0.0 <= number <= 1.0:
        return default
    return round(number, 4)


def _sort_key(entry: Dict[str, Any]) -> Tuple[str, str, str]:
    return (str(entry.get("source") or ""), str(entry.get("target") or ""),
            str(entry.get("semantic") or ""))


def _baseline_entries(asset: Any, sources: List[str],
                      targets: List[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """把 mapping asset / suggest_mapping 结果的 entries 转成基线候选。"""
    warnings: List[str] = []
    if asset is None:
        return [], warnings
    payload = asset
    if not isinstance(payload, dict):
        try:
            payload = _ik_mapping_module().load_mapping_asset(os.fspath(asset))
        except (ValueError, TypeError, OSError) as exc:
            warnings.append("映射资产无法读取，已忽略基线: %s" % exc)
            return [], warnings
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, (list, tuple)):
        warnings.append("映射资产缺少 entries，已忽略基线")
        return [], warnings
    source_set, target_set = set(sources), set(targets)
    result: List[Dict[str, Any]] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        source, target = raw.get("source"), raw.get("target")
        if not isinstance(source, str) or not source \
                or not isinstance(target, str) or not target:
            continue
        if source not in source_set or target not in target_set:
            warnings.append("基线映射 %r -> %r 不在当前骨架中，已跳过"
                            % (source, target))
            continue
        result.append({
            "source": source, "target": target,
            "semantic": str(raw.get("semantic") or semantic_of(source)
                            or semantic_of(target) or ""),
            "confidence": _safe_confidence(raw.get("confidence"), 0.9),
            "method": str(raw.get("method") or "asset"),
            "origin": "asset", "provider": raw.get("provider"),
            "status": "auto",
        })
    result.sort(key=_sort_key)
    return result, warnings


def _ai_messages(sources: List[str], targets: List[str]) -> List[Dict[str, str]]:
    """构造稳定、可复现的提示词（排序 + 固定文案，无随机数 / 时间戳）。"""
    system = ("你是骨骼语义映射助手。只输出 JSON，形如 "
              "{\"entries\":[{\"source\":\"...\",\"target\":\"...\","
              "\"confidence\":0.8}]}；不要把解释文字写进 JSON。")
    user = ("请为下列骨骼给出候选映射，只补齐确定性词典未覆盖的项。\n"
            "source_bones=%s\ntarget_bones=%s"
            % (json.dumps(sources, ensure_ascii=False, sort_keys=True),
               json.dumps(targets, ensure_ascii=False, sort_keys=True)))
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def _ai_candidates(options: Dict[str, Any], provider: Any, sources: List[str],
                   targets: List[str]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """生成 AI 候选；离线 / 非法输出优雅降级为纯确定性结果。"""
    info: Dict[str, Any] = {
        "enabled": True, "provider": None, "candidates": 0, "degraded": False,
        "warning": "",
    }
    active = provider
    if active is None:
        try:
            from tak.core.ai.providers import get_provider
            active = get_provider(task="semantic_mapping")
        except Exception as exc:  # 缺 AI 配置时不影响确定性映射
            info["degraded"] = True
            info["warning"] = "AI provider 不可用，已降级为纯确定性映射: %s" % exc
            return [], info
    if active is None or not hasattr(active, "chat"):
        info["degraded"] = True
        info["warning"] = "AI provider 缺少 chat 接口，已降级为纯确定性映射"
        return [], info
    info["provider"] = str(getattr(active, "model", None)
                           or type(active).__name__)
    try:
        text = active.chat(_ai_messages(sources, targets))
    except Exception as exc:
        info["degraded"] = True
        info["warning"] = "AI provider 调用失败，已降级为纯确定性映射: %s" % exc
        return [], info
    parsed = parse_ai_candidates_with_error(text)
    entries = parsed["entries"]
    if not entries or parsed["error"]:
        info["degraded"] = True
        info["warning"] = ("AI provider 未返回可用候选（%s），已优雅降级为纯确定性映射"
                           % (parsed["error"] or "空结果"))
        return [], info
    info["candidates"] = len(entries)
    return entries, info


def _unresolved(valid_entries: List[Dict[str, Any]], sources: List[str],
                targets: List[str]) -> List[Dict[str, Any]]:
    """未映射骨骼 + 低置信度需人工确认项，排序稳定。"""
    mapped_sources = {item["source"] for item in valid_entries}
    mapped_targets = {item["target"] for item in valid_entries}
    rows: List[Dict[str, Any]] = []
    for name in sources:
        if name not in mapped_sources:
            rows.append({"bone": name, "side": "source",
                         "semantic": semantic_of(name), "reason": "unmapped"})
    for name in targets:
        if name not in mapped_targets:
            rows.append({"bone": name, "side": "target",
                         "semantic": semantic_of(name), "reason": "unmapped"})
    for item in valid_entries:
        if item.get("status") == "needs_review":
            rows.append({"bone": item["source"], "side": "pair",
                         "semantic": item.get("semantic", ""),
                         "reason": "low_confidence",
                         "target": item["target"]})
    rows.sort(key=lambda item: (item["side"], item["bone"],
                                item.get("target") or "", item["reason"]))
    return rows


def _dedupe_conflicts(groups: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen = set()
    for group in groups:
        for item in group:
            if not isinstance(item, dict):
                continue
            key = (str(item.get("type") or ""),
                   str(item.get("canonical") or ""),
                   str(item.get("source") or ""),
                   str(item.get("target") or ""),
                   str(item.get("message") or ""))
            if key in seen:
                continue
            seen.add(key)
            merged.append(dict(item))
    merged.sort(key=lambda item: (item.get("type", ""),
                                  str(item.get("canonical") or ""),
                                  str(item.get("source") or ""),
                                  str(item.get("target") or "")))
    return merged


# --------------------------------------------------------------------------- #
# 公开编排 API
# --------------------------------------------------------------------------- #
def map_skeleton(source_bones: Any, target_bones: Any,
                 mapping_asset: Any = None, options: Any = None,
                 provider: Any = None) -> Dict[str, Any]:
    """把一份源骨架映射到目标骨架，返回可复现的结构化映射结果。

    参数
    ----
    - ``source_bones`` / ``target_bones``：骨骼名序列或 ``snapshot["joints"]``
      形式的字典（取键）。
    - ``mapping_asset``：可选的基线；可以是 v1.0 资产字典、``suggest_mapping``
      结果字典，或资产文件路径（用 ``load_mapping_asset`` 读取）。
    - ``options``：``use_ai`` / ``source_dcc`` / ``target_dcc`` 等。
    - ``provider``：可选 AI provider；不传时用 ``get_provider(task=
      "semantic_mapping")``（默认离线）。

    返回
    ----
    ``{"version", "source_dcc", "target_dcc", "entries", "conflicts",
    "unresolved", "stats", "warnings", "metadata"}``；metadata 含 ``consumers``
    与 ``handoff`` 交接说明。本函数不写任何文件、不触碰宿主场景。
    """
    opts = dict(options) if isinstance(options, dict) else {}
    sources = _names(source_bones)
    targets = _names(target_bones)

    baseline, warnings = _baseline_entries(mapping_asset, sources, targets)
    dictionary = deterministic_pairs(sources, targets)

    ai_entries: List[Dict[str, Any]] = []
    ai_info: Dict[str, Any] = {"enabled": False, "provider": None,
                               "candidates": 0, "degraded": False, "warning": ""}
    if bool(opts.get("use_ai")):
        ai_entries, ai_info = _ai_candidates(opts, provider, sources, targets)

    merged = merge_candidates(dictionary, ai_entries, sources, targets)
    warnings.extend(merged["warnings"])
    if ai_info.get("warning"):
        warnings.append(ai_info["warning"])

    # 基线优先：候选只补基线未占用的 source / target，绝不覆盖已确认映射。
    baseline_sources = {item["source"] for item in baseline}
    baseline_targets = {item["target"] for item in baseline}
    combined: List[Dict[str, Any]] = list(baseline)
    for item in merged["entries"]:
        if item.get("source") in baseline_sources \
                or item.get("target") in baseline_targets:
            continue
        combined.append(item)

    validated = validate_mapping(combined, sources, targets)
    warnings.extend(validated["warnings"])
    warnings.append("冲突与低置信度项不会自动写入，请在 DCC 内人工确认后再应用"
                    "到 IK Retargeter / Characterization / Biped。")
    warnings = sorted(set(warnings))

    entries = validated["valid_entries"]
    conflicts = _dedupe_conflicts([validated["conflicts"], merged["conflicts"]])
    unresolved = _unresolved(entries, sources, targets)

    origin_counts = {"dictionary": 0, "ai": 0, "asset": 0}
    for item in entries:
        key = str(item.get("origin") or "dictionary")
        origin_counts[key] = origin_counts.get(key, 0) + 1
    stats = dict(validated["stats"])
    stats.update({
        "total_source": len(sources),
        "total_target": len(targets),
        "entries": len(entries),
        "conflicts": len(conflicts),
        "unresolved": len(unresolved),
        "warnings": len(warnings),
        "origins": origin_counts,
        "ai_candidates": ai_info.get("candidates", 0),
    })

    source_dcc = str(opts.get("source_dcc") or "")
    target_dcc = str(opts.get("target_dcc") or "")
    metadata = {
        "tool": TOOL_ID,
        "version": ASSET_VERSION,
        "consumers": list(_CONSUMERS),
        "source_dcc": source_dcc,
        "target_dcc": target_dcc,
        "offline": not bool(ai_entries),
        "ai": ai_info,
        "citations": [
            "tools/ik_retarget_guard/mapping.py:write_mapping_asset / "
            "load_mapping_asset（mapping asset v1.0）",
            "tak/core/mapping/semantics.py:SEMANTIC_GROUPS（确定性语义词典）",
            "tak/core/ai/providers.py:get_provider(task='semantic_mapping')",
        ],
        "handoff": {
            "consumer": "P5.2 Skeleton Mapper / UE IK Retargeter",
            "asset_version": ASSET_VERSION,
            "writer": "tools/ik_retarget_guard/mapping.py:write_mapping_asset",
            "reader": "tools/ik_retarget_guard/mapping.py:load_mapping_asset",
            "entries": "mapping.entries（source/target/confidence/method）",
            "review_queue": "mapping.conflicts + mapping.unresolved",
            "note": "冲突绝不自动选择；AI 候选只作建议，须人工确认后才写入下游。",
        },
        "stats": stats,
    }

    return {
        "version": ASSET_VERSION,
        "source_dcc": source_dcc,
        "target_dcc": target_dcc,
        "entries": entries,
        "conflicts": conflicts,
        "unresolved": unresolved,
        "stats": stats,
        "warnings": warnings,
        "metadata": metadata,
    }


def write_mapping(mapping: Any, path: Any) -> str:
    """把映射结果写为与 ik_retarget_guard v1.0 兼容的 JSON 资产，返回路径。

    优先复用 ``ik_retarget_guard.mapping.write_mapping_asset``，因此输出字段、
    排序键与序列化规则完全兼容，可被 ``load_mapping_asset`` 读回。P5.2 的语义
    附加信息（semantic / origin / provider / status）保留在 ``metadata`` 里。
    """
    if not isinstance(mapping, dict):
        raise ValueError("mapping 必须是 map_skeleton 返回的字典")
    filename = os.fspath(path)
    if not filename:
        raise ValueError("path 不能为空")

    entries = mapping.get("entries")
    entries = list(entries) if isinstance(entries, (list, tuple)) else []
    conflicts = mapping.get("conflicts")
    conflicts = [dict(item) for item in conflicts
                 if isinstance(item, dict)] if isinstance(conflicts, (list, tuple)) else []

    details = []
    for item in entries:
        if isinstance(item, dict):
            details.append({key: item.get(key) for key in
                            ("source", "target", "semantic", "origin",
                             "provider", "status")})

    unresolved = mapping.get("unresolved")
    unresolved = list(unresolved) if isinstance(unresolved, (list, tuple)) else []
    unmapped_source = [item.get("bone") for item in unresolved
                       if isinstance(item, dict) and item.get("side") == "source"]
    unmapped_target = [item.get("bone") for item in unresolved
                       if isinstance(item, dict) and item.get("side") == "target"]

    metadata = dict(mapping.get("metadata") or {})
    metadata["entry_details"] = details
    metadata["stats"] = dict(mapping.get("stats") or {})
    metadata["warnings"] = list(mapping.get("warnings") or [])
    metadata["unresolved"] = unresolved
    metadata.setdefault("consumers", list(_CONSUMERS))

    compatible = {
        "entries": entries,
        "conflicts": conflicts,
        "unmapped_source": unmapped_source,
        "unmapped_target": unmapped_target,
        "metadata": metadata,
    }
    return _ik_mapping_module().write_mapping_asset(compatible, filename)


def load_mapping(path: Any) -> Dict[str, Any]:
    """读取并校验 v1.0 映射资产，返回与文件内容一致的字典。"""
    return _ik_mapping_module().load_mapping_asset(os.fspath(path))
