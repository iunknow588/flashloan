# Robinhood Chain 文档索引

更新时间：2026-10-17

## 当前结论

Robinhood Chain（主网 `4663` / 测试网 `46630`）已经完成链配置、只读观察、V4 SQLite 池索引与 Rust/PyO3 执行原语接入。当前生产边界仍然 fail-closed：

- 主网：`observation_shadow_only`，`executionEnabled=false`、`broadcastEnabled=false`、`productionReady=false`。
- 测试网：可走 Rust-first 报价、签名、广播与回执路径，但仍受执行 gate、gas guard、事件解码与 circuit record 约束。
- `robinhood_dex_factory_source_unverified`、`robinhood_executor_unverified`、`robinhood_flashloan_provider_unverified`、`robinhood_fork_simulation_unverified` 任一未解除前，本地测试通过不能代表生产 readiness。

## 文档地图

| 文档 | 回答的问题 | 状态 |
|---|---|---|
| [Robinhood Chain 部署状态.md](<Robinhood Chain 部署状态.md>) | 当前链配置、合约地址、只读/执行边界 | 最新 |
| [Robinhood Chain 多链重构执行checklist.md](<Robinhood Chain 多链重构执行checklist.md>) | 已完成项、未完成项、执行阻断 | 持续更新 |
| [后续完善开发计划.md](<后续完善开发计划.md>) | 未完成任务、分阶段执行计划与完成定义 | 计划/持续更新 |
| [Robinhood Chain 扩展可行性与免Gas政策调研计划.md](<Robinhood Chain 扩展可行性与免Gas政策调研计划.md>) | 能不能扩展、免 Gas 的真实边界 | 调研 |
| [Robinhood Chain DEX套利与多链重构方案.md](<Robinhood Chain DEX套利与多链重构方案.md>) | 架构目标、DB/状态机、重构方案 | 方案与历史落点 |
| [Robinhood Chain行情与套利可行性报告_20260822.md](<Robinhood Chain行情与套利可行性报告_20260822.md>) | 2026-08-22 主网行情快照 | 历史快照 |
| [Robinhood Chain 当前行情与闪电贷套利分析_20260822.md](<Robinhood Chain 当前行情与闪电贷套利分析_20260822.md>) | 2026-08-22 报价/闭环结论 | 历史快照 |
| [最佳路线选择说明.md](<最佳路线选择说明.md>) | 闭环、候选池、最佳路线、临时路径 | 最新 |
| [股票代币/综述.md](<股票代币/综述.md>) | 股票 token 来源与地址验证 | 最新 |
| [股票代币/实施方案.md](<股票代币/实施方案.md>) | 股票 token 接入数据链路 | 最新 |
| [P3-4/P3-5/P4/P5 Acceptance Record](../../flashloan/docs/p3_4_p3_5_p4_p5_acceptance.md) | Rust 执行层与验收结果 | 最新 |

建议阅读顺序：先看部署状态，再按 checklist 判断完成边界；架构和调研文档用于理解设计意图；行情报告是固定日期快照，不作为当前状态；最佳路线与股票代币文档跟随最近实现。

## 当前实现快照

### 链与数据

- 主网配置：`../../flashloan/src/chains/robinhood/chain.json`
- 测试网配置：`../../flashloan/src/chains/robinhood-testnet/chain.json`
- 链注册表：`../../flashloan/src/chains/registry.json`
- V4 池索引、流动性、候选池、市场观察、DEX 总览、报价路由数据统一进入 SQLite 与 `flashloan/src/runtime/evidence/robinhood*`。
- `4663` 与 `46630` 已重建真实池索引并通过 integrity report 的 `0 violations` 验收。

### Rust/PyO3 执行层

- Rust 执行原语：`../../flashloan/rs-src/src/execution.rs`
- Pyo3 注册：`../../flashloan/rs-src/src/lib.rs`
- Python 薄封装：`../../flashloan/src/core/rs_bridge.py`
- 7 个原语：
  - `resolve_private_key_hex`
  - `sign_typed_transaction_json`
  - `build_v4_quote_calldata`
  - `quote_v4_exact_input_single`
  - `submit_v4_route`
  - `load_v4_receipt`
  - `get_v4_pending_nonce`
- `rust_available()` 为真时正式路径优先走 Rust；Rust 不可用时执行类只保留只读/显式兼容分支，不引入 Python 写库回退。

### 页面与执行接线

- 页面回执：`fetch_dex_tx_receipt` 优先 `load_v4_receipt`，Python 只负责合并链元数据与页面字段。
- 广播：`../../flashloan/src/dex/intent_trade/direct_unified_broadcast.py` 使用 Rust 签名、广播与回执；显式 fallback 在 `direct_unified_broadcast_py_fallback.py`。
- V4 报价：主网/测试网 `v4_quote_adapter.py` 在 Rust 可用时使用 Rust calldata + `eth_call`，上下文解析、重试与错误归类仍由 Python 适配层负责。
- 架构守卫：`../../flashloan/app_runtime/tests/unit/test_architecture_guards.py` 强制 Rust-first 广播接线，禁止正式广播路径在模块顶层直接使用 `eth_account.Account` 做广播。

## 代码路径迁移

旧文档中的 `flashloan/src_bot` 已迁移到当前 `flashloan/src` 与 `flashloan/app_runtime`。历史报告保留原文，但其中的 `src_bot` 路径应替换为：

- `flashloan/src/dex/intent_trade/chains/robinhood/*`
- `flashloan/src/dex/intent_trade/chains/robinhood_testnet/*`
- `flashloan/src/market/tools/*`
- `flashloan/app_runtime/tests/unit/*`

## 最近验收

- `cargo test --manifest-path flashloan/rs-src/Cargo.toml`：`27 passed`
- `python -m pytest flashloan/app_runtime/tests/unit -q`：`1621 passed`
- `python -m compileall -q flashloan/src flashloan/app_runtime`：通过
- `build_v4_integrity_report`：主网/测试网均为 `0 violations`
- 详细记录见 [P3-4/P3-5/P4/P5 Acceptance Record](../../flashloan/docs/p3_4_p3_5_p4_p5_acceptance.md)
