# `T-池编.02`｜作用域加载模式不得收窄（根因修复与生产恢复）

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../../../README.md)。

**编号**：`T-池编.02`（纯 ID）　**验收**：`AC-池编.02`　**优先级**：P0
**编号逻辑**：`T-池编.02` / `AC-池编.02` —— **跨模块（组合索引）**，编号索引 `**池编**`
＝ `M-1`（池管理器）＋ `M-8`（编排层），**按模块号升序拼接**；该索引号段内第 `02` 个。
（详见 `D-239-PLAN.md` §1.0）

**改动模块（= 回归范围）**：`M-1`、`M-8`

**前置模块**：无　　**需授权**：否（写入走 `dryRun=false` 的既有接口）

## 文件

| 文件 | 内容 |
|---|---|
| `D-239-PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `D-240-SCOPE_GUARD.md` | 本任务的修改范围守护与「没有降级」的具体含义 |
| `D-241-checklist.md` | 本任务的逐条验收框（**含诚实边界**） |
| `docs/` | 本任务的**独立技术文案**区 |

## 一句话

> **加载模式可以变窄「加载到内存的池子」，但绝不能让作用域「丢掉池子」。**
> 上一轮的缺陷正是把这两件事混成了一件：healer 硬编码 `core` 写进生产作用域，
> 重建时**删掉了股票池**（`deletedRows=14`）—— 池**从未离开主池表**，
> 缺的是**作用域的物化范围**，那 19 条股票路由因此变成不可定价。

## 独立回归

````powershell
# M-1 池管理器
cargo test --offline -p pool-manager

# M-8 编排层
python -m pytest scripts/tests/test_mainnet_arbitrage_healer.py -q
python -m pytest flashloan/app_runtime/tests/unit/test_mainnet_arbitrage_healer_repair_chain.py -q

# 生产库只读复核（不改任何数据）
python plan/runtime_evidence/tools/probe_scope_narrowing_20261128.py
````

> **只跑上面这些。** 未改动模块**不重跑**。
