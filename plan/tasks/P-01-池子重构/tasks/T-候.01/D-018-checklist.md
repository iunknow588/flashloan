# D-018 `T-候.01`｜USDG 估值单位与覆盖 · 测试目标 check list

- [x] **第 51 轮只读核验 1/2/3/5 已闭合**：单位契约全部验算（signed PnL raw↔human 44/44、

      ladder 288 行探针 0 不一致、fee 1e-6、gas 不入 PnL）；估值对象 = **资金锚点**；

      ETH / WETH 两个独立 Currency；USDG identity 36/36 闭环命中；TZ 缺价格 → **fail-closed**。

- [ ] **第 4 项**（测试网 `loops>0`）：⏸ **随测试网任务暂停**。

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
> 三项**未测量** —— 前者因测试网 Manager 未发布闭环（`loopCount=0`），
> 后两者因字段未发布 ⇒ **判据结构上不成立**。**不排期、不改判为完成。**
