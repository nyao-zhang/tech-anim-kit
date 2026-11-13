# -*- coding: utf-8 -*-
"""P6.1 动作库语义检索离线回归测试。

覆盖
----
* 模型兜底解析（缺字段 / 类型错 / 非 dict / 时长反推）与 JSON 往返；
* 12 类动作词表、类型推断优先级与元数据覆盖；
* 特征公式（3 帧手算样本逐项校验，容差 1e-6）、空 / 单帧 / 缺 root /
  骨骼数不一致的安全返回；
* BM25 排序、类型加权、数值过滤排除、空 query、无命中、骨骼检索；
* ``similar_to`` 确定性与自排除、``to_index`` / ``from_index`` 往返；
* ``scan_directory`` 的同名 sidecar / motion_index sidecar / 文件名启发式 /
  目录不存在，且**不读取** FBX 二进制内容；
* sidecar 读写、插件 ``search`` 的 JSON 可序列化、``run_report`` 的 metadata、
  manifest 可发现、ui_web 转义 / 无外链 / 无字面颜色 / 纯数据面板；
* 回归保护：新文件按 Python 3.9 解析、无 DCC / 第三方 import、
  `python -c "import tak.core.motion"` 在纯标准库环境可用。

全部使用 tmp_path 与 NullAdapter，不接触真实 DCC、网络或仓库外文件。
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys

import pytest

from tak.adapters.base import NullAdapter
from tak.core import registry, runner
from tak.core.motion import (CONTRACT_VERSION, MOTION_TYPES, MotionClip,
                             MotionLibrary, clip_from_dict, extract_features)
from tak.core.motion import loader
from tak.core.motion.features import infer_motion_type, tokenize
from tak.core.report import Report

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL_ID = "motion_library"
PLUGIN_DIR = os.path.join(ROOT, "tools", TOOL_ID)
MOTION_DIR = os.path.join(ROOT, "tak", "core", "motion")
NEW_FILES = (
    os.path.join(MOTION_DIR, "__init__.py"),
    os.path.join(MOTION_DIR, "model.py"),
    os.path.join(MOTION_DIR, "features.py"),
    os.path.join(MOTION_DIR, "library.py"),
    os.path.join(MOTION_DIR, "loader.py"),
    os.path.join(PLUGIN_DIR, "core.py"),
    os.path.join(PLUGIN_DIR, "ui_web.py"),
    os.path.abspath(__file__),
)
ALLOWED_STDLIB = {"__future__", "collections", "dataclasses", "hashlib",
                  "html", "json", "math", "os", "re", "typing"}
FORBIDDEN_ROOTS = {"maya", "pymxs", "pyfbsdk", "unreal"}

SAMPLE_3F = {
    "fps": 2.0,
    "frame_count": 2,
    "samples": [
        {"frame": 0, "root": [0.0, 0.0, 0.0],
         "joints": {"hand_l": [0.0, 0.0, 10.0], "hand_r": [0.0, 0.0, 0.0]}},
        {"frame": 1, "root": [3.0, 0.0, 0.0],
         "joints": {"hand_l": [0.0, 0.0, 20.0], "hand_r": [0.0, 0.0, 0.0]}},
        {"frame": 2, "root": [3.0, 4.0, 0.0],
         "joints": {"hand_l": [0.0, 0.0, 30.0], "hand_r": [0.0, 0.0, 0.0]}},
    ],
}


# --------------------------------------------------------------------------- #
# fixtures / 工具
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def library():
    return MotionLibrary()


@pytest.fixture(scope="module")
def core():
    return registry.load_plugin_module(runner.find_plugin(TOOL_ID), "core")


@pytest.fixture(scope="module")
def ui():
    return registry.load_plugin_module(runner.find_plugin(TOOL_ID), "ui_web")


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _import_roots(source):
    tree = ast.parse(source)
    roots = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.append(node.module.split(".")[0])
    return roots


def _write_text(path, text):
    with open(str(path), "w", encoding="utf-8") as handle:
        handle.write(text)


# --------------------------------------------------------------------------- #
# 1. 模型：容错解析 / duration / JSON
# --------------------------------------------------------------------------- #
def test_clip_from_dict_defaults_for_empty_input():
    clip = clip_from_dict({})
    assert isinstance(clip, MotionClip)
    assert clip.clip_id == ""
    assert clip.name == ""
    assert clip.fps == 30.0
    assert clip.frame_count == 0
    assert clip.duration == 0.0
    assert clip.loopable is False
    assert clip.root_motion is False
    assert clip.bones == []
    assert clip.tags == []
    assert clip.features == {}
    assert clip.metadata == {}


def test_clip_from_dict_tolerates_bad_types():
    clip = clip_from_dict({
        "clip_id": 123,
        "name": None,
        "fps": "abc",
        "frame_count": "12",
        "loopable": "yes",
        "root_motion": 0,
        "bones": 5,
        "tags": "walk, loop",
        "features": {"a": "1.5", "b": "x", "c": float("nan")},
        "metadata": "nope",
    })
    assert clip.clip_id == "123"
    assert clip.name == ""
    assert clip.fps == 30.0
    assert clip.frame_count == 12
    assert clip.loopable is True
    assert clip.root_motion is False
    assert clip.bones == []
    assert clip.tags == ["walk", "loop"]
    assert clip.features == {"a": 1.5}
    assert clip.metadata == {}


def test_clip_from_dict_accepts_clip_instance_and_non_dict():
    clip = MotionClip(clip_id="x", name="X")
    assert clip_from_dict(clip) is clip
    for payload in (None, "text", 42, [1, 2, 3]):
        assert clip_from_dict(payload).clip_id == ""


def test_clip_duration_formula_and_invalid_fps():
    assert MotionClip(fps=24.0, frame_count=48).duration == 2.0
    assert MotionClip(fps=0.0, frame_count=48).duration == 0.0
    assert MotionClip(fps=-30.0, frame_count=48).duration == 0.0
    assert MotionClip(fps=30.0, frame_count=-5).duration == 0.0
    assert MotionClip(fps="bad", frame_count=30).duration == 1.0


def test_clip_from_dict_infers_frame_count_from_duration():
    clip = clip_from_dict({"duration": 2.0, "fps": 25.0})
    assert clip.frame_count == 50
    assert clip.duration == 2.0


def test_clip_to_dict_is_json_serializable_and_roundtrips():
    clip = clip_from_dict({
        "clip_id": "walk_01", "name": "Walk_01 走路", "fps": 30,
        "frame_count": 36, "loopable": True,
        "bones": ["root", "hand_l"], "tags": ["走路", "循环"],
        "features": {"root_speed_mean": 82.0},
        "metadata": {"note": "中文备注"},
    })
    payload = clip.to_dict()
    assert payload["duration"] == 1.2
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    restored = clip_from_dict(json.loads(text))
    assert restored.to_dict() == payload


# --------------------------------------------------------------------------- #
# 2. 特征：词表 / 类型推断 / 公式 / 安全性
# --------------------------------------------------------------------------- #
def test_motion_types_cover_required_categories():
    required = {"idle", "walk", "run", "jump", "attack", "hit", "turn",
                "crouch", "climb", "dance", "death", "other"}
    assert required <= set(MOTION_TYPES)
    assert MOTION_TYPES["other"] == "其它"
    for key, label in MOTION_TYPES.items():
        assert label and isinstance(label, str)


def test_tokenize_splits_underscore_and_builds_bigrams():
    assert tokenize("Walk_Fwd_01") == ["walk", "fwd", "01"]
    assert "走路" in tokenize("走路 循环")
    assert "hand" in tokenize("hand_l")


@pytest.mark.parametrize("name,expected", [
    ("Walk_Fwd_01 走路 前进", "walk"),
    ("Run_Fast 奔跑", "run"),
    ("Idle_Breath 待机 呼吸", "idle"),
    ("Jump_Start 起跳 腾空", "jump"),
    ("Attack_Slash 挥砍 连击", "attack"),
    ("Hit_Front 受击 硬直", "hit"),
    ("Turn_Left 转身 左转", "turn"),
    ("Crouch_Idle 蹲伏 待机", "crouch"),
    ("Climb_Ladder 攀爬 上梯", "climb"),
    ("Dance_Wave 舞蹈 挥手", "dance"),
    ("Death_Fall 倒地 阵亡", "death"),
    ("UnknownClip 无关键词", "other"),
])
def test_infer_motion_type_keywords(name, expected):
    assert infer_motion_type({"name": name}) == expected


def test_infer_motion_type_metadata_override():
    assert infer_motion_type({
        "name": "Walk_Fwd_01 走路",
        "metadata": {"motion_type": "run"},
    }) == "run"
    assert infer_motion_type({
        "name": "Walk_Fwd_01 走路",
        "metadata": {"motion_type": "奔跑"},
    }) == "run"
    assert infer_motion_type({
        "name": "Walk_Fwd_01 走路",
        "metadata": {"motion_type": "不存在"},
    }) == "walk"


def test_extract_features_three_frames_hand_computed():
    features = extract_features(SAMPLE_3F)
    # fps=2 → dt=0.5s；root 位移 3 与 4 → 速度 6 与 8
    assert features["root_speed_mean"] == pytest.approx(7.0, abs=1e-6)
    assert features["root_speed_max"] == pytest.approx(8.0, abs=1e-6)
    assert features["root_travel"] == pytest.approx(7.0, abs=1e-6)
    assert features["root_displacement"] == pytest.approx(5.0, abs=1e-6)
    # hand_l 每帧 +10 度 → 角速度 20 deg/s；hand_r 静止 → 均值 (20+0+20+0)/4
    assert features["joint_speed_mean"] == pytest.approx(10.0, abs=1e-6)
    assert features["joint_speed_max"] == pytest.approx(20.0, abs=1e-6)
    # 首末帧 hand_l 差 20 度
    assert features["loop_error"] == pytest.approx(20.0, abs=1e-6)
    # hand_l 0/10/20 的方差 66.6667，三轴平均 22.2222，两根骨骼平均 11.1111
    assert features["pose_variance"] == pytest.approx(11.111111, abs=1e-6)
    # 左右差每帧 10/20/30 度三轴均值 10/3, 20/3, 10 → 均值 20/3
    assert features["symmetry"] == pytest.approx(3.0 / 23.0, abs=1e-6)
    assert features["estimated"] == 0.0
    assert features["duration"] == pytest.approx(1.0, abs=1e-6)
    assert features["frame_count"] == 2.0


def test_extract_features_single_frame_is_zero_motion():
    features = extract_features({
        "fps": 30.0, "frame_count": 1,
        "samples": [{"frame": 0, "root": [1.0, 2.0, 3.0],
                     "joints": {"hand_l": [0.0, 0.0, 5.0]}}],
    })
    assert features["estimated"] == 0.0
    for key in ("root_speed_mean", "root_speed_max", "root_travel",
                "root_displacement", "joint_speed_mean", "joint_speed_max",
                "loop_error", "symmetry", "pose_variance"):
        assert features[key] == 0.0


def test_extract_features_empty_or_invalid_samples_is_estimated():
    for payload in ({}, None, {"samples": []}, {"samples": "bad"},
                    {"samples": [1, 2, 3]}):
        features = extract_features(payload)
        assert features["estimated"] == 1.0
        assert "root_speed_mean" in features
        assert "pose_variance" in features


def test_extract_features_missing_root_is_safe():
    features = extract_features({
        "fps": 1.0,
        "samples": [
            {"frame": 0, "joints": {"a": [0.0, 0.0, 0.0]}},
            {"frame": 1, "joints": {"a": [0.0, 0.0, 30.0]}},
        ],
    })
    assert features["root_speed_mean"] == 0.0
    assert features["root_travel"] == 0.0
    assert features["root_displacement"] == 0.0
    assert features["joint_speed_mean"] == pytest.approx(30.0, abs=1e-6)
    assert features["estimated"] == 0.0


def test_extract_features_inconsistent_joint_sets_is_safe():
    features = extract_features({
        "fps": 10.0,
        "samples": [
            {"frame": 0, "root": [0.0, 0.0, 0.0],
             "joints": {"a": [0.0, 0.0, 0.0], "b": [0.0, 0.0, 0.0]}},
            {"frame": 1, "root": [1.0, 0.0, 0.0],
             "joints": {"a": [0.0, 0.0, 10.0]}},
            {"frame": 2, "root": [2.0, 0.0, 0.0],
             "joints": {"c": [0.0, 0.0, 5.0]}},
        ],
    })
    assert features["loop_error"] == 0.0
    assert features["joint_speed_mean"] > 0.0
    assert features["estimated"] == 0.0


def test_extract_features_keeps_declared_values_and_marks_estimated():
    features = extract_features({
        "fps": 30.0, "frame_count": 30, "root_motion": True,
        "features": {"root_speed_mean": 55.5, "loop_error": 1.5},
    })
    assert features["root_speed_mean"] == 55.5
    assert features["loop_error"] == 1.5
    assert features["root_travel"] == 55.5
    assert features["estimated"] == 1.0


def test_extract_features_preserves_measured_marker():
    # 已实测（estimated=0.0）的索引条目再次提取时不得被改标成占位值。
    features = extract_features({
        "fps": 30.0, "frame_count": 30,
        "features": {"root_speed_mean": 55.5, "estimated": 0.0},
    })
    assert features["estimated"] == 0.0
    assert features["root_speed_mean"] == 55.5


# --------------------------------------------------------------------------- #
# 3. 动作库：建库 / 检索 / 过滤 / 相似 / 序列化
# --------------------------------------------------------------------------- #
def test_builtin_library_covers_eight_types(library):
    assert library.describe()["clip_count"] >= 8
    types = {clip.motion_type for clip in library.clips}
    assert {"idle", "walk", "run", "jump", "attack", "hit", "turn",
            "dance"} <= types
    assert all(clip.clip_id for clip in library.clips)
    assert all(clip.features.get("estimated") == 1.0 for clip in library.clips)


def test_bm25_search_ranks_walk_clips_first(library):
    hits = library.search("走路 循环", top_k=5)
    assert hits
    assert {hits[0]["clip_id"], hits[1]["clip_id"]} == {"walk_fwd_01",
                                                        "walk_back_01"}
    assert all(hit["text_score"] > 0 for hit in hits[:2])
    assert hits[0]["rank"] == 1
    assert "走路" in hits[0]["matched_terms"]
    assert "命中关键词" in hits[0]["why"]


def test_search_english_query_matches_clip_name(library):
    hits = library.search("walk", top_k=3)
    assert hits[0]["clip_id"].startswith("walk_")
    assert hits[0]["text_score"] > 0


def test_search_type_weighting_boosts_matching_type(library):
    hits = library.search("attack", top_k=3)
    assert hits[0]["clip_id"] == "attack_slash_01"
    assert hits[0]["motion_type"] == "attack"
    assert hits[0]["type_score"] == pytest.approx(3.0)
    assert "类型查询命中：攻击" in hits[0]["why"]


def test_search_forced_motion_type_has_higher_weight(library):
    hits = library.search("循环", top_k=3, options={"motion_type": "dance"})
    assert hits[0]["clip_id"] == "dance_wave_01"
    assert hits[0]["type_score"] == pytest.approx(6.0)
    assert "类型显式指定：舞蹈" in hits[0]["why"]


def test_search_is_deterministic(library):
    first = library.search("走路 循环 攻击", top_k=5)
    second = library.search("走路 循环 攻击", top_k=5)
    assert first == second
    scores = [hit["score"] for hit in first]
    assert scores == sorted(scores, reverse=True)
    keys = [(hit["score"], hit["clip_id"]) for hit in first]
    assert keys == sorted(keys, key=lambda item: (-item[0], item[1]))


def test_search_filters_exclude_instead_of_downweight(library):
    hits = library.search("走路", options={"filters": {"max_duration": 1.3}})
    assert [hit["clip_id"] for hit in hits] == ["walk_fwd_01"]
    assert "时长 1.20s 在" not in hits[0]["why"]
    assert "时长 1.20s ≤ 1.30s" in hits[0]["why"]
    assert library.search("走路", options={"filters": {"max_duration": 1.0}}) == []


def test_search_filters_loopable_and_root_motion(library):
    hits = library.search("", options={"filters": {"loopable": True,
                                                   "root_motion": True}})
    assert [hit["clip_id"] for hit in hits] == [
        "climb_ladder_01", "run_fwd_01", "walk_back_01", "walk_fwd_01"]
    assert all(hit["loopable"] and hit["root_motion"] for hit in hits)


def test_search_filters_has_bone(library):
    hits = library.search("", options={"filters": {"has_bone": "tail_01"}})
    assert [hit["clip_id"] for hit in hits] == ["dance_wave_01",
                                                "death_fall_01"]


def test_search_filters_min_fps(library):
    assert len(library.search("", top_k=50,
                              options={"filters": {"min_fps": 24}})) == 12
    assert library.search("", options={"filters": {"min_fps": 60}}) == []


def test_search_empty_query_lists_all_sorted_by_clip_id(library):
    hits = library.search("", top_k=50)
    assert len(hits) == len(library.clips)
    ids = [hit["clip_id"] for hit in hits]
    assert ids == sorted(ids)
    assert all(hit["why"] == "空查询：按名称列出全部动作" for hit in hits)
    assert all(hit["score"] == 0.0 for hit in hits)
    assert len(library.search("", top_k=3)) == 3


def test_search_no_hit_and_bad_options_return_empty(library):
    assert library.search("qqqzzz-not-exist") == []
    assert library.search("走路", options={"filters": "bad"})
    assert library.search("走路", options="bad")
    assert library.search("走路", options={"filters": {"min_duration": 99}}) == []


def test_search_top_k_bounds(library):
    assert library.search("走路", top_k=0) == []
    assert library.search("走路", top_k=-3) == []
    assert len(library.search("", top_k=500)) == len(library.clips)


def test_search_matches_bone_names(library):
    hits = library.search("tail", top_k=5)
    assert [hit["clip_id"] for hit in hits] == ["death_fall_01",
                                                "dance_wave_01"]
    assert all("tail" in hit["matched_terms"] for hit in hits)
    assert all("tail_01" in hit["bones"] for hit in hits)


def test_similar_to_is_deterministic_and_excludes_self(library):
    first = library.similar_to("walk_fwd_01", top_k=5)
    second = library.similar_to("walk_fwd_01", top_k=5)
    assert first == second
    assert first[0]["clip_id"] == "walk_back_01"
    assert all(hit["clip_id"] != "walk_fwd_01" for hit in first)
    assert [hit["rank"] for hit in first] == [1, 2, 3, 4, 5]
    scores = [hit["score"] for hit in first]
    assert scores == sorted(scores, reverse=True)
    assert "特征向量余弦相似度" in first[0]["why"]


def test_similar_to_unknown_clip_and_zero_top_k(library):
    assert library.similar_to("not-exist") == []
    assert library.similar_to("walk_fwd_01", top_k=0) == []


def test_add_clip_rebuilds_and_keeps_ids_unique():
    library = MotionLibrary(clips=[])
    first = library.add_clip({"name": "Jump_Up 跳跃 上升",
                              "frame_count": 30, "fps": 30})
    second = library.add_clip({"name": "Jump_Up 跳跃 上升",
                               "frame_count": 30, "fps": 30})
    assert first.clip_id == "clip_0001"
    assert second.clip_id == "clip_0002"
    assert library.search("跳跃")[0]["motion_type"] == "jump"

    dupe = library.add_clip({"clip_id": "clip_0001", "name": "Dupe 走路"})
    assert dupe.clip_id == "clip_0001_2"
    assert len(library.clips) == 3


def test_empty_library_is_safe():
    library = MotionLibrary(clips=[])
    assert library.search("走路") == []
    assert library.similar_to("x") == []
    assert library.context_for("走路") == ""
    stats = library.describe()
    assert stats["clip_count"] == 0
    assert sum(stats["types"].values()) == 0


def test_describe_contract_fields(library):
    stats = library.describe()
    assert set(stats) == {"contract", "clip_count", "types", "bone_count",
                          "bones", "has_audio", "offline"}
    assert stats["contract"] == CONTRACT_VERSION == "1.0"
    assert stats["has_audio"] is False
    assert stats["offline"] is True
    assert sum(stats["types"].values()) == stats["clip_count"]
    assert len(stats["bones"]) <= 40
    assert stats["bones"] == sorted(stats["bones"])
    assert stats["bone_count"] >= len(stats["bones"])


def test_context_for_truncates_and_has_citations(library):
    text = library.context_for("走路", top_k=2, max_chars=400)
    assert len(text) <= 400
    assert "clip_id=" in text
    assert "类型 行走" in text
    assert library.context_for("走路", top_k=2, max_chars=0) == ""
    assert library.context_for("qqqzzz") == ""


def test_to_index_from_index_roundtrip(library):
    data = library.to_index()
    assert data["contract"] == CONTRACT_VERSION
    assert data["clip_count"] == len(library.clips)
    restored = MotionLibrary.from_index(data)
    assert [clip.clip_id for clip in restored.clips] == [
        clip.clip_id for clip in library.clips]
    assert restored.describe() == library.describe()
    assert restored.search("攻击") == library.search("攻击")


def test_from_index_accepts_list_and_rejects_garbage():
    library = MotionLibrary.from_index([{"clip_id": "a", "name": "A 走路"}])
    assert [clip.clip_id for clip in library.clips] == ["a"]
    assert MotionLibrary.from_index("bad").clips == []
    assert MotionLibrary.from_index({"clips": "bad"}).clips == []
    assert MotionLibrary.from_index(None).clips == []


def test_constructor_accepts_index_dict():
    library = MotionLibrary(clips={
        "contract": "1.0",
        "clips": [{"clip_id": "a", "name": "A 走路"},
                  {"clip_id": "b", "name": "B 攻击"}],
    })
    assert [clip.clip_id for clip in library.clips] == ["a", "b"]
    assert MotionLibrary(clips={"clips": "bad"}).clips == []


def test_index_json_is_stable():
    first = json.dumps(MotionLibrary().to_index(), sort_keys=True,
                       ensure_ascii=False)
    second = json.dumps(MotionLibrary().to_index(), sort_keys=True,
                        ensure_ascii=False)
    assert first == second
    assert first == json.dumps(json.loads(first), sort_keys=True,
                               ensure_ascii=False)


# --------------------------------------------------------------------------- #
# 4. 扫描与 sidecar 读写
# --------------------------------------------------------------------------- #
def test_scan_directory_filename_heuristic(tmp_path):
    folder = tmp_path / "Locomotion"
    folder.mkdir()
    (folder / "Run_Sprint_Loop_30fps_24f.fbx").write_bytes(b"\xff\xfe\x00fake")
    (folder / "notes.txt").write_text("ignore", encoding="utf-8")

    items = loader.scan_directory(str(folder))
    assert len(items) == 1
    item = items[0]
    assert item["clip_id"] == "Run_Sprint_Loop_30fps_24f"
    assert item["fps"] == 30.0
    assert item["frame_count"] == 24
    assert item["loopable"] is True
    assert item["metadata"]["source"] == "filename_heuristic"
    assert item["metadata"]["motion_type"] == "run"
    assert item["features"]["estimated"] == 1.0
    assert item["path"].endswith("Run_Sprint_Loop_30fps_24f.fbx")
    assert item["path"].count("/") >= 1


def test_scan_directory_uses_direct_sidecar(tmp_path):
    (tmp_path / "Walk_Slow.fbx").write_bytes(b"\x00\x01")
    sidecar = {
        "clip_id": "walk_slow",
        "name": "Walk_Slow 走路 慢速",
        "frame_count": 40,
        "fps": 25.0,
        "loopable": True,
        "bones": ["hand_l"],
        "tags": ["走路"],
        "features": {"root_speed_mean": 40.0},
    }
    _write_text(tmp_path / "Walk_Slow.motion.json",
                json.dumps(sidecar, ensure_ascii=False))

    items = loader.scan_directory(str(tmp_path))
    assert len(items) == 1
    item = items[0]
    assert item["clip_id"] == "walk_slow"
    assert item["name"] == "Walk_Slow 走路 慢速"
    assert item["metadata"]["source"] == "sidecar"
    assert item["features"]["root_speed_mean"] == 40.0
    assert item["features"]["estimated"] == 1.0

    library = MotionLibrary.from_index({"clips": items})
    assert library.clips[0].duration == pytest.approx(1.6, abs=1e-6)


def test_scan_directory_uses_motion_index_sidecar(tmp_path):
    (tmp_path / "Idle_Breath.fbx").write_bytes(b"\x00")
    index = {
        "contract": "1.0",
        "clip_count": 1,
        "clips": [{
            "clip_id": "idle_breath",
            "name": "Idle_Breath 待机 呼吸",
            "path": "source/Idle_Breath.fbx",
            "fps": 30.0,
            "frame_count": 60,
            "tags": ["待机"],
        }],
    }
    _write_text(tmp_path / "motion_index.json",
                json.dumps(index, ensure_ascii=False))

    items = loader.scan_directory(str(tmp_path))
    assert len(items) == 1
    assert items[0]["clip_id"] == "idle_breath"
    assert items[0]["metadata"]["source"] == "sidecar"


def test_scan_directory_missing_or_empty_returns_empty(tmp_path):
    assert loader.scan_directory(str(tmp_path / "nope")) == []
    assert loader.scan_directory(None) == []
    assert loader.scan_directory("") == []
    empty = tmp_path / "empty"
    empty.mkdir()
    assert loader.scan_directory(str(empty)) == []


def test_scan_directory_does_not_parse_fbx_binary(tmp_path):
    # 非法 UTF-8 + 伪造的 FBX 头：只要不 open() 内容就能够正常扫描。
    payload = b"Kaydara FBX Binary\x00\x1a\xff\xfe\x00" + b"\xff" * 64
    (tmp_path / "Jump_Fall.fbx").write_bytes(payload)
    items = loader.scan_directory(str(tmp_path))
    assert len(items) == 1
    assert items[0]["metadata"]["source"] == "filename_heuristic"
    assert items[0]["features"]["estimated"] == 1.0
    assert payload == (tmp_path / "Jump_Fall.fbx").read_bytes()


def test_save_index_and_load_index_roundtrip(tmp_path, library):
    target = tmp_path / "sidecar" / "motion_index.json"
    saved = loader.save_index(library, str(target))
    assert saved == str(target)
    text = _read(str(target))
    assert "\r\n" not in text
    assert text.endswith("\n")
    data = json.loads(text)
    assert list(data) == ["clip_count", "clips", "contract"]

    loaded = loader.load_index(str(target))
    assert [clip.clip_id for clip in loaded.clips] == [
        clip.clip_id for clip in library.clips]
    assert loaded.clips[0].features == library.clips[0].features
    assert loaded.search("走路") == library.search("走路")


def test_load_index_missing_or_broken_falls_back_to_builtin(tmp_path):
    assert loader.load_index(str(tmp_path / "missing.json")).clips
    broken = tmp_path / "broken.json"
    _write_text(broken, "{not json")
    assert len(loader.load_index(str(broken)).clips) >= 8
    empty = tmp_path / "empty.json"
    _write_text(empty, json.dumps({"contract": "1.0", "clips": []}))
    assert len(loader.load_index(str(empty)).clips) >= 8


def test_save_index_rejects_unsupported_library(tmp_path):
    with pytest.raises(TypeError):
        loader.save_index(object(), str(tmp_path / "x.json"))


# --------------------------------------------------------------------------- #
# 5. 插件编排层
# --------------------------------------------------------------------------- #
def test_plugin_info(core):
    data = core.info()
    assert data["id"] == TOOL_ID
    assert data["read_only"] is True
    assert data["offline"] is True
    assert data["permissions"] == ["scene.read"]
    assert data["default_query"] == ""


def test_plugin_search_is_json_serializable(core):
    payload = core.search("走路 循环", top_k=3)
    assert set(payload) == {"ok", "tool_id", "query", "top_k", "count",
                            "results", "clips", "stats", "offline", "warning"}
    assert payload["ok"] is True
    assert payload["count"] == len(payload["results"]) == 3
    assert payload["offline"] is True
    assert len(payload["clips"]) == 12
    assert payload["warning"] == ""
    text = json.dumps(payload, ensure_ascii=False)
    assert "Walk_Fwd_01" in text
    for hit in payload["results"]:
        assert set(hit) == {
            "clip_id", "name", "score", "text_score", "type_score",
            "motion_type", "duration", "loopable", "root_motion", "bones",
            "tags", "path", "rank", "matched_terms", "why"}


def test_plugin_search_empty_query_lists_all(core):
    payload = core.search("", top_k=50)
    assert payload["count"] == 12
    assert [hit["clip_id"] for hit in payload["results"]] == sorted(
        hit["clip_id"] for hit in payload["results"])
    assert payload["warning"] == ""


def test_plugin_search_no_hit_warns(core):
    payload = core.search("qqqzzz", top_k=3)
    assert payload["count"] == 0
    assert payload["results"] == []
    assert "未命中" in payload["warning"]


def test_plugin_context_and_similar(core):
    assert "clip_id=" in core.context_for("走路", top_k=2)
    similar = core.similar_to("walk_fwd_01", top_k=3)
    assert similar and similar[0]["clip_id"] == "walk_back_01"


def test_run_report_metadata_and_findings():
    result = runner.run_tool(TOOL_ID, {"metadata": {"query": "走路"}},
                             dcc="maya", adapter=NullAdapter())
    assert isinstance(result.report, Report)
    assert result.report.findings
    assert all(finding.rule_id == "motion_library.hit"
               and finding.severity.value == "info"
               for finding in result.report.findings)
    metadata = result.report.metadata
    assert metadata["query"] == "走路"
    assert metadata["count"] == len(result.report.findings)
    assert metadata["results"] and metadata["stats"]
    safety = metadata["safety"]
    assert safety["read_only"] is True
    assert safety["scene_write"] is False
    assert safety["network"] is False
    assert safety["deterministic"] is True
    assert "warning" in metadata
    assert result.to_dict()["metadata"]["results"] == metadata["results"]


def test_run_report_query_priority_options_first():
    result = runner.run_tool(
        TOOL_ID,
        {"metadata": {"query": "走路"}},
        dcc="maya", adapter=NullAdapter(),
        options={"query": "攻击", "top_k": 2},
    )
    assert result.report.metadata["query"] == "攻击"
    assert result.report.findings[0].data["motion_type"] == "attack"
    assert len(result.report.findings) == 2

    result = runner.run_tool(TOOL_ID, {"metadata": {"query": "舞蹈"}},
                             dcc="maya", adapter=NullAdapter())
    assert result.report.metadata["query"] == "舞蹈"
    assert result.report.findings[0].data["motion_type"] == "dance"


def test_run_report_defaults_to_all_clips():
    result = runner.run_tool(TOOL_ID, {}, dcc="unreal", adapter=NullAdapter(),
                             options={"top_k": 50})
    assert result.report.metadata["query"] == ""
    assert result.report.metadata["count"] == 12
    assert result.report.metadata["warning"] == ""
    assert result.report.metadata["stats"]["clip_count"] == 12


def test_manifest_valid_and_discoverable():
    plugin = registry.load_manifest(os.path.join(PLUGIN_DIR, "manifest.json"))
    assert plugin.id == TOOL_ID
    assert plugin.ui == "web"
    assert plugin.permissions == ["scene.read"]
    assert "rules" not in plugin.raw
    ids = [item.id for item in registry.discover()]
    assert TOOL_ID in ids
    assert registry.validate_manifest(plugin.raw) == []


def test_plugin_registers_no_rules():
    from tak.core.rules.base import RULE_REGISTRY

    runner.load_tool_modules(runner.find_plugin(TOOL_ID))
    assert [rule for rule in RULE_REGISTRY if rule.plugin_id == TOOL_ID] == []


# --------------------------------------------------------------------------- #
# 6. Web 面板
# --------------------------------------------------------------------------- #
def test_ui_web_escapes_and_has_no_external_links(core, ui):
    payload = core.search("走路", top_k=2)
    payload["results"][0]["name"] = "<script>alert(1)</script>"
    payload["results"][0]["why"] = '"><img src=x>'
    panel = ui.render_panel_html(payload)
    assert 'class="tak-panel"' in panel
    assert "tak-table" in panel and "tak-sev-info" in panel
    assert "<script" not in panel
    assert "&lt;script&gt;" in panel
    assert "&lt;img" in panel
    assert "http://" not in panel and "https://" not in panel
    assert "motion_library" in panel


def test_ui_web_has_no_literal_colors(core, ui):
    panel = ui.render_panel_html(core.search("攻击", top_k=2))
    for pattern in (r"#[0-9a-fA-F]{3,8}\b", r"\brgba?\s*\(", r"\bhsla?\s*\(",
                    r"\bwhite\b", r"\bblack\b"):
        assert not re.search(pattern, panel)


def test_ui_web_report_html_entrypoint():
    result = runner.run_tool(TOOL_ID, {"metadata": {"query": "攻击"}},
                             dcc="maya", adapter=NullAdapter())
    html = registry.load_plugin_module(
        runner.find_plugin(TOOL_ID), "ui_web").render_report_html(result)
    assert "动作库语义检索" in html
    assert "attack_slash_01" in html


def test_ui_web_panel_data_is_pure_structure(core, ui):
    data = ui.panel_data(core.search("走路", top_k=2))
    assert data["section_id"] == TOOL_ID
    assert data["title"]
    assert isinstance(data["items"], list) and data["items"]
    for item in data["items"]:
        assert set(item) == {"label", "value", "severity"}
        assert item["severity"] in ("info", "warning", "error", "blocker")
    json.dumps(data, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# 7. 静态回归：3.9 语法 / 零 DCC / 零第三方 / 无 DCC 可 import
# --------------------------------------------------------------------------- #
def test_new_files_exist_and_parse_as_python39():
    for path in NEW_FILES:
        assert os.path.isfile(path), path
        ast.parse(_read(path), filename=path, feature_version=(3, 9))


def test_motion_core_has_no_dcc_or_third_party_imports():
    allowed = ALLOWED_STDLIB | {"tak"}
    for name in sorted(os.listdir(MOTION_DIR)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(MOTION_DIR, name)
        roots = set(_import_roots(_read(path)))
        assert not (roots & FORBIDDEN_ROOTS), "%s 出现 DCC import" % name
        assert roots <= allowed, "%s 出现越界 import: %s" % (
            name, sorted(roots - allowed))


def test_plugin_files_avoid_dcc_imports():
    for name in ("core.py", "ui_web.py"):
        roots = set(_import_roots(_read(os.path.join(PLUGIN_DIR, name))))
        assert not (roots & FORBIDDEN_ROOTS)
        assert roots <= ALLOWED_STDLIB | {"tak", "tools"}


def test_motion_package_imports_without_any_dcc():
    code = ("import sys, tak.core.motion as m; "
            "assert m.CONTRACT_VERSION == '1.0'; "
            "assert 'maya' not in sys.modules and 'unreal' not in sys.modules; "
            "print(len(m.MotionLibrary().clips))")
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                               env=env, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "12"
