# `T-核.05`｜流动性维护槽的 RPC 故障转移 · 修改范围守护（Scope Guard）

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

