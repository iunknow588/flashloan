# Robinhood Chain 部署状态

更新时间：2026-10-17

## 结论

Robinhood Chain 已完成“链配置 + 只读观察 + shadow 候选展示”接入；主网尚未开放可执行广播。

当前正式运行边界：

```text
deploymentLevel = framework_and_observation_shadow
scope            = observation_shadow_only
executionEnabled = false
broadcastEnabled = false
observationReady = true
dexExecutionReady = false
liquidationReady = false
productionReady = false
```

## Uniswap V4 合约地址（主网/测试网一致）

根据公开部署记录确认，Robinhood Chain 主网和测试网的 Uniswap V4 合约地址相同：

| 合约 | 主网 4663 | 测试网 46630 |
|---|---|---|
| V4 PoolManager | `0x8366a39CC670B4001A1121B8F6A443A643e40951` | `0x8366a39CC670B4001A1121B8F6A443A643e40951` |
| V4 Quoter | `0x8dc178efb8111bb0973dd9d722ebeff267c98f94` | `0x8dc178efb8111bb0973dd9d722ebeff267c98f94` |
| PositionManager | `0x58DaEC3116AAe6D93017BAAEA7749052E8a04fa7` | `0x58DaEC3116AAe6D93017BAAEA7749052E8a04fa7` |
| Permit2 | `0x000000000022D473030F116dDEE9F6B43aC78BA3` | `0x000000000022D473030F116dDEE9F6B43aC78BA3` |
| UniversalRouter | `0x8876789976dEcBfCbBbe364623C63652db8C0904` | `0x8876789976dEcBfCbBbe364623C63652db8C0904` |

注意：地址相同不代表状态、资产或交易互相同步。主网和测试网仍是独立链，必须分别用各自 RPC、浏览器和链状态。

## 已完成

| 层级 | 状态 | 代码/证据 |
|---|---|---|
| Robinhood 主网/测试网配置 | 已完成 | `flashloan/src/chains/robinhood/chain.json`、`flashloan/src/chains/robinhood-testnet/chain.json` |
| 主网 V4 合约地址写入 | 已完成 | `flashloan/src/chains/robinhood/chain.json`、`contract/robinhood/DEX/config/*.json` |
| V4 报价适配器链感知 | 已完成 | `flashloan/src/dex/intent_trade/chains/robinhood/v4_quote_adapter.py`、`flashloan/src/dex/intent_trade/chains/robinhood_testnet/v4_quote_adapter.py` |
| 默认测试网入口 | 保持 | `flashloan/src/chains/registry.json` 默认 `46630` |
| 主网只读 shadow | 进行中 | 主网 V3 可进入只读报价；主网 V4 依赖池索引证据后再启用 |

## 执行阻断

以下条件任何一个未满足，都必须保持执行和广播关闭：

```text
robinhood_dex_factory_source_unverified
robinhood_pool_discovery_evidence_missing
robinhood_live_quote_unverified
robinhood_executor_unverified
robinhood_flashloan_provider_unverified
robinhood_fork_simulation_unverified
robinhood_lending_protocol_unverified
robinhood_liquidation_adapter_not_started
```

## 上线判定

| 判定 | 当前结果 |
|---|---|
| Robinhood 链接入 | 通过 |
| Robinhood 只读观察 | 通过 |
| Robinhood DEX shadow | 通过 |
| Robinhood DEX live quote | 未通过（需完成池索引/报价器证据） |
| Robinhood DEX 自动执行 | 未通过 |
| Robinhood 清算 | 未开始 |
| Robinhood 全量生产部署 | **未完成** |

## 2026-09-28 主网切换就绪结论

当前不能从 Robinhood 测试网直接切换到主网执行/广播。主网保持 `executionEnabled=false`、`broadcastEnabled=false`、`readyForPausedDeploy=false`。

已完成或已推进：

- 主网 `4663` RPC、chainId、V4 PoolManager/UniversalRouter/Permit2 合约代码预检通过。
- 主网合约包定向测试通过：`npm run test:robinhood:mainnet` 为 `25 passing`。
- 主网只读 V4 池索引工具和多链 CLI `mainnet-refresh-v4-index` 已落地。
- 主网 bridge-pairs 默认路由已改为从真实 `v4_pool_index` 推断，不再使用主网索引缺失的 `WETH -> ETH -> 0XC9F9C8 -> WETH` 假路由。

仍阻塞：

- 最新 evidence 显示部署方余额为 `0`，blocking reasons：`mainnet_broadcast_gate_closed`、`deployer_balance_insufficient`。
- 主网 V4 池 `liquidity`、`tvlUsd` 证据不足；真实主网逐跳 Quoter/static-call 盈利闭环尚未验证。
- 主网 paused executor 未部署、未 readback code hash；fork/static-call、生产审批、SLO/告警/演练记录未完成。

## 2026-10-17 执行层与 V4 SQLite 接入

主网/测试网生产边界不变，但代码层执行原语已经落地：

- Rust/PyO3 执行原语：`flashloan/rs-src/src/execution.rs`，在 `flashloan/rs-src/src/lib.rs` 注册。
- 7 个原语：`resolve_private_key_hex`、`sign_typed_transaction_json`、`build_v4_quote_calldata`、`quote_v4_exact_input_single`、`submit_v4_route`、`load_v4_receipt`、`get_v4_pending_nonce`。
- Python 薄封装：`flashloan/src/core/rs_bridge.py`；`rust_available()` 为真时正式路径优先 Rust，Rust 不可用时只保留只读/显式兼容分支。
- 页面回执：`flashloan/src/dex/runtime/dex_route_rpc.py::fetch_dex_tx_receipt` 优先 `load_v4_receipt`。
- 广播：`flashloan/src/dex/intent_trade/direct_unified_broadcast.py` 使用 Rust 签名、广播和回执；Python 兼容分支在 `direct_unified_broadcast_py_fallback.py`。
- V4 报价：主网/测试网 `v4_quote_adapter.py` 在 Rust 可用时使用 Rust calldata + `eth_call`，Python 适配层继续负责上下文解析、重试和错误归类。
- 架构守卫：`flashloan/app_runtime/tests/unit/test_architecture_guards.py` 强制 Rust-first 广播接线，并禁止正式广播路径顶层 `eth_account.Account` 广播。

验收结果：

- `cargo test`：`27 passed`
- `pytest flashloan/app_runtime/tests/unit`：`1621 passed`
- `compileall`：通过
- `build_v4_integrity_report`：主网 `4663` 与测试网 `46630` 均为 `0 violations`

验收记录：`flashloan/docs/p3_4_p3_5_p4_p5_acceptance.md`。
