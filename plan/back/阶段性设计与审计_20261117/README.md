# 阶段性设计与审计 2026-11-17（第 22 轮）

本目录承载第 22 轮（v10.7）新增的阶段性设计文档。

| 文件 | 归属 TASK | 状态 |
|---|---|---|
| `m1_authorization_package.md` | `TASK-111` / `TASK-112` / `TASK-113`（M0-C 授权窗口前置包） | 待产出 |

## 说明

`m1_authorization_package.md` 必须在取得生产写入授权**之前**完成，内容至少包含：

1. **单一生产窗口命令单**：按 `开发计划_20261113.md` §7.4 的 L1 → L2 → L3 顺序，
   每步标注 `precondition` / `expectedOutput` / `rollbackCommand` / `abortCondition`；
   命令单必须可由未参与开发的人照单执行。
2. **备份与恢复演练记录**：数据库 + WAL + SHM 一致性备份的 SHA-256、schema checksum、
   row count、磁盘占用；副本恢复演练的恢复耗时、恢复后 checksum 与完整性校验结果。
3. **变更清单与预期 checksum**：每个受影响表的预计新增/修改/删除行数与预期 checksum，
   与 M0-B 的 `projectedTarget` 交叉比对（差异必须为 0 或被显式解释）。
4. **授权文本模板**：目标库绝对路径、chain id、允许动作、禁止动作、有效期、回滚触发条件。

对应验收条件：`AC-176`、`AC-177`、`AC-178`。