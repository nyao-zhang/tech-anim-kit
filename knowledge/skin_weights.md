# 蒙皮权重规范

Tags: skin, weights, influences, deformer, normalize

## 影响数上限
skin.max_influences 为 4，即每个顶点最多受 4 根骨骼影响。
确定性规则 skin.max_influences 读取快照里的 skin.max_influences_found，超过 spec 上限即报 ERROR，修复建议是执行 limit influences 到 4 根并重新归一。
spec_enforcer 与 rig_health_guard 都按同一份 rig_spec 判定，避免不同工具给出互相矛盾的结论。

## 权重和与归一化
skin.weight_sum_tolerance 为 0.001；skin.normalize 为 true，要求每个顶点的权重和归一到 1。
skin.weight_sum 规则比较快照的 skin.weight_sum_deviation 与容差；偏差超限报 ERROR。
Maya 适配器统计 influences 时只计入大于权重 epsilon 的影响，避免把极小权重误算成第 5 根影响。

## 允许的 deformer
skin.allowed_deformers 为 `skinCluster`、`blendShape`、`cluster`、`wrap` 四类。
skin.unknown_deformer 规则会把不在白名单里的 deformer 报为 ERROR；适配器通过节点类型收集 deformers 并去重排序。
规范 ue.allow_mesh_deformer 为 false，因此 UE 侧导入校验要求 LOD 的 allow_deformer 为 false；ML Deformer 相关类型在 UE 5.7 / 5.8 的 Python 侧也未被暴露。

## 采样上限与容差
适配器统一输出 SkeletonSnapshot 的 skin 段，字段包含 influences、max_influences_found、weight_sum_deviation、deformers、sampled_vertices 与 truncated。
Maya 适配器的 max_sample_vertices 为 512，即单次最多采样 512 个顶点；顶点数超过采样上限时 truncated 置为 true。
导出桥接的默认矩阵容差为 0.0001，UE 导入校验默认忽略 bind_matrix（UE Python 不暴露骨骼静止变换），并可用 import_matrix_tolerance、import_ignore_fields、import_warn_fields 覆盖。
采样是性能与精度的折中：真机全量顶点校验仍需在项目侧按 VALIDATION.md 的 B8 复验。
