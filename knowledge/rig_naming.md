# 绑定命名规范与骨架层级

Tags: naming, joint, namespace, schema, skeleton

## 命名正则与 side token
默认规范实例是仓库根目录的 schema/rig_spec.default.json，版本 1.0.0，描述为「以 Maya Y-up / cm 为源，导出到 UE5.8+ 的标准人形骨架」。
naming.joint_pattern 只允许 `^(root|pelvis|spine_[0-9]{2}|neck|head|clavicle_[LR]|upperarm_[LR]|lowerarm_[LR]|hand_[LR]|thigh_[LR]|calf_[LR]|foot_[LR]|toe_[LR]|FACIAL_[A-Za-z0-9_]+)$`。
naming.side_tokens 为 `_L` 与 `_R`；naming.case_sensitive 为 true，因此大小写不一致会被判为不合规。
naming.forbidden_tokens 明确禁止空格、点「.」、竖线「|」、冒号「:」以及 `copy` / `Copy`；带冒号的命名空间前缀在导出前必须清理。

## control namespace
naming.control_namespace 为 `CTRL`，控制器统一归入该命名空间。
命名判定由 tak.core.rules.builtin 中的确定性规则完成，AI 只负责解释与生成候选修复，不参与是否合规的判定。
spec_enforcer 插件按 rig_spec 做校验，并把违规项翻译成参数化 DSL 修复计划；默认 dry-run，必须人工二次确认才写场景。

## 必需关节
skeleton.required_joints 共 19 个：root、pelvis、spine_01、neck、head、clavicle_L、clavicle_R、upperarm_L、upperarm_R、lowerarm_L、lowerarm_R、hand_L、hand_R、thigh_L、thigh_R、calf_L、calf_R、foot_L、foot_R。
缺失任一关节都会被骨架完整性规则拦下；重命名或增删关节会在导出前后快照 diff 中体现为 joint_renamed / joint_added / joint_missing。

## 层级深度与变换
skeleton.max_depth 为 12，超过该深度的层级视为不合规。
skeleton.allow_negative_scale 为 false，不允许负缩放；skeleton.allow_frozen_transforms_only 为 true，要求冻结变换。
dcc.min_versions 规定 Maya / 3ds Max / MotionBuilder 最低 2023，Unreal 最低 5.8。

## joint orient 容差
skeleton.joint_orient_tolerance_deg 为 0.5 度，用于 joint orient 一致性校验。
轴向与单位在 export.fbx 中固定：up_axis 为 Y、unit 为 cm；任何偏差都会在导出前后 diff 中被归类为 up_axis / unit 阻断项。
