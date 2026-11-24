# D-049 `T-复编.02`｜Review 金额与利润完整性 · 测试目标 check list

- [ ] **前置新增**：`T-复编.01` 已闭合 **且** `T-方闭储.01` 已闭合



**利润等价性与 `profitDrop` 实测（**由 `M-4` 的闭环层切换移交**）**：

> **本组验收项的来源**：`M-4`（闭环层）切换到方向池后，闭环层**不存利润**
> （利润由复核层按**实时价**计算）⇒ 利润等价性与 `profitDrop` **在 `M-6` 才可能测**。
> `M-4` 的改动模块集合**不含** `M-6` ⇒ 按**先扩后跑**纪律**不扩 `M-4` 的范围**，
> 把它**如实移交**本任务（本任务改动模块 = `M-6`，且**需授权：是（写库）** ⇒ 具备实测条件）。
> **移交对照**：`T-方闭储.01` 的「利润等价性」/「`profitDrop` 已实测一次」（对应其 `T-P14`）
> —— **本组即其落点**。

- [ ] **利润等价性**：同一闭环**新旧路径**的有效利润**逐个相等**（**逐个**核对，**不得**只报中位数）。

      > **闭环层不存利润** ⇒ 利润等价性**只能在复核层按实时价测**；
      > 可先验的等价部分（USDG 估值 / 利润门禁输入）已在 `M-4` 侧实测**零变化**
      > （`changed_borrow_token_usdg_value = 0`），但**那不等于**利润逐个相等。

- [ ] **`profitDrop` 已实测一次**：受影响闭环的**有效利润中位数跌幅**有**实测值**并写入报告。

      > **「未测量」不得**作为闭合理由 —— 未测即**登记为未完成**（**不得**写 `n/a` 后闭合）。
      > **必须**给出：样本数（旧路径 / 新路径各多少）、中位数跌幅数值、**测量时点**与**价格来源**。

- [ ] **新旧路径的利润都来自同一次测量**（**同一价格快照**），**不得**拿不同时点的两次读数相比。

- [ ] **测得零 / 负盈亏**如实保留为**测量结果**（与本清单「完整性验收」段的 `positive=0` 口径一致），
      **不得**降级成「缺值」。

- [ ] **`profitDrop` 的测量结果**已写入本任务 `docs/`（**独立技术文案区**），**不得**只留在对话里。


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

      **与当前上游一致**（当前应为 closed_loop **19** / candidate_edge **9** / pool_scope **`sha256:ae6b14ac…`**）。

      > **订正（2026-11-24）**：原文写的 `closed_loop 15` 是**陈旧值**。当前闭环管理器
      > 发布的是 **`closed_loop_version=19`**（`sha256:6927d8d6…`，174 闭环 / 522 跳）；
      > 复核库当时仍是 **14** ⇒ C1 当时 NOT MET。
      >
      > **实测更新（2026-11-24 18:16）**：**复核代次已发布** ⇒ **C1 = MET**。
      > 复核库现为 `review_version=25→26`（持续自增）、
      > `closed_loop_version=19`（**= 上游**）、`closed_loop_checksum=sha256:6927d8d6…`（**= 上游**）、
      > `candidate_edge_version=9`（**= 上游**）、`observation_version=12`（**= 上游**）、
      > `pool_scope_checksum=sha256:ae6b14ac…`（**= `core4` 作用域**）；
      > `loop_count=174`、`reviewed_count=174`、`generated_at=2026-11-24T10:16:54`。
      > **覆盖全集成立**：目录 174 / 复核 174 / **缺口 0** ⇒ `covers_catalog()` 成立。
      > 读数出处：[`docs/D-108`](./docs/D-108-复核代次发布落地实测与剩余pool_scope跨作用域误判_20261124.md)。

- [ ] `consumers_need_rebuild` **自然清除**（= **0**），**未**被手工写。

- [ ] `runtime_core` 快照**被接受**：`snapshotAvailable=true`、`generation>=1`、`lastError` 空。

      > **口径订正（2026-11-24 · R4 · 待人工裁决）**：
      > 原文写的 `state=ready` **额外**要求**广播组件也启用**，
      > 而广播**未授权** ⇒ `state` **恒 `partial`** ⇒ 那一半**在广播未授权期间不可满足**。
      > **「快照被接受」这一事实**的**真信号**是
      > **`snapshotAvailable=true` ＋ `generation>=1` ＋ `lastError` 空**（三者**已全部满足**），
      > `state=ready` 属**广播门禁**的维度 ⇒ **本项按上面的三信号判定**。
      > **待人工裁决**：是否把「`state=partial`（源于 broadcast 未授权）」**显式豁免**记入本项。
      > 裁决前**不勾选**本项（**不代裁决盖章**）。

      > **订正（2026-11-24）**：原文写的 `state=ok` **不是 Rust 的状态词汇**。
      > 内核的 `RuntimeCore::status` 只有 **`ready` / `partial` / `registered` / `error` / `closed`**
      > 五个取值（`flashloan/rs-src/src/runtime_core.rs`）；**`ready`** 才是「快照已建立且链路完整」。
      > 当时实测 `state=error`、`generation=0` ⇒ C3 当时 NOT MET。
      >
      > **实测更新（2026-11-24 18:36）**：代次发布后内核**已接受**混合快照 ⇒
      > **`snapshotAvailable=true` / `generation=67`（持续递增）/ `lastError`=空**
      > （原先的 `RuntimeCore rejected a mixed manager snapshot: […closedLoopVersion, expected: 19, actual: 14…]`
      > **已消失**）⇒ **「快照被接受」已达成**。
      >
      > **但 `state` 实测是 `partial`，不是 `ready`** —— **根因已定位**：
      > `runtime_core.rs:697-711` 里 `partial` = 「快照**存在**但 `readiness.chain_complete=false`」；
      > `chain_complete`（`runtime_core.rs:2405-2411`）要求 **8 个组件全部 enabled**，
      > 实测**唯一**未启用的是 **`broadcast`**（`ROBINHOOD_MAINNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST` 未开启）
      > ⇒ `state` **恒 `partial`** ⇒ **「`state=ready`」这一条在广播未授权期间不可满足**。
      > ⇒ **本项是「测量口径」问题，不是缺陷**（详见 [`docs/D-108`](./docs/D-108-复核代次发布落地实测与剩余pool_scope跨作用域误判_20261124.md) §2.5）。
      > **待人工裁决**：C3 判定改为「`snapshotAvailable` + `generation>=1` + `lastError` 空」，
      > **或**对 `state=partial` **显式豁免**（注明源于 broadcast 未授权）。

- [ ] `blockers` 中 `closedLoops` 与 `reviews` **同时消失**。

- [ ] **`overview` 的 `consistencyIssues` 为空**（`versionsConsistent = true`）。

      > **实测更新（2026-11-24 18:16）**：**NOT MET** —— 恒 **2 条**：
      > `candidate_exchange.pool_scope_checksum expected=sha256:2bea9bb4… actual=sha256:ae6b14ac…`
      > 与 `closed_loop.pool_scope_checksum expected=sha256:ae6b14ac… actual=sha256:2bea9bb4…`。
      >
      > **根因（`D-108` §3）**：**不是**数据不一致，而是
      > **`composite_manager/src/overview.rs:1195-1236` 留着**修复前的**跨作用域比较**副本**：
      > `pool_scope_checksum` 标识**作用域的**物化身份
      > （`core4` = `sha256:ae6b14ac…`、`candidate_exchange` = `sha256:2bea9bb4…`），
      > **本就不该相等**。同义的**正确**比较**已存在**于
      > `composite_manager/src/lib.rs:1721-1731`（**带修复注释**）⇒
      > **同一时刻** `pipelineStatus.upstreamAssessment` = **`consistent: true` / `issues: []`**（实证）。
      > ⇒ **同一件事被判了两次**（一次对、一次错）。
      > **修在 `M-8`**（`composite_manager/src/overview.rs`）⇒ **仍在本任务改动模块集合内** ⇒
      > **不需再扩模块范围**，但**须先扩 `SCOPE_GUARD` §1.1 文件级清单**再施工。
      > **它不阻塞复核代次发布**（发布门禁在 `rs-src/src/review_publish.rs`，不看 overview 的 issue 列表）。
      >
      > **修复实测（2026-11-24 · R1 已修）**：`overview.rs` 的**陈旧副本**已对齐
      > `lib.rs` 的**正确口径** —— `closed_loop.pool_scope_checksum` 改与 **`candidate`**（`core4`）
      > 比，并删除 `candidate_exchange` 的**跨作用域**比较与那条**重复**比较；
      > 该比较已提取为具名函数 `push_pool_scope_issue()`。
      > **新增 2 条回归测试**（`overview.rs` 内）；
      > **生产库只读实测**：`core4` vs `closed_loop` **相等** ⇒ 修复后**无 issue**，
      > 而修复前两条比较**恒不等**（假阳性）。
      > **回归**：`cargo test --offline -p composite-manager` ⇒ **20 passed**（原 18 ＋ 新增 2）。
      > 详见 [`docs/D-108`](./docs/D-108-复核代次发布落地实测与剩余pool_scope跨作用域误判_20261124.md) §3.5。
      > **仍待**：**重跑实盘** `/api/dex/overview` 确认 `consistencyIssues` **归零**
      > ⇒ 在此之前本项**不勾选**。

- [ ] **上游一致性补充**：闭环库状态中记录的**方向层版本与校验和**（I-15）与方向层库状态**一致**

      > **口径订正（2026-11-24）**：I-15 的 `directional_pool_checksum` 是 Rust
      > `directional_layer_identity()` 对**全部**方向 scope 求的**复合身份**
      > （`sha256` over `scope:version:checksum`，以 `|` 连接），**不是**任一单个 scope 的
      > `materialized_checksum`。方向层有 `candidate_exchange` 与 `core4` **两个 scope**，
      > 拿复合身份和单 scope 比 ⇒ **假失配**（实测复算 `sha256:84cf7aef…` **逐字相等**，
      > 方向层与闭环层**本就一致**）。采集器已按同一规则复算 ⇒ `C5` = **MET**。



**边界验收**：



- [ ] **未**用历史 / live 表数据**回填** Manager 复核库（**必须重新测量**）。

- [ ] **未**回灌历史深度 / 业务数据。

- [ ] `positive=0` **如实报告为业务结论**（链上不盈利），**未**被写成缺陷、**未**被当作门禁失败。



> **报告口径**：须含链 ID、快照版本、review 时间戳与新鲜度窗口读数；

> **不得**把"重跑完成"写成"缺口已闭合"。



- [ ] **重跑后重新核验**，**不得**沿用第 57 轮读数
