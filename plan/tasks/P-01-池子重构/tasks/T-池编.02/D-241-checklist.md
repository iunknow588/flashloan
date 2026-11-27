# D-241 `T-池编.02`｜验收清单（**checklist**）

> 验收编号 `AC-池编.02`。**每一条都必须带实测读数**；推断不算读数。
> 读数取自**生产库只读**体检（`mode=ro` ＋ `PRAGMA query_only=ON`）与**本地回归**。

## A. 缺陷取证（**已读 · 只读**）

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| A1 | **池是否真的消失** | 19 条缺值复核的去重池 hash 是否还在**主池表** `v4_exchange_pools` | **15 / 15 全部还在**（主池表 2550 行）⇒ **池从未消失** | ✅ |
| A2 | **路由是否可复价** | 逐条看 3 跳的池是否都在主池表 | **19 / 19** 条的**每一跳**都还在 ⇒ **可复价** | ✅ |
| A3 | **缺的是作用域** | 被当前作用域覆盖的池 hash | **6 / 15**；被任一作用域**完整**覆盖的路由 **0 / 19** | ✅ |
| A4 | **代币库是否完好** | 四个代币库的 admitted 行数 | `token_core` 4、`token_stocks` 194、`token_extension` 2、`token_test` 1 ⇒ **全部完好** | ✅ |
| A5 | **缺陷发生点** | `manager_write_requests` 里 `pools.scope.rebuild` 的 `libraryKeys` 时间线 | 11-18 22:50／11-19 00:10 = `["core","stocks","test"]`（**用户所述可用状态**）→ **11-26 19:29 healer 写入 `["core"]`（def=14）** → 之后 10 次保持 `["core"]` | ✅ |
| A6 | **复核被孤儿化** | 19 条复核的写入时刻 vs 收窄时刻 | 复核写于 **11-26 19:06:40–19:11:20**（作用域仍是 `core_stocks`）⇒ 比收窄早 **18 分钟** ⇒ **被收窄孤儿化**，不是「测不到」 | ✅ |

## B. 代码项（**已落地 · 回归已跑**）

- [x] **B1** **`M-1` 默认加载模式不得窄于声明**：`scope.rs` 的 `TokenLoadMode::default_for_scope()` 改为按**作用域自身声明**取模式（新增 `for_library_keys()`）；注释写明「默认不得比声明更窄」
- [x] **B2** **`M-1` 审计判定由「精确相等」改为「声明 ⊆ 已配置」**：`baseline_audit.rs` 新增 `scope_carries_declared_libraries()`；宽于声明**不算** stale，缺库**才算** stale
- [x] **B3** **`M-1` 审计把声明的库报出来**：`PoolBaseline` 新增 `coreScopeExpectedLibraries` / `candidateScopeExpectedLibraries`（camelCase 线格式）
- [x] **B4** **`M-8` healer 不再自带模式**：`AUDITED_SCOPE_TOKEN_LOAD_MODE = None`；新增 `scope_load_mode_name()` / `scope_mode_narrows()` / `scope_expected_libraries()` / `scope_configured_libraries()` / `scope_expected_modes()`
- [x] **B5** **`M-8` 收窄守卫**：`repair_scope_policy()` 在请求模式会**丢掉作用域已有库**时**跳过**并记录 `skipped`，且说明原因

## C. 回归（**只跑改动模块**）

- [x] **C1** **`M-1`**：`cargo test --offline -p pool-manager` ⇒ **175 passed / 0 failed**（含新增 2 项：默认不得窄于声明；宽于声明不算 stale / 缺库才算 stale）
- [x] **C2** **`M-8`**：`python -m pytest scripts/tests/test_mainnet_arbitrage_healer.py -q` ⇒ **126 passed / 0 failed**（基线 119 ＋ 新增 7）
- [x] **C3** **`M-8`**：`python -m pytest flashloan/app_runtime/tests/unit/test_mainnet_arbitrage_healer_repair_chain.py -q` ⇒ **44 passed / 0 failed**

## D. 部署与生产恢复（**顺序不可反**）

- [x] **D1** **先部署**：`cargo build --offline --release -p pool-manager -p manager-api` **成功（3 min 56 s）**，日志 `plan/runtime_evidence/logs/t401_runtime_sup/cargo_build_release_scope_restore_20261128.log` ⇒ 二进制带修复
- [x] **D2** **重启 manager-api**（Windows 下运行中的 exe 无法被覆盖 ⇒ 先停后建后起）与 **重启 healer**（Python 改完必须重启进程）：manager-api 重启为 **pid 25572**；healer 以修复后的脚本重启为 **pid 20160**（命令与日志见 `plan/runtime_evidence/logs/t401_runtime_sup/healer_supervise_scope_restore_20261128.log`）⇒ **首个周期后作用域仍是 `["core","stocks"]` 未被收窄** ⇒ 缺陷在生产上闭环
- [x] **D3** **恢复作用域**：两个作用域由 `["core"]` 重建回**声明模式** `["core","stocks"]`（`manager-api` `/v1/pools/scopes/rebuild`，`dryRun=false`）⇒ `v4_exchange_pools_core4` **414 → 428 行（删 0、加 14）**，`..._candidate_exchange` **428**
- [x] **D4** **重建级联**：`candidateEdge → candidateExchange → directionalPools → closedLoops` 按序重建 ⇒ 第一轮 **52 池 → 216 方向 → 24 跳 → 8 闭环**；**深度重测后的第二轮**（§D6）**64 池 → 278 方向 → 108 跳 → 36 闭环**
- [x] **D5** **实盘读数**：审计 `core_scope_library_policy_stale` / `candidate_scope_library_policy_stale` **消失**（`coreScopePolicyMatches = True`、`candidateScopePolicyMatches = True`、`candidateScopeOutOfPolicyPoolCount = 0`，报告 `plan/runtime_evidence/reports/baseline_audit_after_restore_20261128.json`）；路由每一跳都在主池表 **19 / 19**；作用域覆盖 **12 / 15** 池、**6 / 19** 路由完整覆盖

> **★ D5 的期望值更正（读数推翻原写法）**：本框原写「15 / 15 池被作用域覆盖、19 / 19 路由完整覆盖」。
> 实测**达不到**，原因是**链上事实**而不是缺陷：剩下 **3** 个池的深度档位是 **dust**
> （`0x24107d15` / `0x84bd4e2d` / `0xe1ae6a4c`），而作用域判据只放行 `deep` / `liquid` / `medium`。
> **未为此放宽任何判据** ⇒ 覆盖停在 **12 / 15** 池、**6 / 19** 路由（§D6）。

## D6. 深度修复与重新物化（**本轮第二阶段 · 已执行**）

- [x] **D6.1** **池子工具重测**：`repair_unavailable_pool_depth_20261128.py` ⇒ dry run `returncode = 0` / `refreshedCount = 9` / `writtenCount = 0`；apply **`writtenCount = 9`**、**`errors = []`**
- [x] **D6.2** **档位落地**：`unavailable` → **deep 4 / medium 2 / dust 3**（`has_liquidity = true` ＋ 链上 `tick` 流动性读数）⇒ **测量缺失**已消除
- [x] **D6.3** **作用域重新物化**：两个作用域各 `insertedRows = 6`、**`deletedRows = 0`**、`activePoolCount` **26 → 32**、`idempotentReplay = false` ⇒ 表行数 **428 → 434**
- [x] **D6.4** **幂等键陷阱已修**：`rehearse_scope_rebuild_20261127.py` 原用**固定幂等键** ⇒ 重建只**重放旧结果**、**不重新物化**；已改为**每次调用唯一幂等键**（见 `D-242` §7.1 的教训）
- [x] **D6.5** **级联与复核**：闭环 **8 → 36** 条（**24 → 108** 跳）；新代次复核版本 **70 / 71** 共 **9** 行**全部有明确盈亏**（**全负**，灰尘级）⇒ 恢复的路由**正在被真实定价**

## E. 诚实边界（**永久留占位**）

- [ ] **E1** **不伪造**：本任务**不填任何盈亏数值**、不填 0、不放宽任何门禁、不删任何历史复核行。恢复作用域只让那 19 条路由**重新可定价**，它们**是否真的有盈亏**由链上事实决定，**不预判**。
- [ ] **E2** **不代为关闭**：广播缺口仍是**市场条件**问题（正利润是灰尘级），本任务**不代任何其它任务关闭**；本框**不代为关闭**。
- [ ] **E3** **撤回旧结论**：上一轮登记的「目录外复核不可复价 → 退役语义」**前提已被推翻**（A1／A2）⇒ 该任务的收口口径**必须改写**，不得按「退役」结案。
