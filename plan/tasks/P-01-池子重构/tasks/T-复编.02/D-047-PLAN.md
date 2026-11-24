# D-047 `T-复编.02`｜Review 金额与利润完整性 · 开发计划（**独立 · 自包含**）

> **本目录是 T-复编.02 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**
> **本文件 + `checklist.md` 即 T-复编.02 的全部开发与测试计划。**
> 本目录**不引用**其它任务目录的验收内容；需要别的任务先完成时，只写**模块级前置**（§1）。
>
> **编号是纯 ID**：`T-复编.02` / `AC-复编.02` **不表达任何功能关联**。
> **范围与顺序只按模块编号（`M-n`）表达。**

---

## 1.0 编号逻辑（**本任务的编号从哪来**）

> **本节是固定部分。** 每个任务都必须**自己**声明编号逻辑，
> 使读者**不看编号册**也能知道「这个编号的索引是什么、序号是多少」。

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-复编.02`** |
| **本任务验收编号** | **`AC-复编.02`**（与任务编号**同号段**） |
| **编号作用域** | **跨模块（组合索引）** |
| **编号索引** | **`复编`**（`M-6`（复核层（复核发布 / 金额利润））、`M-8`（编排层）） |
| **序号** | 第 `02` 个（该索引号段内递增，**不复用**；`.01` = 已归档的 `T-复编.01`） |
| **同索引其它任务** | `T-复编.01`（已归档） |


本任务**改动 2 个模块**（`M-6`、`M-8`）⇒ 按 §1.2 规则取这些模块的索引**按模块号升序拼接** ⇒ 组合索引 **`复编`**。

> **编号变更记录**（2026-11-24）：初始登记的索引是 **`复`**（模块集合 `M-6`）。
> 实测发现**常驻 worker 容量问题**（扫描器内联了一份「默认 16 / 硬顶 32」的**复制品**）
> 必须在**编排层 `M-8`**修复 ⇒ 模块集合扩为 `M-6` + `M-8`
> ⇒ 组合索引合法名只能是 **`复编`** ⇒ 任务编号同步改为 **`T-复编.02`**。
> 旧号 `T-复.01` **已消费**（不复用）。扩范围的实质内容见 §1.3。



**规则**：

1. **一个任务只改动一个模块** ⇒ 编号索引 = 该模块索引（模块内编号）。
2. **一个任务改动多个模块** ⇒ 编号索引 = **各模块索引按模块号升序拼接**（组合索引）。
   拼接顺序**按模块号 `M-n`**（`M-3` < `M-4` < … < `M-10`），**不按索引字典序** ——
   否则同一组合会出现两种拼法 ⇒ 编号不可复算。
3. **模块索引表**见 [`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §1.1；
   **组合索引规则**见同文件 §1.2。
4. **不同索引的序号互不消耗** —— `方储` 做到 `.09` **不影响** `储` 的 `.04`。
5. **前置与顺序只按模块号 `M-n` 表达** —— **禁止**写「等 `T-x.yy` 完成」。

**本任务涉及的模块**（**这一列就是本任务的回归范围**）：

| 模块 | 名称 | 索引 |
|---|---|---|
| `M-6` | 复核层（复核发布 / 金额利润） | `复` |
| `M-8` | 编排层 | `编` |


**登记**：本任务的号段占用情况登记在
[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §4.2（**编号唯一真相源**）。

---

## 1. 身份与范围

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-复编.02`** |
| **验收编号（纯 ID）** | **`AC-复编.02`** |
| **标题** | Review 金额与利润完整性 |
| **优先级** | P0 |
| **波次（模块级）** | W-2 |
| **前置模块**（**不是**前置任务） | M-6 |
| **需授权** | 是（写库） |
| **未完成验收项** | **29** |
| **已闭合验收项** | 0 |

### 1.1 改动模块（**这一列就是 T-复编.02 的回归范围**）

| 模块 | 名称 | 代码落点 |
|---|---|---|
| `M-6` | 复核层 | `flashloan/tools/review_manager/` |
| `M-6` | 复核层（**扩范围 · 事后补记 · Rust 原生发布层**） | `flashloan/rs-src/src/review_publish.rs` |
| `M-8` | 编排层（**扩范围 · 事后补记**） | `flashloan/src/dex/runtime/dex_opportunity_scanner_rust_mixin.py`、`flashloan/src/dex/runtime/closed_loop_state_machine.py`、`flashloan/src/dex/runtime/rust_closed_loop.py`、`flashloan/app_runtime/.env.example` |
| `M-8` | 编排层（**扩范围 · 第三次**） | `flashloan/tools/composite_manager/src/overview.rs` |

> **规则**：**只跑**上表模块的独立测试。**未在上表出现的模块，一律不跑。**
> **不得**以「有新任务」或「功能没变」为由跑全量。

### 1.2 回归命令（**逐条可复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| `M-6` | 复核层 | `cargo test --offline -p review-manager` |
| `M-6` | 复核层（**扩范围**：Rust 原生发布层） | `cargo test --offline --release`（工作目录 `flashloan/rs-src/`） |
| `M-8` | 编排层（**扩范围 · 第三次**：一致性判定） | `cargo test --offline -p composite-manager`（工作目录 `flashloan/tools/`） |
| `M-8` | 编排层（扩范围） | `python -m pytest flashloan/app_runtime/tests/unit/test_rust_closed_loop_worker_bridge.py flashloan/app_runtime/tests/unit/test_dynamic_closed_loop_workers.py flashloan/app_runtime/tests/unit/test_chain_scoped_parameter_layering.py flashloan/app_runtime/tests/unit/test_secret_leakage_guards.py flashloan/app_runtime/tests/unit/test_dex_route_test.py flashloan/app_runtime/tests/unit/test_d4_capacity_informed_ladder.py flashloan/app_runtime/tests/unit/test_manager_overview_closed_loop_identity.py -q` |

```powershell
# T-复编.02 的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p review-manager
cargo test --offline --release            # 工作目录 flashloan/rs-src/
cargo test --offline -p composite-manager   # 工作目录 flashloan/tools/
python -m pytest flashloan/app_runtime/tests/unit/test_rust_closed_loop_worker_bridge.py flashloan/app_runtime/tests/unit/test_dynamic_closed_loop_workers.py flashloan/app_runtime/tests/unit/test_chain_scoped_parameter_layering.py flashloan/app_runtime/tests/unit/test_secret_leakage_guards.py flashloan/app_runtime/tests/unit/test_dex_route_test.py flashloan/app_runtime/tests/unit/test_d4_capacity_informed_ladder.py flashloan/app_runtime/tests/unit/test_manager_overview_closed_loop_identity.py -q
```

> **`M-8` 回归命令在 2026-11-24 增补 3 个文件**（§1.3.2 的修复落在
> `flashloan/src/dex/runtime/rust_closed_loop.py`，该文件的直接调用方是这 3 个测试文件）：
> `test_dex_route_test.py`、`test_d4_capacity_informed_ladder.py`、
> `test_manager_overview_closed_loop_identity.py`。

### 1.3 扩范围登记（**事后补记 · 流程欠账**）

**扩的模块**：`M-8`（编排层）—— `flashloan/src/dex/runtime/dex_opportunity_scanner_rust_mixin.py`、
`flashloan/src/dex/runtime/closed_loop_state_machine.py`。

**为什么必须扩**：本任务的**复核发布**链路（`T-复编.01` 交付）本身正确，
但实盘**一次都没有落地**——复核库至今仍是 `review_version=24` / `closed_loop_version=14` /
164 行 / `reviewed_at='unreported'`。

**实测根因**（只读探针，见 `docs/`）：

| 事实 | 读数 |
|---|---|
| 闭环目录发布的方向组数 | **174**（每组恰好 1 个闭环 ⇒ 需 **174** 个 worker） |
| **overview** 给出的方向组数 | **174**（与目录**完全一致**，缺口 0） |
| **扫描器 payload** 给出的方向组数 | **104**（**第一层根因放开容量后仍缺 74** ⇒ 见 `1.3.1） |
| 运行时**常驻 worker** 数 | **16** |
| 常驻上限的**内联硬顶** | **32**（`DEX_CLOSED_LOOP_MAX_RESIDENT_GROUPS` 默认 **16**） |
| ⇒ **没有 worker 的闭环数** | **158** |

⇒ 这 158 个闭环**永远拿不到复核证据** ⇒ `covers_catalog()`
（证据条数 == 目录条数 == 174）**永远不成立** ⇒ `review_publish_block` 永远返回
`coverage_incomplete` ⇒ **复核代次永不发布**。

**为什么只能在编排层修**：常驻容量是**编排层**的策略（`M-6` 复核层只负责**发布门禁**，
门禁本身**一字未改**、**不得**放宽）。Python 侧**已有**正确的解析函数
`closed_loop_state_machine::resolve_resident_group_limit()`（按**已发现组数**解析、
受 `CLOSED_LOOP_RESIDENT_GROUP_CEILING` 约束），但**扫描器没有调用它**，
而是内联了一份**默认 16 / 硬顶 32** 的复制品 ⇒ **容量与闭环目录规模脱钩**。

**改了什么**（**最小改动**）：

| 文件 | 改动 |
|---|---|
| `closed_loop_state_machine.py` | 把**常驻组**上限与**并发**上限**拆开**：并发上限 `CLOSED_LOOP_WORKER_CEILING=32` **不动**（RPC 资源守卫），新增 `CLOSED_LOOP_RESIDENT_GROUP_CEILING=256`（对齐 Rust 内核 `worker_pool::CLOSED_LOOP_WORKER_CEILING`） |
| `dex_opportunity_scanner_rust_mixin.py` | 删掉内联的「默认 16 / 硬顶 32」复制品，改调**共享**的 `resolve_resident_group_limit()`；批量坡道改用共享的 `new_groups_per_scan_override()`；并把**仍持有 worker 但本轮未被发现**的组计入上限（否则上限会被一条陈旧线程卡死） |
| `app_runtime/.env.example` | **钉死值** `DEX_CLOSED_LOOP_MAX_RESIDENT_GROUPS=16` 改为**留空**（＝跟随已发现的方向组数）。该模板若被复制到部署环境，会把上面两处修复**原样打回**（常驻组又被压回 16 ⇒ 复核代次永远发布不出来） |

**没改什么**（**门禁一字未动**）：复核发布门禁（覆盖全集 / 上游代际 / 幂等键）、
`review_manager` 的 `ok && executable` 判定、方向池与固定费率约定、广播门禁。

**缺口如实登记**：`D-014` §0.2 的「M-n 模块表**没有 `rs-src` / Python 运行时归属**」
仍未闭合 —— 本次沿用 `T-复编.01` 先例把运行时落点登记在 `M-8` 名下，**不**改模块表。

---

### 1.3.1 第二层根因：扫描器的**方向组全集**缺了目录的 74 组（**死锁**）

> **§1.3 放开的是「容量」** —— 容量放开后常驻上限不再是 16，
> 但**方向组全集本身**仍缺 74 组 ⇒ 那 74 个闭环**连第一个 worker 都拿不到**。

| 事实 | 读数 |
|---|---|
| 闭环目录发布的**方向组**数 | **174**（每组 1 个闭环 ⇒ 需 174 个 worker） |
| 扫描器 payload 的 **route** 数 | **104** |
| 扫描器 payload 的**方向组**数 | **104**（与常驻 worker 组集合**完全相同**，双向差 0） |
| 两者**交集** | **100** 组 |
| **目录有、扫描器没有 route** 的组 | **74**（如 `meta`¦`eth`¦`spy`、`nvda`¦`mu`¦`eth` …） |
| **扫描器有、目录没有** 的组 | **4**（`spy`¦`coin`¦`usdg`、`spy`¦`eth`¦`meta`、`spy`¦`meta`¦`eth`、`spy`¦`usdg`¦`coin`） |

> **口径说明**：上表按**方向组**（`anchor`¦`Y`¦`Z`，**方向敏感**）统计。
> 按**物理闭合键**（**地址有序**三元组）统计则同源：扫描器 104 / 目录 174 /
> 交集 100 / 扫描器独有 4 / 目录独有 74 ⇒ **两个口径结论一致**。

**死锁的形状**（**不是容量问题，是依赖方向问题**）：

```text
组只有在扫描器产出 route 时才成为常驻 worker
扫描器只产出「已经持有 worker」的组的 route      ← 观察集可达性过滤的结果
  ⇒ 目录里 74 个组永远拿不到**第一个** worker
  ⇒ 这 74 个闭环永远拿不到复核证据
  ⇒ covers_catalog()（证据条数 == 目录条数 == 174）永不成立
  ⇒ 复核代次永远发布不了
```

**为什么是「不一致」而不是「设计」**：
**手动复核路径**（`_refresh_native_closed_loop_group_locked`）**早已优先用 manager 视图**，
其注释原文即为：

> Mainnet refresh prefers the RuntimeCore manager overview so loop libraries that are
> not yet in the static observation snapshot can still be submitted.

⇒ **自动扫描路径漏了这一步** ⇒ 同一份目录，两条路径给出**不同的方向组全集**。

**成因**（代码位置）：

| 路径 | 取数 | 结果 |
|---|---|---|
| 扫描器 payload | `dex_quote_routes_payload` → `dex_overview_payload` → `compact_snapshot` | `dex_surface_snapshots` 表**是空的** ⇒ 走**实时观测**路径 ⇒ 经 `_overview_path_candidates_from_observation_sets` 的 `observationReachable` 过滤 ⇒ **104** |
| overview | `_overview_paths_from_static_summary(..., preserve_direction=(compact_snapshot["path"]=="sqlite_db"))` | 保留**双向** ⇒ **174** |
| 手动复核 | `_native_manager_closed_loop_route_payload`（manager 闭环视图） | **174**（`pathSymbols` / `pathAddresses` / `poolIds` / `poolPathKey` 与 mixin 已有 helper **完全匹配**） |

**改了什么**（**最小改动 · 仍在 `M-8` 内 ⇒ 无需再扩范围**）：

| 文件 | 改动 |
|---|---|
| `dex_opportunity_scanner_rust_mixin.py` | 新增模块级 helper `_native_chain_runtime(chain_id)`：**请求内**用 Flask `g.chain_runtime`，**后台扫描线程**（无 app context）用 `chain_runtime_registry().get(chain_id)`；未注册则返回 `None`，调用方保持原行为 |
| `dex_opportunity_scanner_rust_mixin.py` | `_native_manager_overview_route_payload` / `_native_manager_closed_loop_route_payload`：**去掉 Flask-only 取 runtime 的三段**，统一走 `_native_chain_runtime()`（原来只有手动复核路径够得着 manager 视图） |
| `dex_opportunity_scanner_rust_mixin.py` | `_submit_native_closed_loop_groups`：把「只遍历 payload routes」的循环抽成闭包 `_collect_groups(routes, *, only_missing=False)`；先收 payload routes（**原行为不变**），再取 **manager 闭环视图**并**只补齐扫描 payload 缺失的组**；新增读数 `submission["managerGroupsAdded"]` |

**没改什么**：闭环目录本身**一字未改**（174 组仍是 174 组）；
方向池与固定费率约定不变；复核发布门禁（覆盖全集 / 上游代际 / 幂等键）**一字未动**。

**为什么多出来的 4 组不破坏 `covers_catalog()`**：
扫描器独有的 4 组**不在已发布闭环目录内** ⇒ 内核 `apply_closed_loop_identity()`
找不到对应 identity ⇒ 只写 `closedLoopIdentityStatus = "mismatch"`、**不写** `closedLoopKey`
⇒ **不产出复核证据** ⇒ 证据条数仍为 **174**（= 目录条数）⇒ 门禁成立。

---

### 1.3.2 第三层根因：闭环身份的「**池子那一半**」对不上（**25 条闭环**）

> §1.3.1 补齐了**方向组全集**，但**组齐了 ≠ 身份齐了**。
> 内核的闭环身份有**两半**：**有序地址路径**（funding anchor）＋
> **逐跳发布的有向池**。**两半都对上，身份才成立**；
> 缺哪一半，内核都**不写** `closedLoopKey`。

| 事实 | 读数 |
|---|---|
| 闭环目录发布的闭环数 | **174** |
| 目录给出的**逐跳发布池**（`pool_ids_json`） | 每条闭环 **3** 个（每跳 **1** 个）；备用池在 `backup_pool_ids_json` |
| **身份成立**的闭环数（离线复现**生产函数**） | **149 / 174** |
| ⇒ **身份不成立**的闭环数 | **25** |
| 25 条的失配位置 | **全部是第 2 跳（bridge）**，形态统一为 §[真, 假, 真]§ |
| 25 条的路由池组来源 | **全部来自扫描 payload 的「观测池组」**；另外 **74** 条走**候选面回落**的**全部成立** |

**成因**（`flashloan/src/dex/runtime/rust_closed_loop.py::_route_task_config§）：

内核的契约原文（`flashloan/rs-src/src/worker_pool.rs::apply_closed_loop_identity§）：

> The route prices **every candidate pool** of every hop, while the Manager
> publishes the **one pool it selected** for each hop and keeps the rest as
> backups.

⇒ 内核假定**路由给每一跳定价全部候选池**。但 Python 侧**优先**用
**市场观测的池组**（每跳 **1** 个池），**候选面**只作**回落**：

§§§python
    if route_groups:
        for raw_group in route_groups:
            ...
            groups.append(normalized_group)
        if not groups:
            groups = candidate_groups      # ← 只在没有观测池组时才用候选面
    else:
        groups = candidate_groups
§§§

⇒ 当**观测选的池 ≠ 闭环管理器发布的池**时（25 条闭环的第 2 跳），
路由**根本没定价**管理器发布的那个池 ⇒ 内核
§manager_pools_are_priced_by_route()§ 判**失配** ⇒ 只写
§closedLoopIdentityStatus = "mismatch"§、**不写 §closedLoopKey§**、
**不发布 §singleTickCapacityRaw§** ⇒ 阶梯以
§single_tick_capacity_unavailable§ 被挡 ⇒ **不产出复核证据**
⇒ 证据条数**封顶在 149** < 目录条数 **174** ⇒
§covers_catalog()§ **永不成立** ⇒ 复核代次**依然永不发布**。

**改了什么**（**最小改动** · 仍在 §M-8§ 内 ⇒ 无需再扩模块）：

| 文件 | 改动 |
|---|---|
| §flashloan/src/dex/runtime/rust_closed_loop.py§ | 新增模块级 helper §_merge_pool_key_groups()§：按 **PoolKey 身份**合并两组「池键组」（观测组在前、候选面组在后；已存在的不重复加入）；§_route_task_config()§ 改为**观测组与候选面组取并集**，不再「优先观测、回落候选」 |

**没改什么**：闭环目录本身**一字未改**（仍是 **174** 条 / v19 / 校验和
§sha256:6927d8d6…§）；**内核的身份门禁一字未改**（仍然要求「路由定价的池
**包含**管理器发布的池」）；方向池与固定费率约定不变；广播门禁不变；
**没有新增任何 Python 侧的数据处理** —— 候选面**本来就是 Python 侧已经读到的**，
这次只是**不再把它丢掉**。

**为什么并集是对的**：候选面是**池管理器自己**给出的每跳候选池清单，
而**管理器发布的池就在其中**（实测：**174 / 174** 条闭环的发布池
都能在候选面里找到）。并集之后，**每一跳定价的池** = 候选面里该跳的池
∪ 观测选的池（观测选的池**本来就在候选面里**，否则那一组根本不会被采纳）
⇒ 与内核注释里写的契约**一致** ⇒ 身份**必然成立**。

**实测（离线复现**生产函数** §_route_task_config§，同一库、同一目录）**：

| 口径 | 读数 |
|---|---|
| 修复前：路由定价的池 ∋ 发布的池 | **149 / 174** |
| 修复后 | **174 / 174** |

**回归**：§M-8§ 的 7 个测试文件 → **209 passed**（§1.2）。

---

### 1.3.3 第四层根因：**Rust 原生复核发布层把「金额状态 + 金额阶梯」丢在了链路之外**

> §1.3 / §1.3.1 / §1.3.2 修的是「**复核发不发布**」；
> 这一层修的是「**发布出来的那条复核里有没有金额**」。

§1.3.2 修复后复核代次**真的发布了**（`review_version` 持续自增，`closed_loop_version=19`，
174/174 全覆盖）。但把发布出来的记录拆开看，**金额整块不见了**：

| 事实 | 读数 |
|---|---|
| 发布出的 `manager_v4_reviews.static_preview_json` 的键 | **8 个**（`source` / `measuredAt` / `status` / `routeSymbols` / `routeAddresses` / `selectedAmountRaw` / `selectedProfitRaw` / `attemptedCount`）—— **没有 `bestAmountCalculation`** |
| 内核**实际产出**的金额字段 | `execution.rs:2422-2445` 在**预览 payload 顶层**写 `bestAmountMissing` / `bestAmountMeasured` / `bestAmount` / `bestAmountConfirmed`，阶梯在 `attempts`（`execution.rs:2398`） |
| 发布层的动作 | `review_publish.rs::static_preview()`（`654` 行）**只抄那 8 个字段**，`attempts` 与 `bestAmount*` **一个都没带出来** |
| ⇒ 复核审计结果 | **174 / 174** 条 `amount_state=missing`；只读 healer `missing=174`、`ladderIncomplete=174` |

**这不是「测量没做」，是「测量做了没带出来」。** 系统自己早就知道这件事 ——
`scripts/mainnet_arbitrage_healer.py::kernel_best_amount_calculation()` 的文档字符串原文写着：

> The block used to be dropped on the way into the Manager evidence: only
> ``staticPreview`` and ``runArbitrage`` were forwarded, so every Manager row lost
> the measured amount and the healer reported ``best_amount_missing`` for loops the
> kernel had already measured. The measurement therefore has to be carried across
> explicitly.

healer 只在**它自己**的物化路径上做了补偿（`live_review_evidence()` 里补
`preview["bestAmountCalculation"]`），**Rust 原生发布链路**没有补偿 ⇒ 主网实盘走的
正是后者 ⇒ 每一条复核都像「从未测量过交易金额」。

**为什么只能在 `M-6` 内改**：金额证据的**产生**在 Rust 内核（已正确），
**丢失**发生在 `review_publish.rs::static_preview()` —— 即**复核发布层**本身
（`M-6` = 复核层 · 复核发布 / 金额利润）。改 `M-8`（编排层）只能绕开症状，
不能把丢掉的字段捡回来。

**改了什么**（**最小改动 · 只加不改**）：

| 文件 | 改动 |
|---|---|
| `flashloan/rs-src/src/review_publish.rs` | ① `static_preview()` 的签名增加 `decimals`，输出**增加** `bestAmountCalculation`；② 新增 `best_amount_calculation()`：把内核的 `bestAmount` / `bestAmountConfirmed` / `bestAmountMissing` / `bestAmountMeasured` / `bestAmountSelectionBasis` / `bestAmountZeroReason` **原样**带出，并补 `method` / `requestedAmount` / `requestedSymbol` / `probeCount`；③ 新增 `amount_ladder()`：把内核的 `attempts` **逐档**带出，每档同时给**原始量**（`amountRaw` / `profitRaw`）与**人单位量**（`amount` / `borrowAmount` / `requestedAmount` / `profit` / `profitAmount`，按借款代币 decimals 换算），并保留 `ok` / `profitable` / `status` / `error` / `revertData` / `routeIndex` |
| `flashloan/rs-src/src/review_publish.rs`（测试） | 新增 `the_published_review_carries_the_measured_amount_and_its_ladder`（**T-9**）：断言发布出的复核**带**金额状态**和**两档阶梯，且**测得的亏损仍是测量**（`profit = -1.0`，不是缺失） |

**没改什么**：复核发布门禁（覆盖全集 / 上游代际 / 幂等键）**一字未动**；
金额口径**未放宽**（「测得为零」仍然是「测得的零」，**不是**「缺失」）；
方向池与固定费率约定不变；广播门禁不变。

**缺口如实登记**：`D-014` §0.2 的「M-n 模块表**没有 `rs-src` 复核发布层的归属**」
仍未闭合 —— 本次沿用 `T-复编.01` 先例，把该落点**登记在本任务的 `M-6` 名下**，
**不**改模块表（改模块表要连带重算全部回归范围）。

**回归**：§1.2 的 `M-6`（扩范围）命令 `cargo test --offline --release`（工作目录 `flashloan/rs-src/`）。

---

## 2. 执行步骤

| 步 | 动作 | 完成判据 |
|---:|---|---|
| **1** | 读 `checklist.md`，确认每一条验收项的**当前状态** | 全部条目已知 |
| **2** | 只改**本任务改动模块**内的代码（§1.1） | 改动文件全部落在 §1.1 的落点内 |
| **3** | 跑 §1.2 的**本任务回归范围** | 全绿 |
| **4** | 逐条勾选 `checklist.md` 的 `- [ ]` | 每条都有**实测证据**，不得凭推断勾选 |
| **5** | 记录读数与诚实边界 | 未实测项写「**未测量**」，**不得**用零冒充 |

### 2.1 三条执行纪律

1. **编译 ≠ 测试**：只改接口签名时，`cargo build --offline --workspace` 足以暴露类型破坏 ⇒
   **不必**跑全部 crate 的测试。
2. **跨 crate 的公开接口改动**才需要加跑**调用方**的测试；**内部实现**改动**不需要**。
3. **Python 侧同理**：`flashloan/app_runtime/tests/unit/` 有 **271** 个文件，
   **只跑受影响的那几个**。

---

## 3. 完成定义

- [ ] `checklist.md` 中**全部** `- [ ]` 变为 `- [x]`
- [ ] §1.2 的**全部**回归命令**全绿**
- [ ] 每条已勾选项都有**可复算的实测读数**
- [ ] **未放宽**任何既有门禁（金额 / 容量 / 利润口径 / 幂等键 / 回执关联 / 阈值）
- [ ] **未**因本任务触碰**不属于** §1.1 的模块（若必须触碰 ⇒ **扩范围并重跑**）

---

## 4. 归档

**闭合后必须移出 `plan/tasks/`** —— `plan/tasks/` 只放**未闭合**任务。

```powershell
# 打包四文件 ＋ docs/ → plan/archive/，并登记索引，最后删除本目录
python plan/runtime_evidence/tools/archive_task_20261124.py T-复编.02
# 先预演（不落盘、不删除）：
python plan/runtime_evidence/tools/archive_task_20261124.py T-复编.02 --dry-run
```

工具会自动完成：
1. 四文件 ＋ `docs/` 打包为 `plan/archive/任务归档_T-06_<标题>_<日期>.md`（保留五个小节）；
2. 在 `plan/archive/README.md` §1 **归档索引**登记一行；
3. 在**一级任务** README 的二级清单标记「✅ 已闭合 · 已归档」；
4. **删除** 本目录。

> **归档件内容冻结** —— 更正走新版本 `..._v2.md`，旧版保留。
> 归档区是**受控文档区**，规则见 `plan/README.md` §1.2 与 `plan/archive/README.md`。
> **不得**再在本目录追加新任务 —— 新任务**另建目录**。
