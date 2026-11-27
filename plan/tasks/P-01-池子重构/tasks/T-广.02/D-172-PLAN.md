# D-172 `T-广.02`｜Rust 侧广播记录贯通 · 开发计划

> **本文件是 `T-广.02` 的开发计划（自包含）。**
> 范围契约见 [`D-173-SCOPE_GUARD.md`](./D-173-SCOPE_GUARD.md)，验收框见 [`D-174-checklist.md`](./D-174-checklist.md)，
> 实测读数与架构裁定见 [`docs/D-175-广播记录缺失的实测读数与架构裁定_20261126.md`](./docs/D-175-广播记录缺失的实测读数与架构裁定_20261126.md)。

**任务编号**：`T-广.02`（纯 ID）　**验收**：`AC-广.02`　**优先级**：P0
**改动模块（= 回归范围）**：`M-7`（广播层）＋ `M-13`（运行时内核与接线）＋ `M-12`（接口层，**仅穷尽 match 补一个分支**）
**前置模块**：`M-7`（广播层已就位）　**需授权**：否（只改代码；实盘生效属部署闸门）

---

## 1. 一句话

> 内核在 Rust 侧把一笔套利**广播上链**了，但**没有任何东西把它记下来**：
> `manager_cow_execution_attempts` 实测 **0 行**，`flashloan/db/broadcast_transaction_db/` 实测**空目录**
> ⇒ 月度广播库**从未生成**，「持续稳定的套利广播记录」这条目标在**记录侧**断掉。
> 本任务把**执行结果**接回**广播层**，让每一笔真实广播都落一行主库、并归档进**月度 SQLite**。

## 2. 用户口径（本任务据此裁定的边界）

| 口径 | 来源 | 本任务的落法 |
|---|---|---|
| **广播与复核线程都在 Rust 侧，Python 是遗留旧代码** | 用户 2026-11-26 | 回写**全部在 Rust 侧**完成；Python **零改动** |
| 主广播线程消费「正向盈利预期」队列（广播工作线程消费） | 用户 2026-11-25 | 本任务**不改动**队列结构；回写挂在**广播工作线程执行完任务之后** |
| 复核主线程产出正向盈利数据**并可唤醒**主广播线程 | 用户 2026-11-25 | 同上（W1 已交付，本任务不重复） |
| 手工执行**也能**激发广播线程 | 用户 2026-11-25 | 同上（W1 已交付） |
| 闭环状态机与广播工作线程**并列** | 用户 2026-11-25 | 回写**不经过**闭环状态机，两条线路互不阻塞 |
| 不需要额外授权；**复核有正向利润**即可广播 | 用户 2026-11-25 | 本任务**不动**任何准入门禁；记录发生在**广播之后** |
| **要么成功，要么失败，没有降级处理** | 用户 2026-11-26 | 记录失败**不吞**：错误进 `payload["broadcastRecord"]`，状态里看得见 |
| 异常 / 低概率路径**先留占位** | 用户 2026-11-26 | 跨月统计、落盘失败兜底等**本轮不做**（见 §5） |

## 3. 根因链（实测，见 `D-175`）

```text
内核复核出正利润（payload.status = profitable_amount_found）
  └─ should_route_to_execution_queue  ── 四道门禁全过 ⇒ 入队
       └─ 广播工作线程 ── maybe_broadcast_with_rpc_failover ── 签名 / 上链 / 取回执
            └─ ✗ 断点：结果只进内存 broadcast_history
                 （manager_cow_execution_attempts 0 行 · 月度库目录为空）
```

**唯一写入方**：`manager_cow_execution_attempts` 的唯一 `INSERT` 在
`flashloan/tools/broadcast_manager/src/lib.rs:2598`（`insert_attempt`），
只能经 `submit_broadcasts_write` / `submit_broadcasts` 到达；
**全仓没有任何调用方**在运行时调用它（`manager-api` 的 HTTP 路由有，但无人调用）。
月度库的生成挂在 `finalize_broadcast_attempts_write → publish_trade_archives`
（`broadcast_manager/src/lib.rs:1394`）⇒ **没有终态尝试 ⇒ 月度库目录为空**。

## 4. 开发计划（W 序列）

### W1｜主广播线程的唤醒语义（**上一轮已交付，本任务不重做**）

`tools/broadcast_manager/src/runtime.rs`：`ExecutionQueue` 加条件变量；
`enqueue` ⇒ `notify_all`（复核产出唤醒）；`complete` / `set_wait_until` ⇒ `notify_all`（手工执行唤醒）；
`pop_next_locked` **保留**未到期的任务（修「等待时间在未来 ⇒ 任务被丢弃」的真实缺陷）。
回归：`broadcast-manager` **35 passed / 0 failed**（基线 32 ＋ 新 3）。

### W2｜广播层新增「运行时回写」入口（`M-7`）

**新增** `flashloan/tools/broadcast_manager/src/execution_record.rs`：

- `RuntimeAttempt` —— 运行时知道的**交易事实**（账户、nonce、gas、txHash、回执、状态、证据、实测签名利润）；
- `record_runtime_attempt()` —— 写 `manager_cow_execution_attempts` ＋ 刷新 `manager_broadcast_library_state` ＋ 触发**月度归档**。

**两条硬约束**（本任务的核心裁定）：

1. **元数据来自复核行，不来自运行时**
   闭环身份（`anchor_token_key_id` / `path_*` / `pool_path_key` / `pool_ids` / 容量锚 / 逐跳价格上限）
   由 `manager_v4_reviews` 里该 `loop_key` 的那一行提供 —— 复核层拥有身份，
   所以**尝试行与复核行不可能各说各话**。**该复核行不存在 ⇒ 明确失败**（`BroadcastError::ReviewRowMissing`），
   绝不拿猜的值凑一行。
2. **记录路径不依赖「实时的广播请求行」**
   `manager_v4_review_broadcast_requests` 由复核层**每代整体替换**（先 `DELETE` 再 `INSERT`，
   `review_manager/src/lib.rs:3164-3186`）⇒ 它是**当前代的快照**，不是历史。
   若回写依赖它，则「广播发出 → 回写到达」这几秒里只要市场变了、下一代发布把该行删掉，
   这笔**已经上链**的事实就记不下来。
   ⇒ 回写**不要求**请求行存在；准入判据（`BC-1` / `BC-2`）**管的是「要不要发」，不是「要不要记」**。

### W3｜运行时侧接线（`M-13`）

**新增** `flashloan/rs-src/src/broadcast_record.rs`：
从 payload + 广播结果里取事实，组装 `RuntimeAttempt`，调用 `record_runtime_attempt`。

- 账户：广播结果里的 `signer`；未走到签名那一步时，用**配置里的私钥**推导出同一个账户
  （`execution::signer_address_from_private_key`，新增 `pub(crate)` 包装）；
- 签名利润：payload 里内核自己算出的 `signedPnl`（**同一个结论**，不是二次推导）；
- 状态：`ok=true` 且回执未 revert ⇒ `success`；回执 `0x0` ⇒ `failed`（已上链但回滚）；
  未被接受 ⇒ `failed`（带链上返回的 `error`）；
- 证据：广播结果全文 ＋ 四道门禁的裁决 ＋ 金额 / 利润 / RPC 元数据 ⇒ 「发了什么、凭什么发」都能从这一行读出来。

**接点**：`worker_pool.rs::run_execution_task` 在广播之后写
`payload["broadcastRecord"]`。**该函数在并行线程的改动区之外**（diff 覆盖区止于 `4143` 与 `4691`）。

### W4｜幂等与月份归档

- `attempt_id` 由**运行时**给：上了链的用 `runtime:{network}:{chain_id}:{txHash}`
  ⇒ 同一笔交易重放是**同一行**（更新，不新增）；没上链的用「闭环 + 状态 + 时刻」
  ⇒ 同一闭环的两次失败是**两行**；
- 重复写入走 `UPDATE`，**`created_at` 不动** ⇒ 月度库按 `created_at` 分月，重记不会把行搬进下个月；
- 广播层**只校验不重命名**（`attempt.attempt_id` 为空 ⇒ `InvalidRequest`）
  ⇒ **行里的身份**与**载荷回给内核的身份**永远是同一个字符串；
- 月度归档复用既有 `publish_trade_archives` ⇒ 目录形态就是用户定的
  `flashloan/db/broadcast_transaction_db/<链路>/<chain_id>/<YYYY-MM>/trades.sqlite3`，
  **所有方向闭环同库**（既有 DDL 逐字符克隆主库）。

### W5｜回归与登记

```powershell
cd flashloan/tools ; cargo test --offline -p broadcast-manager      # M-7
cd ../rs-src    ; cargo test --offline                              # M-13
cd ../tools     ; cargo test --offline -p manager-api                # M-12（穷尽 match 补分支）
```

**实测读数（2026-11-26）**：`broadcast-manager` **40 passed / 0 failed**（基线 **35** ＋ W1 的 **3** ＋ 本任务 **5**）；
`flashloan-rs` **230 passed / 0 failed**（基线 **222** ＋ 本任务 **8**）；
`manager-api` **33 passed / 0 failed**。
日志：`plan/runtime_evidence/_cargo_bm02_test.*.log` ／ `_cargo_rs_bm02_test.*.log` ／ `_cargo_api_bm02_test.*.log`。

## 5. 本轮**不做**（留占位，按用户口径「低概率路径最后做」）

| 项 | 为什么留 | 占位位置 |
|---|---|---|
| 跨月 / 跨年聚合查询 | 需要同时挂载多个历史月库，属**报表**能力，不是「跑通流程」的前置 | `D-175` §5 |
| 月度落盘失败的降级兜底 | 既有 `records` 层已有 `note_trade_publish_failure` 通道；本轮**不新增**第二条兜底 | `records.rs`（既有） |
| 广播请求行的**生命周期**改造（`generated` 行不被下一代抹掉） | 属于**准入**语义（`BC-1`）的改动，与本任务「记录已发生的事实」**不同层**，单独立项 | `D-175` §5 |
| 实盘重启 / 迁移 018 部署 | 与 `T-闭.03` / `T-核.02` / `T-核.03` **同窗口**，不在本任务内 | `D-174` §6 |

## 6. 部署闸门（**与其它任务同窗口，无中间态**）

本任务的代码在 **Rust 扩展**里 ⇒ 实盘生效需要：

1. `flashloan/rs-src` 重新构建（`target/release/flashloan_rs.dll`）；
2. runtime 重启（当前 pid **24524** 自 **2026-11-25 17:14:00** 起未重启）；
3. 与迁移 **018** **同批**（库现停在 `20261125_017`）⇒「新二进制 ＋ 旧库」实测报
   `no such column: single_tick_capacity_binding_directional_pool_id`。

⇒ 本任务**如实登记为「代码已闭合、实盘未生效」**，与 `T-闭.03` / `T-核.02` / `T-核.03` 一起等同一个窗口。
