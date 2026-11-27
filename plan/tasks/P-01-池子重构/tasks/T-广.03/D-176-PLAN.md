# D-176 `T-广.03`｜广播链路 Rust 归位 · 开发计划

> **本文件是 `T-广.03` 的开发计划（自包含）。**
> 范围契约见 [`D-177-SCOPE_GUARD.md`](./D-177-SCOPE_GUARD.md)，验收框见 [`D-178-checklist.md`](./D-178-checklist.md)，
> 实测读数与架构裁定见 [`docs/D-179-广播准入归位与复核线程不再广播_20261126.md`](./docs/D-179-广播准入归位与复核线程不再广播_20261126.md)。

**任务编号**：`T-广.03`（纯 ID）　**验收**：`AC-广.03`　**优先级**：P0
**改动模块（= 回归范围）**：`M-13`（运行时内核与接线）
**前置模块**：`M-7`（广播层已就位）　**需授权**：否（只改代码；实盘生效属部署闸门）

---

## 1. 一句话

> 用户口径「**广播与复核线程都工作在 Rust 侧，Python 是遗留的旧代码**」在代码里**还没有兑现**：
> 复核线程**自己广播**＋两枚授权开关（`deferBroadcast` / `broadcastEnabled`）
> **仍由 Python 注入**、Rust 只是读取 ⇒ 实盘**两条广播路都发不出去**（尝试表 **0** 行）。
> 本任务把**广播准入**与**广播动作**全部收进 Rust：准入在收单边界**判定一次**并写回，
> 复核线程**只产出结论**，广播一律由**广播工作线程**执行。

## 2. 用户口径（本任务据此裁定的边界）

| 口径 | 来源 | 本任务的落法 |
|---|---|---|
| **广播与复核线程都在 Rust 侧，Python 是遗留旧代码** | 用户 2026-11-26 | 准入判定、广播动作、记录回写**全部在 Rust 侧**；Python **零改动**，其两枚授权字段降级为**兼容输入**（记录、不作门禁） |
| 主广播线程消费「正向盈利预期」队列（广播工作线程消费） | 用户 2026-11-25 | **已就位**（`broadcast_manager/src/runtime.rs`：`ExecutionQueue` 条件变量）；本任务**不改动**队列结构 |
| 复核主线程产出正向盈利数据**并可唤醒**主广播线程 | 用户 2026-11-25 | **已就位**（`enqueue` ⇒ `notify_all`）；本任务让复核线程**只产出**、把广播交给队列 |
| 手工执行**也能**激发广播线程（设置处理等待时间） | 用户 2026-11-25 | **已就位**（`set_wait_until` ⇒ `notify_all`）；本任务**不改动** |
| 闭环状态机与广播工作线程**并列** | 用户 2026-11-25 | 本任务的准入**不经过**闭环状态机，两条线路互不阻塞 |
| **不需要额外授权**；只要**复核有正向利润**就可以广播 | 用户 2026-11-25 | 授权开关**默认放开**（未设置 ⇒ 放开）；**唯一硬要求**是「有签名私钥」＋「复核报正利润」＋运行期闸门全过 |
| **要么成功，要么失败，没有降级处理** | 用户 2026-11-26 | 执行队列不可用 ⇒ 复核**显式标注** `broadcastSkipped` / `broadcastQueueUnavailable`，**不偷偷直广播** |
| 异常 / 低概率路径**先留占位** | 用户 2026-11-26 | 落盘失败兜底、跨月统计**本轮不做**（见 `D-176` §6） |

## 3. 根因链（实测，见 `D-179`）

```text
Python（遗留）构造任务配置
  ├─ deferBroadcast   = broadcast_enabled AND 执行队列环境开关   ← 遗留注入
  └─ broadcastEnabled = 自动执行开关 AND 链级授权 AND 私钥已配置     ← 遗留注入
       │                        （且私钥**不下发**：配置里写 null）
       ▼
Rust 复核线程读这两枚字段
  ├─ deferBroadcast = true  ⇒ 复核只出结论，入队 ⇒ 广播工作线程广播 ⇒ 记录 ✅
  └─ deferBroadcast = false ⇒ **复核线程自己广播** ⇒ 绕过队列 ⇒ **绕过记录** ✗
⇒ 今天落在**第二行**：绕过队列，而队列那一侧根本没被启用 ⇒ **两条都不发** ⇒ 尝试表 0 行 ⇒ 概览 `summary.broadcastEnabled = false`（**结果**）。
```

**实测读数**（2026-11-26，只读）：

- `GET /api/dex/overview` ⇒ `summary.broadcastAttemptCount = 0`、`summary.broadcastEnabled = false`（**广播库从未发布过状态行** ⇒ **结果**不是原因）、
  `summary.reviewCompletedCount = 0`、`summary.reviewNotExecutableCount = 154`；
- `manager_v4_reviews` 状态分布 ⇒ `completed` **0**、`not_executable` **154**、`data_incomplete` **20**；
  `not_executable` 的主因 ⇒ `runArbitrage returned ok=false` **149** 条；
- `.env` ⇒ `ROBINHOOD_MAINNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST=true`、
  **`DEX_AUTO_EXECUTE_VERIFIED_OPPORTUNITIES` 未设置** ⇒ 遗留闸门算出的 `broadcastEnabled` 必然为 `false`
  （该闸门要求三个条件同时成立）；
- `LIQUIDATION_EXECUTION_PRIVATE_KEY` **已配置** ⇒ 签名私钥在环境里是有的，只是**不下发**。

## 4. 开发计划（W 序列）

### W1｜新增 Rust 侧广播准入模块（`M-13`）

**新增** `flashloan/rs-src/src/broadcast_policy.rs`：

- `resolve()` —— 纯函数，判定由**注入的环境读取器**提供 ⇒ 单测**不碰进程环境**；
- **签名私钥**：任务配置 `broadcastPrivateKey` → 环境变量候选链
  （`{前缀}_DEX_PRIVATE_KEY` → `{前缀}_EXECUTION_PRIVATE_KEY` → `DEX_EXECUTION_PRIVATE_KEY` →
  `OFFICIAL_V4_PRIVATE_KEY` → `DEX_OFFICIAL_V4_PRIVATE_KEY` → `LIQUIDATION_EXECUTION_PRIVATE_KEY`）；
- **授权开关**：`{前缀}_ALLOW_OFFICIAL_V4_ARB_BROADCAST` / `OFFICIAL_V4_ALLOW_ARB_BROADCAST` /
  `DEX_AUTO_EXECUTE_VERIFIED_OPPORTUNITIES` ⇒ **未设置 ⇒ 放开**；显式 `false` ⇒ 关闭；显式 `true` ⇒ 放开；
- `normalize_config()` —— **在收单边界判定一次**并写回配置：
  `broadcastEnabled`（运行时的裁决）、`broadcastPrivateKey`（运行时解析到的私钥）、
  `deferBroadcast`（执行队列是不是唯一广播路径）、`broadcastAdmission`（裁决摘要，含遗留值）。

### W2｜复核线程**不再广播**（`M-13`）

`worker_pool.rs::run_native_review`：**删除**复核线程内的直接广播分支。

- 复核线程**只产出结论**：`broadcastDeferred` / `executionQueueRequested` / `selectedRpcUrl`；
- 执行队列不可用（`deferBroadcast = false`）⇒ **显式**写入
  `broadcastSkipped = "the_broadcast_worker_thread_owns_the_broadcast"` 与 `broadcastQueueUnavailable = true`
  ⇒ **要么成功，要么失败**：不偷偷直广播；
- **停机中的组不向广播队列交接**：`stop_requested()` 为真 ⇒ `executionQueueRequested = false` ＋ `stopRequested = true`
  （广播工作线程签名前**再查一次**同一个停机标志）。

### W3｜收单边界归一化（`M-13`）

`worker_pool.rs::submit_inner`：任务进入系统时**判定一次**准入并写回配置 ⇒
复核工作线程、路由判定、广播工作线程、记录写入**读的是同一份裁决**，谁也不会跟谁不一致。
提交应答里带 `broadcastAdmission`，操作者能直接看到裁决与理由。

### W4｜回归

`flashloan/rs-src` ⇒ **241 passed / 0 failed**（基线 **230** ＋ 新 **11**：
`broadcast_policy` **9** 条 ＋ `worker_pool` **2** 条）。

## 5. 与 `T-广.02` 的关系

| 任务 | 管的层 | 关系 |
|---|---|---|
| `T-广.02` | **记录层**：广播**发生之后**的事实落库（主库一行 ＋ 月度库归档） | 本任务让**所有**广播都从广播工作线程发出 ⇒ `T-广.02` 的记录**不再有旁路** |
| `T-广.03` | **准入层 ＋ 动作层**：谁能广播、谁来广播 | —— |

## 6. 本轮**刻意不做**（留占位）

- 跨月 / 跨年聚合查询（`ATTACH` 多月库）—— 报表能力，不是「跑通处理流程」的前置；
- 月度落盘失败的降级兜底 —— 既有 `records` 层已有 `note_trade_publish_failure` 通道，不新增第二条兜底；
- **`runArbitrage ok=false` 的链上根因**（实盘 `not_executable` 的 **149** 条主因）——
  属链上执行 / 容量侧，**不在本任务范围**，本任务**不代为关闭**；
- 复核**完成**率（实盘 `completed` = **0**）—— 同上，属上游复核层。

## 7. 独立回归

```powershell
cd flashloan/rs-src ; cargo test --offline
```

