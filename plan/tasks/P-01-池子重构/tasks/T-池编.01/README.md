# `T-池编.01`｜核心库加载模式（测试期收敛闭环规模）

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../../../README.md)。

**编号**：`T-池编.01`（纯 ID）　**验收**：`AC-池编.01`　**优先级**：P0
**编号逻辑**：`T-池编.01` / `AC-池编.01` —— **跨模块（组合索引）**，编号索引 **`池编`**
＝ `M-1`（池管理器）＋ `M-8`（编排层），**按模块号升序拼接**；该索引号段内第 `01` 个。
（详见 `D-211-PLAN.md` §1.0）

**改动模块（= 回归范围）**：`M-1`、`M-8`

**前置模块**：`M-1`　　**需授权**：否（`dryRun=false` 才写）

## 文件

| 文件 | 内容 |
|---|---|
| `D-211-PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `D-212-SCOPE_GUARD.md` | 本任务的修改范围守护与「没有降级」的具体含义 |
| `D-213-checklist.md` | 本任务的逐条验收框（**含诚实边界**） |
| `docs/` | 本任务的**独立技术文案**区 |

## 一句话

> **加载模式只有一个来源（`TokenLoadMode::default_for_scope()`），审计跟随它，编排器把审计判定的口径传给它。**
> 核心库模式下闭环 **42 → 10**、跳 **126 → 30**，越界池 **0**、失效引用 **0**。

## 独立回归

```powershell
# M-1 池管理器
cargo test --offline -p pool-manager

# M-8 编排层
python -m pytest scripts/tests/test_mainnet_arbitrage_healer.py -q
python -m pytest flashloan/app_runtime/tests/unit/test_mainnet_arbitrage_healer_repair_chain.py -q
```

> **只跑上面这些。** 未改动模块**不重跑**。

