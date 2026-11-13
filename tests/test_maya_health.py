# -*- coding: utf-8 -*-
"""Maya 适配器的 ``metadata.health`` 采集回归测试（不依赖真实 Maya）。

为什么单独一组
--------------
场景卫生 9 条规则只认 ``snapshot["metadata"]["health"]``。这个键采不到时规则
会**安静地**返回 0 条结论，面板上显示「通过」—— 假通过。所以采集逻辑本身必须
有独立断言：采到了什么、刻意不采什么、单类失败是否影响其余类别。

用假 ``cmds`` 直接喂给 ``MayaAdapter._collect_health``：这是纯函数式的一段逻辑，
不碰宿主状态，用真 Maya 验证反而慢且不可在 CI 跑。
"""
from __future__ import annotations

from tak.adapters.maya import MayaAdapter

JOINTS = {"root": {"parent": None}, "hips": {"parent": "root"}}


class FakeCmds:
    """只实现 _collect_health 会用到的那几个 cmds 命令。

    ``nodes`` 是 {节点名: 属性字典}，属性字典支持的键：
    ``parent`` / ``children`` / ``transforms``(translate) / ``rotate`` / ``scale`` /
    ``type`` / ``connections`` / ``referenced`` / ``is_mesh`` / ``shape_parents`` /
    ``override`` / ``reference``。
    """

    def __init__(self, nodes):
        self.nodes = nodes
        self.raise_for = set()

    # -- 通用 ---------------------------------------------------------
    def _check(self, name):
        if name in self.raise_for:
            raise RuntimeError("fake failure on %s" % name)

    def _info(self, node):
        return self.nodes.get(str(node).split("|")[-1]) or {}

    def ls(self, *args, **kwargs):
        node_type = kwargs.get("type")
        if node_type == "transform":
            return [name for name, info in self.nodes.items()
                    if info.get("type", "transform") == "transform"]
        if node_type in ("mesh", "nurbsCurve"):
            return [name for name, info in self.nodes.items()
                    if info.get("type") == node_type]
        if node_type == "reference":
            return [name for name, info in self.nodes.items()
                    if info.get("type") == "reference"]
        return []

    def listRelatives(self, node, **kwargs):  # noqa: N802 - 与 Maya API 同名
        self._check("listRelatives")
        info = self._info(node)
        if kwargs.get("parent"):
            if info.get("shape_parents") is not None:
                return list(info["shape_parents"])
            parent = info.get("parent")
            return [parent] if parent else []
        if kwargs.get("children"):
            return list(info.get("children") or [])
        return []

    def listConnections(self, node, **kwargs):  # noqa: N802
        self._check("listConnections")
        return list(self._info(node).get("connections") or [])

    def referenceQuery(self, node, **kwargs):  # noqa: N802
        self._check("referenceQuery")
        info = self._info(node)
        if kwargs.get("isNodeReferenced"):
            return bool(info.get("referenced"))
        if kwargs.get("filename"):
            return info.get("reference", "") or ""
        if kwargs.get("isLoaded"):
            return bool(info.get("loaded", True))
        return ""

    def getAttr(self, attr):  # noqa: N802
        self._check("getAttr")
        node, _, name = str(attr).partition(".")
        info = self._info(node)
        if not info:
            raise RuntimeError("No object matches name: %s" % node)
        if name in ("translate", "rotate"):
            return list(info.get("translate") if name == "translate"
                        else info.get("rotate") or [0.0, 0.0, 0.0])
        if name == "scale":
            return list(info.get("scale") or [1.0, 1.0, 1.0])
        if name == "overrideEnabled":
            override = info.get("override")
            return bool(override is not None)
        if name == "overrideColor":
            return int(info.get("override") or 0)
        raise RuntimeError("No attribute matches: %s" % attr)


def _adapter():
    return MayaAdapter(version="2026")


def _base_nodes():
    """一套最小场景：根骨 + 子骨 + 一个正经网格。"""
    return {
        "root": {"type": "transform", "children": ["hips"],
                 "translate": [0.0, 0.0, 0.0]},
        "hips": {"type": "transform", "parent": "root"},
        "body_mesh": {"type": "transform", "children": ["bodyShape"],
                      "translate": [0.0, 0.0, 0.0]},
        "bodyShape": {"type": "mesh", "parent": "body_mesh"},
    }


# --------------------------------------------------------------------------
# 孤立节点
# --------------------------------------------------------------------------
def test_orphan_catches_empty_top_level_transform():
    nodes = _base_nodes()
    nodes["junk_orphan_grp"] = {"type": "transform",
                                "translate": [0.0, 0.0, 0.0]}
    health = _adapter()._collect_health(FakeCmds(nodes), JOINTS)
    assert health["orphan_nodes"] == ["junk_orphan_grp"]


def test_orphan_ignores_nodes_that_have_children_connections_or_reference():
    nodes = _base_nodes()
    nodes["gruop_typo"] = {"type": "transform", "children": ["thing"],
                           "translate": [0.0, 0.0, 0.0]}
    nodes["thing"] = {"type": "transform", "parent": "gruop_typo"}
    nodes["bound_ctrl"] = {"type": "transform",
                           "connections": ["skinCluster1.set"],
                           "translate": [0.0, 0.0, 0.0]}
    nodes["ref_root"] = {"type": "transform", "referenced": True,
                         "translate": [0.0, 0.0, 0.0]}
    health = _adapter()._collect_health(FakeCmds(nodes), JOINTS)
    assert health["orphan_nodes"] == []


def test_orphan_ignores_nodes_with_non_default_transform():
    """动过变换的节点不是垃圾节点 —— 真机里这会把镜像组重复报两条。"""
    nodes = _base_nodes()
    nodes["mirror_grp"] = {"type": "transform", "translate": [0.0, 0.0, 0.0],
                           "scale": [-1.0, 1.0, 1.0]}
    health = _adapter()._collect_health(FakeCmds(nodes), JOINTS)
    assert health["orphan_nodes"] == []
    assert health["negative_scale"] == ["mirror_grp"]


def test_orphan_never_reports_joints():
    nodes = _base_nodes()
    nodes["extra_joint"] = {"type": "transform",
                            "translate": [0.0, 0.0, 0.0]}
    health = _adapter()._collect_health(
        FakeCmds(nodes), dict(JOINTS, extra_joint={"parent": None}))
    assert "extra_joint" not in (health.get("orphan_nodes") or [])


# --------------------------------------------------------------------------
# 负缩放
# --------------------------------------------------------------------------
def test_negative_scale_only_reports_topmost_of_a_chain():
    nodes = _base_nodes()
    nodes["mirror_grp"] = {"type": "transform", "scale": [-1.0, 1.0, 1.0],
                           "translate": [0.0, 0.0, 0.0]}
    nodes["mirror_child"] = {"type": "transform", "parent": "mirror_grp",
                             "scale": [-1.0, 1.0, 1.0]}
    health = _adapter()._collect_health(FakeCmds(nodes), JOINTS)
    assert health["negative_scale"] == ["mirror_grp"]


def test_negative_scale_skips_joints_because_rules_read_them_from_snapshot():
    nodes = _base_nodes()
    nodes["hips"]["scale"] = [-1.0, 1.0, 1.0]
    health = _adapter()._collect_health(FakeCmds(nodes), JOINTS)
    assert "hips" not in (health.get("negative_scale") or [])


# --------------------------------------------------------------------------
# 重复 shape
# --------------------------------------------------------------------------
def test_duplicate_shapes_flags_instanced_shape_only():
    nodes = _base_nodes()
    nodes["sharedShape"] = {"type": "mesh",
                            "shape_parents": ["pa", "pb"]}
    nodes["uniqueShape"] = {"type": "mesh", "shape_parents": ["body_mesh"]}
    health = _adapter()._collect_health(FakeCmds(nodes), JOINTS)
    assert health["duplicate_shapes"] == [
        {"shape": "sharedShape", "nodes": ["pa", "pb"]}]


# --------------------------------------------------------------------------
# 引用
# --------------------------------------------------------------------------
def test_references_silent_when_file_exists(tmp_path):
    ref_file = tmp_path / "prop.ma"
    ref_file.write_text("// mayaAscii", encoding="utf-8")
    nodes = _base_nodes()
    nodes["propRN"] = {"type": "reference", "reference": str(ref_file),
                       "loaded": True}
    health = _adapter()._collect_health(FakeCmds(nodes), JOINTS)
    assert health["references"] == []


def test_references_flagged_when_file_missing(tmp_path):
    nodes = _base_nodes()
    nodes["propRN"] = {"type": "reference",
                       "reference": str(tmp_path / "gone.ma"), "loaded": True}
    health = _adapter()._collect_health(FakeCmds(nodes), JOINTS)
    assert health["references"][0]["missing"] is True
    assert health["references"][0]["node"] == "propRN"


def test_references_flagged_when_unloaded(tmp_path):
    ref_file = tmp_path / "prop.ma"
    ref_file.write_text("// mayaAscii", encoding="utf-8")
    nodes = _base_nodes()
    nodes["propRN"] = {"type": "reference", "reference": str(ref_file),
                       "loaded": False}
    health = _adapter()._collect_health(FakeCmds(nodes), JOINTS)
    assert health["references"][0]["loaded"] is False


# --------------------------------------------------------------------------
# 控制器上色
# --------------------------------------------------------------------------
def test_control_colors_only_reads_controller_like_names():
    nodes = _base_nodes()
    nodes["l_arm_ctrl"] = {"type": "transform", "children": ["l_arm_ctrlShape"]}
    nodes["l_arm_ctrlShape"] = {"type": "nurbsCurve", "parent": "l_arm_ctrl"}
    nodes["r_arm_ctrl"] = {"type": "transform", "children": ["r_arm_ctrlShape"],
                           "override": 13}
    nodes["r_arm_ctrlShape"] = {"type": "nurbsCurve", "parent": "r_arm_ctrl"}
    nodes["faceOutline"] = {"type": "transform", "children": ["faceOutlineShape"]}
    nodes["faceOutlineShape"] = {"type": "nurbsCurve",
                                 "parent": "faceOutline"}
    health = _adapter()._collect_health(FakeCmds(nodes), JOINTS)
    colors = health["control_colors"]
    assert colors["l_arm_ctrl"] is None      # 未上色 = None，规则据此报 INFO
    assert colors["r_arm_ctrl"] == 13
    assert "faceOutline" not in colors        # 不像控制器，不参与


def test_control_colors_stops_at_cap():
    nodes = _base_nodes()
    for index in range(120):
        name = "c%03d_ctrl" % index
        nodes[name] = {"type": "transform", "children": [name + "Shape"]}
        nodes[name + "Shape"] = {"type": "nurbsCurve", "parent": name}
    health = _adapter()._collect_health(FakeCmds(nodes), JOINTS)
    assert len(health["control_colors"]) == 80


# --------------------------------------------------------------------------
# 覆盖声明与失败隔离
# --------------------------------------------------------------------------
def test_meta_declares_what_is_not_collected():
    health = _adapter()._collect_health(FakeCmds(_base_nodes()), JOINTS)
    meta = health["_meta"]
    assert "orphan_nodes" in meta["collected"]
    # 这两个键刻意不采：一个会屏蔽规则自身的快照推断，一个属于规范而非场景。
    assert "unfrozen_transforms" in meta["not_provided"]
    assert "max_influences" in meta["not_provided"]


def test_one_broken_collector_does_not_kill_the_others():
    """单类抛异常只记进 errors，其余类别照常返回。

    宿主调用本身都是 ``_get`` 兜底过的（不会抛），所以这里直接让某一类采集器
    在 Python 层炸掉 —— 这正是 ``_run`` 那层 try 要挡的情况。
    """
    adapter = _adapter()

    def boom(cmds):
        raise RuntimeError("collector exploded")

    adapter._health_references = boom
    health = adapter._collect_health(FakeCmds(_base_nodes()), JOINTS)
    assert "references" in health["_meta"]["errors"]
    assert "collector exploded" in health["_meta"]["errors"]["references"]
    # 其余类别照常采到（空列表也算采到：查过了，没东西）
    assert "control_colors" in health["_meta"]["collected"]
    assert "orphan_nodes" in health["_meta"]["collected"]


def test_health_meta_keys_are_not_treated_as_rule_data():
    """``_meta`` 是采集器自己的元信息，不能被规则当成一个节点去报。"""
    from tools.rig_health_guard.core import _health_state

    snapshot = {"metadata": {"health": _adapter()._collect_health(
        FakeCmds(_base_nodes()), JOINTS)}}
    state = _health_state(snapshot)
    assert state["present"] is True
    assert "_meta" not in state["keys"]
    assert "orphan_nodes" in state["keys"]
    assert state["coverage"]["source"] == "tak.adapters.maya"
