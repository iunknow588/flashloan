


- [ ] **前置新增**：`T-6.01` 已闭合 **且** `T-4.01` 已闭合



**完整性验收（**重跑后必须重新核验，不得沿用旧读数**）**：



- [ ] **缺字段 = 0**（每条复核的必备字段齐全）。

- [ ] **金额状态 = `measured`**（`missing=0`、`unconfirmed=0`）。

- [ ] **PnL missing = 0**。

- [ ] **`bestAmountMissing` = 0**。

- [ ] **测得零的行**必须**同时**带 `bestAmountZeroReason` **与** ladder 行（阶梯规模逐条可查）。

- [ ] **Manager loop key 全唯一**；`staleOrUnmatched=0`；`currentDistinctLoops` = 当前闭环数。

- [ ] hop 表缺失时 healer **安全回落不崩溃**（匹配**精确有序**，未匹配**不猜测**）。

- [ ] 只读 healer 交叉一致：`missing=0`、`ladderIncomplete=0`、`failClosed=True`。



**新鲜度双阈值验收（**两个都要测，不得只报一个**）**：



| 阈值 | 来源 | 用途 | 判定 |

|---|---|---|---|

| **600s** | 观测阈值 | 「这条复核还新不新鲜」的**读数与报告**口径 | ☐ 已测 |

| **120s** | Rust 常量 `MAX_AUTOMATIC_BROADCAST_REVIEW_AGE_SECONDS`（+30s 时钟容差），`review_is_fresh_for_automatic_broadcast()` 使用 | **自动广播门禁** | ☐ 已测 |



- [ ] **600s 观测阈值**：fresh / stale 分布已取。

- [ ] **120s 自动广播门禁**：`review_is_fresh_for_automatic_broadcast()` 对新复核的判定已取。

- [ ] **两者都已报告**（**只报 600s 视为未完成**）。

- [ ] **观测通过 ≠ 门禁通过**这一区分已在报告中写明。

- [ ] 阈值常量**从 Rust 源码读取**，**不得**硬编码。

- [ ] **门禁不通过时如实记录**"广播请求不会被生成"，**不得**改写为"复核失败"。



**上游一致性验收**：



- [ ] `manager_review_library_state` 的 `closed_loop_version` / `closed_loop_checksum` /

      `candidate_edge_version` / `candidate_edge_checksum` / `pool_scope_checksum`

      **与当前上游一致**（当前应为 closed_loop **15** / candidate_edge **9** / pool_scope **`sha256:ae6b14ac…`**）。

- [ ] `consumers_need_rebuild` **自然清除**（= **0**），**未**被手工写。

- [ ] `runtime_core` 快照**被接受**：`state=ok`、`snapshotAvailable=true`、`generation>=1`。

- [ ] `blockers` 中 `closedLoops` 与 `reviews` **同时消失**。

- [ ] **上游一致性补充**：闭环库状态中记录的**方向层版本与校验和**（I-15）与方向层库状态**一致**



**边界验收**：



- [ ] **未**用历史 / live 表数据**回填** Manager 复核库（**必须重新测量**）。

- [ ] **未**回灌历史深度 / 业务数据。

- [ ] `positive=0` **如实报告为业务结论**（链上不盈利），**未**被写成缺陷、**未**被当作门禁失败。



> **报告口径**：须含链 ID、快照版本、review 时间戳与新鲜度窗口读数；

> **不得**把"重跑完成"写成"缺口已闭合"。



- [ ] **重跑后重新核验**，**不得**沿用第 57 轮读数