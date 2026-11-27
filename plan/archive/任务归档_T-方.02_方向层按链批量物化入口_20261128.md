# `T-方.02`｜方向层按链批量物化入口 · 任务归档（**已闭合**）

> **归档元信息**
>
> | 项 | 值 |
> |---|---|
> | 任务编号（纯 ID） | `T-方.02` |
> | 验收编号（纯 ID） | `—` |
> | 标题 | 方向层按链批量物化入口 |
> | 层级 | 二级（执行级） |
> | 归档日期 | 20261128 |
> | 原路径 | `plan/tasks/P-01-池子重构/tasks/T-方.02` |
> | 归档索引 | `plan/archive/README.md` §1 |
>
> **本件已冻结** —— 内容不再改动。更正走新版本 `..._v2.md`，旧版保留。

---

## ① 任务说明 ＋ ② 开发计划

<a id="d144"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方.02/D-144-PLAN.md` -->

# D-144 `T-方.02`｜方向层按链批量物化入口 · 开发计划（**独立 · 自包含**）

> **本目录是 `T-方.02` 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**

---

## 1.0 编号逻辑

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-方.02`** |
| **本任务验收编号** | **`AC-方.02`** |
| **编号作用域** | 模块内（单模块索引） |
| **编号索引** | **`方`**（`M-3`） |
| **序号** | 第 `02` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | `T-方.01`（已归档） |

**本任务涉及的模块**：

| 模块 | 名称 | 索引 |
|---|---|---|
| `M-3` | 方向层（方向池物化 / 评分 / 准入） | `方` |

**登记**：[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §4.2。

---

## 1. 任务说明

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-方.02`** |
| **验收编号（纯 ID）** | **`AC-方.02`** |
| **标题** | 方向层按链批量物化入口 |
| **优先级** | **P0** |
| **波次（模块级）** | `W-1` |
| **前置模块** | `M-3` |
| **需授权** | 是（写生产库方向层） |
| **未完成验收项** | **0** |
| **已闭合验收项** | **13**（2026-11-26 全部闭合，每条附实测读数） |
| **本轮实盘动作** | 方向层**已刷新**（apply 落地，1.63 秒）；刷新前留 **692 行**行级备份 |
| **一句话目标** | 给方向层补一个「按链枚举启用 scope 并逐个物化」的公共入口，并让实盘方向层的快照列回到最新 |

### 1.1 背景与问题

```text
方向层 = 闭环的唯一池权威（D-008 聚合根模型）。
它的快照列来自 v4_exchange_pool_liquidity_snapshots（流动性 worker 刷到 2026-11-25 00:44），
但方向行本身最后一次物化是 2026-11-23 22:46 ⇒ 漂移 83 行 / 994 格（inv-9 FAIL）。

为什么没有刷新：
  · 方向行的唯一生产写入方是 directional_pool_manager（lib.rs:1785 replace_directional_rows）；
  · 它的入口有三条，都不在运行时维护链里：
      ① manager_api 的 POST /v1/directional-pools/rebuild（需 DIRECTIONAL_POOL_MANAGER_AUTHORITY=manager）
      ② directional_pool_manager 的 example 二进制（手工）
      ③ composite-manager materialize-arbitrage —— 它【读】方向层、【不】物化方向层
  · 运行时维护链的 7 个 worker 槽里没有方向池槽（D-143 §2.3）。
```

**为什么这个入口要放在 `M-3` 而不是 Python**：
枚举「本链启用了哪些 scope」是**数据逻辑**，必须留在 Rust 管理器里；
Python 侧只调一次「按链物化」并读回读数 —— 符合
「python 侧没有自己的数据处理逻辑，内存对象直接调用相关的管理工具（rust 实现）」。

### 1.2 不做什么（**范围外**）

* **不**改方向层的**投影规则、准入判定、容量/利润口径、幂等键、回执关联** —— 一字不改。
* **不**改 `manager_api` 的端点契约。
* **不**在本任务里改运行时维护链（那是 `M-13` 的任务）。
* **不**做跨月的历史统计、降级优雅之类的低概率路径（按用户口径：功能点先留占位）。

---

## 2. 开发计划

### 2.1 改动模块（**这一列就是本任务的回归范围**）

| 模块 | 名称 | 代码落点 | 改动内容 |
|---|---|---|---|
| `M-3` | 方向层 | `flashloan/tools/directional_pool_manager/src/lib.rs` | 新增「按链枚举启用 scope 并逐个物化」的公共入口 + 逐 scope 结果 |

### 2.2 执行步骤

| 步 | 动作 | 完成判据 | 状态 |
|---:|---|---|---|
| **1** | 读 `D-146`，确认每条验收项的当前状态 | 全部条目已知 | ✅ **完成** |
| **2** | 在 `M-3` 内新增 `materialize_directional_pools_for_chain(...)` | 单 scope 行为与现有 `materialize_directional_pools` **逐字一致** | ✅ **完成**（`lib.rs:505-556`） |
| **3** | 枚举逻辑读 `pool_scope_config`（`enabled = 1`），**按 scope_key 排序**枚举 | 顺序确定 ⇒ 可复算 | ✅ **完成**（`lib.rs:564-567`；实盘 `scopeKeys = [candidate_exchange, core4]`） |
| **4** | 跑 `cargo test --offline -p directional-pool-manager` | 全绿 | ✅ **完成**（**27 passed / 0 failed**） |
| **5** | 实盘只读复核：入口的 dry-run 读数与 `inv-9` 读数对得上 | 读数可复算 | ✅ **完成**（干跑与副本演练**逐字段相等**） |
| **6** | 实盘 apply（**需授权**），随后复核 `inv-9` | `inv-9` 由 FAIL 转 PASS | ✅ **完成**（1.63 秒；`inv-9` 0 行/0 格；审计 9/9 PASS） |
| **7** | 逐条勾选 `D-146` | 每条都有实测读数 | ✅ **完成**（**13 / 13**） |

### 2.3 技术文案（`docs/`）

* `D-147` —— 入口契约、枚举口径、实盘刷新读数。

### 2.4 三条执行纪律

1. **编译 ≠ 测试**：新增公共函数 ⇒ `cargo build --offline -p directional-pool-manager` 先过。
2. **跨 crate 的公开接口改动**才需要加跑**调用方**的测试；本任务不改任何现有签名。
3. **Python 侧同理**：本任务**不改任何 Python 文件**。

---

## 3. 完成定义

* [x] `D-146` 中**全部** `- [ ]` 变为 `- [x]`（**13 / 13**）
* [x] `D-145` 的**全部**回归命令**全绿**（`directional-pool-manager` **27 passed / 0 failed**）
* [x] 每条已勾选项都有**可复算的实测读数**
* [x] **未放宽**任何既有门禁（投影规则 / 容量 / 利润 / 阈值 / 幂等键 / 回执关联）
      ⇒ 读数：`lib.rs` 的 diff 是 **1 个 hunk / 124 行新增 / 0 行删除**，既有函数**一字未改**
* [x] **未**触碰**不属于** `M-3` 的模块
      ⇒ 读数：改动只落在 `directional_pool_manager`（`lib.rs` / `tests/` / `examples/`）；
      实盘复核佐证：闭环层 `closed_loop_version` 21 → 21、校验和与 `updated_at` 不变

---

## 4. 归档

```powershell
python plan/runtime_evidence/tools/archive_task_20261124.py T-方.02 --dry-run
python plan/runtime_evidence/tools/archive_task_20261124.py T-方.02
```

---

## ③ 修改范围守护

<a id="d145"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方.02/D-145-SCOPE_GUARD.md` -->

# D-145 `T-方.02`｜方向层按链批量物化入口 · 修改范围守护（Scope Guard）

> **本文件是 `T-方.02` 的范围契约。** 违反本契约 ⇒ **本任务不算闭合**。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 改动内容 | 是否改 |
|---|---|---|---|---|
| `M-3` | 方向层 | `flashloan/tools/directional_pool_manager/src/lib.rs` | 新增按链批量物化入口 | **是** |
| `M-3` | 方向层 | `flashloan/tools/directional_pool_manager/examples/materialize_directional_pools_for_chain.rs` | 按链入口的最小驱动（实盘只读干跑的读数入口） | **是** |
| `M-3` | 方向层 | `flashloan/tools/directional_pool_manager/tests/directional_pools.rs` | 按链入口的单测 | **是** |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

> **扩范围记录（2026-11-25）**：按链入口需要**实盘读数**才能进验收清单；
> 管理器自己的 crate 里已有同形态的 example（`materialize_directional_pools`），
> 按链入口补一个同形态的 example 是最小、最一致的做法 —— 它**只调入口**，不重算任何口径。

---

## 2. 逐模块回归命令

| 模块 | 名称 | 命令 | 预期 |
|---|---|---|---|
| `M-3` | 方向层 | `cargo test --offline -p directional-pool-manager` | 全绿（基线 **25** passed） |

```powershell
cd flashloan/tools
cargo test --offline -p directional-pool-manager
```

> **只跑上面这些。** 未改动模块**不重跑**。

---

## 3. 触碰边界（**禁止触碰**）

| 对象 | 禁止理由 |
|---|---|
| `flashloan/tools/closed_loop_manager/**` | 属 `M-4`；本任务只提供入口，不改闭环口径 |
| `flashloan/tools/manager_api/**` | 属 `M-12`；端点契约不变 |
| `flashloan/rs-src/**` | 属 `M-13`；运行时接线是别的任务 |
| `flashloan/src/core/db/builders.py` | 并行施工保护 |
| 方向层的投影规则 / 准入判定 / 容量口径 | 一字不改（本任务只加入口） |

---

## 4. 范围变更流程（**先扩后跑**）

1. **先扩范围** —— 在本文件 §1 增列该模块，同步改 `D-144` §2.1；
2. **记录原因** —— 在 `D-144` §2.2 追加一行说明为什么必须扩；
3. **后施工**；
4. **后重跑** —— **只**跑扩范围后模块的测试。

---

## 5. 授权边界

| 动作 | 需要授权？ | 说明 |
|---|---|---|
| 改 `M-3` 代码 | 否 | 纯代码 |
| 实盘 **apply**（写 `manager_v4_directional_exchange_pools`） | **是** | 写生产库方向层；用户口径「所有需授权操作默认授权通过」 |
| 实盘只读复核 | 否 | `mode=ro` ＋ `PRAGMA query_only = ON` |

---

## 6. 范围守护自检

* [x] 本次改动**全部**落在 §1 的模块集合内（或已按 §4 扩范围并重跑）
      ⇒ 读数：改动文件 = `directional_pool_manager/src/lib.rs`、`tests/directional_pools.rs`、
      `examples/materialize_directional_pools_for_chain.rs`，**三者都在 §1 表内**；
      **未**扩范围（`manager_api` / `closed_loop_manager` / `rs-src` **一行未改**）
* [x] §2 的**全部**命令**已执行**且**全绿**
      ⇒ 读数：`cargo test --offline -p directional-pool-manager` ⇒ **27 passed / 0 failed**
      （基线 25 ＋ 本任务新增 2）
* [x] §3 的禁止触碰项**确未被触碰**
      ⇒ 读数：**本任务**的改动 = `directional_pool_manager` 的三个文件（`lib.rs` / `tests/` / `examples/`）；
      `git status` 里 `flashloan/tools/**` 其余文件的改动属于**其它任务**
      （`T-闭.03` 的 `M-4`/`M-10`/`M-12`、`T-复编.02` 的 `review_manager`），**不是本任务动的**；
      方向层的投影规则 / 准入判定 / 容量口径**零改动**（`lib.rs` 的 diff 无删除行）
* [x] **未**因本任务跑过**全量**测试
      ⇒ 读数：只跑了 §2 的一条命令（`directional-pool-manager`）；
      `rs-src` 的 `cargo test` 属 `T-核.02` 线程，**未**由本任务触发

---

## ④ 测试目标 check list

<a id="d146"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方.02/D-146-checklist.md` -->

# D-146 `T-方.02`｜方向层按链批量物化入口 · 验收清单

> **本文件是 `T-方.02` 的全部验收框。** 编号 `AC-方.02` 属**纯 ID**。
> 每条勾选**必须**有实测读数支撑；**不得**凭推断勾选；**不得**用零冒充未测量。

**验收前置**：`M-3` 的现有单测全绿

**本轮状态（2026-11-26）**：**13** 框**全部**闭合（每条附实测读数）。
**实盘侧**：方向层已**真实刷新**（apply 落地，1.63 秒，`failedScopes` 为空），
刷新前已留 **692 行**行级备份；复核全程只读（`mode=ro` ＋ `PRAGMA query_only = ON`）。
**如实登记**：闭环层**未**重建（`closed_loop_version` 21 → 21 不变）——
闭环重建归 `T-闭.03` 的部署闸门（迁移 018 ＋ 新二进制同批），**不在本任务里代做**。

---

## 1. 根因与定位

- [x] 方向行**唯一**的生产写入方已定位到源码行

      > **读数（2026-11-26 复核）**：`directional_pool_manager/src/lib.rs:1897`
      > `replace_directional_rows` —— 全仓唯一写入
      > `manager_v4_directional_exchange_pools` 的生产语句
      > （`DELETE` 在 `:1907`、`INSERT` 在 `:1913` ⇒ 删旧插新 ＝ **全量替换**）；
      > `candidate_edge_pool_manager:3429` 与 `candidate_exchange_pool_manager:3231` 的同名字语句
      > **只在 `#[cfg(test)]` 的夹具里**出现。

- [x] 方向层的**快照列**来源已定位

      > **读数（2026-11-26 复核）**：`directional_pool_manager/src/lib.rs:1359-1364` ⇒
      > 逐列来自 `v4_exchange_pool_liquidity_snapshots`（即流动性 worker 刷新的那一份）；
      > 该表不存在时报 `MissingSnapshotTable`（`:1326-1333`），**不**降级成「测得为零」。

- [x] 实盘漂移量已量化

      > **读数**（`audit_directional_invariants_20251125` → `directional_invariants_postapply_20261125.json`）：
      > 方向行 **692**；可比 **436**；漂移 **83** 行 / **994** 格；无覆盖 **256** 行；
      > 方向层物化时刻 **2026-11-23 22:46**；快照表最新 **2026-11-25 00:44**。
      > **刷新后（2026-11-26 实盘）**：漂移 **0** 行 / **0** 格（FAIL → PASS），
      > 可比 436 / 无覆盖 256 不变 ⇒ 漂移**由本任务消除**（§4 有逐条读数）。

---

## 2. 实施

- [x] 新增公共入口「按链批量物化」已落地

      > **读数**：`materialize_directional_pools_for_chain`（`directional_pool_manager/src/lib.rs:505-556`）；
      > 返回体 `DirectionalPoolChainMaterializationResult`（`:459-482`）字段清单见 `D-147` §2.2；
      > 最小驱动 `examples/materialize_directional_pools_for_chain.rs`（只调入口，**不**重算任何口径）。

- [x] 枚举口径**确定**（按 `pool_scope_config.enabled = 1`，`scope_key` 升序）

      > **读数**：SQL 在 `lib.rs:564-567`（`enabled = 1` ＋ `ORDER BY scope_key`）；
      > 实盘枚举结果 `scopeKeys = ["candidate_exchange", "core4"]`（`D-147` §3.2）；
      > `pool_scope_config` 只有这 **2** 行 scope（本链），启用的也是这 2 个
      > （`fang02_preapply_state_20261126.json` 的 `scopeConfiguration`）。

- [x] 单 scope 行为与既有入口**逐字一致**

      > **读数**：单测 `the_chain_entry_materializes_every_enabled_scope_in_scope_key_order`
      > 逐字段比对链级入口与既有单 scope 入口的返回体
      > （`directional_pool_count` / `direction0_count` / `direction1_count` / `materialized_checksum`）；
      > **实现层面**：链级入口的每个 scope 调的就是同一个 `materialize_impl`（`lib.rs:530`），
      > 投影 / 准入 / 容量**一行未改** ⇒ 逐字一致不是「测得像」，是**同一段代码**。

- [x] **未放宽**任何门禁（投影规则 / 容量 / 利润 / 阈值 / 幂等键 / 回执关联）

      > **读数**：`git diff -- flashloan/tools/directional_pool_manager/src/lib.rs` =
      > **1 个 hunk、124 行新增、0 行删除** ⇒ 既有函数**一字未改**；
      > 投影 / 准入 / 容量 / 利润 / 阈值 / 幂等键 / 回执关联**全部落在既有函数里**，
      > 它们没有被这个 diff 触及。
      > 另：测试文件改了夹具助手（`insert_pool` → `insert_pool_into`），**仅测试代码**。

---

## 3. 回归（**只跑改动模块**）

- [x] `cargo test --offline -p directional-pool-manager` 全绿

      > **读数（2026-11-26）**：**27 passed / 0 failed / 0 ignored**
      > （基线 **25** ＋ 本任务新增 **2**：`the_chain_entry_materializes_every_enabled_scope_in_scope_key_order`、
      > `a_scope_that_fails_is_reported_and_does_not_hide_the_other_scope`）。

---

## 4. 实盘（**只读复核 → apply → 复核**）

- [x] dry-run 读数与 `inv-9` 读数对得上

      > **读数**：干跑 `changedRows` = **352**（`candidate_exchange`）/ **340**（`core4`）
      > ＝ 刷新前方向行数（`scope` 内每一行都要重写）⇒ **692 行全部被刷新**；
      > `directionalPoolVersion` 预读 **3**（刷新前库内存的是 **2**）。
      > **A/B**：干跑读数与只读副本上的同一次演练**逐字段相等**（机器比对 `True`），
      > 副本上 apply 后 `inv-9` 由 83 行/994 格转 **0/0** ⇒ 干跑**对得上** `inv-9`。

- [x] apply 后 `inv-9` 由 FAIL 转 PASS

      > **读数（2026-11-26 实盘 apply 后）**：`inv-9` **漂移 0 行 / 0 格**
      > （FAIL → PASS）；可比 436 / 无覆盖 256 **不变**。
      > 全量不变式审计：**9 passed / 0 failed**（`inv-1`…`inv-9` 全 PASS，
      > 其中 `inv-2` 692 行 / 0 不符、`inv-4` 522 跳全部可解析、`inv-7` 346 组各 2 行、
      > `inv-8` 222 组序位连续）；证据 `fang02_postapply_invariants_20261126.json`。

- [x] 方向层版本与校验和前进，且**闭环层读数未变**

      > **读数（2026-11-26 实盘）**：
      > `directional_pool_version` **2 → 3**（两个 scope 各前进一代）；
      > `materialized_checksum` `f6836f97…` → `4aa8b791…`（`candidate_exchange`）、
      > `5a609caf…` → `e0d8d6d1…`（`core4`）⇒ **前进**；
      > 方向层记录的 `snapshot_checksum` `863851e3…` → `01adb94a…`（与快照表当前值一致）；
      > `manager_v4_closed_loop_library_state.closed_loop_version` **21 → 21（不变）**、
      > 校验和 `034e7801…` **不变**、`updated_at` 仍为 **2026-11-25T10:14:49Z**
      > ⇒ **闭环层一行未动**（本任务只物化方向层）。

- [x] 实盘复核全程**只读**

      > **读数**：复核探针 `probe_directional_layer_state_20261126.py` 以
      > `file:...?mode=ro` URI 打开 ＋ `PRAGMA query_only = ON`（探针内 `open_read_only`）；
      > 入口的干跑用 `SQLITE_OPEN_READ_ONLY` 句柄（`lib.rs:2264-2271`）；
      > 不变式审计器 `audit_directional_invariants_20251125.py` 亦为只读。
      > ⇒ **实盘的写操作只有一次**：入口的 `apply`（692 行方向行 ＋ 2 行库状态），
      > 其余全部只读；闭链表、遗留表、快照表**零写入**。

---

## 5. 诚实边界

- [x] 本任务**未**改动闭环层 / 运行时维护链 / 内存大对象

      > **读数**：本任务的改动**只**在 `M-3`（`D-145` §1）⇒ 闭环层（`M-4`）、
      > 运行时维护链（`M-13`）、内存大对象（`M-2`）**均未触碰**；
      > 实盘复核也证明这一点：闭环层 `closed_loop_version` 21 → 21、校验和与 `updated_at` 不变。
      > 这三个缺口分别归属 `M-4`（`T-闭.03`）/ `M-13` / `M-2`（`T-核.02`，**另一线程**）的任务（见 `D-143` §3）。

---

## 导航

<a id="readme"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方.02/README.md` -->

# `T-方.02`｜方向层按链批量物化入口

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../../../README.md)。

**编号**：`T-方.02`（纯 ID）　**验收**：`AC-方.02`　**优先级**：P0　**波次**：`W-1`
**编号逻辑**：`T-方.02` / `AC-方.02` —— **模块内编号**，编号索引 **`方`**
（改动模块：`M-3`（方向层）），该索引号段内第 `02` 个。（详见 `D-144` §1.0）

**改动模块（= 回归范围）**：`M-3`

**前置模块**：`M-3`　　**需授权**：是（写生产库方向层）

## 文件

| 文件 | 内容 |
|---|---|
| `D-144` | 本任务的开发计划与回归范围（**自包含**） |
| `D-145` | 本任务的修改范围守护与逐模块回归命令 |
| `D-146` | 本任务的逐条验收框 |
| `docs/` | 本任务的**独立技术文案**区（入口设计 / 实测读数） |

## 一句话

> 方向层是闭环的**唯一池权威**，但它**没有任何自动刷新路径**：
> 实盘方向行停在 **2026-11-23 22:46**，快照列相对流动性快照表漂移 **83** 行 / **994** 格。
> 本任务给方向层补一个**按链批量物化**的公共入口，
> 让运行时只管调用、**不自己算**（Python 侧不做数据处理）。

**当前状态（2026-11-26）**：**13 / 13** 项闭合。
入口已落地（`materialize_directional_pools_for_chain`）并**在实盘真实刷新**过方向层：
`inv-9` 由 FAIL（**83** 行 / **994** 格）转 **PASS（0 / 0）**，
方向层版本 **2 → 3**，闭环层**一行未动**（`closed_loop_version` 21 → 21）。
**如实登记**：运行时「方向层自动刷新槽」**尚未在实盘生效**
（新槽要新的 Rust 扩展，扩展重建归 `T-核.02` 线程）；
闭环层重建归 `T-闭.03` 的部署闸门（迁移 018 ＋ 新二进制**同批**）。

## 独立回归

```powershell
cargo test --offline -p directional-pool-manager
```

**读数（2026-11-26）**：`27 passed / 0 failed`（基线 25 ＋ 本任务新增 2）。

**实盘读数（本轮）**：方向行 **692**（`core4` 340 / `candidate_exchange` 352）；
刷新前 `inv-9` FAIL：可比 **436** 行、漂移 **83** 行、**994** 格、无覆盖 **256** 行；
刷新后 **PASS**：漂移 **0** 行 / **0** 格（全量审计 **9 / 9 PASS**），
方向层版本 **2 → 3**，校验和 `f6836f97…`/`5a609caf…` → `4aa8b791…`/`e0d8d6d1…`。

---

## ⑤ 技术文案区（`docs/`）

> **原 `docs/` 目录已并入本归档件** —— 逐份保留，锚点见下。

<a id="d147"></a>

### `D-147-方向层批量物化入口与实盘刷新_20261125.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方.02/docs/D-147-方向层批量物化入口与实盘刷新_20261125.md` -->

# D-147 方向层「按链批量物化」入口与实盘刷新

> **本文档是 `T-方.02` 的技术文案。**
> 它给出入口契约、枚举口径，以及**实盘刷新**的读数与回滚路径。

> 上级：[D-143 闭环引用有向池核实与三处未收口](../../../docs/D-143-闭环引用有向池核实与三处未收口_20261125.md) §2.3。

---

## 1. 为什么入口要放在 `M-3`

「本链启用了哪些 scope」是**数据逻辑**：它读 `pool_scope_config`、
按 `enabled` 过滤、按 `scope_key` 定序。
把它放在 Python 侧就等于在 Python 里重写一遍管理器的判断 —— 
这正是用户口径里明确排除的那件事：

> python 侧没有自己的数据处理逻辑，仅仅使用内存对象中的实际值，
> 内存对象直接调用相关的管理工具（rust 实现）。

⇒ 入口属于 `M-3`；Python 侧将来只做一次调用 ＋ 读回读数。

---

## 2. 入口契约

### 2.1 签名

```rust
/// Materialize the directional projection of every enabled scope of one chain.
pub fn materialize_directional_pools_for_chain(
    database_path: impl AsRef<Path>,
    network: &str,
    chain_id: i64,
    apply: bool,
) -> Result<DirectionalPoolChainMaterializationResult, DirectionalPoolError>;
```

### 2.2 返回体

```rust
pub struct DirectionalPoolChainMaterializationResult {
    pub network: String,
    pub chain_id: i64,
    pub protocol: String,          // 恒为 uniswap_v4
    pub apply: bool,
    pub dry_run: bool,             // = !apply
    pub scope_keys: Vec<String>,   // 枚举到的启用 scope，按 scope_key 升序
    pub scopes: Vec<DirectionalPoolMaterializationResult>,   // 逐 scope，按 scope_key 升序
    pub failed_scopes: Vec<DirectionalPoolScopeFailure>,      // 物化失败的 scope 与原因
}
```

`DirectionalPoolMaterializationResult` 是**既有**类型（`lib.rs:191`），
**逐字复用** ⇒ 单 scope 的读数字段一个都不新增、不改名。

> **读数（2026-11-26 订正）**：本节初稿曾列 `scope_count` / `total_changed_rows` /
> `directional_pool_versions` / `consumers_need_rebuild` 四个聚合字段。
> **实现未采用**它们，原因是**聚合值本来就在逐 scope 结果里**：
> `DirectionalPoolMaterializationResult` 已带 `changed_rows` / `directional_pool_version` /
> `materialized_checksum` / `consumers_need_rebuild`，
> 再造一份聚合就是**第二套口径**（两份可能不一致，且没人知道该信哪份）。
> ⇒ 返回体以**实现为准**，聚合由调用方按逐 scope 字段自行汇总
> （运行时的 `run_directional_pool_build` 已经是这么做的）。

### 2.3 枚举口径

```sql
SELECT scope_key
  FROM pool_scope_config
 WHERE network = ?1 AND chain_id = ?2 AND enabled = 1
 ORDER BY scope_key
```

| 决定 | 理由 |
|---|---|
| 只取 `enabled = 1` | 停用的 scope 不该被物化；单 scope 入口遇到停用 scope 会返回 `MissingPoolScope` |
| `ORDER BY scope_key` | 顺序确定 ⇒ 读数可复算、可逐行对比 |
| 枚举失败 ⇒ **整体失败** | 枚举读的是 scope 配置；读不到就**不知道**这条链有哪几个 scope，无从物化 |
| 单 scope 物化失败 ⇒ **记录并继续**，链级结论由调用方判为**失败** | 见 §2.3.1 |
| 单 scope 结果**不**调用 `materialize_directional_pools_write` | 批量入口**不**写幂等请求记录（那是 HTTP 写入路径的职责） |

> **口径订正（2026-11-26）**：枚举 SQL 实现为 `enabled = 1`（**不是** `enabled != 0`）。
> `pool_scope_config.enabled` 的建表约束是 `CHECK (enabled IN (0, 1))`，
> 两种写法在**当前** schema 下等价，但只有 `= 1` 与本文档的口径**逐字**一致 ⇒ 取 `= 1`。

### 2.3.1 失败契约（**实现与运行时的共同口径**）

| 层 | 行为 | 依据 |
|---|---|---|
| 管理器（Rust） | 某个 scope 物化失败 ⇒ **记入 `failed_scopes`（带原因）**，其余 scope 继续物化 | `materialize_directional_pools_for_chain` 的 `match` 分支 |
| 运行时（Python） | `ok = receipt.ok and not failedScopes` ⇒ **有失败 scope ⇒ 链级结论为失败** | `v4_maintenance_workers.py:run_directional_pool_build` |
| 下游（闭环槽） | 方向层槽失败 ⇒ 闭环槽**不发布** ⇒ 不会产出「混了两代方向层」的闭环 | 同上，`worker_dependencies()` 把闭环槽挂在方向层槽之后 |

**为什么不是「一个 scope 失败就整体中止」**：
中止能少写一点数据，但**换不来一致性** —— 先写的 scope 已经落库，中止只是把
「两代混在一起」换成「一半新一半旧」。真正决定安全的是
**链级结论必须为失败**，而这一条由运行时的 `not failedScopes` 判定，
不是由管理器是否中止来保证。

**如实登记的边界**：

* 逐 scope 的写入是**各自独立的事务** ⇒ 一个 scope 失败时，
  **其余 scope 的行可能已经落库**；此时库里是两个代次，
  但**每个 scope 内部自洽**（一个 scope 的行全部来自同一次物化）。
* 下游的防线是「**有失败 scope 就不发布闭环**」，不是「库里绝不混代次」。
  若要库里也绝不混代次，得改成**链级单事务**（一次事务写全部 scope）；
  本轮**未**做，登记为 `T-方.02` 的**遗留增强项**（见 §6）。

### 2.4 与既有入口的关系

```text
materialize_directional_pools(db, net, chain, scope, apply)      ← 既有，单 scope
materialize_directional_pools_write(db, request)                 ← 既有，带幂等记录
materialize_directional_pools_for_chain(db, net, chain, apply)   ← 新增，逐 scope
```

新增入口**只做编排**：每个 scope 调一次既有的 `materialize_impl`（`apply=true`、无写请求）。
⇒ 单 scope 的投影、准入、容量计算**一字未改** ⇒ 单 scope 行为与既有入口**逐字一致**。

---

## 3. 实盘刷新读数（**2026-11-26 落地**）

实盘库：`flashloan/db/local_store_4663.sqlite3`（**15.32 GB**，迁移停在 **017**）。
入口二进制：`flashloan/tools/target/release/examples/materialize_directional_pools_for_chain.exe`。

### 3.1 刷新前（只读，`mode=ro` ＋ `PRAGMA query_only = ON`）

| 读数 | 值 |
|---|---|
| 方向行总数 | **692**（`candidate_exchange` 352 / `core4` 340） |
| 方向层物化时刻 | **2026-11-23T22:46:06Z** / **2026-11-23T22:46:22Z** |
| 流动性快照表最新 | **2026-11-25T00:44:27Z**（2486 行） |
| `inv-9` 可比 / 漂移 / 格 / 无覆盖 | **436 / 83 / 994 / 256**（FAIL） |
| 方向层版本 | **2**（两个 scope 同为 2） |
| 方向层校验和 | `candidate_exchange` `sha256:f6836f97aa22a3c7…`／`core4` `sha256:5a609cafc9bda247…` |
| 方向层记录的快照校验和 | `sha256:863851e3dcc1b32e…`（**已过期**，与快照表当前值不符） |
| 闭环层 `closed_loop_version` / 校验和 | **21** / `sha256:034e780179edfdf3…`（刷新前基准） |

刷新前**已留行级备份**：`plan/runtime_evidence/directional_layer_backup_20261126.json`（**692** 行）。

### 3.2 干跑（只读句柄，`SQLITE_OPEN_READ_ONLY`）

| 读数 | `candidate_exchange` | `core4` |
|---|---|---|
| 枚举顺序 | `scopeKeys` = [`candidate_exchange`, `core4`]（`scope_key` 升序） | 同左 |
| `changedRows` | **352** | **340** |
| `directionalPoolVersion` | **3** | **3** |
| `materializedChecksum` | `sha256:4aa8b79107c0259d…` | `sha256:e0d8d6d1890a3d6c…` |
| `snapshotChecksum` | `sha256:01adb94a1b66b123…` | 同左（同一张快照表） |
| `failedScopes` | **空** | **空** |

**A/B 交叉验证**：该干跑读数与**只读副本**上的同一次演练
（`plan/runtime_evidence/kernel01_directional_dryrun_20261126.json`）**逐字段相等**
（机器比对 `live == clone` ⇒ `True`）⇒ 副本演练结论**对实盘成立**。

### 3.3 apply 后（**本任务落地**）

| 读数 | 值 |
|---|---|
| apply 耗时 | **1.63 秒**（两个 scope，各自一个事务） |
| `failedScopes` | **空** |
| 方向层物化时刻 | **2026-11-25T18:19:39Z**（＝ **2026-11-26 02:19:39 +08**） |
| `inv-9` 漂移 | **0 行 / 0 格**（**FAIL → PASS**；可比 436 / 无覆盖 256 不变） |
| 方向层版本 | **2 → 3**（两个 scope 各前进一代） |
| 方向层校验和 | **变**：`f6836f97…` → `4aa8b791…`；`5a609caf…` → `e0d8d6d1…` |
| 方向层记录的快照校验和 | `863851e3…` → `01adb94a…`（**与快照表当前值一致**） |
| 闭环层 `closed_loop_version` / 校验和 | **21 / `034e7801…`（不变）**；`updated_at` 仍为 **2026-11-25T10:14:49Z** |
| 全量不变式审计 | **9 passed / 0 failed**（`inv-1`…`inv-9` 全部 PASS） |
| 运行中进程 | **pid 24524 未重启、未受影响**（方向层不改表结构） |

### 3.4 幂等（**实盘复读**）

apply 后再干跑一次：**`changedRows` 0 / 0**，**版本仍为 3**，**校验和不变**
⇒ 方向层是一个**不动点**，重复调用不会把版本推着走
（单测 `the_materialization_is_idempotent` 另有覆盖）。

### 3.5 上游停更的如实登记（**不是本任务的缺口**）

刷新后方向层与流动性快照表**完全一致**，但快照表本身最新只到
**2026-11-25T00:44:27Z**（距刷新时刻约 **26.5 小时**）
⇒ 方向层能被刷新的**上限**就是上游快照表的**新鲜度**。
快照表停更属**运行时维护链**（`T-核.01` 域）的缺口，
本任务**不越界**处理，登记在 `D-014` 的遗留项里。

### 3.3 读数与闭环的关系（**必须同批落地**）

方向层是闭环的**上游**：方向层的 `pair_direction_rank` 变了，
闭环的**主序与备用组合**就要跟着变。
⇒ 刷新方向层之后**必须**再跑一次闭环重建，
否则闭环会停在「按旧序位选池」的状态 —— 
这是**两段流水线的顺序依赖**，不是可选优化。

---

## 4. 回滚

| 项 | 说明 |
|---|---|
| 形态 | 方向层是**全量替换**（`replace_directional_rows` 删旧插新）⇒ 回滚 = 用刷新前的库状态重跑一次 |
| 前置 | 刷新前必须留一份方向层快照（行级），否则回滚只能靠重跑上游重建 |
| 演练 | 需在**影子库**上做（生产库 15.32 GB，不整库复制） |
| **已留** | **行级备份已落**：`plan/runtime_evidence/directional_layer_backup_20261126.json`（**692** 行，含全部列） |

**回滚怎么做（备份已在手）**：备份里是**物化前**的 692 行，
按 `scope_key` / `base_pool_id` / `direction` 定位后整体写回
`manager_v4_directional_exchange_pools`，并把
`manager_v4_directional_pool_library_state` 的
`directional_pool_version` / `materialized_checksum` / `snapshot_checksum`
改回 `2` / `f6836f97…`（`core4`：`5a609caf…`）/ `863851e3…`。

> **未演练**：回滚**动作本身**未在实盘上执行过（不该为了验证回滚而破坏生产）。
> 可演练的等价形式是「用备份在影子库上重放旧行」，留给需要时再做。

---

## 5. 诚实边界

* 本任务**只**物化方向层：**闭环层一行未动**（`closed_loop_version` 21 → 21，
  校验和与 `updated_at` 均不变）⇒ 「方向层刷新 ⇒ 闭环主序变化多少」
  **仍需要另一次 A/B**（闭环重建前后的逐行 diff），该读数归属闭环侧任务
  （`T-闭.03`），**不在本任务里代填**。
* 本任务**不**引入幂等记录；批量入口被重复调用是**幂等**的
  （内容没变 ⇒ 校验和不变 ⇒ 不写库）。**已由单测证明**
  （`the_materialization_is_idempotent`），并有**实盘复读**佐证
  （第二次干跑 `changedRows` 0 / 0，版本仍 3）。
* **上游新鲜度是本任务的上限**：方向层刷新的内容来自流动性快照表，
  该表最新只到 **2026-11-25T00:44:27Z**（§3.5）⇒ 方向层与快照表**一致**，
  但与**墙上时钟**之间仍有约 **26.5 小时**的差距。这不是本任务的缺口
  （方向层没有自己的采集能力），登记为遗留项。
* **实盘运行期未生效新口径**：方向层的**物化**已落地（数据是新的），
  但**运行时**的「方向层自动刷新槽」仍**未在实盘生效**
  （新槽要新的 Rust 扩展，扩展尚未重建 ⇒ 旧扩展里没有该入口）。
  ⇒ 本次刷新是**一次性手工物化**；**自动刷新**属 `T-核.01` 的部署闸门。

---

## 6. 遗留增强项（本任务**未**做，登记而不假装）

| # | 项 | 现状 | 为什么现在不做 |
|---|---|---|---|
| R1 | **链级单事务** | 逐 scope 各自一个事务 ⇒ 一个 scope 失败时，其余 scope 的行可能已落库 | 逐 scope 内部自洽；链级「失败」的判定在运行时侧（`not failedScopes`）已生效；改成链级单事务要动 `materialize_impl` 的事务边界，**收益小于风险** |
| R2 | **回滚动作演练** | 备份已在手，回滚**动作**未演练 | 为验证回滚而破坏生产不是可接受的做法；等真有需要时在影子库演练 |
| R3 | **运行时自动刷新** | 方向层槽已接线（`v4_directional_pool_worker`），但实盘扩展未重建 ⇒ 槽在实盘跑不起来 | 扩展重建归 `T-核.02` 线程（`rs-src`），本任务不抢 |

---

<a id="docs-readme"></a>

### `README.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-方.02/docs/README.md` -->

# `T-方.02` · 技术文案

| 文件 | 内容 |
|---|---|
| `D-147` | 入口契约、枚举口径、实盘刷新读数 |

> 本目录**只写本任务相关**的技术文案；引用别的任务时**给链接**，不复制正文。

---
