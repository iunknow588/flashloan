# `T-核.09`｜常驻 worker 按当前路由重新注册

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../../../README.md)。

**编号**：`T-核.09`（纯 ID）　**验收**：`AC-核.09`　**优先级**：P0
**编号逻辑**：`T-核.09` / `AC-核.09` —— **模块内索引**，编号索引 `核` ＝ `M-13`（运行时内核与运行时接线），该索引号段内第 `09` 个（`T-核.01`~`T-核.08` 已占用，**不复用**）。

**改动模块（= 回归范围）**：`M-13`

**前置模块**：无　　**需授权**：否

## 文件

| 文件 | 内容 |
|---|---|
| `D-249-PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `D-250-SCOPE_GUARD.md` | 本任务的修改范围守护与「没有降级」的具体含义 |
| `D-251-checklist.md` | 本任务的逐条验收框（**含诚实边界**） |
| `docs/` | 本任务的**独立技术文案**区 |

## 一句话

> **管理器目录里有 48 条 active 闭环，运行时只推进了 28 条 —— 另外 20 条从来没有常驻 worker。**
> 其中 **12 条含 USDG**。本任务把注册集合从「扫描轮碰到的」改成「**管理器目录全量**」，
> 让新闭环**即时**拿到 worker（不等一轮 **24 分钟**的扫描），并把离开目录的残留**退役**掉。

## 当前读数（2026-11-28 14:0x · 生产在产）

| 读数 | 值 |
|---|---|
| 管理器 active 闭环 | **48**（`closed_loop_version = 73`） |
| 运行时自己的闭环表 | **32** 行（落后 **16**） |
| 运行时状态表 | **32** 行（`offchain_estimate` 20 ＋ `execution_not_requested` 12） |
| 覆盖 / 缺口 | **28 / 20**（缺口含 USDG **12** 条） |
| 手工复核对无 worker 的组 | **404** `closed_loop_group_not_found` |

## 独立回归

````powershell
python -m pytest flashloan/app_runtime/tests/unit/test_closed_loop_state_machine.py -q
python -m pytest flashloan/app_runtime/tests/functional/test_dex_closed_loop_manual_transition_routes.py -q
````

> **只跑上面这些。** 未改动模块**不重跑**。
