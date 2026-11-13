# -*- coding: utf-8 -*-
"""Unreal 适配器与编辑器入口回归测试（不依赖真实 UE）。

用 monkeypatch 注入最小 ``unreal`` 假模块（思路同 ``tak/core/mocks``，但不修改
它），覆盖：延迟导入、无 UE 降级、describe / api_probe、骨骼与资产查询、
蒙皮摘要、离线数据注入、import_validate 的通过与阻断两条路径、菜单注册、
启动引导脚本、commandlet 离线路径，以及新文件的 Python 3.9 语法。
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from types import ModuleType

import pytest

from tak.adapters.base import Capability, DCC
from tak.adapters.unreal import UnrealAdapter, create_adapter, is_available

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOOD_FIXTURE = os.path.join(ROOT, "fixtures", "good", "good_001.json")
PROBE_SCRIPT = os.path.join(ROOT, "validation", "ue_adapter_probe.py")

ENGINE_VERSION = "5.8.4-58902052+++UE5+Release-5.8"
SKELETON_PATH = "/Game/TAK/SK_Mannequin_Skeleton"
MESH_PATH = "/Game/TAK/SK_Mannequin"
ANIM_PATHS = ("/Game/TAK/AS_Idle", "/Game/TAK/AS_Run")


# --------------------------------------------------------------------------- #
# 假 unreal 模块
# --------------------------------------------------------------------------- #
class FakeVector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __iter__(self):
        return iter((self.x, self.y, self.z))


class FakeTransform:
    def __init__(self, translation=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0)):
        self.translation = FakeVector(*translation)
        self.scale3d = FakeVector(*scale)


class FakeFrameRate:
    def __init__(self, numerator=30, denominator=1):
        self.numerator, self.denominator = numerator, denominator


class FakeAssetData:
    def __init__(self, package_name, asset_class):
        self.package_name = package_name
        self.package_path = package_name
        self.asset_name = package_name.rsplit("/", 1)[-1]
        self.asset_class = asset_class


class FakeReferenceSkeleton:
    def __init__(self, state):
        self._names = list(state.bones)
        self._index = {name: index for index, name in enumerate(self._names)}
        self._parents = [state.bones[name] for name in self._names]

    def get_num(self):
        return len(self._names)

    def get_bone_name(self, index):
        return self._names[index]

    def get_parent_index(self, index):
        parent = self._parents[index]
        return -1 if parent is None else self._index[parent]

    def get_bone_transform(self, index):
        if not self._transforms:
            return None
        name = self._names[index]
        return FakeTransform(self._transforms.get(name, (0.0, 0.0, 0.0)))

    _transforms = None


class FakeSkeleton:
    asset_class = "Skeleton"

    def __init__(self, state):
        self.package_name = SKELETON_PATH
        self.asset_name = "SK_Mannequin_Skeleton"
        self._state = state

    def get_reference_skeleton(self):
        reference = FakeReferenceSkeleton(self._state)
        reference._transforms = self._state.transforms if self._state.with_transforms else None
        return reference

    def get_editor_property(self, name):
        if name in ("bone_tree", "reference_skeleton"):
            return None
        raise RuntimeError("unknown property %s" % name)


class FakeSkeletalMesh:
    asset_class = "SkeletalMesh"

    def __init__(self, state):
        self.package_name = MESH_PATH
        self.asset_name = "SK_Mannequin"
        self._state = state

    def get_skeleton(self):
        return FakeSkeleton(self._state)

    def get_editor_property(self, name):
        if name == "skeleton":
            return FakeSkeleton(self._state)
        if name in ("skin_summary", "tak_skin_summary"):
            return self._state.skin_summary
        if name in ("morph_targets", "morph_targets_data"):
            return [{"name": "Morph_A"}] if self._state.morph_targets else None
        if name in ("materials", "material_slots"):
            return [{"slot": 0}, {"slot": 1}]
        if name in ("curve_data", "compressed_curve_data", "float_curve_names"):
            return [1.0, 2.0, 3.0]
        if name == "sampling_frame_rate":
            return FakeFrameRate(30, 1)
        if name == "compression_scheme":
            return "Default"
        if name == "enable_root_motion":
            return True
        raise RuntimeError("unknown property %s" % name)


class FakeAnimAsset:
    asset_class = "AnimSequence"

    def __init__(self, path):
        self.package_name = path
        self.asset_name = path.rsplit("/", 1)[-1]


class FakeRegistry:
    def __init__(self, assets):
        self._assets = assets

    def get_assets_by_class(self, class_name, recursive=True):
        return [FakeAssetData(item.package_name, item.asset_class)
                for item in self._assets if item.asset_class == class_name]


class FakeMenu:
    def __init__(self, name, state):
        self.menu_name = name
        self._state = state
        self.entries = []
        self.sections = []

    def add_section(self, name, label=None):
        self.sections.append(name)
        return True

    def add_sub_menu(self, owner, section, name, label):
        menu = FakeMenu(name, self._state)
        self._state.menus[name] = menu
        return menu

    def add_menu_entry_object(self, entry):
        self.entries.append(entry)
        return None


class FakeToolMenus:
    def __init__(self, state):
        self._state = state

    def find_menu(self, name):
        return self._state.menus.get(name)

    def unregister_owner_by_name(self, owner):
        self._state.unregistered.append(owner)
        return True

    def refresh_all_widgets(self):
        self._state.refreshed += 1


class FakeToolMenuEntryScript:
    def init_entry(self, owner, menu_name, section, name, label, *args, **kwargs):
        self.owner = owner
        self.menu_name = menu_name
        self.section = section
        self.entry_name = name
        self.label = label
        return True

    def execute(self, context=None):  # 子类应覆盖
        return None


class FakePaths:
    def __init__(self, state):
        self._state = state

    def project_saved_dir(self):
        return self._state.saved_dir

    def convert_relative_path_to_full(self, package):
        return os.path.join(self._state.saved_dir, package.lstrip("/") + ".uasset")


class FakeUnrealState:
    def __init__(self):
        self.with_transforms = False
        self.transforms = {"root": (0.0, 0.0, 0.0), "pelvis": (0.0, 0.0, 10.0),
                           "spine_01": (0.0, 0.0, 25.0), "head": (0.0, 0.0, 45.0)}
        self.bones = {"root": None, "pelvis": "root",
                      "spine_01": "pelvis", "head": "spine_01"}
        self.skin_summary = None
        self.morph_targets = True
        self.saved_dir = os.getcwd()
        self.messages = []
        self.dialogs = []
        self.unregistered = []
        self.entries = []
        self.refreshed = 0
        self.menus = {"LevelEditor.MainMenu": FakeMenu("LevelEditor.MainMenu", self),
                      "ContentBrowser.AssetContextMenu":
                          FakeMenu("ContentBrowser.AssetContextMenu", self)}
        self.skeleton = FakeSkeleton(self)
        self.mesh = FakeSkeletalMesh(self)


def _build_fake_unreal(state):
    unreal = ModuleType("unreal")
    unreal.__doc__ = "TAK fake unreal 5.8"

    class SystemLibrary:
        @staticmethod
        def get_engine_version():
            return ENGINE_VERSION

        @staticmethod
        def launch_url(url):
            state.messages.append(("launch_url", url))
            return True

    class AssetRegistryHelpers:
        @staticmethod
        def get_asset_registry():
            return FakeRegistry([state.skeleton, state.mesh]
                                + [FakeAnimAsset(path) for path in ANIM_PATHS])

    class EditorAssetLibrary:
        @staticmethod
        def load_asset(path):
            return _load(path)

        @staticmethod
        def does_asset_exist(path):
            return _load(path) is not None

    class EditorUtilityLibrary:
        @staticmethod
        def get_selected_assets():
            return []

    class EditorDialog:
        @staticmethod
        def show_message(**kwargs):
            state.dialogs.append(dict(kwargs))

    class AnimSequence:
        pass

    def _load(path):
        text = str(path)
        if text == SKELETON_PATH:
            return state.skeleton
        if text == MESH_PATH:
            return state.mesh
        return None

    def log(text):
        state.messages.append(("log", text))

    def log_warning(text):
        state.messages.append(("warning", text))

    def log_error(text):
        state.messages.append(("error", text))

    def uclass(*args, **kwargs):
        def decorator(cls):
            return cls
        return decorator

    def ufunction(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

    unreal.SystemLibrary = SystemLibrary
    unreal.AssetRegistryHelpers = AssetRegistryHelpers
    unreal.EditorAssetLibrary = EditorAssetLibrary
    unreal.EditorUtilityLibrary = EditorUtilityLibrary
    unreal.EditorDialog = EditorDialog
    unreal.AnimSequence = AnimSequence
    unreal.Skeleton = FakeSkeleton
    unreal.SkeletalMesh = FakeSkeletalMesh
    unreal.load_asset = _load
    unreal.Paths = FakePaths(state)
    unreal.ToolMenus = type("ToolMenus", (), {
        "get": staticmethod(lambda: FakeToolMenus(state))})
    unreal.ToolMenuEntryScript = FakeToolMenuEntryScript
    unreal.uclass = uclass
    unreal.ufunction = ufunction
    unreal.log = log
    unreal.log_warning = log_warning
    unreal.log_error = log_error
    unreal.AppMsgType = type("AppMsgType", (), {"OK": "OK"})
    unreal.get_editor_subsystem = lambda subsystem: None
    # 故意不提供 EditorDialogLibrary / MLDeformerAsset 等，验证 api_probe 降级。
    return unreal


def _drop_unreal(monkeypatch):
    for name in [item for item in list(sys.modules)
                 if item == "unreal" or item.startswith("unreal.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)


@pytest.fixture
def fake_unreal(monkeypatch):
    _drop_unreal(monkeypatch)
    state = FakeUnrealState()
    monkeypatch.setitem(sys.modules, "unreal", _build_fake_unreal(state))
    yield state


@pytest.fixture
def no_unreal(monkeypatch):
    _drop_unreal(monkeypatch)
    monkeypatch.setitem(sys.modules, "unreal", None)
    yield monkeypatch


@pytest.fixture
def clean_menu_cache():
    from tak.adapters.unreal import menu
    menu._ENTRY_CLASSES.clear()
    yield
    menu._ENTRY_CLASSES.clear()


# --------------------------------------------------------------------------- #
# 延迟导入 / 可用性 / describe
# --------------------------------------------------------------------------- #
def test_headless_import_without_unreal(no_unreal):
    assert is_available() is False
    adapter = create_adapter()
    assert isinstance(adapter, UnrealAdapter)
    assert adapter.dcc is DCC.UNREAL
    assert adapter.name == "unreal"
    assert adapter.available is False
    assert adapter.list_nodes("joint") == []
    assert adapter.list_nodes("anim") == []
    assert adapter.get_node_attrs("root") == {}
    assert adapter.ui_parent() is None
    snapshot = adapter.get_skeleton_snapshot()
    assert set(snapshot) == {"dcc", "version", "unit", "up_axis", "joints",
                            "frame_range", "skin", "metadata"}
    assert snapshot["dcc"] == "unreal"
    assert snapshot["joints"] == {}
    assert snapshot["unit"] == "cm" and snapshot["up_axis"] == "Y"
    assert snapshot["metadata"]["degraded"]


def test_is_available_true_with_fake_host(fake_unreal):
    assert is_available() is True
    assert create_adapter().available is True


def test_describe_reports_engine_version_and_api_probe(fake_unreal):
    adapter = create_adapter()
    info = adapter.describe()
    assert info["dcc"] == "unreal"
    assert info["engine_version"] == ENGINE_VERSION
    assert info["version"] == ENGINE_VERSION
    assert info["available"] is True
    assert info["native_up_axis"] == "Z"
    assert info["api_probe"]["EditorDialog"] is True
    assert info["api_probe"]["EditorDialogLibrary"] is False
    assert info["api_probe"]["MLDeformerAsset"] is False
    for name in ("EditorDialogLibrary", "MLDeformerAsset", "NeuralMorphModel",
                 "DeformerGraphAsset"):
        assert name in info["api_missing"]
    assert any("MLDeformerAsset" in note for note in info["degraded"])
    json.dumps(info)  # describe 必须可 JSON 序列化


def test_describe_without_engine_is_all_false(no_unreal):
    info = create_adapter().describe()
    assert info["available"] is False
    assert info["engine_version"] == ""
    assert info["api_probe"] and not any(info["api_probe"].values())
    assert len(info["api_missing"]) == len(info["api_probe"])
    assert info["degraded"]


def test_supports_declares_import_validate(fake_unreal):
    adapter = create_adapter()
    capabilities = {item for item in Capability if adapter.supports(item)}
    assert capabilities == {
        Capability.SCENE_QUERY, Capability.SKIN_QUERY,
        Capability.SKELETON_SNAPSHOT, Capability.IMPORT_VALIDATE,
    }


def test_runner_adapter_for_unreal(fake_unreal):
    from tak.core.runner import adapter_for
    adapter = adapter_for("unreal")
    assert isinstance(adapter, UnrealAdapter)
    assert adapter.dcc is DCC.UNREAL
    assert adapter.supports(Capability.IMPORT_VALIDATE)


# --------------------------------------------------------------------------- #
# 骨骼 / 资产查询
# --------------------------------------------------------------------------- #
def test_skeleton_snapshot_from_reference_skeleton(fake_unreal):
    snapshot = UnrealAdapter().get_skeleton_snapshot()
    assert snapshot["dcc"] == "unreal"
    assert set(snapshot["joints"]) == {"root", "pelvis", "spine_01", "head"}
    assert snapshot["joints"]["root"]["parent"] is None
    assert snapshot["joints"]["root"]["depth"] == 0
    assert snapshot["joints"]["head"]["parent"] == "spine_01"
    assert snapshot["joints"]["head"]["depth"] == 3
    for joint in snapshot["joints"].values():
        assert len(joint["matrix"]) == 16
        assert len(joint["scale"]) == 3
    # 假引擎默认不给骨骼变换：矩阵占位并显式标记不可比
    assert snapshot["metadata"]["matrix_source"] == "none"
    assert snapshot["metadata"]["native_up_axis"] == "Z"
    assert snapshot["metadata"]["joint_count"] == 4
    assert snapshot["metadata"]["degraded"]
    json.dumps(snapshot)


def test_snapshot_converts_ue_transforms_to_tak_space(fake_unreal):
    fake_unreal.with_transforms = True
    snapshot = UnrealAdapter().get_skeleton_snapshot()
    assert snapshot["metadata"]["matrix_source"] == "reference_skeleton"
    # UE 里 pelvis 在 Z=10，TAK 规范空间应为 Y=10、Z=-0。
    translation = snapshot["joints"]["pelvis"]["matrix"][12:15]
    assert translation == pytest.approx([0.0, 10.0, -0.0])
    assert snapshot["joints"]["head"]["matrix"][13] == pytest.approx(45.0)


def test_list_nodes_anim_returns_asset_paths(fake_unreal):
    adapter = UnrealAdapter()
    assert adapter.list_nodes("anim") == sorted(ANIM_PATHS)
    assert adapter.list_nodes("skeletal_mesh") == [MESH_PATH]
    assert adapter.list_nodes() == ["head", "pelvis", "root", "spine_01"]


def test_get_node_attrs_for_bone(fake_unreal):
    attrs = UnrealAdapter().get_node_attrs("pelvis")
    assert attrs["type"] == "bone"
    assert attrs["parent"] == "root"
    assert attrs["depth"] == 1
    assert len(attrs["matrix"]) == 16


def test_get_node_attrs_for_asset(fake_unreal):
    attrs = UnrealAdapter().get_node_attrs(MESH_PATH)
    assert attrs["type"] == "asset"
    assert attrs["asset_class"] == "SkeletalMesh"
    assert attrs["skeleton"] == SKELETON_PATH
    assert attrs["frame_rate"] == 30.0
    assert attrs["compression"] == "Default"
    assert attrs["root_motion"] is True
    assert attrs["curve_count"] == 3
    assert attrs["material_slots"] == 2
    assert attrs["morph_target_count"] == 1
    assert UnrealAdapter().get_node_attrs("/Game/Nope") == {}


def test_get_skin_summary_from_asset(fake_unreal):
    summary = UnrealAdapter().get_skin_summary(MESH_PATH)
    assert summary["mesh"] == MESH_PATH
    assert summary["deformers"] == ["MorphTarget", "Skin"]
    assert summary["morph_target_count"] == 1
    assert summary["max_influences_found"] == 0
    assert summary["weight_sum_source"] == "unavailable"
    assert summary["source"] == "asset"


def test_get_skin_summary_prefers_engine_summary(fake_unreal):
    fake_unreal.skin_summary = {"max_influences_found": 6,
                                "weight_sum_deviation": 0.002,
                                "deformers": ["skinCluster"],
                                "influences": ["root", "pelvis"],
                                "vertex_count": 128}
    summary = UnrealAdapter().get_skin_summary(MESH_PATH)
    assert summary["max_influences_found"] == 6
    assert summary["weight_sum_deviation"] == pytest.approx(0.002)
    assert summary["deformers"] == ["skinCluster"]
    assert summary["vertex_count"] == 128
    assert summary["source"] == "asset"


def test_get_skin_summary_unknown_mesh_is_zero_shape(fake_unreal):
    summary = UnrealAdapter().get_skin_summary("/Game/Missing")
    assert summary["max_influences_found"] == 0
    assert summary["weight_sum_deviation"] == 0.0
    assert summary["deformers"] == []
    assert summary["source"] == "missing_asset"


# --------------------------------------------------------------------------- #
# 离线数据注入
# --------------------------------------------------------------------------- #
def test_offline_data_injection(no_unreal):
    data = {
        "snapshot": {
            "dcc": "unreal", "version": "5.8", "unit": "cm", "up_axis": "Y",
            "joints": {"root": {"parent": None, "depth": 0, "matrix": [1, 0, 0, 0, 0, 1, 0, 0,
                                                                       0, 0, 1, 0, 0, 0, 0, 1]},
                       "pelvis": {"parent": "root", "depth": 1}},
            "skin": {"max_influences_found": 4, "weight_sum_deviation": 0.0,
                     "deformers": ["Skin"]},
            "metadata": {"source": "unit-test"},
        },
        "anims": list(ANIM_PATHS),
        "attrs": {"pelvis": {"type": "bone", "parent": "root", "depth": 1}},
        "skin": {MESH_PATH: {"max_influences_found": 2, "weight_sum_deviation": 0.0,
                             "deformers": ["Skin"]}},
    }
    adapter = create_adapter(data=data)
    snapshot = adapter.get_skeleton_snapshot()
    assert set(snapshot["joints"]) == {"root", "pelvis"}
    assert snapshot["metadata"]["offline"] is True
    assert snapshot["metadata"]["matrix_source"] == "offline"
    assert adapter.list_nodes("joint") == ["pelvis", "root"]
    assert adapter.list_nodes("anim") == list(ANIM_PATHS)
    assert adapter.get_node_attrs("pelvis")["parent"] == "root"
    assert adapter.get_skin_summary(MESH_PATH)["max_influences_found"] == 2
    assert adapter.describe()["offline"] is True


def test_offline_nodes_without_snapshot(no_unreal):
    adapter = create_adapter(data={"nodes": ["root", "head"]})
    assert adapter.list_nodes() == ["head", "root"]
    assert adapter.get_skeleton_snapshot()["joints"] == {}


# --------------------------------------------------------------------------- #
# import_validate
# --------------------------------------------------------------------------- #
def _write_sidecar(path, snapshot):
    with open(str(path), "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=2)
    return str(path)


def test_import_validate_passes(fake_unreal, tmp_path):
    adapter = UnrealAdapter()
    sidecar = _write_sidecar(tmp_path / "take.fbx.snapshot.json",
                             adapter.get_skeleton_snapshot())
    result = adapter.import_validate(sidecar)
    assert result["passed"] is True
    assert result["diffs"] == []
    assert "校验通过" in result["reason"]


def test_import_validate_accepts_fbx_path(fake_unreal, tmp_path):
    adapter = UnrealAdapter()
    sidecar = _write_sidecar(str(tmp_path / "take.fbx") + ".snapshot.json",
                             adapter.get_skeleton_snapshot())
    result = adapter.import_validate(str(tmp_path / "take.fbx"))
    assert os.path.isfile(sidecar)
    assert result["passed"] is True


def test_import_validate_blocks_on_missing_joint(fake_unreal, tmp_path):
    adapter = UnrealAdapter()
    snapshot = adapter.get_skeleton_snapshot()
    # sidecar 里多一根 UE 侧不存在的骨骼 → joint_missing，必须阻断
    snapshot["joints"]["ghost"] = {"parent": "root", "depth": 1}
    sidecar = _write_sidecar(tmp_path / "bad.snapshot.json", snapshot)
    result = adapter.import_validate(sidecar)
    assert result["passed"] is False
    assert result["diffs"]
    fields = {item["field"] for item in result["diffs"]}
    assert "joint_missing" in fields
    assert "阈值" in result["reason"]


def test_import_validate_skips_matrices_when_unavailable(fake_unreal, tmp_path):
    adapter = UnrealAdapter()
    snapshot = adapter.get_skeleton_snapshot()
    assert snapshot["metadata"]["matrix_source"] == "none"
    snapshot["joints"]["pelvis"]["matrix"][12] = 5.0
    sidecar = _write_sidecar(tmp_path / "matrix.snapshot.json", snapshot)
    result = adapter.import_validate(sidecar)
    assert result["passed"] is True
    assert "bind_matrix" in result["reason"]
    # 显式要求比对矩阵时，偏差仍然会阻断
    strict = adapter.import_validate(
        sidecar, {"ue": {"import_check_bind_matrices": True}})
    assert strict["passed"] is False
    assert any(item["field"] == "bind_matrix" for item in strict["diffs"])


def test_import_validate_reports_matrix_drift(fake_unreal, tmp_path):
    fake_unreal.with_transforms = True
    adapter = UnrealAdapter()
    snapshot = adapter.get_skeleton_snapshot()
    snapshot["joints"]["pelvis"]["matrix"][12] = 5.0
    sidecar = _write_sidecar(tmp_path / "drift.snapshot.json", snapshot)
    result = adapter.import_validate(sidecar)
    assert result["passed"] is False
    assert any(item["field"] == "bind_matrix" for item in result["diffs"])


def test_import_validate_missing_or_broken_sidecar(fake_unreal, tmp_path):
    adapter = UnrealAdapter()
    missing = adapter.import_validate(str(tmp_path / "nope.fbx"))
    assert missing["passed"] is False and missing["diffs"] == []
    assert "找不到 sidecar" in missing["reason"]

    broken = tmp_path / "broken.snapshot.json"
    broken.write_text("{not json", encoding="utf-8")
    result = adapter.import_validate(str(broken))
    assert result["passed"] is False
    assert "无法读取" in result["reason"]

    empty = _write_sidecar(tmp_path / "empty.snapshot.json", {"dcc": "unreal"})
    assert adapter.import_validate(empty)["passed"] is False


# --------------------------------------------------------------------------- #
# 日志 / 菜单 / 启动脚本 / commandlet
# --------------------------------------------------------------------------- #
def test_log_uses_unreal_log(fake_unreal):
    UnrealAdapter().log("hello", "info")
    assert ("log", "[TAK][info] hello") in fake_unreal.messages
    UnrealAdapter().log("careful", "warning")
    assert ("warning", "[TAK][warning] careful") in fake_unreal.messages


def test_log_without_engine_prints(no_unreal, capsys):
    UnrealAdapter().log("fallback", "info")
    assert "fallback" in capsys.readouterr().out


def test_menu_items_are_importable_without_unreal(no_unreal):
    from tak.adapters.unreal import menu
    labels = [item["label"] for item in menu.menu_items()]
    assert labels == ["Run Health Guard", "Open Web Panel", "Validate Import"]
    assert menu.menus_available() is False
    assert menu.register_menus() is False
    assert menu.unregister_menus() is False


def test_menu_registers_all_items(fake_unreal, clean_menu_cache, tmp_path):
    from tak.adapters.unreal import menu
    fake_unreal.saved_dir = str(tmp_path)
    assert menu.register_menus() is True
    assert "TAK" in fake_unreal.menus
    submenu = fake_unreal.menus["TAK"]
    assert {entry.label for entry in submenu.entries} == {
        "Run Health Guard", "Open Web Panel", "Validate Import"}
    assert fake_unreal.unregistered == [menu.MENU_OWNER]
    assert fake_unreal.refreshed == 1
    # 菜单回调真的能跑通（体检 → 报告落盘）
    submenu.entries[0].execute(None)
    assert os.path.isfile(os.path.join(str(tmp_path), "TAK", "health_guard.html"))


def test_menu_validate_import_without_selection(fake_unreal, clean_menu_cache, tmp_path):
    from tak.adapters.unreal import menu
    fake_unreal.saved_dir = str(tmp_path)
    result = menu.validate_import()
    assert result["passed"] is False
    assert fake_unreal.dialogs  # 用 EditorDialog 回显原因


def test_init_unreal_is_import_safe_and_degrades(no_unreal, capsys):
    from tak.adapters.unreal import init_unreal
    assert init_unreal.ensure_repo_on_path() == ROOT
    assert callable(init_unreal.tak_reload)
    info = init_unreal.main()
    assert info["tak_import"] is True
    assert info["unreal"] is False
    assert info["menu_registered"] is False
    assert info["degraded"]
    assert "降级" in capsys.readouterr().out


def test_init_unreal_registers_menu_with_fake_editor(fake_unreal, clean_menu_cache):
    from tak.adapters.unreal import init_unreal
    info = init_unreal.main()
    assert info["unreal"] is True
    assert info["menu_registered"] is True
    assert info["engine_version"] == ENGINE_VERSION


def test_commandlet_offline_run(no_unreal, tmp_path):
    from tak.adapters.unreal import commandlet
    out = tmp_path / "report" / "tak.json"
    result = commandlet.run_headless(snapshot_path=GOOD_FIXTURE, out_path=str(out))
    assert result["source"] == "snapshot"
    assert result["engine_available"] is False
    assert result["errors"] == []
    assert result["joint_count"] == 19
    assert out.is_file()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["dcc"] == "unreal"
    assert payload["passed"] == result["passed"]
    assert "findings" in payload


def test_commandlet_from_adapter_and_cli(no_unreal, tmp_path, capsys):
    from tak.adapters.unreal import commandlet
    out = tmp_path / "tak.json"
    result = commandlet.run_headless(out_path=str(out))
    assert result["source"] == "offline_adapter"
    assert result["joint_count"] == 0
    assert out.is_file()

    argv = ["-snapshot=%s" % GOOD_FIXTURE, "--out", str(tmp_path / "cli.json"),
            "--dcc", "unreal"]
    code = commandlet.main(argv)
    assert code in (0, 1)
    assert (tmp_path / "cli.json").is_file()
    assert "headless ok=" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# 静态约束：Python 3.9 语法 + 无顶层 unreal 导入
# --------------------------------------------------------------------------- #
def _new_sources():
    paths = [os.path.join(dirpath, name)
             for dirpath, _dirnames, filenames in os.walk(
                 os.path.join(ROOT, "tak", "adapters", "unreal"))
             for name in filenames if name.endswith(".py")]
    paths.extend([PROBE_SCRIPT, os.path.abspath(__file__)])
    return sorted(paths)


def test_new_sources_parse_as_python39():
    for path in _new_sources():
        with open(path, "r", encoding="utf-8") as fh:
            ast.parse(fh.read(), filename=path, feature_version=(3, 9))


def test_no_top_level_unreal_import():
    for path in _new_sources():
        with open(path, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        for node in tree.body:  # 只看模块顶层语句
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not any(name.split(".")[0] == "unreal" for name in names), path


def test_probe_script_runs_offline(tmp_path):
    out = tmp_path / "probe.json"
    env = dict(os.environ, TAK_PROBE_OUT=str(out), TAK_REPO_ROOT=ROOT)
    completed = subprocess.run([sys.executable, PROBE_SCRIPT], cwd=ROOT, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=120)
    assert completed.returncode == 0, completed.stdout.decode("utf-8", "replace")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert data["available"] is False
    assert data["engine_version"] is None
    assert data["degraded"]
    assert data["evaluate"]["passed"] in (True, False)
    assert data["adapter_is_unreal"] is True
