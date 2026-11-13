# -*- coding: utf-8 -*-
"""工具统一运行契约（批次 2 共享基线）。

UI（Qt / Web）、DCC 内菜单、commandlet、CI 只有一个入口：``run_tool``。
这样「同一份快照 → 同一套规则 → 同一份 Report」在四个 DCC 与离线测试中
完全可复现，插件作者不需要知道调用方是谁。

设计要点::

    adapter_for(dcc)          # 动态加载 tak.adapters.<dcc>，不可用降级 NullAdapter
    run_tool(id, snapshot)    # 发现插件 -> 加载模块 -> 组装上下文 -> 产出 Report

插件模块按 (tool_id, submodule) 缓存，避免 @rule / ACTION_REGISTRY 重复注册。
"""
from __future__ import annotations

import importlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Union

from tak.adapters.base import DCC, DCCAdapter, NullAdapter
from tak.core.registry import Plugin, discover, load_plugin_module
from tak.core.report import Report
from tak.core.rules.engine import RuleContext, run_rules
from tak.core.spec import load_spec

# 插件可选子模块：存在即加载，用于触发规则与 DSL 动作注册。
_OPTIONAL_SUBMODULES = ("rules", "actions")
_MODULE_CACHE: Dict[tuple, Any] = {}


class ToolError(RuntimeError):
    """工具发现、加载或执行失败。"""


class ToolNotFound(ToolError):
    """指定 id 的插件不存在。"""


@dataclass
class ToolContext:
    """传给插件 ``run`` / ``run_report`` 的唯一上下文对象。

    纯数据 + 一个可选 adapter：无 DCC 环境下 adapter 为 NullAdapter，
    因此插件代码在 CI 中可直接单测。
    """
    dcc: str = "maya"
    snapshot: Dict[str, Any] = field(default_factory=dict)
    spec: Dict[str, Any] = field(default_factory=dict)
    adapter: Optional[DCCAdapter] = None
    options: Dict[str, Any] = field(default_factory=dict)
    tool_id: str = ""
    plugin: Optional[Plugin] = None

    @property
    def adapter_or_null(self) -> DCCAdapter:
        return self.adapter if self.adapter is not None else NullAdapter()

    def rule_context(self) -> RuleContext:
        """转换成规则引擎的纯数据上下文。"""
        return RuleContext(snapshot=dict(self.snapshot or {}),
                           spec=dict(self.spec or {}),
                           dcc=self.dcc,
                           options=dict(self.options or {}))


@dataclass
class ToolResult:
    tool_id: str
    dcc: str
    report: Report
    duration_ms: float = 0.0
    plugin_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.report.passed

    def to_dict(self) -> Dict[str, Any]:
        data = self.report.to_dict()
        # 插件常把 AI 解释、规则统计等写进 report.metadata；此处必须合并而非覆盖，
        # 否则 Web API / JSON 报告会丢掉这些内容。
        merged = dict(self.report.metadata or {})
        merged.update(self.metadata or {})
        data.update({"tool_id": self.tool_id, "plugin_id": self.plugin_id,
                     "duration_ms": round(self.duration_ms, 2),
                     "metadata": merged})
        return data

    def render(self, fmt: str = "json") -> str:
        fmt = (fmt or "json").lower()
        if fmt == "json":
            import json
            return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        if fmt in ("md", "markdown"):
            return self.report.to_markdown()
        if fmt == "html":
            return self.report.to_html()
        raise ValueError("未知渲染格式: %s" % fmt)


def adapter_for(dcc: str, **kwargs: Any) -> DCCAdapter:
    """按 dcc 名动态加载 ``tak.adapters.<dcc>`` 的 ``create_adapter``。

    任何 ImportError / 无 GUI / 未安装宿主都降级为 NullAdapter，
    并把降级原因记录在 ``describe()["degraded"]``，绝不向调用方抛异常。
    """
    name = str(dcc or "maya").lower()
    try:
        module = importlib.import_module("tak.adapters.%s" % name)
        factory = getattr(module, "create_adapter", None)
        if factory is None:
            raise ImportError("tak.adapters.%s 未提供 create_adapter()" % name)
        adapter = factory(**kwargs)
        if not isinstance(adapter, DCCAdapter):
            raise TypeError("create_adapter() 返回值不是 DCCAdapter")
        return adapter
    except Exception as exc:
        null = NullAdapter(version=name)
        # 降级占位也要保留请求的 dcc 语义，否则报告会误标为 maya。
        try:
            null.dcc = DCC(name) if name in DCC._value2member_map_ else DCC.MAYA
        except Exception:
            pass
        null.data = {"degraded": "%s: %s" % (type(exc).__name__, exc)}
        return null


def find_plugin(tool_id: str, dcc: Optional[str] = None) -> Plugin:
    for plugin in discover(dcc=dcc):
        if plugin.id == tool_id:
            return plugin
    raise ToolNotFound("未发现工具插件 %r（dcc=%s）" % (tool_id, dcc or "*"))


def _rules_already_registered(plugin_id: str) -> bool:
    """全局 RULE_REGISTRY 中该插件的规则是否已存在。

    @rule 的注册是全局且不可撤销的；一旦模块缓存被清空，重新 exec
    rules.py 会让同一条规则出现两次，进而污染所有依赖全局规则集的
    报告与测试。这里用注册表本身做幂等判据，比模块缓存更可靠。
    """
    from tak.core.rules.base import RULE_REGISTRY
    return any(item.plugin_id == plugin_id for item in RULE_REGISTRY)


def load_tool_modules(plugin: Plugin) -> Any:
    """加载 core 与可选 rules / actions，带缓存且对规则注册幂等。"""
    key = (plugin.id, "core")
    if key not in _MODULE_CACHE:
        _MODULE_CACHE[key] = load_plugin_module(plugin, "core")
    for submodule in _OPTIONAL_SUBMODULES:
        sub_key = (plugin.id, submodule)
        if sub_key in _MODULE_CACHE:
            continue
        if submodule == "rules" and _rules_already_registered(plugin.id):
            # 规则已注册过（例如 clear_cache 之后），跳过二次 exec。
            _MODULE_CACHE[sub_key] = True
            continue
        try:
            _MODULE_CACHE[sub_key] = load_plugin_module(plugin, submodule)
        except (FileNotFoundError, ImportError):
            _MODULE_CACHE[sub_key] = None  # 该插件没有这个子模块
    return _MODULE_CACHE[key]


def list_tools(dcc: Optional[str] = None) -> List[Dict[str, Any]]:
    """面板用的工具清单（含 manifest 自描述与规则数）。"""
    return [plugin.to_dict() for plugin in discover(dcc=dcc)]


def run_tool(tool_id: str,
             snapshot: Optional[Union[Dict[str, Any], Any]] = None,
             dcc: str = "maya",
             adapter: Optional[DCCAdapter] = None,
             spec: Optional[Dict[str, Any]] = None,
             options: Optional[Dict[str, Any]] = None,
             rule_ids: Union[None, str, Sequence[str]] = None) -> ToolResult:
    """运行一个工具插件，返回统一的 ToolResult。

    - ``snapshot`` 可为 dict、``SkeletonSnapshot`` 或 None（由 adapter 取）。
    - 插件若实现 ``run_report(ctx)`` 优先使用；否则回退 ``run(ctx)``
      并把返回的 Finding 列表包装成 Report，兼容 P0 的模板插件。
    """
    started = time.time()
    plugin = find_plugin(tool_id)
    if dcc not in plugin.dcc:
        raise ToolError("工具 %s 不支持 DCC %s（支持: %s）"
                        % (tool_id, dcc, ", ".join(plugin.dcc)))

    host = adapter if adapter is not None else adapter_for(dcc)
    data = _as_dict(snapshot)
    if not data and hasattr(host, "get_skeleton_snapshot"):
        try:
            data = _as_dict(host.get_skeleton_snapshot()) or {}
        except Exception:
            data = {}
    effective_spec = dict(spec) if isinstance(spec, dict) else load_spec()
    ctx = ToolContext(dcc=dcc, snapshot=data, spec=effective_spec,
                      adapter=host, options=dict(options or {}),
                      tool_id=plugin.id, plugin=plugin)

    module = load_tool_modules(plugin)
    if hasattr(module, "run_report"):
        report = module.run_report(ctx)
    elif hasattr(module, "run"):
        report = _report_from_findings(module.run(ctx), ctx)
    else:
        raise ToolError("插件 %s 缺少 run_report(ctx) / run(ctx)" % tool_id)
    if not isinstance(report, Report):
        raise ToolError("插件 %s 返回值不是 Report" % tool_id)

    if rule_ids:
        report.findings = [f for f in report.findings if f.rule_id in set(
            [rule_ids] if isinstance(rule_ids, str) else list(rule_ids))]
    report.metadata.setdefault("tool_id", tool_id)
    report.metadata.setdefault("adapter", host.describe())

    return ToolResult(tool_id=tool_id, dcc=dcc, report=report,
                      duration_ms=(time.time() - started) * 1000.0,
                      plugin_id=plugin.id,
                      metadata={"rules_available": len(report.metadata.get("rule_ids", []))})


def evaluate(snapshot: Optional[Dict[str, Any]] = None,
             spec: Optional[Dict[str, Any]] = None,
             dcc: str = "maya",
             adapter: Optional[DCCAdapter] = None,
             title: str = "TAK 规则体检") -> Report:
    """不经过插件、直接跑全部内置规则（commandlet 与 Web API 的快速路径）。"""
    host = adapter if adapter is not None else adapter_for(dcc)
    data = _as_dict(snapshot) or {}
    if not data:
        try:
            data = _as_dict(host.get_skeleton_snapshot()) or {}
        except Exception:
            data = {}
    ctx = RuleContext(snapshot=data,
                      spec=dict(spec) if isinstance(spec, dict) else load_spec(),
                      dcc=dcc)
    report = run_rules(ctx, title=title)
    report.metadata.setdefault("adapter", host.describe())
    return report


def _report_from_findings(findings: Any, ctx: ToolContext) -> Report:
    from tak.core.rules.base import Finding
    items = [f for f in (findings or []) if isinstance(f, Finding)]
    return Report(title=ctx.tool_id or "TAK 工具", dcc=ctx.dcc, findings=items,
                  spec_version=str((ctx.spec or {}).get("version", "1.0.0")))


def _as_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "to_dict") and not isinstance(value, dict):
        return value.to_dict()
    return dict(value) if isinstance(value, dict) else {}


def clear_cache() -> None:
    """测试用：清空插件模块缓存（不改变已注册的全局规则）。"""
    _MODULE_CACHE.clear()
