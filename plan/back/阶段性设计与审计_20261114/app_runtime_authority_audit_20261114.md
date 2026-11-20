# app_runtime / Rust Authority 调用链审计（P0-0）

日期：2026-11-14  
范围：`flashloan/app_runtime`、`flashloan/src`、`flashloan/rs-src`、`flashloan/tools`  
模式：只读审计；`productionWrite=false`；未写生产数据库、未切换 Authority、未触发广播。

## 1. 结论

- 生产权威边界仍是 **Rust manager → RuntimeCore → Python thin adapter**。
- Python 可以读取 RuntimeCore manager view、构造并派发 RuntimeCore command、渲染页面与组织运维入口；不得自行决定 fee/token/pool/route/review/broadcast 业务结论。
- 当前仍有若干 Python 侧展示型、兼容型或历史导入型判断。本阶段只登记迁移责任，不删除兼容代码；只有当对应 Rust manager 的 view/command 覆盖、证据可比且回归通过后才允许退役。
- 精确 swap 数学的当前 authority 是 `pool-manager`。Candidate Edge Manager 复用 `pool-manager` 的容量/精确输出数学，不保存第二份实现。

## 2. 责任矩阵与调用链

| 功能域 | 唯一 authority | Rust 入口 | RuntimeCore / Manager API | Python 入口 | 最终状态 |
|---|---|---|---|---|---|
| Token 分类、准入、审计 | `token_manager` | `token-manager` crate | Manager API `/v1/tokens*`；RuntimeCore overview | `app_runtime/web/data_routes/manager_*_payload.py` | `chain_tokens` / token library 表 |
| Pool scope、链状态、深度、single-tick 容量 | `pool_manager` | `pool-manager` crate | RuntimeCore `pool_roles_manage`、`pool_measurement_population`、`pool_resolve_keys`；API `/v1/pools/scopes*` | `manager_*_payload.py`、页面 GET | `v4_exchange_pools*`、pool role / activity 表 |
| 候选与推荐排序 | `candidate_edge_pool_manager` | `candidate-edge-pool-manager` crate | RuntimeCore `rebuild_candidate_edges`、view `candidate_edge`；API `/v1/candidate-edge-pools*` | `manager_candidate_payload.py` | `manager_dex_candidate_*` / candidate shadow 表 |
| Y-Z 观察池与候选 exchange | `candidate_exchange_pool_manager` | `candidate-exchange-pool-manager` crate | RuntimeCore `rebuild_candidate_exchange_pools`、view `candidate_exchange` | `manager_candidate_refresh_payload.py`、`manager_market_observation_payload.py` | `manager_market_observation_*` |
| 闭环组合 | `closed_loop_manager` | `closed-loop-manager` crate | RuntimeCore view `closed_loops`；API `/v1/closed-loops*` | `manager_closed_loop_payload.py` | closed-loop / hop / optimization 表 |
| 复核与收益判定 | `review_manager` | `review-manager` crate | RuntimeCore views `reviews`、`review_parity`；API `/v1/reviews*` | `manager_submission_payload.py` | review / parity 表 |
| 广播 | `broadcast_manager` | `broadcast-manager` crate | RuntimeCore `clear_broadcasts`、view `broadcasts`；API `/v1/broadcasts*` | `manager_submission_execution_payload.py`、`dex_surface_routes_ops.py` | broadcast attempt / diagnostic 表 |
| 组合编排 | `composite_manager` | `composite-manager` crate | Manager API pipeline 路由 | manager pipeline payload | pipeline / composite 状态 |

### 2.1 Python 到 Rust 的实际桥接

1. `flashloan/src/core/runtime/chain_runtime.py` 的 `ChainRuntimeContext.manager_view()` 调用 PyO3 `runtime_core_manager_view()`。
2. `ChainRuntimeContext.execute_command()` 调用 PyO3 `runtime_core_command()`，并保留 `dryRun`、幂等键与请求绑定校验。
3. `flashloan/app_runtime/web/data_routes/manager_page_compare_common.py` 统一包装 manager view envelope；页面数据路由不得自行读取业务表作为生产真相。
4. `flashloan/rs-src/src/runtime_core.rs` 将 view/command 路由到对应 Rust manager；RuntimeCore command 是 Python 写操作的唯一允许边界。
5. `flashloan/tools/manager_api/src/lib.rs` 提供诊断与兼容 HTTP 边界；页面写操作仍应优先 RuntimeCore command。

## 3. RuntimeCore 覆盖面

已识别的 RuntimeCore command：

- `refresh_snapshot`
- `rebuild_candidate_edges`
- `rebuild_candidate_exchange_pools`
- `review_parity_compare`
- `review_shadow_compare`
- `pool_roles_manage`
- `pool_measurement_population`
- `pool_resolve_keys`
- `review_parity_replay`
- `clear_broadcasts`

已识别的 RuntimeCore view：

- `overview`
- `candidate_edge`
- `candidate_exchange`
- `closed_loops`
- `reviews`
- `review_parity`
- `broadcasts`

未识别到 RuntimeCore command 的 manager 域必须在迁移前先补齐 Rust command，而不是由 Python 计算。

## 4. Python 待迁移/保留表

| Python 位置 | 当前判断 | 目标 authority | 处置 | 删除时机 |
|---|---|---|---|---|
| `app_runtime/web/data_routes/manager_candidate_payload.py` | 由 fee 位推导 dynamic/special 标签 | `candidate_edge_pool_manager` / `pool_manager` | 先保留为展示兼容；禁止据此改变候选/推荐角色 | Rust view 输出同字段且页面契约回归通过 |
| `app_runtime/web/data_routes/manager_market_observation_payload.py` | 计算 `usable`，综合 abnormal、dynamic fee、hook | `candidate_exchange_pool_manager` | 降级为展示投影；角色判断由 Rust 输出 | Rust view 提供 `usable`/reason 后迁移 |
| `app_runtime/web/data_routes/dex_surface_routes_quotes.py` | fee 位解码、fee label | `pool_manager` / 对应 manager view | 展示兼容 | Rust view 提供稳定字段后迁移 |
| `src/core/db/page_payloads.py` | fixed-fee candidate、fee 范围、hook 判定 | `pool_manager` / candidate manager | 仅 legacy payload/比较用途 | legacy 表退场且页面改用 manager view 后退役 |
| `src/core/db/builders.py`、`import_existing_json.py` | legacy 表构建与导入准入 | `manager_db` / Rust materializer | 标记 legacy/import-only | legacy authority 正式退场后移除 |
| `src/core/db/direction_pool_score.py` | legacy 方向打分 | `candidate_edge_pool_manager` | 不用于新 manager 数据 | legacy candidate 表停写后移除 |
| `src/dex/runtime/dex_opportunity_scanner_rust_mixin.py` | 从 RuntimeCore overview/closed_loops 派生扫描视图 | RuntimeCore | 保留为消费方，不产生新业务结论 | 已满足；仅补架构守卫覆盖 |

## 5. SDK 决策记录（ADR）

### 5.1 `uniswap-v4-sdk`

结论：**采用，且限定于 `pool_manager` 的精确数学与 tick traversal authority**。

- `flashloan/tools/pool_manager/src/depth.rs` 使用 SDK 的 `full_math`、`sqrt_price_math` 等精确整数数学；SDK 路径是 primary，fallback 仅用于保持 parity/可用性并已有 parity 测试。
- `flashloan/tools/pool_manager/src/tick_swap.rs` 直接使用 `TickDataProvider` 与 `v3_swap`。
- tick 槽位（state slot、ticks offset 4、bitmap offset 5）按 `PoolManagerLens` 约定**手工实现**；源码没有直接实例化 `PoolManagerLens`。
- 未发现生产代码直接采用 `SimpleTickDataProvider`；不得把它写成已采用能力。
- 版本声明：`uniswap-v4-sdk = 2.0`，解析版本 `2.0.0`，features `extensions,std`；workspace Rust 最低版本 1.91。
- 许可证：MIT；仓库 `https://github.com/shuhuiluo/uniswap-v4-sdk-rs`。证据来自 cargo registry 的 `uniswap-v4-sdk-2.0.0/Cargo.toml`。

### 5.2 `Dex-Math-Core-rs`

结论：**版本 1 不采用**。

仓库无依赖、引用、锁定版本或 parity 证据，因此当前不存在可执行的独立 benchmark 基线；若未来提出采用，必须先固定来源与版本，再提交 benchmark、逐样本 parity/舍入差异、许可证与维护性评估，并证明不会形成第二数学 authority。

### 5.3 `arena-core`

结论：**不进入版本 1 实时依赖**；仅作为后续离线压力测试、情景模拟与回测候选。

## 6. 风险与门禁

- Python 兼容判断仍存在，但当前不参与 Rust 候选/推荐物化；在完成 view 字段对齐前不得删除。
- 页面展示字段与 Rust authority 字段不一致时，页面必须标记兼容来源，不得静默覆盖 manager 状态。
- 后续任何新增 Python fee/token/pool/route/review/broadcast 判断必须被 `app_runtime/tests/unit/test_architecture_guards.py` 阻止。

## 7. 验收结果

- 调用链清单：完成。
- Python 待迁移表：完成。
- SDK 版本/许可证/采用决策：完成；`Dex-Math-Core-rs` 因无依赖/无 parity 证据记录为“不采用”。
- 生产写入：无；`productionWrite=false`。
