# D-199 `T-广核.01`｜广播路由闸门 · 启动即全量复核 · 一轮复核耗时的实测拆解 · 开发计划

> **本文件是 `T-广核.01` 自己的开发计划**（自包含）。
> 编号 `T-广核.01` / `AC-广核.01` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。

---

## 1. 触发（用户口径 · 2026-11-26）

| 编号 | 用户口径 | 落点 |
|---|---|---|
| U1 | 「**任何一个闭环，第二次复核出现结果，必然覆盖前一次闭环的结果**，这样，每个闭环**仅仅有一个当前的复核结果**」 | 承认这是**系统语义**，不是缺陷：库的复核行按 `(network, chain_id, protocol, scope_key, loop_key)` **单槽**，后一次复核**覆盖**前一次 ⇒ 复核**没有轮次**，只有「每个闭环的当前结果」 |
| U2 | 「**盈利数据随时会失效，请注意时效问题**。让系统**启动时立即执行一次复核**更新过期数据」 | 启动即全量复核：把「每轮投递多少闭环」的等待**置 0**，启动后**一次扫描**把**整个目录**投进复核池 |
| U3 | 「**启动时等待时间置 0**，清理数据自动会开始的」 | 同 U2 的**具体做法**：等待参数置 0，**不再等**分批投递 |
| U4 | 「多复核工作线程并发，**不可能需要这么长时间**。请分析**一轮复核需要这么长时间的原因**」 | 实测拆解（见 §2 ＋ `docs/D-202-…`） |
| U5 | 「确保当前的系统能够**持续产生链上广播交易记录**」 | 打通「复核 ⇒ 广播请求 ⇒ 执行队列 ⇒ 链上尝试」这条链，并把**仍然为 0** 的原因定位到**具体闸门** |

---

## 2. 实测读数（只读 · 2026-11-26 20:00–22:10）

### 2.1 复核吞吐与「一轮」的真实长度

| 项 | 读数 | 出处 |
|---|---|---|
| 复核表行数 | **174** 行，**单槽**（`loop_key` 唯一）⇒ 后一次覆盖前一次 | `manager_v4_reviews` 主键 |
| 复核吞吐（稳态） | **32 次/分**（`reviews_done` **601 → 645**，历时 **76 s**）；新实例复核**行重写**速率 **24 – 35 次/分**（实测 **1 min** 内 **25** 行／**2 min** 内 **47** 行／**5 min** 内 **110** 行／**10 min** 内 **142** 行） | `probe_review_throughput_20261126.py` ＋ 只读计数 |
| 一轮 174 个闭环 | **≈ 5.4 – 7 min** | 174 ÷ 32（**行重写**实测 **10 min** 内 **142** 行） |
| **启动到第一条复核** | runtime **22:05:01** 起 ⇒ 第一条复核 **22:13:03** 落地 ＝ **8 min 2 s**（前一代 **21:34:56 → 21:43:15** ＝ **8 min 19 s**）⇒ **这 8 分钟不是「等待参数」**，是**首轮投递自身代价**（投递线程 **240/240** 采样 **100%** 在 `_enqueue_closed_loop_groups_from_payload`，见 §2.2 C2） | `manager_v4_reviews.reviewed_at` ＋ `/api/status` 计数 |
| 复核并发上限 | 新实例 `activeConcurrency = 32`、`concurrencyLimit = 32`（前一代 **16**）—— **算子值 48 被 Python 天花板截成 32** | `/api/status` ⇒ `dex_opportunity_scanner.closedLoop` |
| 并发的来源 | `CLOSED_LOOP_WORKER_DEFAULT = 16`（`closed_loop_state_machine.py:2647`）、**Python 天花板 `CLOSED_LOOP_WORKER_CEILING = 32`**（`:2648`，`max_workers_override()` 取 `min(32, 48) = 32`）；Rust 上限 **256**（`worker_pool.rs:845`）；`.env` **未覆盖** | 源码 ＋ `.env` |
| **「等待 = 0」生效读数** | 新实例 `pendingTasks = 132`、`residentWorkers = 132`、`createdTotal = 132`、`reviewQueue.claimed = 132` ⇒ **一次扫描把整个目录全量入组**（分批斜坡下 `claimed` 只会逐批上涨） | `/api/status`（`probe_scanner_startup_20261126.py`） |
| **「清理自动开始」读数** | `[review-stale] robinhood chain 4663: 73 of 174 closed loops went quiet, 0 broadcast requests withdrawn`（**每 scope 60 s** 一次，失败即失败不降级） | `runtime_stderr.log` ＋ `worker_pool.rs:2362` |

### 2.2 「一轮为什么慢」——三个**已定位**的原因（**不是**复核线程不够）

| 编号 | 原因 | 实测读数 |
|---|---|---|
| **C1** | **链上读取被单地址限速**：`execution.rs:55 throttle_rpc_call` 按**地址**限速，最小间隔 `DEX_RPC_MIN_INTERVAL_SECONDS = 0.2` ⇒ **≤ 5 次/秒**；而且它在**持锁期间 sleep** ⇒ 所有线程在**同一个地址**上排队。复核一次约 **9.4** 次链上读取 ⇒ **5 ÷ 9.4 ≈ 0.53 次/秒 ≈ 32 次/分** —— 与实测吞吐**吻合** | `execution.rs:55`；`.env`；§2.1 实测 32 次/分 |
| **C2** | **投递是串行的**：扫描线程 **240 / 240** 个采样（**100%**）都在 `_enqueue_closed_loop_groups_from_payload` 里 | `py-spy record` 90 s / 1105 采样 |
| **C3** | **数据库每次操作都重开连接**：`storage_business_io.query()` 实测 **13.8 ms**，原生连接 + 一条 `SELECT` 只要 **0.22 ms**（**63×**）。剖面里 `connect` **201**（18.2%）＋ `realpath` **196**（17.7%）＋ 路径 `stat` **127**（11.5%）＝ **≈ 47%** 的 CPU 花在「开连接 ＋ 解析路径」，真正执行 SQL 只占 **59**（5.3%） | `py-spy` ＋ 基准测试（见 `docs/D-202-…` §3） |

**结论（直接回答 U4）**：一轮慢**不是**因为复核线程不够并发。并发是 **16**，而**链上读取速率**（单地址 ≤ 5 次/秒）先到顶 ⇒ 再加线程只会让它们在限速锁上排队。**真正该加的是链上读取的并行度（多个地址）与每次复核的读取次数**，不是线程数。

### 2.3 「链上广播仍然是 0」——原因**不在**路由闸门

| 项 | 读数 |
|---|---|
| `manager_v4_review_broadcast_requests` | **0 行** |
| `manager_cow_execution_attempts` | **0 行** |
| `manager_v4_reviews` | `not_executable` **132** ／ `data_incomplete` **42** ／ **`completed` 0** |
| 主要理由码 | `runArbitrage returned ok=false` **131**；`signed PnL is missing or incomplete` **24**；`ladder_not_measured:single_tick_capacity_below_min_probe` **2**；`not_reviewed_since:<t>` **17** |
| 正利润行 | 只有 **5** 行 `signed_pnl_raw > 0`；其中 **2** 行有实测的 **3/3** 跳限值 |
| 那 2 行的量级 | `0x5fc5360d…:0x000…0` USDG `pnl_raw = 0xc` ＝ **1.2e-05 USDG**；`0x7066a64c…:0x5fc5360d…` PFE `pnl_raw = 0x690ae28b32` ＝ **4.5e-07 USDG** |
| 它们被谁挡 | 前者 `state_transition_gate_failed:hop_1_failed:tick_range_changed`；后者 `single_tick_capacity_below_min_probe`（`execution.rs`：容量小于最小探测量 ⇒ **拒绝探测**） |

⇒ **闸门在按设计工作**：链上合约自己回退（`NoProfitableRoute`）、容量小于可探测量、预览态与当前态 tick 区间漂移 —— 三者都是**真实的市场/容量事实**，不是链路断裂。而正利润的量级是**灰尘级**（**1.2e-05** / **4.5e-07** USDG），相对手续费与 gas 是**负收益**。

---

## 3. 目标（可验收）

| 编号 | 目标 | 判据 |
|---|---|---|
| **G1** | **启动等待置 0 真正生效** | 启动后**一次扫描**把**整个目录**投进复核池（不再分批） |
| **G2** | **广播路由不再被组级标签一票否决** | 复核**证明**能到达执行队列；无证明时**仍然**要内核标签 |
| **G3** | **一轮复核的耗时被拆解成可测量的量** | 给出「投递 / 链上读取 / 数据库」三段的**实测**占比，而不是估计 |
| **G4** | **并发上限可被算子覆盖** | 复核并发从 **16** 变成**可配置**，并给出**依据**（不是拍脑袋调大） |
| **G5** | **广播为 0 的原因被定位到具体闸门** | 读数指向**具体理由码**，并说明**哪些不是缺陷** |

---

## 4. 改动计划（按模块）

### 4.1 `M-7`（广播层） ＋ `M-13`（运行时内核与运行时接线）

| 步 | 改动 | 文件 | 状态 |
|---|---|---|---|
| 4.1.1 | **新增** `route_reaches_the_execution_queue(payload)` ＝ `payload.status == "profitable_amount_found"` **或** `review_authorized_broadcast(payload)`；`should_route_to_execution_queue` 改用它 | `flashloan/rs-src/src/worker_pool.rs:4688` ／ `:4641` | ✅ 已落地 |
| 4.1.2 | **新增** 2 条单测：复核证明**在组级标签不盈利时**仍入队 ／ **没有**证明时**仍需要**内核标签 | `flashloan/rs-src/src/worker_pool.rs` | ✅ 已落地 |
| 4.1.3 | **修复** `per_scan_override <= 0` 被 `or 16` 丢弃 ⇒ **0 = 不等待**，一次全量入组 | `flashloan/src/dex/runtime/dex_opportunity_scanner_rust_mixin.py:803` | ✅ 已落地 |
| 4.1.4 | **新增** 单测：零覆盖 ⇒ **一次扫描**接纳**全部**组 | `flashloan/app_runtime/tests/unit/test_rust_closed_loop_worker_bridge.py` | ✅ 已落地 |
| 4.1.5 | 部署参数：`DEX_CLOSED_LOOP_MAX_NEW_GROUPS_PER_SCAN` **32 → 0**、`DEX_CLOSED_LOOP_MAX_RESIDENT_GROUPS = 256`、`DEX_CLOSED_LOOP_MAX_WORKERS = **32**`（**不是 48**：Python 天花板 `CLOSED_LOOP_WORKER_CEILING = 32` 会把 48 截成 32 ⇒ **生效值本来就是 32**；本轮把部署值订正为**如实值**，**运行实例无需重启**） | `plan/runtime_evidence/tools/supervise_runtime_t401_20261124.py` ／ `start_runtime_t401_20261124.ps1` | ✅ 已落地 |

### 4.2 **不做**（本轮明确划界 · 见 `D-200-SCOPE_GUARD.md` §2）

| 编号 | 不做 | 理由 |
|---|---|---|
| N1 | **不改**任何广播**门禁判据**（费率、利润阈值、单 tick 容量、经济性底线） | 那是**市场事实**的投影；本轮只保证「链路能通」，不**放宽**判据 |
| N2 | **不改** `throttle_rpc_call` 的限速语义（**≤ 5 次/秒**） | 该限速是**保护公共 RPC** 的既有约束；提高它需要**先**证明端点承受得住（见 §6 R2） |
| N3 | **不改**数据库连接层的复用策略 | 属 `M-10`（存储层）；本轮只**测出**它占 **47%** CPU，作为后续任务的**依据**登记 |

---

## 5. 回归范围（= 改动模块集合）

| 套件 | 命令 | 读数（2026-11-26） |
|---|---|---|
| 运行时内核 | `cargo test --offline -p flashloan-rs --lib` | **267 passed / 0 failed**（基线 **265** ＋ 新 **2**） |
| 运行时接线（Python） | `pytest flashloan/app_runtime/tests/unit/test_rust_closed_loop_worker_bridge.py -q -k "group or resident"` | **25 passed** |

**可直接粘贴的命令**（守卫按「命令必须整行开头」核对）：

```text
cd flashloan/rs-src && cargo test --offline -p flashloan-rs --lib
cd flashloan/rs-src && cargo check --offline --all-targets
python -m pytest flashloan/app_runtime/tests/unit/test_rust_closed_loop_worker_bridge.py -q -k "group or resident"
```

---

## 6. 遗留（**不代任何其它任务关闭**）

| 编号 | 遗留 | 归属 | 依据 |
|---|---|---|---|
| R1 | **链上读取的单地址限速**（≤ 5 次/秒）是**一轮 5.4 min** 的直接上限；提并发**无效** | `M-13` ＋ `M-7`（后续轮次） | §2.2 C1；端点容量实测见 `docs/D-202-…` §4 |
| R2 | 端点容量实测：publicnode 在 **conc 32** 下 **10.47 次/秒**、**0 错误**；另有 **3** 个端点已废（**403** ／ DNS 失败 ／ **429**） | 同上 | `probe_rpc_capacity_20261126.py` |
| R3 | **数据库连接开销**（每次操作 **13.8 ms**，占运行时 **≈ 47%** CPU） | `M-10`（存储层） | §2.2 C3 |
| R4 | **正利润是灰尘级**（**1.2e-05** / **4.5e-07** USDG）⇒ 即使门禁全通，**一笔真实广播在手续费上也是亏的** | **市场条件**（不可由代码解决） | §2.3 |
| R5 | `runtimeCore.state = error`：`RuntimeCore rejected a mixed manager snapshot: [{closed_loop.consumersNeedRebuild expected=false actual=true}]` | `M-13`（另一条线） | `/api/status` 读数（**本轮不代它关闭**） |
| R6 | **Python 天花板 32 vs Rust 上限 256 的不对称**：`max_workers_override()` 把算子值截到 **32** ⇒ 实盘 `concurrencyLimit = 32`，而 Rust 池本身支持到 **256**（`worker_pool.rs:845`）。**本轮不提并发**（实测 **16 → 48** 吞吐**不变**，瓶颈是**单地址限速**）⇒ 登记为**证据**，等有**能绕开单地址限速的读法**再动 | `M-13`（后续轮次） | §2.1「复核并发上限」行；`docs/D-202` §2.1 候选 A |
| R7 | 启动到**第一条**复核 **8 min** 里，「payload 构造」与「逐组投递」的**精确拆分未测**（只测到投递线程 **240/240** 采样 **100%** 串行）⇒ **不拿估计当读数** | `M-13`（后续轮次） | `docs/D-202` §2.4 订正 |

