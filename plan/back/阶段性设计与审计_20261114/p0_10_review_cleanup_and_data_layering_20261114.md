# P0-10 瞬时记录清理与数据分层收口（2026-11-14）

## 1. 用户裁决

复核、提交、广播结果属于可再生的瞬时运行记录，不具备长期统计意义；
真正具有长期价值的是代币基础数据与池基础数据。价格与链上状态实时变化，
但基础信息不变；只要系统结构正确，实时数据可以随时重新生成大量有效信息。

## 2. 架构结论

- token 元数据、分类、准入状态、库范围是长期权威事实，由 `token_manager` 管理。
- pool 元数据、PoolKey、费率/hook、scope 是长期权威事实，由 `pool_manager` 管理。
- candidate/closed-loop 是结构性派生数据，可以版本化重建。
- review/submission/broadcast 是瞬时运行记录，不再扩展长期持久化 schema。
- 实时价格、流动性、tick 与容量按需重算，不作为长期事实留存。

## 3. 已完成实现

- 撤销 `20261114_010_hop_amount_out_minimum_schema`，最新 Manager 迁移保持为
  `20261114_009_hop_price_limit_schema`。
- `review_manager::clear_review_records_write` 支持 dry-run/apply、幂等键、
  `manager`/`legacy` 双 store 与精确 network/chain scope。
- `POST /v1/reviews/clear` 提供正式 HTTP 清理入口。
- `RuntimeCore` 新增 `clear_reviews` 命令。
- Python 运维入口 `POST /api/dex/submissions/clear` 支持 `recordKind=review`，
  只代理 RuntimeCore，不直接写业务库。

## 4. 数据清理结果

| 环境 | 清理前 review | 清理数量 | 清理后目标 scope | token 目录 | pool 目录 |
|---|---:|---:|---:|---:|---:|
| 主网 `robinhood/4663` | 19,771 | 19,771 | 0 | 6,353 | 4,741 |
| 测试网 `robinhood-testnet/46630` | 52 | 52 | 0 | 47,661 | 47,702 |

主网另有 2 条其他 scope 记录：`('',0)` 1 条、`('bnb',56)` 1 条，已正确保留。

## 5. 安全边界

- 未广播交易。
- 未切换 Authority。
- 未替换被锁定二进制。
- 未写 token/pool 目录。
- 未删除其他 network/chain scope。
- 清理通过管理器接口执行，不使用 Python/SQLite 直写旁路。

## 6. 验证

- `review_manager` 清理专项：2 passed。
- `manager_api` 清理端点专项：1 passed。
- `rs-src`：112 passed。
- `flashloan/tools` workspace：全部测试通过（29 个测试二进制均为 ok）。
- Python 清理路由专项：10 passed。
- 广泛 Python 套件仍存在既有失败/缺失模块，与本次清理功能无关；本次专项未新增失败。

## 7. 证据

机器证据：
`plan/runtime_evidence/reports/p0_10_review_cleanup_and_catalog_preservation_20261114.json`
