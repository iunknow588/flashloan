# D-101 `T-证候储.01`｜`recent_swap_active` 全 NULL 的**真因**与接线裁决

> **本文档是 `T-证候储.01` 的技术文案** —— 记录**实测**根因、两条必须先裁决的边界、
> 以及本任务**真实代码落点**（与 `D-025` §1 原先登记的不一致，已订正）。
>
> **本文件是 `T-证候储.01` 的独立内容**，不引用其它任务的验收结论。

---

## 1. 现象复核（**只读实测**，2026-11-24）

| 表 | 行数 | `recent_swap_active` 为 NULL | 为 1 |
|---|---|---|---|
| `v4_exchange_pool_liquidity_snapshots` | **2486** | **2486** | **0** |
| `market_observation_liquidity_snapshots` | **4158** | **4158** | **0** |

> 裁定 `D-012` §6.5 的「2486 / 2486 全 NULL」**属实**。
> 生产库 `flashloan/db/local_store_4663.sqlite3`（8.01 GB）**本次全程只读**（`mode=ro` 打开）。

---

## 2. 根因（**不是缺扫描器 —— 是该列在结构上不可写**）

> **裁定 §6.5 的判断需要订正。**
> §6.5 写「需要**新增扫描器**」⇒ **与实测不符**：扫描器**早就存在**，且**确实算出了值**。
> 真正的原因是 **TASK-172 修复之后，写入通道被关闭，而唯一的写入方又不写这一列**。

### 2.1 完整链路（**逐段实测**）

```
[1] Python 扫描器（早已存在，且被调用）
    flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py
      :59   RECENT_SWAP_LOOKBACK_DAYS = 60
      :337  _mark_recent_swap_activity()              ← 旧版
      :419  _mark_recent_swap_activity_for_pool_ids() ← 新版（按 pool_id 批量）
      :1003 build_v4_pool_index_from_initialize_logs(..., mark_recent_swaps=True)
      :1090 mark_recent_swap_activity_for_pool_index()
    ⇒ 产出：pool index JSON 每行带 recentSwapActive = 1/0

[2] 调用方（早已存在，且默认打开）
    flashloan/src/dex/tools/refresh_robinhood_mainnet_v4_evidence.py:92
      write_v4_pool_index_from_initialize_logs(..., mark_recent_swaps=True, ...)
    flashloan/src/dex/tools/refresh_robinhood_v4_live.py:1764
      --mark-recent-swaps （CLI 开关，**默认 False**）

[3] 行装配（值在此仍然活着）
    flashloan/src/core/db/import_existing_json.py:290-291
      if raw.get("recentSwapActive") is not None:
          row["recent_swap_active"] = raw.get("recentSwapActive")
    ⇒ index_liquidity_rows 里带着 recent_swap_active

[4] ⛔ 断点 1 —— 证据通道**丢弃**整批 liquidity 行
    flashloan/rs-src/src/evidence.rs:843-844
      let liquidity_count = liquidity_rows.len();          ← 只算长度
      let index_liquidity_count = index_liquidity_rows.len();
      // …没有任何 INSERT / UPDATE 落到快照表…
    flashloan/rs-src/src/evidence.rs:868
      "liquidityRowsWritten": 0,                            ← 实测为 0
    ⇒ **TASK-172 / AC-221 的修复**：证据通道不再写深度快照表
       （理由：该表的唯一写入方是 Rust pool manager，它在一条语句里同时写
        价格列与单跳边界，一行只能描述**链上一个时刻**）

[5] ⛔ 断点 2 —— 唯一写入方**不写这一列**
    flashloan/tools/pool_manager/src/lib.rs:2624  write_depth_snapshot()
    flashloan/tools/pool_manager/src/lib.rs:2689  UPDATE … SET
        liquidity, has_liquidity, tick, sqrt_price_x96,
        capacity_*, price_impact_*, depth_basis,
        liquidity_read_at, refreshed_at, evidence_source,
        capacity_binding_raw, depth_band_json, balances_json,
        single_tick_max_token*, depth_status, estimated_tvl_usd, materialization_batch
      ⇒ **18+ 列，`recent_swap_active` 不在其中**

[6] ⛔ 断点 3 —— 该表**没有 INSERT 路径**
    flashloan/rs-src/src/evidence.rs:755-757（原文注释）
      "the pool manager only ever UPDATEs a snapshot row -- it never inserts one"
    ⇒ 该列**既不能被 UPDATE 写，也不能被 INSERT 写**
```

### 2.2 一句话结论

> **该列不是「没有人算」，而是「算出来了但没有落点」。**
> Python 侧算出的 `recent_swap_active` 在 `[4]` 被**整批丢弃**；
> 而唯一有权写快照表的 Rust pool manager `[5]` 的 UPDATE 语句**不含这一列**，
> 且它 `[6]` **从不 INSERT** 快照行 ⇒ **该列在当前架构下没有可达的写入路径**。

### 2.3 与 `TASK-172` 的关系（**同源**）

| 项 | `TASK-172`（已闭合） | `T-证候储.01`（本任务） |
|---|---|---|
| 争议对象 | **价格列**（`sqrt_price_x96` / `tick` / 边界） | **`recent_swap_active`** |
| 冲突成因 | 证据通道与 pool manager **各写一半** ⇒ 一行两个时刻 | 证据通道**整批丢弃**，pool manager **不写该列** |
| 收口方向 | **收口写入方**（证据通道不写价格列） | **收口写入方**（pool manager 补写该列） |

> ⚠️ **同一个根因的两种表现** —— `TASK-172` 的教训直接适用：
> **深度快照表是「链上某一时刻」的物化，只允许一个写入方。**
> 本任务**不重开 `TASK-172`**（不推翻「证据通道不写快照表」这一已闭合结论），
> 而是**让唯一的写入方把这一列补上**。

---

## 3. 两条必须**先裁决**的边界（**人工裁决点**）

### 3.1 口径冲突 A：**60 天** ≠ 裁定说的「**一周**」

| 来源 | 口径 |
|---|---|
| 裁定 `D-012` §6.5（:530） | 「最后 `Swap` 时间 / **一周内**交易次数」 |
| 裁定 `D-012` §6.5（:472 / :478） | 层 6「最近**一周**无交易 ⇒ 移出备用池」；层 7「查询最近**一周**有交易的池」 |
| 任务标题（`D-024` §1） | 「**最近一周**有交易」数据源 |
| **代码实测** | `v4_pool_index.py:59` `RECENT_SWAP_LOOKBACK_DAYS = 60` |
| CLI 实测 | `build_robinhood_mainnet_v4_pool_index.py` `--recent-swap-lookback-days` **默认 60** |

> **裁决（口径 A）**：**统一为「60 天」，「一周」是裁定的表述笔误。**
>
> **理由**：
> 1. **代码已落地且被生产路径调用** —— `refresh_robinhood_mainnet_v4_evidence.py:92`
>    走的就是 `RECENT_SWAP_LOOKBACK_DAYS = 60`；改成 7 天会让**已产出的证据全部作废**。
> 2. **消费侧对窗口长度不敏感** —— `direction_integrity.py:241-262` 的
>    `_pool_has_liquidity()` 只读 `recent_swap_active == 1` 作**存在性**判据，
>    **不依赖窗口长度**；层 6 / 层 7 的判据是「有没有交易」，不是「多少笔」。
> 3. **60 天是链上真实分布** —— 池子换手周期远长于 7 天；7 天窗口在冷池上
>    会把绝大多数池判成「无交易」，反而让层 6/层 7 失去区分度。
>
> ⇒ **本次不改 `RECENT_SWAP_LOOKBACK_DAYS`**；口径写进本任务 docs，
> **可复算**：`窗口 = [latest_block − 60×86 400s 对应区块, latest_block]`，
> 二分查找定位起始区块（`v4_pool_index.py:453` `_binary_search_block_for_timestamp`）。

### 3.2 口径冲突 B：填列 **≠** 接入版本触发器

`flashloan/src/core/db/pool_state_version.py:25-27` 原文：

> * **No ``recent_swap_active``.** Every ``recent_swap_active`` value in the
>   production snapshot table is ``NULL`` (4669/4669), so using it would make
>   the trigger permanently silent.

> **裁决（口径 B）**：**填充 `recent_swap_active` ≠ 把它接入 `pool_state_version`。**
>
> `pool_state_version.py:25-27` 的「故意不用」**理由**是「值为 NULL ⇒ 触发器永远静默」。
> 本任务把值填上之后，**那条理由就不再成立** —— 但**结论仍然成立**，因为
> ARCH-1 的版本指纹**定义**就是 `(liquidity_block_number, sqrt_price_x96)` 这一对，
> **交易活跃度不是链上状态**，用它当版本会把触发器变回「按时间抖动的定时器」。
>
> ⇒ **硬边界**：
> 1. 本任务**只负责把值写进 `recent_swap_active` 列**。
> 2. **不得**修改 `pool_state_version.py` 的指纹构成。
> 3. **不得**把 `recent_swap_active` 接入任何「版本 / 变更 / 触发重算」的判定。
> 4. **不得**据此新增定时任务。
> ⇒ 该边界已登记进 `D-025` §3「禁止触碰」。

---

## 4. 真实代码落点（**订正 `D-025` §1 原登记**）

> `D-025` §1 原先登记的落点：
> `flashloan/tools/candidate_edge_pool_manager/`（M-5）＋
> `flashloan/src/core/db/storage_business_schema.py`（M-10）
> —— **与 §2 的根因链路不符**（既不在扫描器上，也不在写入方上）。

### 4.1 实际需要改的文件

| 文件 | 角色 | 模块 |
|---|---|---|
| `flashloan/tools/pool_manager/src/lib.rs` | **写入侧**：`write_depth_snapshot()` 的 UPDATE 语句补一列 | `M-10`（存储层） |
| `flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py` | **口径参数**：常量与默认窗口 | `M-5`（候选层） |
| `flashloan/src/core/db/storage_business_schema.py` | **列语义确认**（列已存在，**不改 DDL**） | `M-10` |

> `v4_pool_index.py` 的口径**实际不改**（见 §3.1 裁决 A），但它是口径的**唯一真相源**，
> **必须纳入范围**，否则「口径可复算」这一验收项**无处可查**。

### 4.2 需要**新写**的部分（**落点尚不存在**）

> 「把池子最近是否交易过」**喂给 Rust pool manager** 的通道 —— **当前不存在**。
> `write_depth_snapshot()` 的入参是 `chain::V4PoolState`（链上状态），
> **不含交易活跃度** ⇒ 必须**新增一条**把它带进来的通道。

| 候选落点 | 方案 | 评价 |
|---|---|---|
| **A** | `v4_pool_index.py` 把 `recentSwapActive` 落到**独立的 pool index 表/列**，pool manager 读它 | ✅ **推荐**：不污染深度快照表的「单时刻」语义；pool manager 只**读**不**写**活跃度 |
| B | pool manager 自己扫 `Swap` 日志 | ⛔ 把链上扫描塞进写入方 ⇒ 与「写入方只物化」相悖 |
| C | 恢复证据通道写 `recent_swap_active` 单列 | ⛔ **违反 `TASK-172` 已闭合的「快照表唯一写入方」结论** |

> ⇒ **采用方案 A**。方案 A 的具体接线在 `D-025` §1 修订后由 PLAN 落地。

---

## 5. 剩余的「口径」歧义（**已由本次裁决钉死**）

裁定 §6.5 提了两个量：「**最后 `Swap` 时间**」与「**一周内交易次数**」。

| 量 | 本次落地形态 | 说明 |
|---|---|---|
| **一周内交易次数** | ⛔ **不做**（不新增列） | 裁定 §6.5 允许「写入 `recent_swap_active`（列已存在，**无需 DDL**）**或**新建独立表」。本任务**只做布尔量**，次数留待真需要时再立项 |
| **最后 `Swap` 时间** | ⏸ **不在本任务范围** | 现有 schema **无对应列**；新增列 = 改表结构 ⇒ 与「列已存在，无需 DDL」相悖。列为**后续独立任务**候选 |

> ⇒ 本任务的可交付口径**唯一**：`recent_swap_active ∈ {0, 1, NULL}`，
> 含义 =「**最近 60 天内该池有 `Swap` 事件**」。
> `NULL` = **未测量**（不是「无交易」）—— `as_tri`（`business_builder.rs:107-111`）
> 对 `None` 写 `Value::Null`，对 `Some(0)` 写 `0`，**三者可区分** ⇒ 语义安全。

---

## 6. 验收判据的**可复算**形式

| 判据 | 复算方式 |
|---|---|
| 「最近 60 天」 | `[latest_block − 60×86 400s 的区块, latest_block]`，起点由 `_binary_search_block_for_timestamp` 二分定位 |
| 「有交易」 | 该区间内存在 `topic = V4_SWAP_TOPIC` 且 `poolId ∈ {目标池}` 的日志 |
| 「无交易」 | 该区间内无此类日志 ⇒ `recent_swap_active = 0` |
| 「未测量」 | 该池未参与本轮扫描 ⇒ `recent_swap_active` 保持 `NULL`（**不得**被写成 0） |

> ⚠️ **`NULL` 与 `0` 必须区分** —— `NULL` = 没量过；`0` = 量过且没有交易。
> 写成 0 会让「未覆盖的池」被误判成「无交易的池」⇒ 层 6 会把没量过的池移出备用池。

---

## 7. 与消费侧（层 6 / 层 7）的关系（**只读确认**）

`flashloan/src/core/db/direction_integrity.py:241-262`：

```python
tri    = _tri_state(pool.get("has_liquidity"))
active = _tri_state(pool.get("recent_swap_active"))
if tri == 1:    return True, "has_liquidity"
if active == 1: return True, "recent_swap_active"
```

> ⇒ `recent_swap_active = 1` 是**流动性的独立证据**（与 `has_liquidity` 并列的第二判据）。
> 本任务**只提供数据**，**不改**该判据、**不改**准入 `L1`、**不改**排序主序 `L2`、
> **不广播**、**不放宽任何门禁**（对应 `D-026` i.2 后三项）。

---

## 8. 本文档带来的**三条结论**

1. **根因订正** —— 不是「缺扫描器」，而是「**算出来了但没有落点**」。
   扫描器、调用方、行装配**全部早已存在且在生产路径上**；断点在**写入侧**。
2. **口径钉死** —— 「60 天」为准（§3.1）；「一周」为裁定表述笔误。
   「次数」与「最后时间」**不在本任务**（§5）。
3. **边界钉死** —— 填列 **≠** 接入版本触发器（§3.2）；
   **不得**重开 `TASK-172` 的「快照表唯一写入方」结论（§2.3）。

---

## 9. 后续（**施工后实测**）

本文档记录的是**裁决与根因**；**施工结果与实测读数**见
`D-102-接线实测读数与主网受阻登记_20261124.md`（同目录）。

| 项 | 结论 |
|---|---|
| **接线是否打通** | ✅ **已打通**（测试网端到端：扫描 → 标记 → 独立活动表 → 快照表**零写入**） |
| **§4.1 落点表是否准确** | **已订正** —— `D-025` §1 的文件级落点以 `D-025` §1.1 与 §1 为准；<br>`rs-src`（证据层 `M-2`）与 `storage_v4_exchange.py` / `import_v4_evidence.py` / `import_existing_json.py` / `v4_rust_refresh.py` **也是实际落点** |
| **§4.2 方案 A 是否成立** | ✅ **成立**，但**须补一条写入方约束**：<br>活动表**只发布池库内的池子标记**（`D-102` §3.2） |
| **主网读数** | ⏸ **受阻于外部配额**（非代码缺陷），已如实登记（`D-102` §4） |
