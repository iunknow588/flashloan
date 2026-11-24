# D-018 `T-候.01`｜USDG 估值单位与覆盖 · 测试目标 check list

- [x] **第 51 轮只读核验 1/2/3/5 已闭合**：单位契约全部验算（signed PnL raw↔human 44/44、

      ladder 288 行探针 0 不一致、fee 1e-6、gas 不入 PnL）；估值对象 = **资金锚点**；

      ETH / WETH 两个独立 Currency；USDG identity 36/36 闭环命中；TZ 缺价格 → **fail-closed**。

- [ ] **第 4 项**（测试网 `loops>0`）：**根因已定位，判据仍不成立** —— 读数见
      `docs/D-107-测试网闭环0环根因链与可恢复性实测_20261124.md`。

      **0 环的四层根因（副本实测）**：

      | 层 | 事实 | 读数 |
      |---|---|---|
      | 1 迁移缺口 | 测试网**缺 3 个迁移**（`014` / `015` / `016`）⇒ `manager_v4_directional_exchange_pools` **表都不存在** | 主网 **692** 行 / 测试网 **无表** |
      | 2 池宇宙 | `core4` 只有 **5** 个池（主网 **488**）；全链池索引 28 池中 **28 个带钩子** | 可用无钩子池 **4~5** |
| **2′ 池宇宙（**本轮实测修订**）** | **链上已有 68 个 `poolId`**（`Initialize` 事件、50 000 区块/窗 × 12 窗），**本地库仍只有 5** ⇒ **本地库落后 63 个（≈ 13.6 倍）** ⇒ 性质由「**等链上**」变为「**软件侧可刷新**」 | 见 [`D-110`](./docs/D-110-测试网链上池宇宙实测增长与可恢复性再评估_20261124.md) |
      | 3 YZ 观测 | `manager_market_observation_yz_pairs` 仅 **20** 行（主网 **1420**），**4** 条 `no_reachable_v4_pool` | 缺失对：**AMD↔ETH**、**AMZN↔TSLA** |
      | 4 复核代次 | 8 条复核全 `not_executable`，且挂在 **v2 旧代**（当前代 v10 = 0 环）⇒ **8/8 的 `loop_key` 不在目录里** | 复核覆盖 = **旧代残留** |

      **副本实测**：迁移 014/015/016 全 `appliedNow`（纯 schema）；方向池物化两个作用域各 **10** 行
      （d0=5 / d1=5 / 可用对 8 / 已测容量 10 / 已测输出 10）；**闭环物化仍 `loopCount=0`**
      ⇒ **0 环的根因在第 2/3 层（链上事实），不在第 1 层**。

      **第 4 项里可测的那一半已测且通过**（副本上的采集器读数）：
      `signedPnl.rawToHuman` **8 met / 0 notMet**、`currencyMatchesLibrary` **8/0**、`gas.separateFromPnl` **8/0**、
      `fee.unit` **7 met / 0 notMet / 1 unmeasured**；
      `ladderRows.rawToHuman` / `borrowPrincipal.unit` / `borrowToken.usdgValuation` = **8 条全部 `unmeasured`**
      （字段未发布 ⇒ **不是 `notMet`**，**不得**记为不合规，**不得**用零冒充）。

      **结论（**2026-11-24 晚修订**）**：测试网要 `loops>0`，仍**必须**先补齐
      `AMD↔ETH` / `AMZN↔TSLA` 的可达 V4 池（**层 3 未推翻**）**且**先补齐
      **层 1 的迁移缺口**（`014`/`015`/`016`）；**但层 2 的前提已变** ——
      **链上实测已有 68 个 `poolId`**，本地库只有 **5** 个 ⇒ **池宇宙不再是「等链上」，
      而是「软件侧可刷新」**（[`D-110`](./docs/D-110-测试网链上池宇宙实测增长与可恢复性再评估_20261124.md)）。
      **扫描器基础设施已就绪**（`ADAPTIVE_SCAN_CHUNK_SIZES` 含 `50_000`/`10_000`/`1_000`）
      ⇒ **第 ① 步（刷 `core4` 池域深度）现在有可执行对象** —— **本轮未执行**
      （执行会写库 ⇒ **需单独授权**）。
      ⇒ 本项**仍维持未测量**、**不排期、不改判为完成**（层 1/层 3 **仍在**）。

- [x] **主网侧复核 1/2/3/5 重取**（不沿用旧读数）—— 读数见 `docs/D-105-主网重取读数与语义判定_20261124.md`。

      **重取口径**：`reviewCount` 44 → **164**，`loopCount` 164 → **174**（数据面变化）。

      | 项 | 旧 | 新 | 判定 |
      |---|---|---|---|
      | 1 | `met` | `notMet` | **语义**：150 行 `data_incomplete` **未发布 PnL** ⇒ 记为 `notMet` 属「不适用」 |
      | 2 | `met` | `met` | identity **42/42** 精确、`requiresUsdgUsdgPool=false` |
      | 3 | `met` | `notMet` | **语义**：阶梯行 **0/164**、`requestedAmount` **0/164** ⇒ **未测量** |
      | 5 | `met` | `met` | 覆盖缺口 `COIN`/`SPY`/`TZ` → **fail-closed**；`fee`/`gas` 164/164 |

      **已发布 PnL 的 14 行独立复算：raw↔human 失配 0、`currency` 与 token 库不一致 0、
      token 库跨 4 表 `(symbol, decimals)` 冲突 0 ⇒ 单位契约 100% 合规。**
      **未发布 PnL 的 150 行不得记为「不合规」，也未用零或「已满足」冒充。**

- [x] **本版不变**：方向池重构**不触碰**估值路径 —— **静态核验**（`D-105` §5）：

      估值路径命中文件全部落在 `rs-src` / `closed_loop_manager` / `token_manager` /
      `pool_manager` / `manager_db`；**`candidate_edge_pool_manager` 与
      `candidate_exchange_pool_manager` 均未命中**；`usdg_valuation.rs` 最后改动为
      **`5ff7a1d8`**（早于本轮池重构系列）；池重构系列仅动 `token_manager/src/prune.rs`
      （新增方向池级联清理，**无语义改动**）。

> **诚实边界**：第 4 项（测试网 `loops>0`）、阶梯行、`borrowPrincipal.requestedAmount`
> 三项**未测量** —— 前者因**链上无可达中间跳池**（根因见 `D-107`，非软件缺陷），
> 后两者因字段未发布 ⇒ **判据结构上不成立**。**不排期、不改判为完成。**
>
> **活库零写入** —— 本轮全部变更**只发生在副本** `flashloan/db/sandbox/t46630_directional_probe.sqlite3` 上；
> 活库 `flashloan/db/local_store_46630.sqlite3` **字节未变**；主网读数未变（`164` / `174`）。
