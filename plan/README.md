# 主网自愈编排器开发计划入口 v10.17（2026-11-20）

## 活动文档

| 文档 | 职责 |
|---|---|
| `开发计划_20261113.md` | 架构边界、目录职责、阶段路线和安全约束 |
| `未完成任务清单_20261113.md` | 唯一活动待办和当前阻塞顺序 |
| `主网编排器审查修复_check_list_20261113.md` | 逐项验收条件；未完成项目保持 `[ ]` |
| `back/` | 冻结计划、已完成任务归档和历史索引 |

当前活动版本为 **v10.17**。已分配任务至 `TASK-148`，验收编号至 `AC-213`；编号永久不复用。v10.16 活动文件已冻结于 `back/冻结归档_*_20261120_v10.16.md`。

## 当前状态

- Rust 内核：`cargo test --offline --lib`，183 passed。
- Manager workspace：`cargo test --offline --workspace` 的各 crate 测试全部通过；有 unused import/variable 警告。
- Python 全量测试上次在根 `scripts/` 被移走的期间运行，未能通过；脚本现已回到根目录，必须重新验证后才能更新全量基线。
- 主网 healer 最新留存报告时间为 2026-11-19 12:13Z：60 条 review 中 59 条有盈亏、1 条缺最佳金额，positive=0，broadcast records=0，blockers 为 reviews/broadcast，`safeToAutoBroadcast=false`。该报告已过新鲜度窗口，不能代表当前实时状态。
- 测试网 Round 38 证据显示方向数 0、Manager loops 0；RPC/WSS 刷新和候选池覆盖仍阻塞完整同构验证。
- 已完成代码/迁移/物化范围和未完成边界见 `back/已完成任务归档_20261120_第39轮_跨tick与级联诊断实现.md`。

## 下一执行顺序

1. 修复 TASK-148：定位并修复 `single_tick_capacity_unavailable` 的责任层根因。
2. 重跑主网 TASK-142/143 review，证明金额与利润逐条完整、单位一致、快照新鲜。
3. 验证 TASK-145 估值口径，并恢复测试网 RPC/WSS 与有效闭环前置输入（TASK-147）。
4. 完成 TASK-146 双链级联和自动准入回归。
5. 在正利润且 review 新鲜完整后执行 TASK-144 广播 dry-run；真实提交仅在单独授权后进行。
6. 满足链上回执闭环后执行 TASK-141 三个稳定观察窗口。

## 仓库目录裁决

根 `scripts/` 保持为仓库级 Python CLI、运维、迁移、探针、审计和验证工具区。`README.md`、`.replit`、pytest 与 imports 均引用该位置；不得在计划归档时迁入 `plan/`。`scripts/tool/` 是共享工具，`scripts/tools/` 是专项探针，`scripts/tests/` 是该区脚本测试。Manager crate 位于 `flashloan/tools/`，Hardhat 子项目脚本位于 `flashloan/tools/test-token_create/scripts/`，两者按各自模块边界维护。

## 约束

- Rust 内存内核是闭环、容量、金额和利润的权威；Python 只消费或转发。
- SQLite 初始化/持久化/审计；运行期计算使用内存状态。WSS 更新版本化快照。
- 级联顺序为代币→池→闭环→复核→广播；失败只关闭当前不可执行层，不越级、不永久关闭生产维护。
- 每链 DB、chain ID、RPC/WSS、权限和内存状态隔离。
- 未测量保持缺值；已测零才是零。无完整正利润和用户授权不广播。
- 禁止回灌历史深度或业务数据；代码测试通过不代表生产稳定。

## 归档

- v10.16 快照：`back/冻结归档_*_20261120_v10.16.md`。
- 第 39 轮实现归档：`back/已完成任务归档_20261120_第39轮_跨tick与级联诊断实现.md`。
- 机器证据：`runtime_evidence/reports/`。证据必须按时间、chain ID 和数据库范围解释，不得把过期状态称为当前状态。
## 内存对象处理约束

- **MIO-1** 初始化读取 SQLite；处理循环不逐条查询业务表。
- **MIO-2** 运行期业务状态由全局内存上下文持有，计算只消费当前快照。
- **MIO-3** token decimals 随内存状态维护，金额按权威精度缩放。
- **MIO-4** WSS 更新版本化内存快照，复核携带版本与新鲜度。
- **MIO-5** Review Manager 通过 Rust 内核和管理接口访问业务状态。
- **MIO-6** Python 不重算利润/金额，也不重建闭环身份。
- **MIO-7** Rust 发布有符号利润和 `signedPnl.amount`，区分测得零与未测量。
- **MIO-8** 每链 DB、chain ID、RPC、权限和运行状态隔离。
