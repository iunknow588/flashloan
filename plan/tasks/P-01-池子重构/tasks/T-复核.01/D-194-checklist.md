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

