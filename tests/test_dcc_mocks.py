# -*- coding: utf-8 -*-
"""P1.4：四套 DCC mock 的安装/还原、数据一致性、异常安全与真实 fixtures 加载。"""
import json
import os
import sys
import types

import pytest

from tak.adapters.base import DCC, DCCAdapter, Capability
from tak.core.diff import SkeletonSnapshot, validate_snapshot
from tak.core.mocks import (
    MockScene,
    available_dccs,
    install_mock,
    load_fixture,
    uninstall_mock,
)
from tak.core.spec import load_spec

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "fixtures")
GOOD_FIXTURE = os.path.join(FIXTURES, "good", "good_001.json")
BAD_FIXTURE = os.path.join(FIXTURES, "bad", "bad_001.json")

EXPECTED_MODULES = {
    "maya": {"maya", "maya.cmds", "maya.OpenMaya"},
    "max": {"pymxs", "pymxs.runtime"},
    "mobu": {"pyfbsdk"},
    "unreal": {"unreal"},
}
DCC_LIST = ["maya", "max", "mobu", "unreal"]


@pytest.fixture(autouse=True)
def clean_mocks():
    uninstall_mock()
    yield
    uninstall_mock()


def _fixture(path=GOOD_FIXTURE):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------- 基础契约
def test_available_dccs_is_stable():
    assert available_dccs() == ("maya", "max", "mobu", "unreal")


@pytest.mark.parametrize("dcc", DCC_LIST)
def test_install_injects_expected_modules(dcc):
    handle = install_mock(dcc, GOOD_FIXTURE)
    assert handle.dcc == dcc
    assert set(handle.modules) == EXPECTED_MODULES[dcc]
    for name, module in handle.modules.items():
        assert sys.modules[name] is module
        assert isinstance(module, types.ModuleType)
        assert module.__name__ == name
        if "." in name:
            parent_name, _, child = name.rpartition(".")
            assert getattr(sys.modules[parent_name], child) is module
    assert handle.installed() is True


@pytest.mark.parametrize("dcc", DCC_LIST)
def test_uninstall_restores_modules(dcc):
    expected = EXPECTED_MODULES[dcc]
    before = {name: sys.modules.get(name) for name in expected}
    handle = install_mock(dcc, GOOD_FIXTURE)
    assert uninstall_mock() is True
    for name in expected:
        assert sys.modules.get(name) is before[name]
    assert handle.installed() is False


def test_uninstall_without_install_is_safe():
    assert uninstall_mock() is False
    assert uninstall_mock() is False


@pytest.mark.parametrize("dcc", DCC_LIST)
def test_repeated_install_keeps_modules_reversible(dcc):
    expected = EXPECTED_MODULES[dcc]
    first = install_mock(dcc, GOOD_FIXTURE)
    first_modules = dict(first.modules)
    second = install_mock(dcc, GOOD_FIXTURE)
    sample = sorted(expected)[0]
    assert second.modules[sample] is sys.modules[sample]
    assert all(second.modules[name] is not module for name, module in first_modules.items())
    assert uninstall_mock() is True
    for name in expected:
        assert name not in sys.modules


def test_existing_module_is_restored(monkeypatch):
    original = types.ModuleType("maya")
    monkeypatch.setitem(sys.modules, "maya", original)
    handle = install_mock("maya", GOOD_FIXTURE)
    assert sys.modules["maya"] is not original
    assert uninstall_mock() is True
    assert sys.modules["maya"] is original
    assert "maya.cmds" not in sys.modules
    assert handle.installed() is False


def test_switch_dcc_replaces_previous_installation():
    install_mock("maya", GOOD_FIXTURE)
    handle = install_mock("max", GOOD_FIXTURE)
    assert handle.dcc == "max"
    assert "maya" not in sys.modules and "maya.cmds" not in sys.modules
    assert "pymxs" in sys.modules and "pymxs.runtime" in sys.modules
    uninstall_mock()
    assert "pymxs" not in sys.modules and "pymxs.runtime" not in sys.modules


@pytest.mark.parametrize("alias,expected", [
    (DCC.UNREAL, "unreal"),
    ("3ds Max", "max"),
    ("MotionBuilder", "mobu"),
    ("ue5", "unreal"),
    ("Maya", "maya"),
])
def test_dcc_aliases(alias, expected):
    handle = install_mock(alias, GOOD_FIXTURE)
    assert handle.dcc == expected
    assert handle.adapter.describe()["dcc"] == expected


# --------------------------------------------------------------------- 异常安全
def test_unknown_dcc_raises_and_leaves_modules_untouched():
    with pytest.raises(ValueError, match="未知 DCC"):
        install_mock("blender")
    for name in ("maya", "maya.cmds", "pymxs", "pymxs.runtime", "pyfbsdk", "unreal"):
        assert name not in sys.modules


def test_invalid_snapshot_keeps_previous_install():
    handle = install_mock("maya", GOOD_FIXTURE)
    with pytest.raises(ValueError, match="joints"):
        install_mock("max", {"joints": {}})
    with pytest.raises(ValueError, match="parent"):
        install_mock("max", {"joints": {"a": {"parent": "missing"}}})
    with pytest.raises(TypeError, match="mock 场景"):
        install_mock("max", 42)
    assert sys.modules["maya.cmds"] is handle.modules["maya.cmds"]
    assert handle.installed() is True


def test_builder_failure_rolls_back(monkeypatch):
    import tak.core.mocks as mocks

    def boom(scene):
        raise RuntimeError("boom")

    monkeypatch.setitem(mocks._BUILDERS, "mobu", boom)
    with pytest.raises(RuntimeError, match="boom"):
        mocks.install_mock("mobu")
    assert "pyfbsdk" not in sys.modules
    assert mocks.uninstall_mock() is False

    monkeypatch.undo()
    assert install_mock("mobu", GOOD_FIXTURE).installed() is True


def test_adapter_after_uninstall_raises_clear_error():
    handle = install_mock("maya", GOOD_FIXTURE)
    uninstall_mock()
    with pytest.raises(RuntimeError, match="未安装"):
        handle.adapter.get_skeleton_snapshot()


# --------------------------------------------------------------------- 数据一致性
def test_scene_from_good_fixture_roundtrips():
    data = _fixture()
    scene = load_fixture(GOOD_FIXTURE)
    assert scene.dcc == "maya"
    assert len(scene.joints) == len(data["snapshot"]["joints"])
    snapshot = scene.snapshot()
    assert snapshot["joints"] == data["snapshot"]["joints"]
    assert snapshot["skin"] == data["snapshot"]["skin"]
    assert snapshot["frame_range"] == data["snapshot"]["frame_range"]
    assert snapshot["unit"] == data["snapshot"]["unit"]
    assert snapshot["up_axis"] == data["snapshot"]["up_axis"]
    assert SkeletonSnapshot.from_dict(snapshot).to_dict() == snapshot


def test_bad_fixture_loads_with_same_structure():
    data = _fixture(BAD_FIXTURE)
    scene = load_fixture(BAD_FIXTURE)
    assert len(scene.joints) == len(data["snapshot"]["joints"])
    detected = validate_snapshot(SkeletonSnapshot.from_dict(scene.snapshot()), load_spec())
    assert detected == data["expected_violations"]


@pytest.mark.parametrize("dcc", DCC_LIST)
def test_all_dccs_read_identical_joint_data(dcc):
    data = _fixture()
    handle = install_mock(dcc, GOOD_FIXTURE)
    snapshot = handle.adapter.get_skeleton_snapshot()
    assert snapshot["dcc"] == dcc
    assert snapshot["version"] == "2023"
    assert snapshot["joints"] == data["snapshot"]["joints"]
    assert snapshot["skin"] == data["snapshot"]["skin"]
    assert snapshot["unit"] == "cm" and snapshot["up_axis"] == "Y"
    assert SkeletonSnapshot.from_dict(snapshot).joints.keys() == data["snapshot"]["joints"].keys()


@pytest.mark.parametrize("dcc", DCC_LIST)
def test_default_scene_without_snapshot(dcc):
    handle = install_mock(dcc)
    snapshot = handle.adapter.get_skeleton_snapshot()
    assert list(snapshot["joints"]) == ["root", "pelvis", "spine_01", "head"]
    assert snapshot["joints"]["head"]["parent"] == "spine_01"
    assert snapshot["joints"]["head"]["depth"] == 3
    assert snapshot["skin"]["deformers"] == ["skinCluster"]


@pytest.mark.parametrize("dcc", DCC_LIST)
def test_adapter_contract(dcc):
    handle = install_mock(dcc, GOOD_FIXTURE)
    adapter = handle.adapter
    assert isinstance(adapter, DCCAdapter)
    assert adapter.supports(Capability.SCENE_QUERY)
    assert adapter.supports(Capability.SKELETON_SNAPSHOT)
    assert not adapter.supports(Capability.FBX_EXPORT)
    assert adapter.list_nodes("joint") == list(handle.scene.joints)
    assert adapter.list_nodes("mesh") == ["body"]
    attrs = adapter.get_node_attrs("root")
    assert attrs["type"] == "joint" and attrs["parent"] is None
    assert attrs["matrix"] == [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    assert adapter.get_skin_summary("body")["max_influences_found"] == 4
    assert adapter.get_skin_summary("missing") == {}
    assert adapter.get_node_attrs("missing") == {}


def test_mock_weights_are_uniform_and_reproducible():
    first = install_mock("maya", GOOD_FIXTURE).scene
    weights = first.weights_for("body")
    assert len(weights) == 16
    for vertex in weights:
        assert len(vertex) == first.skin["max_influences_found"] == 4
        assert abs(sum(vertex.values()) - 1.0) < 1e-9
    uninstall_mock()
    second = install_mock("max", GOOD_FIXTURE).scene
    assert second.weights_for("body") == weights


def test_snapshot_root_subtree_filter():
    handle = install_mock("maya", GOOD_FIXTURE)
    snapshot = handle.adapter.get_skeleton_snapshot(root="pelvis")
    assert "root" not in snapshot["joints"]
    assert "pelvis" in snapshot["joints"]
    assert "foot_R" in snapshot["joints"]
    with pytest.raises(KeyError):
        handle.adapter.get_skeleton_snapshot(root="missing")


# --------------------------------------------------------------------- 宿主 API 表面
def test_maya_cmds_surface():
    handle = install_mock("maya", GOOD_FIXTURE)
    import maya.cmds as cmds

    assert "root" in cmds.ls(type="joint")
    assert cmds.ls("foot_*", type="joint") == ["foot_L", "foot_R"]
    assert cmds.objExists("root") and not cmds.objExists("nope")
    assert cmds.nodeType("root") == "joint"
    assert cmds.nodeType("body") == "mesh"
    assert cmds.listRelatives("pelvis", parent=True) == ["root"]
    assert cmds.listRelatives("pelvis", children=True) == ["spine_01", "thigh_L", "thigh_R"]
    assert cmds.getAttr("root.translateX") == 0.0
    assert cmds.getAttr("root.scaleY") == 1.0
    assert len(cmds.getAttr("root.matrix")) == 16
    assert cmds.about(version=True) == "2023"

    cmds.setAttr("root.translateX", 3.0)
    cmds.setAttr("root.visibility", 0)
    attrs = handle.adapter.get_node_attrs("root")
    assert attrs["translation"][0] == 3.0
    assert attrs["visibility"] is False
    assert handle.adapter.get_skeleton_snapshot()["joints"]["root"]["matrix"][12] == 3.0


def test_pymxs_surface():
    install_mock("max", GOOD_FIXTURE)
    from pymxs import runtime as rt

    bones = [node for node in rt.objects if rt.classOf(node) == "BoneGeometry"]
    assert len(bones) == 19
    root = rt.getNodeByName("root")
    assert root is not None and root.name == "root"
    assert root.parent is None
    assert [child.name for child in root.children] == ["pelvis"]
    pelvis = rt.getNodeByName("pelvis")
    assert pelvis.parent is root
    assert list(pelvis.pos) == [0.0, 0.0, 0.0]
    assert len(root.transform) == 16
    assert rt.classOf(rt.getNodeByName("body")) == "Editable_Mesh"
    assert rt.classOf(rt.getNodeByName("missing")) == "NoneType"


def test_pyfbsdk_surface():
    install_mock("mobu", GOOD_FIXTURE)
    import pyfbsdk as fb

    system = fb.FBSystem()
    assert system.Version == "2023"
    models = system.Scene.Models
    skeletons = [model for model in models if isinstance(model, fb.FBModelSkeleton)]
    assert len(skeletons) == 19
    root = fb.FBFindModelByName("root")
    assert isinstance(root, fb.FBModelSkeleton)
    assert root.LongName == "root"
    assert root.Parent is None
    assert [child.Name for child in root.Children] == ["pelvis"]
    assert list(root.Translation) == [0.0, 0.0, 0.0]
    assert list(root.Scaling) == [1.0, 1.0, 1.0]
    assert isinstance(fb.FBFindModelByName("body"), fb.FBModelStatic)


def test_unreal_surface():
    handle = install_mock("unreal", GOOD_FIXTURE)
    import unreal

    assert "5.8" in unreal.SystemLibrary.get_engine_version()
    skeleton = unreal.load_asset("/Game/TAK/SK_Mannequin_Skeleton")
    reference = skeleton.get_reference_skeleton()
    assert reference.get_num() == 19
    assert reference.get_bone_name(0) == "root"
    assert reference.get_parent_index(0) == -1
    pelvis_index = reference.find_bone_index("pelvis")
    assert reference.get_parent_index(pelvis_index) == 0

    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    labels = {actor.get_actor_label() for actor in actors}
    assert "root" in labels and "body" in labels
    assert unreal.load_asset("/Game/TAK/SK_Mannequin").get_skeleton() is skeleton

    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    classes = {data.asset_class for data in registry.get_assets_by_class("Skeleton")}
    assert classes == {"Skeleton"}
    assert handle.scene.dcc == "unreal"


@pytest.mark.parametrize("dcc", DCC_LIST)
def test_mock_scene_is_dcc_neutral(dcc):
    scene = MockScene.from_snapshot(_fixture(), dcc=dcc)
    assert scene.dcc == dcc
    assert scene.snapshot()["joints"] == _fixture()["snapshot"]["joints"]
    assert scene.attrs("root")["type"] == "joint"
    assert scene.skin_summary("body")["deformers"] == ["skinCluster"]
