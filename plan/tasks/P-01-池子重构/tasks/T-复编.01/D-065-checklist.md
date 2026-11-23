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



> **T-7 是本项最容易被写错的地方**：`not_executable` 且 `signed_pnl.amount < 0`

> 是**完整的测量结论**，不是缺值。**一旦被降级成 `data_incomplete`，就重演了当前的 150 条。**



#### 归属验收（**M-5**）



- [ ] 新增架构守卫：Python 侧**不得**出现对 Manager 复核库的写入实现。

- [ ] 架构守卫全量通过（当前基线 **62 passed**，新增后**只增不减**）。



#### 回归验收



- [ ] Rust `flashloan/rs-src` 全量 **0 failed**（基线 **202 passed**）。

- [ ] Rust `flashloan/tools` 全量 **0 failed**（基线 **0 failed**）。

- [ ] `cargo build --offline --workspace` **exit 0**。

- [ ] Python 全量 **3208 passed / 3 skipped**（第 65 轮基线），**不得**回归。



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