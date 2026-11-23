# 主网编排器审查修复 Checklist v9.8（2026-11-14）

> 本 checklist 与 `未完成任务清单_20261113.md` 一一对应，只保留待验收项。既有完成项及 v9.1–v9.6 活动快照已归档到 `back/`；完成一项后应将证据移入归档并从本文件删除。

## 0. 架构与口径硬约束

- 权威业务逻辑位于对应 Rust manager；Python 只通过 RuntimeCore/Manager API 间接工作。
- 精确数学复用 Rust `pool_manager` / Uniswap SDK；版本 1 每跳只允许 single-tick，跨 tick 接口仅保留。
- fixed fee 范围为 `0 < fee <= 30000`；动态费率池在所有池角色中排除。
- 候选排序为 normalized single-tick 精确输出降序 → 方向 tick 顺序 → fee → pool id。
- USDG 在经济计价上替代 USD，但外部 fiat 价格源和 TVL 不得进入池深准入。
- 股票代币受 USDG 可兑换性约束；核心/测试豁免；扩展代币受深度与传递 USDG 关系约束。
- 成熟 SDK 可复用，但一个生产结论只能有一个 Rust authority；Python 不得成为第二 authority。
- [ ] RuntimeCore/Manager API 最终调用链审计完成。
- [ ] Python 重复业务判断已退役，且未长期双写。

## 1. P0 低风险收口

### 1.1 管理器边界、`app_runtime` 与 SDK

- [x] token/pool/edge/exchange/closed-loop/review/broadcast/composite 的责任矩阵与代码路径一致。证据：`app_runtime_authority_audit_20261114.md`。
- [x] `flashloan/app_runtime` 页面、后台任务、脚本和测试调用链清单完成。证据：`app_runtime_authority_audit_20261114.md`。
- [x] Python 中剩余权威业务判断均有迁移任务、责任 manager 和删除时机。证据：`app_runtime_authority_audit_20261114.md` §4。
- [x] RuntimeCore manager view 与 command API 覆盖目标 Python 入口。证据：`app_runtime_authority_audit_20261114.md` §2-3。
- [x] `PoolManagerLens` / `SimpleTickDataProvider` 的状态查询与 tick 数据来源可追溯：槽位按 Lens 约定手工实现；未直接采用 `SimpleTickDataProvider`。证据：`app_runtime_authority_audit_20261114.md` §5.1。
- [x] `Dex-Math-Core-rs` 评估结论为版本 1 不采用：仓库无依赖、锁定版本、引用或 parity 基线。证据：`app_runtime_authority_audit_20261114.md` §5.2。
- [x] 数学 authority 唯一：`uniswap-v4-sdk 2.0.0`（MIT）仅在 `pool_manager` 使用，回滚路径见 P0-0 ADR。证据：`p0_0_app_runtime_authority_audit_20261114.json`。
- [x] P0-0 全程只读，无生产 DB 写入。

### 1.2 影子对照 ✅ 已完成（2026-11-14）

基线报告：`runtime_evidence/reports/arch4_shadow_diff_2026-11-13T14-34-37-316978900-00-00.json`
（`comparisonCount=84` / 42 池 × 2 方向、`unexplainedCount=4`、`executableMismatchCount=23`、
`amountMismatchCount=82`、`memoryDeviationBlockCount=0`、`untrustedChainCount=0`、
台账 `ledger.entries=60` 覆盖 15 池 × 4 字段）。

完成报告：`runtime_evidence/reports/arch4_shadow_diff_2026-11-13T20-54-22-236091900-00-00.json`
（SHA-256 `baa16bf531ba27a8aab6d7ed2d5cce8a0dea31fa8496032e7cf8a6f947ad2d36`）：
`comparisonCount=24`（12 个零钩子池 × 2 方向）、`unexplainedCount=0`、`executableMismatchCount=6`、
`untrustedChainCount=0`、`memoryDeviationBlockCount=0`、24 条比较全部锁定 `block:113647275`、
台账 6 条全部 `explained`。

归因文档：`p0_1_arch4_shadow_diff_attribution_20261114.md`；
机器证据：`runtime_evidence/reports/p0_1_arch4_shadow_diff_attribution_20261114.json`
（`mode=read_only`、`status=passed`、`rowCount=6`、生产库写入 0、权限切换 0、广播 0）。

- [x] `0x03120198…` 归因完成 → `accepted_limit` / `field_observability`（`amountOut` 一致，链端 ABI 不返回 `sqrtPriceX96`/`priceImpact`）。
- [x] `0x02884651…` 归因完成 → `accepted_limit` / `field_observability`（同上）。
- [x] `0x98f19386…` 归因完成 → `explained` / `not_executable`（链端自定义错误 `0x6190b2b`，在固定块及其后 5 个区块逐一复现，回退数据逐字节一致，属链侧稳定判决）。
- [x] `0x84bd4e2d…` 归因完成 → `fixed` / `pool_state_version`（根因为传输层静默回退；修复后归因 `tick_boundary`，`relDelta=5.19e-05`）。
- [x] `tick_range_gate` 单独定性完成 → 版本 1 single-tick 的 **fail-closed 语义**（预期行为，非精度缺口），未混入 unexplained 计数。
- [x] `tick_boundary` 形成容差结论 → 相对偏差全部 ≤ `0.0010`，属舍入级差异，**全局容差不放宽**。
- [x] `executableMismatchCount` 与归因账目平衡 → 完成报告 `executableMismatchCount=6` = `tick_range_gate` 条目 6，全部 explained。
- [x] `amountMismatchCount` 区分“链端字段未返回”与“真实数值偏差”，不可观测字段不计入数值不一致。
- [x] attribution 分布与报告一致（完成报告：`tick_range_gate=6`、`tick_boundary=11`、`not_executable=7`、`unexplained=0`）。
- [x] 所有异常台账含 firstSeen/lastSeen/field/fields/magnitude/attribution/landedFix/regressionResult；台账粒度为一次 swap 一次缺陷，身份键 `pool_id::{zfo|!zfo}`。
- [x] 报告 `unexplainedCount` 与台账 `status=unexplained` 条目数一致（均为 0；`unexplainedCount` 直接取自 `ledger.unexplained_count()`）。
- [x] 内存差异阻断次数=0；RPC 不可信阻断逻辑保持（链侧 revert 判为 trusted 且从不阻塞）。
- [x] 采集未劣化主链路延迟与广播成功率 → 24 条比较整轮约 52 秒（旧实现同口径 61.8 秒，未劣化）；本任务未广播且生产库五张广播表均为 0 行、`safeToAutoBroadcast=false`，无广播成功率基线可回归，表述为“广播路径未被触及，成功率口径无变化”。

#### 1.2.0 本阶段额外修复的传输层根因（阻塞 A1 出口）

- [x] `extsload` 批量突破节点 15 req/s 上限（实测 9/24 请求返回 `-32007`）→ 批量大小由 50 降为 8（由速率上限派生），批次间等待覆盖整个批次。
- [x] 读取失败后静默回退陈旧快照并半替换 → 新增 `V4PoolState.complete`，仅完整测量替换存储态，绝不半替换。
- [x] 失败池仍被标注同块（虚假一致性）→ 仅完整测量写块号；`pool_state_versions_agree` 收紧为时间戳与块号不再视为一致。

回归：`cargo test --locked -p pool-manager -p review-manager` → pool-manager 105 passed、review-manager 101 passed。

### 1.2.1 首版逐跳 single-tick 最终门禁 ✅ 已完成（2026-11-14）

归因文档：`p0_6_single_tick_final_gate_20261114.md`；
机器证据：`runtime_evidence/reports/p0_6_single_tick_final_gate_20261114.json`
（`mode=read_only`、`status=passed`、`rowCount=11`、
`checksum=sha256:45d0fefdd61718d8974e7656a73b104732590ce51b9f8339dfa8bdeab818eeee`、
生产库写入 0、权限切换 0、广播 0、RPC 请求 0；
`failureCodes=['cross_tick_blocked','pool_state_version_drift','single_tick_unprovable']`）。

代码：`flashloan/tools/review_manager/src/single_tick.rs`；
接线：`flashloan/tools/review_manager/src/lib.rs::build_review_rows`
（门禁在生成裁决**之前**执行，失败即 `review_status="not_executable"`、
`review_reason="single_tick_gate_failed:<reason>"`，**不产生广播请求**）。

- [x] `review_manager` 按实际最终输入量逐 hop 输出 initialized tick、精确输出和下一 tick 距离。证据：`p0_6_single_tick_final_gate_20261114.json`（每 hop 输出 `initialized_tick` / `next_tick` / `next_tick_distance` / `amount_in_raw` / `amount_out_raw` / `max_amount_in_raw` / `headroom_raw` / `bound_conclusion`）。
- [x] 任一 hop 跨 tick、容量未知、状态漂移或无法证明 single-tick 时整条路线 fail-closed。证据：`p0_6_single_tick_final_gate_20261114.md` §4 失败闭合矩阵；`failureCodes` 三类均被实际触发（跨 tick / 版本漂移 / 不可证明）。
- [x] YC、Y-Z、ZC 和闭环内全部 hop 均受保护，不只验证首尾 hop。证据：机器报告含 `yc_single_tick` / `yz_single_tick` / `zc_single_tick` / `closed_loop_single_tick` 四条单跳样例与 `closed_loop_middle_hop_cross` 中间跳失败样例（跳 1 inside、跳 2 crosses、跳 3 unprovable）。
- [x] 候选阶段 single-tick 输出与最终复核使用同一数学口径。证据：最终门禁复用 `pool_manager::depth::single_tick_swap_amount_out`（与候选排序同一 authority、同一舍入），下一 tick 距离由 `pool_manager::depth::tick_range_bounds` 按方向导出。
- [x] 跨 tick 样例在广播前被阻断，广播管理器没有收到不可执行路线。证据：门禁接线于 `build_review_rows` 形成裁决之前；跨 tick 样例 `review_status="not_executable"`，广播请求不生成。

补充说明：

- **逐跳输入量**：跳 1 使用借入本金，跳 N+1 使用跳 N 的**实际输出量**。
- **全部跳都被判定**：门禁不在首个失败跳短路，证据仍覆盖后续跳；
  路线裁决仍在**首个失败跳** fail-closed。
- **fail-closed 触发条件**：跨 tick、`tick_spacing` 缺失/不可用、tick 区间不可导出、
  池状态版本漂移、池状态缺失、最终输入量缺失。
- **只读**：本轮全程无生产库写入、无权限切换、无广播、无 RPC 请求。
- **回归**：`cargo test --locked -p review-manager` → 114 passed / 0 failed；
  `cargo test --locked --workspace` 全量通过；`flashloan/rs-src` `runtime_core` 12 passed；
  Python 侧 4 组 parity 用例 72 passed。

### 1.2.2 版本 1 范围、池角色与证据契约

- [x] 版本 1 范围明确为“多跳闭环、逐 hop single-tick”，跨 tick 不进入候选、复核或广播。证据：`version1_scope_roles_evidence_contract_20261114.md` §1。
- [x] 候选、推荐、备用和 Y-Z 观察池的权威表/manager 输出映射完成。证据：`version1_scope_roles_evidence_contract_20261114.md` §2。
- [x] 同一 pool 兼具候选与推荐角色时，清理逻辑按角色集合正确保护。证据：`version1_scope_roles_evidence_contract_20261114.md` §2.1。
- [x] 阶段报告最小字段包含 mode、productionWrite、输入/输出版本、row count、checksum、三态结论和 failure codes。证据：`p0_7_scope_roles_evidence_contract_20261114.json`。
- [x] 阶段报告包含 rollback point、RPC 请求数、测试命令和失败集合摘要。证据：`p0_7_scope_roles_evidence_contract_20261114.json`。
- [x] `productionWrite=true` 的报告包含授权文本和目标库路径。证据：`version1_scope_roles_evidence_contract_20261114.md` §3.2。
- [x] 阶段退出以 checklist 验收项满足为准，不以报告文件存在为准。

### 1.2.3 单 Tick 最佳交易量与池状态突变算法

> 依据：`docs/关于Robinhood/单tick最佳交易量计算说明.md`、
> `docs/关于Robinhood/V4单Tick套利池子状态变化处理与算法优化.md`。
> 本节是 P0-8 的代码—文档—证据一一对应验收清单。

#### 1.2.3.1 数学口径与单位折算

- [x] token1 侧单跳容量使用 `K = L·|Δ√P|`；token0 侧使用 SDK 逐 token delta
  `K = L·|Δ√P|/(s_cur·s_b)`，不再使用漏分母的近似式。证据：`pool_manager::single_tick_capacity`。
- [x] 三跳 M_cap 使用**前跳累积即期汇率**折算到锚定代币；第 2 跳使用第 1 跳汇率，
  第 3 跳使用前两跳汇率乘积。证据：`single_tick_capacity::compute_loop_capacity`。
- [x] 池提供平方根价、容量公式使用线性价时，比例因子折叠两次；全部中间计算使用
  `U256` 定点算术，无浮点、无运行期 `sqrt`。
- [x] 累积因子使用分子/分母对，避免连续除法导致 Q96 因子截断为 0。
- [x] 跨 Tick 最佳交易量/探针阶梯逻辑明确移出当前版本；只保留接口与后续版本任务，
  当前版本不得将其接入候选、复核、广播或链上仲裁。

#### 1.2.3.2 单 Tick 池状态突变三态处理

- [ ] **致命变化**：当前价格跨出预设 Tick 区间，或活跃流动性不可证明/显著消失；
  当前闭环立即 revert，批次内其他闭环继续执行。
- [ ] **状态变坏**：ZeroForOne 当前价高于基准，或 OneForZero 当前价低于基准；
  当前闭环立即 revert，并记录稳定失败码 `adverse_price_move`。
- [ ] **状态变好**：价格朝有利方向移动且未跨 Tick；按当前距离边界比例重算容量，
  禁止沿用旧 M_cap。
- [ ] token1 容量比例使用 `|s_b-s₂|/|s_b-s₁|`；token0 容量比例额外乘 `s₁/s₂`，
  禁止用统一距离比例覆盖两侧。
- [ ] 链上只做常数次减法、乘法和除法；比例分子/分母由链下 O(1) 计算并写入 calldata。
- [ ] 容量放大倍率有硬上限；异常比例、零分母、溢出、未知流动性全部 fail-closed。
- [ ] 链上 Swap 传当前 Tick 的 `sqrtPriceLimitX96`，不得以全局极值替代。
- [ ] 最终 Swap 保留 `amountOutMinimum`，利润不足时整笔回滚。
- [ ] 单闭环 revert 与其他闭环隔离；不得因一个失败闭环阻断整批。
- [ ] 状态变化证据至少包含旧/新 `sqrtPriceX96`、Tick 边界、判定分支、比例分子/分母、
  放大前后容量、最终 `amountOutMinimum` 与失败码。

#### 1.2.3.3 代码接线与回归

- [x] 新增 `flashloan/tools/pool_manager/src/single_tick_capacity.rs`，
  三跳 M_cap 单位折算单测 14/14 通过。
- [x] `closed_loop_manager` 读取候选池发布的 `sqrtPriceX96`、tick、liquidity、fee、
  tick spacing 与 token 顺序，并接入折算后的路径容量。
- [x] 缺少任一跳 tick 状态时不得生成折算容量；旧原始上界只保留“方向容量为 0”的
  不可用信号，禁止跨 token 比较正值。
- [x] 定向回归 `closed-loop-manager` 18/18 通过；`cargo test --locked --workspace` 全量通过。
- [ ] 完整生产物化前发布每跳 tick 状态与 `single_tick_max_token0_raw` /
  `single_tick_max_token1_raw`，并完成重物化 dry-run 影响评估。
- [ ] 将 M_cap 接入最佳交易量选择器：单 Tick 结果必须 `amount_in <= M_cap`，
  超出时截断或拒绝并给出稳定原因码。
- [ ] 合约实现前后执行状态突变回放：跨 Tick、变坏、变好、流动性骤降、比例极端值、
  `amountOutMinimum` 不足、批内单闭环失败隔离。
- [x] `cargo test --locked --workspace` 全量通过；P0-8 机器证据 JSON 与 ADR 已输出：
  `runtime_evidence/reports/p0_8_single_tick_capacity_state_change_20261114.json`、
  `p0_8_single_tick_capacity_state_change_adr_20261114.md`。

#### 1.2.3.4 明确不做

- [x] 不以跨 Tick 探针阶梯替代单 Tick 专用容量算法；当前版本完全不依赖跨 Tick 最佳交易量能力。
- [x] 不以旧 M_cap 充当状态变好后的最终容量。
- [x] 不把闭环内 PoolId 唯一性误解为闭环之间不可复用；闭环之间允许复用池子，
  但必须由链上状态重算识别前序闭环造成的变好/变坏。
- [ ] 未经 P1 显式授权，不写生产数据库、不切换 Authority、不广播。

### 1.2.4 主网数据库启动基线与库完整性

- [x] 代码口径：当前版本候选/推荐/闭环 scope 只允许核心库+股票库；扩展库与测试库不得进入主路径。生产配置仍待授权重建。
- [x] 核心库/股票库代币逐项具备地址、符号、位数、V4 currency key、来源与有效准入状态；主网只读结果 4/4、194/194 完整。
- [ ] 扩展库逐项具备直接流动性合格 V4 池；只读门禁已实现，当前 162 个中 160 个有核心/股票锚定，2 个待修复。
- [x] 推荐池每方向 TOP-1 完整：42 对 × 2 方向 = 84/84；引用池均可解析。
- [x] 候选池基础数据可用：93 个 distinct pool；候选 scope 活跃池 108 个；无越界库池行。
- [x] 观察池 Y-Z 引用一致：93 个 distinct pool、失效引用 0；528 个无直连方向作为可观测空结果，不判为数据错误。
- [x] 三跳闭环 `anchor→Y→Z→anchor` 106/106 完整，318 hops，失效 hop pool 引用 0。
- [ ] 历史资料库迁移前后行数、checksum、准入状态与库分布一致，回执可比较。
- [x] 启动基线只读门禁已接入 Rust CLI、Manager API 与主网自愈编排器；缺失项可路由到 token/pool/manager-db 工具。
- [ ] 生产收敛：应用最新迁移、重建 `core4`/`candidate_exchange` scope、修复 2 个扩展库无锚定 token；仍需显式生产写授权。

### 1.3 候选排序副本物化

- [x] 旧 `selection_basis` 已迁移为 `direction_single_tick_exact_output_then_tick_fee_pool_id`，测试、报告和 checksum 同步。证据：`p0_2_selection_basis_migration_20261114.json`。
- [x] 副本 TOP-5/TOP-1 差异报告完成：84 个方向，TOP-1 变化 10，TOP-5 变化 14。证据：`p0_2_selection_basis_top_differences_20261114.json`。
- [x] 有报价池存在时，无价格池 TOP-1=0。证据：`p0_2_selection_basis_migration_20261114.json`。
- [x] 连跑两次 checksum/排序完全一致：`sha256:c2530bbec0bd8052db55c73017f39a70a42e858514acf8a2f057e54c82aa8a25`，第二次 `changedRows=0`。
- [x] `is_first_hop_recommended` / `is_third_hop_recommended` 与推荐表一致。证据：候选 manager 单测与副本快照。
- [x] Rust manager、RuntimeCore view、Manager API 和报告使用相同的新 label 与 checksum 口径。证据：`p0_2_selection_basis_migration_20261114.json`。

### 1.4 版本触发重算与 VAL

#### 1.4.1 版本触发重算实窗 ✅ 已完成（2026-11-14）

归因文档：`p0_4_recompute_latency_20261114.md`；
机器证据：`runtime_evidence/reports/p0_4_recompute_latency_20261114.json`
（`mode=read_only`、`status=passed`、`rowCount=2`、
生产库写入 0、权限切换 0、广播 0、RPC 请求 0）。

代码：`flashloan/tools/composite_manager/src/recompute_latency.rs`
（`measure_recompute_latency` / `recompute_latency_checksum`）；
探针：`flashloan/tools/composite_manager/examples/p0_4_recompute_latency_probe.rs`。

- [x] token/pool/scope 版本变化到全链重算完成的实窗延迟报告。证据：副本库实窗演练全链 6 步 **11 836 ms**；机器报告 `totalLatencyMs` / `medianTotalLatencyMs` / `minTotalLatencyMs` / `maxTotalLatencyMs`。
- [x] 报告包含开始版本。证据：`measurements[].startVersion.versions` + `startVersion.fingerprint`。
- [x] 报告包含结束版本。证据：`measurements[].endVersion.versions` + `endVersion.fingerprint`。
- [x] 报告包含各 manager checkpoint。证据：`measurements[].checkpoints[]` 覆盖 `pool_scope` / `candidate_edge` / `candidate_exchange` / `closed_loop` / `review` / `broadcast`，每项含步骤耗时、累计延迟、依赖指纹、依赖版本与尝试次数。
- [x] 报告包含总延迟。证据：`totalLatencyMs` 及中位数/最小/最大聚合。
- [x] 报告包含失败重试次数。证据：`failureRetryCount` / `retriedStepCount` / `failedStepCount`；单测覆盖失败后重试成功场景。
- [x] 全程只读，无生产库写入。证据：测量器仅以 `SQLITE_OPEN_READ_ONLY` 打开；单测断言测量前后 `manager_pipeline_runs` 行数不变；探针在临时副本库运行。
- [x] 版本变化判定不假阳性也不漏报。证据：单测 `a_version_change_between_steps_is_detected` 与 `a_version_change_seen_only_by_a_middle_step_is_still_detected`；对照窗口无变化时 `versionChanged=false`。

补充说明：

- `composite_manager` 已有的 pipeline 表提供全部原始 checkpoint，本轮只补**只读测量器**，
  不改 pipeline 执行路径、顺序、依赖或重试策略。
- **版本判定修正两个陷阱**：① 步骤依赖快照只含该步依赖的组件，直接与运行全量快照比较会假阳性
  → 只在 start/end 交集上比较；② 只看最后一步会漏掉中段版本变化
  → 结束边界取全部步骤依赖快照的**并集**。
- 成本归因：`closed_loop`（3.30 s）+ `review`（2.97 s）约占 53%。
- 回归：`composite-manager` 17 passed；`cargo test --locked --workspace` 359 passed / 0 failed。
- 遗留：生产实窗需停机窗口与显式授权，届时用同一 `measure_recompute_latency` 入口采集即可；
  `pool_scope` 目前只有 checksum 没有数值版本，如需数值版本应在 `pool_manager` 增加，不在测量层伪造。

#### 1.4.2 VAL 估值遗留决策 ✅ 已完成（2026-11-14）

- [x] USDG 估值 TTL/过期/fail-closed 口径定案。
  **双档 TTL**：成员关系 **24 h**（`MEMBERSHIP_MAX_AGE`）/ 数值幅度 **1 h**（`MAGNITUDE_MAX_AGE`）。
  **过期 = 未知**：过期 `no_route` 不再是负面，过期 `route_found` 不再是正面。
  **fail-closed**：时间戳缺失 / 不可解析 / 位于未来 / 超容忍度 → 一律未知，无一条解析为负面。
  过期行**不删除**（过期是读取规则，不是清理任务）。
  代码：`flashloan/tools/token_manager/src/valuation_freshness.rs`。
- [x] 本地 no-route 近似是否采用完成决策；近似结果不得作为真负。
  **决定：不采用。** 真负在当前体系里只有上游服务明说这一个生产者；
  本地启发式最多得出"大概没有路径"，那是另一个命题。
  代码强制：`ValuationApproximation::Rejected`（默认值）+ `approximation_may_promote_to_negative` 恒 `false`。
- [x] 长期手册口径更新完成。
  `docs/数据库结构文档/主网自愈编排器/05_数据口径与统计.md` 新增 §11。

**验收补充**

- [x] 机器证据：`runtime_evidence/reports/p0_5_valuation_expiry_policy_20261114.json`
      （`mode=read_only` / `status=passed` / `rowCount=7` / `productionWrite=false` / `rpcRequestCount=0`）。
- [x] 人工摘要：`logs/plan/p0_5_valuation_expiry_policy_20261114.md`。
- [x] 回归：`cargo test --locked -p token-manager` **131 passed / 0 failed**（新增 12）；
      `cargo test --locked --workspace` **451 passed / 0 failed**。
- [x] 顺带核实 `VAL-4-13` 已在代码中修复且有回归测试，**194 只全量不再被其阻塞**。
- [x] 生产写入 0 / 权限切换 0 / 主网广播 0。

**遗留（P1，需显式授权）**

- [ ] 生产库迁移 `20261112_007_usdg_valuation_schema`（当前生产库该表**不存在**）。
- [ ] 生产库落 194 只估值并重新物化库（当前生产 `token_stocks` 仍为 **194 全 admitted / 0 blocked**）。
- [ ] 估值刷新节奏编排：membership 刷新间隔 < 24 h、magnitude 刷新间隔 < 1 h。
- [ ] `refresh_robinhood_token_libraries.py` 增加按 TTL 触发的刷新与过期报告输出。
- [ ] `band_robinhood_candidate_usdg_capacity.py` 读取估值时接入 magnitude TTL。

## 2. P1 生产池库收敛（授权）

- [ ] 用户显式授权 production prune。
- [ ] 生产 DB 备份、hash、schema、行数、空间记录完成。
- [ ] 回滚脚本保存并在副本真实演练。
- [ ] 子表优先 prune 完成。
- [ ] `overCeiling=0`、动态费率池=0、orphan=0。
- [ ] pool scope 重新物化完成。
- [ ] candidate edge/exchange 重新物化完成。
- [ ] `selection_basis=direction_single_tick_exact_output_then_tick_fee_pool_id`。
- [ ] 幂等重跑 `changedRows=0`。

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

## 10. 长期统计与运维

- [ ] token/route/window expected/realized/gas/success/failed 统计完成。
- [ ] `pnl_*.jsonl` 长期视图完成。
- [ ] 快照新鲜度、工具版本、容量实测门禁常开。
- [ ] 连续观察期未依据单次快照下架 token 对。
- [ ] `manager-api.exe` 停机替换完成。
- [ ] 守护器与 `run.py` 重启后 health/version/页面验证。

## 11. 回归与文件格式

- [ ] healer 测试达到当前基线。
- [ ] binary-source-match 测试和脚本退出码 0。
- [ ] capacity + pool-index 合并套件达到当前基线。
- [ ] `app_runtime/tests/unit` 既有失败集合未扩大。
- [ ] `mainnet_arbitrage_healer --mode check` 全程只读。
- [ ] `cargo test --locked` 和 release 构建通过。
- [ ] SDK 升级附 parity、性能、许可证和回滚差异报告。
- [ ] `flashloan/app_runtime` 架构守卫阻止新增 Python 权威业务逻辑。

## 12. 生产写入最终确认

- [ ] 命令明确显示目标为生产库。
- [ ] 用户授权文本已记录。
- [ ] 备份与回滚点已验证。
- [ ] 写入期间 RPC/worker/authority 状态可控。
- [ ] 写入后 checksum/row-count/orphan/幂等验证完成。
