# `T-广.02`｜Rust 侧广播记录贯通

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../../../README.md)。

**编号**：`T-广.02`（纯 ID）　**验收**：`AC-广.02`　**优先级**：P0　**波次**：`W-3`
**编号逻辑**：`T-广.02` / `AC-广.02` —— **模块内编号**，编号索引 **`广`**
（改动模块：`M-7`（广播层）＋ `M-13`（运行时内核与接线）＋ `M-12`（接口层，**仅穷尽 `match` 补一个分支**）），该索引号段内第 `02` 个。

**改动模块（= 回归范围）**：`M-7` ＋ `M-13` ＋ `M-12`

**前置模块**：`M-7`　　**需授权**：否（只改代码；实盘生效属部署闸门）

## 文件

| 文件 | 内容 |
|---|---|
| `D-172-PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `D-173-SCOPE_GUARD.md` | 本任务的修改范围守护与逐模块回归命令 |
| `D-174-checklist.md` | 本任务的逐条验收框 |
| `docs/D-175-广播记录缺失的实测读数与架构裁定_20261126.md` | 实测读数、根因链与六条架构裁定 |

## 一句话

> 内核在 Rust 侧把一笔套利**广播上链**了，但**没有任何东西把它记下来**：
> `manager_cow_execution_attempts` 实测 **0 行**、`flashloan/db/broadcast_transaction_db/` 实测**空目录**
> ⇒ 月度广播库**从未生成**，「持续稳定的套利广播记录」这条目标在**记录侧**断掉。
> 本任务把**执行结果**接回**广播层**，让每一笔真实广播都落一行主库、并归档进**月度 SQLite**。

**当前状态（2026-11-26）**：**代码项全部闭合**（逐条读数见 `D-174`）；
回归 `broadcast-manager` **40 passed / 0 failed**（基线 **35** ＋ 新 **5**）、
`flashloan-rs` **230 passed / 0 failed**（基线 **222** ＋ 新 **8**）、
`manager-api` **33 passed / 0 failed**。
**如实登记**：**实盘尚未生效**（Rust 扩展未重建、runtime 未重启、迁移 **018** 未落）
⇒ 与 `T-闭.02` / `T-闭.03` / `T-核.02` / `T-核.03` **同窗口**、**无中间态**。

## 用户口径（本任务据此裁定的边界）

| 口径 | 落法 |
|---|---|
| **广播与复核线程都在 Rust 侧，Python 是遗留旧代码** | 回写**全部在 Rust 侧**完成；Python **零改动** |
| 主广播线程消费「正向盈利预期」队列（广播工作线程消费） | 本任务**不改动**队列结构；回写挂在**广播工作线程执行完任务之后** |
| 复核主线程产出正向盈利数据**并可唤醒**主广播线程 | 同上（**W1 已交付**，本任务不重做） |
| 手工执行**也能**激发广播线程 | 同上（**W1 已交付**） |
| 闭环状态机与广播工作线程**并列** | 回写**不经过**闭环状态机，两条线路互不阻塞 |
| 不需要额外授权；**复核有正向利润**即可广播 | 本任务**不动**任何准入门禁；记录发生在**广播之后** |
| **要么成功，要么失败，没有降级处理** | 记录失败**不吞**：错误进 `payload["broadcastRecord"]`，状态里看得见 |
| 异常 / 低概率路径**先留占位** | 跨月统计、落盘失败兜底等**本轮不做**（见 `D-172` §5） |

## 实盘读数（只读 · 2026-11-26）

主库 `flashloan/db/local_store_4663.sqlite3`（`mode=ro` ＋ `PRAGMA query_only = ON`）：

```json
{
  "manager_cow_execution_attempts": 0,
  "manager_broadcast_attempt_diagnostics": 0,
  "manager_broadcast_library_state": 0,
  "manager_v4_reviews": 174,
  "manager_v4_review_broadcast_requests": 0,
  "review_status_counts": [["data_incomplete", 20], ["not_executable", 154]]
}
```

`GET /api/dex/overview` ⇒ `summary.broadcastAttemptCount = 0`、`summary.broadcastEnabled = false`、
`versions.broadcastVersion = 0`、`versions.closedLoopVersion = 21`、`routeFramework.totalLoops = 174`；
`flashloan/db/broadcast_transaction_db/` = **空目录**。
库迁移水位停在 **`20261125_017`**（**018 未落**）；runtime pid **24524** 自 **2026-11-25 17:14:00** 起**未重启**。

证据与裁定见 [`docs/D-175-…`](./docs/D-175-广播记录缺失的实测读数与架构裁定_20261126.md)。

## 独立回归

```powershell
cd flashloan/tools  ; cargo test --offline -p broadcast-manager
cd ../rs-src       ; cargo test --offline
cd ../tools        ; cargo test --offline -p manager-api
```

