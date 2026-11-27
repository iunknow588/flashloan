# D-205 `T-广核.02`｜闭环消费者的重建信号滞后 · 验收清单

> **本文件是 `T-广核.02` 的全部验收框**。编号 `AC-广核.02` 属**纯 ID**。
> 每条勾选**必须**有实测读数支撑；**不得**凭推断勾选。

**验收前置**：`M-13` ＋ `M-6` 现有单测全绿

**本轮状态（2026-11-27 00:0x）**：**代码项全部闭合 · 已部署 · 实盘读数齐 · G2 已实盘复现**（证据：`docs/D-206`）。

---

## 1. 根因与定位（**已完成 · 有读数**）

- [x] 实盘症状已确认：RuntimeCore **持续拒收快照**

      > **读数**：`/api/status` ⇒ `chain_runtime.runtimeCore.state = partial`、`lastError` =
      > `RuntimeCore rejected a mixed manager snapshot: [{"component":"closed_loop","field":"consumersNeedRebuild","expected":"false","actual":"true"}]`；
      > `recentEvents` 里 `snapshot_rejected` **每 15 s** 一条（**22:19:17 → 22:25:22**），
      > 而最后一条 `snapshot_committed` 停在 **22:17:06**（第 **21** 代）。

- [x] 标志的**置位**条件已定位

      > **读数**：`closed_loop_manager/src/lib.rs:1100` ⇒ `consumers_need_rebuild: checksum_changed`
      > （目录**变了**就置 1）。

- [x] 标志的**清除**条件已定位（**且只有这一处**）

      > **读数**：`lib.rs:1106-1107` ⇒ `else if previous.consumers_need_rebuild { clear_consumers_need_rebuild(...) }`，
      > 且该分支只在 `checksum_changed == false`（**下一次**物化发现目录**没变**）时进入；
      > `clear_consumers_need_rebuild`（`:4545`）全文件**仅此一处**调用 ⇒ **没有任何其它清除路径**。

- [x] **★ 订正**：`last_rebuilt_at` **不是**消费者的重建时刻（上一轮此处**判断错了**）

      > **读数**：`last_rebuilt_at` 由 `closed_loop_manager/src/lib.rs:4281 replace_closed_loop_rows`
      > → `lib.rs:3571 last_rebuilt_at = chrono::Utc::now()` **在写目录行的同一事务里**写入 ⇒
      > 实测（**23:41**）它与 `manager_v4_closed_loops.updated_at`、`library_state.generated_at` **三者恒等**
      > （全部 **15:34:27.741556900Z**）⇒ 它是**生产者写入时刻**，不是消费者重建时刻。

- [x] 消费者真正重建的时刻**已量出**（落后目录 **4 min 28 s**）

      > **读数**：目录换代 **15:34:27**（代次 **44**）→ 消费者**第一次用代次 44 复核**在 **15:38:55**
      > （复核行按实测代次分裂：代次 43 **115** 行／代次 44 **48** 行）。

- [x] 标志**滞后**的事实已复现（部署前它**正在说谎**）

      > **读数（23:41 · 部署前）**：目录仍是代次 **44**、`generated_at = 15:34:27.741556900Z`，
      > 而消费者**15:38:55** 就已用代次 44 复核过 ⇒ 消费者**已重建**；
      > **`consumers_need_rebuild` 仍然是 `1`** ⇒ 标志**滞后**（旧逻辑要等「下一次物化恰好没变化」）。

- [x] 拒收链条的两端已定位

      > **读数**：`composite_manager/src/lib.rs:1733-1740`（把该标志判为 `ConsistencyIssue`）
      > ⇒ `rs-src/src/runtime_core.rs:2232-2248`（`issues` 非空 ⇒ `Err(InconsistentSnapshot)` ⇒ **拒收整份快照**）。

---

## 2. 改动项（`M-13` ＋ `M-6`）

- [x] 清除信号换成「消费者**确实**重建过」（**回执**：发布层用**实测代次**回执，所有者按代次护栏清标志）

      > **读数**：`closed_loop_manager/src/lib.rs` 新增 `acknowledge_consumers_rebuilt(...)`；
      > `review_publish.rs` 新增 `acknowledge_the_rebuilt_consumers(...)`（发布成功后回执）。

- [x] 回执**对不上代次** ⇒ 保持 **1**（**不猜、不降级**）

      > **读数**：单测 `an_acknowledgement_of_another_generation_clears_nothing`（别的代次／别的校验和都清不掉）
      > ＋ `an_acknowledgement_of_a_generation_the_library_left_clears_nothing`（库换代后旧回执清不掉）。

- [x] 单测：目录变 ⇒ **1** ／ 消费者未重建 ⇒ **仍 1** ／ 消费者已重建 ⇒ **0** ／ 再变 ⇒ 又 **1**

      > **读数**：`closed-loop-manager` **51 passed / 0 failed**（新 **3**）；`flashloan-rs --lib` **269 / 0**（新 **2**）。

- [x] **没有**改 `runtime_core` / `composite_manager` 里的 issue 判定（哨兵**保留**）

      > **读数**：`git diff --stat` 这两个文件**无改动**；`composite_manager` 的 **20 + 6** 个单测**未改**仍全绿。

- [x] **没有**改任何广播门禁判据、**没有**碰 git

      > **读数**：`manager_v4_review_broadcast_requests` 仍 **0** 行、`manager_cow_execution_attempts` 仍 **0** 行（**市场侧**，未放宽任何判据）。

---

## 3. 回归

- [x] `cd flashloan/tools && cargo test --offline -p closed-loop-manager -p composite-manager`（**0 failed**）

      > **读数**：`closed-loop-manager` **51 / 0**；`composite-manager` **20 + 6 / 0**。

- [x] `cd flashloan/rs-src && cargo test --offline -p flashloan-rs --lib`（**0 failed**）

      > **读数**：**269 / 0**（基线 **267** ＋ 新 **2**）。

---

## 4. 实盘读数（**部署闸门**）

- [x] 目录重建后**立刻**读 `manager_v4_closed_loop_library_state.consumers_need_rebuild` ⇒ **0**（不再等下一次物化）

      > **读数**：部署前标志 **1** ⇒ 新二进制上线后**第一次发布**即回执，
      > `library_state.updated_at = **15:48:42.971665400Z**`（＝ **23:48:42**）、`consumers_need_rebuild = **0**`；
      > 连续采样（**23:5x – 00:0x**）恒为 **0**；回执失败日志 **0** 条。

- [x] `runtimeCore.lastError` 变 **None**；`snapshot_committed` 代次**继续前进**

      > **读数**：**23:55:55** `lastError = None`、代次 **11** ⇒ **23:59:44** 代次 **29** ⇒ **00:00:18** 代次 **32**
      > ⇒ **≈ 12.5 s 一代，连续提交**（部署前：标志为 1 的整段时间**拒收**、代次冻结在 **21**）。

- [x] 目录再变一次 ⇒ 标志**又**是 **1**，且**消费者追上之前** RuntimeCore **仍然拒收**（G2）

      > **读数（实盘 · 代次 45 · 采样器每 ≈ 11 s 一条）**：目录换代 **`16:02:13.388166400Z`**（＝ **00:02:13**）
      > ⇒ 标志 **1**（**00:02:21** 起连续 **7** 条采样恒为 1）；此刻 RuntimeCore **持续拒收**，
      > `lastError` **同时**挂着**两条** issue ——
      > `consumersNeedRebuild expected=false actual=true` **与**
      > `review.closedLoopVersion expected=45 actual=44` ＋ `review.closedLoopChecksum` 不匹配
      > ⇒ **混合代次**的快照**不收**（**哨兵没被拆**）；
      > 消费者追上（首条代次 **45** 复核 **`16:03:20.427541Z`**）⇒ 回执写入 **`16:03:20.563979100Z`**（**136 ms** 后）
      > ⇒ **00:03:27** 起标志 **0**、`lastError` 变 **None**、快照代次 **46 → 48** 继续前进。
      > **第二次独立复现（代次 46）**：目录换代 **16:20:58.950756600Z** ⇒ 标志 **1**（`generated_at` ＝ `updated_at`）
      > ⇒ 消费者首条代次 **46** 复核 **16:23:26.747187800Z** ⇒ 回执 **133 ms** 后写入 ⇒ 标志 **0**；
      > 换代瞬间 `lastError` 挂 `upstreamFingerprint` 不一致 ⇒ **不收** ⇒ **00:24:40** 自愈（`lastError = None`、代次 **76 → 78**）。
      > 单测 `an_acknowledgement_of_a_generation_the_library_left_clears_nothing` 同向钉住该行为。

---

## 5. 遗留（**不代任何其它任务关闭**）

- [ ] S1 单地址限速（`T-广核.01` `R1`）／S2 数据库连接开销（`R3`）／S3 正利润灰尘级（`R4`）／S4 天花板不对称（`R6`）／S5 启动 8 min 拆分未测（`R7`）

      > **后续轮次读数（2026-11-28 · `D-233`）** —— **S1 有读数并被订正；S2 / S5 本机无法复测；S3 仍是市场条件；S4 维持原裁定**：
      > · **S1**：限速**仍在**生效（`.env` 的 `DEX_RPC_MIN_INTERVAL_SECONDS = 0.2` ⇒ 每**地址** ≤ **5 次/秒**，
      >   `throttle_rpc_call` **持锁 sleep**）；**但**当前目录只有 **8** 条闭环 ⇒ 限速下限 **≈ 15 s**，
      >   而**实测**一轮 **125 – 332 s**（均值 **236 s**）、**实测**吞吐 **13.8 次/分**（天花板 **32 次/分**）
      >   ⇒ 限速只占一轮 **≈ 7 %** ⇒ **已不是当前瓶颈**；**「提并发无效」仍成立**（conc 32 ⇒ quiknode **17 / 32** 被 429）。
      > · **S2**：本机**没有** `py-spy` ⇒ **同口径**复测**做不到** ⇒ **未闭合**（原读数 **≈ 47 % CPU** 如实保留）。
      > · **S3**：当前目录 `positive = **0**`；窗口级 `positive = **7**` 且**全部不在当前目录** ⇒ **仍是市场条件**。
      > · **S4**：**不对称依然存在**（**32** vs **256**，实盘 `concurrencyLimit = 32`）；当前吞吐 **13.8 次/分**
      >   ⇒ 「等有能绕开单地址限速的读法再动」的前提**仍不成立** ⇒ **维持不提并发**。
      > · **S5**：**总时长** **13 min 22 s**（启动 → 首次接受复核命令）vs 稳态 **≈ 4.7 min**；
      >   但**精确拆分**需要栈采样，本机**没有** `py-spy` ⇒ **拆分仍未测** ⇒ **不拿估计当读数**。
      > · 另：`consumers_need_rebuild = **0**`、`runtimeCore.state = 'partial'`、`lastError = None`
      >   （**只读**）⇒ 本任务 §4 的**代次回执修法在实盘持续成立**。

- [x] **S6（新增）** 消费者重建仍落后目录 **数十秒 – 数分钟**（worker pool 目录缓存按 TTL **300 s** 刷新，**不**按代次变化刷新）

      > **闭合（由 `T-核.07` · `M-13` 代次感知 ＋ 单飞关闭 · 2026-11-27）**：
      > **读数**：目录代次 **53** 写于 **18:06:16.447762400Z** ⇒ 消费者快照 **18:06:37.332386800Z** 换代
      > ⇒ **滞后 20.88 s**（旧：最多 **300 s**，实测三例 **4 min 28 s** ／ **67 s** ／ **2 min 27.8 s**）。
      > **边界**：残差 = 代次比较周期（**10 s**）＋ 重建耗时 ⇒ **上界不再是 TTL**。
      > 证据：`plan/tasks/P-01-池子重构/tasks/T-核.07/docs/D-210-复核轮停滞的根因与快照单飞_20261127.md`。

      > **读数（三次实测 · 不是一个常数）**：代次 **44** ⇒ 目录 **15:34:27** → 消费者首次用代次 44 复核 **15:38:55**（**4 min 28 s**）；
      > 代次 **45** ⇒ 目录 **16:02:13** → 消费者首次用代次 45 复核 **16:03:20**（**67 s**）；
      > 代次 **46** ⇒ 目录 **16:20:58** → 消费者首次用代次 46 复核 **16:23:26**（**2 min 27.8 s**）
      > （`worker_pool.rs:836 CONTEXT_CACHE_TTL_SECONDS = 300`）⇒ 落后时长**随缓存刷新点浮动**。
      > **归属**：`M-13`（**不在本轮**：要改 `worker_pool.rs`（他人 2785 行在途改动）且动热路径）。详见 `docs/D-206` §7。
