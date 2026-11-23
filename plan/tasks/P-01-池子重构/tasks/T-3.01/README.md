# T-3.01｜方向层落地

> 任务编号 `T-<模块>.<序>` / `AC-<模块>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../README.md)。

**编号**：`T-3.01`（纯 ID）　**验收**：`AC-3.01`　**优先级**：P0　**波次**：W-0
**编号逻辑**：`T-3.01` / `AC-3.01` —— **跨模块（主模块定号段）**，按 **`M-3`**（方向层）号段编号，模块内第 `01` 个；同号段其它任务：`T-3.02`。（详见 `PLAN.md` §1.0）

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
