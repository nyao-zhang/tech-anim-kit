# 场景体检（`rig_health_guard`）

> 保存或导出之前，把「视口里看不出来、进引擎才炸」的绑定问题一次性列出来，并给出只读的 dry-run 修复建议。

**适用版本** TAK `0.3.3`　·　**DCC** maya / max / mobu / unreal　·　**权限** `scene.read`　·　**UI** `qt`

## 1. 它解决什么问题

镜像过的左臂。关节上挂着 `[-1.0, 1.0, 1.0]` 的缩放时，Maya 视口完全正常，导出后顶点会沿轴向翻转，蒙皮权重与动画一起错位。这类错误靠肉眼几乎发现不了，通常要等到引擎里跑起来才暴露。`negative_scale` 会在保存前直接把它标成 ERROR。

孤立节点与遗留 reference。场景里被删了一半的 locator、`null_grp`、早就该卸掉的 `rig_legacy.ma`，都不会影响当前绑定，但导出时可能被一起带进引擎，或者在别人打开文件时变成无法解析的外部路径。`orphan_nodes` 与 `legacy_reference` 各自独立报出，不合并计数。

影响骨超限。Maya 里一个顶点挂 6 根骨头看着没问题，引擎侧通常只保留有限的几根，超出的权重会被截断，结果就是「DCC 预览与运行时不一致」这种最难查的反馈。`skin_influence_over_limit` 会用 `skin.max_influences_found` 与上限做比较，并把「限制到几根、再归一化」写进建议里。

它规避的是一次真实的返工：已经导出、已经导入、已经在引擎里被美术发现手是翻的——那时代价不只是重新导一次，还要重新对动画与蓝图。体检把这一步提前到了保存按钮之前。AI 只负责把结论说成人话，判定全部来自确定性规则。

## 2. 怎么打开

| 入口 | 操作 |
|---|---|
| Maya 菜单 | `TAK / Tech Anim Kit → 场景体检`（`MENU_ITEMS` 里的显示名就是「场景体检」，动作标识 `tak_health_guard`） |
| Python | 见下，走统一契约 `run_tool` 或直接调插件 `run_report` |
| Web 面板 | 无。`tak/ui/web/panel.html` 的 `data-tab` 只有 `chat` / `kb` / `vision` / `mapping` / `motion` |
| Qt 面板 | `tools/rig_health_guard/ui_qt.py` 的 `show(dcc="maya", omui=None)`；工具栏「运行体检」/「导出报告」/「严重度过滤」，报告可导出 JSON / Markdown / HTML |

最小可执行代码（在仓库根目录运行）：

```python
import copy, json
from tak.core import runner
from tak.adapters.base import NullAdapter

snap = copy.deepcopy(json.load(open("fixtures/good/good_001.json", encoding="utf-8"))["snapshot"])
snap["metadata"]["health"] = {"orphan_nodes": ["locator_old"]}
res = runner.run_tool("rig_health_guard", snap, dcc="maya", adapter=NullAdapter())
print(res.passed, [f.rule_id for f in res.report.findings])
```

Qt 面板注意：它调用的是 `run_tool(TOOL_ID, dcc=dcc, options={"include_builtin": True})`，即面板是「内置规则 + 专项规则」的完整体检入口。菜单里的「场景体检」也带同一组选项（`tak/adapters/maya/menu.py` 的 `_MENU_OPTIONS`），所以点菜单与点面板得到的是同一个完整体检，不会因为入口不同而少跑一半规则。

## 3. 输入

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `context` | `ToolContext` | 必填 | `run_report(context)` 的唯一参数，从里面取 `snapshot` / `spec` / `options` |
| `options["include_builtin"]` | `bool` | `False` | `True` 时连同 `tak.core.rules.builtin` 的 19 条内置规则一起跑，`metadata.scope` 记为 `all` |
| `dcc` | `str` | `"maya"` | `show(dcc=...)` / `run_tool(dcc=...)` 的默认值；本插件 9 条规则只声明 `dcc=["maya"]` |
| `omui` | `Any` | `None` | Qt 面板入口保留参数，当前实现未使用 |

`snapshot` 需要的真实键名（缺一个不影响其它规则，各自独立跳过）：

- `joints.<关节名>.scale`、`.rotate`、`.namespace` —— 负缩放、未冻结、命名空间三条规则读这里；
- `skin.max_influences_found`、`skin.weight_sum_deviation` —— 蒙皮两条规则读这里；
- `metadata.health` —— 插件专属字段。**不存在或不是 dict 时，9 条规则整组不产出结论**（刻意的零误报开关：没有数据源就不猜），但报告里会额外留一条 `rig_health_guard.datasource.missing` 的 INFO，明确写出「本次未执行体检规则」。这条提示不是通过，是「什么都没检查」的如实标注；
- `metadata.health` 下的键：`orphan_nodes`、`negative_scale`、`unfrozen_transforms`、`duplicate_shapes`、`references`、`control_colors`、`max_influences`。前三类既接受字符串，也接受 `{"node": ...}` / `{"file": ...}` 形式的字典。

`spec` 只读两处，都可缺省：

- `skin.max_influences` —— 没写时回退 `4`；
- `skin.weight_sum_tolerance` —— 没写时回退 `0.001`。另外 `health.max_influences` 存在时优先于 spec。

## 4. 输出

`run_report` 返回 `tak.core.report.Report`，`run_tool` 再包一层 `ToolResult`。关键字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `passed` | `bool` | 只有存在 ERROR / BLOCKER 时为 `False`；WARNING 与 INFO 不影响 |
| `counts` | `dict` | `blocker` / `error` / `warning` / `info` 四档计数 |
| `metadata.scope` | `str` | `plugin_only` 或 `all` |
| `metadata.rules_count` | `int` | 本次实际选中执行的规则条数 |
| `metadata.ai_explanations` | `list` | 只为 ERROR / BLOCKER 生成，含 `rule_id` / `node` / `explanation` / `suggestion` / `model` |
| `metadata.ai` | `dict` | 含 `offline` / `model` / `degraded`，记录 AI 是否降级及原因 |
| `metadata.health_section` | `dict` | 含 `present` / `keys` / `note`；实测注入五个字段后 `keys` 为 `control_colors`、`duplicate_shapes`、`max_influences`、`orphan_nodes`、`references` |
| `metadata.history` | `dict` | 固定 `{"linked": false, "tickets": []}`，占位接口 |

跑 `fixtures/good/good_001.json` 的 snapshot、注入两个孤立节点与一条负缩放后，原文片段如下（`passed` 与计数）：

```json
{
  "title": "Rig Health Guard 场景体检",
  "dcc": "maya",
  "toolkit_version": "0.3.2",
  "spec_version": "1.0.0",
  "passed": false,
  "counts": {
    "info": 1,
    "warning": 6,
    "error": 2,
    "blocker": 0
  },
```

同一次运行中 `metadata.ai_explanations` 的一条：

```json
      {
        "rule_id": "rig_health_guard.negative_scale",
        "severity": "error",
        "node": "upperarm_L",
        "explanation": "骨骼上有负缩放，意味着这个关节被镜像过。DCC 里看起来正常，但导出后顶点会沿轴向翻转，蒙皮和动画都会错位。 定位节点：upperarm_L。",
        "suggestion": "dry-run 建议（不会自动改写场景）：把缩放归正到 1 并重新冻结变换，再检查镜像侧关节；候选动作 reset_scale（需人工确认后执行）",
        "model": "local-template"
      }
```

每条 Finding 的 `data` 里还带 `duration_ms`（规则执行耗时，由规则引擎写入）与 `data["fix"]`（候选动作，**只是描述，不会执行**）。

## 5. 校验项清单

规则定义在 `tools/rig_health_guard/rules.py`，`plugin_id="rig_health_guard"`。**所有规则的前置条件是 `metadata.health` 是 dict**；不满足时整组不产出结论，只留一条说明（见表格下方）。

| rule_id | 严重级 | 触发条件 | 建议 |
|---|---|---|---|
| `rig_health_guard.orphan_nodes` | WARNING | `health.orphan_nodes` 里有能取出名字的条目 | 确认无依赖后删除，或挂回骨架层级 |
| `rig_health_guard.negative_scale` | ERROR | 任一关节 `scale` 某轴 `< -1e-4`；或 `health.negative_scale` 里另有条目 | 归正缩放、冻结变换，再检查镜像侧 |
| `rig_health_guard.unfrozen_transform` | WARNING | `health.unfrozen_transforms` 存在则以它为准；否则关节 `scale` 偏离 1 或 `rotate` 任一轴 `> 1e-4` | 执行 freeze transformations |
| `rig_health_guard.namespace_pollution` | WARNING | 关节 `namespace` 非空，或关节名含 `:` | 移回根命名空间并清掉冒号前缀 |
| `rig_health_guard.duplicate_shape` | WARNING | `health.duplicate_shapes` 有条目（支持 `{"shape", "nodes"}` 或二元数组） | 保留一个，其余删除或复制成独立 shape |
| `rig_health_guard.skin_influence_over_limit` | ERROR | `skin.max_influences_found` 大于上限（`health.max_influences` → `spec.skin.max_influences` → 4） | limit influences 到上限并重新 normalize |
| `rig_health_guard.skin_weight_sum` | ERROR | `abs(skin.weight_sum_deviation)` 超出容差（`spec.skin.weight_sum_tolerance`，缺省 0.001） | normalize weights |
| `rig_health_guard.legacy_reference` | WARNING | `health.references` 有条目 | 确认无依赖后 remove reference，或导入后清理 |
| `rig_health_guard.control_uncolored` | INFO | `health.control_colors` 值为 `None`、空串、`default`、`none`，或 `colored` 为 `False` | 按左右侧约定设置 override color |

`include_builtin=True` 时会额外跑 `tak/core/rules/builtin.py` 的 19 条规范规则（`naming.*` / `skeleton.*` / `skin.*` / `export.*`），它们不属于本插件，不在上表内。只加载本插件时 `metadata.rules_count` 为 `28`（19 内置 + 9 体检）。

注意这个数字依赖进程状态：`run_rules` 在 `plugin_id=None` 时会跑全局注册表里所有 DCC 匹配的规则。如果同一进程里先前还加载过 `spec_enforcer` / `export_bridge`，它们的 9 条会一并计入，实测同一份快照能到 `37`。所以别把 `rules_count` 当固定常量用，要看 `rule_ids` 的实际内容。

上表之外还有一条**不是规则**的提示：`metadata.health` 缺失时，`run_report` 会追加一条 `rig_health_guard.datasource.missing`（INFO），message 写明「本次未执行体检规则；结论为空并不代表场景健康」。它不参与规则计数，也不影响 `passed`——存在的意义是不让「没检查」被读成「没问题」。

## 6. 典型用法

**（1）只跑专项规则。** 注意负缩放同时会被 `unfrozen_transform` 抓到，因为 `scale` 偏离了 1：

```python
import copy, json
from tak.core import runner
from tak.adapters.base import NullAdapter

snap = copy.deepcopy(json.load(open("fixtures/good/good_001.json", encoding="utf-8"))["snapshot"])
snap["metadata"]["health"] = {"orphan_nodes": ["locator_old"]}
snap["joints"]["upperarm_L"]["scale"] = [-1.0, 1.0, 1.0]

res = runner.run_tool("rig_health_guard", snap, dcc="maya", adapter=NullAdapter())
print(res.passed, res.report.counts())
for item in res.report.findings:
    print(item.severity.value, item.rule_id, item.node)
```

实际输出：

```text
False {'info': 0, 'warning': 2, 'error': 1, 'blocker': 0}
error rig_health_guard.negative_scale upperarm_L
warning rig_health_guard.orphan_nodes locator_old
warning rig_health_guard.unfrozen_transform upperarm_L
```

**（2）接保存 / 导出钩子。** `on_before_save` 与 `on_before_export` 都返回 `passed` / `block` / `report` 三个键，`block` 只在 ERROR/BLOCKER 时为真，钩子本身不写场景；下面调的是导出前那一支：

```python
import copy, json
from tak.core import runner, registry
from tak.adapters.base import NullAdapter

snap = copy.deepcopy(json.load(open("fixtures/good/good_001.json", encoding="utf-8"))["snapshot"])
snap["metadata"]["health"] = {}
snap["joints"]["upperarm_L"]["scale"] = [-1.0, 1.0, 1.0]

hooks = registry.load_plugin_module(runner.find_plugin("rig_health_guard"), "hooks")
out = hooks.on_before_export(runner.ToolContext(dcc="maya", snapshot=snap,
                                                adapter=NullAdapter()))
print(out["passed"], out["block"], out["report"]["counts"])
print([f["rule_id"] for f in out["report"]["findings"]])
```

实际输出：

```text
False True {'info': 0, 'warning': 1, 'error': 1, 'blocker': 0}
['rig_health_guard.negative_scale', 'rig_health_guard.unfrozen_transform']
```

把 `metadata.health = {}` 这一行删掉再跑，会得到 `True False` 与一条 `rig_health_guard.datasource.missing`（INFO），计数是 `{'info': 1, 'warning': 0, 'error': 0, 'blocker': 0}` —— 这正是下一节第一条限制：没有数据源时它不会猜，但会明说没检查。

**（3）完整体检。** 加上 `include_builtin=True`，`scope` 变成 `all`，规则总数从 9 变成 28：

```python
import copy, json
from tak.core import runner
from tak.adapters.base import NullAdapter

snap = copy.deepcopy(json.load(open("fixtures/good/good_001.json", encoding="utf-8"))["snapshot"])
res = runner.run_tool("rig_health_guard", snap, dcc="maya",
                      adapter=NullAdapter(), options={"include_builtin": True})
print(res.passed, res.report.metadata["scope"], res.report.metadata["rules_available"])
```

实际输出：

```text
True all 28
```

## 7. 边界与限制

- 不写场景，也不自动修复。负缩放、孤立节点、重复 shape 都只给 `fix_hint` 和 `data["fix"]` 描述，因为自动改绑定结构的风险远高于手工确认。
- 不自己采集健康字段。`metadata.health` 必须由调用方注入；仓库里的 `tak/adapters/maya/adapter.py` 目前只产出 `joints` / `skin` / `frame_range` / `metadata` 的常规字段，**没有采集 `health`**。结果是：菜单「场景体检」与保存钩子在真实 Maya 场景里通常得到 0 条专项结论（实测 `on_before_save` 返回 `passed=True`），报告里只留一条「未执行体检规则」的 INFO 提示。要让它在真机上真正干活，需要一个额外的采集环节把这些字段塞进快照——这是当前最值得补的一块。
- 只在 Maya 跑判定。9 条规则全部 `dcc=["maya"]`，manifest 虽然写了 `max` / `mobu` / `unreal`，在那些宿主里选中不到这些规则，结论会是空的。
- 不解析二进制。既不读 `.ma` / `.mb`，也不读 FBX；一切基于快照字典。
- 不联网。AI 解释默认走本地模板（默认 provider 模型名是 `mock`，此时 `metadata.ai.offline=true`），provider 出错会静默降级并把原因写进 `metadata.ai.degraded`。
- AI 不参与判定。`metadata.ai_explanations` 只解释已有 Finding，不新增、不删除，也不影响 `passed`。
- 不接历史工单。`metadata.history` 是固定占位，`linked` 永远为 `false`。

## 8. 与其它模块的关系

上游是快照采集：`tak/core/diff/snapshot.py` 定义 `SkeletonSnapshot`，Maya 适配器产出 `joints` / `skin`；健康字段需要一个额外采集环节补进 `metadata.health`。规则引擎是 `tak/core/rules/engine.run_rules`，本插件只是注册了 9 条规则。

下游是报告与钩子：Qt 面板拿 `ToolResult` 渲染表格并导出 JSON / Markdown / HTML；`hooks.py` 把同一份 `ToolResult` 转成 `{"passed", "report", "block"}`，供保存/导出事件判断。

共享契约字段：`snapshot.joints.<name>.scale` / `.rotate` / `.namespace`、`snapshot.skin.max_influences_found` / `.weight_sum_deviation`、`metadata.health.*`、`spec.skin.max_influences` / `.weight_sum_tolerance`。与 `spec_enforcer` 的区别是：这里只报告，`spec_enforcer` 才把同类问题翻译成可执行的 DSL 计划。
