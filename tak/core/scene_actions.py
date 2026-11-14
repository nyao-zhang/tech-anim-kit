# -*- coding: utf-8 -*-
"""场景动作注册表：把「自然语言 / MCP」翻译成**受限的**真实 DCC 读写。

为什么需要这一层
----------------
旧 ``tak.core.agent`` 只有 4 个 ``spec_enforcer`` 动作，而且面向「修规范」；
它查不到当前有哪些节点，也创建不了任何东西。用户真正要的是「看一眼场景、
改点东西、建个资产」。本模块就是这条链路的**唯一入口**：

    自然语言 / MCP tools/call
        -> ActionSpec（白名单 + 参数 schema）
        -> dry-run 预览 + 一次性 confirm_token
        -> 显式确认 + 权限 + undo chunk
        -> 适配器注册的 handler（真正碰 DCC 的代码）

硬约束（和 ``tak.core.dsl`` 一脉相承，但更严格）
------------------------------------------------
1. **零 DCC import**：本文件只依赖标准库；handler 由 ``tak/adapters/*`` 在
   导入时注册。``tests/test_py39_compat.py`` 守这条。
2. **不让模型输出代码**：模型只能给 ``action`` 名 + 参数 JSON；未注册动作、
   未声明参数、类型不符、越界一律拒绝。没有 eval / exec / 任意 Python。
3. **写操作两阶段**：第一次调用必定返回 dry-run 预览和 ``confirm_token``；
   只有带上同一个 token 且 ``confirm=True`` 才会真写。token 绑定动作名与参数，
   中途改参数立即失效 —— 防止「给我看的 diff」和「实际执行的」不是一回事。
4. **可撤销**：写操作包在 undo chunk 里，异常整体回滚；``tak.undo`` 动作
   撤销最近一次 TAK 写入（宿主线程安全由适配器保证）。
5. **审计**：每次调用记 seq / action / dry_run / confirm / ok / committed / 错误。

读操作不需要 token，也不需要确认 —— 查询场景本来就不该有摩擦。
"""
from __future__ import annotations

import copy
import json
import math
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

READ_PERMISSION = "scene.read"
WRITE_PERMISSION = "node.write"

#: 字符串参数最大长度。防止模型把整个场景 JSON 塞进一个 node 名。
_MAX_STRING = 512
#: 数组参数最大长度。
_MAX_ARRAY = 256


class ActionError(ValueError):
    """动作不存在 / 参数非法 / 未确认 / 权限不足 / token 失效。"""


class ActionNotImplemented(ActionError):
    """当前 DCC 没有注册这个动作的宿主实现。"""


@dataclass
class ParamSpec:
    """单个参数的声明；同时用来生成 MCP 的 JSON Schema。"""

    type: str = "string"
    description: str = ""
    required: bool = False
    default: Any = None
    enum: Tuple[Any, ...] = ()
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    items: str = ""  # array 元素类型，空表示不校验

    def schema(self) -> Dict[str, Any]:
        # scalar 表示「字符串 / 数字 / 布尔 / 三元组都可以」，不写 type 字段，
        # 免得模型为了凑类型把 1 写成 "1" 或反过来。
        out: Dict[str, Any] = {} if self.type == "scalar" else {"type": self.type}
        if self.description:
            out["description"] = self.description
        if self.enum:
            out["enum"] = list(self.enum)
        if self.minimum is not None:
            out["minimum"] = self.minimum
        if self.maximum is not None:
            out["maximum"] = self.maximum
        if self.default is not None:
            out["default"] = self.default
        if self.type == "array" and self.items:
            out["items"] = {"type": self.items}
        return out


@dataclass
class ActionSpec:
    """一个可被模型 / MCP 调用的动作。"""

    name: str
    description: str
    handler: Callable[[Dict[str, Any]], Any]
    permissions: Tuple[str, ...] = (READ_PERMISSION,)
    params: Dict[str, ParamSpec] = field(default_factory=dict)
    read_only: bool = True
    dcc: str = ""
    #: 写动作的 dry-run 说明；不提供时用参数字典本身当预览。
    previewer: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    #: 分类，供 UI 与文档分组。
    group: str = "scene"
    #: 撤销动作自身不能包在新 undo chunk 里，否则 cmds.undo() 撤的是空 chunk。
    no_undo_chunk: bool = False

    @property
    def mcp_name(self) -> str:
        return "tak_" + self.name.replace(".", "_")

    def input_schema(self) -> Dict[str, Any]:
        properties = {key: value.schema() for key, value in self.params.items()}
        required = [key for key, value in self.params.items() if value.required]
        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

    def to_mcp_tool(self) -> Dict[str, Any]:
        description = self.description
        if not self.read_only:
            description += ("（写操作：首次调用只返回 dry-run 预览与 "
                            "confirm_token，需带 token 再次调用才会执行）")
        return {"name": self.mcp_name, "description": description,
                "inputSchema": self.input_schema()}

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "mcp_name": self.mcp_name,
            "description": self.description,
            "group": self.group,
            "dcc": self.dcc,
            "read_only": bool(self.read_only),
            "implemented": self.handler is not _NOT_IMPLEMENTED,
            "permissions": list(self.permissions),
            "params": {key: value.schema() for key, value in self.params.items()},
        }


def _check_value(name: str, key: str, spec: ParamSpec, value: Any) -> Any:
    """严格类型校验；不做隐式字符串转换，避免 node=123 之类悄悄生效。"""
    expected = spec.type
    ok = {
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "array": lambda v: isinstance(v, (list, tuple)),
        "object": lambda v: isinstance(v, dict),
        "scalar": lambda v: isinstance(v, (str, int, float, bool, list, tuple)),
    }.get(expected)
    if ok is None:
        raise ActionError("动作 %s 的参数 %s 声明了未知类型 %s" % (name, key, expected))
    if not ok(value):
        raise ActionError("动作 %s 的参数 %s 需要 %s，实际是 %s"
                          % (name, key, expected, type(value).__name__))
    if expected == "number" and isinstance(value, float) and not math.isfinite(value):
        raise ActionError("动作 %s 的参数 %s 不能是 NaN / Infinity" % (name, key))
    if expected == "string":
        if len(value) > _MAX_STRING:
            raise ActionError("动作 %s 的参数 %s 超过 %d 字符" % (name, key, _MAX_STRING))
        if "\x00" in value or "\n" in value:
            raise ActionError("动作 %s 的参数 %s 含非法控制字符" % (name, key))
    if expected == "array":
        if len(value) > _MAX_ARRAY:
            raise ActionError("动作 %s 的参数 %s 超过 %d 项" % (name, key, _MAX_ARRAY))
        value = list(value)
        if spec.items:
            item_spec = ParamSpec(type=spec.items)
            value = [_check_value(name, "%s[%d]" % (key, i), item_spec, item)
                     for i, item in enumerate(value)]
    if spec.enum and value not in spec.enum:
        raise ActionError("动作 %s 的参数 %s 必须是 %s 之一"
                          % (name, key, list(spec.enum)))
    if spec.minimum is not None and isinstance(value, (int, float)) \
            and value < spec.minimum:
        raise ActionError("动作 %s 的参数 %s 不能小于 %s" % (name, key, spec.minimum))
    if spec.maximum is not None and isinstance(value, (int, float)) \
            and value > spec.maximum:
        raise ActionError("动作 %s 的参数 %s 不能大于 %s" % (name, key, spec.maximum))
    return value


@dataclass
class _Pending:
    name: str
    params: Dict[str, Any]
    created_at: float
    preview: Dict[str, Any]


class SceneActionRegistry:
    """动作白名单 + 两阶段确认执行器（进程内单例见 ``REGISTRY``）。"""

    def __init__(self) -> None:
        self._specs: Dict[str, ActionSpec] = {}
        self._lock = threading.RLock()
        self._pending: Dict[str, _Pending] = {}
        self._audit: List[Dict[str, Any]] = []
        self._undo_hooks: Dict[str, Callable[..., Any]] = {}
        self._last_write: Dict[str, Any] = {}

    # ------------------------------------------------------------ 注册
    def register(self, spec: ActionSpec) -> None:
        if not spec.name or not callable(spec.handler):
            raise ActionError("动作声明缺少 name 或 handler")
        with self._lock:
            self._specs[spec.name] = spec

    def register_handler(self, dcc: str, hooks: Dict[str, Callable[..., Any]]) -> None:
        """注册 undo 钩子：``begin`` / ``commit`` / ``rollback`` / ``undo_last``。"""
        with self._lock:
            self._undo_hooks[str(dcc)] = dict(hooks)

    def get(self, name: str) -> Optional[ActionSpec]:
        return self._specs.get(str(name or ""))

    def names(self) -> List[str]:
        return sorted(self._specs)

    def describe(self, dcc: str = "") -> List[Dict[str, Any]]:
        items = [spec.describe() for spec in self._specs.values()]
        if dcc:
            items = [item for item in items
                     if not item["dcc"] or item["dcc"] == dcc]
        return sorted(items, key=lambda item: item["name"])

    def mcp_tools(self, dcc: str = "") -> List[Dict[str, Any]]:
        items = [spec.to_mcp_tool() for spec in self._specs.values()
                 if not dcc or not spec.dcc or spec.dcc == dcc]
        return sorted(items, key=lambda item: item["name"])

    def tool_to_action(self, mcp_name: str) -> str:
        prefix = "tak_"
        if not str(mcp_name or "").startswith(prefix):
            return ""
        candidate = str(mcp_name)[len(prefix):].replace("_", ".")
        if candidate in self._specs:
            return candidate
        # 名字里带下划线的动作（例如 frame_range）用反查表兜底。
        for name, spec in self._specs.items():
            if spec.mcp_name == mcp_name:
                return name
        return ""

    # ------------------------------------------------------------ 校验
    def validate(self, name: str, params: Any) -> Tuple[ActionSpec, Dict[str, Any]]:
        spec = self.get(name)
        if spec is None:
            raise ActionError("未注册的动作：%s（可用：%s）"
                              % (name, ", ".join(self.names()[:12])))
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise ActionError("动作 %s 的 params 必须是对象" % name)
        unknown = [key for key in params if key not in spec.params]
        if unknown:
            raise ActionError("动作 %s 不接受参数 %s（只接受 %s）"
                              % (name, unknown, sorted(spec.params)))
        clean: Dict[str, Any] = {}
        for key, decl in spec.params.items():
            if key in params:
                clean[key] = _check_value(name, key, decl, params[key])
            elif decl.required:
                raise ActionError("动作 %s 缺少必填参数 %s" % (name, key))
            elif decl.default is not None:
                clean[key] = copy.deepcopy(decl.default)
        return spec, clean

    # ------------------------------------------------------------ 执行
    def execute(self, name: str, params: Any = None, *, confirm: bool = False,
                dry_run: bool = True, confirm_token: str = "",
                granted: Any = None, dcc: str = "") -> Dict[str, Any]:
        """执行（或预览）一个动作。任何失败都返回 ``ok=false``，不抛给调用方。

        读操作忽略 confirm / dry_run / token，直接跑。
        写操作第一次返回 ``status=dry_run`` + ``confirm_token``；第二次必须带
        同一个 token、``confirm=True``、``dry_run=False`` 且权限齐备才执行。
        """
        try:
            spec, clean = self.validate(name, params)
        except ActionError as exc:
            return self._fail(name, str(exc))
        if spec.handler is _NOT_IMPLEMENTED:
            return self._fail(name, "动作 %s 在当前 DCC 没有宿主实现（能力已声明，"
                                    "但适配器未提供 handler）" % name)

        if spec.read_only:
            try:
                value = spec.handler(clean)
            except Exception as exc:
                return self._fail(name, "%s: %s" % (type(exc).__name__, exc))
            self._record(name, clean, dry_run=False, confirm=False,
                         ok=True, committed=False)
            return {"ok": True, "action": name, "read_only": True,
                    "status": "ok", "result": _jsonable(value),
                    "dry_run": False, "committed": False}

        try:
            preview = spec.previewer(clean) if spec.previewer else {
                "action": name, "params": clean}
        except Exception as exc:
            return self._fail(name, "预览生成失败: %s: %s" % (type(exc).__name__, exc))

        if dry_run or not confirm:
            token = self._new_token(name, clean, preview)
            return {"ok": True, "action": name, "read_only": False,
                    "status": "dry_run", "dry_run": True, "committed": False,
                    "preview": _jsonable(preview), "confirm_token": token,
                    "required_permissions": list(spec.permissions),
                    "hint": "确认无误后再次调用：confirm=True, dry_run=False, "
                            "confirm_token=该值（token 绑定动作与参数，改参数需重新预览）"}

        pending = self._pending.get(str(confirm_token or ""))
        if pending is None:
            return self._fail(name, "缺少有效的 confirm_token：写操作必须先做一次 "
                                    "dry-run 预览并拿到 token")
        if pending.name != name or pending.params != clean:
            return self._fail(name, "confirm_token 与本次动作/参数不匹配，请重新预览")
        if time.time() - pending.created_at > 300:
            self._pending.pop(str(confirm_token), None)
            return self._fail(name, "confirm_token 已过期（5 分钟），请重新预览")

        have = set(str(item) for item in (granted or []))
        missing = [item for item in spec.permissions if item not in have]
        if missing:
            return self._fail(name, "缺少权限: %s" % missing)

        hooks = ({} if spec.no_undo_chunk else
                 self._undo_hooks.get(str(dcc or spec.dcc or ""), {}))
        begin = hooks.get("begin")
        commit = hooks.get("commit")
        rollback = hooks.get("rollback")
        undo_token = None
        try:
            if callable(begin):
                undo_token = begin("TAK: %s" % name)
            value = spec.handler(clean)
            if callable(commit):
                commit(undo_token)
        except Exception as exc:
            if callable(rollback):
                try:
                    rollback(undo_token)
                except Exception:
                    pass
            self._record(name, clean, dry_run=False, confirm=True,
                         ok=False, committed=False, error=str(exc))
            return self._fail(name, "%s: %s（已回滚）" % (type(exc).__name__, exc))

        self._pending.pop(str(confirm_token), None)
        self._last_write[str(dcc or spec.dcc or "")] = {
            "action": name, "params": clean, "at": time.time(),
            "undo_token": undo_token,
        }
        self._record(name, clean, dry_run=False, confirm=True,
                     ok=True, committed=True)
        return {"ok": True, "action": name, "read_only": False,
                "status": "committed", "dry_run": False, "committed": True,
                "result": _jsonable(value), "preview": _jsonable(preview),
                "confirm_token": str(confirm_token)}

    def undo_last(self, dcc: str = "") -> Dict[str, Any]:
        last = self._last_write.get(str(dcc or ""))
        if not last:
            return {"ok": False, "error": "没有可撤销的 TAK 写入"}
        hooks = self._undo_hooks.get(str(dcc or ""), {})
        fn = hooks.get("undo_last")
        if not callable(fn):
            return {"ok": False, "error": "当前 DCC 未实现撤销"}
        try:
            fn(last.get("undo_token"))
        except Exception as exc:
            return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
        self._last_write.pop(str(dcc or ""), None)
        return {"ok": True, "undone": last["action"]}

    def audit(self, limit: int = 50) -> List[Dict[str, Any]]:
        return list(self._audit[-max(1, int(limit)):])

    # ------------------------------------------------------------ 内部
    def _new_token(self, name: str, params: Dict[str, Any],
                   preview: Dict[str, Any]) -> str:
        token = secrets.token_urlsafe(18)
        with self._lock:
            # 会话多了会攒 token，顺手清掉过期的。
            now = time.time()
            for old in [k for k, v in self._pending.items() if now - v.created_at > 300]:
                self._pending.pop(old, None)
            self._pending[token] = _Pending(name, copy.deepcopy(params), now,
                                            _jsonable(preview))
        return token

    def _record(self, name: str, params: Dict[str, Any], *, dry_run: bool,
                confirm: bool, ok: bool, committed: bool,
                error: str = "") -> None:
        with self._lock:
            self._audit.append({
                "seq": len(self._audit) + 1,
                "action": name,
                "params": copy.deepcopy(params),
                "dry_run": bool(dry_run),
                "confirm": bool(confirm),
                "ok": bool(ok),
                "committed": bool(committed),
                "error": error,
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            if len(self._audit) > 500:
                del self._audit[:100]

    def _fail(self, name: str, error: str) -> Dict[str, Any]:
        self._record(name, {}, dry_run=False, confirm=False,
                     ok=False, committed=False, error=error)
        return {"ok": False, "action": name, "error": error,
                "status": "error", "committed": False}


def _jsonable(value: Any) -> Any:
    """保证返回值能过 json.dumps（handler 万一返回了 Maya 的特殊类型）。"""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                     default=str))
    except (TypeError, ValueError):
        return {"repr": repr(value)[:1000]}


def _NOT_IMPLEMENTED(params: Dict[str, Any]) -> Any:  # pragma: no cover
    raise ActionNotImplemented("当前 DCC 未注册宿主实现")


def declare(name: str, description: str, *, params: Optional[Dict[str, ParamSpec]] = None,
            read_only: bool = True, permissions: Tuple[str, ...] = (READ_PERMISSION,),
            dcc: str = "", group: str = "scene",
            previewer: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
            no_undo_chunk: bool = False
            ) -> ActionSpec:
    """声明一个动作但先挂未实现 handler；适配器随后用 :func:`implement` 补上。

    这样「有哪些能力」是 core 的事实（MCP tools/list 在没有 Maya 的环境里
    也能列出完整能力表），而「怎么实现」是适配器的事。
    """
    spec = ActionSpec(name=name, description=description,
                      handler=_NOT_IMPLEMENTED,
                      permissions=tuple(permissions), params=dict(params or {}),
                      read_only=read_only, dcc=dcc, group=group,
                      previewer=previewer, no_undo_chunk=no_undo_chunk)
    REGISTRY.register(spec)
    return spec


def implement(name: str, handler: Callable[[Dict[str, Any]], Any],
              dcc: str = "", previewer: Optional[Callable[..., Any]] = None) -> None:
    """给已声明的动作装入宿主实现（可重复调用，后注册覆盖先注册）。"""
    spec = REGISTRY.get(name)
    if spec is None:
        raise ActionError("动作 %s 尚未声明" % name)
    spec.handler = handler
    # 能力表是 DCC 无关的（declare 时 dcc 为空）；这里不覆写 spec.dcc，
    # 否则 Max/MoBu 环境的 tools/list 会把能力全部过滤掉。哪个 DCC 真的
    # 有实现，由 register_handler / 执行时的报错如实反映。
    if previewer is not None:
        spec.previewer = previewer
    REGISTRY.register(spec)


REGISTRY = SceneActionRegistry()

# --------------------------------------------------------------------------- #
# 能力表：core 层声明，adapter 层实现。分类覆盖用户点名的四类诉求：
# 查询场景 / 查绑定逻辑 / 修改场景 / 创建资产。
# --------------------------------------------------------------------------- #
P_NODE = ParamSpec("string", "节点名（支持 Maya 全路径）", required=True)
P_TYPE = ParamSpec("string", "节点类型过滤，如 transform / joint / mesh / skinCluster")
P_PATTERN = ParamSpec("string", "名字通配符，如 *_ctrl / *:root")
P_LIMIT = ParamSpec("integer", "最多返回多少条", default=50, minimum=1, maximum=_MAX_ARRAY)

declare("scene.summary", "查询当前场景概况：文件名、帧范围、单位、节点计数、当前选择集",
        group="query")
declare("scene.list_nodes", "按类型/名字通配符列出场景节点",
        params={"type": P_TYPE, "pattern": P_PATTERN, "limit": P_LIMIT}, group="query")
declare("scene.find", "按关键字模糊查找节点名（不知道确切名字时先用这个）",
        params={"query": ParamSpec("string", "名字包含的片段", required=True),
                "type": P_TYPE, "limit": P_LIMIT}, group="query")
declare("scene.get_node", "查单个节点的类型、父子、变换、可见性与自定义属性",
        params={"node": P_NODE,
                "attributes": ParamSpec("array", "额外要读的属性名列表",
                                        items="string")}, group="query")
declare("scene.get_attr", "读取节点某个属性的当前值",
        params={"node": P_NODE,
                "attribute": ParamSpec("string", "属性名", required=True)},
        group="query")
declare("scene.frame_range", "查询并可选返回动画帧范围与时间单位", group="query")
declare("selection.get", "查询当前选中了哪些节点", group="query")

declare("rig.hierarchy", "查询骨架层级：给一个根关节，返回关节树与深度",
        params={"root": P_NODE,
                "max_depth": ParamSpec("integer", "最大展开深度", default=6,
                                       minimum=1, maximum=32)}, group="rig")
declare("rig.skin_info", "查询网格的蒙皮信息：skinCluster、影响关节、最大影响数、权重异常",
        params={"mesh": ParamSpec("string", "网格名或 transform 名", required=True),
                "limit": ParamSpec("integer", "影响关节最多列多少", default=40,
                                   minimum=1, maximum=_MAX_ARRAY)}, group="rig")
declare("rig.influences", "反查某个关节影响了哪些网格",
        params={"joint": P_NODE, "limit": P_LIMIT}, group="rig")
declare("rig.connections", "查询节点的连接关系（谁驱动了它 / 它驱动了谁）",
        params={"node": P_NODE,
                "direction": ParamSpec("string", "in / out / both", default="both",
                                       enum=("in", "out", "both"))}, group="rig")

declare("node.create", "创建资产：空组 / 定位器 / 关节 / 控制器圆圈 / 多边形立方体",
        params={"kind": ParamSpec("string", "创建什么", required=True,
                                  enum=("group", "locator", "joint", "control",
                                        "cube")),
                "name": ParamSpec("string", "名字（不含命名空间）", required=True),
                "parent": ParamSpec("string", "父节点名，空表示世界根"),
                "position": ParamSpec("array", "[x, y, z] 世界坐标",
                                      items="number")},
        read_only=False, permissions=(WRITE_PERMISSION,), group="write")
declare("node.rename", "重命名节点",
        params={"node": P_NODE,
                "name": ParamSpec("string", "新名字", required=True)},
        read_only=False, permissions=(WRITE_PERMISSION,), group="write")
declare("node.delete", "删除节点（会连带子节点）",
        params={"node": P_NODE},
        read_only=False, permissions=(WRITE_PERMISSION,), group="write")
declare("node.parent", "改变节点父子关系；parent 为空表示移到世界根",
        params={"node": P_NODE,
                "parent": ParamSpec("string", "新父节点，空表示解除父级")},
        read_only=False, permissions=(WRITE_PERMISSION,), group="write")
declare("attr.set", "设置节点属性（数值 / 布尔 / 字符串 / 三元组）",
        params={"node": P_NODE,
                "attribute": ParamSpec("string", "属性名", required=True),
                "value": ParamSpec("scalar", "新值", required=True),
                },
        read_only=False, permissions=(WRITE_PERMISSION,), group="write")
declare("selection.set", "设置当前选择集",
        params={"nodes": ParamSpec("array", "节点名列表", items="string",
                                   required=True)},
        read_only=False, permissions=(WRITE_PERMISSION,), group="write")
declare("scene.undo_tak", "撤销最近一次由 TAK 执行的写操作",
        read_only=False, permissions=(WRITE_PERMISSION,), group="write",
        no_undo_chunk=True)


def describe_actions(dcc: str = "") -> Dict[str, Any]:
    """给桥 / MCP / UI 的能力清单。"""
    items = REGISTRY.describe(dcc=dcc)
    return {"ok": True, "count": len(items), "actions": items,
            "read_only_count": sum(1 for item in items if item["read_only"]),
            "write_count": sum(1 for item in items if not item["read_only"])}


__all__ = [
    "READ_PERMISSION", "WRITE_PERMISSION", "REGISTRY", "ActionError",
    "ActionNotImplemented", "ActionSpec", "ParamSpec", "SceneActionRegistry",
    "declare", "implement", "describe_actions",
]
