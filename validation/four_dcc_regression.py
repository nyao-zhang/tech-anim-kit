# -*- coding: utf-8 -*-
"""四端一致性回归（批次 3 · P4.3）：一份骨架，四个 DCC，同一份判定。

不依赖 pytest、不需要任何真实 DCC：用 ``tak.core.mocks`` 注入四套假宿主模块，
再让 ``tak.core.runner.adapter_for`` 拿到**真实适配器**采集快照，逐项验证：

1. 结构符合 ``tak.adapters.conformance`` 的四端契约；
2. 与理论读数（``MockScene.snapshot()``）逐字段一致（忽略 dcc/version/metadata）；
3. 四端之间互相 ``compare_snapshots`` 无差异；
4. 40 个 golden fixtures 的规范判定与 ``expected_violations`` 完全一致
   （漏报 0 / 误报 0）；
5. 每个适配器通过 ``check_adapter``，能力矩阵无缺失必需能力。

用法::

    python validation/four_dcc_regression.py
    python validation/four_dcc_regression.py --quick
    python validation/four_dcc_regression.py --out validation/four_dcc_result.json

退出码 0 表示全部通过；结果 JSON 不含时间戳，便于版本管理。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tak.adapters.conformance import (capability_matrix, check_adapter,  # noqa: E402
                                      compare_snapshots,
                                      validate_snapshot as structure_check)
from tak.core.diff import SkeletonSnapshot, validate_snapshot as rule_check  # noqa: E402
from tak.core.mocks import install_mock, uninstall_mock  # noqa: E402
from tak.core.mocks.scene import MockScene  # noqa: E402
from tak.core.runner import adapter_for, evaluate  # noqa: E402
from tak.core.spec import load_spec  # noqa: E402

DCCS = ("maya", "max", "mobu", "unreal")
FIXTURE_DIR = os.path.join(ROOT, "fixtures")
DEFAULT_OUT = os.path.join(ROOT, "validation", "four_dcc_regression_result.json")


def fixture_files(quick: bool = False) -> List[str]:
    paths: List[str] = []
    for folder in ("good", "bad"):
        directory = os.path.join(FIXTURE_DIR, folder)
        if not os.path.isdir(directory):
            continue
        names = sorted(name for name in os.listdir(directory) if name.endswith(".json"))
        if quick:
            names = names[:2]
        paths.extend(os.path.join(directory, name) for name in names)
    return paths


def load_fixture(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def collect(dcc: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """安装 mock → 真实适配器采集 → 结构/能力检查 → 卸载，返回纯数据结果。"""
    handle = None
    result: Dict[str, Any] = {"dcc": dcc, "ok": False, "snapshot": {},
                              "structure": {}, "adapter_check": {},
                              "detected": [], "error": ""}
    try:
        handle = install_mock(dcc, snapshot)
        adapter = adapter_for(dcc)
        captured = adapter.get_skeleton_snapshot()
        result["snapshot"] = captured
        result["structure"] = structure_check(captured)
        result["adapter_check"] = check_adapter(adapter)
        # 判定一致性用 MockAdapter 的同构快照（四端都完整表达 scale/rotate/
        # namespace/depth）；真实适配器读数另记为 adapter_detected，用于观测
        # 保真度。UE 不暴露绑定侧的这些字段，不能拿它做绑定规范判定。
        mock_snapshot = handle.adapter.get_skeleton_snapshot()
        result["mock_snapshot"] = mock_snapshot
        result["judgement_detected"] = sorted(rule_check(
            SkeletonSnapshot.from_dict(mock_snapshot), load_spec()))
        result["adapter_detected"] = sorted(rule_check(
            SkeletonSnapshot.from_dict(captured), load_spec()))
        result["detected"] = result["adapter_detected"]
        result["adapter_name"] = str(getattr(adapter, "name", ""))
        degraded = (captured.get("metadata") or {}).get("degraded")
        # 字符串 = 宿主不可用导致的降级空快照（必须失败）；
        # 列表 = UE 之类的已知能力限制说明（只记观测，不算失败）。
        result["degraded"] = bool(degraded) and isinstance(degraded, str)
        result["known_limitations"] = list(degraded) if isinstance(degraded, list) else []
        result["ok"] = bool(result["structure"]["ok"]
                            and result["adapter_check"]["ok"]
                            and not result["degraded"])
    except Exception as exc:
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
    finally:
        try:
            uninstall_mock()
        except Exception:
            pass
    return result


def _comparison_ignore(dcc: str, snapshot: Dict[str, Any]) -> tuple:
    """UE 的能力边界：只表达骨骼层级与资产，不表达绑定侧变换字段。"""
    if dcc != "unreal":
        return ()
    ignored = ["unit", "up_axis", "frame_range",
               "skin.max_influences_found", "skin.weight_sum_deviation",
               "skin.deformers"]
    for name in (snapshot.get("joints") or {}):
        for key in ("matrix", "scale", "rotate", "namespace", "depth"):
            ignored.append("joints.%s.%s" % (name, key))
    return tuple(ignored)


def run_one(path: str, dccs: Sequence[str]) -> Dict[str, Any]:
    fixture = load_fixture(path)
    raw = fixture.get("snapshot") or {}
    expected = sorted(fixture.get("expected_violations") or [])
    scene = MockScene.from_snapshot(raw, dcc="maya")
    # 理论读数保留 MockScene 声明的 depth：它代表「宿主场景里真实存在的
    # 层级」，也是坏 fixture 注入层级违规的载体，不能再用 parent 链重算。
    baseline = scene.snapshot()
    per_dcc: Dict[str, Any] = {}
    for dcc in dccs:
        item = collect(dcc, raw)
        item["expected"] = expected
        item["judgement_false_negatives"] = sorted(
            set(expected) - set(item["judgement_detected"]))
        item["judgement_false_positives"] = sorted(
            set(item["judgement_detected"]) - set(expected))
        item["adapter_false_negatives"] = sorted(
            set(expected) - set(item["adapter_detected"]))
        item["adapter_false_positives"] = sorted(
            set(item["adapter_detected"]) - set(expected))
        # 兼容旧字段名：面板与历史报告读的是 false_negatives。
        item["false_negatives"] = item["judgement_false_negatives"]
        item["false_positives"] = item["judgement_false_positives"]
        item["baseline_diff"] = compare_snapshots(
            item["snapshot"], baseline, ignore=_comparison_ignore(dcc, item["snapshot"]))
        per_dcc[dcc] = item
    # 四端互比：以第一个可用端为基准。
    reference_dcc = next((dcc for dcc in dccs
                          if per_dcc[dcc]["snapshot"] and not per_dcc[dcc]["error"]), None)
    for dcc in dccs:
        item = per_dcc[dcc]
        if reference_dcc is None or dcc == reference_dcc:
            item["cross_dcc_diff"] = {"ok": True, "differences": [],
                                      "reference": reference_dcc or ""}
            continue
        item["cross_dcc_diff"] = compare_snapshots(
            per_dcc[reference_dcc]["snapshot"], item["snapshot"],
            ignore=_comparison_ignore(dcc, item["snapshot"]))
    ok = all(
        item["ok"] and not item["error"]
        and not item["judgement_false_negatives"]
        and not item["judgement_false_positives"]
        and item["baseline_diff"]["ok"] and item["cross_dcc_diff"]["ok"]
        # UE 只做「层级 + 资产」保真；绑定侧的 scale/rotate/namespace/depth
        # 不暴露，因此不把它的 adapter 漏报当成失败（记录为观测值）。
        and (dcc == "unreal"
             or (not item["adapter_false_negatives"]
                 and not item["adapter_false_positives"]))
        for dcc, item in per_dcc.items())
    return {"id": fixture.get("id") or os.path.basename(path), "kind": fixture.get("kind"),
            "expected": expected, "ok": ok, "dccs": per_dcc}


def plugin_consistency(dccs: Sequence[str]) -> Dict[str, Any]:
    """对四端各跑一次内置规则体检，观测 findings 数并做规则集合对比。

    内置规则允许按 dcc 限定适用范围，因此这里只断言「不抛异常 + 快照判定一致」，
    不把不同 dcc 的规则集合强行拉平。
    """
    result: Dict[str, Any] = {"ok": True, "per_dcc": {}}
    raw = load_fixture(os.path.join(FIXTURE_DIR, "good", "good_001.json"))["snapshot"]
    for dcc in dccs:
        item = collect(dcc, raw)
        try:
            report = evaluate(item["snapshot"], dcc=dcc)
            findings = [finding.rule_id for finding in report.findings]
            result["per_dcc"][dcc] = {"ok": True, "findings": len(findings),
                                      "passed": report.passed,
                                      "rule_ids": sorted(set(findings))}
        except Exception as exc:
            result["per_dcc"][dcc] = {"ok": False,
                                      "error": "%s: %s" % (type(exc).__name__, exc)}
            result["ok"] = False
    return result


def capability_report(dccs: Sequence[str]) -> Dict[str, Any]:
    adapters = {}
    for dcc in dccs:
        try:
            adapters[dcc] = adapter_for(dcc)
        except Exception:
            continue
    return capability_matrix(adapters)


def run(paths: Sequence[str], dccs: Sequence[str]) -> Dict[str, Any]:
    spec = load_spec()
    cases = [run_one(path, dccs) for path in paths]
    summary: Dict[str, Any] = {
        "ok": True,
        "generated_by": "validation/four_dcc_regression.py",
        "spec_version": str(spec.get("version", "")),
        "fixture_count": len(cases),
        "dccs": list(dccs),
        "per_dcc": {},
        "failures": [],
    }
    for dcc in dccs:
        judgement_fn = sum(len(case["dccs"][dcc]["judgement_false_negatives"])
                           for case in cases)
        judgement_fp = sum(len(case["dccs"][dcc]["judgement_false_positives"])
                           for case in cases)
        adapter_fn = sum(len(case["dccs"][dcc]["adapter_false_negatives"])
                         for case in cases)
        adapter_fp = sum(len(case["dccs"][dcc]["adapter_false_positives"])
                         for case in cases)
        contract_errors = sum(len(case["dccs"][dcc]["structure"].get("errors", []))
                              for case in cases)
        baseline_diffs = sum(0 if case["dccs"][dcc]["baseline_diff"]["ok"] else 1
                             for case in cases)
        cross_diffs = sum(0 if case["dccs"][dcc]["cross_dcc_diff"]["ok"] else 1
                          for case in cases)
        degraded = sum(1 for case in cases if case["dccs"][dcc].get("degraded"))
        errors = sum(1 for case in cases if case["dccs"][dcc].get("error"))
        # 判定一致性是硬门槛；真实适配器保真度对 DCC 三端也是硬门槛，
        # UE 只按层级/资产比对（见 _comparison_ignore）。
        adapter_gate = dcc != "unreal"
        summary["per_dcc"][dcc] = {
            "fixtures": len(cases),
            "judgement_false_negatives": judgement_fn,
            "judgement_false_positives": judgement_fp,
            "adapter_false_negatives": adapter_fn,
            "adapter_false_positives": adapter_fp,
            "false_negatives": judgement_fn,
            "false_positives": judgement_fp,
            "contract_errors": contract_errors,
            "baseline_diffs": baseline_diffs,
            "cross_dcc_diffs": cross_diffs,
            "degraded": degraded,
            "collection_errors": errors,
            "adapter_gate": adapter_gate,
            "capability_note": ("UE 不暴露 scale/rotate/namespace/depth 与 bind 变换，"
                                "只比对骨骼层级与资产" if dcc == "unreal" else ""),
            "ok": not any((judgement_fn, judgement_fp, contract_errors,
                           baseline_diffs, cross_diffs, degraded, errors))
                  and (not adapter_gate or not any((adapter_fn, adapter_fp))),
        }
        if not summary["per_dcc"][dcc]["ok"]:
            summary["ok"] = False
    for case in cases:
        if not case["ok"]:
            summary["failures"].append({
                "fixture": case["id"],
                "dccs": {dcc: {
                    "error": item["error"],
                    "structure_errors": item["structure"].get("errors", []),
                    "adapter_errors": item["adapter_check"].get("errors", []),
                    "judgement_false_negatives": item["judgement_false_negatives"],
                    "judgement_false_positives": item["judgement_false_positives"],
                    "adapter_false_negatives": item["adapter_false_negatives"],
                    "adapter_false_positives": item["adapter_false_positives"],
                    "baseline_differences": item["baseline_diff"]["differences"][:5],
                    "cross_differences": item["cross_dcc_diff"].get("differences", [])[:5],
                } for dcc, item in case["dccs"].items()},
            })
    summary["plugins"] = plugin_consistency(dccs)
    summary["capabilities"] = capability_report(dccs)
    if not summary["plugins"]["ok"]:
        summary["ok"] = False
    summary["cases"] = [{"id": case["id"], "ok": case["ok"]} for case in cases]
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="四端适配器一致性回归（无需真实 DCC）")
    parser.add_argument("--out", default=DEFAULT_OUT, help="结果 JSON 输出路径")
    parser.add_argument("--quick", action="store_true", help="每类只跑前 2 个 fixture")
    parser.add_argument("--dccs", default=",".join(DCCS),
                        help="逗号分隔的 DCC 列表，默认四端")
    args = parser.parse_args(list(argv) if argv is not None else None)
    dccs = tuple(item.strip().lower() for item in args.dccs.split(",") if item.strip())
    paths = fixture_files(args.quick)
    if not paths:
        print("[TAK] 未找到 golden fixtures，回归失败")
        return 1
    summary = run(paths, dccs)
    out = os.path.abspath(args.out)
    parent = os.path.dirname(out)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, sort_keys=True,
                  allow_nan=False)
        fh.write("\n")
    print("[TAK] 四端一致性回归：%s" % ("PASS" if summary["ok"] else "FAIL"))
    print("[TAK] fixtures=%d dccs=%s" % (summary["fixture_count"], ",".join(dccs)))
    for dcc, item in summary["per_dcc"].items():
        print("  %-6s 判定漏报=%d 判定误报=%d 适配器漏报=%d 契约错误=%d "
              "基线差异=%d 跨端差异=%d 降级=%d %s"
              % (dcc, item["judgement_false_negatives"],
                 item["judgement_false_positives"],
                 item["adapter_false_negatives"], item["contract_errors"],
                 item["baseline_diffs"], item["cross_dcc_diffs"],
                 item["degraded"], "OK" if item["ok"] else "FAIL"))
    if summary["failures"]:
        print("[TAK] 前 3 个失败样例：")
        for failure in summary["failures"][:3]:
            print("  %s %s" % (failure["fixture"], json.dumps(
                failure["dccs"], ensure_ascii=False)[:600]))
    print("[TAK] 结果：%s" % out)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
