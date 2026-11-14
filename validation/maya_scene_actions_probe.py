# -*- coding: utf-8 -*-
"""Maya 真机验证：场景动作 → 桥 → MCP 全链路（mayapy standalone）。

用法::

    mayapy validation/maya_scene_actions_probe.py

输出 ``validation/maya_scene_actions_probe_result.json``。

安全：脚本开头把 ``TAK_HOME`` 指到临时目录，所有 config / bridge.json /
bridge.log / credential 都写在那里，**绝不碰用户真实的 ~/.tak**，也不会
误删用户当前 Maya 会话的桥发现文件。

覆盖范围：18 个动作的宿主实现、两阶段写入 + undo、桥 ``/bridge/act``、
MCP ``tools/list`` + ``tools/call``（含 MCP 真的创建节点）。
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
_TMP = tempfile.mkdtemp(prefix="tak_scene_probe_")
os.environ["TAK_HOME"] = _TMP

RESULT_PATH = os.path.join(ROOT, "validation",
                           "maya_scene_actions_probe_result.json")
result: dict = {"ok": False, "dcc": "maya", "tak_home": _TMP,
                "steps": [], "errors": [], "actions_tested": []}


def step(name, fn):
    try:
        value = fn()
        result["steps"].append({"name": name, "ok": True, "value": value})
        return value
    except Exception as exc:
        result["steps"].append({"name": name, "ok": False,
                                "error": "%s: %s" % (type(exc).__name__, exc),
                                "trace": traceback.format_exc()[-600:]})
        result["errors"].append(name)
        return None


def check(name, condition, detail=""):
    result["steps"].append({"name": name, "ok": bool(condition),
                            "value": detail})
    if not condition:
        result["errors"].append(name)
    return bool(condition)


def write_result():
    result["ok"] = not result["errors"]
    # 证据文件别把 21 个 MCP tool 的完整 schema 全部塞进去（70 KB+）；
    # 超长的 value 折成截断字符串，短的仍保留原始结构便于排查。
    trimmed = []
    for row in result["steps"]:
        item = dict(row)
        text = json.dumps(item.get("value"), ensure_ascii=False, default=str)
        if len(text) > 800:
            item["value"] = text[:800] + " ...(truncated)"
        trimmed.append(item)
    payload = dict(result, steps=trimmed)
    with io.open(RESULT_PATH, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2,
                                default=str))
    print("PROBE_RESULT ok=%s steps=%d errors=%s"
          % (result["ok"], len(result["steps"]), result["errors"]))
    sys.stdout.flush()


def main():
    import maya.standalone
    maya.standalone.initialize(name="python")
    import maya.cmds as cmds

    result["maya_version"] = str(cmds.about(version=True))
    cmds.undoInfo(state=True, infinity=True)  # 让 undo chunk / 撤销可验证

    import tak.adapters.maya  # noqa: F401  注册 18 个宿主 handler
    from tak.core import scene_actions as sa
    from tak.core import bridge, config as tak_config

    result["registered_actions"] = len(sa.REGISTRY.names())
    result["implemented_actions"] = sum(
        1 for name in sa.REGISTRY.names()
        if sa.REGISTRY.get(name).handler is not sa._NOT_IMPLEMENTED)

    # ---------------------------------------------------------------- 造场景
    def build_scene():
        cmds.file(new=True, force=True)
        cmds.select(clear=True)
        cmds.joint(name="root", position=[0, 0, 0])
        cmds.joint(name="spine", position=[0, 2, 0])
        cmds.select(clear=True)
        body = cmds.polyCube(name="body")[0]
        cmds.circle(name="ctrl_root", radius=1.0)
        cmds.select(["root", "spine", body], replace=True)
        cluster = cmds.skinCluster(name="body_skin")[0]
        cmds.select(clear=True)
        return {"body": body, "skinCluster": cluster,
                "joints": cmds.ls(type="joint")}
    step("build_scene", build_scene)

    def read(action, params=None):
        return sa.REGISTRY.execute(action, params or {}, dcc="maya")

    def commit(action, params):
        preview = sa.REGISTRY.execute(action, params, dcc="maya")
        if not preview.get("ok") or preview.get("status") != "dry_run":
            raise RuntimeError("%s 预览失败: %s" % (action, preview))
        return sa.REGISTRY.execute(
            action, params, confirm=True, dry_run=False,
            confirm_token=preview["confirm_token"],
            granted=[sa.WRITE_PERMISSION], dcc="maya")

    # ---------------------------------------------------------------- 只读动作
    summary = step("scene.summary", lambda: read("scene.summary"))
    check("summary.joints>=2", bool(summary and summary.get("result", {})
                                    .get("counts", {}).get("joint", 0) >= 2))
    nodes = step("scene.list_nodes", lambda: read("scene.list_nodes",
                                                  {"type": "joint"}))
    check("list_nodes.finds_root", bool(nodes and "root" in
                                        (nodes.get("result") or {}).get("nodes", [])))
    found = step("scene.find", lambda: read("scene.find", {"query": "ctrl"}))
    check("find.ctrl_root", bool(found and "ctrl_root" in
                                 (found.get("result") or {}).get("nodes", [])))
    node = step("scene.get_node", lambda: read("scene.get_node", {"node": "root"}))
    check("get_node.type", bool(node and (node.get("result") or {}).get("type") == "joint"))
    attr = step("scene.get_attr", lambda: read("scene.get_attr",
                                               {"node": "body", "attribute": "tx"}))
    check("get_attr.value", bool(attr and (attr.get("result") or {}).get("value") == 0))
    frame = step("scene.frame_range", lambda: read("scene.frame_range"))
    check("frame_range", bool(frame and (frame.get("result") or {})
                              .get("playback_end", 0) > 0))
    sel = step("selection.get", lambda: read("selection.get"))
    check("selection.get", bool(sel and sel.get("ok")))
    hier = step("rig.hierarchy", lambda: read("rig.hierarchy", {"root": "root"}))
    check("hierarchy.two_joints", bool(hier and (hier.get("result") or {})
                                       .get("joint_count") == 2))
    skin = step("rig.skin_info", lambda: read("rig.skin_info", {"mesh": "body"}))
    check("skin_info.skinned", bool(skin and (skin.get("result") or {})
                                    .get("skinned") is True))
    infl = step("rig.influences", lambda: read("rig.influences", {"joint": "root"}))
    check("influences.mesh", bool(infl and (infl.get("result") or {})
                                  .get("mesh_count", 0) >= 1))
    conn = step("rig.connections", lambda: read("rig.connections",
                                                {"node": "body"}))
    check("connections.ok", bool(conn and conn.get("ok")))
    result["actions_tested"] = [
        "scene.summary", "scene.list_nodes", "scene.find", "scene.get_node",
        "scene.get_attr", "scene.frame_range", "selection.get",
        "rig.hierarchy", "rig.skin_info", "rig.influences", "rig.connections"]

    # ---------------------------------------------------------------- 写动作
    for kind, name in (("locator", "probe_loc"), ("joint", "probe_joint"),
                       ("group", "probe_grp"), ("cube", "probe_cube"),
                       ("control", "probe_ctrl")):
        made = step("create.%s" % kind,
                    lambda k=kind, n=name: commit("node.create", {
                        "kind": k, "name": n, "position": [1, 2, 3]}))
        if made:
            result["actions_tested"].append("node.create")
        check("create.%s.exists" % kind, bool(cmds.objExists(name)),
              str((made or {}).get("error", "")))
    check("create.position", all(abs(a - b) < 1e-4 for a, b in zip(
        cmds.xform("probe_loc", q=True, ws=True, t=True), [1, 2, 3])))

    parented = step("node.parent", lambda: commit("node.parent", {
        "node": "probe_loc", "parent": "probe_grp"}))
    check("parent.attached", bool(parented and cmds.listRelatives(
        "probe_loc", parent=True) and cmds.listRelatives(
        "probe_loc", parent=True)[0].startswith("probe_grp")))
    result["actions_tested"].append("node.parent")

    # body.tx 被 skinCluster 连接锁定（真机常见）；TAK 应当明确拒绝而不是擅自解锁。
    locked = step("attr.set.locked_refused", lambda: commit("attr.set", {
        "node": "body", "attribute": "translateX", "value": "2.5"}))
    check("attr.set.locked_refused", bool(locked and not locked.get("ok")
                                          and "锁定" in str(locked.get("error"))))
    attr_set = step("attr.set", lambda: commit("attr.set", {
        "node": "probe_loc", "attribute": "translateY", "value": "2.5"}))
    check("attr.set.applied", bool(attr_set and attr_set.get("ok") and abs(
        cmds.getAttr("probe_loc.ty") - 2.5) < 1e-5),
        str((attr_set or {}).get("error", ""))[:180])
    result["actions_tested"].append("attr.set")

    renamed = step("node.rename", lambda: commit("node.rename", {
        "node": "probe_cube", "name": "probe_cube_new"}))
    check("rename.applied", bool(renamed and cmds.objExists("probe_cube_new")
                                 and not cmds.objExists("probe_cube")))
    result["actions_tested"].append("node.rename")

    selected = step("selection.set", lambda: commit("selection.set", {
        "nodes": ["root", "spine"]}))
    check("selection.set.applied", bool(selected and set(
        cmds.ls(sl=True)) >= {"root", "spine"}))
    result["actions_tested"].append("selection.set")

    deleted = step("node.delete", lambda: commit("node.delete", {
        "node": "probe_joint"}))
    check("delete.applied", bool(deleted and not cmds.objExists("probe_joint")))
    result["actions_tested"].append("node.delete")

    undone = step("scene.undo_tak", lambda: commit("scene.undo_tak", {}))
    check("undo.restored", bool(undone and undone.get("ok")
                                and cmds.objExists("probe_joint")),
          str((undone or {}).get("error", ""))[:160])
    result["actions_tested"].append("scene.undo_tak")

    # ------------------------------------------------- 桥 + MCP（真 Maya 数据）
    # standalone 没有 Qt 事件循环，executeInMainThreadWithResult 的 idle 队列
    # 不会推进；这里显式注销宿主执行器，让桥在同一线程直调（仍然是 cmds 安全路径）。
    bridge.register_executor("maya", None,
                             note="mayapy standalone 探针：同线程直调")
    cfg = tak_config.load()
    cfg["bridge"]["token"] = "probe-token"
    tak_config.save(cfg)

    server = bridge.BridgeServer(dcc="maya", token="probe-token")
    started = step("bridge.start", lambda: server.start())
    bridge._SERVER = server  # 让 bridge_status() / call() 看到这个进程内的桥
    check("bridge.running", bool(started and started.get("ok")))

    actions = step("bridge.actions", lambda: bridge.call("/bridge/actions"))
    check("bridge.actions.count>=18", bool(actions and actions.get("count", 0) >= 18))

    from tak.mcp import handle as mcp_handle
    listing = step("mcp.tools/list", lambda: mcp_handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}))
    check("mcp.tools>=20", bool(listing and len(
        listing["result"]["tools"]) >= 20))

    reply = step("mcp.tools/call scene.summary", lambda: mcp_handle({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "tak_scene_summary", "arguments": {}}}))
    payload = {}
    if reply and not reply["result"].get("isError"):
        payload = json.loads(reply["result"]["content"][0]["text"])
    check("mcp.read.real_scene", payload.get("ok") is True and
          (payload.get("result") or {}).get("counts", {}).get("joint", 0) >= 2,
          json.dumps(payload, ensure_ascii=False)[:200])

    # MCP 写场景：第一次 dry-run，打开写开关后带 token 第二次调用
    create_args = {"kind": "locator", "name": "mcp_created_loc"}
    first = step("mcp.tools/call dry_run", lambda: mcp_handle({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "tak_node_create", "arguments": dict(create_args)}}))
    preview_payload = {}
    if first:
        preview_payload = json.loads(first["result"]["content"][0]["text"])
    check("mcp.dry_run", preview_payload.get("status") == "dry_run")

    blocked = step("mcp.commit_blocked", lambda: mcp_handle({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "tak_node_create",
                   "arguments": dict(create_args, confirm=True,
                                     confirm_token=preview_payload.get("confirm_token"))}}))
    blocked_payload = json.loads(blocked["result"]["content"][0]["text"]) if blocked else {}
    check("mcp.write_switch_blocks", bool(blocked_payload.get("error") and
                                          "外部写入未启用" in blocked_payload["error"]))

    bridge.set_allow_write(True)
    final = step("mcp.commit", lambda: mcp_handle({
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "tak_node_create",
                   "arguments": dict(create_args, confirm=True,
                                     confirm_token=preview_payload.get("confirm_token"))}}))
    final_payload = json.loads(final["result"]["content"][0]["text"]) if final else {}
    check("mcp.created_node", bool(final_payload.get("committed") and
                                   cmds.objExists("mcp_created_loc")),
          json.dumps(final_payload, ensure_ascii=False)[:220])
    bridge.set_allow_write(False)

    step("bridge.stop", lambda: server.stop())
    bridge._SERVER = None

    result["actions_tested"] = sorted(set(result["actions_tested"]))
    result["unique_actions_tested"] = len(result["actions_tested"])
    write_result()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        result["errors"].append("fatal")
        result["fatal"] = "%s: %s\n%s" % (type(exc).__name__, exc,
                                          traceback.format_exc()[-1200:])
        write_result()
    # standalone 的退出流程在本机装了较多第三方插件时会卡住；结果已落盘，
    # 直接退出，不留僵尸进程。
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if not result["errors"] else 1)
