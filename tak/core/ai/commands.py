# -*- coding: utf-8 -*-
"""自然语言 → 受限场景动作：对话控制台真正「能干活」的那一层。

两条解析路径，共用同一套执行器与同一张动作白名单
------------------------------------------------
1. **有 AI 端点**：OpenAI 兼容 ``tools`` function calling。模型只输出
   「动作名 + 参数 JSON」，不能输出代码；参数还要过
   ``tak.core.scene_actions`` 的白名单 / 类型 / 范围校验。
2. **没有 AI 端点**（默认、离线、Ollama 挂了）：本地确定性关键词解析。
   覆盖「查场景 / 列节点 / 找节点 / 查骨架 / 查蒙皮 / 创建 / 改名 / 删除」
   这些高频句式，让**不配 key 也能用**。解析不出来就明说，不瞎猜。

两条路径都只产出 ``calls``；真正执行统一走 :func:`run`。写动作第一次必然
只做 dry-run 并拿到 ``confirm_token``，只有调用方带 token 且
``execute=True`` 且 ``allow_write=True`` 才落到宿主 —— 也就是 UI 上那个
「执行」按钮。模型和 MCP 都不能自己越过这一步。
"""
from __future__ import annotations

import json
import random
import re
from typing import Any, Dict, List, Optional, Tuple

from tak.core import scene_actions as sa

SYSTEM_PROMPT = """你是 Maya / Max / MotionBuilder / UE 技术动画助手，帮用户查询和修改当前 DCC 场景。

规则（必须遵守）：
- 只能从给定的 tools 里选，输出工具名与参数 JSON。绝对不要生成 Python / MEL 代码，也不要编造工具名或参数。
- 查询类工具可以连续调用多个（例如先 scene.find 再 scene.get_node）。
- 写入类工具你只负责给出参数；系统会自动做 dry-run 预览并等待人工确认，你不要尝试自己确认或绕过。
- 一次最多一个写操作。不知道确切节点名时，先用 scene.find 或 scene.list_nodes 查。
- 用户要「创建 N 个随机大小的方块并按大小做父子」这类复合需求时，用 scene.create_cube_chain 一次完成，不要拆成 N 次 node.create + N 次 node.parent（每个写操作都要单独人工确认）。用 volume 还是 edge 按用户给的单位判断；没给 seed 就给一个整数；不要主动把 spacing 设成 0（会同心重叠看不见），留默认值自动排开即可。
- 用户的问题无法用现有工具完成时，用一句中文说明缺什么，不要假装已经做了。
- 回答用简体中文。"""

_LOCAL_HINT = ("未配置 AI 端点或端点不可用，已使用本地关键词解析。"
               "可识别：查场景概况 / 帧范围 / 列出节点 / 找节点 / 当前选择 / "
               "骨架层级 / 蒙皮信息 / 节点连接 / 创建 locator·joint·group·cube / "
               "重命名 / 删除 / 设属性 / 批量创建随机大小立方体并按体积排父子链。"
               "想要任意说法请在「AI 连接」里配好端点。")

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_:|]{1,63}")
_STOPWORDS = frozenset({
    "scene", "maya", "tak", "list", "find", "get", "set", "create", "delete",
    "rename", "joint", "group", "locator", "cube", "control", "mesh", "node",
    "attr", "attribute", "selection", "rig", "hierarchy", "skin", "info",
    "summary", "frame", "range", "the", "a", "an", "and", "or", "to", "of",
    "please", "帮我", "查询", "列出", "创建", "删除", "重命名", "节点", "场景",
    "当前", "所有", "一个", "叫什么", "名字", "属性",
})
_NAME_HINT = re.compile(
    # 前缀后面必须允许空格，否则「改名为 body」「叫 foot_ctrl」这类中文说法
    # 一个名字都抽不到，本地解析会假装听不懂。
    r"(?:叫|名为|名字是|名称是|节点|网格|mesh|选|把|对|将|改成|改为)\s*"
    r"[「『\"'`]?\s*([A-Za-z_][A-Za-z0-9_:|]{0,63})[」』\"'`]?", re.I)


# --------------------------------------------------------------------------- #
# 批量复合需求：N 个随机大小立方体 + 按体积排父子链
# --------------------------------------------------------------------------- #
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_COUNT_RE = re.compile(
    r"([0-9]+|[零一二两三四五六七八九十]+)\s*"
    r"(?:个|块|台|只|根|立方体|正方块|方块|cubes?)", re.I)
_RANGE_RE = re.compile(
    r"([0-9]+(?:\.[0-9]+)?)\s*(?:~|～|-|—|到|至)\s*"
    r"([0-9]+(?:\.[0-9]+)?)")


def _cn_number(token: str) -> Optional[int]:
    token = str(token or "").strip()
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if "十" in token:
        left, _, right = token.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        if left and left not in _CN_DIGITS:
            return None
        if right and right not in _CN_DIGITS:
            return None
        return tens * 10 + ones
    return _CN_DIGITS.get(token)


def _cube_chain_call(raw: str, low: str) -> Optional[Dict[str, Any]]:
    """把「创建十个随机大小 10~100 立方厘米的方块，按体积做父子」变成一个动作。

    只在这里生成随机 ``seed``：它随 calls 回传，所以 dry-run 预览与点「执行」
    后真正建出来的尺寸、名字、层级完全一致。
    """
    if not any(key in low for key in ("创建", "新建", "建", "生成", "create", "make")):
        return None
    if not any(key in low for key in ("方块", "正方", "立方体", "cube")):
        return None
    matched = _COUNT_RE.search(raw)
    if not matched:
        return None
    count = _cn_number(matched.group(1))
    if not count or count < 2 or count > 50:
        return None

    low_v, high_v = 10.0, 100.0
    ranged = _RANGE_RE.search(raw)
    if ranged:
        low_v, high_v = float(ranged.group(1)), float(ranged.group(2))
        if low_v <= 0 or high_v <= 0 or low_v > high_v:
            return None

    # 「立方厘米 / 体积」按体积解释；只说「厘米 / 大小」按边长解释。
    # 注意不能用宽泛的「立方」：「立方体」只是 cube 的中文名，不是体积单位。
    volume_words = ("立方厘米", "立方公分", "立方cm", "cm³", "cm3", "cc", "体积")
    size_by = "volume" if any(key in low for key in volume_words) else "edge"
    link = any(key in low for key in
               ("父子", "父级", "层级", "链", "最大", "按大小", "按体积", "排序"))
    # 不传 spacing 让它走动作的「自动排开」默认值：10 个同心立方体会被
    # 最大的完全包住，看起来像只建了一个。用户明确要求重叠时才传 0。
    params: Dict[str, Any] = {
        "count": count, "min_size": low_v, "max_size": high_v,
        "size_by": size_by, "link": bool(link),
        "prefix": "takCube",
        "seed": random.randrange(1, 2147483647),
    }
    if any(key in low for key in ("原点", "重叠", "同一位置", "不要偏移")):
        params["spacing"] = 0.0
    return {"action": "scene.create_cube_chain", "params": params}


def _names(text: str) -> List[str]:
    """抽候选节点名：优先取「叫 xxx」这类显式说法，再退回英文标识符。"""
    found = [item for item in _NAME_HINT.findall(text)
             if item.lower() not in _STOPWORDS]
    if found:
        return list(dict.fromkeys(found))
    return [item for item in _IDENT.findall(text)
            if item.lower() not in _STOPWORDS][:6]


def _kind(text: str) -> str:
    low = text.lower()
    for key, kind in (("joint", "joint"), ("关节", "joint"), ("骨骼", "joint"),
                      ("group", "group"), ("空组", "group"), ("组", "group"),
                      ("cube", "cube"), ("立方体", "cube"), ("方块", "cube"),
                      ("control", "control"), ("控制器", "control"),
                      ("locator", "locator"), ("定位器", "locator")):
        if key in low:
            return kind
    return "locator"


def _node_type(text: str) -> str:
    low = text.lower()
    if any(key in low for key in ("joint", "关节", "骨骼")):
        return "joint"
    if any(key in low for key in ("mesh", "网格", "模型")):
        return "mesh"
    if any(key in low for key in ("skincluster", "蒙皮簇")):
        return "skinCluster"
    # 「控制器」不映射到 nurbsCurve：控制器通常是命名约定（*_ctrl*），
    # 用 pattern 找比按曲线类型找更准；「曲线」才按节点类型过滤。
    if any(key in low for key in ("curve", "曲线")):
        return "nurbsCurve"
    return ""


def parse_local(text: str) -> Dict[str, Any]:
    """本地确定性解析；返回 ``{"ok", "calls", "mode", "hint", "reply"}``。"""
    raw = str(text or "").strip()
    low = raw.lower()
    if not raw:
        return {"ok": False, "calls": [], "mode": "local",
                "hint": _LOCAL_HINT, "reply": "请输入一句需求。"}
    names = _names(raw)
    calls: List[Dict[str, Any]] = []

    # --- 写操作（只生成调用，执行仍要 UI 上的人工确认） -------------------
    # 批量复合需求必须先判：否则「创建十个随机方块」会被下面的单节点分支
    # 误当成「创建需要给出名字」而直接拒掉。
    batch = _cube_chain_call(raw, low)
    if batch:
        calls.append(batch)
    elif any(key in low for key in ("创建", "新建", "建一个", "加一个", "添加", "create")):
        if not names:
            return {"ok": False, "calls": [], "mode": "local",
                    "hint": "创建需要给出名字，例如「创建一个 locator 叫 foot_ctrl」",
                    "reply": ""}
        calls.append({"action": "node.create",
                      "params": {"kind": _kind(raw), "name": names[-1]}})
    elif any(key in low for key in ("重命名", "改名", "rename")):
        if len(names) < 2:
            return {"ok": False, "calls": [], "mode": "local",
                    "hint": "重命名需要原名和新名，例如「把 pCube1 改名为 body」",
                    "reply": ""}
        calls.append({"action": "node.rename",
                      "params": {"node": names[0], "name": names[-1]}})
    elif any(key in low for key in ("删除", "删掉", "移除", "delete")):
        if not names:
            return {"ok": False, "calls": [], "mode": "local",
                    "hint": "删除需要节点名，例如「删除 pCube1」", "reply": ""}
        calls.append({"action": "node.delete", "params": {"node": names[0]}})
    # --- 只读查询 ---------------------------------------------------------
    elif any(key in low for key in ("帧范围", "时间轴", "frame range", "fps", "帧率")):
        calls.append({"action": "scene.frame_range", "params": {}})
    elif any(key in low for key in ("当前选择", "选中了", "选择集", "selection")):
        calls.append({"action": "selection.get", "params": {}})
    elif any(key in low for key in ("层级", "骨架树", "父子关系", "hierarchy")):
        if not names:
            return {"ok": False, "calls": [], "mode": "local", "hint":
                    "查骨架层级需要根关节名，例如「查 root 的层级」", "reply": ""}
        calls.append({"action": "rig.hierarchy", "params": {"root": names[0]}})
    elif any(key in low for key in ("蒙皮", "skin", "权重", "影响关节")):
        if not names:
            return {"ok": False, "calls": [], "mode": "local", "hint":
                    "查蒙皮需要网格名，例如「查 body 的蒙皮信息」", "reply": ""}
        if any(key in low for key in ("反查", "影响了哪些")):
            calls.append({"action": "rig.influences", "params": {"joint": names[0]}})
        else:
            calls.append({"action": "rig.skin_info", "params": {"mesh": names[0]}})
    elif any(key in low for key in ("连接", "驱动", "constraint", "connection")):
        if not names:
            return {"ok": False, "calls": [], "mode": "local",
                    "hint": "查连接需要节点名", "reply": ""}
        calls.append({"action": "rig.connections", "params": {"node": names[0]}})
    elif any(key in low for key in ("详情", "属性", "信息", "get node", "查一下")):
        if not names:
            return {"ok": False, "calls": [], "mode": "local",
                    "hint": "查节点详情需要节点名，例如「查 pCube1 的详情」", "reply": ""}
        calls.append({"action": "scene.get_node", "params": {"node": names[0]}})
    elif any(key in low for key in ("找", "搜索", "查找", "find", "哪些节点叫")):
        query = names[0] if names else ""
        if not query:
            return {"ok": False, "calls": [], "mode": "local",
                    "hint": "请给出要找的名字片段，例如「找 foot 相关的节点」",
                    "reply": ""}
        params: Dict[str, Any] = {"query": query}
        node_type = _node_type(raw)
        if node_type:
            params["type"] = node_type
        calls.append({"action": "scene.find", "params": params})
    elif any(key in low for key in ("列出", "所有", "有哪些", "多少个", "list")):
        params = {}
        node_type = _node_type(raw)
        if node_type:
            params["type"] = node_type
        elif any(key in low for key in ("控制器", "ctrl", "控制")):
            params["pattern"] = "*ctrl*"
        calls.append({"action": "scene.list_nodes", "params": params})
    elif any(key in low for key in ("概况", "摘要", "场景信息", "状态", "多少",
                                    "summary", "overview")):
        calls.append({"action": "scene.summary", "params": {}})
    else:
        return {"ok": False, "calls": [], "mode": "local", "hint": _LOCAL_HINT,
                "reply": "没识别到可执行的操作。%s" % _LOCAL_HINT}

    for call in calls:
        call["confirm_token"] = ""
    return {"ok": True, "calls": calls, "mode": "local",
            "hint": _LOCAL_HINT, "reply": ""}


def parse_with_model(text: str, provider: Any = None) -> Dict[str, Any]:
    """OpenAI 兼容 function calling；任何失败都返回 ok=false 让上层降级。"""
    from .connection import resolve_settings

    settings = resolve_settings()
    if not settings.get("api_key_present"):
        return {"ok": False, "mode": "model",
                "error": "未配置 AI 端点（没有密钥），使用本地解析"}
    try:
        if provider is None:
            from .providers import get_provider
            # 面板是同步调用的：12 秒还不够就让本地解析接管，不要让 Maya
            # 界面因为一个端点慢而假死（用户刚被「切分类卡几秒」坑过）。
            provider = get_provider(task="default", timeout=12.0,
                                    max_retries=1)
        client = getattr(provider, "client", None)
        if client is None or not hasattr(client, "chat_tools"):
            return {"ok": False, "mode": "model",
                    "error": "当前 provider 不支持 function calling，使用本地解析"}
        # 这里必须是 OpenAI function calling 格式，不是 MCP tools/list 格式；
        # 传错会得到 HTTP 422: tools[0]: missing field `type`。
        tools = sa.REGISTRY.openai_tools()
        message = client.chat_tools(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": str(text)}],
            tools=tools, tool_choice="auto")
    except Exception as exc:
        return {"ok": False, "mode": "model",
                "error": "%s: %s" % (type(exc).__name__, exc)}

    calls: List[Dict[str, Any]] = []
    for item in (message.get("tool_calls") or [])[:8]:
        function = item.get("function") or {}
        action = sa.REGISTRY.tool_to_action(str(function.get("name") or ""))
        if not action:
            continue
        try:
            params = json.loads(function.get("arguments") or "{}")
        except (TypeError, ValueError):
            params = {}
        if not isinstance(params, dict):
            params = {}
        # 模型不该输出确认字段；即使输出了也剥掉，确认权只属于人。
        params.pop("confirm", None)
        params.pop("confirm_token", None)
        params.pop("dry_run", None)
        calls.append({"action": action, "params": params, "confirm_token": ""})
    return {"ok": bool(calls), "calls": calls, "mode": "model",
            "reply": str(message.get("content") or ""),
            "error": "" if calls else "模型没有给出可执行的动作"}


def resolve(text: str, *, use_model: bool = True,
            provider: Any = None) -> Dict[str, Any]:
    """先试模型，失败自动降级本地；绝不因为模型挂了就什么也做不了。"""
    if use_model:
        result = parse_with_model(text, provider=provider)
        if result.get("ok") and result.get("calls"):
            return result
        fallback = parse_local(text)
        fallback["model_error"] = result.get("error") or result.get("reply") or ""
        fallback["mode"] = "local-fallback"
        return fallback
    return parse_local(text)


def run(calls: List[Dict[str, Any]], *, dcc: str = "maya",
        allow_write: bool = False, execute: bool = False) -> Dict[str, Any]:
    """执行（或预览）一批动作。写操作只有在 UI 明确带 token 提交时才落地。"""
    results: List[Dict[str, Any]] = []
    pending = False
    for call in list(calls or [])[:8]:
        action = str(call.get("action") or "")
        params = call.get("params") or {}
        token = str(call.get("confirm_token") or "")
        spec = sa.REGISTRY.get(action)
        commit = bool(execute and token and spec is not None
                      and not spec.read_only and allow_write)
        payload = sa.REGISTRY.execute(
            action, params, confirm=commit,
            dry_run=not (execute and token and spec is not None
                         and not spec.read_only),
            confirm_token=token,
            granted=[sa.WRITE_PERMISSION] if allow_write else [],
            dcc=dcc)
        if payload.get("status") == "dry_run":
            pending = True
            call["confirm_token"] = payload.get("confirm_token") or ""
        payload["action"] = action
        payload["params"] = params
        payload["read_only"] = bool(spec and spec.read_only)
        results.append(payload)
    return {"ok": True, "results": results, "need_confirm": pending,
            "allow_write": bool(allow_write), "dcc": dcc,
            "calls": [dict(call) for call in calls]}


def command(message: str = "", *, calls: Any = None, dcc: str = "maya",
            allow_write: bool = False, execute: bool = False,
            use_model: bool = True, provider: Any = None) -> Dict[str, Any]:
    """对话控制台的主入口（也是 Web API 可复用的纯数据接口）。

    ``calls`` 为空 → 解析 ``message``；非空 → 直接用这批调用（UI 点「执行」
    时把带 token 的 calls 原样传回来）。返回值全部可 JSON 序列化。
    """
    if calls:
        payload = run(list(calls), dcc=dcc, allow_write=allow_write,
                      execute=execute)
        payload.update({"message": message, "mode": "execute" if execute
                        else "preview", "reply": "", "hint": ""})
        return payload
    parsed = resolve(str(message or ""), use_model=use_model, provider=provider)
    if not parsed.get("ok") or not parsed.get("calls"):
        return {
            "ok": False, "message": message, "mode": parsed.get("mode", "local"),
            "reply": parsed.get("reply") or "没能解析出可执行的场景动作。",
            "hint": parsed.get("hint") or parsed.get("model_error") or "",
            "model_error": parsed.get("model_error", ""),
            "results": [], "need_confirm": False, "calls": [],
        }
    payload = run(parsed["calls"], dcc=dcc, allow_write=allow_write,
                  execute=False)
    payload.update({
        "message": message,
        "mode": parsed.get("mode", "local"),
        "reply": parsed.get("reply", ""),
        "hint": parsed.get("hint", ""),
        "model_error": parsed.get("model_error", ""),
    })
    return payload


def _cube_chain_summary(preview: Dict[str, Any]) -> str:
    """批量预览太长，压成一句人话；完整明细仍在 preview 里可查。"""
    cubes = preview.get("cubes") or []
    count = int(preview.get("count") or len(cubes))
    bounds = list(preview.get("range") or [0, 0]) + [0, 0]
    edges = [float(item.get("edge_cm") or 0) for item in cubes] or [0.0]
    unit = "cm³（体积）" if preview.get("size_by") == "volume" else "cm（边长）"
    chain = preview.get("chain") or []
    if preview.get("link") and len(chain) > 2:
        chain_text = "%s → … → %s（最大为最父级）" % (chain[0], chain[-1])
    elif chain and preview.get("link"):
        chain_text = " → ".join(chain)
    else:
        chain_text = "不建父子关系"
    return ("%d 个立方体，%s %g~%g，边长 %g~%g cm；%s；间距 %g cm（%s）；seed=%s"
            % (count, unit, bounds[0], bounds[1], min(edges), max(edges),
               chain_text, preview.get("spacing", 0),
               preview.get("spacing_mode", ""), preview.get("seed", "")))


def format_result(payload: Dict[str, Any]) -> str:
    """把一次动作结果压成一行人话，供 UI 日志台显示。"""
    action = str(payload.get("action") or "?")
    if not payload.get("ok"):
        return "%s ✗ %s" % (action, payload.get("error") or "失败")
    if payload.get("read_only"):
        result = payload.get("result")
        if isinstance(result, dict):
            keys = [key for key in ("scene", "count", "joint_count", "skinned",
                                    "influence_count", "value", "type", "frame_range")
                    if key in result]
            brief = {key: result[key] for key in keys[:4]}
            return "%s ✓ %s" % (action, json.dumps(brief, ensure_ascii=False))
        return "%s ✓ %s" % (action, str(result)[:160])
    if payload.get("status") == "dry_run":
        preview = payload.get("preview") or {}
        if preview.get("operation") == "CREATE_CUBE_CHAIN":
            return "%s · 待确认：%s；点「执行」后一次撤销可全部回滚" % (
                action, _cube_chain_summary(preview))
        return "%s · 待确认：%s" % (action, json.dumps(
            preview, ensure_ascii=False)[:240])
    result = payload.get("result") or {}
    if isinstance(result, dict) and "created" in result and "chain" in result:
        chain = result.get("chain") or []
        brief = ("%s → … → %s" % (chain[0], chain[-1])) if len(chain) > 2 \
            else (" → ".join(chain) or "未建父子链")
        return "%s ✓ 已创建 %d 个立方体（%s）；一次撤销可全部回滚" % (
            action, len(result.get("created") or []), brief)
    return "%s ✓ 已执行 %s" % (action, json.dumps(
        result, ensure_ascii=False)[:200])


__all__ = ["SYSTEM_PROMPT", "command", "format_result", "parse_local",
           "parse_with_model", "resolve", "run"]
