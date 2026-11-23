# 主网编排器审查修复 Check List

更新时间：2026-11-11（S1 + S1.5 归档后重整）
适用范围：`scripts/mainnet_arbitrage_healer.py`、`flashloan/rs-src/src/`、
`flashloan/src/dex/runtime/`、`flashloan/src/core/db/storage_cow_policy.py`、
`flashloan/tools/pool_manager/src/lib.rs`、`flashloan/src/dex/intent_trade/chains/robinhood/`
伴生清单：`logs/plan/未完成任务清单_20261111.md`
已完成归档：`logs/plan/back/已完成任务归档_20261111_S1S1_5.md`
审查依据：生产库 `flashloan/db/local_store_4663.sqlite3`（3345 MB）实测验证

本文件是可执行的验收清单，每项必须回填"完成标准"才能勾选。

> **本清单只保留未完成项。** 阶段 1（S1 口径修正）与阶段 1.5（S1.5 池深根因修复）
> 已于 2026-11-11 完成实施并通过回归，移入 `back/已完成任务归档_20261111_S1S1_5.md`。
>
> **已作废的判据（不得再作为验收依据）**：
> - `corr(log10 li, log10 TVL) > 0.7` —— 实测仅 +0.09，`li` 不是深度信号；
> - 排除 `liquidity_index < 1` 的池 —— 会误删 $97.8K–$4.96M 真深池并打断 24 条闭环；
> - `market_depth_exhausted` / `no_depth_qualified_pool_exists` —— 均不成立；
> - ε = 0.5% —— 已统一为 **10 bps**（复用 `DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS`）。
>
> **保留约束**：在真实储备数据全面铺开前，**不得依据 TVL 结论下架任何 token 对**。
>
> 本轮未执行生产写入、服务重启、闭环重建、authority 切换与真实广播；
> 守护器与 `run.py`（PID `30304`）未停止、未重启。

---

## 阶段 2：门禁补强（依赖阶段 1；已归档）

目标：让编排器能正确识别"候选集↔闭环集脱节"，并修复死锁与口径遗漏。

### 2.1 checksum 交叉校验（`AUD-P0-2`）—— 最关键

- [ ] `audit_pools()` 校验 `manager_v4_closed_loop_library_state.pool_scope_checksum == pool_scope_config.materialized_checksum`
- [ ] 同时校验 `manager_dex_candidate_library_state.pool_scope_checksum`
- [ ] 不一致时判 `pool_scope_drift`，输出三方 checksum 实值
- [ ] 新增对应 `transaction_production_status` 分支（不得落入 `upstream_invalid` 的广义池）
- [ ] 验证：当前生产库必须报出 `pool_scope_drift`（现实值 `5434a25c` vs `8ceb9e02`）
- [ ] 验证：重建后三方一致，该错误消失

### 2.2 候选有效性口径（`AUD-P0-3`）

- [ ] 池层有效性以真实 `dex_candidate_pools` 表为准，不信任 `eligible_pool_count`
- [ ] `manager_dex_candidate_library_state` 降为交叉校验项（陈旧时报差异）
- [ ] 验证：报告同时暴露 `stateEligiblePoolCount` 与 `actualCandidatePoolCount`

### 2.3 修复链死锁（`AUD-P1-1`）

- [ ] `repair()` 的闭环重建触发条件改为 `consumers_need_rebuild != 0`
- [ ] 不再使用修复前的 `before["layers"]["pools"]["valid"]`
- [ ] 池修复后强制进入 `materialize-scope` → `materialize-arbitrage`
- [ ] 验证：池修复一轮后闭环层能自动恢复为 valid

### 2.4 复核缺失三计数（`AUD-P1-2`）

- [ ] `audit_reviews()` 拆分 `missingPnl` / `missingBestAmount` / `estimateOnlyPositive`
- [ ] `missingBestAmount` 仅统计已执行复核的行，不混入 rate-delta 估算行
- [ ] `bestAmount` 子块新增 `estimateOnly` 字段
- [ ] 验证：三计数加总与总数一致，且无重叠
- [ ] 验证：`ladderIncomplete`（1113 条）已计入 `missingBestAmount` 而非单列

### 2.5 父子门禁回溯（`AUD-P3-2`）

- [ ] `audit_pools` 实现 `best_amount_too_small → pool_depth_insufficient` 回溯链
- [ ] 子门禁失效时主 code 必须为 `pool_depth_insufficient`
- [ ] 验证：池深不足时报告只有一个主因因码

### 2.6 深度标签展示层统一（`AUD-P1-5`）

- [ ] `depth_band_json` 的 `level` 由单一函数产出，不再独立计算
- [ ] 验证：TVL 1e5~1e6 的池在两处结论一致
- [x] **阈值常量同源已完成**（`DEP-P2-1`）：`DEPTH_BAND_*` + `>=` 边界，Rust/Python 一致

### 2.7 Rust 并发入口统一（`AUD-P2-2`）

- [ ] `worker_pool.rs` 的 `max_workers=3` 改为 `16`
- [ ] 或删除该残留 `#[pyo3]` 入口
- [ ] 验证：全仓搜索 `max_workers=` 默认值均为 16

### 2.8 Rust `bestAmount` 重复写入（`AUD-P2-3`）

- [ ] 删除 `execution.rs` 中重复的 `bestAmount` 标记写入
- [ ] 保留最后一处，添加注释"必须置于所有其他分支之后"
- [ ] 验证：`cargo build --release --manifest-path flashloan/rs-src/Cargo.toml` 通过

### 2.9 守护循环小修（`AUD-P2-4`）

- [ ] 删除 `supervise` 等待循环中重复的 `time.sleep`
- [ ] 验证：等待周期实测等于 `interval`

### 2.10 证据保真（`AUD-P2-6`）

- [ ] `run_tool()` 优先保留结构化摘要，而非裸文本截断
- [ ] 确保 `pool-manager audit` 的 `ineligiblePoolIds` 不会被截掉
- [ ] 验证：大库上 `audit` 输出不丢失关键字段

### 2.11 归因与回退可观测（`AUD-P2-7`/`AUD-P2-8`）

- [ ] `transaction_production_status` 输出 `invalidLayers` 与各层 `code`
- [ ] `observation_started_at()` 回退时置 `epochFallback=true`
- [ ] `build_report` 在报告顶层显式标注 epoch 回退
- [ ] 验证：人工删除 epoch 文件后报告可见回退而非静默统计全历史

### 2.12 GAS 门禁接入广播链（`AUD-P3-3`）

- [ ] 将"已确认最佳交易量但扣 Gas 后非正"的原因接入 `storage_cow_policy._PREFLIGHT_BLOCKED_AUTO_REASONS`
- [ ] 验证：任一被 GAS 拦截的复核无法进入自动广播候选
- [ ] 说明：`best_amount_ladder_incomplete` **不需**加入该集合——
      它是复核失败原因，不是广播原因，已在 `rust_closed_loop` 广播跳过白名单中处理

### 2.13 Manager authority 契约（`AUD-P3-5`）

- [ ] `audit_reviews` 明确 Manager / legacy 两类数据源的分层统计语义
- [ ] 不再依赖空的 `manager_v4_reviews` 实现有效性判定

### 2.14 索引与触发源收尾（承接 `AUD-P1-3`/`AUD-P2-5`）

- [ ] 评估是否对 `cow_execution_reviews(selected_profit_source)` 建单列索引
      （`created_at` 范围索引 `idx_cow_execution_reviews_scope_created_v2` 已建）
- [ ] 增加候选池 TVL 分布作为池深不足的独立触发源

### 阶段 2 验收

- [ ] `scripts/tests/test_mainnet_arbitrage_healer.py` 全部通过（当前 **40 passed**）
- [ ] 新增单测覆盖：`pool_scope_drift` 检测、`pool_depth_replacement_report` 三态、PnL 三计数、epoch 回退、GAS 门禁
- [ ] `flashloan/app_runtime/tests/unit/test_rust_closed_loop_worker_bridge.py` 全部通过（当前 89 passed）
- [ ] `cargo test --manifest-path flashloan/tools/pool_manager/Cargo.toml` 全部通过（当前 **19 passed**）
- [ ] `cargo build --release --manifest-path flashloan/rs-src/Cargo.toml` 通过
- [ ] 合并回归当前值：**291 Python passed**（9 文件），不得引入新失败

---

## 阶段 2.5：池深后续（新增，解除 reviews blocker 的先决条件）

目标：让 capacity 有真实非零值，使复核阶梯能正常展开，`ladderIncomplete` 归零。

### 2.5.1 股票侧价格来源（`DEP-P1-4`）—— S3 闭环重建前必须完成

- [ ] 接入股票侧实时市场价来源（provider / 刷新周期 / 过期判定 / 兜底策略需明确定义）
- [ ] 12 个 ETH/股票配对候选池从 `unpriced` 转为可定价，产出 `depth_score_usd`
- [ ] 稳定币与股票均按**地址**识别，不得按符号
- [ ] 价格不可用时保持 fail-closed（`unpriced`），不得回退为 0
- [ ] 验证：生产候选集 `unpricedPoolCount` 归零；`balances_json` 开始承载真实容量证据
- [ ] 验证：58 条引用 `unpriced` 池的闭环跳具备可判定的深度结论

### 2.5.2 `balances` 链路根因（`DEP-P3-1`）

- [ ] `balances` 字典写入真实储备 `token0` / `token1`
- [ ] `_pool_borrow_liquidity_raw()` 不再恒返回 `None`
- [ ] `maxProbeAmountRaw` 反映真实池深，不再回退为 **64 tokens** 固定上限
- [ ] 验证：生产库 `balances_json` 不再全为 `'{}'`
- [ ] 验证：复核 `probeRecords` 长度 ≥ 2，`ladderIncomplete` 归零

### 2.5.3 多跳深度一致性（`DEP-P2-2`）

- [ ] 闭环深度分 = `min(各跳 capacity)`，三跳独立评估
- [ ] 任一跳不足则整环标记不可执行，并在复核结果中给出瓶颈跳标识
- [ ] 验证：闭环物化后能输出每环的最弱跳与其 capacity

### 阶段 2.5 验收

- [ ] `flashloan/app_runtime/tests/unit/test_robinhood_mainnet_v4_pool_index.py` 全部通过（当前 19 passed）
- [ ] 新增单测：股票侧价格回退、股票侧缺价拒绝、`balances` 链路容量上限、多跳取最小值
- [ ] 生产只读 `--mode check`：`ladderIncomplete` 显著下降、`unpricedPoolCount` 下降
- [ ] **不得**依据 TVL 结论下架任何 token 对（约束继续有效）

---

## 阶段 3：闭环重建（需停机窗口）

目标：修复 42/106 闭环池零命中的根因。**此阶段需要停止运行中的守护器与 `run.py`。**

### 3.1 修复前准备

- [ ] 备份 `flashloan/db/local_store_4663.sqlite3` 到 `logs/plan/runtime_evidence/backups/`
      （已有备份 `local_store_4663_pre_pool_depth_20261111T094829Z.sqlite3`）
- [ ] 停止守护器 `supervise` 会话与 `run.py`
- [ ] 记录重建前基线：候选池 47 行 / 16 唯一、闭环 106 / hop 318、三方 checksum
- [ ] **前置条件**：阶段 2.5 完成（否则命中的是无 capacity 证据的池，验收无效）
- [ ] **前置条件**：阶段 2.1 `AUD-P0-2` 完成（否则无法在重建前检出上游脱节）

### 3.2 重建执行

- [ ] `pool-manager cleanup`（仅清候选，不删闭环）
- [ ] `pool-manager refresh-key` 重算候选与深度标签
- [ ] `materialize-scope --apply`
- [ ] `composite-manager materialize-arbitrage --apply`
- [ ] 确认 `materialize-arbitrage` 的 `changedRows` **非 0**（当前为 0，说明未真正重建）

### 3.3 重建后验收

- [ ] 106 条闭环的 `pool_ids` **100%** 命中当前候选集
- [ ] 三方 `pool_scope_checksum` 完全一致
- [ ] `consumers_need_rebuild = 0` 且**真实验证通过**（不再依赖该字段写入后即信）
- [ ] 运行态分布中 `preflight_failed` / `execution_failed` 明显下降
- [ ] 使用一个新的测试脚本独立验证"候选集 ⊆ 闭环集"完整性（不得再出现 42 条零命中）
- [ ] 重启 `run.py` 与守护器，观察一个完整交易周期

### 3.4 回滚准备

- [ ] 备份可回滚；回滚验证脚本已保存并实测通过
- [ ] 重建失败时能从备份完整恢复

---

## 阶段 4：共享配置与总体回归

### 4.1 共享深度配置（`AUD-P3-4`）

- [ ] 提取共享配置（如 `flashloan/config/depth_policy.json`），包含：
  - [ ] `MIN_POOL_TVL_USD` = 100000（当前 Python 与 Rust 各写一份）
  - [ ] `MAX_POOL_TVL_USD` = 1000000000（当前 Python 有，Rust 侧待统一）
  - [ ] `MIN_CANDIDATE_CAPACITY_USD` = 10000（当前 Rust 有）
  - [ ] `CANDIDATE_PRICE_IMPACT_LIMIT_BPS` = 10（当前 Rust 有，须与 `DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS` 同源）
  - [ ] `GAS_ESTIMATE_ETH` = 6.2e-06（当前仅 Python 有）
- [ ] Python 与 Rust 同源读取
- [ ] 启动时断言三者一致（不一致直接启动失败）
- [ ] 验证：修改共享配置后，编排器与 pool-manager 同步变化

### 4.2 池实际上限复核（承接 `DEP-P0-1`/`DEP-P1-3`）

- [ ] 将 `MIN_POOL_TVL_USD = 100000` 与深度分带的业务含义写入审查报告
- [ ] 确认 $100K 下限与实际最佳交易量关系（尘尘级交易量 vs 池深）至少一次量化复核
- [ ] 用修正后的真实 depth score 重新推导 `MIN_POOL_TVL_USD`；若新阈值与旧值冲突，以 capacity 判据为准
- [x] 重算并重新归档 `market_depth_exhausted` 结论 —— 已完成，结论**作废**
- [ ] 若实际最佳交易量在 TVL $100K 以上仍尘尘级，说明阈值需上调（启动阶段 4 重新定义阈值）

### 4.3 容量与并发口径被动

- [ ] 验证 `max_workers=16` 在生产环境生效（启动时记录实际并发数）
- [ ] 与 `--candidate-limit 1` 的语义边界在文档中明确区分（已在 `05_数据口径与统计.md` 修正，需回填验证）
- [ ] 验证"扫描/请求数"与"worker 并发上限"两个口径在报告中分开呈现

### 4.4 文档与清单同步

- [ ] `docs/主网自愈编排器/05_数据口径与统计.md` 补写审查发现的口径修正实际数字
- [ ] `docs/主网自愈编排器/04_验收门禁与故障处置.md` 补写 `pool_scope_drift` 故障处置
- [ ] `docs/主网自愈编排器/06_独立守护工具.md` 补写阶段 3 重建记录
- [x] 本 check_list 完成项回填到 `logs/plan/未完成任务清单_20261111.md` 的状态列
- [ ] `docs/` 被 `.gitignore` 排除（无 `.gitignore` 行 34）——确认文档变更的交付方式

---

## 阶段 5：最终观察与移交

- [ ] P0-6.1：三个连续 30 分钟主网观察窗口通过
- [ ] P0-6.2：新基点后无新增 identity 异常、PnL 缺值和悬挂广播
- [ ] P0-6.3：`pnl_*.jsonl`/`pnl_*.log` 持续产出，PnL 口径经阶段 1 修正后口径一致
- [ ] P2-1：按 token / route / window 统计 expected / realized / gas / success / failed
- [ ] 最终报告应能回答：目标环数、活跃环数、每小时复核数、正收益可执行数、广播终态分布

---

## 附：审查发现逐条对照（当前状态）

| 编号 | 一句话结论 | 优先级 | 状态 | 实测证据 |
|---|---|---|---|---|
| `AUD-P0-1` | 池审查重复计数 3 倍，`adequate=47` 实为 16 | P0 | ✅ 已完成 | `dex_candidate_pools` 47 行 / 16 唯一 `pool_id` |
| `AUD-P0-2` | 候选集与闭环集 checksum 不一致，42/106 环池零命中 | P0 | ⏳ 待开始 | `5434a25c` vs `8ceb9e02` |
| `AUD-P0-3` | `eligible_pool_count=114` 与实际 47 不符 | P0 | ⏳ 待开始 | state 表未随 cleanup 同步 |
| `AUD-P0-4` | rate-delta 估算当利润，`nonPositive` 标签错 | P0 | ✅ 已完成 | `positiveEstimateOnly=76`；`nonPositive=1185` |
| `AUD-P1-1` | 闭环重建被修复前池状态卡死 | P1 | ⏳ 待开始 | `healer.py:repair` |
| `AUD-P1-2` | 复核缺失三类混为一谱 | P1 | ⏳ 待开始 | `healer.py:audit_reviews` |
| `AUD-P1-3` | `too_small` 变量永远不参与判定 | P1 | ✅ 部分完成 | `insufficient = below_gas + too_small`；TVL 分布触发源转阶段 2.14 |
| `AUD-P1-4` | 编排器无 TVL 上界，与 Rust 不一致 | P1 | ✅ 部分完成 | 上界 `<= 1e9` 已加；跨语言共享源转 `AUD-P3-4` |
| `AUD-P1-5` | 深度标签与独立阈值矛盾 | P1 | ✅ 部分完成 | 阈值常量已同源；展示层收口待做 |
| `AUD-P2-1` | `COALESCE` 剥离 REAL affinity，历史已踩一次 | P2 | ✅ 已完成 | 统一 `CAST(... AS REAL)` |
| `AUD-P2-2` | Rust 残留入口默认 `max_workers=3` | P2 | ⏳ 待开始 | `worker_pool.rs` |
| `AUD-P2-3` | `bestAmount` 标记重复写入两次 | P2 | ⏳ 待开始 | `execution.rs` |
| `AUD-P2-4` | 守护循环重复 `time.sleep` | P2 | ⏳ 待开始 | `healer.py:supervise` |
| `AUD-P2-5` | `review_rows()` 无 LIMIT，全表扫描 | P2 | ✅ 部分完成 | LIMIT + `scope_created_v2` 索引已加；单列索引转阶段 2.14 |
| `AUD-P2-6` | stdout 截断可丢失关键证据 | P2 | ⏳ 待开始 | `healer.py:run_tool` |
| `AUD-P2-7` | `upstream_invalid` 掩盖具体原因 | P2 | ⏳ 待开始 | `healer.py:transaction_production_status` |
| `AUD-P2-8` | epoch 失败静默回退 1970 | P2 | ⏳ 待开始 | `healer.py:observation_started_at` |
| `AUD-P3-1` | 池深清理后闭环未重建 | P3 | ⏳ 待开始 | `materialize-arbitrage` `changedRows=0` |
| `AUD-P3-2` | bestAmount 与池深门禁并列非父子 | P3 | ⏳ 待开始 | `healer.py:audit_pools` |
| `AUD-P3-3` | GAS 拦截未接入广播门禁 | P3 | ⏳ 待开始 | `storage_cow_policy` |
| `AUD-P3-4` | 常量分散无共享源 | P3 | ⏳ 待开始 | Python / Rust / pool_manager |
| `AUD-P3-5` | Manager authority 与 legacy 语义耦合隐式 | P3 | ⏳ 待开始 | `manager_v4_reviews` 为空 |

### 池深专项发现（`DEP-*`）

| 编号 | 一句话结论 | 优先级 | 状态 | 实测证据 |
|---|---|---|---|---|
| `DEP-P0-1` | TVL 与真实流动性脱钩 | P0 | ✅ 已完成 | 真实储备 TVL + 坏读数清零；~~`corr>0.7`~~ 作废（实测 +0.09） |
| `DEP-P0-2` | ~~16 个候选池中 4 个 `li<0.11` 是伪深池~~ | P0 | ✅ 已完成（**原方案证伪**） | 4 池真实储备 $97.8K–$4.96M，本就是真深池；`li=0.02854` 承载 24 闭环，**不得排除** |
| `DEP-P0-3` | Python healer 无 TVL 上界，`1e43` 可伪装成深池 | P0 | ✅ 已完成 | 全库 1287 条 `TVL > 1e9`；候选集 `badReadingCount=0` |
| `DEP-P1-1` | 单边定价：纯 STOCK 池 TVL=0 → `unpriced` | P1 | ✅ 已完成 | 双边均需有价才产出 TVL，否则 `unpriced` |
| `DEP-P1-2` | ETH 价默认 `$3000` 硬编码 | P1 | ✅ 已完成 | `binance.us → kraken → coingecko`；区间 `[500,20000]`、新鲜度 ≤900s |
| `DEP-P1-3` | 深度判据不测可成交容量 | P1 | ✅ 已完成 | capacity 主判据，**ε=10 bps**；`MIN_CANDIDATE_CAPACITY_USD=10_000` |
| `DEP-P1-4` | 12 个 ETH/股票池无股票侧价格来源 → `unpriced` | P1 | ⏳ **待开始（S1.5 后新登记）** | 12 池 `unpriced`，58 条闭环跳仍引用 |
| `DEP-P2-1` | 三层深度阈值互不一致 | P2 | ✅ 已完成 | `DEPTH_BAND_*` 同源常量 + `>=` 边界 |
| `DEP-P2-2` | 闭环三跳深度未取最小值 | P2 | ⏳ 待开始 | 单跳深不代表闭环可执行 |
| `DEP-P3-1` | `balances` 缺 `token0/token1` → `maxProbeAmountRaw` 恒 64 tokens | P1 | ⏳ 待开始 | 生产 `balances_json` 全为 `'{}'`；`ladderIncomplete=1113` |
| `DEP-P3-2` | 股票侧价格来源契约未定义 | P1 | ⏳ 待开始 | `token_stocks` 194 admitted，但池深定价只覆盖 ETH/USDG |

### 因池深调查而作废/修正的既有结论（持续有效）

| 原结论 | 状态 | 说明 |
|---|---|---|
| "池深市场 `market_depth_exhausted`，无可替换池，需下架 token 对" | **作废** | 建立在错误 TVL 上；实测 4 个 `li<0.11` 池为真深池 |
| "最佳交易量极小 = 池深不足" | **方向对，归因错** | 实为**探测阶梯从未展开**（`probeRecords` 长度全为 1）+ `balances` 缺 `token0/token1` 导致 `maxProbeAmountRaw` 回退为 64 tokens 固定上限；本轮以 `best_amount_ladder_incomplete` fail-closed 止血，根因待 `DEP-P3-1` |
| "`market_depth_exhausted` 可作为池子替换决策依据" | **作废** | `market_depth_exhausted` 与 `no_depth_qualified_pool_exists` 均不成立 |
| "`corr(log10 li, log10 TVL) > 0.7` 作为深度修复验收指标" | **作废** | 实测仅 +0.09；改为**真实储备排名一致性 + 坏读数清零** |
| "排除 `liquidity_index < 1` 的池" | **作废** | 会误删 $1.28M–$4.96M 真深池并打断 24 条闭环；准入改用 capacity 主判据 |
| "候选集含 4 个伪深池" | **证伪** | 不存在伪深池，那 4 个池是真深池 |
