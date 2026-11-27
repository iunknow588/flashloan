# D-192 `T-复核.01`｜复核发布按闭环独立化 · 陈旧闭环显式报错 · 开发计划

> **本文件是 `T-复核.01` 自己的开发计划**（自包含）。
> 编号 `T-复核.01` / `AC-复核.01` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。

---

## 1. 触发（实测 · 只读 · 2026-11-26）

### 1.1 现象

| 项 | 读数 |
|---|---|
| `manager_v4_review_broadcast_requests` | **0 行** |
| `manager_v4_reviews` | **174 行**，**全部** `data_incomplete` |
| 最后一次 `review_publish` | **2026-11-25T23:07:30Z**（此后 **12 h** 零发布） |
| 闭环目录 | `closed_loop_version` **21（23:07）→ 24（02:38）**；`materialized_checksum` `034e7801 → 008bd5c6` |
| Rust 工作线程池 | `reviewQueue={claimed:96, completed:502, failed:0}`、`completed=0` |

⇒ 复核**在跑**（**502** 次完成、**0** 次失败），**发布一条也没有**。

### 1.2 根因（详见 `docs/D-195-…`）

1. **第一道门禁**：全局 `ReviewEvidenceAccumulator` 在**目录键集合**或**上游代际**变化时
   **清空**全部证据；96 个组各持不同时间点的目录快照（TTL **300 s**）＋ 目录**每分钟**重建
   ⇒ 累加器**永远被清空** ⇒ `covers_catalog()` **永假** ⇒ **一条缺证据压住全部 174 条**。
2. **第二道门禁**：`publish_review_generation` 的**全局上游代际比对** ＋ **全目录覆盖**要求。

### 1.3 与用户口径的冲突

| 用户口径 | 旧实现 | 判定 |
|---|---|---|
| 「复核数据**每个闭环都是独立的**，任何一条都不能影响其他的闭环数据」 | 全局累加器 ＋ 全目录覆盖门禁 | **违反** |
| 「随着时间，会**多次累加**」 | 复核行**每代整体替换** | **违反** |
| 「**要么成功，要么失败，没有降级处理**」 | 覆盖不全 ⇒ **什么都不写**（沉默＝降级） | **违反** |
| 「**任何一个闭环都不应该长期没有任何输出信息**」 | 174 条恒为 `data_incomplete`、无诊断、无解释 | **违反** |

---

## 2. 目标（可验收）

| 编号 | 目标 | 判据 |
|---|---|---|
| G1 | **一条闭环的复核不影响另一条** | 写入按 `loop_key` 定位；无证据的闭环**不被覆盖** |
| G2 | **复核数据随时间累加** | 库的复核集 ＝ 存量未携带行 ＋ 本批新行；计数与校验和按累加集算 |
| G3 | **发布不再被一条缺失拖死** | 发布按闭环；不做全目录覆盖门禁、不做全局上游比对 |
| G4 | **陈旧闭环有明确错误输出** | 无复核行 / `reviewed_at` 解析不出 / `reviewed_at` 早于阈值 ⇒ 写 `data_incomplete` ＋ **明确** `review_reason` ＋ `review_stale` 诊断 |
| G5 | **陈旧闭环不会被广播** | 标记时删除该闭环的 `manager_v4_review_broadcast_requests` 行 |
| G6 | **陈旧清扫独立于发布链路** | 清扫器**自有时钟**（按 scope **60 s** 去重）、失败**如实上报** |
| G7 | **复核回来即覆盖标记** | 新复核行按 `loop_key` 覆盖标记行 |

---

## 3. 改动计划（按模块）

### 3.1 `M-6`｜复核层（`flashloan/tools/review_manager/src/lib.rs`）

| 步 | 改动 | 状态 |
|---|---|---|
| 3.1.1 | `ReviewEvidenceInput` 新增 `closed_loop_generation`（测量代次随证据走） | ✅ 已落地 |
| 3.1.2 | `build_review_rows`：无证据的闭环 `continue` | ✅ 已落地 |
| 3.1.3 | `replace_review_rows` → `upsert_review_rows`（按 `loop_key` 定位三张表） | ✅ 已落地 |
| 3.1.4 | 存量累加：`read_stored_review_projections` / `accumulate_review_state` / `batch_content_changed` / `review_state_checksum` | ✅ 已落地 |
| 3.1.5 | **新增** `mark_stale_reviews`（含 `stale_loops` / `mark_stale_loop` / `withdraw_broadcast_request` / `publish_stale_audit`） | ✅ 已落地 |
| 3.1.6 | 单测 **5** 条（陈旧标记 / 从未复核 / 新鲜不标 / 只标一次 / 复核覆盖标记） | ✅ 已落地 |

### 3.2 `M-13`｜运行时内核（`flashloan/rs-src/src/`）

| 步 | 改动 | 状态 |
|---|---|---|
| 3.2.1 | `closed_loop_identity.rs`：`ClosedLoopIdentity` ＋**四个代次字段**、**新增 `is_same_loop()`** | ✅ 已落地 |
| 3.2.2 | `sqlite_read.rs`：闭环行查询**补四列** | ✅ 已落地 |
| 3.2.3 | `review_publish.rs`：**新增 `publish_review_loops()`**（按闭环发布 ＋ 丢弃离开目录/被重建的闭环 ＋ 幂等键按批 ＋ `measured_generation()` ＋ `identity_row_of()`） | ✅ 已落地 |
| 3.2.4 | `worker_pool.rs`：`review_publish_block` **去全局门禁**（直接按组发布） | ✅ 已落地 |
| 3.2.5 | `worker_pool.rs`：**新增 `stale_review_sweep_block()`** ＋ 工作线程**每轮复核前**调用（按 scope **60 s** 去重） | ✅ 已落地 |
| 3.2.6 | `cargo check --all-targets` 无 warning | ✅ 已落地 |

---

## 4. 部署闸门（**待做 · 与其它部署闸门同批**）

| 步 | 动作 | 状态 |
|---|---|---|
| 4.1 | 停运行时（`restart_runtime_t401_20261126.ps1 -StopOnly`） | ✅ **11:41**（看护 **760** ／ runtime **17852** 已停） |
| 4.2 | `cargo build --offline --release`（约 **3 min**） | ✅ **11:40:59**（**3m08s**、**0 warning**）／ 二次 **12:11:21**（**3m42s**） |
| 4.3 | 备份旧 DLL、确认新 DLL 落位 | ✅ `flashloan_rs.dll.bak-20261126-rev01` ／ `-rev02`（**诚实边界见 D-194 §6**） |
| 4.4 | 起看护 ＋ runtime（**新旧二进制无中间态**） | ✅ **11:42:23**（看护 **760** ／ runtime **17852**）／ 二次 **12:11**（看护 **30600** ／ runtime **12444**） |
| 4.5 | 实盘读数：`manager_write_requests` 出现**新的** `review_publish` | ✅ **191 → 219**（新 **28** 条） |
| 4.6 | 实盘读数：`manager_v4_reviews` 出现 `review_status ≠ 'data_incomplete'` 的行 | ✅ `not_executable` **19 → 58** 条 |
| 4.7 | 实盘读数：`manager_v4_review_broadcast_requests` **> 0 行** | ⬜ |
| 4.8 | 实盘读数：`flashloan/db/review_record_jsonl/` 出现**当日**闭环文件 | ✅ 当日目录 **174** 个（**每闭环一个**） |
| 4.9 | 实盘读数：陈旧闭环被标 `not_reviewed_since:` ＋ `review_stale` 诊断 | ✅ **155** 条 ＋ **155** 条诊断 |

---

## 5. 回归范围（= 改动模块集合）

| 套件 | 命令 | 期望 |
|---|---|---|
| 复核层 | `cargo test -p review-manager` | **167 passed / 0 failed** |
| 运行时内核 | `cargo test`（`flashloan/rs-src`） | **251 passed / 0 failed**、无 warning |
| 工具层其余 | `cargo test --workspace`（`flashloan/tools`） | 全绿、**0 failed** |

**可直接粘贴的命令**（守卫按「命令必须整行开头」核对）：

```text
cd flashloan/tools && cargo test --offline -p review-manager
cd flashloan/tools && cargo test --offline --workspace
cd flashloan/rs-src && cargo test --offline
cd flashloan/rs-src && cargo check --offline --all-targets
```

**读数（2026-11-26）**：`review-manager` **167 passed / 0 failed**（基线 **166** ＋ 新 **1**：
`the_default_stale_window_outlasts_a_review_round`）；`flashloan-rs` **251 passed / 0 failed**
（基线 **249** ＋ 新 **2**：`a_per_loop_publication_publishes_the_measured_loop_under_its_own_generation`
＋ `a_loop_that_cannot_be_published_does_not_block_the_other_loops`）；
`flashloan/tools` 工作区**全绿 · 0 failed**。

---

## 6. 不做的事（**明确划界**）

| 编号 | 不做 | 理由 |
|---|---|---|
| N1 | 不改**广播准入**判据（费率、利润阈值、授权开关） | 归 `M-7` / `T-广.03`；本任务只保证**复核能发布** |
| N2 | 不改**闭环层**物化逻辑 | 归 `M-4`；目录**每分钟重建**是事实，本任务**适应**它而不是**禁止**它 |
| N3 | 不做**写请求账本清理** | 低概率路径，按用户口径**先留占位** |
| N4 | 不做**跨月统计**、**落盘失败降级** | 同上（用户口径：先跑通正常功能） |
| N5 | 不碰 `worker_pool.rs` 的**并行编辑禁区**行段（**16 / 219–695 / 1384–1602 / 2119–2125 / 2471–2490 / 3723–4143 / 4691–4758 / 6560+**） | 其它线程正在改；本任务只碰 **2130–2420** 与 **4850–4880** 两段 |
| N6 | 不碰 `review_publish.rs` 的 **1331 行以下** | 同上（测试段在 **1366–1632**） |

