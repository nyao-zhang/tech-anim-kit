# -*- coding: utf-8 -*-
"""Maya 2023+ 的**真实场景读写动作**：``tak.core.scene_actions`` 的宿主实现。

设计约束
--------
* 顶层不 import maya；每个 handler 内部延迟取 ``maya.cmds``，所以纯 Python / CI
  / Web 工作台里 import 本模块是安全的（只是调用时会明确报「宿主不可用」）。
* 只读动作绝不修改场景，不打开 undo chunk，不改选择集。
* 写动作一次只做一件参数化的事；执行体由 core 的执行器统一包在 undo chunk 里，
  异常时按 undo 队列长度差回滚，不误撤用户自己的操作。
* 所有返回值都是可 JSON 序列化的纯数据，方便桥 / MCP / UI 直接转发。

注册时机：``tak.adapters.maya.__init__`` 在导入时调用 :func:`register_all()`。
"""
from __future__ import annotations

import importlib
import re
from typing import Any, Dict, List, Optional

from tak.core import scene_actions as sa

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_NODES = 256
_MAX_VERTEX_SAMPLE = 48
#: 我们自己的 undo chunk 台账。Maya 2026 的 ``undoInfo(q=True, chunkName=True)``
#: 实测返回空串，不能靠它判断「最近一次操作是不是 TAK」；改用 undo 队列长度
#: 快照：只有队列长度与 TAK 写入结束时完全一致（说明之后没人动过场景）才撤。
_LAST_CHUNK: Dict[str, Any] = {"label": "", "after": -1, "used": True}


def _c() -> Any:
    """取 maya.cmds；不在 Maya 里时给出能读懂的错误。"""
    try:
        return importlib.import_module("maya.cmds")
    except Exception as exc:
        raise RuntimeError(
            "当前进程不是 Maya（无法导入 maya.cmds: %s）。场景动作只能在 Maya "
            "主线程里执行；外部调用请走 DCC 面板启动的本地桥。" % type(exc).__name__
        ) from exc


def _check_name(name: str) -> str:
    value = str(name or "").strip()
    if not _NAME_RE.match(value):
        raise ValueError("节点名 %r 不合法：只允许字母/数字/下划线，且不能以数字开头"
                         % value)
    return value


def _exists(node: str) -> str:
    c = _c()
    if not c.objExists(node):
        raise ValueError("场景里找不到节点：%s" % node)
    return node


def _ls(c: Any, pattern: str = "", node_type: str = "", limit: int = 50,
        **kwargs: Any) -> List[str]:
    args: List[Any] = []
    if pattern:
        # 只允许 Maya 通配符与普通名字字符，杜绝把任意字符串喂给 ls。
        cleaned = re.sub(r"[^A-Za-z0-9_:*?\[\].|]", "", str(pattern))[:128]
        if cleaned:
            args.append(cleaned)
    if node_type:
        kwargs["type"] = str(node_type)
    try:
        found = c.ls(*args, **kwargs) or []
    except Exception:
        found = []
    result = [str(item) for item in found]
    return result[:max(1, int(limit or 50))]


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# 查询类
# --------------------------------------------------------------------------- #
def scene_summary(params: Dict[str, Any]) -> Dict[str, Any]:
    c = _c()
    def count(node_type: str) -> int:
        try:
            return len(c.ls(type=node_type) or [])
        except Exception:
            return 0
    try:
        scene = str(c.file(q=True, sn=True) or "")
    except Exception:
        scene = ""
    try:
        start = _num(c.playbackOptions(q=True, minTime=True), 1.0)
        end = _num(c.playbackOptions(q=True, maxTime=True), 120.0)
        current = _num(c.currentTime(q=True), 1.0)
    except Exception:
        start, end, current = 1.0, 120.0, 1.0
    try:
        unit = str(c.currentUnit(q=True, linear=True) or "")
        time_unit = str(c.currentUnit(q=True, time=True) or "")
    except Exception:
        unit, time_unit = "", ""
    # 每个子项独立兜底：经桥调用时请求在 HTTP 线程上，Maya 的 cmds 参数
    # 解析偶发「必须为标志 sl 传递一个布尔参数」。不能让一个选择集查询
    # 把整份场景摘要拖成 500 —— 查询类动作要尽量返回能拿到的部分。
    try:
        selection = [str(item) for item in (c.ls(sl=True, long=True) or [])][:40]
        selection_error = ""
    except Exception as exc:
        selection, selection_error = [], "%s: %s" % (type(exc).__name__, exc)
    try:
        modified = bool(c.file(q=True, modified=True))
    except Exception:
        modified = False
    return {
        "scene": scene,
        "scene_name": scene.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
        "dcc": "maya",
        "frame_range": [start, end],
        "current_frame": current,
        "linear_unit": unit,
        "time_unit": time_unit,
        "counts": {
            "transform": count("transform"),
            "joint": count("joint"),
            "mesh": count("mesh"),
            "skinCluster": count("skinCluster"),
            "nurbsCurve": count("nurbsCurve"),
        },
        "selection": selection,
        "selection_error": selection_error,
        "modified": modified,
    }


def scene_list_nodes(params: Dict[str, Any]) -> Dict[str, Any]:
    c = _c()
    limit = int(params.get("limit") or 50)
    found = _ls(c, str(params.get("pattern") or ""),
                str(params.get("type") or ""), limit=limit)
    return {"count": len(found), "limit": limit, "nodes": found,
            "truncated": len(found) >= limit}


def scene_find(params: Dict[str, Any]) -> Dict[str, Any]:
    c = _c()
    query = re.sub(r"[^A-Za-z0-9_:*]", "", str(params.get("query") or ""))[:128]
    limit = int(params.get("limit") or 50)
    if not query:
        raise ValueError("查询关键字不能为空")
    found = _ls(c, "*%s*" % query, str(params.get("type") or ""), limit=limit)
    return {"query": query, "count": len(found), "nodes": found,
            "truncated": len(found) >= limit}


def scene_get_node(params: Dict[str, Any]) -> Dict[str, Any]:
    c = _c()
    node = _exists(str(params.get("node") or ""))
    info: Dict[str, Any] = {
        "node": node,
        "type": str(c.nodeType(node) or ""),
        "full_path": (c.ls(node, long=True) or [node])[0],
    }
    try:
        info["parent"] = (c.listRelatives(node, parent=True, fullPath=True) or [None])[0]
    except Exception:
        info["parent"] = None
    try:
        info["children"] = [str(item) for item in
                            (c.listRelatives(node, children=True, fullPath=True) or [])][:40]
    except Exception:
        info["children"] = []
    for attr in ("translate", "rotate", "scale", "visibility"):
        plug = "%s.%s" % (node, attr)
        try:
            if c.objExists(plug):
                value = c.getAttr(plug)
                info[attr] = list(value[0]) if isinstance(value, tuple) else value
        except Exception:
            pass
    try:
        info["user_attributes"] = [str(item) for item in
                                   (c.listAttr(node, userDefined=True) or [])][:40]
    except Exception:
        info["user_attributes"] = []
    requested = params.get("attributes") or []
    extra: Dict[str, Any] = {}
    for attr in list(requested)[:20]:
        key = re.sub(r"[^A-Za-z0-9_.\[\]]", "", str(attr))
        plug = "%s.%s" % (node, key)
        try:
            extra[key] = c.getAttr(plug) if c.objExists(plug) else None
        except Exception:
            extra[key] = None
    info["extra"] = extra
    return info


def scene_get_attr(params: Dict[str, Any]) -> Dict[str, Any]:
    c = _c()
    node = _exists(str(params.get("node") or ""))
    attr = re.sub(r"[^A-Za-z0-9_.\[\]]", "", str(params.get("attribute") or ""))
    plug = "%s.%s" % (node, attr)
    if not c.objExists(plug):
        raise ValueError("属性不存在：%s" % plug)
    value = c.getAttr(plug)
    return {"node": node, "attribute": attr, "value": value,
            "type": str(c.getAttr(plug, type=True) or ""),
            "locked": bool(c.getAttr(plug, lock=True))}


def scene_frame_range(params: Dict[str, Any]) -> Dict[str, Any]:
    c = _c()
    return {
        "playback_start": _num(c.playbackOptions(q=True, minTime=True), 1.0),
        "playback_end": _num(c.playbackOptions(q=True, maxTime=True), 120.0),
        "animation_start": _num(c.playbackOptions(q=True, animationStartTime=True), 1.0),
        "animation_end": _num(c.playbackOptions(q=True, animationEndTime=True), 120.0),
        "time_unit": str(c.currentUnit(q=True, time=True) or ""),
        "fps": {"film": 24, "ntsc": 30, "pal": 25, "ntscf": 60}.get(
            str(c.currentUnit(q=True, time=True) or ""), None),
    }


def selection_get(params: Dict[str, Any]) -> Dict[str, Any]:
    c = _c()
    try:
        nodes = [str(item) for item in (c.ls(sl=True, long=True) or [])]
    except Exception as exc:
        raise RuntimeError("读取选择集失败（%s: %s）；可改用 scene.list_nodes 查节点"
                           % (type(exc).__name__, exc)) from exc
    return {"count": len(nodes), "nodes": nodes[:_MAX_NODES]}


# --------------------------------------------------------------------------- #
# 绑定逻辑查询
# --------------------------------------------------------------------------- #
def rig_hierarchy(params: Dict[str, Any]) -> Dict[str, Any]:
    c = _c()
    root = _exists(str(params.get("root") or ""))
    max_depth = int(params.get("max_depth") or 6)
    rows: List[Dict[str, Any]] = []
    queue: List[tuple] = [(root, 0)]
    while queue and len(rows) < _MAX_NODES:
        node, depth = queue.pop(0)
        try:
            node_type = str(c.nodeType(node) or "")
        except Exception:
            continue
        children: List[str] = []
        if depth < max_depth:
            try:
                children = [str(item) for item in
                            (c.listRelatives(node, children=True,
                                             type="joint", fullPath=True) or [])]
            except Exception:
                children = []
        rows.append({"name": node, "depth": depth, "type": node_type,
                     "child_count": len(children)})
        queue.extend((child, depth + 1) for child in children)
    return {"root": root, "joint_count": len(rows), "truncated": bool(queue),
            "joints": rows}


def _skin_cluster_for(c: Any, mesh: str) -> List[str]:
    shapes: List[str] = []
    try:
        shapes = [str(item) for item in
                  (c.listRelatives(mesh, shapes=True, noIntermediate=True,
                                   fullPath=True) or [])]
    except Exception:
        shapes = []
    targets = shapes or [mesh]
    clusters: List[str] = []
    for target in targets:
        try:
            history = c.listHistory(target, pruneDagObjects=True) or []
            clusters.extend(str(item) for item in (c.ls(history, type="skinCluster") or []))
        except Exception:
            continue
    return list(dict.fromkeys(clusters))


def rig_skin_info(params: Dict[str, Any]) -> Dict[str, Any]:
    c = _c()
    mesh = _exists(str(params.get("mesh") or ""))
    limit = int(params.get("limit") or 40)
    clusters = _skin_cluster_for(c, mesh)
    if not clusters:
        return {"mesh": mesh, "skinned": False,
                "hint": "该网格没有 skinCluster；确认传入的是网格或其 transform。"}
    out: List[Dict[str, Any]] = []
    for cluster in clusters[:4]:
        try:
            influences = [str(item) for item in
                          (c.skinCluster(cluster, q=True, influence=True) or [])]
        except Exception:
            influences = []
        try:
            max_influences = int(c.getAttr("%s.maxInfluences" % cluster) or 4)
        except Exception:
            max_influences = 0
        # 抽样顶点看权重和；只做上限内的小样本，避免重骨架卡住主线程。
        worst = 0.0
        sampled = 0
        try:
            vertex_count = int(c.polyEvaluate(mesh, vertex=True) or 0)
            step = max(1, vertex_count // _MAX_VERTEX_SAMPLE)
            for index in range(0, min(vertex_count, _MAX_VERTEX_SAMPLE * step), step):
                values = c.skinPercent(cluster, "%s.vtx[%d]" % (mesh, index),
                                       q=True, value=True) or []
                total = sum(float(item) for item in values)
                worst = max(worst, abs(total - 1.0))
                sampled += 1
        except Exception:
            pass
        out.append({
            "skinCluster": cluster,
            "influence_count": len(influences),
            "influences": influences[:limit],
            "influences_truncated": len(influences) > limit,
            "max_influences": max_influences,
            "sampled_vertices": sampled,
            "max_weight_sum_deviation": round(worst, 6),
        })
    return {"mesh": mesh, "skinned": True, "clusters": out}


def rig_influences(params: Dict[str, Any]) -> Dict[str, Any]:
    c = _c()
    joint = _exists(str(params.get("joint") or ""))
    short = joint.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
    limit = int(params.get("limit") or 50)
    hits: List[Dict[str, Any]] = []
    for cluster in (c.ls(type="skinCluster") or [])[:_MAX_NODES]:
        try:
            influences = [str(item) for item in
                          (c.skinCluster(cluster, q=True, influence=True) or [])]
            if not any(item == joint or item.endswith("|" + short) or
                       item.rsplit(":", 1)[-1] == short for item in influences):
                continue
            geometry = [str(item) for item in
                        (c.skinCluster(cluster, q=True, geometry=True) or [])]
            hits.append({"skinCluster": str(cluster), "geometry": geometry[:10]})
        except Exception:
            continue
        if len(hits) >= limit:
            break
    return {"joint": joint, "mesh_count": len(hits), "skins": hits}


def rig_connections(params: Dict[str, Any]) -> Dict[str, Any]:
    c = _c()
    node = _exists(str(params.get("node") or ""))
    direction = str(params.get("direction") or "both")
    try:
        pairs = c.listConnections(node, connections=True, plugs=True,
                                  source=direction in ("in", "both"),
                                  destination=direction in ("out", "both")) or []
    except Exception:
        pairs = []
    rows: List[Dict[str, Any]] = []
    for index in range(0, len(pairs) - 1, 2):
        left, right = str(pairs[index]), str(pairs[index + 1])
        other = right if left.startswith(node) else left
        rows.append({"plug": left if left.startswith(node) else right,
                     "other": other})
        if len(rows) >= 80:
            break
    return {"node": node, "direction": direction, "count": len(rows),
            "connections": rows}


# --------------------------------------------------------------------------- #
# 写入类
# --------------------------------------------------------------------------- #
def node_create(params: Dict[str, Any]) -> Dict[str, Any]:
    c = _c()
    kind = str(params.get("kind") or "")
    name = _check_name(str(params.get("name") or ""))
    if c.objExists(name):
        raise ValueError("节点 %s 已存在，换一个名字或用 node.rename" % name)
    parent = str(params.get("parent") or "").strip()
    if parent:
        _exists(parent)
    position = params.get("position") or [0.0, 0.0, 0.0]
    if len(position) != 3:
        raise ValueError("position 必须是 [x, y, z] 三个数")
    xyz = [_num(item) for item in position]

    if kind == "group":
        created = str(c.group(empty=True, name=name))
    elif kind == "locator":
        created = str(c.spaceLocator(name=name)[0])
    elif kind == "joint":
        c.select(clear=True)
        created = str(c.joint(name=name, position=xyz))
    elif kind == "control":
        created = str(c.circle(name=name, radius=1.0, normal=(0, 1, 0))[0])
    elif kind == "cube":
        created = str(c.polyCube(name=name, width=1.0, height=1.0, depth=1.0)[0])
    else:
        raise ValueError("未知创建类型：%s" % kind)

    if parent:
        created = str(c.parent(created, parent)[0])
    if kind != "joint":
        try:
            c.xform(created, worldSpace=True, translation=xyz)
        except Exception:
            pass
    return {"created": created, "kind": kind, "name": name,
            "type": str(c.nodeType(created)), "parent": parent,
            "position": xyz,
            "full_path": (c.ls(created, long=True) or [created])[0]}


def cube_chain_create(params: Dict[str, Any]) -> Dict[str, Any]:
    """一次建 N 个 polyCube + 体积降序父子链。

    尺寸、名字、层级**完全复用** core 的 :func:`cube_chain_plan`，不在宿主里
    重新随机 —— 否则 dry-run 给人看的 diff 和实际建出来的东西会对不上。
    整体查重放在创建之前，任何一个名字被占用就整体失败（由 core 的 undo
    chunk 保证不留半成品）。
    """
    c = _c()
    plan = sa.cube_chain_plan(params)
    cubes = plan["cubes"]
    for item in cubes:
        name = _check_name(str(item["name"]))
        if c.objExists(name):
            raise ValueError("节点 %s 已存在，换一个 prefix 再试；本次不创建任何节点"
                             % name)

    created: List[str] = []
    for item in cubes:
        edge = float(item["edge_cm"])
        transform = str(c.polyCube(name=item["name"], width=edge,
                                   height=edge, depth=edge)[0])
        # Maya 在重名或命名空间情况下会自动加后缀；统一改回预览里的名字。
        if transform != item["name"]:
            transform = str(c.rename(transform, item["name"]))
        created.append(transform)

    if plan["link"]:
        for index in range(1, len(cubes)):
            child = str(cubes[index]["name"])
            parent = str(cubes[index - 1]["name"])
            c.parent(child, parent)
            if plan["spacing"] > 0:
                c.setAttr(child + ".translateX", float(plan["spacing"]))
    elif plan["spacing"] > 0:
        for index, item in enumerate(cubes):
            c.setAttr(str(item["name"]) + ".translateX",
                      float(plan["spacing"]) * index)

    return {
        "created": created,
        "root": plan["root"],
        "chain": plan["chain"] if plan["link"] else [],
        "count": len(created),
        "size_by": plan["size_by"],
        "seed": plan["seed"],
        "unit": plan["unit"],
        "parent_of": {str(item["name"]): (item["parent"] or "<world>")
                      for item in cubes},
    }


def node_rename(params: Dict[str, Any]) -> Dict[str, Any]:
    c = _c()
    node = _exists(str(params.get("node") or ""))
    name = _check_name(str(params.get("name") or ""))
    if c.objExists(name) and name != node:
        raise ValueError("目标名字 %s 已被占用" % name)
    new_name = str(c.rename(node, name))
    return {"before": node, "after": new_name}


def node_delete(params: Dict[str, Any]) -> Dict[str, Any]:
    c = _c()
    node = _exists(str(params.get("node") or ""))
    node_type = str(c.nodeType(node))
    c.delete(node)
    return {"deleted": node, "type": node_type, "exists_after": bool(c.objExists(node))}


def node_parent(params: Dict[str, Any]) -> Dict[str, Any]:
    c = _c()
    node = _exists(str(params.get("node") or ""))
    parent = str(params.get("parent") or "").strip()
    if parent:
        _exists(parent)
        result = c.parent(node, parent)
    else:
        result = c.parent(node, world=True)
    return {"node": node, "parent": parent or "<world>",
            "result": str((result or [node])[0])}


def attr_set(params: Dict[str, Any]) -> Dict[str, Any]:
    c = _c()
    node = _exists(str(params.get("node") or ""))
    attr = re.sub(r"[^A-Za-z0-9_.\[\]]", "", str(params.get("attribute") or ""))
    plug = "%s.%s" % (node, attr)
    if not c.objExists(plug):
        raise ValueError("属性不存在：%s" % plug)
    if c.getAttr(plug, lock=True):
        raise ValueError(
            "属性被锁定：%s（常见于被蒙皮/约束连接的变换通道）。"
            "TAK 不擅自解锁，请在 Maya 里解锁后再试，或改一个未锁定的属性。" % plug)
    value = params.get("value")
    old = c.getAttr(plug)
    attr_type = str(c.getAttr(plug, type=True) or "")
    if attr_type == "string":
        c.setAttr(plug, str(value), type="string")
        applied: Any = str(value)
    elif attr_type in ("double3", "float3") and isinstance(value, (list, tuple)) \
            and len(value) == 3:
        c.setAttr(plug, *[_num(item) for item in value], type=attr_type)
        applied = [_num(item) for item in value]
    elif attr_type == "bool":
        flag = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes")
        c.setAttr(plug, bool(flag))
        applied = bool(flag)
    elif isinstance(value, str):
        # 模型经常把数字写成字符串；按目标属性类型还原，而不是一律 setAttr 字符串。
        if attr_type in ("double", "float", "doubleLinear", "doubleAngle", "long", "short"):
            number = float(value)
            c.setAttr(plug, number)
            applied = number
        else:
            c.setAttr(plug, value)
            applied = value
    else:
        c.setAttr(plug, value)
        applied = value
    return {"node": node, "attribute": attr, "old": old, "new": applied,
            "new_readback": c.getAttr(plug), "attr_type": attr_type}


def selection_set(params: Dict[str, Any]) -> Dict[str, Any]:
    c = _c()
    nodes = [str(item) for item in (params.get("nodes") or [])]
    if not nodes:
        c.select(clear=True)
        return {"selection": [], "count": 0}
    for node in nodes[:_MAX_NODES]:
        _exists(node)
    c.select(nodes[:_MAX_NODES], replace=True)
    return {"selection": nodes[:_MAX_NODES], "count": len(nodes[:_MAX_NODES])}


def scene_undo_tak(params: Dict[str, Any]) -> Dict[str, Any]:
    result = sa.REGISTRY.undo_last("maya")
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "撤销失败")
    return result


# --------------------------------------------------------------------------- #
# undo 钩子：按 undo 队列长度差回滚，绝不误撤用户自己的操作
# --------------------------------------------------------------------------- #
def _undo_length(c: Any) -> int:
    try:
        return int(c.undoInfo(q=True, length=True) or 0)
    except Exception:
        return -1


def _begin(label: str) -> Dict[str, Any]:
    c = _c()
    before = _undo_length(c)
    c.undoInfo(openChunk=True, chunkName=str(label)[:64])
    _LAST_CHUNK.update({"label": str(label), "after": -1, "used": False})
    return {"before": before, "label": str(label)}


def _commit(token: Any) -> None:
    c = _c()
    c.undoInfo(closeChunk=True)
    _LAST_CHUNK["after"] = _undo_length(c)


def _rollback(token: Any) -> None:
    c = _c()
    try:
        c.undoInfo(closeChunk=True)
    except Exception:
        pass
    before = int((token or {}).get("before", -1))
    if before < 0:
        return  # 拿不到队列长度时宁可留着，也不能盲撤
    try:
        after = int(c.undoInfo(q=True, length=True) or 0)
        for _ in range(max(0, min(after - before, 32))):
            c.undo()
    except Exception:
        pass


def _undo_last(token: Any) -> Dict[str, Any]:
    c = _c()
    label = str(_LAST_CHUNK.get("label") or "")
    after = int(_LAST_CHUNK.get("after", -1))
    if _LAST_CHUNK.get("used") or not label or after < 0:
        raise RuntimeError("没有可撤销的 TAK 写入（或上一次已撤销过）")
    current = _undo_length(c)
    if current < 0 or current != after:
        # 自上次 TAK 写入后用户/别的插件又改了场景，撤销会误伤，直接拒绝。
        raise RuntimeError(
            "自上次 TAK 写入后场景发生了其它操作（undo 队列 %s→%s），"
            "拒绝撤销以免误撤你自己的改动。请改用 Maya 自带 Ctrl+Z。"
            % (after, current))
    c.undo()
    _LAST_CHUNK["used"] = True
    return {"undone": label}


# --------------------------------------------------------------------------- #
# 预览器：写操作的 dry-run 说明（不碰宿主）
# --------------------------------------------------------------------------- #
def _preview_create(params: Dict[str, Any]) -> Dict[str, Any]:
    return {"operation": "CREATE", "kind": params.get("kind"),
            "name": params.get("name"), "parent": params.get("parent") or "<world>",
            "position": params.get("position") or [0, 0, 0],
            "effect": "场景新增 1 个节点，可用 scene.undo_tak 撤销"}


def _preview_generic(verb: str):
    def _fn(params: Dict[str, Any]) -> Dict[str, Any]:
        return {"operation": verb, "params": dict(params),
                "effect": "可用 scene.undo_tak 撤销"}
    return _fn


_HANDLERS = {
    "scene.summary": scene_summary,
    "scene.list_nodes": scene_list_nodes,
    "scene.find": scene_find,
    "scene.get_node": scene_get_node,
    "scene.get_attr": scene_get_attr,
    "scene.frame_range": scene_frame_range,
    "selection.get": selection_get,
    "rig.hierarchy": rig_hierarchy,
    "rig.skin_info": rig_skin_info,
    "rig.influences": rig_influences,
    "rig.connections": rig_connections,
    "node.create": node_create,
    "scene.create_cube_chain": cube_chain_create,
    "node.rename": node_rename,
    "node.delete": node_delete,
    "node.parent": node_parent,
    "attr.set": attr_set,
    "selection.set": selection_set,
    "scene.undo_tak": scene_undo_tak,
}

# scene.create_cube_chain 故意不在这里：它的预览是 core 的纯计算
# cube_chain_plan()，MCP / Web 进程里没有 Maya 也要能 dry-run。
_PREVIEWS = {
    "node.create": _preview_create,
    "node.rename": _preview_generic("RENAME"),
    "node.delete": _preview_generic("DELETE"),
    "node.parent": _preview_generic("REPARENT"),
    "attr.set": _preview_generic("SET_ATTR"),
    "selection.set": _preview_generic("SELECT"),
    "scene.undo_tak": _preview_generic("UNDO"),
}


def register_all() -> int:
    """把 Maya 实现装进 core 注册表；可重复调用（幂等）。"""
    for name, handler in _HANDLERS.items():
        sa.implement(name, handler, previewer=_PREVIEWS.get(name))
    sa.REGISTRY.register_handler("maya", {
        "begin": _begin, "commit": _commit, "rollback": _rollback,
        "undo_last": _undo_last,
    })
    return len(_HANDLERS)


__all__ = ["register_all"]
