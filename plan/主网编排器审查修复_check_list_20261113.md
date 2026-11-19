# 主网自愈编排器审查修复 Checklist v10.17（2026-11-20）

> 仅包含开放验收条件。全部保持 `[ ]`；完成时先保存当前机器证据与人工摘要，再把对应任务和验收项归档至 `back/`。

## 0. 约束

- 活动任务：`TASK-141..TASK-148`；验收编号：既有 `AC-211..AC-212`，新增 `AC-213`。编号不复用。
- Rust 内存内核是金额、容量、利润和闭环身份的权威。Python 仅消费和转发。
- 链库、chain ID、RPC/WSS、权限和运行状态互相隔离。
- 测得 0 与未测量必须显式区分；不完整、陈旧或未知一律 fail-closed。
- 根目录 `scripts/` 为仓库级工具入口；不移动到 `plan/`。计划目录仅承载计划、证据、checklist 和归档。

## 1. P0 金额与复核

### TASK-148｜单 tick 容量不可用根因

- [ ] **AC-213** 用最新主网复核复现 `single_tick_capacity_unavailable` 样本；保存闭环身份、pool ID、状态版本和 Rust 原始诊断。
- [ ] 定位责任层是实时池状态、pool-manager 容量投影、Rust registry 还是 review 输入；修复责任层，不在 healer/Python 填默认值。
- [ ] 对容量缺失、容量为零和正容量分别测试；缺失不能伪装为 0，容量 0 必须有真实测量和原因。
- [ ] 在同一当前内存快照下，单 tick 起始量、跨 tick 搜索上限和最终 best amount 证据可追踪且单位正确。
- [ ] 内核及 pool-manager/review-manager 定向测试通过，并在真实 schema/状态 fixture 上验证。

### TASK-142｜单 tick 初始量到跨 tick 搜索

- [ ] 非零单 tick 初值作为既有 double/halve ladder 起点；无固定 `1`、无递归、无 Python 金额计算。
- [ ] 单 tick 初值为 0 时跨 tick 调用数为 0；只有确实测量后才发布 `bestAmount=0`、`profit=0`、`measured=true`。
- [ ] 跨 tick 基于闭环真实输入/输出，不做 tick 遍历模拟；深度容量仅用于搜索上限。
- [ ] 主网和测试网使用相同 Rust 计算代码，并分别提供独立链状态证据。
- [ ] 保存 Rust 原始 ladder 输出、选中金额、利润和状态快照版本。

### TASK-143｜Review 金额与利润完整性

- [ ] 每条 active review 含 Rust 确认的 best amount raw、测量标记、有符号 PnL raw、币种/精度、来源、路线身份、快照版本和 review 时间。
- [ ] 修复主网所有 `best_amount_missing` 与 `data_incomplete`；完整 review 中缺值为 0。`not_executable` 保留真实原因，不伪造利润。
- [ ] 已测零利润被计为有效零；未执行 ladder 或未收到 Rust 结果仍是缺值。
- [ ] Manager/legacy 同路线归并唯一且保留明确金额/利润事实；Manager hop 表缺失不致崩溃。
- [ ] 重新运行并验证 review 新鲜度在 600 秒内；报告完整/缺值/零/负/正数量和逐条修复目标。

### TASK-145｜USDG 估值单位与覆盖

- [ ] **AC-212** USDG identity、ETH/WETH 路径、token decimals、借款本金、fee、gas 和 signed PnL 的 raw/human 单位契约经 Rust 单测与真实 review 输出共同验证；缺价格时 fail-closed，不以默认零或估值补值。
- [ ] `token_library_catalog` table/view 两种契约读取和 decimals 测试通过。
- [ ] ETH/WETH 估值方向与 token decimals 正确；USDG anchor 使用稳定币原始单位 identity，不依赖 USDG/USDG 池。
- [ ] review 的借款本金、fee、gas、signed PnL 使用可审计一致单位；保存实际 review 原始字段对照。
- [ ] 主网 88/88 覆盖在新鲜 Manager 状态下复核；测试网 Manager loops>0 后执行同一验证。覆盖率不能替代真实 review 单位检查。
- [ ] 其他无可用价格/精度的代币明确报告 coverage gap，不使用零汇率或默认汇率。

## 2. P1 测试网级联和广播

### TASK-147｜测试网有效候选、池与 Manager 闭环

- [ ] 记录 RPC/WSS endpoint 解析、chain ID 与只读请求状态；失败时禁止 apply。
- [ ] 每个 token 报告 primary library 与 extension overlap 身份；AMZN overlap 不重复准入或手改状态。
- [ ] 每个 scope 报告 raw/active/blocked/skip reason；以当前链状态刷新 pool depth/tick，不放宽固定门槛。
- [ ] 证明确有至少两个不同 admitted token 形成观察方向；要求 `direction_count>0`、Manager `loopCount>0`。
- [ ] Manager loops/hops/optimizations、上游版本/checksum 全部闭合后才运行测试网 review。
- [ ] 不从历史备份或 legacy 表回灌闭环、深度或权限数据。

### TASK-146｜级联治理与自动放行

- [ ] token 缺关键字段先交 token-manager；只有修复失败才允许删除，并同步清理依赖。
- [ ] pool 缺陷指向 pool-manager；闭环缺边指向 closed-loop-manager；金额/利润缺失指向 review-manager/Rust 内核。
- [ ] 迁移 011..013 按两条链核验；每条链目标 DB/schema/checksum 与备份可读性有独立证据。
- [ ] 所有修复具幂等键与原因/影响/回滚点；失败不丢候选、不跨链写入。
- [ ] 当前层依赖和回归通过后自动解除对应限制；不得永久关闭生产库或越层放行。

### TASK-144｜广播门禁与错误修复

- [ ] 主网复核新鲜、完整后执行 dry-run，核对 chain、账户、nonce、gas、目标合约、金额、快照、幂等键。
- [ ] `profit<=0`、利润或金额未知、状态陈旧、权限不匹配均不能进入真实广播队列。
- [ ] 测试网完成广播错误分类、有限重试、幂等、hash/receipt 和 Manager 修复路径演练。
- [ ] 真实广播须有独立、明确的链、账户、限额和时间窗口授权；没有授权不发送。
- [ ] 交易记录、复核证据和链上回执可审计关联；失败不描述为已成交或盈利。

## 3. P2 生产稳定

### TASK-141｜双链准入和稳定窗口

- [ ] 测试网完整级联先通过；主网按相同代码逐层验证，DB 与链配置独立。
- [ ] 运行时 API 与 Manager API 健康状态仅作为服务检查，不替代业务闭环验收。
- [ ] 连续 3 个各至少 30 分钟窗口均满足 `blockers=[]`、`missingValue=0`、`positive>0`。
- [ ] 每个窗口关联复核、广播请求、交易 hash、receipt 和实际盈亏；记录 gas、借款费、滑点及模型偏差。
- [ ] stale WSS、版本漂移、重复提交、错误激增或实际亏损触发对应广播准入关闭，同时保留诊断/修复通道。

## 4. 总体停止条件

- [ ] 机器证据包含 TASK/AC、代码版本、chain ID、DB 绝对路径、schema/checksum、工具版本、时间范围和原始计数。
- [ ] Python 全量单测、Rust 内核、Manager workspace、架构守卫及相关回归在当前 `scripts/` 根目录布局下通过；跳过和警告逐项说明。
- [ ] 无历史深度/业务数据回灌；无用默认零或估值掩盖缺陷；无未授权真实广播。
- [ ] 缺 amount/PnL、陈旧快照、身份不唯一、签名授权不足、重复提交或 receipt 无法关联时停止广播。
