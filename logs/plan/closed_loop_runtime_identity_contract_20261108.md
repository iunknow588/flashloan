# 闭环运行态方向级身份契约（2026-11-08）

> 本文固定 `P0-2.2` 的身份决策，是 `P0-2.3` 至 `P0-2.7` 的代码改造、
> 数据迁移和验收依据。任务状态与完成标准仍以
> [未完成任务清单_20261108.md](未完成任务清单_20261108.md) 为唯一来源。

## 1. 身份定义

| 身份 | 规范形式 | 所有者 | 允许用途 | 禁止用途 |
|---|---|---|---|---|
| `loopKey` | `0xanchor:0xy:0xz`，小写地址，Y/Z 保持方向 | `v4_closed_loops` 与运行态状态机 | SQLite 运行态主键、Review、Broadcast、租约、心跳、终态、幂等命令 | 不得排序 Y/Z，不得用 `routeKeyId` 替代 |
| `directedSymbolKey` | `anchor\|y\|z`，Y/Z 保持方向 | 展示/诊断投影 | UI 展示、人工诊断、日志关联 | 不得作为运行态持久主键或命令幂等键 |
| `workerGroupKey` | `anchor\|sorted(y,z)` | Rust 常驻 worker | 复用同时扫描双向路径的 worker、合并任务配置 | 不得领取、完成或聚合一条具体运行态状态 |
| `routeKeyId` | Manager 路由键 | Manager/RuntimeCore | Manager 路由索引和跨 authority 引用 | 不得替代物理 `loopKey` |

地址路径可以是闭环四段 `[anchor, y, z, anchor]`，也可已规范化为三段
`[anchor, y, z]`。四段路径首尾必须相同；三段路径三个地址必须互不相同。
显式 `loopKey` 只接受物理地址型键，拒绝 `route:v1:*`、符号组、缺失段或
未闭合路径。

## 2. 读写消费者矩阵

| 入口 | 当前责任 | 目标身份 | 改造要求 |
|---|---|---|---|
| `backfill_runtime_state_for_closed_loops` | 从 `v4_closed_loops` 补运行态 | 物理 `loopKey` | 一行闭环生成一行状态；禁止按 `workerGroupKey` 去重 |
| `claim_loop` / `heartbeat_loop` / `complete_loop` | 复核和广播状态推进 | 物理 `loopKey` | owner、generation、lease 和幂等均绑定完整物理键 |
| `prune_orphan_runtime_state` | 清理无闭环运行态 | 物理 `loopKey` | 稳定态只将精确物理键视为匹配；旧符号组仅作迁移诊断并保留，终态和已知 Testnet 残留默认保留 |
| `closed_loop_consumer._attempt_identity` | 将 Rust 结果映射到运行态 | 物理 `loopKey` | 优先读取 `loopKey`，其次可信 `routeAddresses`；最后才用不排序的符号路径作诊断，不得排序 Y/Z |
| `_submit_native_closed_loop_groups` | 组织 Rust worker 并领取状态 | 双身份 | `workerGroupKey` 只用于 worker 复用；每个方向按各自物理 `loopKey` 独立领取和失败处置 |
| Rust `direction_results` | 返回双向复核结果 | 物理 `loopKey` | 每个方向结果必须映射回自己的地址路径，不共享另一方向的广播或终态 |
| Manager/UI 聚合 | 展示组级 worker 与方向明细 | 展示键 + 物理键 | UI 可按 anchor 聚合，但每个方向必须携带独立 `loopKey`，状态和命令不得回写组级键 |

## 3. 写入与迁移规则

- 新写入只能使用物理 `loopKey`。任何写入入口发现空键、符号组键或
  `route:v1:*` 时必须拒绝并记录诊断错误。
- 符号组是兼容观察值，不得用于 `INSERT`、`UPSERT`、领取、租约、完成或广播
  幂等。
- 现有 96 条符号组状态只作为聚合历史证据。迁移时不得把其
  `preflight_failed` 等聚合终态复制给两个方向；192 条方向状态默认从
  `offchain_pending` 建立。
- 旧符号组在迁移完成前由孤儿清理标记为
  `legacy_symbol_identity_pending_migration`，不得按普通孤儿静默删除。
- 孤儿清理只接受同 scope 的精确物理键。执行中、执行成功、执行失败和已知
  Testnet 残留继续保留。

### 3.1 `P0-2.4` 迁移映射算法

迁移输入必须来自同一只读快照：

- `v4_closed_loops`、`v4_closed_loop_runtime_state` 和 P0-2.1 canonical 报告
  使用相同 scope、数据库快照和逻辑 checksum。
- 活库在 dry-run 期间发生变化时，本轮结果作废，重新创建快照并重跑。
- 不得在 live DB 上一边读取一边生成可 apply 的迁移计划。

对每个无序 Y/Z 组执行以下确定性规则：

1. 从 canonical 报告读取该组的 `directedSymbol` 精确匹配。
2. 必须恰好存在 1 条命中的有向闭环，并以该闭环的地址路径生成命中方向
   `matchedLoopKey`。
3. 将现有运行态映射为 `rekey`：`sourceRuntimeKey -> matchedLoopKey`。
4. 同组另一条反向闭环映射为 `insert`：生成 `reverseLoopKey`，不复制原状态。
5. `rekey` 和 `insert` 的目标状态均为 `offchain_pending`；
   `stateSource=identity_migration_reset`，不是 `preflight_failed` 等聚合终态。
6. 原始 `sourceRuntimeKey`、状态、updatedAt、租约信息和 checksum 只写入
   archive/disposition 记录，作为迁移证据，不把原终态带给任一方向。

以下任一情况必须生成 finding 并停止 apply：

- 无序组找不到有向精确匹配，或找到多条匹配。
- 两个闭环方向生成相同物理 `loopKey`。
- `directedSymbolKey`、`routeAddresses` 和闭环物理 `loopKey` 不能互相解释。
- 源运行态不属于目标 scope，或数据库/报告 checksum 不一致。
- 目标键已存在且不是本计划中有明确来源的同一条现有运行态。

当前 canonical 基线满足以下目标时才允许进入 `P0-2.6`：

```text
sourceRuntimeGroups=96
sourceExactDirectedMatches=96
rekeyPlans=96
reverseInsertPlans=96
proposedRuntimeStates=192
proposedState=offchain_pending
unresolvedDirections=0
duplicateTargetKeys=0
```

生产加载门禁：

- 不得在 `P0-2.6` 受控 apply 和目标计数校验前重启新代码进程。
- 当前 `FLASHLOAN_RUNTIME_PROFILE=v4_dex` 启动后会立即启动
  `v4_closed_loop_worker`；该 worker 会先重建闭环，再调用运行态回填、孤儿清理
  和残留保护。
- 提前重启会让运行时自动写入替代迁移计划，破坏 dry-run、copy DB、rollback
  和维护窗口的一致性。
- 允许的重启顺序为：暂停自动写入 -> 固定数据库/checksum -> 执行已验收 apply
  -> 目标键计数为 192 且状态为 `offchain_pending` -> 重启 DEX-only 进程 ->
  对同 PID 执行 P0-2.7。

## 4. 双反向闭环规则

对同一组 `anchor/Y/Z`：

- 正向键为 `anchor:y:z`，反向键为 `anchor:z:y`。
- 两条路径共享一个 `workerGroupKey=anchor|sorted(y,z)`，但拥有两个独立运行态。
- Rust worker 可以一次任务扫描两条路径，但每条路径分别返回结果、分别领取状态、
  分别记录复核/广播结论。
- 一条路径的广播结果不得附着到另一条路径；未选中方向必须记录
  `broadcast_not_selected`，不得伪造缺失广播。

## 5. Scope 与证据门禁

所有读写必须同时绑定：

```text
network + chain_id + protocol + market_id
```

主网固定为：

```text
robinhood + 4663 + uniswap_v4 + dex-arbitrage-4663-uniswap-v4
```

Testnet `46630` 只用于隔离回归。证据必须记录任务编号、scope、数据库版本或
checksum、观察窗口、退出码、异常处置和 `X-Request-Id`（存在远端命令时）。

## 6. 验收口径

`P0-2.3` 工程验收已满足（临时库/单测边界）：

- 双反向闭环回填为两条运行态，键分别为正向和反向物理 `loopKey`。
- 跨 network、chain、protocol、market 的状态不会被匹配或清理。
- 同一物理键重复领取被租约阻止；过期租约可按 owner/generation 回收。
- `execution_success` 和 `execution_failed` 终态不会被普通清理删除。
- 旧符号组只产生迁移诊断，不产生精确匹配，也不被静默删除。
- Rust 双向任务只提交一个 worker，但两个方向分别可领取和完成。

工程证据：
[p0_2_3_directional_identity_regression_20261108T174638Z.json](runtime_evidence/reports/p0_2_3_directional_identity_regression_20261108T174638Z.json)
记录 `138 passed` 和 `py_compile` 退出码 `0`。该工程证据不等于生产库已有
192 条方向运行态。

最终 `P0-2.7` 验收目标：

```text
directedClosed=192
directedRuntime=192
exactMatched=192
exactMissing=0
duplicateDirectedKey=0
orphan=0
scopeLeak=0
```
