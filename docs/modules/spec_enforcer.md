# 规范校验与修复（`spec_enforcer`）

> 用 `rig_spec` 当尺子量骨架，把不合规的地方翻译成参数化修复计划；计划默认只预览，写场景必须你亲手确认。

**适用版本** TAK `0.3.3`　·　**DCC** maya / max / mobu　·　**权限** `scene.read` / `node.write`　·　**UI** `qt`

## 1. 它解决什么问题

命名不合规的关节。`LeftUpArm` 在 Maya 里读起来很自然，但规范要求 `upperarm_L`，重定向、动画曲线匹配、引擎侧骨骼对应都会因此多出一轮人工映射。`naming_pattern` 用 `naming.joint_pattern` 正则逐条比对，命中不了的关节直接报 ERROR，并把「按哪个正则改名」写进建议。

缺必需关节与层级过深。少一根 `foot_L` 时，绑定看着能用，导出到引擎才发现骨架对不上；层级 13 层的辅助骨骼会把动画误差逐级放大。`required_joints` 与 `max_depth` 分别用 `skeleton.required_joints`、`skeleton.max_depth` 判定，不依赖任何经验值。

蒙皮的两条硬指标。单顶点影响骨超过 `skin.max_influences`，引擎侧会被截断；权重和偏离超出 `skin.weight_sum_tolerance`，蒙皮会整体偏胖或偏瘦。两条规则都按 spec 的阈值判定，不给「差不多就行」的余地。

真正让这个模块不同的是第三步：每条问题都带一个 `{"op": ..., "params": {...}}` 的修复描述，`plan_fixes` 把它们翻译成 `ExecutionPlan`，`preview` 给你逐条 diff，`apply_plan` 默认 dry-run。规范校验因此不只是「报错清单」，而是一份可以审、可以改、可以只执行一半的施工单。

## 2. 怎么打开

| 入口 | 操作 |
|---|---|
| Maya 菜单 | `TAK / Tech Anim Kit → 规范校验`（`MENU_ITEMS` 显示名「规范校验」，动作 `tak_spec_enforcer`） |
| Python | 见下；`run_tool` 只出报告，要计划和预览需加载插件 `core` |
| Web 面板 | 无。`panel.html` 的 `data-tab` 没有规范校验页签 |
| Qt 面板 | `tools/spec_enforcer/ui_qt.py` 的 `show(dcc="maya", omui=None)`；「运行规范校验」「应用修复」，中间是结果表与 diff 预览，底部日志台 |

最小可执行代码（在仓库根目录运行）：

```python
import copy, json
from tak.core import runner
from tak.adapters.base import NullAdapter
from tak.core.spec import load_spec

snap = copy.deepcopy(json.load(open("fixtures/good/good_001.json", encoding="utf-8"))["snapshot"])
snap["joints"]["LeftUpArm"] = {"parent": "clavicle_L", "depth": 7,
                               "scale": [1.0, 1.0, 1.0],
                               "rotate": [0.0, 0.0, 0.0], "namespace": ""}
res = runner.run_tool("spec_enforcer", snap, dcc="maya", adapter=NullAdapter(),
                      spec=load_spec(), options={"spec_enforcer": True})
print(res.passed, res.report.metadata["plan_count"])
```

注意菜单入口：`tak/adapters/maya/menu.py` 的 `_MENU_OPTIONS` 给本模块传 `{"spec_enforcer": True}`，与 Qt 面板一致，所以点菜单点面板都能出结果。从脚本调用才需要自己开作用域（见第 3 节），漏传时会得到一条「未开启作用域」的提示，而不是静默通过。

## 3. 输入

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `ctx` | `ToolContext` | 必填 | `run_report` / `plan_fixes` / `preview` 的唯一参数 |
| `options["spec_enforcer"]` | `bool` 或 `dict` | 未设置即关闭 | `True` 开启；`dict` 里可放 `joint_pattern` / `required_joints` / `max_depth` / `weight_sum_tolerance` / `max_influences` / `control_namespace` / `controls` |
| `plan` | `ExecutionPlan` | 必填 | `apply_plan` 的第一个参数，通常来自 `plan_fixes` |
| `adapter` | `DCCAdapter` | 必填 | `apply_plan` 的第二个参数，真正写场景只走它 |
| `granted` | `list[str]` | `None` | 权限白名单；不含 `node.write` 时执行器抛 `DSLError` |
| `confirm` | `bool` | `False` | 默认强制 dry-run，绝不写场景 |
| `dry_run` | `bool` 或 `None` | `None` | `None` 时按 `not confirm` 推导 |

作用域也可以用快照开启：`snapshot["metadata"]["spec_enforcer"]` 是 dict 且 `enabled` 不为 `False`。两条路径都没有时，**7 条规则整组不产出结论**（对普通快照零误报），但会追加一条 `spec_enforcer.scope.disabled`（INFO）说明「本次未执行任何规范规则」——不这样写的话，界面会把「跳过」显示成「规范全部合格」。菜单入口会自动开启作用域，从脚本调用才需要自己传。

`snapshot` 需要的键：

- `joints.<关节名>.depth`、`.namespace`、`.scale`、`.rotate` —— 层级与命名规则读这里；
- `joints` 的键集合本身用于命名正则与必需关节比对；
- `skin.max_influences_found`、`skin.weight_sum_deviation`；
- `metadata.spec_enforcer`（可选）—— 作用域开关。

作用域 `controls` 是列表，元素可以是字符串，也可以是 `{"name": ..., "namespace": ..., "color": ...}`；控制器命名空间与颜色两条规则只读这里，不读 `joints`。

`spec` 需要的键：`naming.joint_pattern`、`naming.control_namespace`、`skeleton.required_joints`、`skeleton.max_depth`、`skin.max_influences`、`skin.weight_sum_tolerance`。作用域里的同名字段优先于 spec。

## 4. 输出

`run_report` 除标准 `Report` 字段外，把修复计划写进 `metadata`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `metadata.fix_plan` | `dict` | `{"actions": [{"tool": ..., "params": {...}}]}`，可直接 `ExecutionPlan.from_dict` |
| `metadata.plan_count` | `int` | 计划动作数 |
| `metadata.skipped_fixes` | `list` | 没有对应 DSL 动作的问题，含 `rule_id` / `node` / `op` / `params` / `reason` |
| `metadata.scope` | `dict` | `enabled` / `key` / `note`；`enabled=false` 说明规则没跑 |
| `metadata.dry_run_only` | `bool` | 固定 `True` |
| `metadata.legend` | `dict` | 三条中文说明：dry_run / confirm / manual |

`fixtures/good/good_001.json` 的 snapshot 加一个 `LeftUpArm`、并注入两个控制器后，`fix_plan` 原文：

```json
    "fix_plan": {
      "actions": [
        {
          "tool": "spec_enforcer.rename_joint",
          "params": {
            "node": "LeftUpArm",
            "pattern": "^(root|pelvis|spine_[0-9]{2}|neck|head|clavicle_[LR]|upperarm_[LR]|lowerarm_[LR]|hand_[LR]|thigh_[LR]|calf_[LR]|foot_[LR]|toe_[LR]|FACIAL_[A-Za-z0-9_]+)$"
          }
        },
        {
          "tool": "spec_enforcer.set_namespace",
          "params": {
            "node": "ctl_arm_R",
            "namespace": "CTRL"
          }
        },
```

同一次运行的 `plan_count` 是 `3`，`skipped_fixes` 是 `[]`。计划里的动作名就是 `ACTION_REGISTRY` 里已注册的名字，参数已通过声明式校验（类型、必填、范围、严格 JSON），所以能进计划的都是「可执行形状」的。删掉 `foot_L`、再加一根 13 层的 `tail_01` 时，实测 `plan_count` 为 `1`，`skipped_fixes` 里是 `reparent_joint` 与 `create_joint` 两条，`reason` 都是「没有对应的 DSL 动作，需人工在 DCC 内处理」。

## 5. 校验项清单

规则定义在 `tools/spec_enforcer/rules.py`，`plugin_id="spec_enforcer"`，全部 `dcc=["maya"]`，且都要求作用域已开启。

| rule_id | 严重级 | 触发条件 | 建议 |
|---|---|---|---|
| `spec_enforcer.naming_pattern` | ERROR | 关节名不匹配 `naming.joint_pattern`（或作用域 `joint_pattern`） | 按正则重命名；目标名需人工确认 |
| `spec_enforcer.required_joints` | ERROR | `skeleton.required_joints` 里的名字不在 `joints` 中 | 补建关节并重建权重 |
| `spec_enforcer.max_depth` | ERROR | 任一关节 `depth` 大于 `skeleton.max_depth` | 压平辅助骨骼，或合并到父级 |
| `spec_enforcer.weight_sum` | ERROR | `abs(skin.weight_sum_deviation)` 超出容差加 `1e-4` | normalize weights |
| `spec_enforcer.influence_limit` | ERROR | `skin.max_influences_found` 大于 `skin.max_influences` | limit influences 后再 normalize |
| `spec_enforcer.control_namespace` | WARNING | 作用域 `controls[]` 的 `namespace`（去掉尾部冒号）不等于 `naming.control_namespace` | 把控制器移入目标命名空间 |
| `spec_enforcer.control_color` | INFO | 作用域 `controls[]` 的 `color` 缺失或为 `default` / `none` | 按左红右蓝设置 override color |

正则本身写错时不报任何 Finding（`re.error` 直接返回空），因为那属于规范文件的问题，交给 schema 校验器处理。

上表之外还有一条**不是规则**的提示：作用域未开启时，`run_report` 会追加 `spec_enforcer.scope.disabled`（INFO），message 为「规范校验未开启作用域，本次未执行任何规范规则；结论为空不代表符合规范」。不计入 `rules_count`，不影响 `passed`。

## 6. 典型用法

**（1）看报告与计划数量。** 控制器 `ctl_arm_R` 既不在 `CTRL` 下也没上色，因此同时命中两条：

```python
import copy, json
from tak.core import runner
from tak.adapters.base import NullAdapter
from tak.core.spec import load_spec

snap = copy.deepcopy(json.load(open("fixtures/good/good_001.json", encoding="utf-8"))["snapshot"])
snap["joints"]["LeftUpArm"] = {"parent": "clavicle_L", "depth": 7,
                               "scale": [1.0, 1.0, 1.0],
                               "rotate": [0.0, 0.0, 0.0], "namespace": ""}
options = {"spec_enforcer": {"controls": [
    {"name": "ctl_arm_L", "namespace": "CTRL:", "color": 13},
    {"name": "ctl_arm_R", "namespace": "", "color": None},
]}}
res = runner.run_tool("spec_enforcer", snap, dcc="maya", adapter=NullAdapter(),
                      spec=load_spec(), options=options)
print(res.passed, res.report.counts())
for item in res.report.findings:
    print(item.severity.value, item.rule_id, item.node)
print("plan_count", res.report.metadata["plan_count"])
```

实际输出：

```text
False {'info': 1, 'warning': 1, 'error': 1, 'blocker': 0}
error spec_enforcer.naming_pattern LeftUpArm
warning spec_enforcer.control_namespace ctl_arm_R
info spec_enforcer.control_color ctl_arm_R
plan_count 3
```

**（2）预览与 dry-run 执行。** 默认 `confirm=False`，`apply_plan` 只回一份待办清单，连只读的宿主查询都不发生：

```python
import copy, json
from tak.core import runner, registry
from tak.adapters.base import NullAdapter
from tak.core.spec import load_spec

snap = copy.deepcopy(json.load(open("fixtures/good/good_001.json", encoding="utf-8"))["snapshot"])
snap["joints"]["LeftUpArm"] = {"parent": "clavicle_L", "depth": 7,
                               "scale": [1.0, 1.0, 1.0],
                               "rotate": [0.0, 0.0, 0.0], "namespace": ""}
ctx = runner.ToolContext(dcc="maya", snapshot=snap, adapter=NullAdapter(),
                         spec=load_spec(),
                         options={"spec_enforcer": {"controls": [
                             {"name": "ctl_arm_R", "namespace": "", "color": None}]}},
                         tool_id="spec_enforcer")
core = registry.load_plugin_module(runner.find_plugin("spec_enforcer"), "core")
plan = core.plan_fixes(ctx)
print([(a.tool, a.params) for a in plan.actions])
print([(r["index"], r["tool"], r["permissions"]) for r in core.preview(ctx)])
out = core.apply_plan(plan, NullAdapter())
print(out["dry_run"], out["committed"], out["required_permissions"])
print([c["status"] for c in out["changes"]])
```

实际输出（第一条动作的 `params` 是完整的命名正则，此处按原样打印）：

```text
[('spec_enforcer.rename_joint', {'node': 'LeftUpArm', 'pattern': '^(root|pelvis|spine_[0-9]{2}|neck|head|clavicle_[LR]|upperarm_[LR]|lowerarm_[LR]|hand_[LR]|thigh_[LR]|calf_[LR]|foot_[LR]|toe_[LR]|FACIAL_[A-Za-z0-9_]+)$'}), ('spec_enforcer.set_namespace', {'node': 'ctl_arm_R', 'namespace': 'CTRL'}), ('spec_enforcer.set_control_color', {'node': 'ctl_arm_R', 'color': 6})]
[(0, 'spec_enforcer.rename_joint', ['node.write']), (1, 'spec_enforcer.set_namespace', ['node.write']), (2, 'spec_enforcer.set_control_color', ['node.write'])]
True False ['node.write']
['pending', 'pending', 'pending']
```

## 7. 边界与限制

- 默认什么都不做。没有 `options["spec_enforcer"]` 也没有 `metadata.spec_enforcer` 时，7 条规则全部返回空，`metadata.scope.enabled` 为 `false`。这是为了不污染其它工具与 golden fixtures，但也意味着忘了开作用域就会得到一份「假通过」。
- 只在 Maya 判。manifest 写了 `max` / `mobu`，但规则声明 `dcc=["maya"]`，在这两端选中不到规则；三端的写方法（`rename_node` / `set_control_color` / `normalize_weights` / `set_namespace`）都已实现，可执行的动作在 Max / MoBu 里仍然能跑。
- 只有四类修复动作。`create_joint` 与 `reparent_joint` 没有 DSL 动作，只在 `skipped_fixes` 里提示，因为补建关节需要重建权重，自动做无法保证结果可用。
- 不猜重命名目标。`rename_joint` 必须有显式 `name`；只有 `pattern` 时动作返回 `skipped`，但执行器把该步标成 `done`（动作函数没抛异常），实测 `committed=True`、`changes` 全 `done`、adapter 日志里是一条 warning。因此「已提交」不等于「已改名」，落库前要看日志。
- 默认 dry-run。`apply_plan` 的 `confirm` 默认 `False`；要真正写入必须 `confirm=True` 且 `granted` 覆盖 `node.write`，否则执行器抛 `DSLError`。
- 不改规范文件。正则非法、字段类型不对都不在这里报，交给 `tak/core/spec/schema.py`。
- 不读写二进制、不联网。快照与 spec 都是 JSON 字典，计划是纯 JSON。

## 8. 与其它模块的关系

上游是快照与规范：`tak/core/diff/snapshot.py` 产出 `joints` / `skin`，`tak/core/spec.load_spec` 读 `schema/rig_spec.default.json`。控制器信息不在标准快照里，需要调用方通过作用域 `controls` 注入。

下游是受限 DSL：`plan_fixes` 产出 `ExecutionPlan`，`tak/core/dsl/executor.py` 负责白名单、权限、dry-run、`begin_undo`、逐条执行与整体回滚；真正改场景由 `tak/adapters/{maya,max,mobu}` 的四个写方法完成。`agent_console` 生成的候选也使用同一批 `spec_enforcer.*` 动作，两边共享 `ACTION_REGISTRY`。

共享契约字段：`data["fix"] = {"op", "params"}`（规则的修复描述）、动作名 `spec_enforcer.rename_joint` / `.set_control_color` / `.normalize_weights` / `.set_namespace`、以及 `snapshot.skin.*` 与 `spec.skin.*`。与 `rig_health_guard` 的分工是：那边只报告、给 dry-run 文本；这边把可自动化的部分变成可校验、可回滚的计划。
