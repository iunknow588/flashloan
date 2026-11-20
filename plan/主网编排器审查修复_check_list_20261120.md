# 主网自愈编排器审查修复 Checklist v10.30（2026-11-21）

> **本清单仅包含未完成的验收条件。**已完成的验收项已从本文件移除，其证据与诚实边界完整保存在 `plan/back/`。
>
> **归档索引**：`plan/back/已完成任务归档索引_20261120_v10.22.md`（v10.29）。
> **归档前快照**：`plan/back/冻结归档_check_list_20261121_v10.29.md`（保留了本次收敛前的全部验收项与读数）。
>
> **当前开放任务**：`TASK-143`（剩新鲜度）、`TASK-145`（剩测试网同一验证路径）、`TASK-154`（原生 ETH 偿还）、`TASK-147`（测试网闭环恢复）、`TASK-146` / `TASK-144` / `TASK-141`。
> **已归档不再复用的编号**：`TASK-142`、`TASK-148`、`TASK-149`、`TASK-150`、`TASK-151`、`TASK-152`、`TASK-153`、`AC-213`、`AC-214`。
> **下一个可用任务编号**：`TASK-155`。

## 0. 约束

- 活动任务：`TASK-141`..`TASK-147`、`TASK-154`（已归档不复用：`TASK-142`、`TASK-148`..`TASK-153`）；验收编号：`AC-211`..`AC-212`（`AC-213`/`AC-214` 已关闭）。**编号不复用；下一个可用任务编号为 `TASK-155`。**
- Rust 内存内核是金额、容量、利润和闭环身份的权威。Python 仅消费和转发。
- 链库、chain ID、RPC/WSS、权限和运行状态互相隔离。
- 测得 0 与未测量必须显式区分；不完整、陈旧或未知一律 fail-closed。
- 根目录 `scripts/` 为仓库级工具入口；**不移动到 `plan/`**。计划目录仅承载计划、证据、checklist 和归档。
- **前提证伪即删除**（2026-11-20 第 44 轮）：原「GOOGL 样本 `loop_key` Y 段为 `0x0` 是退化路由」一项的前提经只读核验为假 —— `0x0` 是 robinhood chain 4663 上 **ETH 的规范原生地址**（`token_kind='native'`、`is_native=1`、`admission_status='admitted'`），路径含它的 **76** 条闭环**全部** active 且容量 > 0。**该检查项已整体删除，不改写为否定式断言，也不作为验收项、指标或测试断言。**

## 1. P0 金额与复核

### TASK-143｜Review 金额与利润完整性（**第 50 轮只读核验完成，任务仍开放**）
> **已完成部分（已归档，不再重复验收）**：五项中的 **4 项**（完整性、缺值清零、测得零与缺值区分、Manager/legacy 归并唯一）已由机器证据证实。
> 证据 `plan/runtime_evidence/reports/task143_review_completeness_20261121.json`；归档 `plan/back/已完成任务归档_20261121_第50轮_TASK143只读完整性与新鲜度核验.md`。
> **诚实边界（仍然有效）**：主网 44 条 review 合计 `-3.385058`（`positive=0`）属**链上不盈利的真实业务结论**，不是缺陷。

> 证据：`plan/runtime_evidence/reports/task143_review_completeness_20261121.json`；
> healer 只读复核 `plan/runtime_evidence/reports/mainnet_arbitrage_healer_check_chain4663_20261120T205928Z.json`；
> 脚本 `plan/runtime_evidence/tools/collect_task143_review_completeness_20261121.py`（可重跑，只读）；
> 归档 `plan/back/已完成任务归档_20261121_第50轮_TASK143只读完整性与新鲜度核验.md`。
> **第 1–4 项已由机器证据全部证实**；**第 5 项（新鲜度）未测量** —— 重跑 review 是**写库**动作，
> **需单独授权**，本轮**未执行**，因此**不可**关闭本任务。

- [ ] 重新运行并验证 review 新鲜度在 **600 秒**内；报告完整/缺值/零/负/正数量和逐条修复目标。
      —— **未测量**：阈值 **600s**，fresh **0** / stale **44**，最旧 ≈ **1.649 天**（`reviewAgeSeconds=133873`），
      `productionState=review_stalled`、`reason=no_fresh_review_within_threshold`、`blockers=broadcast`。
      **剩余唯一动作是重跑主网 review（写库）**，属需单独授权的范围，本轮**未执行**；
      重跑后**必须重新核验**完整性与金额/利润事实（PnL 会重新测量），**不可**沿用本轮数字。

### TASK-145｜USDG 估值单位与覆盖（AC-212）
> **已完成部分（已归档，不再重复验收）**：五项中的 **4 项**（单位契约、ETH/WETH 方向与 USDG identity、review 原始字段对照、coverage gap 报告）已由机器证据证实。
> 证据 `plan/runtime_evidence/reports/task145_usdg_valuation_units_20261121.json`；归档 `plan/back/已完成任务归档_20261121_第51轮_TASK145估值单位与覆盖核验.md`。
> **诚实边界（仍然有效）**：USDG identity 在**已发布闭环**上已证得（36/36），但 **0/44** 被审 review 是 USDG-锚点（如实报告 `reviewAnchorCount=0`）；TZ 无 USDG 路径 → **fail-closed**（缺价格，**不是零**）。

> Manager 侧 USDG identity 估值与内核侧跨 tick 估值输入的发布已归档（第 39 轮）。以下只保留**尚未达成**的验收项。
>
> **第 51 轮只读核验已完成：五项中 1/2/3/5 已证实（下方勾选），第 4 项**未达成**（下方保持未勾）。**
> 证据：`plan/runtime_evidence/reports/task145_usdg_valuation_units_20261121.json`（脚本 `plan/runtime_evidence/tools/collect_task145_valuation_units_20261121.py`，**可重跑、只读**，退出码 **1** 属设计行为）；
> 归档：`back/已完成任务归档_20261121_第51轮_TASK145估值单位与覆盖核验.md`。

- [ ] 主网 88/88 覆盖在新鲜 Manager 状态下复核；测试网 Manager loops>0 后执行同一验证。**覆盖率不能替代真实 review 单位检查。**
      —— **未达成**：测试网 `manager_v4_closed_loops` = **0**（主网 164）→ `loopCount = 0`，
      `borrowToken.usdgValuation` **0 met / 0 notMet / 8 unmeasured**（无闭环可定价）。
      测试网 `token_library_catalog` **存在**（VIEW）且 USDG decimals=6 已 admitted → **锚点 identity 本可解析**，
      **阻塞项是 Manager loop 缺失**，即 `TASK-147` 的 `candidate_exchange` 域版本漂移（**需写库，需单独授权**）。
      主网侧亦**非新鲜状态**（`TASK-143` 的 600 秒新鲜度仍开放）。
      另：测试网 review 为 **legacy 格式**（`historicalEvidence: true`、`legacyReviewId`、无 `bestAmountCalculation`）
      → ladder / principal 判为 **unmeasured**（而非 notMet）。
      **下一步**：① `TASK-147` 重新钉版 + 重建观测 + 重跑 Manager（**需写库**）；
      ② loop > 0 后用**同一脚本**重跑并**重新核验**本项及 1/2/3/5（**不可沿用本轮数字**）。

### TASK-154｜原生 ETH 闪贷偿还（新增任务，不得使用 WETH 中转）

> **使用者指示（2026-11-21）**：“不要用 WETH 偿还 ETH。这是浪费。”
> 已写入开发计划 **§3.3.1**（绑定性架构规则）；协议分析见开发计划 **§7.12.10**。
> 本任务只负责把该规则落成**可独立核验的验收项**，不新增估值口径。

- [ ] 任何以 ETH 计值的闪贷借款，借出与偿还均使用**原生 ETH**（`address(0)`）；
      **不得**出现“借 ETH → wrap → 交易 → unwrap → 偿还”的包装/解包中转。
      —— **未实现**：本仓库不直接调用 PoolManager 的 `take`/`settle`，而是构造对**外部 V4 闪贷执行器合约**的请求
      （`build_v4_flash_executor_request`，`flashloan/rs-src/src/execution.rs` 约 529 行；`route_currency()` 约 517 行读 `currency0`/`currency1`）。
      执行器合约源码不在本仓库内（未找到 `.sol`），故**需先确认执行器的实际行为**，才能判定本项是否已满足。
- [ ] 执行器请求中的借款/偿还代币字段，当借款代币为原生 ETH 时为 **`address(0)`**（而非 WETH 地址 `0x0bd7...ad73`），
      且偿还不降低到需要 WETH 中转的交易体。
      —— **未核验**：需实盘请求里的代币字段可被独立核验时才能关闭。
      仓库侧事实：`manager_dex_candidate_pools` 中 **36/180** 个池配对 `address(0)`（原生 ETH），其余 144 个为 ERC-20；
      ETH = `token_kind='native'`、WETH = `token_kind='wrapped_native'`，各自 18 小数；两者 USDG 估值 **2461669444** vs **1804537685**（**不同估值**）。
- [ ] 对 `address(0)` 的任何 `transfer` / `approve` 调用均回滚；代码必须通过 `isAddressZero()` / `isNative` 分支走 `msg.value`，
      不得对 `address(0)` 做 ERC-20 调用。
      —— **未实现**：当前请求构建层以地址字段传递，未见分支语义。
- [ ] 原生偿还的协议顺序为 `sync(address(0))` → `settle{value: amount}(address(0))`（**`sync` 不可省略**：
      V4 `PoolManager._settle` 注释明确提示原生结算仍需先 `sync` 以避免 DoS）。
      —— **未核验**：需执行器合约源码可核。

> **边界**：本任务**不引入任何 WETH 包装/解包能力**。若路径某段本身就是 WETH 池，
> 应在**路径构建层**尽量避开（优先选原生 ETH 池），而不是在偿还阶段包一层封装。

---

## 2. P1 测试网级联和广播

### TASK-147｜测试网有效候选、池与 Manager 闭环（AC-211）
> **已完成部分（已归档，不再重复验收）**：端点与 chain ID 记录、token admission 与 overlap 身份、scope 的 raw/active/blocked/skip reason、**不回灌历史数据** 四项已完成只读采集。
> 证据 `plan/runtime_evidence/reports/task147_testnet_prerequisite_20261121.json`；归档 `plan/back/已完成任务归档_20261121_第48轮_TASK147测试网只读前置采集.md`。
> **诚实边界（仍然有效）**：`rpc.wss = []` 记为**不可用/未测量**，**不记为零**。

**剩余动作**（需写库，属需单独授权的范围）：

- [ ] 以当前链状态刷新 pool depth/tick，**不放宽固定门槛**。（需写库，**待授权**）
- [ ] 证明确有至少两个不同 admitted token 形成观察方向；要求 `direction_count>0`、Manager `loopCount>0`。
      —— **当前 `direction_count=0`、`loopCount=0`、`hopCount=0`，未达成**；根因已定位为
      `candidate_exchange` 域的库版本漂移（候选对本身存在且两端均已 admitted，但观测未产出 Y→Z 中间方向）。
- [ ] Manager loops/hops/optimizations、上游版本/checksum 全部闭合后才运行测试网 review。
      —— 上游版本与 checksum **已记录**；但 Manager 闭环表 **0 行**，**不满足闭合**，**未运行**测试网 review。

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

## 3. P2 生产稳定

### TASK-141｜双链准入和稳定窗口

- [ ] 测试网完整级联先通过；主网按相同代码逐层验证，DB 与链配置独立。
- [ ] 运行时 API 与 Manager API 健康状态**仅作为服务检查**，不替代业务闭环验收。
- [ ] 连续 **3 个各至少 30 分钟**窗口均满足 `blockers=[]`、`missingValue=0`、`positive>0`。
- [ ] 每个窗口关联复核、广播请求、交易 hash、receipt 和实际盈亏；记录 gas、借款费、滑点及模型偏差。
- [ ] stale WSS、版本漂移、重复提交、错误激增或实际亏损触发对应广播准入关闭，同时保留诊断/修复通道。

## 4. 总体停止条件

- [ ] 机器证据包含 TASK/AC、代码版本、chain ID、DB 绝对路径、schema/checksum、工具版本、时间范围和原始计数。
- [ ] Python 全量单测、Rust 内核、Manager workspace、架构守卫及相关回归在当前 `scripts/` 根目录布局下通过；**跳过和警告逐项说明**。
- [ ] 无历史深度/业务数据回灌；无用默认零或估值掩盖缺陷；**无未授权真实广播**。
- [ ] 缺 amount/PnL、陈旧快照、身份不唯一、签名授权不足、重复提交或 receipt 无法关联时**停止广播**。

## 5. 归档门槛

- 关闭任一任务前，先把当前机器证据与人工摘要存入 `back/`，再从活动清单移除该任务，并在此清单删除对应验收项。
- 归档记录须含：任务号与验收号、代码版本、证据文件路径、chain ID、DB 路径与 checksum、执行时间、结论与残留风险。
- **编号永久不复用**；归档后不得再以同一编号新开任务。
- 实现完成但缺端到端证据的任务**保持开放**，只归档其已验证子项，并在归档记录中写明未覆盖部分。
