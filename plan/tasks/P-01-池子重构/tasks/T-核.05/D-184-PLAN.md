# D-184 `T-核.05`｜流动性维护槽的 RPC 故障转移 · 开发计划

> **本文件是 `T-核.05` 自己的开发计划**（自包含）。
> 编号 `T-核.05` / `AC-核.05` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。

---

## 1. 触发（实测 · 只读 · 2026-11-26 09:0x）

`chain_business_initialization` 的 `v4_pool_liquidity_worker` 一行：

| 字段 | 读数 |
|---|---|
| `status` | **`error`** |
| `refreshed_at` | **2026-11-26T00:39:55Z**（＝ 08:39:55 +08） |
| `error` | `all V4 pool state RPC endpoints failed: https://[REDACTED]: endpoint probe failed: host lookup failed: 不知道这样的主机。 (os error 11001)` |

**关键读数**：错误串里**只有一段**端点信息。
`pool_manager::chain::read_v4_pool_states_with_fallback()` 把每个失败端点的
原因用 `; ` 串起来 —— 只有一个端点 ⇒ **这次刷新拿到的端点清单里只有 1 个端点**。

同批读数（`manager_review_library_state`）：`review_version` **214**、
`closed_loop_version` **21**、`completed` **0** ／ `data_incomplete` **174**；
`manager_v4_closed_loop_library_state` ⇒ `closed_loop_version` **22**
（`generated_at` **00:04:33Z** ／ `updated_at` **00:49:51Z**）
⇒ **闭环层已经前进，复核层还停在旧的闭环代次上**。

端点健康度（`eth_blockNumber`，2026-11-26 09:0x）⇒
publicnode **200**（1703 ms）／ QuickNode **200**（843 ms）／
`rpc.mainnet.chain.robinhood.com` **403** ／ `rpc.ordofi.network` **DNS 解析失败** ／
Alchemy **429** ⇒ **活 2 / 5**。

---

## 2. 根因（两条，都已定位到行）

### 2.1 根因 A：**流动性刷新只试一个端点**（主因）

调用链（逐跳读数）：

| 跳 | 落点 | 事实 |
|---|---|---|
| 1 | `flashloan/src/dex/runtime/robinhood_v4_idle_maintenance.py:72` `_robinhood_rpc_url()` | 返回**一个** URL 字符串 |
| 2 | `flashloan/src/dex/intent_trade/chains/robinhood/v4_pool_rpc.py:23` `_rpc_url_from_env_or_default()` | `return urls[0]` —— **只取第 1 个** |
| 3 | `flashloan/src/core/db/v4_pool_liquidity_worker.py` → `core/rs_bridge.py:230` | 以 `rpc_url=<单个 URL>` 传给 Rust 入口 |
| 4 | `flashloan/rs-src/src/pool_depth.rs:58` | 写成 `DepthOptions.rpc_url = Some(url)` |
| 5 | `flashloan/tools/pool_manager/src/lib.rs` `refresh_depth()` | `Some(url) => vec![url]` ⇒ **端点清单长度 = 1** |
| 6 | `flashloan/tools/pool_manager/src/chain.rs` `read_v4_pool_states_with_fallback()` | 故障转移机制**早已就绪**，但清单只有 1 个 ⇒ **无转移可做** |

**同一时刻，同一条链**：复核定价路径（`T-核.04` 之后）**有**故障转移，
流动性刷新路径**没有** ⇒ 两条读链入口的**口径不一致**。
一次 DNS 抖动 ⇒ 整个流动性维护槽失败 ⇒ 快照表停更 ⇒ 方向层 / 闭环层
拿到的是**陈旧**证据 ⇒ 复核层没有理由发布新一代。

### 2.2 根因 B：**测试网会读主网的节点**（同源缺陷）

`refresh_depth()` 原来的兜底是 `chain::rpc_urls_for(chain_id)`，
而 `rpc_urls_for()` 内部调的是 `rpc_urls_from_env()` —— **主网**的环境变量组
（`ROBINHOOD_MAINNET_RPC_URLS` / `ROBINHOOD_RPCS` / …），
**与 chain_id 无关** ⇒ 跑测试网时，端点清单是**主网的节点**。
`T-核.04` 已经为这一层写好了正确的读取口：`chain::rpc_urls_for_chain(chain_id)`
（主网 / 测试网**分离**），本任务只是把它接到**深度刷新**这条路上。

---

## 3. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 改动内容 | 是否改 |
|---|---|---|---|---|
| `M-1` | 池管理器 | `flashloan/tools/pool_manager/src/lib.rs` | `DepthOptions` **新增** `rpc_urls` 字段；**新增** `resolve_depth_rpc_urls()`（纯函数）与 `push_endpoint()`；`refresh_depth()` 改用它 | **是** |
| `M-1` | 池管理器（命令行） | `flashloan/tools/pool_manager/src/main.rs` | `DepthOptions` 构造处补 `rpc_urls: None`（**命令行语义不变**） | **是** |
| `M-13` | 运行时内核与接线 | `flashloan/rs-src/src/pool_depth.rs` | **新增** `liquidity_rpc_urls()`（纯函数）与 `push_endpoint()`；`refresh_pool_liquidity()` 按链组装端点清单 | **是** |

**未改动**：`flashloan/src/**`（Python **零改动**）、`worker_pool.rs`、
`runtime_core.rs`、`review_publish.rs`、`manager_api/**`、
`flashloan/tools/manager_db/**`（不涉及迁移）。

---

## 4. 开发计划（**四步**）

| 步 | 做什么 | 落点 | 完成判据 |
|---|---|---|---|
| **1** | 给深度刷新一条**有序端点清单**的入口 | `pool_manager/src/lib.rs` | `DepthOptions.rpc_urls` 存在；`resolve_depth_rpc_urls()` 是**纯函数**（无 IO） |
| **2** | 让刷新**按清单**依次试，并**按链**取端点 | `pool_manager/src/lib.rs` `refresh_depth()` | 清单顺序 = 给定顺序；兜底走 `rpc_urls_for_chain(chain_id)`（**不再**跨链） |
| **3** | 运行时入口按链组装清单 | `rs-src/src/pool_depth.rs` | 调用方命名的端点**在前**，链上端点**在后**，去重；链由 `chain_id` 选定 |
| **4** | 单测锁住三条性质 | 两处 `mod tests` | ① 顺序与去重 ② 单端点仍可用 ③ **测试网清单不含主网节点** |

**不做**（用户口径：异常 / 低概率路径**先留占位**）：
端点健康度自动摘除、重试次数 / 退避 / 超时预算、跨月统计、落盘失败兜底。

---

## 5. 回归范围与命令

> **回归范围 = 本次改动触及的模块集合** ⇒ 只跑 `M-1` ＋ `M-13`。
> **未出现在改动集合里的模块，一律不跑。**

```powershell
cd flashloan/tools
cargo test --offline -p pool-manager

cd ../rs-src
cargo test --offline
```

**实测读数**（2026-11-26 09:0x）：

| 模块 | 基线 | 本任务后 | 证据 |
|---|---|---|---|
| `M-1` `pool-manager` | **169** passed | **172** passed / 0 failed（新 **3**） | `plan/runtime_evidence/_deploy_rpcfix2_pm_tests.log` |
| `M-13` `flashloan-rs` | **245** passed | 见 `D-186` §3 | `plan/runtime_evidence/_deploy_rpcfix2_rs_tests.log` |

---

## 6. 部署闸门（**实盘**）

| # | 闸门 | 判据 |
|---|---|---|
| 1 | 旧 DLL 已备份 | `flashloan_rs.dll.bak-20261126` 存在且可回滚 |
| 2 | release 重建 | 新 `flashloan_rs.dll` 的 mtime 晚于本任务最后一次改动 |
| 3 | **新旧二进制同批上线** | 旧实例先停、新实例后起 ⇒ **无中间态** |
| 4 | **实盘读数** | `v4_pool_liquidity_worker` 的 `status` **离开** `error`；快照表 `liquidity_read_at` **前进** |

> **读数口径**：`refresh_v4_pool_liquidity` 的失败是**整槽失败**（不降级），
> 所以闸门 4 的判据是**槽状态**而不是「有没有写几行」。

---

## 7. 与既有任务的关系（**只写模块级前置**）

| 关系 | 说明 |
|---|---|
| `M-1` ＋ `M-13` | 本任务的改动模块集合（回归范围） |
| `M-7`（广播层） | **不依赖**：广播侧端点解析已在 `T-核.04` 落地，本任务**不代它关验收项** |
| `M-10`（存储层） | **不涉及**：本任务**零迁移**、**零写库结构**改动 |

