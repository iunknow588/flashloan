# 主网编排器计划目录 v10.12（2026-11-18 第 34 轮临时文件清理与快照证据复核版）

## 1. 唯一活动入口

本目录只允许以下三份文档承载活动任务：

| 文档 | 职责 | 使用方式 |
|---|---|---|
| `开发计划_20261113.md` | 目标架构、阶段路线、责任边界、门禁与当前执行顺序 | 了解“为什么做、按什么顺序做” |
| `未完成任务清单_20261113.md` | 当前唯一未完成任务来源 | 决定“下一项做什么” |
| `主网编排器审查修复_check_list_20261113.md` | 未完成任务的逐项验收条件 | 决定“何时算完成” |

四份活动文档统一为 **v10.12（2026-11-18 第 34 轮临时文件清理与快照证据复核版）**；
活动任务与验收条件分别使用 `TASK-001..TASK-144` 与 `AC-001..AC-210` 全局稳定编号，
机器证据必须引用所有相关编号。
已完成项不得继续留在活动文档中：历史版本遗留的已完成项已全部移入 `back/` 的
`已完成任务归档索引_*_*.md` 与 `冻结归档_*_*.md` 快照，活动清单与 checklist 只保留未完成门禁。

### 1.0.1 版本沿革

| 版本 | 日期 | 状态 | 说明 |
|---|---|---|---|
| v10.6 | 2026-11-16 | 冻结归档 | 第 21 轮回归门禁全绿（`back/冻结归档_*_20261117_v10.6.md`） |
| v10.7 | 2026-11-17 | **被 v10.8 取代** | 同日中间规划版本（新增 M0-C 前置包、三段授权拆分）；用户随后要求改为编排器驱动 + 生产库自动解除，v10.7 的三段人工授权模型被 v10.8 的级联模型取代。**未单独冻结归档**，其有效内容（M0-C 前置包、TASK-111..126、AC-176..189）已并入 v10.8。 |
| v10.8 | 2026-11-17 | 冻结归档 | 编排器级联闭环主轴 + 生产库自动解除（UNLOCK-1..6）；`back/冻结归档_*_20261117_v10.8.md` |
| v10.9 | 2026-11-17 | 冻结归档 | 第 22 轮：主网自愈编排器缺陷清除轮（8 项缺陷，10/10 门禁全绿）；`back/冻结归档_*_20261117_v10.9.md` |
| v10.10 | 2026-11-17 | 冻结归档 | 第 23 轮：编排器级联能力补齐轮（GAP-C/GAP-D/GAP-F + 链无关性证明，10/10 门禁全绿）；`back/冻结归档_*_20261117_v10.10.md` |
| **v10.11** | 2026-11-18 | **当前活动版本** | 第 33 轮：**核心缺陷定位与链无关性证明**（闭环池解析链级回落 + Rust 内核准入判决发布到链级快照）；主网闭环序列 0 → 6；Python 全量 3047 通过 / 0 失败；`back/冻结归档_*_20261118_v10.11.md` |

### 1.0.2 v10.9 归档轮（第 22 轮，编排器缺陷清除）已完成

第 22 轮清除的是**代码缺陷**，不是计划任务：`TASK-127..136` 与 `AC-190..202` 的
实施状态不受影响，活动清单条目未被删除，编号未复用。

- 归档索引：`back/已完成任务归档索引_20261117_v10.9.md`
- 人读摘要：`back/已完成任务归档_20261117_第22轮_编排器缺陷清除.md`
- 机器证据：`runtime_evidence/reports/regression_gates_round22_20261117.json`
- 冻结快照：`back/冻结归档_*_20261117_v10.9.md`

本轮清除的 8 项缺陷中，**FIX-R22-02**（`manager_v4_reviews` 没有 `payload_json`
列，导致编排器把每一条 Manager 复核都误判为缺少最优交易金额）与
**FIX-R22-04**（`pool_manager_command_support` 是死代码，旧二进制会让深度刷新
静默空跑）属于生产级缺陷，直接阻断了"复核有明确盈亏数值 → 广播"这条主链。

**明确未达成**：尚未产生套利广播交易，尚未满足稳定广播判据，生产库写入尚未解锁。
当前上游阻塞点为 L2 池深度（`pool_depth_insufficient`：`badReadings=311` /
`thin=216` / `unpriced=331`）与 L4 复核 best-amount 缺失（`missing=69`、
`ladderIncomplete=29`），须由池管理工具与复核管理工具分别修复。

### 1.0.3 v10.10 归档轮（第 23 轮，编排器级联能力补齐）已完成

本轮补齐计划 §7.0 的能力缺口，完成 `TASK-127` / `TASK-128` / `TASK-129` /
`TASK-131`，并完成 `TASK-130` 的**判据实现**部分。

- 归档索引：`back/已完成任务归档索引_20261117_v10.10.md`
- 人读摘要：`back/已完成任务归档_20261117_第23轮_编排器级联能力补齐.md`
- 机器证据：`runtime_evidence/reports/regression_gates_round23_20261117.json`
- 冻结快照：`back/冻结归档_*_20261117_v10.10.md`

| 缺口 | 成果 | 关联 |
|---|---|---|
| GAP-C | `resolve_token` 逐符号定点修复（`POST /v1/tokens/resolve`） | `TASK-128` / `AC-192` |
| GAP-D | 池「读数异常」与「容量不足」分离 + F→C→P→L 贯通矩阵 | `TASK-129` / `AC-193` |
| GAP-F | 稳定达成判据（连续 3 窗口 × ≥30min）已实现并接入 `watch`/`supervise` | `TASK-130` / `AC-200` |
| 链无关性 | 5 条可执行回归用例固化证明 | `TASK-131` / `AC-197` |

**本轮新增可见缺口**：贯通矩阵显示 **58 个闭环 hop 引用了候选集外的池**
（`hopsInScope=260/318`）——该缺口此前在编排器报告里完全不可见。

**明确未达成**：尚未产生套利广播交易；`AC-200` 稳定判据尚未满足；
`TASK-132` / `AC-196` 测试网五层全链收敛尚未完成；生产库写入尚未解锁。
下一个阻塞点为 L2 池深度的两条并行路径：`badReadingPools=691`（重测读数）与
`adequatePoolIds=17`（补足深度）。

### 1.0.4 v10.11 归档轮（第 33 轮，核心缺陷定位与链无关性证明）已完成

本轮把"主网有问题"这一现象拆成**核心缺陷**与**链特定数据条件**两部分，并用同一条代码路径
在两条链上做了对照实验。

| 缺陷 | 链无关性 | 修复位置 | 修复前 → 修复后 |
|---|---|---|---|
| **CORE-R33-01** 闭环池解析只查锚点作用域面，共享桥接池被整体丢弃（一条跳缺行 = 全市场零序列） | 两条链同构 | `market_observation_core.py`（锚点面查不到时回落到链级 `v4_exchange_pools`） | 主网序列 0 → **6** |
| **CORE-R33-02** Rust 内核准入判决只发布在锚点作用域候选面，链级快照 0/2480 带判决，读取方一律判为 `kernel_verdict_missing` | 两条链同构 | `rs-src/src/evidence.rs` + `tools/pool_manager/src/{depth_config,lib}.rs` | 主网 21 个池带判决；测试网 8/16 准入 |

- 归档索引：`back/已完成任务归档索引_20261118_v10.11.md`
- 人读摘要：`back/已完成任务归档_20261118_第33轮_核心缺陷定位与链无关性证明.md`
- 机器证据：`runtime_evidence/reports/regression_gates_round33_20261118.json`
- 冻结快照：`back/冻结归档_*_20261118_v10.11.md`

本轮一并归档第 23 轮已完成却仍以 `[x]` 留在活动 checklist 的 6 项验收条件
（`AC-190/191/192/193/197/199`），活动 checklist 的 `[x]` 计数恢复为 **0**。

### 1.0.5 核心 vs 链特定的分离规则（强制判定口径）

> **系统数据与处理流程是分开的，处理流程不得依赖具体链。**

判定一条缺陷是否为核心缺陷，必须按下列步骤取证，缺一不可：

| 步骤 | 动作 | 判据 |
|---|---|---|
| 1 | 在**两条链**上用**同一条代码路径**复现 | 两条链的缺陷签名一致 → 指向核心 |
| 2 | 定位缺陷所在代码是否**引用了链标识**（`chain_id` / `network` / `market_id` / 地址常量） | 代码内不含链标识 → 指向核心 |
| 3 | 检查缺陷是否由**该链的数据内容**触发（深度、精度、符号、费率） | 数据触发 → 链特定 |
| 4 | 修复只允许改**核心代码**或**共享策略文档** | 改核心代码后两条链同时受益 → 确认核心缺陷 |

- **核心缺陷**：在两条链上同构复现，且代码路径不引用链标识。修复必须落在核心代码里，**不得**为某条链加特判。
- **链特定数据条件**：由该链自身的链上事实触发（例：测试网闭环池实测为 `dust`/`shallow`）。
  只能用该链的管理工具（代币管理 / 池管理 / 复核管理 / 广播工具）修复，
  **不得**改核心代码绕过，也不得写进共享策略文档去迁就单链。
- **共享策略文档**（`flashloan/config/depth_policy.json` 等）的任何变更都是**全链生效**的，
  必须先证明不是为掩盖单链缺陷而调参。

第 33 轮的判定结论：

| 链 | 修复前 | 修复后 | 残余阻塞 | 性质 |
|---|---|---|---|---|
| 主网 4663 | 0 序列 | **6 序列** | 无 | — |
| 测试网 46630 | 0 序列 | 0 序列 | 38/38 闭环均被至少一个 `dust`/`shallow` 池挡住（逐跳判决统计） | **链特定数据条件** |

### 1.0.5 v10.12 维护轮（2026-11-18 临时文件清理）已完成

本轮只做**临时文件与冗余快照清理**，不改变任何业务判定逻辑、不改变生产库内容。

**清理原则（先证据后删除）**：任何快照在删除前必须逐份实测
「独有代币数 / 独有池数 / 独有表数」三项；只有相对**保留集**三项全为 0
才判定为冗余。禁止按文件名、时间或大小推断冗余。

**已删除**（约 29.8 GB）：

| 对象 | 判定依据 |
|---|---|
| `flashloan/tools/.cargo-verify-target/` | Rust 编译缓存，可由 `cargo` 重建 |
| `logs/plan/` | 2026-11-14 已迁移到 `plan/`；其中 9 份独有 healer 报告先归档到 `plan/runtime_evidence/reports/` |
| 4 份主网快照（`before_token_manager` / `pre_pool_apply` / `pre_token_invalid_path_fix` / `pre_token_repair`） | 独有代币 0、独有池 0、独有表 0 |
| `back/p0_2_4_snapshot` / `back/p0_2_5_copy_154352Z` | 同上 |
| `back/p0_2_5_source_20261108T223953Z` | 与 `mainnet_prod_before_p0_2_6` **SHA256 完全一致** |
| `back/mainnet_empty_20261108T203925Z` | 仅 `kv_meta` 1 行 |
| `diagnostics/testnet_review/` 下 5 份测试网复现/探针库 | 独有代币 0、独有池 0、独有表 0 |
| `_tmp_*.txt` × 6、零字节文件 × 41、孤儿 WAL/SHM × 9 | 脚本抓取临时件 / 零字节占位 |

**保留**（带独有历史证据，不得再清理）：

- 5 份 `plan/back/` 快照：`mainnet_prod_before_p0_2_6`（正式库已无的历史表）
  与 4 份 `p0_2_5_copy`（`v4_closed_loop_runtime_state_archive` 内容互不相同，4 个摘要）。
- 5 份 `plan/runtime_evidence/backups/` 快照：
  `local_store_4663_pre_pool_depth`（`dex_candidate_abnormal_pools_directional_v1` 2,971 行，
  其中 1,440 个代币地址正式库已无）、2 份 `46630_before_*`（测试网迁移前 47,661 代币 / 47,702 池，
  是 8,105 个零流动性池清理前的唯一记录）、2 份 `testnet_pre_*`。
- `plan/runtime_evidence/archives/`：`cow_execution_reviews` 15,981 行压缩归档
  （正式库现存仅 166 行，差额只此一份）。

**约束保持**：深度历史数据**不得**用于恢复生产库；深度异常只修代码并重新实测。

详见 `back/数据库快照保留清单_20261114.md`（已更新第 4/5/6/7 节与清理回执）。

### 1.1 v10.6 归档轮（第 21 轮）已完成

第 21 轮（闭环数据库解析分裂修复 + 生产库写入防护 + 测试环境污染修复 + 全量门禁全绿收口）
完成的 `TASK-107/108/109/110` 与 `AC-171..AC-175` 已登记到
`back/已完成任务归档索引_20261116_v10.6.md`；
人读摘要见 `back/已完成任务归档_20261116_第21轮_闭环数据库解析分裂与生产库写入防护.md`；
机器证据见 `runtime_evidence/reports/regression_gates_round21_20261116.json`；
归档前活动文档正文冻结于 `back/冻结归档_*_20261116_v10.6.md`。

### 1.2 编号契约

- 活动任务使用 `TASK-001` 至 `TASK-144` 的全局稳定编号。
- 验收条件使用 `AC-001` 至 `AC-210` 的全局稳定编号。
- 机器证据 JSON 必须引用所有相关 `TASK-*` 与 `AC-*` 编号；任务和验收允许多对多覆盖，
  但必须在证据中明确映射。
- 编号一经分配不得复用，即使对应任务已归档。

### 1.3 归档纪律

- 活动清单与 checklist 的 `[x]` 计数必须恒为 0（第 33 轮起由 `test_architecture_guards.py` 之外的计划审计核对）。
- 完成项一律先写机器证据 JSON + 人工摘要 Markdown，再登记归档索引，最后从活动文档删除。
- 同时冻结归档活动文档正文到 `back/冻结归档_*_<date>_v<version>.md`。
- 版本号变更时四份活动文档必须同步，且同步冻结旧版正文。

## 2. 文档优先级

发生冲突时按以下优先级处理：

1. 用户最新明确裁决；
2. `未完成任务清单_20261113.md` 的当前阻塞链；
3. `主网编排器审查修复_check_list_20261113.md` 的验收门禁；
4. `开发计划_20261113.md` 的阶段路线与架构约束；
5. 当前 ADR / 证据文档；
6. `back/` 内历史计划、冻结快照和归档记录。

`back/` 只用于追溯，不得作为当前执行入口。

## 3. 当前执行顺序（2026-11-17）

```text
【阶段一：可立即开始，只读/副本】
M0' 编排器级联补齐（开发任务主轴）
     GAP-A/B/E  广播修复接入 supervise + 委派化 + 五层门禁   TASK-127
     GAP-C      单币种代币定点修复（resolve_token）          TASK-128
     GAP-D      池深度不足 vs 读数异常分离 + 贯通矩阵        TASK-129
     稳定达成判据（3 窗口 × ≥30min）                        TASK-130
     链无关性证明（主网/测试网同码同路径）                   TASK-131
  ↓（可并行）
M0-C 授权窗口前置包（命令单 + 恢复演练 + 变更清单）          TASK-111/112/113
P0-9  数据库副本、估值、lineage、projected target            TASK-119/120
P0-11 只读基线刷新 + 回归口径统一                            TASK-121/122
TASK-123 EIP-170 空间重测（离线，不部署）
TASK-125 未授权期操作边界守卫
  ↓
【阶段二：测试网全链收敛】
TASK-132 五层 L1→L5 循环收敛（blockers=[] / missing=0 / positive>0）
  ↓
【阶段三：生产库自动解除 + 级联收敛】
UNLOCK-1..6 判定 → 全满足即自动解除生产写入限制             TASK-133
  ↓
L1 代币（199 admitted / 0 missing，实测已 valid）            TASK-114
  ↓
L2 池（invalid：pool_depth_insufficient，adequate 21/862）    TASK-115 / 129
  ↓
L3 闭环（106 有效；pool_scope_drift 需重物化）               TASK-116
  ↓
L4 复核（69 条，valued 69，positive 0，全部 best_amount_missing） TASK-117
  ↓
L5 广播（manager 0 条）                                      TASK-118 / 127
  ↓
稳定达成（3 窗口 ≥30min，positive>0 且 broadcast.success>0）  TASK-130 / 136
  ↓
【阶段四：后续里程碑】
AC-147/148  release manager-api.exe 停机替换 + 守护器重启（需停机窗口）
TASK-036    逐跳 amountOutMinimum（新 ABI + EIP-170 重测）    TASK-036 / 124
AC-006..010 主网 Executor 空间接线
P0-2 + P1   生产池库收敛（label / prune / 估值 / 消费者重物化）
P2–P4       精确口径、链上保护、ARCH-4 观察门禁
P5–P7       W6/W7/W8 生产铺开、闭环重建、Authority/广播移交
P8–P10      生命周期、统计与停机运维
```

**不得跳序**：

- UNLOCK 条件未全部满足前，只允许只读、dry-run、测试网与纯代码侧动作。
- **级联链只允许自上而下**：L1 代币未有效不得修 L2 池；L2 未有效不得修 L3 闭环；
  L3 未有效不得修 L4 复核；L4 未带明确盈亏不得进入 L5 广播。
- AC-155 的真实链上 parity 样本依赖 L4 复核，L4 之前不得宣称 SDK parity 已验证。
- 不得跳过 M2/M3/M4 进入 W6–W8；不得在 W7 完成前移交广播 Authority。
- **生产库写入在 UNLOCK-1..6 全部满足后自动解除**，不得以「未再次人工确认」为由长期关闭。
- Authority 切换、二进制替换、合约部署三类不可逆动作**不随自动解锁放开**，仍需停机窗口。

## 4. 当前主网只读基线

机器证据：`runtime_evidence/reports/p0_9_database_baseline_20261114.json`；
瞬时记录清理与目录保留证据：`runtime_evidence/reports/p0_10_review_cleanup_and_catalog_preservation_20261114.json`；
最新只读诊断：`runtime_evidence/reports/mainnet_readonly_diagnosis_chain4663_20261116.json`。

> **2026-11-17 只读实测（`--mode check`）**：
> L1 tokens **valid**（199 admitted / 0 missing）→
> L2 pools **invalid**（`pool_depth_insufficient`：862 unique pools，adequate 21、
> insufficient 841、thin 216、unpriced 331、badReadings 311）→
> L3 loops valid（106 / rebuild 0）→
> L4 reviews **invalid**（total 69、valued 69、missing 0、**positive 0**、
> 全部 `best_amount_missing`）→
> L5 broadcast **invalid**（manager 0、epochLegacy 0、historicalPositiveInEpoch 0）。
> `productionState=upstream_invalid`，`blockers=pools,reviews,broadcast`，
> `safeToAutoBroadcast=false`。
> **首要断裂点是 L2 池容量不足**——这正是编排器级联链要修的第一件事。

健康项：

- 核心库 4/4、股票库 194/194 元数据完整；
- 候选对 42，TOP-1 方向 84/84；
- 候选 distinct pool 93，候选 scope 活跃池 108；
- 活跃池缺失状态 0，动态费率/非法 hook 0；
- 观察池 93，非法引用 0；
- 三跳闭环 106/106，318 hops，失效 hop 引用 0；
- migration ledger 已应用 7，未知迁移 0。

当前阻塞项：

| issue | 当前事实 | 处理阶段 |
|---|---|---|
| `extension_library_contains_unanchored_tokens` | 扩展库 162，锚定 160；TB、TA 未锚定 | P0-9 |
| `core_scope_library_policy_stale` | 持久化 `core4 = [core]` | P0-8-L1 / P0-9 |
| `candidate_scope_library_policy_stale` | 持久化 `candidate_exchange = [core, stocks, extension, test]` | P0-8-L1 / P0-9 |
| `latest_manager_migration_not_applied` | `20261112_007_usdg_valuation_schema` 未应用 | P0-9 / M1 |
| `depth_snapshot_schema_stale` | 生产 `v4_exchange_pool_liquidity_snapshots` 缺 `capacity_raw` 与 `single_tick_max_*` 列 | P0-9 / M1 |
| `legacy_closed_loop_catalog_stale` | legacy `v4_closed_loops` 88 条，含 2 条 TZ/TB/TA 旧闭环；Manager 权威表为 106 条、318 hops | P0-8-L1 / P7 |

副本复验新增事实：

- 历史 `chain_tokens` 共 6,353 行；4,816 行通过标准化，1,735 行因缺少 decimals 被拒绝，
  未静默进入目标库；
- 未携带 USDG 估值证据时，194 只股票全部保持 pending，迁移回执正确返回 `valid=false`；
  生产 apply 前必须使用持久估值库或受控估值报告复验；
- 修复后 `composite-manager materialize-arbitrage` 可在副本完整 dry-run：
  候选池 114、推荐 200、观察池 130、Manager 闭环 106/318，所有 hop 均命中 candidate scope；
- `liquidity` / `capacity_raw` 是 uint128 十进制 TEXT，禁止以 SQLite `INTEGER` 或 Rust `i64`
  读取、比较或排序。

## 4.5 多闭环批处理口径（2026-11-15）

广播管理器此前**不能**一次打包多个盈利闭环：worker 只保留 `best_route`，
每条闭环各发一笔独立交易。现已实现受限批处理：

- 合约入口 `requestV4FlashLoanBatch`，单批上限 `MAX_BATCH_REQUESTS = 16`；
- 每项通过合约自调用形成独立回滚边界，失败项只记录 revert selector；
- 每项余额必须独立覆盖自身 `minProfit`，禁止前序利润串用；
- Rust 批次分支默认关闭，仅 `executorBatchEnabled=true` 时启用；
- 未部署、未切换 Authority、未广播。

机器证据：`runtime_evidence/reports/p0_8_bounded_v4_flash_batch_20261115.json`

## 4.6 输出下限口径（2026-11-15）

Uniswap V4 原生 `swap` **没有** `amountOutMinimum` 参数，只有 `sqrtPriceLimitX96`。
因此链上唯一能表达的成交价约束是限价，不是输出下限。

当前实现是**路线级** reviewed minimum output：

- Rust 从成功的复核结果读取 `selectedKeys`、`selectedDirections`、`outTb`、`outTa`、`outTz`
  与三跳限价，自动构造 bounded executor 请求；
- `swapTargets` 全部解析为 pool 基础目录中三只池共同的 PoolManager，
  `swapCalldatas` 为确定性编码的三笔 exact-input `swap`；
- 路线级 `minAmountOut` 取复核最终输出 `outTz`（raw units）；
- 该值经请求既有 `gasReserve` 字段传入——V4 记账路径从未强制该字段
  （gas 是沉没成本，余额判定只用 `minProfit`），复用它不改变既有语义，
  也不需要改动处于 EIP-170 极限的热路径 ABI；
- 合约在利润门禁与结算之前比较本次路线的**增量输出**：
  `balanceAfter >= balanceBefore + minAmountOut`，旧余额不得补贴劣化成交；
- Rust 侧拒绝没有可证明下限的路线进入 executor batch。

自动构造只使用 pool 基础目录与当次可再生复核结果，不为 review/submission/broadcast
增加长期持久化字段。

## 4.7 第 17–21 轮已归档结论摘要

### 4.7.1 第 17 轮：测试网闭环复核全链收敛

- 测试网 `robinhood-testnet/46630` Manager 全工具链打通；
- token → candidate-edge → candidate-exchange → closed-loop → review 按同一版本链重建成功；
- 8/8 复核带明确盈亏（`withExplicitPnl=8/8`、`dataIncompleteCount=0`、`failedCount=0`）；
- 三个结构性根因：`Pool.State` 流动性槽位偏移、陈旧 `manager-api.exe`、legacy 旋转环匹配失配。

> **限制（必须与结论同时引用）**：8 条复核全部 `not_executable`——测试网采样价格下
> **不存在执行确认的正净收益**，链路结构完整但**当前没有盈利路由**。

### 4.7.2 第 18–21 轮回归门禁收口

| 轮次 | 范围 | 关键结果 |
|---|---|---|
| 第 18 轮 | 回归门禁收口 | tools 519/0、rs-src 127/0；失败集合 97 → 93，新增 0 |
| 第 19 轮 | 测试网页面复验 + 跨 scope 假阳性 | tools 520/0；503 实为 fail-closed 409 守卫；`composite_manager` 跨 scope 假阳性修复 |
| 第 20 轮 | 工作区清理 + 回归基线 + 参数分层 | 11 collect error → 0；失败 93 → 41；`.env` 参数下沉 35 项链 |
| 第 21 轮 | 闭环库解析分裂 + 生产写入防护 | unit **2624 passed / 0 failed**；functional+integration **398/0**；两库 fixture rows = 0 |

第 21 轮修复：

| ID | 根因 | 修复 |
|---|---|---|
| `FIX-R21-01` | `mark_group_submitted` 与 `complete_loop` 使用不同数据库解析来源 → 永久停在 `preflight_running` | 单一权威解析器 `_closed_loop_database_url`，链级 scope 绑定，禁止回落到别链库 |
| `FIX-R21-02` | 测试可直接写真实链库 | `_guard_real_chain_databases` 拦截写入口，含按名 import 的模块级绑定 |
| `FIX-R21-03` | `FLASHLOAN_KV_PATH` / registry / CWD / RPC offline 污染 | 4 个隔离 fixture |
| `FIX-R21-04` | 主网 2 行 + 测试网 1 行 fixture 污染 | 清理并二次只读复核 |

## 4.9 内存对象处理约束（强制）

系统只有一条合法数据流：**SQLite 初始化读取 → 全局内存对象（WSS 实时更新）→ 业务处理全部走内存对象 → 结果经队列传递 → 落库**。

| 编号 | 约束 |
|---|---|
| MIO-1 | SQLite 只在初始化/刷新时读取；处理路径不得逐次查库 |
| MIO-2 | 实时数据必须写入全局内存对象，处理模块读内存对象 |
| MIO-3 | 最佳交易量、闭环容量、池状态、代币元数据全部来自内存对象 |
| MIO-4 | 处理结果经队列传递给对应模块，再由该模块落库 |
| MIO-5 | Python 只做纯消费与展示，不得拼装业务对象 |
| MIO-6 | Python 不得成为第二 authority，不得重建权威值 |
| MIO-7 | 闭环身份（loop key）、闭环容量、复核盈亏必须来自闭环管理器内核对象 |
| MIO-8 | 每条链独立 SQLite，查询必须同时带 network + chain_id |

补充强制项（与 MIO 同级）：

| 编号 | 约束 |
|---|---|
| MIO-9 | 复核证据的 `loopKey` 必须由内核发布；任何一层都**不得**用路由地址兜底重建，无身份即不予复核 |
| MIO-10 | 内核必须发布 `signedPnl.amount`（f64 人类可读金额）与 `runArbitrage.executable` / `runArbitrage.signedPnl`，否则复核记录无法落库 |

完整定义、禁止的实现方式、复核证据字段形态与验收方式见
`plan/开发计划_20261113.md` §4.1（含 §4.1.3 复核证据的内核发布形态）。
违反 MIO 的实现一律判定为不合格，必须在当轮修正。

代币层缺信息的处理见 `plan/开发计划_20261113.md` §4.2：
**能修复就修复，找不到可用的符号与精度就删除**，并同步删除引用它的记录。

## 5. v10.7 规划变更摘要

| 变更 | 原因 | 影响 |
|---|---|---|
| 新增 M0-C「授权窗口前置包」 | v10.6 中 M1 准备散落，未形成可签字的独立交付物 | `TASK-111/112/113`、`AC-176/177/178` |
| P0-8-L 拆为 L1/L2/L3 三段授权 | 混在一个窗口会让广播异常回滚波及已正确的 scope 与复核数据 | `TASK-114..118`、`AC-179/180/181` |
| 新增关键路径依赖图与并行槽 | v10.6 只有线性文字流，无法判断并行可行性 | `开发计划` §6.3 |
| 新增授权矩阵 | 授权类型分散描述，无单一事实源 | `开发计划` §8.2 |
| 新增证据状态机 | 证据规范只定义字段，未定义状态转移 | `开发计划` §10.2、checklist §0.3 |
| 新增风险登记册（含责任边界） | 风险表无触发后的具体动作 | `开发计划` §12 |
| 新增未授权期允许/禁止清单 | 避免等待授权期间越界操作 | `开发计划` §15 |
| 统一回归基线口径 | 文档记载 41 项与实测 4 项两种口径并存 | `TASK-122`、`AC-185` |

### 5.1 v10.8 规划变更（用户裁决驱动）

> 用户明确要求：**生产库限制在前提依赖解决后自动解除**（不要长期关闭）、
> **用主网自愈编排器驱动开发任务**（编排器本身与链无关，是一种处理机制）、
> **从资金代币 / 候选代币 / 闭环角度全范围考察**，
> 按 **代币 → 池 → 闭环 → 复核 → 广播** 级联反复修复，
> **直到主网稳定产生套利广播交易**，然后持续观察统计盈亏。

| 变更 | 原因 | 影响 |
|---|---|---|
| 新增 **编排器级联闭环**主轴 | 需要把五层修复串成一条自动收敛链 | `TASK-127..132`、`AC-190..193`、`AC-196/197/199` |
| **生产库写入改为自动解除**（UNLOCK-1..6） | v10.7 把生产写入当静态人工门禁，计划执行到一半卡死 | `TASK-133..136`、`AC-194/195/198` |
| 补 **GAP-A/B/E** | `repair_broadcast_failures()` 已实现但**从未被调用**（死代码）；`repair()` 广播层硬编码 `skipped`；门禁只覆盖三层 | `TASK-127` |
| 补 **GAP-C** | 代币分支只有批量 `materialize-libraries`，无法定点修一个坏币 | `TASK-128` |
| 补 **GAP-D** | 池容量不足与读数异常修复路径不同，混为一谈会误修 | `TASK-129` |
| 新增 **稳定达成判据** | 无「稳定产生广播」的可判定定义 | `TASK-130`、`AC-200` |
| 新增 **链无关性证明** | 用户强调编排器本身与链无关 | `TASK-131`、`AC-197` |
| 执行队列重排为 **四阶段** | 反映「先补编排器 → 测试网验证 → 生产自动解锁 → 后续里程碑」 | §6 |

## 6. 当前立即执行队列（2026-11-17）

**执行主轴 = 编排器级联闭环。** UNLOCK 条件未全部满足前，只允许第 1–11 项。

### 6.1 阶段一：编排器补齐 + 只读/副本（全部无阻塞）

| # | 项目 | 类型 | 阻塞 |
|---|---|---|---|
| 1 | `TASK-127` GAP-A/B/E：广播修复接入 `supervise` + 委派化 + 五层门禁 | 代码/测试 | 无 |
| 2 | `TASK-128` GAP-C：单币种代币定点修复 `resolve_token` | 代码/测试 | 无 |
| 3 | `TASK-129` GAP-D：池异常分离 + 资金→候选→闭环贯通矩阵 | 代码/测试 | 无 |
| 4 | `TASK-130` 稳定达成判据 + 滚动窗口观测 | 代码/测试 | 无 |
| 5 | `TASK-131` 编排器回归 + 链无关性证明 | 测试 | 无 |
| 6 | M0-C 前置包（命令单 / 恢复演练 / 变更清单） | 只读+副本 | 无（并行） |
| 7 | P0-9 副本门禁（估值证据 / lineage receipt / projected target） | 只读+副本 | 无（并行） |
| 8 | `TASK-123` EIP-170 空间重测脚本（离线，不部署） | 只读/离线 | 无 |
| 9 | `TASK-121` 主网只读基线刷新 | 只读 | 无 |
| 10 | `TASK-122` 回归基线口径统一与自动化比对 | 只读/测试 | 无 |
| 11 | `TASK-125` 未授权期操作边界守卫 | 只读/测试 | 无 |

### 6.2 阶段二：测试网全链收敛

| # | 项目 | 类型 | 阻塞 |
|---|---|---|---|
| 12 | `TASK-132` 测试网五层收敛（`blockers=[]` / `missing=0` / `positive>0`） | 测试网写 | 依赖 #1–#5 |

### 6.3 阶段三：生产库自动解除 + 级联收敛

| # | 项目 | 类型 | 阻塞 |
|---|---|---|---|
| 13 | `TASK-133` UNLOCK-1..6 判定 → **自动解除生产写入限制** | 判定 | 依赖 #6 #7 #12 + 回归门禁 |
| 14 | `TASK-134` 主网级联执行器（L1→L5 循环，单层失败即回滚） | 生产写 | 依赖 #13 |
| 15 | `TASK-135` 单层回滚演练证明 | 生产回滚 | 依赖 #14 |
| 16 | `TASK-136` 稳定广播达成 + 持续观察统计 | 生产写 + 广播 | 依赖 #14 |

### 6.4 阶段四：后续里程碑

| # | 项目 | 类型 | 阻塞 |
|---|---|---|---|
| 17 | AC-147/148 停机替换 + 守护器重启 + health/页面验证 | 停机 | 需停机窗口 |
| 18 | `TASK-036` 逐跳 `amountOutMinimum` 实施 | 合约 + 部署 | 需停机窗口 + EIP-170 重测 |
| 19 | `TASK-124` 主网 Executor 空间接线 | 生产接线 | 依赖 #18 |
| 20 | P2–P10 里程碑序列 | 混合 | 按 §3 顺序 |

> **阶段退出未达成，不得宣称主网已产生套利广播交易。**
> 当前主网只读实测（2026-11-17）：
> L1 tokens **valid**（199 admitted / 0 missing）；
> L2 pools **invalid**（`pool_depth_insufficient`，862 unique pools 中 adequate 仅 **21**）；
> L3 loops valid（106）；L4 reviews **invalid**（69 条、valued 69、missing 0、
> **positive 0**、全部 `best_amount_missing`）；L5 broadcast **invalid**（manager 0）。
> `productionState=upstream_invalid`，`blockers=pools,reviews,broadcast`，
> `safeToAutoBroadcast=false`。
>
> **L2 池容量不足是当前首要断裂点**——先修 L2，L4 的正收益与 L5 的广播才会出现。

## 7. 目录结构

```text
plan/
├── README.md                                  ← 本文件（目录导航）
├── 开发计划_20261113.md                        ← 阶段路线（唯一）
├── 未完成任务清单_20261113.md                   ← 任务来源（唯一）
├── 主网编排器审查修复_check_list_20261113.md     ← 验收门禁（唯一）
├── runtime_evidence/
│   ├── reports/                               ← 机器证据 JSON / 报告 MD
│   ├── diagnostics/                           ← 诊断产物
│   └── ...                                    ← 阶段证据目录
└── back/                                      ← 归档、冻结快照（仅追溯）
    ├── 已完成任务归档索引_*.md
    ├── 已完成任务归档_*.md
    ├── 冻结归档_*.md
    └── 阶段性设计与审计_*/
```

## 8. 归档与追溯

| 需要追溯什么 | 去哪看 |
|---|---|
| 某任务是否完成 | `back/已完成任务归档索引_<date>_v<version>.md` |
| 某轮做了什么 | `back/已完成任务归档_<date>_第<N>轮_<主题>.md` |
| 某版本活动文档原文 | `back/冻结归档_<doc>_<date>_v<version>.md` |
| 某结论的机器证据 | `runtime_evidence/reports/<name>.json` / `.md` |
| 某设计的决策过程 | `back/阶段性设计与审计_*/` |

`back/` 只用于追溯，**不得作为当前执行入口**。