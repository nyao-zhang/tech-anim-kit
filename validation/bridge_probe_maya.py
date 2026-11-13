# -*- coding: utf-8 -*-
"""Maya 真机验证：本地桥能否被外部调到，且宿主调用是否回到主线程。

要验证的四件事
--------------
1. ``maya.utils.executeInMainThreadWithResult`` 的执行器确实被适配器注册上了
   （``thread_safe`` 为 True）；
2. 从 HTTP 侧调 ``/bridge/scene`` 能拿到真场景的关节数与 health 覆盖；
3. ``/bridge/run`` 能跑真检查并给出 finding（含场景卫生里真实的孤立节点）；
4. ``401`` 守得住 —— 没带 token 的请求拿不到任何场景数据。

mayabatch 足够（不起窗口）。结果写 ``validation/bridge_probe_result.json``。

跑法::

    "/c/Program Files/Autodesk/Maya2026/bin/mayabatch.exe" \
      -command "python(\"exec(open(r'C:/.../validation/bridge_probe_maya.py', encoding='utf-8').read())\")"
"""
from __future__ import annotations

import io
import json
import os
import sys
import traceback
import urllib.error
import urllib.request

_RESULT = {"ok": False, "steps": [], "error": ""}


def _repo_root() -> str:
    try:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    except NameError:  # exec(open(...).read()) 下没有 __file__
        return os.environ.get("TAK_REPO_ROOT") or os.getcwd()


def _step(name: str, **payload) -> None:
    entry = {"step": name}
    entry.update(payload)
    _RESULT["steps"].append(entry)


def _raw(path: str, token=None, method="GET", body=None):
    """绕过客户端封装直接发请求，才能验证 401。"""
    from tak.core import bridge
    base = bridge.load_discovery().get("url")
    if not base:
        return None, {"ok": False, "error": "发现文件缺失"}
    headers = {}
    if token is not None:
        headers["X-TAK-Token"] = token
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base + path, data=data, headers=headers,
                                     method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _build_scene(cmds) -> dict:
    cmds.file(new=True, force=True)
    root = cmds.createNode("joint", name="root")
    hips = cmds.createNode("joint", name="hips")
    cmds.parent(hips, root)
    junk = cmds.createNode("transform", name="junk_orphan_grp")
    curve = cmds.circle(name="l_arm_ctrl", normal=(0, 1, 0), radius=1)[0]
    return {"root": root, "hips": hips, "junk": junk, "control": curve}


def main() -> None:
    try:
        import maya.cmds as cmds  # noqa: F401
    except Exception as exc:
        _RESULT["error"] = "not in maya: %s" % exc
        _write()
        return
    import threading

    root = _repo_root()
    if root and root not in sys.path:
        sys.path.insert(0, root)

    from tak.core import bridge

    try:
        made = _build_scene(cmds)
        _step("scene_built", nodes=made)
        payload = bridge.start_bridge(dcc="maya", port=0)
        _step("bridge_started",
              ok=bool(payload.get("ok")), port=payload.get("port"),
              url=payload.get("url"), thread_safe=payload.get("thread_safe"),
              thread_note=payload.get("thread_note"))
    except Exception:
        _RESULT["error"] = traceback.format_exc()
        _write()
        return

    # 关键：主线程到此结束，把控制权还给 Maya 事件循环；真正的 HTTP 断言
    # 放在工作线程里跑，宿主的 idle 队列才有机会被泵到。
    threading.Thread(target=_probe_worker, name="tak-probe", daemon=False).start()


def _probe_worker() -> None:
    from tak.core import bridge
    try:
        token = bridge.tak_config.load()["bridge"]["token"]

        status, health = _raw("/bridge/health")
        _step("health", status=status, auth_required=health.get("auth_required"))

        status, denied = _raw("/bridge/scene")            # 不带 token
        _step("auth_denied", status=status, error=denied.get("error"))

        status, scene = _raw("/bridge/scene", token=token)
        _step("scene", status=status, joint_count=scene.get("joint_count"),
              joints_preview=scene.get("joints_preview"),
              health_keys=scene.get("health_keys"),
              scene_file=scene.get("scene"))

        status, tools = _raw("/bridge/tools", token=token)
        _step("tools", status=status,
              ids=[item["id"] for item in tools.get("tools") or []])

        status, run = _raw("/bridge/run", token=token, method="POST", body={
            "tool_id": "qa_inspector",
            "options": {"checks": ["rig_health_guard.orphan_nodes",
                                   "rig_health_guard.negative_scale",
                                   "rig_health_guard.control_uncolored"]}})
        _step("run", status=status, ok=run.get("ok"), passed=run.get("passed"),
              finding_count=run.get("finding_count"),
              rule_ids=sorted({item["rule_id"] for item in run.get("findings") or []}),
              nodes=sorted({item.get("node") for item in run.get("findings") or []}),
              health_gaps=(run.get("metadata") or {}).get("health_gaps"))

        status, chat = _raw("/bridge/chat", token=token, method="POST",
                            body={"message": "解释当前快照存在的问题"})
        _step("chat", status=status, ok=chat.get("ok"),
              intent=(chat.get("turn") or {}).get("intent"),
              note=chat.get("note"))

        _RESULT["ok"] = all([
            bool(_step_value("bridge_started", "thread_safe")),
            _step_value("auth_denied", "status") == 401,
            _step_value("scene", "joint_count") == 2,
            bool(_step_value("run", "ok")),
            bool(_step_value("chat", "ok")),
        ])
    except Exception:
        _RESULT["error"] = traceback.format_exc()
    finally:
        try:
            bridge.stop_bridge()
        except Exception:
            pass
        _write()


def _step_value(step: str, key: str):
    for entry in _RESULT["steps"]:
        if entry.get("step") == step:
            return entry.get(key)
    return None


def _write() -> None:
    target = os.path.join(_repo_root() or ".", "validation",
                          "bridge_probe_result.json")
    with io.open(target, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(_RESULT, ensure_ascii=False, indent=2))
    try:
        import maya.utils as maya_utils
        maya_utils.executeDeferred("__import__('maya.cmds', fromlist=['x']).quit(force=True)")
    except Exception:
        pass


main()
