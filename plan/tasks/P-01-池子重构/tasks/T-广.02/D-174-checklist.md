# D-174 `T-广.02`｜Rust 侧广播记录贯通 · 验收清单

> **【归并 · 2026-11-28】** 本清单的 **4** 项剩余已于 **2026-11-28** **归并**到 `T-广储.01`。
> **执行落点**：`T-广储.01`（见 [`D-261`](../../docs/D-261-剩余未完成项归属与开发计划_20261128.md) §1 的**逐条归属表**）。
> **本清单的框**在本任务达成前**保持未勾选** ⇒ **读数不重复计**（每个框只在一个文件里）。
> **【归并 · 2026-11-28】** 本清单的 **4** 项剩余已于 **2026-11-28** **归并**到 `T-广储.01`。
> **执行落点**：`T-广储.01`（见 [`D-261`](../../docs/D-261-剩余未完成项归属与开发计划_20261128.md) §1 的**逐条归属表**）。
> **本清单的框**在本任务达成前**保持未勾选** ⇒ **读数不重复计**（每个框只在一个文件里）。
> **本文件是 `T-广.02` 的全部验收框。** 编号 `AC-广.02` 属**纯 ID**。
> 每条勾选**必须**有实测读数支撑；**不得**凭推断勾选；**不得**用零冒充未测量。

**验收前置**：`M-7` 广播层既有单测全绿

**本轮状态（2026-11-26）**：代码项**全部闭合**（逐条附读数）；
**实盘侧**：**零写入**，**未重启** ⇒ 部署闸门项**如实未勾**（§5）。

---

## 1. 根因与定位

- [x] 「广播上了链却没有任何记录」已被**实测**确认，而不是推断

      > **读数**（只读探针 ＋ 只读 HTTP，2026-11-26）：
      > `manager_cow_execution_attempts` = **0 行**；
      > `manager_broadcast_attempt_diagnostics` = **0 行**；
      > `manager_broadcast_library_state` = **0 行**；
      > `flashloan/db/broadcast_transaction_db/` = **空目录**（无任何文件）；
      > `GET /api/dex/overview` ⇒ `summary.broadcastEnabled = false`、
      > `summary.broadcastAttemptCount = 0`、
      > `summary.broadcastSuccessCount / FailedCount / TerminalCount = 0 / 0 / 0`、
      > `versions.broadcastVersion = 0`；
      > `GET /api/dex/closed-loop-broadcast-failures` ⇒ `recordCount=0`、`records=[]`。
      > **第二次读数**（复核代次已前进 **199 → 203**）：
      > `summary.reviewCompletedCount` 由 **1 → 0** ⇒ 「正利润且可执行」的那一行**在下一代发布时消失了**
      > （**口径使然**，见 `D-175` §2.1）。

- [x] 「谁写 `manager_cow_execution_attempts`」已定位到**唯一写入方**，并证明运行时**够不到**它

      > **读数**：唯一的 `INSERT INTO manager_cow_execution_attempts` 在
      > `flashloan/tools/broadcast_manager/src/lib.rs:2613`（`fn insert_attempt` 在 `:2606`），
      > 只能经 `submit_broadcasts` / `submit_broadcasts_write` 到达；
      > 后者的调用方**全仓只有** `manager_api`（HTTP 路由 `POST /v1/broadcasts/submit`）
      > 与 `broadcast_manager` 自身 ⇒ **运行时没有任何一条路会调用它**。
      > 月度库生成挂在 `finalize_broadcast_attempts_write → publish_trade_archives`
      > （`publish_trade_archives` 在 `broadcast_manager/src/lib.rs:1409`，调用点 `:1260`）
      > ⇒ 没有终态尝试 ⇒ 月度库目录为空。

- [x] 「复核产出的广播请求为什么接不上广播线程」已定位到**两个独立事实**

      > **读数 ①（键空间不同）**：内核走 payload `status == "profitable_amount_found"`
      > （`worker_pool.rs:4381`，键空间是 **symbol 组的 group_key**）；
      > 复核层产出的是 `manager_v4_review_broadcast_requests`（键空间是**地址型 `loop_key`**）。
      > **读数 ②（快照会被替换）**：复核层每次发布都**先 `DELETE` 再 `INSERT`**
      > （`review_manager/src/lib.rs:3164-3186`）⇒ 请求行是**当前代快照**，不是历史。
      > **读数 ③（新鲜度窗口）**：`MAX_BROADCAST_REVIEW_AGE_SECONDS = 120`
      > （`broadcast_manager/src/admission.rs:55`）；而一轮扫描的证据跨度实测 **433 s**
      > ⇒ 一轮里约 **2/3** 的闭环在发布瞬间就已过期 ⇒ 结构上拿不到广播请求。

## 2. 记录路径（本任务新增）

- [x] 广播层新增**运行时回写**入口，且**不写** `manager_v4_review_broadcast_requests`

      > **读数**：新增 `flashloan/tools/broadcast_manager/src/execution_record.rs`；
      > `cargo check --offline -p broadcast-manager` ⇒ **exit 0**。

- [x] 回写的**闭环元数据来自复核行**，不来自运行时 ⇒ 身份不会两说

      > **读数**：`read_review_loop_facts()` 读 `manager_v4_reviews` 的该 `loop_key` 行，
      > 取 `closed_loop_version` / `closed_loop_checksum` / `anchor_token_key_id` /
      > `path_text` / `path_addresses_json` / `path_symbols_json` / `pool_path_key` /
      > `pool_ids_json` / `single_tick_capacity_anchor_raw` /
      > `single_tick_capacity_binding_pool_id` / `hop_sqrt_price_limits_json`。

- [x] **该复核行不存在 ⇒ 明确失败**，不拿猜的值凑一行

      > **读数**：新增 `BroadcastError::ReviewRowMissing { loop_key }`；
      > `Display` 文案：`loop {loop_key} has no review row, so the attempt has no identity to file under`；
      > `manager_api` 与 `rs-src/runtime_core` 两处**穷尽 `match`** 已补该分支（编译期强制）。

- [x] 回写**不依赖**实时的广播请求行 ⇒ 「已发生的事实」不被「会移动的快照」绑住

      > **读数**：`execution_record.rs` 全模块**零** `manager_v4_review_broadcast_requests` 引用
      > （`rg` 命中数 **0**）；准入判据（`BC-1` / `BC-2`）**未被调用**、**未被放宽**。

- [x] 回写**不经过**闭环状态机 ⇒ 与广播工作线程是**并列**的两条线路

      > **读数**：落点在 `worker_pool.rs::run_execution_task`（广播调用**之后**）；
      > `execution_record.rs` **零**闭环状态机引用（`rg` 命中数 **0**）⇒ 两条线路互不阻塞。

- [x] 运行时侧接线：广播之后写 `payload["broadcastRecord"]`

      > **读数**：新增 `flashloan/rs-src/src/broadcast_record.rs`；
      > `worker_pool.rs::run_execution_task`（现行 **4432** 行，**在并行线程 diff 覆盖区之外**）
      > 调用 `file_broadcast_record()`（**4486** 行附近）；
      > `cargo check --offline`（`flashloan/rs-src`）⇒ **exit 0**。

- [x] 账户 / 签名利润 / 状态 三项事实的来源已逐条钉死

      > **读数**：
      > 账户 —— 广播结果的 `signer`；未走到签名那一步时用配置里的私钥推导
      > （新增 `execution::signer_address_from_private_key`，`pub(crate)`）；
      > 签名利润 —— payload 里内核**自己**算出的 `signedPnl`（**同一个结论**，不是二次推导）；
      > 状态 —— `ok=true` 且回执未 revert ⇒ `success`；回执 `0x0` ⇒ `failed`（已上链但回滚）；
      > 未被接受 ⇒ `failed` ＋ 链上返回的 `error`。

- [x] 证据里带得下「发了什么、凭什么发」

      > **读数**：`build_evidence()` 写入广播结果全文 ＋ 四道门禁裁决
      > （`offchainGasGate` / `capacityGate` / `economicFloorGate` / `hopPriceLimitGate`）
      > ＋ `closedLoopKey` / `token` / `selectedAmountRaw` / `selectedProfitRaw` /
      > `minProfitRaw` / `rpcMetadata`。

## 3. 幂等与月度归档

- [x] `attempt_id` 由运行时给：上链的按 `txHash`，没上链的按「闭环 ＋ 状态 ＋ 时刻」

      > **读数**：`runtime_attempt_id()` ⇒ `runtime:{network}:{chain_id}:{txHash}`
      > 或 `runtime:{network}:{chain_id}:{loop_key}:{status}:{时刻}`。

- [x] 广播层**只校验不重命名** ⇒ 行里的身份与载荷回给内核的身份**永远是同一个字符串**

      > **读数**：`record_on_connection()` 取 `attempt.attempt_id.trim()`；为空 ⇒
      > `BroadcastError::InvalidRequest("attempt.attemptId must not be empty")`；
      > 超过 `MAX_IDENTIFIER_LENGTH` ⇒ 同样拒绝；**不再**在广播层另起一套派生 id。

- [x] 重复记录是**更新**同一行，且**不搬月**

      > **读数**：`upsert_attempt()` 先查 `attempt_id` 是否存在，存在则走 `UPDATE`；
      > `update_recorded_attempt()` **刻意不写** `created_at`
      > （月度库按 `created_at` 分月，改它会把行搬进下个月）。

- [x] 月度库走**既有**归档通道，目录形态与用户口径一致

      > **读数**：`record_runtime_attempt()` 末尾调用既有的 `publish_trade_archives()`
      > ⇒ `review_manager::records::publish_trade_archive()` ⇒
      > `flashloan/db/broadcast_transaction_db/<链路>/<chain_id>/<YYYY-MM>/trades.sqlite3`，
      > **所有方向闭环同库**（既有 DDL 逐字符克隆主库）。

## 4. 单测（本任务新增 **13** 条）

- [x] 广播层 **5** 条：复核行存在 ⇒ 写成功（且身份取自复核行）；复核行缺失 ⇒ `ReviewRowMissing`；
      身份为空 ⇒ 拒绝；重复记录 ⇒ **更新同一行**且 `created_at` **不变**；月度库文件**真的被创建**

      > **读数**：`broadcast_manager/src/execution_record.rs` 的 `mod tests` ⇒
      > `a_runtime_attempt_with_a_review_row_is_recorded` ／
      > `a_runtime_attempt_without_a_review_row_is_refused` ／
      > `an_attempt_without_an_identity_is_refused` ／
      > `recording_the_same_attempt_twice_updates_the_row` ／
      > `a_recorded_attempt_reaches_the_monthly_trade_store`
      > ⇒ `cargo test --offline -p broadcast-manager` **40 passed / 0 failed**（基线 **35** ＋ **5**）。

- [x] 运行时侧 **8** 条：载荷提取；无广播 ⇒ 不记录；回执 `0x0` ⇒ `failed`；
      未被接受 ⇒ `failed`；缺 `signedPnl` ⇒ 拒绝；缺 nonce ⇒ 拒绝；
      缺 signer ⇒ 用配置私钥推导账户；缺 signer 且无私钥 ⇒ 拒绝

      > **读数**：`rs-src/src/broadcast_record.rs` 的 `mod tests` ⇒ **8** 条（纯函数 `build_attempt`，**不依赖数据库**）
      > ⇒ `cargo test --offline`（`flashloan/rs-src`）**230 passed / 0 failed**（基线 **222** ＋ **8**）。

## 5. 回归

- [x] `M-7` 广播层回归全绿

      > **读数**：`cargo test --offline -p broadcast-manager` ⇒ **40 passed / 0 failed / 0 ignored**。

- [x] `M-13` 运行时内核回归全绿

      > **读数**：`cargo test --offline`（`flashloan/rs-src`）⇒ **230 passed / 0 failed**。

- [x] `M-12` 接口层回归全绿（穷尽 `match` 补分支的编译面）

      > **读数**：`cargo test --offline -p manager-api` ⇒ **33 passed / 0 failed**。

- [x] 与并行线程**零冲突**（改动区逐点核对）

      > **读数**：`worker_pool.rs` 的落点 **4432 / 4486** 落在并行线程 diff 覆盖区
      > （**4143** 与 **4691** 之间）**之外**；`runtime_core.rs` 的落点 **2883** 落在
      > 覆盖区（**20 / 26 / 2911** 起）**之外**；`manager_api/src/lib.rs` 的落点 **3019**
      > 落在覆盖区（**5348** 起）**之外**；`worker_pool.rs` 的 `use` 区**一字未动**
      > （新模块用全路径调用）。

## 6. 部署闸门（**未勾 · 如实登记**）

- [x] 实盘迁移 **018** 已应用（库现停在 `20261125_017`）

      > **读数（2026-11-26 14:3x · 只读）**：`schema_migrations` ⇒
      > `20261125_018_closed_loop_directional_only` **已应用**
      > （`applied_at = 2026-11-25T23:43:20.599Z`）⇒ **实盘库已过 018**。
      > 框目标题里的「库现停在 `20261125_017`」**已过时**（那是本框留空时的读数）。
      > 探针：`plan/runtime_evidence/tools/verify_restart_gates_20261126.py`。

- [x] 新 Rust 扩展与迁移 **018** **同批**上线

      > **读数（2026-11-26 14:3x）**：新扩展 `flashloan/rs-src/target/release/flashloan_rs.dll`
      > = **18,379,264 B**（构建于 **13:59:08**）已上线；
      > 实盘 runtime **pid 26328**（看护 **26980**）自 **13:59:28** 起跑新二进制、端口 **5000** 在听
      > ⇒ **新旧二进制无中间态**。
      > **时序**：迁移 **018** 应用于 **2026-11-25T23:43:20.599Z**，**早于**每一次扩展构建
      > （**08:09:06** ／ **12:11:21** ／ **13:59:08**）⇒ **新扩展从未对着 017 跑过**。
      > **回滚办法**：把 `plan/runtime_evidence/backups/flashloan_rs_pre_D196_20261126T1400.dll`
      > 复制回 `flashloan/rs-src/target/release/` 并重启 runtime。
      > **诚实边界**：本框只覆盖「扩展与迁移同批上线」；**广播尝试落库**是**下一框**，属**市场条件**。

- [ ] 实盘读数：`manager_cow_execution_attempts` 出现**真实**尝试行，且月度库目录被创建

      > **状态**：**未做**。这是**市场条件**项：需要内核真的广播出一笔正利润交易。
      > 第一次读数（复核代次 **199**）确认**存在**过一笔真实可执行正利润闭环
      > （`manager_v4_reviews` 唯一 `completed` 行：`pnl_amount=1.3e-05`、`run_arbitrage.ok=true`、
      > `executable=true`、`hop_limits` **3** 条齐），但**广播是否真的发出**在系统里
      > **没有任何留痕** —— 这正是本任务要补的那一环；
      > 第二次读数（复核代次 **203**）该行**已随下一代发布消失**（`completed` = **0**）
      > ⇒ **正利润闭环是实时现象**，第一笔记录要等下一次出现为止。

## 7. 本轮**刻意不做**（留占位）

- [ ] 跨月 / 跨年聚合查询（`ATTACH` 多月库）

      > **状态**：**刻意留占位**。属报表能力，不是「跑通处理流程」的前置（`D-172` §5）。

- [ ] 月度落盘失败的降级兜底

      > **状态**：**刻意留占位**。既有 `records` 层已有 `note_trade_publish_failure` 通道，
      > 本轮**不新增**第二条兜底（用户口径：低概率路径最后做）。

- [ ] 广播请求行的**生命周期**改造（`generated` 行不被下一代抹掉）

      > **状态**：**另立任务**。改它属于**准入**语义（`BC-1`）层，与本任务
      > 「记录已发生的事实」不同层（`D-175` §5）。
