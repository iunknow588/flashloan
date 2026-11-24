# D-048 `T-复编.02`｜Review 金额与利润完整性 · 修改范围守护（Scope Guard）

> **本文件是 `T-复编.02` 的范围契约。** 它定义**允许改什么**、**必须跑什么**、**禁止碰什么**。
> 违反本契约 ⇒ **本任务不算闭合**。
> 总纲见 `plan/README.md`；模板见 `plan/template/`。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 是否改 |
|---|---|---|---|
| `M-6` | 复核层 | ``flashloan/tools/review_manager/`` | 是 |
| `M-8` | 编排层（**扩范围 · 事后补记**） | ``flashloan/src/dex/runtime/dex_opportunity_scanner_rust_mixin.py``<br>``flashloan/src/dex/runtime/closed_loop_state_machine.py``<br>``flashloan/src/dex/runtime/rust_closed_loop.py``<br>``flashloan/app_runtime/.env.example`` | 是 |

> **扩范围原因（`D-047` §1.3 同步登记）**：本任务的**复核发布**链路本身正确，
> 但它在实盘**一次都没有落地**。实测根因在**编排层**的**常驻方向组容量**：
> 闭环目录发布 **174** 个方向组（每组 1 个闭环），而扫描器的常驻上限被写死成
> **16**（且内联硬上限 **32**）⇒ 只有 16 个组拿到 worker ⇒ 其余 **158** 个闭环
> **永远拿不到证据** ⇒ `covers_catalog()`（证据条数 == 目录条数）**永远不成立**
> ⇒ `review_publish_block` 永远返回 `coverage_incomplete` ⇒ **复核代次永不发布**。
> 该缺陷只在**编排层**可修（容量是编排层的策略），故按 `D-048` §4「先扩后跑」**扩范围**。
> **缺口如实登记**：`D-014` §0.2 已登记「M-n 模块表**没有 `rs-src` / Python 运行时归属**」
> —— 本次扩登记沿用 `T-复编.01` 的先例（把运行时落点登记在 `M-8` 名下），
> **不**改模块表（模块表是 `M-n` 体系的基座，改它要连带重算全部回归范围）。

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

---

## 1.1 代码落点（**文件级** · 并行判定用）

> **本节把 §1 的目录粒度细化到文件级** —— 目录粒度会让**几乎所有**任务看起来都冲突，
> 失焦。**只有同文件的争用才是真争用**；目录相同而文件不同 ⇒ **可并列**。
> 本节由 `plan/runtime_evidence/tools/refresh_scope_guard_file_level_20261124.py` **实测生成**，
> **不靠人工枚举**；重跑该脚本即可刷新。
>
| 模块 | 代码落点（文件级） | 与他任务**同文件** |
|---|---|---|
| `M-6` | `flashloan/tools/review_manager/examples/p0_6_single_tick_gate_probe.rs`<br>`flashloan/tools/review_manager/examples/t401_profit_drop_probe.rs`<br>`flashloan/tools/review_manager/src/lib.rs`<br>`flashloan/tools/review_manager/src/parity.rs`<br>`flashloan/tools/review_manager/src/records.rs`<br>`flashloan/tools/review_manager/src/runtime.rs`<br>`flashloan/tools/review_manager/src/shadow.rs`<br>`flashloan/tools/review_manager/src/single_tick.rs`<br>`flashloan/tools/review_manager/src/state_transition_gate.rs` | `flashloan/tools/review_manager/examples/p0_6_single_tick_gate_probe.rs`<br>↔ 与 T-复.02 / T-复广.01 / T-复广.02 同文件<br>`flashloan/tools/review_manager/examples/t401_profit_drop_probe.rs`<br>↔ 与 T-复.02 / T-复广.01 / T-复广.02 同文件<br>`flashloan/tools/review_manager/src/lib.rs`<br>↔ 与 T-复.02 / T-复广.01 / T-复广.02 同文件<br>`flashloan/tools/review_manager/src/parity.rs`<br>↔ 与 T-复.02 / T-复广.01 / T-复广.02 同文件<br>`flashloan/tools/review_manager/src/records.rs`<br>↔ 与 T-复.02 / T-复广.01 / T-复广.02 同文件<br>`flashloan/tools/review_manager/src/runtime.rs`<br>↔ 与 T-复.02 / T-复广.01 / T-复广.02 同文件<br>`flashloan/tools/review_manager/src/shadow.rs`<br>↔ 与 T-复.02 / T-复广.01 / T-复广.02 同文件<br>`flashloan/tools/review_manager/src/single_tick.rs`<br>↔ 与 T-复.02 / T-复广.01 / T-复广.02 同文件<br>`flashloan/tools/review_manager/src/state_transition_gate.rs`<br>↔ 与 T-复.02 / T-复广.01 / T-复广.02 同文件 |

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
> - **无**（本任务的每个文件都与其它任务同文件 ⇒ 提交必须错开）
## 2. 逐模块回归命令（**可逐条复制执行**）

| 模块 | 名称 | 命令 |
|---|---|---|
| — | — | `cargo test --offline -p review-manager` |
| `M-8` | 编排层（扩范围） | `python -m pytest flashloan/app_runtime/tests/unit/test_rust_closed_loop_worker_bridge.py flashloan/app_runtime/tests/unit/test_dynamic_closed_loop_workers.py flashloan/app_runtime/tests/unit/test_chain_scoped_parameter_layering.py flashloan/app_runtime/tests/unit/test_secret_leakage_guards.py flashloan/app_runtime/tests/unit/test_dex_route_test.py flashloan/app_runtime/tests/unit/test_d4_capacity_informed_ladder.py flashloan/app_runtime/tests/unit/test_manager_overview_closed_loop_identity.py -q` |

```powershell
# 本任务的完整回归范围 = 上表所有模块的命令之并集
cargo test --offline -p review-manager
python -m pytest flashloan/app_runtime/tests/unit/test_rust_closed_loop_worker_bridge.py flashloan/app_runtime/tests/unit/test_dynamic_closed_loop_workers.py flashloan/app_runtime/tests/unit/test_chain_scoped_parameter_layering.py flashloan/app_runtime/tests/unit/test_secret_leakage_guards.py flashloan/app_runtime/tests/unit/test_dex_route_test.py flashloan/app_runtime/tests/unit/test_d4_capacity_informed_ladder.py flashloan/app_runtime/tests/unit/test_manager_overview_closed_loop_identity.py -q
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

- [ ] 本次改动**全部**落在 §1 的模块集合内（或已按 §4 扩范围并重跑）
- [ ] §2 的**全部**命令**已执行**且**全绿**
- [ ] §3 的禁止触碰项**确未被触碰**
- [ ] **未**因本任务跑过**全量**测试（若跑了，需说明原因）
