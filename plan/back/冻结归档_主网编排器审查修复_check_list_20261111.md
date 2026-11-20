# 主网编排器审查修复 Check List

> **已冻结（2026-11-13）**：活动版本已迁移到 `主网编排器审查修复_check_list_20261113.md`。本文件仅作历史审计，不再更新状态或勾选。


**版本**：v7（2026-11-12）
**配套清单**：`未完成任务清单_20261111.md`（v7）
**配套计划**：`开发计划_20261111.md`（v7）

> 本清单**只列未完成项**，可勾选。已完成项一律见 `back/`。

> **标记约定**：`[~]`=已在**生产库副本**上演练通过（生产未写入）；`[ ]`=未完成。
> `[x]`=已完成。**除个别刚完成、尚未写入 `back/` 的条目外**，`[x]` 均已
> 转入归档并从本清单移除（见 `back/`）。
>
> **v8 归档**：`back/已完成任务归档_20261112_v8基线校正.md`
> （`D7` / healer 漂移审计 / 副本库重放 / `R-11` / `R-12` /
> `VAL-4-12` / `VAL-4-13` / `ARCH-1a` / 二进制指纹）
>
> **当前生产状态**（2026-11-12 healer `--mode check`）：
> `L0 tooling valid=True`、`sourceSync verified=True`
> ⇒ **`tooling_deployment_gap` 已消除**。
> 剩余 blocker：`pools:pool_scope_drift`（**W6**）、
> `reviews:best_amount_missing`（**D4 数据修复 + W7 重建**）、
> `broadcast:layer_invalid`（**W8**）；
> `secondaryBlockerCodes=pools:pool_depth_snapshot_stale`。

---

## 阶段 VAL：代币 USDG 粗估值（**不涉及生产写入**）

> 归档：`back/已完成任务归档_20261112_VAL1代币USDG估值.md`
> 证据：`logs/plan/runtime_evidence/reports/val1_usdg_token_valuation_20261112.json`

### VAL-2 实盘价值覆盖（**Key ✅ 已就位，待全量跑**）

- [x] 申请 Uniswap Trading API Key（developers.uniswap.org）并配置环境变量
      → `UNISWAP_TRADING_API_KEY` 已写入 `flashloan/app_runtime/.env`（gitignored）
      → 实测：链 4663 报价可用，60 只样本 `route_found=45`（75%）
- [ ] **VAL-4-13 先行**：404 + `NoRouteFoundError` 归入 `no_route`（不修则结果不可用）
- [ ] 跑 194 只股票代币全量估值，产出价值覆盖报告
      （`route_found` / `no_route` / 未知 各占多少）
- [ ] 未知项（`api_error`）单独归因，**不得**混入"无价值"

### VAL-3 估值结果落地（**已定口径，见 VAL-4**）

> 估值结果的使用边界已定（**不进深度/容量/广播门禁**，只管**代币库成员关系**），
> 且**股票代币准入必须绑定 USDG 可兑换性**（`R-12`），见 `back/已完成任务归档_20261112_VAL4_USDG经济门禁.md`

- [x] 决定是否落入 `manager` 代币元数据层（VAL-4-8 落库时一并决定）—— **决定：落独立证据表 `token_usdg_depth_measurements`**，不落 `manager` 代币元数据层。理由：该表回答的是"某个池能承载多少 USDG"，与代币元数据（symbol/decimals/source）不同源、不同生命周期；估值刷新不应触碰池观测，池刷新也不应凭空造出估值
- [ ] 估值快照**新鲜度**策略（TTL）与失效口径定案
- [ ] "USDG 无路径"是否需要**本地兜底**判定（不依赖外部 API）定案

---

## 阶段 VAL-4：USDG 计价的经济准入门禁（**不涉及生产写入**）

> **裁定（2026-11-12）**：资金池都可以用 USDG 衡量。
> - **股票池（`R-12`，关键约束）**：**不能与 USDG 兑换**的股票代币
>   **不得进入股票代币表**，因此**也不会进入资金池代币**；
>   后续动态刷新发现有 USDG 兑换路径后**再自动加回**。
> - **核心代币 / 测试代币**：**无此约束**。
> - **扩展池**：**不要求自身估值**，但中间代币的池子**必须能承载资金池的量**；
>   连最低初始交易量（1 token 或 10 USDG）都承接不了 → **灰尘池子，排除**。
> - 中间池子**不是无标准**，标准是**承载能力**而非自身价值。
> - 扩展代币与 USDG 的关系**传递成立**：经由股票/核心代币相连。
> - **`R-11`（USDG ≡ USD）——USDG 取代 USD 的计价作用**：
>   大量代币**没有兑换渠道**，任何外部 USD 价都取不到；但 **USDG 在绝大多数情况下有兑换**，
>   因此以 USD 表述的经济量（经济下限、承载能力、名义价值、Gas 成本折算）
>   一律改用**同额 USDG** 表述，**无需外部 fiat 价格源**。
>   **边界**：USDG 只取代**计价作用**；**不**把 USD/USG 换算引入深度/容量判据。

### VAL-4-A｜门禁代码（**已完成并归档**）

> 归档：`back/已完成任务归档_20261112_VAL4_USDG经济门禁.md`
> 证据：`logs/plan/runtime_evidence/reports/val4_usdg_economic_gate_dry_run_20261112.json`

### VAL-4-B｜实盘证据与动态刷新（**未完成**）

- [x] `VAL-4-13` **404 + `NoRouteFoundError` → `no_route`**（其余非 200 → `api_error`）
      —— ✅ **已完成**（`flashloan/tools/token_manager/src/valuation.rs`：
      `no_route_detail_from_body()` + `explicit_no_route_detail()`
      解析 404 响应体中的 `errorCode: NoRouteFoundError` ⇒ `QuoteOutcome::NoRoute`
      ⇒ `ValuationVerdict::NoRoute`）
- [x] `VAL-4-13` 修完后，`api_error` 占比 = **0**（Key 可达时不应有传输错误）—— 194 只实测 `unknownStockCount=0`，全部为 `route_found` / `no_route` 两类结论
- [x] `VAL-4-7` 194 只股票代币全部有 USDG 结论（**Key ✅ 已就位**，**VAL-4-13 已解除前置**）
      —— ✅ **已完成并落库**（2026-11-12）：报告 194 = `route_found` 159 + `no_route` 35 + 未知 0；
      **已落为规范产物** —— 迁移 `20261112_007_usdg_valuation_schema` 建表 `token_usdg_valuations`，
      新增 `token-manager land-valuations` 命令（幂等 `ON CONFLICT DO UPDATE`），
      副本库实测 `written=194`（159 valued / 35 unvaluable / 0 unknown）、
      `no_route` 行 `usdg_amount_raw` 全为 `NULL`（**绝不写 0**）、194 个地址互不重复
- [x] `VAL-4-8` `token_usdg_depth_measurements` 表由 `pool-manager` 写入（2026-11-12：表已定义 + 写入路径打通 + 3 缺陷修复 + 94 池实测 61 行；`typeof` 全 `integer`、零地址行 0）
- [x] `VAL-4-9` 94 个候选池按 USDG 承载能力分档（可承载 / 灰尘 / 未测量）—— ✅ **已完成并可复现**（2026-11-12：分带改为直读持久化存储 `--valuation-database` / `--depth-database`；副本库实测 **94/94 全部有归属、26 可承载 / 68 dust / 0 未测量**，连跑两次逐字段一致；顺带修复"基础资产估值报告无法落库"缺陷。详见清单 §7.7.5）
- [x] `VAL-4-10` **动态刷新闭环**：刷新 → 估值 → 门禁 → 物化 → 重新纳入 —— ✅ **已完成**（2026-11-12：新增编排器 `scripts/refresh_robinhood_token_libraries.py`；副本库实测 VTI 由 `blocked/no_usdg_valuation` 自动回到 `admitted`，125→126 admitted / 69→68 blocked；修复 3 个真实缺陷，含"一次探针故障会摧毁全部已知估值"。详见清单 §7.7.6）
- [x] `VAL-4-11` 门禁与 `capacityGate` 的边界写入 `docs/主网自愈编排器/05_数据口径与统计.md` —— ✅ **已完成**（2026-11-12：§10「经济门禁与 `capacityGate` 的边界」）
- [x] `VAL-4-12` 经济下限阈值并入 `depth_policy.json` 单一真源（对齐 `W3`）
      —— ✅ **已完成**（`depth_policy.json` → `economicPolicy`；
      Rust `assert_economic_policy_matches_file()` 启动时断言，
      Python 读同一文件 ⇒ 三处不可能各持一份）

### VAL-4-B 验收

- [x] `measuredStockCount + unknownStockCount = 194` —— 实测 `194 + 0 = 194`
- [x] `unknownStockCount` **不并入** `noRouteCount` —— 实测 `unknownStockCount=0`，`unvaluableStockCount=35`（`no_route`）独立统计
- [x] 股票池物化中**无估值证据的代币 = 0** —— 实测 `pendingStockCount=0`；69 只被排除（35 `no_usdg_valuation` + 34 低于 10 USDG 下限）
- [x] 扩展池 `InsufficientPoolDepth` 代币**已全部移出** —— 实测 `excludedExtensionCount=11`（`insufficient_pool_depth`）；`measuredExtensionCount=61` / `dustExtensionCount=37`
- [x] 新获得 USDG 估值的代币**自动进入股票池**（VAL-4-10 回归）—— ✅ 实测通过（副本库：VTI `blocked` → `admitted`，`excludedStockCount` 69 → 68）
- [x] **`R-12`**：股票代币表中每个代币都有 USDG 兑换路径
      —— ✅ **代码已落地**（`economic_gate.rs::library_requires_usdg_exchange()`；
      `no_route` ⇒ `NoUsdgValuation` ⇒ **在 `write_library` 之前即被排除**）；
      ✅ **落库已跑通**（生产库副本 + 持久估值库 + 61 条深度行：
      `excludedStockCount=69`、`pendingStockCount=0`）；
      ✅ **表内状态已修正**（2026-11-12，见 §7.7.4：门禁此前只在内存里移除成员关系，
      **库里的旧行仍停在 `admitted`**——本轮改为写入 `blocked` 撤回行；
      实测 `token_stocks` = **125 admitted + 69 blocked**，
      35 只 `no_route` 全部 `blocked` 且带 `no exchange path to USDG` 原因）；
      ⏳ **生产物化未跑**（待 `VAL-4-10` 刷新闭环 + 写入授权）
- [x] **`R-12`**：核心/测试代币表**不受** USDG 可兑换性约束
      —— ✅ **代码已落地**（`library_requires_usdg_exchange()` 仅 `Stocks` 为真）
- [x] **`R-12`**：扩展代币按**承载能力**判定，而非自身估值
      —— ✅ **代码已落地**（扩展池走深度/承载能力路径，
      与 USDG 的关系经股票/核心代币**传递成立**）
- [x] **`R-12`**：唯一撤销依据是 `no_route`；估值偏低**不**触发下架
      —— ✅ **代码已落地**
- [x] **`R-11`**：`depth_policy.json` → `usdgPegPolicy` 生效，
      `pegRatio=1`、`deviationToleranceBps=0`、`allowedUses` / `forbiddenUses` 齐备
      —— ✅ **代码已落地**（Rust `assert_usdg_peg_policy_matches_file()` **启动时 fail-closed**；
      Python `load_usdg_peg_policy()` 同样校验）
- [x] **`R-11`**：深度判据仍为**池子自身代币计价**的链上价格冲击容量，
      **未**出现任何外部 fiat 价格源依赖（深度判据不引入计价换算）
      —— ✅ **代码已落地**（`forbiddenUses` 含 `depth_admission` / `capacity_admission`）
- [x] **`R-11`**：以 USD 表述的经济量（经济下限 / 承载能力 / 名义价值 / Gas 折算）
      **全部改用 USDG 表述**，且**未**引入任何外部 fiat 价格源
      —— ✅ **代码已落地**（`allowedUses` 含
      `usdg_replaces_usd_denomination` / `economic_quantity_denominated_in_usdg`；
      `economicPolicy` 两个下限均以 **raw USDG** 计）

---

## 阶段 ARCH：架构升级（ARCH-1 ~ ARCH-7）

> 归档：`back/池子管理工具设计方案_20261112_架构裁决归档.md`
> 详见 `未完成任务清单_20261111.md` §0.6.2

### ARCH-1 版本触发重算（P1，独立）

- [x] **版本变化触发即时重算**，替代 60s 死等
      （实测 `reviewAgeSeconds=66130`；healer 实测 `reviewAgeSeconds=119020`）
      —— ✅ **已完成**（2026-11-12）
- [x] 版本定义 = `(liquidity_block_number, sqrt_price_x96)`
      —— ✅ **已完成**（`flashloan/src/core/db/pool_state_version.py`）
- [x] **不得**用 TTL / `recent_swap_active` / `liquidity_read_at` 充当版本
      （TTL 会退化成计时器；`recent_swap_active` 生产 4669/4669 全为 NULL；
      `liquidity_read_at` 每次刷新都动，哪怕状态逐字节相同）
      —— ✅ **已完成**（三项均有专项测试锁定）
- [x] 指纹**与顺序无关**；池子出现或消失也算变化
      —— ✅ **已完成**
- [x] 触发**只入队不启动线程**；同 scope 重复变化只排一次
      —— ✅ **已完成**（`_enqueue_scan()`）
- [x] 扫描**成功**后才移动基线；失败**不**移动
      —— ✅ **已完成**
- [x] **生产路径核实**：`run.py` → `runtime.scanner.start()` → `run_forever()`
      ⇒ Python 扫描器**就是**生产扫描器，版本触发在生产路径上
      —— ✅ **已核实**（2026-11-12）
- [x] **触发器不得静默失效**：读失败必须报 `available=False` + `error`，
      **绝不**伪装成"状态未变化"
      —— ✅ **已完成**（修复了 `storage_chain_markets` 导入错误导致的
      **永久静默**；现用 `storage_v4_exchange`）
- [x] 版本检查**必须节流**（默认 5s，`DEX_POOL_STATE_VERSION_CHECK_INTERVAL_SECONDS`）
      —— ✅ **已完成**（生产快照表 4669 行，单次读 ~110ms）
- [x] 指纹体积可控（SHA-256，64 字符）
      —— ✅ **已完成**（原拼接串 502KB）
- [ ] 端到端时延实窗验证：版本变化 → 重算完成的实际耗时
      —— ⏳ **待下一次生产窗口**
- [x] **ARCH-1a** 推荐池主排序键定义：
      主键 `capacityBindingRaw DESC`（token 计价容量）、
      次键 `priceImpactBps ASC`、末键 `pool_id ASC`（确定性）
      —— ✅ **已完成**（2026-11-12，唯一真源
      `flashloan/src/core/db/pool_depth_keys.py`；
      `builders.py` / `direction_pool_score.py` 共用；
      **方向价分保留在容量键之下**作为 tie-break，以维持镜像方向语义）
- [x] 禁止 `estimated_tvl_usd` 作为主排序键（与深度无关）
      —— ✅ **已完成**（`pool_depth_sort_key()` 中**从不**出现该字段）
- [ ] 候选表 `is_first_hop_recommended` / `is_third_hop_recommended`
      按上述键重新生成
      —— ⏳ **代码已就绪，生产未生效**：生产快照表尚无
      `capacity_binding_raw` / `price_impact_bps` 列
      （由 **W6-1 `ensure-depth-schema --apply`** 铺设，需显式授权）。
      在此之前生产排序键退化为 `pool_id ASC`（**稳定但无信息**）

### ARCH-2b 链下模拟器接入生产（P2，依赖 ARCH-2）

- [x] **架构整改：逐笔比对下沉到 Rust 管理器**（2026-11-13）——
      `review_manager::replay_recorded_routes_with` 报告，
      `pool_manager::read_pool_parity_states` 读池状态，
      Python 仅通过 `RuntimeCore.execute_command("review_parity_replay")`
      转发，不再直读管理器表、不再在 Python 里做
      Uniswap 数学。
- [x] **缺陷修复（本轮）**：
      (1) 池快照的 `sqrt_price_x96` / `liquidity` 在 SQLite 里是 **TEXT**，
      旧读取端当 `i64` 读，每行静默失败 → 整条路由报
      "no live pool state"；(2) 链上负收益的 `0x` 前置负号丢失，
      回退被当成"无收益"；(3) 负向 `stateAgeSeconds`
      语义错误（池状态比复核时间还新）。
- [x] **测量完成（2026-11-13）**：副本库实测
      `routeCount=29` / `comparableCount=29` / `notComparableCount=0` /
      `deviationCount=29` / `explainedByLegacyFeeCount=15` /
      `unexplainedDeviationCount=14` / `reviewTimeStateCount=29` /
      `reviewTimeStateExplainedCount=15` / `gatePassed=false`，
      证据轮票 `logs/plan/runtime_evidence/reports/arch2b_parity_replay_20261113_rust_review_time_state.json`
      （修正前轮票 `arch2b_parity_replay_20261113_rust.json`，
      手续费修正轮票 `arch2b_parity_replay_20261113_rust_fee_fixed.json`）。
- [x] **闪电贷手续费口径归因与修正（2026-11-13）**：链下硬编码 **5 bps**，
      链上实为 **0 bps**（直接借自 V4 PoolManager，合约只要
      `amount + minProfit(1 wei)`）。已改为**从链上结算推导**有效手续费
      （`review_manager::effective_flash_fee_bps`），并新增
      `ParityAttribution::FlashFeeAssumption` 独立归因。
      Python 侧同步拆分 `CHAIN_FLASH_LOAN_FEE_BPS`（0.0，链上事实）与
      `DEFAULT_FLASH_LOAN_FEE_BPS`（5.0，预检余量）；**修复了
      `repayAmountRaw` 被虚增 0.05% 的生产缺陷**。
- [x] **复现时间池状态对齐（2026-11-13）**：原实现拿**当前快照**去比较
      **发事前**的记录，是比较两个市场，偏差不能归因于算术。现已改为
      **优先使用记录那一笔当时记下的池状态**（`review_manager::extract_review_time_pool_states`），
      当前快照仅作回退（仍通过 Pool Manager 公开 API 读取）。
      池深度指数口径逐条取证：`liquidityIndex × 1e18` 在 179 / 179 条记录上正确。
      归因分布：`review_time_state_explained=15` / `stale_local_state=9` /
      `unknown_no_chain_live_state=5`。
- [ ] **偏差必须为 0** —— 未达成（剩余 14 条均已归因，见下方），阻塞项见下方。
- [ ] 切换后 D4-d 的 `unit_profit` 改用链下精确口径
- [ ] **不得放宽** `best_amount_ladder_incomplete` fail-closed

#### ARCH-2b 测量口径裁定（2026-11-13，关键）

本轮测量发现了一个不能漏读的口径问题，它改变了“34 符合”
这个数字的含义：

| 链上收益来源 | 受测路由 | 一致 | 偏差 |
|---|---|---|---|
| `offline_v4_sqlite_route_snapshot` | 9 | 9 | 8 |
| `native_rust_contract_preview` | 10 | **0** | **10** |
| `native_contract_rate_delta_estimate` | 2 | 0 | 2 |

- `offline_v4_sqlite_route_snapshot` 表示**链上幂算读的也是本地
  SQLite 路由快照**——两边读的是同一份数据，一致是
  构造性的，不是独立证据。
- `native_rust_contract_preview` 才是真正独立的对比（链上预览 vs
  链下本地算本）：**10 条全部偏差，零匹配**。
- 因此旧测量的 "34 match / 20 mismatch" 不能当作“链下算本已被现场验证”的
  依据；真正能否证链下算本的 **12 条独立对比全部偏差**。
- 报告现在必须输出 `evidenceBreakdown`，把“共用本地快照”与
  “独立链上预览”分开计数，避免后续阅读者把循环一致当成验证。

#### ARCH-2b 剩余阻塞（2026-11-13 更新）

1. ~~**副本池快照过期**~~ → **已刷新（2026-11-13）**：
   `pool-manager depth --apply --depth-scope candidate` 在副本库上
   `poolCount=94 / writtenCount=94 / errors=[] / durationMs=19794 / truncated=false`（生产未写入）。
   刷新后复测仍为 29 条偏差，说明**偏差不在快照新旧上**。
   真正的口径缺口是**复现时间状态**（上方已修正）。
2. ~~**独立对比全部偏差**~~ → **已归因（2026-11-13，两次）**：
   第一次根因：**闪电贷手续费口径错误**（链下 5 bps vs 链上 0 bps）。
   第二次根因：**复现口径错误**（拿当前快照比较发事前的记录）。
   现已对齐到**记录那一笔当时的池状态**，15 条偏差屈服遗留 5 bps 后**精确相等**，
   `explainedByLegacyFeeCount` 由 9 升至 **15**。
3. **剩余 14 条均已归因，不是算术缺陷**：9 条 `stale_local_state`（链上用已移动的现场合约预览，
   已逐笔验证链上自身算术复现其记录收益且结算费用为 0 bps）；5 条
   `unknown_no_chain_live_state`（记录未落盘链上实时结算，属证据缺失）。
4. **仍不得关闭链侧预览**：链上兜底（ARCH-3）未就位，
   且链上预览语义本身尚未独立复核。

### ARCH-3 链上快照比对 + 降级数组（P3，依赖 ARCH-2）

- [ ] 合约改造：传入 `expectedSqrtPriceX96`，执行前比对当前价
- [ ] 偏离分档（主参数 / 降级参数 / revert）
- [ ] V4 使用 `sqrtPriceLimitX96`（**无** `amountOutMinimum`）

## 阶段 ARCH-4：内存池取代 `eth_call`（**分阶段；先并行对照**）

> 策略：**并行对照 → 长期一致 → 切换纯内存**。绝不先切换后验证。
> 详见 `开发计划_20261111.md` §10。前置：**ARCH-2b**。
>
> **差异处理（裁决，两步）**：
> 1. **先判 `eth_call` 可信性**：默认**可信**（链上权威，一般不出错）→
>    **按其结果执行**；仅有具体证据（超时/畸形/链上已变）才**阻断**。
> 2. **再修本地内存**：归不出因 → **标记异常 + 修正内存计算逻辑**。
>
> 内存池**永不阻断**执行；异常要**修到代码**，不要只**记一笔**、更不要**挡**。

### ARCH-4-A｜并行对照

- [ ] 链下计算双写：内存池 + `eth_call`，各带 `sourceTag`
- [ ] 逐笔差异报告落盘（`amountOut`/`amountIn`/`sqrtPriceX96`/`priceImpact`/`executable`）
- [ ] 差异**归因**机制就位（池状态版本/tick 边界/费用口径/舍入）
- [ ] 归不出因的差异 → **标记异常 + 修正本地内存计算逻辑**，**不阻断执行**（按 `eth_call` 结果继续）
- [ ] **`eth_call` 可信性判定**就位：产出 `eth_callTrust`（`trusted`/`untrusted`）
- [ ] `untrusted` 仅由 RPC 超时/响应畸形/链上已变更触发（**内存偏差不触发**）
- [ ] **异常台账**：差异字段/量级/首现时间/影响路径/归因状态/**已落地的修正内容**
- [ ] 异常台账条目都有**修正记录**（`unexplained → explained → fixed`）
- [ ] 每条异常都有**内存计算逻辑的修正落地**（改了哪段逻辑），且修正后**回归一致**
- [ ] **因内存偏差而阻断的次数 = 0**
- [ ] 全候选池 × 全 token 对 × 全方向 采集覆盖
- [ ] 采集**不劣化**主链路延迟与广播成功率
- [ ] **`executable` 不一致 = 0**（阶段 A 出口条件）
- [ ] `amountOut` 相对差 < 1e-9 或**可逐笔归因**
- [ ] 每条差异带 `poolStateVersion`

### ARCH-4-B｜影子旁路

- [ ] 内存池成为主计算源，`eth_call` 降为影子（只记录不阻塞）
- [ ] 影子差异率**连续 7 天为 0**
- [ ] 一键回切开关 + 回切路径**演练通过**

### ARCH-4-C｜纯内存（门禁最严）

- [ ] 累计观测期 **≥ 30 天**
- [ ] 观测期内 `executable` 不一致 = 0
- [ ] 观测期内 `amountOut` 不可归因差异 = 0
- [ ] **异常台账已清零**（不得带未修复异常进入阶段 C）
- [ ] **ARCH-3**（链上快照比对 + 降级数组）已上线
- [ ] 内存池状态新鲜度门禁已生效（版本落后 → 停止）
- [ ] 保留手动回切能力至少一个发布周期
- [ ] 断线/偏差过大时 `Multicall` 补数据路径已验证

### ARCH-4-L｜池子生命周期强依赖（一体机制）

- [ ] 观察池 3 天 TTL 清理**配套 WSS 消息监听**
- [ ] **推荐池与备用池不参与 TTL 淘汰**（物理分表，永不清理）
- [ ] **WSS 监听缺失时 TTL 清理停用**（fail-closed，不得盲删）
- [ ] 监听 → 状态更新 → 活跃判定 → 晋升/降级/淘汰 一体化验证

---

## 阶段 W6：生产铺开（**需显式授权写生产**）

> 顺序固定，每步失败即停并回报。**未获授权前一项都不做。**

### W6-1 schema 铺设

- [~] `pool-manager ensure-depth-schema --apply` 执行成功
- [~] `v4_exchange_pool_liquidity_snapshots` 存在 `capacity_raw` 列
- [~] `v4_exchange_pool_liquidity_snapshots` 存在 `capacity_binding_raw` 列
- [ ] 报告 `depthSnapshot.stale=false`（依赖 W6-4 生产写入后由 healer 复核确认）

> 副本演练：新增列 `capacity_token0_raw / capacity_token1_raw / capacity_raw /
> capacity_binding_raw / price_impact_bps / price_impact_limit_bps / depth_basis`。

### W6-2 索引铺设

- [~] `pool-manager ensure-indexes --apply` 执行成功（**修复缺陷 D1 后退出码 0**）
- [~] `idx_cow_execution_reviews_scope_profit_source_created` 存在
- [~] 报告 `profitSourceQueryPlanUsesIndex=true`
- [~] `positiveEstimateOnly` 统计走索引（EXPLAIN 确认走 COVERING INDEX）

> 副本演练实测：
> `SEARCH cow_execution_reviews USING COVERING INDEX
> idx_cow_execution_reviews_scope_profit_source_created
> (network=? AND chain_id=? AND selected_profit_source=? AND created_at>?)`。
> 评审窗口走 `idx_cow_execution_reviews_scope_created_v2`。
> **注**：修复前该命令因 `EXPLAIN` 的 `detail` 列被按整数读取而以 exit 1 失败（见缺陷 D1）。

### W6-3 dry-run 验收

- [~] `pool-manager depth --dry-run --scope candidate` 执行成功
- [~] `scope=candidate`
- [~] `poolCount <= 94`（实测 94）
- [~] `totalPoolCount` 与 `processedPoolCount` 一致（94 / 94）
- [~] `errors` 为空
- [~] `truncated=false`
- [~] **单次运行 < 60s**（实测 12.85s dry-run / 13.28s apply；修复前 >300s）
- [~] `skippedEndpoints` 中**不含**配置里的存活端点（实测 `[]`）

> 本节为**只读**操作，已在生产库上直接执行完毕，不涉及任何写入。

### W6-4 apply 验收

- [~] `pool-manager depth --apply --scope candidate` 执行成功（副本库，94/94 写入）
- [~] `capacityRowCount > 0`（94）
- [~] `depthSnapshot.stale=false`（`liquidity_read_at` 全部刷新到演练时刻）
- [~] 快照表 `balances_json` 非空率 > 0（**94/4669**，修复前 **0/4669**）
- [~] `balances_json` 含 `token0` / `token1` / `capacityToken0` / `capacityToken1`
- [~] **候选表 `dex_candidate_pools.balances_json` 非空率 > 0** ✅ 2026-11-12（D5）
      实测 **47/47**（修复前 **0/47**），`candidateBalancesPropagated=47`
- [~] **候选表 `token0`/`token1` 必须是数量而非地址** ✅ 2026-11-12（D6）
      `0x` 前缀 `token0`/`token1` 计数 = **0**
- [~] **`_liquidity_cap_raw()` 返回非 `None`** ✅ 2026-11-12（**对照候选表复验**）
      12/12 个 token 符号返回 `liquidity_cap_pool_reserve_50pct`
      （ETH `cap_raw=57479700579778347008`、CRCL `17427328119809081344`、META `67742082464148712`）
      > ⚠️ 原判据错误地对照**快照表**检查；尺寸路径真正读取的是**候选表**，
      > 已在 D5 修复后按正确判据于副本库复验通过。
- [~] `capacity_binding_raw` 非空率 > 0（94/94）

> 副本演练实测：`writtenCount=94`、`errors=[]`、`durationMs=13069`、
> 分带分布 `deep 19 / medium 11 / shallow 6 / dust 58`（与上一轮独立复算一致）。
> **生产写入仍需显式授权。**

### W6-5 复核链路恢复

- [ ] `probeRecords` 长度 ≥ 2（阶梯展开）
- [ ] `ladderIncomplete` 从 1113 归零
- [ ] `best_amount_missing` 解除
- [ ] `bestAmountBacktrace.dominantCause` 非 `best_amount_unresolved`
- [ ] **`best_amount_ladder_incomplete` fail-closed 未被放宽**（靠数据修复归零，不是靠放宽门禁）

### W6-6 容量实测门禁启用

- [ ] `DEX_V4_CAPACITY_GATE_REQUIRE_MEASURED=1` 已设置
- [ ] `capacityGate.verdict=unknown` 由"不阻断"升级为"阻断"
- [ ] 已知未测量 hop 的清单已确认（不是静默阻断）

### W6-7 历史容量值重算（`DEP-P3-8`） ✅ 已关闭

- [~] 生产库存在历史容量写入 —— **不存在**（生产 `capacity_raw` 列尚未创建，
      `balances_json` 非空率 0/4669，无历史污染数据）
- [~] `u128::MAX` 量级的历史容量值已重算 —— **无需重算**（无历史值）
- [~] 饱和读数按**坏读数**处理，未被当作"极深"

> 结论：`DEP-P3-8` 在 v5 批次**关闭**。生产写入完成后复检一次即可。

---

## 阶段 W7：闭环重建（**需停机窗口**；依赖 W6 完成）

### W7-1 修复前准备

- [ ] 生产 DB 备份落 `logs/plan/runtime_evidence/backups/`
- [ ] 回滚脚本已保存
- [ ] **回滚脚本实测通过**（在副本上真实执行）
- [ ] 守护器与 `run.py`（PID 30304，`0.0.0.0:5000`）停机窗口已取得

### W7-2 重建执行

- [ ] `pool-manager cleanup` 成功
- [ ] `pool-manager refresh-key` 成功
- [ ] `pool-manager materialize-scope --apply` 成功
- [ ] `composite-manager materialize-arbitrage --apply` 成功
- [ ] `changedRows` 语义已确认（幂等时为 0 是**正确**结果，见清单 §0.3 事实更正 2）

### W7-3 重建后验收（`S3-AC`）

- [ ] 106 条闭环 **100%** 命中候选集
- [ ] 318 跳 **100%** 命中候选集
- [ ] 四方 `pool_scope_checksum` 一致
- [ ] `missingPoolHopCount = 0`
- [ ] `consumers_need_rebuild = 0` 且**真实验证通过**
- [ ] `path_capacity_below_minimum` 命中符合预期
- [ ] `gateEnforced = true`

### W7-4 约束确认

- [ ] **未依据 TVL 结论下架任何 token 对**
- [ ] `market_depth_exhausted` 结论未被引用
- [ ] `corr(log10 li, log10 TVL) > 0.7` 指标未被引用
- [ ] `li < 1` 排除规则未被引用

---

## 阶段 W8：Manager Authority 与观察移交

### W8-1 Authority 迁移门禁（顺序不变）

每项都需 copy DB 上的 **repeated apply** / **checksum** / **rollback** 演练。

- [ ] `M0-P` migration
- [ ] `M1-P` token authority
- [ ] `M2-P` pool authority
- [ ] `M3-P` edge authority
- [ ] `M4-P` exchange authority
- [ ] `M5-P` closed-loop authority
- [ ] `M6-P` review authority
- [ ] `M7-P` broadcast authority
- [ ] `M8-P` composite authority

### W8-2 `M6-P` 专项：`manager_v4_reviews` 物化（`AUD-P3-5`）

- [ ] `manager_v4_reviews` 物化（非 0 行）
- [ ] legacy 表降级为**只读交叉校验**
- [ ] authority 切换前**未双写**
- [ ] `reviewSources.sourcesDisagree = false`

### W8-3 观察窗口（`P0-6.1`）

- [ ] 连续观察窗口 1（≥ 30 分钟）
- [ ] 连续观察窗口 2（≥ 30 分钟）
- [ ] 连续观察窗口 3（≥ 30 分钟）
- [ ] 每个窗口同 PID / authority / schema / scope 可识别复核，或明确 idle
- [ ] `safeToAutoBroadcast` 判定依据明确

### W8-4 长期盈利统计（`P2-1`）

- [ ] 按 token 统计 expected / realized / gas / success / failed
- [ ] 按 route 统计同上
- [ ] 按 window 统计同上
- [ ] 产出 `pnl_*.jsonl` 长期视图
- [ ] **窗口 PnL、Gas、广播、原因码数据缺失已被直接消除**

### W8-5 UI 与架构收敛（`UI-R1`～`UI-R5` + `P1-9`）

- [ ] GET 迁移到 RuntimeCore manager view / Manager API
- [ ] 写命令迁移到 command API
- [ ] 按表范围退役旧读写（**不长期双写**）
- [ ] 页面回归通过
- [ ] 命令回归通过
- [ ] fail-closed 回归通过
- [ ] 架构守卫回归通过

---

## 阶段 S：停机窗口待办

- [ ] `AUD-P1-8` `manager-api.exe` 重新部署（Windows 文件锁：被运行中服务占用）
- [ ] 守护器与 `run.py` 重启（PID 30304，`0.0.0.0:5000`）

---

## 阶段 G：长期治理（`P3-*`）

- [ ] 连续观察期内未依据单次快照下架 token 对
- [ ] 任何门禁未引入外部 fiat 价格源；经济量一律以同额 USDG 表述（`R-11`）
- [ ] 快照新鲜度门禁常开
- [ ] 工具版本门禁常开（`pool_manager_binary_out_of_sync` 为 blocker）
- [ ] 容量实测门禁按 `DEX_V4_CAPACITY_GATE_REQUIRE_MEASURED` 常开

---

## 阶段 R：回归门禁（每轮必跑）

- [ ] `pytest scripts/tests/test_mainnet_arbitrage_healer.py -q` ≥ **95 passed**
- [ ] `pytest scripts/tests/test_verify_pool_manager_binary_matches_source.py -q` ≥ **11 passed**
- [ ] `pytest flashloan/app_runtime/tests/unit/test_robinhood_v4_pool_capacity.py flashloan/app_runtime/tests/unit/test_robinhood_mainnet_v4_pool_index.py` ≥ **45 passed**
- [ ] 合并套件（healer + binary + capacity + pool_index，4 文件）≥ **157 passed**
- [ ] `cargo test --manifest-path flashloan/tools/pool_manager/Cargo.toml` ≥ **56 passed**
- [ ] `cargo test --manifest-path flashloan/tools/token_manager/Cargo.toml` ≥ **86 passed**
- [ ] `cargo test --manifest-path flashloan/tools/closed_loop_manager/Cargo.toml` ≥ **10 passed**
- [ ] `cargo build --release --manifest-path flashloan/rs-src/Cargo.toml` 成功
- [ ] `python scripts/verify_pool_manager_binary_matches_source.py` 退出码 **0**
- [ ] `python scripts/mainnet_arbitrage_healer.py --mode check` 全程只读、耗时可接受

> 已知既有失败（**不修、不计入新增失败**）：
> `test_verify_closed_loop_runtime_identity_isolation.py` 的 5 个失败为改动前既有失败。


---

## 附：**v7 批次回归基线（2026-11-12 实测）**

| 套件 | 结果 |
|---|---|
| `cargo test --manifest-path flashloan/tools/pool_manager/Cargo.toml` | **56 passed** |
| `cargo test --manifest-path flashloan/tools/token_manager/Cargo.toml` | **86 passed**（基线 61 → 新增 25） |
| `cargo test --manifest-path flashloan/tools/closed_loop_manager/Cargo.toml` | **10 passed** |
| `scripts/tests/test_mainnet_arbitrage_healer.py` | **95 passed** |
| healer + binary + capacity + pool_index（4 文件） | **157 passed** |
| `flashloan/app_runtime/tests` 全量（321 文件） | **2785 passed / 32 failed / 3 skipped** |
| `cargo build --release --manifest-path flashloan/rs-src/Cargo.toml` | 成功 |

### 失败项归因（**本批次零新增**）

32 项失败经**逐项因果核验**（分别把 `pool_manager/src/lib.rs` 与
`v4_offchain_parity_bridge` 接线还原为 `HEAD` 后重跑，失败集合完全不变）
确认全部属于**更早批次遗留**，与本轮改动无因果关系：

- `test_architecture_guards.py::test_active_source_files_stay_under_1000_lines`
  （`storage_v4_exchange.py` 1017 行 / 另一文件 1165 行，均为 **Python** 文件）
- `test_direction_pool_model.py`（2 项）
- `test_manager_overview_closed_loop_identity.py`（`groupKey` 语义）
- `test_migrate_closed_loop_runtime_identity.py`（4 项）
- `test_robinhood_chain_test_pack.py`（`review_failed_missing_best_amount` 标记）
- `test_verify_closed_loop_runtime_identity_isolation.py`（5 项，**计划中已登记为既有失败**）
- `env_loader`（2）/ `pool_candidates_scanner`（3）/ `pool_candidates_surfaces`（1）/
  `symbol_safety`（1）/ `web_app_assembly`（1）/
  `chromium`/`market_config`/`market_page_registry`(2)/`official_v4_config`/
  `stock_pool_depth_verifier`/`rust_closed_loop_worker_bridge`(2)/`secret_leakage_guards`/
  `dex_surface_closed_loop_monitor`/`market_observation_core_db_only`
  （16 项，`HEAD` 基线同样失败）
