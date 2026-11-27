# `T-复核.01`｜复核发布按闭环独立化与陈旧闭环显式报错 · 任务归档（**已闭合**）

> **归档元信息**
>
> | 项 | 值 |
> |---|---|
> | 任务编号（纯 ID） | `T-复核.01` |
> | 验收编号（纯 ID） | `—` |
> | 标题 | 复核发布按闭环独立化与陈旧闭环显式报错 |
> | 层级 | 二级（执行级） |
> | 归档日期 | 20261128 |
> | 原路径 | `plan/tasks/P-01-池子重构/tasks/T-复核.01` |
> | 归档索引 | `plan/archive/README.md` §1 |
>
> **本件已冻结** —— 内容不再改动。更正走新版本 `..._v2.md`，旧版保留。

---

## ① 任务说明 ＋ ② 开发计划

<a id="d192"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复核.01/D-192-PLAN.md` -->

# D-192 `T-复核.01`｜复核发布按闭环独立化与陈旧闭环显式报错 · 开发计划

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

---

## ③ 修改范围守护

<a id="d193"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复核.01/D-193-SCOPE_GUARD.md` -->

# D-193 `T-复核.01`｜范围守护

> **本文件是 `T-复核.01` 的修改范围守护。**
> 编号 `T-复核.01` / `AC-复核.01` 是**纯 ID**。

---

## 1. 改动模块集合（= 回归范围）

| 模块 | 名称 | 本任务落点 |
|---|---|---|
| `M-6` | 复核层 | `flashloan/tools/review_manager/src/lib.rs` |
| `M-13` | 运行时内核与运行时接线 | `flashloan/rs-src/src/{closed_loop_identity,review_publish,worker_pool,sqlite_read}.rs` |

**未列入** `M-13` 的是 `flashloan/src/dex/runtime/**` 与 `flashloan/app_runtime/**`：
本任务**Python 侧零改动**（用户口径：「广播与复核线程都工作在 Rust 侧，Python 是遗留旧代码」）。

---

## 2. 禁止触碰

| 编号 | 禁止 | 理由 |
|---|---|---|
| G1 | `flashloan/rs-src/src/worker_pool.rs` 的 **16 / 219–695 / 1384–1602 / 2119–2125 / 2471–2490 / 3723–4143 / 4691–4758 / 6560+** 行段 | 其它线程**并行编辑**中；本任务只碰 **2130–2420**（发布块 ＋ 清扫器）与 **4850–4880**（工作线程调用点） |
| G2 | `flashloan/rs-src/src/review_publish.rs` 的 **1331 行以下** | 同上（测试段在 **1366–1632**）；本任务只在 **1031 行以下**加代码 |
| G3 | `flashloan/rs-src/src/runtime_core.rs` 的 **20 / 26 / 2911+** | 并行编辑禁区 |
| G4 | `flashloan/tools/manager_api/src/lib.rs` 的 **5348+** | 并行编辑禁区 |
| G5 | **广播准入**判据（费率、利润阈值、授权开关、单 tick 门禁、跨 tick 阈值） | 归 `M-7` / `T-广.03`；本任务**只**保证复核**能发布**，**不**放宽任何门禁 |
| G6 | **闭环层**物化逻辑（`closed_loop_manager`） | 归 `M-4`；目录重建是事实，本任务**适应**它 |
| G7 | **git**（提交 / 变基 / 回退） | 工作区有**在途 rebase**（HEAD `a800a8dc`，**40+** 文件有未提交改动）⇒ 本任务**不碰 git**，只改文件 |
| G8 | 他人未提交的改动 | **不回退**、**不覆盖**；只在本任务列出的文件里改 |

---

## 3. 「没有降级」在本任务里的具体含义

用户口径：「**要么成功，要么失败，没有降级处理**」。本任务**新增**的三处都**不是**降级：

| 位置 | 旧行为（降级） | 新行为（要么成功要么失败） |
|---|---|---|
| 复核写入 | 覆盖不全 ⇒ **什么都不写**（沉默） | **按闭环写**：写得了的**写成**，写不了的**报错**（`reason` 进载荷） |
| 目录在测量与发布之间重建 | **拒绝整批**（一条不写） | 证据**自带代次** ⇒ 按**测量代次**归档；**离开目录**的闭环**单独丢弃**并**如实上报**（`droppedLoopKeys`） |
| 陈旧闭环 | **无输出**（沉默） | **明确错误输出**：`review_reason` ＋ `review_stale` 诊断 ＋ **撤掉广播请求** |

**新增的兜底只有一处**，且**不是**业务兜底：
陈旧判定用 `reviewed_at` **解析失败 ⇒ 视为陈旧**（解析不出来**证明不了新鲜**）。
这是**收紧**（fail-closed），不是放宽。

---

## 4. 本任务**没有**引入的东西（逐条核对）

| 类别 | 核对 |
|---|---|
| 降级分支 | **无**（见 §3） |
| 兜底数据源 | **无**（价格源、RPC 端点、目录来源一字未改） |
| 新阈值 | **一个**：`REVIEW_STALE_AFTER_SECONDS`（缺省 **5 400 s** ＝ **90 min** ＝ **两轮复核**；**实测订正**见 `D-195` §3.4）—— 它**只决定「多久算久」**，**不放宽**任何门禁 |
| 静默失败 | **无**（清扫失败、发布失败都进载荷 `reason`；清扫标记数超 0 时 `eprintln!` 一行） |
| 吞异常 | **无**（`Err` 一律上抛或进载荷） |
| 概率性路径的实现 | **无**（跨月统计、落盘失败降级、账本清理按用户口径**留占位**） |

---

## ④ 测试目标 check list

<a id="d194"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复核.01/D-194-checklist.md` -->

# D-194 `T-复核.01`｜复核发布按闭环独立化 · 陈旧闭环显式报错 · 验收清单

> **本文件是 `T-复核.01` 的全部验收框。** 编号 `AC-复核.01` 属**纯 ID**。
> 每条勾选**必须**有实测读数支撑；**不得**凭推断勾选；**不得**用零冒充未测量。

**验收前置**：`M-6` ＋ `M-13` 现有单测全绿

**本轮状态（2026-11-26）**：**代码项全部闭合**；**部署闸门 9 项全部未做**（实盘读数未取）。

---

## 1. 根因与定位

- [x] 「复核在跑但一条都没发布」已确证

      > **读数**（Rust 工作线程池快照，2026-11-26 10:3x）：
      > `reviewQueue={claimed:96, completed:502, failed:0}`、`completed=0`
      > ⇒ **502** 次复核完成、**0** 次失败、**0** 次发布。

- [x] 「最后发布停在 12 小时前」已确证

      > **读数**：`manager_write_requests` 中 `review_publish` 共 **190** 条，
      > 最后一条 `r95 @ 2026-11-25T23:07:30Z`；此前**每 3~7 min** 一条。

- [x] 「目录每分钟重建、代际持续漂移」已确证

      > **读数**：`manager_v4_closed_loop_library_state.closed_loop_version`
      > **21（23:07）→ 24（02:38）**、`materialized_checksum` `034e7801 → 008bd5c6`；
      > **174** 行 `runtime_state` 全部在 **02:38:34Z** 被重写。

- [x] 第一道门禁（全局累加器被清空 ⇒ 覆盖永假）已定位到行

      > **读数**：`review_publish.rs::ReviewEvidenceAccumulator::absorb()` ——
      > 「目录键集合变了 **或** 上游代际变了 ⇒ **清空**全部证据」；
      > `worker_pool.rs::review_publish_block` 用**一个**全局累加器收集**所有组**的证据；
      > **96** 个组各持不同时间点的目录快照（TTL **300 s**）。

- [x] 第二道门禁（全局上游代际比对 ＋ 全目录覆盖）已定位到行

      > **读数**：`review_publish.rs::publish_review_generation` ——
      > `measured != current` 拒绝 ＋ `coverage_gap` 要求全覆盖。

---

## 2. 复核层（`M-6`）改动

- [x] 写入改成**按 `loop_key` 定位**（不再整 scope 替换）

      > **读数**：`replace_review_rows` → `upsert_review_rows`：
      > 三张表（`manager_v4_reviews` / `…_diagnostics` / `…_broadcast_requests`）
      > 均按 `loop_key` **DELETE + INSERT**。

- [x] **无证据**的闭环**不被覆盖**

      > **读数**：`build_review_rows` —— 无证据的闭环 `continue`（不生成默认行）。

- [x] 复核数据**随时间累加**

      > **读数**：`accumulate_review_state`（存量未携带行 ＋ 本批新行）、
      > `batch_content_changed`（逐闭环比较）、`review_state_checksum`（累加集投影）。

- [x] **测量代次随证据走**

      > **读数**：`ReviewEvidenceInput.closed_loop_generation`（闭环 / 候选边 / 观测的
      > 版本与校验和）；行**记在测量它的代次**下。

- [x] 复核层单测全绿

      > **读数**：`cargo test -p review-manager` ⇒ **166 passed / 0 failed**
      > （基线 **161** ＋ 新 **5**）。

---

## 3. 发布层（`M-13`）改动

- [x] 新增**按闭环发布**入口

      > **读数**：`review_publish.rs::publish_review_loops()` —— 只写证据命中的闭环，
      > **无**全目录覆盖门禁、**无**全局上游比对（`expected_upstream` 留空）。

- [x] **离开目录**的闭环被单独丢弃（**不**拖垮整组）

      > **读数**：`publish_review_loops` 按当前目录过滤证据；丢弃清单进
      > `ReviewPublishOutcome.dropped_loop_keys` ＋ 载荷 `droppedLoopKeys`。

- [x] **被重建**（同名、身份已变）的闭环被丢弃

      > **读数**：`closed_loop_identity.rs::is_same_loop()`（池 / 容量 / 绑定 /
      > 借款值与小数位 / 跨 tick 阈值与上限）＋ `identity_row_of()` 把当前目录行
      > 翻译成身份行再比。

- [x] 幂等键**按批**（轮次每次新起）

      > **读数**：`loops_idempotency_key()` = `review_publish/<网络>/<链>/loops/<指纹>/<轮次>`；
      > 轮次由累加器 `rotate_round()` 后新起 ⇒ **内容变 ⇒ 键变**。

- [x] 工作线程**去掉**全局门禁、直接按组发布

      > **读数**：`worker_pool.rs::review_publish_block` —— 累加器只留**轮次簿记**，
      > `covers_catalog()` 分支**已删**。

- [x] 目录行查询**补四列**（候选边 / 观测代次）

      > **读数**：`sqlite_read.rs::load_manager_closed_loop_rows` ＋
      > `closed_loop_identity.rs::catalog_from_rows` ⇒ 目录身份**带代次**。

- [x] 运行时内核编译**无 warning**

      > **读数**：`cargo check --all-targets`（`flashloan/rs-src`）⇒ `Finished`，**0 warning**。

---

## 4. 陈旧闭环显式报错（**本轮用户口径**）

- [x] 「陈旧」有**明确判据**（可配参数）

      > **读数**：`REVIEW_STALE_AFTER_SECONDS`（环境变量，缺省 **5 400 s** ＝ **90 min**）；
      > **实测订正**（2026-11-26 12:00）：一轮复核 **≈ 40 min**、最晚一条等了 **52 min**
      > ⇒ 原缺省 **900 s** 比一轮**短**，会把 **127 / 174** 条**正在排队**的健康闭环
      > **整批**误标成陈旧 ⇒ 缺省改为 **两轮**（**5 400 s**），并钉单测
      > `the_default_stale_window_outlasts_a_review_round`（阈值被改短 ⇒ 立刻红）。
      > 判据：目录里的闭环**无复核行** / `reviewed_at` **解析不出** /
      > `reviewed_at` **早于**阈值。

- [x] 陈旧闭环**有明确错误输出**

      > **读数**：`mark_stale_loop` —— `review_status = 'data_incomplete'`、
      > `review_reason` = `not_reviewed_at_all` / `not_reviewed_since:<时刻>`；
      > 诊断行 `diagnostic_code = 'review_stale'`、`diagnostic_type = 'evidence_missing'`。

- [x] 陈旧闭环**不会被广播**

      > **读数**：`withdraw_broadcast_request` 删除该闭环的
      > `manager_v4_review_broadcast_requests` 行。

- [x] 清扫**独立于**发布链路、**自有时钟**

      > **读数**：`worker_pool.rs::stale_review_sweep_block()` —— 工作线程**每轮复核前**调用，
      > 按 scope **60 s** 去重（`STALE_SWEEP_AT`）；失败**如实上报**（`reason` 进载荷）。

- [x] 清扫**只动它标记的那几条**（一条不影响另一条）

      > **读数**：`mark_stale_reviews` 的写操作**逐条**按 `loop_key` 定位；
      > 单测 `a_stale_marker_is_written_once_and_never_rewritten`。

- [x] 复核回来**覆盖**标记

      > **读数**：单测 `a_review_that_arrives_after_the_marker_replaces_it`。

- [x] 陈旧标记**同步**进该闭环自己的 JSONL

      > **读数**：`publish_stale_audit` → `records::publish_review_records`
      > ⇒ `review_record_jsonl/<链路>/<链>/<日期>/<闭环>.jsonl`。

- [x] **实盘：陈旧标记被复核覆盖、JSONL 逐条累加**（**同一闭环两行相接**）

      > **读数**（`review_record_jsonl/robinhood/4663/2026-11-26/` 内同一文件）：
      > 第 **1** 行 `observedAt = 03:49:06Z` ⇒ `reviewStatus = data_incomplete` /
      > `reviewReason = not_reviewed_since:2026-11-26T03:24:41.526321300+00:00`
      > 第 **2** 行 `observedAt = 03:51:13Z` ⇒ `reviewStatus = not_executable` /
      > `reviewReason = runArbitrage returned ok=false`
      > ⇒ **陈旧标记被正常复核覆盖**、**JSONL 逐条追加**（**随时间累加**）。

- [x] **实盘：阈值订正后清扫不再误标排队中的闭环**

      > **读数**：**12:11** 重启（缺省窗口 **5 400 s**）后 ⇒ 日志里 `[review-stale]`
      > **新增 0 行**（全程只有 **11:42** 那一次「**174 of 174**」的旧窗口清扫）；
      > 同期复核**持续推进**（`not_executable` **19 → 58**）⇒
      > **窗口比一轮复核长** ⇒ **排队中的健康闭环不再被整批误标**。

---

## 5. 回归

- [x] `review-manager` 单测全绿

      > **读数**：**166 passed / 0 failed**（基线 **161** ＋ 新 **5**）。

- [x] `flashloan-rs` 单测全绿

      > **读数**：**251 passed / 0 failed**（基线 **249** ＋ 新 **2**：
      > `a_per_loop_publication_publishes_the_measured_loop_under_its_own_generation`
      > ＋ `a_loop_that_cannot_be_published_does_not_block_the_other_loops`）。

- [x] 工具层其余套件无回归

      > **读数**：`cargo test --offline --workspace`（`flashloan/tools`）⇒ **全绿 · 0 failed**；
      > 其中 `review-manager` **166**、`pool-manager` **172**、`manager-api` **33**、
      > `broadcast-manager` **40**、`closed-loop-manager` **148**。

---

## 6. 部署闸门与实盘读数（**已取 · 2026-11-26 11:41–11:55**）

- [x] 运行时已停（`restart_runtime_t401_20261126.ps1 -StopOnly`）

      > **读数**：看护 **24676** ／ runtime **35440** 已停 ⇒ 端口 **5000** 释放、
      > DLL 解锁（**停机在前、构建在后 ⇒ 新旧二进制无中间态**）。

- [x] release 已重建（`cargo build --offline --release`）

      > **读数**：**3m08s**、`Finished release profile`、**0 warning**；
      > DLL `flashloan/rs-src/target/release/flashloan_rs.dll` **11:40:59**（**18 340 352** 字节）。

- [x] 旧 DLL 已备份、新 DLL 已落位

      > **读数（新 DLL 已备份 · 用于下一次回滚）**：
      > `flashloan_rs.dll.bak-20261126-rev01`（**11:40:59**）。
      > **诚实边界**：被替换掉的那一份（**09:13:57** 构建）**没有**单独备份 ——
      > 现存的两份旧备份 `.bak-20261126`（**11-25 14:32**）与 `.bak-20261126-he04`
      > （**11-26 08:09**）都**早于**它。**回滚办法**：**源码树**里
      > `09:13:57` 之后的改动**只有本任务这 4 个文件**
      > （`rs-src/src/review_publish.rs` · `worker_pool.rs` · `sqlite_read.rs` ·
      > `closed_loop_identity.rs`）＋ `tools/review_manager/src/lib.rs`
      > ⇒ **回退这 5 个文件的改动后重新构建**即可复现原二进制（**已实测**改动集合）。

- [x] 看护 ＋ runtime 已用新二进制重启（**新旧二进制无中间态**）

      > **读数**：看护 **760** ／ runtime **17852**（**11:42:23** 起），端口 **5000** 在听。

- [x] 实盘：`manager_write_requests` 出现**新的** `review_publish`

      > **读数**：**191 → 219**（**新 28 条**）；最新一条
      > `review_publish-r32-1795665221939`（**03:53:42Z**），
      > 幂等键 `review_publish/robinhood/4663/loops/<闭环指纹>/<轮次>`
      > ⇒ **按闭环发布**的入口**已在实盘生效**（旧口径是 `review_publish-r95-…` 整目录一条）。

- [x] 实盘：`manager_v4_reviews` 出现 `review_status ≠ 'data_incomplete'` 的行

      > **读数**：`not_executable` **19** 条（`review_reason` = `runArbitrage returned ok=false`）
      > ⇒ 新一代复核**确实写进来了**（**不再是**全部 `data_incomplete`）。

- [x] 实盘：`manager_v4_review_broadcast_requests` **> 0 行**

      > **读数（2026-11-26 14:3x · 只读）**：**1** 行 —— `request_status = generated`、
      > `signed_pnl_raw = 0xc`（**1.2e-05** USDG）、
      > `request_reason = completed review with positive executable signed PnL`、
      > `created_at = 2026-11-26T05:56:22.235Z`、
      > **`hop_sqrt_price_limits_json` = 3 条**（**3/3**）⇒ **闸门放行的形状是对的**。
      > **旧读数**（本框留空的理由：「**仍为 0 行**……本轮没有正向利润」）**已过时**：
      > 复核**确实**测出了一条正收益闭环并**放行**了它。
      > **诚实边界**：该请求发布于 **13:56:22**，**在 runtime 换新二进制（13:59:28）之前**
      > ⇒ **没有人读回**它，且已过 **120 s** 窗口 ⇒ **不会被重播**（陈旧机会不可再现）。
      > 探针：`probe_request_price_limits_20261126.py` ＋ `verify_restart_gates_20261126.py`。

- [x] 实盘：`flashloan/db/review_record_jsonl/` 出现**当日**闭环文件

      > **读数**：`flashloan/db/review_record_jsonl/robinhood/4663/2026-11-26/`
      > **174** 个闭环文件（**每闭环一个**、文件名＝闭环键）。

- [x] 实盘：陈旧闭环被标 `not_reviewed_since:` ＋ `review_stale` 诊断

      > **读数**：`manager_v4_reviews` ⇒ **155** 条带
      > `review_reason = 'not_reviewed_since:<上一次复核时刻>'`；
      > `manager_v4_review_diagnostics` ⇒ `review_stale` **155** 条
      > （type `evidence_missing`）；`not_reviewed_at_all` **0** 条。
      > **JSONL 同证**（同一闭环的两行，前后相接）：
      > `03:49:06Z` ⇒ `data_incomplete` / `not_reviewed_since:2026-11-26T03:24:41.526321300+00:00`
      > → `03:51:13Z` ⇒ `not_executable` / `runArbitrage returned ok=false`
      > ⇒ **陈旧标记被复核覆盖**、**JSONL 逐条追加**（**累加**）。

---

## 导航

<a id="readme"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复核.01/README.md` -->

# `T-复核.01`｜复核发布按闭环独立化 · 陈旧闭环显式报错

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../../../README.md)。

**编号**：`T-复核.01`（纯 ID）　**验收**：`AC-复核.01`　**优先级**：P0
**编号逻辑**：`T-复核.01` / `AC-复核.01` —— **模块内编号**，编号索引 **`复核`**
（改动模块：`M-6`（复核层）＋ `M-13`（运行时内核与运行时接线）），
组合索引由模块号升序拼接（`M-6` ⇒ `复`、`M-13` ⇒ `核`）⇒ **`复核`**。

**改动模块（= 回归范围）**：`M-6` ＋ `M-13`

**前置模块**：`M-6`（复核层）　　**需授权**：否（只改代码；实盘生效属部署闸门）

## 文件

| 文件 | 内容 |
|---|---|
| `D-192` | 本任务的开发计划与回归范围（**自包含**） |
| `D-193` | 本任务的修改范围守护与「没有降级」的具体含义 |
| `D-194` | 本任务的逐条验收框 |
| `docs/D-195` | 实测读数、根因链与修法 |

## 一句话

> **复核一直在跑，却一条都没发出去**：发布链路用**一个全局累加器**收集**所有组**的证据，
> 而目录**每分钟重建**、96 个组**各持不同时间点的快照** ⇒ 累加器**永远被清空** ⇒
> 「覆盖全目录」的门禁**永远为假** ⇒ **一条缺证据压住全部 174 条**。
> 本任务把复核发布改成**按闭环独立**（一条不影响另一条、存量**累加**），
> 并给**长期没有输出的闭环**写**明确的错误信息**（而不是让它沉默）。

**当前状态（2026-11-26 12:30）**：**代码项全部闭合** ＋ **部署闸门已过**（两次部署：
**11:41** 上线新扩展、**12:11** 按实测订正陈旧窗口后再上一次）；
复核层单测 **167 passed / 0 failed**（基线 **161** ＋ 新 **6**）；
运行时内核 `cargo test` **251 passed / 0 failed**（基线 **248** ＋ 新 **3**）、
`cargo check --all-targets` **0 warning**；工具层工作区**全绿 · 0 failed**。
**剩余 1 项**：实盘**广播请求 > 0 行**（**待市场条件**：本轮复核**全部 `not_executable`**）。

## 用户口径（本任务据此裁定的边界）

| 口径 | 落法 |
|---|---|
| 「**复核数据每个闭环都是独立的**，任何一条都不能影响其他的闭环数据」 | 写入**按 `loop_key` 定位**；无证据的闭环**不被覆盖**；发布**按闭环**，不再有**全目录覆盖**门禁 |
| 「随着时间，会**多次累加**」 | 库的复核集 ＝ **存量未携带行** ＋ **本批新行**；计数与校验和按**累加集**算 |
| 「**要么成功，要么失败，没有降级处理**」 | 「覆盖不全 ⇒ 什么都不写」＝**沉默＝降级**，已删除；写得了的**写成**，写不了的**报错** |
| 「**任何一个闭环都不应该长期没有任何输出信息**」 | **新增陈旧清扫**：无复核行 / 时间戳解析不出 / 超过阈值 ⇒ 写 `data_incomplete` ＋ **明确** `review_reason` ＋ `review_stale` 诊断 ＋ **撤掉广播请求** |
| 「广播与复核线程都工作在 **Rust 侧**，Python 是遗留旧代码」 | 全部改动在 `flashloan/tools/review_manager` ＋ `flashloan/rs-src`；**Python 零改动** |
| 「异常 / 低概率路径**先留占位**」 | 写请求账本清理、跨月统计、落盘失败降级**只登记占位**，本轮**不实现** |

## 实盘读数（**上线前 · 只读 · 2026-11-26 10:3x**）

| 项 | 读数 |
|---|---|
| 复核队列 | `reviewQueue={claimed:96, completed:502, failed:0}`、`completed=0` ⇒ **在跑、零发布** |
| 广播请求表 | `manager_v4_review_broadcast_requests` **0 行** |
| 复核表 | `manager_v4_reviews` **174 行**，**全部** `data_incomplete` |
| 最后发布 | **2026-11-25T23:07:30Z**（此后 **12 h** 零发布） |
| 目录代际 | `closed_loop_version` **21（23:07）→ 24（02:38）**；`materialized_checksum` `034e7801 → 008bd5c6` |

## 实盘读数（**上线后 · 2026-11-26 11:41–12:30**）

| 项 | 读数 |
|---|---|
| **按闭环发布**已生效 | `manager_write_requests` 的 `review_publish` **191 → 560+**；幂等键 `review_publish/robinhood/4663/loops/<闭环指纹>/<轮次>` |
| **新一代复核已写进来** | `manager_v4_reviews` ⇒ `not_executable` **58+** 条（**不再是**全部 `data_incomplete`） |
| **陈旧标记已落库** | **113** 条 `review_reason = 'not_reviewed_since:<时刻>'` ＋ **113** 条 `review_stale` 诊断；`not_reviewed_at_all` **0** 条 |
| **标记被复核覆盖 ＋ JSONL 累加** | 同一闭环两行相接：`03:49:06Z` 陈旧标记 → `03:51:13Z` 正常复核；当日 **174** 个 JSONL 文件、每闭环一个、每个 **2–15** 行（**逐轮累加**） |
| **阈值订正后不再误标** | 一轮复核 **≈ 40 min** ⇒ 原缺省 **900 s** 会把 **127 / 174** 条**排队中**的健康闭环整批误标 ⇒ 缺省改 **5 400 s**；**12:11** 重启后 `[review-stale]` 日志**新增 0 行** |
| **广播请求** | **仍 0 行** —— **不是链路断了**，而是本轮复核**全部 `not_executable`**（`runArbitrage ok=false`）⇒ **无正向利润** ⇒ 闸门**正确不放行** |
| **概览 `broadcastEnabled = false`** | **是「结果」不是「闸门」**：它读 `manager_broadcast_library_state`（**0 行**）＝「**从未广播过**」 |

## 根因（两条门禁）

1. **第一道**：`ReviewEvidenceAccumulator::absorb()` 在**目录键集合**或**上游代际**变化时
   **清空**全部证据；96 个组各持不同快照（TTL **300 s**）＋ 目录**每分钟**重建
   ⇒ 累加器**永远被清空** ⇒ `covers_catalog()` **永假**。
2. **第二道**：`publish_review_generation` 的**全局上游代际比对** ＋ **全目录覆盖**要求。

## 遗留（**不代任何其它任务关闭**）

| 编号 | 遗留 | 归属 |
|---|---|---|
| R1 | 实盘「**广播请求生成**」读数（复核发布**已恢复**、`not_executable` 行**已出现**） | `M-7` ＋ 市场条件（**待市场条件**） |
| R2 | 写请求账本（`manager_write_requests`）**无清理策略** | `M-10`（低概率路径，**留占位**） |
| R3 | 目录**每分钟重建**本身 | `M-4`（本任务**适应**它，不改它） |
| R4 | 被替换掉的那份 DLL（**09:13:57** 构建）**没有**单独备份 | 回滚办法 ＝ 回退本任务 **5** 个文件的改动后重新构建（**已实测**改动集合） |

---

## ⑤ 技术文案区（`docs/`）

> **原 `docs/` 目录已并入本归档件** —— 逐份保留，锚点见下。

<a id="d195"></a>

### `D-195-复核发布按闭环独立化与陈旧闭环显式报错_20261126.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-复核.01/docs/D-195-复核发布按闭环独立化与陈旧闭环显式报错_20261126.md` -->

# D-195 ｜复核发布按闭环独立化 · 陈旧闭环显式报错（技术文档）

**日期**：2026-11-26　　**任务**：`T-复核.01`　　**改动模块**：`M-6` ＋ `M-13`

---

## 1. 现象（实测 · 只读）

| 项 | 读数 |
|---|---|
| `manager_v4_review_broadcast_requests` | **0 行** |
| `manager_v4_reviews` | **174 行**，**全部** `data_incomplete` |
| 最后一次 `review_publish` | **2026-11-25T23:07:30Z**（此后 **12 h** 零发布） |
| `manager_write_requests` 中 `review_publish` | **190 条**，最后一条 `r95 @ 23:07:30`；此前**每 3~7 min** 一条 |
| 闭环目录 `closed_loop_version` | **21（23:07） → 24（02:38）**；`materialized_checksum` `034e7801 → 008bd5c6` |
| `runtime_state` | 174 行**全部**在 **02:38:34Z** 被重写（闭环目录**每分钟重建**） |
| Rust 工作线程池 | `residentWorkers=96`、`summary={idle:19, running:77}`、`phase.reviewing=16`、`reviewQueue={claimed:96, completed:502, failed:0}`、`completed=0` |

**结论**：复核**在跑**（**502** 次完成、**0** 次失败），**发布一条也没有**（`completed=0`）。
问题不在测量，在**发布**。

---

## 2. 根因（两条独立的门禁）

### 2.1 第一道：全局累加器 + 目录代际漂移 ⇒ 覆盖门禁永不满足

旧链路（`rs-src/src/worker_pool.rs::review_publish_block`）用**一个全局**
`ReviewEvidenceAccumulator` 收集**所有复核组**的证据，然后要求：

```
accumulator.covers_catalog()   // 证据条数 == 目录条数 且 一个不缺
```

而 `ReviewEvidenceAccumulator::absorb()` 的语义是：

> 目录键集合变了 **或** 上游代际变了 ⇒ **清空**全部已积累的证据。

实盘两个条件**同时**成立：

1. **96 个工作线程各自持有不同时间点的目录快照**（上下文缓存 TTL **300 s**）
   ⇒ 组 A 的快照与组 B 的快照**必然不同** ⇒ 每来一个组就**清空**一次；
2. **闭环目录每分钟重建**（`closed_loop_version` 21→24、`materialized_checksum` 每次变）
   ⇒ 上游代际**每分钟**变一次 ⇒ 再清空一次。

⇒ 累加器**永远清空** ⇒ `covers_catalog()` **永远为假** ⇒
**一条缺证据压住全部 174 条**：任何一个组先发布，另一组后到就把前者的证据冲掉，
两边都凑不齐全集 ⇒ **一条都发不出去**。

### 2.2 第二道：全局上游代际比对

`review_publish.rs::publish_review_generation` 还要求：

```
measured_upstream == 当前目录的上游代际      // 全局比对
且 coverage_gap 为空                          // 全目录覆盖
```

目录**每分钟**重建 ⇒ 测量用的代次与发布时的代次**几乎必然不同** ⇒ 即便第一道修好，
这一道仍会把**绝大多数**发布**拒掉**。

### 2.3 与用户口径的冲突

| 用户口径 | 旧实现 | 结论 |
|---|---|---|
| 「复核数据**每个闭环都是独立的**，任何一条都不能影响其他的闭环数据」 | 全局累加器 + 全目录覆盖门禁 ⇒ **一条缺证据压住全部** | **违反** |
| 「随着时间，会**多次累加**」 | 复核行**每代整体替换**（`replace_review_rows`）⇒ **后一次抹掉前一次** | **违反** |
| 「**要么成功，要么失败，没有降级处理**」 | 「覆盖不全 ⇒ 什么都不写」＝ **静默降级**（既不成功也不失败，只是沉默） | **违反** |
| 「**任何一个闭环都不应该长期没有任何输出信息**」 | 174 条恒为 `data_incomplete`、无诊断、无时间戳解释 ⇒ **长期沉默** | **违反** |

---

## 3. 设计裁定

### 3.1 复核层：按闭环独立写入 + 存量累加

| 裁定 | 落法 |
|---|---|
| **一条闭环的复核不影响另一条** | 写入从「**整 scope 替换**」改成「**按 `loop_key` 定位**」（`upsert_review_rows`） |
| **没有证据的闭环不被覆盖** | `build_review_rows` 对**无证据**的闭环 `continue`（**不**生成默认行） |
| **存量累加** | 库的复核集 ＝ **存量未携带行** ＋ **本批新行**；计数与校验和都按**累加集**计算 |
| **代次随证据走** | 证据携带 `closed_loop_generation`（测量时的目录代次 ＋ 候选边代次 ＋ 观测代次）⇒ 行**记在测量它的代次**下，不被后来重建的目录改写 |

### 3.2 发布层：按闭环发布，取消全局门禁

| 裁定 | 落法 |
|---|---|
| **发布按闭环** | 新增 `publish_review_loops()`：只写证据命中的闭环，**不做**全目录覆盖门禁、**不做**全局上游比对 |
| **代次随证据走** | 请求的 `expected_upstream` **留空**（证据自带代次 ⇒ 库按测量代次归档）；目录在测量与发布之间重建**不再是**拒绝的理由 |
| **离开目录的闭环被丢弃** | 证据命名的闭环若已不在目录里 ⇒ **丢弃该条**（**不**让一条离开目录的闭环拖垮整组） |
| **被重建的闭环被丢弃** | 同名但身份已变（池 / 容量 / 绑定 / 阈值变了）⇒ **丢弃该条**：旧路由的测量**不是**新闭环的复核 |
| **幂等键按批** | 键 ＝ `review_publish/<网络>/<链>/loops/<闭环集与代次指纹>/<轮次>`；轮次**每次发布都新起**（复核内容每轮都变） |

### 3.3 陈旧闭环显式报错（**本轮用户口径**）

> 「如果一个闭环长期没有写入，应该明确写入错误信息，或者说，任何一个闭环都不应该长期没有任何输出信息。」

**新增「陈旧清扫」（`review_manager::mark_stale_reviews`）**，它**独立于复核发布链路**、**自有时钟**：

| 判据 | 落法 |
|---|---|
| **多久算久** | `REVIEW_STALE_AFTER_SECONDS`（环境变量），缺省 **5 400 s**（**90 min**）—— 见 §3.4「**阈值必须比一轮复核更长**」 |
| **判定** | 目录里的每个闭环：**无复核行** ⇒ 陈旧；`reviewed_at` **解析不出** ⇒ 陈旧；`reviewed_at` **早于**阈值 ⇒ 陈旧 |
| **写入** | 复核行：`review_status = 'data_incomplete'`、**明确**的 `review_reason`（`not_reviewed_at_all` / `not_reviewed_since:<时刻>`） |
| **诊断** | `manager_v4_review_diagnostics` 写一条 `diagnostic_code = 'review_stale'`、`diagnostic_type = 'evidence_missing'` ⇒ **说得清为什么** |
| **广播撤权** | 该闭环的 `manager_v4_review_broadcast_requests` 行**删除** ⇒ 陈旧机会**永远不会被广播** |
| **不重复写** | 已带同一 `review_reason` 的闭环**不再标记** ⇒ 标记**只写一次**，`updated_at` 不抖动 |
| **不影响他人** | 清扫**只动它标记的那几条**；一条闭环变陈旧**不改变**其它闭环的复核行 |
| **复核回来即覆盖** | 闭环重新被复核 ⇒ 正常复核行**覆盖**标记（`upsert_review_rows` 按 `loop_key` 定位） |
| **JSONL 同步** | 标记的复核行**追加**进该闭环自己的 `review_record_jsonl/<链路>/<链>/<日期>/<闭环>.jsonl` |

**清扫器跑在哪**：`rs-src` 的复核工作线程**每轮复核前**调一次，**按 scope 60 s 去重**
（96 个组不会重复扫同一个目录）；失败**如实上报**（`reason` 进载荷），**不吞**。

### 3.4 「多久算久」必须**比一轮复核更长**（**实测订正 · 2026-11-26 12:00**）

> **原假设错了**：文档初稿按「复核周期 **30 s** ⇒ 缺省 **900 s**」写。
> **实盘读数否掉了这个假设**。

| 读数（**2026-11-26 03:49–04:00Z · 重启后的新一轮**） | 值 |
|---|---|
| 一轮里被刷新的闭环数 / 用时 | **48 / 174** 条 · **11 min** ⇒ 折合 **≈ 40 min / 轮** |
| 一轮里**最晚**被轮到的那条闭环等了多久 | **52 min**（`reviewed_at` 最早 **03:07:45Z**、最晚 **04:00:05Z**） |
| 此刻**超过 900 s** 还没被复核到的闭环 | **127 / 174**（**73 %**） |
| 此刻**超过 3 600 s** 还没被复核到的闭环 | **0 / 174** |

⇒ **900 s 的窗口比一轮复核短**：它会把**正在排队**的健康闭环**整批**标成陈旧，
**标记本身就会淹没它要说的那句话**（实测：**127** 条同时带 `not_reviewed_since:`）。

**裁定**：缺省窗口改为 **5 400 s**（**90 min** ＝ **两轮**），
并**钉一条单测**（`the_default_stale_window_outlasts_a_review_round`）
——**阈值一旦被改短到一轮以内，单测立刻红**。
**窗口仍然是参数**（`REVIEW_STALE_AFTER_SECONDS`）：它只回答「多久算久」，**不放宽任何门禁**。

---

## 4. 改动清单

### 4.1 `flashloan/tools/review_manager/src/lib.rs`（`M-6`）

| 改动 | 位置 |
|---|---|
| `ReviewEvidenceInput` 新增 `closed_loop_generation: Option<ClosedLoopGenerationEvidence>` | 结构体 |
| `build_review_rows`：无证据的闭环 `continue` | 行构建 |
| `replace_review_rows` → `upsert_review_rows`（按 `loop_key` 定位） | 写入 |
| `read_stored_review_projections` / `accumulate_review_state` / `batch_content_changed` / `review_state_checksum`（存量累加） | 状态 |
| **新增** `mark_stale_reviews` ＋ `stale_loops` ＋ `mark_stale_loop` ＋ `withdraw_broadcast_request` ＋ `publish_stale_audit` | 陈旧清扫 |

### 4.2 `flashloan/rs-src/src/`（`M-13`）

| 文件 | 改动 |
|---|---|
| `closed_loop_identity.rs` | `ClosedLoopIdentity` 新增**四个代次字段**（候选边 / 观测）＋ **新增 `is_same_loop()`**（同一闭环比较：池 / 容量 / 绑定 / 借款值与小数位 / 跨 tick 阈值与上限） |
| `sqlite_read.rs` | 闭环行查询**补四列**（候选边与观测代次）⇒ 目录身份带代次 |
| `review_publish.rs` | **新增 `publish_review_loops()`**（按闭环发布 ＋ 离开目录/被重建的闭环丢弃 ＋ 幂等键按批）＋ `measured_generation()`（证据携带测量代次）＋ `identity_row_of()` |
| `worker_pool.rs` | `review_publish_block` **去全局门禁**（直接按组发布）＋ **新增 `stale_review_sweep_block()`**（陈旧清扫，工作线程每轮调用、按 scope 60 s 去重） |

---

## 5. 回归

| 套件 | 结果 |
|---|---|
| `cargo test -p review-manager` | **167 passed / 0 failed**（基线 **161** ＋ 新 **6**） |
| `cargo test`（`flashloan-rs`） | **251 passed / 0 failed**（基线 **248** ＋ 新 **3**） |
| `cargo test --workspace`（`flashloan/tools`） | **全绿 · 0 failed** |
| `cargo check --all-targets`（`flashloan-rs`） | **0 warning** |

**新增单测（复核层）**：

1. `a_closed_loop_that_went_quiet_is_marked_with_an_explicit_error`
2. `a_closed_loop_that_was_never_reviewed_is_marked_as_such`
3. `a_fresh_review_is_never_marked_as_stale`
4. `a_stale_marker_is_written_once_and_never_rewritten`
5. `a_review_that_arrives_after_the_marker_replaces_it`
6. `the_default_stale_window_outlasts_a_review_round`（**钉住阈值**）

**新增单测（发布层）**：

1. `a_per_loop_publication_publishes_the_measured_loop_under_its_own_generation`
   （**代次随证据走**：复核行落库的是**测量时**的代次 **7**，不是管理器当前持有的 **19**）
2. `a_loop_that_cannot_be_published_does_not_block_the_other_loops`
   （**三条证据**：一条可发布、一条**身份已变**、一条**已离开目录**
   ⇒ **只发布第一条**，另两条进 `dropped_loop_keys`）

---

## 5bis. 实盘读数（**已取 · 2026-11-26 11:41–12:20**）

| 项 | 读数 |
|---|---|
| **按闭环发布**已生效 | `manager_write_requests` 的 `review_publish` **191 → 466**；幂等键 `review_publish/robinhood/4663/loops/<闭环指纹>/<轮次>`（旧口径是**整目录**一条 `review_publish-r95-…`） |
| **新一代复核已写进来** | `manager_v4_reviews` ⇒ `not_executable` **19 → 58** 条（`review_reason` = `runArbitrage returned ok=false`）⇒ **不再是**全部 `data_incomplete` |
| **陈旧标记已落库** | **155** 条 `review_reason = 'not_reviewed_since:<时刻>'` ＋ **155** 条 `review_stale` 诊断（type `evidence_missing`）；`not_reviewed_at_all` **0** 条 |
| **JSONL 每闭环一个** | `flashloan/db/review_record_jsonl/robinhood/4663/2026-11-26/` 下 **174** 个文件（文件名＝闭环键） |
| **标记被复核覆盖 ＋ JSONL 累加** | 同一闭环两行相接：`03:49:06Z` ⇒ `data_incomplete` / `not_reviewed_since:2026-11-26T03:24:41.526321300+00:00`；`03:51:13Z` ⇒ `not_executable` / `runArbitrage returned ok=false` |
| **阈值订正后不再误标** | **12:11** 重启（缺省窗口 **5 400 s**）后 `[review-stale]` 日志**新增 0 行**（全程只有 11:42 那一次旧窗口的「174 of 174」）；同期复核持续推进（`not_executable` 19 → 58） |
| **广播请求** | **仍 0 行** —— **不是链路断了**：本轮复核**全部 `not_executable`**（`runArbitrage ok=false`）⇒ **没有一条正向利润** ⇒ 闸门**正确不放行** |
| **概览的 `broadcastEnabled = false`** | **是「结果」，不是「闸门」**：它读的是 `manager_broadcast_library_state`（**0 行**）—— 广播管理器**从未发布过状态行**，等价于「**从未广播过**」；**不是**「广播被关掉了」 |

---

## 6. 诚实边界

1. **广播请求仍未生成**：链路已通（**466** 次按闭环发布），但本轮复核**全部 `not_executable`
   （`runArbitrage ok=false`）** ⇒ **没有正向利润** ⇒ 闸门**正确不放行**。
   ⇒ **「持续稳定的套利广播记录」仍待市场条件**（归 `M-4` / `M-7`，**本任务不代为关闭**）。
2. **陈旧窗口是参数不是判决**：`REVIEW_STALE_AFTER_SECONDS` 缺省 **5 400 s**（**90 min** ＝ **两轮**）；
   一轮复核 **≈ 40 min** ⇒ 正常闭环**不会**被误标；但**复核组停摆**时，闭环会在 **90 min** 后
   变成 `not_reviewed_since:<时刻>`（**这正是要显式说出的事实**）。
   **窗口一旦被改短到一轮以内，单测立刻红**（`the_default_stale_window_outlasts_a_review_round`）。
3. **被重建的闭环会被丢弃**：目录重建后，**身份变了**的闭环在**缓存刷新前**（≤ **300 s**）
   收不到新复核 ⇒ 会被标成陈旧；这是**如实**的（它确实没有新复核），不是降级。
4. **写请求账本会变大**：每轮发布一条 `manager_write_requests`（**内容变化 ⇒ 键变化 ⇒ 必写**）；
   这是**幂等语义的代价**，未做清理（清理属**低概率路径**，按用户口径**先留占位**）。
5. **被替换掉的那份 DLL（`09:13:57` 构建）没有单独备份**：现存两份旧备份都早于它；
   **回滚办法** ＝ 回退本任务 **5** 个文件的改动后重新构建（**已实测**改动集合）。
6. **旧窗口写下的陈旧标记不会被撤销**：标记一旦写下，就在复核覆盖它之前一直显示
   （这是**如实**的：那些闭环**确实**没有新复核）。实测：第二轮把它们逐条覆盖回正常状态。

---
