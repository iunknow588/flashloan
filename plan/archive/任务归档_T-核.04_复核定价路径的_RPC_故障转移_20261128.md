# `T-核.04`｜复核定价路径的 RPC 故障转移 · 任务归档（**已闭合**）

> **归档元信息**
>
> | 项 | 值 |
> |---|---|
> | 任务编号（纯 ID） | `T-核.04` |
> | 验收编号（纯 ID） | `—` |
> | 标题 | 复核定价路径的 RPC 故障转移 |
> | 层级 | 二级（执行级） |
> | 归档日期 | 20261128 |
> | 原路径 | `plan/tasks/P-01-池子重构/tasks/T-核.04` |
> | 归档索引 | `plan/archive/README.md` §1 |
>
> **本件已冻结** —— 内容不再改动。更正走新版本 `..._v2.md`，旧版保留。

---

## ① 任务说明 ＋ ② 开发计划

<a id="d180"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.04/D-180-PLAN.md` -->

# D-180 `T-核.04`｜复核定价路径的 RPC 故障转移 · 开发计划（**独立 · 自包含**）

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

* `D-183` —— 根因链、端点实测、裁定、部署闸门与后果预告。

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

---

## ③ 修改范围守护

<a id="d181"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.04/D-181-SCOPE_GUARD.md` -->

# D-181 `T-核.04`｜复核定价路径的 RPC 故障转移 · 修改范围守护（Scope Guard）

> **本文件是 `T-核.04` 的范围契约。** 违反本契约 ⇒ **本任务不算闭合**。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 改动内容 | 是否改 |
|---|---|---|---|---|
| `M-1` | 池管理器 | `flashloan/tools/pool_manager/src/chain.rs` | **新增** `rpc_env_vars_for(chain_id)`、`rpc_urls_for_chain(chain_id)`、私有 `rpc_urls_from_env_vars(names)`；`rpc_urls_from_env()` 改为委托 | **是** |
| `M-13` | 运行时内核与接线 | `flashloan/rs-src/src/worker_pool.rs` | `config_rpc_urls(config, fallback_url, chain_urls)` ＋ **新增** `chain_rpc_urls(config)`；两个调用点接入 | **是** |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

> **注**：`worker_pool.rs` 是**并行线程**也在改的文件。本任务的改动**只落**在
> `2815-2858`（两个新函数）、`3311`（广播调用点）、`3808`（复核调用点）
> 与 `9096-9182`（本任务的 4 条单测）四段，
> **未**触碰该文件的其它区段（内核大对象属 `T-核.02`）。

---

## 2. 逐模块回归命令

| 模块 | 名称 | 命令 | 预期 |
|---|---|---|---|
| `M-1` | 池管理器 | `cargo test --offline -p pool-manager` | 全绿（基线 **167** passed，本任务后 **169**） |
| `M-13` | 运行时内核与接线（Rust） | `cargo test --offline`（`flashloan/rs-src`） | 全绿（基线 **241** passed，本任务后 **245**） |

```powershell
cd flashloan/tools
cargo test --offline -p pool-manager

cd ../rs-src
cargo test --offline
```

> **只跑上面这些。** 未改动模块**不重跑**。
> 日志：`plan/runtime_evidence/_deploy_rpcfix_pm_tests.*.log`、`_deploy_rpcfix_rs_tests.*.log`。

---

## 3. 触碰边界（**禁止触碰**）

| 对象 | 禁止理由 |
|---|---|
| **重试次数 / 退避策略 / 超时预算** | 用户口径：异常路径**先留占位**；本任务只补**端点清单**，不改**重试策略** |
| **广播门禁 / 利润口径（忽略费率、价格优先）** | 核心目标口径，本任务**一字未改** |
| **动态费率 / 带 hook 池子的排除约定** | 池管理器现有约定，本任务**一字未改** |
| `worker_pool.rs` 的**内核大对象**区段 | 属 `T-核.02`，**另一线程**在改 |
| `rs-src/src/runtime_core.rs` | 同上 |
| `flashloan/src/**`（Python 侧） | 用户口径：**广播与复核都在 Rust 侧，Python 是遗留旧代码** ⇒ 本任务 **Python 零改动** |
| 方向层 / 闭环层 / 候选层 / 广播层的任何口径 | 选池条件、排序、容量**一字不改** |
| `flashloan/tools/manager_db/**` | 属 `M-10`；本任务**不涉及迁移** |

---

## 4. 范围变更流程（**先扩后跑**）

1. **先扩范围** —— 在本文件 §1 增列该模块，同步改 `D-180` §2.1；
2. **记录原因** —— 在 `D-180` §2.2 追加一行说明为什么必须扩；
3. **后施工**；
4. **后重跑** —— **只**跑扩范围后模块的测试。

---

## 5. 授权边界

| 动作 | 需要授权？ | 说明 |
|---|---|---|
| 改 `M-13` / `M-1` 代码 | 否 | 纯代码 |
| 跑改动模块的测试 | 否 | 测试库，**不碰实盘** |
| 重建 Rust 扩展（`cargo build --release`） | **是** | 部署闸门；用户口径「所有需授权操作默认授权通过」 |
| 重启实盘 runtime | **是** | 同上 |
| 实盘只读复核 | 否 | `mode=ro` ＋ `PRAGMA query_only = ON` |

---

## 6. 范围守护自检

* [x] 本次改动**全部**落在 §1 的模块集合内
      ⇒ 读数：改动文件 = `flashloan/tools/pool_manager/src/chain.rs`、
      `flashloan/rs-src/src/worker_pool.rs` ⇒ **二者都在 §1 表内**；**未**扩范围
* [x] §2 的**全部**命令**已执行**且**全绿**
      ⇒ 读数：`pool-manager` **169 passed / 0 failed**（基线 **167** ＋ 新 **2**，
      **无 warning**）；`flashloan-rs` **245 passed / 0 failed**（基线 **241** ＋ 新 **4**）
* [x] §3 的禁止触碰项**确未被本任务触碰**
      ⇒ 读数：**本任务**的写入只有三个文件 ——
      `flashloan/tools/pool_manager/src/chain.rs`（Rust）、
      `flashloan/rs-src/src/worker_pool.rs`（Rust）、
      `plan/runtime_evidence/tools/probe_review_status_rpcfix_20261126.py` ＋
      `watch_review_after_rpcfix_20261126.py`（**只读探针**，不参与运行）；
      **Python 运行时代码零改动**；重试 / 退避 / 超时 / 门禁 / 利润口径**零改动**；
      内核大对象区段**零改动**
* [x] **未**把「端点全挂」翻译成「无利润」
      ⇒ 读数：全部端点失败**照旧上抛**（错误字符串原样进 `static_preview_json`），
      **未**新增任何兜底分支、**未**新增「跳过本轮」路径
* [x] **未**跑**全量**测试
      ⇒ 读数：只跑了 §2 的两条命令

---

## 7. 部署闸门的边界（**实盘**）

| 动作 | 状态 | 读数 |
|---|---|---|
| 备份旧 DLL | ✅ 完成 | `flashloan_rs.dll.bak-20261126` = 16,380,928 B（2026-11-25 14:32:12） |
| 重建 release | ✅ 完成 | `Finished `release` profile in 2m 53s`；新 DLL = 18,135,040 B（08:09:06） |
| 重启 runtime | ✅ 完成 | 看护 **16052** ＋ runtime **15528**，起于 08:10:06 |
| 实盘复核读数 | 🔄 进行中 | 见 `D-182` §4 |

---

## ④ 测试目标 check list

<a id="d182"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.04/D-182-checklist.md` -->

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

---

## 导航

<a id="readme"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.04/README.md` -->

# `T-核.04`｜复核定价路径的 RPC 故障转移

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../../../README.md)。

**编号**：`T-核.04`（纯 ID）　**验收**：`AC-核.04`　**优先级**：P0
**编号逻辑**：`T-核.04` / `AC-核.04` —— **模块内编号**，编号索引 **`核`**
（改动模块：`M-13`（运行时内核与运行时接线）、`M-1`（池管理器 / 链配置读取）），
该索引号段内第 `04` 个。

**改动模块（= 回归范围）**：`M-13`、`M-1`

**前置模块**：`M-7`　　**需授权**：否（只改代码；实盘生效属部署闸门）

## 文件

| 文件 | 内容 |
|---|---|
| `D-180` | 本任务的开发计划与回归范围（**自包含**） |
| `D-181` | 本任务的修改范围守护与逐模块回归命令 |
| `D-182` | 本任务的逐条验收框 |
| `docs/D-183` | 实测读数、根因链与裁定 |

## 一句话

> 实盘 **174** 条复核**全部**是 `data_incomplete`（`signed PnL is missing or incomplete`），
> 根因**不是**链上执行、也不是容量，而是**路由根本没有被定价**：
> `official_v4:previewBestPool:eth_call` 请求失败，而**定价路径只用一个 RPC 端点**。
> 链上发布了 **5** 个端点，其中 **3** 个当时健康，可定价路径一个都没试。
> 本任务让定价路径与广播路径**按链取回端点全集**并沿用既有的故障转移，
> 于是「一个节点坏了」不再等于「一条路由没测过」。

**当前状态（2026-11-26）**：**代码项全部闭合**（逐条读数见 `D-182`）；
回归 `flashloan-rs` **245 passed / 0 failed**（基线 **241** ＋ 新 **4**）、
`pool-manager` **169 passed / 0 failed**（含新 **2**）。

## 用户口径（本任务据此裁定的边界）

| 口径 | 落法 |
|---|---|
| **广播与复核线程都在 Rust 侧，Python 是遗留旧代码** | 端点解析放在 **Rust** 侧（`pool_manager::chain` ＋ `worker_pool.rs`），**Python 零改动** |
| **要么成功，要么失败，没有降级处理** | 故障转移**不是降级**：端点全集是「这条链可用节点」的真相，择其一成功即成功；**全部失败就是失败**（错误照旧上抛） |
| 异常 / 低概率路径**先留占位** | 本任务**不碰**重试次数、退避策略与超时预算（**一字未改**），只把**可用端点补齐** |
| 优先把系统**跑通** | 本任务是「跑通处理流程」的**直接前置**：没有定价就没有复核完成，就没有广播记录 |

## 实盘读数（只读 · 2026-11-26 07:0x–07:5x）

主库 `flashloan/db/local_store_4663.sqlite3`（`mode=ro`）⇒ `manager_v4_reviews`
`data_incomplete` **174** / `completed` **0**；
`manager_v4_review_diagnostics` ⇒ `evidence_missing` / `measured_signed_pnl_missing` **174**；
诊断 `payload_json.error` ⇒ `official_v4:previewBestPool:eth_call: eth_call RPC request failed: error sending request for url (…)`，
`errorKind = rpc_verification_failed`，`status = route_error`。

`.env` 的 `ROBINHOOD_RPCS` **5** 个端点实测（`eth_blockNumber`）⇒
`rpc.ordofi.network` **DNS 解析失败**、`robinhood-mainnet.g.alchemy.com` **HTTP 429**，
其余 **3** 个（`rpc.mainnet.chain.robinhood.com`、`robinhood-rpc.publicnode.com`、
QuickNode）**HTTP 200**。

证据与裁定见 [`docs/D-183-…`](#d183)。

## 独立回归

```powershell
cd flashloan/tools ; cargo test --offline -p pool-manager
cd flashloan/rs-src ; cargo test --offline
```

---

## ⑤ 技术文案区（`docs/`）

> **原 `docs/` 目录已并入本归档件** —— 逐份保留，锚点见下。

<a id="d183"></a>

### `D-183-复核定价路径的RPC故障转移_20261126.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.04/docs/D-183-复核定价路径的RPC故障转移_20261126.md` -->

# D-183 复核定价路径的 RPC 故障转移（2026-11-26）

> 本文是 `T-核.04` 的独立技术文案：**根因链**、**实测读数**、**裁定**、**闸门**。
> 读数可用 `plan/runtime_evidence/tools/probe_rpc_endpoints_20261126.py`、
> `probe_review_status_rpcfix_20261126.py`、
> `watch_review_after_rpcfix_20261126.py` 复算。

---

## 1. 一句话

**实盘 174 条复核全部是「没定价」，不是「没利润」。**
定价路径只拿到 **1 个** RPC 端点，那个端点当时 **429**，
于是「一个节点坏了」被记成了「一条路由没测过」。
链上发布 **5** 个端点，当时 **3** 个健康 —— **一个都没试**。

---

## 2. 根因链

```text
复核（Rust worker_pool::run_native_review）
  → config_rpc_urls(&config, &rpc_url, …)          ← 根因在这里
      · config["rpcUrls"] 有多值 ⇒ 用它
      · 否则 ⇒ **退化成一个** config["rpcUrl"]        ← 列表长度 = 1
  → run_preview_with_rpc_failover(&rpc_urls)         ← 机制早已就绪
  → is_retryable_rpc_error(...)                      ← 覆盖 429 / error sending request
  ⇒ 列表只有 1 个 ⇒ **无处可转** ⇒ 一个节点坏 = 整条路由失败
```

**关键区分**：故障转移机制**不缺**，缺的是**可以转的那份清单**。
机制在代码里躺了很久，只因为清单一直是 1 个元素而从未被用上。

### 2.1 端点从哪来

| 来源 | 现状 |
|---|---|
| `config["rpcUrls"]` | 任务配置里**没有**这个键 ⇒ 走缺省分支 |
| `config["rpcUrl"]` | 来自 `.env` 的 `ROBINHOOD_ALCHEMY_RPC_URL` ⇒ **就是那个 429 的端点** |
| `pool_manager::chain::rpc_urls_from_env()` | **能读到 5 个**，但定价路径**从来没问过它** |

⇒ **链上发布了端点清单，池管理器早就读得到，定价路径没接。**

---

## 3. 实测（只读）

### 3.1 端点健康度（`eth_blockNumber`，2026-11-26 08:28）

证据：`plan/runtime_evidence/rpc_endpoints_mainnet_20261126_0828.json`、
`rpc_endpoints_robinhood_rpcs_20261126_0828.json`
（探针 `probe_rpc_endpoints_20261126.py`）

| 端点 | HTTP | 区块 | 活 |
|---|---:|---|:-:|
| `https://robinhood-rpc.publicnode.com` | **200** | `0x75a50c4` | ✅ |
| `https://blissful-billowing-pine.robinh…`（QuickNode） | **200** | `0x75a50c9` | ✅ |
| `https://rpc.mainnet.chain.robinhood.com` | **403** Forbidden | — | ❌ |
| `https://rpc.ordofi.network` | — | — | ❌ **DNS 解析失败** |
| `https://robinhood-mainnet.g.alchemy.com/v2/alch_…` | **429** | — | ❌ **Too Many Requests** |

⇒ **5 个端点，2 个活着**。而失败的那条复核，错就错在**它只试了那 1 个 429 的端点**。

> 早前 07:0x 的实测里 `rpc.mainnet.chain.robinhood.com` 还能 200，
> 08:28 变成 **403** —— **端点健康度本身就在变**。
> 这正是「必须按链取全集、必须能转」的**根本理由**：
> 任何**写死一个端点**的实现，都会在某个时刻整体失明。

### 3.2 复核侧的失败形状（主库 `mode=ro` ＋ `query_only`）

| 读数 | 值 |
|---|---|
| `manager_v4_reviews` 的 `review_status` | `data_incomplete` **174** ／ `completed` **0** |
| `review_reason` | `"signed PnL is missing or incomplete"` |
| `run_arbitrage_json` | `{"blockNumber":null,"diagnostics":[],"executable":false,"ok":false,"signedPnl":null}` |
| `static_preview_json.bestAmountMissingReason` | `"official_v4:previewBestPool:eth_call: eth_call RPC request failed: error sending request for url (…alchemy…)"` |
| `manager_v4_review_diagnostics` | `diagnostic_code`：**`EvidenceMissing` 174** ＋ **`measured_signed_pnl_missing` 174** |

⇒ `blockNumber` 为 `null` ⇒ **连区块都没取到** ⇒ 路由**从未进入定价**。
⇒ 这**不是**链上执行失败、**不是**容量不足、**不是**利润判定失败、**不是**动态费率或 hook 的排除。

---

## 4. 裁定

### 4.1 改什么

| 改 | 落点 |
|---|---|
| **按链取回端点全集**（主网 / 测试网**分离**） | `pool_manager::chain::rpc_urls_for_chain(chain_id)` |
| **配置端点在前，链上端点在后，去重** | `worker_pool::config_rpc_urls(config, fallback_url, chain_urls)` |
| **两个调用点都接入** | 广播路径 `worker_pool.rs:3311`、复核路径 `worker_pool.rs:3808` |

### 4.2 为什么不改什么

| 不改 | 理由 |
|---|---|
| 重试次数 / 退避 / 超时预算 | 用户口径：异常路径**先留占位**，最后再实现降级优雅；本任务只补**清单** |
| 广播门禁 / 利润口径（忽略费率、价格优先） | 核心目标口径 |
| 动态费率 / 带 hook 池子的排除 | 池管理器现有约定 |
| 方向层 / 闭环层 / 候选层 / 广播层口径 | 与本任务无关 |
| Python 侧 | 用户口径：**广播与复核都在 Rust 侧**，Python 是遗留旧代码 |
| `worker_pool.rs` 的内核大对象 | 属 `T-核.02`，**另一线程**在改 |

### 4.3 「故障转移」不是「降级」

用户口径：**要么成功，要么失败，没有降级处理。**

端点全集是「这条链**可用节点**」的**真相**，不是「备用方案」。
择其一成功即**成功**；全部失败即**失败**（错误**原样上抛**，不吞、不转成空结果）。
本任务**未新增**任何兜底分支，也**未**新增「跳过本轮」路径。

---

## 5. 边界与后果预告

### 5.1 本任务解决的是什么

> **一个节点坏了，不再等于一条路由没测过。**

定价路径与广播路径从此**按链取回端点全集**，任一端点健康即可完成定价。
实测清单里 **2 / 5** 活着 ⇒ 定价有**两台**可用的机器，而不是**半台**。

### 5.2 本任务**没有**解决什么（**留占位**）

| 项 | 后果 |
|---|---|
| **端点健康度探测 / 自动摘除** | 清单里可能有当下已坏的节点 ⇒ 故障转移会**先试它再换下一个**（多一次往返，**不影响正确性**） |
| **记录生命周期清理** | 复核 JSONL 与月度交易库会持续增长 |
| **跨月统计（`ATTACH DATABASE`）** | 跨月报表暂不可用 |

### 5.3 对「跑通处理流程」的意义

用户口径的核心目标是**产生持续稳定的套利广播记录**。
当前链路上：

```text
复核定价（❌ 全挂）→ 复核完成（0）→ 正向利润（0）→ 广播（0）→ 广播记录（0）
```

定价是这条链的**第一环**。第一环不通，后面每一环的读数都是
「没有机会」而不是「有机会但没成交」。
⇒ 本任务是**跑通**的前置条件，不是优化项。

---

## 6. 部署闸门读数

| 步 | 读数 |
|---|---|
| 备份旧 DLL | `flashloan_rs.dll.bak-20261126` = **16,380,928 B**（2026-11-25 14:32:12） |
| 重建 release | `Finished `release` profile [optimized] target(s) in 2m 53s`；新 DLL = **18,135,040 B**（08:09:06） |
| 重启 runtime | 看护 **16052** ＋ runtime **15528**，起于 **08:10:06**，端口 5000 |
| 流水线节奏（实测） | 旧实例 **07:44:31** 启动 ⇒ 池流动性 **07:50** ／ 候选 **07:53** ／ 方向层 **08:04** ／ 闭环 **08:05** ⇒ **启动到复核落地约 21 分钟** |
| 实盘复核读数 | **进行中** ⇒ 见 `D-182` §4 与 `plan/runtime_evidence/logs/t401_runtime_sup/watch_review_rpcfix*_20261126.log` |

---
