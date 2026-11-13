# -*- coding: utf-8 -*-
"""Maya 适配器回归测试（不依赖真实 Maya）。

用 monkeypatch 注入最小 maya / maya.cmds / maya.OpenMaya 假模块，
覆盖：延迟导入、无 Maya 降级、快照字段、蒙皮采样、undo 三段、异常兜底、
离线 fixture、FBX sidecar 与 headless 可导入。
"""
from __future__ import annotations

import json
import sys
from types import ModuleType

import pytest

from tak.adapters.base import Capability, DCC
from tak.adapters.maya import (MayaAdapter, capture_snapshot, create_adapter,
                               is_available, load_fixture_snapshot, write_snapshot)

IDENTITY = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]


def _drop_maya(monkeypatch):
    for name in [item for item in list(sys.modules)
                 if item == "maya" or item.startswith("maya.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)


class FakeMayaState:
    """假宿主状态：所有 cmds 调用都读写这里，便于断言副作用。"""

    def __init__(self):
        self.fail = False
        self.calls = []
        self.selection = []
        self.messages = []
        self.meshes = ["body"]
        self.joints = {
            "root": {"parent": None, "translate": [0.0, 0.0, 0.0]},
            "child": {"parent": "root", "translate": [0.0, 10.0, 0.0]},
            "ns:extra": {"parent": None, "translate": [0.0, 0.0, 0.0]},
        }
        self.weights = {0: [0.5, 0.5], 1: [1.0, 0.0]}

    def check(self):
        if self.fail:
            raise RuntimeError("fake maya failure")


def _build_fake_maya(state):
    maya = ModuleType("maya")
    maya.__path__ = []  # type: ignore[attr-defined]
    cmds = ModuleType("maya.cmds")
    open_maya = ModuleType("maya.OpenMaya")

    def ls(*args, **kwargs):
        state.check()
        if kwargs.get("selection"):
            return list(state.selection)
        node_type = kwargs.get("type")
        if node_type == "joint":
            return list(state.joints)
        if node_type == "mesh":
            return list(state.meshes)
        return list(state.joints) + list(state.meshes)

    def list_relatives(node, **kwargs):
        state.check()
        if kwargs.get("parent"):
            info = state.joints.get(node)
            if info is None:
                return []
            parent = info.get("parent")
            return [parent] if parent else []
        return []

    def get_attr(attr):
        state.check()
        node, _, name = str(attr).partition(".")
        if name in ("worldMatrix", "matrix"):
            return list(IDENTITY)
        info = state.joints.get(node)
        if info is None:
            if node == "body" and name == "visibility":
                return True
            raise RuntimeError("No object matches name: %s" % node)
        if name in ("translate", "t"):
            return list(info["translate"])
        if name in ("rotate", "r"):
            return [0.0, 0.0, 0.0]
        if name in ("scale", "s"):
            return [1.0, 1.0, 1.0]
        if name in ("visibility", "v"):
            return True
        raise RuntimeError("No attribute matches: %s" % attr)

    def node_type(node):
        state.check()
        if node in state.joints:
            return "joint"
        if node in state.meshes:
            return "mesh"
        if node == "skinCluster1":
            return "skinCluster"
        return ""

    def list_history(node, **kwargs):
        state.check()
        return ["skinCluster1"] if node in state.meshes else []

    def skin_cluster(cluster, **kwargs):
        state.check()
        query = kwargs.get("query") or kwargs.get("q")
        if query and kwargs.get("influence"):
            return ["root", "child"]
        return "skinCluster1"

    def skin_percent(cluster, vertex, **kwargs):
        state.check()
        index = int(str(vertex).rsplit("[", 1)[-1].rstrip("]"))
        return list(state.weights.get(index, [1.0, 0.0]))

    def poly_evaluate(node, **kwargs):
        state.check()
        return len(state.weights)

    def obj_exists(node):
        state.check()
        return node in state.joints or node in state.meshes

    def about(**kwargs):
        state.check()
        return "2023" if kwargs.get("version") else "Fake Maya 2023"

    def current_unit(**kwargs):
        state.check()
        return "cm"

    def up_axis(**kwargs):
        state.check()
        return "y"

    def playback_options(**kwargs):
        state.check()
        if kwargs.get("minTime"):
            return 1.0
        if kwargs.get("maxTime"):
            return 120.0
        return 0.0

    def undo_info(**kwargs):
        state.check()
        if kwargs.get("openChunk"):
            state.calls.append(("openChunk", kwargs.get("chunkName")))
        elif kwargs.get("closeChunk"):
            state.calls.append(("closeChunk", None))

    def undo():
        state.check()
        state.calls.append(("undo", None))

    def select(nodes=None, **kwargs):
        state.check()
        state.selection = list(nodes or [])
        return state.selection

    def file(path=None, **kwargs):
        state.check()
        if kwargs.get("query"):
            return "probe_scene.ma"
        state.calls.append(("file", path))
        with open(path, "wb") as fh:
            fh.write(b"FBX")
        return path

    def record(name):
        def inner(*args, **kwargs):
            state.check()
            state.calls.append((name, args, kwargs))
            return True
        return inner

    for name, func in {
        "ls": ls, "listRelatives": list_relatives, "getAttr": get_attr,
        "nodeType": node_type, "listHistory": list_history,
        "skinCluster": skin_cluster, "skinPercent": skin_percent,
        "polyEvaluate": poly_evaluate, "objExists": obj_exists, "about": about,        "currentUnit": current_unit, "upAxis": up_axis,
        "playbackOptions": playback_options, "undoInfo": undo_info,
        "undo": undo, "select": select, "file": file,
        "loadPlugin": record("loadPlugin"), "FBXResetExport": record("FBXResetExport"),
        "FBXProperty": record("FBXProperty"),
    }.items():
        setattr(cmds, name, func)

    class MGlobal(object):
        @staticmethod
        def displayInfo(message):
            state.messages.append(("info", message))

        @staticmethod
        def displayWarning(message):
            state.messages.append(("warning", message))

        @staticmethod
        def displayError(message):
            state.messages.append(("error", message))

    open_maya.MGlobal = MGlobal
    standalone = ModuleType("maya.standalone")

    def initialize(**kwargs):
        state.calls.append(("initialize", None))

    def uninitialize():
        state.calls.append(("uninitialize", None))

    standalone.initialize = initialize
    standalone.uninitialize = uninitialize
    maya.cmds = cmds
    maya.OpenMaya = open_maya
    maya.standalone = standalone
    # 故意不提供 xform：验证适配器会退回 getAttr(worldMatrix)。
    return {"maya": maya, "maya.cmds": cmds, "maya.OpenMaya": open_maya,
            "maya.standalone": standalone}


@pytest.fixture
def fake_maya(monkeypatch):
    _drop_maya(monkeypatch)
    state = FakeMayaState()
    for name, module in _build_fake_maya(state).items():
        monkeypatch.setitem(sys.modules, name, module)
    return state


@pytest.fixture
def no_maya(monkeypatch):
    _drop_maya(monkeypatch)
    return monkeypatch


# --------------------------------------------------------------------- 延迟导入
def test_headless_import_without_maya(no_maya):
    assert is_available() is False
    adapter = create_adapter()
    assert isinstance(adapter, MayaAdapter)
    assert adapter.available is False
    assert adapter.dcc is DCC.MAYA and adapter.name == "maya"
    assert adapter.list_nodes() == []
    assert adapter.get_node_attrs("root") == {}
    snapshot = adapter.get_skeleton_snapshot()
    assert set(snapshot) == {"dcc", "version", "unit", "up_axis", "joints",
                             "frame_range", "skin", "metadata"}
    assert snapshot["joints"] == {}
    assert "degraded" in snapshot["metadata"]


def test_is_available_false_when_import_raises(monkeypatch):
    _drop_maya(monkeypatch)
    monkeypatch.setitem(sys.modules, "maya", None)
    assert is_available() is False


def test_is_available_true_with_fake_host(fake_maya):
    assert is_available() is True


def test_probe_does_not_initialize_standalone(fake_maya):
    is_available()
    create_adapter()
    assert not any(call[0] in ("initialize", "uninitialize") for call in fake_maya.calls)


def test_create_adapter_forwards_kwargs(no_maya):
    adapter = create_adapter(version="2024", python_version="3.10", data={}, ignored=True)
    assert adapter.version == "2024"
    assert adapter.python_version == "3.10"
    capabilities = {item for item in Capability if adapter.supports(item)}
    assert capabilities == {
        Capability.SCENE_QUERY, Capability.SKIN_QUERY,
        Capability.SKELETON_SNAPSHOT, Capability.FBX_EXPORT,
        Capability.UNDO_SCOPE, Capability.UI_HOST,
    }


def test_runner_adapter_for_maya(fake_maya):
    from tak.core.runner import adapter_for
    adapter = adapter_for("maya")
    assert isinstance(adapter, MayaAdapter)
    assert adapter.dcc is DCC.MAYA
    assert adapter.available is True
    assert adapter.supports(Capability.SKELETON_SNAPSHOT)
    assert adapter.supports(Capability.FBX_EXPORT)


# --------------------------------------------------------------------- 快照
def test_snapshot_fields_complete(fake_maya):
    adapter = MayaAdapter()
    snapshot = adapter.get_skeleton_snapshot()
    assert snapshot["dcc"] == "maya"
    assert snapshot["version"] == "2023"
    assert snapshot["unit"] == "cm"
    assert snapshot["up_axis"] == "Y"
    assert snapshot["frame_range"] == [1.0, 120.0]
    assert set(snapshot["joints"]) == {"root", "child", "ns:extra"}
    for name, joint in snapshot["joints"].items():
        assert set(joint) == {"parent", "matrix", "depth", "scale", "rotate", "namespace"}
        assert len(joint["matrix"]) == 16
        assert len(joint["scale"]) == 3
        assert len(joint["rotate"]) == 3
        assert isinstance(joint["depth"], int)
        assert joint["matrix"] == IDENTITY
    assert snapshot["joints"]["root"]["parent"] is None
    assert snapshot["joints"]["root"]["depth"] == 0
    assert snapshot["joints"]["child"]["parent"] == "root"
    assert snapshot["joints"]["child"]["depth"] == 1
    assert snapshot["joints"]["ns:extra"]["namespace"] == "ns"
    assert snapshot["skin"]["max_influences_found"] == 2
    assert snapshot["skin"]["deformers"] == ["skinCluster"]
    assert snapshot["metadata"]["joint_count"] == 3
    assert json.dumps(snapshot)  # 必须可 JSON 序列化


def test_root_filter(fake_maya):
    snapshot = MayaAdapter().get_skeleton_snapshot(root="root")
    assert set(snapshot["joints"]) == {"root", "child"}
    assert snapshot["metadata"]["root"] == "root"


def test_node_attrs(fake_maya):
    attrs = MayaAdapter().get_node_attrs("child")
    assert attrs["node"] == "child"
    assert attrs["parent"] == "root"
    assert attrs["translate"] == [0.0, 10.0, 0.0]
    assert attrs["rotate"] == [0.0, 0.0, 0.0]
    assert attrs["scale"] == [1.0, 1.0, 1.0]
    assert attrs["visibility"] is True
    assert attrs["frozen"] is False
    assert attrs["namespace"] == ""
    assert MayaAdapter().get_node_attrs("ns:extra")["namespace"] == "ns"
    assert MayaAdapter().get_node_attrs("not_exist") == {}


def test_skin_summary(fake_maya):
    summary = MayaAdapter().get_skin_summary("body")
    assert summary["skin_clusters"] == ["skinCluster1"]
    assert summary["influences"] == ["root", "child"]
    assert summary["max_influences_found"] == 2
    assert summary["weight_sum_deviation"] == 0.0
    assert summary["deformers"] == ["skinCluster"]
    assert summary["vertex_count"] == 2
    assert summary["sampled_vertices"] == 2
    assert summary["truncated"] is False


def test_skin_sampling_is_capped(fake_maya, monkeypatch):
    adapter = MayaAdapter()
    monkeypatch.setattr(adapter, "max_sample_vertices", 1)
    summary = adapter.get_skin_summary("body")
    assert summary["sampled_vertices"] == 1
    assert summary["truncated"] is True
    assert summary["max_influences_found"] == 2  # 第 0 个顶点两路影响


def test_skin_without_cluster_returns_zero_shape(fake_maya):
    summary = MayaAdapter().get_skin_summary("not_a_mesh")
    assert summary["max_influences_found"] == 0
    assert summary["weight_sum_deviation"] == 0.0
    assert summary["deformers"] == []


# --------------------------------------------------------------------- undo / FBX
def test_undo_three_phases(fake_maya):
    adapter = MayaAdapter()
    token = adapter.begin_undo("probe")
    assert token.startswith("maya:")
    adapter.commit_undo(token)
    rollback_token = adapter.begin_undo("rollback")
    adapter.rollback(rollback_token)
    assert ("openChunk", "probe") in fake_maya.calls
    assert ("openChunk", "rollback") in fake_maya.calls
    assert ("closeChunk", None) in fake_maya.calls
    assert ("undo", None) in fake_maya.calls


def test_export_fbx_writes_sidecar(fake_maya, tmp_path):
    target = tmp_path / "probe.fbx"
    result = MayaAdapter().export_fbx(str(target), {"fbx": {"include_meshes": True}})
    assert result["ok"] is True
    assert result["sidecar"].endswith(".snapshot.json")
    assert target.is_file()
    with open(result["sidecar"], "r", encoding="utf-8") as fh:
        sidecar = json.load(fh)
    assert set(sidecar["joints"]) == {"root", "child", "ns:extra"}
    assert any(call[0] == "file" for call in fake_maya.calls)


def test_export_fbx_rejects_empty_path(fake_maya):
    result = MayaAdapter().export_fbx("")
    assert result["ok"] is False
    assert result["error"]


# --------------------------------------------------------------------- 兜底
def test_all_host_calls_degrade_on_failure(fake_maya):
    fake_maya.fail = True
    adapter = MayaAdapter()
    assert adapter.list_nodes() == []
    assert adapter.get_node_attrs("root") == {}
    assert adapter.get_skin_summary("body")["max_influences_found"] == 0
    snapshot = adapter.get_skeleton_snapshot()
    assert snapshot["joints"] == {}
    assert snapshot["skin"]["max_influences_found"] == 0
    assert adapter.begin_undo("x") == ""
    adapter.commit_undo("")
    adapter.rollback("")
    result = adapter.export_fbx("out.fbx")
    assert result["ok"] is False and result["error"]
    adapter.log("no host", "error")  # 不抛异常即可


def test_log_uses_openmaya(fake_maya):
    MayaAdapter().log("hello", "info")
    assert fake_maya.messages[-1][0] == "info"
    assert "hello" in fake_maya.messages[-1][1]


def test_log_falls_back_to_stdout(fake_maya, monkeypatch, capsys):
    monkeypatch.delitem(sys.modules, "maya.OpenMaya", raising=False)
    monkeypatch.delattr(sys.modules["maya"], "OpenMaya", raising=False)
    MayaAdapter().log("fallback", "warning")
    assert "fallback" in capsys.readouterr().out


# --------------------------------------------------------------------- 离线 / 写盘
def test_offline_adapter_from_fixture(no_maya):
    fixture = load_fixture_snapshot()
    adapter = MayaAdapter(data={"snapshot": fixture})
    snapshot = adapter.get_skeleton_snapshot()
    assert len(snapshot["joints"]) == len(fixture["joints"]) == 19
    assert snapshot["metadata"]["offline"] is True
    assert snapshot["skin"]["max_influences_found"] == 4


def test_capture_snapshot_uses_fixture_without_host(no_maya):
    snapshot = capture_snapshot()
    assert snapshot["metadata"]["offline"] is True
    assert len(snapshot["joints"]) == 19


def test_capture_snapshot_prefers_adapter(fake_maya):
    snapshot = capture_snapshot(MayaAdapter())
    assert set(snapshot["joints"]) == {"root", "child", "ns:extra"}
    assert "offline" not in snapshot["metadata"]


def test_write_snapshot_roundtrip(tmp_path):
    snapshot = load_fixture_snapshot()
    path = write_snapshot(str(tmp_path / "nested" / "snap.json"), snapshot)
    with open(path, "r", encoding="utf-8") as fh:
        loaded = json.load(fh)
    assert loaded["joints"]["root"]["depth"] == 0
    assert loaded["skin"]["deformers"] == ["skinCluster"]


def test_missing_fixture_degrades(tmp_path):
    snapshot = load_fixture_snapshot(str(tmp_path / "missing.json"))
    assert snapshot["joints"] == {}
    assert "error" in snapshot["metadata"]
