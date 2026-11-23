# `T-方授储.01`｜方向层落地

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../README.md)。

**编号**：`T-方授储.01`（纯 ID）　**验收**：`AC-方授储.01`　**优先级**：P0　**波次**：W-0
**编号逻辑**：`T-方授储.01` / `AC-方授储.01` —— **跨模块（组合索引）**，编号索引 **`方授储`**（改动模块：`M-3`（方向层（方向池物化 / 评分 / 准入）） ＋ `M-9`（授权与回滚（授权维度 / 守卫 / 回滚脚本）） ＋ `M-10`（存储层（schema / 迁移 / 记录剥离））），该索引号段内第 `01` 个。（详见 `PLAN.md` §1.0）

**改动模块（= 回归范围）**：`M-3` 、`M-9` 、`M-10`

**前置模块**：—　　**需授权**：否（DDL）

## 文件

| 文件 | 内容 |
|---|---|
| `PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `SCOPE_GUARD.md` | 本任务的修改范围守护与逐模块回归命令 |
| `checklist.md` | 本任务的逐条验收框（**32** 项未完成） |
| `docs/` | 本任务的**独立技术文案**区（设计推导 / 口径定义 / 读数记录） |

## 独立回归

```powershell
cargo test --offline -p directional-pool-manager
cargo test --offline -p manager-contracts
pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py
cargo test --offline -p manager-db
```

> **只跑上面这些。** 未改动模块**不重跑**。
