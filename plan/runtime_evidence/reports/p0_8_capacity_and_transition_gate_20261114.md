# P0-8｜折叠单 tick 容量贯通与三态执行门禁（2026-11-14 第 2 轮）

## 1. 本轮完成内容

| 区域 | 变更 | 验收 |
|---|---|---|
| `manager_db` | 新增迁移 `20261114_008_single_tick_capacity_schema`：`manager_v4_closed_loops`、`manager_v4_reviews`、`manager_v4_review_broadcast_requests`、`manager_cow_execution_attempts` 各增加 `single_tick_capacity_anchor_raw` 与 `single_tick_capacity_binding_pool_id` | migration smoke 5 项通过 |
| `closed_loop_manager` | 折叠单 tick 容量（`M_cap`）不再只作过滤器后丢弃，改为计算一次并持久化到闭环行，同时记录约束池 | 20 项测试通过 |
| `review_manager` | 复核门禁强制 `amount_in <= M_cap`；缺折叠容量时以 `single_tick_capacity_unavailable` fail-closed；新增 `state_transition_gate` 模块执行三态门禁 | 122 项测试通过 |
| `broadcast_manager` | 广播尝试携带复核已证明的折叠容量，作为广播金额未超界的持久证据 | 12 项测试通过 |
| 运行时 | Python 任务配置发布 `singleTickCapacityRaw` 与 `singleTickCapacityBasis`；Rust 阶梯优先消费折叠容量，`maxProbeAmountRaw` 仅作兼容回退并记录口径 | rs-src 111 项、Python 180 项通过 |

## 2. 三态语义

| 判定 | 含义 | 广播 |
|---|---|---|
| `fatal` | 价格跨出预设 Tick 区间，或活跃流动性不可证明 | 禁止 |
| `adverse` | 同输入预期输出下降（稳定原因码 `adverse_price_move`） | 禁止 |
| `unchanged` | 同输入输出不变 | 允许，沿用已证明容量 |
| `favorable` | 同输入输出改善且未跨 Tick | 允许，但容量只是**候选上界**，仍须按最终输入量逐 hop 重算 |

## 3. Fail-closed 清单

- 无折叠容量 → `single_tick_capacity_unavailable`。
- 最终输入量超过折叠容量 → `final_input_exceeds_single_tick_capacity`。
- 期望态或当前态缺失 → `expected_pool_state_unavailable` / `current_pool_state_unavailable`。
- tick 间距变化 → `tick_spacing_changed`。
- 期望输入量跨 tick → `expected_amount_crosses_tick`。
- 市场变坏 → `adverse_price_move`。

## 4. 回归结果

| 组件 | 结果 |
|---|---|
| pool-manager | 131 passed |
| closed-loop-manager | 20 passed |
| review-manager | 122 passed |
| broadcast-manager | 12 passed |
| manager-api | 24 passed |
| manager-db | 5 passed |
| composite-manager | 17 passed |
| rs-src | 111 passed |
| 主网执行器合约 | 24 passed |
| Python 桥接与运行时 | 180 passed |

`flashloan/tools` 与 `flashloan/rs-src` 两棵 Rust 树 `cargo fmt --all -- --check` 均干净。

## 5. 仍未完成（保持未勾选）

1. 逐 hop `sqrtPriceLimitX96` 尚未写入生产广播载荷；合约侧已有兼容的有界 calldata 构建器，但生产请求尚未切换。
2. 合约侧尚未读取链上当前池状态并重做三态校验，当前仍只信链下 limit。
3. 批内单闭环 revert 隔离尚未端到端证明。
4. 链上/链下最终三跳输入量 parity 尚未记录。
5. 生产广播开关保持关闭；本轮未写生产库、未切换 Authority、未广播、未替换锁定二进制。

## 6. 机器证据

- `plan/runtime_evidence/reports/p0_8_capacity_and_transition_gate_20261114.json`
- `plan/runtime_evidence/reports/p0_8_state_transition_probe_20261114.json`
- `plan/runtime_evidence/reports/p0_8_bounded_calldata_20261114.json`