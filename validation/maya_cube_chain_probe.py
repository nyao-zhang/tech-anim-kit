# -*- coding: utf-8 -*-
"""Maya 2026 真机验证：一句话 → 10 个随机立方体 + 体积降序父子链 + 一次撤销。

用法::

    mayapy validation/maya_cube_chain_probe.py

输出 ``validation/maya_cube_chain_probe_result.json``。
独立 mayapy 进程、``TAK_HOME`` 指临时目录，**不碰用户当前 Maya 会话**。
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
_TMP = tempfile.mkdtemp(prefix="tak_cube_chain_probe_")
os.environ["TAK_HOME"] = _TMP

RESULT_PATH = os.path.join(ROOT, "validation",
                           "maya_cube_chain_probe_result.json")
result: dict = {"ok": False, "dcc": "maya", "tak_home": _TMP,
                "steps": [], "errors": []}
REQUEST = ("创建十个随机大小在10~100立方厘米的正方块，"
           "以大小体积做父子关系，最大的作为最父级")


def check(name, condition, detail=""):
    result["steps"].append({"name": name, "ok": bool(condition),
                            "value": detail})
    if not condition:
        result["errors"].append(name)
    return bool(condition)


def step(name, fn):
    try:
        value = fn()
        result["steps"].append({"name": name, "ok": True,
                                "value": json.loads(json.dumps(
                                    value, ensure_ascii=False, default=str))})
        return value
    except Exception as exc:
        result["steps"].append({"name": name, "ok": False,
                                "error": "%s: %s" % (type(exc).__name__, exc),
                                "trace": traceback.format_exc()[-600:]})
        result["errors"].append(name)
        return None


def write_result():
    result["ok"] = not result["errors"]
    with io.open(RESULT_PATH, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False, indent=2,
                                default=str))
    print("PROBE_RESULT ok=%s steps=%d errors=%s"
          % (result["ok"], len(result["steps"]), result["errors"]))


def main():
    import maya.standalone
    maya.standalone.initialize(name="python")
    import maya.cmds as cmds

    from tak.adapters import maya as maya_adapter  # 导入即 register_all()
    from tak.core import scene_actions as sa
    from tak.core.ai import commands

    check("adapter_registered",
          sa.REGISTRY.get("scene.create_cube_chain").handler is not
          sa._NOT_IMPLEMENTED)

    parsed = step("natural_language_parse",
                  lambda: commands.parse_local(REQUEST))
    if not parsed or not parsed.get("calls"):
        return
    call = parsed["calls"][0]
    check("parsed_action", call["action"] == "scene.create_cube_chain",
          call["action"])
    params = call["params"]

    # 用同一份参数走完整两阶段：预览必须和真正执行逐字段一致。
    preview = step("dry_run", lambda: sa.REGISTRY.execute(
        "scene.create_cube_chain", params, dry_run=True, dcc="maya"))
    if not preview or not preview.get("ok"):
        return
    plan = preview["preview"]
    check("preview_is_dry_run", preview["status"] == "dry_run")
    check("preview_count_10", plan["count"] == 10, plan["count"])
    check("preview_volume_mode", plan["size_by"] == "volume", plan["size_by"])
    check("preview_link", plan["link"] is True)
    check("preview_spacing_auto", plan["spacing_mode"] == "auto"
          and plan["spacing"] > 0, plan["spacing"])

    before = set(cmds.ls("takCube_*", long=False) or [])
    check("scene_clean_before", not before, sorted(before))

    committed = step("commit", lambda: sa.REGISTRY.execute(
        "scene.create_cube_chain", params, confirm=True, dry_run=False,
        confirm_token=preview["confirm_token"],
        granted=[sa.WRITE_PERMISSION], dcc="maya"))
    if not committed or not committed.get("ok"):
        return
    check("status_committed", committed["status"] == "committed")
    # handler 返回的是「宿主实际建了什么」，不是预览对象本身；逐字段对齐关键结论。
    actual = committed["result"]
    check("result_matches_preview", all([
        actual["chain"] == plan["chain"],
        actual["root"] == plan["root"],
        actual["count"] == plan["count"],
        actual["seed"] == plan["seed"],
        committed["preview"] == plan,
    ]), {"result_keys": sorted(actual), "preview_equal": committed["preview"] == plan})

    cubes = plan["cubes"]
    names = [item["name"] for item in cubes]
    check("all_nodes_exist", all(cmds.objExists(name) for name in names),
          [name for name in names if not cmds.objExists(name)])
    check("all_are_transforms",
          all(cmds.nodeType(name) == "transform" for name in names))

    # 用 polyCube 构造节点的 width 反查真实边长，而不是只信 handler 返回值。
    widths = []
    for name in names:
        history = cmds.listHistory(name) or []
        constructors = [n for n in history if cmds.nodeType(n) == "polyCube"]
        widths.append(float(cmds.getAttr(constructors[0] + ".width"))
                      if constructors else -1.0)
    expected = [item["edge_cm"] for item in cubes]
    check("edge_lengths_match_preview",
          all(abs(a - b) < 1e-4 for a, b in zip(widths, expected)),
          {"actual": widths, "expected": expected})
    volumes = [w ** 3 for w in widths]
    check("volumes_descending",
          all(volumes[i] >= volumes[i + 1] for i in range(len(volumes) - 1)),
          volumes)
    check("volume_range_10_100",
          all(10 - 1e-3 <= v <= 100 + 1e-3 for v in volumes),
          [round(v, 3) for v in volumes])

    parents = []
    for name in names:
        found = cmds.listRelatives(name, parent=True, fullPath=False) or []
        parents.append(found[0] if found else "")
    expected_parents = ["" if i == 0 else names[i - 1]
                        for i in range(len(names))]
    check("parent_chain_by_volume", parents == expected_parents,
          {"actual": parents, "expected": expected_parents})
    check("root_is_world_child", cmds.listRelatives(names[0], parent=True) is None)

    offsets = [float(cmds.getAttr(name + ".translateX")) for name in names[1:]]
    check("spacing_applied",
          all(abs(value - plan["spacing"]) < 1e-4 for value in offsets),
          offsets)

    # 一次撤销应该把整批 10 个都撤掉
    undone = step("undo_last", lambda: sa.REGISTRY.undo_last("maya"))
    check("undo_ok", bool(undone and undone.get("ok")), undone)
    check("all_nodes_gone_after_undo",
          not any(cmds.objExists(name) for name in names),
          [name for name in names if cmds.objExists(name)])

    # 名字冲突必须整体失败，不留下半成品
    cmds.polyCube(name="takCube_01", width=1)
    # 直接调 handler 验证「创建前整体查重」，绕开两阶段 token（那是另一层的事）。
    from tak.adapters.maya import scene_ops
    try:
        scene_ops.cube_chain_create(dict(params))
        rejected = False
        detail = "handler 没有拒绝"
    except ValueError as exc:
        rejected = True
        detail = str(exc)
    check("handler_rejects_name_clash", rejected, detail)
    check("no_partial_creation",
          not any(cmds.objExists(name) for name in names[1:]),
          [name for name in names[1:] if cmds.objExists(name)])
    cmds.delete("takCube_01")

    result["plan_summary"] = {
        "seed": plan["seed"], "root": plan["root"],
        "chain": plan["chain"], "spacing": plan["spacing"],
        "volumes_cm3": [round(item["volume_cm3"], 3) for item in cubes],
        "edges_cm": expected,
    }


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        result["errors"].append("fatal")
        result["fatal"] = "%s: %s\n%s" % (type(exc).__name__, exc,
                                          traceback.format_exc()[-1200:])
    write_result()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if not result["errors"] else 1)
