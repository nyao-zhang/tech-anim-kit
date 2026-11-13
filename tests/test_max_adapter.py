# -*- coding: utf-8 -*-
"""3ds Max 2023+ 适配器与宿主入口回归测试（不依赖真实 3ds Max）。

用 ``tak.core.mocks.install_mock("max", ...)`` 注入假 pymxs，覆盖：
延迟导入 / 无宿主降级、四端契约校验、跨端快照比对、离线快照、四个 DSL
写方法与状态日志 undo、FBX 占位文件与 sidecar、菜单与启动入口的零副作用，
以及所有新增源码的 Python 3.9 语法约束。

每个用例都自行安装 / 卸载 mock（fixture 的 try/finally + monkeypatch），
不注册任何全局 @rule，不污染 RULE_REGISTRY 或 sys.modules。
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from typing import Any, Dict, List

import pytest

from tak.adapters import conformance
from tak.adapters.base import Capability, DCC
from tak.adapters.max import MaxAdapter, create_adapter, is_available
from tak.core.mocks import install_mock, uninstall_mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOOD_FIXTURE = os.path.join(ROOT, "fixtures", "good", "good_001.json")
MAX_DIR = os.path.join(ROOT, "tak", "adapters", "max")
_MISSING = object()

EXPECTED_JOINT_COUNT = 19
CAPABILITIES = {
    Capability.SCENE_QUERY, Capability.SKIN_QUERY,
    Capability.SKELETON_SNAPSHOT, Capability.FBX_EXPORT,
    Capability.NODE_WRITE, Capability.WEIGHT_WRITE,
    Capability.UNDO_SCOPE, Capability.UI_HOST,
}


# --------------------------------------------------------------------------- #
# fixtures：自行安装 / 卸载，隔离 sys.modules
# --------------------------------------------------------------------------- #
def _drop_pymxs(monkeypatch: Any) -> None:
    for name in [item for item in list(sys.modules)
                 if item == "pymxs" or item.startswith("pymxs.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)


@pytest.fixture
def max_env(monkeypatch: Any):
    """注入 max mock，用例结束精确还原 sys.modules。"""
    uninstall_mock()
    _drop_pymxs(monkeypatch)
    handle = install_mock("max", GOOD_FIXTURE)
    try:
        yield handle
    finally:
        uninstall_mock()


@pytest.fixture
def no_max(monkeypatch: Any):
    """确保没有任何 3ds Max 宿主，也不存在残留的 pymxs 模块。"""
    uninstall_mock()
    _drop_pymxs(monkeypatch)
    yield monkeypatch


# --------------------------------------------------------------------------- #
# 延迟导入 / 可用性 / describe
# --------------------------------------------------------------------------- #
def test_headless_import_without_max(no_max):
    assert is_available() is False
    adapter = create_adapter()
    assert isinstance(adapter, MaxAdapter)
    assert adapter.dcc is DCC.MAX and adapter.name == "max"
    assert adapter.available is False
    assert adapter.list_nodes() == []
    assert adapter.get_node_attrs("root") == {}
    snapshot = adapter.get_skeleton_snapshot()
    assert set(snapshot) == {"dcc", "version", "unit", "up_axis", "joints",
                             "frame_range", "skin", "metadata"}
    assert snapshot["dcc"] == "max"
    assert snapshot["joints"] == {}
    assert snapshot["metadata"].get("degraded")
    assert conformance.validate_snapshot(snapshot)["ok"] is True
    assert adapter.ui_parent() is None
    adapter.log("no host", "error")  # 不抛异常即可


def test_is_available_true_with_mock(max_env):
    assert is_available() is True
    adapter = create_adapter()
    assert isinstance(adapter, MaxAdapter)
    assert adapter.available is True
    assert adapter.dcc is DCC.MAX and adapter.name == "max"
    assert adapter.version == "2023"
    assert adapter.describe()["available"] is True
    assert adapter.describe()["offline"] is False
    assert adapter.describe()["undo_strategy"] == "state_log"
    json.dumps(adapter.describe())


def test_create_adapter_forwards_kwargs(no_max):
    adapter = create_adapter(version="2024", python_version="3.9",
                             data={}, ignored=True)
    assert adapter.version == "2024"
    assert adapter.python_version == "3.9"
    assert {item for item in Capability if adapter.supports(item)} == CAPABILITIES


# --------------------------------------------------------------------------- #
# 节点 / 蒙皮查询与 MockScene 一致
# --------------------------------------------------------------------------- #
def test_list_nodes_matches_mock_scene(max_env):
    adapter = create_adapter()
    joints = adapter.list_nodes("joint")
    meshes = adapter.list_nodes("mesh")
    assert joints == sorted(max_env.scene.node_names("joint"))
    assert len(joints) == EXPECTED_JOINT_COUNT
    assert meshes == sorted(max_env.scene.node_names("mesh"))
    assert meshes == ["body"]
    assert joints == sorted(set(joints))  # 排序且去重


def test_get_node_attrs_matches_mock_scene(max_env):
    adapter = create_adapter()
    attrs = adapter.get_node_attrs("pelvis")
    expected = max_env.scene.attrs("pelvis")
    assert attrs["node"] == "pelvis"
    assert attrs["type"] == expected["type"] == "joint"
    assert attrs["parent"] == expected["parent"] == "root"
    assert attrs["translate"] == pytest.approx(expected["translation"])
    assert attrs["scale"] == pytest.approx(expected["scale"])
    assert attrs["rotate"] == pytest.approx(expected["rotate"])
    assert attrs["visibility"] == expected["visibility"]
    assert attrs["namespace"] == expected["namespace"]
    assert isinstance(attrs["frozen"], bool)
    assert set(attrs) == {"node", "type", "parent", "translate", "rotate",
                          "scale", "visibility", "namespace", "frozen"}
    assert adapter.get_node_attrs("not_exist") == {}


def test_get_skin_summary_matches_mock_scene(max_env):
    adapter = create_adapter()
    expected = max_env.scene.skin_summary("body")
    summary = adapter.get_skin_summary("body")
    assert summary["mesh"] == "body"
    assert summary["max_influences_found"] == expected["max_influences_found"] == 4
    assert summary["weight_sum_deviation"] == pytest.approx(
        expected["weight_sum_deviation"])
    assert summary["deformers"] == expected["deformers"] == ["skinCluster"]
    # 不存在的网格退化为可预测的零值结构，且不抛异常
    missing = adapter.get_skin_summary("not_a_mesh")
    assert missing["max_influences_found"] == 0
    assert missing["deformers"] == []


# --------------------------------------------------------------------------- #
# 四端契约
# --------------------------------------------------------------------------- #
def test_snapshot_validates_and_joint_count(max_env):
    adapter = create_adapter()
    snapshot = adapter.get_skeleton_snapshot()
    validation = conformance.validate_snapshot(snapshot)
    assert validation["ok"] is True
    assert validation["dcc"] == "max"
    assert validation["joint_count"] == EXPECTED_JOINT_COUNT
    assert snapshot["unit"] == "cm" and snapshot["up_axis"] == "Y"
    assert snapshot["frame_range"] == [1.0, 120.0]
    assert snapshot["metadata"]["joint_count"] == EXPECTED_JOINT_COUNT
    assert snapshot["metadata"]["source"] == "tak.adapters.max"
    assert "max_version" in snapshot["metadata"]
    assert "python_version" in snapshot["metadata"]
    for joint in snapshot["joints"].values():
        assert len(joint["matrix"]) == 16
        assert len(joint["scale"]) == 3
        assert len(joint["rotate"]) == 3
        assert isinstance(joint["depth"], int)
    json.dumps(snapshot)  # 必须可 JSON 序列化
    # root 子树过滤
    sub = adapter.get_skeleton_snapshot(root="clavicle_L")
    assert "clavicle_L" in sub["joints"]
    assert "root" not in sub["joints"]
    assert sub["metadata"]["root"] == "clavicle_L"
    assert len(sub["joints"]) < EXPECTED_JOINT_COUNT


def test_compare_snapshots_with_maya(max_env):
    max_snapshot = create_adapter().get_skeleton_snapshot()
    install_mock("maya", GOOD_FIXTURE)  # 会自动先卸载 max mock
    try:
        from tak.adapters.maya import create_adapter as maya_factory
        maya_snapshot = maya_factory().get_skeleton_snapshot()
    finally:
        uninstall_mock()
    comparison = conformance.compare_snapshots(max_snapshot, maya_snapshot)
    assert comparison["ok"] is True
    assert comparison["differences"] == []
    assert set(("dcc", "version", "metadata")) <= set(comparison["ignored"])
    assert comparison["joint_count_before"] == EXPECTED_JOINT_COUNT
    assert comparison["joint_count_after"] == EXPECTED_JOINT_COUNT


def test_check_adapter_ok(max_env):
    result = conformance.check_adapter(create_adapter())
    assert result["ok"] is True
    assert result["adapter"] == "max"
    assert result["capability"]["conformant"] is True
    assert result["capability"]["missing_required"] == []
    assert result["joint_count"] == EXPECTED_JOINT_COUNT


# --------------------------------------------------------------------------- #
# 离线 / 降级
# --------------------------------------------------------------------------- #
def test_offline_data_and_empty_after_uninstall(no_max):
    # 无宿主、无 data：合法空结构
    empty = create_adapter().get_skeleton_snapshot()
    assert conformance.validate_snapshot(empty)["ok"] is True
    assert empty["joints"] == {}

    # data= 离线快照字典
    with open(GOOD_FIXTURE, "r", encoding="utf-8") as fh:
        offline = json.load(fh)["snapshot"]
    adapter = create_adapter(data=offline)
    snapshot = adapter.get_skeleton_snapshot()
    assert snapshot["dcc"] == "max"
    assert len(snapshot["joints"]) == EXPECTED_JOINT_COUNT
    assert snapshot["metadata"].get("offline") is True
    assert conformance.validate_snapshot(snapshot)["ok"] is True

    # data= 指向 JSON fixture 文件的路径也可用
    by_path = create_adapter(data=GOOD_FIXTURE)
    assert len(by_path.get_skeleton_snapshot()["joints"]) == EXPECTED_JOINT_COUNT
    assert by_path.describe()["offline"] is True

    # 宿主彻底不可用时写方法也不抛异常
    adapter.rename_node("root", "x")
    adapter.rollback("unknown-token")


# --------------------------------------------------------------------------- #
# 写方法 / 状态日志 undo
# --------------------------------------------------------------------------- #
def test_write_methods_write_log_and_joints_intact(max_env):
    adapter = create_adapter()
    log = max_env.scene.metadata.setdefault("write_log", [])
    joints_before = set(max_env.scene.joints)
    results: List[Dict[str, Any]] = [
        adapter.rename_node("pelvis", "pelvis_fix"),
        adapter.set_control_color("head", 6),
        adapter.normalize_weights(max_influences=4, tolerance=0.001),
        adapter.set_namespace("root", "rig"),
    ]
    for result in results:
        assert result["status"] == "done"
        assert "action" in result and "node" in result
        assert result["mock"] is True
    assert len(log) == 4
    assert {entry["action"] for entry in log} == {
        "rename_node", "set_control_color", "normalize_weights", "set_namespace"}
    assert all(entry["mock"] is True for entry in log)
    # 不得破坏 joints dict 结构
    assert set(max_env.scene.joints) == joints_before
    assert len(max_env.scene.joints) == EXPECTED_JOINT_COUNT


def test_undo_rollback_and_commit(max_env):
    adapter = create_adapter()
    log = max_env.scene.metadata.setdefault("write_log", [])
    base = len(log)

    token = adapter.begin_undo("probe")
    assert token.startswith("max:")
    adapter.rename_node("head", "head_x")
    adapter.set_control_color("root", 3)
    assert len(log) == base + 2
    adapter.rollback(token)
    assert len(log) == base  # 逆序恢复：write_log 回到改前长度
    assert adapter.describe()["undo_strategy"] == "state_log"

    token2 = adapter.begin_undo("commit")
    adapter.rename_node("neck", "neck_y")
    adapter.commit_undo(token2)
    assert len(log) == base + 1  # commit 丢弃记录但保留写入
    adapter.rollback("does-not-exist")  # 未知 token 不抛


# --------------------------------------------------------------------------- #
# DSL 联动（带注册守卫）
# --------------------------------------------------------------------------- #
def test_dsl_actions_drive_max_writes(max_env):
    from tak.core import registry as plugin_registry
    from tak.core.dsl import ACTION_REGISTRY
    from tak.core.runner import find_plugin

    key = "tak_plugin_spec_enforcer_actions"
    prior = sys.modules.get(key, _MISSING)
    try:
        plugin = find_plugin("spec_enforcer")
        plugin_registry.load_plugin_module(plugin, "actions")
        plugin_registry.load_plugin_module(plugin, "actions")  # 重复加载守卫：不抛
        names = set(ACTION_REGISTRY.names)
        assert {
            "spec_enforcer.rename_joint", "spec_enforcer.set_control_color",
            "spec_enforcer.normalize_weights", "spec_enforcer.set_namespace",
        } <= names

        payload = {"actions": [
            {"tool": "spec_enforcer.rename_joint",
             "params": {"node": "pelvis", "name": "pelvis_fix"}},
            {"tool": "spec_enforcer.set_control_color",
             "params": {"node": "head", "color": 6}},
            {"tool": "spec_enforcer.normalize_weights",
             "params": {"max_influences": 4}},
            {"tool": "spec_enforcer.set_namespace",
             "params": {"node": "root", "namespace": "rig"}},
        ]}
        plan = ACTION_REGISTRY.build_plan(payload)
        preview = ACTION_REGISTRY.preview(plan)
        assert len(preview) == 4
        assert all(item["status"] == "pending" for item in preview)

        adapter = create_adapter()
        log = max_env.scene.metadata.setdefault("write_log", [])
        before = len(log)
        dry = ACTION_REGISTRY.execute(plan, adapter, dry_run=True)
        assert dry["dry_run"] is True and dry["committed"] is False
        assert len(log) == before  # dry-run 零副作用

        result = ACTION_REGISTRY.execute(
            plan, adapter, granted=["node.write"], dry_run=False, confirm=True)
        assert result["committed"] is True
        assert all(item["status"] == "done" for item in result["changes"])
        assert len(log) == before + 4
        assert {entry["action"] for entry in log[before:]} == {
            "rename_node", "set_control_color",
            "normalize_weights", "set_namespace"}
    finally:
        if prior is _MISSING:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = prior


# --------------------------------------------------------------------------- #
# FBX 导出
# --------------------------------------------------------------------------- #
def test_export_fbx_writes_sidecar(max_env, tmp_path):
    adapter = create_adapter()
    target = tmp_path / "nested" / "take.fbx"
    result = adapter.export_fbx(str(target), {"selection": ["root", "head"]})
    assert result["ok"] is True
    assert result["path"] == str(target)
    assert result["sidecar"] == str(target) + ".snapshot.json"
    assert result["selected"] == 2
    assert result["mock"] is True
    assert result["error"] == ""
    assert set(("ok", "path", "sidecar", "bytes", "selected",
                "error", "mock")) <= set(result)
    assert target.is_file()
    with open(str(target), "rb") as fh:
        assert b"PLACEHOLDER" in fh.read()  # 明确标注的占位文件
    with open(result["sidecar"], "r", encoding="utf-8") as fh:
        sidecar = json.load(fh)
    assert sidecar["dcc"] == "max"
    assert len(sidecar["joints"]) == EXPECTED_JOINT_COUNT
    assert conformance.validate_snapshot(sidecar)["ok"] is True
    assert result["bytes"] > 0

    bad = adapter.export_fbx("")
    assert bad["ok"] is False and bad["error"]


# --------------------------------------------------------------------------- #
# 菜单 / 启动入口
# --------------------------------------------------------------------------- #
def test_menu_and_init_safe_without_host(no_max, capsys):
    from tak.adapters.max import init_max, menu
    registered = menu.register_menu()
    assert isinstance(registered, dict)
    assert registered["ok"] is False and registered["reason"]
    unregistered = menu.unregister_menu()
    assert unregistered["ok"] is False and unregistered["reason"]
    script = menu.menu_script()
    assert isinstance(script, str) and script
    assert "TAK" in script and "MAXScript" in script and "register_menu" in script
    info = init_max.initialize()
    assert info["dcc"] == "max"
    assert info["menu"]["ok"] is False
    assert "TAK" in info["startup_script"]
    assert "TAK" in capsys.readouterr().out


def test_import_has_no_side_effects():
    """全新进程 import 三个模块：零输出、零异常（import 时零副作用）。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-c",
         "import tak.adapters.max; "
         "import tak.adapters.max.menu; "
         "import tak.adapters.max.init_max"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=60)
    assert completed.returncode == 0, completed.stdout.decode("utf-8", "replace")
    assert completed.stdout.strip() == b""


# --------------------------------------------------------------------------- #
# 静态约束：Python 3.9 语法 + 顶层零 DCC 导入
# --------------------------------------------------------------------------- #
def _new_sources():
    paths = [os.path.join(dirpath, name)
             for dirpath, _dirnames, filenames in os.walk(MAX_DIR)
             for name in filenames if name.endswith(".py")]
    paths.append(os.path.abspath(__file__))
    return sorted(paths)


def test_new_sources_parse_as_python39():
    for path in _new_sources():
        with open(path, "r", encoding="utf-8") as fh:
            ast.parse(fh.read(), filename=path, feature_version=(3, 9))


def test_no_top_level_dcc_import():
    forbidden = ("pymxs", "maya", "pyfbsdk", "unreal",
                 "PySide", "PySide2", "PySide6")
    for path in _new_sources():
        with open(path, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        for node in tree.body:
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert name.split(".")[0] not in forbidden, (path, name)
