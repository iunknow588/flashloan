# `T-核.08`｜写锁根治·其一：运行时写事务瘦身与测量外置

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../../../README.md)。

**编号**：`T-核.08`（纯 ID）　**验收**：`AC-核.08`　**优先级**：P0
**编号逻辑**：`T-核.08` / `AC-核.08` —— **模块内索引**，编号索引 `核` ＝ `M-13`（运行时内核与运行时接线），该索引号段内第 `08` 个（`T-核.01`~`T-核.07` 已占用，**不复用**）。

**改动模块（= 回归范围）**：`M-13`

**前置模块**：无　　**需授权**：否

## 文件

| 文件 | 内容 |
|---|---|
| `D-246-PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `D-247-SCOPE_GUARD.md` | 本任务的修改范围守护与「没有降级」的具体含义 |
| `D-248-checklist.md` | 本任务的逐条验收框（**含诚实边界**） |
| `docs/` | 本任务的**独立技术文案**区 |

## 一句话

> **写锁不是被「一次大批量重写」霸占的，是被「12 条常驻 worker 的连续小写入 + 管理器的整块重写」常年占住的。**
> 本任务把运行时的写事务**瘦身**：测量**移出**写事务、一轮的状态写入**合并**成一次、忙等**分级**，
> 让「锁满 100%」变成「锁有空窗」，从而让复核刷新不再整片 `503 database is locked`。

## 当前读数（2026-11-28 · 生产在产）

| 读数 | 值 |
|---|---|
| 主库 | **18.83 GiB**（WAL 8.35 MB） |
| 写锁（500 ms 预算，12 次） | **12 / 12 全锁** |
| 写锁（90 s 窗口，90 次） | **89 / 90 锁**、**0** 次检查点 |
| 复核刷新 | **503** `database is locked`（`retryable: true`） |
| 运行时状态同步 | `closed-loop runtime state sync failed: database is locked` |

## 独立回归

````powershell
python -m pytest flashloan/app_runtime/tests/unit/test_storage_business_io_write_lock.py -q
python -m pytest flashloan/app_runtime/tests/unit/test_closed_loop_state_machine.py -q
python plan/runtime_evidence/tools/probe_write_lock_20261128.py --samples 40 --interval 2
````

> **只跑上面这些。** 未改动模块**不重跑**。

