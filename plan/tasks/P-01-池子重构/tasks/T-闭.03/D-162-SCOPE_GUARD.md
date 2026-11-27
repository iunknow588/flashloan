# `T-闭.03`｜基础池内化 · 修改范围守护（Scope Guard）

> **本文件是 `T-闭.03` 的范围契约。** 违反本契约 ⇒ **本任务不算闭合**。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 改动内容 | 是否改 |
|---|---|---|---|---|
| `M-3` | 方向层契约 | `flashloan/tools/manager_contracts/src/directional_pool.rs` | `base` 私有 ＋ 标量访问器 | **是** |
| `M-4` | 闭环层 | `flashloan/tools/closed_loop_manager/src/lib.rs` | `.base` → 访问器；容量绑定改方向池 id；删死字段 | **是** |
| `M-6` | 复核发布层 | `flashloan/tools/review_manager/src/lib.rs` | 复核与闭环的方向口径对齐；夹具补方向层行 | **是** |
| `M-6` | 复核层示例 | `flashloan/tools/review_manager/examples/t401_profit_drop_probe.rs` | 探测工具改读方向列 ＋ 边界解析基础池 | **是** |
| `M-10` | 存储层 | `flashloan/tools/manager_db/src/{lib,migrate}.rs` | 018 迁移登记；台账 pending 集合修正 | **是** |
| `M-10` | 存储层（迁移） | `flashloan/tools/manager_db/migrations/20261125_018_closed_loop_directional_only.sql` | 新增迁移文件 | **是** |
| `M-10` | 存储层（审计） | `flashloan/tools/pool_manager/src/baseline_audit.rs` | 跳引用检查改方向池 | **是** |
| `M-2` | **扩范围** 内核对象层 | `flashloan/rs-src/src/{sqlite_read,closed_loop_identity,worker_pool,review_publish}.rs` | 方向列读；边界解析；**读失败不降级** | **是** |
| `M-2` | **扩范围** 组合层 | `flashloan/tools/composite_manager/src/overview.rs` | 概览改方向列 | **是**（见 §1.2） |
| `M-2` | **扩范围** API 层 | `flashloan/tools/manager_api/src/lib.rs` | **仅测试夹具** | **是**（见 §1.2） |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

### 1.1 为什么必须扩到 `M-2`（内核对象层）

本任务的口径是「**闭环引用的是有向池**」与「**要么成功，要么失败，没有降级处理**」。
这两条在内核里有一个**真实的、可复现的违反**：

```rust
// flashloan/rs-src/src/worker_pool.rs（改前）
let closed_loop_rows =
    sqlite_read::load_manager_closed_loop_rows(&connection, network, chain_id)
        .unwrap_or_default();          // ← 读失败 = 空闭环目录
let directional_pool_rows = sqlite_read::load_v4_directional_pool_registry_rows(...)
    .unwrap_or_default();             // ← 读失败 = 空池数组
```

空目录与空池数组**和「市场里没有机会」长得一模一样**。
叠加 018 迁移的破坏性（新二进制 ＋ 旧库 ⇒ `no such column`），
最坏结果不是「报错」，而是**系统看起来正常却永远不出单**。

⇒ 本任务把内核的这两处读取改为**全有或全无**：
读失败 ⇒ 快照构建失败 ⇒ worker **报错**；
并把 `run_native_review` 的「闭环目录」与「池数组」由 `Option` 改为**非可选**，
让「无目录复核」这种降级在**编译期**就不存在。

### 1.2 扩范围登记（**逐条说明，附原因**）

| # | 落点 | 性质 | 原因 |
|---|---|---|---|
| 1 | `flashloan/tools/composite_manager/src/overview.rs` | **生产代码** | 概览读 `manager_v4_closed_loops` 的 `pool_ids_json`；018 删了该列 ⇒ **不编译**。改动限于「读方向列 ＋ 基础池按方向层解析」。**本轮因编译必须破禁改**，已在此登记。 |
| 2 | `flashloan/tools/manager_api/src/lib.rs` | **仅测试夹具** | 夹具按 018 之前的列名插入闭环表 ⇒ 夹具本身报错（`no such column: pool_ids_json`），4 个测试连坐失败。生产读路径只读 `manager_v4_reviews` / `manager_v4_review_broadcast_requests`（**018 未触碰**），**未改生产契约**。夹具同时补了方向层行，因为复核层是通过方向层解析基础池的。 |
| 3 | `flashloan/tools/review_manager/examples/t401_profit_drop_probe.rs` | **诊断示例** | 探测工具读闭环表的 `pool_id` / `pool_ids_json`；018 之后这两个列不存在 ⇒ 工具失效。改为读方向列 ＋ 在边界处解析基础池（池状态仍按基础池发布）。 |
| 4 | `flashloan/rs-src/src/worker_pool.rs` | **生产代码** | 见 §1.1。**「内核对象一次性存储」由另一线程负责**，本轮只改「读失败不降级」与「目录/池数组非可选」两处，**不碰**内核的池对象存储结构。 |

> **禁改名单本轮的实际破例**：仅 `composite_manager`（生产代码，编译必须）与
> `manager_api`（测试夹具）两处，均为**最小改动**且已逐条登记。

---

## 2. 逐模块回归命令

| 模块 | 名称 | 命令 | 预期 |
|---|---|---|---|
| `M-3` `M-4` `M-10` | 契约 / 闭环 / 存储 | `cd flashloan/tools && cargo test --offline -p manager-contracts -p closed-loop-manager -p manager-db` | 全绿 |
| `M-6` | 复核发布层 | `cd flashloan/tools && cargo test --offline -p review-manager` | 全绿 |
| `M-10` | 存储层（审计） | `cd flashloan/tools && cargo test --offline -p pool-manager` | 全绿 |
| `M-2` | 内核对象层 | `cd flashloan/rs-src && cargo test --offline` | 全绿（基线 **220** passed） |
| `M-2` | 组合层 / API 层（扩范围） | `cd flashloan/tools && cargo test --offline -p composite-manager -p manager-api` | 全绿 |
| 全局 | 架构守卫 | `python -m pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py -q` | 全绿 |

```powershell
cd flashloan/tools
cargo test --offline -p manager-contracts -p closed-loop-manager -p manager-db -p review-manager -p pool-manager -p composite-manager -p manager-api

cd ..\rs-src
cargo test --offline

cd ..\..
python -m pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py -q
```

> **只跑上面这些。** 未改动模块**不重跑**。

---

## 3. 触碰边界（**禁止触碰**）

| 对象 | 禁止理由 |
|---|---|
| `flashloan/tools/broadcast_manager/**` | 属 `M-7`；它的 `pool_ids_json` 属于**复核/广播记录表**（`manager_v4_reviews` / `manager_cow_execution_attempts` / `manager_v4_review_broadcast_requests`），**018 未触碰** ⇒ 本任务不改 |
| `flashloan/tools/directional_pool_manager/**` | 属 `M-3`；方向层的投影规则、准入判定、容量口径一字不改 |
| `flashloan/src/core/db/builders.py` | 并行施工保护 |
| `flashloan/rs-src/src/evidence.rs` | 并行施工保护 |
| `flashloan/tools/manager_api/src/lib.rs` 的**生产**路径 | 只允许改**测试夹具**（§1.2 第 2 条） |
| 任何 `chain.json` / `.env` / `.env.example` | 本任务**不引入**任何运行期参数 |
| 实盘**写**操作（迁移 018） | 本轮**只做只读核对**；落地是独立部署动作（`D-161` §2.5） |

---

## 4. 范围变更流程（**先扩后跑**）

1. **先扩范围** —— 在本文件 §1 增列该模块，同步改 `D-161-PLAN.md` §2.1；
2. **记录原因** —— 在 §1.2 追加一行说明为什么必须扩；
3. **后施工**；
4. **后重跑** —— **只**跑扩范围后模块的测试。

---

## 5. 授权边界

| 动作 | 需要授权？ | 说明 |
|---|---|---|
| 改 `M-3` / `M-4` / `M-6` / `M-10` 代码 | 否 | 纯代码 |
| 改 `M-2` 内核（读失败不降级） | 否 | 纯代码，不改存储结构 |
| 破禁改 `composite_manager` / `manager_api` | **已授权**（用户：默认授权通过） | 已在 §1.2 逐条登记 |
| 实盘**只读**复核 | 否 | `mode=ro` ＋ `PRAGMA query_only = ON` |
| 实盘应用迁移 018 ＋ 换二进制 | **需单独授权** | 破坏性且与新二进制强耦合（`D-161` §2.5） |

---

## 6. 范围守护自检

* [ ] 本次改动**全部**落在 §1 的模块集合内（或已按 §4 扩范围并重跑）
* [ ] §2 的**全部**命令**已执行**且**全绿**
* [ ] §3 的禁止触碰项**确未被触碰**
* [ ] `flashloan/tools/**` 中除 `manager_contracts/src/directional_pool.rs` 外**零** `.base` 访问
* [ ] `manager_contracts` 中**没有**返回 `&V4Pool` / `V4Pool` 的出口
* [ ] 内核的闭环目录与池数组**读失败即失败**（无 `unwrap_or_default` 降级）
* [ ] **未**因本任务跑过**全量**测试
