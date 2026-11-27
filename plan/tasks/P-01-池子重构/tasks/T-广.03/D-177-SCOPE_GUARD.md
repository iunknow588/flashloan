# `T-广.03`｜广播链路 Rust 归位 · 修改范围守护（Scope Guard）

> **本文件是 `T-广.03` 的范围契约。** 违反本契约 ⇒ **本任务不算闭合**。

---

## 1. 改动模块集合（**= 回归范围**）

| 模块 | 名称 | 代码落点 | 改动内容 | 是否改 |
|---|---|---|---|---|
| `M-13` | 运行时内核与接线 | `flashloan/rs-src/src/broadcast_policy.rs`（**新增**） | 广播准入：签名私钥解析（任务配置 → 环境变量候选链）、授权开关（**默认放开**，显式 `false` 才关闭）、`normalize_config()` 在收单边界判定一次并写回 | **是** |
| `M-13` | 运行时内核与接线 | `flashloan/rs-src/src/lib.rs` | `mod broadcast_policy;` | **是** |
| `M-13` | 运行时内核与接线 | `flashloan/rs-src/src/worker_pool.rs` → `run_native_review` | **删除**复核线程内的直接广播分支；改写 `broadcastDeferred` / `executionQueueRequested` / `selectedRpcUrl`；队列不可用 ⇒ 显式 `broadcastSkipped` ＋ `broadcastQueueUnavailable`；停机中的组不向队列交接 | **是** |
| `M-13` | 运行时内核与接线 | `flashloan/rs-src/src/worker_pool.rs` → `submit_inner` | 收单边界调用 `normalize_config()` 归一化任务配置；提交应答带 `broadcastAdmission` | **是** |

**改动模块集合 = 回归范围。** 未列入本表的模块**一律不跑测试**。

### 1.1 与并行线程的**改动区**的关系（**零冲突证明**）

另一线程正在改 `rs-src/src/worker_pool.rs`（**+921** 行）与 `rs-src/src/runtime_core.rs`（**+119** 行）。
本任务在这两个文件里的落点**必须**落在它们的 diff 覆盖区**之外**：

| 落点 | 现行行号 | 并行线程 diff 覆盖区（现行坐标） | 是否冲突 |
|---|---|---|---|
| `worker_pool.rs` → `run_native_review` 的广播分支 | **4289** 附近 | 覆盖区 **3723–4143**、**4691–4758** ⇒ **4289 在区外** | **否** |
| `worker_pool.rs` → `submit_inner` 归一化 | **5420** 附近 | 覆盖区 **6560** 起 ⇒ **5420 在区外** | **否** |
| `worker_pool.rs` → 新增单测 | **8050** 附近 | 覆盖区 **6560** 起 ⇒ 在区内？—— 本任务**只改**既有测试块之间的**新增测试**，不改既有测试 | ⚠️ 见 §1.1.1 |
| `worker_pool.rs` → `use` 区 | 文件头 | diff 覆盖区起于 **16**（`use` 行）⇒ **同一区** | ⚠️ 见 §1.1.1 |
| `rs-src/src/lib.rs` → `mod broadcast_policy;` | **6** | 该文件不在并行线程的改动清单内 | **否** |
| `rs-src/src/broadcast_policy.rs` | 新文件 | **纯新增** | **否** |

#### 1.1.1 `use` 区与测试区的冲突回避

并行线程在 `worker_pool.rs` 的 **16** 行加了 `use`。本任务**不在** `worker_pool.rs` 加 `use`：
新模块用**全路径**调用 `crate::broadcast_policy::…` ⇒ `worker_pool.rs` 的 `use` 区**一字未动** ⇒ 冲突面为零。
本任务在测试模块里**只新增**测试函数、**不改**任何既有测试函数的正文 ⇒ 既有测试块不动。

### 1.2 **不**在本任务范围内（明令）

| 不动的东西 | 为什么 |
|---|---|
| `worker_pool.rs` 的内核对象部分（`V4PoolRegistry` / `ContextCache`） | 属 `T-核.02`（内存大对象）线程，本任务**不碰** |
| `worker_pool.rs` 的 `use` 区 | 避免与并行线程在同一处落笔（§1.1.1） |
| `broadcast_manager`（`M-7`）的队列结构、唤醒语义 | **已就位**（复核入队唤醒、手工执行唤醒、广播工作线程消费），本任务**不改动** |
| 广播层**准入门禁**（`BC-1` / `BC-2`）与内核四道门禁（gas / 容量 / 经济下限 / 跳价） | 用户口径：默认放开，但**必须确认复核正向利润** ⇒ 四道门禁**一字未改**，本任务只改「谁有资格进入」 |
| `manager_v4_reviews` 的**发布**逻辑与行生命周期 | 属复核层，另一任务范围；本任务**只读**该表 |
| Python 侧任何文件 | 用户口径：广播与复核都在 Rust 侧，Python 是遗留旧代码；本任务**Python 零改动** |
| `runArbitrage ok=false` 的链上根因（实盘 `not_executable` **149** 条主因） | 属链上执行 / 容量侧；本任务**只负责把裁决交到对的人手里**，不代为关闭链上根因 |
| 迁移 **018** / 实盘库 / 实盘重启 | 部署闸门，与 `T-闭.02` / `T-闭.03` / `T-核.02` / `T-核.03` 同窗口、**无中间态** |

---

## 2. 逐模块回归命令

| 模块 | 名称 | 命令 | 预期 |
|---|---|---|---|
| `M-13` | 运行时内核与接线 | `cargo test --offline`（`flashloan/rs-src`） | 全绿（**241** passed ＝ 基线 **230** ＋ 本任务 **11**） |

```powershell
cd flashloan/rs-src
cargo test --offline
```

### 2.1 旁证回归（**不在**本任务的改动模块集合内，只作旁证）

| 命令 | 读数 | 结论 |
|---|---|---|
| `cargo test --offline -p broadcast-manager`（`flashloan/tools`） | **40 passed / 0 failed** | 与 `T-广.02` 同读数 ⇒ 本任务**未触碰**广播层 |
| `cargo test --offline -p manager-api`（`flashloan/tools`） | **33 passed / 0 failed** | 同上 |

