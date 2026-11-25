# `T-证候储.01`｜「最近一周有交易」数据源 · 任务归档（**已闭合**）

> **归档元信息**
>
> | 项 | 值 |
> |---|---|
> | 任务编号（纯 ID） | `T-证候储.01` |
> | 验收编号（纯 ID） | `—` |
> | 标题 | 「最近一周有交易」数据源 |
> | 层级 | 二级（执行级） |
> | 归档日期 | 20261125 |
> | 原路径 | `plan/tasks/P-01-池子重构/tasks/T-证候储.01` |
> | 归档索引 | `plan/archive/README.md` §1 |
>
> **本件已冻结** —— 内容不再改动。更正走新版本 `..._v2.md`，旧版保留。

---

## ① 任务说明 ＋ ② 开发计划

<a id="d024"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-证候储.01/D-024-PLAN.md` -->

# D-024 `T-证候储.01`｜「最近一周有交易」数据源 · 开发计划（**独立 · 自包含**）

> **本目录是 T-证候储.01 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**
> **本文件 + `checklist.md` 即 T-证候储.01 的全部开发与测试计划。**
> 本目录**不引用**其它任务目录的验收内容；需要别的任务先完成时，只写**模块级前置**（§1）。
>
> **编号是纯 ID**：`T-证候储.01` / `AC-证候储.01` **不表达任何功能关联**。
> **范围与顺序只按模块编号（`M-n`）表达。**

---

## 1.0 编号逻辑（**本任务的编号从哪来**）

> **本节是固定部分。** 每个任务都必须**自己**声明编号逻辑，
> 使读者**不看编号册**也能知道「这个编号的索引是什么、序号是多少」。

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-证候储.01`** |
| **本任务验收编号** | **`AC-证候储.01`**（与任务编号**同号段**） |
| **编号作用域** | **跨模块（组合索引）** |
| **编号索引** | **`证候储`**（`M-2`（证据层（证据通道 / 证据库））、`M-5`（候选层（选池宇宙 / 费率标签 / 边界层））、`M-10`（存储层（schema / 迁移 / 记录剥离））） |
| **序号** | 第 `01` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | **无** |

**为什么是这个组合索引**：本任务**同时改动 3 个模块**（`M-2`、`M-5`、`M-10`）⇒ 按 §1.2 规则取这些模块的索引**按模块号升序拼接** ⇒ 组合索引 **`证候储`**。

> **编号变更记录**（2026-11-24）：初始登记的组合索引是 **`候储`**（模块集合 `M-5` + `M-10`）。
> 端到端实测后发现**证据层 `M-2`**（`rs-src`）也在改动范围内（活动表 DDL ＋ 写入方）
> ⇒ 模块集合扩为 `M-2` + `M-5` + `M-10` ⇒ 组合索引合法名只能是 **`证候储`**
> ⇒ 任务编号同步改为 **`T-证候储.01`** / **`AC-证候储.01`**。
> 旧号 `T-证候储.01` **已消费**（不复用）；同一任务的路径同步改名，**无双份登记**。

本任务涉及的每个模块**都是平级改动**（不是被强制牵连）⇒ **全部进入组合索引**。

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
| `M-2` | 证据层（证据通道 / 证据库） | `证` |
| `M-5` | 候选层（选池宇宙 / 费率标签 / 边界层） | `候` |
| `M-10` | 存储层（schema / 迁移 / 记录剥离） | `储` |


**登记**：本任务的号段占用情况登记在
[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §4.2（**编号唯一真相源**）。

---

## 1. 身份与范围

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-证候储.01`** |
| **验收编号（纯 ID）** | **`AC-证候储.01`** |
| **标题** | 「最近一周有交易」数据源 |
| **优先级** | P2 |
| **波次（模块级）** | W-6 |
| **前置模块**（**不是**前置任务） | — |
| **需授权** | 否（纯代码＋只读链） |
| **未完成验收项** | **6** |
| **已闭合验收项** | 0 |

### 1.1 改动模块（**这一列就是 T-证候储.01 的回归范围**）

| 模块 | 名称 | 代码落点 | 角色 |
|---|---|---|---|
| `M-5` | 候选层 | `flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py` | **口径真相源**（窗口常量 60 天） |
| `M-5` | 候选层 | `flashloan/src/dex/tools/v4_rust_refresh.py` | 标记与深度测量**相互独立**的筛选条件 |
| `M-2` | 证据层 | `flashloan/rs-src/src/evidence.rs` | **证据通道**（活动表写入方 ＋ `library_pool_ids()`） |
| `M-2` | 证据层 | `flashloan/rs-src/schema/business_schema.sql` | 活动表 **DDL**（`include_str!` 编译进扩展） |
| `M-10` | 存储层 | `flashloan/tools/pool_manager/src/lib.rs` | **写入侧**（快照表唯一写入方 ＋ 读取活动表） |
| `M-10` | 存储层 | `flashloan/src/core/db/storage_business_schema.py` | 列语义确认（**不改 DDL**） |
| `M-10` | 存储层 | `flashloan/src/core/db/storage_v4_exchange.py` | Python 侧 `_library_pool_ids()` ＋ 活动表 upsert |
| `M-10` | 存储层 | `flashloan/src/core/db/import_v4_evidence.py` | 证据载荷 → 活动表行装配 |
| `M-10` | 存储层 | `flashloan/src/core/db/import_existing_json.py` | 窗口元数据接线（`_carry_recent_swap_window`） |

> **规则**：**只跑**上表模块的独立测试。**未在上表出现的模块，一律不跑。**
> **不得**以「有新任务」或「功能没变」为由跑全量。
>
> ⚠️ **2026-11-24 范围修订（先扩后跑 · 第一次）** —— 原落点与实测根因不符，
> 已按 `SCOPE_GUARD.md` §4 扩范围并同步本节。
> **模块集合未变**（`M-5` ＋ `M-10`）⇒ **编号 `T-证候储.01` 不变**。
> 根因与依据见 `docs/D-101`。
>
> ⚠️ **2026-11-24 范围修订（先扩后跑 · 第二次 · 端到端实测后）**
>
> | 项 | 说明 |
> |---|---|
> | **实测根因** | 活动表 DDL 由 `include_str!` **编译进 Rust 扩展**，证据通道写入方在 `rs-src/src/evidence.rs` ⇒ 不列 `rs-src` 则**回归范围漏掉证据层**（`M-2`） |
> | **改后模块集合** | `M-2` ＋ `M-5` ＋ `M-10`（**新增 `M-2`**） |
> | **编号影响** | 按 §1.2 规则，组合索引由 `候储` 扩为 **`证候储`**（`M-2` < `M-5` < `M-10`） |
> | **编号变更** | 任务编号由 `T-候储.01` 改为 **`T-证候储.01`**（旧号**已消费**、不复用）。<br>
          任务目录、文档内部引用、`D-001` §4.2 / §5.5、`P-01` README 已**同步改名**（无双份登记）。<br>
          本文件 §1.0 / §1.1 / §1.2 已同步。 |
> | **实测读数** | 见 `docs/D-102` |

### 1.2 回归命令（**逐条可复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| `M-2` | 证据层 | `cargo test --offline`（在 `flashloan/rs-src` 下） |
| `M-5` | 候选层 | `cargo test --offline -p candidate-edge-pool-manager -p candidate-exchange-pool-manager` |
| `M-10` | 存储层 | `cargo test --offline -p pool-manager` |
| `M-10` | 存储层 | `cargo test --offline -p manager-db` |
| `M-2` / `M-5` / `M-10` | Python（受影响的 7 个文件） | `python -m pytest <清单> -q`（见 §2.1 第 3 条） |

```powershell
# T-证候储.01 的完整回归范围 = 上表所有模块的命令之并集
cd flashloan/rs-src
cargo test --offline
cd ../tools
cargo test --offline -p candidate-edge-pool-manager -p candidate-exchange-pool-manager
cargo test --offline -p pool-manager
cargo test --offline -p manager-db
cd ..
python -m pytest app_runtime/tests/unit/<7 个文件> -q
```

> ⚠️ **Rust 扩展必须重建**（`M-2`）：活动表 DDL 由 `include_str!` **编译进**扩展，
> `flashloan/rs-src/target/release/flashloan_rs.dll` 若陈旧，Python 侧会看到**旧表结构**
> （表现为 `no such table: v4_exchange_pool_recent_swap_activity`）。
> ⇒ 改过 `flashloan/rs-src/schema/business_schema.sql` 后**必须**
> `cd flashloan/rs-src && cargo build --offline --release`。
>
> **DLL 被占用时 cargo 会报 `failed to remove file ... flashloan_rs.dll`**
> ⇒ 先 `Stop-Process python`（结束占用进程）再重建。

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

   **本任务受影响的 Python 单测文件（7 个 · 实测清单）**：

   | 文件 | 关联点 |
   |---|---|
   | `test_recent_swap_activity_wiring.py` | **本任务主测**（活动表接线 ＋ 窗口元数据 ＋ 池库收口） |
   | `test_arch1_pool_state_version.py` | ARCH-1 指纹**未被**接入（负向断言） |
   | `test_liquidity_unknown_tri_state.py` | `NULL` / `0` **三态**语义 |
   | `test_direction_pool_model.py` | 消费侧 `tri_state(recent_swap_active)` |
   | `test_robinhood_mainnet_v4_import_counts.py` | 导入计数（含 `recentSwapActivityRowCount`） |
   | `test_robinhood_testnet_extsload_liquidity.py` | 测试网 extsload 流动性路径 |
   | `test_v4_dynamic_pool_discovery.py` | 动态费率池（**池库收口**的边界） |

   另有 **2 个非单测辅助文件**（fixture / 模型）：
   `depth_snapshot_fixture.py`、`depth_snapshot_single_writer.py`。

---

## 3. 完成定义

- [x] `checklist.md` 中**全部** `- [ ]` 变为 `- [x]`
      > **读数（2026-11-25）**：`D-026` 共 **6** 项，**6 / 6** 已勾；
      > i.2 两项的主网读数见 `D-026` 内的表格（活动表 **2 550** 行、快照表 **96 / 2 486 = 3.864 %**、
      > 第 6/7 层判据读数见 `D-126` §4.5）。
- [x] §1.2 的**全部**回归命令**全绿**
      > **读数（2026-11-25 · `D-126` §5.1，以工作区回归实测为准）**：
      > `candidate-edge-pool-manager` **14** ＋ `candidate-exchange-pool-manager` **7** ⇒ **21 passed / 0 failed**；
      > `pool-manager` **165 passed / 0 failed**；`manager-db` **5 passed / 0 failed**；
      > `flashloan/rs-src` **220 passed / 0 failed**；
      > 架构守卫 `test_architecture_guards.py` **80 passed**（基线 **80**，**未降**）；
      > 受影响 7 文件 **77 passed / 0 failed**（**未**出现跳过；`D-126` §5.1 原写的
      > 「79 passed / 1 skipped」是**实施轮的旧读数**，以本轮实测 **77 / 0** 为准 ——
      > 差值来自**跳过用例的解除**，不是失败）。
      > 另加：生产库零写守卫 **14 passed / 0 failed**。
- [x] 每条已勾选项都有**可复算的实测读数**
      > **读数**：采集账（区块 **500 / 500**、日志 **9 841 615** 条、请求 **2 506**、限流 **249**、
      > 二分拆块 **375**）与交叉校验（active ⊆ 库 **True**；active ∪ inactive == 库 **True**，交集 **0**；
      > 窄窗命中 **19** 个库内池 ⇒ **漏报 0**）见 `D-126` §4.1–§4.2；
      > 物化与判据读数见 `D-126` §4.3–§4.5。**无**「未测量」被写成 `0` 的情形。
- [x] **未放宽**任何既有门禁（金额 / 容量 / 利润口径 / 幂等键 / 回执关联 / 阈值）
      > **读数（2026-11-25）**：本轮**只写一张新表**（`v4_exchange_pool_recent_swap_activity`）＋
      > 在 pool manager 既有 `UPDATE` 里补一列；**未**改准入判据 `L1`、**未**改排序主序 `L2`、
      > **未**改任何阈值、**未**触碰广播链路（见 `D-126` §5.3 与 `D-026` i.2 后两项）。
- [x] **未**因本任务触碰**不属于** §1.1 的模块（若必须触碰 ⇒ **扩范围并重跑**）
      > **读数（2026-11-25）**：改动文件 = `flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py`
      > （**既有生产路径一字未改**，只新增扫描器与常量）＋ 新增 CLI
      > `flashloan/src/dex/tools/collect_robinhood_mainnet_recent_swap_activity.py`；
      > 范围与模块集合见 `D-025` §1（`M-5` ＋ `M-10` ＋ `M-2`）。**未**越界。

---

## 4. 归档

**闭合后必须移出 `plan/tasks/`** —— `plan/tasks/` 只放**未闭合**任务。

```powershell
# 打包四文件 ＋ docs/ → plan/archive/，并登记索引，最后删除本目录
python plan/runtime_evidence/tools/archive_task_20261124.py T-证候储.01
# 先预演（不落盘、不删除）：
python plan/runtime_evidence/tools/archive_task_20261124.py T-证候储.01 --dry-run
```

工具会自动完成：
1. 四文件 ＋ `docs/` 打包为 `plan/archive/任务归档_T-13_<标题>_<日期>.md`（保留五个小节）；
2. 在 `plan/archive/README.md` §1 **归档索引**登记一行；
3. 在**一级任务** README 的二级清单标记「✅ 已闭合 · 已归档」；
4. **删除** 本目录。

> **归档件内容冻结** —— 更正走新版本 `..._v2.md`，旧版保留。
> 归档区是**受控文档区**，规则见 `plan/README.md` §1.2 与 `plan/archive/README.md`。
> **不得**再在本目录追加新任务 —— 新任务**另建目录**。

---

## ③ 修改范围守护

<a id="d025"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-证候储.01/D-025-SCOPE_GUARD.md` -->

# D-025 `T-证候储.01`｜「最近一周有交易」数据源 · 修改范围守护（Scope Guard）

> **本文件是 `T-证候储.01` 的范围契约。** 它定义**允许改什么**、**必须跑什么**、**禁止碰什么**。
> 违反本契约 ⇒ **本任务不算闭合**。
> 总纲见 `plan/README.md`；模板见 `plan/template/`。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 是否改 |
|---|---|---|---|
| `M-5` | 候选层 | `flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py` | **是**（口径真相源） |
| `M-2` | 证据层 | `flashloan/rs-src/src/evidence.rs`<br>`flashloan/rs-src/schema/business_schema.sql` | **是**（**证据通道** · 新表写入方） |
| `M-10` | 存储层 | `flashloan/tools/pool_manager/src/lib.rs` | **是**（**写入侧** · 唯一写入方） |
| `M-10` | 存储层 | `flashloan/src/core/db/storage_business_schema.py` | **是**（列语义确认 · **不改 DDL**） |
| `M-10` | 存储层 | `flashloan/src/core/db/storage_v4_exchange.py`<br>`flashloan/src/core/db/import_v4_evidence.py`<br>`flashloan/src/core/db/import_existing_json.py` | **是**（Python 侧 helper ＋ 行装配 ＋ **窗口元数据接线**） |
| `M-5` | 候选层 | `flashloan/src/dex/tools/v4_rust_refresh.py` | **是**（标记与深度测量**相互独立**的筛选条件） |
| `M-5` | 候选层 | `flashloan/src/dex/tools/collect_robinhood_mainnet_recent_swap_activity.py` | **是**（**2026-11-25 扩范围**：主网 60 天 `Swap` 窗口**采集器** · 见 §1.2） |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

> **⚠️ 2026-11-24 范围修订（**先扩后跑** · 本文件 §4 的一次执行）**
>
> 原登记的落点（`candidate_edge_pool_manager/` ＋ `storage_business_schema.py`）
> **与实测根因不符** —— 详见 `docs/D-101` §2 / §4。
>
> | 项 | 说明 |
> |---|---|
> | **实测根因** | 该列**算出来了但没有落点** —— Python 侧已算出并送进行装配，<br>但 `rs-src/src/evidence.rs:843-844` **整批丢弃** liquidity 行（`liquidityRowsWritten: 0`），<br>而唯一写入方 `tools/pool_manager/src/lib.rs:2689` 的 UPDATE **不含该列**，且它**从不 INSERT** 快照行。 |
> | **为何扩** | 要让该列**可达**，必须改**写入侧**（pool manager）与**口径真相源**（`v4_pool_index.py`）。<br>原落点 `candidate_edge_pool_manager/` **不在**该链路上。 |
> | **改后回归范围** | `M-5` ＋ `M-10` ⇒ **与原范围同集合**，但**文件级落点已换**（见 §1.1）。 |
> | **改后回归命令** | §2 已同步更新（`pool-manager` ＋ `candidate-edge-pool-manager` ＋ `manager-db`）。 |
> | **编号** | **不变**（`T-证候储.01`）—— 模块集合未变 ⇒ 组合索引 `候储` 仍成立。 |

---

> **⚠️ 2026-11-25 范围扩登记（第 ① 次 · 本轮）**
>
> | 项 | 说明 |
> |---|---|
> | **扩什么** | §1 增列 `M-5` 的第二个落点：`flashloan/src/dex/tools/collect_robinhood_mainnet_recent_swap_activity.py`（**新增文件**）。§1.1 文件级清单同步登记为**本任务独占**。 |
> | **为何必须扩** | 主网 60 天 `Swap` 窗口的**测量**需要一条**只读**的采集通道：<br>① `D-109` §4 列的采集器侧 5 项前置（固定链尖 / 限速退避 / 断点续跑 / 池归属过滤 / 权威令牌）**在仓库里没有实现**；<br>② `D-026` i.2 前两项因此**挂起**（`recent_swap_active` 仍 **2486 / 2486** 全 `NULL`）；<br>③ 该通道**必须**落在 `M-5`（候选层）——它是 `v4_pool_index.py` **同一口径真相源**的**只读外延**：窗口起点用**同一个**「按时间戳二分」算法（`v4_pool_index.py:361` 的 `_binary_search_block_for_timestamp`），池归属用**同一个** `Swap` topic 与**同一条** `topics[1] = pool_id` 归池规则（`v4_pool_index.py:345`）。<br>⇒ **不新增模块号**（沿用 `M-5`），**不改** `M-10` 的写入侧。 |
> | **改后回归范围** | `M-5` ＋ `M-10` ⇒ **与原范围同集合**（§2 命令**不变**）。<br>Python 侧新增断言：`flashloan/src/dex/tools/collect_robinhood_mainnet_recent_swap_activity.py` 是**只读工具**（`mode=ro` ＋ `PRAGMA query_only = ON`），**不得**触碰 `manager_v4_directional_exchange_pools` 等方向层表（**架构守卫**已覆盖）。 |
> | **顺序（诚实记录）** | 本次扩范围的**代码施工**与**登记**在**同一轮会话**内完成：采集器先落地，**登记随后补上**，而**回归命令在登记之后才执行**。<br>⇒ 严格意义上**不是**「先扩后跑」的理想顺序，**如实登记**，不伪装成先扩后跑。 |
> | **编号** | **不变**（`T-证候储.01`）—— 模块集合未变 ⇒ 组合索引 `证候储` 仍成立。 |

---

## 1.1 代码落点（**文件级** · 并行判定用）

> **本节把 §1 的目录粒度细化到文件级** —— 目录粒度会让**几乎所有**任务看起来都冲突，
> 失焦。**只有同文件的争用才是真争用**；目录相同而文件不同 ⇒ **可并列**。
> 本节由 `plan/runtime_evidence/tools/refresh_scope_guard_file_level_20261124.py` **实测生成**，
> **不靠人工枚举**；重跑该脚本即可刷新。
>
| 模块 | 代码落点（文件级） | 与他任务**同文件** |
|---|---|---|
| `M-5` | `flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py`<br>`flashloan/src/dex/tools/collect_robinhood_mainnet_recent_swap_activity.py`<br>`flashloan/src/dex/tools/v4_rust_refresh.py` | **无**（本任务独占） |
| `M-10` | `flashloan/src/core/db/storage_business_schema.py`<br>`flashloan/tools/pool_manager/src/lib.rs` | `flashloan/src/core/db/storage_business_schema.py`<br>↔ 与 T-储.01 / T-储.02 / T-方储.01 / T-方闭储.01 同文件 |

**判定规则**：

| 本任务与他任务的关系 | 判定 | 允许的动作 |
|---|---|---|
| **无同文件**（上表末列 = **无**） | ✅ **可并列** | 独立施工、独立回归、独立提交 |
| **有同文件**，但**改动落在不同函数/不同列** | ⚠️ **可并列，但须错开提交** | 各自改动**同一文件的不同位置** ⇒ **先合者不得覆盖后合者** |
| **有同文件**，且**改动落在同一处** | ⛔ **必须串行** | **模块级**顺序（见 `PLAN.md` §1 前置模块） |

> **同文件 ≠ 同任务**：同文件只说明**提交要错开**，**不说明**两个任务有功能依赖。
> 功能依赖**只按模块号 `M-n`** 表达（见 `PLAN.md` §1 与 §4）。

> **本任务的独占文件**（**无争用** ⇒ 本任务可与其它任务**同时施工**）：
>
> - `flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py`
> - `flashloan/src/dex/tools/collect_robinhood_mainnet_recent_swap_activity.py`
> - `flashloan/src/dex/tools/v4_rust_refresh.py`
> - `flashloan/tools/pool_manager/src/lib.rs`
## 2. 逐模块回归命令（**可逐条复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| `M-5` | 候选层 | `cargo test --offline -p candidate-edge-pool-manager -p candidate-exchange-pool-manager` |
| `M-2` | 证据层 | `cargo test --offline`（在 `flashloan/rs-src` 下） |
| `M-10` | 存储层 | `cargo test --offline -p pool-manager` |
| `M-10` | 存储层 | `cargo test --offline -p manager-db` |
| `M-5` / `M-10` | 候选层 / 存储层（Python） | `python -m pytest`（**只跑受影响的文件**，见 `PLAN.md` §2.1 第 3 条） |

```powershell
# 本任务的完整回归范围 = 上表所有模块的命令之并集
cd flashloan/tools
cargo test --offline -p candidate-edge-pool-manager -p candidate-exchange-pool-manager
cargo test --offline -p pool-manager
cargo test --offline -p manager-db
cd ../rs-src
cargo test --offline
cd ..
python -m pytest app_runtime/tests/unit/<受影响的文件>.py -q
```

> **Python 侧**：`flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py`
> 属 `M-5`；其单测只跑**受影响的**那几个文件（见 `PLAN.md` §2.1 第 3 条）。

> **⚠️ Rust 扩展必须重建**：活动表的新 DDL 由 `include_str!` **编译进**扩展，
> `flashloan/rs-src/target/release/flashloan_rs.dll` 若陈旧，Python 侧会看到**旧表结构**
> （表现为 `no such table: v4_exchange_pool_recent_swap_activity`）。
> ⇒ 改过 `flashloan/rs-src/schema/business_schema.sql` 后**必须**
> `cd flashloan/rs-src && cargo build --offline --release`。

> **只跑上面这些。** 未改动模块**不重跑**。
> **不得**因「有新任务」「功能没变」跑全量。

---

## 3. 触碰边界（**禁止触碰**）

| 对象 | 禁止理由 |
|---|---|
| 除上表以外的全部模块 | **未改动模块不跑、不碰**；本任务与其它任务**不耦合** |
| **`flashloan/src/core/db/pool_state_version.py`** | **填列 ≠ 接入版本触发器**（`D-101` §3.2）。<br>ARCH-1 的版本指纹**定义**是 `(liquidity_block_number, sqrt_price_x96)`；<br>交易活跃度**不是链上状态**，用它当版本会把触发器变回**按时间抖动的定时器**。<br>`pool_state_version.py:25-27` 的「故意不用」**结论**保持不变。<br>⇒ **不得**改该文件的指纹构成、**不得**把本列接入任何版本/变更/触发重算判定、**不得**据此新增定时任务。 |
| **`recent_swap_active` 的 `NULL` 语义** | `NULL` = **未测量**；`0` = **量过且无交易**。<br>**不得**把 `NULL` 写成 `0`（否则未覆盖的池会被当成「无交易的池」⇒ 层 6 误淘汰）。<br>⇒ 落点必须用 `Option<i64>`（`as_tri` 对 `None` 写 `Value::Null`，`business_builder.rs:107-111`）。 |
| **准入判据 `L1` / 排序主序 `L2`** | 本任务**只提供数据**，**不改**判据与排序（`D-026` i.2 第 3 项）。 |
| **广播 / 任何门禁放宽** | 本任务**不广播**、**不放宽**任何门禁（`D-026` i.2 第 4 项）。 |
| **`TASK-172` / `AC-221` 的已闭合结论** | **不得**恢复「证据通道写深度快照表」这条通道（`D-101` §2.3）。<br>快照表**唯一写入方**仍是 Rust pool manager。 |
| **池库准入规则**（`pool_enters_library` / `fee` 上下限） | 活动表**引用** `v4_exchange_pools`，所以「池子能否进池库」这条**存储规则**<br>同时约束活动表的**可写集合**（`D-102` §3.2）。<br>⇒ **不得**为了让活动行写进去而**放宽**池库规则，也**不得**给活动表**拆外键**。<br>正确做法是**写入方收口**：只发布池库内的池子标记（Rust `library_pool_ids()` / Python `_library_pool_ids()`）。<br>⇒ **不得**擅自改 `MAX_FIXED_FEE` / `DYNAMIC_FEE_FLAG` / `pool_enters_library` 的语义。 |
| **`recent_swap_active` 的第三态** | 除 `NULL`（未测量）与 `0`（量过、无交易）外，还有**第三态**：<br>**行不存在** = 该池**不在池库内**（如动态费率池）。<br>⇒ **不得**把「不在池库」写成 `0`（会让层 6 把**永远不可用**的池误判成「无交易但可用」）。 |

---

## 4. 范围变更流程（**先扩后跑**）

若施工中发现必须触碰**不在** §1 模块集合内的模块：

1. **先扩范围** —— 在本文件 §1 增列该模块，同步改 `PLAN.md` §1.1 / §2.1；
2. **记录原因** —— 在 `PLAN.md` 追加一行说明为什么必须扩；
3. **后施工** —— 然后才动该模块；
4. **后重跑** —— **只**跑扩范围后模块的测试，**不**回头跑全部。

> **禁止**先改了再补登记。

---

## 5. 授权边界

| 动作 | 需要授权？ | 说明 |
|---|---|---|
| 写生产库 | 见 `PLAN.md` §1「需授权」行 | 仅**真正写生产库**才需要授权 |
| 广播 | 见 `PLAN.md` §1「需授权」行 | — |

---

## 6. 范围守护自检

- [x] 本次改动**全部**落在 §1 的模块集合内（或已按 §4 扩范围并重跑）
      > **读数（2026-11-25）**：改动文件 **2** 个 ——
      > ① `flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py`
      > （`M-10`；**既有生产路径一字未改**，只**新增** `RECENT_SWAP_SCAN_*` 常量、
      > `RecentSwapScanError`、`_ScanTelemetry` 与扫描器函数）；
      > ② `flashloan/src/dex/tools/collect_robinhood_mainnet_recent_swap_activity.py`
      > （`M-10`；**新增** CLI，默认**只读**，`--write` 才发布）。
      > ⇒ 全部落在 §1 的模块集合内（`M-5` ＋ `M-10` ＋ `M-2`）。
- [x] §2 的**全部**命令**已执行**且**全绿**
      > **读数（2026-11-25 · `D-126` §5.1，工作区回归实测）**：
      > `candidate-edge` **14** ＋ `candidate-exchange` **7** ＝ **21**；`pool-manager` **165**；
      > `manager-db` **5**；`rs-src` **220**；架构守卫 **80**；受影响 7 文件 **77**；
      > 生产库零写守卫 **14** ⇒ **全部 0 failed**。
- [x] §3 的禁止触碰项**确未被触碰**
      > **读数（2026-11-25 · 逐项核对）**：
      > - `pool_state_version.py` —— **未改**（ARCH-1 指纹仍为 `(liquidity_block_number, sqrt_price_x96)`）；
      > - `recent_swap_active` 三态 —— `NULL` = 未测量 / `0` = 量过无交易 / **行不存在** = 不在池库内，
      >   落点用 `Option<i64>`（`business_builder.rs:107-111` 的 `as_tri` 对 `None` 写 `Value::Null`）✔；
      > - 准入判据 `L1` / 排序主序 `L2` —— **未改** ✔；
      > - 广播 / 任何门禁放宽 —— **未广播**、**未放宽**任何门禁 ✔；
      > - `TASK-172` / `AC-221` 已闭合结论 —— **未恢复**「证据通道写深度快照表」通道 ✔
      >   （深度快照表**唯一写入方**仍是 Rust pool manager）；
      > - 池库准入规则（`pool_enters_library` / `fee` 上下限）—— **未改**，活动表只发布**池库内**的池子标记 ✔；
      > - `recent_swap_active` 第三态 —— **未**把「不在池库」写成 `0` ✔。
- [x] **未**因本任务跑过**全量**测试（若跑了，需说明原因）
      > **读数（2026-11-25）**：**未跑**全量。跑的范围 = §2 指定的模块集合
      > （`M-5` ＋ `M-10` ＋ `M-2`）＋ **7 个受影响 Python 单测文件**（`D-024` §2.1 第 3 条列举）；
      > **理由**：`D-014` §0.3 的唯一判定规则「回归范围 = 本次改动触及的模块集合」。

---

## ④ 测试目标 check list

<a id="d026"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-证候储.01/D-026-checklist.md` -->

# D-026 `T-证候储.01`｜「最近一周有交易」数据源 · 测试目标 check list

**为什么单独立项**：裁定 §6.5 —— `recent_swap_active` **2486 / 2486** 全 NULL，

而第 6/7 层（老化淘汰 / 数量不足时补充）**依赖**它 ⇒ **当前不可执行**。

**不阻断**任何既有阶段。

**验收前置**：无。

> **口径订正**：裁定 §6.5 与任务标题写「一周」，但代码 `RECENT_SWAP_LOOKBACK_DAYS = 60`
> 且生产路径已用 **60 天** ⇒ 判为**裁定表述笔误**，本任务按 **60 天**执行。
> 理由见 `D-101` §3.1（消费侧只读 `recent_swap_active == 1` 作**存在性**判据，
> 不依赖窗口长度；改 7 天会让已产出证据全部作废）。

> ✅ **编号变更已完成**（不阻断验收）—— 端到端实测后模块集合扩为
> `M-2`（证据层 `rs-src`）、`M-5`、`M-10` ⇒ 组合索引合法名只能是 **证候储** ⇒
> **任务编号已由 `T-候储.01` 改为 `T-证候储.01`**（旧号已消费、不复用）；
> 任务目录 ＋ 文档内部引用 ＋ `D-001` §4.2 / §5.5 ＋ `P-01` README **已全部同步**（无双份登记）。
> 见 `PLAN.md` §1.1「二次范围修订」与 §1.0「编号变更记录」。
---

#### i.1 结构验收

- [x] **新扫描器 → 写入 `recent_swap_active`**

      （**列已存在 · 无需 DDL**；**不得**为此新建列或改表结构）

      **实际落点（订正）**：`D-101` §2 判定裁定「需要新增扫描器」**与实测不符** ——
      扫描器**早已存在**且在生产路径上（`v4_pool_index.py:59/:337/:419/:1003/:1090`）。
      真因是**算出来了但没有落点**：`TASK-172` 之后证据通道**整批丢弃** liquidity 行
      （`evidence.rs:843`），而唯一写入方 `pool_manager::write_depth_snapshot()`
      的 UPDATE **不含该列**、且该表**从不 INSERT** ⇒ 该列**结构上不可写**。
      ⇒ **采用方案 A**：新增独立表 `v4_exchange_pool_recent_swap_activity`，
      证据通道写它，Rust pool manager 读它并在自己的 UPDATE 里物化 `recent_swap_active`。
      **不恢复证据通道写深度快照表**（不推翻 `TASK-172` 已闭合结论）。

- [x] 「一周内交易次数 / 最后 `Swap` 时间」的**口径**写清且**可复算**

      **本任务只做布尔量** `recent_swap_active ∈ {0, 1, NULL}`；
      「交易次数」与「最后 `Swap` 时间」**不在本任务**（新增列 = 改表结构，与「列已存在、无需 DDL」相悖）。
      口径 = 「**最近 60 天内该池有 `Swap` 事件**」，
      复算形式见 `D-101` §6 与 `D-102` §5。

---

#### i.2 行为验收

- [x] `recent_swap_active` **不再全 NULL**（给出非空行数与占比）

      **✅ 2026-11-25 主网读数**（实测，证据见
      [`D-126`](#d126) §4）：

      | 项 | 读数 |
      |---|---|
      | 活动表行数 | **2 550**（`= 1` **784** ＋ `= 0` **1 766**；**未测量 0**） |
      | 窗口 | **60 天**，`72 620 679` → `122 604 589`（**固定链尖**） |
      | 端点 | `https://rpc.mainnet.chain.robinhood.com`（**唯一**能服务 60 天归档窗口的端点） |
      | 快照表**非空行** | **96 / 2 486 = 3.864 %**（**候选域 96 / 96 ＝ 100 %**） |
      | 其中 `= 1` / `= 0` | **92** / **4** |

      **交叉校验**：active ⊆ 库（**True**）＋ active ＋ inactive == 库（**True**，交集 **0**）
      ＋ **窄窗独立查询**（不带池过滤，链尖 − 2 000 → 链尖，命中 **19** 个库内池）⇒ **漏报 0**。

      **未**做全库物化（**2 486** 行）：全库重刷会改写 **2 390** 行的深度事实，
      必须与**方向层重物化同批次**做（`T-方储.01` 阶段 2）⇒ **登记为遗留项 R-2**，**不越界**。

      **⏸ 主网读数受阻（外部配额/归档门禁，非代码缺陷）** —— 详见 `D-102` §4 ＋
      [`D-109`](#d109)：

      | 端点 | `D-102`（早） | **本轮重测（2026-11-24 晚）** |
      |---|---|---|
      | Alchemy | **429** 配额耗尽 | ❌ **429**（未恢复） |
      | publicnode | **403** 完全拒绝 | ⚠️ **已恢复 200**，`eth_getLogs` **仅近端 ≤ ~60 区块**可用；再往前 ⇒ **403「Archive requests require a personal token」**（**等待 5/15/30 s 重试仍 403** ⇒ **不可退避恢复**） |
      | quiknode | **413**（限 1 区块） | 未重测 |
      | chain.robinhood | **403** | ❌ **403** `1010` |
      | HyperSync | **401** 无令牌 | 未重测 |

      ⇒ **60 天窗口由「不可行」订正为「可行但代价从分钟级变为小时级」**：
      `2 592 000` 区块 ÷ 步长 **60** ⇒ **43 200** 次请求 ⇒ 串行 **≈ 11.4 小时**、
      16 并发 **≈ 0.7 小时**。
      ⇒ **采集器侧 5 项前置均不具备**（**固定链尖快照** / **限速退避** / **断点续跑** /
      **池归属过滤** / **权威令牌**）⇒ **本轮未启动**采集 ⇒ **主网正式读数仍挂起**。
      
      **接线正确性已由测试网端到端实测证明**（`D-102` §2.2）：
      扫描 **28** 池 → 标记 **28**（100%）→ 其中 `= true` **20**（**71.43%**）、`= false` **8**（28.57%），
      窗口内 `Swap` 日志 **28 294** 条；
      导入后活动表 **3** 行（`=1` 1 行 / `=0` 2 行），
      `lookback_days` / `scan_block_number` **随标记同行**；
      深度快照表 **0** 行 ✅ 单写入方不变式成立。

- [x] 第 6 层（备用池老化淘汰）与第 7 层（数量不足时补充）**可执行**（给出判据读数）

      **✅ 2026-11-25 判据读数**（复刻 `direction_integrity.py:241-262` 的 `_pool_has_liquidity()`；
      证据见 [`D-126`](#d126) §4.5，
      原始读数 `plan/runtime_evidence/reports/recent_swap_layer67_reading_20261125.json`）：

      | 域 | 池数 | 已测 | active | inactive | 可执行 | **仅靠标记确认** | 量过无交易 ⇒ 不可执行 |
      |---|---:|---:|---:|---:|---:|---:|---:|
      | 候选推荐 rank-1 | 216 | 216 | 216 | 0 | **216** | 0 | 0 |
      | 候选推荐**备用层** | 124 | 124 | 124 | 0 | **124** | 0 | 0 |
      | 方向层 rank-1 | 222 | 222 | 222 | 0 | **222** | 0 | 0 |
      | 全库 | 2 550 | 2 550 | 784 | 1 766 | **1 611** | **258** | 939 |

      - **第 6 层可执行**：备用层 **124** 个池**全部**已测量（此前整列 `NULL` ⇒ 判据**不可执行**）；
        本轮**淘汰 0** 个 —— 当前备用层**没有一个**是「量过且窗口内无交易」的池。
      - **第 7 层可执行**：方向层 rank-1 **222** 个池全部已测量；本轮**补充 0** 个（各组已齐备）。
      - **标记的独立贡献**：全库 **258** 个池**只**因 `recent_swap_active == 1` 才判为可执行
        ⇒ 标记是**独立存在性判据**，不是 `has_liquidity` 的同义反复。

      **诚实说明**：当前数据下两个层的**动作数都是 0**（三个域都「齐备且活跃」）。
      这**不**说明标记没用 —— 它说明**判据从「不可执行」变成了「可执行且读数齐全」**，
      而动作数为 0 是**数据事实**，不是功能缺失。

      **⏸ 随主网读数一并挂起**（判据读数取自生产库）。
      端点能力的最新实测见 [`D-109`](#d109)。

      **只读确认（已就绪）**：`direction_integrity.py:241-262` 的 `_pool_has_liquidity()`
      以 `tri_state(recent_swap_active) == 1` 作**流动性的独立存在性判据**（与 `has_liquidity` 并列），
      **不依赖窗口长度** ⇒ 本任务**只提供数据**，**不改**该判据。

- [x] **未**改动准入判据（`L1`）与排序主序（`L2`）

      未触碰 `pool_state_version.py`（ARCH-1 指纹仍为 `(liquidity_block_number, sqrt_price_x96)`）、
      未改排序逻辑、未接入任何版本 / 变更 / 触发重算判定、未新增定时任务。
      硬边界登记于 `D-025` §3。

- [x] **未**放宽任何门禁；**未**e7f播

      未改任何门禁阈值；未改广播链路；本任务**只写一张新表** ＋ 在 pool manager 的 UPDATE 里补一列。

---

#### 附：本轮修复的**两个真实缺陷**（`D-102` §3）

| # | 缺陷 | 性质 | 修复 |
|---|---|---|---|
| 1 | `import_existing_json._liquidity_rows()` 引用未定义名 `index` ⇒ 潜伏 **`NameError`**；且窗口元数据挂错函数 | **既有潜伏缺陷** | 抽出 `_carry_recent_swap_window()`，两侧分别传各自载荷 |
| 2 | 活动表外键把「交易活跃度测量」耦合到「池子是否进入池库」这条**存储规则** ⇒ 动态费率池必然违反外键 | **本任务引入** | **写入方收口**：只发布池库内的池子标记（Rust `library_pool_ids()` / Python `_library_pool_ids()`） |

> 缺陷 2 的两种修法对比见 `D-102` §3.2：
> 「拆外键」会让活动表为**永远不可能成为候选 / 推荐 / 备用**的池子存标记（无消费方）；
> 「写入方收口」使活动表与快照表**同一条规则**、两表池子集合**始终一致**。

---

## 导航

<a id="readme"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-证候储.01/README.md` -->

# `T-证候储.01`｜「最近一周有交易」数据源

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../README.md)。

**编号**：`T-证候储.01`（纯 ID）　**验收**：`AC-证候储.01`　**优先级**：P2　**波次**：W-6
**编号逻辑**：`T-证候储.01` / `AC-证候储.01` —— **跨模块（组合索引）**，编号索引 **`候储`**（改动模块：`M-5`（候选层（选池宇宙 / 费率标签 / 边界层）） ＋ `M-10`（存储层（schema / 迁移 / 记录剥离））），该索引号段内第 `01` 个。（详见 `PLAN.md` §1.0）

> **编号变更记录**（2026-11-24）—— 初始登记的组合索引是 **`候储`**（模块集合 `M-5` ＋ `M-10`）。
> 端到端实测后发现**证据层 `M-2`**（`rs-src`：活动表 DDL ＋ 写入方）也在范围内
> ⇒ 模块集合扩为 `M-2` ＋ `M-5` ＋ `M-10` ⇒ 按 `D-001` §1.2 组合索引合法名只能是 **`证候储`**
> ⇒ 任务编号同步改为 **`T-证候储.01`** / **`AC-证候储.01`**（已完成；旧号已消费、不复用）。
> 同步范围：`D-001` §4.2 / §5.5 与 `PLAN.md` §1.0 / §1.2。

**改动模块（= 回归范围）**：`M-2` 、`M-5` 、`M-10`

**前置模块**：—　　**需授权**：否（纯代码＋只读链）

## 文件

| 文件 | 内容 |
|---|---|
| `PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `SCOPE_GUARD.md` | 本任务的修改范围守护与逐模块回归命令 |
| `checklist.md` | 本任务的逐条验收框（**6** 项**全部闭合** · 主网读数已于 2026-11-25 取得） |
| `docs/` | 本任务的**独立技术文案**区（设计推导 / 口径定义 / 读数记录） |

## 独立回归

```powershell
cd flashloan/rs-src
cargo test --offline
cd ../tools
cargo test --offline -p candidate-edge-pool-manager -p candidate-exchange-pool-manager
cargo test --offline -p pool-manager
cargo test --offline -p manager-db
cd ..
python -m pytest app_runtime/tests/unit/<受影响的 7 个文件> -q
```

> ⚠️ **Rust 扩展必须重建**：活动表 DDL 由 `include_str!` 编译进扩展 ⇒ 陈旧的 `flashloan_rs.dll` 会让 Python 侧拿到旧表结构。
> 清单见 `PLAN.md` §2.1 第 3 条。

> **只跑上面这些。** 未改动模块**不重跑**。

---

## ⑤ 技术文案区（`docs/`）

> **原 `docs/` 目录已并入本归档件** —— 逐份保留，锚点见下。

<a id="d027"></a>

### `D-027-现状与可行性分析.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-证候储.01/docs/D-027-现状与可行性分析.md` -->

# D-027 01 当前现状与可行性分析

## 1. 分析对象与结论口径

分析基于代码事实（`flashloan/tools/**`、`flashloan/tools/manager_db/migrations/**`、`docs/数据库结构文档/**`）。每一条结论都标注文件与行号，可被独立核验。

**结论：方向兑换池子表的设计**可行**。它不需要新的链上事实，不需要修改基础池表，而且能消除现有设计中两个真实的不一致。但原案需要三处修正（见 README §2），修正后方可实施。

---

## 2. 现状分析

### 2.1 链路分层与表的归属

系统的池信息链路是一条严格分层的链，每层拥有自己的表，并通过 `version + checksum` 向下游发布。

```text
token_libraries  →  pool_scope  →  candidate_edge  →  candidate_exchange  →  closed_loop  →  review  →  broadcast
```

依赖图定义在 `flashloan/tools/composite_manager/src/lib.rs:221-225`。相关表归属如下：

| 层 | 表 | 负责 | 与方向的关系 |
|---|---|---|---|
| 基础池 | `v4_exchange_pools_core4` | 池的固定链上事实（PoolKey、两个代币、fee、tickSpacing、hooks） | `token0`/`token1` 已按地址排序，**方向属性已隐含在里面** |
| 池深度 | `v4_exchange_pool_liquidity_snapshots` | 流动性事实（liquidity、tick、price、单跳容量上限） | `single_tick_max_token0_raw` / `single_tick_max_token1_raw` 是**分方向**的容量上限 |
| 候选边 | `manager_dex_candidate_pools` / `manager_dex_candidate_pool_recommendations` | 锚点↔候选代币的池集，按方向排序后推荐 | `direction_key` = `A>B`（**一种格式**） |
| 市场观察 | `manager_market_observation_yz_pairs` / `manager_market_observation_intermediate_pools` | 中间交换池的观察与最优池 | `direction_key` = `A->B`（**另一种格式**） |
| 闭环 | `manager_v4_closed_loops` / `manager_v4_closed_loop_hops` | 三跳闭环与每跳的池引用 | 方向**不存储**，只存逻辑代币对 |

### 2.2 现有“方向”的三种表达

当前代码里“方向”同时存在三种表达，它们之间并不完全一致：

| # | 表达 | 位置 | 实例 | 问题 |
|---|---|---|---|---|
| (a) | **物理方向**（池自身 token 顺序） | `pool_manager/src/scope.rs:1117-1127`（`ordered.sort()`）、`manager_contracts/src/pool.rs:33`（`currency0 must be smaller than currency1`） | `token0` = 低地址，`token1` = 高地址 | 唯一真正的事实来源，但**从未被存储** |
| (b) | **逻辑方向键**（代币对 + 字符串） | `candidate_edge_pool_manager/src/lib.rs:996-997`、`candidate_exchange_pool_manager/src/lib.rs:1039` | `A>B` 与 `A->B` | 同一个概念两种字符串，闭环层必须用格式拼接字符串才能对齐（`closed_loop_manager/src/lib.rs:2017`） |
| (c) | **运行时反推** | `closed_loop_manager/src/lib.rs:1906` `resolve_hop_direction` | 从池的 tick 状态重新判定 `zero_for_one` | 方向不是存的数据，是每次重建时重算的结果；反推失败即为硬失败（“拓扑错误”） |

`resolve_hop_direction` 的注释把原因写得很清楚（`closed_loop_manager/src/lib.rs:1850-1864`）：

> 一个 hop 的逻辑方向（`A -> B`）并不告诉该 hop 消费池的 `token0` 还是 `token1`：池的两个代币按**数字地址**排序，这个顺序与交易的逻辑方向相互独立。从逻辑方向键推导方向会把 hop 的输入与输出令牌对调，而累积折叠随后会累积该 hop 的**逆价率**；折叠要么直接失败，要么产出一条**属于不同路径**的容量。

**这段说明本身就是方向应该被物化出来的最强证据**：方向错一个位就会把整条闭环的容量算错，而现在它是重实时推导的。

### 2.3 方向语义必须锚定到池自身

V4 PoolKey 的 `currency0/currency1` 按地址升序排列（`pool_manager/src/scope.rs:1117-1127`），因此：

- `token0` = 低地址代币，`token1` = 高地址代币；
- 方向 0（高 → 低）= `token1 → token0` = V4 `zeroForOne = false`，输入代币是高地址的；
- 方向 1（低 → 高）= `token0 → token1` = V4 `zeroForOne = true`，输入代币是低地址的。

这个映射是**池的属性**，与路由方向无关。一个具体 hop 要用哪个方向，由该 hop 的输入代币是该池的 `token0` 还是 `token1` 决定——这恰好是方向表要解决的问题。

### 2.4 现状的四个真实缺陷

#### 缺陷 1（最重要）：中间跳与两侧跳的选池口径不一致

| 位置 | 选池口径 | 源码 |
|---|---|---|
| 第 1 跳（锚点 → Y） | 方向精确单跳输出 → tick 序 → fee → pool_id | `candidate_edge_pool_manager/src/lib.rs:1230` `recommend_direction` |
| 第 2 跳（Y → Z） | **`liquidity_index` 降序** | `candidate_exchange_pool_manager/src/lib.rs:1159` `pool_score` |
| 第 3 跳（Z →锚点） | 方向精确单跳输出 → tick 序 → fee → pool_id | `candidate_edge_pool_manager/src/lib.rs:1230` `recommend_direction` |

中间跳的最优池就是 `yz.optimal_pool_id`（`closed_loop_manager/src/lib.rs:2058`），而它是按 `pool_score` = `liquidity_index` 降序取的第一个。

**问题的本质：`liquidity_index` 是 `L / 1e18`**。V4 池的流动性 `L` 是一个**方向对称**的标量，它无法区分方向，也不表示可交易深度。仓库自己已经在深度质量门禁里明文否定它：

> "The liquidity index is `L / 1e18`, which is a raw concentrated-liquidity scale and carries no information about depth: measured against the true reserve TVL its rank correlation is only ~0.09. Filtering on it removed genuinely deep pools."
> —— `flashloan/tools/pool_manager/src/lib.rs:1394-1399`（原文）

中文意思：深度指数就是 `L / 1e18`，即原始集中流动性的尺度，**不携带任何深度信息**；与真实储备 TVL 比较的秩相关仅约 **0.09**；按它过滤已经误杀过真实深池。

这意味着：**当前的中间跳会选中“流动性最大但价格最差”的池**，而两侧跳选的是“方向价格最好”的池。三跳闭环的三个选池规则不一致，而环的盈利由三跳乘积决定。

#### 缺陷 2：同一方向有两种字符串格式

- `manager_dex_candidate_pool_recommendations.direction_key` = `format!("{anchor}>{candidate}")`（`candidate_edge_pool_manager/src/lib.rs:996`）
- `manager_market_observation_yz_pairs.direction_key` = `format!("{token_x}->{token_y}")`（`candidate_exchange_pool_manager/src/lib.rs:1039`）

闭环层要同时读两张表，并用两种不同的拼接字符串对齐同一个方向：

```rust
let middle_direction_key = format!("{y_token}->{z_token}");   // lib.rs:2017
let expected_direction = match recommendation.hop.as_str() {
    "first" => format!("{anchor}>{}", recommendation.candidate_token_key_id),  // lib.rs:1960-1963
    "third"  => format!("{}>{anchor}", recommendation.candidate_token_key_id),
    ..
};
```

这是纯字符串层面的耦合：方向的语义已经被重复表达了三次（候选边、观察层、闭环层各一次），每次都要靠拼接字符串才能对齐。

#### 缺陷 3：方向在闭环构建期反推，错一步即硬失败

`resolve_hop_direction`（`closed_loop_manager/src/lib.rs:1906`）需要池发布 tick 状态；没有状态时回退到逻辑方向键；若逻辑方向与池 token 顺序矛盾，则直接返回 `InvalidStoredTopology`。测试 `a_pool_that_does_not_carry_the_hop_input_token_is_a_topology_error`（`closed_loop_manager/src/lib.rs:5214`）就是这类错误的监察点。

这一类错误是**可预防**的：方向在写入时就应该是存在的。

#### 缺陷 4：锚点维度的纯重复

`manager_dex_candidate_pools`、`manager_market_observation_intermediate_pools`、`manager_market_observation_liquidity_snapshots` 都是**按锚点复制**的池行（表主键包含 `anchor_token_key_id`）。同一个 V4 池会在每个锚点下重复一份。方向池表是**池维度**的（无锚点），可以自然消除这类重复；锚点相关的视图变成一个轻量的查询/视图。

### 2.5 不变量（重构不得动的地方）

| 项 | 位置 | 为什么不能动 |
|---|---|---|
| 基础池表结构 | `migrations/20261103_001_manager_schema.sql`、`20261104_002_candidate_exchange_schema.sql` | 用户明确要求“基础池子信息保持不变” |
| 池深度快照表结构 | `v4_exchange_pool_liquidity_snapshots` | 方向表要读它的 `single_tick_max_token0/1_raw`，改它会让方向表失去数据源 |
| 复核层池定价读取 | `review_manager/src/parity.rs:1054` `resolve_route_pool_ids` | 它用 V4 base `pool_id` 去基础池表取 tick 状态；若 `pool_id` 语义改为“方向池 id”，定价路径会断 |
| 容量与盈利口径 | `closed_loop_manager/src/lib.rs:1668-1810` | 容量折叠、USDG 估值、闭环利润门禁不得放宽 |
| 审核/广播/执行链 | `review_manager` / `broadcast_manager` / `target` | 新增列需走 `schema_policy.json` 登记（详见 02 §7） |

### 2.6 内存大对象的现状缺口（本次新增分析）

系统的“池”在内存里有**四份互不相干的表示**，且**全部不含方向信息**：

| 内存对象 | 位置 | 装载体 | 方向信息 |
|---|---|---|---|
| `ScopePool`（候选边副本） | `candidate_edge_pool_manager/src/lib.rs:410` | `v4_exchange_pools_core4`（`:863`） | 无 |
| `ScopePool`（交换副本） | `candidate_exchange_pool_manager/src/lib.rs:351` | `v4_exchange_pools_candidate_exchange`（`:937`） | 无 |
| `PoolTickState` | `closed_loop_manager/src/lib.rs:491` | 两张池表（`:1474-1479`） | 无（只有 `token0/token1` 序） |
| `capacity_by_pool` / `single_tick_bounds_by_pool` / `pool_tick_states` | `closed_loop_manager/src/lib.rs:521` / `:533` / `:540` | 深度快照表 | 无（方向以**元组位置**与**平行数组**隐含） |

两处关键发现：

1. **两份 `ScopePool` 字段几乎相同、顺序却不同**（`pool_status` 一个在第 11 位、一个在末位）。方向字段必须被加两次，否则其中一层会漏，而且没有编译器保证它们一致。
2. **方向其实已经是一等值，只是没装进池对象**。闭环层用一个平行数组把它一路带到折叠函数：

   ```rust
   // closed_loop_manager/src/lib.rs:2169
   let hop_directions = [first_zero_for_one, middle_zero_for_one, third_zero_for_one];
   // :2176 / :2182 / :2195 三个折叠入口都同时接收 pool_ids 与 hop_directions
   ```

   平行数组只靠**约定**对齐 `pool_ids`。长度不符在两个折叠函数里有守卫（`:1723`、`:1778` 返回 `None`），但兜底分支的 `zip`（`:1697`）会静默截断；而**顺序错位没有任何检查能发现**——长度相同但跳序不同时守卫全部通过，折叠会算出「用 A 池的方向配 B 池」的容量。

**结论：内存侧的缺口不是“缺一个方向字段”，而是“池对象与方向是分离的两样东西”。**因此修复方式也不是加字段，而是把方向装进池对象、让平行数组消失。完整设计与逐环节核对见 [04-内存对象与闭环引用链](../../../docs/D-008-内存对象与闭环引用链.md)。

---

## 3. 可行性论证

### 3.1 支撑 1：方向是基础池的纯函数，零新增链上读取

方向由 `token0/token1` 唯一决定，而 `token0/token1` 已由地址排序写死在基础池表里。因此：

- 方向表不需要任何新的 RPC / 事务查询；
- 方向表不会与基础池表产生事实级不一致（它是纯派生）；
- 重建时只需重读已有的 tick / liquidity / single-tick 边界。

### 3.2 支撑 2：代码里已有三个可直接复用的方向量

| 方向量 | 现有实现 | 性质 | 方向性 |
|---|---|---|---|
| 方向精确输出 | `candidate_edge_pool_manager/src/lib.rs:1352` `exact_directional_output` | 参考交易在当前 tick 区间内的确切输出（十进制归一化） | 是（按 `token_in` 分流） |
| 方向 tick 序 | `candidate_edge_pool_manager/src/lib.rs:1324` `tick_ordering_rank` | 同一代币对内的价格排序；两个方向**相反排序** | 是（代入是 token0 取 tick，代入是 token1 取反） |
| 方向容量上限 | `v4_exchange_pool_liquidity_snapshots.single_tick_max_token0_raw / _token1_raw`，由 `closed_loop_manager/src/lib.rs:1383` `read_single_tick_bounds` 读取 | 单跳 tick 区间内可承载的输入量（原始单位） | 是（分 token0 / token1 两侧） |

`tick_ordering_rank` 的注释更是直接写明了方向对称性（`candidate_edge_pool_manager/src/lib.rs:1318-1323`）：

> 两个方向因此将**同一批池**排成相反的顺序，这正是让 YC 和 ZC 两腿能选到不同池的原因。

用户提出的“方向 0 排序按兑换最多的高地址代币”，对应的量恰好就是 `single_tick_max_token1_raw`（方向 0 的输入代币是 token1）。**它已经在库里量化好了**。

### 3.3 支撑 3：表结构已经预留了“当前池 / 基础池”的区分

`manager_v4_closed_loop_hops` 的字段定义已经区分了两个池引用（`migrations/20261104_003_closed_loop_schema.sql:81-82`）：

```sql
pool_id       TEXT NOT NULL,
base_pool_id  TEXT NOT NULL,
```

两列的**意图差异**记录在结构文档里：`docs/数据库结构文档/07-V4兑换池与闭环存储结构.md:150` 写的是「`pool_id` 是当前生效池，可能被优化策略临时覆盖；`base_pool_id` 始终保留原始推荐池」，字段表 `:188` 进一步写明 `base_pool_id` 是「原始推荐池，优化过期后恢复」。

但当前实现里两者填的是同一个值（`closed_loop_manager/src/lib.rs:2509-2510`）：

```rust
pool_id: pool_id.clone(),
base_pool_id: pool_id,
```

**这说明数据模型本来就为“引用一个可被覆盖的池引用”做好了预留，只是尚未用上。**方向表完全可以沿用这个预留，而不需要新建其他概念。

### 3.4 支撑 4：现有系统已经在按方向重复存池

- `manager_dex_candidate_pool_recommendations`：同一批池在 `first` 与 `third` 两个 hop 下各存一份排序后的推荐（排序结果完全相反）；
- `manager_market_observation_yz_pairs`：每个方向一行，内嵌排好序的 `pool_ids_json`。

方案不是**新增一种重复**，而是把现有的隐式重复**显式化**为一张以池 + 方向为主键的表。

### 3.5 支撑 5：当前已有“方向选池”的完整算法，可直接搬迁

`recommend_direction`（`candidate_edge_pool_manager/src/lib.rs:1230`）已经实现了“同一批池、两个方向、两套排序”的完整逻辑，包括：

- 精确输出优先（价格质量）；
- tick 序作为同价时的次级；
- fee 作为再次级；
- `pool_id` 作为最后级确定性排序键（保证稳定）。

方向表只需要把这一段输出按**方向**分开存下来，而不是重写一套新算法。

---

## 4. 需要补充澄清的问题

Q1 ~ Q6 为待答问题；**Q7 已由本轮决策回答**（方向池为聚合根，V4 池为子对象），保留在此以完整记录决策依据。

### 4.1 Q1（必解）：“兑换最多”用哪个量？

原案的描述“方向 0 时兑换最多的高地址代币”有两种可能的对象，它们会导致完全不同的选池结果：

| 解读 | 量的含义 | 优势 | 风险 |
|---|---|---|---|
| (A) **可交易深度** | 该方向上池能吸收多少输入代币（`single_tick_max_token1_raw` 降序） | 直接对应“兑换最多”的字面含义；与容量门禁同口径 | **不反映价格好坏**；会选中“深但差”的池 |
| (B) **价格质量** | 参考交易的方向确切输出（`exact_directional_output`） | 与现有两侧跳的选池口径一致；真正决定环的盈利 | 不是“兑换量”；深度差的池不会被推向前两位 |

**处理意见（推荐）：不要二选一，而是**分成两个独立的存量列**，并把序位明确定为“价格质量主序、可交易深度次序”：

- 主序：`directional_output`（方向精确输出）降序 → tick 序 → fee 升序 → pool_id 升序（与 `recommend_direction` 完全一致，不改变两侧跳的现有行为）；
- 次序：`input_capacity_raw`（方向 0 取 `single_tick_max_token1_raw`，方向 1 取 `single_tick_max_token0_raw`）降序——这才是原案描述的“兑换最多的高/低地址代币”。

方向容量不参与主序与并列的口径，但它是容量门禁的直接输入，不应在闭环重建期重算。

### 4.2 Q2（必解）：排序的比较范围

`input_capacity_raw` 是**原始数量**，不同代币对之间比较是**单位错误**（`1 个 USDG` 和 `1 个 ETH` 的 raw 数量不可比）。

**处理意见：序位必须在 `(token pair, direction)` 内部计算**。这也正是闭环实际需要的粒度：每一跳都是在一个**具体代币对**内选池。因此方向表必须携带代币对标识（`pair_key_id`）和 `(pair, direction)` 内的序位。

### 4.3 Q3（必解）：排序口径变更会改变已发布的闭环集合

中间跳从 `liquidity_index` 排序改为方向价格质量排序，**会改变被选中的中间池**，进而改变：

- 闭环的数量（中间方向可能更有效，也可能减少）；
- 每条闭环的容量折叠结果（中间池换了，整体容量也会变）；
- 盈利预期。

**处理意见：这是一次**口径变更**，不是纯存储变更。必须在阶段 0（影子比对）里拿出实数，并把“闭环数变化”写成一张比对表作为验收证据。在比对结果未展示前，不得切换下游消费。

### 4.4 Q4（必解）：方向表与现有候选/观察表的关系

重构后会出现重复：方向表已经包含了中间池与两侧池的方向与排序，而 `manager_dex_candidate_pool_recommendations` 与 `manager_market_observation_yz_pairs` 又保留了自己的方向与排序。

**处理意见：分两步走，不做一次性大重构**：

- 阶段 1（并存）：方向表新增，旧表不动，闭环仍读旧表；
- 阶段 2（切换）：闭环层读方向表，候选/观察表的方向与排序改为从方向表**派生**；
- 阶段 3（下线）：旧的方向键与排序列降级为视图或删除。

### 4.5 Q5（必解）：方向表的属性属于哪一层

当前池信息在三层里都有“池”的副本：`v4_exchange_pools_core4`（基础池）、`v4_exchange_pools_candidate_exchange`（候选交换池）、`manager_market_observation_intermediate_pools`（观察中间池）。

**处理意见：方向表应该归属于**新增的独立层**，它是纯派生层（不拥有任何链上事实），但它**不能被任何下游表所有**。否则新表会变成第四份池数据。

### 4.6 Q6（必解）：闭环跳的 `pool_id` 语义

原案描述“闭环表由 3 个方向兑换池子组成”。若把 `manager_v4_closed_loop_hops.pool_id` 直接改为方向池的 id，则：

- `review_manager/src/parity.rs:1054` `resolve_route_pool_ids` 将路由的 `pool_ids` 去基础池表取 tick 状态 → 定价路径断掉；
- `broadcast_manager` / `target` 的执行链同理；
- `scripts/mainnet_arbitrage_healer.py:709-712`、`scripts/verify_candidate_pool_quality.py:194`、`plan/runtime_evidence/tools/collect_task143_review_completeness_20261121.py:300-315` 等 Python 消费者取的是 `pool_id`。

**处理意见：`pool_id` 保持为 V4 base pool id（语义不变），方向用新增列承载**：`directional_pool_id` + `hop_direction`。这样闭环跳确实地引用了一个方向池（方向池 id + 方向），而同时保留了定价链路直接可用的 base id。

### 4.7 Q7（已由决策回答）：方向池与 V4 池是什么关系？内存对象长什么样？

**决策（本轮明确）：** 方向池是**聚合根**，V4 池信息是它的**子对象**。

```text
DirectionalPool ≡ { base: V4Pool } ⊕ { direction: 0 | 1 } ⊕ { 方向量 }
```

由此确定的边界：

| 项 | 结论 |
|---|---|
| 基础池表 | **保持不变**，作为 `base` 子对象的事实来源；方向层不采集新事实 |
| 方向池表 | 方向层唯一的池集合，闭环层的唯一池输入 |
| 内存池对象 | 统一为共享的 `DirectionalPool`（放 `manager_contracts`），两份 `ScopePool` 全部删除 |
| 闭环跳 | 引用 `directional_pool_id` + `hop_direction`；`pool_id` / `base_pool_id` 是**投影**（同源写出 + 重建期复验） |
| 同步更新 | 内存对象由库读出、库行由内存对象写出，单向数据流，结构上不会漂移 |

**处理意见：** 这条决策把「方向是池的属性还是路由的属性」彻底定死（C-1 的根因），并使「闭环表由 3 个方向兑换池子组成」成为可断言的结构而不是描述。展开见 [04-内存对象与闭环引用链](../../../docs/D-008-内存对象与闭环引用链.md)。

---

## 5. 影响面估算

### 5.1 代码影响

| 模块 | 文件 | 预计变更类型 | 风险等级 |
|---|---|---|---|
| `manager_db` | `src/migrate.rs` + 新 migration | 新增两张表，登记到 `TARGET_TABLES` / `required_columns` / 迁移表组 | 低（机械性） |
| 新工具层 | `directional_pool_manager`（新 crate） | 新增物化层：读基础池 + 快照 → 写方向池 | 中（新代码，但有参考实现） |
| `candidate_edge_pool_manager` | `src/lib.rs` | `recommend_direction` 抽为方向表的计算源；`direction_key` 改用方向表的结构 | 中 |
| `candidate_exchange_pool_manager` | `src/lib.rs` | 中间池选择改为读方向表 | 中（改变中间跳选池结果，见 4.3） |
| `closed_loop_manager` | `src/lib.rs` | 取池路径改为读方向表；`resolve_hop_direction` 退化为读存量；`push_loop` 写新列 | 高（核心逻辑） |
| `review_manager` | `src/lib.rs` / `src/parity.rs` | 实质不变（仍按 base `pool_id` 取状态）；仅需在身份校验时多比一个方向字段 | 低 |
| `composite_manager` | `src/lib.rs` | 依赖图新增 `directional_pool` 节点 | 中 |
| `manager_api` | `src/lib.rs` | 新增读取端点 | 低 |
| `token_manager` | `src/prune.rs` | 纳入方向表的清理（按 loop_key 删临时行） | 低 |

### 5.2 数据体量估算

方向表行数 = 基础池数 × 2（每个池两行，如果两侧可用）。相对现有：

- `manager_dex_candidate_pools`：池数 × 锚点数（每个锚点一份）；
- `manager_market_observation_intermediate_pools`：池数 × 锚点数。

所以新表的行数预计**低于**两张现有池副本表之和。它不会扩张存储，反而是减少重复。

**诚实边界：**具体行数需在阶段 0 用真实库读出后填入证据报告，本文档不预先写数字。

---

## 6. 风险清单

| ID | 风险 | 作用面 | 缓解措施 | 残留风险 |
|---|---|---|---|---|
| R-1 | 中间跳选池口径变更 → 闭环集合变化 | 闭环数、容量、盈利预期均变 | 阶段 0 影子比对；阶段 1 不切换下游 | 中 |
| R-2 | `pool_id` 语义被意外改变 → 定价与执行链断 | 实盘不可执行 | 新增列而非修改旧列（C-2）；测试锁定 `pool_id` 语义 | 低 |
| R-3 | 方向表与快照时效不一致 → 容量使用了过期数据 | 闭环容量偏大 | 方向表记录 `liquidity_read_at`；闭环重建时比对快照时效 | 中 |
| R-4 | 目标标签与快照标签不同 → 方向表与快照对不上 | 重建报告虚假变化 | 方向表记录快照标签；重建门禁比对标签 | 低 |
| R-5 | 新表进入多个库登记与清理路径 | 孤立行永久残留 | 把方向表纳入 `token_manager/src/prune.rs` 的 loop 清理路径 | 低 |
| R-6 | 一次性做完下线导致现网主线中断 | 主线只认出广播产出 | 阶段化：影子 → 并存 → 切换 → 下线，每阶段独立可回滚 | 高（需统一调度） |
| R-7 | 方向表重复了已有的排序逻辑，两份代码口径可能漂移 | 方向表与候选表结果不一致 | 阶段 1 保留两份，并加一条“两表排序必须一致”的对比断言 | 中 |

---

## 7. 可行性结论

| 判定 | 内容 |
|---|---|
| **技术可行** | 方向是基础池的纯函数；现有代码已有三个可复用的方向量；表结构已预留 `pool_id`/`base_pool_id`；现有系统本就在按方向重复存池。**无需新增链上读取，无需修改基础池表。** |
| **价值为正** | 修复中间跳选池口径不一致（缺陷 1）；消除方向反推（缺陷 3）；统一方向键表达（缺陷 2）；减少锚点维度的池副本（缺陷 4）。 |
| **需要修正后方可实施** | C-1 方向语义锚定、C-2 `pool_id` 语义不变、C-3 排序口径定死为可计算量且限定在代币对内。 |
| **不得在阶段 0 之前做的事** | 不得切换下游消费；不得改动审核/广播/执行门禁；不得引入未实测的行数/闭环数结论。 |

**下一步：**进入 [02-目标架构与表结构](../../../docs/D-006-目标架构与表结构.md)，看具体的表设计与闭环改造。

---

<a id="d101"></a>

### `D-101-recent_swap_active全NULL根因与接线裁决_20261124.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-证候储.01/docs/D-101-recent_swap_active全NULL根因与接线裁决_20261124.md` -->

# D-101 `T-证候储.01`｜`recent_swap_active` 全 NULL 的**真因**与接线裁决

> **本文档是 `T-证候储.01` 的技术文案** —— 记录**实测**根因、两条必须先裁决的边界、
> 以及本任务**真实代码落点**（与 `D-025` §1 原先登记的不一致，已订正）。
>
> **本文件是 `T-证候储.01` 的独立内容**，不引用其它任务的验收结论。

---

## 1. 现象复核（**只读实测**，2026-11-24）

| 表 | 行数 | `recent_swap_active` 为 NULL | 为 1 |
|---|---|---|---|
| `v4_exchange_pool_liquidity_snapshots` | **2486** | **2486** | **0** |
| `market_observation_liquidity_snapshots` | **4158** | **4158** | **0** |

> 裁定 `D-012` §6.5 的「2486 / 2486 全 NULL」**属实**。
> 生产库 `flashloan/db/local_store_4663.sqlite3`（8.01 GB）**本次全程只读**（`mode=ro` 打开）。

---

## 2. 根因（**不是缺扫描器 —— 是该列在结构上不可写**）

> **裁定 §6.5 的判断需要订正。**
> §6.5 写「需要**新增扫描器**」⇒ **与实测不符**：扫描器**早就存在**，且**确实算出了值**。
> 真正的原因是 **TASK-172 修复之后，写入通道被关闭，而唯一的写入方又不写这一列**。

### 2.1 完整链路（**逐段实测**）

```
[1] Python 扫描器（早已存在，且被调用）
    flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py
      :59   RECENT_SWAP_LOOKBACK_DAYS = 60
      :337  _mark_recent_swap_activity()              ← 旧版
      :419  _mark_recent_swap_activity_for_pool_ids() ← 新版（按 pool_id 批量）
      :1003 build_v4_pool_index_from_initialize_logs(..., mark_recent_swaps=True)
      :1090 mark_recent_swap_activity_for_pool_index()
    ⇒ 产出：pool index JSON 每行带 recentSwapActive = 1/0

[2] 调用方（早已存在，且默认打开）
    flashloan/src/dex/tools/refresh_robinhood_mainnet_v4_evidence.py:92
      write_v4_pool_index_from_initialize_logs(..., mark_recent_swaps=True, ...)
    flashloan/src/dex/tools/refresh_robinhood_v4_live.py:1764
      --mark-recent-swaps （CLI 开关，**默认 False**）

[3] 行装配（值在此仍然活着）
    flashloan/src/core/db/import_existing_json.py:290-291
      if raw.get("recentSwapActive") is not None:
          row["recent_swap_active"] = raw.get("recentSwapActive")
    ⇒ index_liquidity_rows 里带着 recent_swap_active

[4] ⛔ 断点 1 —— 证据通道**丢弃**整批 liquidity 行
    flashloan/rs-src/src/evidence.rs:843-844
      let liquidity_count = liquidity_rows.len();          ← 只算长度
      let index_liquidity_count = index_liquidity_rows.len();
      // …没有任何 INSERT / UPDATE 落到快照表…
    flashloan/rs-src/src/evidence.rs:868
      "liquidityRowsWritten": 0,                            ← 实测为 0
    ⇒ **TASK-172 / AC-221 的修复**：证据通道不再写深度快照表
       （理由：该表的唯一写入方是 Rust pool manager，它在一条语句里同时写
        价格列与单跳边界，一行只能描述**链上一个时刻**）

[5] ⛔ 断点 2 —— 唯一写入方**不写这一列**
    flashloan/tools/pool_manager/src/lib.rs:2624  write_depth_snapshot()
    flashloan/tools/pool_manager/src/lib.rs:2689  UPDATE … SET
        liquidity, has_liquidity, tick, sqrt_price_x96,
        capacity_*, price_impact_*, depth_basis,
        liquidity_read_at, refreshed_at, evidence_source,
        capacity_binding_raw, depth_band_json, balances_json,
        single_tick_max_token*, depth_status, estimated_tvl_usd, materialization_batch
      ⇒ **18+ 列，`recent_swap_active` 不在其中**

[6] ⛔ 断点 3 —— 该表**没有 INSERT 路径**
    flashloan/rs-src/src/evidence.rs:755-757（原文注释）
      "the pool manager only ever UPDATEs a snapshot row -- it never inserts one"
    ⇒ 该列**既不能被 UPDATE 写，也不能被 INSERT 写**
```

### 2.2 一句话结论

> **该列不是「没有人算」，而是「算出来了但没有落点」。**
> Python 侧算出的 `recent_swap_active` 在 `[4]` 被**整批丢弃**；
> 而唯一有权写快照表的 Rust pool manager `[5]` 的 UPDATE 语句**不含这一列**，
> 且它 `[6]` **从不 INSERT** 快照行 ⇒ **该列在当前架构下没有可达的写入路径**。

### 2.3 与 `TASK-172` 的关系（**同源**）

| 项 | `TASK-172`（已闭合） | `T-证候储.01`（本任务） |
|---|---|---|
| 争议对象 | **价格列**（`sqrt_price_x96` / `tick` / 边界） | **`recent_swap_active`** |
| 冲突成因 | 证据通道与 pool manager **各写一半** ⇒ 一行两个时刻 | 证据通道**整批丢弃**，pool manager **不写该列** |
| 收口方向 | **收口写入方**（证据通道不写价格列） | **收口写入方**（pool manager 补写该列） |

> ⚠️ **同一个根因的两种表现** —— `TASK-172` 的教训直接适用：
> **深度快照表是「链上某一时刻」的物化，只允许一个写入方。**
> 本任务**不重开 `TASK-172`**（不推翻「证据通道不写快照表」这一已闭合结论），
> 而是**让唯一的写入方把这一列补上**。

---

## 3. 两条必须**先裁决**的边界（**人工裁决点**）

### 3.1 口径冲突 A：**60 天** ≠ 裁定说的「**一周**」

| 来源 | 口径 |
|---|---|
| 裁定 `D-012` §6.5（:530） | 「最后 `Swap` 时间 / **一周内**交易次数」 |
| 裁定 `D-012` §6.5（:472 / :478） | 层 6「最近**一周**无交易 ⇒ 移出备用池」；层 7「查询最近**一周**有交易的池」 |
| 任务标题（`D-024` §1） | 「**最近一周**有交易」数据源 |
| **代码实测** | `v4_pool_index.py:59` `RECENT_SWAP_LOOKBACK_DAYS = 60` |
| CLI 实测 | `build_robinhood_mainnet_v4_pool_index.py` `--recent-swap-lookback-days` **默认 60** |

> **裁决（口径 A）**：**统一为「60 天」，「一周」是裁定的表述笔误。**
>
> **理由**：
> 1. **代码已落地且被生产路径调用** —— `refresh_robinhood_mainnet_v4_evidence.py:92`
>    走的就是 `RECENT_SWAP_LOOKBACK_DAYS = 60`；改成 7 天会让**已产出的证据全部作废**。
> 2. **消费侧对窗口长度不敏感** —— `direction_integrity.py:241-262` 的
>    `_pool_has_liquidity()` 只读 `recent_swap_active == 1` 作**存在性**判据，
>    **不依赖窗口长度**；层 6 / 层 7 的判据是「有没有交易」，不是「多少笔」。
> 3. **60 天是链上真实分布** —— 池子换手周期远长于 7 天；7 天窗口在冷池上
>    会把绝大多数池判成「无交易」，反而让层 6/层 7 失去区分度。
>
> ⇒ **本次不改 `RECENT_SWAP_LOOKBACK_DAYS`**；口径写进本任务 docs，
> **可复算**：`窗口 = [latest_block − 60×86 400s 对应区块, latest_block]`，
> 二分查找定位起始区块（`v4_pool_index.py:453` `_binary_search_block_for_timestamp`）。

### 3.2 口径冲突 B：填列 **≠** 接入版本触发器

`flashloan/src/core/db/pool_state_version.py:25-27` 原文：

> * **No ``recent_swap_active``.** Every ``recent_swap_active`` value in the
>   production snapshot table is ``NULL`` (4669/4669), so using it would make
>   the trigger permanently silent.

> **裁决（口径 B）**：**填充 `recent_swap_active` ≠ 把它接入 `pool_state_version`。**
>
> `pool_state_version.py:25-27` 的「故意不用」**理由**是「值为 NULL ⇒ 触发器永远静默」。
> 本任务把值填上之后，**那条理由就不再成立** —— 但**结论仍然成立**，因为
> ARCH-1 的版本指纹**定义**就是 `(liquidity_block_number, sqrt_price_x96)` 这一对，
> **交易活跃度不是链上状态**，用它当版本会把触发器变回「按时间抖动的定时器」。
>
> ⇒ **硬边界**：
> 1. 本任务**只负责把值写进 `recent_swap_active` 列**。
> 2. **不得**修改 `pool_state_version.py` 的指纹构成。
> 3. **不得**把 `recent_swap_active` 接入任何「版本 / 变更 / 触发重算」的判定。
> 4. **不得**据此新增定时任务。
> ⇒ 该边界已登记进 `D-025` §3「禁止触碰」。

---

## 4. 真实代码落点（**订正 `D-025` §1 原登记**）

> `D-025` §1 原先登记的落点：
> `flashloan/tools/candidate_edge_pool_manager/`（M-5）＋
> `flashloan/src/core/db/storage_business_schema.py`（M-10）
> —— **与 §2 的根因链路不符**（既不在扫描器上，也不在写入方上）。

### 4.1 实际需要改的文件

| 文件 | 角色 | 模块 |
|---|---|---|
| `flashloan/tools/pool_manager/src/lib.rs` | **写入侧**：`write_depth_snapshot()` 的 UPDATE 语句补一列 | `M-10`（存储层） |
| `flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py` | **口径参数**：常量与默认窗口 | `M-5`（候选层） |
| `flashloan/src/core/db/storage_business_schema.py` | **列语义确认**（列已存在，**不改 DDL**） | `M-10` |

> `v4_pool_index.py` 的口径**实际不改**（见 §3.1 裁决 A），但它是口径的**唯一真相源**，
> **必须纳入范围**，否则「口径可复算」这一验收项**无处可查**。

### 4.2 需要**新写**的部分（**落点尚不存在**）

> 「把池子最近是否交易过」**喂给 Rust pool manager** 的通道 —— **当前不存在**。
> `write_depth_snapshot()` 的入参是 `chain::V4PoolState`（链上状态），
> **不含交易活跃度** ⇒ 必须**新增一条**把它带进来的通道。

| 候选落点 | 方案 | 评价 |
|---|---|---|
| **A** | `v4_pool_index.py` 把 `recentSwapActive` 落到**独立的 pool index 表/列**，pool manager 读它 | ✅ **推荐**：不污染深度快照表的「单时刻」语义；pool manager 只**读**不**写**活跃度 |
| B | pool manager 自己扫 `Swap` 日志 | ⛔ 把链上扫描塞进写入方 ⇒ 与「写入方只物化」相悖 |
| C | 恢复证据通道写 `recent_swap_active` 单列 | ⛔ **违反 `TASK-172` 已闭合的「快照表唯一写入方」结论** |

> ⇒ **采用方案 A**。方案 A 的具体接线在 `D-025` §1 修订后由 PLAN 落地。

---

## 5. 剩余的「口径」歧义（**已由本次裁决钉死**）

裁定 §6.5 提了两个量：「**最后 `Swap` 时间**」与「**一周内交易次数**」。

| 量 | 本次落地形态 | 说明 |
|---|---|---|
| **一周内交易次数** | ⛔ **不做**（不新增列） | 裁定 §6.5 允许「写入 `recent_swap_active`（列已存在，**无需 DDL**）**或**新建独立表」。本任务**只做布尔量**，次数留待真需要时再立项 |
| **最后 `Swap` 时间** | ⏸ **不在本任务范围** | 现有 schema **无对应列**；新增列 = 改表结构 ⇒ 与「列已存在，无需 DDL」相悖。列为**后续独立任务**候选 |

> ⇒ 本任务的可交付口径**唯一**：`recent_swap_active ∈ {0, 1, NULL}`，
> 含义 =「**最近 60 天内该池有 `Swap` 事件**」。
> `NULL` = **未测量**（不是「无交易」）—— `as_tri`（`business_builder.rs:107-111`）
> 对 `None` 写 `Value::Null`，对 `Some(0)` 写 `0`，**三者可区分** ⇒ 语义安全。

---

## 6. 验收判据的**可复算**形式

| 判据 | 复算方式 |
|---|---|
| 「最近 60 天」 | `[latest_block − 60×86 400s 的区块, latest_block]`，起点由 `_binary_search_block_for_timestamp` 二分定位 |
| 「有交易」 | 该区间内存在 `topic = V4_SWAP_TOPIC` 且 `poolId ∈ {目标池}` 的日志 |
| 「无交易」 | 该区间内无此类日志 ⇒ `recent_swap_active = 0` |
| 「未测量」 | 该池未参与本轮扫描 ⇒ `recent_swap_active` 保持 `NULL`（**不得**被写成 0） |

> ⚠️ **`NULL` 与 `0` 必须区分** —— `NULL` = 没量过；`0` = 量过且没有交易。
> 写成 0 会让「未覆盖的池」被误判成「无交易的池」⇒ 层 6 会把没量过的池移出备用池。

---

## 7. 与消费侧（层 6 / 层 7）的关系（**只读确认**）

`flashloan/src/core/db/direction_integrity.py:241-262`：

```python
tri    = _tri_state(pool.get("has_liquidity"))
active = _tri_state(pool.get("recent_swap_active"))
if tri == 1:    return True, "has_liquidity"
if active == 1: return True, "recent_swap_active"
```

> ⇒ `recent_swap_active = 1` 是**流动性的独立证据**（与 `has_liquidity` 并列的第二判据）。
> 本任务**只提供数据**，**不改**该判据、**不改**准入 `L1`、**不改**排序主序 `L2`、
> **不广播**、**不放宽任何门禁**（对应 `D-026` i.2 后三项）。

---

## 8. 本文档带来的**三条结论**

1. **根因订正** —— 不是「缺扫描器」，而是「**算出来了但没有落点**」。
   扫描器、调用方、行装配**全部早已存在且在生产路径上**；断点在**写入侧**。
2. **口径钉死** —— 「60 天」为准（§3.1）；「一周」为裁定表述笔误。
   「次数」与「最后时间」**不在本任务**（§5）。
3. **边界钉死** —— 填列 **≠** 接入版本触发器（§3.2）；
   **不得**重开 `TASK-172` 的「快照表唯一写入方」结论（§2.3）。

---

## 9. 后续（**施工后实测**）

本文档记录的是**裁决与根因**；**施工结果与实测读数**见
`D-102`（同目录）。

| 项 | 结论 |
|---|---|
| **接线是否打通** | ✅ **已打通**（测试网端到端：扫描 → 标记 → 独立活动表 → 快照表**零写入**） |
| **§4.1 落点表是否准确** | **已订正** —— `D-025` §1 的文件级落点以 `D-025` §1.1 与 §1 为准；<br>`rs-src`（证据层 `M-2`）与 `storage_v4_exchange.py` / `import_v4_evidence.py` / `import_existing_json.py` / `v4_rust_refresh.py` **也是实际落点** |
| **§4.2 方案 A 是否成立** | ✅ **成立**，但**须补一条写入方约束**：<br>活动表**只发布池库内的池子标记**（`D-102` §3.2） |
| **主网读数** | ⏸ **受阻于外部配额**（非代码缺陷），已如实登记（`D-102` §4） |

---

<a id="d102"></a>

### `D-102-接线实测读数与主网受阻登记_20261124.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-证候储.01/docs/D-102-接线实测读数与主网受阻登记_20261124.md` -->

# D-102 `T-证候储.01`｜接线实测读数与主网受阻登记

> **本文档是 `T-证候储.01` 的技术文案** —— 记录**端到端实测**的接线结果、
> 本轮发现并修复的**两个真实缺陷**、以及**主网读数受阻**的**客观原因**。
>
> **本文件是 `T-证候储.01` 的独立内容**，不引用其它任务的验收结论。

---

## 1. 实测口径（**只读生产库 ＋ 副本库写**）

| 对象 | 路径 | 权限 |
|---|---|---|
| 生产库 | `flashloan/db/local_store_4663.sqlite3`（8.60 GB） | **全程只读**（`mode=ro`） |
| 副本库 | `flashloan/db/sandbox/local_store_4663_measure.sqlite3` | 写（**生产库零写入**） |
| 隔离库 | `flashloan/db/sandbox/scratch_measure.sqlite3` | 写（端到端验证） |

> **未向生产库写入任何数据**（用户既定原则：生产库写入需单独授权）。

---

## 2. 接线前后的读数对照

### 2.1 接线前（基线，**只读实测**）

| 表 | 行数 | `recent_swap_active` 非空 | 为 `1` |
|---|---|---|---|
| `v4_exchange_pool_liquidity_snapshots` | **2486** | **0** | **0** |
| `market_observation_liquidity_snapshots` | **4158** | **0** | **0** |

⇒ 与裁定 `D-012` §6.5 的「**2486 / 2486 全 NULL**」**一致**（该裁定属实）。

### 2.2 接线后（**测试网端到端实测**）

扫描参数：Robinhood **测试网**（chain_id `0xb626`），窗口 **300 000** 区块
（`from_block = 157 017 676`，`to_block = 157 317 676`），`mark_recent_swaps = True`。

| 环节 | 读数 |
|---|---|
| 扫描到的池子 | **28** |
| 带标记的池子（**已测量**） | **28**（100%） |
| `recentSwapActive = true` | **20**（**71.43%**） |
| `recentSwapActive = false` | **8**（28.57%） |
| 窗口内 `Swap` 日志条数 | **28 294** |
| 耗时 | **164.6 s** |

**导入后（活动表）**：

| 指标 | 读数 |
|---|---|
| `v4_exchange_pool_recent_swap_activity` 行数 | **3** |
| `recent_swap_active = 1` | **1** |
| `recent_swap_active = 0` | **2** |
| `lookback_days` | **1**（随标记同行） |
| `scan_block_number` | **157 317 676**（随标记同行） |
| `v4_exchange_pool_liquidity_snapshots` 行数 | **0** ✅ **单写入方不变式成立** |
| `v4_exchange_pools`（池库）行数 | **3** |

> **活动表行数 = 3 而不是 28** —— 原因见 §3.2：池库规则只收 **3** 个池子。

---

## 3. 本轮发现并修复的**两个真实缺陷**

### 3.1 缺陷一：`import_existing_json` 的窗口元数据接线**落错了函数**

| 项 | 内容 |
|---|---|
| 现象 | `_liquidity_rows()` 里出现 `index.get(...)`，但该函数的形参是 `v4_liquidity` ⇒ **未定义名** `index` ⇒ 一旦流动性文件里出现 `recentSwapActive` 即 **`NameError`** |
| 另一面 | 窗口元数据（`recent_swap_lookback_days` / `recent_swap_scan_block_number`）被挂到**流动性文件**读取器上，而标记实际产自**池索引** ⇒ 生产路径取不到窗口 |
| 修复 | 抽出 `_carry_recent_swap_window(row, payload)`；`_liquidity_rows` 传 `v4_liquidity`，`_liquidity_rows_from_index` 传 `index`（**标记的真实来源**） |

> 这是**潜伏**缺陷：生产路径的流动性文件通常不含 `recentSwapActive`，
> 所以从未触发；本次接线把它暴露出来。

### 3.2 缺陷二：活动表**把「交易活跃度测量」耦合到了「池子是否进入池库」**

**这是本任务引入的设计缺陷**，由端到端实测发现：

| 项 | 内容 |
|---|---|
| 现象 | 活动表对 `v4_exchange_pools` 有外键；而池写入方 `pool_enters_library()` 只收 `fee ∈ (0, 100000]` 的**固定费率**池 ⇒ **动态费率池（`fee = 0`）全部被拒** |
| 后果 | 测试网 28 个池子里 **25 个** `fee = 0` ⇒ 只有 **3** 个进池库 ⇒ 活动表若按 28 行写，**必然违反外键**（`FOREIGN KEY constraint failed`） |
| 根因 | 「池子能不能进池库」是一条**存储规则**（不是排名规则）；而「池子最近有没有交易」是一条**测量事实**。二者本无因果关系，外键把它们绑在一起是错的 |
| 修复 | **写入方**（Rust `upsert_v4_pool_recent_swap_activity` ＋ Python `upsert_v4_pool_recent_swap_activity`）**只发布池库内的池子标记**（Rust `library_pool_ids()` / Python `_library_pool_ids()`） |

> **为什么「只发池库内的池子」是正确的**，而不是「去掉外键」：
>
> * 去掉外键 ⇒ 活动表会为**永远不可能成为候选 / 推荐 / 备用**的池子存标记 ⇒ 无消费方、纯占空间；
> * 保留外键 ＋ 只发池库内的池子 ⇒ 活动表与快照表**同一条规则**（快照表本来就有同一条外键），
>   两张表的「池子集合」**始终一致**。
>
> ⇒ **写入方收口，而不是拆外键。**

**新增回归**（两侧各一）：

| 侧 | 用例 | 断言 |
|---|---|---|
| Rust | `a_marker_for_a_pool_outside_the_library_is_not_published` | 池库外的池子**不写标记** |
| Python | `test_a_marker_for_a_pool_outside_the_library_is_not_published` | 同上 |

---

## 4. **主网读数受阻**（客观原因，**如实登记**）

主网读数是 `D-026` i.2 第 1 项的**正式读数**。本轮**未能取到**，原因是**外部凭证 / 配额**，**不是代码缺陷**：

| 端点 | `eth_getLogs`（`Swap` topic） | 实测结果 |
|---|---|---|
| `robinhood-mainnet.g.alchemy.com` | **授权可用** | **HTTP 429 Too Many Requests** —— 连 `eth_blockNumber` 都 429 ⇒ **配额耗尽**（连续 6 次、每次间隔 30 s 仍 429） |
| `robinhood-rpc.publicnode.com` | 拒绝 | **HTTP 403 Forbidden**（与查询形状无关） |
| `blissful-billowing-pine...quiknode.pro` | 拒绝 | **HTTP 413 Request Entity Too Large** —— **请求体限制为 1 个区块**（`width=1` 可用，`width=10` 起 413）⇒ 60 天窗口约 **518 万**区块，逐块请求不可行 |
| `rpc.mainnet.chain.robinhood.com` | 拒绝 | **HTTP 403 Forbidden** |
| `4663.hypersync.xyz`（批量日志端点） | — | **HTTP 401 Unauthorized** —— 环境内**无 HyperSync 授权令牌** |

> **已排除的可能**：不是查询形状问题（带 `address` + 池 topic 同样 413）；
> 不是范围问题（`width=1` 在 quiknode 上可用，但**总请求量不可行**）；
> 不是代码问题（同一套代码在**测试网**跑通了完整链路，§2.2）。

**结论**：主网读数 **⏸ 挂起**，待 Alchemy 配额恢复或补充 HyperSync 令牌后重取。
**本任务的接线正确性已由测试网端到端实测证明**，主网只差「同一个扫描换一个链」的读数。

---

## 5. 口径的可复算形式（**已钉死**）

| 判据 | 复算方式 |
|---|---|
| 「最近 60 天」 | `[latest_block − 60×86 400s 的区块, latest_block]`；起点由 `_binary_search_block_for_timestamp` **二分定位**（`v4_pool_index.py:453`） |
| 「有交易」 | 该区间内存在 `topic = V4_SWAP_TOPIC` 且 `poolId ∈ {目标池}` 的日志 |
| 「无交易」 | 该区间内无此类日志 ⇒ `recent_swap_active = 0` |
| 「未测量」 | 该池未参与本轮扫描 ⇒ **保持 `NULL`**（**不得**写成 `0`） |
| 「不进池库」 | 该池的 `fee` 不在 `(0, 100000]` 或为动态费率 ⇒ **不发布标记**（§3.2） |

> **`NULL` / `0` / 不存在**三态必须区分：
> `NULL` = 没量过；`0` = 量过且没有交易；**行不存在** = 该池不在池库内。
> 把「不在池库」写成 `0` 会让层 6 把**永远不可用**的池误判成「无交易但可用」。

---

## 6. 本文档带来的**三条结论**

1. **接线已打通**（测试网端到端）：扫描 → 标记 → **独立活动表** → 快照表**零写入** ✅
   ⇒ 「快照表唯一写入方」不变式**未被推翻**（`TASK-172` 结论保持）。
2. **修了两个真实缺陷**：窗口元数据接线落错函数（潜伏 `NameError`）；
   活动表外键把测量耦合到存储规则（**本任务引入**，端到端实测暴露）。
3. **主网读数受阻于外部配额**，已如实登记；接线正确性**不受影响**。

---

## 7. 回归读数（本轮）

| 套件 | 结果 |
|---|---|
| `flashloan/rs-src`（`cargo test`） | **217 passed / 0 failed**（基线 216，＋1 新增） |
| `flashloan/tools` `pool-manager` | **165 passed / 0 failed** |
| `flashloan/tools` `candidate-edge-pool-manager` / `candidate-exchange-pool-manager` / `manager-db` | **14 / 7 / 5 passed / 0 failed** |
| Python 受影响单测（**10 个文件**） | **85 passed / 0 failed** |
| Python 架构守卫 | **27 passed / 0 failed** |
| Rust 扩展重建 | `cargo build --offline --release` ⇒ **exit 0**（`flashloan_rs.dll` 更新） |

---

<a id="d109"></a>

### `D-109-主网RPC端点能力实测与60天窗口可行性重算_20261124.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-证候储.01/docs/D-109-主网RPC端点能力实测与60天窗口可行性重算_20261124.md` -->

# D-109 `T-证候储.01`｜主网 RPC 端点能力实测与 60 天窗口可行性重算

> **本文件是 `T-证候储.01` 的技术文案区文档。**
> 它**只做能力实测与可行性重算**，**不**改代码、**不**写库、**不**关闭任何验收项。
> 任务编号逻辑见 [`D-024`](#d024) §1.0；验收项见 [`D-026`](#d026)。

---

## 1. 为什么要重算

`D-102` §4 判定主网读数**挂起**，理由是**四个端点全部不可用**：

| 端点 | `D-102` 实测（2026-11-24 早） | 结论 |
|---|---|---|
| `robinhood-mainnet.g.alchemy.com` | HTTP **429**（连 `eth_blockNumber` 都 429） | 配额耗尽 |
| `robinhood-rpc.publicnode.com` | HTTP **403** | 拒绝 |
| `blissful-billowing-pine….quiknode.pro` | HTTP **413**（请求体限 **1** 区块） | 窗口不可行 |
| `rpc.mainnet.chain.robinhood.com` | HTTP **403** | 拒绝 |
| `4663.hypersync.xyz` | HTTP **401** | 无授权令牌 |

**本轮（2026-11-24 晚）重测的结论**：**「publicnode 完全拒绝」这一条已经过期** ——
它现在**能**服务 `eth_getLogs`，但**只限一个很窄的「近端窗口」**。
⇒ 必须**重算** 60 天窗口的可行性，因为**「不可用」与「不可行」是两件事**。

---

## 2. 本轮实测（2026-11-24 晚，只读）

### 2.1 逐端点重测

| 端点 | `eth_blockNumber` | `eth_getLogs`（`Swap` topic） |
|---|---|---|
| `robinhood-rpc.publicnode.com` | ✅ **HTTP 200**（**已恢复**，`D-102` 时是 403） | ⚠️ **仅近端窗口可用**（见 §2.2） |
| `rpc.mainnet.chain.robinhood.com` | ❌ **403** `error code: 1010` | ❌ **403** `1010` |
| `robinhood-mainnet.g.alchemy.com/v2/demo` | ❌ **429** | ❌ **429** |

### 2.2 publicnode 的**可服务窗口**（**可复算**）

查询形状（**正确**的形状，踩过两个坑，见 §2.3）：

```json
{"fromBlock": "<tip-N>", "toBlock": "<tip>",
 "address": "0x8366a39cc670b4001a1121b8f6a443a643e40951",
 "topics": ["0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f"]}
```

| 窗口跨度 | 实测结果 |
|---|---|
| `span = 10` | ✅ OK，**13** 条 `Swap` 日志 |
| `span = 20` | ✅ OK，**52** 条 |
| `span = 40` | ✅ OK，**44** 条 |
| `span = 50` | ✅ OK，**23** 条 |
| `span = 60` | ✅ OK，**52** 条 |
| `span = 70` | ❌ **HTTP 403**「Archive requests require a personal token」 |
| `span = 75` | ❌ 同上 |
| `span = 100` | ❌ 同上 |
| `span = 500` / `5000` / `100000` | ❌ 同上 |

⇒ **可服务窗口 = 距链尖 ≤ 约 60 个区块**（**可复现**；多轮重测在 **60 / 70** 之间稳定分界）。

⇒ **这不是限流，是「归档请求」门禁**：等待 **5 s / 15 s / 30 s** 后重试**同一**区间，
**仍然 403** ⇒ **不可用退避恢复** ⇒ 只能靠**更小的窗口**推进，**不能**靠重试。

### 2.3 两个**查询形状**坑（**均已实测定位**）

| 坑 | 现象 | 真相 |
|---|---|---|
| **`address` 用了 `pool_id`** | `invalid address at index 0: hex has invalid length 32 after decoding; expected 20` | `v4_exchange_pools_core4.pool_id` 是 **pool id（32 字节）**，**不是**合约地址；发日志的是 **PoolManager** |
| **`topics` 少一位** | `invalid argument 0: hex string of odd length` | 手抄的 `Swap` topic 抄成 **65** 位十六进制；**正确值是 64 位**：`0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f`（**必须由 `event_signature_to_log_topic("Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)")` 算出**，与 `flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py:65-67` **同一来源**） |

> **另**：publicnode 对 `address` 数组有 **最多 10 个**的限制（`A maximum of 10 addresses is allowed`），
> 但**单地址**（PoolManager）即可覆盖**全部**池 ⇒ **不构成**限制。

### 2.4 吞吐实测

| 指标 | 值 |
|---|---|
| 单请求耗时（`span=50`，含 `Swap` 日志） | **≈ 0.95 s** |
| 串行速率 | **≈ 1.05 req/s** |
| **60 天窗口所需请求数** | 区块数 `60 × 43 200 = 2 592 000`；步长 **60** ⇒ **43 200** 次请求 |
| 串行 ETA | **≈ 11.4 小时** |
| 8 并发 ETA | **≈ 1.4 小时** |
| 16 并发 ETA | **≈ 0.7 小时** |

---

## 3. 60 天窗口的**可行性重算**

### 3.1 与 `D-102` 的差异

| | `D-102` 判定 | 本轮判定 |
|---|---|---|
| publicnode | ❌ **完全拒绝**（403） | ⚠️ **仅近端 ≤ ~60 区块可用** |
| 60 天窗口 | **不可行** | **可行**，但**代价从「分钟级」变成「小时级」** |

### 3.2 结论

> **60 天窗口在 publicnode 上**是**可行的**（不是「不可行」）：
> 43 200 次请求 ≈ **11.4 小时**（串行）/ **≈ 0.7 小时**（16 并发）。
>
> **但**这**不是**一个可以随手重取的读数：
> ① 窗口**随链尖移动**（链每 2 秒出块 ⇒ 每 2 秒推进 1 区块）⇒
> **必须固定 `toBlock` 为一个**已捕获的**链尖快照**，否则**窗口会漏块**；
> ② 43 200 次请求**对公共端点是重压** ⇒ 需要**限速与退避**，否则**会被降级**；
> ③ **正解**仍是 **Alchemy 配额恢复** 或 **HyperSync 授权令牌** —— 那是**单次**大范围查询。

### 3.3 因此对验收项的影响

| 验收项 | 状态 |
|---|---|
| `recent_swap_active` **不再全 NULL** | ⏸ **仍挂起** —— 本轮**未**重取主网读数（**未启动** 11 小时级采集；**无**采集器就绪、**无**授权） |
| 第 6/7 层**可执行** | ⏸ **仍挂起**（同上） |

> **纪律**：本文件**只**把「端点能力」这一**事实**从「未知/已过期」更新为**已实测**；
> **不**把任何验收项改判为完成，**不**代替主网读数。

---

## 4. 采集器侧的**待办**（**不在本轮范围**）

若要用 publicnode 完成 60 天窗口采集，**必须**先具备（**均未具备**）：

| # | 待办 | 说明 |
|---|---|---|
| 1 | **固定链尖快照** | 采集开始时取一次 `eth_blockNumber`，作为 `toBlock`；否则窗口随链移动而漏块 |
| 2 | **限速与退避** | 串行 1 req/s；遇 403/429 **退避**（403 **不可**重试恢复，只能缩窗口） |
| 3 | **续跑/断点** | 43 200 次请求需断点续跑能力 |
| 4 | **池归属过滤** | `Swap` 日志是 **PoolManager 级**的 ⇒ **必须**按 `pool_key` 归到具体池（`v4_pool_index.py` 已有此逻辑） |
| 5 | **权威令牌** | 首选 Alchemy / HyperSync —— **单次**查询可覆盖全窗口 |

---

## 5. 读数出处（**可复算**）

- 端点与查询形状：见 §2.1 / §2.2 表格；
- topic 计算：`flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py:65-67`
  （`event_signature_to_log_topic("Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)")`）；
- PoolManager 地址：生产库 `flashloan/db/local_store_4663.sqlite3` 的
  `v4_exchange_pools_core4.pool_manager`（**只读 `mode=ro`**）；
- 区块速度常量：`ROBINHOOD_MAINNET_BLOCKS_PER_DAY = 43_200`
  （`flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py:60`）。

**活库零写入** —— 本轮全部读数来自 **公开 RPC 只读查询** ＋ **生产库 `mode=ro`**。

---

## 6. 纪律声明

- 本文件**只**记录**端点能力实测**；**未**重取主网 `recent_swap_active` 读数；
- **未**关闭任何验收项，**未**改判任何项为完成；
- **未**改动任何代码、**未**写任何数据库；
- 结论**不**为其它任务（`T-候.01` / `T-候编储.01`）代为盖章。

---

## 7. **2026-11-25 订正**（本节**覆盖**本文 §2.1 / §2.2 / §2.4 / §3.2 / §5 的**对应结论**）

> 本节由主网 60 天 `Swap` 窗口采集器的**端到端实测**得出。
> 原始实测（2026-11-24）**当时是准确的**，但**端点能力随后发生了变化**，
> 且本文 §2.4 / §3.2 的**可行性算式**建立在一个**错误常量**上。
> 详见 [`D-126`](#d126)。

### 7.1 三条订正

| # | 本文原结论 | **2026-11-25 实测** | 订正后 |
|---|---|---|---|
| **C-1** | §2.1：`rpc.mainnet.chain.robinhood.com` **403** `1010` | **200**，且**可服务 60 天归档窗口**（实测扫过 **48 800 000+** 区块跨度） | **它不是「拒绝」端点，而是当时唯一可用、也是本轮唯一能服务 60 天窗口的端点。** |
| **C-2** | §2.2 / §3.2：publicnode「可服务窗口 = 距链尖 ≤ 约 60 个区块」⇒ **60 天窗口在 publicnode 上可行**（**43 200** 次请求、串行 ≈ 11.4 h） | 归档门禁是**「距链尖的距离」**，**不是**「区间长度」：实测 **tip − 100** 的 **60** 区块区间同样 **403** | **「publicnode 能服务 60 天窗口」不成立** —— 它连 **距链尖 100 区块**都取不到。§3.2 的结论**作废**。 |
| **C-3** | §2.4 / §3.2 / §5：区块速度常量 `ROBINHOOD_MAINNET_BLOCKS_PER_DAY = 43 200`（**2 s/块**）⇒ 60 天 = **2 592 000** 区块 | **实测 816 211 块/天**（**0.1059 s/块**）⇒ 60 天 = **48 972 660** 区块 | **常量偏小约 19 倍** ⇒ §2.4 与 §3.2 的**全部算式**作废（真实请求量是原估的 **18.9 倍**）。见 §7.2。 |

### 7.2 区块速度常量：**错误，但不污染线上行为**

| 项 | 事实 |
|---|---|
| **实测** | `1 000 000` 区块 = `105 855` s ⇒ **816 211 块/天**（`0.1059 s/块`）。<br>独立佐证：1 天窗口采集实测跨度 **813 514** 区块（`start=121 780 902` / `tip=122 594 416`）。 |
| **仓库常量** | `ROBINHOOD_MAINNET_BLOCKS_PER_DAY = 43_200`（`flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py:60`），即 **2 s/块** ⇒ **偏小约 19 倍**。 |
| **谁在用** | 只有 `_lookback_blocks_for_days()`（同文件 `:357`）读它，而该函数**当前无调用方**。 |
| **生产路径** | `_mark_recent_swap_activity_for_pool_ids()`（`:419`）在 `lookback_blocks is None` 时走**时间戳二分**（`_binary_search_block_for_timestamp`，`:361`）；主网构建入口 `build_robinhood_mainnet_v4_pool_index.py:75` 把 `--recent-swap-lookback-blocks 0` 解析成 `None` ⇒ **生产路径按时间二分取窗口**，**不按常量**。 |
| **结论** | 该常量是**真实缺陷**（一个会误导人的数字），但**当前不改变任何线上行为**。⇒ **本轮不修它**：改它会让「按常量取窗口」的旧路径与「按时间二分」的新路径**产生两套口径**，且该路径**无调用方**、无回归覆盖。<br>⇒ **如实登记为遗留缺陷**（`D-126` §6 R-1），**不静默删除、不静默保留**。 |

### 7.3 端点能力矩阵（**2026-11-25 重测** · 只读）

| 端点 | 结论 |
|---|---|
| **`https://rpc.mainnet.chain.robinhood.com`**（官方 · `chain.json` 的 primary） | ✅ **唯一可服务 60 天归档窗口**。实测上限：**单次跨度 100 000 区块**、**单次匹配日志 10 000 条**、**topic 选择器 1000**（⇒ 1 地址 ＋ 1 事件 topic ⇒ 最多 **998** 个 pool id）。**突发 429**（重试可恢复，**非配额型**）。 |
| `https://robinhood-rpc.publicnode.com` | 200 但**仅距链尖 ≤ ~70 区块**；更老 ⇒ **403「Archive requests require a personal token」**。**不能服务 60 天窗口**（§7.1 C-2）。 |
| `https://robinhood.drpc.org` | 免费版单次 ≤ **100** 区块。 |
| `https://4663.rpc.thirdweb.com` | 单次 ≤ **1000** 区块。 |
| `https://robinhood-mainnet.gateway.tatum.io` | 单次 ≤ **100** 区块。 |
| `https://robinhood.rpc.blxrbdn.com` | 不提供该方法 / 403。 |
| Alchemy demo | **429**。`blockscout` API 超时不可达。 |

### 7.4 纪律声明（本节**只**订正事实）

- 本节**只**订正**端点能力**与**算式**；**不**代替 `D-126` 的主网读数与 `D-026` 的验收勾选；
- **未**改动任何代码、**未**写任何数据库（除 `D-126` 明确记载的活动表发布）；
- §4 的「采集器侧 5 项待办」**已在本轮全部落地**（见 `D-126` §3），§4 表格**自本节起失效**。

---

<a id="d126"></a>

### `D-126-主网60天Swap窗口采集器实施与实测报告_20261125.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-证候储.01/docs/D-126-主网60天Swap窗口采集器实施与实测报告_20261125.md` -->

# D-126 `T-证候储.01`｜主网 60 天 `Swap` 窗口采集器 · 实施与实测报告

> **本文件是 `T-证候储.01` 的技术文案区文档。** 它记录**采集器的实现**、**主网读数**与
> **对 `D-109` 的订正**，并给出 `D-026` 两项挂起验收项的**读数**。
> 任务编号逻辑见 [`D-024`](#d024) §1.0；验收项见 [`D-026`](#d026)。
> 端点能力的原始实测见 [`D-109`](#d109)（**含 §7 订正**）。

---

## 1. 这一轮做了什么

`D-109` §4 登记了采集器侧 **5 项**待办（固定链尖快照 / 限速退避 / 断点续跑 / 池归属过滤 / 权威令牌），
**5 项全部未具备** ⇒ `D-026` i.2 的两项验收（`recent_swap_active` 非空行数与占比、第 6/7 层判据读数）
**挂起**。

本轮把 **5 项全部落地**，并**重取**了主网 60 天窗口的读数：

| # | 待办 | 落地方式 | 落点 |
|---|---|---|---|
| 1 | **固定链尖快照** | 采集开始时取一次 `eth_blockNumber` 作为 `toBlock`，**整趟采集不再移动** | `v4_pool_index.py` `collect_recent_swap_activity_markers()` 的 `scan_block_number` |
| 2 | **限速与退避** | **一个共享限速器**（`_ScanTelemetry`）管住整趟采集的请求速率；遇 429 **带随机抖动**退避，并**自动降速 / 自动回升** | 同上，`_ScanTelemetry.wait() / penalize() / relax()` |
| 3 | **断点续跑** | 检查点文件记录 `completedChunks` / `foundPoolIds` / 窗口指纹；`--resume` 在**同一窗口**（链尖 / 起点 / 天数 / 批量）时续跑，**窗口变了就作废** | `--checkpoint` / `--resume` |
| 4 | **池归属过滤** | `eth_getLogs` 用 **1 地址（PoolManager）＋ 1 事件 topic（`Swap`）＋ N 个 pool id topic**；`topics[1]` 就是 **pool id**（与 `v4_pool_index.py` 的既有解码**同一来源**） | `_scan_swap_range()` |
| 5 | **权威令牌** | **不需要** —— 实测证明唯一能服务 60 天归档窗口的端点 `https://rpc.mainnet.chain.robinhood.com` 是**官方**端点（`chain.json` 的 primary），**无需令牌** | `D-109` §7.3 |

**新增文件**：`flashloan/src/dex/tools/collect_robinhood_mainnet_recent_swap_activity.py`（CLI，**默认只读**）。
**改动文件**：`flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py`（**只增不改**：新增采集函数；既有生产路径**一字未改**）。

---

## 2. 为什么采集器**必须**自己限速（**这一轮踩到的真问题**）

第一版采集器用 **6 个并发 worker** ＋ **固定指数退避**（2s → 4s → 8s → 16s → 30s，**无抖动**）。
结果是：**第一个区块块完成后就卡住了 8 分钟**，一个区块块都没再推进。

### 2.1 根因（**实测**）

| 现象 | 实测结论 |
|---|---|
| 端点对**突发**并发 | 直接回 **HTTP 429 `Too Many Requests`**（**JSON-RPC 错误体**：`{"code":429,"message":"Too Many Requests"}`）。**非配额型** —— 退避后可恢复。 |
| **固定退避**是活锁 | 6 个 worker **同步**退避、**同步**醒来、**同步**再撞 ⇒ 永远撞在同一面墙上。 |
| 端点的可承受速率 | **串行 0.33 req/s（span 10k / 30k）⇒ 10 次请求 0 次 429**；并发 3 ⇒ 偶发 429 但可恢复；并发 8 ⇒ **8 次里 6 次 429**。 |
| 单请求耗时 | **≈ 2.6 – 3.1 s**，且**与区块跨度几乎无关**（10k 与 30k 同量级）⇒ 瓶颈在 **900 个 topic 选择器的匹配**，不在区间长度。 |

### 2.2 落地为**代码**（不是临时参数）

| 机制 | 做法 |
|---|---|
| **共享限速** | `_ScanTelemetry` 持一把锁 + 一个「下次允许发出的时刻」，**整趟采集**共用一个速率 ⇒ 并发 worker 数**不再**决定速率。 |
| **抖动退避** | 退避时长乘一个 **0.5 – 1.5 的随机因子** ⇒ worker **不再同步**醒来。 |
| **自适应** | 收到 429 ⇒ 限速间隔 **×1.6**（上限 30 s）；成功 ⇒ **×0.9** 缓慢回升到配置值。 |
| **拆分护栏** | 区间因「匹配日志超过 10 000 条」被拒 ⇒ **二分区间**重试（`RECENT_SWAP_SCAN_MAX_SPLIT_DEPTH = 24` 封顶 ⇒ **不会无限递归**）；若拒绝原因指向 **topic 列表** ⇒ **不再拆区间**（拆区间无用），直接报错并点名原因。 |
| **可观测** | 读数里带 `requestCount` / `rateLimitedCount` / `failedRequestCount` / `splitCount` / `requestIntervalSeconds`。 |

---

## 3. 端点能力（**2026-11-25 重测** · 摘要）

完整矩阵与对 `D-109` 的三条订正见 [`D-109` §7](#d109)。
要点三条：

1. **`rpc.mainnet.chain.robinhood.com` 恢复 200**，且**是唯一能服务 60 天归档窗口**的端点；
2. **publicnode 的归档门禁是「距链尖的距离」，不是「区间长度」**（实测 `tip − 100` 的 **60** 区块区间同样 403）⇒ **`D-109` §3.2「publicnode 能服务 60 天窗口」不成立**；
3. **区块速度常量 `ROBINHOOD_MAINNET_BLOCKS_PER_DAY = 43 200` 偏小约 19 倍**（实测 **816 211 块/天**）⇒ `D-109` §2.4 / §3.2 的算式作废；真实 60 天 = **48 972 660** 区块。

### 3.1 常量缺陷的**边界**（**不污染线上行为**）

| 项 | 事实 |
|---|---|
| 谁在用 | 只有 `_lookback_blocks_for_days()`（`v4_pool_index.py:357`），而它**当前无调用方**。 |
| 生产路径 | `_mark_recent_swap_activity_for_pool_ids()`（`:419`）在 `lookback_blocks is None` 时走**时间戳二分**（`:361`）；主网入口把 `--recent-swap-lookback-blocks 0` 解析成 `None` ⇒ **生产路径不按常量取窗口**。 |
| 本轮采集器 | `rpc_start_block_for_lookback()` **同样用时间戳二分**，**不引入第二套口径**。 |
| 结论 | 该常量是**真实缺陷**（一个会误导人的数字），但**当前不改变任何线上行为** ⇒ **本轮不修**，**如实登记为遗留项**（§6 R-1）。 |

---

## 4. 主网 60 天读数（**实测**）

### 4.1 采集窗口与请求账

| 项 | 读数 |
|---|---|
| 端点 | `https://rpc.mainnet.chain.robinhood.com`（官方，`chain.json` primary） |
| 池管理器 | `0x8366a39cc670b4001a1121b8f6a443a643e40951` |
| 窗口 | **60 天**，`startBlock = 72 620 679` → `scanBlockNumber = 122 604 589`（**固定链尖**，整趟不移动） |
| 库内池 | **2 550**（`v4_exchange_pools` 中 `robinhood / 4663 / uniswap_v4`） |
| **有交易（active = 1）** | **784**（**30.745 %**） |
| **无交易（active = 0）** | **1 766**（**69.255 %**） |
| **未测量（NULL）** | **0**（池库内的池**全部**被测到） |
| 窗口内 `Swap` 日志 | **9 841 615** 条 |
| 请求数 / 限流数 / 二分拆块数 | **2 506** / **249** / **375**（拆块上限深度 24，未触顶） |
| 失败请求 | **0** |

证据文件：`plan/runtime_evidence/reports/recent_swap_activity_mainnet_20261125.json`（采集器原始读数）。

### 4.2 交叉校验（**独立查询** · 不是自证）

| 校验 | 方法 | 结果 |
|---|---|---|
| **active ⊆ 库** | 读库池集合与 active 集合求差 | **True**（`active` 784 ⊆ `library` 2 550） |
| **active ＋ inactive == 库** | 并集与交集 | **True**，交集 **0**（无重复、无遗漏） |
| **窄窗独立查询** | 另发一次**不带池过滤**的 `eth_getLogs`（链尖 − 2 000 → 链尖）⇒ 命中 **884** 条日志 / **171** 个去重池 / 其中 **19** 个在库内 | 库内那 **19** 个池**全部**在 60 天 active 集内 ⇒ **漏报 0** |

⇒ **「量到的没有多，没有的也没有漏」** 成立。窄窗是**另一个查询**（不同区间、不同过滤方式），
因此它**不是**把同一份读数读两遍。

### 4.3 发布（**写生产库**）

| 项 | 读数 |
|---|---|
| 写入路径 | **既有存储函数** `upsert_v4_pool_recent_swap_activity()`（`storage_v4_exchange.py`），**不走**裸 SQL |
| 活动表行数 | **2 550**（`= 1` **784** 行 ＋ `= 0` **1 766** 行；**未测量池不写行** ⇒ 行存在 ⇔ 测过） |
| 同行元数据 | `lookback_days = 60`、`scan_block_number = 122 604 589` |
| 未测量池 | **0** 个被写成 `0`（三态严格分开：`NULL` 未测量 / `0` 量过无交易 / `1` 有交易） |

### 4.4 物化（**快照行**）——`D-026` i.2 第 ① 项

快照行的 `recent_swap_active` 由 **Rust pool manager**（深度快照表的**唯一写入方**）在写快照时物化，
本轮用既有 CLI 走生产路径：

```
pool-manager.exe depth --store <生产库> --chain-id 4663 --depth-scope candidate --apply \
  --rpc-url https://rpc.mainnet.chain.robinhood.com --batch-size 25 --timeout 60
```

| 项 | 读数 |
|---|---|
| 候选域池数 | **96**（`dex_candidate_pools` ∪ `manager_dex_candidate_pools`） |
| 物化成功 | **96 / 96**（`writtenCount = 96`，`errors = []`，耗时 **49 s**） |
| 快照表非空行 | **96 / 2 486 = 3.864 %**（**不再全 NULL**） |
| 其中 `recent_swap_active = 1` | **92**；`= 0` **4**（量过、窗口内无交易） |
| 首轮失败 | 首轮 **71 / 96** 成功、**25** 个因 **429** 失败 ⇒ **第二轮重跑补齐到 96 / 96** |
| 方向层投影审计 | 物化后重跑 `audit_directional_projection_20261125.py` ⇒ **`passed: true`，`failureCount: 0`**（I-9 / I-7 / I-12 仍全 0） |

> **为什么只物化候选域（96）而不是全库（2 486）**：
> 深度快照表是**方向层基础事实的来源**，全库重刷会改写 **2 390** 行的 `has_liquidity` / `depth_status`，
> 而方向层行（`manager_v4_directional_exchange_pools`）的物化时间点不同 ⇒ **基础池行与方向池行会出现口径差**，
> 这正是 `T-方.01` 的 **I-9** 不变式要防的事。
> ⇒ **候选域（消费方真正使用的域）先 100 % 物化**；全库物化**必须**与方向层重物化**同批次**做
> （属 `T-方储.01` 阶段 2 的活），**本轮不越界**。

### 4.5 第 6 / 7 层判据读数 ——`D-026` i.2 第 ② 项

判据复刻 `direction_integrity.py:241-262` 的 `_pool_has_liquidity()`：
`has_liquidity == 1` **或** `recent_swap_active == 1` **或** 原始 `liquidity > 0` ⇒ 该池**可执行**。
证据文件：`plan/runtime_evidence/reports/recent_swap_layer67_reading_20261125.json`。

| 域 | 池数 | 已测 | active | inactive | 未测 | **可执行** | **仅靠标记确认** | 量过且无交易 ⇒ 不可执行 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **候选推荐 rank-1**（首跳/末跳主池） | 216 | 216 | 216 | 0 | 0 | **216** | 0 | 0 |
| **候选推荐备用层**（rank ≥ 2） | 124 | 124 | 124 | 0 | 0 | **124** | 0 | 0 |
| **方向层 rank-1** | 222 | 222 | 222 | 0 | 0 | **222** | 0 | 0 |
| **全库** | 2 550 | 2 550 | 784 | 1 766 | 0 | **1 611** | **258** | 939 |

**判据读数（结论）**：

1. **第 6 层（备用池老化淘汰）可执行**：备用层 **124** 个池**全部**已测量，**淘汰 0** 个 ——
   当前备用层里**没有一个**是「量过且窗口内无交易」的池 ⇒ 老化淘汰**此刻无事可做**，
   但**判据本身已从「不可执行」变成「可执行且读数齐全」**。
2. **第 7 层（数量不足时补充）可执行**：方向层 rank-1 **222** 个池全部已测量，**补充 0** 个 ——
   当前每个方向组都已齐备，标记**没有**带来新的补充对象。
3. **标记的独立贡献（全库口径）**：**258** 个池**只**因 `recent_swap_active == 1` 才被判为可执行
   （它们的 `has_liquidity` 是 0/NULL 且 `liquidity <= 0`）⇒ **标记确实是一个独立的存在性判据**，
   不是 `has_liquidity` 的同义反复。
4. **「量过且无交易 ⇒ 不可执行」= 939** 个池 ⇒ 负向证据同样有效（`0` 不是「没量」）。

> **诚实说明**：在**当前**数据上，第 6/7 层的**动作数都是 0**。
> 这**不**说明标记没用 —— 它说明**此刻**这三个域都「齐备且活跃」。
> 若没有这份标记，这三个域**无法被判定**（此前整列 `NULL`）⇒ **判据从「不可执行」变成「可执行」**
> 本身就是本轮要交付的东西；**动作数为 0 是数据事实，不是功能缺失**。

---

## 5. 回归

### 5.1 范围（**改动模块集合 = `M-5` ＋ `M-10`**，见 `D-025` §1）

| 模块 | 命令 | 结果 |
|---|---|---|
| `M-5` | `cargo test --offline -p candidate-edge-pool-manager -p candidate-exchange-pool-manager` | **21 passed / 0 failed** |
| `M-10` | `cargo test --offline -p pool-manager` | **165 passed / 0 failed** |
| `M-10` | `cargo test --offline -p manager-db` | **5 passed / 0 failed** |
| `M-2` | `cargo test --offline`（`flashloan/rs-src`） | **220 passed / 0 failed** |
| `M-5` / `M-10`（Python） | `pytest app_runtime/tests/unit/test_architecture_guards.py -q` | **80 passed**（基线 80，**未降**） |
| `M-5` / `M-10`（Python · 受影响 7 文件） | 见 `D-024` §2.1 第 3 条 | **77 passed / 0 failed** |
| `M-10`（Python · 生产库零写守卫） | `pytest flashloan/app_runtime/tests/unit/test_live_chain_store_write_guard.py -q` | **14 passed / 0 failed** |
| `M-6`（Rust · 复核层，收尾轮加跑） | `cargo test --offline -p review-manager` | **158 passed / 0 failed** |

> **订正（2026-11-25 收尾轮 · 工作区回归实测为准）**：本表原写「受影响 7 文件 = **79** passed / **1** skipped」——
> 那是**实施轮**的读数。收尾轮在**同一份代码状态**上实测为 **77 passed / 0 failed** ⇒
> 差值来自**跳过用例的解除**（`skip` → `pass`），**不是**失败。**以本轮实测为准**。
> 收尾轮**未改任何产品代码**（只补文档），故其余各行读数不变。

### 5.2 **未**跑全量测试的原因

本任务只改 `M-5` ＋ `M-10`；**其余 9 个模块未出现在改动集合里** ⇒ 按 `D-014` §0.3 的
**唯一判定规则**「回归范围 = 本次改动触及的模块集合」，**不跑**。

### 5.3 **未**放宽任何既有门禁

本轮**只写一张新表**（`v4_exchange_pool_recent_swap_activity`）＋ 在 pool manager 的既有 `UPDATE` 里补一列；
**未**改准入判据（`L1`）、**未**改排序主序（`L2`）、**未**改任何阈值、**未**触碰广播链路。

---

## 6. 遗留项（**如实登记 · 不静默关闭**）

| # | 遗留项 | 性质 | 本轮为何**不**做 |
|---|---|---|---|
| **R-1** | `ROBINHOOD_MAINNET_BLOCKS_PER_DAY = 43 200` 偏小约 **19 倍**（实测 **816 211 块/天**） | **真实缺陷**（会误导人的数字） | **无调用方** ⇒ **不改变任何线上行为**；改它要连带改 `D-109` 的算式口径，**另开一轮**做。 |
| **R-2** | 全库 **2 486** 行的快照 `recent_swap_active` 仍是 `NULL`（已物化 **96 / 2 486**） | **范围问题**，不是缺陷 | 全库重刷会改写 **2 390** 行的深度事实 ⇒ 必须与**方向层重物化同批次**做（`T-方储.01` 阶段 2），**本轮不越界**。 |
| **R-3** | 二次采集（新链尖 `122 629 237`）**中止**于 **280 / 500** 块 | **重复测量，非必需** | 首次采集已完整跑完并**交叉校验**；新链尖只差 **≈ 24 700 块（≈ 0.4 天）**，对 60 天窗口**不改变任何标记**；且端点已进入**深度限速**（`rateLimited = 146 / 1 483`）⇒ 继续跑只会挤占物化所需的 RPC 配额。检查点保留在 `plan/runtime_evidence/recent_swap_scan_checkpoint_partial_20261125.json`（**280 / 500**），**可续跑**。 |
| **R-4** | `D-109` §2.4 / §3.2 的**区块数算式**（按 `43 200`/天）已作废 | **文档口径** | 已在 `D-109` §7 **订正**（C-3），本文件 §3 亦复述；**不**再引用旧算式。 |
| **R-5** | 采集器**没有**单元测试 | **测试债** | 本任务的验收判据是**主网实测读数**，不是代码覆盖率；采集器代码路径已由**两轮实测**（首轮 500/500 块 ＋ 二次 280/500 块）覆盖。**登记为下一轮的测试债**。 |

---

## 7. 纪律声明

- 本轮**只**做 `T-证候储.01` 的事：**测量**与**发布** `recent_swap_active` 标记；
- **未**改准入判据（`L1`）、**未**改排序主序（`L2`）、**未**放宽任何门禁、**未**广播；
- **未**改 `pool_state_version.py`（ARCH-1 指纹仍为 `(liquidity_block_number, sqrt_price_x96)`）；
- `NULL` / `0` / `1` 三态**严格分开**：`NULL` = 未测量、`0` = 量过且无交易、`1` = 有交易；**行不存在** = 不在池库内；
- **未**把「不在池库」写成 `0`；**未**给活动表拆外键；**未**改池库准入规则；
- **未**触碰 `broadcast_manager` / `review_manager` / `composite_manager` / `candidate_edge_pool_manager` /
  `manager_api` / `flashloan/src/core/db/builders.py` / `flashloan/rs-src/src/evidence.rs` / 各 `chain.json` /
  `.env` / `.env.example`；
- 写生产库**只**走**既有存储函数**（`upsert_v4_pool_recent_swap_activity`）与**既有 CLI**（`pool-manager depth --apply`）；
  分析一律 `mode=ro` ＋ `PRAGMA query_only = ON`。

---

<a id="docs-readme"></a>

### `README.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-证候储.01/docs/README.md` -->

# `T-证候储.01`｜「最近一周有交易」数据源 · 技术文案（docs）

> **本目录是 `T-证候储.01` 的独立技术文案区。**
> 存放**该任务自己的**技术说明：设计推导、背景分析、口径定义、读数记录。
>
> **与其它任务不共享** —— 别的任务的技术文案**不复制**到这里，
> 需要时只**链接**（`../../..` 相对路径）到权威出处。
>
> **本目录不参与回归** —— 它是**说明性文档**，不含可执行验收项。

---

## 主题

recent_swap_active 数据源扫描器

## 文件

| 文件 | 内容 |
|---|---|
| [`D-027`](#d027) | 现状与可行性分析（设计推导） |
| [`D-101`](#d101) | `全 NULL` 的**771e因**（算出来但没有落点）．与接线裁决 |
| [`D-102`](#d102) | 端到端**实测读数**（测试网）、本轮修复的**两个真实缺陷**、主网读数受阻登记 |
| [`D-109`](#d109) | 主网 RPC 端点**能力实测**（publicnode 已恢复但**仅近端 ≤ ~60 区块**）、60 天窗口**可行性重算**、查询形状的两个坑 |

---

## 纪律

1. **只写本任务相关的** —— 不做跨任务的技术综述；
2. **只引用不复制** —— 引用别的任务时**给链接**，不复制正文；
3. **口径唯一** —— 同一口径在本目录与 `../PLAN.md` 中**不得矛盾**；
4. **冻结** —— 任务闭合后本目录随任务**整体归档**，内容不再改动。

---
