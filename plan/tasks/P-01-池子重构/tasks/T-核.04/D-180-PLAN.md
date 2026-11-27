# `T-核.04`｜复核定价路径的 RPC 故障转移 · 开发计划（**独立 · 自包含**）

> **本目录是 `T-核.04` 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**

---

## 1.0 编号逻辑

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-核.04`** |
| **本任务验收编号** | **`AC-核.04`** |
| **编号作用域** | 模块内（单模块索引） |
| **编号索引** | **`核`**（`M-13`） |
| **序号** | 第 `04` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | `T-核.01`（已闭合）、`T-核.02`（**另一线程**，只监测）、`T-核.03`（已闭合） |

**本任务涉及的模块**：

| 模块 | 名称 | 代码落点 |
|---|---|---|
| `M-13` | 运行时内核与运行时接线 | `flashloan/rs-src/src/worker_pool.rs` |
| `M-1` | 池管理器（链与端点读取） | `flashloan/tools/pool_manager/src/chain.rs` |

**登记**：[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §4.2。

---

## 1. 任务说明

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-核.04`** |
| **验收编号（纯 ID）** | **`AC-核.04`** |
| **标题** | 复核定价路径的 RPC 故障转移（让「一个节点坏了」不再等于「一条路由没测过」） |
| **优先级** | **P0** |
| **波次（模块级）** | `W-1` |
| **前置模块** | `M-7`（主网 RPC 配置与环境） |
| **需授权** | 否（本任务只改代码；实盘生效属部署闸门） |
| **一句话目标** | 让**复核定价路径**与**广播路径**一样，按**链**取回**端点全集**并沿用既有的故障转移机制 ⇒ 路由不再因为「当时那一个节点坏了」被判为「没定价」。 |

### 1.1 背景与问题（**只读实测 · 2026-11-26 07:0x**）

```text
实盘复核：174 / 174 全部 data_incomplete
  review_reason     = "signed PnL is missing or incomplete"
  run_arbitrage_json= {"blockNumber":null,"diagnostics":[],"executable":false,
                       "ok":false,"signedPnl":null}
  static_preview_json → bestAmountMissingReason =
      "official_v4:previewBestPool:eth_call: eth_call RPC request failed:
       error sending request for url (https://robinhood-mainnet.g.alchemy.com/v2/alch_…)"

  ⇒ 路由**根本没有被定价**（不是链上执行失败、不是容量不足、不是利润判定失败）。
  ⇒ 诊断表的 174 条全是 evidence_missing / measured_signed_pnl_missing。
```

**链上发布了 5 个端点，当时 3 个健康**（`eth_blockNumber` 实测）：

| 端点 | 实测 |
|---|---|
| `https://rpc.ordofi.network` | **DNS 解析失败** |
| `https://robinhood-mainnet.g.alchemy.com/v2/alch_…` | **HTTP 429** |
| `https://rpc.mainnet.chain.robinhood.com` | **HTTP 200**（1,241 ms） |
| `https://robinhood-rpc.publicnode.com` | **HTTP 200** |
| `https://…quiknode.pro/…` | **HTTP 200** |

**根因（源码级）**：`worker_pool.rs::config_rpc_urls()`
只从 `config["rpcUrls"]` 取列表，**缺省退化为单个 `config["rpcUrl"]`**
⇒ 故障转移列表**只有 1 个元素** ⇒ **没有故障转移**。

而故障转移机制**早已就绪**：`run_preview_with_rpc_failover()` 与
`is_retryable_rpc_error()`（覆盖 `"error sending request"` / `"429"` /
`"too many requests"`）都已经在代码里。
**缺的不是机制，是「机制可以用的那份端点清单」。**

### 1.2 本任务据此裁定的边界（**用户口径**）

| 口径 | 落法 |
|---|---|
| **广播与复核线程都工作在 Rust 侧，Python 是遗留旧代码** | 端点解析放在 **Rust** 侧（`pool_manager::chain` ＋ `worker_pool.rs`），**Python 零改动** |
| **要么成功，要么失败，没有降级处理** | 故障转移**不是降级**：端点全集是「这条链可用节点」的真相，择其一成功即成功；**全部失败就是失败**（错误照旧上抛，不吞、不转成空结果） |
| **异常 / 低概率路径先留占位** | 本任务**不碰**重试次数、退避策略、超时预算、门禁与利润口径（**一字未改**），只把**可用端点补齐** |
| **优先把系统跑通** | 本任务是「跑通处理流程」的**直接前置**：没有定价就没有复核完成，就没有广播记录 |
| **动态费率 / 带 hook 的池子被排除** | 与本任务无关，本任务**一字未碰**该约定 |

### 1.3 不做什么（**范围外**）

* **不**改重试次数、退避、超时预算、广播门禁、利润口径（忽略费率、价格优先）。
* **不**改方向层 / 闭环层 / 候选层 / 广播层的任何准入、排序、容量口径。
* **不**改 `worker_pool.rs` 里**内核大对象**的部分（`T-核.02`，**另一线程**）。
* **不**把「端点全挂」翻译成「无利润」—— 全部端点失败**就是失败**（用户口径：无降级）。
* **不**做跨月统计、落盘滞后、失败降级等低概率路径（先留占位）。

---

## 2. 开发计划

### 2.1 改动模块（**这一列就是本任务的回归范围**）

| 模块 | 名称 | 代码落点 | 改动内容 |
|---|---|---|---|
| `M-1` | 池管理器 | `flashloan/tools/pool_manager/src/chain.rs` | **新增** `rpc_env_vars_for(chain_id)`（主网 / 测试网**分离**）、`rpc_urls_for_chain(chain_id)`；`rpc_urls_from_env()` 改为**委托**新增私有 `rpc_urls_from_env_vars(names)`（**既有行为不变**） |
| `M-13` | 运行时内核与接线 | `flashloan/rs-src/src/worker_pool.rs` | `config_rpc_urls(config, fallback_url, chain_urls)` —— 配置显式 `rpcUrls` **仍优先且不追加**；否则「配置端点在前 ＋ 链上端点在后」去重；**新增** `chain_rpc_urls(config)`（按 `config["chainId"]` 取，缺省 **4663**）；两处调用点（广播路径 / 复核路径）传入 `&chain_rpc_urls(config)` |

**改动落点（精确）**：

| 文件 | 行（2026-11-26 工作区） | 内容 |
|---|---|---|
| `pool_manager/src/chain.rs` | `318` | `rpc_urls_from_env()` 委托 `rpc_urls_from_env_vars(ROBINHOOD_RPC_ENV_VARS)` |
| `pool_manager/src/chain.rs` | `327-345` | `pub fn rpc_env_vars_for(chain_id) -> &'static [&'static str]` |
| `pool_manager/src/chain.rs` | `349-358` | `pub fn rpc_urls_for_chain(chain_id) -> Vec<String>` |
| `pool_manager/src/chain.rs` | `361-…` | `fn rpc_urls_from_env_vars(names: &[&str]) -> Vec<String>`（既有逻辑原样搬入） |
| `worker_pool.rs` | `2815-2846` | `fn config_rpc_urls(config, fallback_url, chain_urls) -> Vec<String>` |
| `worker_pool.rs` | `2848-2858` | `fn chain_rpc_urls(config) -> Vec<String>` |
| `worker_pool.rs` | `3311` | 广播路径调用点 |
| `worker_pool.rs` | `3808` | 复核路径调用点 |

### 2.2 执行步骤

| 步 | 动作 | 完成判据 | 状态 |
|---:|---|---|---|
| **1** | 只读探针：复核状态 ＋ 诊断 ＋ 端点实测（`eth_blockNumber`） | 读数可复算 | ✅ **完成** |
| **2** | 定位根因到**源码行** | 根因 = `config_rpc_urls` 只给 1 个端点 | ✅ **完成** |
| **3** | `chain.rs`：按链取回端点全集（主网 / 测试网**分离**） | 既有 `rpc_urls_from_env()` **行为不变** | ✅ **完成**（新增 2 条单测） |
| **4** | `worker_pool.rs`：`config_rpc_urls` 接受链上端点；显式 `rpcUrls` **仍优先且不追加** | 三条语义各有单测 | ✅ **完成**（新增 4 条单测） |
| **5** | 两个调用点接入（广播路径 ＋ 复核路径） | 两处都传 `&chain_rpc_urls(config)` | ✅ **完成** |
| **6** | 跑改动模块回归 | 全绿 | ✅ **完成**（见 §2.3） |
| **7** | 部署闸门：重建 Rust 扩展 ＋ 重启实盘 runtime ＋ 实盘复核读数 | 复核状态**离开** `data_incomplete` | 🔄 **进行中**（见 §4） |

### 2.3 回归（**只跑改动模块**）

| 模块 | 命令 | 读数（2026-11-26） |
|---|---|---|
| `M-1` | `cargo test --offline -p pool-manager` | **169 passed / 0 failed**（基线 **167** ＋ 新 **2**） |
| `M-13` | `cargo test --offline`（`flashloan/rs-src`） | **245 passed / 0 failed**（基线 **241** ＋ 新 **4**） |

```powershell
cd flashloan/tools
cargo test --offline -p pool-manager

cd ../rs-src
cargo test --offline
```

日志：`plan/runtime_evidence/_deploy_rpcfix_pm_tests.*.log`、`_deploy_rpcfix_rs_tests.*.log`。

### 2.4 技术文案（`docs/`）

* `D-183-复核定价路径的RPC故障转移_20261126.md` —— 根因链、端点实测、裁定、部署闸门与后果预告。

---

## 3. 完成定义

### 3.1 本任务（`T-核.04`）的完成定义

* [x] 根因定位到**源码行**，且有只读探针可复算
* [x] 端点解析在 **Rust** 侧完成（**Python 零改动**）
* [x] 主网 / 测试网端点变量**分离**（一条链的节点不回答另一条链的问题）
* [x] 配置显式 `rpcUrls` **仍优先且不追加**（运维的顺序就是运维的顺序）
* [x] 端点去重（同一个端点不试两次）
* [x] **未**改重试次数 / 退避 / 超时 / 门禁 / 利润口径
* [x] 改动模块回归全绿（`pool-manager` **169** ／ `flashloan-rs` **245**）
* [x] **未**触碰内核大对象（`T-核.02`，另一线程）
* [ ] **实盘读数**：复核状态离开 `data_incomplete`（部署闸门，见 §4）

### 3.2 后置项（**不属于本任务**，如实登记）

| 项 | 为什么后置 | 后果预告 |
|---|---|---|
| **端点健康度探测 / 自动摘除** | 用户口径：异常路径**先留占位**，最后再实现降级优雅 | 端点清单里可能有当下已坏的节点，故障转移会**试过**它再换下一个（多一次往返，不影响正确性） |
| **广播记录与复核记录的生命周期清理** | 低概率路径，先留占位 | 目录会持续增长 |
| **跨月统计（`ATTACH DATABASE`）** | 低概率路径，先留占位 | 跨月报表暂不可用 |

---

## 4. 部署闸门（**本轮正在执行**）

```text
① 等 T-核.02（内核大对象，另一线程）的 rs-src 改动收敛并回归
② cargo build --offline --release（flashloan/rs-src）⇒ 新的 flashloan_rs.dll
③ 备份旧 DLL（flashloan_rs.dll.bak-20261126）
④ 重启实盘 runtime（看护脚本接管，cwd = 仓库根，端口 5000）
⑤ 只读复核读数：
     manager_v4_reviews 的 review_status 分布
     manager_v4_review_diagnostics 的 diagnostic_code 分布
     review_record_jsonl/robinhood/4663/<日期>/*.jsonl 是否前进
⑥ 若复核出现 completed ⇒ 跟进广播与月度库落盘
     flashloan/db/broadcast_transaction_db/robinhood/4663/<月份>/trades.sqlite3
```

**实测读数（2026-11-26）**：

| 步 | 读数 |
|---|---|
| ② | `Finished `release` profile [optimized] target(s) in 2m 53s`；`flashloan_rs.dll` = **18,135,040 B**，mtime **2026-11-26 08:09:06** |
| ③ | `flashloan_rs.dll.bak-20261126` = **16,380,928 B**，mtime 2026-11-25 14:32:12 |
| ④ | 看护 pid **16052** ＋ runtime pid **15528**，起于 **08:10:06**，端口 5000 |
| ⑤ | **进行中** —— 见 `D-182` §4 |

