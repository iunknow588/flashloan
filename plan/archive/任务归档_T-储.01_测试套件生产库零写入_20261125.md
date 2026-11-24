# `T-储.01`｜测试套件生产库零写入 · 任务归档（**已闭合**）

> **归档元信息**
>
> | 项 | 值 |
> |---|---|
> | 任务编号（纯 ID） | `T-储.01` |
> | 验收编号（纯 ID） | `—` |
> | 标题 | 测试套件生产库零写入 |
> | 层级 | 二级（执行级） |
> | 归档日期 | 20261125 |
> | 原路径 | `plan/tasks/P-01-池子重构/tasks/T-储.01` |
> | 归档索引 | `plan/archive/README.md` §1 |
>
> **本件已冻结** —— 内容不再改动。更正走新版本 `..._v2.md`，旧版保留。

---

## ① 任务说明 ＋ ② 开发计划

<a id="d032"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-储.01/D-032-PLAN.md` -->

# D-032 `T-储.01`｜测试套件生产库零写入 · 开发计划（**独立 · 自包含**）

> **本目录是 AC-储.01 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**
> **本文件 + `checklist.md` 即 AC-储.01 的全部开发与测试计划。**
> 本目录**不引用**其它任务目录的验收内容；需要别的任务先完成时，只写**模块级前置**（§1）。
>
> **编号是纯 ID**：`T-储.01` / `AC-储.01` **不表达任何功能关联**。
> **范围与顺序只按模块编号（`M-n`）表达。**

---

## 1.0 编号逻辑（**本任务的编号从哪来**）

> **本节是固定部分。** 每个任务都必须**自己**声明编号逻辑，
> 使读者**不看编号册**也能知道「这个编号的索引是什么、序号是多少」。

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-储.01`** |
| **本任务验收编号** | **`AC-储.01`**（与任务编号**同号段**） |
| **编号作用域** | **模块内（单模块索引）** |
| **编号索引** | **`储`**（`M-10`（存储层（schema / 迁移 / 记录剥离））） |
| **序号** | 第 `01` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | `T-储.02`、`T-储.03`、`T-储.04` |

本任务**只改动一个模块** `M-10`（存储层（schema / 迁移 / 记录剥离））⇒ 编号索引即该模块索引 **`储`**。



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
| `M-10` | 存储层（schema / 迁移 / 记录剥离） | `储` |


**登记**：本任务的号段占用情况登记在
[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §4.2（**编号唯一真相源**）。

---

## 1. 身份与范围

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`AC-储.01`** |
| **验收编号（纯 ID）** | **`AC-储.01`** |
| **标题** | 测试套件生产库零写入 |
| **优先级** | P1 |
| **波次（模块级）** | W-4 |
| **前置模块**（**不是**前置任务） | — |
| **需授权** | 否（只读） |
| **未完成验收项** | **0**（**2026-11-25 闭合**） |
| **已闭合验收项** | **11** |

### 1.1 改动模块（**这一列就是 AC-储.01 的回归范围**）

| 模块 | 名称 | 代码落点 |
|---|---|---|
| `M-10` | 存储层 | `flashloan/app_runtime/tests/live_chain_store_guard.py`（**守卫的共享判定** ＋ **路径改道器** ＋ **Rust 写守卫**）、`flashloan/app_runtime/tests/conftest.py`（**连接级守卫** ＋ **默认存储路径改道** ＋ **Rust 写守卫装配**） |
| `M-10` | 存储层（**守卫自测**） | `flashloan/app_runtime/tests/unit/test_live_chain_store_write_guard.py` |
| `M-10` | 存储层（**逃生舱包裹**） | `flashloan/app_runtime/tests/unit/test_database_path.py`、`flashloan/app_runtime/tests/unit/test_database_url_helpers.py`、`flashloan/app_runtime/tests/unit/test_stock_token_registry.py`（各 **1** 处包裹，见 §3.2.5） |

> **产品代码零改动** —— 本任务**没有**改 `flashloan/src/**` 的任何一行：
> 守卫与路径注入**全部落在测试树**内（`flashloan/app_runtime/tests/**`），
> 因此对**在跑的实盘运行时零影响**，也**不与**其它线程在途的 `src/core/db/*` 争用。
>
> **规则**：**只跑**上表模块的独立测试。**未在上表出现的模块，一律不跑。**
> **不得**以「有新任务」或「功能没变」为由跑全量。

### 1.2 回归命令（**逐条可复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| `M-10` | 存储层 | `cargo test --offline -p manager-db` |
| `M-10` | 存储层（**守卫自测**） | `python -m pytest flashloan/app_runtime/tests/unit/test_live_chain_store_write_guard.py -q` |

```powershell
# AC-储.01 的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p manager-db
python -m pytest flashloan/app_runtime/tests/unit/test_live_chain_store_write_guard.py -q
```

### 1.3 落点订正与扩范围登记（**顺序倒置 · 如实登记**）

**原 §1.1 的落点**是 `flashloan/src/core/db/storage_business_schema.py` ——
那是**模板占位**（Rust 管理器迁移账本的 Python 侧 DDL），本任务**并不改**它。
实际落点**全部在测试树内**，见上表。

**为什么必须落在这里**：本任务的判据**全部是**「测试进程对生产库的实测读数」，
而这些读数**只能**由测试树里的守卫产生；**产品代码不需要改动**就能满足判据
（守卫的价值恰恰在于它拦的是**连接**，不是产品逻辑）。

| 文件 | 改了什么 |
|---|---|
| `flashloan/app_runtime/tests/live_chain_store_guard.py` | **新增**：① 守卫的共享判定（什么算「活链库」／如何只读打开）；② **路径改道器**（§make_store_path_redirect§ ／ §install_store_path_redirect§）—— 收口到 `core.db.database_path.resolve_database_path` 这**唯一根**，并**自动重绑**按名导入的别名；③ **Rust 写守卫**（§install_rust_write_guard§）—— 包住 `core.rs_bridge` 的 **10** 个写入口 |
| `flashloan/app_runtime/tests/conftest.py` | **改**：守卫从「守**函数名**」改为「**守连接**」（`Database.connect` ＋ `core.local_kv._connect` ＋ `sqlite3.connect` 兜底）＋ **默认存储路径改道**（未绑定链作用域时改道会话沙箱，**KV ＋ 业务表 ＋ Rust 桥同时生效**）＋ 装配 **Rust 写守卫** |
| `flashloan/app_runtime/tests/unit/test_live_chain_store_write_guard.py` | **新增**：守卫自测（**14** 项，含「守卫**非空洞**」的反证构造、「沙箱写放行」、「Rust 桥也被改道」、**「无漏网绑定点」**、**「逃生舱不解除写保护」**） |
| `flashloan/app_runtime/tests/unit/test_database_path.py`、`flashloan/app_runtime/tests/unit/test_database_url_helpers.py`、`flashloan/app_runtime/tests/unit/test_stock_token_registry.py` | **改**：各 **1** 处用 `unguarded_default_store()` 包裹 —— 这 **3** 个用例断言的是「**默认库＝生产库**」这条**产品规则**，而测试会话**故意**把默认解析改道到沙箱 ⇒ 必须**临时看回生产解析**才能断言。**逃生舱只恢复路径解析**，**连接守卫与 Rust 写守卫不受影响**（守卫自测 `test_the_escape_hatch_restores_the_resolution_but_not_the_write_protection` 钉死这一点） |

**缺口如实登记**：`D-014` §0.2 的「M-n 模块表**没有 Python 运行时归属**」仍未闭合 ——
本次沿用 `T-复编.01`／`T-复编.02` 先例把运行时落点登记在 `M-10` 名下，**不**改模块表。

**⚠️ 流程欠账（如实登记 · 顺序倒置）**：`D-033` §4 的「**先扩后跑**」要求
**先扩范围、再施工**；本次是**先施工、后补登记** ⇒ **顺序倒置**。
如实登记，**不美化**。**最终读数**取自补登记**之后**重跑的全量回归（见 §3.2）。

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

- [x] `checklist.md` 中**全部** `- [ ]` 变为 `- [x]`（**11 / 11**）

      **读数**：`D-034` 实测 `- [ ]` = **0**、`- [x]` = **11**。

- [x] §1.2 的**全部**回归命令**全绿**

      **读数**：§cargo test --offline -p manager-db§ ⇒ **5 passed / 0 failed**；
      §python -m pytest flashloan/app_runtime/tests/unit/test_live_chain_store_write_guard.py -q§
      ⇒ **14 passed**（2026-11-25 03:05 复跑）。

- [x] 每条已勾选项都有**可复算的实测读数**

      **读数**：`D-034` 的 **11** 项全部带**实测**读数（含**数值**与**证据文件**名）；
      证据落在 `plan/runtime_evidence/`（探针 JSON ＋ 日志），
      工具与命令落在 `plan/runtime_evidence/tools/` ⇒ **可复算**。

- [x] **未放宽**任何既有门禁（金额 / 容量 / 利润口径 / 幂等键 / 回执关联 / 阈值）

      **读数**：本任务**只改测试树**（`flashloan/app_runtime/tests/**`）
      ⇒ 生产门禁**无处可放宽**；广播三道闸门
      （`review_manager/src/lib.rs:1884-1900`、`worker_pool.rs:2600-2601`、
      `dex_opportunity_auto_execute.py:45-64`）**一字未动**。

- [x] **未**因本任务触碰**不属于** §1.1 的模块（若必须触碰 ⇒ **扩范围并重跑**）

      **读数**：`git status` 显示本任务改动**只在**
      `flashloan/app_runtime/tests/**` ＋ `plan/**`；`flashloan/src/**` **一行未动**。
      **未触发** §D-033§ §4 的扩范围流程。

---

## 3.2 全量回归读数（**最终判据 · 2026-11-25**）

> **本节是 §D-034§ §f.2 三条行为验收项的**全部**依据。**
> 它只回答一个问句：**把整个测试套件放进被观测窗口里跑一遍，活链库会不会被写？**
> 背景与根因见 §D-035§；勾选见 §D-034§ §f.2。

### 3.2.1 判据设计（**为什么不能只看一个数**）

**活链库是活的** —— 主网机会控制台（运行时进程）**同时**在写它。
因此「窗口前后文件变了」**不能**直接判定「测试写了库」⇒ 必须做**差分**：

| 层 | 判据 | 回答什么 |
|---|---|---|
| **① 闸门键字节级** | `kv_entries` 的 **`parameters` 集合**（**全部 19 行**）的 `value_json` ＋ `updated_at` **逐字节**比对 | 生产**控制面板状态**（两道暂停闸门 ＋ 4 个 page state）有没有被测试改写 |
| **② `kv_meta` 字节级** | `kv_meta` **整表**逐字节比对 | 迁移账本（`schema_migrations` 等）有没有被测试改写 |
| **③ 全库摘要**（第二轮） | **每一张表**的行数 ＋ 内容 `sha256`，另加 `kv_entries` / `kv_meta` **整表摘要** ＋ 5 个**测试残留**定向探针 | 业务表层、其它 KV 集合有没有被测试碰过 |
| **④ 同时长静默对照** | 同样长的窗口里**不跑** pytest | 把「运行时自身写入」从「测试写入」里**分离**出来 |

> **§① 的覆盖面已被独立核实**：`parameters` 集合**一共 19 行**，
> 而探针的 `gateKeyCount` 读数同样是 **19** ⇒ **闸门键比对覆盖的是整个集合**，
> 不是抽样。（`tools/probe_full_store_snapshot_20261125.py` 的
> `watch.kv_entries_parameters.count` = **19**，逐字可复算。）

### 3.2.2 最终读数（**守卫版 v4**）

| 观测 | **守卫版**（全量回归在窗口内） | **同时长静默对照**（无 pytest） | 判读 |
|---|---|---|---|
| 命令 | §python -m pytest flashloan scripts -q -p no:cacheprovider§ | §python -c "import time; time.sleep(2100)"§ | — |
| 退出码 | **0** | **0** | — |
| pytest 结果 | **3267 passed / 3 skipped / 0 failed**（**1289.65 s**） | — | **0 failed**（v3 的 3 个红例已用 `unguarded_default_store()` 修正，见 §3.2.5） |
| 窗口长度 | **1303.734 s** | **2102.234 s** | 对照窗**更长** ⇒ 更保守 |
| **① 闸门键字节级比对** | **changedCount = 0**（19 键） | **changedCount = 0** | **测试没有改写任何生产控制面板状态** |
| **② `kv_meta` 字节级比对** | **changedCount = 0** | **changedCount = 0** | **测试没有改写迁移账本** |
| §PRAGMA data_version§ | 2 → 2（采样到 **0** 次跳变） | 2 → 2（采样到 **2** 次跳变） | 见 §3.2.3（**辅助读数**，不作判据） |
| `cow_execution_reviews` 行数 | 70 224 → 70 779（**+555**） | 70 224 → 71 348（**+1124**） | **两个窗口都涨，且对照窗涨得更多** ⇒ 是**运行时自身**在写复核，**不是**测试 |
| `cow_execution_attempts` 行数 | 0 → 0 | 0 → 0 | 不变 |
| 主库文件 size | 12 126 711 808 → 12 223 111 168（**+92 MB / 1304 s ＝ 70.6 MB/1000 s**） | 12 126 711 808 → 12 319 637 504（**+184 MB / 2102 s ＝ 87.6 MB/1000 s**） | **对照窗（无 pytest）涨得更多** ⇒ 文件变化**不是**测试造成的 |

**证据**：§plan/runtime_evidence/probe_live_db_writes_20261125_full_guard_v4.json§、
§…_full_control_v4.json§。

### 3.2.3 方法学订正（**两条读数降级，如实登记**）

| 读数 | 原口径 | **实测结论** | 处理 |
|---|---|---|---|
| §PRAGMA data_version§ | 「全程不变」 | 在 **WAL** 下**不与每次提交一一对应**：缺陷复现的 **9.10 s** 短窗里闸门键被改写 **4** 个，它仍 **2 → 2**；`TASK-184` 的 **943.705 s** 长窗里 **185** 次提交才 **2 → 187**；守卫后的 **2102.234 s** 对照窗（运行时自身写 **1124** 行）也只跳 **2** 次 ⇒ **不可作判据** | **降级为辅助读数**；判据换成 §3.2.1 的**逐字节比对**（§D-035§ §2.6，**不推翻** `TASK-184` 的语句级计数结论） |
| 主库文件 **mtime / size** | 「全程不变」 | **字面口径未达成**：活链库**同时**被实盘运行时写入 ⇒ 窗口内文件**确实**变了 | **按归因闭合**：**同时长**静默对照窗（**无 pytest**）里主库涨得**更多** —— **+184 MB / 2102 s ＝ 87.6 MB/1000 s**，比守卫窗的 **+92 MB / 1304 s ＝ 70.6 MB/1000 s** 还大 ⇒ 变化来自**运行时**，**不是**测试。**归因**证据 = 闸门键 / `kv_meta` **0 变化** ＋ `kv_entries`／`kv_meta` **整表**摘要**完全相同** ＋ **81** 张表里 **76** 张**完全相同**、余 **6** 张**对照窗同样变化**（**差分为空**）＋ 文件涨幅对照窗更大 |

> **诚实边界**：「主库文件字节级零变化」**在活链库被运行时写入的前提下不成立**，
> 本任务**不**把它伪装成达成；本任务能证明的是
> **「测试套件对活链库的写入 = 0」**（归因后），而**不是**「活链库文件不动」。

### 3.2.4 第二轮加测：**整库摘要**（把判据扩到全库）

第一轮（v4）的闸门键比对只覆盖 `parameters` 集合 ＋ `kv_meta`。
为把覆盖扩到**整库**，第二轮用 §tools/probe_full_store_snapshot_20261125.py§
再跑一次：**每一张表**行数 ＋ 内容 `sha256`（**81 张表**取摘要；
`cow_execution_reviews` 因运行时**持续追加**只取行数），
另加 `kv_entries` / `kv_meta` **整表摘要**与 **5 个测试残留**定向探针
（`attempt|local|%` 键、`chain_id = 0` 行、`parameters` 集合）。

读数见 §3.2.6。

### 3.2.5 守卫版 v3 的 **3 个红例**及其处置（如实登记）

第一轮全量回归（v3）有 **3 个红例**，**全部**是「断言默认库＝生产库」
这一条**被守卫改掉**的**产品规则**：

| 红例 | 处置 |
|---|---|
| §test_database_path.py::test_absolute_database_path_defaults_to_default_chain_database§ | 用 §unguarded_default_store()§ 包住（**只恢复路径解析**，**写保护不受影响**） |
| §test_database_url_helpers.py::test_database_url_defaults_to_default_chain_database§ | 同上 |
| §test_stock_token_registry.py::test_page_payload_default_anchor_symbols_include_stocks§ | 同上 |

**处置的边界**：逃生舱 §unguarded_default_store()§ **只恢复路径解析**；
连接守卫（`mode=ro`）与 Rust 写守卫**不看解析结果** ⇒
包在逃生舱里的用例**仍然写不进生产库**（守卫自测
§test_the_escape_hatch_restores_the_resolution_but_not_the_write_protection§ 通过）。
**不是**「把守卫关掉」。

### 3.2.6 第二轮（整库摘要）读数

**守卫窗**（全量回归在窗口内，**1389.718 s**，**3267 passed / 3 skipped / 0 failed**）
**对** **同时长静默对照窗**（**无 pytest**，**2172.609 s**）：

| 观测 | **守卫窗**（有 pytest） | **静默对照窗**（无 pytest） | 判读 |
|---|---|---|---|
| **`kv_entries` 整表摘要**（**6068** 行） | `0f2096ab1e6bad83…` → **同值** | `0f2096ab1e6bad83…` → **同值** | **整个 KV 库**（含 `chain_markets` 等只读注册表集合）**一个字节都没变** |
| **`kv_meta` 整表摘要** | `3e195ae331670e2a…` → **同值** | `3e195ae331670e2a…` → **同值** | 迁移账本**未被碰** |
| `kv_entries` / `kv_meta` **行级**差异 | **0** / **0** | **0** / **0** | — |
| **81 张表**内容 `sha256` | **76 张完全相同** | **76 张完全相同** | — |
| **变化表清单** | **6** 张：`cow_execution_reviews`、`manager_review_library_state`、`manager_v4_review_diagnostics`、`manager_v4_reviews`、`manager_write_requests`、`v4_closed_loop_runtime_state` | **完全相同的 6 张** | **差分为空** ⇒ pytest **没有多改任何一张表** |
| `cow_execution_reviews` 行数 | 71 070 → 71 733（**+663**） | 71 070 → 72 303（**+1233**） | **对照窗涨得更多** ⇒ 运行时自身在写复核 |
| `manager_write_requests` 行数 | 90 → **93**（**+3**） | 90 → **95**（**+5**） | 同上 |
| `v4_closed_loop_runtime_state` 行数 | 不变 | 104 → **125**（**+21**） | 同上（只在对照窗里涨） |
| **① `parameters` 集合**（19 行）摘要 | `fb3a00fec9b9ee15…` → **同值** | `fb3a00fec9b9ee15…` → **同值** | 与 §3.2.2 的**闸门键**读数**互相印证** |
| **② `attempt\|local\|%` 复核残留** | **1** → **1**（摘要同） | **1** → **1**（摘要同） | **没有新增**测试残留（那 1 行是**守卫之前**的历史残留） |
| **③ `attempt\|local\|%` 尝试残留** | **0** → **0** | **0** → **0** | 不变 |
| **④ `chain_id = 0` 复核行** | **1** → **1**（摘要同） | **1** → **1**（摘要同） | 不变（同上，历史残留） |
| **⑤ `chain_id = 0` 尝试行** | **0** → **0** | **0** → **0** | 不变 |

**结论（决定性）**：两个窗口**变化的表集合完全相同**，而**唯一**能说明
「pytest 写了什么」的读数 —— `kv_entries` 整表摘要、`kv_meta` 整表摘要、
`parameters` 集合摘要、**4 个测试残留探针** —— 在**两个窗口里都完全不变**。
⇒ **测试套件对活链库的写入 = 0**（**归因后**）。

**证据**：§plan/runtime_evidence/probe_full_store_window_20261125_guard_v5.json§、
§…_control_v5.json§。

---

## 4. 归档

**闭合后必须移出 `plan/tasks/`** —— `plan/tasks/` 只放**未闭合**任务。

```powershell
# 打包四文件 ＋ docs/ → plan/archive/，并登记索引，最后删除本目录
python plan/runtime_evidence/tools/archive_task_20261124.py T-储.01
# 先预演（不落盘、不删除）：
python plan/runtime_evidence/tools/archive_task_20261124.py T-储.01 --dry-run
```

> **入口是任务号 `T-储.01`**（`AC-储.01` 是**验收号**，归档工具按**目录名**定位任务）。

工具会自动完成：
1. 四文件 ＋ `docs/` 打包为 `plan/archive/任务归档_T-12_<标题>_<日期>.md`（保留五个小节）；
2. 在 `plan/archive/README.md` §1 **归档索引**登记一行；
3. 在**一级任务** README 的二级清单标记「✅ 已闭合 · 已归档」；
4. **删除** 本目录。

> **归档件内容冻结** —— 更正走新版本 `..._v2.md`，旧版保留。
> 归档区是**受控文档区**，规则见 `plan/README.md` §1.2 与 `plan/archive/README.md`。
> **不得**再在本目录追加新任务 —— 新任务**另建目录**。

---

## ③ 修改范围守护

<a id="d033"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-储.01/D-033-SCOPE_GUARD.md` -->

# D-033 `T-储.01`｜测试套件生产库零写入 · 修改范围守护（Scope Guard）

> **本文件是 `T-储.01` 的范围契约。** 它定义**允许改什么**、**必须跑什么**、**禁止碰什么**。
> 违反本契约 ⇒ **本任务不算闭合**。
> 总纲见 `plan/README.md`；模板见 `plan/template/`。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 是否改 |
|---|---|---|---|
| `M-10` | 存储层 | `flashloan/app_runtime/tests/live_chain_store_guard.py`、`flashloan/app_runtime/tests/conftest.py` | 是 |
| `M-10` | 存储层（**守卫自测**） | `flashloan/app_runtime/tests/unit/test_live_chain_store_write_guard.py` | 是 |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

> **产品代码零改动** —— 本任务**没有**改 `flashloan/src/**` 的任何一行：
> 守卫与路径注入**全部落在测试树**内 ⇒ 对**在跑的实盘运行时零影响**。
> 原表里的 ``flashloan/src/core/db/storage_business_schema.py`` 是**模板占位**，本任务**并不改**它。

---

## 1.1 代码落点（**文件级** · 并行判定用）

> **本节把 §1 的目录粒度细化到文件级** —— 目录粒度会让**几乎所有**任务看起来都冲突，
> 失焦。**只有同文件的争用才是真争用**；目录相同而文件不同 ⇒ **可并列**。
> 本节由 `plan/runtime_evidence/tools/refresh_scope_guard_file_level_20261124.py` **实测生成**，
> **不靠人工枚举**；重跑该脚本即可刷新。
>
| 模块 | 代码落点（文件级） | 与他任务**同文件** |
|---|---|---|
| `M-10` | `flashloan/app_runtime/tests/live_chain_store_guard.py`<br>`flashloan/app_runtime/tests/conftest.py`<br>`flashloan/app_runtime/tests/unit/test_live_chain_store_write_guard.py` | **无**（本任务独占） |

> **本节为手工订正**（原表写的是模板占位落点）。生成器
> `plan/runtime_evidence/tools/refresh_scope_guard_file_level_20261124.py`
> **未重跑** —— 它会**一并重写其它任务**的 §1.1（含另一修复线程在途的 `D-029`），
> 落点已**逐条核对**：三个文件**均不在**其它任务的落点里 ⇒ **独占、无争用**。

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
> - `flashloan/app_runtime/tests/live_chain_store_guard.py`
> - `flashloan/app_runtime/tests/conftest.py`
> - `flashloan/app_runtime/tests/unit/test_live_chain_store_write_guard.py`
>
> 三个文件**均不在**其它任务的落点里 ⇒ **独占、无争用**。
## 2. 逐模块回归命令（**可逐条复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| `M-10` | 存储层 | `cargo test --offline -p manager-db` |
| `M-10` | 存储层（**守卫自测**） | `python -m pytest flashloan/app_runtime/tests/unit/test_live_chain_store_write_guard.py -q` |

```powershell
# 本任务的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p manager-db
python -m pytest flashloan/app_runtime/tests/unit/test_live_chain_store_write_guard.py -q
```

> **只跑上面这些。** 未改动模块**不重跑**。
> **不得**因「有新任务」「功能没变」跑全量。

---

## 3. 触碰边界（**禁止触碰**）

| 对象 | 禁止理由 |
|---|---|
| 除上表以外的全部模块 | **未改动模块不跑、不碰**；本任务与其它任务**不耦合** |

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

      **读数**：§1.1 的**文件级**落点共 **3** 个文件
      （§live_chain_store_guard.py§ / §conftest.py§ /
      §unit/test_live_chain_store_write_guard.py§），**全部在测试树内**；
      §flashloan/src/**§ **一行未动**（见 §D-034§ §f.1 第 4 项的 `git status` 读数）。
      **未触发** §4 的扩范围流程（**没有**触碰模块集合外的模块）。

- [x] §2 的**全部**命令**已执行**且**全绿**

      **读数**：§2 的两条命令均已执行 ——
      §cargo test --offline -p manager-db§ ⇒ **5 passed / 0 failed**；
      §python -m pytest flashloan/app_runtime/tests/unit/test_live_chain_store_write_guard.py -q§
      ⇒ **14 passed**（**2026-11-25 03:05** 复跑）。

- [x] §3 的禁止触碰项**确未被触碰**

      **读数**：§3 的禁止触碰项为「除 §1 以外的全部模块」⇒
      本任务**未改动**任何模块的生产代码（§1.1 三文件均在 §flashloan/app_runtime/tests/§ 下）；
      广播三道闸门与链级 §chain.json§ 的 §allow_official_v4_broadcast§ **一字未动**。

- [x] **未**因本任务跑过**全量**测试（若跑了，需说明原因）

      **读数（如实登记 · 与字面口径的偏差）**：本任务**确实**跑了全量回归，
      **但这不是「顺手多跑」** —— 全量回归**就是本任务的判据仪表**：
      §D-034§ §f.2 的三条验收项问的是「**pytest 全程**会不会写生产库」，
      **只有把全量套件放在被观测的窗口里跑**，这个问句才有答案。
      ⇒ **区别**：§2 的「回归范围」＝ **改动模块的测试**（已跑、已绿）；
      §D-034§ §f.2 的「全量」＝ **被测对象**（被观测窗口里的**被测量**），
      **不是**改动模块的回归范围。两者**不冲突**。
      全量回归**不重复计数**为 §2 的回归范围，也**不**用于判定其它模块的通过与否。

---

## ④ 测试目标 check list

<a id="d034"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-储.01/D-034-checklist.md` -->

# D-034 `T-储.01`｜测试套件生产库零写入 · 测试目标 check list

**为什么单独立项**：`TASK-183` 只收口了 `kv_meta` 一处。

**一次全量实测**：主库 `PRAGMA data_version` 由 **2 → 187**（**185 次提交**，

窗口**恰好**覆盖全量运行），写入分布：`kv_entries` **348**（`parameters` 集合 =

**控制面板 page state**）／ `kv_meta` **8**（`schema_migrations`）／

`cow_execution_reviews` **3** ／ `cow_execution_attempts` **3 DELETE**。



**根因五条**（语句级探针 + 只读 `data_version` 采样器钉死；

第 4、5 条是本轮施工过程中**追加**发现的，详见 `D-035` §2.4／§2.5）：



1. `core.local_kv` 的**默认路径就是主链库**（`store_path(None)` → `resolve_database_path(None)`

   → 主库），而 `bot/page_state/store.py` 等**生产读路径**直接用它

   ⇒ 测试套件**改写主库的控制面板 page state**；

2. `app_runtime/web/control_panel_liquidation_discovery.py:156/158` 写 `kv_meta.schema_migrations`

   （**与 `TASK-183` 收口的 `schema_version` 是不同的键**）

3. `core/db/builders.py:50` 等模块**按名导入** `delete_rows` ⇒ **绕过**了

   `flashloan/app_runtime/tests/conftest.py:98` 的 `_guard_real_chain_databases`

   （它守的是 `storage_business_io` 的 5 个**写函数名**，不是**连接**）。

4. **路径解析的根只有一个，但按名导入的绑定点有很多个** ——

   `core/db/database_path.py::resolve_database_path()` 是唯一根，

   `absolute_database_path()` 是它的别名；但 `core/db/database.py:23`、

   `core/db/builders.py:36`、`core/db/storage_common.py:16`、

   `core/local_kv.py:19` 等模块在**导入时**就把函数对象绑走了 ⇒

   **只改根不够**。第一版只重绑了 2 个绑定点 ⇒

   **业务表层与 Rust 桥仍然直连主链库**。

5. **Rust 桥在 Rust 侧用 `open_rw` 打开活链库** ——

   `flashloan/rs-src/src/schema.rs:10` 的 `open_rw()` 是 Rust 侧唯一入口，

   被 `ensure_database_schema` / `ensure_database_indexes` /

   `migrate_direction_schema` / `update_v4_scan_state` /

   `merge_v4_pool_index` / `build_v4_anchor_data` /

   `import_v4_evidence` / `prune_v4_evidence` 共 **10** 个写入口使用 ⇒

   **完全不经过 Python 的 `sqlite3.connect`** ⇒

   Python 层连接守卫对它们**无效**。

   **实测**：在测试机上对主链库调用 `migrate_business_direction_schema`

   **成功返回**（耗时 **0.321 s**）⇒ 它**真的读写生产**。



**证据已产出**：`plan/runtime_evidence/reports/task184_test_suite_mainnet_writes_20261123.{json,md}`

（**范围复测**（本次现场实测）：3 个测试文件 · 14.81 s · `data_version` **2 → 58**

（**56 次提交**）· DML **58** 条 ／ **全量**：`data_version` **2 → 187**

（**185 次提交**，观测窗 **943.705 s**）· DML **362** 条 ／

**静默对照 150 s 零提交** ⇒ 可归因于 pytest 进程本身）。



**生产影响（**实测读数 · 不是估计**）**：当日写入 `parameters` **19** 个键，

跨三个命名空间 —— `cow_submission_pause_guard`（**6**）、

`liquidation_pause_guard`（**9**）、`page_state`（**4**）；

值均为**默认值**（`paused=false` · `order_submission_enabled=true` ·

`consecutive_failure_count=0` · `pause_reason=null`）。

⇒ **这不是脏数据，而是把生产的两道闸门重置成默认值**：

**广播提交闸门**与**清算闸门**。若实盘当时正因真实失败连击而处于暂停态，

一次测试跑完就会**把暂停清掉**。

（**当前状态**：未暂停，**恰好就是默认值** ⇒ 本次**无需**人工恢复。）

`cow_execution_reviews` 表内 **49719** 行（绝大多数是实盘复核），

测试**净残留 1 行**（`attempt|local|0|…`，链 ID **0**、pair 为空）。



**验收前置**：无（本项可独立实施）。



#### f.1 结构验收



- [x] `conftest.py` 的守卫从「守**函数名**」改为「**守连接**」：

      对**活链库**的 sqlite 连接强制 `mode=ro`，**写即在 SQLite 层失败**（而不是静默写进去）

      **读数**：活链库连接一律走 `live_chain_store_guard.open_live_chain_store_read_only`

      （`mode=ro` ＋ `Path.as_uri()` 百分号编码）；守卫自测

      `test_the_guard_blocks_a_write_into_a_live_chain_store` 与

      `test_the_real_live_chain_store_stays_read_only` **均通过**，

      失败信息为 **§OperationalError: attempt to write a readonly database§**。

- [x] `bot/page_state/store.py` 等生产读路径的**默认路径可注入**

      （测试可指向临时库，运行时仍指向主链库）

      **读数**：改道收口在**唯一根** `core.db.database_path.resolve_database_path`

      （`live_chain_store_guard.install_store_path_redirect`）；沙箱为会话级临时目录。

      实测 `storage_common.database_url()` 与 `local_kv.store_path(None)`

      **均不再是活链库**（守卫自测 `test_the_implicit_default_store_never_resolves_to_a_live_chain_store`）。

      **运行时不受影响**：守卫全部落在测试树内，实盘进程**不加载** `conftest.py`。

- [x] `core/db/builders.py` 等**按名导入**的写入口**纳入同一守卫**

      **读数（三侧）**：

      ① **Python 直连**：`sqlite3.connect` 兜底 → 守卫自测

      `test_the_guard_covers_a_by_name_imported_write_entry` **通过**

      （`builders.delete_rows` 与 `storage_business_io.execute` 双双被拦）；

      ② **路径侧**：`install_store_path_redirect` 遍历 `sys.modules`

      **自动重绑**所有按名导入的别名 ⇒ 守卫自测

      `test_no_module_keeps_the_unredirected_path_resolver` **通过**（**0** 个漏网绑定点），

      探针 `probe_store_path_binding_20261125.py` 实测 **stale bindings = []**；

      ③ **Rust 侧**：`install_rust_write_guard` 包住 `core.rs_bridge` 的

      **10** 个写入口（并同样重绑按名导入的别名）⇒ 守卫自测

      `test_the_rust_write_guard_refuses_a_live_chain_store` **通过**（抛 `LiveChainStoreWriteError`），

      `test_the_rust_write_guard_lets_a_sandbox_write_through` **通过**（沙箱写放行）。

- [x] **未**新增主库表或迁移；**未**放宽任何既有门禁

      **读数**：`git status` 显示本任务**只改** **6** 个**测试树**文件

      （`flashloan/app_runtime/tests/**`：§live_chain_store_guard.py§（新增）、
      §conftest.py§、§unit/test_live_chain_store_write_guard.py§（新增）、
      §unit/test_database_path.py§、§unit/test_database_url_helpers.py§、
      §unit/test_stock_token_registry.py§ —— 后三个是**逃生舱**
      §unguarded_default_store()§ 的**最小包裹**）＋ `plan/**` 下的文档；
      `flashloan/src/**` **一行未动**。

      **旁证**：同一 `git status` 里另有 **3** 个测试树文件被改动
      （§test_robinhood_official_v4_config_consistency.py§、
      §test_robinhood_v4_sqlite_rebuild_tool.py§、§test_run_port.py§）——
      **属其它任务**（`T-候.01` / `T-候编储.01` / 运行时重启缺陷修复），
      **不是**本任务落的 ⇒ 已逐条核对，不计入本任务。

      广播三道闸门（`review_manager/src/lib.rs:1884-1900`、`worker_pool.rs:2600-2601`、

      `dex_opportunity_auto_execute.py:45-64`）**一字未动**；

      链级 `chain.json` 的 `allow_official_v4_broadcast` 保持 `false`。



#### f.2 行为验收



- [x] **pytest 全程主库 `PRAGMA data_version` 不变**（一次全量读数作为判据）

      **读数（全量回归窗，2026-11-25 02:46:51 → 03:08）**：
      `data_version` **2 → 2**，窗口内采样到 **0** 次跳变；
      同时长静默对照窗（**2102.234 s**，无 pytest）**同样 2 → 2**，采样到 **2** 次跳变。

      **⚠️ 口径订正（如实登记 · 不美化）**：这一条的**字面口径**
      「`data_version` 全程不变」**不是可靠判据** —— 在 **WAL** 下它**不与每次提交一一对应**：
      缺陷复现的 **9.10 s** 短窗里闸门键被改写 **4** 个，它仍然 **2 → 2**（**0** 次跳变）；
      而 `TASK-184` 的 **943.705 s** 长窗里 **185** 次提交，它才从 **2 → 187**；
      守卫后的 **2102.234 s** 对照窗（运行时自身写了 **1124** 行复核）它也只跳了 **2** 次。
      ⇒ 本条**按订正后的口径**闭合：判据换成**闸门键逐字节比对**（见下条与
      §D-032§ §3.2.3），`data_version` **降级为辅助读数**。
      **订正依据**：§D-035§ §2.6（**不推翻** `TASK-184` 的语句级计数结论）。

- [x] **pytest 全程主库文件 mtime / size 不变**

      **读数（全量回归窗）**：主库 **12 126 711 808 → 12 223 111 168 bytes**（**+92 MB**）、
      `-wal` **33 166 032 → 55 401 672 bytes**、mtime 全部变化
      ⇒ **字面口径未达成**。

      **归因（如实登记 · 不是「未达成就当达成」）**：
      活链库**同时**被**实盘运行时**写入 ⇒ 文件变化**不能**直接归给测试。
      归因读数三条：
      ① **同时长静默对照窗**（**无 pytest**，**2102.234 s**）里主库涨得**更多**：
      **+184 MB / 2102 s ＝ 87.6 MB/1000 s**，
      比守卫窗的 **+92 MB / 1304 s ＝ 70.6 MB/1000 s** 还大
      ⇒ 文件变化**不可能**由更短的 pytest 窗口造成；
      ② `cow_execution_reviews` 守卫窗 **+555** 行、对照窗 **+1124** 行
      （**对照窗涨得更多**）⇒ 写入者是**运行时**，不是 pytest；
      ③ 真正按内容判据测：**闸门键 0 变化 ＋ `kv_meta` 0 变化
      ＋ `kv_entries`／`kv_meta` **整表摘要**完全相同
      ＋ **81** 张表里 **76** 张**完全相同**、余 **6** 张在**同时长无 pytest 的对照窗里同样变化**
      （§D-032§ §3.2 / §3.2.6）。

      **诚实边界**：「活链库文件字节级零变化」在**运行时同时写入**的前提下
      **不成立**；本任务能证明的是「**测试套件对活链库的写入 = 0**」（归因后）。
      **不**把字面口径伪装成达成。

- [x] 生产闸门状态**不被测试改写**：`cow_submission_pause_guard` / `liquidation_pause_guard`

      的**每一个键**在一次全量前后**逐字节一致**（默认值也**不许**被写）

      **读数（**决定性**）**：全量回归窗（**1303.734 s**）内
      **19 个** `parameters` 键（**`cow_submission_pause_guard` 6 个
      ＋ `liquidation_pause_guard` 9 个 ＋ `page_state` 4 个**）
      的 §value_json§ ＋ §updated_at§ **逐字节**比对 ⇒ **changedCount = 0**；
      同时长静默对照窗（**2102.234 s**）**同样 changedCount = 0**。

      **覆盖面核实**：§parameters§ 集合**一共 19 行**（实测），
      与探针 §gateKeyCount = 19§ **相等** ⇒ **不是抽样，是全集合**。

      **整库摘要（第二轮，判据扩到全库）**：`kv_entries` **整表**（**6068** 行）摘要
      **完全相同**、`kv_meta` **整表**摘要**完全相同**；**81** 张表里 **76** 张内容摘要**完全相同**；
      余 **6** 张（`cow_execution_reviews` / `manager_review_library_state` /
      `manager_v4_review_diagnostics` / `manager_v4_reviews` / `manager_write_requests` /
      `v4_closed_loop_runtime_state`）在**同时长无 pytest 的对照窗里变化的是完全相同的 6 张**
      ⇒ **差分为空** ⇒ pytest **没有多改任何一张表**。
      **测试残留探针**（**4** 个）全部**不变**：
      `attempt\|local\|%` 复核 **1 → 1**（摘要同）、尝试 **0 → 0**、
      `chain_id = 0` 复核 **1 → 1**（摘要同）、尝试 **0 → 0** ⇒ **没有新增残留**。

      **对照（同一范围复测，证明测量本身敏感）**：
      **守卫之前**同一范围复测改写 **4** 个键（全是 `page_state|*`）；
      **守卫之后**同一范围复测改写 **0** 个；
      **同时长静默对照**改写 **0** 个 ⇒ **归因成立**。

      **全量回归结果**：**3267 passed / 3 skipped / 0 failed**（**1289.65 s**）
      ⇒ **全量套件自身全绿**，不是靠跳过用例换来的 0。

- [x] 守卫**非空洞**：临时关闭守卫 ⇒ **稳定红**（写操作被拦截）

      **读数**：守卫自测 `test_the_guard_is_what_blocks_the_write` 用**反证构造** ——

      把 `live_chain_store_root()` 指向别处后，**同一个写入成功**（行数 1）⇒

      拦住它的**就是守卫本身**；另守卫开着时同一写入**被拦**（行数 0）。

      **现场复现**：守卫**之前**的范围复测把闸门键改写了 **4** 个，

      同窗口的**静默对照**改写 **0** 个；守卫**之后**同一复测改写 **0** 个。

- [x] 活链库的**只读**访问**仍然可用**（守卫不得把合法读路径一起打死）

      **读数**：守卫自测 `test_the_guard_leaves_the_read_paths_usable` 与

      `test_the_real_live_chain_store_stays_readable` **均通过**；

      功能子集 **296 passed / 301.33 s**，其中包含 **2** 个**故意读生产数据**的用例

      （`test_flash_loan_pool_page_keeps_robinhood_testnet_on_its_own_scope`、

      `test_flash_loan_pool_page_prepares_robinhood_mainnet_v4_scope`）——

      它们**读活链库成功**（`borrow_asset_count` > 0），**写**则被守卫拦。



#### f.3 边界验收（**不得**发生）



- [x] **未**写库、**未**广播

      **读数**：本任务全程**只读**（无写库授权）；守卫的**作用**恰恰是拦写。

      未发起任何广播调用；链级 `chain.json` 的 `allow_official_v4_broadcast` 保持 `false`，

      §.env§ / §.env.example§ 既有取值**未改**。

- [x] **未**改动方向池或记录层的生产代码

      **读数**：`git status` 中本任务的改动**只有** **6** 个**测试树**文件

      （`flashloan/app_runtime/tests/live_chain_store_guard.py`（新增）、

      `flashloan/app_runtime/tests/conftest.py`、

      `flashloan/app_runtime/tests/unit/test_live_chain_store_write_guard.py`（新增）、

      `flashloan/app_runtime/tests/unit/test_database_path.py`、

      `flashloan/app_runtime/tests/unit/test_database_url_helpers.py`、

      `flashloan/app_runtime/tests/unit/test_stock_token_registry.py`

      —— 后三个是**逃生舱** §unguarded_default_store()§ 的**最小包裹**，

      **只恢复路径解析**，**写保护不受影响**）

      ＋ `plan/**` 下的文档；**方向池层**（`M-3`）、**闭环层**（`M-4`）、

      **记录层**（`M-6`／`M-7`）的**生产代码一字未动**。

---

## 导航

<a id="readme"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-储.01/README.md` -->

# `T-储.01`｜测试套件生产库零写入

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../README.md)。

**编号**：`T-储.01`（纯 ID）　**验收**：`AC-储.01`　**优先级**：P1　**波次**：W-4
**编号逻辑**：`T-储.01` / `AC-储.01` —— **模块内（单模块索引）**，编号索引 **`储`**（改动模块：`M-10`（存储层（schema / 迁移 / 记录剥离））），该索引号段内第 `01` 个。（详见 `PLAN.md` §1.0）

**改动模块（= 回归范围）**：`M-10`

**前置模块**：—　　**需授权**：否（只读）

**状态**：✅ **已闭合**（2026-11-25）—— **11** 项验收**全闭合**；
守卫自测 **14 passed**、Rust `manager-db` **5 passed**、
**全量回归 3267 passed / 3 skipped / 0 failed**（**1289.65 s**）⇒
闸门键（**19** 个，**全集合**）**逐字节 0 变化**、`kv_meta` **0**；
**整库摘要**：`kv_entries`／`kv_meta` **整表**摘要**完全相同**、
**81** 张表里 **76** 张**完全相同**，余 **6** 张**对照窗同样变化** ⇒ **差分为空**；
**同时长静默对照**同样 **0** 且主库文件涨幅**更大** ⇒ **归因成立**。
读数见 [`D-032`](#d032) §3.2 ＋ [`D-035`](#d035) §7。

## 文件

| 文件 | 内容 |
|---|---|
| `PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `SCOPE_GUARD.md` | 本任务的修改范围守护与逐模块回归命令 |
| `checklist.md` | 本任务的逐条验收框（**11** 项，**已全部闭合**） |
| `docs/` | 本任务的**独立技术文案**区（设计推导 / 口径定义 / 读数记录） |

## 独立回归

```powershell
cargo test --offline -p manager-db
python -m pytest flashloan/app_runtime/tests/unit/test_live_chain_store_write_guard.py -q
```

> **只跑上面这些。** 未改动模块**不重跑**。

---

## ⑤ 技术文案区（`docs/`）

> **原 `docs/` 目录已并入本归档件** —— 逐份保留，锚点见下。

<a id="d035"></a>

### `D-035-现状与可行性分析.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-储.01/docs/D-035-现状与可行性分析.md` -->

# D-035 `T-储.01`｜测试套件生产库零写入 · 现状与可行性分析

> **本文件是 `T-储.01` 的现状与可行性分析**（2026-11-25 实测重写）。
> 每一条结论都标注文件与行号，可被独立核验；每一条读数都标注证据文件。

---

## 1. 分析对象与结论口径

**分析对象**：Python 测试套件对**活链库**（`flashloan/db/local_store_*.sqlite3`）的写入。

**结论口径**：

| 口径 | 含义 |
|---|---|
| **缺陷存在** | 一次 pytest 运行**确实**改写了生产库的行 |
| **归因成立** | 同一时间窗内的**静默对照**（同样时长的采样，无 pytest）**没有**改写 |
| **守卫非空洞** | 守卫**开着**时写被拦、**关掉**时写能过 ⇒ 拦住它的就是守卫本身 |
| **读不被打死** | 守卫**不**把合法的只读路径一起打死 |

**诚实边界**：活链库**同时**被在跑的实盘运行时写入 ⇒ 单次前后对比**不能**归因；
因此本分析**一律**用「**范围复测窗 vs 静默对照窗**」的差分，并额外比对
**生产闸门键的逐字节内容**（见 §2.4 的方法学订正）。

---

## 2. 现状：五条根因（代码级定位）

### 2.1 根因一：KV 存储的「默认路径」就是主链库

路径解析只有一处真相：`core/db/database_path.py::resolve_database_path()`（§57-70）。

§§§text
explicit url  →  current_chain_scope()  →  FLASHLOAN_KV_PATH  →  default_database_path()
§§§

而 §default_database_path()§（§48-54）→ §default_chain_database_path()§（§35-45）
→ 链注册表的**默认链** = **4663 = 主网** ⇒
§flashloan/db/local_store_4663.sqlite3§。

**放大器**在测试侧：§flashloan/app_runtime/tests/conftest.py:72§ 的
§_isolate_flashloan_kv_path§ 把 §FLASHLOAN_KV_PATH§ **pop 掉**
（docstring：§.env§ 自带该变量），于是**默认解析**落到主链库。

**写入链**（逐跳核对过）：

§§§text
bot/page_state/store.py::_save_to_database            (src/bot/page_state/store.py:93-102)
  → core.parameter_config.save_page_state_parameter_map (src/core/parameter_config.py:170)
  → core.db.storage_control_parameters.save_control_panel_parameter_map (src/core/db/storage_control_parameters.py:37)
  → core.kv_rows.upsert_row / ups_rows              (src/core/kv_rows.py:16/26/31)
  → core.local_kv.put / put_many                        (src/core/local_kv.py:172/202)
  → core.local_kv._connect                              (src/core/local_kv.py:45)
§§§

⇒ **测试把夹具写进了主网库**。实测被改写的 4 个键**全部**是 §page_state|*§
（§account_scan§ / §debt_pool§ / §execution§ / §market_observation§），
其 §updated_at§ 变成**测试运行时刻**，value 里带着**测试夹具数据**
（§0x…0001§ 账户、§AVAXUSDT/BTCUSDT§ 波动告警、§fork_simulation_failed§）。

### 2.2 根因二：守卫守的是「函数名」，而写入口**按名导入**

改造前的守卫（§conftest.py:98-170§）守的是
§core.db.storage_business_io§ 里的 5 个**写函数名**
（§execute§ / §executemany§ / §replace_rows§ / §transaction§ / §delete_rows§），
外加两个**已知**的按名导入模块。

而 §core/db/builders.py:50§ 是：

§§§python
from core.db.storage_business_io import delete_rows, query
§§§

⇒ **按名导入的绑定绕过守卫**：守卫改的是 §storage_business_io.execute§ 这个属性，
而 §builders.delete_rows§ 仍然指向**原函数**。守卫因此**只挡住了它知道的那些入口**。

### 2.3 根因三：直连 §sqlite3.connect§ 绕过两个连接工厂

写入路径全部收敛到**两个连接工厂**，但**还有第三条路**：

| 路径 | 位置 | 性质 |
|---|---|---|
| §core.local_kv._connect§ | §src/core/local_kv.py:45§ | KV 工厂（§kv_entries§ / §kv_meta§） |
| §core.db.storage_business_io._connect§ | §src/core/db/storage_business_io.py:19§ | 业务表工厂（§cow_execution_*§ 等） |
| **直连 §sqlite3.connect§** | §src/core/db/storage_business_schema.py:822/833/882§、§src/core/db/storage_v4_exchange.py:561§ | **绕过上面两个** |

⇒ 只改两个工厂**仍然不是结构性保证**：§_python_ensure_business_schema§ 的 DDL
（§storage_business_schema.py:816-826§）会直接在目标库上建表。

### 2.4 根因四：路径解析**只有一个根**，但**按名导入的绑定点有很多个**

路径解析的**唯一根**是 `core/db/database_path.py::resolve_database_path()`
（§57-70）；`absolute_database_path()` 是它的别名（§74-79）。

但是**消费方**有 N 个，且分成两类：

| 类型 | 例子 | 是否自动跟随根的改动 |
|---|---|---|
| **函数内导入** | `core/db/storage_business_schema.py:789`、`core/rs_bridge.py:49`、`core/flash_loan_pool_discovery.py:402` | ✅ 会（调用时从模块全局取） |
| **模块级导入** | `core/db/database.py:23`、`core/db/builders.py:36`、`core/db/storage_common.py:16`、`core/local_kv.py:19`、`core/route_symbol_identity.py:18`、`core/db/page_payloads.py:16` …… | ❌ **不会**（导入时就把函数对象绑走了） |

⇒ **只改根不够**：模块级导入的绑定点**各自持有原函数对象**，
必须**一并重绑**才能让改道生效。

**第一版实现就踩了这个坑**：只重绑了 `core.local_kv` 与 `core.db.storage_common`
两个绑定点 ⇒ **业务表层**（`core.db.database`）与 **Rust 桥**
（`core.rs_bridge`）仍然解析到主链库。

**实测证据（第一版的实测缺陷）**：`core.rs_bridge._database_path(None)`
返回 `E:\…\flashloan\db\local_store_4663.sqlite3`（**主链库**）。

### 2.5 根因五：Rust 桥在 **Rust 侧**用 `open_rw` 打开活链库

`flashloan/rs-src/src/schema.rs:10` 的 `open_rw()` 是 Rust 侧唯一的库入口：

| 入口 | 位置 | 动作 |
|---|---|---|
| `ensure_database_schema` | `schema.rs:264` | DDL ＋ `INSERT OR REPLACE INTO kv_meta` |
| `ensure_database_indexes` | `schema.rs:324` | DDL |
| `migrate_direction_schema` | `schema.rs:334` | `ALTER TABLE` ＋ `UPDATE` |
| `update_v4_scan_state` | `lib.rs:619` | 写 |
| `merge_v4_pool_index` | `lib.rs:653` | 写 |
| `build_v4_anchor_data` | `lib.rs:682` | 写 |
| `import_v4_evidence` / `prune_v4_evidence` | `evidence.rs:951/1028` | 写 / 删 |

⇒ 这些入口**根本不经过 Python 的 `sqlite3.connect`** ⇒
**Python 层的连接守卫对它们完全无效**。

**实测证据（现场）**：`migrate_business_direction_schema(主链库)`
在测试机上**成功返回**，耗时 **0.321 s** —— 它**真的打开了主链库并跑了迁移**。

**副作用（性能）**：主链库 **11.86 GB**。每次业务层建表/迁移都在
11.86 GB 的库上做 DDL ⇒ **单次 0.3 s**，被测试用例反复触发 ⇒
**全量回归慢 4 倍**（实测见 §7.5）。

### 2.6 方法学订正：WAL 模式下 §PRAGMA data_version§ **不是**可靠判据

**两次实测给出的是同一件事的两面**（**必须合起来读，否则会自相矛盾**）：

| 窗口 | 观测方式 | §data_version§ | 闸门键改写 |
|---|---|---|---|
| **9.10 s**（28 个用例，缺陷存在时） | **独立观测连接**采样 | **2 → 2（0 次跳变）** | **4 个** |
| **943.705 s**（全量，缺陷存在时，`TASK-184`） | 进程内语句级探针 ＋ 独立连接采样 | **2 → 187（185 次跳变）** | **348 + 8 + 3 + 3** 条 DML |
| **1303.734 s**（全量，守卫后） | **独立观测连接**采样 | 2 → 2（**0** 次跳变） | **0** |
| **2102.234 s**（同时长静默对照，无 pytest） | **独立观测连接**采样 | 2 → 2（**2** 次跳变） | **0**（运行时自身在写复核） |

**结论（口径）**：§data_version§ **既不是逐提交计数器**，**也不是**「全程不变」判据。
在 **WAL** 下它的刷新**不与每次提交一一对应** —— 短窗口里**可能完全不跳**
（9.1 s 内 4 个键被改写，仍然 2 → 2），长窗口里才会跳
（943 s 内 185 次提交，2 → 187），且跳变**稀疏**（守卫后 **2102 s** 的对照窗只跳 **2** 次，
而同期运行时写入 **1124** 行复核）。

⇒ **判据改用「生产闸门键的逐字节比对」**（§value_json§ ＋ §updated_at§ 双字段），
§data_version§ 降级为**辅助读数**。这是本任务的**主**归因口径
（第二层 = **整库摘要**，见 §7.6），
也是 §D-034§ 的 f.2 判据**改写**的原因。

> **与 `TASK-184` 报告的关系（不推翻它）**：`TASK-184` 的 **185 次提交 / 362 条 DML**
> 是**语句级探针**直接计数的结果（探针挂在 `sqlite3` 上，**逐条**抓 DML），
> **该结论成立且不受本次订正影响**；被订正的只是「**用 `data_version` 当判据**」这一条。

**同一原则的第二条订正（2026-11-25 补）**：**主库文件的 mtime / size**
同样**不能**直接当判据 —— 活链库**同时**被**实盘运行时**写入
⇒ 窗口内文件**确实**变（实测：守卫窗 **+92 MB / 1304 s**，
**同时长无 pytest 的对照窗 +184 MB / 2102 s**，**对照窗涨得更多**）
⇒ 文件变化**归因于运行时**，**不归因于测试**。
⇒ 本任务证明的是「**测试套件对活链库的写入 = 0**」（**归因后**），
**不是**「活链库文件不动」—— 后者在活链库被运行时写入的前提下**不成立**，
**不**伪装成达成。完整读数见 §D-032§ §3.2.3。

---

## 3. 可行性论证

### 3.1 支撑一：§mode=ro§ 在 SQLite 层拒绝写入（**已实测**）

实测（§plan/runtime_evidence/tools/probe_readonly_uri_modes_20261125.py§）：

§§§text
file:///E:/2026OPC%E5%A4%A7%E8%B5%9B/flashLoan/flashloan/db/local_store_4663.sqlite3?mode=ro
  打开成功：kv_meta 4 行、cow_execution_reviews 67 418 行
  写入尝试 → OperationalError: attempt to write a readonly database
§§§

⇒ 拦截发生在 **SQLite 层**，**不是** Python 层的「先检查再放行」⇒
**没有 TOCTOU 窗口**，也没有「换个入口就能绕过」的可能。

### 3.2 支撑二：只读连接工厂**已经存在**

§core/db/database.py::Database.connect_read_only()§（§69-83§）已经在用
§mode=ro§ URI。守卫**复用**这个既有约定（而不是引入第二套语义）。

### 3.3 支撑三：默认路径的注入点**已经存在**

§resolve_database_path()§ 的第 3 级就是 §FLASHLOAN_KV_PATH§。
⇒ 「测试可指向临时库、运行时仍指向主链库」**不需要**新增产品机制，
只要求测试**不要把**这个变量**摘掉**，而是**指向**沙箱。

### 3.4 支撑四：守卫可以**完全落在测试树内** ⇒ 产品代码零改动

守卫的价值是**拦住连接**，不是改产品逻辑 ⇒ 全部改动都在
§flashloan/app_runtime/tests/**§ 内：

| 收益 | 说明 |
|---|---|
| **实盘零影响** | 在跑的运行时**不加载** §conftest.py§ ⇒ 行为**一字未变** |
| **不与在途线程争用** | 其它修复线程在途的落点（§src/core/db/*§、§tools/*§、§rs-src/*§）**一个都没碰** |
| **可随时回退** | 删掉测试树里的 3 个文件即回到原状 |

### 3.5 支撑五：只读连接**不**打断合法读路径

§mode=ro§ 只拦**写**。所有「读活链库元数据」的路径
（§chain_database_path§ / §storage_business_io.query§ / §local_kv.scan§）
**照常工作** ⇒ 那些**故意**读生产数据的用例**不受影响**。

### 3.6 支撑六：改道的**唯一收口点**是路径解析的根

§resolve_database_path()§ 是路径解析的**唯一根**，而
§absolute_database_path()§ 通过**模块全局查找**调用它 ⇒
改掉根，**所有**别名（无论何时导入）**自动跟随**。

只有**直接绑定 §resolve_database_path§ 本身**的模块需要**额外重绑**；
这些绑定点可以**自动枚举**（遍历 §sys.modules§，按**函数对象 id** 匹配）
⇒ **不需要人工维护清单**，新增导入点也不会漏。

这一条同时覆盖了 **Rust 桥**：§core/rs_bridge.py:49§ 在**函数内**导入
§absolute_database_path§ ⇒ 自动跟随 ⇒ **Rust 桥与 Python 侧走同一条路**。

### 3.7 支撑七：Rust 侧写入口**可枚举**且**可拒绝**

Rust 侧写入口**只有一份清单**（§schema.rs§ 的三个建表/迁移 ＋
§lib.rs§ / §evidence.rs§ 的五个数据写 ＋ 两个 BNB 写），
且**全部经由 §core.rs_bridge§ 的同名函数**暴露给 Python。

⇒ 在**测试树**里包一层即可：**目标命中活链库 ⇒ 抛错**。
这不需要改 Rust（Rust 是实盘运行时，改它等于改生产行为），
也**不需要**逐个改 §src/§ 里的调用点。

---

## 4. 设计（改了什么 / 没改什么）

### 4.1 改了什么（**3 个文件，全在测试树内**）

| 文件 | 改动 |
|---|---|
| §flashloan/app_runtime/tests/live_chain_store_guard.py§ | **新增**：① 共享判定（什么算「活链库」／如何只读打开）；② **改道器** §make_store_path_redirect§ ／ §install_store_path_redirect§（收口到路径解析的根，并**自动重绑**按名导入的别名）；③ **Rust 写守卫** §install_rust_write_guard§（命中活链库即抛错） |
| §flashloan/app_runtime/tests/conftest.py§ | **改**：① 守卫从「守**函数名**」改为「**守连接**」——§Database.connect§ ＋ §core.local_kv._connect§ ＋ **§sqlite3.connect§ 兜底**；② **默认存储路径改道**——未绑定链作用域时，解析到活链库的**默认路径**（KV ＋ 业务表 ＋ Rust 桥）改道**会话沙箱**；③ 装上 **Rust 写守卫** |
| §flashloan/app_runtime/tests/unit/test_live_chain_store_write_guard.py§ | **新增**：守卫自测（**14** 项） |

### 4.2 没改什么（**产品代码零改动**）

§flashloan/src/**§ **一行未动**；方向池层、记录层、复核层、广播层**一字未动**；
**未**新增主库表、**未**新增迁移、**未**放宽任何既有门禁。

### 4.3 为什么「默认路径注入」只覆盖 **KV 层**（**已订正 · 历史版本**）

> **⚠️ 本节记录的是**第一版**（KV 单层注入）**的失败与订正过程，
> 保留以便复核；**最终实现**见 §4.4。

第一次实现把注入做成了**全局**（凡解析到活链库就改道沙箱）⇒
**业务读也被改道** ⇒ 2 个**故意读生产数据**的用例失败：

§§§text
FAILED flashloan/app_runtime/tests/functional/test_web_app_assembly.py::test_flash_loan_pool_page_keeps_robinhood_testnet_on_its_own_scope
FAILED flashloan/app_runtime/tests/functional/test_web_app_assembly.py::test_flash_loan_pool_page_prepares_robinhood_mainnet_v4_scope
§§§

（§borrow_asset_count§ 期望 > 0、§view["status"]§ 期望不存在 —— 沙箱里没有这些行。）

**订正**：注入只覆盖 **KV 存储及其门面**（§core.local_kv§ ＋
§core.db.storage_common§），**且只在没有绑定链作用域时**生效。

| 判据 | 改道沙箱 | 留在活链库 |
|---|---|---|
| 未绑定链作用域的 KV 默认路径 | ✅ | |
| **已绑定链作用域**的路径（Web 请求按 §chain_id§ 进 §chain_database_scope§） | | ✅ |
| 测试**显式**传入的路径 | | ✅ |
| 业务表路径（§core.db.database§） | | ✅ |

⇒ **「默认路径」= 没有绑定作用域时的进程默认**，这正是缺陷一的原义；
而**故意读生产数据**的用例**保持原状**（读照旧，写被守卫拦）。

### 4.4 最终设计（**统一改道 ＋ 三层守卫**）

**第 0 层 · 改道（唯一的路径决策点）**

```text
resolve_database_path(database_url)
  ├─ 显式传了路径      → 原样返回（调用方点名了库）
  ├─ 绑定了链作用域    → 原样返回（该链的库；读照旧，写被守）
  └─ 未绑定的进程默认  → 命中活链库时改道到「会话沙箱」
```

改道落在**根**上 ⇒ **KV 层、业务表层、页面载荷层、Rust 桥**同时生效。

**第 1 层 · 连接守卫（Python 写）**

活链库一律以 `mode=ro` 打开 ⇒ 写**在 SQLite 层**失败。
装在**三个**入口：`Database.connect` ／ `core.local_kv._connect` ／
**`sqlite3.connect` 兜底**（直连模块）。

**第 2 层 · 写守卫（Rust 写）**

Rust 侧 `open_rw` 不经过 Python ⇒ 由 `install_rust_write_guard` 包住
**全部 10 个**写入口：目标命中活链库 ⇒ **抛 `LiveChainStoreWriteError`**。

**三层的分工**：

| 层 | 拦什么 | 效果 |
|---|---|---|
| 改道 | 未绑定的**默认**路径 | 写**落不到**活链库（多数情况根本不会去） |
| 连接守卫 | Python 的**直连**写 | 写**在 SQLite 层**失败 |
| 写守卫 | Rust 的 **`open_rw`** 写 | 写**显式失败**（不静默落库） |

**为什么「绑定了链作用域」不改成道**：
Web 请求按 §chain_id§ 进 §chain_database_scope§ 后，页面要读**该链的生产数据**
（flash loan 池页面读活链的 borrow asset 宇宙）⇒ **读必须留在活链库**；
同一时刻**写**由第 1／2 层拦死 ⇒ **读写分离、只读放行**。

---

## 5. 影响面

| 面 | 影响 |
|---|---|
| **实盘运行时** | **零**（守卫在测试树内） |
| **产品代码** | **零**（§flashloan/src/**§ 一行未动） |
| **测试套件** | 未绑定的**默认**写入改道沙箱；活链库写入**一律被拦**（Python 层 ＋ Rust 层） |
| **读生产数据的用例** | **不受影响**（§mode=ro§ 只拦写） |
| **性能** | 沙箱建库 **0.203 s**（会话级，一次）；**不再在 11.86 GB 的主链库上反复跑 DDL**（见 §7.5） |
| **其它线程** | 落点**全在测试树**，与在途的 §src/core/db/*§、§tools/*§、§rs-src/*§ **零重叠** |

---

## 6. 风险与残留

| ID | 风险 | 缓解 | 残留 |
|---|---|---|---|
| R-1 | 守卫把**合法读**一起打死 | §mode=ro§ 只拦写；守卫自测里有「只读路径仍可用」一项 | **无** |
| R-2 | 守卫被绕过（换入口） | 守卫装在**三个**连接入口上（两个工厂 ＋ §sqlite3.connect§ 兜底） | 新增直连入口时**必须**重跑守卫自测 |
| R-2b | **Rust 侧绕过**（§open_rw§） | 第 2 层写守卫包住**全部 10 个** Rust 写入口；命中活链库即**抛错** | Rust 侧**新增**写入口时**必须**补进 §RUST_WRITE_FUNCTIONS§ 并重跑守卫自测 |
| R-3 | 沙箱让**故意读生产数据**的用例失真 | 改道**只在无作用域时**生效；绑了作用域的路径**留在活链库** | 需新增「显式读活链库」的用例时，**必须**显式传路径或绑作用域 |
| R-4 | 会话沙箱跨用例共享 ⇒ 状态互染 | 与改造前「共享生产库」相比是**收紧**；如需隔离可按用例建沙箱 | 低 |
| R-5 | 只读连接在 WAL 库上需要 §-shm§ 存在 | 实盘运行时持有该库 ⇒ §-shm§ 常在；探针已实测打开成功 | 运行时停机且 §-shm§ 缺失时，只读打开会失败（**报错而非静默写**） |
| R-6 | 写守卫**误伤**合法的沙箱写 | 守卫**只**拦命中活链库的目标；沙箱目标**放行**（守卫自测有「沙箱写放行」一项） | **无** |
| R-7 | 「读在活链库／写在沙箱」的**不对称** | 只在**绑了链作用域**时才会出现；此时**写会显式失败**而不是静默错位 | 若将来有「写作用域库再读回来」的用例，**必须**改为绑作用域到临时库 |
| R-8 | **判据本身**被误读（「文件没变」才算零写入） | 活链库**同时**被实盘运行时写入 ⇒ 用「**同时长静默对照**」做**差分**：对照窗涨得**更多**（**87.6 vs 70.6 MB/1000 s**）⇒ 变化**不归因于测试** | **字面口径（「活链库文件零变化」）不成立**，本任务证明的是「**测试套件写入 = 0**」（**归因后**）—— §2.6 已把 `data_version` 与文件 mtime/size **双双降级为辅助读数** |
| R-9 | **新增**的写入口**绕过**三层守卫 | 守卫装在**三个**连接入口 ＋ **10** 个 Rust 写入口 ＋ 路径解析**唯一根**（自动重绑按名导入的别名 ⇒ 漏网绑定点实测 **0**） | 未来新增入口时**必须**重跑守卫自测（含「**无漏网绑定点**」一项） |

---

## 7. 实测读数

### 7.1 缺陷存在时（2026-11-25 现场 · **守卫之前**）

| 观测 | 静默对照（30 s，无 pytest） | 范围复测（28 用例 / 9.10 s） |
|---|---|---|
| §PRAGMA data_version§ | 2 → 2（0 次递增） | 2 → 2（**0 次递增**） |
| **闸门键（19 个）字节级比对** | **changedCount = 0** | **changedCount = 4** |
| §kv_meta§ | 0 | 0 |
| §cow_execution_reviews§ | 67 084 → 67 084 | 67 084 → 67 084 |
| §cow_execution_attempts§ | 0 → 0 | 0 → 0 |

被改写的 4 个键**全部**是 §page_state|*§（§account_scan§ / §debt_pool§ /
§execution§ / §market_observation§），§updated_at§ = 测试运行时刻，value 含**测试夹具数据**
⇒ **归因成立**（对照窗零改写）。

**证据**：§plan/runtime_evidence/probe_live_db_writes_20261125_control.json§、
§…_scoped.json§。

### 7.2 第一版守卫（KV 单层注入）· 同一范围复测

> **⚠️ 这一版**只覆盖了 KV 层 ⇒ **业务表层与 Rust 桥仍然直连主链库**
> （见 §2.4／§2.5）。它的读数**只对该范围有效**，
> **不能**当作全量判据 —— 后续版本见 §7.4。

| 观测 | 读数 |
|---|---|
| 用例结果 | **28 passed** / 8.85 s |
| §PRAGMA data_version§ | 2 → 2 |
| **闸门键字节级比对** | **changedCount = 0** |
| §kv_meta§ 字节级比对 | **changedCount = 0** |
| §cow_execution_reviews§ | 67 639 → 67 639 |
| §cow_execution_attempts§ | 0 → 0 |

**证据**：§plan/runtime_evidence/probe_live_db_writes_20261125_guard_v2.json§。

### 7.3 其它实测

| 项 | 读数 | 证据 |
|---|---|---|
| §mode=ro§ 打开活链库 | 成功（§kv_meta§ 4 行、§cow_execution_reviews§ 67 418 行） | §probe_readonly_uri_modes_20261125.py§ |
| §mode=ro§ 下的写入 | **§OperationalError: attempt to write a readonly database§** | 同上 |
| 沙箱建库成本 | **0.203 s** / 389 120 bytes | §probe_sandbox_schema_cost_20261125.py§ |
| 守卫自测（第一版） | **7 passed** | §test_live_chain_store_write_guard.py§ |
| **Rust 桥的默认落点**（第一版实测） | **主链库** `…\db\local_store_4663.sqlite3` ⇒ 证明业务层与 Rust 桥**未被**第一版覆盖 | §probe_rust_bridge_live_cost_20261125.py§ |
| **Rust 迁移在主链库上的实测** | **成功返回**（耗时 **0.321 s**）⇒ Rust 侧**确实**读写生产 | 同上 |
| 守卫自测（当前版） | **14 passed** | §test_live_chain_store_write_guard.py§ |
| 功能子集（当前版） | **296 passed / 301.33 s**（含那 2 个**故意读生产数据**的用例） | §t401_guard_functional_unified.log§ |

### 7.4 全量回归（**当前版**）

**最终读数见 §D-032§ §3.2**（**取自补登记之后重跑的全量回归**）。要点摘录：

| 项 | 读数 |
|---|---|
| 全量回归结果 | **3267 passed / 3 skipped / 0 failed**（**1289.65 s**） |
| 闸门键（19 个）逐字节比对 | **changedCount = 0** |
| `kv_meta` 逐字节比对 | **changedCount = 0** |
| 窗口 | **1303.734 s**（同时长静默对照 **2102.234 s**，同样 **0 / 0**） |

### 7.5 性能实测（**为什么必须收口到路径解析的根**）

| 观测 | 第一版（KV 单层注入） | 当前版（统一改道） |
|---|---|---|
| 全量回归进度 | **393 s 只跑到 6%** | **1289.65 s 跑完全量**（**3267 passed / 3 skipped / 0 failed**） |
| 功能子集（296 例） | **257.46 s** | **301.33 s** |
| 慢用例 Top 1 | 25.53 s | 28.28 s |

**根因**：第一版让**业务层**仍然解析到主链库 ⇒
每次业务层建表/迁移都在 **11.86 GB** 的主链库上跑 DDL
（实测单次 **0.321 s**）⇒ 被用例反复触发。

⇒ 收口到路径解析的根之后，**业务层改道沙箱**，DDL 在**空库**上跑
⇒ **性能回到基线量级**，同时**生产库不再被迁移**。

### 7.6 第二轮：**整库摘要**（把判据扩到全库）

> **第一轮（v4）的覆盖面**：§parameters§ 集合 **19 行** ＋ `kv_meta` **整表**。
> **第二轮**把覆盖面扩到**每一张表**：行数 ＋ 内容 `sha256`
> （**81 张表**取摘要；`cow_execution_reviews` 因**运行时持续追加**只取行数），
> 另加 `kv_entries` / `kv_meta` **整表摘要** ＋ **5 个测试残留**定向探针
> （`attempt|local|%` 键、`chain_id = 0` 行、`parameters` 集合）。

**守卫窗**（全量回归在窗口内，**1389.718 s**，**3267 passed / 3 skipped / 0 failed**）
**对** **同时长静默对照窗**（**无 pytest**，**2172.609 s**）：

| 观测 | **守卫窗**（有 pytest） | **静默对照窗**（无 pytest） | 判读 |
|---|---|---|---|
| **`kv_entries` 整表摘要**（**6068** 行） | **完全相同**（`0f2096ab1e6bad83…`） | **完全相同** | 整个 KV 库**一个字节都没变** |
| **`kv_meta` 整表摘要** | **完全相同**（`3e195ae331670e2a…`） | **完全相同** | 迁移账本**未被碰** |
| **81 张表**内容摘要 | **76 张完全相同**，**6** 张变化 | **76 张完全相同**，**同样的 6** 张变化 | **差分为空** ⇒ pytest **没有多改任何一张表** |
| `parameters` 集合（**19** 行）摘要 | **完全相同**（`fb3a00fec9b9ee15…`） | **完全相同** | 与 §7.4 的闸门键读数**互相印证** |
| **测试残留**探针（**4** 个） | 全部**不变**（`attempt\|local\|%` 复核 **1 → 1**、尝试 **0 → 0**、`chain_id=0` 复核 **1 → 1**、尝试 **0 → 0**） | 全部**不变** | **没有新增残留**（那 1 行是**守卫之前**的历史残留） |
| `cow_execution_reviews` 行数 | **+663** | **+1233** | 对照窗涨得更多 ⇒ **运行时自身**在写 |

**完整差分表**见 §D-032§ §3.2.6。

---

## 8. 结论

| 判定 | 内容 |
|---|---|
| **技术可行** | 三层守卫：① **改道**（路径解析的根，唯一收口点）② **连接守卫**（§mode=ro§ ⇒ 无 TOCTOU）③ **Rust 写守卫**（§open_rw§ 命中即抛错）⇒ **Python 与 Rust 两侧都没有静默写入口** |
| **产品代码零改动** | 3 个文件**全在测试树** ⇒ 实盘零影响、不与在途线程争用、可秒级回退 |
| **已闭环的部分** | 缺陷复现（对照窗 0 ／ 复测窗 4）、守卫生效（复测窗 **0**）、守卫非空洞（**14** 项自测，含反证构造与「沙箱写放行」）、只读路径可用、**Rust 桥不再直连主链库**（实测）、性能回到基线量级、**全量回归零红例**（**3267 passed / 3 skipped / 0 failed**）且**闸门键（19 个全集合）＋ `kv_meta` 逐字节 0 变化**、**整库摘要**：`kv_entries`／`kv_meta` **整表**摘要**完全相同**、**81** 张表里 **76** 张**完全相同**、余 **6** 张**对照窗同样变化** ⇒ **差分为空**、**测试残留探针**全部不变 |
| **诚实边界** | 活链库**同时**被实盘运行时写入 ⇒ 主库文件 mtime/size 会因**运行时自身**变化 ⇒ 「文件不变」只能**对照窗**成立，**不能**绝对成立；本任务证明的是「**测试套件对活链库的写入 = 0**」（归因后），**不是**「活链库文件不动」 |

**下一步**：§D-034§ 已逐条勾选 ＋ §D-032§ §3.2 已记录全量回归读数 ⇒
**本任务可闭合**；§D-034§ §f.2 的两条读数依赖第二轮「整库摘要」测量，
其读数见 §7.6 ＝ §D-032§ §3.2.6。

---

<a id="docs-readme"></a>

### `README.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-储.01/docs/README.md` -->

# `AC-储.01`｜测试套件生产库零写入 · 技术文案（docs）

> **本目录是 `AC-储.01` 的独立技术文案区。**
> 存放**该任务自己的**技术说明：设计推导、背景分析、口径定义、读数记录。
>
> **与其它任务不共享** —— 别的任务的技术文案**不复制**到这里，
> 需要时只**链接**（`../../..` 相对路径）到权威出处。
>
> **本目录不参与回归** —— 它是**说明性文档**，不含可执行验收项。

---

## 主题

测试路径只读打开生产库与逐条写入探针

## 文件

| 文件 | 内容 |
|---|---|
| [`01-现状与可行性分析.md`](#d035) | 本任务相关的技术文案 |

---

## 纪律

1. **只写本任务相关的** —— 不做跨任务的技术综述；
2. **只引用不复制** —— 引用别的任务时**给链接**，不复制正文；
3. **口径唯一** —— 同一口径在本目录与 `../PLAN.md` 中**不得矛盾**；
4. **冻结** —— 任务闭合后本目录随任务**整体归档**，内容不再改动。

---
