# 发布手册（Release Runbook）

> 面向维护者。一次发布 = 版本号、变更日志、证据回填、全量回归、打包、tag、推送，七步。
> 任何一步没做都算没发布 —— 尤其是「证据回填」，它决定别人能不能相信这个版本。

## 0. 前置检查

```bash
git status --short          # 必须是空
git pull --ff-only          # 本地必须是远端最新
python -m pytest -q         # 必须全绿
```

`git status` 不空就先别动版本号：混着未完成改动打 tag，之后的 `diff` 再也说不清哪个改动属于哪个版本。

## 1. 版本号

**唯一事实源是 `tak/version.py`**。其它地方一律引用它，不得硬编码：

```python
from tak.version import __version__          # 0.3.2
```

需要同步的位置（`tests/test_release_meta.py` 会逐项校验）：

| 位置 | 内容 |
|---|---|
| `README.md` | 首段「当前版本 `x.y.z`」 |
| `CHANGELOG.md` | 新增 `## [x.y.z] - YYYY-MM-DD` 段落 |
| `tak/install/writer.py` | `MARKER_VERSION`（安装器标记块版本） |
| `.github/workflows/ci.yml` | 无版本号，但需确认回归步骤齐全 |

语义化版本约定：

- **patch**：只修 bug、补测试、改文档，不动公共 API 与快照契约。
- **minor**：加插件、加能力位、加 API，旧行为保持不变。
- **major**：改 `SNAPSHOT_CONTRACT` / `DATASET_CONTRACT` / mapping asset 格式 —— 这类改动会让旧 sidecar 失效，必须写迁移说明。

## 2. 变更日志

`CHANGELOG.md` 按 [Keep a Changelog](https://keepachangelog.com/) 分组：`Added` / `Changed` / `Fixed` / `Removed` / `Known limitations`。

三条硬要求：

1. **Fixed 必须写清「原来错在哪」**，不是「修了 bug」。例如「`compare_snapshots` 的 `ignore` 只过滤顶层字段，关节字段仍参与比对，导致 UE 的能力边界被误判为差异」。
2. **Known limitations 必须如实写**。没验证的就写没验证，不要用「待优化」糊过去。
3. 每条尽量带文件路径，方便回溯。

## 3. 证据回填

| 证据 | 来源 | 落到哪 |
|---|---|---|
| 四端一致性 | `python validation/four_dcc_regression.py` | `validation/four_dcc_regression_result.json` |
| Maya 真机 | `mayapy validation/maya_full_regression.py` | `validation/maya_full_regression_<ver>.json` |
| UE 探测 | `validation/run_probe.py` | `validation/ue_probe_*.json` |
| 跨版本冒烟 | `python validation/py_compat_smoke.py` | 终端输出，粘进 `docs/VALIDATION.md` |
| 人工复验 | `docs/VALIDATION.md` B/W 清单 | 回填格式 `B11 ✅ 3dsMax2025 / Python3.11 / 备注：…` |

**没回填的项目必须在报告里标「未验证」**。`docs/BATCH*_REPORT.md` 的「已知限制」与「需要用户复验」两节就是为此存在的，不要删。

## 4. 全量回归

```bash
python -m pytest -q                                          # 全绿
python -m pytest --cov=tak.core --cov-fail-under=80 -q       # 核心层门槛
python fixtures/generate_fixtures.py --check                 # fixtures 未漂移
python tak/ui/generate_theme.py --check                      # 设计 token 未漂移
python validation/py_compat_smoke.py                         # 无 pytest / 无 DCC
python validation/four_dcc_regression.py                     # 四端一致
python tools/new_tool.py --list                              # 插件可发现
python -m tak.install --list                                 # 安装器可探测（dry-run）
```

外加两条 CI 也在跑、但本地容易忘的：

```bash
# 全部 shipped Python 按 3.9 语法解析（DCC 内置解释器最老就是 3.9）
python - <<'PY'
import ast, os, sys
skip = ('.git', '.pytest_cache', '__pycache__', '.workbuddy', 'legacy',
        'Intermediate', 'Saved', 'DerivedDataCache', 'TakProbe')
bad = []
for root, dirs, files in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in skip]
    for name in files:
        if name.endswith('.py'):
            path = os.path.join(root, name)
            try:
                ast.parse(open(path, encoding='utf-8').read(), feature_version=(3, 9))
            except (SyntaxError, UnicodeError) as exc:
                bad.append((path, str(exc)))
print('Python 3.9 incompatible:', bad)
sys.exit(1 if bad else 0)
PY

# core 层不得出现真实 DCC import
python - <<'PY'
import os, re, sys
pattern = re.compile(r"^\s*(from|import)\s+(maya|pymxs|pyfbsdk|unreal)\b", re.M)
bad = [os.path.join(b, n) for b, _, fs in os.walk('tak/core') for n in fs
       if n.endswith('.py') and pattern.search(open(os.path.join(b, n), encoding='utf-8').read())]
print('DCC imports in core:', bad)
sys.exit(1 if bad else 0)
PY
```

## 5. 打包

当前不产出 wheel（这是工具箱不是库），发布物是**仓库快照 + 安装器**：

```bash
# 记录仓库快照（用于复现某次发布时的确切内容）
git archive --format=zip --prefix=tech-anim-kit-<ver>/ HEAD -o tech-anim-kit-<ver>.zip
```

安装到四端：

```bash
python -m tak.install --list                                    # 先看探测结果
python -m tak.install --plan --dcc maya,max,mobu                # 再看将要做什么
python -m tak.install --apply --dcc maya,max,mobu               # 真写（会备份）
python -m tak.install --uninstall --apply --dcc maya            # 卸载
```

**默认 dry-run**。`--apply` 前必须看过 `--plan` 输出，尤其是 `[replace-block]` 的行 —— 那意味着你之前手改过块内内容。

## 6. Tag 与推送

```bash
git add -A
git commit -m "release: v<ver>"
git tag -a v<ver> -m "tech-anim-kit v<ver>"
git push origin main
git push origin v<ver>
```

推送失败先分清两类：

- `Empty reply from server` / `Connection was reset` / `443 超时` → 网络问题，**重试即可**，不要改历史。
- `rejected (fetch first)` → 远端有新提交，先 `git pull --rebase`，不要 `--force`。

本地 tag 打完但推送失败时，**不要重新打 tag**：`git push origin v<ver>` 再试即可；tag 已存在于远端说明之前那次其实成功了。

## 7. 发布后

```bash
python -c "import tak; print(tak.__version__)"     # 与 tag 一致
python -m tak.ui.web.server --port 8765            # 面板能起、版本号正确
```

然后把本次发布写进 `.workbuddy/memory/YYYY-MM-DD.md`（版本、用例数、修掉的真问题、待复验项）。下一次发布时，这份记录比 commit message 有用得多。

## 回滚

发布错了不要 `git reset --hard` 抹历史。按影响面分三种：

| 影响面 | 做法 |
|---|---|
| 只是代码 bug | 打补丁版本 `x.y.z+1`，正常走一遍流程 |
| 安装器写坏了某端 | `python -m tak.install --uninstall --apply --dcc <端>`，从 `<file>.tak-backup-<N>` 恢复 |
| 快照 / 数据集契约不兼容 | 保留旧版 sidecar 读取分支，写迁移脚本，升 major；**不要**直接改 `CONTRACT_VERSION` 常量糊过去 |
