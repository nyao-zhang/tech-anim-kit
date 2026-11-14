# -*- coding: utf-8 -*-
"""本机凭据文件（``~/.tak/credential.json``）。

为什么需要它
------------
``~/.tak/config.json`` 里**只存环境变量名，不存密钥** —— 这样把配置发给同事、
或者它被同步进网盘，都不会顺带泄露凭据。这个原则保持不变。

但只靠环境变量有个现实代价：想用 AI 就得去「系统属性 → 环境变量」新建一条、
然后重启 DCC。对一个自己用的工位来说，这个代价太高，实际结果就是**功能不可用**
（用户配好地址却永远提示「缺少密钥」）。

折中方案：密钥单独存这个文件，与 config.json 分开管理：

* 权限收到当前用户（POSIX ``chmod 600``，Windows 用 ``icacls`` 去掉继承）；
* 面板里可一键清除，不需要去翻文件系统；
* **环境变量永远优先** —— CI 与临时切换仍然走 ``TAK_AI_API_KEY``。

优先级：环境变量 > 本文件 > 无。

设计约束与 ``tak.core.config`` 一致：读永不抛异常（坏 JSON 当作「没有凭据」），
写是原子的（tmp + ``os.replace``），路径可被 ``TAK_HOME`` 覆盖。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Dict, Optional

CREDENTIAL_NAME = "credential.json"
ENV_HOME = "TAK_HOME"

#: 密钥低于这个长度基本是误填（本地端点如 Ollama 允许任意值，所以不硬拒，
#: 只在校验里给出提示）。
_MIN_REASONABLE_KEY = 8


def credential_home() -> str:
    override = os.environ.get(ENV_HOME, "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.join(os.path.expanduser("~"), ".tak")


def credential_path() -> str:
    return os.path.join(credential_home(), CREDENTIAL_NAME)


def _empty() -> Dict[str, Any]:
    return {"api_key": "", "provider": "", "saved_at": "", "note": ""}


def load() -> Dict[str, Any]:
    """读取凭据。永不抛异常：缺失 / 损坏 / 类型不对都当作「没有凭据」。"""
    path = credential_path()
    if not os.path.isfile(path):
        return _empty()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except Exception:
        return _empty()
    if not isinstance(raw, dict):
        return _empty()
    data = _empty()
    for key in data:
        value = raw.get(key)
        if isinstance(value, str):
            data[key] = value
    data["api_key"] = data["api_key"].strip()
    return data


def api_key() -> str:
    return str(load().get("api_key") or "")


def save(api_key_value: str, provider: str = "", note: str = "") -> Dict[str, Any]:
    """保存密钥。返回 ``{"ok": bool, ...}``，不抛异常。

    ``api_key_value`` 允许带首尾空白（从终端复制时很常见），会 strip 掉。
    含换行的值一律拒绝 —— 那多半是复制时多带了整段，写进去只会得到一个
    看不懂的 401。
    """
    value = str(api_key_value or "").strip()
    if not value:
        return {"ok": False, "error": "密钥为空"}
    if "\n" in value or "\r" in value:
        return {"ok": False, "error": "密钥里含有换行，多半是复制时多带了内容"}

    payload = {
        "api_key": value,
        "provider": str(provider or ""),
        "saved_at": _now(),
        "note": str(note or ""),
    }
    home = credential_home()
    path = credential_path()
    try:
        os.makedirs(home, exist_ok=True)
        _restrict_dir(home)
        temp = path + ".tmp"
        with open(temp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        _restrict_file(path)
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc),
                "path": path}

    result = {"ok": True, "path": path, "tail": _tail(value),
              "short": len(value) < _MIN_REASONABLE_KEY,
              "permissions": _permissions_report(path)}
    return result


def clear() -> Dict[str, Any]:
    """删除凭据文件。文件不存在也算成功（幂等）。"""
    path = credential_path()
    if not os.path.isfile(path):
        return {"ok": True, "removed": False, "path": path}
    try:
        os.remove(path)
    except Exception as exc:
        return {"ok": False, "removed": False,
                "error": "%s: %s" % (type(exc).__name__, exc), "path": path}
    return {"ok": True, "removed": True, "path": path}


def describe() -> Dict[str, Any]:
    """给面板看的摘要：有没有、什么时候存的、尾部四位、文件在哪。"""
    data = load()
    key = str(data.get("api_key") or "")
    return {
        "present": bool(key),
        "path": credential_path(),
        "saved_at": data.get("saved_at", ""),
        "provider": data.get("provider", ""),
        "tail": _tail(key),
        "permissions": _permissions_report(credential_path()) if key else "n/a",
    }


# --------------------------------------------------------------------------- #
# 内部
# --------------------------------------------------------------------------- #
def _now() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _tail(key: str) -> str:
    if not key:
        return ""
    return ("…" + key[-4:]) if len(key) > 8 else "…"


def _restrict_dir(home: str) -> None:
    """配置目录收到当前用户（POSIX）。Windows 靠上级目录继承即可，不额外处理。"""
    if os.name == "nt":
        return
    try:
        os.chmod(home, 0o700)
    except Exception:
        pass


def _restrict_file(path: str) -> None:
    if os.name == "nt":
        _restrict_windows(path)
        return
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _restrict_windows(path: str) -> None:
    """用 icacls 去掉继承并只授权当前用户。

    失败不影响保存 —— 有些受管机器禁止改 ACL。此时 ``describe()`` 会显示
    ``unknown``，面板据此提示，而不是假装权限已经收好了。
    """
    user = os.environ.get("USERNAME") or ""
    if not user:
        return
    try:
        subprocess.run(
            ["icacls", path, "/inheritance:r", "/grant:r", "%s:F" % user],
            capture_output=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


def _permissions_report(path: str) -> str:
    """粗粒度地报一下文件权限现状，供面板展示。"""
    if not os.path.isfile(path):
        return "n/a"
    if os.name == "nt":
        return "windows-acl"
    try:
        mode = os.stat(path).st_mode & 0o777
        return "octal-%o" % mode
    except Exception:
        return "unknown"


def main(argv: Optional[list] = None) -> int:
    """``python -m tak.core.ai.credential status|clear``"""
    args = list(argv if argv is not None else sys.argv[1:])
    action = args[0] if args else "status"
    if action == "status":
        payload = describe()
        payload["env_override"] = sorted(
            name for name in ("TAK_AI_API_KEY", "TAK_AI_BASE_URL")
            if os.environ.get(name))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if action == "clear":
        payload = clear()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("ok") else 1
    print("用法：python -m tak.core.ai.credential [status|clear]")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
