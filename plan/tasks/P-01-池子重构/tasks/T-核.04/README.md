# `T-核.04`｜复核定价路径的 RPC 故障转移

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../../../README.md)。

**编号**：`T-核.04`（纯 ID）　**验收**：`AC-核.04`　**优先级**：P0
**编号逻辑**：`T-核.04` / `AC-核.04` —— **模块内编号**，编号索引 **`核`**
（改动模块：`M-13`（运行时内核与运行时接线）、`M-1`（池管理器 / 链配置读取）），
该索引号段内第 `04` 个。

**改动模块（= 回归范围）**：`M-13`、`M-1`

**前置模块**：`M-7`　　**需授权**：否（只改代码；实盘生效属部署闸门）

## 文件

| 文件 | 内容 |
|---|---|
| `D-180-PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `D-181-SCOPE_GUARD.md` | 本任务的修改范围守护与逐模块回归命令 |
| `D-182-checklist.md` | 本任务的逐条验收框 |
| `docs/D-183-复核定价路径的RPC故障转移_20261126.md` | 实测读数、根因链与裁定 |

## 一句话

> 实盘 **174** 条复核**全部**是 `data_incomplete`（`signed PnL is missing or incomplete`），
> 根因**不是**链上执行、也不是容量，而是**路由根本没有被定价**：
> `official_v4:previewBestPool:eth_call` 请求失败，而**定价路径只用一个 RPC 端点**。
> 链上发布了 **5** 个端点，其中 **3** 个当时健康，可定价路径一个都没试。
> 本任务让定价路径与广播路径**按链取回端点全集**并沿用既有的故障转移，
> 于是「一个节点坏了」不再等于「一条路由没测过」。

**当前状态（2026-11-26）**：**代码项全部闭合**（逐条读数见 `D-182`）；
回归 `flashloan-rs` **245 passed / 0 failed**（基线 **241** ＋ 新 **4**）、
`pool-manager` **169 passed / 0 failed**（含新 **2**）。

## 用户口径（本任务据此裁定的边界）

| 口径 | 落法 |
|---|---|
| **广播与复核线程都在 Rust 侧，Python 是遗留旧代码** | 端点解析放在 **Rust** 侧（`pool_manager::chain` ＋ `worker_pool.rs`），**Python 零改动** |
| **要么成功，要么失败，没有降级处理** | 故障转移**不是降级**：端点全集是「这条链可用节点」的真相，择其一成功即成功；**全部失败就是失败**（错误照旧上抛） |
| 异常 / 低概率路径**先留占位** | 本任务**不碰**重试次数、退避策略与超时预算（**一字未改**），只把**可用端点补齐** |
| 优先把系统**跑通** | 本任务是「跑通处理流程」的**直接前置**：没有定价就没有复核完成，就没有广播记录 |

## 实盘读数（只读 · 2026-11-26 07:0x–07:5x）

主库 `flashloan/db/local_store_4663.sqlite3`（`mode=ro`）⇒ `manager_v4_reviews`
`data_incomplete` **174** / `completed` **0**；
`manager_v4_review_diagnostics` ⇒ `evidence_missing` / `measured_signed_pnl_missing` **174**；
诊断 `payload_json.error` ⇒ `official_v4:previewBestPool:eth_call: eth_call RPC request failed: error sending request for url (…)`，
`errorKind = rpc_verification_failed`，`status = route_error`。

`.env` 的 `ROBINHOOD_RPCS` **5** 个端点实测（`eth_blockNumber`）⇒
`rpc.ordofi.network` **DNS 解析失败**、`robinhood-mainnet.g.alchemy.com` **HTTP 429**，
其余 **3** 个（`rpc.mainnet.chain.robinhood.com`、`robinhood-rpc.publicnode.com`、
QuickNode）**HTTP 200**。

证据与裁定见 [`docs/D-183-…`](./docs/D-183-复核定价路径的RPC故障转移_20261126.md)。

## 独立回归

```powershell
cd flashloan/tools ; cargo test --offline -p pool-manager
cd flashloan/rs-src ; cargo test --offline
```
