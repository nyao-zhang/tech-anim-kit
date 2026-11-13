# 插件开发指南

> 后续会持续补充大量工具。本指南的目标：**新增任何工具都不需要修改框架代码。**

## 1. 一键创建

```bash
python tools/new_tool.py rig_health_guard --name "场景体检" --dcc maya,max,mobu,unreal
python tools/new_tool.py --list
```

生成 `tools/rig_health_guard/`，立即被 `tak.core.registry.discover()` 发现，并自带可跑单测：

```bash
python -m pytest tools/rig_health_guard/test_core.py -q
```

外部插件目录（不改仓库也能扩展）：设置环境变量 `TAK_PLUGIN_PATH`，多个目录用 `os.pathsep` 分隔。以下划线开头的目录（如 `_template`）会被跳过。

## 2. 目录结构

```
tools/<id>/
├── manifest.json   # 必需：插件声明
├── core.py         # 必需：编排逻辑，零 DCC / 零 UI 依赖
├── rules.py        # 推荐：确定性规则，导入时自动注册
├── actions.py      # 可选：需要改写场景的 DSL 动作
├── ui_qt.py        # 可选：Qt 界面（无 Qt 时自动降级）
├── ui_web.py       # 可选：UE 侧 Web 面板
└── test_core.py    # 推荐：自带单测，CI 自动收集
```

框架按**文件路径**加载模块（`load_plugin_module`），插件目录不必是 Python 包，也无需 `__init__.py`；模块名带插件 id 前缀，多个插件同名文件不会冲突。

## 3. manifest.json 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 必填，小写字母/数字/下划线，且以字母开头；全局唯一 |
| `name` | string | 必填，显示名称 |
| `dcc` | list | 必填，`maya` / `max` / `mobu` / `unreal` 子集 |
| `min_version` | object | 必填，按 DCC 声明最低版本，如 `{"maya":"2023","unreal":"5.8"}` |
| `entry` | string | 必填，入口名（一般与 id 相同） |
| `ui` | string | `qt` / `web` / `both` / `none`，默认 `qt` |
| `permissions` | list | 权限声明，如 `scene.read`、`node.write`、`export.fbx` |
| `description` | string | 面板说明 |

坏 manifest 不会阻断其他插件，`discover()` 会跳过它。

## 4. 写规则（确定性判定）

```python
from tak.core.rules import Finding, Severity, rule

@rule("my_tool.no_negative_scale", severity=Severity.ERROR,
      description="骨骼不允许负缩放", plugin_id="my_tool")
def no_negative_scale(ctx):
    findings = []
    for name, data in (ctx.snapshot or {}).get("joints", {}).items():
        if any(v < 0 for v in data.get("scale", [])):
            findings.append(Finding(
                rule_id="my_tool.no_negative_scale",
                severity=Severity.ERROR,
                message="骨骼存在负缩放",
                node=name,
                fix_hint="在 DCC 中冻结变换或修正镜像",
            ))
    return findings
```

- 规则必须是纯函数，只接收 `RuleContext`（`spec` / `adapter` / `snapshot` / `options`）。
- **能写 `if` 的判定一律放这里**，不要交给 AI；AI 只负责解释报错、生成候选修复、打标与诊断。
- `rule()` 的 `dcc` 参数可限定规则适用宿主；`fix_available=True` 表示后续会提供自动修复。

## 5. 写动作（需要改写场景）

```python
from tak.core.dsl import ACTION_REGISTRY

def rename_node(adapter, old, new):
    # 通过 adapter 调用宿主，不直接 import maya / unreal
    ...

ACTION_REGISTRY.register("my_tool.rename_node", rename_node,
                         permissions=["node.write"], description="重命名节点")
```

执行链固定为：白名单校验 → **dry-run diff 预览** → 权限校验 → 人工 `confirm=True` → `begin_undo` → 逐条执行 → 任一失败 **整体 rollback**。LLM 只能产出：

```json
{"actions": [{"tool": "my_tool.rename_node", "params": {"old": "a", "new": "b"}}]}
```

绝不允许模型自由生成 DCC 脚本。

## 6. 界面

- Qt 端：`from tak.compat.qt import QtWidgets, apply_theme, host_main_window`；**不要**自己写 PySide2/PySide6 版本判断。QSS 用 `tak/ui/theme.qss`（浅色为 `theme_light.qss`）。
- Web 端：`tak/ui/theme.css`，根节点加 `data-theme="light"` 切浅色。
- 颜色、圆角、间距只改 `tak/ui/tokens.json`，然后 `python tak/ui/generate_theme.py` 重新生成；**禁止手改生成物**。

## 7. 报告

工具返回 `Finding` 列表，交给 `tak.core.report.Report` 渲染 JSON / Markdown / HTML。三种渲染共用同一份 `Finding`，HTML 复用主题 CSS，因此跨 DCC 报告外观一致、可 diff。

## 8. 提交前检查

```bash
python -m pytest -q
python tak/ui/generate_theme.py --check
python fixtures/generate_fixtures.py --check
python tools/new_tool.py --list
```

新工具若引入新的规则类别，请在 `fixtures/generate_fixtures.py` 增加对应的坏场景注入，保证回归覆盖。

## 9. 批次 2：统一运行契约（新增工具必读）

P2/P3 起，UI、DCC 菜单、commandlet、CI 一律通过 `tak.core.runner` 调用工具，插件作者不需要知道调用方是谁：

```python
from tak.core.runner import run_tool, evaluate, list_tools

result = run_tool("my_tool", snapshot, dcc="maya", adapter=None, options={})
print(result.passed, result.render("md"))
```

- `core.py` 优先实现 `run_report(ctx) -> tak.core.report.Report`；也兼容模板的 `run(ctx) -> List[Finding]`（runner 自动包装成 Report）。
- `ctx` 是 `ToolContext`：`dcc` / `snapshot` / `spec` / `adapter` / `options` / `tool_id` / `plugin`；`ctx.rule_context()` 返回规则引擎上下文，`ctx.adapter_or_null` 保证无宿主时也有可用 adapter。
- runner 按**文件路径**加载 `core.py`，并自动加载 `rules.py` / `actions.py`（若存在）来触发规则与 DSL 动作注册，且带缓存避免重复注册。因此**插件内禁止 `import rules` / 相对导入**。
- 规则必须兼容 `tak.core.rules.engine.RuleContext`（用 `getattr` 取 `snapshot` / `spec`，见 `builtin.py`），并显式传 `plugin_id=TOOL_ID`。
- **回归硬约束**：`tests/test_builtin_rules.py` 会对 40 个 golden fixtures 跑「全部已注册规则」。你的规则在专属字段缺失时必须整组返回 `[]`，且用 `dcc` 限定适用宿主，否则会污染别人的回归结果。
- 需要「内置规则 + 插件规则」一起跑时，参考 `rig_health_guard` 的 `options["include_builtin"]` 做法：默认隔离便于单测，UI/钩子传 `True` 开完整体检。
- 插件自带 `test_core.py` 需**显式运行**（`python -m pytest tools/<id>/test_core.py -q`）：多个插件同名文件在 pytest 默认 import 模式下会冲突，且插件自测与 `tests/` 主回归共用全局 `RULE_REGISTRY`，混跑会造成状态耦合。CI 的主回归入口固定为 `tests/`。

仓库内已有的九个插件（`rig_health_guard` / `spec_enforcer` / `export_bridge` / `ue_import_guard` / `ik_retarget_guard` / `agent_console` / `skeleton_mapper` / `defect_inspector` / `rig_kb`）就是完整范例，新工具建议直接复制其中一个的结构。纯规则型插件抄 `rig_health_guard`，AI 型插件抄 `skeleton_mapper`（只读候选）或 `agent_console`（受控写入）。

## 10. 批次 3：AI 工具插件与四端适配器约定

批次 3 新增四个插件：`agent_console`（对话候选）、`skeleton_mapper`（骨骼语义映射）、`defect_inspector`（VLM 抽帧诊断）、`rig_kb`（绑定知识库 RAG）。它们与批次 2 插件共享 `run_report(ctx)` 契约，但有几条额外约定：

1. **不注册全局 `@rule`**：这四个工具的判定是「工具内部的一次性分析」，不是可复用的场景规范。若注册进全局 `RULE_REGISTRY`，40 个 golden fixtures 的「全部已注册规则」回归会被污染。因此判定写成 `tak/core/<domain>/` 里的纯函数，插件 `core.py` 直接构造 `Finding` 与 `Report`。
2. **AI 只产出候选**：`tak.core.ai.providers.get_provider()` 默认离线；真实模型返回的结构化 JSON 必须解析后经确定性校验（帧号/类别/骨骼白名单、映射目标存在性、DSL 动作白名单）才能进入结果。解析失败一律降级并标注 `source`，绝不伪称模型判定。
3. **写入唯一入口仍是 DSL**：`agent_console` 的 `chat()` 绝不写场景；`confirm()` 默认 dry-run，必须 `confirm=True` + `dry_run=False` + 授权 `node.write` 才调用 `ACTION_REGISTRY.execute`。Web 服务端在 `/api/agent` 又收窄了一次权限白名单。
4. **可复现**：不写时间戳、不写随机数、排序键固定；`knowledge/` 的检索是本地倒排索引，不联网、不写缓存。
5. **UI 片段契约**：Web 侧 `render_panel_html(result)` / `render_report_html(result)`，Qt 侧 `show(dcc, omui)`；颜色只用 `var(--tak-*)`，HTML 必须 `html.escape`、无 CDN/外链。

新增 DCC 适配器（3ds Max / MotionBuilder）请复用 `tak/adapters/snapshot_utils.py` 的纯函数产出 `SkeletonSnapshot` 兼容字典，并用 `tak/adapters/conformance.py` 的 `validate_snapshot` / `compare_snapshots` / `check_adapter` 自检：

```python
from tak.adapters.snapshot_utils import build_snapshot, write_sidecar
from tak.adapters.conformance import check_adapter, validate_snapshot

snapshot = build_snapshot(dcc="max", source="tak.adapters.max", joints=joints, skin=skin)
assert validate_snapshot(snapshot)["ok"]
assert check_adapter(adapter)["ok"]       # 能力、DCC 标识、结构一体检
```

`validation/four_dcc_regression.py` 会在四套 mock 上跑 40 个 fixtures，逐项比对结构、基线读数、跨端 diff 与规范判定；CI 里这一步是硬门槛。新增适配器或改动快照字段后，先跑：

```bash
python validation/four_dcc_regression.py --quick
python validation/four_dcc_regression.py
```

## 11. 批次 4：只读型 AI 插件、确定性与安装器

批次 4 加了三个插件（`motion_library` / `mldeformer_factory`）和一套安装器。它们引入了三条新的约定，新工具请照抄。

### 11.1 「只读型 AI 插件」的标准结构

`motion_library` 与 `mldeformer_factory` 都不注册 `@rule`、不写场景、不联网。它们的 `manifest.json` 里**没有 `rules` 字段**，`tests/test_release_meta.py::test_ai_plugins_do_not_register_global_rules` 会拦住手滑加回去的人。

为什么这条重要：插件规则一旦注册进全局 `RULE_REGISTRY`，就会参与 40 个 golden fixtures 的判定。AI 插件的规则本该是「候选」，一旦混进判定链，fixtures 的漏报/误报基线立刻失真。

标准结构：

```
tools/<id>/
├── manifest.json     # id/name/description/dcc/min_version/entry/ui/permissions，无 rules
├── core.py           # TOOL_ID + info() + 纯函数 API + run_report(ctx)
├── ui_web.py         # 返回纯数据，不含 HTML 外链、不含 <script>
└── ui_qt.py          # 无 Qt 时必须返回 None，不得抛异常
```

`core.py` 的三段式：

```python
TOOL_ID = "<id>"                       # 必须与目录名一致（有测试拦）

def info() -> dict:                    # 自描述，给面板/菜单用
    ...

def some_query(...) -> dict:           # 薄封装，返回 json.dumps 安全的 dict
    ...

def run_report(ctx) -> Report:         # 把结果转成统一 Report
    ...
    report.metadata.update({
        "results": ..., "stats": ...,
        "safety": {"read_only": True, "scene_write": False,
                   "network": False, "deterministic": True, "note": "..."},
    })
```

`safety` 不是装饰：Web API 会把它原样透给前端，UI 据此决定是否显示「只读」标记。写了 `scene_write: False` 却真的改场景，比不写更糟。

### 11.2 确定性是硬要求，不是「尽量」

只要插件输出可能被当作基线（数据集清单、检索排名、映射结果），就必须满足：

1. **排序键完整**。只按 score 排不够 —— 同分时顺序取决于字典插入序，换个 Python 版本就变了。至少补一个稳定 tie-break（`clip_id` 升序 / `pose_id` 升序）。
2. **随机只准用局部实例**。`random.Random(seed)`，不要碰全局 `random`。全局状态会被别的库污染，也会污染别人。
3. **不写时间戳、不写绝对路径进摘要**。它们会让「同样的输入」产出「不同的哈希」，内容寻址直接失效。
4. **哈希用规范化 JSON**：`json.dumps(payload, sort_keys=True, separators=(",", ":"))` 再 `sha256`。`sort_keys` 少了，键序变化就会改哈希。

自检方式（`mldeformer` 就是这么验证的）：

```python
a = build_manifest(build_plan(snapshot))
b = build_manifest(build_plan(snapshot))
assert a["version"] == b["version"]           # 同进程两次

# 再换 PYTHONHASHSEED 起两个进程比一次，防字典序依赖
```

### 11.3 安装器：只准动自己的标记块

`tak/install` 的写入模型是**标记块**，不是「生成整份配置文件」：

```
# >>> TAK BEGIN maya v0.3.2
...生成的内容（块外内容不受影响）...
# >>> TAK END maya
```

四条不可协商的约束：

| 约束 | 原因 |
|---|---|
| 默认 `dry_run=True` | 装启动脚本是不可逆操作里最容易翻车的一类：用户 `userSetup.py` 里可能有一年的积累 |
| 写前备份 `<file>.tak-backup-<N>`，N 递增不覆盖 | 用户可能装过好几个版本，最早的备份才是「原始状态」 |
| 块外逐字节不动 | 包括行尾、末尾换行、BOM |
| 卸载精确删块并还原块外内容 | 不是「还原备份」（备份是安装后的状态，不是安装前的） |

新增一个 DCC 目标时，在 `tak/install/targets.py` 的 `TARGETS` 里加一项，必须提供：`id`、`default_roots`、`entry_file`、`block_template`、`comment_style`、`notes`。`block_template` 里的代码必须**自包含、可重复执行无副作用**，且每一段都要 `try/except` 兜住 —— 一个菜单注册失败不该让用户的整个启动脚本崩掉。

`tests/test_installer.py` 里有几条必须照着写的测试：

- `plan_install(dry_run=True)` 期间 `builtins.open` 不得以写模式被调用（用 monkeypatch 计数）。
- 连跑两次 `apply`，第二次全部 `skip`，文件逐字节相同。
- 块外手写的用户代码安装后逐字节不变。
- 安装 → 卸载后文件内容与安装前完全一致。

### 11.4 DCC 菜单模块的统一契约

四端各有一个 `tak/adapters/<dcc>/menu.py`，函数签名保持一致：

```python
register_menu() -> {"ok": bool, "menu": str, "reason"?: str}
unregister_menu() -> {"ok": bool, "menu": str, "removed"?: bool}
```

**不抛异常**是硬要求：这些函数由 `userSetup.py` 在启动时调用，抛出去会让 Maya 静默丢失后续所有启动项。无宿主时返回 `{"ok": False, "reason": "maya.cmds 不可用（无 Maya 宿主）"}`，让调用方能打印出人话。

Maya 侧的 `register_menu()` 必须**幂等**：`cmds.menu` 不查重，反复执行 `userSetup` 会堆出多个同名菜单。做法是先 `menu -exists` 判断再 `deleteUI`，然后重建。

`docs/VALIDATION.md` 的 B21–B23 是这一层的真机验证项 —— mock 测不出「菜单到底有没有出现在 Maya 菜单栏上」。
