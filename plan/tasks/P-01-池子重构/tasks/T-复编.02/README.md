# `T-复编.02`｜Review 金额与利润完整性

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../README.md)。

**编号**：`T-复编.02`（纯 ID）　**验收**：`AC-复编.02`　**优先级**：P0　**波次**：W-2
**编号逻辑**：`T-复编.02` / `AC-复编.02` —— **跨模块（组合索引）**，编号索引 **`复编`**（改动模块：`M-6`（复核层（复核发布 / 金额利润））＋ `M-8`（编排层），按模块号升序拼接），该索引号段内第 `02` 个（`.01` 已归档）。（详见 `PLAN.md` §1.0）

**改动模块（= 回归范围）**：`M-6`（含**扩范围**落点 `flashloan/rs-src/src/review_publish.rs`）＋ `M-8`（含**扩范围**落点 `flashloan/src/dex/runtime/rust_closed_loop.py` 等 ＋ **第三次扩范围**落点 `flashloan/tools/composite_manager/src/overview.rs` ＋ **第四次扩范围**落点 `flashloan/app_runtime/tests/unit/test_run_port.py`）

**前置模块**：M-6　　**需授权**：是（写库）

## 文件

| 文件 | 内容 |
|---|---|
| `PLAN.md` | 本任务的开发计划与回归范围（**自包含**） |
| `SCOPE_GUARD.md` | 本任务的修改范围守护与逐模块回归命令 |
| `checklist.md` | 本任务的逐条验收框（**7** 项未完成 ＋ **23** 项已闭合） |
| `docs/` | 本任务的**独立技术文案**区（设计推导 / 口径定义 / 读数记录） |

## 独立回归

```powershell
cargo test --offline -p review-manager
cargo test --offline --release            # 工作目录 flashloan/rs-src/（M-6 扩范围）
cargo test --offline -p composite-manager   # 工作目录 flashloan/tools/（M-8 第三次扩范围）
python -m pytest flashloan/app_runtime/tests/unit/test_rust_closed_loop_worker_bridge.py flashloan/app_runtime/tests/unit/test_dynamic_closed_loop_workers.py flashloan/app_runtime/tests/unit/test_chain_scoped_parameter_layering.py flashloan/app_runtime/tests/unit/test_secret_leakage_guards.py flashloan/app_runtime/tests/unit/test_dex_route_test.py flashloan/app_runtime/tests/unit/test_d4_capacity_informed_ladder.py flashloan/app_runtime/tests/unit/test_manager_overview_closed_loop_identity.py -q
python -m pytest flashloan/app_runtime/tests/unit/test_run_port.py -q   # M-8 第四次扩范围
```

> **只跑上面这些。** 未改动模块**不重跑**。

## 本轮实况（2026-11-25 · 8 → 7）

- **最新代次读数**（**2026-11-25 09:50 +08**，只读采集）：
  [`t401_review_integrity_v_current_20261125.json`](../../../../runtime_evidence/t401_review_integrity_v_current_20261125.json)
  —— 完整 **150 / 174**、金额 measured **166** / missing **8** / unconfirmed **0**、
  PnL negative **148** / **positive 6** / missing **20**、`bestAmountMissing` = **8**、
  **说不出原因的缺失 = 0**、fresh **174** / stale **0**、
  上游版本逐项相等（`closed_loop_version = 20`）、`consumers_need_rebuild = 0`。
- **`C3` 已闭合**（8 → 7）：按**清单已写明**的订正口径
  （`snapshotAvailable` ＋ `generation>=1` ＋ `lastError` 空）实测通过；
  `state = partial` 的根因（**实盘广播库 0 行** ⇒ `broadcast_enabled = false`）**归 `M-7`**、已登记。
  逐条读数与裁定依据见
  [`D-130`](./docs/D-130-复核完整性最新代次读数与剩余项阻塞登记_20261125.md) §2。
- **剩余 7 项**全部是**链上事实**（`execution reverted` ＋ `single_tick_capacity_below_min_probe`）
  **或其派生** ⇒ **不靠改代码闭合**、**不靠改口径勾框**；逐条阻塞条件见 `D-130` §3。
- **正利润复核 4 → 6** ⇒ 市场**确实**在给出正利润机会；但**没有一条**同时满足
  「`completed` ＋ 可执行 ＋ 120s 新鲜」⇒ **广播请求仍是 0 条**（**市场条件**，非接线缺陷）。

## 前一轮实况（2026-11-24）

- **发布层丢证据的两层根因已修并实盘生效**：**第五层**（金额证据被整块丢在发布链路之外）
  ＋ **第六层**（「阶梯为什么没跑」的诊断留在 route 的**子对象**里）。
- **读数（钉住快照）**：[`t401_review_integrity_v39.json`](../../../../runtime_evidence/t401_review_integrity_v39.json)
  —— `review_version = 39`、闭环 **174**、采集于 `2026-11-24T21:41:06+08:00`（**紧跟代次发布的同一秒**）。
  **说不出原因的缺失 = 0** ⇒ **每一条缺失都带得说原因**；发布层 `bestAmountMissingFlags` = **11**
  ＝ 只读 healer `missing` = **11** ＝ `ladderIncomplete` = **11**（**三方同数**）。
- **剩余 8 项**全部是**链上事实**（单 tick 容量不足 ⇒ 阶梯**根本没跑**）或**广播层口径**
  （`state=ready` 因广播库状态 **0 行**而**结构性不可达**）⇒ **归 `M-7`**，已登记到
  [`D-014`](../../docs/D-014-未完成任务清单_20261123.md) §1.2.11 ⇒ **本任务按「部分闭合」处置，不归档**。
- **前置项已闭合**：`T-复编.01` 与 `T-方闭储.01` **均已整项闭合归档**（后者 **38 / 38**）
  ⇒ 「前置新增」验收项**已勾选**（闭合来源：Python 全量第 2 次 **3251 passed / 3 skipped / 0 failed**）。
- **主网广播闸门已放开**（使用者口径：**复核有正向利润即可广播**）
  ⇒ **「必须确认复核正向利润」的三道闸门一字未动** ⇒ 详见
  [`D-104`](./docs/D-104-复核完整性实测.md) §2.9 / §2.9.1。
- **利润等价性 / `profitDrop`** 已实测（v18 → v19 **168 / 168 逐个相等**；`profitDrop` **82.3943 %**）
  ⇒ 见 [`D-103`](./docs/D-103-利润等价性与profitDrop实测.md)。
- **编排层假阳性已归零**（`overview.rs` 的 `pool_scope_checksum` 跨作用域陈旧副本已对齐 `lib.rs`）
  ⇒ 见 [`D-108`](./docs/D-108-复核代次发布落地实测与剩余pool_scope跨作用域误判_20261124.md) §3.5。
- **运行时被静默重启 3 次的根因已定位并修复（2026-11-25）**：
  早前登记为「其它修复进程抢端口」**属误判，已更正** —— 真因是**测试套件自身**：
  `test_run_port.py` 的端口回收用例**未 mock** `taskkill`，**真的杀掉** 5000 端口上的运行时
  （静默退出码 1、无 traceback）⇒ **已修复**（新增 autouse fixture，去掉真实 kill 副作用，
  **产品代码一字未改**）⇒ 修复后同一命令运行时 PID 未变、监督日志零增长。
  ⇒ **⚠️ 流程欠账**：第四次扩范围**顺序倒置**（先改后登记），且本轮**跑过一次全量**
  （理由：必须证明「全量跑完不再杀掉运行时」）⇒ 详见 [`D-047` §1.3.5](./D-047-PLAN.md)
  与 [`D-048` §6](./D-048-SCOPE_GUARD.md)。
- **完整读数与边界登记**：见 [`D-104`](./docs/D-104-复核完整性实测.md)。
