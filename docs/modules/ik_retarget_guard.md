# 重定向映射校验（`ik_retarget_guard`）

> 离线校验 IK Rig / IK Retargeter 的链映射、命名对齐、Root 与 Pelvis 偏移、姿态偏差，并把候选映射固化成可交接的 v1.0 资产。

**适用版本** TAK `0.3.2`　·　**DCC** unreal　·　**权限** `scene.read`　·　**UI** `both`

## 1. 它解决什么问题

IK Retargeter 里最容易「看着好其实坏了」的三件事，都在这条线上：链映射漏一条（例如只配了 `LeftArm` 没配 `Spine`，重定向后上身整段不动，但手臂还在摆，肉眼很难第一时间定位）；两端命名不一致（`hand_L` 对 `LeftHand`，词典能猜，猜错就把手挂到小臂末端）；Root 与 Pelvis 补偿偏移单位搞错（0.03 在米制骨架里是 3 厘米，在厘米制里是 3 毫米，阈值判断完全两个结果）。

这些都能离线算。`ik_retarget_guard` 读一份 retarget 数据（IK Rig 链、源/目标骨骼列表、Retargeter 链映射、偏移向量、实测姿态偏差），跑六条确定性规则，输出 `passed` 与逐条 Finding；同时用离线词典给出一份骨骼候选映射，让你不用对着两列名字硬看。

它只读快照，不 import `unreal`，也不改任何 IK Rig 资产。修法写在 `fix_hint` 与 `Finding.data["fix"]` 里，是给你照着做的人工步骤，不是自动执行的动作。

## 2. 怎么打开

| 入口 | 操作 |
|---|---|
| Maya 菜单 | 未挂菜单，用 Python 入口。本模块只声明 `unreal` 一个 DCC，Maya 菜单本来也不会列它 |
| Python | 见下 |
| Web 面板 | 无专属页签。「骨骼映射」页签走的是 `skeleton_mapper` 的 `POST /api/mapping`，与本模块无关；要跑本模块用 `POST /api/run` 并带 `tool_id` |
| Qt 面板 | 无。模块目录只有 `ui_web.py`，没有 `ui_qt.py` |

```python
from tools.ik_retarget_guard import core

inspected = core.inspect_retarget({"retargeter": {"root_offset": [1, 0, 0]}})
print(inspected["passed"], inspected["findings"][0]["rule_id"])
```

```python
from tak.core.runner import run_tool

result = run_tool("ik_retarget_guard", {"metadata": {"retarget": data}},
                  dcc="unreal")
print(result.passed, result.report.counts())
```

## 3. 输入

数据从 `ctx.options["retarget"]` 或 `ctx.snapshot["metadata"]["retarget"]` 读取，**`options` 优先**。注意 `POST /api/run` 不透传 `options`，所以 Web 路径必须把 `retarget` 放进 `snapshot.metadata.retarget`。

`inspect_retarget(data, spec=None)` 的 `data` 就是下面这个 retarget 段（所有字段均可缺省）：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `ik_rig.chains` | `list[object]` | 缺省跳过 | 链对象含 `name` / `bones`；用于完整性与必需链校验 |
| `ik_rig.retarget_root` | `str` | 缺省不推断 | 重定向根骨 |
| `ik_rig.goal` | `object` | 缺省跳过 | IK Goal 采集数据，本批次只透传不判定 |
| `source_bones` / `target_bones` | `list[str]` | 缺省跳过 | 两者同时存在才启用命名映射与集合差异 |
| `retargeter.chain_mapping` | `object` / `list` | 缺省跳过 | 源链到目标链；列表形式可保留重复 source 以检测冲突 |
| `retargeter.root_offset` / `pelvis_offset` | `[float,float,float]` | 缺省跳过 | 按向量长度比较阈值；非三元或含非有限数直接跳过 |
| `pose_deviation.max` | `float` | 缺省跳过 | 全局最大实测偏差 |
| `pose_deviation.per_bone` | `object[str,float]` | 缺省跳过 | 逐骨骼**实测**偏差，不是阈值 |
| `required_chains` | `list[str]` | 见下 | 覆盖默认必需链；空列表表示不强制 |
| `thresholds` | `object` | `{}` | `root_offset` / `pelvis_offset` / `pose_deviation` / `pose_per_bone` |

阈值优先级（`root_offset` / `pelvis_offset` / `pose_deviation` 三处一致）：`data.thresholds[名字]` → `data[名字 + "_max"]` → `spec.ue.retarget_<名字>_tolerance` → 缺省 `0.01`。逐骨骼容差按 `data.thresholds.pose_per_bone` → `data.pose_deviation_thresholds.per_bone` → `spec.ue.retarget_pose_per_bone_tolerances` → 全局容差顺序命中。

必需链默认 `Spine` / `LeftArm` / `RightArm` / `LeftLeg` / `RightLeg`，可由 `data.required_chains` 或 `spec.ue.retarget_required_chains` 覆盖。仓里默认规范（`schema/rig_spec.default.json`）没有 `retarget_*` 这些键，所以不显式配置时走的就是插件缺省值。

**映射资产的版本与必需字段**（`tools/ik_retarget_guard/mapping.py`，写读共用）：

| 字段 | 必需 | 校验规则 |
|---|---|---|
| `version` | 是 | 必须 `startswith("1.")`，否则 `load_mapping_asset` 抛 `ValueError` |
| `generated_by` | 是 | 缺字段即拒绝读取 |
| `entries` | 是 | 必须是列表；每项 `source` / `target` 为非空 `str`，`confidence` 为有限数值，`method` 缺省补 `dictionary` |
| `conflicts` | 是 | 必须是列表；不合法（非对象）的条目在写盘时被丢弃 |
| `metadata` | 是 | 必须是对象；写盘时补 `unmapped_source` / `unmapped_target` / `consumers` / `offline` |

## 4. 输出

`inspect_retarget()` 返回严格可 JSON 序列化的五个键：

| 字段 | 类型 | 说明 |
|---|---|---|
| `passed` | `bool` | 仅当 `blocker` 与 `error` 都为 0 时为真；`warning` 不阻断 |
| `findings` | `list[dict]` | 每条是 `Finding.to_dict()`：`rule_id` / `severity` / `message` / `node` / `fix_hint` / `data` |
| `diffs` | `list[dict]` | `{"field": "source_bone_only" / "target_bone_only", "bone", "candidate"}`，候选只作建议 |
| `mapping` | `dict` | `suggest_mapping` 结果：`entries` / `unmapped_source` / `unmapped_target` / `conflicts` |
| `summary` | `dict` | `total` / `counts`（`blocker` / `error` / `warning` / `info`）/ `passed` / `mapped` / `unmapped_source` / `unmapped_target` / `conflicts` / `diffs` / `retarget_present` |

真实输出（`ik_rig` 两条链、漏配 `Spine`、root 偏移 0.03、全局姿态偏差 0.02、缺 `RightArm`）：

```json
{
  "passed": false,
  "summary": {"total": 7,
              "counts": {"blocker": 0, "error": 4, "warning": 3, "info": 0},
              "passed": false, "mapped": 3,
              "unmapped_source": 0, "unmapped_target": 0,
              "conflicts": 0, "diffs": 6, "retarget_present": true},
  "findings": [
    {"rule_id": "ik_retarget_guard.chain_mapping_completeness", "severity": "error",
     "node": "Spine",
     "message": "IK Rig 链 'Spine' 缺少有效的 Retargeter chain_mapping"},
    {"rule_id": "ik_retarget_guard.root_pelvis_offset", "severity": "error",
     "node": "root",
     "message": "root_offset 的向量长度 0.03 超过阈值 0.01"},
    {"rule_id": "ik_retarget_guard.bone_naming_mismatch", "severity": "warning",
     "node": "upperarm_L",
     "message": "骨骼命名不一致：'upperarm_L' 可映射到 'LeftUpArm'（置信度 0.85）"}
  ]
}
```

`run_report()` 的 `metadata` 额外带 `rule_ids`（六条）、`rules_count`、`retarget_present`、`summary`、`diffs`、`mapping`、`mapping_summary`、`chain_mapping`、`data_contract`、`handoff`。上面 `findings` 是节选三条，`summary.total` 才是真实条数（7）。`chain_mapping` 每行是 `{"source_chain", "target_chain", "status"}`，`status` 取值 `mapped` / `missing_mapping` / `unknown_source`；`mapping_summary` 是 `entries` / `average_confidence` 与三个计数的精简版。

## 5. 校验项 / 规则清单

六条规则全部通过 `@rule(..., plugin_id="ik_retarget_guard", dcc=["unreal"])` 注册，`info()["rule_count"]` 是 `6`。数据段整体缺失时六条全部返回空列表；单个字段缺失只跳过对应检查，绝不产生 ERROR / BLOCKER。

| rule_id 或 code | 严重级 | 触发条件 | 建议 |
|---|---|---|---|
| `ik_retarget_guard.chain_mapping_completeness` | error | IK Rig 链有、Retargeter `chain_mapping` 无；或映射引用了不存在的源链 | 为该源链选目标链；移除失效映射或补建同名链 |
| `ik_retarget_guard.bone_naming_mismatch` | error / warning | 源骨在目标骨架里没有同名项。有词典候选时该条降为 **warning**；无候选保持 error；目标骨无源映射恒为 warning | 确认候选后写入 Skeleton Mapper / IK Retargeter，不要直接重命名资产 |
| `ik_retarget_guard.root_pelvis_offset` | error | `root_offset` 或 `pelvis_offset` 的向量长度超过阈值（缺省 0.01） | 检查单位、骨架比例与 Retarget Root，重置补偿后重新烘焙预览 |
| `ik_retarget_guard.pose_deviation` | error | `pose_deviation.max` 或 `per_bone` 任一骨骼偏差超过容差 | 重新匹配参考姿态与链朝向，烘焙后重新采样偏差 |
| `ik_retarget_guard.required_chains` | error | IK Rig 链名（归一化后）不含必需链之一 | 补建该链并指定起止骨骼，再配链映射 |
| `ik_retarget_guard.mapping_conflicts` | error | `chain_mapping` 里同一源链多条映射（`duplicate_source`）、同一目标链被多源占用（`shared_target`），或骨骼候选有碰撞 | 只保留一条经人工确认的映射；未解决前不得自动写入 |

`Finding.data["fix"]` 里的 `op` 是给人看的动作标识，不是可执行指令：`set_chain_mapping` / `remove_chain_mapping` / `set_bone_mapping` / `review_bone_mapping` / `set_retarget_offset` / `recheck_retarget_pose` / `create_ik_chain` / `resolve_chain_conflict` / `resolve_bone_conflict`。

## 6. 典型用法

第一段：只查 root 偏移，最小输入。

```python
from tools.ik_retarget_guard import core

r = core.inspect_retarget({"retargeter": {"root_offset": [1, 0, 0]}})
print(r["passed"], r["summary"]["counts"]["error"], r["findings"][0]["message"])
```

```text
False 1 root_offset 的向量长度 1 超过阈值 0.01
```

第二段：出一份骨架候选映射并落成 v1.0 资产。

```python
from tak.core import registry, runner

mapping = registry.load_plugin_module(
    runner.find_plugin("ik_retarget_guard"), "mapping")
suggestion = mapping.suggest_mapping(["upperarm_L", "hand_L"],
                                     ["LeftUpArm", "LeftHand"])
print(suggestion["entries"])
path = mapping.write_mapping_asset(suggestion, "tak_out/bones.json")
print(mapping.load_mapping_asset(path)["version"])
```

```text
[{'source': 'hand_L', 'target': 'LeftHand', 'confidence': 0.85, 'method': 'dictionary'}, {'source': 'upperarm_L', 'target': 'LeftUpArm', 'confidence': 0.85, 'method': 'dictionary'}]
1.0
```

第三段：Web 路径。`/api/run` 只吃 `snapshot`，所以 `retarget` 要挂在 `metadata` 下。

```json
POST /api/run
{"tool_id": "ik_retarget_guard", "dcc": "unreal",
 "snapshot": {"dcc": "unreal", "joints": {},
              "metadata": {"retarget": {"retargeter": {"root_offset": [1, 0, 0]}}}}}
```

```text
200 {"ok": true, "tool_id": "ik_retarget_guard", "passed": false,
     "toolkit_version": "0.3.2",
     "counts": {"info": 0, "warning": 0, "error": 1, "blocker": 0}}
```

## 7. 边界与限制

**只读，不改 IK Rig 资产。** 六条规则只做判定，`fix` 字段是结构化建议；插件不调用任何 `unreal` 接口，采集 IK Rig / Retargeter 数据是 adapter 与调用方的事。UE 5.8 的 `IKRigDefinition` / `IKRetargeter` / `ControlRig` 确实有 Python 暴露，本插件仍然不 import `unreal`——判定与采集分开，才能在无引擎的机器上跑 CI。

**字段缺失一律跳过，绝不臆造数值。** 没有 `pose_deviation` 就不评姿态偏差，没有 `chain_mapping` 就不评链完整性。代价是「数据采集不全时可能全绿」：`summary.retarget_present` 与 `metadata.rules_count` 要一起看，`retarget_present: false` 的报告不代表通过。

**`pose_deviation.per_bone` 是实测值，不是阈值。** 阈值来自 `thresholds.pose_per_bone` 或规范；把两者搞混会让整份报告失真。

**不改写骨骼名。** `diffs[].candidate` 只是候选，报告里不会出现「已帮你重命名」。修改骨架命名牵动蒙皮与动画曲线，必须人工做。

**两套归一化不完全相同。** 本模块的 `normalize_bone_name` 把侧别收敛成 `_l` / `_r`（`upperarm_L` → `upperarm_l`），`tak/core/mapping/semantics.py` 收敛成 `_left` / `_right`；置信度分档也不同（本模块 1.0 / 0.95 / 0.85，语义层 1.0 / 0.95 / 0.55）。语义组判定一致，但数字不要互相引用。

## 8. 与其它模块的关系

**对 `skeleton_mapper` 是上游。** 本模块的 `suggest_mapping` 结果经 `write_mapping_asset` 固化成 v1.0 资产，`skeleton_mapper` 用 `load_mapping` 读回，把已确认条目当基线（`origin="asset"`），词典与 AI 候选只填空位。两边共用同一个 writer / reader，字段与排序规则一致；资产里的 `conflicts` 与 `metadata.unmapped_*` 是必须保留的人工复核队列。

**对体检/规范模块是同一份快照契约。** 数据挂在 `snapshot["metadata"]["retarget"]` 下，与 `rig_health_guard`、`spec_enforcer`、`export_bridge` 共用 `SkeletonSnapshot.to_dict()` 的结构（`dcc` / `version` / `unit` / `up_axis` / `joints` / `frame_range` / `skin` / `metadata`）。这也意味着一个快照可以同时喂给多个工具，不需要各模块自定义容器。

**交接目标不止 UE。** `run_report` 的 `handoff.targets` 写的是 `UE IK Retargeter`、`MotionBuilder Characterization`、`3ds Max Biped`——同一份 `entries` 可以翻译到另外两套命名体系，`ik_rig` 与 `retargeter` 那两段是 UE 专有的，不跟着走。
