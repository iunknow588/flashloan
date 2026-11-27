# `T-核.05`｜流动性维护槽的 RPC 故障转移 · 任务归档（**已闭合**）

> **归档元信息**
>
> | 项 | 值 |
> |---|---|
> | 任务编号（纯 ID） | `T-核.05` |
> | 验收编号（纯 ID） | `—` |
> | 标题 | 流动性维护槽的 RPC 故障转移 |
> | 层级 | 二级（执行级） |
> | 归档日期 | 20261128 |
> | 原路径 | `plan/tasks/P-01-池子重构/tasks/T-核.05` |
> | 归档索引 | `plan/archive/README.md` §1 |
>
> **本件已冻结** —— 内容不再改动。更正走新版本 `..._v2.md`，旧版保留。

---

## ① 任务说明 ＋ ② 开发计划

<a id="d184"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.05/D-184-PLAN.md` -->

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

---

## ③ 修改范围守护

<a id="d185"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.05/D-185-SCOPE_GUARD.md` -->

# D-185 `T-核.05`｜流动性维护槽的 RPC 故障转移 · 修改范围守护（Scope Guard）

> **本文件是 `T-核.05` 的范围契约。** 违反本契约 ⇒ **本任务不算闭合**。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 改动内容 | 是否改 |
|---|---|---|---|---|
| `M-1` | 池管理器 | `flashloan/tools/pool_manager/src/lib.rs` | `DepthOptions` **新增** `rpc_urls` 字段；**新增** `resolve_depth_rpc_urls()` ＋ `push_endpoint()`；`refresh_depth()` 改用之 | **是** |
| `M-1` | 池管理器（命令行） | `flashloan/tools/pool_manager/src/main.rs` | `DepthOptions` 构造处补 `rpc_urls: None` | **是** |
| `M-13` | 运行时内核与接线 | `flashloan/rs-src/src/pool_depth.rs` | **新增** `liquidity_rpc_urls()` ＋ `push_endpoint()`；`refresh_pool_liquidity()` 按链组装清单 | **是** |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

> **并行线程的覆盖区（本任务一律不碰）**：
> `rs-src/src/worker_pool.rs`（**16** ／ **219–695** ／ **1384–1602** ／ **2119–2125** ／
> **2471–2490** ／ **3723–4143** ／ **4691–4758** ／ **6560+**）、
> `rs-src/src/runtime_core.rs`（**20** ／ **26** ／ **2911+**）、
> `tools/manager_api/src/lib.rs`（**5348+**）、
> `rs-src/src/review_publish.rs`（**1331+**）。
> 本任务的写入**只落**在 `pool_manager/src/lib.rs`（`DepthOptions` ＋ 新函数 ＋
> `refresh_depth()` 的端点解析）、`pool_manager/src/main.rs`（一处构造）
> 与 `rs-src/src/pool_depth.rs`（新函数 ＋ 一处组装）。

---

## 2. 逐模块回归命令

| 模块 | 名称 | 命令 | 预期 |
|---|---|---|---|
| `M-1` | 池管理器 | `cargo test --offline -p pool-manager` | 全绿（基线 **169** passed，本任务后 **172**） |
| `M-13` | 运行时内核与接线（Rust） | `cargo test --offline`（`flashloan/rs-src`） | 全绿（基线 **245** passed） |

```powershell
cd flashloan/tools
cargo test --offline -p pool-manager

cd ../rs-src
cargo test --offline
```

> **只跑上面这些。** 未改动模块**不重跑**。
> 日志：`plan/runtime_evidence/_deploy_rpcfix2_pm_tests.log`、
> `plan/runtime_evidence/_deploy_rpcfix2_rs_tests.log`。

---

## 3. 触碰边界（**禁止触碰**）

| 对象 | 禁止理由 |
|---|---|
| **`flashloan/src/**`（Python 侧）** | 用户口径：**广播与复核都在 Rust 侧，Python 是遗留旧代码** ⇒ 端点解析**全在 Rust** ⇒ 本任务 **Python 零改动**。遗留 Python 取 `urls[0]` 的行为**由 Rust 侧吸收**，不改它 |
| **重试次数 / 退避策略 / 超时预算** | 用户口径：异常路径**先留占位**；本任务只补**端点清单**，不改**重试策略** |
| **广播门禁 / 利润口径（忽略费率、价格优先）** | 核心目标口径，本任务**一字未改** |
| **动态费率 / 带 hook 池子的排除约定** | 池管理器现有约定，本任务**一字未改** |
| **方向层 / 闭环层 / 候选层 / 广播层的任何口径** | 选池条件、排序、容量**一字不改** |
| **`flashloan/tools/manager_db/**`** | 属 `M-10`；本任务**不涉及迁移** |
| **`worker_pool.rs` / `runtime_core.rs` / `review_publish.rs` / `manager_api`** | 属并行线程的覆盖区，本任务**零改动** |
| **`main.rs` 的命令行端点语义** | 操作员点名一个端点就是点名一个端点 ⇒ `--rpc-url` **仍然只试那一个**；运行时路径走的是**另一条**组装逻辑 |

---

## 4. 「要么成功，要么失败」的边界

本任务**不新增**任何兜底分支：

* 清单里所有端点都失败 ⇒ **仍然失败**，错误**原样上抛**（`read_v4_pool_states_with_fallback` 的既有行为）；
* **不新增**「跳过本轮」「用旧快照继续」「标记为部分成功」这类路径；
* 故障转移**不是降级** —— 端点清单是「这条链可用节点」的**真相**，
  择其一成功即成功；全部失败即**失败**。

---

## ④ 测试目标 check list

<a id="d186"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.05/D-186-checklist.md` -->

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

---

## 导航

<a id="readme"></a>

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.05/README.md` -->

# `T-核.05`｜流动性维护槽的 RPC 故障转移

> 任务编号 `T-<索引>.<序>` / `AC-<索引>.<序>` 是**纯 ID**，不表达功能关联。
> 范围与顺序只按**模块编号（`M-n`）**表达。总纲见 [`plan/README.md`](../../../../README.md)。

**编号**：`T-核.05`（纯 ID）　**验收**：`AC-核.05`　**优先级**：P0
**编号逻辑**：`T-核.05` / `AC-核.05` —— **模块内编号**，编号索引 **`核`**
（改动模块：`M-13`（运行时内核与运行时接线）、`M-1`（池管理器 / 链配置读取）），
该索引号段内第 `05` 个。

**改动模块（= 回归范围）**：`M-13`、`M-1`

**前置模块**：`M-7`　　**需授权**：否（只改代码；实盘生效属部署闸门）

## 文件

| 文件 | 内容 |
|---|---|
| `D-184` | 本任务的开发计划与回归范围（**自包含**） |
| `D-185` | 本任务的修改范围守护与逐模块回归命令 |
| `D-186` | 本任务的逐条验收框 |
| `docs/D-187` | 实测读数、根因链与裁定 |

## 一句话

> **同一条链上的两条读链入口，口径不一致**：复核定价路径有**端点故障转移**，
> 流动性刷新路径**只试一个端点**。一次 DNS 抖动 ⇒ 整个流动性维护槽失败
> ⇒ 快照表停更 ⇒ 方向层 / 闭环层拿到**陈旧**证据 ⇒ 复核层没有理由发布新一代。
> 本任务让深度刷新**按链**取回端点全集并沿用既有的故障转移，
> 于是「一个节点坏了」不再等于「一整个维护阶段停更」。

**当前状态（2026-11-26）**：**代码项全部闭合**（逐条读数见 `D-186`）；
回归 `flashloan-rs` **248 passed / 0 failed**（基线 **245** ＋ 新 **3**）、
`pool-manager` **172 passed / 0 failed**（基线 **169** ＋ 新 **3**）。

## 用户口径（本任务据此裁定的边界）

| 口径 | 落法 |
|---|---|
| **广播与复核线程都在 Rust 侧，Python 是遗留旧代码** | 端点组装放在 **Rust** 侧（`pool_manager` ＋ `rs-src/src/pool_depth.rs`），**Python 零改动** —— 遗留 Python 取 `urls[0]` 的行为**由 Rust 侧吸收** |
| **要么成功，要么失败，没有降级处理** | 故障转移**不是降级**：端点全集是「这条链可用节点」的真相，择其一成功即成功；**全部失败就是失败**（错误原样上抛，**未新增**任何兜底分支） |
| 异常 / 低概率路径**先留占位** | 本任务**不碰**重试次数、退避策略与超时预算（**一字未改**），只把**可用端点补齐** |
| 优先把系统**跑通** | 快照表停更 ⇒ 方向层 / 闭环层 / 复核层全部拿**陈旧**证据 ⇒ 这是「跑通处理流程」的**直接前置** |

## 实盘读数（只读 · 2026-11-26 09:0x）

`chain_business_initialization` ⇒ `v4_pool_liquidity_worker`
`status = error`、`refreshed_at = 2026-11-26T00:39:55Z`、
`error = "all V4 pool state RPC endpoints failed: … host lookup failed … (os error 11001)"`。

**错误串里只有一段端点信息** ⇒ 清单长度 = **1**（该函数用 `; ` 串每个失败端点）。
同期端点健康度实测 ⇒ **活 2 / 5**（publicnode **200** ／ QuickNode **200** ／
robinhood **403** ／ ordofi **DNS 死** ／ Alchemy **429**）
⇒ **不是没有可用节点，是没有去试**。

证据与裁定见 [`docs/D-187-…`](#d187)。

## 独立回归

```powershell
cd flashloan/tools ; cargo test --offline -p pool-manager
cd flashloan/rs-src ; cargo test --offline
```

---

## ⑤ 技术文案区（`docs/`）

> **原 `docs/` 目录已并入本归档件** —— 逐份保留，锚点见下。

<a id="d187"></a>

### `D-187-流动性维护槽的RPC故障转移_20261126.md`

<!-- 原文件：`plan/tasks/P-01-池子重构/tasks/T-核.05/docs/D-187-流动性维护槽的RPC故障转移_20261126.md` -->

# D-187 流动性维护槽的 RPC 故障转移（实测读数 · 根因链 · 裁定）

> **本文件是 `T-核.05` 的技术分析件。** 结论只建立在**实测读数**上；
> 推断的部分一律标成推断。
> 任务目录：[`T-核.05/`](../)；计划：[`D-184`](#d184)。

---

## 1. 一句话

**同一条链上的两条读链入口，口径不一致**：
复核定价路径有**端点故障转移**，流动性刷新路径**只试一个端点**。
一次 DNS 抖动 ⇒ 整个流动性维护槽失败 ⇒ 快照表停更 ⇒
方向层 / 闭环层拿到**陈旧**证据 ⇒ 复核层没有理由发布新一代。

---

## 2. 实盘读数（只读 · 主库 `mode=ro` ＋ `PRAGMA query_only = ON`）

### 2.1 失败的维护槽

`chain_business_initialization` ⇒ `v4_pool_liquidity_worker`：

| 字段 | 读数 |
|---|---|
| `status` | **`error`** |
| `refreshed_at` | **2026-11-26T00:39:55Z**（＝ 08:39:55 +08） |
| `error` | `all V4 pool state RPC endpoints failed: https://[REDACTED]: endpoint probe failed: host lookup failed: 不知道这样的主机。 (os error 11001)` |

### 2.2 「清单里只有 1 个端点」的**读数判据**

`pool_manager::chain::read_v4_pool_states_with_fallback()`（`chain.rs`）的失败分支：

```rust
Err(format!(
    "all V4 pool state RPC endpoints failed: {}",
    errors.join("; ")
))
```

`errors` 每失败一个端点 `push` 一条，**用 `; ` 相连**。
实测错误串里 **`; ` 一次都没有** ⇒ **清单长度 = 1**。

> **这是读数，不是推断**：错误串本身就是清单长度的证据。

### 2.3 端点健康度（`eth_blockNumber` · 2026-11-26 09:0x）

| 端点 | HTTP | 备注 |
|---|---|---|
| `robinhood-rpc.publicnode.com` | **200** | 1703 ms |
| `…quiknode.pro/…` | **200** | 843 ms |
| `rpc.mainnet.chain.robinhood.com` | **403** | Forbidden |
| `rpc.ordofi.network` | — | **DNS 解析失败**（`getaddrinfo failed`） |
| `robinhood-mainnet.g.alchemy.com` | **429** | Too Many Requests |

⇒ **活 2 / 5**。**不是**没有可用节点，是**没有去试**。

### 2.4 上游代次（为什么复核层没有前进）

| 表 | 字段 | 读数 |
|---|---|---|
| `manager_v4_closed_loop_library_state` | `closed_loop_version` | **22**（`generated_at` 00:04:33Z ／ `updated_at` 00:49:51Z） |
| `manager_review_library_state` | `closed_loop_version` | **21**（`review_version` **214**、`updated_at` **2026-11-25T23:07:30Z**） |
| `manager_v4_reviews` | `review_status` | `data_incomplete` **174** ／ `completed` **0** |

⇒ **闭环层已经前进一代，复核层还指着旧的闭环代次** ⇒ 复核层**没有发布**新一代。

---

## 3. 根因链（逐跳，每跳都有落点）

```
维护槽 v4_pool_liquidity_worker
  └─ robinhood_v4_idle_maintenance.py:72  _robinhood_rpc_url(network, chain_id)
       └─ v4_pool_rpc.py:23  _rpc_url_from_env_or_default()  →  return urls[0]   ← 单端点
            └─ core/rs_bridge.py:230  refresh_v4_pool_liquidity(rpc_url=<单个 URL>)
                 └─ rs-src/src/pool_depth.rs:58  DepthOptions.rpc_url = Some(url)
                      └─ pool_manager/src/lib.rs  refresh_depth()  →  vec![url]      ← 清单 = 1
                           └─ chain.rs  read_v4_pool_states_with_fallback()
                                └─ 机制在，但清单只有 1 个 ⇒ 无转移可做
```

**跨链缺陷（同一段代码）**：`refresh_depth()` 的兜底是
`chain::rpc_urls_for(chain_id)`，而 `rpc_urls_for()` 内部调
`rpc_urls_from_env()` —— **主网**的环境变量组，**与 chain_id 无关**。

---

## 4. 与「复核定价路径」的口径对照

| 读链入口 | 端点清单 | 故障转移 | 归属 |
|---|---|---|---|
| 复核定价（`previewBestPool`） | 配置端点在前 ＋ **链上端点在后** | **有**（`run_preview_with_rpc_failover`） | `T-核.04` |
| 广播定价 | 同上 | **有** | `T-核.04` |
| **流动性刷新（本任务）** | **只有一个** | **无** | **`T-核.05`** |
| 候选 YC/ZC ／ 市场观测 | 各自入口 | 各查各的 | 本任务**不代关** |

**裁定**：同一条链的读链入口**必须**有**同一套**端点解析口径。
故障转移**不是降级** —— 端点清单是「这条链可用节点」的**真相**，
择其一成功即成功；**全部失败就是失败**（错误原样上抛，不吞、不跳过、不用旧值顶替）。

---

## 5. 改法与「不改法」

### 5.1 改（`M-1` ＋ `M-13`）

1. `DepthOptions` **新增** `rpc_urls: Option<Vec<String>>`（有序清单）；
2. **新增**纯函数 `resolve_depth_rpc_urls()`：优先级 `rpc_urls` ＞ `rpc_url` ＞
   `chain::rpc_urls_for_chain(chain_id)`，**去重**、**保序**、**去空白**；
3. `refresh_depth()` 改用之 ⇒ 兜底**按链**取端点（**不再跨链**）；
4. `rs-src/src/pool_depth.rs` **新增**纯函数 `liquidity_rpc_urls()`：
   调用方命名的端点**在前** ＋ 链上端点**在后**。

### 5.2 不改（边界）

| 对象 | 理由 |
|---|---|
| `flashloan/src/**`（Python） | 用户口径：端点解析**全在 Rust**；遗留 Python 的 `urls[0]` 行为**由 Rust 侧吸收** |
| 命令行 `--rpc-url` 的语义 | 操作员点名一个端点就是点名一个端点 ⇒ 仍然**只试那一个** |
| 重试次数 / 退避 / 超时预算 | 异常路径**先留占位** |
| 广播门禁 / 利润口径 / 选池与排序口径 | 核心目标口径，**一字不改** |
| 端点健康度自动摘除 | 异常路径**先留占位** |

---

## 6. 遗留（**如实登记**）

| # | 遗留 | 归属 |
|---|---|---|
| 1 | 端点健康度探测与自动摘除（把死端点移出清单） | 留占位 |
| 2 | 候选 YC/ZC、市场观测等其余入口的端点口径 | 各自任务，**本任务不代关** |
| 3 | 复核层为何不发布新一代（闭环代次已到 22、复核仍指 21） | **待查**（本任务只解决「槽失败 ⇒ 快照停更」这一段） |
| 4 | 复核定价路径的**实盘读数**（离开 `data_incomplete`） | `T-核.04` 的部署闸门 |

---
