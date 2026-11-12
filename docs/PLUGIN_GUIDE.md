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
