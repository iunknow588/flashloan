# D-201 `T-广核.01`｜广播路由闸门 · 启动即全量复核 · 一轮复核耗时拆解 · 验收清单

> **本文件是 `T-广核.01` 的全部验收框。** 编号 `AC-广核.01` 属**纯 ID**。
> 每条勾选**必须**有实测读数支撑；**不得**凭推断勾选；**不得**用零冒充未测量。

**验收前置**：`M-7` ＋ `M-13` 现有单测全绿

**本轮状态（2026-11-26 22:1x）**：**代码项全部闭合**；**部署闸门 3 / 5 已闭合**（见 §4）。

---

## 1. 根因与定位

- [x] 「一轮复核慢」**不是**读数伪像（我此前说的 **49 min**）已更正

      > **读数**：我量的 **48.1 min** 是 `manager_v4_reviews` 两条 `reviewed_at` 之差 —— 那是**停机窗口**
      > （**12:01 → 12:49**），不是复核耗时。稳态实测：**32 次/分** ⇒ 一轮 **≈ 5.4 min**。

- [x] 「一个闭环只有**一个当前复核结果**」已确证为**系统语义**（不是缺陷）

      > **读数**：`manager_v4_reviews` 主键 `(network, chain_id, protocol, scope_key, loop_key)`，**174** 行，
      > 后一次复核**覆盖**前一次；`manager_v4_review_broadcast_requests` 同样**单槽**（每闭环 **1** 行）
      > ⇒ 「轮次」在库里**不存在**，只有「每个闭环的当前结果」。

- [x] 陈旧闭环会**撤掉**它的广播请求（利润证据随之不可恢复）已确证

      > **读数**：日志 `[review-stale] … 19 of 174 closed loops went quiet, 1 broadcast requests withdrawn`；
      > 代码 `review_manager/src/lib.rs:1579 mark_stale_reviews` → `:1948 withdraw_broadcast_request`。

- [x] **一轮慢的真实原因**已定位到**行**

      > **读数**：① `execution.rs:55 throttle_rpc_call` 按**地址**限速 **0.2 s** ⇒ **≤ 5 次/秒**，
      > 且**持锁 sleep** ⇒ 全线程在**同一地址**排队；实测复核 **32 次/分** ＝ **5 ÷ 9.4**，
      > 与限速上限**吻合** ⇒ **并发（16）不是瓶颈，链上读取速率才是**。
      > ② 扫描线程 **240 / 240** 采样在**投递**里（串行）。
      > ③ 数据库每次操作 **13.8 ms**（原生 **0.22 ms**），占运行时 **≈ 47%** CPU。

- [x] 「链上广播 = 0」的**原因**已定位到**具体理由码**（不是链路断裂）

      > **读数**：`manager_v4_reviews` ⇒ `completed` **0**；理由码 `runArbitrage returned ok=false` **131**、
      > `signed PnL is missing or incomplete` **24**、`ladder_not_measured:single_tick_capacity_below_min_probe` **2**、
      > `not_reviewed_since:<t>` **17**。正利润只有 **5** 行，其中 **2** 行有 **3/3** 跳限值，
      > 量级 **1.2e-05** / **4.5e-07** USDG ⇒ **灰尘级**。

---

## 2. 改动项（`M-7` ＋ `M-13`）

- [x] 广播路由不再被**组级标签**一票否决

      > **读数**：`worker_pool.rs:4688 route_reaches_the_execution_queue` ＝
      > `payload.status == "profitable_amount_found" || review_authorized_broadcast(payload)`；
      > `:4641 should_route_to_execution_queue` 改用它（**唯一**生产调用点 `:4980 enqueue_execution_from_review`）。

- [x] **没有**证明时**仍然**要组级标签（不是无条件放开）

      > **读数**：单测 `a_route_without_the_review_proof_still_needs_the_kernel_label`。

- [x] 组级标签不盈利时，复核证明**仍能**入队

      > **读数**：单测 `a_review_authorized_route_reaches_the_queue_beneath_an_unprofitable_group_label`。

- [x] **启动等待置 0 真正生效**（`0` 不再被当成「未设置」）

      > **读数**：`dex_opportunity_scanner_rust_mixin.py:803` ⇒
      > `per_scan_override <= 0 ⇒ batch_limit = resident_room`（**0 ＝ 不等待**，一次全量入组）；
      > 缺省路径（`or 16`）**一字未改**。**实盘读数（22:05:01 起的新实例）**：
      > `pendingTasks = 132` / `residentWorkers = 132` / `createdTotal = 132` /
      > `reviewQueue.claimed = 132` ⇒ **一次扫描把整个目录全量入组**（分批斜坡下 `claimed` 只会逐批上涨）。

- [x] 单测：零覆盖 ⇒ **一次扫描**接纳**全部**组

      > **读数**：`test_a_zero_per_scan_override_admits_every_group_in_one_scan`。

---

## 3. 回归

- [x] 运行时内核单测

      > **读数**：`cargo test --offline -p flashloan-rs --lib` ⇒ **267 passed / 0 failed**
      > （基线 **265** ＋ 新 **2**）。

- [x] 运行时接线（Python）单测

      > **读数**：`pytest flashloan/app_runtime/tests/unit/test_rust_closed_loop_worker_bridge.py -q -k "group or resident"` ⇒ **25 passed**。

---

## 4. 部署闸门

- [x] 部署参数：每扫描投递 **32 → 0**、常驻 **256**、复核并发 **32**

      > **读数**：`supervise_runtime_t401_20261124.py` ⇒ `DEX_CLOSED_LOOP_MAX_NEW_GROUPS_PER_SCAN = "0"`、
      > `DEX_CLOSED_LOOP_MAX_RESIDENT_GROUPS = "256"`、`DEX_CLOSED_LOOP_MAX_WORKERS = "32"`；
      > `start_runtime_t401_20261124.ps1` ⇒ 同样 **0** ＋ **256** ＋ **32**。
      > **★ 订正**：原先写 **48**，但 Python 侧 `max_workers_override()` 取 `min(CLOSED_LOOP_WORKER_CEILING = 32, 48) = 32`
      > ⇒ **生效值本来就是 32**，实盘读数 `concurrencyLimit = 32`；本轮把**部署值**订正为**如实值**
      > ⇒ **运行实例无需重启**（生效值未变）。**提并发不是提速杠杆**：实测 **16 → 48** 吞吐**不变**（≈ **34 次/分**）。

- [x] 新二进制上线（**新旧二进制无中间态**）

      > **读数**：旧 DLL 备份 `flashloan/rs-src/target/release/flashloan_rs.dll.bak-20261126-broadcastgate`
      > （**18,437,632 B**）；新 DLL **21:34:43** 构建，SHA256 `81ADFAEF…` ≠ 旧 `F0111954…`。

- [x] 看护 ＋ runtime 起来（新实例）

      > **读数**：看护 **9360** ／ runtime **19040**（**21:34:55** 起，端口 **5000**）；
      > 二次：**看护 19888** ／ runtime **20656**（**22:05:01** 起，端口 **5000**）＝ **当前在跑的实例**。

- [x] **启动即全量复核**生效（一次扫描接纳全部组）

      > **读数**：新实例启动后**首个 5 分钟**内 `workerCount` 由 **0** 升到 **111**（旧实例在 **32**/扫描的
      > 斜坡下同一时刻只有 **24** 个闭环被触达）；实测启动到**第一条**复核落地 **8 min 19 s**
      > （**21:34:56 → 21:43:15**），此后稳定 **32 次/分**。**当前实例（22:05:01 起）**：
      > `claimed = 132` / `pendingTasks = 132` ⇒ **一次扫描全量入组**；第一条复核 **22:13:03** 落地
      > （启动后 **8 min 2 s**）⇒ **这 8 分钟不是「等待参数」，是首轮投递自身代价**（投递线程 **240/240**
      > 采样 **100%** 串行）；此后复核**行重写**速率 **24 – 35 次/分**、**174** 行走完一圈 **≈ 7 min**。

- [x] **过期数据清理在启动后自动开始**（用户口径「**清理数据自动会开始的**」）

      > **读数**：`[review-stale] robinhood chain 4663: 73 of 174 closed loops went quiet, 0 broadcast requests withdrawn`
      > —— **陈旧清扫**（`worker_pool.rs:2362 stale_review_sweep_block`，**每 scope 60 s** 一次）在复核**之前**
      > 自动运行：把**过期**闭环标成 `not_reviewed_since:` ＋ 撤下它的**广播请求** ⇒ **旧的盈利数据不会留着冒充有效**。
      > 复核**行**本身则是**单槽覆盖**（`DELETE` ＋ `INSERT`）：同一 `loop_key` 的 `created_at` 随复核前进
      > ⇒ **每个闭环只有一个当前复核结果**（用户口径 U1 **实测成立**）。

- [ ] 实盘读数：`manager_v4_review_broadcast_requests` **> 0 行**

      > **待市场条件**（**不是**链路问题）：复核 **0** 条到达 `completed`；正利润量级
      > **1.2e-05** / **4.5e-07** USDG（灰尘级）；被挡在 `state_transition_gate_failed` ／
      > `single_tick_capacity_below_min_probe` ／ 链上合约自己的 `NoProfitableRoute`。

---

## 5. 遗留（**不代任何其它任务关闭**）

- [x] **单地址限速**：**仍然生效**，但**已不是当前一轮的瓶颈** ⇒ 结论**按实测订正**（`M-13` ＋ `M-7` 后续轮次 · 2026-11-28）

      > **读数**（`D-233` §3）：限速**仍在**生效 —— `flashloan/app_runtime/.env` 有 `DEX_RPC_MIN_INTERVAL_SECONDS = 0.2`
      > ⇒ 每**地址** ≤ **5 次/秒**，且 `flashloan/rs-src/src/execution.rs:55-78` 的 `throttle_rpc_call` **持锁 sleep**
      > （锁在 `sleep` 期间**不放**）⇒ 同地址**全局排队**。
      > **但**：当前目录只有 **8** 条闭环 ⇒ 一轮约 **75** 次链上读取 ⇒ 限速下限 **≈ 15 s**；
      > 而**实测**一轮 **125 – 332 s**（均值 **236 s**）、编排器周期 **283 s**；
      > **实测**复核吞吐 **13.8 次/分**（689 → 715 条 / 113 s）**远低于**限速天花板 **32 次/分**
      > ⇒ 限速只占一轮的 **≈ 7 %** ⇒ 原措辞「**一轮 5.4 min** 的硬上限」是**目录 174 条**时测的
      > ⇒ 本框**按实测改写**，**不按原措辞勾选**。
      > ⇒ **「提并发无效」仍然成立**，但**理由换了**：不是顶到了限速，而是**端点会回 429**
      > （quiknode 在 conc 32 下 **17 / 32** 被限速）＋**编排器的轮次节拍本身**。
      > **没有**为提速而**放宽**限速语义（`throttle_rpc_call` 一字未改）。

- [ ] **数据库连接开销**占运行时 **≈ 47%** CPU（`M-10`）

      > **本轮未复现**（2026-11-28 · `D-233` §5）：本机**没有** `py-spy`
      > （`py-spy --version` 不存在、`python -m py_spy` 无此模块）⇒ **同口径**
      > （剖面 `connect` / `realpath` / `stat` 采样）**无法复测**
      > ⇒ 该框**保持未勾选**，2026-11-26 那**一次**剖面读数（**≈ 47 %**）**如实保留**。

- [x] 端点容量：publicnode **conc 32** 时 **5.61 次/秒**、**0 错误**；另有 **3** 个端点已废（`M-13` 后续轮次 · 2026-11-28）

      > **读数**（`D-233` §1 / §2）：活 **2 / 5** —— publicnode **200 / 1531 ms**、quiknode **200 / 843 ms**；
      > 废 **3** —— `rpc.mainnet.chain.robinhood.com` **403**、`rpc.ordofi.network` **DNS 死**、Alchemy **429**。
      > conc **32**：publicnode **5.61 次/秒**、中位 **755.1 ms**、p95 **5574.2 ms**、**0 / 32** 错误；
      > quiknode **35.40 次/秒** 但 **17 / 32 = 429**（**加并发换来的是限速错误，不是更快**）。
      > **口径订正**：原写的 **10.47 次/秒** 是 2026-11-26 的读数 ⇒ 本框**按实测改写**为 **5.61 次/秒**。

- [x] `runtimeCore.state = error`：`consumersNeedRebuild expected=false actual=true` → **已恢复**（`M-13` 另一条线 · 2026-11-28）

      > **读数**（`D-233` §4）：`runtimeCore.state = 'partial'`（**不是** `error`）、`lastError = None`、
      > `generation = 190`（快照**持续换代**，不是停在旧代次）；
      > `manager_v4_closed_loop_library_state.consumers_need_rebuild = **0**`（生产库**只读**）
      > ⇒ 该遗留**已由 `M-13` 的代次回执修法关闭**（**归属 `M-13`**，本任务**只登记观察读数**、
      > **不代为关闭** `M-13` 的验收项）。

