# UE 导入校验（`ue_import_guard`）

> 在 Unreal 侧把「这个资产到底能不能用」问清楚：骨骼链、根运动、压缩、曲线、材质槽、LOD 标记，以及与导出前 sidecar 的快照差异。

**适用版本** TAK `0.3.2`　·　**DCC** unreal　·　**权限** `scene.read` / `import.validate`　·　**UI** `both`

## 1. 它解决什么问题

动画曲线在导入时静默消失。`curve_count` 声明有 3 条曲线，UE 侧 `curves` 是空列表，武器切换、表情驱动这些依赖曲线的逻辑会全部失效，但资产本身「导入成功」，没人会去数曲线。`curve_loss` 专门比对这两个字段，空列表直接报 ERROR。

骨骼链断了却还在跑根运动。`root_motion=True` 但 Skeleton 里没有可用的根骨骼，运行时会原地漂移或位移丢失。`root_motion` 会同时看序列的开关和 `skeleton.root_bone` 是否真的在 `bones` 里，缺一边就报出来。

命名空间与禁用字符带进引擎。DCC 里叫 `hand_L:copy` 的骨骼，在 UE 里会让路径与曲线对不上；`bone_naming` 检查 `:`、`|`、空格，以及「仅靠大小写区分」的重名骨骼。最后一类是「导入链路悄悄改了什么」。`validate_import` 拿 Export Bridge 的 sidecar 与 UE 侧快照做 diff，关节被重命名、少了、多了、父级变了都会阻断。这一层是跨 DCC 的闭环：DCC 侧认为导出正确，UE 侧才知道实际收到了什么。

## 2. 怎么打开

| 入口 | 操作 |
|---|---|
| Maya 菜单 | 未挂菜单，用下面的 Python 入口。`MENU_ITEMS` 只有「打开面板」「场景体检」「规范校验」「导出校验」「动作检索」「Web 面板」「关于 TAK」，没有 UE 项 |
| Python | 见下；纯函数方向是 `validate_import`，报告方向是 `run_tool` |
| Web 面板 | 无独立页签。`tools/ue_import_guard/ui_web.py` 提供 `render_panel_html` 片段（不含 `<html>`，只使用 `tak-panel` 等类名），设计上由服务端嵌入，但当前 `tak/ui/web/server.py` 尚未接线，只能直接调用该函数；`panel.html` 的五个 `data-tab` 里也没有 UE 页 |
| Qt 面板 | `tools/ue_import_guard/ui_qt.py` 的 `show(dcc="unreal", omui=None)`；单按钮「运行校验」+ 结果表 + 日志台（日志里会提示 API 缺口条数） |

最小可执行代码（在仓库根目录运行）：

```python
import copy, json
from tak.core import runner
from tak.adapters.base import NullAdapter

snap = copy.deepcopy(json.load(open("fixtures/good/good_001.json", encoding="utf-8"))["snapshot"])
res = runner.run_tool("ue_import_guard", snap, dcc="unreal", adapter=NullAdapter())
print(res.passed, len(res.report.findings), res.report.metadata["ue_assets_present"])
```

上面这段没有注入 UE 资产数据，所以结论是 `True 0 False`：规则整组跳过、导入校验也跳过。要看真实结论，必须给 `ue_assets`（见第 3 节）。

## 3. 输入

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `ctx` | `ToolContext` | 必填 | `run_report(ctx)` 的唯一参数 |
| `options["ue_assets"]` | `dict` | 未设置 | UE 资产数据，优先级高于快照里的 `metadata.ue` |
| `options["sidecar"]` | `dict` | 未设置 | Export Bridge 写出的 sidecar；缺省时回落到 `ue_assets.import.sidecar` |
| `options["ue_snapshot"]` | `dict` | 未设置 | UE 侧快照；缺省时回落到 `ctx.snapshot` |
| `sidecar` | `dict` 或 `None` | 必填 | `validate_import` 第一个参数，接受裸快照或 `{"snapshot": {...}}` |
| `ue_snapshot` | `dict` 或 `None` | 必填 | `validate_import` 第二个参数 |
| `spec` | `dict` | `None` | 只读 `ue.*` 配置 |
| `spec.ue.import_matrix_tolerance` | `float` | `0.0001` | 矩阵容差 |
| `spec.ue.import_check_bind_matrices` | `bool` | `False` | 为 `True` 才比对 `bind_matrix`，否则忽略 |
| `spec.ue.import_ignore_fields` | `list` | `[]` | 额外忽略的 diff 字段 |
| `spec.ue.import_warn_fields` | `list` | `["frame_range"]` | 只提示不阻断；`frame_range` 始终在内 |

`ue_assets` 的真实结构（全部字段都可缺省）：

- `skeleton.bones`（`list[str]`）、`skeleton.compatible_with`（`list[str]`）、`skeleton.root_bone`（`str`）、`skeleton.kind`（`str`，`"humanoid"` 时强制检查完整人形链）；
- `anim_sequences[]`：`name`、`skeleton`、`compression`、`root_motion`、`curve_count`、`curves`；
- `skeletal_meshes[]`：`name`、`material_slots`（`int` 或槽名列表）、`lods[]`（每项含 `allow_deformer`）；
- `import.sidecar`：Export Bridge 的 sidecar dict。

`spec` 侧的规则字段：`ue.skeleton_asset`、`ue.animation_compression`、`ue.allow_mesh_deformer`。

## 4. 输出

`validate_import` 返回五个键，固定为 `{"passed", "diffs", "blocking", "reason", "ignored"}`。`run_report` 再补 `metadata`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `metadata.rules_count` | `int` | 本插件选中执行的规则数，正常是 `9` |
| `metadata.validate` | `dict` | `compared` / `passed` / `diff_count` / `blocking_count` / `ignored` / `reason` |
| `metadata.api_notes` | `list` | 4 条 UE Python API 缺口说明，静态内容 |
| `metadata.ue_assets_present` | `bool` | 是否读到了 `ue_assets` |

给 sidecar、UE 快照与一份健康 `ue_assets`，同时把 `pelvis` 改名成 `hips`、改帧范围、改 root 矩阵后，`metadata.validate` 原文：

```json
{
  "compared": true,
  "passed": false,
  "diff_count": 2,
  "blocking_count": 1,
  "ignored": [
    "bind_matrix"
  ],
  "reason": "导入校验未通过：1 处阻断性偏差（joint_renamed）；已忽略 bind_matrix"
}
```

同一次运行的阻断 Finding（另一条是 `ue_import_guard.import_informational_diff` / `info`，message 为「帧范围不一致：['1.0', '120.0'] → ['1.0', '100.0']」）：

```json
[
  {
    "rule_id": "ue_import_guard.import_blocking_diff",
    "severity": "blocker",
    "message": "关节被重命名：pelvis → hips",
    "node": "pelvis",
    "fix_hint": "按 diff 定位后修正资产或重新导出；该偏差不允许进入引擎",
```

`root` 矩阵被改动之所以没出现在 `diffs` 里，是因为 `bind_matrix` 默认被忽略（`ignored` 里能看到它）。

## 5. 校验项清单

规则定义在 `tools/ue_import_guard/rules.py`，`plugin_id="ue_import_guard"`，全部 `dcc=["unreal"]`，每条规则最多产出 24 条 Finding（`_MAX_FINDINGS_PER_RULE`）。

| rule_id | 严重级 | 触发条件 | 建议 |
|---|---|---|---|
| `ue_import_guard.skeleton_compatible` | ERROR 或 WARNING | `bones` 为空、或 `compatible_with` 不含 `spec.ue.skeleton_asset` 报 ERROR；`compatible_with` 为空但 spec 有目标骨架报 WARNING | 补兼容列表或改 spec 目标骨架 |
| `ue_import_guard.required_bone_chain` | ERROR | 人形链缺 `root` / `pelvis` / `spine_01` / `neck` / `head`；或 `root_bone` 不在 `bones` | 补建骨骼并重建蒙皮与动画轨道 |
| `ue_import_guard.bone_naming` | ERROR 或 WARNING | 名字含 `:` / `|` / 空格报 ERROR；仅大小写重名、非字符串条目报 WARNING | 去掉命名空间前缀与空格后重导出 |
| `ue_import_guard.root_motion` | ERROR 或 WARNING | `root_motion=True` 但无可用根骨骼报 ERROR；`root_motion` 不是布尔报 WARNING | 补根骨骼，或关闭 Enable Root Motion |
| `ue_import_guard.anim_compression` | WARNING 或 ERROR | 压缩方案与 `spec.ue.animation_compression` 不一致报 WARNING；为空报 ERROR | 改序列压缩设置或更新 spec |
| `ue_import_guard.curve_loss` | ERROR 或 WARNING | `curve_count>0` 且曲线列表为空报 ERROR；数量不符报 WARNING | 检查导出是否勾选曲线、是否被压缩清理 |
| `ue_import_guard.material_slots` | ERROR 或 WARNING | 槽数为 0 或空列表报 ERROR；存在空槽报 WARNING | 补材质实例后重新导入 |
| `ue_import_guard.lod_allow_deformer` | WARNING | `allow_deformer` 不是布尔；或为 `true` 但 `spec.ue.allow_mesh_deformer=false` | 关闭该 LOD 的 Allow Deformer |
| `ue_import_guard.sidecar_mismatch` | ERROR 或 WARNING | `metadata.ue.import.sidecar` 与 UE 快照在关节级不一致报 ERROR；单位/轴向不一致报 WARNING | 核对命名空间、骨骼过滤、重命名 |

导入 diff 另外翻译成两条 Finding，由 `core.py` 的 `_validation_findings` 产出：

| rule_id | 严重级 | 触发条件 | 建议 |
|---|---|---|---|
| `ue_import_guard.import_blocking_diff` | BLOCKER | 每个阻断 diff 一条；默认实际阻断的是 `unit` / `up_axis` / `joint_count` / `joint_missing` / `joint_added` / `joint_renamed` / `parent` 七类 | 修正资产或重新导出 |
| `ue_import_guard.import_informational_diff` | INFO | 每个非阻断 diff 一条 | 不参与阻断，仅供核对 |

字段缺失一律跳过对应规则，**不会臆造数值**；这也是 40 份 golden fixtures（不含 UE 字段）零误报的原因。

## 6. 典型用法

**（1）查一组有问题的资产。** 缺 `neck`、骨骼名带冒号、曲线丢了、材质槽为 0、LOD 开了 deformer：

```python
import copy, json
from tak.core import runner
from tak.adapters.base import NullAdapter
from tak.core.spec import load_spec

snap = copy.deepcopy(json.load(open("fixtures/good/good_001.json", encoding="utf-8"))["snapshot"])
assets = {
    "skeleton": {"bones": ["root", "pelvis", "spine_01", "head", "hand_L:copy"],
                 "compatible_with": ["/Game/Characters/Default/SK_Default"],
                 "root_bone": "root"},
    "anim_sequences": [{"name": "/Game/Anim/AS_Run", "compression": "Uniform",
                        "root_motion": True, "curve_count": 3, "curves": []}],
    "skeletal_meshes": [{"name": "/Game/Characters/SKM_Body",
                         "material_slots": 0,
                         "lods": [{"allow_deformer": True}]}],
}
res = runner.run_tool("ue_import_guard", snap, dcc="unreal", adapter=NullAdapter(),
                      spec=load_spec(), options={"ue_assets": assets})
print(res.passed, res.report.counts(), res.report.metadata["rules_count"])
for item in res.report.findings:
    print(item.severity.value, item.rule_id, item.node)
```

实际输出：

```text
False {'info': 0, 'warning': 2, 'error': 4, 'blocker': 0} 9
error ue_import_guard.bone_naming hand_L:copy
error ue_import_guard.curve_loss /Game/Anim/AS_Run
error ue_import_guard.material_slots /Game/Characters/SKM_Body
error ue_import_guard.required_bone_chain neck
warning ue_import_guard.anim_compression /Game/Anim/AS_Run
warning ue_import_guard.lod_allow_deformer /Game/Characters/SKM_Body[LOD0]
```

`root_motion=True` 这次没报，因为 `root` 确实在 `bones` 里——规则只在根骨骼不可用时才报。

**（2）只比两份快照。** `pelvis` 被改名成 `hips`，结构完全一致，因此判定为 `joint_renamed`：

```python
import copy, json
from tak.core import runner, registry
from tak.core.spec import load_spec

snap = copy.deepcopy(json.load(open("fixtures/good/good_001.json", encoding="utf-8"))["snapshot"])
after = copy.deepcopy(snap)
after["joints"]["hips"] = after["joints"].pop("pelvis")

core = registry.load_plugin_module(runner.find_plugin("ue_import_guard"), "core")
result = core.validate_import({"snapshot": snap}, after, load_spec())
print(result["passed"], result["reason"], result["ignored"])
print(json.dumps(result["blocking"][0], ensure_ascii=False))
```

实际输出：

```text
False 导入校验未通过：1 处阻断性偏差（joint_renamed） []
{"field": "joint_renamed", "before": "pelvis", "after": "hips", "blocking": true, "message": "关节被重命名：pelvis → hips"}
```

## 7. 边界与限制

- 只读，不改资产。所有规则与校验都不会保存、重导入或修改 UE 资产；`fix_hint` 只是操作说明。
- 需要你提供资产数据。`ue_assets` 与 `metadata.ue` 都没有时，9 条规则全部跳过，`run_report` 返回 `passed=True`、0 条 Finding、`ue_assets_present=false`（实测）。仓库里的 `tak/adapters/unreal/adapter.py` 目前只产出 `joints` / `skin` / `metadata` 的常规字段，**没有采集 `metadata.ue`**，所以 Qt 面板的「运行校验」在真机上也需要额外注入才能出结论。
- 只支持 unreal。`runner.run_tool` 在 `dcc` 不在 manifest 的 `["unreal"]` 里时直接抛 `ToolError`；本模块没有 Maya / Max / MoBu 分支。
- 忽略 `bind_matrix`，`frame_range` 永不阻断。UE Python 不暴露骨骼静止变换（真机探测记录在 `validation/ue_adapter_probe_result.json`），该字段只会是单位阵占位，参与阻断会造成全量误报，要开启必须显式设 `import_check_bind_matrices=True`；`frame_range` 被强制加入 warn 集合，只出 INFO。
- 不臆造缺失字段。骨骼列表、压缩方案、曲线数量、材质槽任一缺失就跳过对应规则；这是「宁可漏报也不误报」的取舍。
- 每规则最多 24 条。超出部分被截断，不汇总、不分页。
- `api_notes` 是静态说明。它固定返回 4 条（`MLDeformerAsset`、`NeuralMorphModel`、`DeformerGraphAsset`、`EditorDialogLibrary`），不检测当前工程是否真的缺这些能力。
- 不联网、不解析二进制。快照、sidecar、spec 都是 JSON 字典。

## 8. 与其它模块的关系

上游是 `export_bridge`：sidecar 由它的 `pre_export` 写出，payload 里带 `tool` / `version` / `sidecar_version` / `snapshot`；本模块的 `validate_import` 只吃 dict（若是文件，需要调用方先读出 JSON 再传进来）。UE 侧快照与 `ue_assets` 由调用方（UE Python 脚本或 CI）提供。

下游是报告层与 Web 面板片段：`run_report` 产出 `Report`，Qt 面板渲染表格，`ui_web.render_panel_html` 输出可嵌入的 HTML 片段（不写颜色值、不引外链、动态文本全部转义）。

共享契约字段：九类 diff 字段名（与 `export_bridge` 完全一致）、`sidecar_version`、`SkeletonSnapshot` 的 `joints` / `unit` / `up_axis` / `frame_range`、`spec.ue.*`。两边策略不同但字段相同，所以同一份 sidecar 在 DCC 侧和 UE 侧可以对照着看，不会出现两套命名。
