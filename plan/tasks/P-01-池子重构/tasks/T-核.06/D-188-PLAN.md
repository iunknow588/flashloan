# D-188 `T-核.06`｜流动性维护槽的价格源依赖 · 开发计划

> **本文件是 `T-核.06` 自己的开发计划**（自包含）。
> 编号 `T-核.06` / `AC-核.06` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。

---

## 1. 触发（实测 · 只读 · 2026-11-26 09:34 → 10:07）

### 1.1 网络修复后的复测结果

链上 RPC 端点（`eth_blockNumber`，2026-11-26 09:36）：

| 端点 | HTTP | 耗时 | 判定 |
|---|---|---|---|
| `robinhood-rpc.publicnode.com` | **200** | 1280 ms | 活 |
| `blissful-billowing-pine.robinhood-mainnet.quiknode.pro` | **200** | 797 ms | 活 |
| `rpc.mainnet.chain.robinhood.com` | 403 | 734 ms | 拒答 |
| `rpc.ordofi.network` | — | DNS 失败 | 死 |
| `robinhood-mainnet.g.alchemy.com` | 429 | 702 ms | 限流 |

⇒ **活 2 / 5**（与修复前同），但**活着的两个都快**（此前 publicnode 单次 13.2 s）。

**外部价格源**（`plan/runtime_evidence/tools/probe_price_sources_20261126.py`，
2026-11-26 10:00，并发 12 源、每源 12 s 超时）：

| 价格源 | HTTP | 耗时 | 判定 |
|---|---|---|---|
| `api.gateio.ws` | **200** | 2030 ms | **活** |
| `api.binance.us` | — | 16077 ms | 超时 |
| `api.binance.com` | — | 16157 ms | 超时 |
| `api.kraken.com` | — | 16077 ms | 超时 |
| `api.coingecko.com` | — | 16061 ms | 超时 |
| `api.coinbase.com` | — | 15250 ms | 超时 |
| `api.bybit.com` | — | 12875 ms | 超时 |
| `api.bitfinex.com` | — | 12375 ms | 超时 |
| `api.mexc.com` | — | 12282 ms | 超时 |
| `api.bitstamp.net` | — | 12233 ms | 超时 |
| `api.exodus.com` | — | 12171 ms | 超时 |
| `www.okx.com` | — | 2780 ms | DNS 失败 |

⇒ **活 1 / 12**。**部署网络的出口只放行链上 RPC 主机与 `api.gateio.ws`。**

### 1.2 复测暴露的第二个阻塞（比 RPC 更靠后、更致命）

**读数**（`py-spy dump --pid 29224 --locals`，2026-11-26 09:5x，
旧 runtime 线程 `v4-maintenance-v4_pool_liquidity_worker`）：

```
Thread 13696 (idle): "v4-maintenance-v4_pool_liquidity_worker"
    create_connection (socket.py:850)          ← address: ("api.kraken.com", 443)
    ...
    _fetch_json (dex\intent_trade\chains\robinhood\v4_pool_index.py:141)
    eth_price_usd (dex\intent_trade\chains\robinhood\v4_pool_index.py:189)
        Locals:
            errors: ["binance_us: <urlopen error timed out>"]
            source: "kraken"
            url: "https://api.kraken.com/0/public/Ticker?pair=ETHUSD"
```

⇒ 流动性槽**不是失败，是卡住**：它在**价格抓取**上阻塞，
而**链上 RPC 已经不是它当时的阻塞点了**。

**读数**（槽位表，`chain_business_initialization`，2026-11-26 09:41）：
`v4_pool_liquidity_worker` 的 `refreshed_at` 停在 **2026-11-26T01:16:42Z**
＝ 09:16:42，**此后 35 min 没有第二次写入** —— 槽间隔是 900 s，
⇒ **第二次 tick 从未落地**。

---

## 2. 根因（已定位到行）

### 2.1 根因 A：价格抓取在流动性 tick 的**关键路径**上

```
robinhood_v4_idle_maintenance.py:1428  refresh_result = refresh_v4_live(...)
robinhood_v4_idle_maintenance.py:1491  _materialize_pool_liquidity_snapshots(...)  ← 快照表的唯一写者
```

**Python 段（`refresh_v4_live` → `v4_pool_index.eth_price_usd()`）排在 Rust 段前面**，
而 Python 段的价格抓取挂在 `_liquidity_reserve_metrics()`（**每池一次**）上。
价格源全不可达 ⇒ **Rust 那一段根本没机会执行**。

### 2.2 根因 B：失败的价格抓取**按池重试** ⇒ 停更从「失败」退化成「卡死」

`flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py`：

- `eth_price_usd()` 失败时执行 `ETH_PRICE_CACHE.update({"price": None, ...})`
  ⇒ **缓存被清空**；
- 下一次调用（**下一个池子**）看到 `price is None` ⇒ **重新抓一遍全部源**；
- 每个源 `_fetch_json(url)` 超时 10 s ⇒ **一轮 3 源 ≈ 30 s**；
- 一个 tick 走 **363 个池子** ⇒ **≈ 3 小时**才走完一轮。

⇒ 观测到的形态就是「**槽不再更新，但也不报错**」。
这比 `T-核.05` 的整槽 `error` 更难诊断：`error` 至少会说出一条原因。

### 2.3 根因 C：**缓存里的陈旧价格永远不会自我刷新**

`eth_price_usd()` 的 `refresh` 参数**全仓只有一个调用点**（`v4_pool_index.py:1105`），
且**不传** ⇒ 命中缓存就直接返回。
`ETH_PRICE_MAX_AGE_SECONDS` 默认 **900 s** ⇒ 15 min 后调用方按
`_price_is_fresh()` 把价格置空 ⇒ **USD 估值列永久为空**，而且**没有任何重试路径**。

---

## 3. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 改动内容 | 是否改 |
|---|---|---|---|---|
| `M-13` | 运行时内核 | `flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_index.py` | 价格源清单 **+Gate.io**；`_extract_eth_price()` **+gateio 分支**；`eth_price_usd()` **缓存新鲜度判定** ＋ **失败冷却**；新增 `_recent_price_failure()` | **改** |
| `M-13` | 运行时内核 | `flashloan/app_runtime/tests/unit/test_robinhood_mainnet_v4_pool_index.py` | **+3 条**新单测（Gate.io 取价 / 失败不按池重试 / 陈旧价格自我刷新） | **改** |

**未改**（口径声明）：

- **Rust 侧零改动** ⇒ `T-核.05` 的端点故障转移口径**一字未动**；
- **快照表写者仍是 Rust 池管理器**（`v4_exchange_pool_liquidity_snapshots` 单一写者口径不变）；
- **没有新增任何降级分支**：价格仍然拿不到就**拿不到**，
  池子仍然按「未定价」处理（`estimatedTvlUsd = None`），
  **绝不**用过期价或编造价去算 TVL；
- 广播闸门 / 复核口径 / 利润口径**一字未改**。

---

## 4. 落地（口径）

1. **价格源**：`ETH_PRICE_SOURCES` **首位加 `gateio`**
   （`https://api.gateio.ws/api/v4/spot/tickers?currency_pair=ETH_USDT`），
   原有三源**降为后备链**（不是删除）。
   Gate.io 报价 **ETH/USDT**，携带 USDT/USD 基差；
   `ETH_USD_PRICE_MIN/MAX`（默认 500 / 20000）**继续**作为带外拒绝闸门。
2. **取价解析**：新增 `gateio` 分支 —— 只认 `currency_pair` 为
   `ETH_USDT` / `ETH_USDC` 的行，取 `last`；**列表里别的交易对不会被当成 ETH**。
3. **失败冷却**：`ETH_PRICE_RETRY_COOLDOWN_SECONDS`（默认 **60 s**）
   —— 一次失败被记住，冷却窗内的后续调用**直接返回这次失败**，
   **不再按池重复抓** ⇒ 槽从「卡 3 小时」变成「**失败一次**」。
4. **陈旧刷新**：缓存里的读数过了 `ETH_PRICE_MAX_AGE_SECONDS` 就**重抓**，
   不再把陈旧读数直接递出去 ⇒ USD 估值列不再永久为空。

---

## 5. 验收闸门（实测读数）

| 闸门 | 判据 | 读数 |
|---|---|---|
| G1 | 价格源可达 | `api.gateio.ws` **200 / 2030 ms**；其余 11 源超时 |
| G2 | 取价走通 | 首次 `eth_price_usd()` **704 ms** ⇒ `1702.18`、`priceSource = gateio` |
| G3 | 命中缓存不重复抓 | 第二 / 三次调用 **0 ms** |
| G4 | 单元回归 | `test_robinhood_mainnet_v4_pool_index.py` **23 passed**（基线 20 ＋ 3） |
| G5 | 相关回归 | `test_robinhood_v4_pool_capacity.py` ＋ `test_v4_maintenance_workers.py` ⇒ **58 passed / 0 failed** |
| G6 | 实盘：槽恢复 | `v4_pool_liquidity_worker` ⇒ `status = ok`、`mode = complete`、`refreshed_at = 2026-11-26T02:07:12Z`、`scanned_pool_count = 363`、`snapshot_rows_written = 363`、`error = ""` |
| G7 | 实盘：快照表动 | `MAX(liquidity_read_at)` 由 **2026-11-25T23:50:02Z** ⇒ **2026-11-26T02:07:09Z** |
| G8 | 实盘：下游解封 | `v4_candidate_yc_zc_worker` 由 **`blocked_upstream`** ⇒ **运行中**（`py-spy` 读到 `run_candidate_yc_zc_build`） |

---

## 6. 遗留（**如实登记，本任务不代任何其它任务关闭**）

| 编号 | 遗留 | 归属 |
|---|---|---|
| R1 | 流动性 tick 的**遗留 Python 段仍按池逐个发 RPC**，363 池实测 **≈ 5.5 min / tick**（Rust 段是批量的，慢的是 Python 段） | `M-13`（按用户口径「**Python 侧没有自己的数据处理逻辑**」⇒ 测量职责应整体归 Rust 池管理器） |
| R2 | Gate.io 报价 **ETH/USDT**，携带 **USDT/USD 基差**（未单独取 USDT/USD） | `M-13`（**先留占位**：等 USDT/USD 源可达再补，不为此新建降级分支） |
| R3 | 价格源冷却窗内**返回的是上次失败**（不含新信息） | **有意为之**：把「卡死」换成「失败一次」；跨窗重试即恢复 |
| R4 | 复核层仍是旧一代（`review_updated_at_max = 2026-11-25T23:07:30Z`、174 条全 `data_incomplete`） | `M-6`（**上游代际**）：等闭环层与复核层跑完新代次后另取读数 |

---

## 7. 不做的事（口径）

- **不改**广播闸门、复核口径、利润口径（用户：**默认放开授权，但必须确认复核正向利润**）；
- **不**新增任何「价格拿不到就跳过」之类的降级分支；
- **不**动 `T-核.05` 的 Rust 端点组装；
- **不**碰其它线程负责的内核大对象（`worker_pool.rs` 的内核对象部分）。

---

## 8. 回归命令（**可直接粘贴**）

```text
cd flashloan/app_runtime && python -m pytest tests/unit/test_robinhood_mainnet_v4_pool_index.py -q
cd flashloan/app_runtime && python -m pytest tests/unit/test_robinhood_v4_pool_capacity.py tests/unit/test_v4_maintenance_workers.py -q
```

**期望读数**（2026-11-26）：目标单测 **23 passed**（基线 20 ＋ 3）、相关 **58 passed / 0 failed**。

