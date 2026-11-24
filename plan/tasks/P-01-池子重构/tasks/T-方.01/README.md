# `T-方.01`｜方向层投影不变式守卫（I-9 / I-7 / I-12）

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../README.md)。

**编号**：`T-方.01`（纯 ID）　**验收**：`AC-方.01`　**优先级**：P1　**波次**：W-0
**编号逻辑**：`T-方.01` / `AC-方.01` —— **模块内编号**，编号索引 **`方`**（改动模块：`M-3`（方向层（方向池物化 / 评分 / 准入））），该索引号段内第 `01` 个。`方` 号段此前为空（历史上 `M-3` 只出现在组合索引里）。（详见 `PLAN.md` §1.0）

**改动模块（= 回归范围）**：`M-3`

**前置模块**：—　　**需授权**：否（只改 Rust 代码 ＋ 文档 ＋ 只读审计工具；**实盘库零写入**）

**状态**：**部分闭合** —— **16** 项中 **15** 项闭合、**1** 项待运行期重建 ＋ 重启（**不归档**）

## 一句话

方向层是基础池的**投影**，但**没有任何代码在检查「方向行仍然是基础池行的忠实副本」** ——
旧比较只看**同一基础池的两个方向行彼此**，两个方向行**可以一起过期**而比较依然绿。
本任务补上 **I-9** 的检测器（并把 I-7 / I-12 做成可复算的只读审计）。

## 文件

| 文件 | 内容 |
|---|---|
| `PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `SCOPE_GUARD.md` | 本任务的修改范围守护与逐模块回归命令 |
| `checklist.md` | 本任务的逐条验收框（**16** 项：**15** 项已闭合、**1** 项待运行期重启） |
| `docs/` | 本任务的**独立技术文案**区（设计推导 / 口径定义 / 读数记录） |

## 独立回归

```powershell
cargo test --offline -p directional-pool-manager
cargo test --offline -p manager-api
```

> **只跑上面这些。** 未改动模块**不重跑**。
> `manager-api` 是 `DirectionalPoolComparison` 的**唯一**序列化调用方 ⇒
> 本任务新增了字段 ⇒ 按「跨 crate 公开接口改动」加跑。

## 只读审计（实盘库零写入）

```powershell
python plan/runtime_evidence/tools/audit_directional_projection_20261125.py `
  --database flashloan/db/local_store_4663.sqlite3 `
  --json-out plan/runtime_evidence/reports/directional_projection_audit_20261125.json `
  --md-out  plan/runtime_evidence/reports/directional_projection_audit_20261125.md
```

> 退出码 **0** ＝ 零失败项；**1** ＝ 有失败项（报告里逐条列名）。
> 工具**故意**放在 `plan/runtime_evidence/tools/` —— 架构守卫要求
> **Python 运行时代码不得直查方向池表**（`M-6` 守卫），本工具是**离线取证**工具。

## 诚实边界

本轮**刻意不重启**实盘运行时：并行进程 `T-复广.02`（pid **29140**）**仍在途**改动
`broadcast_manager` / `review_manager` / `manager_api`，而 `rs-src` 依赖这两个 crate ⇒
此刻重建会把**未经回归验证**的代码带上线。
⇒ 「运行期生效复测」**如实登记为剩 1 项**，**不代任何其它任务关闭验收项**。
