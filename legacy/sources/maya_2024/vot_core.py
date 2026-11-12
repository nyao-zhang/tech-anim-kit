# -*- coding: utf-8 -*-
"""
vot_core.py  --  Vertex Order Transfer
================================================================================
在「拓扑完全一致、顶点顺序(point order)不同」的两个多边形模型之间，
建立顶点级的一一对应关系，并把 A 的点序传递给 B。

环境：Maya 2024 / Python 3 / OpenMaya 2.0 (maya.api.OpenMaya)

--------------------------------------------------------------------------------
原理
--------------------------------------------------------------------------------
1. 网格 = 一张无向图 G(V, E)：顶点 = 顶点，边 = 多边形边。
   拓扑一致  <=>  两张图同构（isomorphic）。
2. 同构映射 σ: A_i -> B_j 由「已确定的对应点(种子)」+「邻接约束传播」唯一确定：
       A 的点 a 已匹配到 B 的点 b，
       则 a 的邻居只能匹配到 b 的邻居，
       再用「度数相同」「已匹配邻居必须一一对应」两条规则剪枝。
       候选唯一 -> 确定；候选 > 1 -> 暂缓（对称区域）；
       候选 = 0  -> 拓扑不一致。
3. 单个种子点能否定出全部映射，取决于网格图的**自同构(symmetry)**：
       - 有机模型 / 扫描模型：通常一个种子就够；
       - 规则网格、左右对称的头模、球、立方体：必然有歧义 -> 需要多个种子，
         或开启「坐标匹配」兜底（两模型摆放/形状完全重合时，直接按坐标哈希配对）。
4. 多个互不相连的壳(shell / connected component)：每个壳至少需要 1 个种子，
   坐标匹配可以自动解决。
5. 求完后做一次**全边校验**：A 的每一条边 (a1,a2) 在 B 上必须也是边。
   通过才允许应用 —— 这是「拓扑一致」的硬保证。
6. 应用：新网格的面表直接沿用 A 的 faceConnects（面序号、面内顶点顺序都不变），
   只把顶点坐标按 σ 重排。因此 UV（face-vertex 存储）、面 ID 天然保留。

--------------------------------------------------------------------------------
脚本用法
--------------------------------------------------------------------------------
    import vot_core as vot

    # 1) 只算映射
    A = vot.MeshGraph('head_A')
    B = vot.MeshGraph('head_B')
    rep = vot.build_mapping(A, B, seeds=[(12, 87)])     # A.vtx[12] <-> B.vtx[87]
    print(rep['ok'], rep['reason'])
    mapping = rep['mapping']                            # dict: A索引 -> B索引

    # 2) 一步到位（生成新模型）
    vot.transfer_vertex_order('head_A', 'head_B', seeds=[(12, 87)],
                              name='head_B_reordered')

    # 3) UI
    import vot_ui; vot_ui.show()
"""

from __future__ import print_function

import collections
import math
import re
import sys

import maya.cmds as cmds
import maya.api.OpenMaya as om

try:
    import maya.api.OpenMayaAnim as oma
except Exception:  # pragma: no cover
    oma = None

# 版本号：用来确认 Maya 里加载的到底是哪一份代码
# （改文件后必须 reload 才生效，import 不会重读）
__version__ = '2.1.0'


# ==============================================================================
# 日志（UI 会替换掉）
# ==============================================================================

_LOG = [print]


def set_logger(fn):
    """UI 通过它把日志接到界面上"""
    _LOG[0] = fn


def L(msg):
    try:
        _LOG[0](str(msg))
    except Exception:
        try:
            print(str(msg))
        except Exception:
            pass


# ==============================================================================
# Maya 基础工具
# ==============================================================================

_VTX_RE = re.compile(r'^(?P<obj>.+)\.(?:vtx|pt)\[(?P<idx>\d+)\]$')
_EDGE_RE = re.compile(r'^(?P<obj>.+)\.(?:e|edge)\[(?P<idx>\d+)\]$')


def get_mesh_fn(node):
    """返回 (MFnMesh, MDagPath(指向 shape))"""
    sel = om.MSelectionList()
    try:
        sel.add(node)
    except Exception:
        raise RuntimeError(u'场景中找不到对象：%s' % node)
    path = sel.getDagPath(0)
    if path.apiType() != om.MFn.kMesh:
        try:
            path.extendToShape()
        except Exception:
            pass
    if path.apiType() != om.MFn.kMesh:
        raise RuntimeError(u'%s 不是多边形网格' % node)
    return om.MFnMesh(path), path


def transform_of(shape_path):
    p = om.MDagPath(shape_path)
    p.pop()  # shape -> transform
    return p.partialPathName()


def parse_vertex(component):
    """'pSphere1.vtx[12]' -> ('pSphere1', 12)"""
    m = _VTX_RE.match(str(component))
    if not m:
        raise ValueError(u'不是顶点组件：%s' % component)
    return m.group('obj'), int(m.group('idx'))


def parse_edge(component):
    """'pSphere1.e[12]' -> ('pSphere1', 12)"""
    m = _EDGE_RE.match(str(component))
    if not m:
        raise ValueError(u'不是边组件：%s' % component)
    return m.group('obj'), int(m.group('idx'))


def selected_vertices():
    """当前选择的、被展开成单个顶点的组件列表"""
    flat = cmds.ls(sl=True, fl=True, long=False) or []
    return cmds.filterExpand(flat, sm=31) or []


def selected_edges():
    """当前选择的、被展开成单条边的组件列表"""
    flat = cmds.ls(sl=True, fl=True, long=False) or []
    return cmds.filterExpand(flat, sm=32) or []


def select_edges(obj, edge_ids, add=False):
    if not edge_ids:
        return
    cmds.select(['%s.e[%d]' % (obj, int(i)) for i in edge_ids], add=add)


def _group_by_object(components, parser):
    """把组件按所属对象分组 -> OrderedDict(obj -> [idx])"""
    out = collections.OrderedDict()
    for c in components:
        obj, idx = parser(c)
        out.setdefault(obj, []).append(idx)
    return out


def select_vertices(obj, indices, add=False):
    if not indices:
        return
    cmds.select(['%s.vtx[%d]' % (obj, int(i)) for i in indices], add=add)


def seeds_from_selection():
    """
    要求：选中恰好 2 个顶点，分别来自 2 个不同的网格。
    返回 ((meshA, idxA), (meshB, idxB))
    """
    vtxs = selected_vertices()
    if len(vtxs) != 2:
        raise RuntimeError(u'请选中恰好 2 个顶点（A 上一个 + B 上对应的那个）')
    grouped = collections.OrderedDict()
    for v in vtxs:
        obj, idx = parse_vertex(v)
        grouped.setdefault(obj, []).append(idx)
    if len(grouped) != 2:
        raise RuntimeError(u'两个顶点必须来自两个不同的模型')
    items = list(grouped.items())
    for k, v in items:
        if len(v) != 1:
            raise RuntimeError(u'%s 上只能选 1 个顶点' % k)
    return (items[0][0], items[0][1][0]), (items[1][0], items[1][1][0])


# ==============================================================================
# 网格 -> 邻接图
# ==============================================================================

class MeshGraph(object):
    """把多边形网格抽象成顶点邻接图"""

    def __init__(self, node):
        self.fn, self.path = get_mesh_fn(node)
        self.shape = self.path.partialPathName()
        self.transform = transform_of(self.path)

        self.nV = self.fn.numVertices
        self.nE = self.fn.numEdges
        self.nP = self.fn.numPolygons

        self.counts, self.conn = self.fn.getVertices()   # (polyCounts, polyConnects)
        self.points = self.fn.getPoints(om.MSpace.kObject)

        # 面表（list of list）
        self.polys = []
        off = 0
        for c in self.counts:
            c = int(c)
            self.polys.append([int(x) for x in self.conn[off:off + c]])
            off += c

        # 邻接
        self.adj = [set() for _ in range(self.nV)]
        self.edges = set()
        for poly in self.polys:
            k = len(poly)
            for i in range(k):
                a = poly[i]
                b = poly[(i + 1) % k]
                if a == b:
                    continue
                self.adj[a].add(b)
                self.adj[b].add(a)
                self.edges.add((a, b) if a < b else (b, a))
        self.deg = [len(s) for s in self.adj]

        # 边 -> 面（用于循环边扩展 / 边界边判定）
        self.face_of_edge = collections.defaultdict(list)
        for fi, poly in enumerate(self.polys):
            k = len(poly)
            for i in range(k):
                a = poly[i]
                b = poly[(i + 1) % k]
                if a == b:
                    continue
                self.face_of_edge[(a, b) if a < b else (b, a)].append(fi)

        # Maya 边序号 -> 顶点对（MItMeshEdge 的 index 与 e[i] 组件序号一致）
        self.edge_vtx = {}
        self.edge_key = {}          # (min,max) 顶点对 -> Maya 边序号
        try:
            it = om.MItMeshEdge(self.path)
            while not it.isDone():
                a, b = it.vertexId(0), it.vertexId(1)
                self.edge_vtx[it.index()] = (a, b)
                self.edge_key[(a, b) if a < b else (b, a)] = it.index()
                it.next()
        except Exception:
            pass

        # 边界边：只被 1 个面使用的边
        self.border_edges = []
        for eid, (a, b) in self.edge_vtx.items():
            if len(self.face_of_edge.get((min(a, b), max(a, b)), ())) == 1:
                self.border_edges.append(eid)
        self.border_edges.sort()

        # 包围盒对角（用于坐标匹配容差）
        mnx = mny = mnz = float('inf')
        mxx = mxy = mxz = float('-inf')
        for p in self.points:
            if p.x < mnx: mnx = p.x
            if p.y < mny: mny = p.y
            if p.z < mnz: mnz = p.z
            if p.x > mxx: mxx = p.x
            if p.y > mxy: mxy = p.y
            if p.z > mxz: mxz = p.z
        self.diag = math.sqrt((mxx - mnx) ** 2 + (mxy - mny) ** 2 + (mxz - mnz) ** 2)

    # -- 连通分量（多壳） ------------------------------------------------
    def components(self):
        seen = bytearray(self.nV)
        comps = []
        for v in range(self.nV):
            if seen[v]:
                continue
            seen[v] = 1
            stack = [v]
            comp = []
            while stack:
                x = stack.pop()
                comp.append(x)
                for y in self.adj[x]:
                    if not seen[y]:
                        seen[y] = 1
                        stack.append(y)
            comps.append(comp)
        return comps

    def component_of(self, v):
        seen = {v}
        stack = [v]
        while stack:
            x = stack.pop()
            for y in self.adj[x]:
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        return seen

    # -- 循环边扩展（四边形网格上的"直通"规则）----------------------------
    def _face_edges_at(self, face_id, v):
        """面 face_id 上，与顶点 v 相连的两条边"""
        poly = self.polys[face_id]
        k = len(poly)
        for i in range(k):
            if poly[i] == v:
                out = []
                for w in (poly[(i - 1) % k], poly[(i + 1) % k]):
                    out.append((v, w) if v < w else (w, v))
                return out
        return []

    def _other_face(self, edge, face_id):
        fs = self.face_of_edge.get(edge, [])
        for f in fs:
            if f != face_id:
                return f
        return None

    def expand_edge_loop(self, edge_id, stop_on_valence4=True):
        """
        从一条边出发扩展出整条循环边（与 Maya 双击选循环边行为一致）。
        规则：在顶点 b 处，穿过"侧面边"所在的下一个面，取它的另一条边继续前进。
        返回 list of edge_id（找不到 edge_vtx 时返回 []）。
        """
        e0 = self.edge_vtx.get(int(edge_id))
        if e0 is None:
            return []
        chain = {tuple(sorted(e0))}
        result = [int(edge_id)]

        for direction in (0, 1):
            if direction == 1:
                a, b = e0[1], e0[0]
            else:
                a, b = e0[0], e0[1]
            prev_face = None
            guard = 0
            while guard < self.nV + 8:
                guard += 1
                if stop_on_valence4 and self.deg[b] != 4:
                    break
                edge = (a, b) if a < b else (b, a)
                faces = [f for f in self.face_of_edge.get(edge, []) if f != prev_face]
                if not faces:
                    break
                f0 = faces[0]
                side = [e for e in self._face_edges_at(f0, b) if e != edge]
                if not side:
                    break
                side_e = side[0]
                f1 = self._other_face(side_e, f0)
                if f1 is None:
                    break
                nxt = [e for e in self._face_edges_at(f1, b) if e != side_e]
                if not nxt:
                    break
                nxt_e = nxt[0]
                key = tuple(sorted(nxt_e))
                if key == tuple(sorted(e0)) or key in chain:
                    break
                chain.add(key)
                eid = self.edge_key.get(key)
                if eid is None:
                    break
                result.append(eid)
                prev_face = f0
                a, b = b, (nxt_e[0] if nxt_e[1] == b else nxt_e[1])
        return sorted(set(result))


# ==============================================================================
# 循环边 / 边界边  ->  有序顶点链  ->  候选种子（本工具的主入口）
# ==============================================================================

def chains_from_edges(G, edge_ids):
    """
    把一组边拆成若干条链，并给出沿链行走的顶点顺序。
    返回 list of dict:
        {'verts': [...], 'cyclic': True/False, 'ok': bool, 'why': str, 'n_edges': int}
    只有 ok=True 的链能用来对齐（必须是一条简单路径或简单环，不能有分叉）。
    """
    ids = []
    for eid in edge_ids:
        vt = G.edge_vtx.get(int(eid))
        if vt is not None:
            ids.append((vt[0], vt[1]))
    if not ids:
        return []

    local = collections.defaultdict(set)
    for a, b in ids:
        if a == b:
            continue
        local[a].add(b)
        local[b].add(a)
    verts = set(local.keys())

    seen = set()
    comps = []
    for v in verts:
        if v in seen:
            continue
        seen.add(v)
        stack = [v]
        comp = []
        while stack:
            x = stack.pop()
            comp.append(x)
            for y in local[x]:
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        comps.append(comp)

    out = []
    for comp in comps:
        degs = {v: len(local[v]) for v in comp}
        ones = [v for v in comp if degs[v] == 1]
        if all(d == 2 for d in degs.values()):
            cyclic = True
            start = comp[0]
        elif len(ones) == 2 and all(d in (1, 2) for d in degs.values()):
            cyclic = False
            start = ones[0]
        else:
            out.append({'verts': sorted(comp), 'cyclic': None, 'ok': False,
                        'why': u'选中的边有分叉（不是一条链），无法排序', 'n_edges': len(comp)})
            continue

        order = [start]
        prev, cur = None, start
        while True:
            nxt = [y for y in local[cur] if y != prev]
            nxt = nxt[0] if nxt else None
            if nxt is None:
                break
            if cyclic and nxt == start:
                break
            order.append(nxt)
            prev, cur = cur, nxt
            if len(order) > len(comp):
                break

        ok = len(order) == len(comp)
        out.append({'verts': order, 'cyclic': cyclic, 'ok': ok,
                    'why': u'' if ok else u'链未能完整排序',
                    'n_edges': len(order) - 1 if not cyclic else len(order)})
    out.sort(key=lambda c: -len(c['verts']))
    return out


def chain_signature(G, chain):
    """沿链每个顶点的结构指纹：度数 + 链外邻居数 + 链外邻居度数序列"""
    vs = chain['verts']
    inside = set(vs)
    sig = []
    for v in vs:
        ext = tuple(sorted(G.deg[x] for x in G.adj[v] if x not in inside))
        sig.append((G.deg[v], len(ext), ext))
    return sig


def align_chains(A, ca, B, cb, max_candidates=12):
    """
    把两条链对齐，产出候选种子 dict。
    开放链：2 种朝向；闭合环：2L 种（旋转 × 朝向）。

    用结构指纹给每种对齐打分（而不是要求完全一致）——
    这样即使两模型拓扑有局部差异（个别点度数不同），
    正确旋转仍然会拿到最高分，不会被"指纹对不上"直接带偏。
    返回按分值降序排列的候选列表。
    """
    va, vb = ca['verts'], cb['verts']
    if len(va) != len(vb) or not ca.get('ok') or not cb.get('ok'):
        return []
    if bool(ca['cyclic']) != bool(cb['cyclic']):
        return []

    sa = chain_signature(A, ca)
    sb = chain_signature(B, cb)
    n = len(va)

    def _fuzzy(x, y):
        """指纹不完全一致时的近似分：度数相同 +1、链外邻居数相同 +1、共有度数个数"""
        if x == y:
            return 10
        common = sum((collections.Counter(x[2]) & collections.Counter(y[2])).values())
        return common + (1 if x[0] == y[0] else 0) + (1 if x[1] == y[1] else 0)

    scored = []
    for rev in (False, True):
        vb2 = vb[::-1] if rev else vb
        sb2 = sb[::-1] if rev else sb
        rots = range(n) if ca['cyclic'] else (0,)
        for r in rots:
            hit = 0
            fz = 0
            for i in range(n):
                fz += _fuzzy(sa[i], sb2[(i + r) % n])
                if sa[i] == sb2[(i + r) % n]:
                    hit += 1
            scored.append((hit, fz, 0 if not rev else 1, r, vb2))

    scored.sort(key=lambda t: (-t[0], -t[1], t[2], t[3]))
    if not scored:
        return []

    best = scored[0][0]
    if best < n:
        L(u'  边链对齐：指纹最佳匹配 %d / %d（两模型拓扑有局部差异，按最优旋转对齐）'
          % (best, n))

    cands = []
    for hit, _fz, _rev, r, vb2 in scored[:max(max_candidates, 1)]:
        cands.append({va[i]: vb2[(i + r) % n] for i in range(n)})
    return cands


def candidates_from_chains(A, ca_list, B, cb_list, log_missing=True):
    """
    两边的链做配对（按长度 + 是否闭合），返回候选种子 dict 列表。
    多条链时优先用最长的一对（通常足够把整张图定下来）。
    """
    oka = [c for c in ca_list if c.get('ok')]
    okb = [c for c in cb_list if c.get('ok')]
    if log_missing:
        for c in ca_list:
            if not c.get('ok'):
                L(u'A 上有 %d 个顶点的一段边无法排序：%s' % (len(c['verts']), c['why']))
        for c in cb_list:
            if not c.get('ok'):
                L(u'B 上有 %d 个顶点的一段边无法排序：%s' % (len(c['verts']), c['why']))
    if not oka or not okb:
        return []

    pairs = []
    for ca in oka:
        for cb in okb:
            if len(ca['verts']) == len(cb['verts']) and bool(ca['cyclic']) == bool(cb['cyclic']):
                pairs.append((ca, cb))
    if not pairs:
        L(u'两条边链长度/类型对不上：A=%s  B=%s'
          % ([len(c['verts']) for c in oka], [len(c['verts']) for c in okb]))
        return []

    # 长度相同但有多条时，取最长的一对（信息量最大）
    pairs.sort(key=lambda p: -len(p[0]['verts']))
    ca, cb = pairs[0]
    if len(pairs) > 1:
        L(u'检测到 %d 组长度匹配的边链，先用最长的一对（%d 个顶点）'
          % (len(pairs), len(ca['verts'])))
    cands = align_chains(A, ca, B, cb)
    L(u'边链：A %d 点 / B %d 点，%s，得到 %d 个候选朝向'
      % (len(ca['verts']), len(cb['verts']),
         u'闭合环' if ca['cyclic'] else u'开放链', len(cands)))
    return cands


def border_candidates(A, B):
    """一键模式：直接用两个模型各自的边界边（开口边）"""
    ca = chains_from_edges(A, A.border_edges)
    cb = chains_from_edges(B, B.border_edges)
    L(u'A 边界边 %d 条 -> %d 段链；B 边界边 %d 条 -> %d 段链'
      % (len(A.border_edges), len(ca), len(B.border_edges), len(cb)))
    if not A.border_edges or not B.border_edges:
        L(u'至少一个模型没有边界边（是封闭网格），请改用手动选择循环边')
        return []
    return candidates_from_chains(A, ca, B, cb)


def candidates_from_selection(A, B, components=None):
    """
    当前选择（边组件）里挑出 A 的边和 B 的边，产出候选种子。
    components 为空则读当前选择。
    """
    comps = components if components is not None else selected_edges()
    if not comps:
        raise RuntimeError(u'没有选中任何边。请双击 A 上一条边选中整条循环边，'
                           u'再按住 Shift 双击 B 上对应的那条。')
    grouped = _group_by_object(comps, parse_edge)
    if len(grouped) != 2:
        raise RuntimeError(u'选择的边必须只来自 2 个模型（A 和 B），当前来自 %d 个' % len(grouped))

    sha, shb = A.shape, B.shape
    ea = eb = None
    for obj, ids in grouped.items():
        s = None
        try:
            s = get_mesh_fn(obj)[1].partialPathName()
        except Exception:
            s = obj
        if s == sha:
            ea = ids
        elif s == shb:
            eb = ids
    if ea is None or eb is None:
        raise RuntimeError(u'选择的边必须一个来自 A(%s)、一个来自 B(%s)' % (sha, shb))

    L(u'A 选中 %d 条边 / B 选中 %d 条边' % (len(ea), len(eb)))
    ca = chains_from_edges(A, ea)
    cb = chains_from_edges(B, eb)
    cands = candidates_from_chains(A, ca, B, cb)
    if not cands:
        raise RuntimeError(u'这两段边对不上（长度不同 / 有分叉 / 一个闭合一个开放）')
    return cands


# ==============================================================================
# 第 1 招：坐标匹配（两模型形状/摆放完全重合时最快最稳）
# ==============================================================================

def match_by_position(A, B, tol=None):
    """
    用空间网格哈希把 A 的顶点按坐标配对到 B。
    返回**双向唯一**的配对 dict（可能是部分的）。
    """
    if tol is None:
        tol = max(A.diag, B.diag) * 1e-5
    tol = max(tol, 1e-9)

    grid = collections.defaultdict(list)
    for j in range(B.nV):
        p = B.points[j]
        key = (int(math.floor(p.x / tol)), int(math.floor(p.y / tol)), int(math.floor(p.z / tol)))
        grid[key].append(j)

    tol2 = (tol * 2.0) ** 2
    forward = {}
    for i in range(A.nV):
        p = A.points[i]
        cx = int(math.floor(p.x / tol))
        cy = int(math.floor(p.y / tol))
        cz = int(math.floor(p.z / tol))
        best, bd = -1, None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in grid.get((cx + dx, cy + dy, cz + dz), ()):
                        q = B.points[j]
                        d = (p.x - q.x) ** 2 + (p.y - q.y) ** 2 + (p.z - q.z) ** 2
                        if bd is None or d < bd:
                            bd, best = d, j
        if best >= 0 and bd is not None and bd <= tol2:
            forward[i] = best

    # 双向唯一
    inv = collections.defaultdict(list)
    for i, j in forward.items():
        inv[j].append(i)
    result = {}
    for i, j in forward.items():
        if len(inv[j]) == 1:
            result[i] = j
    return result


def prune_inconsistent(A, B, m):
    """删掉与邻接结构冲突的坐标匹配结果（防止坐标重复/精度问题导致错配）"""
    changed = True
    while changed:
        changed = False
        for a, b in list(m.items()):
            if A.deg[a] != B.deg[b]:
                del m[a]
                changed = True
                continue
            bad = False
            for x in A.adj[a]:
                if x in m and m[x] not in B.adj[b]:
                    bad = True
                    break
            if bad:
                del m[a]
                changed = True
    return m


# ==============================================================================
# 第 2 招：邻接约束传播（种子驱动）
# ==============================================================================

def _compatible(A, B, m, inv, a, b):
    """a(A) 能否匹配 b(B)：度数 + 已匹配邻居必须一一对应"""
    if A.deg[a] != B.deg[b]:
        return False
    for x in A.adj[a]:
        if x in m and m[x] not in B.adj[b]:
            return False
    for y in B.adj[b]:
        if y in inv and inv[y] not in A.adj[a]:
            return False
    return True


def propagate(A, B, seeds):
    """
    从 seeds(dict: a->b) 出发做约束传播。
    返回 (mapping, contradictions)
    """
    m = dict(seeds)
    inv = {b: a for a, b in m.items()}
    contradictions = []

    work = collections.deque(list(m.keys()))
    while work:
        a = work.popleft()
        b = m[a]
        for an in A.adj[a]:
            if an in m:
                continue
            cand = [x for x in B.adj[b] if x not in inv and _compatible(A, B, m, inv, an, x)]
            if len(cand) == 1:
                bn = cand[0]
                m[an] = bn
                inv[bn] = an
                work.append(an)
            elif len(cand) == 0:
                # a 的邻居在 B 侧找不到任何合法位置 -> 拓扑不一致 或 种子给错
                contradictions.append((an, a))

    # 传播是「信息越多剪枝越强」，再来几轮全量扫描（便宜且能解掉大部分暂缓）
    for _ in range(8):
        progressed = False
        for a in list(m.keys()):
            b = m[a]
            for an in A.adj[a]:
                if an in m:
                    continue
                cand = [x for x in B.adj[b] if x not in inv and _compatible(A, B, m, inv, an, x)]
                if len(cand) == 1:
                    bn = cand[0]
                    m[an] = bn
                    inv[bn] = an
                    progressed = True
        if not progressed:
            break

    return m, contradictions


# ==============================================================================
# 第 3 招：小范围回溯（解决对称区域的残余歧义）
# ==============================================================================

def _backtrack(A, B, m, inv, rest, budget):
    """
    VF2 风格的回溯：每次挑「已匹配邻居最多」的点先定，保证候选集始终很小。
    rest 必须是**残余的整个连通块**（不能只给前沿），否则无法推进。
    返回 True 表示块内全部确定；False 表示失败（此时 m 会完整回滚）。
    """
    if not rest:
        return True
    # 选约束最强的点（已匹配邻居最多，其次度数最大）
    a = max(rest, key=lambda v: (sum(1 for x in A.adj[v] if x in m), A.deg[v], -v))
    anchors = [x for x in A.adj[a] if x in m]
    if anchors:
        pool = set(B.adj[m[anchors[0]]])
        for x in anchors[1:]:
            pool &= set(B.adj[m[x]])
        pool = [x for x in pool if x not in inv]
    else:
        pool = [x for x in range(B.nV) if x not in inv and B.deg[x] == A.deg[a]]

    pool.sort(key=lambda b: -B.deg[b])
    for bn in pool:
        if budget[0] <= 0:
            break
        if not _compatible(A, B, m, inv, a, bn):
            continue
        m[a] = bn
        inv[bn] = a
        rest.discard(a)
        if _backtrack(A, B, m, inv, rest, budget):
            return True
        # 回滚
        del m[a]
        del inv[bn]
        rest.add(a)
        budget[0] -= 1
    return False


def _rest_blocks(A, rest, m, limit=10 ** 9):
    """
    残余顶点按连通性分块，只保留「与已匹配区域相邻」的块（其余块缺种子，回溯无意义）。
    返回按大小升序排列的块列表。
    """
    seen = set()
    blocks = []
    for v in rest:
        if v in seen:
            continue
        seen.add(v)
        stack = [v]
        blk = []
        while stack:
            x = stack.pop()
            blk.append(x)
            for y in A.adj[x]:
                if y in rest and y not in seen:
                    seen.add(y)
                    stack.append(y)
        if any(any(z in m for z in A.adj[x]) for x in blk):
            blocks.append(blk)
    blocks.sort(key=len)
    return [b for b in blocks if len(b) <= limit]


# ==============================================================================
# 校验
# ==============================================================================

def verify(A, B, m):
    """严格同构校验：A 的每条边在 B 上必须存在"""
    if len(m) != A.nV:
        return False, u'映射不完整：%d / %d' % (len(m), A.nV)
    if len(set(m.values())) != A.nV:
        return False, u'映射不是一一对应（有重复目标顶点）'
    for a1, a2 in A.edges:
        b1, b2 = m[a1], m[a2]
        if b1 > b2:
            b1, b2 = b2, b1
        if (b1, b2) not in B.edges:
            return False, u'边校验失败：A(%d,%d) -> B(%d,%d) 不是 B 上的边' % (a1, a2, b1, b2)
    return True, u'通过：%d 顶点 / %d 边全部对应' % (A.nV, len(A.edges))


def _geom_conflicts(A, B, m, pm):
    """用坐标匹配结果做交叉验证：返回冲突点数"""
    if not pm:
        return 0
    bad = 0
    for a, b in pm.items():
        if a in m and m[a] != b:
            bad += 1
    return bad


# ==============================================================================
# 部分匹配（尽力而为）：允许个别点对不上，只保证「能对上的部分」拓扑一致
# ==============================================================================

def verify_partial(A, B, m):
    """
    部分匹配校验：只检查「两端都已匹配」的边。
    返回 dict:
        matched      : 已匹配顶点数
        total        : A 顶点总数
        covered      : 两端都匹配的边数
        ok           : 其中在 B 上确实存在的边数
        broken       : 断掉的边 [(a1,a2), ...]
        edges_total  : A 的总边数
        consistent   : 是否没有断边（边一致）
    """
    covered = 0
    ok = 0
    broken = []
    for a1, a2 in A.edges:
        b1 = m.get(a1)
        b2 = m.get(a2)
        if b1 is None or b2 is None:
            continue
        covered += 1
        if ((b1, b2) if b1 < b2 else (b2, b1)) in B.edges:
            ok += 1
        else:
            broken.append((a1, a2))
    return {'matched': len(m), 'total': A.nV, 'covered': covered, 'ok': ok,
            'broken': broken, 'edges_total': len(A.edges),
            'consistent': not broken}


def _prune_broken(A, B, m, max_drops=None):
    """
    反复删掉「造成断边」的映射点，直到剩余映射没有断边。
    每删一个点，参与统计的边只会减少，所以断边数严格单调下降，必然收敛。
    优先删：度数对不上的 > 断边多的 > 好边少的。
    返回被删除的 A 顶点列表。
    """
    dropped = []
    while True:
        if max_drops is not None and len(dropped) >= max_drops:
            break
        bad_of = collections.defaultdict(int)
        good_of = collections.defaultdict(int)
        for a1, a2 in A.edges:
            b1 = m.get(a1)
            b2 = m.get(a2)
            if b1 is None or b2 is None:
                continue
            key = (b1, b2) if b1 < b2 else (b2, b1)
            if key in B.edges:
                good_of[a1] += 1
                good_of[a2] += 1
            else:
                bad_of[a1] += 1
                bad_of[a2] += 1
        if not bad_of:
            break
        a = max(bad_of, key=lambda v: (1 if A.deg[v] != B.deg[m[v]] else 0,
                                       bad_of[v], -good_of[v], v))
        del m[a]
        dropped.append(a)
    return dropped


def _grow(A, B, m, banned=()):
    """
    给还没匹配上的 A 顶点补配：只在「候选唯一」且「不会新增断边」时才接受。
    banned: 已被判定为冲突、不允许再补回来的 A 顶点（防止「删了又补」死循环）。
    返回新补上的 A 顶点列表。
    """
    inv = {b: a for a, b in m.items()}
    banned = set(banned)
    added = []
    changed = True
    while changed:
        changed = False
        for a in range(A.nV):
            if a in m or a in banned:
                continue
            anchors = [x for x in A.adj[a] if x in m]
            if not anchors:
                continue
            pool = None
            for x in anchors:
                s = set(y for y in B.adj[m[x]] if y not in inv)
                pool = s if pool is None else (pool & s)
            if not pool:
                continue
            cand = [b for b in pool
                    if all(((b, m[x]) if b < m[x] else (m[x], b)) in B.edges
                           for x in anchors)]
            if not cand:
                continue
            same_deg = [b for b in cand if B.deg[b] == A.deg[a]]
            pick = same_deg if len(same_deg) == 1 else cand
            if len(pick) == 1:
                b = pick[0]
                m[a] = b
                inv[b] = a
                added.append(a)
                changed = True
    return added


def fill_by_position(A, B, m, radius=None, allow_duplicate=True):
    """
    对仍未匹配的 A 顶点，用「最近的 B 顶点」近似配对（不保证边连接一致）。
    radius=None 时取包围盒对角线的 0.1%。
    返回 (added_dict, approx_list, dup_list)
        added_dict : a -> b
        approx     : 用未占用的 B 点配上的 A 顶点（可信度较高）
        dup        : B 侧已无剩余点，只能复用已占用点的 A 顶点（会产生重合点）
    """
    diag = max(A.diag, B.diag) or 1.0
    if radius is None:
        radius = diag * 1e-3
    cell = radius if radius > 1e-9 else diag * 1e-3

    grid = collections.defaultdict(list)
    for j in range(B.nV):
        p = B.points[j]
        grid[(int(math.floor(p.x / cell)),
              int(math.floor(p.y / cell)),
              int(math.floor(p.z / cell)))].append(j)

    taken = set(m.values())
    added = {}
    approx = []
    dup = []
    r2 = radius * radius

    def _nearest(p, skip_taken):
        cx = int(math.floor(p.x / cell))
        cy = int(math.floor(p.y / cell))
        cz = int(math.floor(p.z / cell))
        best, bd = -1, None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in grid.get((cx + dx, cy + dy, cz + dz), ()):
                        if skip_taken and j in taken:
                            continue
                        q = B.points[j]
                        d = (p.x - q.x) ** 2 + (p.y - q.y) ** 2 + (p.z - q.z) ** 2
                        if bd is None or d < bd:
                            bd, best = d, j
        return best, bd

    for a in range(A.nV):
        if a in m:
            continue
        p = A.points[a]
        b, d = _nearest(p, True)
        if b >= 0 and (radius <= 0 or (d is not None and d <= r2)):
            added[a] = b
            taken.add(b)
            approx.append(a)
        elif allow_duplicate:
            b2, _d = _nearest(p, False)
            if b2 >= 0:
                added[a] = b2
                dup.append(a)
    return added, approx, dup


def repair_partial(A, B, m, rounds=8, fill=True, radius=None,
                   allow_duplicate=True, prune=True):
    """
    把一个「不完整 / 有断边」的映射修成可用结果：
      1) 删掉造成断边的点  -> 保证剩余部分边一致（prune=True 时）
      2) 在干净映射上重新补齐能唯一确定的点
      3) 剩下的用最近点近似填充（标记为 approx / dup）

    prune=False 时保留全部已确定的对应关系，只报告断边、不删点。
    适合「两模型姿态/形状并不完全重合」的场景：此时删掉一个点再靠最近点回填，
    反而可能比保留原对应（只是局部连接对不上）更糟。

    返回 (mapping, info)
        mapping : 覆盖 A 全部顶点的完整映射（含近似点）
        info    : 统计信息 dict
    """
    m = dict(m)
    info = {'dropped': [], 'grown': [], 'approx': [], 'dup': [],
            'broken_before': 0, 'broken_after': 0,
            'matched_exact': 0, 'consistent': True,
            'covered': 0, 'ok_edges': 0, 'edges_total': len(A.edges)}

    st0 = verify_partial(A, B, m)
    info['broken_before'] = len(st0['broken'])
    L(u'  部分匹配修复：起始 %d / %d，断边 %d 条'
      % (st0['matched'], A.nV, len(st0['broken'])))

    banned = set()
    for _r in range(max(1, rounds) if prune else 1):
        n0 = len(m)
        d = _prune_broken(A, B, m) if prune else []
        if d:
            info['dropped'].extend(d)
            banned.update(d)
            L(u'    第%d轮：忽略 %d 个冲突点 -> 剩 %d' % (_r + 1, len(d), len(m)))
        g = _grow(A, B, m, banned=banned)
        if g:
            info['grown'].extend(g)
            L(u'    第%d轮：补齐 %d 个点 -> %d / %d' % (_r + 1, len(g), len(m), A.nV))
        if not d and not g:
            break
        if len(m) <= n0 and not g:
            break

    st1 = verify_partial(A, B, m)
    info['broken_after'] = len(st1['broken'])
    info['consistent'] = st1['consistent']
    info['matched_exact'] = len(m)
    info['covered'] = st1['covered']
    info['ok_edges'] = st1['ok']

    if fill and len(m) < A.nV:
        added, approx, dup = fill_by_position(A, B, m, radius=radius,
                                              allow_duplicate=allow_duplicate)
        m.update(added)
        info['approx'] = approx
        info['dup'] = dup
        if approx:
            L(u'    最近点近似填充 %d 个（半径 %.4g）' % (len(approx),
              max(A.diag, B.diag) * 1e-3 if radius is None else radius))
        if dup:
            L(u'    %d 个点在 B 侧找不到剩余对应点，已复用最近点（会产生重合点）'
              % len(dup))

    L(u'  修复结果：精确对应 %d / %d，近似 %d，复用 %d，忽略后断边 %d 条'
      % (info['matched_exact'], A.nV, len(info['approx']), len(info['dup']),
         info['broken_after']))
    return m, info


def partial_summary(A, B, m, info):
    """生成一段给人看的总结"""
    lines = []
    lines.append(u'部分匹配：精确对应 %d / %d 个顶点（%.1f%%）'
                 % (info['matched_exact'], A.nV,
                    100.0 * info['matched_exact'] / max(1, A.nV)))
    lines.append(u'边：%d / %d 条可核对，其中 %d 条一致，%d 条缺失'
                 % (info['covered'], info['edges_total'], info['ok_edges'],
                    info['covered'] - info['ok_edges']))
    if info['dropped']:
        lines.append(u'被忽略的冲突点：%d 个 -> %s'
                     % (len(info['dropped']), _fmt_idx(info['dropped'])))
    if info['approx']:
        lines.append(u'最近点近似：%d 个 -> %s'
                     % (len(info['approx']), _fmt_idx(info['approx'])))
    if info['dup']:
        lines.append(u'无剩余点、复用最近点：%d 个 -> %s'
                     % (len(info['dup']), _fmt_idx(info['dup'])))
    return u'\n'.join(lines)


def _fmt_idx(seq, limit=40):
    seq = sorted(seq)
    if len(seq) <= limit:
        return u', '.join(str(x) for x in seq)
    return u', '.join(str(x) for x in seq[:limit]) + u' …（共%d个）' % len(seq)


# ==============================================================================
# 主求解器
# ==============================================================================

def _solve(A, B, seeds, pm, backtrack_limit, tag=''):
    """
    单次求解：把 seeds（用户给的对应点）与 pm（坐标匹配结果）合并后，
    传播 -> 回溯 -> 全边校验。返回 report dict。
    """
    report = {'ok': False, 'mapping': {}, 'mapping_exact': {}, 'reason': '',
              'ambiguous': [], 'unseeded': [], 'geom_conflicts': 0, 'tag': tag,
              'partial': False, 'partial_info': None}

    # 合并：坐标匹配打底，用户给的对应点优先
    base = dict(pm) if pm else {}
    conflicts = 0
    for a, b in (seeds or {}).items():
        if a in base and base[a] != b:
            conflicts += 1
        base[a] = b
    if conflicts:
        L(u'  其中 %d 个点与坐标匹配结果不一致，以边链/手动指定为准' % conflicts)

    if not base:
        report['unseeded'] = A.components()
        report['reason'] = u'没有任何可用对应点：请选一条循环边 / 边界边，或手动指定顶点对'
        return report

    # ---- 传播 -----------------------------------------------------------
    m, contra = propagate(A, B, base)
    L(u'约束传播后：%d / %d' % (len(m), A.nV))
    if contra:
        L(u'警告：%d 处邻接冲突，可能是种子选错或拓扑并非完全一致' % len(contra))

    # ---- 回溯补残余（按连通块逐个击破，每块解完立刻重新传播）-------------
    used_backtrack = False
    for _round in range(64):
        if len(m) == A.nV:
            break
        rest = set(range(A.nV)) - set(m.keys())
        blocks = _rest_blocks(A, rest, m, limit=backtrack_limit)
        if not blocks:
            break
        progressed = False
        old_limit = sys.getrecursionlimit()
        try:
            sys.setrecursionlimit(max(old_limit, backtrack_limit * 4 + 1000))
            for blk in blocks:
                before = len(m)
                inv = {b: a for a, b in m.items()}
                _backtrack(A, B, m, inv, set(blk), [500000])
                if len(m) > before:
                    progressed = True
                    used_backtrack = True
                m, _c = propagate(A, B, m)
                if len(m) == A.nV:
                    break
        finally:
            sys.setrecursionlimit(old_limit)
        L(u'第 %d 轮回溯后：%d / %d' % (_round + 1, len(m), A.nV))
        if not progressed:
            break

    # ---- 孤立顶点（度数为 0）无法靠拓扑确定，任意配对即可 ----------------
    # 它们不产生任何边，所以不影响全边校验，也不影响几何形状
    if len(m) < A.nV:
        rest_a = [v for v in range(A.nV) if v not in m]
        used_b = set(m.values())
        rest_b = [v for v in range(B.nV) if v not in used_b]
        if (len(rest_a) == len(rest_b)
                and all(A.deg[v] == 0 for v in rest_a)
                and all(B.deg[v] == 0 for v in rest_b)):
            for a, b in zip(rest_a, rest_b):
                m[a] = b
            L(u'  %d 个孤立顶点（没有任何连接）已任意配对' % len(rest_a))

    # ---- 校验 -----------------------------------------------------------
    ok, msg = verify(A, B, m)
    report['mapping'] = m
    if ok:
        report['ok'] = True
        report['reason'] = msg
        report['mapping_exact'] = dict(m)
        if used_backtrack:
            report['reason'] += u'（用到回溯：说明存在对称歧义，若是左右对称模型，' \
                                u'建议再补 1~2 组种子或用坐标匹配确认点序落在正确分支上）'
            L(u'注意：求解过程用到回溯，网格存在对称性，' 
              u'最终点序可能是「对称分支」上的解 —— 形状不受影响，'
              u'但按索引搬权重/形变时请确认对应关系正确。')
        # 与坐标匹配结果对照
        bad = report['geom_conflicts'] = _geom_conflicts(A, B, m, pm or {})
        if bad:
            L(u'警告：%d 个点与坐标匹配结果冲突，映射很可能落在对称分支上' % bad)
        return report

    # ---- 失败：告诉用户去哪补种子 ---------------------------------------
    rest = sorted(set(range(A.nV)) - set(m.keys()))
    frontier = [v for v in rest if any(x in m for x in A.adj[v])]
    report['ambiguous'] = (frontier or rest)[:200]
    comps = []
    for comp in A.components():
        if not any(v in m for v in comp):
            comps.append(sorted(comp)[:50])
    report['unseeded'] = comps
    report['reason'] = msg + u'。剩余 %d 个顶点未确定' % len(rest)
    if comps:
        report['reason'] += u'；有 %d 个独立壳完全没有种子点' % len(comps)
    return report


def build_mapping(A, B, seeds=(), seed_candidates=(), use_position=True,
                  backtrack_limit=400, allow_partial=False, partial_radius=None,
                  partial_fill=True, prune_conflicts=True):
    """
    主入口。依次尝试，返回第一个通过全边校验的结果：
      1) 坐标匹配（两模型形状完全重合时，这里通常就解完了）
      2) seed_candidates —— 由「循环边 / 边界边」对齐得到的若干种朝向
      3) 单点 seeds（备用）

    若全部失败且 allow_partial=True：改走「部分匹配」——
    忽略对不上的点，保证能对上的部分拓扑一致，剩下的用最近点近似填充。

    seeds           : [(a_idx, b_idx), ...] 或 [('A.vtx[1]', 'B.vtx[9]'), ...]
    seed_candidates : [{a: b, ...}, ...]，来自 align_chains / candidates_from_selection
    allow_partial   : 允许部分匹配（个别点对不上时也能出结果）
    partial_radius  : 近似填充的搜索半径（None = 包围盒对角线的 0.1%）
    partial_fill    : 未匹配点是否用最近点近似填充（False 则只输出能确定的部分）
    prune_conflicts : 是否删掉造成断边的点（False = 保留，只提示）
    """
    empty = {'ok': False, 'mapping': {}, 'reason': '', 'ambiguous': [],
             'unseeded': [], 'geom_conflicts': 0, 'tag': '',
             'partial': False, 'partial_info': None}

    if A.nV != B.nV and not allow_partial:
        empty['reason'] = u'顶点数不一致：A=%d  B=%d（若只想对上大部分，请勾选「允许部分匹配」）' \
                          % (A.nV, B.nV)
        return empty
    if A.nV != B.nV:
        L(u'警告：顶点数不一致 A=%d B=%d，部分匹配模式下继续（多出来的点会被近似处理）'
          % (A.nV, B.nV))
    if len(A.edges) != len(B.edges):
        L(u'警告：边数不一致 A=%d B=%d，可能并非严格同构，继续尝试…'
          % (len(A.edges), len(B.edges)))
    if A.nP != B.nP:
        L(u'警告：面数不一致 A=%d B=%d' % (A.nP, B.nP))

    # ---- 归一化单点种子 -------------------------------------------------
    seed_map = {}
    for s in seeds or ():
        if isinstance(s[0], str):
            _, ai = parse_vertex(s[0])
            _, bi = parse_vertex(s[1])
        else:
            ai, bi = int(s[0]), int(s[1])
        seed_map[ai] = bi

    # ---- 坐标匹配 -------------------------------------------------------
    pm = {}
    if use_position:
        pm = match_by_position(A, B)
        pm = prune_inconsistent(A, B, pm)
        if pm:
            L(u'坐标匹配：%d / %d 个顶点可直接按位置配对（容差 %.2e）'
              % (len(pm), A.nV, max(A.diag, B.diag) * 1e-5))
        if len(pm) == A.nV:
            rep = _solve(A, B, seed_map, pm, backtrack_limit, u'坐标匹配')
            if rep['ok']:
                return rep
            L(u'坐标匹配虽然完整但没通过校验，改用边链候选重试')

    # ---- 逐候选尝试 -----------------------------------------------------
    attempts = [(u'坐标匹配', dict(seed_map))]
    for i, sd in enumerate(seed_candidates or ()):
        d = dict(sd)
        d.update(seed_map)
        attempts.append((u'边链朝向#%d' % (i + 1), d))
    if seed_map and not seed_candidates:
        attempts.append((u'单点种子', dict(seed_map)))

    def _score(rep):
        """部分匹配下用「一致边数 - 断边惩罚」挑最优候选，而不是单纯看匹配点数"""
        st = verify_partial(A, B, rep['mapping'])
        return st['ok'] - 3 * len(st['broken'])

    best = None
    best_score = None
    for tag, user in attempts:
        if not user and not pm:
            continue
        L(u'—— 尝试【%s】：%d 个已知对应点 ——' % (tag, len(user)))
        rep = _solve(A, B, user, pm, backtrack_limit, tag)
        if rep['ok']:
            return rep
        s = _score(rep)
        if best is None or s > best_score:
            best, best_score = rep, s

    if best is None:
        return empty

    if not allow_partial:
        return best

    # ---- 部分匹配：忽略对不上的，先把能对上的对上 -----------------------
    L(u'—— 未能完全对应，转为【部分匹配】：忽略冲突点，保留能对上的部分 ——')
    m2, info = repair_partial(A, B, best['mapping'], fill=partial_fill,
                              radius=partial_radius, prune=prune_conflicts)
    best['partial'] = True
    best['partial_info'] = info
    best['mapping'] = m2
    best['mapping_exact'] = dict((a, b) for a, b in m2.items()
                                 if a not in set(info['approx']) | set(info['dup']))
    best['ok'] = True
    best['tag'] = best.get('tag') or u'部分匹配'
    best['reason'] = partial_summary(A, B, m2, info)
    if not info['consistent']:
        best['reason'] += u'\n注意：仍有 %d 条断边（无法两端都核对），' \
                          u'重合点/近似点所在区域可能不完全正确' % info['broken_after']
    L(best['reason'])
    return best


# ==============================================================================
# 应用：按 A 的点序重建 B
# ==============================================================================

def _new_points(A, B, m, use_source_positions=False):
    pts = om.MPointArray()
    for i in range(A.nV):
        j = m.get(i)
        if use_source_positions or j is None:
            p = A.points[i]
        else:
            p = B.points[j]
        pts.append(om.MPoint(p))
    return pts


def _copy_uvs(B, new_shape):
    fn = om.MFnMesh(get_mesh_fn(new_shape)[1])
    try:
        src_sets = list(B.fn.getUVSets())
    except Exception as e:
        L(u'读取 UV 集失败：%s' % e)
        return 0
    have = list(fn.getUVSets())
    n = 0
    for uv_set in src_sets:
        try:
            u, v = B.fn.getUVs(uv_set)
            counts, ids = B.fn.getAssignedUVs(uv_set)
            if uv_set not in have:
                try:
                    cmds.polyUVSet(new_shape, create=True, uvSet=uv_set)
                    have = list(fn.getUVSets())
                except Exception:
                    pass
            fn.setUVs(u, v, uv_set)
            fn.assignUVs(counts, ids, uv_set)
            n += 1
        except Exception as e:
            L(u'UV 集 [%s] 复制失败：%s' % (uv_set, e))
    try:
        fn.updateSurface()
    except Exception:
        pass
    if n:
        L(u'已复制 %d 个 UV 集' % n)
    return n


def _copy_smoothing(A, B, m, new_shape):
    """硬边/软边（按顶点对匹配，不依赖边序号）"""
    src = {}
    try:
        it = om.MItMeshEdge(B.path)
        while not it.isDone():
            a, b = it.vertexId(0), it.vertexId(1)
            src[(min(a, b), max(a, b))] = it.isSmooth()
            it.next()
    except Exception as e:
        L(u'读取原始硬边信息失败：%s' % e)
        return False

    new_fn, new_path = get_mesh_fn(new_shape)
    todo = []
    try:
        it = om.MItMeshEdge(new_path)
        while not it.isDone():
            a, b = it.vertexId(0), it.vertexId(1)
            b1, b2 = m[a], m[b]
            key = (min(b1, b2), max(b1, b2))
            if key in src:
                todo.append((it.index(), src[key]))
            it.next()
    except Exception as e:
        L(u'遍历新网格边失败：%s' % e)
        return False

    ok = 0
    for eid, smooth in todo:
        try:
            new_fn.setEdgeSmoothing(eid, smooth)
            ok += 1
        except Exception:
            continue
    try:
        new_fn.updateSurface()
    except Exception:
        pass
    L(u'硬/软边：%d / %d 条边已还原' % (ok, len(todo)))
    return ok > 0


def _copy_colors(B, new_shape, m, max_verts=60000):
    """顶点色：优先用 transferAttributes（两网格几何完全重合，世界空间采样是精确的）"""
    try:
        sets = cmds.polyColorSet(B.shape, query=True, allColorSets=True) or []
    except Exception:
        sets = []
    if not sets:
        return 0
    try:
        cmds.transferAttributes(B.shape, new_shape,
                                transferPositions=0, transferNormals=0,
                                transferUVs=0, transferColors=1,
                                sampleSpace=0)
        L(u'顶点色：已用 transferAttributes 复制（%d 个 colorSet）' % len(sets))
        return len(sets)
    except Exception as e:
        L(u'transferAttributes 复制顶点色失败（%s），改用逐点复制…' % e)

    if B.nV > max_verts:
        L(u'顶点数 %d 超过 %d，跳过逐点顶点色复制' % (B.nV, max_verts))
        return 0

    inv = {b: a for a, b in m.items()}   # B 索引 -> 新网格索引
    done = 0
    for cs in sets:
        try:
            cmds.polyColorSet(new_shape, create=True, colorSet=cs)
        except Exception:
            pass
        copied = 0
        for i in range(B.nV):
            j = inv.get(i)
            if j is None:
                continue
            try:
                c = cmds.polyColorPerVertex('%s.vtx[%d]' % (B.shape, i),
                                            query=True, rgb=True, colorSet=cs)
            except Exception:
                break
            if not c:
                continue
            try:
                cmds.polyColorPerVertex('%s.vtx[%d]' % (new_shape, j),
                                        rgb=list(c), colorSet=cs)
                copied += 1
            except Exception:
                break
        L(u'顶点色 [%s]：逐点复制 %d / %d' % (cs, copied, B.nV))
        done += 1
    return done


def _copy_skin(A, B, m, new_shape):
    """把 B 上的蒙皮权重按映射搬到新网格"""
    if oma is None:
        L(u'OpenMayaAnim 不可用，跳过蒙皮')
        return None
    try:
        sc = cmds.findRelatedSkinCluster(B.transform)
    except Exception:
        sc = None
    if not sc:
        L(u'B 上没有 skinCluster，跳过蒙皮复制')
        return None
    sc = sc[0] if isinstance(sc, list) else sc
    if not sc:
        return None

    try:
        infs = cmds.skinCluster(sc, query=True, influence=True) or []
        if not infs:
            return None
        mi = cmds.skinCluster(sc, query=True, maximumInfluences=True)
        obey = cmds.skinCluster(sc, query=True, obeyMaxInfluences=True)
        nrm = cmds.skinCluster(sc, query=True, normalizeWeights=True)
        smethod = cmds.skinCluster(sc, query=True, skinningMethod=True)

        # --- 读原权重 ---
        sel = om.MSelectionList()
        sel.add(sc)
        sc_obj = sel.getDependNode(0)
        sc_fn = oma.MFnSkinCluster(sc_obj)

        comp_fn = om.MFnSingleIndexedComponent()
        comp = comp_fn.create(om.MFn.kMeshVertComponent)
        try:
            comp_fn.setCompleteData(B.nV)
        except Exception:
            comp_fn.addElements(list(range(B.nV)))

        inf_idx = om.MIntArray()
        for i in range(len(infs)):
            inf_idx.append(i)

        w = sc_fn.getWeights(B.path, comp, inf_idx)
        if len(w) != B.nV * len(infs):
            L(u'权重数量异常（%d），跳过蒙皮复制' % len(w))
            return None

        # --- 建新 skinCluster ---
        new_sc = cmds.skinCluster(infs, new_shape,
                                  maximumInfluences=mi,
                                  obeyMaxInfluences=obey,
                                  normalizeWeights=nrm,
                                  skinningMethod=smethod,
                                  toSelectedBones=True)[0]

        # --- 按映射重排后写入 ---
        w2 = om.MDoubleArray()
        for i in range(A.nV):
            src = m[i] * len(infs)
            for k in range(len(infs)):
                w2.append(float(w[src + k]))

        new_fn, new_path = get_mesh_fn(new_shape)
        c2_fn = om.MFnSingleIndexedComponent()
        c2 = c2_fn.create(om.MFn.kMeshVertComponent)
        try:
            c2_fn.setCompleteData(A.nV)
        except Exception:
            c2_fn.addElements(list(range(A.nV)))

        sel2 = om.MSelectionList()
        sel2.add(new_sc)
        sc2_fn = oma.MFnSkinCluster(sel2.getDependNode(0))
        sc2_fn.setWeights(new_path, c2, inf_idx, w2, False)
        L(u'蒙皮：已复制 %d 个影响的权重' % len(infs))
        return new_sc
    except Exception as e:
        L(u'蒙皮复制失败：%s（可手动用 copySkinWeights 处理）' % e)
        return None


def apply_mapping(A, B, m, name=None, keep_original=True,
                  use_source_positions=False, copy_uv=True, copy_smoothing=True,
                  copy_colors=False, copy_skin=False, highlight=None):
    """
    生成一个新模型：面表 = A 的面表，顶点坐标 = B 的坐标（按 m 重排）。
    highlight: 需要高亮（选中）的顶点索引列表，用于标记「近似/复用」的点，方便检查。
    返回新模型 transform 名。
    """
    L(u'重建网格：%d 顶点 / %d 面' % (A.nV, A.nP))
    missing = sum(1 for i in range(A.nV) if i not in m)
    if missing:
        L(u'  其中 %d 个顶点没有对应关系，将沿用 A（源模型）的坐标' % missing)

    pts = _new_points(A, B, m, use_source_positions)

    tr_path = om.MDagPath(B.path)
    tr_path.pop()
    try:
        parent_obj = om.MFnDagNode(tr_path).parent(0)
    except Exception:
        parent_obj = om.MObject.kNullObj

    mesh_fn = om.MFnMesh()
    new_obj = mesh_fn.create(pts, A.counts, A.conn, parent=parent_obj)

    if new_obj.apiType() == om.MFn.kTransform:
        new_tr_path = om.MFnDagNode(new_obj).getPath()
        new_sh_path = om.MDagPath(new_tr_path)
        new_sh_path.extendToShape()
    else:
        new_sh_path = om.MFnDagNode(new_obj).getPath()
        new_tr_path = om.MDagPath(new_sh_path)
        new_tr_path.pop()

    new_tr = new_tr_path.partialPathName()
    new_sh = new_sh_path.partialPathName()

    # 变换矩阵跟随 B
    try:
        mat = cmds.xform(B.transform, query=True, matrix=True, ws=True)
        cmds.xform(new_tr, matrix=mat, ws=True)
    except Exception as e:
        L(u'复制变换矩阵失败：%s' % e)

    # 命名
    want = name or (B.transform + '_vorder')
    try:
        new_tr = cmds.rename(new_tr, want)
        rel = cmds.listRelatives(new_tr, shapes=True, fullPath=False) or []
        if rel:
            new_sh = rel[0]
    except Exception as e:
        L(u'重命名失败：%s' % e)

    if copy_uv:
        _copy_uvs(B, new_sh)
    if copy_smoothing:
        _copy_smoothing(A, B, m, new_sh)
    if copy_colors:
        _copy_colors(B, new_sh, m)
    if copy_skin:
        _copy_skin(A, B, m, new_sh)

    try:
        cmds.polyNormalPerVertex(new_sh, unFreezeNormal=True)
    except Exception:
        pass
    try:
        cmds.sets(new_sh, add='initialShadingGroup')
    except Exception:
        pass

    # 高亮「近似 / 复用」的点，方便人工检查
    if highlight:
        try:
            cmds.select(['%s.vtx[%d]' % (new_sh, i)
                         for i in sorted(set(highlight))[:2000]], replace=True)
            L(u'已选中 %d 个「近似/复用」顶点（%s），请检查这些点是否符合预期'
              % (len(highlight), new_sh))
        except Exception as e:
            L(u'高亮顶点失败：%s' % e)

    if keep_original:
        try:
            cmds.setAttr(B.transform + '.visibility', 0)
            L(u'原模型已保留但隐藏：%s' % B.transform)
        except Exception:
            pass
    else:
        try:
            old_name = B.transform
            cmds.delete(B.transform)
            cmds.rename(new_tr, old_name)
            new_tr = old_name
            L(u'原模型已删除，新模型沿用其名称')
        except Exception as e:
            L(u'删除原模型失败：%s' % e)

    L(u'完成：%s（顶点顺序已与 %s 一致）' % (new_tr, A.transform))
    return new_tr


# ==============================================================================
# 一步到位
# ==============================================================================

def transfer_vertex_order(source, target, seeds=(), seed_candidates=(), name=None,
                          use_position=True, keep_original=True,
                          use_source_positions=False, copy_uv=True,
                          copy_smoothing=True, copy_colors=False, copy_skin=False,
                          allow_partial=False, partial_radius=None):
    A = MeshGraph(source)
    B = MeshGraph(target)
    rep = build_mapping(A, B, seeds=seeds, seed_candidates=seed_candidates,
                        use_position=use_position, allow_partial=allow_partial,
                        partial_radius=partial_radius)
    if not rep['ok']:
        raise RuntimeError(rep['reason'])
    hl = None
    info = rep.get('partial_info') or {}
    if info:
        hl = list(info.get('approx') or []) + list(info.get('dup') or [])
    return apply_mapping(A, B, rep['mapping'], name=name, keep_original=keep_original,
                         use_source_positions=use_source_positions, copy_uv=copy_uv,
                         copy_smoothing=copy_smoothing, copy_colors=copy_colors,
                         copy_skin=copy_skin, highlight=hl)


def remap_skin_weights(mesh, m_src_to_dst, src_skin=None):
    """
    进阶：不重建网格，只把 skinCluster 的权重按映射重排（原地改）。
    m_src_to_dst: dict 源顶点索引 -> 目标顶点索引（本工具里 A->B 是 m，B->A 是反查）
    """
    # 本函数留给批处理脚本使用，UI 未接入
    raise NotImplementedError(u'请使用 apply_mapping(copy_skin=True)')
