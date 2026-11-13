# -*- coding: utf-8 -*-
"""第三方工具导入器：把「别人的一个脚本」变成工具面板里的一项。

用户的原话是「导入第三方工具到工具面板上，或者通过 AI 读取某个插件然后自动
安装到面板并记录插件功能与用法」。这个模块就是那句话的实现，分三步：

1. :func:`analyze` —— **只用 ast 解析，不执行任何一行目标代码**，读出：
   有哪些函数、哪些宿主调用（``cmds.setAttr`` 这类）、有没有 ``show()``、
   有没有 TAK 运行契约、模块 docstring 说了什么；
2. :func:`draft_manifest` —— 由分析结果生成一份 manifest 草稿：id / name /
   分类 / summary / features / usage / 输入输出 / 权限 / 支持的 DCC。
   :func:`enrich` 可以在有 AI 时把文案写得更像人话，**没有 AI 也能用**，
   并且逐字段标注哪些是 AI 写的；
3. :func:`install` —— 复制到 ``~/.tak/plugins/<id>/`` 并写入 ``core.py`` /
   ``ui_qt.py`` 两个薄壳（逻辑在 :mod:`tak.core.importer_runtime`），
   面板下次刷新就能看到并运行。

边界
----
* 导入**不执行**目标代码：``ast.parse`` 只读文本。执行发生在用户点运行时；
* 生成物里 ``target.py`` / ``source/`` 原样保留，便于审计与替换；
* ``core.py`` / ``ui_qt.py`` 是**生成的薄壳**，重装会覆盖 —— 文件头写明了这一点；
* 任何一步失败都返回 ``{"ok": False, "error": ...}``，不留半成品目录。
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import shutil
import time
from typing import Any, Dict, List, Optional, Tuple

from tak.core import registry

META_NAME = "import_meta.json"
MANIFEST_NAME = "manifest.json"
TARGET_NAME = "target.py"
SOURCE_DIR = "source"

# 导入保护：工具面板不是文件同步工具，超过这个量级说明用法不对。
_MAX_FILES = 400
_MAX_BYTES = 8 * 1024 * 1024
_SKIP_DIRS = {"__pycache__", ".git", ".svn", "node_modules", ".idea", ".vscode"}
_SKIP_SUFFIX = (".pyc", ".pyo", ".pyd", ".so", ".dll", ".log", ".tmp")

# 宿主调用 → 权限。只认「明显会改场景」的那批，宁可少标也不要给脚本多开权限。
_WRITE_CALLS = {
    "setAttr", "setKeyframe", "rename", "delete", "parent", "connectAttr",
    "disconnectAttr", "createNode", "group", "duplicate", "lockNode",
    "addAttr", "deleteAttr", "freeze", "makeIdentity", "xform", "move",
    "rotate", "scale", "select", "setDrivenKeyframe", "skinCluster",
}
_WEIGHT_CALLS = {"skinPercent", "normalizeWeights", "pruneWeights",
                 "skinCluster", "copySkinWeights", "exportWeights",
                 "importWeights", "moveWeights"}

_DCC_IMPORTS = {
    "maya": ("maya.cmds", "maya.mel", "maya.api", "maya.OpenMaya",
             "pymel", "maya.standalone", "maya.utils"),
    "max": ("pymxs", "MaxPlus", "maxscript"),
    "mobu": ("pyfbsdk", "pyfbsdk_additions"),
    "unreal": ("unreal",),
}
_ALL_DCC = ("maya", "max", "mobu", "unreal")

_ENTRY_NAMES = ("show", "main", "run", "run_report", "execute")


# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #
def import_root() -> str:
    return registry.user_plugin_dir()


def imported_dir(tool_id: str) -> str:
    return os.path.join(import_root(), tool_id)


def is_imported(tool_id: str) -> bool:
    return os.path.isfile(os.path.join(imported_dir(tool_id), META_NAME))


def list_imported() -> List[Dict[str, Any]]:
    """列出已导入的工具（按导入时间倒序）。"""
    root = import_root()
    if not os.path.isdir(root):
        return []
    items: List[Dict[str, Any]] = []
    for name in sorted(os.listdir(root)):
        directory = os.path.join(root, name)
        meta_path = os.path.join(directory, META_NAME)
        if not os.path.isfile(meta_path):
            continue
        try:
            with io.open(meta_path, "r", encoding="utf-8") as handle:
                meta = json.load(handle)
        except Exception:
            meta = {}
        items.append({
            "tool_id": str(meta.get("id") or name),
            "name": str(meta.get("name") or name),
            "source": str(meta.get("source") or ""),
            "imported_at": str(meta.get("imported_at") or ""),
            "has_show": bool(meta.get("has_show")),
            "has_report_contract": bool(meta.get("has_report_contract")),
            "dir": directory,
            "broken": not os.path.isfile(os.path.join(directory, MANIFEST_NAME)),
        })
    items.sort(key=lambda item: item.get("imported_at") or "", reverse=True)
    return items


# --------------------------------------------------------------------------- #
# 分析（只读文本，不执行）
# --------------------------------------------------------------------------- #
def _walk_files(root: str) -> Tuple[List[str], List[str]]:
    files: List[str] = []
    warnings: List[str] = []
    total = 0
    for base, dirs, names in os.walk(root):
        dirs[:] = [item for item in dirs if item not in _SKIP_DIRS]
        for name in names:
            if name.lower().endswith(_SKIP_SUFFIX):
                continue
            path = os.path.join(base, name)
            try:
                total += os.path.getsize(path)
            except OSError:
                continue
            files.append(path)
            if len(files) > _MAX_FILES:
                warnings.append("文件数超过 %d，只分析前 %d 个" % (_MAX_FILES, _MAX_FILES))
                return files[:_MAX_FILES], warnings
            if total > _MAX_BYTES:
                warnings.append("总大小超过 %d MB，只分析前面的文件"
                                % (_MAX_BYTES // (1024 * 1024)))
                return files, warnings
    return files, warnings


def _parse_file(path: str) -> Dict[str, Any]:
    """解析单个 .py；语法错误不抛，记进 errors 让用户知道为什么读不出信息。"""
    info: Dict[str, Any] = {"path": path, "docstring": "", "functions": [],
                            "classes": [], "imports": [], "host_calls": [],
                            "error": ""}
    try:
        raw = io.open(path, "r", encoding="utf-8", errors="replace").read()
    except OSError as exc:
        info["error"] = "读取失败: %s" % exc
        return info
    try:
        tree = ast.parse(raw, filename=path)
    except SyntaxError as exc:
        info["error"] = "语法错误: %s（第 %s 行）" % (exc.msg, exc.lineno)
        return info

    info["docstring"] = (ast.get_docstring(tree) or "").strip()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            info["functions"].append({
                "name": node.name,
                "doc": (ast.get_docstring(node) or "").strip(),
                "args": [arg.arg for arg in node.args.args],
            })
        elif isinstance(node, ast.ClassDef):
            info["classes"].append({"name": node.name,
                                    "doc": (ast.get_docstring(node) or "").strip()})
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                info["imports"].append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                info["imports"].append(node.module)
        elif isinstance(node, ast.Call):
            func = node.func
            # 只认「模块.函数」这一种形态。早期实现把裸调用名也算进来，结果
            # 脚本自己的 `cleanup()` / `print()` 全被当成宿主调用，权限推断跟着
            # 失真（一个纯 print 的脚本被判成会改场景）。
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id in ("cmds", "pymxs", "fb", "unreal",
                                     "maya", "mel", "rt"):
                    info["host_calls"].append(func.attr)
    info["imports"] = sorted(set(info["imports"]))
    info["host_calls"] = sorted(set(info["host_calls"]))
    return info


def analyze(source: str) -> Dict[str, Any]:
    """只看不跑：给出一份「这个脚本是什么、能干什么、会改什么」的分析。"""
    result: Dict[str, Any] = {
        "ok": False, "kind": "", "source": os.path.abspath(source),
        "files": [], "entry": "", "docstring": "", "functions": [],
        "classes": [], "imports": [], "host_calls": [], "dcc": [],
        "has_show": False, "has_report_contract": False, "entrypoints": [],
        "permissions": [], "warnings": [], "errors": [],
    }
    if not os.path.exists(source):
        result["errors"].append("路径不存在: %s" % source)
        return result

    if os.path.isfile(source):
        if not source.lower().endswith(".py"):
            result["errors"].append("目前只支持导入 .py 脚本或含 .py 的目录")
            return result
        result["kind"] = "file"
        files = [source]
    else:
        result["kind"] = "dir"
        files, warnings = _walk_files(source)
        result["warnings"].extend(warnings)

    parsed = []
    for path in files:
        if not path.lower().endswith(".py"):
            continue
        info = _parse_file(path)
        if info["error"]:
            result["errors"].append("%s：%s" % (os.path.basename(path), info["error"]))
            continue
        parsed.append(info)
    if not parsed:
        result["errors"].append("目录里没有可解析的 .py 文件")
        return result

    result["files"] = [os.path.relpath(item["path"], os.path.dirname(source)
                                       if os.path.isfile(source) else source)
                       for item in parsed]
    # 入口优先级：有 show 的 > 有 run/run_report 的 > main > __init__ > 第一个
    def _score(info: Dict[str, Any]) -> Tuple[int, int, str]:
        names = {item["name"] for item in info["functions"]}
        score = 0
        if "show" in names:
            score += 8
        if names & {"run_report", "run"}:
            score += 4
        if "main" in names:
            score += 2
        if os.path.basename(info["path"]) == "__init__.py":
            score += 1
        return (-score, 0, info["path"])

    parsed.sort(key=_score)
    primary = parsed[0]
    result["entry"] = primary["path"]
    result["docstring"] = primary["docstring"]
    for info in parsed:
        result["functions"].extend(info["functions"])
        result["classes"].extend(info["classes"])
        result["imports"].extend(info["imports"])
        result["host_calls"].extend(info["host_calls"])
    result["imports"] = sorted(set(result["imports"]))
    result["host_calls"] = sorted(set(result["host_calls"]))

    names = {item["name"] for item in result["functions"]}
    result["has_show"] = "show" in names
    result["has_report_contract"] = bool(names & {"run_report", "run"})
    result["entrypoints"] = [name for name in _ENTRY_NAMES if name in names]

    result["dcc"] = _guess_dcc(result["imports"], result["host_calls"],
                               result["warnings"])
    result["permissions"] = _guess_permissions(result["host_calls"])
    result["ok"] = True
    return result


def _guess_dcc(imports: List[str], host_calls: List[str],
               warnings: List[str]) -> List[str]:
    found: List[str] = []
    lowered = [item.lower() for item in imports]
    for dcc, markers in _DCC_IMPORTS.items():
        for marker in markers:
            key = marker.lower()
            if any(item == key or item.startswith(key + ".") or key in item
                   for item in lowered):
                found.append(dcc)
                break
    if not found:
        if host_calls:
            # 出现 cmds / mel 这类调用但没 import，最可能是 Maya 的 old-style 脚本
            found = ["maya"]
            warnings.append("没有识别到宿主库，但出现了 cmds/mel 风格调用，"
                            "已按 Maya 处理；不是 Maya 脚本请在 manifest 里改 dcc")
        else:
            found = list(_ALL_DCC)
            warnings.append("没有识别到宿主依赖，已对所有 DCC 开放")
    return found


def _guess_permissions(host_calls: List[str]) -> List[str]:
    permissions = ["scene.read"]
    calls = set(host_calls)
    if calls & _WRITE_CALLS:
        permissions.append("node.write")
    if calls & _WEIGHT_CALLS:
        permissions.append("weight.write")
    return permissions


# --------------------------------------------------------------------------- #
# manifest 草稿
# --------------------------------------------------------------------------- #
def _slug(text: str) -> str:
    value = re.sub(r"[^0-9a-zA-Z_]+", "_", str(text or "").strip()).strip("_")
    value = re.sub(r"_+", "_", value).lower()
    if not value:
        value = "imported_tool"
    if value[0].isdigit():
        value = "tool_" + value
    return value[:48]


def _humanize(name: str) -> str:
    base = os.path.splitext(os.path.basename(name.rstrip("/\\")))[0]
    words = re.split(r"[_\-\s]+", base)
    pretty = " ".join(word[:1].upper() + word[1:] for word in words if word)
    return pretty or base or "Imported Tool"


def _first_paragraph(text: str) -> str:
    for block in (text or "").split("\n\n"):
        cleaned = " ".join(line.strip() for line in block.splitlines()).strip()
        if cleaned:
            return cleaned
    return ""


def _bullets(text: str, limit: int = 6) -> List[str]:
    """从 docstring 里抽 ``- xxx`` / ``* xxx`` / ``1. xxx`` 形式的条目。"""
    items: List[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        match = re.match(r"^(?:[-*•]|\d+[.)])\s+(.+)$", stripped)
        if match:
            items.append(match.group(1).strip())
    return items[:limit]


def draft_manifest(analysis: Dict[str, Any], tool_id: Optional[str] = None,
                   name: Optional[str] = None) -> Dict[str, Any]:
    """由分析结果生成 manifest 草稿；字段全部可被用户在安装前改掉。"""
    if not analysis.get("ok"):
        return {}
    entry_path = str(analysis.get("entry") or "")
    derived_id = _slug(tool_id or os.path.splitext(os.path.basename(entry_path))[0])
    doc = str(analysis.get("docstring") or "")
    features = _bullets(doc)
    if not features:
        # 没有条目就用函数 docstring 的第一句兜底 —— 比空列表有用得多。
        for item in analysis.get("functions") or []:
            text = _first_paragraph(str(item.get("doc") or ""))
            if text:
                features.append("%s()：%s" % (item["name"], text))
            if len(features) >= 6:
                break

    usage: List[str] = []
    if analysis.get("has_show"):
        usage.append("在面板里选中本工具，点「打开工具界面」使用它自己的窗口")
    if analysis.get("has_report_contract"):
        usage.append("点「快速运行」把结果并入结果表（脚本自带 run/run_report）")
    if not usage:
        usage.append("点「快速运行」按原样执行脚本；它没有 TAK 运行契约，"
                     "不会产出判定结论")
        if analysis.get("entrypoints"):
            usage.append("脚本入口：%s" % "、".join(analysis["entrypoints"]))
    usage.append("需要调整分类/描述时，在「设置 → 工具归类」里改，不用重装")

    outputs = ["执行输出（stdout / 报错原样回报）"]
    if analysis.get("has_report_contract"):
        outputs.insert(0, "检查报告（脚本自带的 run / run_report 产出）")

    manifest: Dict[str, Any] = {
        "id": derived_id,
        "name": name or _humanize(entry_path or analysis.get("source", "")),
        "category": ["general"],
        "summary": _first_paragraph(doc) or "导入的第三方工具（未提供说明）",
        "description": doc or ("由 TAK 导入器从 %s 导入；脚本本身没有写 docstring，"
                               "说明字段需要补。" % analysis.get("source")),
        "features": features,
        "usage": usage,
        "inputs": ["当前场景（脚本自行访问宿主 API）"] if analysis.get("host_calls")
                  else ["用户的交互输入"],
        "outputs": outputs,
        "tags": ["导入", "第三方"],
        "dcc": list(analysis.get("dcc") or _ALL_DCC),
        "min_version": {dcc: "2023" if dcc != "unreal" else "5.8"
                        for dcc in (analysis.get("dcc") or _ALL_DCC)},
        "entry": derived_id,
        "ui": "qt" if analysis.get("has_show") else "none",
        "permissions": list(analysis.get("permissions") or ["scene.read"]),
        "imported": True,
        "origin": {"source": analysis.get("source", ""),
                   "kind": analysis.get("kind", ""),
                   "entrypoints": list(analysis.get("entrypoints") or [])},
    }
    return manifest


# --------------------------------------------------------------------------- #
# AI 补全（可选、可降级、逐字段标注）
# --------------------------------------------------------------------------- #
_AI_SCHEMA_HINT = (
    '只输出 JSON：{"summary": "一句话（≤40字）", '
    '"features": ["能力点", "..."], "usage": ["操作步骤", "..."], '
    '"tags": ["短标签"]}。summary 说清「做什么」，features 说清「能干什么」，'
    "usage 是给使用者看的操作顺序。不要写营销词。"
)


def enrich(manifest: Dict[str, Any], analysis: Dict[str, Any],
           provider: Any = None) -> Dict[str, Any]:
    """用 AI 把 summary / features / usage 写得更像人话。

    没有 provider 或调用失败时**原样返回草稿**，只把 ``ai`` 标成 False ——
    导入流程不因为「AI 不可用」而中断，这是内置工具的既有约定。
    """
    result = {"ok": False, "manifest": dict(manifest), "ai": False,
              "fields": [], "note": "", "error": ""}
    if not manifest:
        result["error"] = "manifest 草稿为空"
        return result
    if provider is None:
        try:
            from tak.core.ai import get_provider
            provider = get_provider(task="explain")
        except Exception as exc:
            result["note"] = "AI 不可用（%s），已保留确定性草稿" % type(exc).__name__
            return result
    if provider is None:
        result["note"] = "AI 不可用，已保留确定性草稿"
        return result

    prompt = _ai_prompt(manifest, analysis)
    try:
        raw = provider.chat([
            {"role": "system", "content": _AI_SCHEMA_HINT},
            {"role": "user", "content": prompt},
        ])
    except Exception as exc:
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        result["note"] = "AI 调用失败，已保留确定性草稿"
        return result

    data = _extract_json(raw)
    if not data:
        result["note"] = "AI 返回的不是可解析的 JSON，已保留确定性草稿"
        return result

    updated = dict(manifest)
    fields: List[str] = []
    summary = str(data.get("summary") or "").strip()
    if summary and len(summary) <= 60:
        updated["summary"] = summary
        fields.append("summary")
    for key in ("features", "usage"):
        values = [str(item).strip() for item in (data.get(key) or [])
                  if str(item).strip()]
        if values:
            updated[key] = values[:8]
            fields.append(key)
    tags = [str(item).strip() for item in (data.get("tags") or [])
            if str(item).strip()]
    if tags:
        updated["tags"] = sorted(set(list(manifest.get("tags") or []) + tags))[:10]
        fields.append("tags")

    if not fields:
        result["note"] = "AI 没有给出可用字段，已保留确定性草稿"
        return result
    updated["ai_fields"] = fields
    result.update({"ok": True, "manifest": updated, "ai": True, "fields": fields,
                   "note": "AI 已补全 %d 个字段：%s" % (len(fields), "、".join(fields))})
    return result


def _ai_prompt(manifest: Dict[str, Any], analysis: Dict[str, Any]) -> str:
    functions = [item.get("name") for item in (analysis.get("functions") or [])[:20]]
    lines = [
        "这是一个要导入 DCC 工具面板的第三方脚本，请补全工具说明。",
        "脚本名：%s" % manifest.get("name"),
        "宿主：%s" % ", ".join(manifest.get("dcc") or []),
        "模块 docstring：",
        (analysis.get("docstring") or "（无）")[:1500],
        "函数：%s" % ", ".join(str(item) for item in functions),
        "用到的宿主调用：%s" % ", ".join((analysis.get("host_calls") or [])[:30]),
        "推断的权限：%s" % ", ".join(manifest.get("permissions") or []),
        "现有草稿 summary：%s" % manifest.get("summary"),
        "现有草稿 features：%s" % "；".join(manifest.get("features") or []),
    ]
    return "\n".join(lines)


def _extract_json(raw: Any) -> Dict[str, Any]:
    """从模型输出里挖出第一个 JSON 对象；挖不到返回空字典。"""
    text = str(raw or "")
    start = text.find("{")
    while start != -1:
        depth = 0
        for index in range(start, len(text)):
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(text[start:index + 1])
                    except Exception:
                        break
                    return data if isinstance(data, dict) else {}
        start = text.find("{", start + 1)
    return {}


# --------------------------------------------------------------------------- #
# 安装 / 卸载
# --------------------------------------------------------------------------- #
_CORE_SHIM = '''# -*- coding: utf-8 -*-
"""由 TAK 导入器生成，重装会被覆盖 —— 请不要手改这个文件。

真正的运行逻辑在 tak.core.importer_runtime；
原脚本在 {target}，改脚本请改那里。
"""
from __future__ import annotations

import os

from tak.core import importer_runtime

_HERE = os.path.dirname(os.path.abspath(__file__))


def run_report(ctx):
    return importer_runtime.run_report(ctx, _HERE)


def capabilities():
    return importer_runtime.capabilities(_HERE)
'''

_UI_SHIM = '''# -*- coding: utf-8 -*-
"""由 TAK 导入器生成，重装会被覆盖 —— 请不要手改这个文件。

转调原脚本的 show()/main() 打开它自己的界面。
"""
from __future__ import annotations

import os

from tak.core import importer_runtime

_HERE = os.path.dirname(os.path.abspath(__file__))


def show(dcc="maya", omui=None):
    return importer_runtime.show(_HERE, dcc=dcc, omui=omui)
'''


def _copy_tree(source_dir: str, target_dir: str) -> int:
    count = 0
    for base, dirs, names in os.walk(source_dir):
        dirs[:] = [item for item in dirs if item not in _SKIP_DIRS]
        relative = os.path.relpath(base, source_dir)
        destination = target_dir if relative == "." else os.path.join(target_dir,
                                                                      relative)
        if not os.path.isdir(destination):
            os.makedirs(destination)
        for name in names:
            if name.lower().endswith(_SKIP_SUFFIX):
                continue
            src = os.path.join(base, name)
            dst = os.path.join(destination, name)
            try:
                shutil.copy2(src, dst)
                count += 1
            except OSError:
                continue
    return count


def install(source: str, manifest: Optional[Dict[str, Any]] = None,
            analysis: Optional[Dict[str, Any]] = None,
            overwrite: bool = False,
            ai_fields: Optional[List[str]] = None) -> Dict[str, Any]:
    """把脚本装进 ``~/.tak/plugins/<id>/``。返回安装结果与最终 manifest。"""
    info = analysis if isinstance(analysis, dict) and analysis.get("ok") \
        else analyze(source)
    if not info.get("ok"):
        return {"ok": False, "error": "分析失败：%s" % "；".join(info.get("errors") or [])}

    draft = manifest if isinstance(manifest, dict) and manifest else \
        draft_manifest(info)
    if not draft:
        return {"ok": False, "error": "无法生成 manifest 草稿"}

    tool_id = _slug(draft.get("id"))
    draft["id"] = tool_id
    draft["entry"] = tool_id
    draft.setdefault("imported", True)

    target_dir = imported_dir(tool_id)
    if os.path.isdir(target_dir) and not overwrite:
        return {"ok": False,
                "error": "已存在同名导入工具 %s；如要替换请勾选覆盖" % tool_id,
                "tool_id": tool_id, "dir": target_dir}

    staging = target_dir + ".installing"
    if os.path.isdir(staging):
        shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)

    try:
        # 1) 放原脚本
        if info.get("kind") == "file":
            shutil.copy2(source, os.path.join(staging, TARGET_NAME))
            relative_target = TARGET_NAME
            copy_count = 1
        else:
            copy_count = _copy_tree(source, os.path.join(staging, SOURCE_DIR))
            relative_target = os.path.relpath(info["entry"], source)
            relative_target = os.path.join(SOURCE_DIR, relative_target)
        # 2) 薄壳
        with io.open(os.path.join(staging, "core.py"), "w",
                     encoding="utf-8", newline="\n") as handle:
            handle.write(_CORE_SHIM.format(target=relative_target))
        if info.get("has_show"):
            with io.open(os.path.join(staging, "ui_qt.py"), "w",
                         encoding="utf-8", newline="\n") as handle:
                handle.write(_UI_SHIM)
        ui_path = os.path.join(staging, "ui_qt.py")
        if not os.path.isfile(ui_path):
            draft["ui"] = "none"
        else:
            draft["ui"] = draft.get("ui") if draft.get("ui") in ("qt", "both") else "qt"

        # 3) manifest 与导入元信息
        with io.open(os.path.join(staging, MANIFEST_NAME), "w",
                     encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(draft, ensure_ascii=False, indent=2))
        meta = {
            "id": tool_id,
            "name": draft.get("name") or tool_id,
            "source": os.path.abspath(source),
            "kind": info.get("kind"),
            "target": relative_target,
            "entrypoints": list(info.get("entrypoints") or []),
            "has_show": bool(info.get("has_show")),
            "has_report_contract": bool(info.get("has_report_contract")),
            "permissions": list(draft.get("permissions") or []),
            "dcc": list(draft.get("dcc") or []),
            "ai_fields": list(ai_fields or draft.get("ai_fields") or []),
            "imported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "file_count": copy_count,
            "warnings": list(info.get("warnings") or []),
            "errors": list(info.get("errors") or []),
        }
        with io.open(os.path.join(staging, META_NAME), "w",
                     encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(meta, ensure_ascii=False, indent=2))

        # 4) 校验这份 manifest 能被 registry 接受，再原子换上去
        try:
            registry.load_manifest(os.path.join(staging, MANIFEST_NAME))
        except Exception as exc:
            shutil.rmtree(staging, ignore_errors=True)
            return {"ok": False, "error": "生成的 manifest 不合法：%s" % exc,
                    "tool_id": tool_id}

        if os.path.isdir(target_dir):
            shutil.rmtree(target_dir, ignore_errors=True)
        os.replace(staging, target_dir)
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc),
                "tool_id": tool_id}

    return {"ok": True, "tool_id": tool_id, "dir": target_dir,
            "manifest": draft, "meta": meta,
            "warnings": list(info.get("warnings") or []),
            "ai_fields": list(meta["ai_fields"])}


def uninstall(tool_id: str) -> Dict[str, Any]:
    """删除一个导入工具（先校验它确实是导入的，别误删内置插件）。"""
    slug = _slug(tool_id)
    target_dir = imported_dir(slug)
    if not os.path.isdir(target_dir):
        return {"ok": False, "error": "没有找到导入工具 %s" % slug, "tool_id": slug}
    if not os.path.isfile(os.path.join(target_dir, META_NAME)):
        return {"ok": False, "tool_id": slug,
                "error": "%s 不是导入的工具（缺少 %s），拒绝删除"
                         % (target_dir, META_NAME)}
    try:
        shutil.rmtree(target_dir)
    except OSError as exc:
        return {"ok": False, "tool_id": slug, "error": "删除失败: %s" % exc}
    return {"ok": True, "tool_id": slug, "removed": True, "dir": target_dir}
