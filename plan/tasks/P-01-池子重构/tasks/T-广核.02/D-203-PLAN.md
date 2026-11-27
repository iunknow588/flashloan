# D-203 `T-广核.02`｜闭环消费者的重建信号滞后 · RuntimeCore 快照被拒 · 开发计划

> **本文件是 `T-广核.02` 自己的开发计划**（自包含）。
> 编号 `T-广核.02` / `AC-广核.02` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。

---

## 1. 触发（实盘读数 · 只读 · 2026-11-26 22:05–22:55）

运行中的实例（看护 **19888** ／ runtime **20656**，**22:05:01** 起）持续报：

```text
RuntimeCore rejected a mixed manager snapshot: [
  {"component":"closed_loop","field":"consumersNeedRebuild","expected":"false","actual":"true"}
]
```

`/api/status` ⇒ `chain_runtime.runtimeCore.state = **partial**`、`lastError` 非空；
`recentEvents` 里 `snapshot_rejected` **每 15 s** 一条（**22:19:17 → 22:25:22** 连续），
而 `snapshot_committed` 停在 **22:17:06**（第 **21** 代）。
⇒ **RuntimeCore 从 22:19 起就再也发布不出新快照**，页面上的「运行时视图」停在 **21** 代。

**这不是**上一任务 `T-广核.01` 引入的：它在 `T-广核.01` 立项时就被登记为 `R5`
（`D-199` §6：*「`runtimeCore.state = error`：`consumersNeedRebuild expected=false actual=true`」*）。
本任务把 `R5` 从「一条待办」变成「**已定位的根因 + 可执行的修法**」。

---

## 2. 根因链（**每一环都有读数** · **2026-11-26 23:3x–23:5x 订正**）

> **★ 本节已被实测订正**（订正依据见 `docs/D-206` §2）。上一轮写的 ③ 环
> 「消费者在目录写完的同一刻就重建了」**是错的**，据此定下的修法（同义反复）**已作废**。

| 环 | 事实 | 读数 |
|---|---|---|
| **① 标志的置位** | `closed_loop_manager` 每次**目录变了**就置 `consumers_need_rebuild = 1` | `closed_loop_manager/src/lib.rs:1100` `consumers_need_rebuild: checksum_changed` |
| **② 标志的清除** | **只有**一个清除点：`checksum_changed == false` 时（即**下一次**物化发现**目录没变**）才清 | `lib.rs:1106-1107` → `clear_consumers_need_rebuild`（`:4545`）；`grep` 全文件**只有这一处**调用 |
| **③ 消费者的重建**（**订正**） | `manager_v4_closed_loop_runtime_state.last_rebuilt_at` **不是**消费者的重建时刻：它是**生产者写目录行时**在同一事务里写的（`replace_closed_loop_rows` → `lib.rs:3571 last_rebuilt_at = chrono::Utc::now()`）⇒ 与 `manager_v4_closed_loops.updated_at`、`library_state.generated_at` **三者恒等** | 实测（**23:41**）：三者**全部**＝ **15:34:27.741556900Z**，`last_rebuilt_at` 的**唯一**取值也是它 |
| **③′ 消费者真正重建的时刻** | 消费者**落后目录 4 min 28 s**：目录 **15:34:27** 换代（代次 44）→ 消费者**第一次用代次 44 复核**在 **15:38:55** | 复核行按实测代次分裂：**代次 43 共 115 行**（末条 **15:37:06**）／**代次 44 共 48 行**（首条 **15:38:55**） |
| **④ 标志滞后的时长** | 消费者 **15:38:55** 就追上了，标志却要到**下一次「没变化的物化」**才清 ⇒ 在持续变化的市场里**可能一直不清** | 部署前实测：**15:41** 时目录仍是代次 **44**、`consumers_need_rebuild = **1**` ⇒ **标志在说谎**（消费者早已追上） |
| **⑤ 谁因此报错** | `composite_manager` 的上游评估把「闭环组件说消费者需要重建」判为 **issue** | `composite_manager/src/lib.rs:1733-1740`：`if snapshot.closed_loop.consumers_need_rebuild { issues.push(ConsistencyIssue{ component: "closed_loop", field: "consumersNeedRebuild", expected: "false", actual: "true" }) }` |
| **⑥ 谁因此拒收** | `runtime_core` 把评估里的 **issue** 当作「快照不自洽」⇒ **拒收整份快照** | `rs-src/src/runtime_core.rs:2232-2248`：`if !pipeline.upstream_assessment.consistent || !issues.is_empty() { return Err(InconsistentSnapshot(issues)) }` |

**一句话**：**标志在说谎** —— 它在消费者**早已重建**之后，还继续说「消费者需要重建」，
而且要等到**下一次**物化恰好没变化才肯改口。在一个**目录每十几分钟就变一次**的市场里，
它**几乎永远是 1** ⇒ RuntimeCore **在标志为 1 的整段时间里拒收快照**。

**★ 真正的「消费者已重建」凭据（不需新表、不需新列）**：
**复核行携带的实测代次**（`manager_v4_reviews.closed_loop_version/checksum`）——
它来自 worker pool 内存目录的 `upstream`（`rs-src/src/review_publish.rs:846-849 measured_generation`）
⇒ 「消费者持有了哪一代」是**可直接读、已持久化**的事实。

**为什么现在才暴露**：目录重建以前很少发生（重建是 `T-闭.03` 的部署闸门留下的动作），
现在是**运行中的实例自己在周期性重建目录**（`v4_closed_loop_worker`），所以它变成了**常态**。

---

## 3. 目标（可验收）

| 编号 | 目标 | 判据 | 状态 |
|---|---|---|---|
| **G1** | 标志的**含义**与事实一致 | 消费者**确实重建**之后，标志**立即**回到 **0**（不再等下一次物化） | ✅ **达成**（回执在 **23:48:42** 清零） |
| **G2** | **不提前清** | 目录变了、消费者**还没**重建时，标志**仍是 1** ⇒ RuntimeCore **继续拒收**（**失败即失败，不降级**） | ✅ **达成**（回执按代次护栏；单测 + 实盘） |
| **G3** | 实盘读数闭环 | `runtimeCore.lastError` 变 **None**；`snapshot_committed` 的代次**继续前进** | ✅ **达成**（`lastError = None`；代次 **11 → 15 → 16**） |
| **G4** | 判据**没有被放宽** | 任何「把 issue 直接忽略掉 / 把标志硬编码成 0」的改法**一律不算完成** | ✅ **达成**（**没碰** `runtime_core` / `composite_manager` 的 issue 判定） |

---

## 4. 改动计划（按模块）

### 4.1 `M-13`（标志所有者：`closed_loop_manager`） ＋ `M-6`（复核发布层：`review_publish`）

| 步 | 改动 | 文件 | 状态 |
|---|---|---|---|
| 4.1.1 | **清标志的依据换成「消费者对当前代次的回执」**：新增 `acknowledge_consumers_rebuilt(...)`（所有者暴露的 API）；**删除**「下一次物化恰好没变化就清」那条路径（`materialize_on_connection` 的 `else if previous.consumers_need_rebuild` 分支与 `clear_consumers_need_rebuild`） | `flashloan/tools/closed_loop_manager/src/lib.rs` | ✅ **已做** |
| 4.1.2 | **护栏**：回执只在「实测代次 ＝ 盘上当前代次」时生效（SQL 的 `WHERE closed_loop_version = ?` ＋ 可选 `materialized_checksum = ?`）⇒ 过期回执**清不掉任何东西**（**不猜、不降级**） | 同上 | ✅ **已做** |
| 4.1.3 | **消费者回执**：复核发布成功后，用**实测目录的代次**回执（失败只记日志，**不**改复核结果） | `flashloan/rs-src/src/review_publish.rs`（`publish_review_loops` → `acknowledge_the_rebuilt_consumers`） | ✅ **已做** |
| 4.1.4 | 单测：① 目录变 → 标志 **1**；② 回执代次不符 ⇒ **仍 1**；③ 回执当前代次 ⇒ **0**；④ 库换代后旧代次回执 ⇒ **仍 1**；⑤ 发布层真的回执 | 同上两个文件 | ✅ **已做**（**3** ＋ **2** 个新单测，**1** 个已改） |
| 4.1.5 | 实盘读数：回执后**立刻**读 `manager_v4_closed_loop_library_state.consumers_need_rebuild` ⇒ **0**；`runtimeCore.lastError` ⇒ **None**；快照代次**前进** | 实盘（只读） | ✅ **已做**（**23:48:42** 清零；代次 **11 → 15 → 16**） |

### 4.2 **不做**（本轮明确划界 · 见 `D-204-SCOPE_GUARD.md` §2）

| 编号 | 不做 | 理由 |
|---|---|---|
| N1 | **不**在 `runtime_core` / `composite_manager` 里**忽略** `consumersNeedRebuild` 这条 issue | 那是**把哨兵拆掉**，不是把哨兵修对（G4）—— **本轮确实没碰这两个文件** |
| N2 | **不**把标志**硬编码成 0**、**不**删列、**不**跳过检查 | 同上；也会让 `mainnet_arbitrage_healer.py` 的有效性判据失效（**本轮没碰列、没改默认值**） |
| N3 | **不**改广播门禁判据、**不**改复核口径 | 那是**市场事实**的投影，与本任务无关 |
| N4 | **不**碰 git | 工作区有 **400+** 文件在途改动（含他人工作）⇒ 只改文件、不提交 |
| N5 | **不**碰 `worker_pool.rs` | 该文件有他人 **2785** 行在途改动；本轮改的是**标志所有者**与**发布层**（详见 `docs/D-206` §6 `N5` / `S6`） |

---

## 5. 回归范围（= 改动模块集合）

| 套件 | 命令 |
|---|---|
| 闭环管理器 | `cd flashloan/tools && cargo test --offline -p closed-loop-manager` |
| 组合管理器 | `cd flashloan/tools && cargo test --offline -p composite-manager` |
| 运行时内核 | `cd flashloan/rs-src && cargo test --offline -p flashloan-rs --lib` |

**可直接粘贴的命令**（守卫按「命令必须整行开头」核对）：

```text
cd flashloan/tools && cargo test --offline -p closed-loop-manager -p composite-manager
cd flashloan/rs-src && cargo test --offline -p flashloan-rs --lib
```

**回归结果（2026-11-26 23:3x 实跑）**：

| 套件 | 结果 |
|---|---|
| `closed-loop-manager` | **51 passed / 0 failed**（基线 **48** ＋ 新 **3**） |
| `composite-manager` | **20 + 6 passed / 0 failed**（**未改**该 crate，只读确认它仍把该标志判为 issue） |
| `flashloan-rs --lib` | **269 passed / 0 failed**（基线 **267** ＋ 新 **2**） |

---

## 6. 遗留（**不代任何其它任务关闭**）

| 编号 | 遗留 | 归属 | 依据 |
|---|---|---|---|
| S1 | **单地址限速**（≤ 5 次/秒）＝ 一轮 **5.4 – 7 min** 的硬上限（提并发**无效**） | `M-13` ＋ `M-7` | `T-广核.01` §6 `R1` |
| S2 | **数据库连接开销**占运行时 **≈ 47%** CPU | `M-10` | `T-广核.01` §6 `R3` |
| S3 | **正利润是灰尘级**（**1.2e-05 USDG** ＝ 全表最大；另有 **4.5e-07 PFE** / **2.7e-07 CRCL** / **7.8e-09 ETH**）⇒ 即使门禁全通，真实广播在手续费上也是**亏**的 | **市场条件** | `T-广核.01` §6 `R4` |
| S4 | **Python 天花板 32 vs Rust 上限 256** 的不对称 | `M-13` | `T-广核.01` §6 `R6` |
| S5 | 启动到第一条复核的 **8 min** 里「payload 构造 / 逐组投递」的**精确拆分未测** | `M-13` | `T-广核.01` §6 `R7` |
| **S6** | **消费者重建仍落后目录 ≈ 4 min 28 s**（worker pool 的目录缓存按 **TTL 300 s** 刷新，**不**按代次变化刷新）⇒ 标志仍会在这段**真实落后**里保持 **1**（**这是对的**：消费者确实没追上） | `M-13` | 本轮实测：目录 **15:34:27** → 消费者首次用代次 44 复核 **15:38:55**（`worker_pool.rs:836 CONTEXT_CACHE_TTL_SECONDS = 300`） |

**S6 的可选后续**（**不在本轮**）：让 worker pool 在**代次变化**时立刻刷新目录，可把这段「真实的落后」压到秒级。
**为什么不在本轮做**：那要改 `worker_pool.rs`（**他人 2785 行在途改动**）且动它的**热路径**；
而它**不影响**复核与广播链（实测：标志为 1 时复核照常写入）。详见 `docs/D-206` §7。
