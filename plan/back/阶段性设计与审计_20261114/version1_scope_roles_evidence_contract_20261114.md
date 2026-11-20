# 版本 1 范围、池角色与证据契约（P0-7）

日期：2026-11-14  
状态：冻结（version 1 / v9.8）  
写入策略：本契约阶段 `productionWrite=false`。

## 1. 版本 1 范围

- 允许多跳闭环。
- 每个 hop 都必须满足 single-tick：按该 hop 的**实际最终输入量**，在 initialized tick/liquidity 状态下计算，最终输出不得跨越下一 tick。
- 跨 tick 路径可以保留计算接口与诊断输出，但不得进入候选、复核或广播。
- 以下情况整条路线 fail-closed：
  - 任一 hop 跨 tick；
  - 池状态版本漂移；
  - single-tick capacity 未知或不足；
  - initialized tick / liquidity / sqrtPriceX96 无法证明；
  - RPC 或状态不可信；
  - 动态费率池进入候选、推荐、备用或 Y-Z 观察角色。
- fixed fee 范围固定为 `0 < fee <= 30000`；该判断只允许存在于 `pool_manager` / `candidate_edge_pool_manager` 等 Rust authority。

## 2. 池角色来源映射

不新增统一角色枚举。角色继续由现有 manager 输出和表承载。

| 角色 | 唯一 authority | 权威表 / 输出 | 规则 |
|---|---|---|---|
| 候选池 | `candidate_edge_pool_manager` | `manager_dex_candidate_pool_recommendations`、RuntimeCore view `candidate_edge` | 每方向 TOP-5，动态刷新，不清理 |
| 推荐池 | `candidate_edge_pool_manager` | `manager_dex_candidate_pool_recommendations.rank=1`、pool pair 的 primary pool | 每方向恰好 TOP-1，是候选的子集，不清理 |
| 备用池 | `pool_manager` | `pool_role_assignment` 中 role=`backup`；`pool_trade_activity` | 最近 3 个自然日有交易且满足深度/固定费率/hook 限制；默认不清理 |
| Y-Z 观察池 | `candidate_exchange_pool_manager` | `manager_market_observation_yz_pairs`、`manager_market_observation_intermediate_pools`、RuntimeCore view `candidate_exchange` | 观察池不直接成为广播路径；排除动态费率和不合格 hook |

### 2.1 多角色池

- 同一 pool 可同时是候选、推荐、备用角色。
- 清理逻辑必须按**角色集合**保护；只要池在任一受保护角色集合中，就不得被备用池清理删除。
- 候选/推荐刷新与备用池清理必须使用同一个 pool state version。
- pool role authority 只使用 `PoolRole::{Candidate, Recommendation, Backup}` 现有枚举。

## 3. 统一机器报告最小字段

所有阶段报告必须包含以下字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `mode` | string | 阶段/操作模式，例如 `audit`、`dry_run`、`copy_materialize` |
| `productionWrite` | boolean | 是否写生产数据库；未授权时必须为 `false` |
| `inputVersions` | object | 输入数据、schema、工具和状态版本 |
| `outputVersions` | object | 输出数据、schema、工具和状态版本 |
| `rowCount` | integer | 验证范围内的行数 |
| `checksum` | string | 确定性内容 checksum |
| `status` | string | 固定为 `passed`、`blocked` 或 `failed` |
| `failureCodes` | array[string] | 机器可判定失败原因；成功时为空数组 |
| `rollbackPoint` | string | 回滚点或 `none`；只读阶段不得写生产 |
| `rpcRequestCount` | integer | 本阶段 RPC 请求总数；纯本地阶段为 0 |
| `testCommands` | array[string] | 实际执行的验证命令 |
| `failureSummary` | array[string] | 失败集合摘要；必须可追溯到失败项 |

### 3.1 状态语义

- `passed`：所有必需断言通过，且无阻断项。
- `blocked`：因外部条件、授权、状态或依赖未满足而未执行/未完成；不是通过。
- `failed`：已执行但断言不成立、结果不确定或出现回归。

### 3.2 `productionWrite=true` 附加要求

- 授权文本引用；
- 目标数据库绝对路径；
- 备份路径和恢复命令；
- 写入前后 row count 与 checksum；
- 执行窗口和执行者。

缺少任一项时报告不得标记 `passed`。

## 4. 阶段退出标准

- 版本 1 范围、角色映射、证据字段与源码/schema 一致。
- 候选/推荐/备用/Y-Z 四类角色均可从其 Rust authority 追溯。
- 多角色池保护逻辑存在且有测试。
- 所有 P0 报告满足最小字段，且 `productionWrite=false`。
- 跨 tick、状态漂移、容量未知均有 fail-closed 证据。

## 5. 验收结果

- 范围：已冻结。
- 角色来源：已映射到现有 Rust manager/表。
- 证据 schema：已冻结。
- 生产写入：无。