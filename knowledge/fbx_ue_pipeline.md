# Maya 到 UE 的 FBX 导出与导入校验

Tags: fbx, maya, unreal, export, sidecar, import

## FBX 导出设置
export.fbx 段固定：up_axis 为 Y、unit 为 cm、bake_animation 为 true、include_constraints 为 false、include_meshes 为 true、frame_range 为 [1, 120]。
export.usd 段另有 up_axis 为 Y、export_skel 为 true 的骨架导出选项。
默认规范以 Maya Y-up / cm 为源，导出到 UE 5.8 及以上；轴向或单位变化会被判为阻断性偏差。

## sidecar 快照 diff
Export Bridge 的 pre_export 把导出前快照写成后缀 `.tak_snapshot.json` 的 sidecar，sidecar_version 为 1，内容是严格 JSON。
post_export 读取 sidecar 与导出后快照做 diff，偏差类型包括 unit、up_axis、joint_count、joint_missing、joint_added、joint_renamed、bind_matrix、parent、frame_range。
其中 unit、up_axis、joint_count、joint_missing、bind_matrix、parent、frame_range 属于阻断类型，可直接拦下导出；spec 可用 export.fbx.verify.blocking 与 ignore 覆盖。
frame_range 在 UE 导入校验里默认归入 import_warn_fields，只提示不阻断。

## UE 导入与根运动
UE 侧由 ue_import_guard 消费 sidecar，把 diff 结果翻译成 Finding，并校验 UE 资产契约。
anim_sequences 每项含 compression、root_motion、curve_count、curves；规范要求 animation_compression 为 Default，root_motion 为 false。
skeletal_meshes 的 lods 每项含 allow_deformer；规范 ue.allow_mesh_deformer 为 false，因此 LOD 不允许挂 deformer。
ue.skeleton_asset 默认为 `/Game/Characters/Default/SK_Default`，skeleton.compatible_with 需与之匹配。
任一侧缺少 joints 时不做 diff，直接跳过并说明原因，避免 UE Python 读不到资产时误判为导入失败。

## 压缩与 LOD 规则
动画压缩、根运动与 LOD 的判定都是确定性规则，只读快照、不 import unreal，也不修改 UE 资产。
根运动开启或压缩设置偏离 Default 会被报出；LOD 允许 deformer 与规范冲突时同样报错。
完整链路是 Maya → FBX → sidecar 快照 → UE 导入校验，B8 项要求用真实角色资产跑一次前后快照 diff 并确认差异为空。
