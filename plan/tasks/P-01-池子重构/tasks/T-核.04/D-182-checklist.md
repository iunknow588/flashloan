# D-182 `T-核.04`｜复核定价路径的 RPC 故障转移 · 验收清单

> **本文件是 `T-核.04` 的全部验收框。** 编号 `AC-核.04` 属**纯 ID**。
> 每条勾选**必须**有实测读数支撑；**不得**凭推断勾选；**不得**用零冒充未测量。

**验收前置**：`M-1` 现有单测全绿

**本轮状态（2026-11-26）**：代码项**全部**闭合；**部署闸门进行中**（见 §4）。

---

## 1. 根因与定位

- [x] 复核失败的**真因**已定位（**不是**链上执行、**不是**容量、**不是**利润判定）

      > **读数**（主库 `mode=ro` ＋ `query_only`）：
      > `manager_v4_reviews` ⇒ `data_incomplete` **174** ／ `completed` **0**；
      > `review_reason` = `"signed PnL is missing or incomplete"`；
      > `run_arbitrage_json` = `{"blockNumber":null,"diagnostics":[],
      > "executable":false,"ok":false,"signedPnl":null}`；
      > `static_preview_json.bestAmountMissingReason` =
      > `"official_v4:previewBestPool:eth_call: eth_call RPC request failed:
      > error sending request for url (…robinhood-mainnet.g.alchemy.com…)"`。
      > ⇒ **路由没有被定价**（`blockNumber` 为空 ⇒ 连区块都没取到）。

- [x] 故障转移机制**早已存在**，缺的是「可以用的那份端点清单」

      > **读数**：`rs-src/src/worker_pool.rs` 里 `run_preview_with_rpc_failover()`
      > 与 `is_retryable_rpc_error()`（覆盖 `"error sending request"` / `"429"` /
      > `"too many requests"`）**早已就绪**；
      > 但 `config_rpc_urls()` 只从 `config["rpcUrls"]` 取，
      > **缺省退化为单个 `config["rpcUrl"]`** ⇒ 列表**只有 1 个元素**
      > ⇒ **无故障转移可做**。

- [x] 链上发布了**多个**端点，且当时**多数健康**（实测）

      > **读数**（`eth_blockNumber`，2026-11-26 07:0x）：
      > `https://rpc.ordofi.network` ⇒ **DNS 解析失败**；
      > `https://robinhood-mainnet.g.alchemy.com/v2/alch_…` ⇒ **HTTP 429**；
      > `https://rpc.mainnet.chain.robinhood.com` ⇒ **HTTP 200**（1,241 ms）；
      > `https://robinhood-rpc.publicnode.com` ⇒ **HTTP 200**；
      > `https://…quiknode.pro/…` ⇒ **HTTP 200**。
      > `.env` 侧读数：`ROBINHOOD_MAINNET_RPC_URLS` **5** 个、`ROBINHOOD_RPCS` **5** 个、
      > `ROBINHOOD_TESTNET_RPCS` **4** 个。

- [x] 失败发生在**第一个**被试的端点上（Alchemy 429）

      > **读数**：错误串里的 URL 是 `robinhood-mainnet.g.alchemy.com/v2/alch_…`
      > ⇒ 与 `ROBINHOOD_MAINNET_RPC_URLS` 的**第 5 个**（`rpc.ordofi.network` DNS 死、
      > Alchemy 429）同属「当时不健康」的那一批。

---

## 2. 实施（端点清单 ＋ 故障转移接线）

- [x] 池管理器：**按链**取回端点全集（主网 / 测试网**分离**）

      > **读数**：`flashloan/tools/pool_manager/src/chain.rs`
      > **新增** `pub fn rpc_env_vars_for(chain_id) -> &'static [&'static str]`
      > （主网 `ROBINHOOD_*` ／ 测试网 `ROBINHOOD_TESTNET_*` **两组不共用变量**）、
      > `pub fn rpc_urls_for_chain(chain_id) -> Vec<String>`；
      > `pub fn rpc_urls_from_env()` 改为**委托**新增私有
      > `fn rpc_urls_from_env_vars(names: &[&str])` ⇒ **既有行为一字未变**。
      > 单测：`the_endpoint_variables_follow_the_chain`、
      > `the_endpoints_of_a_chain_never_name_another_chain`。

- [x] 运行时：配置端点**在前**、链上端点**在后**，去重

      > **读数**：`rs-src/src/worker_pool.rs`
      > `fn config_rpc_urls(config: &Value, fallback_url: &str, chain_urls: &[String])`
      > ＋ **新增** `fn chain_rpc_urls(config: &Value)`（按 `config["chainId"]` 取，
      > 缺省 `ROBINHOOD_CHAIN_ID`）。

- [x] 配置显式 `rpcUrls` **仍优先且不追加**（运维的顺序就是运维的顺序）

      > **读数**：单测 `config_rpc_urls_keeps_the_configured_order`
      > —— `{"rpcUrls":["http://a","http://b"]}` ⇒ 仍是 `[a, b]`，
      > 链上端点 **不**被追加。

- [x] 端点**不重复**

      > **读数**：单测 `config_rpc_urls_never_repeats_an_endpoint`。

- [x] 两个调用点都已接入（广播路径 ＋ 复核路径）

      > **读数**：`worker_pool.rs:3311`（`maybe_broadcast_with_rpc_failover`）、
      > `worker_pool.rs:3808`（`run_native_review`）
      > —— 均改为 `config_rpc_urls(config, &primary_rpc_url, &chain_rpc_urls(config))`。

- [x] **Python 侧零改动**（用户口径：广播与复核都在 Rust 侧）

      > **读数**：本任务的代码写入**只有** `chain.rs` ＋ `worker_pool.rs`
      > 两个 Rust 文件；Python 侧只新增了 `plan/runtime_evidence/tools/` 下的
      > **只读探针**（不参与运行）。

- [x] **未**改重试次数 / 退避 / 超时预算 / 广播门禁 / 利润口径

      > **读数**：`config_rpc_urls` 与 `chain_rpc_urls` 之外，本任务对
      > `worker_pool.rs` 的写入**只有两个调用点的实参**；
      > `is_retryable_rpc_error` 与 `run_preview_with_rpc_failover` **一字未改**。

- [x] 「全部端点失败」**仍是失败**（无降级）

      > **读数**：本任务**未新增**任何兜底分支、**未新增**「跳过本轮」路径；
      > 错误串**原样**进 `static_preview_json`。

---

## 3. 回归

- [x] `M-1`（池管理器）回归全绿

      > **读数**：`cargo test --offline -p pool-manager`
      > ⇒ **169 passed / 0 failed**（基线 **167** ＋ 本任务新 **2**），**无 warning**。
      > 日志：`plan/runtime_evidence/_deploy_rpcfix_pm_tests.*.log`。

- [x] `M-13`（运行时内核，Rust）回归全绿

      > **读数**：`cargo test --offline`（`flashloan/rs-src`）
      > ⇒ **245 passed / 0 failed**（基线 **241** ＋ 本任务新 **4**）。
      > 日志：`plan/runtime_evidence/_deploy_rpcfix_rs_tests.*.log`。

- [x] 只跑了**改动模块**的测试

      > **读数**：只执行了 §2 的两条 cargo 命令；**未**跑全工作区测试。

---

## 4. 部署闸门（**实盘**）

- [x] 旧 DLL 已备份

      > **读数**：`flashloan/rs-src/target/release/flashloan_rs.dll.bak-20261126`
      > = **16,380,928 B**，mtime **2026-11-25 14:32:12**。

- [x] release 重建完成

      > **读数**：`Finished `release` profile [optimized] target(s) in 2m 53s`；
      > `flashloan_rs.dll` = **18,135,040 B**，mtime **2026-11-26 08:09:06**，
      > PE 0x8664。

- [x] 实盘 runtime 已用新二进制重启

      > **读数**：看护 pid **16052** ＋ runtime pid **15528**，起于 **08:10:06**，
      > 端口 **5000**；`GET /api/dex/opportunity-scanner/snapshot` 与
      > `GET /api/dex/closed-loop-groups` 均 **HTTP 200**。
      > 旧实例（看护 26432 ／ runtime 10380）已停 ⇒ **新旧二进制无中间态**。

- [x] **实盘复核读数**：复核状态**离开** `data_incomplete`

      > **读数（2026-11-26 14:3x · 只读）**：`manager_v4_reviews` 的判定分布 ⇒
      > `completed` **1** ／ `not_executable` **135** ／ `data_incomplete` **38**
      > （旧读数：**174** 条**全部** `data_incomplete`）⇒ **状态已离开 `data_incomplete`**。
      > 复核层**在跑**：`MAX(reviewed_at)` = **2026-11-26T06:27:48Z**（**14:27** 本地，**在重启之后**）。
      > 概览 `summary.reviewCompletedCount = 1` ／ `reviewNotExecutableCount = 136`。
      > **诚实边界**：剩下的 `data_incomplete` **不是**故障 —— 多数是「本轮还没排到」的
      > `not_reviewed_since:` 标记；`not_executable` 的内层错误是 `NoProfitableRoute`
      > （**链上套利合约自己回退**，此刻市场不赚钱）。
      > 探针：`verify_restart_gates_20261126.py` ＋ `probe_review_generation_20261126.py`。

- **广播侧读数**：出现 `manager_cow_execution_attempts` 真实行
      ＋ `broadcast_transaction_db/robinhood/4663/<月份>/` 被创建
      —— **不在本任务勾选**：广播记录归 `M-7`（`T-广.02` 的部署闸门），
      本任务**不代其它模块关验收项**。这里只**留指针**。

---

## 5. 如实登记

* [x] 本任务的**代码**已落地并回归通过
* [x] 本任务的**实盘生效**取决于 §4 的复核读数，**未**用推断冒充读数
* [x] 「端点健康度探测 / 自动摘除」「记录生命周期清理」「跨月统计」
      **留作占位**（用户口径：异常路径最后再实现）

