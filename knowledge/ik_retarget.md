# IK Rig 与 Retargeter 链映射

Tags: ik, retarget, unreal, chain, mapping

## UE 侧可用 API
docs/COMPATIBILITY.md 的实测结论：UE 5.7.2 与 5.8.4 两个版本的 27 项 Python API 探测结果完全一致。
`IKRigDefinition` 与 `IKRetargeter` 均已暴露，`ControlRig`、`Skeleton`、`SkeletalMesh`、`AnimSequence` 可用，因此 IK Rig / Retargeter 链映射可以离线校验而不必进入 GUI。
`EditorDialogLibrary` 不存在，对话框相关代码须改走 `EditorDialog`；`MLDeformerAsset` 等类型在 Python 侧未暴露。

## 链映射数据契约
ik_retarget_guard 从 snapshot.metadata.retarget 或 options.retarget 读取重定向数据，options 优先。
ik_rig.chains 声明链名与骨骼列表，ik_rig.retarget_root 指定重定向根骨；retargeter.chain_mapping 记录源链到目标链的映射。
内置规则覆盖链映射完整性、骨骼命名不匹配、root / pelvis 偏移、姿态偏差、必需链与映射冲突，共 6 条，全部只读。
缺省策略是字段缺失即整条规则跳过，绝不臆造数值；Root / Pelvis 与姿态容差默认值均为 0.01。

## mapping asset v1.0
ik_retarget_guard/mapping.py 的 write_mapping_asset 写出 version 为 `1.0` 的映射资产，包含 entries、unmapped_source、unmapped_target、conflicts 与 metadata。
entries 每项含 source、target、confidence、method，由确定性归一化规则与内置词典生成候选，相同输入始终得到完全一致的结果。
load_mapping_asset 会校验 version、entries 与 conflicts 的类型，非法资产直接抛 ValueError，避免脏数据流入下游。
run_report 会把 mapping 摘要、链映射表与 handoff 写进 report.metadata，交接给 P5.2 Skeleton Mapper 消费。

## 冲突需人工确认
多个 source 指向同一 canonical、或候选存在歧义时会进入 conflicts，相关骨骼不会擅自写入 entries。
conflicts 与 unmapped_source / unmapped_target 构成必须保留的人工复核队列；AI 可补充候选，但不能替代人工确认。
handoff 明确写出 consumer 为 P5.2 Skeleton Mapper、asset_version 为 1.0，并指出冲突须人工确认；本插件不实现 UE 资产写入，也不 import unreal。
