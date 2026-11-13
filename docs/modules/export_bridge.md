# 导出前后校验（`export_bridge`）

> 导出前落一份 sidecar 骨架快照，导出后再取一份，两份一比就知道这一趟导出有没有偷偷改掉单位、轴向、关节或绑定矩阵。

**适用版本** TAK `0.3.2`　·　**DCC** maya / max / mobu / unreal　·　**权限** `scene.read` / `export.fbx`　·　**UI** `qt`

## 1. 它解决什么问题

「DCC 里看着对，进引擎就错位」是技术动画最常见的一类返工。成因往往不是绑定本身，而是导出这一步悄悄改了东西：场景单位被切过、up axis 被另一个脚本改回 Z、某个 `_R` 关节在导出选项里被过滤掉、父级被重设、帧范围沿用上一段动画。单看导出结果一切正常，因为错误在导出那一刻才发生。

这个模块的做法是把对照物固定下来：`pre_export` 在导出前把骨架写成 sidecar（约定后缀 `.tak_snapshot.json`，路径由你传入），导出后再取一份快照，`verify_pair` 逐字段比较，把偏差分成九类并给出中文说明。阻断性偏差在报告里是 BLOCKER，非阻断的只是 INFO。真实收益是「不用再靠猜」：关节少了 `foot_R`、`hand_L` 的父级从 `lowerarm_L` 变成 `None`、单位从 `cm` 变成 `m`——报告里直接写着三行，定位到关节名，不需要打开 FBX 逐个对。

另一层价值是交接：sidecar 是 UE 侧导入校验的唯一事实源，`ue_import_hook` 把路径交给 `ue_import_guard`，导出侧不重复实现 UE 校验。

## 2. 怎么打开

| 入口 | 操作 |
|---|---|
| Maya 菜单 | `TAK / Tech Anim Kit → 导出校验`（`MENU_ITEMS` 显示名「导出校验」，动作 `tak_export_bridge`） |
| Python | 见下；纯函数方向是 `pre_export` / `verify_pair` / `post_export` |
| Web 面板 | 无。`panel.html` 的 `data-tab` 没有导出校验页签 |
| Qt 面板 | `tools/export_bridge/ui_qt.py` 的 `show(dcc="maya", omui=None)`；两个「选择 before / after ...」按钮 + 「开始对比」，下方是差异表与结论标签 |

最小可执行代码（在仓库根目录运行）：

```python
import copy, json
from tak.core import runner, registry

snap = copy.deepcopy(json.load(open("fixtures/good/good_001.json", encoding="utf-8"))["snapshot"])
core = registry.load_plugin_module(runner.find_plugin("export_bridge"), "core")
after = copy.deepcopy(snap)
after["unit"] = "m"
print(core.verify_pair(snap, after)["passed"])
```

Qt 面板走的是 `run_tool(..., options={"before_path": ..., "after_path": ...})`，所以它需要你手上已经有两份 JSON；Maya 菜单的「导出校验」只传当前快照，没有对照物，因此只会得到一条 `export_bridge.verify_skipped`。

## 3. 输入

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `before` / `after` | `dict` 或 `SkeletonSnapshot` 或 `None` | 必填 | `verify_pair` 的两份快照；`None` 按空快照处理 |
| `spec` | `dict` | `None` | 只读 `export.fbx.verify.*` 三个键 |
| `matrix_tolerance` | `float` | `0.0001` | 绑定矩阵比较容差；传 `None` 时回退到 spec，再回退 `1e-4` |
| `out_path` | `str` | 必填 | `pre_export` 的 sidecar 路径，父目录不存在会自动创建 |
| `sidecar_path` | `str` | 必填 | `post_export` 读的 sidecar 路径 |
| `after_snapshot` | `dict` 或 `None` | 必填 | `post_export` 的导出后快照 |

`run_report` 从 `ctx.options` 里按顺序找对照物，找到哪一组用哪一组：`sidecar`（路径字符串）加可选 `after` 走 `post_export`；`before` 与 `after`（快照 dict）直接 `verify_pair`；`before_path` 与 `after_path`（JSON 文件）读文件后 `verify_pair`；都没有时返回跳过结果，并产出一条 `export_bridge.verify_skipped`。

`snapshot` 需要的键就是 `SkeletonSnapshot` 的那几个：`unit`、`up_axis`、`joints`（每个关节含 `parent` / `matrix` / `depth` / `scale` / `rotate` / `namespace`）、`frame_range`、`skin`、`metadata`。`spec` 只需要 `export.fbx.verify` 下的 `matrix_tolerance` / `blocking` / `ignore`。

## 4. 输出

`verify_pair` 返回 dict，`post_export` 在它基础上补 sidecar 与摘要：

| 字段 | 类型 | 说明 |
|---|---|---|
| `passed` | `bool` | 没有阻断性偏差时为 `True` |
| `diffs` | `list` | 每条含 `field` / `type` / `blocking` / `message`，以及 `before` / `after` 或 `joint` |
| `blocking` | `list` | `diffs` 中参与阻断的子集 |
| `reason` | `str` | 中文结论，会列出偏差类型 |
| `matrix_tolerance` | `float` | 实际使用的容差 |
| `blocking_fields` / `ignored_fields` | `list` | 当前生效的字段策略 |
| `before_summary` / `after_summary` | `dict` | `unit` / `up_axis` / `frame_range` / `joint_count` |

`fixtures/good/good_001.json` 的 snapshot 作为 before，改单位、删 `foot_R`、改帧范围后作为 after，原文片段：

```json
{
 "passed": false,
 "diffs": [
  {
   "field": "unit",
   "before": "cm",
   "after": "m",
   "type": "unit",
   "blocking": true,
   "message": "导出前后场景单位不一致：cm → m"
  },
  {
   "field": "joint_count",
   "before": 19,
   "after": 18,
   "type": "joint_count",
   "blocking": true,
   "message": "导出前后关节数量不一致：19 → 18"
  },
```

`run_report` 会把结果塞进 `metadata.verify`（只保留 JSON 安全字段），并额外写 `metadata.sidecar`（sidecar 契约）与 `metadata.ue_import`（交接占位，`status` 固定 `delegated`）。

## 5. 校验项清单

本模块的判定结果有两种表达方式：`verify_pair` 的 `diffs` / `blocking`，以及 `run_report` 翻译出来的 Finding。

由 `core.py` 的 `_findings` 产出：

| rule_id | 严重级 | 触发条件 | 建议 |
|---|---|---|---|
| `export_bridge.blocking_diff` | BLOCKER | 每个落在阻断集合里的 diff 一条 | 按 diff 定位修复后重新导出 |
| `export_bridge.informational_diff` | INFO | 每个非阻断 diff 一条（被 `ignore` 的字段不出现） | 不参与阻断，仅供核对 |
| `export_bridge.pair_match` | INFO | 没有 diff 且未跳过 | 无需处理 |
| `export_bridge.verify_skipped` | INFO | 没提供 before/after 或 sidecar | 通过 options 传入对照物 |
| `export_bridge.sidecar_unreadable` | ERROR | sidecar 打不开、不是 JSON、或缺 `snapshot` 对象 | 确认导出前调用过 `pre_export` |

`tools/export_bridge/rules.py` 另有两条规则，随插件加载注册，但 `run_report` 不走规则引擎，所以常规入口看不到它们（实测 `run_tool` 只返回 `verify_skipped`）：

| rule_id | 严重级 | 触发条件 | 建议 |
|---|---|---|---|
| `export_bridge.unit_axis_consistency` | ERROR | `snapshot.unit` / `up_axis` 与 `spec.export.fbx.unit` / `up_axis` 不一致 | 改 working unit 或按规范设置导出缩放 |
| `export_bridge.frame_range_consistency` | ERROR | `snapshot.frame_range` 与 `spec.export.fbx.frame_range` 不一致 | 把时间轴起止帧改成规范值 |

九类 diff 字段（`DIFF_TYPES`）默认全部阻断：`unit`、`up_axis`、`joint_count`、`joint_missing`、`joint_added`、`joint_renamed`、`bind_matrix`、`parent`、`frame_range`。要放宽，在 `spec.export.fbx.verify.ignore` 里列字段名，或在 `.blocking` 里显式给白名单。

## 6. 典型用法

**（1）纯函数对比。** 一份快照，改三个地方：

```python
import copy, json
from tak.core import runner, registry

snap = copy.deepcopy(json.load(open("fixtures/good/good_001.json", encoding="utf-8"))["snapshot"])
after = copy.deepcopy(snap)
after["unit"] = "m"
after["joints"].pop("foot_R")
after["frame_range"] = [1.0, 100.0]

core = registry.load_plugin_module(runner.find_plugin("export_bridge"), "core")
result = core.verify_pair(snap, after)
print(result["passed"], result["reason"])
for item in result["diffs"]:
    print(item["type"], item["blocking"], item["message"])
```

实际输出：

```text
False 检测到 4 项阻断性偏差（unit、joint_count、joint_missing、frame_range），应阻断导出
unit True 导出前后场景单位不一致：cm → m
joint_count True 导出前后关节数量不一致：19 → 18
joint_missing True 关节 foot_R 在导出后丢失
frame_range True 导出前后帧范围不一致：['1.0', '120.0'] → ['1.0', '100.0']
```

**（2）sidecar 往返。** 写出去，再读回来对比；`hand_L` 的父级被清空：

```python
import copy, json, os, tempfile
from tak.core import runner, registry

snap = copy.deepcopy(json.load(open("fixtures/good/good_001.json", encoding="utf-8"))["snapshot"])
core = registry.load_plugin_module(runner.find_plugin("export_bridge"), "core")
path = os.path.join(tempfile.gettempdir(), "rig.tak_snapshot.json")

pre = core.pre_export(snap, path)
print(pre["written"], pre["sidecar_version"], os.path.basename(pre["path"]))
after = copy.deepcopy(snap)
after["joints"]["hand_L"]["parent"] = None
post = core.post_export(path, after)
print(post["passed"], post["reason"], post["readable"])
print(json.dumps(post["before_summary"], ensure_ascii=False))
print([f["rule_id"] for f in post["findings"]])
```

实际输出：

```text
True 1 rig.tak_snapshot.json
False 检测到 1 项阻断性偏差（parent），应阻断导出 True
{"unit": "cm", "up_axis": "Y", "frame_range": [1.0, 120.0], "joint_count": 19}
['export_bridge.blocking_diff']
```

**（3）走统一契约出报告。** 两份快照一致时，结论是「允许继续」，同时能看到 UE 交接状态：

```python
import copy, json
from tak.core import runner
from tak.adapters.base import NullAdapter

snap = copy.deepcopy(json.load(open("fixtures/good/good_001.json", encoding="utf-8"))["snapshot"])
res = runner.run_tool("export_bridge", snap, dcc="maya", adapter=NullAdapter(),
                      options={"before": snap, "after": snap})
print(res.passed, res.report.metadata["verify"]["passed"])
print([f.rule_id for f in res.report.findings])
print(res.report.metadata["ue_import"]["status"],
      res.report.metadata["ue_import"]["target"])
```

实际输出：

```text
True True
['export_bridge.pair_match']
delegated ue_import_guard
```

## 7. 边界与限制

- 不执行导出，也不解析 FBX 二进制。`pre_export` / `post_export` 只读写 JSON 与做比较，真正的 FBX 导出由 adapter 或你的导出脚本负责。
- 不会自己拦下导出，也不做 UE 侧校验。`verify_pair` 只返回 `passed`，`run_report` 只产出 BLOCKER Finding，是否中止导出由菜单、钩子或流水线决定；`ue_import_hook` 固定返回 `{"status": "delegated", "implemented": false}`，把 sidecar 交给 `ue_import_guard`，不假装已经验证过 UE 资产。
- `rules.py` 的两条规则不在常规路径上。`run_report` 没调用 `run_rules`，实测只有 `export_bridge.verify_skipped`；要跑那两条得自己 `run_rules(..., plugin_id="export_bridge")`，此时会得到 `frame_range_consistency` 与 `unit_axis_consistency` 两条 ERROR。
- 重命名靠结构推断。`diff_snapshots` 用「丢失的关节 + 新增的关节 + 结构完全一致」判定 `joint_renamed`；改名同时改了矩阵或深度，就会退化成 `joint_missing` 加 `joint_added`（实测 `pelvis → hips` 且深度变化时如此）。
- 数值会变成字符串。`diff_snapshots` 的 `_json_value` 对有限浮点也走 `str()`，所以 `frame_range` 在 diff 与 message 里显示成 `['1.0', '120.0']`；用字符串比较时别按浮点解析。
- 默认偏严。九类字段默认全部阻断，第一次接入可能被 `bind_matrix` 的容差问题挡住；确认容差后再决定是否放进 `ignore`。

## 8. 与其它模块的关系

上游是快照采集与导出流程：`tak/core/diff/snapshot.py` 的 `SkeletonSnapshot` / `diff_snapshots` 提供比较实现，DCC 适配器提供导出前后的快照，`pre_export` 负责把它们落盘成 sidecar。

下游有两个：报告层（`Report` → Qt / JSON / Markdown / HTML）与 UE 侧 `ue_import_guard`。后者读同一份 sidecar，用同样的 diff 字段名比较，只是策略不同——UE 侧默认忽略 `bind_matrix`、把 `frame_range` 降为提示。

共享契约字段：sidecar 后缀 `.tak_snapshot.json`、`sidecar_version=1`、payload 里的 `tool` / `version` / `sidecar_version` / `snapshot`、九类 diff 字段名、`matrix_tolerance`。`sidecar_contract()` 把这份约定原样写进报告，供外部脚本直接消费。
