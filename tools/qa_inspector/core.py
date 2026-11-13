# -*- coding: utf-8 -*-
"""场景检查：把所有「检查」能力收进一个工具，检查项可勾选、可保存。

为什么合并
----------
v0.3 之前在菜单和面板里平铺了三个独立入口（场景体检 / 规范校验 / 导出校验），
它们背后是同一个规则引擎、同一份快照、同一张结果表。用户点开任何一个看到
的都是「体检报告」，但没人说得清这三个到底差在哪 —— 这是反馈里最集中的问题。

现在只有一个「场景检查」：

* 检查项**全部来自规则注册表**，按提供者分组（基础规范 / 场景卫生 / 绑定规范 /
  IK 重定向 / 导出前置 / 导入后校验），不做第二套判定逻辑；
* 每组、每项都能勾选，勾选结果写进 ``~/.tak/config.json`` 的 ``check_state``；
* 只跑勾中的 ``rule_ids``，跑完仍是一份标准 ``Report``，与其它工具共用渲染层。

原来的 ``rig_health_guard`` / ``spec_enforcer`` / ``ik_retarget_guard`` /
``export_bridge`` / ``ue_import_guard`` 插件目录保留为**规则提供者**，
manifest 标了 ``"internal": true``，不再出现在工具列表里。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from tak.core import config as tak_config
from tak.core.rules import Severity, run_rules
from tak.core.rules.base import Finding

TOOL_ID = "qa_inspector"
TITLE = "场景检查"

#: 规则提供者：plugin_id -> (分组 id, 分组名, 一句话说明)。
#: 顺序决定 UI 里检查组的排列顺序（从最基础到最靠近导出）。
GROUP_META: Dict[str, Dict[str, str]] = {
    "builtin": {
        "id": "base", "name": "基础规范",
        "description": "关节命名、命名空间、镜像标记等通用规范",
    },
    "rig_health_guard": {
        "id": "scene", "name": "场景卫生",
        "description": "孤立节点、未冻结变换、负缩放、重复 shape、权重异常",
    },
    "spec_enforcer": {
        "id": "spec", "name": "绑定规范",
        "description": "控制器命名与颜色、关节层级、影响骨上限、命名空间作用域",
    },
    "ik_retarget_guard": {
        "id": "retarget", "name": "IK / 重定向",
        "description": "重定向前后骨骼链一致性、IK 手柄与约束残留",
    },
    "export_bridge": {
        "id": "export", "name": "导出前置",
        "description": "导出前的引用、材质、骨骼数量、命名合法性",
    },
    "ue_import_guard": {
        "id": "import", "name": "导入后校验",
        "description": "UE 侧资产命名、骨骼层级、材质槽与 LOD",
    },
}

#: 需要触发加载的插件 id（顺序即加载顺序，与 GROUP_META 一致）。
#: ``builtin`` 不是插件，它的规则在 ``tak.core.rules.engine`` 导入时已注册，
#: 不能走 ``find_plugin``，否则每次都会报「未发现插件 builtin」。
PROVIDER_PLUGINS = tuple(pid for pid in GROUP_META if pid != "builtin")

_SEVERITY_WEIGHT = {"blocker": 0, "error": 1, "warning": 2, "info": 3}
_GROUP_ORDER = {meta["id"]: index
                for index, meta in enumerate(GROUP_META.values())}
_PRIVILEGED = frozenset(("blocker", "error"))


def _severity_of(severity: Any) -> str:
    value = getattr(severity, "value", severity)
    text = str(value or "warning").lower()
    return text if text in _SEVERITY_WEIGHT else "warning"


def info() -> Dict[str, Any]:
    from tak.version import __version__
    return {
        "id": TOOL_ID, "name": TITLE, "version": __version__,
        "groups": [dict(meta) for meta in GROUP_META.values()],
        "providers": list(PROVIDER_PLUGINS),
    }


# --------------------------------------------------------------------------- #
# 规则加载
# --------------------------------------------------------------------------- #
_LOADED = False


def ensure_providers() -> Dict[str, str]:
    """加载所有规则提供者插件；返回 ``{plugin_id: 失败原因}``（空表示全部就绪）。

    加载是幂等的（``runner.load_tool_modules`` 内部按插件缓存并对规则注册去重），
    单个插件缺失或加载失败只记录原因，不影响其余检查项可用。
    """
    global _LOADED
    from tak.core import runner
    # 确保内置规则已注册（engine 导入即注册，这里只是显式声明依赖）。
    from tak.core.rules import builtin as _builtin  # noqa: F401

    failures: Dict[str, str] = {}
    for plugin_id in PROVIDER_PLUGINS:
        try:
            plugin = runner.find_plugin(plugin_id)
        except Exception as exc:
            failures[plugin_id] = "%s: %s" % (type(exc).__name__, exc)
            continue
        try:
            runner.load_tool_modules(plugin)
        except Exception as exc:
            failures[plugin_id] = "%s: %s" % (type(exc).__name__, exc)
    _LOADED = not failures
    return failures


def checks(dcc: str = "maya") -> List[Dict[str, Any]]:
    """返回全部检查项（每条规则一项），含分组、默认开关与严重级别。"""
    from tak.core.rules import list_rules

    ensure_providers()
    items: List[Dict[str, Any]] = []
    for entry in list_rules(dcc=dcc):
        plugin_id = str(entry.get("plugin_id") or "builtin")
        meta = GROUP_META.get(plugin_id, {
            "id": plugin_id, "name": plugin_id,
            "description": "由插件 %s 提供" % plugin_id,
        })
        items.append({
            "id": str(entry["id"]),
            "name": _check_name(entry),
            "group": meta["id"],
            "group_name": meta["name"],
            "description": str(entry.get("description") or ""),
            "severity": str(entry.get("severity") or "warning"),
            "fix_available": bool(entry.get("fix_available")),
            "plugin_id": plugin_id,
            "default_on": True,
        })
    items.sort(key=lambda c: (_GROUP_ORDER.get(c["group"], 99),
                              _SEVERITY_WEIGHT.get(c["severity"], 9),
                              c["id"]))
    return items


def _check_name(entry: Dict[str, Any]) -> str:
    """规则 id 取末段做可读名；description 里已有中文说明，二者互补。"""
    rule_id = str(entry.get("id") or "")
    tail = rule_id.split(".")[-1] if "." in rule_id else rule_id
    return tail.replace("_", " ")


def groups(dcc: str = "maya") -> List[Dict[str, Any]]:
    """按组聚合检查项，供 UI 渲染树形清单。"""
    buckets: Dict[str, Dict[str, Any]] = {}
    for item in checks(dcc=dcc):
        bucket = buckets.setdefault(item["group"], {
            "id": item["group"],
            "name": item["group_name"],
            "description": GROUP_META.get(item["plugin_id"], {}).get("description", ""),
            "checks": [],
        })
        bucket["checks"].append(item)
    return [buckets[key] for key in
            sorted(buckets, key=lambda k: _GROUP_ORDER.get(k, 99))]


# --------------------------------------------------------------------------- #
# 勾选状态
# --------------------------------------------------------------------------- #
def default_selection(dcc: str = "maya") -> List[str]:
    return [item["id"] for item in checks(dcc=dcc) if item["default_on"]]


def resolve_selection(dcc: str = "maya",
                      requested: Optional[Iterable[str]] = None) -> List[str]:
    """确定本次要跑哪些检查项。

    优先级：显式传入（UI 当前勾选）> 用户保存的勾选 > 默认全开。
    传入的 id 里不属于当前规则集的一律丢弃 —— 用户可能删过插件，
    残留 id 不该让运行报错，也不该悄悄多跑没勾的东西。
    """
    available = {item["id"] for item in checks(dcc=dcc)}
    if requested is not None:
        wanted = [str(item) for item in requested]
        return [item for item in wanted if item in available]
    saved = tak_config.check_state(TOOL_ID)
    if saved is not None:
        kept = [item for item in saved if item in available]
        if kept or not saved:
            return kept
    return sorted(available)


def save_selection(selected: Iterable[str]) -> Dict[str, Any]:
    return tak_config.set_check_state(TOOL_ID, list(selected))


# --------------------------------------------------------------------------- #
# 运行
# --------------------------------------------------------------------------- #
def run_report(ctx: Any) -> Any:
    """跑选中检查项，返回标准 Report。"""
    from tak.core.report import Report

    dcc = getattr(ctx, "dcc", "maya") or "maya"
    options = getattr(ctx, "options", None) or {}
    snapshot = getattr(ctx, "snapshot", None) or {}
    failures = ensure_providers()

    requested = options.get("checks")
    if isinstance(requested, str):
        requested = [requested]
    selected = resolve_selection(dcc=dcc, requested=requested)

    # 没有场景数据时不要跑规则：一条「缺少必需关节 calf_L」在空快照里是纯噪声，
    # 会让用户以为模型坏了。先确认数据源在不在，再决定跑不跑。
    if not _has_source(snapshot):
        report = Report(title=TITLE, dcc=dcc, findings=[Finding(
            rule_id=TOOL_ID + ".datasource.missing",
            severity=Severity.INFO,
            message="当前没有采集到场景数据，本次未执行任何检查。",
            fix_hint="确认在 DCC 内运行，且适配器已实现 get_skeleton_snapshot()。",
        )], spec_version=str((getattr(ctx, "spec", {}) or {}).get("version", "1.0.0")))
        report.metadata.update({"checks_selected": [], "checks_available": len(checks(dcc=dcc)),
                                "providers_failed": dict(failures)})
        return report

    report = run_rules(ctx.rule_context(), title=TITLE, rule_ids=selected,
                       dcc=dcc)
    report.findings = list(report.findings)

    # 「跳过」必须可见：什么都不勾就点运行，看到「通过」是最误导人的假成功。
    if not selected:
        report.findings.append(Finding(
            rule_id=TOOL_ID + ".nothing_selected",
            severity=Severity.INFO,
            message="没有勾选任何检查项，本次未执行检查。请在检查清单里至少选中一项。",
            fix_hint="勾选需要的检查项后重新运行；勾选会自动保存。",
        ))
    for plugin_id, reason in sorted(failures.items()):
        report.findings.append(Finding(
            rule_id=TOOL_ID + ".provider.missing",
            severity=Severity.INFO,
            message="规则提供者 %s 未能加载，相关检查项本次不可用：%s"
                    % (plugin_id, reason),
            fix_hint="确认该插件目录完整（core.py / rules.py / manifest.json）。",
            data={"plugin_id": plugin_id},
        ))

    report.metadata.update({
        "checks_selected": list(selected),
        "checks_available": len(checks(dcc=dcc)),
        "providers_failed": dict(failures),
    })
    return report


def _has_source(snapshot: Any) -> bool:
    """快照里是否存在**可用的**数据源。

    两个坑：

    1. 判据是「键存在」而不是「值非空」：``{"health": {}}`` 表示采集成功了但
       场景确实没问题，属于合法的空结果；``{}`` 才是根本没采到数据。
    2. 降级适配器会返回一份「长得像有数据」的骨架（``joints: {}`` +
       ``metadata.degraded``）。只看键会把「Maya 没装 / 不在宿主里」误判成
       「场景干净」，于是满屏都不会报错 —— 这是最坏的一种假通过。
    """
    if not isinstance(snapshot, dict) or not snapshot:
        return False
    meta = snapshot.get("metadata")
    if isinstance(meta, dict) and meta.get("degraded"):
        return False
    return any(key in snapshot for key in ("joints", "meshes", "skin", "health"))


def summarize(report: Any) -> Dict[str, Any]:
    """按组统计 findings，供 UI 画分组结果条。"""
    counts: Dict[str, int] = {}
    for finding in getattr(report, "findings", []) or []:
        group = GROUP_META.get(_plugin_of(finding.rule_id), {}).get("id", "other")
        counts[group] = counts.get(group, 0) + 1
    return counts


def _plugin_of(rule_id: str) -> str:
    prefix = str(rule_id or "").split(".")[0]
    return prefix if prefix in GROUP_META else "builtin"
