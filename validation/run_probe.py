# -*- coding: utf-8 -*-
"""在本机以 headless commandlet 运行 UE 探测，结果写入 validation/ue_probe_result.json。

    python validation/run_probe.py
    python validation/run_probe.py --engine H:/UE_5.9/Engine/Binaries/Win64/UnrealEditor-Cmd.exe
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ENGINE = r"H:\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
RESULT = os.path.join(HERE, "ue_probe_result.json")
LOG = os.path.join(HERE, "ue_probe.log")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", default=DEFAULT_ENGINE)
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--out", default=RESULT, help="结果 JSON 路径，多版本复验时分开保存")
    parser.add_argument("--script", default="ue_probe.py",
                        help="validation/ 下的探测脚本文件名，如 ue_adapter_probe.py")
    args = parser.parse_args(argv)
    result_path = os.path.abspath(args.out)

    if not os.path.isfile(args.engine):
        print("ERROR: 找不到引擎可执行文件 %s" % args.engine)
        return 2
    if os.path.isfile(result_path):
        os.remove(result_path)

    cmd = [
        args.engine,
        os.path.join(HERE, "TakProbe.uproject"),
        "-nullrhi", "-unattended", "-nosplash", "-nop4", "-NoSound",
        "-ExecutePythonScript=" + os.path.join(HERE, args.script),
        "-log", "-abslog=" + LOG,
    ]
    env = dict(os.environ, TAK_PROBE_OUT=result_path)
    print("running: %s" % " ".join(cmd))
    try:
        completed = subprocess.run(cmd, env=env, timeout=args.timeout,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        tail = completed.stdout.decode("utf-8", "replace")[-3000:]
        print("exit=%s\n%s" % (completed.returncode, tail))
    except subprocess.TimeoutExpired:
        print("TIMEOUT after %ss（引擎首次启动可能较慢，可加大 --timeout）" % args.timeout)
        return 3

    if not os.path.isfile(result_path):
        print("FAILED: 未生成探测结果，请查看 %s" % LOG)
        return 4
    with open(result_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    print(json.dumps({k: data[k] for k in ("ok", "engine_version", "python_version",
                                           "tak_import") if k in data},
                     ensure_ascii=False, indent=2))
    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
