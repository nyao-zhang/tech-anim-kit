# -*- coding: utf-8 -*-
"""让 pytest 在无安装的情况下直接 import 仓库根目录的 tak / tools。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
