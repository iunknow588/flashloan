# Robinhood V4 计划索引

> 文档基线：2026-11-08。`logs/plan/` 根目录只保留当前执行计划、未完成任务
> checklist、整体工作计划、架构支持文档、实施手册和运行证据目录；历史计划和
> 已完成任务归档放入 `back/`。

## 开发前阅读顺序

1. [开发计划_20261108.md](开发计划_20261108.md)：当前唯一执行入口。
2. [未完成任务清单_20261108.md](未完成任务清单_20261108.md)：当前唯一未完成
   checklist。
3. [整体工作计划_20261107.md](整体工作计划_20261107.md)：阶段顺序、生产窗口和
   整体推进路线。
4. [closed_loop_runtime_identity_contract_20261108.md](closed_loop_runtime_identity_contract_20261108.md)：
   闭环运行态方向级身份、读写消费者和迁移门禁。
5. [token_and_manager_rust_rearchitecture_20261103.md](token_and_manager_rust_rearchitecture_20261103.md)：
   模块边界和数据所有权。
6. [Rust管理模块实施手册_20261103.md](Rust管理模块实施手册_20261103.md)：
   DDL、接口、迁移和测试直接依据。

## 文档权威边界

| 内容 | 权威文档 | 使用规则 |
|---|---|---|
| 当前执行入口 | `开发计划_20261108.md` | 每轮只从这里派生任务 |
| 未完成任务 | `未完成任务清单_20261108.md` | 状态、依赖和完成标准唯一来源 |
| 整体推进路线 | `整体工作计划_20261107.md` | 不在旧历史计划里派生新任务 |
| 闭环运行态身份 | `closed_loop_runtime_identity_contract_20261108.md` | 状态键、消费者和迁移身份冲突时使用 |
| 模块职责、数据所有权 | `token_and_manager_rust_rearchitecture_20261103.md` | 解决架构冲突时使用 |
| DDL、KEY ID、API、迁移、测试 | `Rust管理模块实施手册_20261103.md` | 编码和评审的直接依据 |
| 已完成事实、测试结果 | `back/已完成任务归档_20261107.md` | 只追加有证据的完成项 |

### 文档变更顺序

每轮任务结束后按以下顺序更新，避免状态和证据互相倒置：

1. 固化原始命令输出、JSON/JSONL 和失败样本。
2. 更新 `未完成任务清单_20261108.md` 的状态、依赖和完成标准核对结果。
3. 更新 `开发计划_20261108.md` 的下一执行项和技术门禁。
4. 更新 `整体工作计划_20261107.md` 的阶段状态和窗口边界。
5. 更新本索引、运行证据索引和已完成任务归档链接。

冲突处理规则：

- 任务状态冲突时，以当前 checklist 中引用证据后的状态为准。
- 工程证据与生产证据冲突时，以绑定当前 PID、authority、版本和 checksum 的
  生产证据为准，并保留工程证据说明差异。
- 历史计划与活动文档冲突时，活动文档优先；历史计划只读。
- 未提供证据路径、退出码或完成标准核对结果的“已完成”描述无效。

## 当前状态摘要

- D0/M0 至 D9.41/M9 的代码、临时库、shadow、API、RuntimeCore 命令和多数
  UI manager-only 迁移已有证据。
- `DOC-1/DOC-2/DOC-3` 已于 2026-11-07 完成并归档；未完成任务 checklist
  只保留未完成项。
- 当前活动业务仅为 Robinhood V4 DEX 套利。CoW 实时报价/订单提交、清算、
  BNB Venus watcher 和 BNB 共享采集器不是活动任务；命名歧义、推荐名和
  兼容迁移顺序见 checklist 的“业务范围与优先级”和“不合适命名与推荐用名”。
- 最新主网范围审计已落盘：
  [closed_loop_runtime_sync_20261108T041955Z_robinhood-4663-uniswap_v4.json](runtime_evidence/reports/closed_loop_runtime_sync_20261108T041955Z_robinhood-4663-uniswap_v4.json)。
  2026-11-07 范围审计只保留为历史基线。
- 高优缺陷 `BUG-CLOSEDLOOP-RUNTIME-SYNC`：主网 192 条有向闭环只有 96 条运行态；
  96 个无序 Y/Z 组全部存在，但 96 条反向闭环没有方向专属状态；运行态
  `loop_key` 仍是符号组而非闭环地址键。`P0-2.1`、`P0-2.2` 和 `P0-2.3`
  已完成并归档；其中 `P0-2.3` 只表示代码和单测完成，生产数据仍为 96 条。
  当前报告
  `closed_loop_runtime_sync_20261108T041955Z_robinhood-4663-uniswap_v4.json`
  的 `outOfScopeRuntime=0`。
- `P0-2.2` 已冻结地址型 `loopKey`、方向级符号诊断键和 `workerGroupKey` 的
  唯一用途；`P0-2.3` 已让状态机、Consumer、Rust worker、Review 分类、清理和
  Manager overview 保留方向。专项回归 `138 passed`，证据为
  `runtime_evidence/reports/p0_2_3_directional_identity_regression_20261108T174638Z.json`。
- 高优缺陷 `BUG-CLOSEDLOOP-TESTNET-RESIDUE-AUTOPRUNE`：历史审计曾记录
  Testnet `aaa|bbb|ccc` 行，当前审计确认该行已被例行维护自动删除。代码已改为
  默认保留并要求显式审批清理；专项回归 `20 passed`：
  `runtime_evidence/reports/p0_2_5_residue_scope_regression_20261108T183621Z.json`。
  当前运行进程尚未加载新代码；生产重启安排在 P0-2.6 apply 之后，同进程生产
  窗口在 P0-2.7 采集。
- 主网 M1-M8 生产 authority 尚未接管；M0 migration 尚未在主网 apply。
- 主网 Web 已按 `FLASHLOAN_RUNTIME_PROFILE=v4_dex` 于 2026-11-08 10:12 重启：
  `run.py` PID `24036` 监听 `5000`，wrapper PID `20400`；CoW、清算和 BNB
  后台关闭。页面与 worker 周期使用两个独立参数，当前均为 `60s`。
  `P0-1` 三个旧窗口和 `P0-1-R1` 当前 PID 增量复验均已完成并归档。当前下一项
  为 `P0-2.4` 迁移 dry-run；`P0-2.5` 的 copy DB/隔离进程验证仍待完成。
  两个周期参数当前同为 `60s` 只是验收取值，后续由 `P3-4` 分别调整并验证。
- 生产重启不得早于 P0-2.6。新代码启动会立即运行 `v4_closed_loop_worker`，
  其中包含闭环重建和运行态自动回填；提前重启会绕过迁移 dry-run、copy DB、
  rollback 和维护窗口门禁。
- 当前 `/api/status` 仍显示 RuntimeCore `state=error`、`managerAvailable=false`：
  token library 的 `core/stocks/extension/test.enabled` 期望 `true`、实际 `false`，
  RuntimeCore 因而拒绝混合 Manager snapshot。该问题保留在 `M1-P` 切换门禁内。
- P0-2/P0-4/P0-5 处于进行中，P0-3/P0-6/P2-1/P3-1/P3-3/P3-4 尚未开始；
  均未达到完成标准。P3-2 文档同步持续进行。
- 已被否决的 D9.24 Python Manager 重组方案保留为失败方案审计，不作为完成项。

## 最新归档

- [back/未完成任务清单_20261107_重建前归档_20261108.md](back/未完成任务清单_20261107_重建前归档_20261108.md)：
  新 checklist 重建前的完整快照。活动目录现在只使用
  `未完成任务清单_20261108.md`。
- [back/已完成任务归档_20261107_重建前归档_20261108.md](back/已完成任务归档_20261107_重建前归档_20261108.md)：
  在写入 P0-2.2/P0-2.3 增量前的累计归档快照，仅供追溯。
- [back/开发计划_20261107_增强前归档_20261107.md](back/开发计划_20261107_增强前归档_20261107.md)：
  全周期开发计划重构前的开发入口快照，仅供追溯；当前任务从
  `开发计划_20261108.md` 派生。
- [back/已完成任务归档_20261107.md](back/已完成任务归档_20261107.md)：
  D0/M0 至 D9.41/M9 的完成项、P0-1/P0-1-R1 运行验收、P0-2.1 审计、
  P0-2.2 身份契约、P0-2.3 工程回归和证据边界。
- [back/开发计划_20261103_活动计划归档_20261107.md](back/开发计划_20261103_活动计划归档_20261107.md)：
  2026-11-03 活动计划的完整历史版本，仅供追溯。
- [back/已完成任务归档_20261103.md](back/已完成任务归档_20261103.md)：更早的
  2026-11-03 归档，仅作历史。

## 运行证据

[runtime_evidence/README.md](runtime_evidence/README.md)：观察 JSONL、报告和诊断
快照的目录与证据索引。不要修改历史证据；新证据写入
`runtime_evidence/reports/` 并在归档中登记。

最新计划基线：
[closed_loop_runtime_sync_20261108T041955Z_robinhood-4663-uniswap_v4.json](runtime_evidence/reports/closed_loop_runtime_sync_20261108T041955Z_robinhood-4663-uniswap_v4.json)。
