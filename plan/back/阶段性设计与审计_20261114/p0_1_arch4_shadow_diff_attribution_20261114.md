# P0-1｜ARCH-4-A 影子差异归因（2026-11-14）

- 计划版本：v9.8
- 阶段：P0-1（ARCH-4-A）
- 模式：只读证据（`mode=read_only`，`productionWrite=false`）
- 生产库写入：0；权限切换：0；主网广播：0
- 证据报告：`logs/plan/runtime_evidence/reports/arch4_shadow_diff_2026-11-13T20-54-22-236091900-00-00.json`
- 证据 SHA-256：`baa16bf531ba27a8aab6d7ed2d5cce8a0dea31fa8496032e7cf8a6f947ad2d36`

---

## 1. 结论

P0-1 的基线存在 **4 条 unexplained**。本次复核把 4 条全部收敛，并额外发现并修复了 **3 个使「未解释」得以长期存在的传输层根因**。

修复后的重跑结果：

| 指标 | 基线（2026-11-13T14-34-37） | 修复后（2026-11-13T20-54-22） |
| --- | --- | --- |
| 比较数 | 84（42 池 × 2 方向） | 24（12 个零钩子池 × 2 方向） |
| `unexplainedCount` | **4** | **0** |
| `executableMismatchCount` | 23 | 6 |
| 台账条目 | 60（全部误标 `unexplained`） | 6（全部 `explained`） |
| `untrustedChainCount` | 0 | 0 |
| `memoryDeviationBlockCount` | 0 | 0 |
| 状态版本 | 混用时间戳 / `latest` | 全部 `block:113647275` |

**判定：P0-1 通过。** 4 条 unexplained 全部有明确归因，且归因中包含 2 项真实代码缺陷（已修复）与 2 项结构性/语义边界（已接受为版本边界）。

---

## 2. 基线 4 条 unexplained 的逐条归因

### 2.1 `0x03120198…` 与 `0x02884651…` → `accepted_limit`（字段不可观测）

- 现象：`amountOut` 两侧**完全一致**（分别为 `88` 与 `175`，`relativeDelta=0.0`），`executable` 两侧也一致；唯一越界字段是 `sqrtPriceX96` 与 `priceImpact`，链侧均为 `null`。
- 根因：部署的 V4 Quoter ABI 为
  `quoteExactInputSingle(((address,address,uint24,int24,address),bool,uint128,bytes))`，
  其返回值**只有两个 ABI word**：`amountOut` 与 `gasEstimate`。该 ABI **从不返回** `sqrtPriceX96`，也**从不返回** `priceImpact`。
- 结论：这是**可观测性边界**，不是算术缺陷。已新增 `field_observability` 归因分支（wire name `field_observability`），且分支刻意收窄——要求 `amountOut` 一致、`executable` 一致，且**所有**越界字段都属于 `["sqrtPriceX96","priceImpact"]`；任一可比较字段越界仍落入 `unexplained`。
- 处置：`accepted_limit`。未放宽全局容差（`DEFAULT_SHADOW_TOLERANCE=1e-9` 保持不变）。

### 2.2 `0x98f19386…` → `explained`（链侧真实拒绝，非本地缺陷）

- 现象：链侧 Quoter 以自定义错误 `0x6190b2b` 回退，参数中带该 pool id；记忆侧当时给出 `amountOut=13996002308547573`、`executable=true`。
- 独立复核：对部署 Quoter 直接发起 `eth_call`，在 **6 个不同区块**（固定块 `113621544`、`+1`、`+10`、`+100`、`+1000`、以及当前链头 `113627047`）上重复该 calldata。
  - 复核脚本：`scripts/tools/p0_1_quoter_revert_probe.py`（只读，不发交易、不写库）；证据：`logs/plan/runtime_evidence/diagnostics/p0_1_98f19386_quoter_revert_probe_20261114.json`。
  - `zeroForOne=true`：**每个区块都回退**，回退数据逐字节一致。
  - `zeroForOne=false`：**每个区块都成功**，返回 `amountOut=70775587840025`、`gas=42459`。
- 结论：该回退是**链侧对同一 swap 的稳定判决**，不是竞态、不是块漂移，也不是本地实现缺陷。`0x6190b2b` 是 PoolManager 侧携带 pool id 的自定义错误。
- 关键修正：修复传输层根因后，记忆侧使用**真实 live 状态**（`sqrtPriceX96=297336929907128902727188198433`，`tick=26452`）时，记忆侧与链侧**一致地**判定该 swap 不可执行（`not_executable`）。该条从 unexplained 收敛为 `explained`。

### 2.3 `0x84bd4e2d…` → `fixed`（状态不一致，已修复）

- 现象：`relativeDelta=0.8524283833413571`，记忆侧 `amountOut=136792316613480151805`，链侧 `20186663309132206507`。
- 根因：见第 3 节——记忆侧在批量 RPC 部分失败时**静默回退到数据库陈旧快照**，却仍被打上 `block:{N}` 版本戳，报告因此声称「两侧同块」，实际两侧描述的是**不同状态**。
- 处置：`fixed`。根因修复后重跑，该池归因为 `tick_boundary`（`relativeDelta=5.19e-05`），落在容差边界内。

---

## 3. 发现的传输层根因（本次新发现并修复）

上一轮把「区块钉住」当作状态一致性的充分条件。实测证明它并不充分——**批量 RPC 本身会部分失败**，而失败被静默吞掉。

### 3.1 根因一：`extsload` 批量突破节点 15 req/s 上限

- 实测：向部署节点发送 24 个 `eth_call` 的 JSON-RPC 批量（12 池 × `slot0`/`liquidity`），节点对其中 **9 个**返回
  `-32007 "15/second request limit reached"` / `-32013 "account limited to 15/sec"`。
- 代码原因：`POOL_STATE_BATCH_SIZE = 50`，24 个请求被作为**一次突发**发出；`throttle_rpc_call` 是空实现；批次之间的等待只发生在**批次之间**，而 24 < 50，因此只有一次突发、零等待。注释声称遵守 8 req/s，实际并未遵守。
- 修复：`POOL_STATE_BATCH_SIZE` 由 50 降为 **8**（由速率上限派生），并把批次间等待改为覆盖**整个批次**的请求数：
  `wait_ms = 1000 × chunk_len / POOL_STATE_REQUESTS_PER_SECOND`。

### 3.2 根因二：读取失败后静默回退到陈旧快照

- 失败时 `V4PoolState.sqrt_price_x96` 为空字符串，`parity_state.rs` 的 `fresh.sqrt_price_x96.parse::<u128>()` 解析失败，于是**保留数据库里陈旧的 `sqrt_price_x96`**，同时又用 live 的 `tick`/`liquidity` 覆盖旧值。
- 结果是一个**混合态**：陈旧价格 + live tick/liquidity。它既不描述存储块，也不描述 live 块。
- 修复：新增 `V4PoolState.complete` 字段；只有当 `slot0` **与** `liquidity` 两次读取**都**成功且 `sqrtPriceX96` 非零时，该池才是完整测量。`read_live_pool_parity_states` 仅在 `complete` 时整体替换存储态，**绝不半替换**，并保留存储版本戳以便两侧能看出不同块。

### 3.3 根因三：失败池仍被标注为同块，制造「虚假一致性」

- 原实现对批次内**每一个**池都写入 `liquidity_read_at = block:{N}`，包括其读取已失败的池。报告因此声称内存侧与链侧同块，而实际不同。
- 修复：`liquidity_read_at` 只在完整 live 测量上写入块号；失败池保留其存储版本戳。
- 同时收紧 `pool_state_versions_agree`：**时间戳与块号属于不同命名空间，不再视为一致**。此前 `timestamp vs block` 被当作「两个视角的 when，不矛盾」，恰好掩盖了本次的真实故障；现在该组合明确判为**版本不一致**，归因为 `pool_state_version`。

### 3.4 证据：修复前后对照

- 修复前（2026-11-13T20-09-31）：12 个池中 **3 个**（`0x4ad1c182c`、`0x84bd4e2d`、`0x98f19386`）使用陈旧快照，其中 `0x98f19386` 产出 1 条 unexplained。
- 修复后（2026-11-13T20-54-22）：**24 条比较全部**携带 `block:113647275`，`unexplainedCount=0`；`0x98f19386` 的记忆侧与链侧一致判定 `not_executable`。

---

## 4. `tick_range_gate` 定性：版本 1 的 fail-closed 语义，不是精度差距

- 修复后共 **6 条** `tick_range_gate`（`0x01f82f36`、`0x086a3368`、`0x4ad1c182`、`0x77c25b93`、`0x84bd4e2d`、`0x98f19386`，均为 `zeroForOne=true`）。
- 记忆侧原因串属于三类：
  - `the price limit is not below the current price`
  - `the hop leaves the current tick range (max input … raw)`
  - `pool state is not usable`
- 结论：版本 1 约束每个 hop **必须落在单个 tick 区间内**（`single_tick`），无法证明落在区间内时**主动失败关闭**，而不是给出跨 tick 的近似价格。链侧则允许跨 tick 成交，因此链侧 `executable=true` 而记忆侧 `executable=false`。
- 这是**冻结的 v1 语义边界**，不是算术缺陷，也**不应**并入 unexplained。跨 tick 能力属于独立接口（tick traversal），由 P0-7 冻结范围中 `crossTickAllowedInCandidates=false / InReview=false / InBroadcast=false` 明确排除。
- 处置：`accepted_limit`。不修改单 tick 算术，不放宽容差。

---

## 5. `tick_boundary` 定性：舍入级差异，全局容差未放宽

- 修复后共 **11 条** `tick_boundary`。实测相对差全部远低于 0.05 上限：

  | pool | zeroForOne | relativeDelta |
  | --- | --- | --- |
  | `0x01f82f36` | false | `0.0005020658776276152` |
  | `0x02a0ae73` | true | `0.0009999004641217904` |
  | `0x02a0ae73` | false | `0.0009999999984749708` |
  | `0x032ac698` | true | `0.0009999004651771136` |
  | `0x032ac698` | false | `0.001000000042699913` |
  | `0x086a3368` | false | `0.00000002492738030931389` |
  | `0x0a4b6ca2` | true | `0.0009993533688756055` |
  | `0x0a4b6ca2` | false | `0.000999862543682032` |
  | `0x84bd4e2d` | true | `0.00005186088138851605` |
  | `0x936daaa6` | true | `0.00050050150365696` |
  | `0x936daaa6` | false | `0.0005005015016099702` |

- 最大值约 `0.0010`，即 **0.1%**，属于跨 tick 边界处多一个有效 tick 带来的量级差异，与单 tick 舍入一致。
- 处置：单列结论，**全局容差不放宽**（`DEFAULT_SHADOW_TOLERANCE=1e-9` 保持不变）。

---

## 6. 台账与计数一致性

- 台账粒度修正为**一次 swap 一次缺陷**：身份键由 `pool_id` 改为 `pool_id::{zfo|!zfo}`，方向不再丢失；一条比较的多个越界字段归入同一条目并由 `fields` 列出。
- `status` 跟随归因：有具名归因即 `explained` 并写入 `landedFix` / `regressionResult`；仅 `ShadowAttribution::Unexplained` 才是 `unexplained`。
- `unexplainedCount` 改为直接取 `ledger.unexplained_count()`，不再二次遍历比较结果。
- 修复后：**台账 6 条，`status=explained` 6 条，`unexplainedCount=0`**，两者严格相等。
- `executableMismatchCount=6` 与 `tick_range_gate` 条目数 **6** 严格相等，全部 explained。

---

## 7. 可信度与安全阀保持

- `untrustedChainCount=0`。
- RPC 不可信判定仍然**只**由 `EthCallTrustReason::{RpcTimeout, MalformedResponse, ChainStateChanged}` 触发；链侧 revert 属于**节点正确作答**，判为 `trusted` 且**从不阻塞**。
- `memoryDeviationBlockCount=0` 已实测确认：记忆侧偏差从不产生 untrusted、从不阻塞执行。

---

## 8. 范围变化说明：84 → 24

- 基线人口为 42 个池 × 2 方向 = 84 条比较，其中 **30 个池带非零 hooks**。
- v1 算术不执行 hook 回调；把规范 V4 算术与带 hook 的链上报价相比，测的是 **hook 行为**，不是算术缺陷。
- P0-7 已冻结范围：`pool_manager/src/scope.rs` 将非零 hook 池标为 `non_zero_hook`（阻断）。
- 因此影子人口在**发起任何 live-state RPC 之前**即按存储行（fee / spacing / hooks）排除，合法人口为 **12 个零钩子池 × 2 方向 = 24 条比较**，被排除的池**不消耗任何 RPC**。
- 这是**范围收敛**，不是覆盖度回退；被排除的 30 个池按 P0-7 契约属于 `blocked` 范围。

---

## 9. 延迟与广播成功率

- 延迟：修复后 24 条比较整轮耗时约 **52 秒**（2026-11-13T20:54:22Z 报告，链上批次 3 批 × 8 请求，按 8 req/s 节流）。基线 84 条比较需 2 次 RPC/hop、且失败后重跑，耗时约 160–180 秒。**同口径比较（24 条）下延迟未劣化**：本轮从旧实现 61.8 秒降至约 52 秒，主因是省去了失败批次的重试。
- 广播成功率：本任务**未广播**，因此不存在可回归的广播成功率基线。已核实生产库
  `cow_execution_attempts`、`manager_v4_review_broadcast_requests`、`manager_broadcast_library_state`、
  `manager_cow_execution_attempts`、`manager_broadcast_attempt_diagnostics` 五张表**均为 0 行**——
  该系统从未产生过生产广播，广播权威仍属 legacy 且 `safeToAutoBroadcast=false`
  （`mainnet_arbitrage_healer_check_20261112T115237Z.json`）。
  因此「广播成功率未劣化」在本阶段的正确表述是：**广播路径未被本任务触及，成功率口径无变化**。

---

## 10. 本次代码改动

| 文件 | 改动 |
| --- | --- |
| `flashloan/tools/pool_manager/src/chain.rs` | `POOL_STATE_BATCH_SIZE` 50→8；批次间等待覆盖整个批次；`V4PoolState.complete`；零价格视为读取失败 |
| `flashloan/tools/pool_manager/src/parity_state.rs` | 仅在 live 测量完整时整体替换存储态；绝不半替换；失败池保留存储版本戳 |
| `flashloan/tools/review_manager/src/shadow.rs` | `pool_state_versions_agree` 将 timestamp vs block 判为不一致；新增两项回归测试 |
| `flashloan/tools/pool_manager/src/{depth,tick_swap,lib}.rs` | 跟随 `V4PoolState` 新字段的测试构造 |

此前阶段（同一 P0-1）已落地的改动保持不变：方向感知台账身份、台账粒度与状态语义、`field_observability` 归因、`unexplainedCount` 取自台账、live state 与链侧报价同块钉住、非零 hook 池排除。

---

## 11. 回归测试

```
cargo test --locked -p pool-manager -p review-manager
  pool-manager:   105 passed, 0 failed
  review-manager: 101 passed, 0 failed
```

新增回归用例：

- `one_batch_fits_inside_the_nodes_per_second_budget`：批量大小必须落在节点每秒预算内。
- `a_pool_with_one_failed_read_is_not_a_complete_measurement`：半读状态不得被视为完整测量。
- `an_incomplete_live_read_keeps_the_stored_measurement_whole`：不完整 live 读保留整份存储测量。
- `a_stored_snapshot_against_a_live_block_is_a_state_version_difference`：存储快照 vs live 块必须判为版本不一致。
- `two_block_versions_agree_only_when_they_are_the_same_block`：两个块号仅在同块时一致。
