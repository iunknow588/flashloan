# D-257 `T-复储.01`｜验收框（**Checklist**）

> 任务 `T-复储.01`　验收 `AC-复储.01`　改动模块 `M-6` ＋ `M-10`　优先级 P1
> **勾选规则**：一个框只有**有读数**才可勾；「应该可以」不是读数。
> **归并**：`T-复编储.01` 的 **8** 项剩余（B1–B4 ／ C1–C3 ／ D1）**整体并入本文件**，以 `归并` 标记。

## A. 取证（**先测，再改**）

- [x] **A1** **19** 条缺行**逐条归因表**：当前代次、审计流代次、`data_incomplete` 成因、是否在 active 目录里　**（🟢 已闭合 · `D-266` 第十八轮 · 2026-11-28 20:05 只读实测 · 探针 `probe_review_gap_attribution_20261128.py`）**：缺行 **18** 条（不是 19）—— 逐条归因：**全部仍在 active 目录**（48 条 active 目录里 **30** 条有本代次复核行、**18** 条没有）；当前代次 **73** ／ 审计流代次 **53**（**11** 条）／**54**（**4** 条）／**72**（**3** 条）；**审计流里「本代次（73）」的行数逐条为 0** ⇒ **不是「测了但没落库」**，而是**这些闭环本代次压根没被推进**（与 `T-核.09` 的 worker 覆盖 **28–29 / 48** 同源）；它们上一代的行：**14** 条 `completed`（`measured_not_profitable:NoProfitableRoute`）＋ **4** 条 `data_incomplete`（`not_reviewed_since:…`，**盈亏缺值**）
- [x] **A2** 确认「测了但没落本代次行」的**条数**（基线：`missingRowButAuditedToday = 18`、`measuredForThisGenerationButNoRow = 0`、`missingRowNeverAudited = 0`）　**（🟢 已闭合 · `D-266` 第十八轮 · 2026-11-28 20:05 复测）**：**实测 `measuredForThisGenerationButNoRow` = 0、`missingRowNeverAudited` = 0、`missingRowButAuditedToday` = 18** ⇒ **基线 18 / 0 / 0 逐项复现**；「测了但没落本代次行」的条数 **= 0**（`audit_review_coverage_vs_audit_stream_20261128.py`）—— 18 条缺行的真实成因见 `A1`：**本代次没被推进**
- [x] **A3** `not_executable` **135** 条 `runArbitrage ok=false` 的**基线读数**入报告　**（🟢 已闭合 · `D-266` 第十八轮 · 2026-11-28 20:05 只读实测 · 探针 `probe_review_gap_attribution_20261128.py`）**：全表 **176** 行按代次分布 —— **53**：132 行（`not_executable` **109** ／ `data_incomplete` **17** ／ `completed` **6**；`ok=false` **125** ／ `ok=true` **7**）、**54**：7 行（`ok=false` **7**）、**67**：2 行（`ok=true` **2**）、**72**：5 行（`ok=false` **5**）、**73**：30 行（`ok=false` **29** ／ `ok=true` **1**）⇒ **`not_executable` 合计 116 条**（原记 135 是 **11-26** 的旧值）、**`ok=false` 合计 166 条**；**逐条归因**归 `B6`，本框只登记基线
- [x] **A4** 基线：代次覆盖 **29 / 48**；`nullPnl = 0`、`positivePnl = 0`；`completed` **0** 行　**（🟢 已闭合 · `D-266` 第十八轮 · 2026-11-28 20:05 只读实测 · 基线随实测更新）**：本代次（**73**）**30** 条复核行（`completed` **29** ／ `not_executable` **1**）、**缺盈亏数值 0 条**、**正盈亏 0 条**（全部为负 —— `measured_not_profitable`）；代次覆盖 **30 / 48**（缺口 **18** 条，见 `A1`）；**原记的 `completed = 0 行` 是 11-26 的旧值** ⇒ 一并订正；**全表正盈亏 7 条全部在代次 53**（`not_executable` ／ `loop_options_unavailable:hop_N`），**不在当前 48 条 active 目录里**

## B. 施工（**每步一个框** · 含 `T-复编储.01` 的归并项）

- [ ] **B1**（**归并**）**迁移 `20261128_022_review_retirement_schema`**：`manager_v4_reviews.review_status` 的 `CHECK` 约束**放宽**以容纳 `retired`（现役约束只允许 `completed` / `not_executable` / `data_incomplete`）
- [ ] **B2**（**归并**）**复核层退役判定**：`stored` 里**不在当前目录**的复核行 ⇒ 写 `review_status = 'retired'` ＋ `review_reason = 'closed_loop_left_catalog:<版本>'`，并**撤下**该闭环的广播请求（**不删行**、**不填数**、**不碰门禁**）
- [ ] **B3**（**归并**）**库状态统计分离**：`data_incomplete_count` 与新增的 `retired_count` **分开**（基线：今天 `data_incomplete_count = 19` **全**是已退役行 ⇒ 统计在说谎）
- [ ] **B4**（**归并**）**编排层窗口口径分离**：PnL 窗口把 `missingValue` 与新增的 `retiredReviews` **分开计数**；**在产目录**的 L4 判据**一字不改**
- [ ] **B5** 在产目录的**每条**闭环都拿到本代次复核行：代次覆盖 **29 / 48 → 48 / 48**
- [ ] **B6** `runArbitrage ok=false` **135** 条逐条归到**具体**链上原因（容量 / 定价 / 路由 / RPC）；无法归因的**数量明确列出**
- [ ] **B7** 退役行的广播请求**已撤下**（`manager_v4_review_broadcast_requests` 里不再有退役闭环）
- [ ] **B8** 退役**只改状态 ＋ 原因**：**零** `DELETE`、**零**历史代次变更

## C. 回归（**只跑 `M-6` ＋ `M-10`** · 含 `T-复编储.01` 的归并项）

- [ ] **C1**（**归并**）**回归全绿**：`review-manager` ＋ `flashloan-rs` ＋ 两套 healer pytest **0 failed**（**只跑改动模块**的）
- [ ] **C2**（**归并**）**部署闸门无中间态**：迁移 **022** 与新二进制**同批**上线（生产库停在 **021** ⇒ 新旧不可混用）
- [ ] **C3**（**归并**）**实盘读数**：退役行 **19** 条、窗口 `missingValue` **19 → 0**、**在产目录 48 / 48** 仍**全有**明确盈亏且 `positive` 读数**不变**
- [ ] **C4** 新增回归：`retired` **不被**计入 `missingValue`
- [ ] **C5** 新增回归：退役**不删行**、**不填数**

## D. 实盘（**3 个连续窗口**）

- [ ] **D1** 窗口 `missingValue` **= 0**；`retiredReviews` **单独可读**
- [ ] **D2** 在产目录 **48 / 48** 全有明确盈亏（`missing = 0`、`unconfirmed = 0`）
- [ ] **D3** `measuredForThisGenerationButNoRow` 保持 **0**；`missingRowNeverAudited` 保持 **0**
- [ ] **D4** `positive` 读数**不变**（本任务**不**产生新的正利润 ⇒ 不预判市场）
- [ ] **D5** 退役闭环的广播请求**已撤下**；**无**退役闭环进过广播队列

## E. 诚实边界（**不代其它任务关闭**）

- [ ] **E1**（**归并 D1**）**口径 ＝ 先复价，退役兜底**：目录外的复核**不补数**（**填数即伪造**）；退役**只**表达「已离开目录」
- [ ] **E2** **不伪造**：本任务**未写任何盈亏数值**、未填 0、未删任何历史复核行、未改任何历史代次
- [ ] **E3** **不放宽任何门禁**：广播门仍是「新鲜的、明确盈利的复核」；`ok=false` **不**被改写成 `ok=true`
- [ ] **E4** **不预判**：`ok=false` 最终**可能**归到「链上确实不盈利」⇒ 那是**业务结论**，**不是**缺陷
- [ ] **E5** **不代其它任务关闭**：常驻 worker 覆盖（`T-核.09`）、广播链路（`T-广储.01`）**未**在本任务内关闭
