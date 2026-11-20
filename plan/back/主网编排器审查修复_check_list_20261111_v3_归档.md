# 主网编排器审查修复 Check List

更新时间：2026-11-11（S1 → S2.5''' 全量归档后重整）
适用范围：`scripts/mainnet_arbitrage_healer.py`、`flashloan/rs-src/src/`、
`flashloan/src/dex/runtime/`、`flashloan/src/core/db/storage_cow_policy.py`、
`flashloan/tools/pool_manager/`、`flashloan/src/dex/intent_trade/chains/robinhood/`
伴生清单：`logs/plan/未完成任务清单_20261111.md`
执行计划：`logs/plan/开发计划_20261111.md`（v2）
已完成归档：`logs/plan/back/已完成任务归档_20261111_S1至S2_5ppp_总归档.md`
清单归档：`logs/plan/back/主网编排器审查修复_check_list_20261111_v1_归档.md`
审查依据：生产库 `flashloan/db/local_store_4663.sqlite3`（3345 MB）实测验证

本文件是可执行的验收清单，每项必须回填"完成标准"才能勾选。

> **本清单只保留未完成项。** 阶段 1（S1 口径修正）、阶段 1.5（S1.5 池深根因修复）、
> 阶段 2（S2 门禁补强）、阶段 2.5'（池深/容量去 USD 化）、阶段 2.5''（剩余代码项收口）、
> 阶段 2.5'''（部署门禁 + Rust 容量饱和修复）**均已于 2026-11-11 完成实施并通过回归**，
> 已移入 `back/已完成任务归档_20261111_S1至S2_5ppp_总归档.md`。
>
> **代码侧已无剩余项。** 以下每一项都需要**显式授权的生产写入**、
> **生产停机窗口**或**外部依赖修复**。
>
> **已作废的判据（不得再作为验收依据）**：
> - `corr(log10 li, log10 TVL) > 0.7` —— 实测仅 +0.09，`li` 不是深度信号；
> - 排除 `liquidity_index < 1` 的池 —— 会误删 $97.8K–$4.96M 真深池并打断 24 条闭环；
> - `market_depth_exhausted` / `no_depth_qualified_pool_exists` —— 均不成立；
> - ε = 0.5% —— 已统一为 **10 bps**（复用 `DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS`）；
> - **ETH / 股票价格源参与执行判断（`DEP-P1-4`、`DEP-P3-2`）** —— 已作废：
>   这些价格在套利环境中无法体现交易价值；`estimated_tvl_usd` 降级为
>   **排序 tie-breaker 与人工观测**，不再作任何准入门槛；
> - **以 `estimated_tvl_usd` 或 `depth_score_usd` 作准入门槛** —— 已移除，
>   主判据改为 `capacityRaw`。
>
> **保留约束**：在真实储备/容量数据全面铺开前，**不得依据 TVL 结论下架任何 token 对**。
>
> 本轮未执行生产写入、服务重启、闭环重建、authority 切换与真实广播；
> 守护器与 `run.py`（PID `30304`）未停止、未重启。

---

## 阶段 2 收尾：生产侧验收项（需生产写入权限）

### 2.1 `AUD-P0-2` 漂移消除验证

- [ ] 重建后 `manager_v4_closed_loop_library_state.pool_scope_checksum ==
      pool_scope_config.materialized_checksum`
- [ ] 重建后 `manager_market_observation_library_state.pool_scope_checksum` 一致
- [ ] 重建后 `manager_dex_candidate_library_state.pool_scope_checksum` 一致
- [ ] 生产报告不再出现 `code=pool_scope_drift`
- [ ] `poolScopeDrift.missingPoolHopCount` = **0**
- [ ] `poolScopeDrift.affectedLoops` = **0**

### 2.2 `AUD-P1-1` 修复链闭环验证

- [ ] 池修复一轮后闭环层能自动恢复为 valid
- [ ] `repair()` 连续两轮均能走到闭环重建步骤（`changedRows` 非 0）

---

## 阶段 2.5'：生产铺开验收（需生产写入权限）

### 2.5'.1 schema 与容量快照

- [ ] `pool-manager ensure-depth-schema --apply` 执行成功
- [ ] 快照表出现 `capacity_raw` 等容量列
- [ ] `pool-manager depth --apply` 对 **16 个候选池**全部成功取回链上状态
- [ ] `capacityRowCount > 0`
- [ ] `sqrtPriceX96` / `tick` / `liquidity` 均非空
- [ ] `priceImpactBps` 落在合理区间（**不再恒为 0**）
- [ ] 容量值**有限**（不得出现 `u128::MAX` 饱和哨兵）
- [ ] `depthSnapshot.stale == false`（`ageSeconds` < `maxAgeSeconds=900`）
- [ ] `depthBasis` 由 `estimated_tvl_usd_legacy` 切换为链上容量口径

### 2.5'.2 `balances` 链路生产验收

- [ ] 生产 `balances_json` 非空率 **> 0**
- [ ] `balances` 含 `token0` / `token1` / `capacityToken0` / `capacityToken1`
- [ ] `_liquidity_cap_raw()` 返回**非 `None`**
- [ ] `maxProbeAmountRaw` **不再回退 64 tokens**
- [ ] 复核 `probeRecords` 长度 **≥ 2**（阶梯展开）
- [ ] `ladderIncomplete` **归零**
- [ ] `best_amount_ladder_incomplete` 安全网**保持武装**（不得放宽）

### 2.5'.3 准入判据生产验收

- [ ] `dex_candidate_pools` 出现非空 `capacity_raw`
- [ ] 低 TVL 但 capacity 达标的池**保留**在候选集
- [ ] capacity 为 0 的池**排除**
- [ ] 候选集唯一池数仍为 **16**
- [ ] ETH/USDG `li=0.02854432` 池（承载 24 条闭环跳）**仍在候选集内**
- [ ] 全快照表坏读数（`TVL > 1e9`）**不进入** `depth --apply` 写入结果

### 2.5'.4 路径级 min capacity 门禁生产验收

- [ ] `pathCapacity.available == true`
- [ ] `pathCapacity.reason` 不再为 `capacity_schema_missing`
- [ ] 重建闭环后 `path_capacity_below_minimum:<pool>` 命中符合预期
- [ ] healer 审计理由串出现 `path_capacity_below_minimum` 且 `gateEnforced=true`
- [ ] **未测量跳不阻断整环**（覆盖缺口语义保持）

### 2.5'.5 容量实测门禁启用

- [ ] 铺开且 `ladderIncomplete` 归零后，启用
      `DEX_V4_CAPACITY_GATE_REQUIRE_MEASURED=1`
- [ ] 启用后 `capacityGate.verdict=unknown` 由「不阻断」升级为「阻断」
- [ ] 升级后 `capacity_insufficient` 出现在 `_PREFLIGHT_BLOCKED_AUTO_REASONS` 路径

---

## 阶段 2.5'''：遗留项

### 2.5'''.1 `DEP-P1-9` 链配置失效 RPC 端点（P1）✅ 根因已定位，修复未做

- [ ] 从 `flashloan/src/chains/robinhood/chain.json` 的 `rpc.fallbacks`
      **移除或修复** `rpc.ordofi.network`（当前 DNS 不可解析）
- [ ] `pool-manager depth --dry-run` 对 16 候选池**全部成功**返回，
      无反复超时、无孤儿进程
- [ ] 多 URL 轮询与失败切换行为符合预期（可注入失败端点验证）
- [ ] **本项是阶段 2.5' 生产铺开的前置**，未完成前不得开启生产链

### 2.5'''.2 `AUD-P1-8` `manager-api.exe` 重新部署（P2，阻塞）

- [ ] 在允许重启常驻服务时重建
      `flashloan/tools/target/debug/manager-api.exe`
- [ ] 重建后版本与源码一致（命令集探测通过）
- [ ] 服务重启后 PID 与 authority 记录完整

### 2.5'''.3 `DEP-P2-9` 工具二进制部署一致性 CI（P2）

- [ ] 在构建流程中校验 `flashloan/tools/target/debug/*.exe` 与源码的
      **构建时间 / 命令集**一致性
- [ ] 不一致时**构建即失败**（不得产出可运行的陈旧二进制）
- [ ] 覆盖 `pool-manager` 与 `manager-api` 两个产物

### 2.5'''.4 `DEP-P3-8` 容量数值的历史重算（P2）

- [ ] 核查生产库中是否存在饱和缺陷期间写入的容量值
- [ ] 若存在，重算并复核（重算范围与时间戳留痕）
- [ ] 重算后跨语言一致性测试仍通过

---

## 阶段 3：闭环重建（需停机窗口）

目标：消除 318 跳中 38 跳失联（涉及 38 条闭环）的上游脱节。
**此阶段需要停止运行中的守护器与 `run.py`。**

### 3.1 修复前准备

- [ ] 备份 `flashloan/db/local_store_4663.sqlite3` 到 `logs/plan/runtime_evidence/backups/`
      （已有备份 `local_store_4663_pre_pool_depth_20261111T094829Z.sqlite3`）
- [ ] 停止守护器 `supervise` 会话与 `run.py`
- [ ] 记录重建前基线：候选池 47 行 / 16 唯一、闭环 106 / hop 318、四方 checksum
- [ ] **前置条件**：阶段 2.5' 生产铺开完成（否则命中的是无 capacity 证据的池，验收无效）
- [ ] **前置条件**：阶段 2.5''' 的 `DEP-P1-9` 已修复（否则刷新挂起）

### 3.2 重建执行

- [ ] `pool-manager cleanup`（仅清候选，不删闭环）
- [ ] `pool-manager refresh-key` 重算候选与深度标签
- [ ] `materialize-scope --apply`
- [ ] `composite-manager materialize-arbitrage --apply`
- [ ] 确认 `materialize-arbitrage` 的 `changedRows` **非 0**（当前为 0，说明未真正重建）

### 3.3 重建后验收

- [ ] 106 条闭环的 `pool_ids` **100%** 命中当前候选集
- [ ] 四方 `pool_scope_checksum` 完全一致
- [ ] `consumers_need_rebuild = 0` 且**真实验证通过**（不再依赖该字段写入后即信）
- [ ] `AUD-P3-1`：`composite-manager` 读**真实表**而非 state 计数
- [ ] 运行态分布中 `preflight_failed` / `execution_failed` 明显下降
- [ ] 使用一个新的测试脚本独立验证"候选集 ⊆ 闭环集"完整性（不得再出现部分失联）
- [ ] 重启 `run.py` 与守护器，观察一个完整交易周期

### 3.4 回滚准备

- [ ] 备份可回滚；回滚验证脚本已保存并实测通过
- [ ] 重建失败时能从备份完整恢复

---

## 阶段 4：共享配置与总体回归

### 4.1 共享深度配置（`AUD-P3-4-R`）—— 跨语言单一真源

> 深度主判据已去 USD 化，下列 `*_TVL_USD` 常量
> **仅作参考与 tie-breaker**，不再作准入门槛；共享源必须反映这一点。

- [x] **Python 侧共享配置源已建立**：
      `flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_capacity.py`
  - [x] `DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS` = 10（ε，唯一真源）
  - [x] `DEPTH_BASIS` = `onchain_tick_liquidity`
  - [x] `DEPTH_BAND_DEEP_CAPACITY` = 10000 / `MEDIUM` = 1000 / `SHALLOW` = 100
- [x] **Rust ↔ Python 跨语言关键常量已对齐**：
      Rust `MIN_CANDIDATE_CAPACITY_RAW` 由 `1` → `100`（共享 shallow 边界）；
      healer `MIN_POOL_CAPACITY_RAW` 改为**直接 import** Python 共享源；
      双侧各有一条单测锁定该边界
- [ ] **跨语言共享配置文件**（`flashloan/config/depth_policy.json`），包含：
    - [ ] `PRICE_IMPACT_LIMIT_BPS` = 10
        （Rust `CANDIDATE_PRICE_IMPACT_LIMIT_BPS` 须与
        `DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS` 同源）
    - [ ] `DEPTH_BAND_DEEP_CAPACITY` = 10000 / `MEDIUM` = 1000 / `SHALLOW` = 100
    - [ ] `MIN_CANDIDATE_CAPACITY_RAW`（Rust）与
        `MIN_POOL_CAPACITY_RAW`（healer）—— **两侧当前不一致，需对齐**
    - [ ] `GAS_ESTIMATE_ETH` = 6.2e-06（当前仅 Python 有）
    - [ ] `MIN_CANDIDATE_TVL_USD_REFERENCE` / `MAX_CANDIDATE_TVL_USD_REFERENCE`
        —— **标注为"仅参考，非门槛"**
- [ ] Python 与 Rust 同源读取
- [ ] 启动时断言两侧一致（不一致直接启动失败）
- [ ] 验证：修改共享配置后，编排器与 pool-manager 同步变化
- [ ] 扩展现有跨语言测试到新配置文件（`AUD-P3-4-S`）

### 4.2 池实际上限复核（承接 `DEP-P0-1`/`DEP-P1-3`）—— 已去 USD 化

- [x] 重算并重新归档 `market_depth_exhausted` 结论 —— 结论**作废**
- [x] `li<1` 排除规则作废 —— 4 个低 `li` 候选池全部保留，承载 28 条闭环跳
- [x] 准入门槛去 USD 化 —— `estimated_tvl_usd` 与 `depth_score_usd` 门槛均已移除，
      主判据为 `capacityRaw`
- [x] `depth_score_usd` 列语义重定义为 token 计价 capacity
- [ ] 用**容量口径**（而非 TVL）重新推导分带阈值与准入下限；
      以 `capacityRaw` 与 gas 覆盖能力为准，不再以 `$100K` 下限为准
- [ ] 确认 capacity 分带与实际最佳交易量的关系至少一次量化复核
      （依赖生产铺开后 `ladderIncomplete` 归零）

### 4.3 容量与并发口径被动

- [ ] 验证 `max_workers=16` 在生产环境生效（启动时记录实际并发）
- [ ] 澄清 `--candidate-limit 1` 与 worker 并发的区别：
      报告**「扫描/请求数」与「worker 并发数」分开呈现**
- [ ] 确认复核与广播的异步调用没有把主线程工作线程数压到 1
      （"限制为 1" 实为**每轮主动 repair 请求数**，不是并发上限）

### 4.4 长期文档回填

- [ ] `docs/主网自愈编排器/05_数据口径与统计.md` 补上：
      capacity 口径、快照新鲜度阈值、`positiveEstimateOnly` 口径、
      `estimated_tvl_usd` 仅作 tie-breaker
- [ ] `docs/主网自愈编排器/04_验收门禁与故障处置.md` 补上：
      工具版本门禁（`pool_manager_commands_missing`）、
      快照陈旧阻断（`pool_depth_snapshot_stale`）、
      Rust 容量饱和历史教训（`u128::MAX` 哨兵）
- [ ] `docs/主网自愈编排器/06_独立守护工具.md` 补上：
      `L0 tooling` 层与 `stopped="tool_deployment_gap"` 处置流程

### 4.5 编排器审查剩余项（`AUD-*`）

- [ ] `AUD-P2-5`：`pool-manager ensure-indexes` 补建单列
      `selected_profit_source` 索引（或含该列的复合索引），
      并在 `EXPLAIN` 中确认 `positiveEstimateOnly` 统计走索引
- [ ] `AUD-P2-8`：epoch 读取失败时显式报错或标 `epochUnknown=true`，
      **不得静默回退 1970-01-01**；单测覆盖失败分支
- [ ] `AUD-P3-2`：bestAmount 与池深门禁改为**父子链**，
      接入路径级 min capacity 数据源
- [ ] `AUD-P3-5`：`manager_v4_reviews` 物化后 legacy 表只读降级为交叉校验，
      authority 切换前不双写

---

## 阶段 5：观察与移交

### 5.1 三个连续 30 分钟主网观察窗口（`P0-6.1`）

- [ ] 窗口 1：同 PID / authority / schema / scope，无新增 identity 异常
- [ ] 窗口 2：同上，PnL 缺值为 0
- [ ] 窗口 3：同上，广播无悬挂 pending
- [ ] 三个窗口连续，无重启插入

### 5.2 长期盈利统计移交（`P2-1`）

- [ ] 按 token / route / window 统计 expected / realized / gas / success / failed
- [ ] `pnl_*.jsonl` / `pnl_*.log` 持续产出且可被统计器直接消费
- [ ] 估算正收益与真实利润**分列**，不混算

### 5.3 长期治理（`P3-*`）

- [ ] `P3-1` stdout/stderr 与 JSONL 日志轮转、保留和恢复验证
- [ ] `P3-3` DEX 历史命名兼容迁移，禁止双写和改写历史证据
- [ ] `P3-4` UI refresh 与 worker scan 周期独立变更验证

---

## 附：已完成项对照索引（**不在本清单勾选**）

> 完整逐条证据见 `back/已完成任务归档_20261111_S1至S2_5ppp_总归档.md` §2。

| 编号 | 一句话结论 | 状态 | 实测证据 |
|---|---|---|---|
| `AUD-P0-1` | 池审查重复计数 3 倍 | ✅ | 47 行 / 16 唯一 `pool_id` |
| `AUD-P0-2` | 候选集与闭环集 checksum 不一致（**检出**部分完成，**消除**待生产） | ✅ | `5434a25c` vs `8ceb9e02`；38 跳 / 38 环 |
| `AUD-P0-3` | `eligible_pool_count=114` 与实际不符 | ✅ | 真实表 `rows=114 unique=93 pairs=42` |
| `AUD-P0-4` | rate-delta 估算当利润，`nonPositive` 标签错 | ✅ | `positiveEstimateOnly=76`；`nonPositive=1185` |
| `AUD-P0-6` | 部署二进制比源码旧 → 容量铺设静默 no-op | ✅ | 命令集探测门禁 + `tool_deployment_gap` |
| `AUD-P1-1` | 闭环重建被修复前池状态卡死 | ✅ | 触发条件改为 `consumers_need_rebuild != 0` |
| `AUD-P1-2` | 复核缺失三类混为一谱 | ✅ | 三计数互斥；`ladderIncomplete` 计入 `missingBestAmount` |
| `AUD-P1-3` | `too_small` 变量永远不参与判定 | ✅ | `insufficient = below_gas + too_small` |
| `AUD-P1-4` | 编排器无 TVL 上界 | ✅ | 上界 `<= 1e9` + `badReadingCount` |
| `AUD-P1-5` | 深度标签与独立阈值矛盾 | ✅ | 分带常量同源 + `>=` 边界 |
| `AUD-P1-6` | 修复链遇工具失败仍继续并报告成功 | ✅ | 四步各自即停 + `stopped="tool_failure"` |
| `AUD-P2-1` | `COALESCE` 剥离 REAL affinity | ✅ | 统一 `CAST(... AS REAL)` |
| `AUD-P2-2` | Rust 残留入口默认 `max_workers=3` | ✅ | 改为 16；全仓三处一致 |
| `AUD-P2-3` | `bestAmount` 标记重复写入两次 | ✅ | 仅保留 payload 构建处一次写入 |
| `AUD-P2-4` | 守护循环重复 `time.sleep` | ✅ | 周期由 `2×interval` 恢复为 `interval` |
| `AUD-P2-6` | stdout 截断可丢失关键证据 | ✅ | `run_tool` 新增 `payload` + `summary` |
| `AUD-P2-7` | `upstream_invalid` 掩盖具体原因 | ✅ | `invalidLayers` + 逐层 `code` + `blockerCodes` |
| `AUD-P2-8` | Rust 容量在 `u128::MAX` 处饱和 | ✅ | 512 位 `mul_div` + `reserve_ceiling()` 封顶 |
| `AUD-P3-2` | bestAmount 与池深门禁并列非父子 | ✅ | 父子回溯链 + 三类主因 |
| `AUD-P3-3` | GAS 拦截未接入广播门禁 | ✅ | `capacity_insufficient` 已入门禁集合 |
| `AUD-P3-4` | 常量分散无共享源 | ✅ 关键项 | Python + Rust 双源 + 准入下限对齐 |
| `AUD-P3-6` | Rust 工具只读环境变量取 RPC | ✅ | `chain.rs` 链配置回退 |
| `AUD-P4-6` | Python/Rust 容量无跨语言一致性约束 | ✅ | 相对误差 < `1e-6` |
| `DEP-P0-1` | TVL 与真实流动性脱钩 | ✅ | 真实储备 TVL + 坏读数清零 |
| `DEP-P0-2` | ~~4 个 `li<0.11` 是伪深池~~ | ✅ 原方案证伪 | 真实储备 $97.8K–$4.96M；不得排除 |
| `DEP-P0-3` | Python healer 无 TVL 上界 | ✅ | 1287 条坏读数已排除 |
| `DEP-P1-1` | 单边定价 → `unpriced` | ✅ | 双边均需有价 |
| `DEP-P1-2` | ETH 价默认 `$3000` 硬编码 | ✅ 仅作参考 | 不参与执行判断 |
| `DEP-P1-3` | 深度判据不测可成交容量 | ✅ | capacity 主判据，ε=10 bps，token 计价 |
| `DEP-P1-4` | 12 个 ETH/股票池无股票侧价格来源 | 🚫 **作废** | USD 价格不体现套利交易价值 |
| `DEP-P2-1` | 三层深度阈值互不一致 | ✅ | 同源常量 + `>=` 边界 |
| `DEP-P2-2` | 闭环三跳深度未取最小值 | ✅ | 路径最小容量判定 + 跳过原因 |
| `DEP-P3-1` | `balances` 缺 `token0/token1` | ✅ 代码 / ⏳ 生产 | 代码已修；生产待铺开 |
| `DEP-P3-2` | 股票侧价格来源契约未定义 | 🚫 **作废** | 同 `DEP-P1-4` |
