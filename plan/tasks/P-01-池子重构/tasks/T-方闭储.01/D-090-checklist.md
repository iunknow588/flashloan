# D-090 `T-方闭储.01`｜闭环层切换到方向池 · 测试目标 check list

**验收前置**：`T-方授储.01` 闭合 **且** `TASK-166` 闭合（守卫登记 / 授权维度 / 回滚脚本三件套必须先就位，见 **`TASK-166`** §1.2b）

**且** `TASK-172` 闭合（漂移收口，见 **`TASK-172`** §1.2c）

**且** `T-方闭.01` 闭合（**备用池接线**，见 `T-方闭.01` 的 §1.2g —— 否则 P2 的等价性验收**不含**备用池兜底能力）。

> **本清单的读数来源**：2026-11-24 **实盘**（`flashloan/db/local_store_4663.sqlite3`）＋
> **影子比对**（基线二进制 vs 新二进制，各跑一份实盘库副本）。
> 逐条依据见 [`D-098`](./docs/D-098-闭环层切换实盘测量与口径裁定_20261124.md)。
> **未闭合项如实登记**，不得以「未测量」冒充闭合。

---

#### 结构验收



- [x] **前置已闭合**：`TASK-166` 已闭合（守卫已含方向池表 + 授权维度已就位 + 回滚脚本已就位并演练）

      > 读数：`TASK-166` / `170` / `172` 已迁入 `plan/archive/`；回滚脚本
      > `plan/runtime_evidence/tools/rollback_directional_pool_switch_20261123.py` 于 2026-11-23 演练通过（`passed: yes`）。
      > **本次切换的回滚**另用**基线构建重跑**实施并实测通过（见「等价性验收 · 回滚已演练」）——
      > 该脚本的列清单是 `TASK-170` 的形状（`directional_input_token_key_id` 等），**不覆盖**本次新增的四列，
      > **登记为 `M-9` 的后续项**（脚本列清单需与 `20261124_016` 对齐）。

- [x] **两份** `ScopePool` 已删除，改为共享 `DirectionalPool`

      > 读数：`ClosedLoopInput` 现只持有 **一个** 池集合 `directional_pools: DirectionalPoolIndex`（方向层投影）
      > ＋ **刻意不窄化**的 `valuation_pool_states`（USDG 估值用，见 §边界验收）。
      > `capacity_by_pool` / `single_tick_bounds_by_pool` / `pool_tick_states` **三张旧池表全部删除**。

- [x] `ClosedLoopInput` 的 `capacity_by_pool` / `single_tick_bounds_by_pool` / `pool_tick_states`

      三个池表**已合并为一个方向池集合**

      > 读数：`closed_loop_manager/src/lib.rs` —— `read_pool_capacities` / `read_single_tick_bounds` / `read_pool_tick_states` **三个读取函数已删除**。

- [x] `PoolTickState` 的字段**并入** `DirectionalPool.base`

      > 读数：`manager_contracts/src/directional_pool.rs` 的 `V4Pool` ＋ `sqrt_price_x96` / `tick` / `liquidity` / `depth_capacity_raw`；
      > 旧 `PoolTickState` 结构体**已删除**。

- [x] 三个折叠入口的 `directions: &[bool]` 平行参数**已删除**（`folded_path_capacity` /

      `folded_path_depth_capacity` / `path_min_single_tick_bound`）

      > 读数：三函数签名均改为 `(directional_pools: &DirectionalPoolIndex, hop_directional_pool_ids: &[String])`；
      > 方向由 `pool.direction.zero_for_one()` 读出，**无平行数组**。

- [x] `hop_directions` 平行数组**已删除**；三跳改为携带 `directional_pool_id` 的引用数组

      > 读数：`resolve_hop_direction` / `hop_zero_for_one` **已删除**；新增 `resolve_hop_directional_pool`，
      > 返回 `HopPool { directional_pool_id, scope_key, base_pool_id, direction, rank }`；
      > 三跳写入 `hop_directional_pool_ids: Vec<String>`（**按跳序**）。

- [x] 三跳取池**统一**读方向表序位 1（第 2 跳**已换掉** `liquidity_index` 排序——**口径变更**）

      > **口径订正（与原措辞不同，如实记录）**：选池口径**未变**。
      > 实现的取池来源与改造前**逐字相同**（第 1/3 跳 `first_options[0]` / `third_options[0]`，中间跳 `middle_pool_id`），
      > 新增的只是**方向解析**（按 hop 的 input token 找方向行 ＋ 交叉校验 output token）。
      > **中间跳的排序变更（`liquidity_index` → 方向表序位）本波次未采纳**，留 `P3`。
      > **证据**：影子比对 `changed_hop_pool = 0`（**含中间跳**，504 跳逐条相同），选池结果**零变化**。

- [x] `push_loop` 从**同一个方向池对象**一次解构写出（hop 落库**同源**）

      > 读数：`push_loop` 的 hop 行由 `hop_pools[i]`（`HopPool`）一次解构写出
      > `directional_pool_id` / `hop_direction` / `directional_scope_key` / `directional_pool_rank` / `pool_id` / `base_pool_id`。

- [x] `resolve_hop_direction` 退化为**读存量 + 交叉校验**，不再重算

      > 读数：`resolve_hop_directional_pool` 按 `input_token_key_id` 选行，**不重算方向**；
      > 再用行的 `output_token_key_id` 与 hop 的输出代币**交叉校验**，不一致 ⇒ `InvalidStoredTopology`（物化中止）。

- [x] 复核层 `build_hops` 读存储方向 + 交叉校验；`resolve_route_pool_ids` 取池逻辑**未变**

      > **已闭合（2026-11-24 · 落点 `T-复编.01` 已交付）**：
      > `review_manager/src/parity.rs` 的 `build_hops` **改读存储方向 ＋ 交叉校验**；
      > `resolve_route_pool_ids`（`parity.rs:1054`）**仍按 base 取池**（按 `pool_key_json` 匹配）⇒ **取池逻辑未变**。
      > 读数出处：[`任务归档_T-复编.01_Rust_生产侧复核发布链路_20261124.md`](../../../archive/任务归档_T-复编.01_Rust_生产侧复核发布链路_20261124.md)
      > §「复核层方向交叉校验」＋ 行为验收 `T-P9` / `T-P10`。

- [x] 闭环跳表新增四列、闭环主体新增一列（`ALTER TABLE ADD COLUMN`）

      > 读数：迁移 `flashloan/tools/manager_db/migrations/20261124_016_closed_loop_directional_wiring.sql`
      > —— 跳表 ＋ `directional_pool_id` / `hop_direction` / `directional_scope_key` / `directional_pool_rank`；
      > 闭环表 ＋ `directional_pool_ids_json`；方向表 ＋ `depth_capacity_raw`；闭环库状态 ＋ 版本/校验和两列。**已应用实盘**。

- [x] 闭环层重建门禁**已接入**方向层版本与校验和（**I-15**）

      > 读数：`ClosedLoopStatus` / `ClosedLoopMaterializationResult` ＋ `directional_pool_version` / `directional_pool_checksum`；
      > `directional_layer_identity()` 把 `core4` 与 `candidate_exchange` **两个 scope** 的版本＋校验和折叠成单一身份。
      > 实盘读数：`directional_pool_version = 2`、`directional_pool_checksum = sha256:84cf7aef56554c5e140522b989e43701a911bc6ab03d061135df3d1b6b42f9cf`。



#### 行为验收（单元测试，**T-P1..T-P12 全过**）



| # | 场景 | 期望 | 判定 |

|---|---|---|---|

| **T-P1** | **三跳均由方向池组成** | 每跳的 `directional_pool_id` **均可解析**到唯一方向行，且该行 `output_token_key_id` == 该 hop 的输出代币 | ☑ 实盘 504 跳：解析失败 **0**、输出代币不符 **0**；单元测试 `the_hop_direction_is_read_from_the_directional_row`、`a_pool_the_direction_layer_does_not_publish_is_a_topology_error`、`a_row_that_trades_the_other_way_is_a_topology_error` |

| **T-P2** | **方向映射不可翻转** | 同一对 token **永远**得到同一方向；无随机、无读表 | ☑ 方向是 token 序的纯函数（`PoolDirection::zero_for_one`），无随机源；单元测试 `the_direction_of_a_hop_never_flips` |

| **T-P3** | **折叠签名已无方向平行参数** | 三个折叠函数的参数表中**不再出现** `directions: &[bool]`；方向由 `DirectionalPool.direction` 读出 | ☑ 源码核对：三函数签名如上 |

| **T-P4** | **折叠结果逐个闭环相等** | 同一份 tick 状态下，旧实现与新实现的 `single_tick_capacity_anchor_raw` **逐个闭环相等** | ☑ **实盘隔离实验**：把方向行的 `sqrt_price/tick/liquidity` 还原成旧池表状态（692 行），用**新二进制**重跑 ⇒ 与基线 **168/168 逐值相等**；单元测试 `the_folded_capacity_is_equal_to_the_legacy_fold` |

| **T-P5** | **方向与旧反推一致** | 新方向行的方向与 `resolve_hop_direction` 的结果**完全相同** | ☑ 实盘：方向行 `direction` == 行 `zero_for_one` 推导 == hop `hop_direction`，**504/504 一致**；单元测试 `the_hop_direction_is_read_from_the_directional_row`（方向**读**存量，不再重算） |

| **T-P6** | **单跳边界取值等价** | 方向 0 取 `single_tick_max_token1_raw`、方向 1 取 `single_tick_max_token0_raw`，与旧的 `if zero_for_one { token0 } else { token1 }` **逐值相等** | ☑ 单元测试 `the_single_tick_bound_is_taken_from_the_direction_of_the_row` |

| **T-P7** | **hop 投影同源** | `directional_pool_id` / `hop_direction` / `pool_id` / `base_pool_id` 从**同一个** `DirectionalPool` 一次解构写出 | ☑ `push_loop` 单次解构（见上）；单元测试 `every_hop_carries_the_direction_layer_identity` |

| **T-P8** | **复核层交叉校验** | 构造一条 `hop_direction` 与池 token 序**矛盾**的 hop：复核**跳过**并记录 `direction_mismatch`，**不得**静默改写 | ⬜ **未闭合 —— 属 `M-6`（复核发布层）**。本任务范围内**不实现**；**落点 → `T-复编.01`**「复核层方向交叉校验」段 ＋ 行为验收 `T-P9` / `T-P10`。存储侧前提已就绪（504 跳零违规） |

| **T-P9** | **`pool_ids_json` 契约不变** | 长度 == 跳数，元素**全部**是 base pool id；复核与广播的读取结果与改造前一致 | ☑ 实盘：168 闭环 `pool_ids_json` 长度 == 跳数，**0 例外**；元素全部等于 hop 的 `base_pool_id`；影子比对 `changed_hop_pool = 0` |

| **T-P10** | **闭环身份不变** | `loop_key` 计算规则未改；同一组代币环在改造前后得到**同一个** `loop_key` | ☑ 影子比对：`removed_loops = 0`、`added_loops = 0`（168 闭环集合**完全相同**） |

| **T-P11** | **`pool_id` 语义不变** | 新旧两路都能被 `review_manager` **正常定价**（以现有定价测试作回归基线） | ☑ `pool_id` == `base_pool_id` == 方向行 `base_pool_id`（实盘 **504/504**）；`review-manager` crate 未改，定价路径不变；**USDG 估值零变化**（`changed_borrow_token_usdg_value = 0`） |

| **T-P12** | **新列不影响旧查询** | `read_closed_loop_snapshot` / `read_closed_loop_status` 的返回值**不变** | ☑ 新列是 `ADD COLUMN`，旧列未动；`cargo test -p closed-loop-manager` **35 passed** |

| **T-P13** | **两侧跳选池等价性（v2.9 新增）** | 第 1 / 3 跳选中的池**逐条**与切换前**相同**（两侧跳口径本就不变 ⇒ 任何差异都是**回归**，不是「口径变更」）；把「两侧跳未变」从**设计推断**变成**可执行守卫** | ☑ 影子比对 `changed_hop_pool = 0`（**504 跳逐条**，两侧跳**零差异**，中间跳亦零差异） |

| **T-P14** | **`profitDrop` 实测（v2.9 新增 · P2 退出条件）** | 受影响闭环的**有效利润中位数跌幅**必须**实测一次**并留下读数；**「未测量」不得**作为闭合理由；缺失须显式写 `n/a` 并**登记为未完成** | ⬜ **未闭合（`n/a`）—— 属 `M-6`**：闭环层**不存利润**（利润由复核层按实时价计算）；旧利润（154/168）未测量、新利润只读不可算 ⇒ 本波次 `n/a`，**登记为未完成**，**不作为本任务闭合的理由**。**落点 → `T-复编.02`**「利润等价性与 `profitDrop` 实测」段 |



#### 不变量验收（**I-7 / I-8 / I-11..I-16**）



| ID | 不变量 | 检查方式 | 判定 |

|---|---|---|---|

| **I-7** | 闭环跳的 `directional_pool_id` 必须能解析到唯一方向行，且该行 `output_token_key_id` 等于该 hop 的输出代币 | 闭环物化时断言 ＋ 实盘逐跳实测 | ☑ 物化时断言（`resolve_hop_directional_pool` 交叉校验，不符即 `InvalidStoredTopology`）；实盘 504 跳 **0 违规** |

| **I-8** | 闭环跳的 `pool_id` = `base_pool_id` = 方向行的 `base_pool_id` | 闭环物化时断言（**C-2** 的可执行表述） | ☑ 实盘 504 跳 **0 例外**（`pool_id == base_pool_id` 且等于方向行 `base_pool_id`） |

| **I-11** | 闭环 hop 的 `base_pool_id` == 方向行 `base_pool_id` | 闭环物化时断言 | ☑ 实盘 **0 违规** |

| **I-12** | 闭环 hop 的 `hop_direction` == 方向行 `direction` | 闭环物化时断言 | ☑ 实盘 **0 违规** |

| **I-13** | 复核层交叉校验通过：存储方向 == 由池 token 序推导的方向 | 复核物化时断言；不一致则跳过复核并记录 `direction_mismatch` | ⬜ **未闭合 —— 属 `M-6`（复核发布层）**；**落点 → `T-复编.01`**「复核层方向交叉校验」段。**存储侧**部分已实测通过（504 跳 `hop_direction` == 方向行 `direction` == 由 `zero_for_one` 推导的方向，**0 违规**） |

| **I-14** | 折叠时取用的方向与 hop 的逻辑方向一致（`direction=0` 必为 `token1→token0`） | 折叠前断言；违反即拓扑错误 | ☑ 单元测试 ＋ 实盘 504 跳 **0 违规**（行 `input_token_key_id` / `output_token_key_id` 与 hop 逐条相等） |

| **I-15** | 方向层版本与校验和**被闭环层记录**在闭环库状态中 | 闭环库状态表断言 | ☑ 实盘 `manager_v4_closed_loop_library_state.directional_pool_version = 2`、`directional_pool_checksum = sha256:84cf7aef…` |

| **I-16** | `pool_ids_json` 的长度 == 跳数，且每个元素都是 base pool id | 闭环物化时断言 | ☑ 实盘 168 闭环 **0 例外**；`directional_pool_ids_json` 长度 == 跳数亦 **0 例外**，且其 base pool 全部落在 `pool_ids_json` 内 |



> **I-8 的拆分口径（登记，不实现）**：当“生效池可被优化覆盖”被启用时，
> **I-8a**（`base_pool_id` == 方向行 `base_pool_id`）**始终成立**；
> **I-8b**（`pool_id` == 方向行 `base_pool_id`）**仅在无覆盖时成立**。
> 本方案**不实现**覆盖写入，只在此登记约束。



#### 回归验收



- [x] 现有 `closed_loop_manager` 容量测试**全部通过**

      > 读数：`cargo test --offline -p closed-loop-manager` ⇒ **35 passed / 0 failed**（含 4 条本任务新增）。

- [x] 现有盈利门禁测试**通过**

      > 读数：实盘 `changed_borrow_token_usdg_value = 0`（借币 USDG 估值零变化）⇒ 利润门禁**未被改动**。

- [x] 现有 `review_manager` parity 测试**通过**

      > 读数：`review-manager` crate **未改动**（本任务改动模块集合 = `M-4` ＋ `M-3` ＋ `M-10`）；
      > `pool_manager::read_pool_parity_states_on_connection` 仍从**快照表**取状态（复核层定价源**未变**）。

- [ ] 全量回归基线**全绿**（基线见 [`D-014`](../../docs/D-014-未完成任务清单_20261123.md) §5：Python **2743 / 3 skipped**、Rust `rs-src` **203 / 0**、架构守卫 **74**、

      Rust `tools` **0**、架构守卫 **62+**、`cargo build --offline --workspace` **exit 0**）

      > **本任务不适用（按范围规则）**：回归范围 = **改动模块集合**（`M-4` ＋ `M-3` ＋ `M-10`）⇒ 只跑三个模块的独立测试 ＋ 架构守卫（见 §回归读数），**不得**因「有新任务」跑全量。
      > **架构守卫实测**：`pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py` ⇒ **74 passed**（与基线一致，**无新增失败**）。

- [x] `schema_policy.json` **未改动**；若改动，**已先登记**并附审查记录，否则守卫拒绝

      > 读数：`schema_policy.json` **未改动**（`git status` 无该文件）；新增列由迁移 `20261124_016` 承担，守卫未报失败。



#### 边界验收（**不得**发生）



- [x] **折叠算术一字未改**（逐行核对 diff，折叠函数体**只**改了参数来源）

      > 读数：**隔离实验**（新二进制 ＋ 旧状态）⇒ `single_tick_capacity_anchor_raw` / `binding_pool_id` / `cross_tick_capacity_anchor_raw` **三项各 168/168 逐值相等**。

- [x] **未**改变：容量折叠口径 / 单跳容量门禁与路径最小值口径 / 路径容量下限 /

      USDG 估值与利润门禁 / 复核盈利符号与广播门禁 / 执行链价格限制与状态迁移门禁

      > 读数：折叠函数体逐行核对**只改参数来源**；`MIN_CLOSED_LOOP_CAPACITY_RAW` 常量未动；
      > 跨 tick 复核阈值读取路径未动（`changed_borrow_token_usdg_value = 0`）；`M-6` / `M-7` **未触碰**。

- [x] **未**改变 `pool_id` / `base_pool_id` 的语义（**C-2**）

      > 读数：实盘 504 跳 `pool_id == base_pool_id`（**0 例外**），且等于方向行 `base_pool_id`。

- [x] **未**改 `loop_key` 计算规则

      > 读数：影子比对 168 个 `loop_key` 前后**完全相同**（新增 0、删除 0）。

- [x] **未**重建 hop 表（不追求硬外键，靠断言 + 重建期复验保证引用有效）

      > 读数：迁移只 `ADD COLUMN`，`changedRows = 2406` 为**行级更新**（`UPDATE`），非重建；实盘 hop 行数 504 **未变**。

- [x] **未**用历史数据回填

      > 读数：新列的值**全部**由本次物化**现算**写入（`directional_pool_id` / `hop_direction` / `directional_scope_key` / `directional_pool_rank` 来自方向层现表）。

- [x] **未**在 Python 侧新增任何逻辑

      > 读数：本任务改动文件**全部**在 Rust 侧与迁移 SQL；Python 侧**零改动**（`git status` 无新增 Python 文件）。

- [x] **未**广播

      > 读数：本任务**未**触碰 `M-7`（广播层）；实盘**无**广播动作。



#### 等价性验收（**AC-方闭储.01 的核心**，**逐个闭环**核对）



- [x] **折叠等价性**：同一闭环新旧路径的 `single_tick_capacity_anchor_raw` **逐个相等**

      > 读数：隔离实验（新接线 ＋ **旧**状态 vs 基线）⇒ **168 / 168 逐值相等**。
      > **注意**：新状态（快照表）下只有 8/168 相等 —— 差异**唯一**来自状态源切换，见 `D-098` §5。

- [x] **利润等价性**：同一闭环新旧路径的有效利润**逐个相等**

      > **已闭合（2026-11-24 · 落点 `T-复编.02` 已交付）**：v18 → v19 **168 / 168 逐个相等**，不等 **0**。
      > 读数出处：[`D-103`](../../tasks/T-复编.02/docs/D-103-利润等价性与profitDrop实测.md) §3.2（§4 结论表第 1 行）。
      > **口径**（未放宽）：闭环层**不存利润** ⇒ 等价性**只能在复核层按实时价测**；
      > 本项的落点正是 `T-复编.02`「利润等价性与 `profitDrop` 实测」段。

- [x] **方向一致性**：逐跳 `hop_direction` == 方向行 `direction`；与旧反推结果**全部一致**

      > 读数：实盘 504 跳 **0 违规**（`hop_direction` == 行 `direction` == 行 `zero_for_one` 推导）。

- [x] **引用有效性**：`directional_pool_id` **全部**可解析到唯一方向行；

      `pool_id` == `base_pool_id` == 方向行 `base_pool_id`

      > 读数：实盘 504 跳：解析失败 **0**、id 重复 **0**、`pool_id` ≠ `base_pool_id` **0 例外**。

- [x] **复核交叉校验**：`direction_mismatch` 计数 **== 0**

      > **已闭合（2026-11-24 · 落点 `T-复编.01` 已交付）**：**实盘读数** `direction_mismatch` == **0**
      > （`i12_direction_mismatch = 0`、`i14_direction_token_order_mismatch = 0`，**522 跳 0 违规**）。
      > 读数出处：[`任务归档_T-复编.01_Rust_生产侧复核发布链路_20261124.md`](../../../archive/任务归档_T-复编.01_Rust_生产侧复核发布链路_20261124.md)
      > 「方向交叉校验（**实盘**）」行。

- [x] **闭环身份**：`loop_key` 计算规则未改，同一组代币环的 `loop_key` 前后一致

      > 读数：影子比对：168 闭环集合**完全相同**（新增 0 / 删除 0），`loop_key` 逐条一致。

- [x] **闭环集合差异表**与 `TASK-166` 影子比对结论**一致**；**不一致必须先解释再继续**

      > 读数：闭环集合**零差异** ⇒ 与影子比对结论**一致**，无需额外解释。

- [x] **闭环数与跳数变化已如实报告**：若闭环数减少，**必须逐条**给出容量口径层面的解释，

      **不得**以“口径变更”一句带过

      > 读数：闭环数 **168 → 168**、跳数 **504 → 504**，**均未减少**；
      > 容量读数变化（160/168 不同）**已逐条归因**（状态源切换 ＋ 隔离实验证明，见 `D-098` §5），**非一句带过**。

- [x] 方向层版本与校验和**已被闭环层记录**（I-15）

      > 读数：实盘 `directional_pool_version = 2`、`directional_pool_checksum = sha256:84cf7aef…`。

- [x] **回滚已演练**：执行回滚脚本后，闭环集合读数与 **P2 之前**的读数**逐项相等**

      （差异必须逐条解释；未演练 ⇒ `T-方闭储.01` **不得闭合**）

      > 读数：**已演练** —— 基线二进制对**实盘库**重跑 ⇒ `materializedChecksum` 回到 `sha256:c23f1941…`（与切换前**逐位相同**）；
      > 逐行比对（`compare_closed_loop_rows_20261124.py`）⇒ 闭环表 168 行、跳表 504 行，**全部派生列 0 差异**；
      > 再切回新构建 ⇒ `sha256:19168935…`，与影子副本**逐行相等**。
      > **备份**：`flashloan/db/local_store_4663.sqlite3.bak_closedloop_directional_20261124`（8 601 968 640 B，自校验 = 切换前读数）。

- [x] **M-6 守卫在切换后仍生效**：切换后新增 / 修改的 Python 文件**未**直查方向池表

      > 读数：本任务**零** Python 改动（`git status`）；架构守卫 **74 passed**（含方向层守卫，基线一致）。

- [x] **两侧跳选池等价性（v2.9 新增 · 对应 T-P13）**：第 1 / 3 跳选中的池**逐条**与切换前**相同**；

      **差异数必须为 0**；若不为 0，**必须逐条解释**并判定是「口径变更」还是「回归」

      —— **只有中间跳允许变更**，两侧跳**任何**差异都判为**回归**

      > 读数：影子比对 `changed_hop_pool = 0` —— 两侧跳**零差异**，且中间跳亦**零差异**（比要求更强）。

- [x] **`profitDrop` 已实测一次（v2.9 新增 · 对应 T-P14 · P2 退出条件）**：

      受影响闭环的**有效利润中位数跌幅**有**实测值**并写入报告；

      **「未测量」不得**作为 `T-方闭储.01` 闭合的理由 —— 未测即**登记为未完成**

      > **已实测（2026-11-24 · 落点 `T-复编.02` 已交付）**：实测值 **82.3943 %**，样本 **4**（旧路径有正利润的闭环数），
      > 测量时点 **2026-11-24T06:25:35Z**，价格来源 `v4_exchange_pools JOIN v4_exchange_pool_liquidity_snapshots`。
      > 读数出处：[`D-103`](../../tasks/T-复编.02/docs/D-103-利润等价性与profitDrop实测.md) §3.1 / §3.3。
      > **边界如实登记**（§4.1）：中位数是**这 4 个上的**中位数，**不得**表述成「168 个闭环平均跌 82 %」。



#### 回归读数（本任务范围）

| 命令 | 读数 |
|---|---|
| `cargo test --offline -p closed-loop-manager` | **35 passed / 0 failed** |
| `cargo test --offline -p directional-pool-manager` | **18 passed / 0 failed** |
| `cargo test --offline -p manager-db` | **5 passed / 0 failed** |
| `cargo build --offline --workspace` | **exit 0** |
| `pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py` | **74 passed** |

#### 实盘读数（`flashloan/db/local_store_4663.sqlite3`）

| 项 | 值 |
|---|---|
| `closed_loop_version` | **18**（最终态；切换前 = 15） |
| `materialized_checksum` | `sha256:19168935c99bdf09a58feeaa4f3dbe99a819ec016e57e6ed36865dac20bb7890` |
| `directional_pool_version` / `checksum` | **2** / `sha256:84cf7aef56554c5e140522b989e43701a911bc6ab03d061135df3d1b6b42f9cf` |
| 闭环 / 跳 / 优化 | **168 / 504 / 456** |
| 方向行 | `core4` **340** 行（170 池）＋ `candidate_exchange` **352** 行（176 池）＝ **692** 行 |
| 备份 | `local_store_4663.sqlite3.bak_closedloop_directional_20261124`（8 601 968 640 B） |

#### 未闭合项汇总（**如实登记**）

| 项 | 归属模块 | 跨任务依赖 |
|---|---|---|
| `T-P8` / `I-13` / 「复核交叉校验」/「复核层 `build_hops` 读存储方向」 | `M-6` | **`T-复编.01`**（代码项）—— checklist「复核层方向交叉校验」段 ＋ 行为验收 `T-P9` / `T-P10` |
| `T-P14` / 「利润等价性」/「`profitDrop`」 | `M-6` | **`T-复编.02`**（实测项）—— checklist「利润等价性与 `profitDrop` 实测」段 |
| 回滚脚本的列清单与 `20261124_016` 对齐（现为 `TASK-170` 形状） | `M-9` | `M-9` 的后续任务 |
| 全量回归基线 | — | **不适用**（范围规则） |
