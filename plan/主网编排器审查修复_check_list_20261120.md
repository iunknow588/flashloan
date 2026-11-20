# 主网自愈编排器审查修复 Checklist v10.22（2026-11-20）

> 仅包含**开放**验收条件。全部保持 `[ ]`；完成时先保存当前机器证据与人工摘要，再把对应任务和验收项归档至 `back/`，然后从活动清单与本清单移除。

## 0. 约束

- 活动任务：`TASK-141..TASK-153`；验收编号：`AC-211..AC-214`。**编号不复用。**
- Rust 内存内核是金额、容量、利润和闭环身份的权威。Python 仅消费和转发。
- 链库、chain ID、RPC/WSS、权限和运行状态互相隔离。
- 测得 0 与未测量必须显式区分；不完整、陈旧或未知一律 fail-closed。
- 根目录 `scripts/` 为仓库级工具入口；**不移动到 `plan/`**。计划目录仅承载计划、证据、checklist 和归档。
- **前提证伪即删除**（2026-11-20 第 44 轮）：原「GOOGL 样本 `loop_key` Y 段为 `0x0` 是退化路由」一项的前提经只读核验为假 —— `0x0` 是 robinhood chain 4663 上 **ETH 的规范原生地址**（`token_kind='native'`、`is_native=1`、`admission_status='admitted'`），路径含它的 **76** 条闭环**全部** active 且容量 > 0。**该检查项已整体删除，不改写为否定式断言，也不作为验收项、指标或测试断言。**

## 1. P0 闭环身份正确性

### TASK-149｜闭环起点身份精确匹配

> 代码实现**已完成并归档**（`back/已完成任务归档_20261120_第40轮_闭环身份精确匹配与基线收口.md` §2）。
> 匹配机制已在主网库只读核验中验证（164/164 精确命中、无路径冲突，见开发计划 §7.1）。
> 以下条目为**仍需运行期复跑**的部分。

- [ ] **AC-214** 运行期复核确认闭环 lookup 使用 Manager 发布的完整、有序身份精确匹配；闭环身份 = **资金锚点 + 方向池子**，起点包含在身份中；命中失败时不得继承任何其他闭环的 loop key、pool IDs、capacity、USDG valuation 或阈值。
- [ ] **旋转能力零存在**（源码级守卫，非行为断言）：全仓（Rust 内核 / Python 运行时 / 脚本层）**不存在**闭环旋转查找、旋转归一化、旋转 alias merge、canonical rotation 的任何定义或调用；命中与否**不作为**验收项、指标或测试断言。
- [x] **同锚点双方向闭环结构已确认**（2026-11-20 只读核验，见开发计划 §7.4）：同一资金锚点下的 `X-Y-Z-X` 与 `X-Z-Y-X` 被实现为**两个完全独立的闭环**（各自独立 loop key / pool ids / 容量 / 绑定池 / 阈值 / 运行时状态行）。主网 158 对同锚点反向闭环 **158/158 解析到不同身份、池集合与容量**，池集合相同或容量相同的对均为 **0**；四层（Manager 发布 / Rust 目录 / Python 状态机 / review 管理器）**无合并、无归一化、无重排**。Rust 回归测试 `one_anchor_carries_two_independent_directed_closed_loops` 已补齐。
- [ ] **实盘证据包**（缺任一项视为未验证）：
  - [ ] 被测闭环的 Manager 原始发布身份（loop_key + path_symbols/path_addresses + pool_ids）
  - [ ] 被测闭环的查询身份
  - [ ] 匹配结果（hit / miss）
  - [ ] miss 诊断（含 `repairTarget`）
  - [ ] 真实 mainnet review 消费 Manager 原始身份的确认
  - [x] **纯消费后归并正确性**：`currentDistinctLoops` 与 Manager 闭环数同口径对照，不出现“一个 Manager 闭环被拆成多个 Python 身份”。**（复跑读数：`currentDistinctLoops=44`、`currentLoopMatched=44`、`staleOrUnmatched=0` —— 无拆分、无多余身份。）**
- [ ] **孤儿状态行处置**：`v4_closed_loop_runtime_state` 中 2 条在 Manager 目录无对应有序路径的状态行（TZ/TA/TB 闭环的不同起点）按**精确键孤儿**处置：被精确诊断出来，不得与任何闭环关联，不得据此选择 capacity / pool / USDG / threshold。
- [x] **强制复跑**（不可省略）：重跑主网 review，产出对比报告，至少分出三类 —— `closed_loop_identity_mismatch`（新增诊断）、`single_tick_capacity_unavailable`（真实缺容量）、正常完成。**（2026-11-20T12:04:19Z 只读复跑完成，三类已分离：`single_tick_capacity_unavailable` **1 → 0**；`closed_loop_identity_mismatch` **0**（`missingReasons` 空、`missingClassifications` 空、`staleOrUnmatched=0`）；正常完成 44/44 且 `bestAmountConfirmed=44`。）**
  - [x] 判读规则：若 `single_tick_capacity_unavailable` 样本数**未减少**，说明 TASK-148 是独立阻塞项，按 `TASK-148` 继续；**不得**因此判定 TASK-149 失败。**（样本数已减少 1→0，TASK-148 独立阻塞成立且已修复，见 §2。）**
- [ ] **关闭门槛**：`AC-214` 与本节全部条目通过，且归档记录写明"资金锚点 + 方向池子"的身份语义与精确检索方式；**仅通过单元测试不构成关闭依据**。

## 2. P0 金额与复核

### TASK-148｜单 tick 容量不可用根因（AC-213）

> 容量投影与缺失/零/正的诊断实现已归档（内核已发布 `single_tick_capacity_unavailable` 与 `singleTickCapacityBasis`）。
> **根因已定位并修复**（2026-11-20，见开发计划 §7.3）：内核 SQL 投影 `load_manager_closed_loop_rows` **遗漏 `path_addresses_json`**，导致闭环目录恒空、容量永不落地。修复后目录 164/164、容量 164/164 > 0。
> 以下只保留**修复后仍需的实盘验证**。

- [x] **AC-213** 用最新主网复核复现 `single_tick_capacity_unavailable` 样本；保存闭环身份、pool ID、状态版本和 Rust 原始诊断。**（2026-11-20 只读复跑已完成：复跑前唯一缺失样本为 `cow_execution_reviews.id=69547`，原因串 `chain review failed: single_tick_capacity_unavailable`；复跑后缺失样本 0。）**
- [x] 沿四层归因责任层：① 实时池状态 ② pool-manager 容量投影 ③ Rust registry ④ review 输入；**修复责任层，不在 healer/Python 填默认值**。**（责任层 = 内核 `sqlite_read.rs::load_manager_closed_loop_rows`；未在 healer/Python 引入任何默认值。）**
- [x] **定位链路收敛**（2026-11-20 只读归因已确立）：Manager 库发布的 164 条闭环容量**全部 > 0**，而 legacy review 仍有 11536 条报 `single_tick_capacity_unavailable`。容量缺失**不在 Manager 发布列上**，必须沿 **Manager → 内核快照 → review 输入** 定位（内核 `apply_closed_loop_identity` 的命中与 pool identity 校验、review 组装阶段），**不得**回头反复查看 Manager 容量投影。
- [x] **责任层定位与修复**（源码级守卫，非行为断言）：`sqlite_read::load_manager_closed_loop_rows` 的 SELECT **必须**包含 `path_addresses_json`（闭环目录以有序 3 地址路径为键，缺该列 → 目录恒空 → 容量永不落地）。修复只恢复**已发布**容量，**不得**引入默认值、零填充或兜底容量。
- [x] **修复后实盘复跑**：重跑主网 review，确认 `single_tick_capacity_unavailable` 样本数显著下降；剩余样本须逐条给出**新的**真实原因（初始量缺失、RPC 失败等），**不得**以容量缺失笼统归因。**（2026-11-20T12:04:19Z 只读 `--mode check` 复跑完成：`missingValue` 1→0、`bestAmountMissing` 1→0、`bestAmountLadderIncomplete` 1→0、`missingReasons` 清空、`blockers` `['reviews','broadcast']`→`['broadcast']`、44/44 manager review 容量 > 0。剩余 44 条 `review_reason='runArbitrage returned ok=false'`、`positive=0` 为链上不盈利的**真实业务结论**。）**
- [x] 次要原因一并归因（不另开任务）：`preview pool initialAmountRaw is required` 1180 条、RPC `eth_call` 失败 437 条。**（2026-11-20 只读归因完成：1180 条写于 11-18 12:17–13:34Z，早于修复 `5ff7a1d8`，成因是 preview-pool 路径把调用方金额当必填，内核已改为从已发布容量推导起始量，回归 `the_preview_pool_path_does_not_require_the_caller_to_supply_an_initial_amount`；437 条属**传输层外部依赖**，内核无缺陷可修。三类目标缺值全部早于对应修复提交，均为**历史数据**。）**
- [x] 在实盘样本上分别确认容量缺失、容量为零和正容量三种状态；**缺失不能伪装为 0**，容量 0 必须有真实测量和原因。**（只读核验：Manager 发布 164 条闭环容量全部 > 0，无 NULL、无 0；修复后 44/44 manager review 携带正容量与绑定池 ID，缺失态在复跑后为 0。）**
- [ ] 在同一当前内存快照下，单 tick 起始量、跨 tick 搜索上限和最终 best amount 证据可追踪且单位正确。
- [x] 内核及 pool-manager/review-manager 定向测试通过，并在真实 schema/状态 fixture 上验证。**（`cargo test --offline`：rs-src 185 passed / tools 29 crate 通过；新增回归 `the_closed_loop_rows_carry_the_address_path_the_catalog_is_keyed_by`。）**

### TASK-142｜单 tick 初始量到跨 tick 搜索

> Rust 搜索边界和零值语义的代码实现已在第 39 轮归档。以下只保留端到端验证。

- [ ] 用真实闭环状态证明：非零单 tick 初值进入既有 double/halve ladder；单 tick 初值为 0 时跨 tick 调用数为 0；无固定 `1`、无递归、无 Python 金额计算。
- [ ] 用同一快照证明跨 tick 基于闭环真实输入/输出且不做 tick 遍历模拟；深度容量仅作为搜索上限。
- [ ] 主网和测试网使用相同 Rust 计算代码，并分别保存原始 ladder 输出、选中金额、利润和状态快照版本。

### TASK-143｜Review 金额与利润完整性

- [ ] 每条 active review 含 Rust 确认的 best amount raw、测量标记、有符号 PnL raw、币种/精度、来源、路线身份、快照版本和 review 时间。
- [ ] 修复主网所有 `best_amount_missing` 与 `data_incomplete`；完整 review 中**缺值为 0**。`not_executable` 保留真实原因，不伪造利润。
- [ ] 已测零利润被计为**有效零**；未执行 ladder 或未收到 Rust 结果**仍是缺值**。
- [ ] Manager/legacy 同路线归并唯一且保留明确金额/利润事实；Manager hop 表缺失**不致崩溃**。
- [ ] 重新运行并验证 review 新鲜度在 **600 秒**内；报告完整/缺值/零/负/正数量和逐条修复目标。

### TASK-145｜USDG 估值单位与覆盖（AC-212）

> Manager 侧 USDG identity 估值与内核侧跨 tick 估值输入的发布已归档（第 39 轮）。以下只保留真实 review 的单位复核。

- [ ] **AC-212** USDG identity、ETH/WETH 路径、token decimals、借款本金、fee、gas 和 signed PnL 的 raw/human 单位契约经 Rust 单测与真实 review 输出共同验证；**缺价格时 fail-closed**，不以默认零或估值补值。
- [ ] ETH/WETH 估值方向与 token decimals 正确；USDG anchor 使用稳定币原始单位 identity，**不依赖 USDG/USDG 池**。
- [ ] review 的借款本金、fee、gas、signed PnL 使用可审计一致单位；保存实际 review 原始字段对照。
- [ ] 主网 88/88 覆盖在新鲜 Manager 状态下复核；测试网 Manager loops>0 后执行同一验证。**覆盖率不能替代真实 review 单位检查。**
- [ ] 其他无可用价格/精度的代币明确报告 **coverage gap**，不使用零汇率或默认汇率。

### TASK-150｜闭环身份第二维度：方向池子集合可独立复核（P0）

> 缺口已核实（2026-11-20 源码 + 只读）：`worker_pool.rs::apply_closed_loop_identity` 的池集合比对分支依赖 `routePoolIds` / 顶层 `poolIds`，而主网 44 条 review payload 中这两个键**均不出现**（`poolIds` 只在 `quote.candidate_pools` / `quote.route.hopCandidates` 嵌套位置），因此该分支**永不参与判定**。身份结果正确，但这道保险是哑的。

- [ ] review 配置以 `routePoolIds` 承载**该路由实际使用的有序池 ID 列表**，与 Manager `pool_ids_json` 语义一致（有序、逐跳对齐）。
- [ ] **池集合比对真实参与判定**：构造「地址路径正确但池集合不一致」样本，必须报 `manager_closed_loop_pool_identity_mismatch` 并 fail-closed；**不得**沿用 Manager 池集合或容量。
- [ ] 比对**顺序敏感**（方向池子有方向）；比较前仅做小写/去空白归一，**不得**去重、**不得**重排。
- [ ] 池集合缺失必须**显式**记录状态，**不得**静默跳过校验后宣称身份已完整匹配。
- [ ] **不得**引入兜底池集合、默认池或空集合默认值来"通过"校验；失败即 fail-closed。
- [ ] 回归测试覆盖三类：一致通过 / 不一致 fail-closed / 缺失显式。
- [ ] 主网只读复跑后核对 44/44 review 的池集合校验状态可读，且与 `manager_v4_reviews.pool_ids_json` 同口径一致。

### TASK-151｜同锚点双方向闭环的跨层守卫（P1）

- [ ] **架构守卫**：`flashloan/app_runtime/tests/unit/test_architecture_guards.py` 增加守卫 —— 任一锚点的方向闭环集合**两两拥有互斥的池集合与容量**；不得出现「一个 Manager 闭环被拆成多个 Python 身份」或反向按锚点聚合。
- [ ] **Manager 侧断言**：`flashloan/tools/closed_loop_manager` 增加测试 —— 同一锚点存在 `Y→Z` 与 `Z→Y` 时必须发布**两条独立记录**（不同 `loop_key`、不同 `pool_ids`），不得合并。
- [ ] **方向敏感**：两条反向闭环的中间方向键必须**互为反向**（`{y}->{z}` 与 `{z}->{y}`），不得同向或缺向。
- [ ] 守卫必须能在**结构退化时红灯**（构造聚合/合并场景应使守卫失败），避免恒真断言。

### TASK-152｜观测层命名消歧（P2）

- [ ] `market_observation` 的 `group_key`（`market_observation.py:884`）更名为 `edge_group_key` 或等价明确命名，并同步更新全部读取点与测试。
- [ ] 改名**不得**改变分组语义、键值内容或持久化形态。
- [ ] 落地命名约束：凡出现该键的观测层代码必须注明"池边观测分组，闭环身份在 Manager/内核层"。

### TASK-153｜开发计划口径补充（P2，文档）

- [x] 开发计划 §3.2 补充「**同一资金锚点下可以有多条方向闭环，它们各自独立**」，给出 `X-Y-Z-X` / `X-Z-Y-X` 实例与 §7.4 引用。**（2026-11-20 第 45 轮已完成）**
- [x] §3.2 明确观测层 `edge_group_key`（原 `group_key`）是池边分组，**不是**闭环身份。**（2026-11-20 第 45 轮已完成）**
- [x] §7.6 记录「方向池子这一半是哑保险」的缺口分析与核验读数。**（2026-11-20 第 45 轮已完成）**
- [x] 文档**不引入**"旋转"概念或任何以"旋转"为前提的表述。**（已复核：无）**

## 3. P1 测试网级联和广播

### TASK-147｜测试网有效候选、池与 Manager 闭环（AC-211）

- [ ] 记录 RPC/WSS endpoint 解析、chain ID 与只读请求状态；失败时**禁止 apply**。
- [ ] 每个 token 报告 primary library 与 extension overlap 身份；AMZN overlap 不重复准入或手改状态。
- [ ] 每个 scope 报告 raw/active/blocked/skip reason；以当前链状态刷新 pool depth/tick，**不放宽固定门槛**。
- [ ] 证明确有至少两个不同 admitted token 形成观察方向；要求 `direction_count>0`、Manager `loopCount>0`。
- [ ] Manager loops/hops/optimizations、上游版本/checksum 全部闭合后才运行测试网 review。
- [ ] **不从历史备份或 legacy 表回灌**闭环、深度或权限数据。

### TASK-146｜级联治理与自动放行

- [ ] token 缺关键字段先交 token-manager；只有修复失败才允许删除，并同步清理依赖。
- [ ] pool 缺陷指向 pool-manager；闭环缺边指向 closed-loop-manager；金额/利润缺失指向 review-manager/Rust 内核。
- [ ] 迁移 011..013 按两条链核验；每条链目标 DB/schema/checksum 与备份可读性有独立证据。
- [ ] 所有修复具幂等键与原因/影响/回滚点；失败**不丢候选、不跨链写入**。
- [ ] 当前层依赖和回归通过后自动解除对应限制；**不得永久关闭生产库或越层放行**。

### TASK-144｜广播门禁与错误修复

- [ ] 主网复核新鲜、完整后执行 **dry-run**，核对 chain、账户、nonce、gas、目标合约、金额、快照、幂等键。
- [ ] `profit<=0`、利润或金额未知、状态陈旧、权限错配均**不能**进入真实广播队列。
- [ ] 测试网完成广播错误分类、有限重试、幂等、hash/receipt 和 Manager 修复路径演练。
- [ ] 真实广播须有**独立、明确**的链、账户、限额和时间窗口授权；没有授权**不发送**。
- [ ] 交易记录、复核证据和链上回执可审计关联；失败不描述为已成交或盈利。

## 4. P2 生产稳定

### TASK-141｜双链准入和稳定窗口

- [ ] 测试网完整级联先通过；主网按相同代码逐层验证，DB 与链配置独立。
- [ ] 运行时 API 与 Manager API 健康状态**仅作为服务检查**，不替代业务闭环验收。
- [ ] 连续 **3 个各至少 30 分钟**窗口均满足 `blockers=[]`、`missingValue=0`、`positive>0`。
- [ ] 每个窗口关联复核、广播请求、交易 hash、receipt 和实际盈亏；记录 gas、借款费、滑点及模型偏差。
- [ ] stale WSS、版本漂移、重复提交、错误激增或实际亏损触发对应广播准入关闭，同时保留诊断/修复通道。

## 5. 总体停止条件

- [ ] 机器证据包含 TASK/AC、代码版本、chain ID、DB 绝对路径、schema/checksum、工具版本、时间范围和原始计数。
- [ ] Python 全量单测、Rust 内核、Manager workspace、架构守卫及相关回归在当前 `scripts/` 根目录布局下通过；**跳过和警告逐项说明**。
- [ ] 无历史深度/业务数据回灌；无用默认零或估值掩盖缺陷；**无未授权真实广播**。
- [ ] 缺 amount/PnL、陈旧快照、身份不唯一、签名授权不足、重复提交或 receipt 无法关联时**停止广播**。

## 6. 归档门槛

- 关闭任一任务前，先把当前机器证据与人工摘要存入 `back/`，再从活动清单移除该任务，并在此清单删除对应验收项。
- 归档记录须含：任务号与验收号、代码版本、证据文件路径、chain ID、DB 路径与 checksum、执行时间、结论与残留风险。
- **编号永久不复用**；归档后不得再以同一编号新开任务。
- 实现完成但缺端到端证据的任务**保持开放**，只归档其已验证子项，并在归档记录中写明未覆盖部分。
