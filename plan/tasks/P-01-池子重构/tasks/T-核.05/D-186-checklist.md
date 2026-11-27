# D-186 `T-核.05`｜流动性维护槽的 RPC 故障转移 · 验收清单

> **本文件是 `T-核.05` 的全部验收框。** 编号 `AC-核.05` 属**纯 ID**。
> 每条勾选**必须**有实测读数支撑；**不得**凭推断勾选；**不得**用零冒充未测量。

**验收前置**：`M-1` 现有单测全绿

**本轮状态（2026-11-26）**：代码项**全部**闭合；**部署闸门进行中**（见 §4）。

---

## 1. 根因与定位

- [x] 流动性维护槽**失败**这件事本身已定位（**只读**）

      > **读数**（主库 `mode=ro` ＋ `query_only`）：
      > `chain_business_initialization` ⇒ `v4_pool_liquidity_worker`
      > `status = error`、`refreshed_at = 2026-11-26T00:39:55Z`、
      > `error = "all V4 pool state RPC endpoints failed: https://[REDACTED]:
      > endpoint probe failed: host lookup failed: 不知道这样的主机。 (os error 11001)"`。
      > ⇒ **整槽失败**（不降级），快照表停更。

- [x] 失败时端点清单里**只有 1 个端点**（不是「5 个都挂了」）

      > **读数**：`pool_manager::chain::read_v4_pool_states_with_fallback()`
      > 把每个失败端点的原因用 `; ` 串起来；上列错误串**只有一段** ⇒
      > 清单长度 = **1**。
      > 端点健康度实测（`eth_blockNumber`，09:0x）⇒ **活 2 / 5**
      > （publicnode **200** ／ QuickNode **200** ／ robinhood **403** ／
      > ordofi **DNS 死** ／ Alchemy **429**）
      > ⇒ **不是没有可用节点**，是**没有去试**。

- [x] 「只试一个端点」的**代码根因**已定位到行

      > **读数**（逐跳）：
      > `flashloan/src/dex/runtime/robinhood_v4_idle_maintenance.py:72`
      > `_robinhood_rpc_url()` →
      > `flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_rpc.py:23`
      > `_rpc_url_from_env_or_default()`（`return urls[0]`）→
      > `core/rs_bridge.py:230` →
      > `flashloan/rs-src/src/pool_depth.rs:58`（`DepthOptions.rpc_url = Some(url)`）→
      > `flashloan/tools/pool_manager/src/lib.rs` `refresh_depth()`
      > （`Some(url) => vec![url]`）
      > ⇒ **清单长度 = 1** ⇒
      > `read_v4_pool_states_with_fallback()`（**故障转移机制早已就绪**）**无转移可做**。

- [x] 同链的**另一条**读链入口**有**故障转移 ⇒ 两条路**口径不一致**

      > **读数**：复核定价路径（`T-核.04` 之后）按「配置端点在前 ＋ 链上端点在后」
      > 试链；流动性刷新路径**只试一个**。
      > ⇒ 同一时刻、同一条链，**一条能自愈、一条不能**。

- [x] **跨链**缺陷（测试网会读主网节点）已定位

      > **读数**：`refresh_depth()` 原来的兜底是 `chain::rpc_urls_for(chain_id)`，
      > 而 `rpc_urls_for()` 内部调 `rpc_urls_from_env()` —— **主网**的环境变量组，
      > **与 chain_id 无关** ⇒ 测试网跑起来量的是**主网**的节点。
      > 正确的读取口 `chain::rpc_urls_for_chain(chain_id)` 已由 `T-核.04` 写好，
      > 本任务只是把它接到**深度刷新**这条路上。

---

## 2. 实施

- [x] 深度刷新有一条**有序端点清单**的入口（`M-1`）

      > **读数**：`pool_manager::DepthOptions` **新增** `rpc_urls: Option<Vec<String>>`；
      > **新增**纯函数 `resolve_depth_rpc_urls(options, chain_id)` ＋ `push_endpoint()`；
      > 优先级 = `rpc_urls` ＞ `rpc_url` ＞ 链上端点；**去重**、**去空白**、**保序**。

- [x] 刷新**按清单**依次试，且兜底**按链**取端点（`M-1`）

      > **读数**：`refresh_depth()` 改为 `resolve_depth_rpc_urls(options, chain_id)`；
      > 兜底由 `chain::rpc_urls_for(chain_id)` 改为
      > `chain::rpc_urls_for_chain(chain_id)` ⇒ **测试网不再读主网节点**。

- [x] 运行时入口**按链**组装清单（`M-13`）

      > **读数**：`rs-src/src/pool_depth.rs` **新增**纯函数
      > `liquidity_rpc_urls(named, chain_urls)` ＋ `push_endpoint()`；
      > `refresh_pool_liquidity()` 取
      > `pool_manager::chain::rpc_urls_for_chain(chain_id)`，
      > 组装成「**调用方命名的端点在前 ＋ 链上端点在后**」的清单。

- [x] **Python 零改动**、命令行语义不变

      > **读数**：`flashloan/src/**` **零改动**（遗留 Python 取 `urls[0]` 的行为
      > **由 Rust 侧吸收**）；`pool_manager/src/main.rs` 的 `DepthOptions`
      > 构造处补 `rpc_urls: None` ⇒ 操作员点名的 `--rpc-url` **仍然只试那一个**。

- [x] **未**改重试次数 / 退避 / 超时预算 / 广播门禁 / 利润口径

      > **读数**：本任务的写入只有三处（两个纯函数 ＋ 一处端点解析）；
      > `read_v4_pool_states_with_fallback()` 的重试与退避**一字未改**。

- [x] 「全部端点失败」**仍是失败**（无降级）

      > **读数**：本任务**未新增**任何兜底分支、**未新增**「跳过本轮」路径；
      > 失败端点的原因仍然逐条拼进同一条错误串。

---

## 3. 回归

- [x] `M-1`（池管理器）回归全绿

      > **读数**：`cargo test --offline -p pool-manager`
      > ⇒ **172 passed / 0 failed**（基线 **169** ＋ 本任务新 **3**）。
      > 日志：`plan/runtime_evidence/_deploy_rpcfix2_pm_tests.log`。

- [x] `M-13`（运行时内核，Rust）回归全绿

      > **读数**：`cargo test --offline`（`flashloan/rs-src`）
      > ⇒ **248 passed / 0 failed**（基线 **245** ＋ 本任务新 **3**）。
      > 日志：`plan/runtime_evidence/_deploy_rpcfix2_rs_tests.log`。

- [x] 只跑了**改动模块**的测试

      > **读数**：只执行了 §2 的两条 cargo 命令；**未**跑全工作区测试。

---

## 4. 部署闸门（**实盘**）

- [x] 旧 DLL 已备份

      > **读数**：`flashloan/rs-src/target/release/flashloan_rs.dll.bak-20261126-he04`
      > = **18,135,040 B**，mtime **2026-11-26 08:09:06**（＝ `T-核.04` 那份新二进制的原件）。
      > 上一轮那份 `flashloan_rs.dll.bak-20261126` = **16,380,928 B**（2026-11-25 14:32:12）**未动**。

- [x] release 重建完成

      > **读数**：`cargo build --offline --release`（`flashloan/rs-src`）
      > ⇒ `Finished `release` profile [optimized] target(s) in 1m 12s`；
      > 新 `flashloan_rs.dll` = **18,134,528 B**，mtime **2026-11-26 09:13:57**。
      > **第一次构建失败**（`failed to remove file … flashloan_rs.dll` ⇒ `拒绝访问 (os error 5)`）：
      > DLL 被**运行中的 runtime** 锁定 ⇒ 先停机再构建 ⇒ **构建 → 装载**之间没有旧实例。

- [x] 实盘 runtime 已用新二进制重启（**新旧同批 · 无中间态**）

      > **读数**：看护 pid **34548** ＋ runtime pid **29224**，起于 **09:14:52**，端口 **5000**；
      > 新 DLL 的 mtime（**09:13:57**）**早于** runtime 启动时刻 ⇒ 装载的**就是**新二进制；
      > 停机窗口 **09:11:47 → 09:14:52** 内**没有**新旧二进制混跑。

- [x] **实盘读数**：`v4_pool_liquidity_worker` 的 `status` **离开** `error`

      > **读数（2026-11-26 14:3x · 只读）**：`chain_business_initialization` ⇒
      > `v4_pool_liquidity_worker` `status = ok` ／ `mode = complete` ／ `error = null` ／
      > `pool_count = 322` ／ `scanned_pool_count = 322` ／ `last_block_number = 123483565` ／
      > `refreshed_at = 2026-11-26T06:05:10Z` ⇒ **已离开 `error`**。
      > 快照表 `v4_exchange_pool_liquidity_snapshots` **2486** 行，
      > `MAX(liquidity_read_at)` = **2026-11-26T06:29:53Z**（**在前进**）。
      > 其余 **7** 个槽（`v4_candidate_yc_zc_worker` ／ `v4_market_observation_worker` ／
      > `v4_directional_pool_worker` ／ `v4_dex_overview_worker` ／ `v4_closed_loop_worker` ／
      > `v4_onchain_review_worker` ／ `v4_broadcast_execution_worker`）**全部 `status = ok`**。
      > 探针：`probe_maintenance_slots_20261126.py`。

---

## 5. 如实登记

* [x] 本任务的**代码**已落地并回归通过
* [x] 本任务的**实盘生效**取决于 §4 的读数，**未**用推断冒充读数
* [x] 「端点健康度自动摘除」「重试预算」「记录生命周期清理」「跨月统计」
      **留作占位**（用户口径：异常路径最后再实现）

