# -*- coding: utf-8 -*-
"""MotionBuilder 适配器与宿主入口回归测试（不依赖真实 MoBu）。

用 ``tak.core.mocks.install_mock("mobu", ...)`` 注入假 pyfbsdk，覆盖：
延迟导入、无宿主降级、节点/蒙皮/骨架采集、四端契约（validate / compare /
check_adapter）、离线 data=、四个写方法与状态日志回滚、spec_enforcer DSL
联动（dry-run / confirm）、export_fbx sidecar、menu/init 无宿主安全，以及
新文件的 Python 3.9 语法与顶层零 DCC import。

用例自行安装/卸载 mock：autouse 夹具在首尾各调一次 uninstall_mock()，
保证不污染 sys.modules 与全局注册表；DSL 动作注册到**局部** ActionRegistry，
不向全局 ACTION_REGISTRY / RULE_REGISTRY 写入任何东西。
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys

import pytest

from tak.adapters.base import Capability, DCC
from tak.adapters.conformance import (check_adapter, compare_snapshots,
                                      validate_snapshot)
from tak.adapters.mobu import MobuAdapter, create_adapter, is_available
from tak.core.mocks import install_mock, uninstall_mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "fixtures", "good", "good_001.json")
ACTIONS_PATH = os.path.join(ROOT, "tools", "spec_enforcer", "actions.py")
MOBU_DIR = os.path.join(ROOT, "tak", "adapters", "mobu")

_IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
             0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


@pytest.fixture(autouse=True)
def _isolated():
    """每个用例前后都清空 mock 状态，避免 pyfbsdk 残留污染其它测试。"""
    uninstall_mock()
    yield
    uninstall_mock()


def _fixture_snapshot():
    with open(FIXTURE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _local_action_registry():
    """把真实 spec_enforcer 动作注册到一个**局部** ActionRegistry。

    通过临时交换 ``tak.core.dsl.ACTION_REGISTRY``，让 actions.py 模块级的
    ``register_actions()`` 写入局部注册表；用完立即还原全局，且不把临时模块
    写入 sys.modules。这样既复用了真实 DSL 动作，又零全局污染。
    """
    import tak.core.dsl as dsl_pkg
    from tak.core.dsl.executor import ActionRegistry

    local = ActionRegistry()
    saved = dsl_pkg.ACTION_REGISTRY
    dsl_pkg.ACTION_REGISTRY = local
    try:
        spec = importlib.util.spec_from_file_location(
            "_tak_mobu_actions_probe", ACTIONS_PATH)
        module = importlib.util.module_from_spec(spec)
        # 故意不写 sys.modules：actions.py 只用绝对导入，不需要登记。
        spec.loader.exec_module(module)
    finally:
        dsl_pkg.ACTION_REGISTRY = saved
    return local


# --------------------------------------------------------------------------- #
# 延迟导入 / 可用性 / describe / kwargs
# --------------------------------------------------------------------------- #
def test_headless_import_without_mobu():
    assert is_available() is False
    adapter = create_adapter()
    assert isinstance(adapter, MobuAdapter)
    assert adapter.dcc is DCC.MOBU and adapter.name == "mobu"
    assert adapter.available is False
    assert adapter.list_nodes() == []
    assert adapter.get_node_attrs("root") == {}
    snapshot = adapter.get_skeleton_snapshot()
    assert set(snapshot) == {"dcc", "version", "unit", "up_axis", "joints",
                             "frame_range", "skin", "metadata"}
    assert snapshot["dcc"] == "mobu"
    assert snapshot["joints"] == {}
    assert snapshot["metadata"]["degraded"]
    assert validate_snapshot(snapshot)["ok"] is True


def test_is_available_true_and_adapter_wired():
    handle = install_mock("mobu", FIXTURE)
    assert handle.installed() is True
    assert is_available() is True
    adapter = create_adapter()
    assert adapter.available is True
    assert adapter.dcc is DCC.MOBU
    info = adapter.describe()
    assert info["available"] is True
    assert info["offline"] is False
    assert info["undo_strategy"] == "state_log"
    assert any("PLAYBLAST" in note for note in info["notes"])


def test_create_adapter_forwards_kwargs_and_capabilities():
    adapter = create_adapter(version="2024", python_version="3.10",
                             data={}, ignored=True)
    assert adapter.version == "2024"
    assert adapter.python_version == "3.10"
    caps = {item for item in Capability if adapter.supports(item)}
    assert caps == {
        Capability.SCENE_QUERY, Capability.SKIN_QUERY,
        Capability.SKELETON_SNAPSHOT, Capability.FBX_EXPORT,
        Capability.NODE_WRITE, Capability.WEIGHT_WRITE,
        Capability.UNDO_SCOPE, Capability.UI_HOST,
    }
    assert Capability.PLAYBLAST not in caps
    assert adapter.ui_parent() is None


def test_headless_log_and_ui_are_safe(capsys):
    adapter = create_adapter()
    adapter.log("hello", "info")
    assert "hello" in capsys.readouterr().out
    assert adapter.export_fbx("")["ok"] is False


# --------------------------------------------------------------------------- #
# 采集：与 MockScene 对齐
# --------------------------------------------------------------------------- #
def test_list_nodes_matches_scene():
    handle = install_mock("mobu", FIXTURE)
    scene = handle.scene
    adapter = create_adapter()
    assert adapter.list_nodes("joint") == sorted(scene.node_names("joint"))
    assert adapter.list_nodes("mesh") == sorted(scene.node_names("mesh"))
    assert adapter.list_nodes("unknown") == []


def test_get_node_attrs_matches_scene():
    handle = install_mock("mobu", FIXTURE)
    scene = handle.scene
    adapter = create_adapter()
    attrs = adapter.get_node_attrs("pelvis")
    expected = scene.attrs("pelvis")
    assert set(attrs) == {"node", "type", "parent", "translate", "rotate",
                          "scale", "visibility", "namespace", "frozen"}
    assert attrs["node"] == "pelvis"
    assert attrs["type"] == "joint"
    assert attrs["parent"] == "root"
    assert attrs["translate"] == expected["translation"]
    assert attrs["rotate"] == expected["rotate"]
    assert attrs["scale"] == expected["scale"]
    assert attrs["visibility"] == expected["visibility"]
    assert attrs["namespace"] == expected["namespace"]
    assert attrs["frozen"] is True
    assert adapter.get_node_attrs("does_not_exist") == {}


def test_get_skin_summary_matches_scene():
    handle = install_mock("mobu", FIXTURE)
    scene = handle.scene
    adapter = create_adapter()
    summary = adapter.get_skin_summary("body")
    expected = scene.skin_summary("body")
    assert summary["mesh"] == "body"
    assert summary["max_influences_found"] == expected["max_influences_found"]
    assert summary["weight_sum_deviation"] == expected["weight_sum_deviation"]
    assert summary["deformers"] == sorted(expected["deformers"])
    assert adapter.get_skin_summary("missing")["max_influences_found"] == 0


# --------------------------------------------------------------------------- #
# 四端契约
# --------------------------------------------------------------------------- #
def test_snapshot_passes_conformance_validation():
    handle = install_mock("mobu", FIXTURE)
    adapter = create_adapter()
    snapshot = adapter.get_skeleton_snapshot()
    result = validate_snapshot(snapshot)
    assert result["ok"] is True, result["errors"]
    assert result["dcc"] == "mobu"
    assert result["joint_count"] == len(handle.scene.joints) == 19
    for joint in snapshot["joints"].values():
        assert set(joint) == {"parent", "matrix", "depth", "scale", "rotate",
                              "namespace"}
        assert len(joint["matrix"]) == 16
        assert len(joint["scale"]) == 3
        assert len(joint["rotate"]) == 3
    assert snapshot["metadata"]["joint_count"] == 19
    assert snapshot["metadata"]["mobu_version"]
    assert snapshot["metadata"]["python_version"]
    assert "frame_range_source" in snapshot["metadata"]
    json.dumps(snapshot)  # 必须可 JSON 序列化


def test_mobu_and_maya_snapshots_are_identical():
    install_mock("mobu", FIXTURE)
    mobu_snapshot = create_adapter().get_skeleton_snapshot()
    # 注入同一 fixture 的 maya mock（install_mock 会自动卸掉 mobu 状态）。
    install_mock("maya", FIXTURE)
    from tak.core.runner import adapter_for
    maya_snapshot = adapter_for("maya").get_skeleton_snapshot()
    result = compare_snapshots(mobu_snapshot, maya_snapshot)
    assert result["ok"] is True, result["differences"]
    assert result["joint_count_before"] == result["joint_count_after"] == 19


def test_check_adapter_ok():
    install_mock("mobu", FIXTURE)
    adapter = create_adapter()
    result = check_adapter(adapter)
    assert result["ok"] is True, result
    assert result["joint_count"] == 19
    assert result["capability"]["conformant"] is True


def test_root_filter_keeps_subtree():
    install_mock("mobu", FIXTURE)
    snapshot = create_adapter().get_skeleton_snapshot(root="spine_01")
    assert "root" not in snapshot["joints"]
    assert "spine_01" in snapshot["joints"]
    assert "head" in snapshot["joints"]
    assert snapshot["metadata"]["root"] == "spine_01"


# --------------------------------------------------------------------------- #
# 离线 / 写盘
# --------------------------------------------------------------------------- #
def test_offline_after_uninstall_and_data_injection():
    install_mock("mobu", FIXTURE)
    uninstall_mock()  # 显式卸载，模拟宿主消失
    assert is_available() is False
    empty = create_adapter().get_skeleton_snapshot()
    assert empty["dcc"] == "mobu"
    assert empty["joints"] == {}
    assert empty["metadata"]["degraded"]
    assert validate_snapshot(empty)["ok"] is True

    offline = create_adapter(data=_fixture_snapshot())
    snapshot = offline.get_skeleton_snapshot()
    assert snapshot["dcc"] == "mobu"
    assert len(snapshot["joints"]) == 19
    assert snapshot["metadata"]["offline"] is True
    assert validate_snapshot(snapshot)["ok"] is True
    assert offline.list_nodes() == sorted(_fixture_snapshot()["snapshot"]["joints"])
    assert offline.describe()["offline"] is True


def test_export_fbx_writes_sidecar(tmp_path):
    install_mock("mobu", FIXTURE)
    adapter = create_adapter()
    target = tmp_path / "out" / "take.fbx"
    result = adapter.export_fbx(str(target), {"selection": ["root"]})
    assert result["ok"] is True
    assert result["mock"] is True
    assert result["path"] == str(target)
    assert result["sidecar"].endswith(".snapshot.json")
    assert result["bytes"] > 0
    assert result["selected"] == 1
    assert target.is_file()
    assert b"placeholder" in target.read_bytes()  # 明确标注的占位文件
    with open(result["sidecar"], "r", encoding="utf-8") as fh:
        sidecar = json.load(fh)
    assert validate_snapshot(sidecar)["ok"] is True
    assert len(sidecar["joints"]) == 19
    assert adapter.export_fbx("")["ok"] is False


# --------------------------------------------------------------------------- #
# 写方法 + 状态日志回滚
# --------------------------------------------------------------------------- #
def test_write_methods_log_and_rollback():
    handle = install_mock("mobu", FIXTURE)
    scene = handle.scene
    scene.metadata.pop("write_log", None)
    joints_before = set(scene.joints)
    adapter = create_adapter()

    token = adapter.begin_undo("probe")
    assert token.startswith("mobu:")
    r1 = adapter.rename_node("root", "root_x")
    r2 = adapter.set_control_color("root", 13)
    r3 = adapter.normalize_weights(max_influences=4, tolerance=0.001)
    r4 = adapter.set_namespace("root", "CTRL")
    log = scene.metadata["write_log"]
    assert len(log) == 4
    assert all(r["ok"] is True and r["mock"] is True for r in (r1, r2, r3, r4))
    assert [item["op"] for item in log] == [
        "rename_node", "set_control_color", "normalize_weights", "set_namespace"]
    assert set(scene.joints) == joints_before  # 不破坏 joints 结构

    adapter.rollback(token)
    assert len(scene.metadata["write_log"]) == 0  # 逆序恢复到改前长度

    # commit 不回滚
    token2 = adapter.begin_undo("commit")
    adapter.rename_node("root", "root_y")
    adapter.commit_undo(token2)
    assert len(scene.metadata["write_log"]) == 1


def test_dsl_dry_run_zero_side_effects_confirm_done():
    handle = install_mock("mobu", FIXTURE)
    scene = handle.scene
    scene.metadata.pop("write_log", None)
    adapter = create_adapter()
    registry = _local_action_registry()
    plan = registry.build_plan({"actions": [
        {"tool": "spec_enforcer.set_control_color",
         "params": {"node": "root", "color": 13}},
        {"tool": "spec_enforcer.set_namespace",
         "params": {"node": "root", "namespace": "CTRL"}},
        {"tool": "spec_enforcer.normalize_weights",
         "params": {"max_influences": 4}},
        {"tool": "spec_enforcer.rename_joint",
         "params": {"node": "root", "name": "root_z"}},
    ]})
    # dry-run：零副作用，连 adapter 都不会被调用。
    dry = registry.execute(plan, adapter, dry_run=True)
    assert dry["dry_run"] is True and dry["committed"] is False
    assert all(item["status"] == "pending" for item in dry["changes"])
    assert "write_log" not in scene.metadata
    # confirm：四个动作全部 done，且写日志确实落到 adapter。
    done = registry.execute(plan, adapter, granted=["node.write"],
                            dry_run=False, confirm=True)
    assert done["committed"] is True
    assert all(item["status"] == "done" for item in done["changes"])
    assert len(scene.metadata["write_log"]) == 4


# --------------------------------------------------------------------------- #
# menu / init 无宿主安全
# --------------------------------------------------------------------------- #
def test_menu_and_init_safe_without_host(capsys):
    from tak.adapters.mobu import menu, init_mobu
    assert menu.register_menu()["ok"] is False
    assert menu.unregister_menu()["ok"] is False
    script = menu.startup_script()
    assert isinstance(script, str)
    assert "init_mobu" in script and "sys.path" in script
    info = init_mobu.initialize()
    assert info["ok"] is False and info["reason"]
    assert "MotionBuilder" in capsys.readouterr().out


def test_menu_safe_with_mock_host():
    install_mock("mobu", FIXTURE)  # 假 pyfbsdk 没有 FBMenuManager
    from tak.adapters.mobu import menu
    assert menu.register_menu()["ok"] is False
    assert menu.unregister_menu()["ok"] is False


# --------------------------------------------------------------------------- #
# 静态约束：Python 3.9 语法 + 顶层零 DCC import
# --------------------------------------------------------------------------- #
def test_new_sources_parse_as_python39_and_no_top_level_dcc_import():
    paths = [os.path.join(MOBU_DIR, name) for name in
             ("__init__.py", "adapter.py", "menu.py", "init_mobu.py")]
    paths.append(os.path.abspath(__file__))
    banned = ("pyfbsdk", "maya", "pymxs", "unreal", "PySide")
    for path in paths:
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        ast.parse(source, filename=path, feature_version=(3, 9))
        tree = ast.parse(source, filename=path)
        for node in tree.body:  # 只检查模块顶层语句
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for full in names:
                assert full.split(".")[0] not in banned, (path, full)
