# `T-核.03`｜流动性快照写入路径接线

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../../../README.md)。

**编号**：`T-核.03`（纯 ID）　**验收**：`AC-核.03`　**优先级**：P0　**波次**：`W-1`
**编号逻辑**：`T-核.03` / `AC-核.03` —— **模块内编号**，编号索引 **`核`**
（改动模块：`M-13`（运行时维护链）＋ `M-1`（池管理器）），该索引号段内第 `03` 个。

**改动模块（= 回归范围）**：`M-13` ＋ `M-1`

**前置模块**：`M-1`　　**需授权**：否（只改代码；实盘生效属部署闸门）

## 文件

| 文件 | 内容 |
|---|---|
| `D-164-PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `D-165-SCOPE_GUARD.md` | 本任务的修改范围守护与逐模块回归命令 |
| `D-166-checklist.md` | 本任务的逐条验收框 |
| `docs/` | 本任务的**独立技术文案**区（根因链 / 实测读数 / 边界） |

## 一句话

> 快照表 `v4_exchange_pool_liquidity_snapshots` 的**唯一写入方**是 Rust 池管理器，
> 而它**只能通过命令行调用** ⇒ 维护槽每 tick 量到的链上状态**被测量、被丢弃**；
> worker 却因「裁决都在」报 `complete` ⇒ **陈旧被当成完整**。
> 本任务把池管理器接进维护链，并让回执报告**真正写进去的行数**。

**当前状态（2026-11-26）**：**16 / 16** 项闭合。
修复**已在代码里落地**：`rs-src/src/pool_depth.rs`（新入口）＋ 维护槽调用 ＋ 桥 ＋
池管理器回执诚实计数；回归 `pool-manager` **167** ／ `flashloan-rs` **222** ／ pytest **19 ＋ 3**。
**如实登记**：**实盘尚未生效**（扩展未重建、runtime 未重启 ⇒ 属部署闸门）；
`mode` 判据**未切换** —— 新鲜度在本任务里**只做观测量**，
**不是选池条件**（选池条件是方向价格优势 ＋ 深度 ＋ 费率，本任务**一字未改**）。

## 实盘读数（只读 · 2026-11-26）

快照表 **2486** 行，最新 `liquidity_read_at` = **2026-11-25T00:44:27Z**（+08 08:44）；
行龄 `11-17: 1244` / `11-22: 358` / `11-25: 96`；
worker 游标 `mode = complete`、`last_block_number = 123234506`（快照行的最大区块只有 **120653940**）。
证据：`plan/runtime_evidence/liquidity_snapshot_freshness_20261126.json`。

## 独立回归

```powershell
cd flashloan/tools  ; cargo test --offline -p pool-manager
cd ../rs-src       ; cargo test --offline
cd ../..           ; pytest flashloan/app_runtime/tests/unit/test_v4_maintenance_workers.py `
                            flashloan/app_runtime/tests/unit/test_v4_pool_liquidity_snapshot_writer.py -q
```

