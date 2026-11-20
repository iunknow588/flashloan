# P0-6｜首版逐跳 single-tick 最终门禁（2026-11-14）

- 计划版本：v9.8
- 阶段：P0-6（首版逐跳 single-tick 最终门禁）
- 模式：只读证据（`mode=read_only`，`productionWrite=false`）
- 生产库写入：0；权限切换：0；主网广播：0
- 证据报告：`logs/plan/runtime_evidence/reports/p0_6_single_tick_final_gate_20261114.json`
- 证据 SHA-256：`45d0fefdd61718d8974e7656a73b104732590ce51b9f8339dfa8bdeab818eeee`
- 代码：`flashloan/tools/review_manager/src/single_tick.rs`（新）、`flashloan/tools/review_manager/src/lib.rs`（接线）

---

## 1. 结论

**P0-6 通过。** 复核阶段现在**自己**逐 hop 做 single-tick 精确复核，跨 tick / 状态漂移 / 无法证明的路线在 `review_manager` 内被 fail-closed 拦下，**不会**生成广播请求。

| 验收项 | 结果 |
| --- | --- |
| 跨 tick 样例在复核阶段被阻断 | ✅ `not_executable`，`broadcastRequestCount=0` |
| 全部 single-tick 样例保持原有收益/gas/fee 口径 | ✅ `completed`，收益、`gasEstimate`、fee 均未被改写 |
| 广播管理器没有收到不可执行路线 | ✅ 广播请求表为空 |
| 覆盖 YC、Y-Z、ZC 及闭环内全部 hop | ✅ 每个 hop 都有独立 verdict，中间 hop（Y-Z）不是「不验证」 |

---

## 2. 修复前的缺口（为什么这是缺陷而不是重复劳动）

复核之前，`review_manager` 的最终复核阶段有三处**结构性**缺口：

### 2.1 复核只信任上游证据，自己不做 single-tick 判定

`review_manager/src/lib.rs::build_review_rows` 的判定链只有：

```
runArbitrage.ok && runArbitrage.executable  ->  "completed"  ->  写广播请求
```

也就是说，**只要上游 Python 证据说 ok/executable，review_manager 就直接放行**。复核阶段没有自己的执行性判断，候选排序用的 single-tick 精确输出成了唯一的门槛，而它**只是筛选**，不是复核。

这直接违反 P0-7 冻结契约：

> 每个 hop 都必须满足 single-tick：按该 hop 的**实际最终输入量**，在 initialized tick/liquidity 状态下计算，最终输出不得跨越下一 tick。

### 2.2 模拟器用的是**可跨 tick** 的报价，不是 single-tick 报价

`parity.rs::simulate_hop` 走的是 `pool_manager::exact_input_swap_amount_out`——这是链上的完整 exact-input 语义，**允许跨 tick**。而版本 1 的约束是「每 hop 必须 single-tick」。两者不是同一个口径：

- `exact_input_swap_amount_out` 给出的是「若允许穿越 tick，链会给多少」；
- `single_tick_swap_amount_out` 给出的是「若价格停在当前 tick 区间内，链会给多少」，并且**明确报告是否越界**。

前者无法回答「有没有跨 tick」，所以它不能作为版本 1 的门禁。

### 2.3 状态里没有 tick，证据里没有 bound 结论

`parity.rs::HopState` 只有 `sqrt_price_x96 / liquidity / fee_pips / zero_for_one`，**没有 `tick`、没有 `tick_spacing`**；`HopResult` 只有 `amount_in / amount_out / price_impact / ok / reason`，**没有 initialized tick、没有下一 tick 距离、没有 bound 结论**。

因此复核阶段在结构上就**不可能**回答：

- 这一 hop 从哪个 initialized tick 出发；
- 距离下一 tick 还有多远；
- 结论是「在区间内」「跨 tick」还是「无法证明」。

---

## 3. 修复方案

### 3.1 新模块 `review_manager::single_tick`

新增 `flashloan/tools/review_manager/src/single_tick.rs`，对外暴露：

| 类型 | 作用 |
| --- | --- |
| `SingleTickHopState` | 一个 hop 的 tick / tick_spacing / pool_state_version |
| `BoundConclusion` | `inside_tick_range` / `crosses_next_tick` / `unprovable` |
| `SingleTickHopVerdict` | 单 hop 的 initialized tick、next tick、next tick 距离、实际输出、bound、headroom |
| `SingleTickRouteVerdict` | 整条路线的可执行性与逐 hop verdict |

核心定价使用 Pool Manager 已发布的 single-tick 算术 `pool_manager::depth::single_tick_swap_amount_out`，因此门禁与候选排序**不可能**出现两套舍入规则。

**实际最终输入量的传递**：第一 hop 用借入本金，**第 n+1 hop 用第 n hop 的实际输出**。所以门禁定价的输入量就是链上真正会流过的量，而不是把本金套用到每一跳。

### 3.2 接线到最终复核阶段

`review_manager/src/lib.rs`：

- `read_review_input` 通过 **Pool Manager 已发布的 API** `read_pool_parity_states` 读取 route 全部 pool 的实测状态（含 `tick` / `tick_spacing` / `liquidity_read_at`），存入 `ReviewInput.pool_states`；
- `build_review_rows` 在形成 verdict **之前**执行门禁：
  - 门禁通过 → 维持 `completed`，收益 / `gasEstimate` / fee 口径**不变**；
  - 门禁不通过 → `review_status = "not_executable"`，`review_reason = "single_tick_gate_failed:<原因>"`，**不写广播请求**；
- 失败时写一条 `diagnostic_code = "SingleTickGateFailed"` 的诊断，`payload.hops` 携带**全部 hop** 的 verdict（initialized tick、实际输出、下一 tick 距离、bound 结论）。

### 3.3 fail-closed 清单

| 情形 | 门禁结论 | 复核状态 |
| --- | --- | --- |
| 实际输入量跨越下一 tick | `crosses_next_tick` | `not_executable` |
| hop 的 `tick_spacing` 缺失/不可用 | `unprovable` | `not_executable` |
| tick 区间无法从状态推导 | `unprovable` | `not_executable` |
| pool state 版本在 hop 之间漂移 | — | `not_executable`（`pool_state_version_drift`） |
| pool state 读不到（表缺失/快照缺失） | — | `not_executable`（`route_state_unavailable`） |
| 复核证据没有实际最终输入量 | — | `not_executable`（`final_input_amount_unavailable`） |

关键点：**测量缺口按 fail-closed 处理，而不是按 fail-open 处理**。读不到状态不等于状态安全，所以路线被拦下；同时读状态失败**不会**让整批复核崩溃——只有被影响的那条路线被拦下，其余路线照常出报告。

---

## 4. 覆盖性：不是只验首尾 hop

门禁按链上顺序遍历**每一个** hop，并给每个 hop 都留下 verdict（即使前一个 hop 已经失败，后面的 hop 仍会被判定，只是输入量记为 0）。因此：

- **YC**（锚点→Y→锚点）单测通过；
- **Y-Z**（锚点→Y→Z）单测通过；
- **ZC**（锚点→Z→锚点）单测通过；
- **闭环**（三跳）单测通过；
- **中间 hop 单独跨界**的样例被定位到 `hop_2_failed`——证明中间 hop 确实被验证，而不是被跳过。

对应证据行见 `p0_6_single_tick_final_gate_20261114.json` 的 `rows`：

| 样例 | 可执行 | 结论序列 |
| --- | --- | --- |
| `yc_single_tick` | ✅ | `inside_tick_range × 2` |
| `yz_single_tick` | ✅ | `inside_tick_range × 2` |
| `zc_single_tick` | ✅ | `inside_tick_range × 2` |
| `closed_loop_single_tick` | ✅ | `inside_tick_range × 3` |
| `yc_cross_tick` | ❌ | `crosses_next_tick, unprovable` |
| `yz_cross_tick` | ❌ | `crosses_next_tick, unprovable` |
| `zc_cross_tick` | ❌ | `crosses_next_tick, unprovable` |
| `closed_loop_cross_tick` | ❌ | `crosses_next_tick, unprovable, unprovable` |
| `closed_loop_middle_hop_cross` | ❌ | `inside_tick_range, crosses_next_tick, unprovable` |
| `closed_loop_state_drift` | ❌ | 三跳均在区间内，但 `pool_state_version_drift` |
| `closed_loop_unprovable` | ❌ | `inside_tick_range, unprovable, unprovable` |

---

## 5. 回归测试

### 5.1 Rust（`review_manager`）

新增单模块测试（`review_manager::single_tick`）：

- `a_single_tick_route_passes_with_a_bound_conclusion_per_hop`
- `every_hop_is_priced_with_its_own_actual_input_amount`
- `a_cross_tick_route_is_blocked`
- `the_gate_covers_every_hop_of_the_closed_loop`
- `a_hop_without_a_tick_spacing_is_blocked`
- `a_drifted_pool_state_version_blocks_the_route`
- `a_mismatched_hop_count_blocks_the_route`
- `a_middle_hop_that_crosses_its_boundary_blocks_the_route`

新增复核阶段集成测试（`review_manager::tests`）：

- `a_cross_tick_route_is_blocked_at_the_review_stage`
- `a_single_tick_route_keeps_its_profit_gas_and_fee_semantics`
- `a_route_without_a_usable_pool_state_is_blocked`
- `a_drifted_pool_state_version_blocks_the_route_at_the_review_stage`
- `a_route_without_a_final_input_amount_is_blocked`

### 5.2 全量回归

| 命令 | 结果 |
| --- | --- |
| `cargo test --locked -p review-manager` | 114 passed / 0 failed |
| `cargo test --locked -p pool-manager -p review-manager -p candidate-edge-pool-manager -p manager-api` | 105 + 114 + 14 + 23 passed / 0 failed |
| `cargo test --locked --workspace` | 全部 passed / 0 failed |
| `cargo test --locked --manifest-path flashloan/rs-src/Cargo.toml runtime_core` | 12 passed / 0 failed |
| `cargo build --release --locked` | 成功，`flashloan_rs.dll` 已重建 |
| `python -m pytest ...`（shadow / arch2b / pool_roles / parity bridge / token_authority） | 72 passed |

---

## 6. 影响与未做的事

### 6.1 行为变化（重要）

复核阶段现在会**主动拦下**此前会被放行的路线。具体影响：

- 上游 `runArbitrage` 说 `ok && executable`，但路线跨 tick / 状态漂移 / 缺状态 / 缺实际输入量 → 现在是 `not_executable`，且**不写广播请求**。
- 这是 P0-7 契约要求的 fail-closed 行为，**不是回归**。

### 6.2 未做（需要后续阶段）

- **本阶段不写生产库**：所有改动都是代码 + 单元测试 + 副本 dry-run；`productionWrite=false`。
- **本阶段不发广播**：`broadcasts=0`、`authoritySwitches=0`。
- **不新增表结构**：门禁复用 Pool Manager 已有的 `v4_exchange_pools` / `v4_exchange_pool_liquidity_snapshots`，不引入新 authority。
- **候选排序阶段仍是筛选**：`candidate_edge_pool_manager` 的 single-tick 精确输出只做筛选，本阶段没有改变它的角色（符合「筛选不替代复核」）。
- **跨 tick 路径保留计算接口与诊断输出**：门禁不删除 `tick_swap` 的遍历能力，只是把它排除在候选、复核和广播之外（P0-7 要求）。

### 6.3 后续观察点

- 生产库中 `v4_exchange_pool_liquidity_snapshots` 的 `tick` / `sqrt_price_x96` / `liquidity` 覆盖率决定门禁的有效样本比例；覆盖率不足时路线会被 fail-closed 拦下，这是契约要求，但需要在 W6/W7 铺开前确认覆盖率。
- 门禁目前使用**存储的** pool state（`read_pool_parity_states`），不是 live state。若复核与执行之间的状态漂移窗口需要收紧，可在 P2–P4 阶段接入 `read_live_pool_parity_states`（同一 API 已存在）。

---

## 7. 复现方式

```powershell
# 门禁单测
cargo test --locked -p review-manager single_tick

# 复核阶段集成测试
cargo test --locked -p review-manager tests::a_cross_tick_route_is_blocked_at_the_review_stage

# 证据探针（只读，输出 rows）
cargo run --locked --example p0_6_single_tick_gate_probe -p review-manager
```

证据探针源码：`flashloan/tools/review_manager/examples/p0_6_single_tick_gate_probe.rs`。
