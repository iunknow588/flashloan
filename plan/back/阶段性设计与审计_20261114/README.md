# 阶段性设计与审计归档说明（2026-11-14）

本目录保存已完成或已冻结的阶段性审计、决策与 ADR。它们仍具有以下价值，因此不删除：

- 解释活动任务为何采用当前架构和门禁；
- 为生产变更、回滚与审计提供历史依据；
- 保留机器证据路径、版本口径与已验证结论；
- 避免已完成过程重新进入活动任务队列。

## 文件状态

| 文件 | 归档理由 | 当前活动任务承接位置 |
|---|---|---|
| `app_runtime_authority_audit_20261114.md` | app_runtime 调用链和 Rust authority 审计已完成 | `plan/开发计划_20261113.md` 的 Authority 边界 |
| `version1_scope_roles_evidence_contract_20261114.md` | 版本 1 范围、角色和证据契约已冻结 | 三份活动文档的硬约束 |
| `p0_1_arch4_shadow_diff_attribution_20261114.md` | ARCH-4-A 差异归因已完成 | 后续 ARCH-4-B/C 任务 |
| `p0_4_recompute_latency_20261114.md` | 重算延迟实窗已完成 | 生产门禁与长期统计 |
| `p0_5_valuation_expiry_policy_20261114.md` | USDG TTL/过期/no-route 语义已定案 | P0-9/P1 估值落库任务 |
| `p0_6_single_tick_final_gate_20261114.md` | 逐 hop single-tick 最终门禁已完成 | P0-8/P2/P3 剩余任务 |
| `p0_8_single_tick_capacity_state_change_adr_20261114.md` | 容量与状态变化设计已冻结 | P0-8 实现与生产接线任务 |
| `p0_9_database_baseline_adr_20261114.md` | 数据库基线设计和只读审计已完成 | P0-9/P1 生产收敛任务 |

归档文件不得作为当前任务来源。当前唯一入口仍是：

- `plan/未完成任务清单_20261113.md`
- `plan/开发计划_20261113.md`
- `plan/主网编排器审查修复_check_list_20261113.md`

