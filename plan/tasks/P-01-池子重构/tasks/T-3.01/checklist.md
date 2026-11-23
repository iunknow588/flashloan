


**验收前置**：✅ **已满足** —— 已闭合·已归档 **已闭合**（第 72 轮）、已闭合·已归档 口径判定**已裁定** = **采用新口径为默认**。



#### 结构验收



- [ ] migration `20261123_015_directional_pool_schema.sql` 存在，含**主表 + 库状态表 + 三个索引 + 一个 UNIQUE 索引**

- [ ] `manager_db/src/migrate.rs` 五处登记齐全：`MIGRATION_IDS` / `LATEST_MIGRATION_ID` /

      `TARGET_TABLES` / `required_columns` / 迁移→表组映射

- [ ] `LATEST_MIGRATION_ID` 指向新迁移，且新迁移 id **恰为** `20261123_015`（**不得**跳过 `20261121_014`）

- [ ] `manager_contracts/src/directional_pool.rs` 存在，导出 `PoolDirection`（0 / 1，含

      `zero_for_one()` 与 `of_pool()` **纯函数**）、`V4Pool`、`DirectionalPool`（`base` + `direction` + 方向量）

- [ ] `manager_contracts/src/ids.rs` 新增 `DirectionalPoolId`，并从 `manager_contracts/src/lib.rs` 导出

- [ ] `flashloan/tools/directional_pool_manager` crate 存在，提供 `materialize_directional_pools` /

      `materialize_directional_pools_write` / `read_directional_pool_status` / `read_directional_pool_snapshot`

- [ ] 幂等键 + 请求摘要 + dry-run / apply 语义**与 `candidate_exchange_pool_manager` 保持一致**

- [ ] `composite_manager/src/lib.rs` 依赖图新增方向层节点，**阶段内不开启自动调度**

- [ ] `manager_api` 提供 `GET /v1/directional-pools` / `GET /v1/directional-pools/compare` /

      `POST /v1/directional-pools/rebuild`，权限动作 `pools.directional.read` / `pools.directional.rebuild`

- [ ] **新增第 8 个授权维度** `DIRECTIONAL_POOL_MANAGER_AUTHORITY` + `directional_pool_authority` 状态位

      （**不得**复用 `candidate_exchange_authority` / `composite_authority`）；

      缺省回落 `Legacy` ⇒ 写端点被拒（`manager_apply_enabled()` 仅在 `Manager` 时为真）

- [ ] **负向验证**：未设置该环境变量时，`POST /v1/directional-pools/rebuild`（`--apply`）

      **被拒绝**并返回 `authority_denied`；设置后才可写

- [ ] `token_manager/src/prune.rs` **纳入方向表清理**（不留孤立行）

- [ ] 迁移烟雾测试与架构守卫**已登记**新表名 / 新列



#### 行为验收（单元测试，**T-D-1..T-D-12 全过**）



| # | 场景 | 期望 | 判定 |

|---|---|---|---|

| **T-D-1** | **每个池产生两行** | 方向 0 / 1 各一行，`input_token_key_id` 符合 `T-3.01` 内的方向映射表 | ☐ |

| **T-D-2** | **方向映射不可翻转** | 人为构造 `token0` 地址更高的池，方向仍为 0，输入仍是 token1 | ☐ |

| **T-D-3** | **排序与旧口径一致** | 方向表序位 `1..N` 与 `recommend_direction` 的输出顺序**逐行相等** | ☐ |

| **T-D-4** | **序位连续** | `(pair_key_id, direction)` 内无缺号、无重号 | ☐ |

| **T-D-5** | **容量来源正确** | 方向 0 取 `single_tick_max_token1_raw`，方向 1 取 `single_tick_max_token0_raw` | ☐ |

| **T-D-6** | **未测量不被当作零** | `NULL` 与 `'0'` 分开存储，`input_capacity_measured` 为 0 / 1 | ☐ |

| **T-D-7** | **非活池不获序位** | `pool_status != 'active'` 的行 `pair_direction_rank IS NULL` | ☐ |

| **T-D-8** | **物化幂等** | 重复 apply → `changedRows = 0`；摘要变化 → 校验和变 | ☐ |

| **T-D-9** | **dry-run 不写库** | 幂等表**无新记录** | ☐ |

| **T-D-10** | **幂等重放** | 相同键 + 相同摘要 → `idempotentReplay=true`；摘要变 → `IDEMPOTENCY_KEY_REUSED` | ☐ |

| **T-D-11** | **一个基础池恰好两行** | 行数 == 基础池数 × 2；两行**仅**方向相关字段不同（**I-3**） | ☐ |

| **T-D-12** | **内存集合与库行集合一一对应** | 装载后 `directional_pools.len()` == 方向表行数（**I-10**） | ☐ |

| **T-D-13** | **准入约定不被突破**（**2026-11-23 新增 · DPO-9**） | 方向行**只**指向 `pool_status = 'active'`、**固定费率**（`0 < fee <= 30000`）、**特殊费率 = 0**、**无 hook**（`hooks = 0x0`）、**非动态费率**的基础池；任一违反 ⇒ **判定失败** | ☐ |

| **T-D-14** | **边界来源不得是漂移的存量列**（**2026-11-23 新增 · DPO-10**） | 方向层的 `input_capacity_raw` **必须由 Rust 侧重算**（或只读 Rust **当前写入**的那一份），**不得**直接采信可能已过期的 `single_tick_max_token*_raw`（见 **已闭合·已归档** §1.1.6 / 已闭合·已归档） | ☐ |



#### 不变量验收（**I-1..I-6 / I-9 / I-10**）



| ID | 不变量 | 检查方式 | 判定 |

|---|---|---|---|

| **I-1** | 方向 0 行的 `input_token_key_id` 恒等于该池的 `token1_key_id`；方向 1 行恒等于 `token0_key_id` | 物化时断言 | ☐ |

| **I-2** | `direction = 1 ⇔ zero_for_one = 1`；`direction = 0 ⇔ zero_for_one = 0` | 物化时断言 | ☐ |

| **I-3** | 同一 `base_pool_id` 的两行，除方向字段与方向量外**必须完全一致** | 物化时断言 | ☐ |

| **I-4** | `input_capacity_raw` 来源恒定：方向 0 ← `single_tick_max_token1_raw`，方向 1 ← `single_tick_max_token0_raw`；且**只取 Rust 当前写入的那一份**（已闭合·已归档 闭合前**必须自己重算**） | 物化时断言 | ☐ |

| **I-5** | 方向表**不包含**任何基础池表与快照表之外的数据 | 字段白名单 + 审计 | ☐ |

| **I-6** | `pair_direction_rank` 唯一且在 `(pair_key_id, direction)` 内连续 | 物化时断言 | ☐ |

| **I-9** | 方向行的 `base` 字段与基础池表对应行**逐列一致**（身份列 + 快照列） | 物化时逐列断言；P0 影子比对抽样 | ☐ |

| **I-10** | 内存中的 `DirectionalPool` 集合与方向表**行集合一一对应**（无遗漏、无多余） | 物化时计数断言 | ☐ |



#### 归属验收（**M-6**）



- [ ] 新增架构守卫：Python **不得**直查方向池表

- [ ] 新增架构守卫：Python **不得**实现方向池的排序 / 去重 / 物化逻辑

- [ ] 新增架构守卫：Python **不得**实现方向池的**准入**判定 / **边界重算** / 方向映射

      （**DPO-10**：方向池的物化、排序、准入、去重、边界重算**全部只在 Rust 侧**；

      Python 侧**只**消费内存对象里的**实际值**）

- [ ] 新增架构守卫：方向层**不得**把**非 active / 动态费率 / 带 hook** 的基础池投影成方向行

      （**DPO-9**）

- [ ] 架构守卫全量通过（基线 **62 passed**，新增后**只增不减**）



#### 边界验收（**不得**发生）



- [ ] **本阶段只新增类型，不改任何调用方**——两份 `ScopePool` 仍在使用，旧链路行为**一字不变**

- [ ] 基础池表（`v4_exchange_pools_core4` / `v4_exchange_pools_candidate_exchange`）与

      `v4_exchange_pool_liquidity_snapshots` 的**表结构与写入语义**未变

- [ ] 现有链路任何表**行未变**（候选表 / 观察表 / 闭环表 / 复核表的行数与校验和均未变）

- [ ] **未**改 `liquidity_index` 的任何含义（**C-3**）

- [ ] **未**删除任何旧函数（旧方向键、旧排序仍可用，下线是 `T-3.02`）

- [ ] **未**改变任何循环 / 折叠 / 定价算术

- [ ] **未**广播



#### 证据与回归验收



- [ ] 单元测试通过记录（`T-D-1..T-D-12` 逐条）

- [ ] 迁移烟雾通过记录

- [ ] 架构守卫通过记录

- [ ] 只读物化比对报告：新表行数 == 基础池数 × 2；两行除方向相关字段外**逐列一致**；

      `changedRows` 幂等证据；校验和前后值

- [ ] migration 已**实盘写入**目标生产库（基础框架库**不需**额外授权，验证通过即**必须**写入）

- [ ] 方向表**行的物化**已按 §0.0 完成授权

- [ ] 全量回归：Python **3208 passed / 3 skipped**、Rust `rs-src` **202 passed / 0 failed**、

      Rust `tools` **0 failed**、架构守卫 **62+ passed**、`cargo build --offline --workspace` **exit 0**