# D-124 `T-方.01`｜方向层投影不变式守卫 · 测试目标 check list

**验收前置**：无（`M-3` 方向层已落地：`T-方授储.01` 已闭合）。

> **勾选纪律**：**每一条都必须有可复算的实测读数**；未实测项写「**未测量**」，**不得**用零冒充。
> 全部读数的出处见 [`docs/D-125-方向层投影不变式守卫_实施与实测报告_20261125.md`](./docs/D-125-方向层投影不变式守卫_实施与实测报告_20261125.md)。

---

#### 结构验收

- [x] **缺口已定位**：`compare_directional_pools` 只把**同一基础池的两个方向行**互相比较，

      **从不与基础池表**（scope 的 `pool_table`）比较 ⇒ **停止条件 I-9 没有任何检测器**

      （`base_pool_count` 早就在被读，但**只用来报数，从不参与判断**）。

- [x] **比较函数已有测试**（此前 `compare_directional_pools` **零测试**）

- [x] `DirectionalPoolComparison` 已**新增**四个字段（**增量** ⇒ 既有字段语义不变）：

      `base_pool_mismatches` ／ `rows_without_base_pool` ／ `projected_base_pool_count` ／
      `base_pool_mismatch_samples`

- [x] `ready_for_cutover` 已纳入两个新计数（**只加严**，见 `D-123` §5）

- [x] 回归范围已按「跨 crate 公开接口改动 ⇒ 加跑调用方」执行：

      `cargo test --offline -p directional-pool-manager` ⇒ **25 passed / 0 failed**（原 **22** ＋ 新 **3**）；
      `cargo test --offline -p manager-api` ⇒ **33 passed / 0 failed**

#### 行为验收

- [x] **新鲜投影**比对干净：`base_pool_mismatches = 0` ／ `rows_without_base_pool = 0` ／

      `projected_base_pool_count = 1` ／ `directional_pool_count = 2` ／ `ready_for_cutover = true`

- [x] **陈旧投影被检出**（I-9）：物化之后改动基础池行的 `fee` ⇒ `base_pool_mismatches = 2`

      （两个方向行都带着这份陈旧的 `fee`），样本**点名** `differing_columns = ["fee"]` ＋ `base_pool_id`；
      `ready_for_cutover = false`

- [x] **同一时刻兄弟比对仍为绿**（`projection_mismatches = 0`、`direction_mismatches = 0`）

      ⇒ **证明缺口为真**：旧比较**看不见**这件事

- [x] **孤儿投影被检出**：基础池行被删而方向层仍在 ⇒ `rows_without_base_pool = 2` ＋

      `base_pool_mismatches = 0` ＋ `ready_for_cutover = false`

- [x] **实盘只读审计全绿**（工具 `audit_directional_projection_20261125.py`，报告

      `directional_projection_audit_20261125.{json,md}`）：**零失败项** ——

      | 检查 | `core4` | `candidate_exchange` |
      |---|---:|---:|
      | 基础池 / 方向行 / 已投影基础池 | 488 / 340 / 170 | 494 / 352 / 176 |
      | **I-9** 与基础池不一致 | **0** | **0** |
      | **I-9** 无基础池行 | **0** | **0** |
      | 方向语义错 | **0** | **0** |
      | 序位重复组 / 缺口组 | **0 / 0** | **0 / 0** |
      | 身份重复 | **0** | **0** |
      | 容量 / 输出「未测量≠零」标记错 | **0 / 0** | **0 / 0** |
      | 投影了动态费率池 / 带 hook 池 | **0 / 0** | **0 / 0** |
      | **I-7** 跳的方向池无法解析 | **0**（跳 **522**） | — |
      | **I-12** 跳的方向与方向行不一致 | **0**（跳 **522**） | — |
      | 闭环体与跳序列不一致 | **0** | — |

#### 边界验收（**不得**发生）

- [x] **实盘库零写入**：审计全程 `mode=ro` ＋ `PRAGMA query_only = ON` ⇒ 写入 **0**

- [x] **未放宽任何门禁**：投影规则（动态费率 / 带 hook / token 序）**一字未改**；

      容量 / 利润 / 阈值 / 幂等键 / 回执关联**一字未改**；`ready_for_cutover` **只加严**

- [x] **未**触碰 `M-6` / `M-7` / `M-8` / Python 运行时代码 / `.env` / `chain.json` /

      主库 schema 与迁移（**产品代码改动仅 2 个文件**，见 `D-123` §4）

- [x] **未**在同一批次内删除旧口径：`recommend_direction` / `pool_score` **仍在**

      （下线是 `T-方储.01` 阶段 2 的活，本任务**只**加检测器）

- [x] **固定费率 · 无 hook 约定未被突破**：审计实测**已投影的池**

      `dynamic_fee = 0` 且 `hooks` 为零地址 ⇒ 与方向层的既有排除规则一致

#### 待运行期生效（**如实登记为未完成**）

- [ ] **运行期生效复测**：守卫随 `manager-api` / `rs-src` **重建并重启**后，

      对实盘库生效（**本轮未重启**）—— 实盘运行时的二进制

      （`flashloan/tools/target/release/manager-api.exe`）mtime = **2026-11-24 08:06**，

      早于本任务与 `T-闭.01` 的全部改动；且 `rs-src` 依赖 `broadcast-manager` ＋ `review-manager`，

      并行进程 `T-复广.02`（pid **29140**）**仍在途**改动这两个 crate ⇒

      **此刻重启会把未经回归验证的代码带上线** ⇒ **刻意不重启**，**不代任何项盖章**

---

> **诚实边界**：本任务 **16** 项中 **15** 项闭合、**1** 项待运行期重启 ⇒
> 按「**部分闭合**」登记（**不归档**）。
> **本任务不代任何其它任务关闭验收项** ——
> `T-闭.01` 的「实盘生效复测」与 `T-方储.01` 阶段 2 的前置条件**状态不变**。
