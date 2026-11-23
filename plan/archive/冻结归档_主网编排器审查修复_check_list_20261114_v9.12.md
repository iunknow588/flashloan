# 主网编排器审查修复 Checklist v9.13（2026-11-15 归档纪律收口版）

> 本 checklist 与 `未完成任务清单_20261113.md` 一一对应，**只保留待验收项**（全部为未勾选状态）。
> 已完成项及其证据路径见 `plan/back/已完成任务归档索引_20261114_v9.8.md`；归档前完整正文见 `plan/back/冻结归档_*_20261114_v9.8.md` 与 `plan/back/冻结归档_*_20261114_v9.12.md`。
> 完成一项后：先写机器证据 JSON 与人工摘要 Markdown，再登记到归档索引，最后从本文件删除对应条目。

## 0. 架构与口径硬约束

- 权威业务逻辑位于对应 Rust manager；Python 只通过 RuntimeCore/Manager API 间接工作。
- 精确数学复用 Rust `pool_manager` / Uniswap SDK；版本 1 每跳只允许 single-tick，跨 tick 接口仅保留。
- fixed fee 范围为 `0 < fee <= 30000`；动态费率池在所有池角色中排除。
- 候选排序为 normalized single-tick 精确输出降序 → 方向 tick 顺序 → fee → pool id。
- USDG 在经济计价上替代 USD，但外部 fiat 价格源和 TVL 不得进入池深准入。
- 股票代币受 USDG 可兑换性约束；核心/测试仅在 token 分类层豁免部分估值门禁，但测试库不得因此进入候选、观察、闭环或广播；扩展代币只做完整性与传递关系审计。
- 成熟 SDK 可复用，但一个生产结论只能有一个 Rust authority；Python 不得成为第二 authority。
- 当前版本只处理 single-tick；跨 tick 仅保留接口与后续版本任务，不得进入候选、复核、广播或生产闭环。
- scope 契约：`core4 = [core, stocks]`、`candidate_exchange = [core, stocks]`；扩展库与测试库不得进入主路径。
- 测试代币允许保留在数据库和内部启动校验中，但不得展示在 UI，也不得进入候选、观察、闭环、统计或广播；除 schema/启动完整性或主路径污染外不作为专项优化对象。
- 无显式授权不得写生产库、切换 Authority、广播或替换被锁定二进制。
- 数据分层（2026-11-14 用户裁决）：token 与 pool 基础数据是长期权威事实；
  review / submission / broadcast 记录是可再生瞬时运行记录，不具备长期统计意义。
- 不得为 review / submission / broadcast 记录扩展长期持久化 schema；
  新字段必须先证明其支撑 token/pool 基础结构正确性。
- 瞬时记录清理必须走管理器清理接口（`review_manager` / `broadcast_manager`），
  不得由 Python 直接写业务库；必须支持 dry-run、幂等键与精确 scope 匹配。
- 清理不得触碰 token 目录、pool 目录、准入状态与 scope 状态。

## 1. P0 低风险收口（剩余尾项）

### 1.1 候选排序副本物化（生产存量，P0-2）

- [ ] 在生产库显式授权写入前完成备份、SHA-256、WAL 一致性检查和回滚点记录。
- [ ] 将生产旧 label `direction_tick_ordering_then_fee_pool_id` 迁移为 `direction_single_tick_exact_output_then_tick_fee_pool_id`。
- [ ] 重建候选、推荐、观察、闭环消费者，验证 TOP-5/TOP-1、排序方向、checksum 和幂等性。
- [ ] 证明生产旧 label 计数为 0，且不存在新 label 混用。
- [ ] 复跑 P0-2 差异报告，确认旧副本比较差异全部得到解释。

### 1.2 单 Tick 最佳交易量与池状态突变算法（P0-8）

> 依据：`docs/关于Robinhood/单tick最佳交易量计算说明.md`、
> `docs/关于Robinhood/V4单Tick套利池子状态变化处理与算法优化.md`。
> 已完成的数学口径折算与代码接线见归档索引 P0-8 行。
> 2026-11-14（第 2 轮）新增勾选项：折叠 `M_cap` 贯通（Manager 持久化 → 复核门禁 → 广播载荷 → 运行时）、复核层三态执行门禁、状态变好/变坏稳定原因码。
> 机器证据：`plan/runtime_evidence/reports/p0_8_capacity_and_transition_gate_20261114.json`、
> `plan/runtime_evidence/reports/p0_8_bounded_v4_flash_batch_20261115.json`。

#### 1.2.1 单 Tick 池状态突变三态处理

- [ ] 链上只做常数次减法、乘法和除法；比例分子/分母由链下 O(1) 计算并写入 calldata。
  - 链下算法已就绪；链上比例 calldata 尚未实现。
- [ ] 最终 Swap 保留 `amountOutMinimum`，利润不足时整笔回滚。
- [ ] 状态变化证据至少包含旧/新 `sqrtPriceX96`、Tick 边界、判定分支、比例分子/分母、放大前后容量、最终 `amountOutMinimum` 与失败码。
  - 复核诊断已包含旧/新 `sqrtPriceX96`、价格限制、当前/候选容量、容量口径与失败码；比例分子/分母、最终 `amountOutMinimum` 仍待补齐。

#### 1.2.2 代码接线与回归

- [ ] 完整生产物化前发布每跳 tick 状态与 `single_tick_max_token0_raw` / `single_tick_max_token1_raw`，并完成重物化 dry-run 影响评估。
- [ ] 合约实现前后执行状态突变回放：跨 Tick、变坏、变好、流动性骤降、比例极端值、`amountOutMinimum` 不足、批内单闭环失败隔离。
  - 批内单闭环失败隔离已在 Executor 单元层证明（失败项不回滚成功项）；链上回放仍需在部署后的 bounded executor 上执行。
- [ ] 池状态变化导致容量放大时，复核按最终输入量重新计算，不复用旧利润。
  - 复核按最终输入量逐 hop 重算已实现；状态变好后的容量放大与实际最终输入量联动仍需端到端验证。
- [ ] 单 Tick 单元测试、三跳闭环测试、链上/链下 parity、状态变好/变坏/跨 Tick 回归全部通过。
  - 单 tick、三跳闭环、状态变好/变坏/跨 tick、逐跳限价与链上当前状态方向校验已通过；主网 live parity 与同源数学精确三态输出分类尚未执行。

### 1.3 主网数据库启动基线与库完整性（P0-9）

- [ ] 携带持久 USDG 估值库或受控报告，在生产一致性副本重跑 `materialize-libraries` dry-run；历史源 6,353 行、缺 decimals 拒绝 1,735 行必须稳定复现。
- [ ] 历史源 → 标准化 token → 四库 membership → pool scope → 候选/推荐/观察/闭环 lineage receipt 完整，逐层包含源版本、行数、拒绝原因、checksum 和工具版本。
- [ ] 194 只股票不再全部 `pending:not_measured`；`migrationReceipt.valid=true`，每个库 `projectedConsistentWithSource=true`，并保留行数、状态分布、metadata checksum、缺失/多余/身份冲突 token。
- [ ] dry-run 的 `projectedTarget` 与授权 apply 后实际目标库一致；任何 `projectedConsistentWithSource=false` 必须 fail-closed。
- [ ] 显式授权后执行 `manager-db migrate`，应用 `20261112_007_usdg_valuation_schema`（当前生产库该表**不存在**）。
- [ ] 显式授权后执行 `pool-manager ensure-depth-schema --apply`，补齐 `capacity_raw`、`capacity_binding_raw`、`single_tick_max_token0_raw`、`single_tick_max_token1_raw`；uint128 TEXT 不得再经 SQLite INTEGER/Rust i64 截断。
- [ ] 显式授权后运行 token-manager 处理 TB/TA 未锚定扩展成员，并重建 `core4 = [core, stocks]` 与 `candidate_exchange = [core, stocks]` scope。
- [ ] 扩展库完成完整性审计但不进入版本 1 候选主路径；扩展/测试成员在物化层和 Manager API 查询层均被阻断。
- [ ] 重建候选、推荐、观察、Manager 闭环消费者；记录候选池、推荐、观察池、闭环/hop 的 row count、checksum、版本和变化原因。
- [ ] Manager 闭环全部恰好 3 hops，全部 pool 引用命中 active candidate scope；legacy 88 条旧目录及 2 条 TZ/TB/TA 旧闭环不得进入运行时/UI/复核/统计/广播。
- [ ] UI 代币、候选池、观察池、闭环和统计视图对测试库成员返回 0 展示结果；数据库仍可保留测试库行。
- [ ] UI 隐藏测试代币依赖库角色过滤而非删除数据；主网、测试网和历史快照的测试数据完整性保持不变。
- [ ] 测试网数据库 `integrity_check=ok`，原始 `chain_tokens`/`v4_exchange_pools` 保持完整；不因 UI 展示需求创建额外测试代币物化任务。
- [ ] 重跑 baseline-audit，除生产授权/数据修复项外不得出现其他 issue。
- [ ] `back/阶段性设计与审计_20261114/p0_9_database_baseline_adr_20261114.md` 已补写生产执行结果、回滚点、最终 checksum 与收敛证据路径。

## 2. P1 生产池库收敛（授权）

### 2.1 备份与影响评估

- [ ] 用户显式授权 production prune。
- [ ] 生产 DB 备份、hash、schema、行数、空间记录完成。
- [ ] 回滚脚本保存并在副本真实演练。

### 2.2 Production prune

- [ ] 子表优先 prune 完成。
- [ ] `overCeiling=0`、动态费率池=0、orphan=0。

### 2.3 推荐/候选/观察/闭环重物化

- [ ] pool scope 重新物化完成（`core4 = [core, stocks]`、`candidate_exchange = [core, stocks]`）。
- [ ] candidate edge/exchange 重新物化完成。
- [ ] `selection_basis=direction_single_tick_exact_output_then_tick_fee_pool_id`。
- [ ] 幂等重跑 `changedRows=0`。
- [ ] 准入失败原因可区分 `token_library_out_of_scope`；扩展库/测试库不得进入候选 exchange。
- [ ] 测试代币保留在数据库时，UI 默认列表、搜索结果、聚合计数和导出均不展示测试库成员。
- [ ] 三跳闭环恰好三跳，hop 引用全部命中 active candidate pool。
- [ ] 推荐池是候选池的确定性子集；观察池、候选池和闭环使用同一状态版本，不存在跨快照拼接。
- [ ] 每个消费者的 row count、checksum、版本、耗时和失败项均记录。

### 2.4 USDG 估值生产落地

- [ ] 生产迁移完成后落库 194 只股票代币估值，并按 membership TTL 重新物化核心/股票/扩展/测试库。
- [ ] 生产 `token_stocks` 的 admitted/blocked/pending 分布与持久估值证据一致，不再沿用“194 全 admitted”的旧状态。
- [ ] 估值刷新编排满足 membership 刷新间隔 < 24 h、magnitude 刷新间隔 < 1 h。
- [ ] `scripts/refresh_robinhood_token_libraries.py` 按 TTL 触发刷新并输出过期报告。
- [ ] `flashloan/src/dex/tools/band_robinhood_candidate_usdg_capacity.py` 读取估值时执行 magnitude TTL 门禁。

## 3. ARCH-2b / D4-d

- [ ] 剩余偏差逐条归因，无伪造零差异。
- [ ] D4-d `unit_profit` 切到 Rust 精确口径。
- [ ] 切换前后收益/gas/fee/single-tick bound 可回溯。
- [ ] `best_amount_ladder_incomplete` fail-closed 未放宽。
- [ ] `eth_call` 回退开关与演练通过。
- [ ] 最终复核使用实际输入量验证每个 hop 不跨 tick。

## 4. ARCH-3 链上保护

- [ ] 合约传入 expected price/state version。
- [ ] 主参数/降级参数/revert 分档完成。
- [ ] V4 `sqrtPriceLimitX96` 正确使用。
- [ ] 状态陈旧 fail-closed 测试通过。
- [ ] 降级数组顺序、上限和回放证据完成。

## 5. ARCH-4-B/C

### 5.1 影子旁路

- [ ] 内存主算、`eth_call` 影子。
- [ ] 连续 7 天无不可归因差异。
- [ ] 一键回切及演练通过。

### 5.2 纯内存

- [ ] 累计观测至少 30 天。
- [ ] executable 不一致=0。
- [ ] amountOut 不可归因差异=0。
- [ ] 异常台账清零。
- [ ] ARCH-3 已上线。
- [ ] 内存状态新鲜度门禁生效。
- [ ] Multicall 补数据验证。
- [ ] 手动回切保留至少一个发布周期。

## 6. W6 生产铺开

- [ ] 用户显式授权 W6 apply。
- [ ] schema/index/dry-run/apply 报告齐全。
- [ ] `depthSnapshot.stale=false`。
- [ ] `pool_scope_drift` 消除。
- [ ] `probeRecords>=2`。
- [ ] `ladderIncomplete=0`。
- [ ] `best_amount_missing` 解除。
- [ ] `bestAmountBacktrace` 非 unresolved。
- [ ] `DEX_V4_CAPACITY_GATE_REQUIRE_MEASURED=1`。
- [ ] capacity unknown 升级为阻断。
- [ ] 未测量 hop 清单和补测路径完成。

## 7. W7 闭环重建

- [ ] 停机窗口批准。
- [ ] cleanup / refresh-key / scope apply / composite apply 成功。
- [ ] 闭环 100% 命中候选集。
- [ ] hop 100% 命中候选集。
- [ ] 四方 `pool_scope_checksum` 一致。
- [ ] `missingPoolHopCount=0`。
- [ ] `consumers_need_rebuild=0`。
- [ ] single-tick bound / capacity gate 生效。
- [ ] 闭环全部 hop 的最终输入量 single-tick 证明可回溯。
- [ ] 未引用 TVL、`market_depth_exhausted`、相关性或 `li<1` 下架规则。

## 8. W8 Authority 与广播

- [ ] M0-P migration。
- [ ] M1-P token authority。
- [ ] M2-P pool authority。
- [ ] M3-P edge authority。
- [ ] M4-P exchange authority。
- [ ] M5-P closed-loop authority。
- [ ] M6-P review authority。
- [ ] M7-P broadcast authority。
- [ ] M8-P composite authority。
- [ ] `manager_v4_reviews` 非零物化。
- [ ] legacy 只读交叉校验，未双写。
- [ ] `reviewSources.sourcesDisagree=false`。
- [ ] 连续 3 个 ≥30 分钟观察窗口通过。
- [ ] `safeToAutoBroadcast` 判定依据明确。
- [ ] GET/command API/UI/fail-closed/架构守卫回归通过。
- [ ] 完成 RuntimeCore/Manager API 最终调用链审计，确认 Python 未保留 token/pool/route/review/broadcast 权威判断。
- [ ] legacy 仅作只读交叉校验，未长期双写；架构守卫可阻止新增 Python 权威业务逻辑。

## 9. 池生命周期、WSS 与上限

- [ ] 备用池三日活跃清理开关默认关闭。
- [ ] 正式运行且数据量达到配置阈值后，每 3 个自然日的当日 24:00 调度验证。
- [ ] 候选池/推荐池不参与 TTL 清理。
- [ ] 动态费率池未进入备用/候选/推荐角色。
- [ ] 清理配置含默认关闭开关和数据量启用阈值。
- [ ] 清理配置含 3 日窗口、Asia/Shanghai 时区、单次最大删除量和 dry-run/apply。
- [ ] 候选/推荐刷新与备用清理使用同一 pool state version，竞态测试通过。
- [ ] WSS 缺失时清理 fail-closed 停用。
- [ ] 监听→状态→活跃→晋升/降级/淘汰一体化验证。
- [ ] RPC 总速率 ≤15 req/s；manager 目标 8 req/s、batch 50。
- [ ] 生产池表总量 ≤3000。
- [ ] 超限淘汰规则确定性、角色保护和幂等性验证。

## 9.5 瞬时记录清理与数据分层（P0-10）

> 依据：开发计划 §2.4 数据分层原则。
> 已完成基线及机器证据：
> `plan/runtime_evidence/reports/p0_10_review_cleanup_and_catalog_preservation_20261114.json`。
> 已完成内容不在本 checklist 保留完成勾选；以下只列剩余待验收门禁。

- [ ] 后续新增 review/submission/broadcast 字段时，先在评审记录中证明其支撑
  token/pool 基础结构正确性；否则不得进入长期持久化 schema。
- [ ] 架构守卫覆盖：Python 不得直接清理业务库，清理必须通过管理器接口，
  且 token/pool 目录与其他 network/chain scope 保持不变。

## 10. 长期统计与运维

- [ ] token/route/window expected/realized/gas/success/failed 统计完成。
- [ ] `pnl_*.jsonl` 长期视图完成。
- [ ] 快照新鲜度、工具版本、容量实测门禁常开。
- [ ] 连续观察期未依据单次快照下架 token 对。
- [ ] `flashloan/tools/target/release/manager-api.exe` 停机替换完成。
- [ ] 守护器与 `flashloan/app_runtime/run.py` 重启后 health/version/页面验证。

## 11. 回归与文件格式

- [ ] healer 测试达到当前基线。
- [ ] binary-source-match 测试和脚本退出码 0。
- [ ] capacity + pool-index 合并套件达到当前基线。
- [ ] `app_runtime/tests/unit` 既有失败集合未扩大。
- [ ] `mainnet_arbitrage_healer --mode check` 全程只读。
- [ ] `cargo test --locked` 和 release 构建通过。
- [ ] SDK 升级附 parity、性能、许可证和回滚差异报告。
- [ ] `flashloan/app_runtime` 架构守卫阻止新增 Python 权威业务逻辑。
- [ ] 计划与证据文件统一 UTF-8 无 BOM、LF 换行、JSON `sort_keys=true` / `ensure_ascii=false`。

## 12. 生产写入最终确认

- [ ] 命令明确显示目标为生产库。
- [ ] 用户授权文本已记录。
- [ ] 备份与回滚点已验证。
- [ ] 写入期间 RPC/worker/authority 状态可控。
- [ ] 写入后 checksum/row-count/orphan/幂等验证完成。
