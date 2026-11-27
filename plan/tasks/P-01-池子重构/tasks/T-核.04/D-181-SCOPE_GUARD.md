# `T-核.04`｜复核定价路径的 RPC 故障转移 · 修改范围守护（Scope Guard）

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

1. **先扩范围** —— 在本文件 §1 增列该模块，同步改 `D-180-PLAN.md` §2.1；
2. **记录原因** —— 在 `D-180-PLAN.md` §2.2 追加一行说明为什么必须扩；
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

