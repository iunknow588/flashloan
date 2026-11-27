# D-178 `T-广.03`｜广播链路 Rust 归位 · 逐条验收框

> **本文件是 `T-广.03` 的验收框。** 未勾选 ⇒ **本任务不算闭合**。
> 计划见 [`D-176-PLAN.md`](./D-176-PLAN.md)，范围契约见 [`D-177-SCOPE_GUARD.md`](./D-177-SCOPE_GUARD.md)，
> 实测读数与裁定见 [`docs/D-179-…`](./docs/D-179-广播准入归位与复核线程不再广播_20261126.md)；
> **2026-11-26 第二轮（广播闸门放开 ＋ 复核证明接线）**见 [`docs/D-196-…`](./docs/D-196-广播闸门放开与复核证明接线_20261126.md)。

**任务编号**：`T-广.03`　**验收**：`AC-广.03`　**优先级**：P0
**改动模块（= 回归范围）**：`M-13`

---

## 1. 用户口径落地

- [x] **广播与复核线程都在 Rust 侧**，Python 侧**零改动**

      > **读数**：本任务改动文件清单只有 `flashloan/rs-src/src/broadcast_policy.rs`（新增）、
      > `flashloan/rs-src/src/lib.rs`、`flashloan/rs-src/src/worker_pool.rs` ⇒ **Python 零改动**。

- [x] 复核线程**只产出结论**，广播动作归**广播工作线程**

      > **读数**：`run_native_review` 内的 `maybe_broadcast_with_rpc_failover` 调用点已删除；
      > 该函数现在**只有** `run_execution_task` 一个调用点（即广播工作线程）。

- [x] 授权开关**默认放开**，但**必须确认复核正向利润**才广播

      > **读数**：`broadcast_policy.rs::read_switch` —— 未设置 ⇒ 放开；显式 `false` ⇒ 关闭；
      > 显式 `true` ⇒ 放开。内核四道门禁（gas / 容量 / 经济下限 / 跳价）与
      > `status == profitable_amount_found` **一字未改**。

- [x] **要么成功，要么失败，没有降级处理**

      > **读数**：执行队列不可用 ⇒ 载荷写 `broadcastSkipped` ＋ `broadcastQueueUnavailable = true`；
      > **没有**任何偷偷直广播的旁路。

## 2. 代码项

- [x] `broadcast_policy.rs`（新增）：签名私钥解析（任务配置 → 环境变量候选链）
- [x] `broadcast_policy.rs`（新增）：授权开关（**默认放开** ＋ 显式 `false` 关闭）
- [x] `broadcast_policy.rs`（新增）：`normalize_config()` 在收单边界判定一次并写回
- [x] `worker_pool.rs` → `submit_inner`：收单边界归一化 ＋ 应答带 `broadcastAdmission`
- [x] `worker_pool.rs` → `run_native_review`：**删除**复核线程内的直接广播分支
- [x] `worker_pool.rs` → `run_native_review`：停机中的组不向广播队列交接
- [x] `worker_pool.rs` → `run_native_review`：载荷带 `selectedRpcUrl`（复核用的端点）

## 3. 单测

- [x] `broadcast_policy` **9** 条

      > 开关默认放开；显式 `false` 关闭；显式 `true` 覆盖其它名字；
      > 私钥来自环境；任务配置**优先于**环境；无私钥 ⇒ 拒绝广播；
      > 遗留标志只被记录、**不作门禁**；归一化写回裁决；非法 JSON 原样返回。
      > ⇒ `cargo test --offline`（`flashloan/rs-src`）**241 passed / 0 failed**
      > （基线 **230** ＋ 新 **11**：本模块 **9** ＋ `worker_pool` **2**）。

- [x] `worker_pool` **2** 条：收单边界的裁决归一化；归一化后的任务确实到达广播队列

## 4. 回归

- [x] `M-13` 运行时内核回归全绿

      > **读数**：`cargo test --offline`（`flashloan/rs-src`）⇒ **241 passed / 0 failed / 0 ignored**。

- [x] 旁证：`broadcast-manager` **40 passed / 0 failed**、`manager-api` **33 passed / 0 failed**

      > **读数**：`cargo test --offline -p broadcast-manager -p manager-api`（`flashloan/tools`）⇒
      > **40 / 33**，与 `T-广.02` 同读数 ⇒ 本任务**未触碰**广播层与接口层。

- [x] 与并行线程**零冲突**（落点逐点核对，见 `D-177` §1.1）

      > **读数**：`worker_pool.rs` 的落点 **4289** 与 **5420** 均在并行线程 diff 覆盖区
      > （**3723–4143** / **4691–4758** / **6560** 起）**之外**；`use` 区**一字未动**。

## 5. 部署闸门（**未勾 · 如实登记**）

- [x] 实盘迁移 **018** 已应用

      > **读数（2026-11-26 只读）**：`schema_migrations` ⇒ `20261125_018_closed_loop_directional_only`
      > **已应用**（`applied_at = 2026-11-25T23:43:20.599Z`）⇒ 本闸门**已闭合**
      > （此前登记的「库停在 017」已过时）。

- [x] 新 Rust 扩展与迁移 **018** **同批**上线

      > **读数（2026-11-26 13:59）**：旧扩展已备份
      > （`plan/runtime_evidence/backups/flashloan_rs_pre_D196_20261126T1400.dll`，**18,340,352 B** / **12:11:21**）；
      > 新扩展 `flashloan/rs-src/target/release/flashloan_rs.dll`（**18,379,264 B** / **13:59:08**）已上线；
      > runtime **pid 26328**（看护 **26980**）自 **13:59:28** 起跑新二进制 ⇒ **新旧二进制无中间态**。
      > **回滚办法**：把备份复制回 `flashloan/rs-src/target/release/` 并重启 runtime。

- [ ] 实盘读数：任务提交应答里的 `broadcastAdmission.broadcastEnabled` 为 `true`（运行时准入**默认放开**）

      > **状态**：**未做**。这是**部署**项：新二进制启动后，收单边界的裁决即生效。
      > **注**：概览里的 `summary.broadcastEnabled` **不会**随之变 `true` —— 它读的是广播库
      > **是否发布过状态行**，要等**首次尝试落库**才翻。
      > **代码走查（第二轮）**：`submit_inner` 用 `broadcast_policy::normalize_config` 在**收单边界**判定一次并写回
      > （`worker_pool.rs:5903-5916`）⇒ `config[deferBroadcast] = execution_queue_enabled`、
      > `config[broadcastEnabled] = 裁决结果`、`config[broadcastPrivateKey] = 签名私钥`；
      > `should_route_to_execution_queue`（**4654-4659**）读的就是**这三枚写回值** ⇒ **不是** Python 注入的旧字段。
      > **实盘**：`.env` 的 `ROBINHOOD_MAINNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST=true` ＋ 私钥已配置 ⇒ **裁决为开**；
      > 仍**未取到**提交应答的读数（**没有广播发生** ⇒ 没有收单应答可读）⇒ **如实登记**。

- [ ] 实盘读数：`manager_cow_execution_attempts` 出现**真实**尝试行，且月度库目录被创建

      > **状态**：**未做**。这是**市场条件**项：需要内核真的广播出一笔正利润交易。
      > 实盘当前 `reviewCompletedCount = 0`、`not_executable = 154`（主因 `runArbitrage returned ok=false`）
      > ⇒ 复核**尚未产出**任何 `completed` 行，本任务**不能**替它产出（见 `D-179` §4 的边界声明）。

## 5.1 第二轮（2026-11-26 · 广播闸门放开 ＋ 复核证明接线）

> 触发口径：「**这个可以把它放开了，只要复核有正收益，就应该广播测试了。**」
> 诊断与裁定见 [`docs/D-196-…`](./docs/D-196-广播闸门放开与复核证明接线_20261126.md)。

### 5.1.1 诊断

- [x] 概览 `summary.broadcastEnabled = false` 是**结果**（广播库从未发布状态行），**不是闸门**

      > **读数**：`manager_api/src/lib.rs:6767-6789` 读 `manager_broadcast_library_state`；
      > 真正的授权裁决在 `broadcast_policy.rs::normalize_config`（**默认放开**）⇒ 实盘 `.env`
      > `ROBINHOOD_MAINNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST=true` ＋ 私钥已配置 ⇒ **闸门本来就开着**。

- [x] 真正卡死的是**价格限值闸门恒为 0/3**

      > **读数**：`hop_price_limit_gate_blocks_broadcast` 要求 **3** 条 `hopSqrtPriceLimits`；
      > 内核原生载荷**从不写**该字段；三条限值只存在于**复核**的落库结果
      > （`manager_v4_reviews.hop_sqrt_price_limits_json` / `manager_v4_review_broadcast_requests.…`）
      > ⇒ `should_route_to_execution_queue` **恒为 false** ⇒ 队列永远收不到任务。

- [x] 复核**确实**在产出正收益的广播请求（实测）

      > **读数（2026-11-26 13:56 实盘）**：`manager_v4_review_broadcast_requests` 出现 **1** 行
      > `request_status = generated`、`signed_pnl_raw = 0xc`（`1.2e-05` USDG）、
      > `loop_key = 0x5fc5360d…:0x7066a64c…:0xdf0992e4…` ⇒ **复核侧**放行过，
      > 缺的只是**广播侧**把这份证明接回载荷。

### 5.1.2 代码项

- [x] `review_manager::read_broadcast_requests_for_chain`（**只读**回读复核发布的请求）
- [x] `worker_pool.rs::wake_broadcast_with_review_proof`（按闭环键读回证明 ＋ 逐条校验 ＋ 写回载荷 ＋ 入队）
- [x] 调用顺序倒置：**先发布复核、后唤醒广播**（原来先入队后发布 ⇒ 读不到证明）
- [x] `review_authorized_broadcast`（「复核是否授权了这次广播」的**唯一**判据）
- [x] `economic_gates_allow` ＋ `maybe_broadcast`：经济性三闸对**已授权**路线**只记录不否决**
- [x] `raw_profit_is_positive` / `signed_pnl_is_positive`（利润符号由**精确原始值**决定）
- [x] 时效窗口 **120 s** ＋ 未来容差 **30 s**（与复核侧同口径）
- [x] **正确性闸门不放宽**：三条价格限值必须齐全（v1 只允许「每跳一个 tick 区间」）

### 5.1.3 单测（**7** 条新）

- [x] `a_positive_review_reaches_the_broadcast_queue_and_the_monthly_trade_store`（**端到端**：复核发布 ⇒ 读回证明 ⇒ 入队 ⇒ 广播 ⇒ **月度库落 1 行** `status = success`）
- [x] `a_review_without_the_proved_price_limits_is_not_broadcast`（缺限值 ⇒ 显式拒绝 `0/3`）
- [x] `a_stale_published_request_is_not_broadcast_again`（**600 s** 前的请求 ⇒ 显式拒绝）
- [x] `a_review_authorized_route_is_broadcast_even_when_the_gas_gate_would_block`（gas 闸门**只记录**）
- [x] `a_route_without_the_review_proof_still_obeys_the_economic_gates`（无证明 ⇒ gas 闸门**照旧拦**）
- [x] `a_review_authorized_route_without_the_price_limits_is_refused`（正确性闸门**不放宽**）
- [x] `the_raw_profit_decides_the_sign`

### 5.1.4 回归

- [x] `flashloan-rs` **258 passed / 0 failed**（基线 **251** ＋ 新 **7**）
- [x] `flashloan/tools` 工作区**全绿 · 0 failed**（`review-manager` **167** ／ `broadcast-manager` **40** ／
      `manager-api` **33** ／ `pool-manager` **172** ／ `closed-loop-manager` **48** ／ `composite-manager` **20** ／
      `directional-pool-manager` **148** ／ `candidate-edge` **15** ／ `candidate-exchange` **7** ／ `manager-db` **7**）
- [x] `cargo check --offline --all-targets` ⇒ **0 error / 0 warning**

### 5.1.5 实盘闸门（**未勾 · 如实登记**）

- [ ] 实盘读数：复核发布请求后，**广播尝试**落库（`manager_cow_execution_attempts` > 0 行）

      > **状态**：**未做**。这是**市场条件**项：需要复核在**新二进制**下再放行一条正收益闭环。
      > 已有的那一条（**13:56:22** 发布）在 runtime 重启（**13:59:28**）之前 ⇒ 已过 **120 s** 窗口。
      > **第二轮实测（14:0x）**：复核**确实在跑**（`not_executable` 的 `MAX(reviewed_at)` = **06:13:17Z** = **14:13** 本地，
      > 在重启之后）；**133 / 136** 条 `not_executable` 的内层错误**全是** `NoProfitableRoute`
      > （链上套利合约**自己回退**，`executable = true`）⇒ **此刻市场不赚钱**，是**市场读数**不是链路故障；
      > **复核 → 发布**的时差实测 **4.4 s** ⇒ 远在 **120 s** 窗口内 ⇒ 新鲜的请求**不会被窗口挡下**。
      > **守候**：`plan/runtime_evidence/tools/watch_broadcast_chain_20261126.py`（按**行的身份**播报）。

- [ ] 实盘读数：月度库目录 `flashloan/db/broadcast_transaction_db/robinhood/4663/<月>/trades.sqlite3` 出现**第一行**

      > **状态**：**未做**。随上一框一起发生（归档线程把尝试搬进月度库）。
      > **当前**：该目录**尚无月度库**（`flashloan/db/broadcast_transaction_db/` 只有空目录）。

## 5.2 第三轮（2026-11-26 · 复核确认池 → 广播用确认池）

> 触发口径：「**闭环本身是固定的……三个池子的选择是在复核的时候确定的……复核本身就同步确认了池子地址。后续广播进程使用的是这个复核时候确认的池子。**」
> 触发：「**闭环里面可以装候选池子，后续当池子变动的时候，主池子不能套利的时候，可以考虑自己计算下候选池子是否可以继续套利。**」
> 触发：「**复核通过的闭环数据同步确认对应的池子数据……这条规则可以临时起个作用，就是主池子就是套利池子。**」
> 诊断与裁定见 [`docs/D-197-…`](./docs/D-197-复核确认池与广播用池对齐_20261126.md)。

### 5.2.1 诊断（实测 · 只读）

- [x] 复核**实际定价的池**与闭环**目录的池**确实不同（不是理论风险）

      > **读数**（`probe_loop_option_guard_20261126.py`）：可核路由 **6** 条 ⇒ **候选顶替 2** 处
      > （闭环 `0x5fc5360d…` 族第 1 跳 定价 `0x54f78839…` ＝ 备池；`0x0000…0000` 族第 3 跳 定价 `0x387bf619…` ＝ 备池）。
      > ⇒ 复核确认的池**本来就可能不是目录的主池** ⇒ 「确认池 ＝ 目录池」的口径**不成立**。

- [x] 守卫**不会误伤**：定价的池**全部**落在该跳的选项集内

      > **读数**：**越界 0** ／ 6 条路由 ⇒ 「确认池 ∈ 主池 ∪ 候选池」这条规则**当前零误伤**。

- [x] 方向池 id 的 **scope 歧义**已量化

      > **读数**：方向层 **692** 行 ／ **178** 基础池，其中 **168** 个基础池**同时**在 `core4` 与 `candidate_exchange` 两个 scope
      > ⇒ 「按 (基础池, 方向) 全局搜方向池」会**指错 scope** ⇒ 确认池必须**从闭环自己的选项列表取**。

- [x] 选项集**从不为空**（守卫可以「失败即失败」）

      > **读数**（`probe_loop_option_coverage_20261126.py`）：闭环 **174** ／ 跳 **522** ⇒
      > **空选项集 0**、主池不可解析 **0**、备池不可解析 **0**（备池共 **507** 个）。
      > ⇒ 「选项集为空 ⇒ 放行」这条退路**没有实盘价值**，按「**没有降级处理**」**去掉**。

- [x] 每跳的**主池与备池方向**与本跳方向**全部一致**

      > **读数**（`probe_hop_direction_alignment_20261126.py`）：主池 **522/522** 一致、备池 **507/507** 一致、跳行缺失 **0**
      > ⇒ 确认池可以要求「方向与本跳一致」，**不会误伤**。

### 5.2.2 代码项

- [x] `review_manager`：复核行 `pool_ids_json` ＝ **复核确认的方向池 id**（原来 ＝ 目录的主池）
- [x] `review_manager`：广播请求 `pool_ids_json` ＝ **同一份确认池**；**必须三个**，否则写明原因、不生成请求
- [x] `review_manager`：复核行 `pool_path_key` ＝ `loop_key` ＋ 确认池
- [x] `review_manager`：确认池**从闭环自己的选项列表取**（主池 ∪ 候选池），**不做全局搜索**（规避 scope 歧义）
- [x] `review_manager`：闸门链改用**报价池** ⇒ 修掉「路由走备池、闸门按主池找期望状态」的 `expected_pool_state_unavailable`
- [x] `review_manager`：**守卫** 确认池 ∉ 选项集 ⇒ `not_executable` ＋ `pool_not_in_loop_options:hop_{i}:{pool}`
- [x] `review_manager`：**守卫** 该跳选项集为空 ⇒ `not_executable` ＋ `loop_options_unavailable:hop_{i}`（**失败即失败**）
- [x] `review_manager`：**诊断** 候选顶替 ⇒ `CandidateSubstitution`
- [x] `review_manager`：**守卫** 阶梯没量过 ⇒ `data_incomplete` ＋ `ladder_not_measured:{status}`（**不得 `completed`**，不得广播）
- [x] `worker_pool`：广播前**逐跳核对**路由实际选中的池 ＝ 复核确认的池（**base 池 + 方向**）
- [x] `worker_pool`：路由未命名池 ⇒ `the reviewed route names no selected pools`；路由交易别的池 ⇒ `the route no longer trades the pools the review confirmed`

### 5.2.3 单测

- [x] `review-manager` **4** 条

      > `a_review_confirming_a_candidate_pool_passes_and_records_the_substitution` ／
      > `a_review_pricing_a_pool_outside_the_loop_options_is_not_executable` ／
      > `a_hop_without_a_published_option_cannot_be_confirmed` ／
      > `a_review_without_a_measured_ladder_is_data_incomplete`。

- [x] `flashloan-rs` **2** 条

      > `a_route_that_trades_other_pools_is_not_broadcast` ／
      > `a_route_without_a_selected_pool_is_not_broadcast`。

### 5.2.4 回归

- [x] `review-manager` **171 passed / 0 failed**（基线 **167** ＋ 新 **4**）
- [x] `flashloan-rs` **260 passed / 0 failed**（基线 **258** ＋ 新 **2**）
- [x] `flashloan/tools` 工作区**全绿 · 0 failed**

### 5.2.5 回归抓到的**两个真问题**（本轮自己引入 ⇒ 留档）

- [x] **方向池 id 的基础池段位取错**（`worker_pool.rs::directional_pool_identity`）

      > 症状：**所有**正收益复核都被判成「路由不再交易复核确认的池」⇒ 一笔也发不出去。
      > 根因：方向池 id 的形状是 `dpool:v1:<network>:<chain>:<scope>:<base pool>:<direction>`，
      > 基础池是**倒数第二段**；初版按「最后一段之前**整串**」取 ⇒ 与路由现算的池 id 永远不等。

- [x] **测试夹具的池键顺序非法**（V4 要求 `currency0 < currency1`）

      > 症状：三个广播用例**一起** panic（池 id 算不出来）。
      > 修法：第三跳的键改成 `currency0 = 0x1111…` / `currency1 = 0x3333…`，方向随之 `zeroForOne = false`（方向池 id 末段 `0`）。

### 5.2.6 实盘闸门（**未勾 · 如实登记**）

- [x] 实盘读数：复核行 `pool_ids_json` ＝ **方向池 id**（`dpool:v1:…:<base>:<dir>`）
- [x] 实盘读数：`state_transition_gate_failed:expected_pool_state_unavailable` **归零**
- [x] 实盘读数：灰尘那一行**不再**进 `manager_v4_review_broadcast_requests`
- [ ] 实盘读数：**首笔真实广播**落 `manager_cow_execution_attempts` ＋ 月度库

      > **状态**：**前三框已读**（2026-11-28，生产库只读 `mode=ro` ＋ `query_only=ON`，
      > 探针 `plan/runtime_evidence/tools/probe_checklist_readings_20261128.py`）：
      > 当前目录 **8** 条复核行的 `pool_ids_json` **全部**是 `dpool:v1:` 方向池 id、
      > **全部 3 跳**（`hopCountHistogram = {3: 8}`、`directionalRows = 8`、`notDirectionalRows = []`）；
      > `manager_v4_review_diagnostics` 全表 `expected_pool_state_unavailable` **0** 行；
      > `manager_v4_review_broadcast_requests` **0** 行，而**灰尘级正利润复核行有 7 条**
      > （`loop_options_unavailable`，量级 **1.2e-05 … 7.67e-09**）⇒ 灰尘行确实没进广播请求表。
      > 第四框**仍是市场条件**：`runArbitrage` 正收益复核 **0** 条 ⇒ 链上没有可广播的盈利。

## 5.3 第四轮（2026-11-26 · 单 tick 容量上限 ＋ 每跳金额链）

> 文档：`docs/D-198-单tick容量上限与每跳金额链_20261126.md`

### 5.3.1 诊断（实测 · 只读）

- [x] `F-2` 根因：阶梯**向上翻倍**时**没有天花板**（路由配置无 `maxProbeAmountRaw` ⇒ 无上限）

      > 实盘那一跳：折叠容量 `2_030_566_946_888_083`、起点 `1_015_283_473_444_041`（＝容量的一半）、
      > 翻倍后选中 `4_061_133_893_776_164`（＝容量的 **2 倍**）⇒ 复核**必然**以
      > `final_input_exceeds_single_tick_capacity` 拒绝。

- [x] `F-3` 根因：两道**同族**闸门口径不一致

      > `evaluate_route_single_tick`（单 tick 闸门）**按链传递**金额 ✅；
      > `evaluate_final_state_transition_gate`（状态迁移闸门）**每跳都用锚定本金** ❌
      > ⇒ 第二、三跳的真实输入是上一跳的产出（不同代币、不同单位）⇒ 实盘 **5** 行被
      > `expected_amount_crosses_tick` 误杀。

- [x] 实盘读数（探针 `probe_f2_f3_gate_readings_20261126.py` · `mode=ro` ＋ `query_only`）

      > 代次 `MAX(reviewed_at) = 2026-11-26T10:45:05Z`；复核 **174** 行；
      > `not_executable` **80** ／ `data_incomplete` **94**；
      > 阶梯裁决 `not_profitable_after_costs` **164** ／ `single_tick_capacity_below_min_probe` **8** ／
      > **`profitable_amount_found` 2**；「选出的金额 > 容量」**0** 行；
      > 广播请求 **0** ／ 执行尝试 **0**。

### 5.3.2 代码项

- [x] `flashloan/rs-src/src/execution.rs`：新增 `derive_search_ceiling()`（上限 ＝ 已发布容量；通用探针上限**只能收窄**）
- [x] `flashloan/rs-src/src/execution.rs`：阶梯 **5 处**翻倍点改用该上限
- [x] `flashloan/rs-src/src/execution.rs`：载荷新增 `searchCeilingRaw` ／ `searchCeilingBasis`（**4** 个分支）
- [x] `flashloan/tools/review_manager/src/lib.rs`：新增 `HopAmount` ／ `HopAmountFailure` ／ `hop_expected_amount()`
- [x] `flashloan/tools/review_manager/src/lib.rs`：`evaluate_final_state_transition_gate` 改为「**先解析每跳状态 → 再按链计价**」
- [x] `flashloan/tools/review_manager/src/state_transition_gate.rs`：`HopTransitionVerdict.amount_in_raw`
- [x] `flashloan/tools/review_manager/src/lib.rs`：诊断载荷新增 `"amountInRaw"`

### 5.3.3 单测（**7** 条新）

- [x] `flashloan-rs` **4** 条

      > `p0_9_the_search_ceiling_is_the_published_capacity` ／
      > `p0_9_the_search_ceiling_is_the_capacity_without_a_generic_probe_maximum` ／
      > `p0_9_the_generic_probe_maximum_narrows_the_search_ceiling` ／
      > `p0_9_the_expansion_never_leaves_the_published_capacity`。

- [x] `review-manager` **3** 条

      > `a_hop_is_priced_with_the_amount_that_reaches_it` ／
      > `a_hop_that_crosses_its_tick_range_is_refused` ／
      > `a_hop_without_a_price_is_refused`。

### 5.3.4 回归

- [x] `flashloan-rs` **264 passed / 0 failed**（基线 **260** ＋ 新 **4**）
- [x] `review-manager` **174 passed / 0 failed**（基线 **171** ＋ 新 **3**）
- [x] `flashloan/tools` 工作区 **718 passed / 0 failed**（含 doc-tests）

### 5.3.5 实盘闸门（**未勾 · 如实登记**）

- [x] 部署：新 `flashloan_rs.dll` 上线（**新旧二进制无中间态**）

      > 旧二进制备份 `plan/runtime_evidence/backups/flashloan_rs_pre_D198_20261126T170940.dll`
      > （**18,421,248 B** ／ 17:09:40）；新二进制 **18,427,392 B** ／ **19:19:14**；
      > 看护 pid **29360**（19:20:13）＋ 运行时 pid **24860**（**19:24:34**）
      > ⇒ `Get-Process … .Modules` 核对：`flashloan_rs.dll` **已从新路径加载**。
      > **诚实边界**：前两次启动（pid 18768 ／ 12740）以 `exit_code = 1` 退出，
      > 原因是 `xray.exe`（pid 3452）对 `[fc00::1]:5000` 的**出站**连接未回收
      > ⇒ 端口 5000 绑不上；而 5001…5200 又被本机 **28,806** 条 ESTABLISHED 的
      > **临时出站端口**占满（实测 `5001…5011` 绑定返回 `WinError 10013`）
      > ⇒ 「下一个可用端口」的退路也走不通。**与本轮改动无关**；
      > `_port_in_use` 把**保留端口**与**出站临时端口**一并算成占用，留档待专案处理。

- [x] 实盘读数：阶梯载荷出现 `searchCeilingRaw` ／ `searchCeilingBasis`

      > **状态**：**已读**（2026-11-28）：`manager_v4_reviews` **176** 行里 **174** 行的
      > `static_preview_json` 同时带 `searchCeilingRaw` ＋ `searchCeilingBasis`（**11** 键）；
      > **当前目录 8 行全部带**。缺的 **2** 行是**遗留闭环** `0x0000…0000` 的历史行
      > （2026-11-27T14:41/14:42Z，编排器**摘要**证据，**9** 键），不在当前目录内。

- [x] 实盘读数：状态迁移诊断**带上每跳的到达金额**（`F-3` 落地后的直接读数）

      > 新二进制（pid **24860**）写下的 `StateTransitionGateFailed` 诊断自带 `amountInRaw`：
      > 跳 1 `1_070_504_893_027_534` → 跳 2 **`454_349_796_509_263`** → 跳 3
      > **`5_130_858_008_940_094`** ⇒ 第二、三跳收到的**不是**锚定本金 ⇒ **按链计价已生效**。
      > 该诊断的剩余理由是 `tick_range_changed`（**非** `expected_amount_crosses_tick`）
      > ⇒ 剩下 **2** 行属 tick 区间漂移，**`F-3` 不修**。

- [x] 实盘读数：`expected_amount_crosses_tick` **归零**（`F-3` 落地后的直接读数）

      > **状态**：**已读**（2026-11-28）：`manager_v4_review_diagnostics` 全表
      > `expected_amount_crosses_tick` **0** 行。**这次不是空档**——`F-3` 之后**真的走到过**
      > 状态迁移闸门的那条诊断（闭环 `0xdf0992e4…`，跳 1 带 `amountInRaw`）把理由判成
      > `hop_2_failed:tick_range_changed`，**不是** `expected_amount_crosses_tick`
      > ⇒ 闸门**被行使过**且没判成它。当前目录 8 条复核停在
      > `runArbitrage returned ok=false`，本轮闸门未触发。

- [x] 实盘读数：`final_input_exceeds_single_tick_capacity` **归零**（`F-2` 落地后的直接读数）

      > **状态**：**已读**（2026-11-28）：全表 `final_input_exceeds_single_tick_capacity`
      > **0** 行。同样**不是空档**——`F-3` 之后走到单跳闸门的那条路由，跳 1 的
      > `candidateCapacityRaw = 511287708386`、`capacityBasis = ExpectedState` 都算出来了，
      > 却**没有**判成「金额超容量」。`review_manager` 的分支顺序是
      > `single_tick_capacity_unavailable` → **`final_input_exceeds_single_tick_capacity`**
      > → `route_state_unavailable`，容量比较排在 `route_state_unavailable` **之前**
      > ⇒ 「没判成容量超限」来自**比较本身**，不是提前返回。

- [ ] 实盘读数：**首笔真实广播**落 `manager_cow_execution_attempts` ＋ 月度库

      > **状态**：**未做**。前四框是**部署项**（新二进制上线后即可读）；
      > 第五框是**市场条件**（实盘当前 `runArbitrage ok=false` **79** 行 ⇒ 此刻市场不赚钱）。

## 6. 本轮**刻意不做**（留占位）

- [ ] 跨月 / 跨年聚合查询（`ATTACH` 多月库）

      > **状态**：**刻意留占位**。报表能力，不是「跑通处理流程」的前置。

- [ ] 月度落盘失败的降级兜底

      > **状态**：**刻意留占位**。既有 `records` 层已有 `note_trade_publish_failure` 通道，不新增第二条兜底。

- [ ] `runArbitrage ok=false` 的链上根因（实盘 **149** 条主因）

      > **状态**：**另立任务**。属链上执行 / 容量侧；本任务**不代为关闭**。

- [ ] 复核**完成**率（实盘 `completed` = **0**）

      > **状态**：**另立任务**。属上游复核层；本任务只保证「一旦有正利润结论，广播与记录这条路是通的」。

