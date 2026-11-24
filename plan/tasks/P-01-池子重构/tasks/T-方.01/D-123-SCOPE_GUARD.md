# D-123 `T-方.01`｜修改范围守护（Scope Guard）

> **本文件是本任务的「不可越界」凭据。**
> 范围规则见 [`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §2–§4。

---

## 1. 允许改动的文件（**白名单**）

| 文件 | 改动内容 |
|---|---|
| `flashloan/tools/directional_pool_manager/src/lib.rs` | `DirectionalPoolComparison` **新增** 4 个字段 ＋ `compare_directional_pools` **新增**基础池比对 ＋ 新增 `base_fact_differences` 辅助函数 ＋ 新增常量 `BASE_POOL_MISMATCH_SAMPLE_LIMIT` |
| `flashloan/tools/directional_pool_manager/tests/directional_pools.rs` | 新增 **3** 个回归用例（新鲜投影 / 陈旧投影 / 孤儿投影） ＋ 导入 `compare_directional_pools` |
| `plan/runtime_evidence/tools/audit_directional_projection_20261125.py` | **新增**只读审计工具（离线取证，**不在运行时包内**） |
| `plan/tasks/P-01-池子重构/tasks/T-方.01/**` | 本任务的工作单元 |
| `plan/runtime_evidence/reports/directional_projection_audit_20261125.{json,md}` | 审计产物 |
| `plan/D-001-TASK_REGISTRY.md` / `plan/tasks/P-01-池子重构/docs/D-014-…` / `plan/tasks/P-01-池子重构/README.md` / `plan/runtime_evidence/README.md` | **台账同步**（编号册是唯一真相源 ⇒ 必改） |

## 2. 禁止触碰的文件（**黑名单**）

| 路径 | 理由 |
|---|---|
| `flashloan/tools/broadcast_manager/**` | `M-7`；**并行进程 `T-复广.02` 在途改动** ⇒ 碰它就是冲突 |
| `flashloan/tools/review_manager/**` | `M-6`；同上 |
| `flashloan/tools/composite_manager/**` | `M-8`；同上（编排层在途） |
| `flashloan/tools/manager_api/**` | `M-8`；同上（**在途**）—— 本任务**只**依赖它的序列化，不改它 |
| `flashloan/tools/closed_loop_manager/**` | `M-4`；`T-闭.01` 刚落地的代码，本任务不碰 |
| `flashloan/src/**`、`flashloan/app_runtime/**` | Python 运行时代码；**架构守卫要求 Python 不直查方向池表** ⇒ 本任务的审计工具**必须**待在 `plan/runtime_evidence/tools/`（守卫扫描范围之外） |
| `flashloan/tools/manager_db/migrations/**`、`schema_policy.json` | `M-10`；本任务**零 DDL** |
| `flashloan/chains/**/chain.json`、`.env`、`.env.example` | 链配置与运维配置；本任务**零配置改动** |

## 3. 越界了怎么办（**流程**）

1. **先停手** —— 不把越界的改动留在工作区里；
2. **先扩后跑**：确需触碰 ⇒ 先改 `D-123` §1 白名单 **＋** `D-122` §1.1 改动模块表 **＋** 任务编号（组合索引可能变）；
3. **再跑**：按扩后的模块集合**重跑回归**，并在 `D-001` §7 **迁移记录**里写明扩范围的原因与读数；
4. **不得**以「顺手改一下」跳过本流程。

## 4. 本轮的实际改动面（**对账**）

| 项 | 值 |
|---|---|
| 改动的**产品代码**文件 | **2**（`directional_pool_manager/src/lib.rs`、`directional_pool_manager/tests/directional_pools.rs`） |
| 改动的**运行时模块** | **仅 `M-3`** ⇒ **未扩范围** |
| 改动的**Python 运行时代码** | **0**（新增工具在 `plan/runtime_evidence/tools/`，守卫扫描范围之外） |
| 改动的**主库 schema / 迁移** | **0** |
| 改动的**链配置 / 环境变量** | **0** |
| 实盘库**写入** | **0**（全部读数 `mode=ro` ＋ `PRAGMA query_only = ON`） |

## 5. 「只加严」的具体落点（**未放宽任何门禁**）

| 位置 | 改动 | 方向 |
|---|---|---|
| `DirectionalPoolComparison::ready_for_cutover` | 新增 `base_pool_mismatches == 0` ＋ `rows_without_base_pool == 0` 两个必要条件 | **只加严**（`false` 仍是 `false`，`true` 可能变 `false`） |
| 投影规则（`is_dynamic_fee_pool` / `hooks_is_absent` / `token_order_is_canonical`） | **一字未改** | 不变 |
| 容量 / 利润 / 阈值 / 幂等键 / 回执关联 | **一字未改** | 不变 |

> `ready_for_cutover` 是**诊断位**，不是**门禁**：实盘读数（改动前后）均为 `false`
> （`T-闭.01` 的 `D-116` §3.4 已记「改动前已是 `false`」）⇒ 本次加严**不会**翻转实盘读数。
> 它**不被**任何广播/复核判定引用（唯一消费点是 `manager-api` 的比对端点与页面展示）。
