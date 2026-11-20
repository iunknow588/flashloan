# P0-4｜ARCH-1 版本触发重算实窗验证（归因文档）

> 日期：2026-11-14
> 阶段：阶段 A｜低风险收口（A4 版本触发重算实窗）
> 模式：`read_only` ｜ 生产库写入：**0** ｜ 权限切换：**0** ｜ 广播：**0** ｜ RPC 请求：**0**
> 机器证据：`logs/plan/runtime_evidence/reports/p0_4_recompute_latency_20261114.json`
> 责任 manager：`composite_manager`

---

## 1. 目标与验收口径

P0-4 的验收要求（来自 `未完成任务清单_20261113.md` §P0-4）：

- 测量 token/pool/scope 版本变化到 edge/exchange/closed-loop 重算完成的实际耗时。
- 报告包含：**开始版本**、**结束版本**、**各 manager checkpoint**、**总延迟**、**失败重试次数**。

本轮不引入任何新的权威业务逻辑，只补齐**测量能力**并产出结构化证据。

## 2. 缺口分析（实施前）

### 2.1 已存在的基础设施

`composite_manager` 已经持久化 ARCH-1 全链重算所需的全部 checkpoint：

| 表 | 用途 | 关键字段 |
|---|---|---|
| `manager_pipeline_runs` | 一次重算运行 | `created_at`（触发点）、`upstream_snapshot_json`（开始版本）、`upstream_fingerprint`、`completed_at` |
| `manager_pipeline_steps` | 每个 manager 步骤 | `step_name`、`status`、`attempt_count`、`dependency_snapshot_json`（该步开始时观察到的依赖版本）、`started_at`、`completed_at`、`last_error` |
| `manager_pipeline_events` | 步骤事件 | `event_type`（`pipeline.step_started` / `pipeline.step_completed` / `pipeline.step_failed`）、`payload_json` |

步骤顺序与依赖已经在 `PipelineStepName::order()` / `dependencies()` 中冻结：

```
pool_scope → candidate_edge → candidate_exchange → closed_loop → review → broadcast
```

### 2.2 实际缺口

1. **没有消费者**。这些 checkpoint 被写入，但没有任何只读组件把它们解释成"版本变化 → 全链重算完成"的延迟报告。
2. **没有统一口径**。`manager_api` 暴露的是 `/v1/pipeline/status`、`/v1/pipeline/runs`，返回原始运行/步骤行；延迟、起止版本边界、重试次数需要调用方自己拼装，容易出现口径漂移。
3. **没有验收证据**。P0-7 冻结的机器报告契约要求 `inputVersion` / `outputVersion` / `durationMs` / `failureCodes`，P0-4 之前没有任何一份符合该契约的重算延迟报告。

结论：**不需要新建 pipeline**，只需要一个**只读测量器** + 一份符合 P0-7 契约的证据。

## 3. 设计

### 3.1 新增组件

`flashloan/tools/composite_manager/src/recompute_latency.rs`（新文件）：

| 类型 | 作用 |
|---|---|
| `RecomputeCheckpoint` | 单个 manager checkpoint：步骤名、状态、`attempt_count`、起止时间、步骤耗时、累计延迟、依赖指纹、依赖版本 |
| `RecomputeVersionBoundary` | 一个版本边界的完整版本映射 + 上游指纹 |
| `RecomputeRunMeasurement` | 一次运行的完整测量结果 |
| `RecomputeLatencyReport` | 跨运行的聚合报告（中位数/最小/最大/重试总数） |
| `measure_recompute_latency()` | **只读**入口：以 `SQLITE_OPEN_READ_ONLY` 打开数据库，读取运行/步骤/事件并计算延迟 |

导出入口：`composite_manager::measure_recompute_latency`、
`composite_manager::recompute_latency_checksum`。

### 3.2 测量模型（关键定义）

| 概念 | 定义 | 来源 |
|---|---|---|
| **触发点** | `run.created_at` | 重算运行被打开的瞬间 |
| **版本变化点** | 各步骤 `begin` 时捕获的 `dependency_snapshot` 版本 | 步骤真正开始工作的时刻 |
| **完成点** | 各步骤 `completed_at` 的最大值 | 最后一步完成 |
| **总延迟** | `完成点 − 触发点` | `total_latency_ms` |
| **重算跨度** | `完成点 − 最早 started_at` | `recompute_span_ms` |
| **单步延迟** | `步骤 completed_at − 步骤 started_at` | `step_duration_ms` |
| **累计延迟** | `步骤 completed_at − 触发点` | `cumulative_latency_ms` |
| **失败重试次数** | `Σ (attempt_count − 1)` | `failure_retry_count` |
| **全链完成** | 所有 requested step 均到达 `completed` / `idle` 终态 | `full_chain_complete` |

### 3.3 版本变化判定的两个陷阱（已修正）

**陷阱一：拿 step 的依赖快照和 run 的全量快照直接比较 → 假阳性。**
每个步骤的 `dependency_snapshot` **只包含该步骤真正依赖的组件**。例如 `broadcast` 只依赖 `closed_loop` 和 `review`。若把"最后一步的依赖快照"与"运行开始时的全量快照"直接比较，
`candidate_exchange` / `closed_loop` / `review` / `broadcast` 这些**不在最后一步依赖集里**的组件会被误判为"版本变化"。

修正：版本比较只在 **start / end 两侧都存在**的组件集合（交集）上进行。

**陷阱二：只看最后一步 → 漏报。**
真实场景中版本变化通常发生在中段。窗口 1 的 `candidate_edge` / `candidate_exchange` 版本抬升只有中间步骤观察到；
最后一步（`broadcast`）根本不依赖它们，只看最后一步会完全漏掉这次变化。

修正：结束边界取 **所有步骤依赖快照的并集**，而不是最后一次。

### 3.4 只读保证

- `measure_recompute_latency()` 只用 `OpenFlags::SQLITE_OPEN_READ_ONLY` 打开数据库。
- 单测 `measurement_is_read_only_and_does_not_create_rows` 断言测量前后 `manager_pipeline_runs` 行数不变。
- 证据生成全程在 `tempfile::tempdir()` 的临时副本上运行，副本随进程退出销毁。
- **无生产库写入、无权限切换、无广播、无 RPC 请求。**

## 4. 实窗演练

### 4.1 演练构造

`flashloan/tools/composite_manager/examples/p0_4_recompute_latency_probe.rs`：

1. 在临时目录上建库并执行 `manager_db::migrate`。
2. 播种 6 个 manager 的 library state（pool scope / candidate edge / candidate exchange / closed loop / review / broadcast），使 `validate_step_output` 能验证每个步骤确实物化了输出。
3. **窗口 1**：跑完整 6 步 pipeline，在第 1 步完成后抬高 `candidate_edge` 与 `candidate_exchange` 版本（模拟 token/pool 版本变化触发的重算）。
4. **窗口 2**：再跑一次完整 pipeline，不抬版本（幂等重跑对照）。
5. 用 `measure_recompute_latency` 读回测量结果并输出机器报告。

每个步骤用固定模拟成本推进（`STEP_COST_MS = [1250, 980, 1640, 3210, 2870, 640]`），
使延迟可复现而不是依赖机器负载。

### 4.2 测量结果

| 窗口 | 上游版本变化 | 全链完成 | 总延迟 | 变化组件 |
|---|---|---|---|---|
| window-1（版本触发） | **是** | 是 | **11 836 ms** | `candidate_edge`、`candidate_exchange` |
| window-2（幂等重跑） | 否 | 是 | 11 743 ms | — |

> 窗口顺序按 `created_at` 倒序排列；window-1 是版本触发窗口，window-2 是幂等对照窗口。

聚合（仅统计"版本变化 + 全链完成"的运行）：

- `versionChangedRunCount = 1`
- `medianTotalLatencyMs = 11836`
- `minTotalLatencyMs = 11836`
- `maxTotalLatencyMs = 11836`
- `totalFailureRetryCount = 0`

### 4.3 逐 manager checkpoint（window-1，版本触发窗口）

| 步骤 | manager 职责 | 依赖 | 步骤耗时 | 累计延迟 |
|---|---|---|---|---|
| `pool_scope` | pool scope 物化 | token_libraries | 1 339 ms | 1 421 ms |
| `candidate_edge` | candidate edge 物化 | token_libraries, pool_scope | 1 082 ms | 2 625 ms |
| `candidate_exchange` | candidate exchange / Y-Z 物化 | token_libraries, pool_scope, candidate_edge | 1 787 ms | 4 502 ms |
| `closed_loop` | 闭环物化 | candidate_edge, candidate_exchange | 3 300 ms | 7 897 ms |
| `review` | 复核物化 | closed_loop | 2 970 ms | 10 964 ms |
| `broadcast` | 广播授权物化 | closed_loop, review | 753 ms | 11 836 ms |

每个 checkpoint 还携带：
`dependencyFingerprint`（依赖指纹）、`dependencyVersions`（该步开始时观察到的版本）、
`attemptCount`、`lastError`。

### 4.4 观测结论

1. **全链重算在本演练口径下约 11.8 s**，其中 `closed_loop`（3.30 s）与 `review`（2.97 s）是主要成本项，合计占比约 53%。
2. **版本变化被可靠检出**：窗口 1 检出 `candidate_edge` + `candidate_exchange` 版本抬升；窗口 2 无变化，未误报。
3. **步骤级 checkpoint 足以定位瓶颈**：每个 manager 的贡献可独立归因，不需要事后猜测。
4. **重试计数可用**：失败步骤的 `attempt_count` 累加被正确换算成 `failure_retry_count`（单测覆盖：失败 1 次后重试成功 → `failure_retry_count = 1`）。

## 5. 覆盖与验收对照

| P0-4 验收项 | 覆盖方式 | 结论 |
|---|---|---|
| 测量 token/pool/scope 版本变化到 edge/exchange/closed-loop 重算完成的实际耗时 | `measure_recompute_latency` + 窗口 1 探针 | ✅ 11 836 ms |
| 报告包含开始版本 | `start_version.versions` + `start_version.fingerprint` | ✅ |
| 报告包含结束版本 | `end_version.versions` + `end_version.fingerprint` | ✅ |
| 报告包含各 manager checkpoint | `checkpoints[]`（6 步 × 步骤耗时 / 累计延迟 / 依赖指纹 / 依赖版本） | ✅ |
| 报告包含总延迟 | `total_latency_ms`（含中位数 / 最小 / 最大） | ✅ |
| 报告包含失败重试次数 | `failure_retry_count` / `retried_step_count` / `failed_step_count` | ✅ |
| 只读、不写生产 | `SQLITE_OPEN_READ_ONLY` + 行数不变单测 + 临时副本 | ✅ |
| 机器报告符合 P0-7 冻结契约 | `mode`/`productionWrite`/`inputVersions`/`outputVersions`/`rowCount`/`checksum`/`status`/`failureCodes`/`rollbackPoint`/`rpcRequestCount`/`testCommands`/`failureSummary` 全部存在 | ✅ |

## 6. 回归

| 命令 | 结果 |
|---|---|
| `cargo test --locked -p composite-manager` | **17 passed / 0 failed** |
| `cargo test --locked --workspace` | **359 passed / 0 failed** |
| `cargo run --locked -p composite-manager --example p0_4_recompute_latency_probe` | 成功，`status=passed` |

新增单测（`composite_manager::recompute_latency::tests`）：

1. `a_full_chain_run_reports_per_step_checkpoints_and_total_latency` — 全链 checkpoint 与总延迟。
2. `retries_are_counted_and_a_failed_step_is_not_full_chain` — 重试计数与失败步骤判定。
3. `an_unfinished_run_is_not_reported_as_full_chain` — 未完成运行不得计为全链完成。
4. `measurement_is_read_only_and_does_not_create_rows` — 只读保证。
5. `a_version_change_between_steps_is_detected` — 版本变化检出。
6. `a_version_change_seen_only_by_a_middle_step_is_still_detected` — 中段步骤版本变化不得漏报。
7. `median_handles_even_and_odd_populations` — 中位数计算。

## 7. 影响范围与非目标

### 7.1 影响

- 新增 `composite_manager::recompute_latency` 模块与 6 个公开类型；**不改变任何既有 manager 行为**。
- `composite_manager/Cargo.toml` 新增 `[dev-dependencies] manager-contracts`（仅供 example 使用）。
- 新增 example 探针，不进入生产构建。

### 7.2 非目标

- **不改** pipeline 执行路径、顺序、依赖或重试策略。
- **不新增** authority、manager 或 Python 侧业务判断。
- **不写**生产库、不切换权限、不启动广播。
- **不做**生产实窗采集（生产实窗需停机窗口与授权，见 §8）。

## 8. 遗留与后续

1. **生产实窗仍待采集**。本轮实窗为**副本库演练**，测量模型与报告契约已就位；
   生产实窗需要 W6/W7 停机窗口与用户显式授权，届时以同一 `measure_recompute_latency` 入口采集即可，
   无需再改测量代码。
2. **`pool_scope` 目前没有数值版本**，只有 checksum 与 library 版本映射；
   报告中的 `pool_scope` 版本项固定为 `0`，真实变化以 `dependencyFingerprint` 与 `libraryVersions` 体现。
   若后续需要数值版本，应在 `pool_manager` 增加 scope version，而不是在测量层伪造。
3. **`review` / `broadcast` 的重算成本占比高**，后续若实窗显示真实环境同样偏重，
   应在 W6 之后评估是否拆分复核物化粒度；本轮不预先优化。

## 9. 复现命令

```powershell
# 单元测试
cd E:\2026OPC大赛\flashLoan\flashloan\tools
cargo test --locked -p composite-manager

# 只读探针（临时副本库）
cargo run --locked -p composite-manager --example p0_4_recompute_latency_probe

# 全量回归
cargo test --locked --workspace
```

机器证据：`logs/plan/runtime_evidence/reports/p0_4_recompute_latency_20261114.json`
（UTF-8 无 BOM、LF、`sort_keys=True`）。
