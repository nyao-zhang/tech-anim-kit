# -*- coding: utf-8 -*-
"""
mh_ssdr.mirror
==============
把 SSDR 反求出的骨骼偏移镜像到身体的另一侧。

只有两条数学事实，其余都是工程细节：

1. **镜像一个"相对变换"要用反射矩阵做共轭**

       D_m = R · D · R          （行向量约定，p' = p · M）

   D = base⁻¹ · target 是这根骨骼"从绑定姿态到目标姿态"的相对变换。
   把整条肢体翻转过去时，相对的形变也跟着翻转，所以要对它做共轭。
   **不能**把欧拉角里的某个分量取反——那在带 jointOrient、绑定姿态不是单位
   矩阵的 MetaHuman rig 上会得到方向完全错误的偏移。

2. **反射矩阵**（关于 axis 轴坐标 = c 的平面）

       R[axis, axis] = -1
       R[3, axis]    = 2c

   共轭后 det(R·D·R) = det(D)，仍是正常的刚体变换（没有负缩放），
   可以直接写回骨骼的世界矩阵。

配对靠**名称里的左右标记**（`FACIAL_L_*` / `*_L` / `*.R` / `*_Left` 等）。
生成候选名字后一定要用"该名字是否真实存在于这套 rig 里"来确认，
因为光靠字符串规则猜会误伤（例如 `FACIAL_C_LipUpper` 里的字母）。

本模块不依赖 Maya，可离线单测（tests/test_mirror.py）。
"""

from __future__ import unicode_literals

import re

import numpy as np

SIDE_L = "L"
SIDE_R = "R"

# 左右标记的词表，**顺序即优先级**：MetaHuman / UE 用单个大写字母，
# 排在前面可以让 `FACIAL_L_Eye` 优先命中 `L→R`，而不是被更宽的规则抢走。
_TOKEN_PAIRS = (
    ("L", "R"),
    ("Left", "Right"),
    ("left", "right"),
    ("LEFT", "RIGHT"),
    ("l", "r"),
)

# 允许出现在标记两侧的分隔符
_DELIMS = r"[._\-\s|:/]"


def _token_matches(name, token):
    """返回 token 作为"独立标记"出现在 name 中的所有匹配（大小写敏感）。"""
    pat = re.compile(r"(?:^|%s)(%s)(?=%s|$)" % (_DELIMS, re.escape(token), _DELIMS),
                     re.UNICODE)
    return list(pat.finditer(name))


def side_of(name):
    """判断骨骼名属于哪一侧，返回 'L' / 'R' / None（中线骨骼）。"""
    for left, right in _TOKEN_PAIRS:
        if _token_matches(name, left):
            return SIDE_L
        if _token_matches(name, right):
            return SIDE_R
    return None


def opposite_candidates(name):
    """按优先级列出 name 所有可能的"另一侧"名字（不做存在性检查）。"""
    out = []
    for left, right in _TOKEN_PAIRS:
        for src, dst in ((left, right), (right, left)):
            ms = _token_matches(name, src)
            if not ms:
                continue
            for m in ms:                                  # 逐个替换
                out.append(name[:m.start(1)] + dst + name[m.end(1):])
            if len(ms) > 1:                               # 一次全替换
                buf = name
                for m in reversed(ms):
                    buf = buf[:m.start(1)] + dst + buf[m.end(1):]
                out.append(buf)
    return out


def opposite_name(name, available=None):
    """返回确认为"另一侧"的那个名字；找不到返回 None。

    :param available: 这套 rig 里真实存在的骨骼名集合（强烈建议传入，
        否则只能按字符串规则取第一个候选，可能对不上）
    """
    seen = set()
    for cand in opposite_candidates(name):
        if cand == name or cand in seen:
            continue
        seen.add(cand)
        if available is None:
            return cand
        if cand in available:
            return cand
    if available is not None:
        # 名字里带路径（|a|b|bone）时，也允许用短名匹配一次
        for cand in opposite_candidates(name.split("|")[-1]):
            if cand in seen:
                continue
            seen.add(cand)
            for real in available:
                if real.split("|")[-1] == cand:
                    return real
    return None


def build_pairs(bones, want_side, available=None):
    """列出 (源骨骼, 目标骨骼) 配对。

    :param want_side: 'L' 表示把左侧镜像到右侧，'R' 反之
    """
    if available is None:
        available = set(bones)
    pairs, used = [], set()
    for name in bones:
        if side_of(name) != want_side:
            continue
        other = opposite_name(name, available)
        if not other or other == name or other in used:
            continue
        used.add(other)
        pairs.append((name, other))
    return pairs


# ----------------------------------------------------------------------------
# 反射
# ----------------------------------------------------------------------------
def reflection_row(axis, coord):
    """关于「axis 轴坐标 = coord」那面平面反射的 4x4 矩阵（行向量约定）。"""
    axis = int(axis)
    if axis not in (0, 1, 2):
        raise ValueError("axis 必须是 0/1/2")
    R = np.eye(4)
    R[axis, axis] = -1.0
    R[3, axis] = 2.0 * float(coord)
    return R


def conjugate(P, R):
    """R · P · R —— 把变换 P 镜像到平面的另一侧。"""
    return np.asarray(R, dtype=np.float64) @ np.asarray(P, dtype=np.float64) \
        @ np.asarray(R, dtype=np.float64)


def reflect_point(p, axis, coord):
    """点（行向量）关于轴对齐平面的镜像。"""
    q = np.array(p, dtype=np.float64, copy=True)
    q[..., int(axis)] = 2.0 * float(coord) - q[..., int(axis)]
    return q


def relative(base, target):
    """row 约定下的相对变换：base⁻¹ · target"""
    return np.linalg.inv(np.asarray(base, dtype=np.float64)) \
        @ np.asarray(target, dtype=np.float64)


def estimate_plane(src_points, dst_points, axis=None):
    """从左右配对的关节位置上估计镜像平面。

    对每个候选轴，用配对中点均值作为平面坐标，然后用"左点镜像后与右点的
    平均距离"打分。真实对称的 rig 会在正确轴上得到接近 0 的残差。

    :return: (axis, coord, residual)
    """
    src = np.asarray(src_points, dtype=np.float64).reshape(-1, 3)
    dst = np.asarray(dst_points, dtype=np.float64).reshape(-1, 3)
    if src.size == 0:
        return (0 if axis is None else int(axis)), 0.0, 0.0
    axes = [int(axis)] if axis is not None else [0, 1, 2]
    best = None
    for a in axes:
        c = float(np.mean(0.5 * (src[:, a] + dst[:, a])))
        res = float(np.linalg.norm(reflect_point(src, a, c) - dst, axis=1).mean())
        if best is None or res < best[2]:
            best = (a, c, res)
    return best


AXIS_NAMES = ("X", "Y", "Z")
