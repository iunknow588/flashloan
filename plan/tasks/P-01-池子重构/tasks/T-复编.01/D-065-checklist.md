# D-065 `T-复编.01`｜Rust 生产侧复核发布链路 · 测试目标 check list

**验收前置**：无（`rs-src` **已依赖** `review-manager`，**依赖在，调用缺**）。



#### 结构验收



- [ ] **发布调用存在**：Rust 侧存在对 `review_manager::run_reviews_write` 的**生产调用**

      （不是只有单测在调，不是只有 `manager_api` 在调）。

- [ ] **调用点在复核产出侧**：位于 `flashloan/rs-src/src/worker_pool.rs` 的复核产出路径

      （`run_native_review` 结果落地之后），**不是**在 Python 侧、**不是**在 HTTP 路由里。

- [ ] **全量提交语义**：发布前**必须**判定"**覆盖全集**"

      （`accumulated.len() == closed_loop.loop_count` 且每个 key 都在闭环目录中）；

      **部分覆盖时必须拒绝发布**。

      **理由**：`replace_review_rows` 先 `DELETE FROM manager_v4_reviews WHERE network/chain_id/protocol/scope_key`

      再全量 `INSERT` ⇒ **部分提交 = 抹除其他闭环的复核**。

- [ ] **`loop_key` 精确匹配**：使用 Manager 发布的闭环 key（`closedLoopKey` / `identity.loop_key`），

      **不得**本地自造；**匹配不上时不得**写入 `run_arbitrage_json=null` 的 `data_incomplete` 行。

- [ ] **`ExpectedUpstream` 来源唯一**：全部字段取自

      `closed_loop_manager::read_closed_loop_status(db, network, chain_id)`，

      **不得**在本模块另算 checksum / version。

- [ ] **幂等键非常量**：含上游版本 + 校验和 + 轮次标识；连续两轮刷新**不碰撞**。

- [ ] **失败可观测**：`run_reviews_write` 的失败进入 Rust 状态输出，

      **不得**在状态里表现为"复核正常"。

- [ ] **`consumers_need_rebuild` 未被手工清**：本任务**不得**直接写该标志；

      其清除只能来自 `closed_loop_manager` 的自愈路径。



#### 行为验收（单元测试，**8 项全过**）



| # | 场景 | 期望 | 判定 |

|---|---|---|---|

| **T-1** | **全量提交** | `manager_v4_reviews` 行数 = 闭环数（当前 **168**）；`data_incomplete_count` 从 **150 → 0** | ☐ |

| **T-2** | **部分覆盖时发布** | **被拒绝**（或合并到全量）；**其他闭环的复核行不得被抹除** | ☐ |

| **T-3** | **`loop_key` 不匹配** | **不得**产出 `data_incomplete` 行；要么匹配成功，要么整体拒绝 | ☐ |

| **T-4** | **幂等键复用** | 同键不同内容 → 报错（`IdempotencyKeyReused`），**不得**静默成功或静默覆盖 | ☐ |

| **T-5** | **上游不匹配** | 写入被拒 | ☐ |

| **T-6** | **`signed_pnl` 校验** | `raw` / `amount` / `currency` / `unit` / `source` 缺一即失败 | ☐ |

| **T-7** | **测得零 / 负盈亏** | 原样保留为**测量结果**，**不得**降级成"缺值"（`positive=0` 是真实业务结论） | ☐ |

| **T-8** | **发布失败不静默** | 状态输出显示失败，**不得**显示为复核正常 | ☐ |

| **T-P9** | **存储方向矛盾** | 复核**跳过**该 hop 并记 `direction_mismatch`；**不得**静默改写方向 | ☐ |

| **T-P10** | **存储方向一致** | 复核**正常放行**（交叉校验只加断言，**不放宽**既有门禁） | ☐ |



> **T-7 是本项最容易被写错的地方**：`not_executable` 且 `signed_pnl.amount < 0`

> 是**完整的测量结论**，不是缺值。**一旦被降级成 `data_incomplete`，就重演了当前的 150 条。**



#### 复核层方向交叉校验（**`M-6` · 由 `M-4` 的闭环层切换移交**）

> **本组验收项的来源**：`M-4`（闭环层）切换到方向池后，复核层**仍按路由地址重算方向**，
> 与存储方向**可能漂移**。`M-4` 的改动模块集合**不含** `M-6` ⇒ 按**先扩后跑**纪律
> **不扩 `M-4` 的范围**，把这组项**如实移交**本任务（`M-6` 已在改动模块集合内）。
> **移交对照**：`T-方闭储.01` 的 `T-P8` / `I-13` / 「复核层 `build_hops` 读存储方向 + 交叉校验」
> / 「复核交叉校验：`direction_mismatch` 计数 == 0」—— **本组即其落点**。

- [ ] **`build_hops` 改为读存储方向 ＋ 交叉校验**：`review_manager/src/parity.rs` 的 `build_hops`

      **不再由路由地址与池 token 序重算 `zero_for_one`**，改为从闭环 hop 的**存储方向**
      （经 `directional_pool_id` 解析）读取，并保留**一次交叉校验**。

      **取池逻辑不变**：`resolve_route_pool_ids` **仍按 base 取池**（按 `pool_key_json` 匹配
      `PoolKey`），**不得**改成按方向池表解析。

- [ ] **`direction_mismatch` 已实现并可观测**：存储方向 ≠ 由池 token 序推导的方向时，

      **跳过该条复核**并记录 `direction_mismatch`（计数 + 可定位到闭环 / hop）；
      **不得**静默改写方向，**不得**静默跳过。

- [ ] **交叉校验不一致的 hop 被跳过、其余 hop 正常处理**（**不得**整条复核连坐作废）。

- [ ] **`build_hops` 的既有拒绝语义未变**：池不在路由两端之间时**仍跳过复核**（**不变**）。

- [ ] **单元测试覆盖**：构造一条 `hop_direction` 与池 token 序**矛盾**的 hop ⇒
      复核**跳过**并记 `direction_mismatch`；构造一致 hop ⇒ **正常放行**。

- [ ] **`direction_mismatch` 计数 == 0**（**实盘读数**）：物化后逐条复核的
      `direction_mismatch` 计数 **必须为 0**；**不得**以「未测量」冒充闭合。

      > **存储侧前提已就绪**（`M-4` 侧实测）：实盘 **504 跳** `hop_direction` == 方向行 `direction`
      > == 由 `zero_for_one` 推导的方向，**0 违规** —— 本项是**复核层**的**读取侧**断言。

- [ ] **回归：`cargo test --offline -p review-manager` 全绿**（本组项改动 `M-6` ⇒ 落在本任务回归范围内）。


#### 归属验收（**M-5**）



- [ ] 新增架构守卫：Python 侧**不得**出现对 Manager 复核库的写入实现。

- [ ] 架构守卫全量通过（当前基线 **74 passed**（2026-11-24 实测：71 ＋ 3 条新增方向层守卫），新增后**只增不减**）。



#### 回归验收



- [ ] Rust `flashloan/rs-src` 全量 **0 failed**（基线 **202 passed**）。

- [ ] Rust `flashloan/tools` 全量 **0 failed**（基线 **0 failed**）。

- [ ] `cargo build --offline --workspace` **exit 0**。

- [ ] Python 全量 **全绿**（基线见 [`D-014`](../../docs/D-014-未完成任务清单_20261123.md) §5：第 84 轮 **2743 passed / 3 skipped**），**不得**回归。



#### 归属边界验收（**不得**发生）



- [ ] **未**改动任何金额 / 容量 / 利润**计算**逻辑。

- [ ] **未**改动 `runArbitrage` 的 `ok` / `executable` 判定。

- [ ] **未**改动新鲜度阈值（**600s** / **120s**）。

- [ ] **未**在 Python 侧新增任何发布 / 物化 / 调度代码。

- [ ] **未**新增表或迁移（**纯代码任务**）。

- [ ] **未**广播。



> **说明**：本项**只交付代码**。"复核因此变得新鲜"是 `T-复.01` 的验收内容，

> **不得**在本项报告里写成"已完成"。



- [ ] **本版新增前置**：`T-方闭储.01` **已闭合**

      （否则 `T-复.01` 物化出的复核会被上游漂移作废，等于要物化两次）
