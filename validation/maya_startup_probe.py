# -*- coding: utf-8 -*-
"""在**真实 Maya** 里验证 TAK 启动链路，不需要 GUI。

为什么需要它
------------
``pytest`` 里的 Maya 用例用的是假 ``maya.cmds``，能验证逻辑，但验证不了
「用户脚本目录到底是哪个」「``userSetup.py`` 有没有被执行」「``import tak``
能不能通」这类只有真宿主才知道的事。0.3.1 就是栽在这里：安装块写进了
``Documents/maya/2026/scripts``，而中文界面 Maya 的用户脚本目录是
``Documents/maya/2026/zh_CN/scripts`` —— 单测全绿，真机上一个字都没执行。

``mayabatch.exe`` 有真 ``maya.cmds``、没有主窗口，正好覆盖「启动 + 导入 +
降级路径」这一段；菜单栏是否出现仍然只能靠人开 GUI 看。

两种跑法
--------
1. 普通 Python（推荐，自动找 ``mayabatch.exe``）::

       python validation/maya_startup_probe.py
       python validation/maya_startup_probe.py --maya-version 2024

2. 已经把自己塞进 Maya（``maya.cmds`` 可导入）——本文件会直接探测。

结果写进 ``validation/maya_startup_probe_result.json``，同时打印人读摘要。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RESULT_PATH = os.path.join(HERE, "maya_startup_probe_result.json")

#: 写进 Maya 的 mel 壳：显式 utf-8 读本文件（Maya 默认用 locale 编码，中文会炸），
#: 注入 ``__file__``（exec 进空命名空间时它不存在，模块顶层的 HERE 会 NameError），
#: 并**显式调用 main()** —— 不能指望 ``__name__ == "__main__"``。
_MEL = ("python(\"import io; _ns = {'__file__': r'%s'}; "
        "exec(compile(io.open(r'%s', 'r', encoding='utf-8').read(), 'tak_probe', 'exec'), _ns); "
        "_ns['main']()\");\nquit -f;\n")


# --------------------------------------------------------------------------- #
# 在 Maya 内部：收集证据
# --------------------------------------------------------------------------- #
def _probe_inside_maya() -> dict:
    import maya.cmds as cmds

    report = {
        "inside_maya": True,
        "maya_version": str(cmds.about(version=True)),
        "batch": bool(cmds.about(batch=True)),
        "user_app_dir": "",
        "user_script_dir": "",
        "sys_path_maya": [],
        "tak_importable": False,
        "tak_version": "",
        "tak_file": "",
        "register": {},
        "dispatch_about": {},
        "errors": [],
    }
    for key, kwargs in (("user_app_dir", {"userAppDir": True}),
                        ("user_script_dir", {"userScriptDir": True})):
        try:
            report[key] = str(cmds.internalVar(**kwargs))
        except Exception as exc:                    # pragma: no cover - 宿主差异
            report["errors"].append("%s: %s: %s" % (key, type(exc).__name__, exc))

    report["sys_path_maya"] = [p for p in sys.path
                               if "maya" in p.lower().replace("\\", "/")]
    # 只留用户脚本目录（Documents 下）+ 仓库根：Maya 自带的几十个安装目录
    # 对判断「装到哪、有没有被执行」没有信息量，只会把证据文件撑大。
    report["sys_path_maya"] = [p for p in report["sys_path_maya"]
                               if "documents" in p.lower().replace("\\", "/")
                               or "workbuddy" in p.lower()]
    report["sys_path_total"] = len(sys.path)

    try:
        import tak
        report["tak_importable"] = True
        report["tak_version"] = str(getattr(tak, "__version__", ""))
        report["tak_file"] = str(getattr(tak, "__file__", ""))
    except Exception as exc:
        report["errors"].append("import tak: %s: %s" % (type(exc).__name__, exc))
        return report

    try:
        from tak.adapters.maya import menu as maya_menu
        report["register"] = maya_menu.register_menu()
        report["dispatch_about"] = maya_menu.dispatch("tak_about")
    except Exception as exc:                        # pragma: no cover - 宿主差异
        report["errors"].append("menu: %s: %s" % (type(exc).__name__, exc))
    return report


# --------------------------------------------------------------------------- #
# 在普通 Python 里：拉起 mayabatch
# --------------------------------------------------------------------------- #
def _find_mayabatch(version: str = "") -> str:
    """定位 ``mayabatch.exe``：环境变量 → Autodesk 安装目录里最高的可用版本。"""
    env = os.environ.get("TAK_MAYABATCH", "").strip()
    if env and os.path.isfile(env):
        return env
    if os.name != "nt":
        return ""
    root = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    autodesk = os.path.join(root, "Autodesk")
    try:
        names = sorted(os.listdir(autodesk), reverse=True)
    except OSError:
        return ""
    for name in names:
        if not name.startswith("Maya"):
            continue
        digits = name[4:]
        if version and digits != version:
            continue
        candidate = os.path.join(autodesk, name, "bin", "mayabatch.exe")
        if os.path.isfile(candidate):
            return candidate
    return ""


def _run_in_maya(mayabatch: str) -> dict:
    """用 mayabatch 执行本文件，返回它写出的 JSON。"""
    fd, mel_path = tempfile.mkstemp(suffix=".mel", prefix="tak_probe_")
    os.close(fd)
    log_path = mel_path[:-4] + ".log"
    try:
        # MEL 的双引号字符串里反斜杠是转义符，Windows 路径必须转成正斜杠，
        # 否则 C:\Users\... 会被吃成 C:Users...（本轮踩过）。
        target = os.path.abspath(__file__).replace("\\", "/")
        with open(mel_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_MEL % (target, target))
        # 输出落文件的最后几行要带出来：静默丢弃日志等于把失败藏起来
        with open(log_path, "wb") as log:
            subprocess.run([mayabatch, "-script", mel_path],
                           stdout=log, stderr=subprocess.STDOUT,
                           timeout=600, check=False)
        tail = _tail(log_path)
    finally:
        for path in (mel_path,):
            try:
                os.remove(path)
            except OSError:
                pass
    if not os.path.isfile(RESULT_PATH):
        return {"inside_maya": False,
                "errors": ["Maya 未写出结果文件（启动失败？）",
                           "日志尾部见 %s" % log_path, tail]}
    try:
        os.remove(log_path)
    except OSError:
        pass
    with open(RESULT_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _tail(path: str, lines: int = 12) -> str:
    """读日志最后几行（Maya 输出是 GBK/UTF-8 混合，容错解码）。"""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return ""
    text = raw.decode("utf-8", "replace")
    return "\n".join(text.splitlines()[-lines:])


def _summarize(report: dict) -> str:
    lines = ["=== TAK / Maya 启动链路探测 ==="]
    if not report.get("inside_maya"):
        lines.append("未在 Maya 内运行：%s" % report.get("errors"))
        return "\n".join(lines)
    lines.append("Maya 版本      : %s (batch=%s)" % (report.get("maya_version"),
                                                    report.get("batch")))
    lines.append("userAppDir     : %s" % report.get("user_app_dir"))
    lines.append("userScriptDir  : %s" % report.get("user_script_dir"))
    # 只列用户脚本目录（Documents 下）与仓库根，Maya 自带的几十个安装目录没有信息量
    user_paths = [p for p in report.get("sys_path_maya", [])
                  if "documents" in p.lower().replace("\\", "/")]
    lines.append("sys.path（用户脚本目录 %d 个）:" % len(user_paths))
    for entry in user_paths:
        lines.append("   %s" % entry)
    if report.get("tak_importable"):
        lines.append("import tak     : YES %s (%s)" % (report.get("tak_version"),
                                                      report.get("tak_file")))
    else:
        lines.append("import tak     : NO  ← 启动块没被执行，或没插进 sys.path")
    lines.append("register_menu  : %s" % report.get("register"))
    lines.append("dispatch(about): %s" % report.get("dispatch_about"))
    for message in report.get("errors", []):
        lines.append("ERROR: %s" % message)
    if report.get("tak_importable") and not report.get("errors"):
        lines.append("结论           : 启动链路 PASS —— "
                     "菜单栏是否真的出现仍需开 GUI 确认")
    else:
        lines.append("结论           : FAIL")
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    version = ""
    if "--maya-version" in argv:
        index = argv.index("--maya-version")
        if index + 1 < len(argv):
            version = argv[index + 1]

    if "maya.cmds" in sys.modules or _can_import_maya():
        report = _probe_inside_maya()
        with open(RESULT_PATH, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        print(_summarize(report))
        return 0

    mayabatch = _find_mayabatch(version)
    if not mayabatch:
        print("找不到 mayabatch.exe：设置 TAK_MAYABATCH 环境变量，"
              "或用 --maya-version 指定已安装的版本")
        return 2
    print("使用 %s" % mayabatch)
    report = _run_in_maya(mayabatch)
    print(_summarize(report))
    ok = bool(report.get("tak_importable")) and not report.get("errors")
    return 0 if ok else 1


def _can_import_maya() -> bool:
    try:
        import maya.cmds  # noqa: F401
    except Exception:
        return False
    return True


if __name__ == "__main__":
    sys.exit(main())
