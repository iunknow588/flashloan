# P0-5｜VAL 估值遗留决策（USDG 估值快照 TTL / 过期语义 / no-route 近似边界）

- 日期：2026-11-14
- 责任 manager：`token_manager`
- 机器证据：`runtime_evidence/reports/p0_5_valuation_expiry_policy_20261114.json`
- 长期手册口径：`docs/主网自愈编排器/05_数据口径与统计.md` §11
- 生产写入：**0**；权限切换 **0**；主网广播 **0**；RPC 请求 **0**

---

## 1. 本次决策要回答的三个问题

P0-5 在计划里只有三行，但每一行都指向一个**已经存在于代码中、却没有被写下来的判断**：

1. USDG 估值快照的 **TTL** 是多少？过期后怎么算？
2. 过期 / 时间戳不可信 / 未来时间戳的 **fail-closed 边界**在哪里？
3. 要不要做**不依赖外部 API 的本地 no-route 近似**？

第 1、2 两个问题的答案原本"藏在代码里"——**其实连代码里也没有**。见 §2。

---

## 2. 缺口分析：TTL 从来没有被读过

### 2.1 已有的部分

- `token_usdg_valuations` 表（迁移 `20261112_007_usdg_valuation_schema`）**确实**存了 `measured_at`。
- `valuation_store.rs` 落库时**确实**区分四态判定：
  `route_found` / `no_route` / `api_unavailable` / `api_error`。
- 落库时**确实**拒绝用一个 unknown 观察覆盖已有的 known 观察
  （`valuation_store::land_valuation_report` 的 `protected` 计数）。

### 2.2 缺失的部分（本次发现的真实缺口）

**没有任何代码读取 `measured_at` 来判断一个观察是否还能用。**

证据链：

| 环节 | 现状 |
|---|---|
| 写入 `measured_at` | ✅ `valuation_store.rs` 落库时写入 |
| 读出 `measured_at` | ✅ `read_stored_valuations` 读出并放进 `TokenValuation.observed_at` |
| **判定是否过期** | ❌ **无** |
| **过期后如何影响成员关系** | ❌ **无** |
| **过期后如何影响承载能力分档** | ❌ **无** |

`usdg_economic.rs::assess_token` 只看三件事：
`valuation_attempted`（测过没有）、`usdg_value_raw`（值多少）、`pool_depth_usdg_raw`。
**它完全不看时间。**

### 2.3 这是一个潜在的 fail-open

后果有两类，方向相反，但都来自同一个根因：

| 失效形态 | 具体表现 | 严重性 |
|---|---|---|
| **过期负面被永久保留** | 一条几个月前测到的 `no_route` 让一个股票代币**永远**留在"已排除"状态，即使路由早已出现 | 高——成员关系永久性错误，且没有任何机制能自动纠正 |
| **过期正面被永久保留** | 一条几个月前测到的 `route_found` 让一个代币以早已不存在的估值留在股票库里，并参与承载能力分档 | 中——经济量失真，但方向是"保留"而非"误删" |

**根因不是"TTL 设得太长"，而是"根本没有 TTL"。**

---

## 3. 口径定案

### 3.1 双档 TTL

关键判断：**两个消费者对同一个估值观察的容忍度完全不同**，所以必须两档，不能一档了事。

| 用途 | 消费方 | 问题 | TTL | 理由 |
|---|---|---|---|---|
| **成员关系**（membership） | 股票代币库准入 / 撤回 | "这个代币到底有没有 USDG 路径？" | **24 小时** | 这是该代币在场所路由图中位置的**粗粒度事实**，变化慢。判错的代价温和：最多滞留一天 |
| **数值幅度**（magnitude） | 承载能力分档（`carryable` / `dust` / `unmeasured`） | "一个单位现在值多少 USDG？" | **1 小时** | 这是**市场观察值**，直接参与"这个池子能不能承载最小经济头寸"的判定。用周前的价格会把池子留在一个它早已不属于的档位 |

> 两个常量以代码形式固化在 `token_manager::valuation_freshness`：
> `MEMBERSHIP_MAX_AGE = 86400s`、`MAGNITUDE_MAX_AGE = 3600s`。

### 3.2 过期语义：过期 = 未知，不是负面

这是本次决策**最关键**的一条。它与手册 §10.5 的"未知绝不覆盖已知"同源，
但补上了之前缺失的另一半：**已知也不许被过期事实覆盖**。

```
新鲜观察   → 保留原判定（正 / 负 / 未知）
过期观察   → 未知（不管它原来说了什么）
不可验证   → 未知
```

具体到四种情况：

| 存储的判定 | 观察状态 | **生效判定** | 说明 |
|---|---|---|---|
| `route_found` | 新鲜 | `positive` | 可用 |
| `no_route` | 新鲜 | `negative` | 真负面 |
| `route_found` | **过期** | `unknown_expired` | **不再是正面** |
| `no_route` | **过期** | `unknown_expired` | **不再是负面** |
| `api_error` / `api_unavailable` | 任意 | `unknown_probe` | 探针本身就没问出结果 |

**"过期的 `no_route` 不再是负面"这一条是整个决策的支点。**
它保证了过期观察**永远无法**把一个代币从库里踢出去——只能让它回到 `pending`，
由下一次刷新重新裁决。反过来，过期的 `route_found` 也不再保住成员关系，
因为一个周前的估值不足以支撑今天的经济判定。

**过期不等于删除。** 过期行仍留在 `token_usdg_valuations` 表里，
过期是**读取规则**，不是清理任务：审计线索必须完整，
而且下一次刷新可以直接覆盖它（落库本身是幂等的 upsert）。

### 3.3 fail-closed 边界

所有歧义一律解析为**未知**，没有任何一条解析为负面：

| 情形 | 生效判定 | 理由 |
|---|---|---|
| `measured_at` 缺失 | `unknown_expired`（unverifiable） | 没有时间戳就**无法证明**它新鲜 |
| `measured_at` 无法解析 | `unknown_expired`（unverifiable） | 同上 |
| `measured_at` 在未来 | `unknown_expired`（stale） | 未来时间戳是时钟漂移或写入错误，不是"更新鲜" |
| 观察超过其容忍度 | `unknown_expired`（stale） | 超出容忍度即过期 |
| 存储判定本身是 unknown | `unknown_probe` | 探针失败从来不是负面 |

> **时间戳解析为什么自己写而不交给 SQLite**：
> `datetime()` 会把 RFC-3339 改写成空格分隔形式并截断小数部分，
> 调用方还得再解析一遍。`valuation_freshness.rs` 自带 RFC-3339 解析器
> （`Z` / `±HH:MM` / `±HHMM` / 小数秒全部接受），并且**不做浮点运算**。

### 3.4 本地 no-route 近似：**不采用**

**决定：不采用。** 而且这个决定被写进了代码，不只是写进文档。

理由：

1. **近似与真负是两个不同的命题。**
   本地启发式最多只能得出"这个代币*大概*没有路径"，
   而真负是"这个代币*确实*没有路径"。
   只有后者能作为排除一个代币的依据。

2. **真负在当前体系里只有一个生产者：上游服务明说。**
   四态判定词表（`route_found` / `no_route` / `api_unavailable` / `api_error`）
   的设计目的，就是把"服务明说没有路径"与"我们自己没问出来"分开。
   引入本地近似会把这个唯一的、明确的语义边界重新搅浑。

3. **不依赖外部 API 的动机不成立。**
   真正的问题不是"API 不可用"，而是"API 不可用时我们**不知道**"。
   近似恰好把"不知道"伪装成"知道"，正好破坏 fail-closed。

**如果将来要引入近似，它必须：**
- 拥有自己的判定值，且该判定值被归类为**未知**；
- 只能出现在面向操作员的报告里作为**筛查提示**；
- **永远不能**写入 `token_usdg_valuations` 的 `no_route`；
- **永远不能**改变任何成员关系。

这四条目前由代码强制：

```rust
// token_manager::valuation_freshness
pub enum ValuationApproximation { Rejected }

pub fn approximation_may_promote_to_negative(_kind: &str) -> bool { false }
```

`ValuationApproximation::Rejected` 是默认值，
`approximation_may_promote_to_negative` 对任何输入都返回 `false`，
并由单测钉死。

### 3.5 不改变的边界（必须同时确认）

本次决策**没有**放松任何既有裁决：

- **USDG 估值是价值/经济判断，不得作为深度或容量信号。**
  R-02 / R-03 / R-04 继续成立，深度仍然是链上 tick/liquidity。
  1 小时 magnitude TTL 只约束"估值本身还能不能用"，不改变"估值不是深度信号"。
- **估值不参与广播金额定档。**
  `depth_policy.json` 的 `usdgPegPolicy.forbiddenUses` 未改动。
- **本阶段不写生产库、不迁移生产库、不切换 Authority。**

---

## 4. 落地实现

### 4.1 新增模块 `flashloan/tools/token_manager/src/valuation_freshness.rs`

| 公开项 | 作用 |
|---|---|
| `MEMBERSHIP_MAX_AGE` / `MAGNITUDE_MAX_AGE` | 双档 TTL 常量 |
| `ValuationFreshness` | `Fresh` / `Stale` / `UnverifiableTimestamp` |
| `ValuationUse` | `Membership` / `Magnitude`，决定用哪档容忍度 |
| `evaluate_freshness` / `evaluate_freshness_now` | 单条观察的新鲜度判定 |
| `effective_verdict` | 存储判定 + 新鲜度 → **生效判定**（负 / 正 / 未知） |
| `EffectiveVerdict` | `Positive` / `Negative` / `UnknownExpired` / `UnknownProbe` |
| `evaluate_stored_valuations` | 整表过期审计 → `ValuationExpiryReport` |
| `ValuationApproximation` / `approximation_may_promote_to_negative` | no-route 近似边界的代码强制 |

### 4.2 接入 `valuation_store.rs`

新增 `audit_valuation_expiry(database, network, chain_id)`：
**只读**打开数据库，扫 `token_usdg_valuations`，产出 `ValuationExpiryReport`。
表不存在时返回空报告（"从未创建估值存储"不是错误）。

### 4.3 新增 CLI `token-manager audit-valuation-expiry`

```bash
token-manager audit-valuation-expiry --database <manager.sqlite>
```

只读，输出机器可读的过期审计报告。

### 4.4 新增探针 `flashloan/tools/token_manager/examples/p0_5_valuation_expiry_probe.rs`

在**临时目录的副本库**上种入 7 条刻意混合的观察，一次性覆盖所有边界：

| 代币 | 存储判定 | 年龄 | membership | magnitude |
|---|---|---|---|---|
| `…0001` | `route_found` | 60 s | fresh | fresh |
| `…0002` | `no_route` | 120 s | fresh | fresh |
| `…0003` | `route_found` | 2 h | **fresh** | **stale** |
| `…0004` | `no_route` | 2 d | **stale** | **stale** |
| `…0005` | `api_error` | 30 s | unknown | unknown |
| `…0006` | `route_found` | 不可解析 | unverifiable | unverifiable |
| `…0007` | `route_found` | 未来 600 s | stale | stale |

实测结果（`rowCount=7`）：

| 指标 | membership | magnitude |
|---|---|---|
| fresh | **4** | **3** |
| stale | **2** | **3** |
| unverifiable | **1** | **1** |

判定分类：`positive=4`、`negative=2`、`unknown=1`。

> 注意 `…0003`（2 小时）与 `…0004`（2 天）：前者 membership 新鲜、magnitude 过期；
> 后者两档都过期。**两档 TTL 因此是可区分的，而不是同一个阈值的两种叫法。**
> 另外 `…0004` 原本是一条 `no_route`，过期后**不再计入负面**——
> 这正是 §3.2 支点规则的实测体现。

---

## 5. 顺带核实的既有缺陷：`VAL-4-13` 已经修好了

README 把 `VAL-4-13` 记为"必须先修再跑 194 只全量"的 P1 缺陷。本次审计确认：

**该修复已经在代码里，并且有回归测试钉住。**

- `valuation.rs::execute_quote_request` 在分类 HTTP 状态码**之前**先读非成功响应体，
  命中 `no_route_detail_from_body` 就直接返回 `QuoteOutcome::NoRoute`；
- `no_route_detail_from_body` 与 `explicit_no_route_detail` 共用**同一套**关键词集合，
  因此两条路径不可能漂移；
- `valuation_tests.rs` 覆盖了 404 带 `NoRouteFoundError`、裸 `errorCode`、
  404 但无 no-route 信号、200 带 no-route、429、401 六种情形。

**结论：194 只全量不再被 `VAL-4-13` 阻塞。** README 中该条需要改写为"已修复"。

---

## 6. 生产库现状（只读观测）

| 观测项 | 值 |
|---|---|
| 生产库 | `flashloan/db/local_store_4663.sqlite3`（3.5 GB） |
| `token_usdg_valuations` 表 | **不存在**（未应用迁移 `20261112_007_usdg_valuation_schema`） |
| `token_stocks` 行数 | 194 |
| `admission_status = admitted` | **194** |
| `admission_status = blocked` | **0** |

**重要事实：VAL-4-7 的 R-12 撤回修复只在副本库上验证过，生产库仍是 194 全 admitted。**
也就是说生产环境当前**还没有**任何 USDG 估值证据在生效，
成员关系完全由 2026-11-09 的初始快照决定。

这不影响本次决策本身（决策是口径与代码），但它决定了后续阶段的顺序：

> 生产库迁移 `20261112_007` → 落估值 → 重新物化库 → 才能看到 TTL 真正起作用。
> 该链路属于 **P1 范畴，需要显式授权**，不在 P0-5 内。

---

## 7. 回归

| 命令 | 结果 |
|---|---|
| `cargo test --locked -p token-manager valuation_freshness` | **12 passed / 0 failed**（全部新增） |
| `cargo test --locked -p token-manager` | **131 passed / 0 failed**（基线 119，+12） |
| `cargo test --locked --workspace` | **451 passed / 0 failed** |
| `cargo run --locked -p token-manager --example p0_5_valuation_expiry_probe` | 正常产出报告 |

新增单测（12 条）：

1. `a_fresh_membership_observation_is_usable`
2. `membership_survives_for_a_full_day_and_expires_after`
3. `magnitude_expires_far_sooner_than_membership`
4. `an_unparseable_timestamp_is_unverifiable_and_never_fresh`
5. `a_future_timestamp_is_stale_not_fresh`
6. `rfc3339_forms_are_all_parsed`
7. `a_stale_negative_never_remains_a_negative` ← **支点规则**
8. `a_stale_positive_never_remains_a_positive`
9. `a_fresh_negative_is_a_real_negative`
10. `a_fresh_probe_failure_stays_unknown`
11. `the_table_report_separates_fresh_stale_and_unverifiable`
12. `the_local_approximation_is_rejected` ← **近似边界**

---

## 8. 影响面与非目标

### 8.1 影响面

- `token_manager` 新增一个模块与一个只读审计命令；**既有判定路径的逻辑未改变**，
  只是给"这条观察还能不能用"补上了答案。
- 手册新增 §11，把口径固化到长期说明。
- README 中 `VAL-4-13` 的状态从"未修的 P1 缺陷"更正为"已修复"。

### 8.2 非目标（本阶段明确不做）

- 不迁移生产库、不应用 `20261112_007`。
- 不落任何估值、不撤回任何股票代币成员关系。
- 不实现本地 no-route 近似（决定不采用）。
- 不改 `depth_policy.json`、不改 peg 策略、不改深度/容量口径。
- 不改 `token_usdg_valuations` 表结构（`measured_at` 已存在，够用）。
- 不做 TTL 清理任务（过期是读取规则）。

---

## 9. 遗留项

| # | 遗留 | 归属阶段 |
|---|---|---|
| 1 | 生产库迁移 `20261112_007_usdg_valuation_schema` | P1（需授权） |
| 2 | 生产库落 194 只代币估值并重新物化库（让 TTL 真正生效） | P1（需授权） |
| 3 | 估值刷新周期编排：需保证 membership 刷新间隔 < 24 h、magnitude 刷新间隔 < 1 h | P1 |
| 4 | `scripts/refresh_robinhood_token_libraries.py` 增加按 TTL 触发的刷新节奏与过期报告输出 | P1 |
| 5 | 承载能力分档工具（`band_robinhood_candidate_usdg_capacity.py`）读取估值时接入 magnitude TTL | P1 |
| 6 | 若将来确需本地近似，必须先走"未知判定值"改造，不得复用 `no_route` | 非目标 |

---

## 10. 复现命令

```bash
cd flashloan/tools

# 单元测试
cargo test --locked -p token-manager valuation_freshness

# 全量回归
cargo test --locked --workspace

# 过期审计探针（临时副本库）
cargo run --locked -p token-manager --example p0_5_valuation_expiry_probe

# 对任意库做只读过期审计
cargo run --locked -p token-manager -- audit-valuation-expiry \
  --database <manager.sqlite>
```

---

## 11. 一句话结论

**TTL 定为双档（成员关系 24 小时、数值幅度 1 小时）；任何过期、不可解析或来自未来的观察一律降级为"未知"，绝不转成负面也绝不转成正面；本地 no-route 近似不采用，并由代码强制；口径已写入长期手册 §11。**
