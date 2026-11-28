# D-057 `T-复广.01`｜主网持续套利广播产出 · 测试目标 check list

> **【归并 · 2026-11-28】** 本清单的 **4** 项（广播库状态行 ／ 链路贯通 ／ 已实现盈亏可核算 ／ 广播后盈亏核算口径）已于 **2026-11-28** **归并**到 `T-广储.01`；其余 **3** 项等**市场条件**。
> **执行落点**：`T-广储.01`（见 [`D-261`](../../docs/D-261-剩余未完成项归属与开发计划_20261128.md) §1 的**逐条归属表**）。
> **本清单的框**在本任务达成前**保持未勾选** ⇒ **读数不重复计**（每个框只在一个文件里）。
> **【归并 · 2026-11-28】** 本清单的 **4** 项（广播库状态行 ／ 链路贯通 ／ 已实现盈亏可核算 ／ 广播后盈亏核算口径）已于 **2026-11-28** **归并**到 `T-广储.01`；其余 **3** 项等**市场条件**。
> **执行落点**：`T-广储.01`（见 [`D-261`](../../docs/D-261-剩余未完成项归属与开发计划_20261128.md) §1 的**逐条归属表**）。
> **本清单的框**在本任务达成前**保持未勾选** ⇒ **读数不重复计**（每个框只在一个文件里）。
> **2026-11-25 闭合进展（28 → 7）**：本轮闭合 **21** 项，证据落在
> [`docs/D-129-第4环接线与四类拒绝路径闭合实测_20261125.md`](./docs/D-129-第4环接线与四类拒绝路径闭合实测_20261125.md)。
> **剩余 7 项**（`manager_broadcast_library_state` 行被创建 / 第 6 环回执与已实现盈亏 /
> 链路贯通 / 真实尝试 / 回执可关联 / 已实现盈亏可核算 / 连续可重复）
> **全部**量的是「**实盘上真的发出去了**」—— 实盘 `manager_v4_review_broadcast_requests` **0 行**、
> `manager_cow_execution_attempts` **0 行** ⇒ 它们等的是**市场条件**（正利润机会尚未出现），
> **不是**代码缺口、**不是**门禁缺口。逐条阻塞条件见 `D-129` §8。
> ⇒ **不**用夹具读数冒充实盘读数。

**第 4 环｜L2→L3 接线（**写库，不广播**）**：



- [x] 接线点**在复核产出侧**（`TASK-163` 实测纠正）——

      **不是**"给 `POST /v1/broadcasts/submit` 接调用方"（`submit_impl` **只消费不创建**）。

      > **读数（2026-11-25 · `D-129` §2 · B＋C）**：复核层在**生成复核行的同一趟**里推广播请求
      > —— `review_manager/src/lib.rs:1926`，触发谓词 `:1915-1922`；
      > 落库 `:3210-3212`（`manager_v4_review_broadcast_requests`）。
      > 复核层单测断言 `broadcast_request_count == 1` 且
      > `request_status == "generated"`（`:4851` / `:4854`）。
      > ⇒ `submit_impl` 确实**只消费不创建**。

- [x] 由**复核完成且可执行**（`run.ok && run.executable`）的记录**生成广播请求并落库**。

      > **读数（2026-11-25 · `D-129` §2 · A＋B）**：生成谓词含 `review_status == "completed"`，
      > 「可执行」由准入侧 `BC-1.run_executable` 独立强制；
      > 夹具库 **8 条** `manager_v4_review_broadcast_requests` 已落库（`request_status = "generated"`）。

- [ ] `manager_broadcast_library_state` 行**被创建**（当前 **0 行**）。

- [x] 广播请求与复核**按 `loop_key` 精确关联**。

      > **读数（2026-11-25 · `D-129` §2 · A＋B）**：夹具 **8** 条请求行的 `loop_key`
      > 与 **2** 条 attempt 行**逐字相等**；审计字段 `reviewIdentifier = "broadcast.submit:<loop_key>"`。

- [x] **未**广播（接线不触发发送）。

      > **读数（2026-11-25 · `D-129` §2 · A）**：attempt 行 `tx_hash = null`、`status = pending`、
      > `receipt_json = {}`；`success_count = 0`；7 条拒绝留痕 `broadcasted = false`。



**第 4 环｜主网 dry-run（**不广播**）**：



- [x] 复核新鲜完整后先跑 dry-run，取**候选 / 拒绝原因 / 幂等键 / 门禁判定**的可核对输出。

      > **读数（2026-11-25 · `D-129` §3 · A）**：dry-run 响应给出
      > `requestCount / attemptCount / pendingCount / broadcastVersion / materializedChecksum /
      > idempotentReplay` ＋ `error.details.reasons` ＋ 请求头 `Idempotency-Key`。
      > **dry-run 写 0 条**：16 次提交（8 dry ＋ 8 apply）而 `request_count = 8`。

- [x] **不得**出现 txHash（dry-run 不广播）。

      > **读数（2026-11-25 · `D-129` §3 · A）**：dry-run 响应**不含** `txHash`；
      > 落库 `tx_hash = null`；`verdict.txHashesPresent = []`。



**第 4 环｜四类拒绝路径（**必须留证 · `BC-2` 硬前置**）**：



- [x] `profit <= 0`：**逐条真实触发**并留证（**含测得零**与**测得负**两种）。

      > **读数（2026-11-25 · `D-129` §4 · A）**：`profit-zero` 与 `profit-negative`
      > **两种都触发**，规则均为 `BC-1.pnl_positive`，HTTP **409**。

- [x] 利润或金额**未知**（`missing`）：**逐条真实触发**并留证。

      > **读数（2026-11-25 · `D-129` §4 · A）**：`profit-unmeasured` → `BC-1.pnl_measured`；
      > `price-limits-missing` → `BC-1.price_limits_complete` ⇒ **两条都触发**。

- [x] 状态**陈旧**（超过 120s 门禁）：**逐条真实触发**并留证。

      > **读数（2026-11-25 · `D-129` §4 · A）**：`review-stale` → `BC-1.review_fresh`，HTTP **409**。

- [x] **权限 / 配置错配**（链或账户缺失、限额越界）：**逐条真实触发**并留证。

      > **读数（2026-11-25 · `D-129` §4 · A）**：`guard-account-mismatch` → `BC-2.account_matches`；
      > `guard-amount-over-limit` → `BC-2.amount_within_limit`（限额越界）⇒ **两条都触发**。
      > （链缺失这一支由广播层自带单测 `p2_a_missing_chain_refuses_the_broadcast` 覆盖。）

- [x] 每一条的**拒绝原因**均**落库可查**（`BC-3` / `BC-5`）。

      > **读数（2026-11-25 · `D-129` §4 · A）**：**7 条** `manager_broadcast_attempt_diagnostics`，
      > `unsatisfiedRules` 与期望规则**逐条相等**、`broadcasted = false`（**BC-3**）；
      > **BC-5** 九个字段齐全（见下条）。



**第 5 环｜真实广播（**默认放开 · `BC-1` 正向利润准入**）**：



- [x] **不需要**独立广播授权；**不得**把「链 / 账户 / 限额 / 时间窗口」写成人工审批项

      —— 它们是**运行时配置与自动护栏**（缺失 ⇒ 拒绝发送；超限 ⇒ 自动停止）。

      > **读数（2026-11-25 · `D-129` §5 · B）**：`broadcast_manager/src/lib.rs:350-361`
      > 明文：*「The policy is **runtime configuration**, not an approval … 缺失 ⇒ 拒绝并留痕」*；
      > 护栏来自 `manager-api` 的 `BROADCAST_GUARD_*` 环境变量 ⇒ **无人工审批开关**。
      > 与用户 2026-11 的口径一致：*默认放开授权，但必须确认复核正向利润才广播执行*。

- [x] 广播**只**在 `BC-1` 五条**全部满足**时发生：

      `run.ok` ∧ `run.executable` ∧ **`signed_pnl.amount > 0`（已测量）** ∧ **120s 新鲜度** ∧ **完整性**。

      > **读数（2026-11-25 · `D-129` §5 · A＋B）**：正向对照 12 条规则**全部** `satisfied = true`、
      > `failedRules = []`；任一条不满足即 **409** 且 `failedRules` 非空。
      > 五条在**生成侧**（复核层谓词）与**准入侧**（广播层 `BC-1`）**两侧都强制**。

- [x] **每次广播**都能回答「**凭什么发这一条**」—— `BC-5` **九个审计字段齐全可查**。

      > **读数（2026-11-25 · `D-129` §5 · A＋B＋C）**：`evidence_json.broadcastAudit` 的
      > **九个字段齐全**（`reviewIdentifier` / `signedPnlAmount` / `reviewAgeSeconds` /
      > `idempotencyKey` / `chainId` / `accountIdentifier` / `limitVerdict` /
      > `broadcastAt` / `outcome`）＋ 完整 `admission` 轨迹；
      > 结构定义 `broadcast_manager/src/lib.rs:31-64`（无法测量为 `null`、绝不缺席）；
      > 单测 `p1_a_positive_fresh_review_carries_the_nine_audit_fields` 逐个断言。

- [x] **硬前置已闭合**：`T-广.01` 四类拒绝路径已留证 **且** `T-复广.02`（`AC-复广.02`）已闭合。

      > **读数（2026-11-25）**：`T-广.01` **已归档**（`plan/archive/任务归档_T-广.01_广播门禁与错误自愈_20261125.md`）；
      > `T-复广.02` **已闭合并提交**（`c9b4bfb0`）⇒ **本项的硬前置已解除**。

- [x] **未**因「默认放开」而放宽任何门禁（120s / 600s / 完整性 / 容量 / 利润 / 幂等 / 回执关联）。

      > **读数（2026-11-25 · `D-129` §5 · A＋B）**：越限 / 错账户 / 缺价限 / 陈旧 / 缺利润
      > **全部被拒**；幂等键**同键不同 body 仍 409**；广播层单测
      > `p2_a_foreign_account_refuses_the_broadcast` / `p2_an_amount_above_the_limit_refuses_the_broadcast` /
      > `n6_stale_review_refuses_the_broadcast` / `n5_unmeasured_profit_refuses_the_broadcast` 逐条钉住。



**第 6 环｜回执关联与已实现盈亏（**只读**）**：



- [ ] txHash → receipt → **实际** token 增量 → gas → 借款费 → 滑点 → 模型偏差。

- [x] **不得**用复核阶段的预期金额替代已实现盈亏。

      > **读数（2026-11-25 · `D-129` §6 · B）**：复核测得金额在 `signed_pnl_amount` /
      > `signed_pnl_raw`（**复核阶段**），链上结果在 `receipt_json`（**回执**，
      > `AttemptFinalizeInput.receipt` `:471-484`）；finalize 只写 `receipt_json`（`:2679`）。
      > 广播层**没有**「已实现盈亏」列 ⇒ 结构上**无法**用预期金额冒充。



**主线达成判据（**8 条，逐条核对**；第 8 条为 v2.2 新增）**：



- [ ] **链路贯通**：L1 → L2 → L3 → L4 四级全部有读数。

- [ ] **真实尝试**：存在实际广播尝试记录。

- [ ] **回执可关联**：txHash → receipt → token 增量可连。

- [ ] **已实现盈亏可核算**：含 gas / 借款费 / 滑点。

- [x] **门禁未被放宽**。

      > **读数（2026-11-25）**：同「**未因默认放开而放宽任何门禁**」一条。

- [x] **记录读取合规**（Rust Manager 拥有，Python 纯消费）。

      > **读数（2026-11-25 · `D-129` §7 · C）**：架构守卫
      > `flashloan/app_runtime/tests/unit/test_architecture_guards.py`
      > 的 `MANAGER_BUSINESS_READ_TABLES`（`:1518-1530`）与 `MANAGER_BUSINESS_WRITE_TABLES`
      > （`:1532-1543`）把广播侧两张表登记为 **Rust Manager 独占**，并带**负向断言**；
      > 仓库实测 Python 侧**零处**直读（只有守卫测试在读）。

- [ ] **连续可重复**。

- [x] **准入可审计**（v2.2）：每次广播都能回答「**凭什么发这一条**」——

      `BC-1` 五条**全部满足**，且 `BC-5` **九个审计字段**齐全可查。

      > **读数（2026-11-25）**：同 §5 的两条（`BC-1` 五条 ＋ `BC-5` 九字段）。



- [x] **本版唯一变化**：整条链的前置延长至 `T-方闭储.01`

      > **读数（2026-11-25）**：`T-方闭储.01` **已闭合**
      > （`plan/archive/任务归档_T-方闭储.01_方向层落地_20261124.md`）⇒ 前置已延长且**已满足**。
