# -*- coding: utf-8 -*-
"""生成 golden fixtures：20 个好场景 + 20 个坏场景的骨架快照。

这些是纯 JSON，不依赖任何 DCC；四个 DCC 的 adapter 产出同一结构，
因此回归测试可在无 Maya / UE 的机器上完整运行。

每个 bad fixture 都显式声明注入了哪些违规，测试断言
validate_snapshot 的输出与该声明完全一致（漏报 0 / 误报 0）。
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from typing import Callable, Dict, List, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_GOOD = os.path.join(_HERE, "good")
_BAD = os.path.join(_HERE, "bad")

_BASE_JOINTS: List[Tuple[str, str, int]] = [
    ("root", None, 0),
    ("pelvis", "root", 1),
    ("spine_01", "pelvis", 2),
    ("neck", "spine_01", 3),
    ("head", "neck", 4),
    ("clavicle_L", "spine_01", 3),
    ("upperarm_L", "clavicle_L", 4),
    ("lowerarm_L", "upperarm_L", 5),
    ("hand_L", "lowerarm_L", 6),
    ("clavicle_R", "spine_01", 3),
    ("upperarm_R", "clavicle_R", 4),
    ("lowerarm_R", "upperarm_R", 5),
    ("hand_R", "lowerarm_R", 6),
    ("thigh_L", "pelvis", 2),
    ("calf_L", "thigh_L", 3),
    ("foot_L", "calf_L", 4),
    ("thigh_R", "pelvis", 2),
    ("calf_R", "thigh_R", 3),
    ("foot_R", "calf_R", 4),
]

_IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def base_snapshot(seed: int = 0) -> Dict:
    joints = {}
    for name, parent, depth in _BASE_JOINTS:
        joints[name] = {
            "parent": parent, "matrix": list(_IDENTITY), "depth": depth,
            "scale": [1.0, 1.0, 1.0], "rotate": [0.0, 0.0, 0.0], "namespace": "",
        }
    return {
        "dcc": ["maya", "max", "mobu", "unreal"][seed % 4],
        "version": {"maya": "2023", "max": "2023", "mobu": "2023", "unreal": "5.8"}[
            ["maya", "max", "mobu", "unreal"][seed % 4]],
        "unit": "cm", "up_axis": "Y",
        "joints": joints,
        "frame_range": [1.0, 120.0],
        "skin": {"max_influences_found": 4, "weight_sum_deviation": 0.0,
                 "deformers": ["skinCluster"]},
        "metadata": {"seed": seed, "source": "tak golden fixtures"},
    }


def _joint_template() -> Dict:
    return {"parent": "root", "matrix": list(_IDENTITY), "depth": 2,
            "scale": [1.0, 1.0, 1.0], "rotate": [0.0, 0.0, 0.0], "namespace": ""}


def _inject(snapshot: Dict, kind: str) -> None:
    joints = snapshot["joints"]
    skin = snapshot["skin"]
    if kind == "naming.joint_pattern":
        joints["LeftUpArm"] = _joint_template()
    elif kind == "naming.forbidden_token":
        joints["foot_L copy"] = _joint_template()
    elif kind == "naming.namespace":
        joints["head"]["namespace"] = "CHAR_A:"
    elif kind == "skeleton.missing_required":
        joints.pop("foot_R", None)
    elif kind == "skeleton.max_depth":
        joints["head"]["depth"] = 20
    elif kind == "skeleton.negative_scale":
        joints["thigh_L"]["scale"] = [-1.0, 1.0, 1.0]
    elif kind == "skeleton.unfrozen_transform":
        joints["neck"]["rotate"] = [0.0, 45.0, 0.0]
    elif kind == "skin.max_influences":
        skin["max_influences_found"] = 6
    elif kind == "skin.weight_sum":
        skin["weight_sum_deviation"] = 0.05
    elif kind == "skin.unknown_deformer":
        skin["deformers"] = ["skinCluster", "lattice"]
    elif kind == "export.unit_mismatch":
        snapshot["unit"] = "m"
    elif kind == "export.up_axis_mismatch":
        snapshot["up_axis"] = "Z"
    elif kind == "export.frame_range_mismatch":
        snapshot["frame_range"] = [1.0, 60.0]
    else:
        raise ValueError("未知注入类型 %s" % kind)


# 注入一个类型可能带来多个确定性的 violation（例如含空格的名字同时违反命名正则）
_SIDE_EFFECTS = {
    "naming.forbidden_token": ["naming.joint_pattern"],
}


def expected_for(kinds: List[str]) -> List[str]:
    result = set()
    for kind in kinds:
        result.add(kind)
        result.update(_SIDE_EFFECTS.get(kind, []))
    return sorted(result)


_SINGLE_KINDS = [
    "naming.joint_pattern", "naming.forbidden_token", "naming.namespace",
    "skeleton.missing_required", "skeleton.max_depth", "skeleton.negative_scale",
    "skeleton.unfrozen_transform", "skin.max_influences", "skin.weight_sum",
    "skin.unknown_deformer", "export.unit_mismatch", "export.up_axis_mismatch",
    "export.frame_range_mismatch",
]

_COMBINATIONS = [
    ["naming.namespace", "skin.max_influences"],
    ["skeleton.missing_required", "export.unit_mismatch"],
    ["skeleton.negative_scale", "skin.weight_sum", "export.up_axis_mismatch"],
    ["skeleton.max_depth", "skin.unknown_deformer"],
    ["naming.joint_pattern", "export.frame_range_mismatch", "skin.max_influences"],
    ["skeleton.unfrozen_transform", "skin.weight_sum"],
    ["export.unit_mismatch", "export.up_axis_mismatch", "export.frame_range_mismatch"],
]


def build() -> Dict[str, List[Dict]]:
    """返回 {'good': [...], 'bad': [...]}，纯函数，便于测试与复现。"""
    fixtures: Dict[str, List[Dict]] = {"good": [], "bad": []}
    for i in range(20):
        fixtures["good"].append({
            "id": "good_%03d" % (i + 1), "kind": "good", "expected_pass": True,
            "expected_violations": [], "injected": [],
            "spec": "schema/rig_spec.default.json", "snapshot": base_snapshot(i),
        })
    for i in range(20):
        kinds = _SINGLE_KINDS[i] if i < len(_SINGLE_KINDS) else None
        if kinds is None:
            kinds = _COMBINATIONS[i - len(_SINGLE_KINDS)]
        if isinstance(kinds, str):
            kinds = [kinds]
        snapshot = copy.deepcopy(base_snapshot(i + 100))
        for kind in kinds:
            _inject(snapshot, kind)
        fixtures["bad"].append({
            "id": "bad_%03d" % (i + 1), "kind": "bad", "expected_pass": False,
            "expected_violations": expected_for(kinds), "injected": list(kinds),
            "spec": "schema/rig_spec.default.json", "snapshot": snapshot,
        })
    return fixtures


def write(fixtures: Dict[str, List[Dict]]) -> int:
    for folder, items in (("good", fixtures["good"]), ("bad", fixtures["bad"])):
        target = os.path.join(_HERE, folder)
        os.makedirs(target, exist_ok=True)
        for item in items:
            with open(os.path.join(target, item["id"] + ".json"), "w", encoding="utf-8") as fh:
                json.dump(item, fh, ensure_ascii=False, indent=2)
    manifest = {
        "version": "1.0.0",
        "spec": "schema/rig_spec.default.json",
        "good_count": len(fixtures["good"]),
        "bad_count": len(fixtures["bad"]),
        "violation_types": sorted({v for f in fixtures["bad"]
                                   for v in f["expected_violations"]}),
    }
    with open(os.path.join(_HERE, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    return len(fixtures["good"]) + len(fixtures["bad"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="校验磁盘 fixtures 是否与生成器一致")
    args = parser.parse_args(argv)
    fixtures = build()
    if args.check:
        stale = []
        for folder in ("good", "bad"):
            for item in fixtures[folder]:
                path = os.path.join(_HERE, folder, item["id"] + ".json")
                if not os.path.isfile(path):
                    stale.append(item["id"])
                    continue
                with open(path, "r", encoding="utf-8") as fh:
                    if json.load(fh) != item:
                        stale.append(item["id"])
        print("STALE: %s" % ", ".join(stale) if stale else
              "OK: %d 个 fixtures 与生成器一致" % sum(len(v) for v in fixtures.values()))
        return 1 if stale else 0
    print("generated %d golden fixtures" % write(fixtures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
