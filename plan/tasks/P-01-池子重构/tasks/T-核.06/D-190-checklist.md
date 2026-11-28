# D-190 `T-核.06`｜流动性维护槽的价格源依赖 · 验收清单

> **【归并 · 2026-11-28】** 本清单的 **1** 项剩余（实盘：广播记录**出现**）已于 **2026-11-28** **归并**到 `T-广储.01`。
> **执行落点**：`T-广储.01`（见 [`D-261`](../../docs/D-261-剩余未完成项归属与开发计划_20261128.md) §1 的**逐条归属表**）。
> **本清单的框**在本任务达成前**保持未勾选** ⇒ **读数不重复计**（每个框只在一个文件里）。
> **【归并 · 2026-11-28】** 本清单的 **1** 项剩余（实盘：广播记录**出现**）已于 **2026-11-28** **归并**到 `T-广储.01`。
> **执行落点**：`T-广储.01`（见 [`D-261`](../../docs/D-261-剩余未完成项归属与开发计划_20261128.md) §1 的**逐条归属表**）。
> **本清单的框**在本任务达成前**保持未勾选** ⇒ **读数不重复计**（每个框只在一个文件里）。
> **本文件是 `T-核.06` 的全部验收框。** 编号 `AC-核.06` 属**纯 ID**。
> 每条勾选**必须**有实测读数支撑；**不得**凭推断勾选；**不得**用零冒充未测量。

**验收前置**：`M-13` 现有单测全绿

**本轮状态（2026-11-26）**：代码项**全部**闭合；**实盘读数**已取（见 §4、§5）。

---

## 1. 根因与定位

- [x] 复测确认**链上 RPC 已恢复**（活 2 / 5，且活着的都快）

      > **读数**（`eth_blockNumber`，2026-11-26 09:36）：
      > publicnode **200 / 1280 ms**、QuickNode **200 / 797 ms**、
      > `chain.robinhood` **403**、ordofi **DNS 死**、Alchemy **429**
      > ⇒ **活 2 / 5**。

- [x] 复测确认**外部价格源仍不可达**（活 1 / 12）

      > **读数**（`probe_price_sources_20261126.py`，2026-11-26 10:00，并发 12 源）：
      > **只有 `api.gateio.ws` 200 / 2030 ms**；
      > binance.us **16077 ms 超时**、binance.com **16157 ms 超时**、
      > kraken **16077 ms 超时**、coingecko **16061 ms 超时**、
      > coinbase **15250 ms 超时**、bybit **12875 ms 超时**、
      > bitfinex **12375 ms 超时**、mexc **12282 ms 超时**、
      > bitstamp **12233 ms 超时**、exodus **12171 ms 超时**、
      > okx **DNS 失败** ⇒ **活 1 / 12**。

- [x] 流动性槽**卡住**这件事已定位到行（**不是**失败）

      > **读数**（`py-spy dump --pid 29224 --locals`，2026-11-26 09:5x）：
      > 线程 `v4-maintenance-v4_pool_liquidity_worker` 停在
      > `_fetch_json (v4_pool_index.py:141)` →
      > `eth_price_usd (v4_pool_index.py:189)`，
      > `url = "https://api.kraken.com/0/public/Ticker?pair=ETHUSD"`，
      > `errors = ["binance_us: <urlopen error timed out>"]`。

- [x] 「槽 35 min 没有第二次写入」已确认

      > **读数**（`chain_business_initialization`，2026-11-26 09:41）：
      > `v4_pool_liquidity_worker` 的 `refreshed_at` 停在 **2026-11-26T01:16:42Z**
      > ＝ 09:16:42，槽间隔 **900 s** ⇒ **第二次 tick 从未落地**。

- [x] 「失败按池重试」已定位到行

      > **读数**（`v4_pool_index.py`）：
      > `eth_price_usd()` 失败 ⇒ `ETH_PRICE_CACHE.update({"price": None, ...})`
      > ⇒ 缓存被清空 ⇒ **下一个池子**重新抓全部源；
      > 每源 `_fetch_json(url)` 超时 **10 s** × 3 源 ⇒ 一轮 **≈ 30 s**；
      > 本 tick 实测 **363 池** ⇒ **≈ 3 h / tick**。

- [x] 「陈旧价格永不刷新」已定位

      > **读数**：`eth_price_usd()` 的 `refresh` 参数**全仓只有一个调用点**
      > （`v4_pool_index.py:1105`）且**不传**
      > ⇒ 命中缓存即返回；`ETH_PRICE_MAX_AGE_SECONDS` 默认 **900 s**
      > ⇒ 之后调用方按 `_price_is_fresh()` 置空，**无重试路径**。

---

## 2. 改动

- [x] 价格源**追加**一个可达源（后备链保留，不是替换）

      > **读数**：`ETH_PRICE_SOURCES` 首位 `("gateio", "https://api.gateio.ws/api/v4/spot/tickers?currency_pair=ETH_USDT")`，
      > 原三源（binance_us / kraken / coingecko）**降为后备**。

- [x] 新增 `gateio` 取价分支（**只认 ETH 交易对**）

      > **读数**：`_extract_eth_price()` 新分支只在
      > `currency_pair` ∈ {`ETH_USDT`, `ETH_USDC`} 时取 `last`；
      > 列表里的别的交易对**不会被当成 ETH**（单测里用 `BTC_USDT` 行验证）。

- [x] 失败**冷却**：一次失败被记住，冷却窗内不重抓

      > **读数**：新增 `ETH_PRICE_RETRY_COOLDOWN_SECONDS`（默认 **60 s**）
      > ＋ `_recent_price_failure()`；单测 `test_a_failed_price_fetch_is_not_retried_for_every_pool`
      > 断言**第二次调用不再发起抓取**。

- [x] 陈旧缓存**自我刷新**

      > **读数**：`eth_price_usd()` 命中缓存时**额外校验 `_price_is_fresh()`**；
      > 单测 `test_a_stale_price_is_refreshed` 断言陈旧读数被替换。

- [x] **没有**新增任何降级分支（口径核对）

      > **读数**：价格仍拿不到 ⇒ `price = None` ⇒ 池子**未定价**
      > （`estimatedTvlUsd = None`）；**绝不**用过期价 / 编造价算 TVL。

- [x] **Rust 侧零改动**（与 `T-核.05` 正交）

      > **读数**：本任务改动文件仅
      > `flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py`
      > ＋ `flashloan/app_runtime/tests/unit/test_robinhood_mainnet_v4_pool_index.py`。

---

## 3. 回归

- [x] 目标单测全绿（**+3**）

      > **读数**：`test_robinhood_mainnet_v4_pool_index.py` ⇒ **23 passed**
      > （基线 **20** ＋ 新 **3**）。

- [x] 相关回归全绿

      > **读数**：`test_robinhood_v4_pool_capacity.py`
      > ＋ `test_v4_maintenance_workers.py` ⇒ **58 passed / 0 failed**。

---

## 4. 实盘读数（**部署后实测**）

- [x] 实盘取价走通

      > **读数**（模块直调，2026-11-26 10:0x）：
      > 首次 `eth_price_usd()` **704 ms** ⇒ `price = 1702.18`、`priceSource = gateio`；
      > 第二 / 三次调用 **0 ms**（命中缓存）。

- [x] 实盘：流动性槽**恢复**

      > **读数**（2026-11-26 10:07:17，watcher 读到）：
      > `v4_pool_liquidity_worker` ⇒ `status = ok`、`mode = complete`、
      > `refreshed_at = 2026-11-26T02:07:12Z`、`scanned_pool_count = 363`、
      > `snapshot_rows_written = 363`、`error = ""`；
      > watcher 打印 **"liquidity slot recovered"**。

- [x] 实盘：快照表**真的动了**

      > **读数**：`v4_exchange_pool_liquidity_snapshots` 的 `MAX(liquidity_read_at)`
      > 由 **2026-11-25T23:50:02Z** ⇒ **2026-11-26T02:07:09Z**（行数 **2486** 不变）。

- [x] 实盘：下游**解封**

      > **读数**（2026-11-26 10:09:51）：
      > `v4_candidate_yc_zc_worker` 的 `refreshed_at` 由
      > **2026-11-25T23:53:15Z** ⇒ **2026-11-26T02:09:51Z**
      > （此前它一直 `blocked_upstream`）。

- [x] 实盘：复核层发布**新一代**

      > **读数（2026-11-26 14:3x · 只读）**：`manager_v4_reviews` 的 `MAX(reviewed_at)`
      > = **2026-11-26T06:27:48Z**（**14:27** 本地）⇒ **远晚于**旧读数
      > （**2026-11-25T23:07:30Z**）⇒ 复核层**发布了一代新的**。
      > 判定分布 `completed` **1** ／ `not_executable` **135** ／ `data_incomplete` **38**
      > （旧读数：**174** 条全 `data_incomplete`）⇒ **新一代不是空的**。
      > 复核 → 广播请求的**时差实测 4.4 s**（`reviewed_at 05:56:17.867Z` → `created_at 05:56:22.235Z`）
      > ⇒ 复核放行的正收益闭环**会**被发布成广播请求。
      > 探针：`probe_review_generation_20261126.py` ＋ `probe_review_to_publish_lag_20261126.py`。

- [ ] 实盘：广播记录**出现**

      > **待取**：`flashloan/db/broadcast_transaction_db` 仍是 **0 文件 / 0 字节**，
      > `manager_v4_review_broadcast_requests = 0`。
      > ⇒ **必须先有复核正向利润**（用户口径）⇒ 与上一条同源，**归 `M-6` / `M-7`**。

---

## 5. 遗留（如实登记，**不代任何其它任务关闭**）

- [x] R1 已登记：流动性 tick 的**遗留 Python 段仍按池逐个发 RPC**，
      **363 池实测 ≈ 5.5 min / tick** ⇒ 归属 `M-13`（测量职责应整体归 Rust 池管理器）
- [x] R2 已登记：Gate.io 报价 **ETH/USDT**，携带 **USDT/USD 基差** ⇒ 归属 `M-13`（**先留占位**）
- [x] R3 已登记：冷却窗内返回的是**上次失败**（不含新信息）⇒ **有意为之**（把「卡死」换成「失败一次」）
- [x] R4 已登记：复核层仍是旧一代 ⇒ 归属 `M-6`

