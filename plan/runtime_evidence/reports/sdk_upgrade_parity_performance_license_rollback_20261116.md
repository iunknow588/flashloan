# TASK-103 / AC-155｜SDK 升级差异报告（parity / 性能 / 许可证 / 回滚）

- 生成日期：2026-11-16
- 模式：`read_only`｜生产库写入 **0**｜合约部署 **0**｜链上交易 **0**｜切网 **0**｜广播 Authority 切换 **0**
- 机器报告：`plan/runtime_evidence/reports/sdk_upgrade_parity_performance_license_rollback_20261116.json`
- 关联：`AC-155`、`TASK-036`（见 `task036_executor_expansion_evaluation_20261116.md`）

## 1. 结论摘要

| 维度 | 结论 |
|---|---|
| parity | 对照工具链与 fail-closed 闸门**已就位**；主网 `manager_v4_reviews=0`，**无实际链上样本**，因此本轮无法给出真实逐笔差异分布 |
| 性能 | 版本触发重算实窗 **11,836 ms**（基线不变）；容量计算本轮**未重测**（只读边界） |
| 许可证 | SDK 与 alloy 依赖链共 **164 / 169** 个传递依赖，**未发现 copyleft**，与 workspace `MIT` 声明兼容 |
| 回滚 | 锁文件回滚点已记录，6 步回滚流程可执行，回滚风险**低**（无合约依赖该 SDK） |

> **必须强调**：本报告不宣称主网已完成 SDK parity 验证。
> 主网复核表为空，缺少真实样本是**客观事实**，不能用"工具已就位"替代"差异已核清"。

## 2. Parity（逐笔对照口径）

### 2.1 当前 authority

| 项 | 值 |
|---|---|
| crate | `uniswap-v4-sdk` |
| 版本 | `2.0.0` |
| Cargo.lock checksum | `3c69ec7fc9d9c27dab0194c0c373fce0fb78bcdfa14e416d71597a97be7ba48d` |
| 来源 | crates.io registry（精确 checksum 锁定） |

直接依赖：`alloy`、`alloy-contract`、`alloy-primitives`、`alloy-sol-types`、
`derive_more`、`thiserror 2.0.21`、`tokio`、`uniswap-sdk-core`、`uniswap-v3-sdk`。

### 2.2 对照方法

| 工具 | 用途 |
|---|---|
| `review_manager::parity` | 链上读取报价 vs 链下 SDK 报价 |
| `review_manager::shadow` | 链下影子结果 vs 已观测链上结果 |
| `review_manager::single_tick` | 单 tick 边界等价性 |
| `pool_manager::parity_state` | pool scope 状态一致性 |
| `pool_manager::state_transition` | 状态转移一致性 |
| `pool_manager::single_tick_capacity` | 单 tick 容量等价性 |
| `review_manager::state_transition_gate` | **广播前 fail-closed 闸门** |

Parity 按 route group 逐组判定；链上与链下结果不一致时闸门保持关闭。

### 2.3 差异分类

| 类别 | 定义 | 处置 |
|---|---|---|
| **可解释差异** | 有具名成因（gas 舍入、区块时间戳、token 小数归一、fee tier 精度）且落在显式容差内 | 记录并放行（带容差） |
| **单 tick 边界差异** | 仅在精确输入金额跨入下一 tick 时出现；同一路线链上与链下选中不同 tick | 记录；须与该路线容量口径一致 |
| **不可解释差异** | 排除上述两类后仍然存在 | **禁止广播**，route group 保持挂起 |

### 2.4 当前主网状态（决定本轮限制）

| 指标 | 值 |
|---|---|
| `manager_v4_reviews` | **0** |
| `manager_v4_closed_loops` | 106 |
| `manager_v4_review_broadcast_requests` | 0 |
| `manager_broadcast_attempt_diagnostics` | 0 |
| healer `productionState` | `review_stalled` |
| `safeToAutoBroadcast` | `false` |

**含义**：主网复核表为空 ⇒ 不存在可供比对的真实链上样本。
产生样本需要生产写入授权，**不在本轮 L0–L2 授权范围内**。
因此本轮交付的是**可执行的对照口径 + 分类法 + 闸门**，而不是"差异已清零"的结论。

## 3. 性能

### 3.1 新排序 label 触发的全链重算

测量器：`composite_manager::measure_recompute_latency`（只读，`SQLITE_OPEN_READ_ONLY`）
探针：`flashloan/tools/composite_manager/examples/p0_4_recompute_latency_probe.rs`
方法：6 步 pipeline 使用固定模拟步进成本，结果可复现，不依赖机器负载。

| 窗口 | 上游版本变化 | 全链完成 | 总延迟 |
|---|---|---|---|
| window-1（版本触发） | 是 | 是 | **11,836 ms** |
| window-2（幂等重跑） | 否 | 是 | 11,743 ms |

聚合（仅统计"版本变化 + 全链完成"）：

- `versionChangedRunCount = 1`
- `medianTotalLatencyMs = 11836`
- `minTotalLatencyMs = 11836`／`maxTotalLatencyMs = 11836`
- `totalFailureRetryCount = 0`

逐步 checkpoint（window-1）：

| 步骤 | 耗时 | 累计延迟 |
|---|---|---|
| `pool_scope` | 1,339 ms | 1,421 ms |
| `candidate_edge` | 1,082 ms | 2,625 ms |
| `candidate_exchange` | 1,787 ms | 4,502 ms |
| `closed_loop` | 3,300 ms | 7,897 ms |
| `review` | 2,970 ms | 10,964 ms |
| `broadcast` | 753 ms | 11,836 ms |

主要成本：`closed_loop`（27.9%）+ `review`（25.1%）≈ **53%**。

### 3.2 容量计算

工具：`pool_manager::single_tick_capacity`、`pool_manager::usdg_depth`、
`pool_manager::depth`、`token_manager::economic_gate`。

**本轮未重测**。容量测量需经主网库读取生产 pool scope，超出本轮只读审计边界；
沿用 `plan/runtime_evidence/reports/val4_*` 已记录数值，不重复声明新测量。

## 4. 许可证

| 项 | 值 |
|---|---|
| workspace 声明 | `license = "MIT"`、`rust-version = "1.91"`、`edition = "2021"` |
| 声明位置 | `flashloan/tools/Cargo.toml` `[workspace.package]` |
| SDK | `uniswap-v4-sdk 2.0.0`，MIT |
| SDK 传递依赖（深度 3） | **164** 个 |
| alloy 传递依赖（深度 3） | **169** 个 |

结论：

1. SDK 与 alloy 依赖链均来自 crates.io registry，采用与 workspace `MIT` 兼容的宽松许可证。
2. 依赖闭包内**未出现 copyleft**（GPL/LGPL/AGPL）crate。
3. 闭包内每个 crate 都被 `Cargo.lock` 精确 checksum 锁定 ⇒ 许可证集合可复现。

## 5. 回滚

### 5.1 锁文件回滚点

| 文件 | 当前条目 |
|---|---|
| `flashloan/rs-src/Cargo.lock` | `uniswap-v4-sdk 2.0.0` / `3c69ec7fc9d9c27dab0194c0c373fce0fb78bcdfa14e416d71597a97be7ba48d` |

### 5.2 回滚流程

1. 从版本控制恢复 `uniswap-v4-sdk` 的旧 `Cargo.lock` 条目（版本 + checksum）。
2. `cargo update -p uniswap-v4-sdk --precise <previous-version>`，随后 `cargo test --locked`（`flashloan/rs-src`）。
3. `cargo test --locked --workspace`（`flashloan/tools`）与 `cargo build --release --locked`（`flashloan/rs-src`）。
4. **重跑 parity 闸门**：`review_manager::state_transition_gate` 必须保持 fail-closed，
   直至每个候选 route group 的链上读取报价与链下报价一致。
5. **重跑 `scripts/verify_pool_manager_binary_matches_source.py`**：依赖变更会影响容量计算
   所依赖的 SDK 数学，pool-manager 二进制必须重建并重写 source stamp 后，healer 才可驱动它。
6. **确认主网库未变**：size 保持 `3507453952` 字节，`manager_v4_reviews` 保持 `0`，
   healer 仍报 `review_stalled` 且 `safeToAutoBroadcast=false`。

### 5.3 回滚风险

**低**。没有任何合约字节码依赖该 Rust SDK —— SDK 只驱动链下报价与容量计算。
回滚会改变报价结果，因此**必须重新验证 parity 闸门**后才能启用任何广播。

## 6. 安全断言

| 断言 | 实测值 |
|---|---|
| 主网库路径 | `flashloan/db/local_store_4663.sqlite3` |
| 主网库 size | `3507453952` 字节（未变） |
| `manager_v4_reviews` | `0` |
| `manager_v4_closed_loops` | `106` |
| `manager_v4_review_broadcast_requests` | `0` |
| `manager_broadcast_attempt_diagnostics` | `0` |
| healer `productionState` | `review_stalled` |
| `safeToAutoBroadcast` | `false` |
| 是否宣称已产生套利广播交易 | **否** |
