# `T-复编.02`｜Review 金额与利润完整性

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../README.md)。

**编号**：`T-复编.02`（纯 ID）　**验收**：`AC-复编.02`　**优先级**：P0　**波次**：W-2
**编号逻辑**：`T-复编.02` / `AC-复编.02` —— **跨模块（组合索引）**，编号索引 **`复编`**（改动模块：`M-6`（复核层（复核发布 / 金额利润））＋ `M-8`（编排层），按模块号升序拼接），该索引号段内第 `02` 个（`.01` 已归档）。（详见 `PLAN.md` §1.0）

**改动模块（= 回归范围）**：`M-6`（含**扩范围**落点 `flashloan/rs-src/src/review_publish.rs`）＋ `M-8`（含**扩范围**落点 `flashloan/src/dex/runtime/rust_closed_loop.py` 等 ＋ **第三次扩范围**落点 `flashloan/tools/composite_manager/src/overview.rs`）

**前置模块**：M-6　　**需授权**：是（写库）

## 文件

| 文件 | 内容 |
|---|---|
| `PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `SCOPE_GUARD.md` | 本任务的修改范围守护与逐模块回归命令 |
| `checklist.md` | 本任务的逐条验收框（**29** 项未完成） |
| `docs/` | 本任务的**独立技术文案**区（设计推导 / 口径定义 / 读数记录） |

## 独立回归

```powershell
cargo test --offline -p review-manager
cargo test --offline --release            # 工作目录 flashloan/rs-src/（M-6 扩范围）
cargo test --offline -p composite-manager   # 工作目录 flashloan/tools/（M-8 第三次扩范围）
python -m pytest flashloan/app_runtime/tests/unit/test_rust_closed_loop_worker_bridge.py flashloan/app_runtime/tests/unit/test_dynamic_closed_loop_workers.py flashloan/app_runtime/tests/unit/test_chain_scoped_parameter_layering.py flashloan/app_runtime/tests/unit/test_secret_leakage_guards.py flashloan/app_runtime/tests/unit/test_dex_route_test.py flashloan/app_runtime/tests/unit/test_d4_capacity_informed_ladder.py flashloan/app_runtime/tests/unit/test_manager_overview_closed_loop_identity.py -q
```

> **只跑上面这些。** 未改动模块**不重跑**。
