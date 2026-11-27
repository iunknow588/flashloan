# D-173 `T-广.02`｜Rust 侧广播记录贯通 · 修改范围守护（Scope Guard）

> **本文件是 `T-广.02` 的范围契约。** 违反本契约 ⇒ **本任务不算闭合**。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 改动内容 | 是否改 |
|---|---|---|---|---|
| `M-7` | 广播层 | `flashloan/tools/broadcast_manager/src/execution_record.rs`（**新增**） | 运行时回写入口：读复核行元数据 → 写 `manager_cow_execution_attempts` → 刷新广播库状态 → 触发月度归档 | **是** |
| `M-7` | 广播层 | `flashloan/tools/broadcast_manager/src/lib.rs` | `pub mod execution_record;` ＋ 导出；`BroadcastError::ReviewRowMissing` 变体 ＋ `Display`；**测试夹具可见性**（`database` / `database_with_record_root` / `seed_store` 三个夹具改为 `pub(crate)`，**只影响 `#[cfg(test)]` 模块**，产品代码零影响） | **是** |
| `M-13` | 运行时内核与接线 | `flashloan/rs-src/src/broadcast_record.rs`（**新增**） | 从 payload ＋ 广播结果取事实，组装 `RuntimeAttempt`，调用回写 | **是** |
| `M-13` | 运行时内核与接线 | `flashloan/rs-src/src/worker_pool.rs` | `run_execution_task` 之后写 `payload["broadcastRecord"]` ＋ 新增 `file_broadcast_record`（库路径取 `inner.database_path`，**缺省时回退** `active_runtime_database_path(chain_id)` —— 与既有三处调用点**同一口径**） | **是** |
| `M-13` | 运行时内核与接线 | `flashloan/rs-src/src/execution.rs` | 新增 `pub(crate) fn signer_address_from_private_key`（对既有内部函数的命名澄清） | **是** |
| `M-13` | 运行时内核与接线 | `flashloan/rs-src/src/lib.rs` | `mod broadcast_record;` | **是** |
| `M-12` | 接口层 | `flashloan/tools/manager_api/src/lib.rs` | **仅**：`BroadcastError` 穷尽 `match` 补 `ReviewRowMissing { .. }` 一个分支（编译期要求） | **是** |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

### 1.1 与并行线程的**改动区**的关系（**零冲突证明**）

另一线程正在改 `rs-src/src/worker_pool.rs`（**+921** 行）与 `rs-src/src/runtime_core.rs`（**+119** 行）。
本任务在这两个文件里的落点**必须**落在它们的 diff 覆盖区**之外**：

| 落点 | 现行行号 | 并行线程 diff 覆盖区（现行坐标） | 是否冲突 |
|---|---|---|---|
| `worker_pool.rs` → `run_execution_task` | **4432** | 覆盖区在 `4143` 与 `4691` 之间 ⇒ **4432 在区外** | **否** |
| `worker_pool.rs` → `file_broadcast_record`（新增，紧随其后） | **4486** 附近 | 同上 | **否** |
| `worker_pool.rs` → `use` 区 | 文件头 | `worker_pool.rs` 的 diff 覆盖区起于 **16**（`use` 行）⇒ **同一区** | ⚠️ 见 §1.2 |
| `runtime_core.rs` → `map_broadcast_command_error` | **2883** | diff 覆盖区为 **20 / 26**（`use` 行）＋ **2911** 起的新增块 ⇒ **2883 在区外** | **否** |
| `manager_api/src/lib.rs` → `BroadcastError` 穷尽 match | **3019** | diff 覆盖区 **5348** 起（测试模块）⇒ **3019 在区外** | **否** |

#### 1.1.1 `use` 区的冲突回避

并行线程在 `worker_pool.rs` 的 **16** 行加了 `use`。本任务**不在** `worker_pool.rs` 加 `use`：
新模块 `crate::broadcast_record::…` 用**全路径**调用 ⇒ `worker_pool.rs` 的 `use` 区**一字未动** ⇒ 冲突面为零。
`execution_record.rs`（新文件）与 `broadcast_record.rs`（新文件）是**纯新增**，不可能与并行线程冲突。

### 1.2 **不**在本任务范围内（明令）

| 不动的东西 | 为什么 |
|---|---|
| `worker_pool.rs` 的内核对象部分（`V4PoolRegistry` / `ContextCache` / `run_native_review`） | 属 `T-核.02`（内存大对象）线程，本任务**不碰** |
| `worker_pool.rs` 的 `use` 区 | 避免与并行线程在同一处落笔（§1.1.1） |
| `review_manager` 的**发布**逻辑（`replace_review_rows` 的 `DELETE` ＋ `INSERT`） | 改它就是改**准入**语义（`BC-1` 的新鲜度 / 请求行生命周期），属另一层，单独立项（`D-175` §6） |
| `manager_v4_review_broadcast_requests` 的写入方 | 同上；本任务的回写**不写**这张表 |
| 任何**准入门禁**（`BC-1` / `BC-2` / 内核四道门禁） | 用户口径：默认放开，只看正向利润；本任务**一字未改** |
| Python 侧任何文件 | 用户口径：广播与复核都在 Rust 侧，Python 是遗留旧代码 |
| 迁移 **018** / 实盘库 / 实盘重启 | 部署闸门，与 `T-闭.03` / `T-核.02` / `T-核.03` 同窗口 |

---

## 2. 逐模块回归命令

| 模块 | 名称 | 命令 | 预期 |
|---|---|---|---|
| `M-7` | 广播层 | `cargo test --offline -p broadcast-manager` | 全绿（**40** passed ＝ 基线 **35** ＋ W1 的 **3** ＋ 本任务 **5**） |
| `M-13` | 运行时内核与接线 | `cargo test --offline`（`flashloan/rs-src`） | 全绿（**230** passed ＝ 基线 **222** ＋ 本任务 **8**） |
| `M-12` | 接口层 | `cargo test --offline -p manager-api` | 全绿（**33** passed） |

```powershell
cd flashloan/tools
cargo test --offline -p broadcast-manager
cargo test --offline -p manager-api

cd ../rs-src
cargo test --offline
```

## 3. 「编译 ≠ 测试」的适用

本任务**改了两个 crate 的公开 API 表面**（`BroadcastError` 加变体、`RuntimeAttempt` 新类型）⇒
`cargo build --offline --workspace`（`flashloan/tools`）与 `cargo check --offline`（`flashloan/rs-src`）
足以暴露**类型破坏**；但**新增的行为**（回写、月度归档）**必须**跑单测 ⇒ 上表三条**都要跑**。

## 4. 实盘写入

本任务**零实盘写入**。所有读数来自只读探针（`mode=ro` ＋ `PRAGMA query_only = ON`）与只读 HTTP。
回写的**第一次真实写入**发生在部署闸门之后（`D-172` §6）。
