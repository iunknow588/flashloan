# 环境参数分层分析（.env.example 全量普查）

- 生成日期：2026-11-16
- 分析对象：`flashloan/app_runtime/.env.example`（272 个有效键）
- 代码扫描范围：`flashloan/src`、`flashloan/app_runtime`、`scripts`、`flashloan/tools`、`contract`（1,497 个源文件）
- 机器报告：`plan/runtime_evidence/reports/env_parameter_layering_20261116.json`

## 0. 结论摘要

| 分类 | 键数 | 处置 |
|---|---|---|
| G-全局（跨链通用策略/运维） | 226 | **保留**在 `flashloan/app_runtime/.env` |
| C-链专属（只对某一条链有意义） | 35 | **下沉**到 `flashloan/chains/<network>/chain.json`，env 仅作 override |
| S-链专属且为密钥 | 5 | **留在** `app_runtime/.env`（私钥不入库、不进版本库） |
| 失效别名（无任何引用） | 1 | **移除**：`ROBINHOOD_HyperRPC_URL` |
| 另有 5 项为 COW 生态多链 owner，属独立命名空间 | 5 | 保留在 `.env`，**不下沉**（非本项目链） |

关键事实：**272 个键中 271 个被代码实际引用**，不存在"整段无效"的配置块。
问题不在于键多，而在于**链专属键与全局键混存在同一个文件**，导致不同组件可能读到不同链的地址/RPC。

## 1. 两份 .env 的来源与危害（已修复）

| 文件 | 创建 | 归属 | 状态 |
|---|---|---|---|
| `flashloan/app_runtime/.env` | 2026-08-04 | 本项目正式配置 | 保留（唯一权威） |
| `E:\2026OPC大赛\.env` | 2026-09-05 | 共享父目录遗留（父目录含 10+ 其他项目且自带 `.git`） | **代码层已阻断** |

- `core/env_loader.py::candidate_env_paths()` 原先一路向上遍历到文件系统根，父目录 `.env` 被真实加载。
- 父目录文件额外贡献 **32 个** 本项目未定义的键，其中高危项：
  - 5 个第三方 API Key（`DUNE_API_KEY` / `ETHERSCAN_API_KEY` / `BSCSCAN_API_KEY` / `PINAX_API_KEY` / `PINAX_API_KEY_ID`）
  - `PINAX_JWT`（439 字节）
  - `DATABASE_URL=postgres://...` —— 与本项目 SQLite 架构冲突
  - 9 个已退役 `TRIANGULAR_*` 合约地址
  - `DATABASE_POOL_ENABLED=true`
- **已修复**：向上遍历在仓库根停止（`find_repo_root()` 作为 stop），父目录 `.env` 不再被加载。
- **待人工处理**：`E:\2026OPC大赛\.env` 仍在磁盘上（父目录不可写，沙箱无法改名）。
  建议手工执行 `E:\2026OPC大赛\.env` → `E:\2026OPC大赛\.env.bak-20261116`。

## 2. G-全局键（226 项）——必须保留在 app_runtime/.env

这些是**跨链策略、权限门禁、行情采集、广播熔断**等系统级参数，与具体链无关。

| 组 | 键数 | 举例 |
|---|---|---|
| 权限/Authority 门禁 | 7 | `TOKEN_MANAGER_AUTHORITY`、`CANDIDATE_MANAGER_AUTHORITY`、`CANDIDATE_EXCHANGE_MANAGER_AUTHORITY`、`BROADCAST_MANAGER_AUTHORITY`、`MANAGER_UI_DATA_SOURCE`、`MANAGER_API_BASE_URL`、`MANAGER_API_TIMEOUT_SECONDS` |
| 活跃链选择 | 3 | `DEX_ACTIVE_CHAIN_ID`、`DEX_ACTIVE_NETWORK`、`FLASHLOAN_CHAIN_DATABASES` |
| 控制面板/清算开关 | 4 | `CONTROL_PANEL_DEFAULT_FEATURE`、`LIQUIDATION_UI_SCAN_ENABLED`、`LIQUIDATION_BACKGROUND_REFRESH`、`BNB_VENUS_WATCHER_ENABLED` |
| Binance 行情 | 18 | `BINANCE_WS_BASES`、`BINANCE_REST_BASES`、`BINANCE_SCAN_PROFILE`、`BINANCE_VELOCITY_SIDE_LIMIT` … |
| 套利策略阈值 | 10 | `ARBITRAGE_NOTIONAL_USD`、`ARBITRAGE_TARGET_PROFIT_PERCENT`、`ARBITRAGE_MIN_PAPER_PROFIT_USD` … |
| 清算策略阈值 | 63 | `LIQUIDATION_CLOSE_FACTOR`、`LIQUIDATION_BONUS_PERCENT`、`LIQUIDATION_MAX_DEBT_TO_COVER` … |
| DEX 扫描/闭环 | ~20 | `DEX_CLOSED_LOOP_MAX_WORKERS`、`DEX_OPPORTUNITY_SCAN_INTERVAL_SECONDS`、`DEX_AUTO_EXECUTE_COOLDOWN_SECONDS` … |
| V4 后台 worker 周期 | 9 | `V4_POOL_LIQUIDITY_WORKER_INTERVAL_SECONDS`、`V4_BROADCAST_EXECUTION_WORKER_INTERVAL_SECONDS` … |
| 熔断/观察器 | 10 | `DIRECT_ONCHAIN_CIRCUIT_BREAKER_*`、`OBSERVER_*` … |
| 运行时目录 | 1 | `FLASHLOAN_RUNTIME_DIR`（= `../src/runtime`，权威运行时数据目录） |
| COW 生态多链 | 35 | `COW_OWNER_*`（12 条 CoW 支持链）、`COW_FLASHLOAN_*` — **独立命名空间，非本项目链，不下沉** |

> **重要区分**：`LIQUIDATION_*` 虽有 63 个键，但**不是**链专属 ——
> 它是清算策略参数（close factor、bonus、gas 预算），切换链不应改变清算风控语义。
> 链专属的是**地址**与**RPC**，不是**阈值**。

## 3. C-链专属键（35 项）——下沉到 chain.json

判据：该键的**值是某条链的地址或 RPC 端点**，且消费者只在一条链的上下文里生效。

### 3.1 Robinhood 主网（chain_id 4663）→ `flashloan/chains/robinhood/chain.json`

| env 键 | 目标字段 | chain.json 现状 | 处置 |
|---|---|---|---|
| `ROBINHOOD_RPC_URL` | `rpc.primary` | 已存在 `https://rpc.mainnet.chain.robinhood.com` | env 降级为 override |
| `ROBINHOOD_RPCS` | `rpc.fallbacks` | 已存在 1 条 publicnode | 追加其余 |
| `ROBINHOOD_ALCHEMY_RPC_URL` | `rpc.alchemy` | **缺失，需新增** | 新增字段 |
| `ROBINHOOD_HYPERRPC_URL` | `rpc.hyperrpc` | **缺失，需新增** | 新增字段 |
| `ROBINHOOD_MAINNET_RPC_URLS` | `rpc.overrides` | **缺失，需新增** | 保留 env override 语义 |
| `ROBINHOOD_MAINNET_WSS_URLS` | `rpc.wss` | **缺失，需新增** | 新增字段 |
| `ROBINHOOD_V3_QUOTER_ADDRESS` | `contracts.v3_quoter` | 已存在 | env 降级为 override |
| `ROBINHOOD_V3_QUOTER_ADDRESSES` | `contracts.v3_quoter` | 同上（别名） | 同上 |
| `DEX_V3_QUOTER_ADDRESS` | `contracts.v3_quoter` | 同上（别名） | 同上 |
| `DEX_ROUTER_ADDRESS` | `contracts.v3_router` | 已存在 `0xCaf681a6…` | env 降级为 override |
| `ROBINHOOD_MAINNET_OFFICIAL_V4_ARB_ADDRESS` | `officialV4.arb` | 已存在 | env 降级为 override |
| `ROBINHOOD_MAINNET_OFFICIAL_V4_COMPOSER_ADDRESS` | `officialV4.composer` | 已存在 | env 降级为 override |
| `ROBINHOOD_MAINNET_V4_QUOTE_ADAPTER` | `defaults.v4_quote_adapter` | 已存在 | env 降级为 override |
| `ROBINHOOD_MAINNET_V4_SDK_ADAPTER` | `defaults.v4_sdk_adapter` | **缺失，需新增** | 新增字段 |
| `UNISWAP_V4_QUOTE_ADAPTER` / `UNISWAP_V4_SDK_ADAPTER` | 同上（通用别名） | — | 同上 |
| `DEX_V4_QUOTE_ADAPTER` / `DEX_V4_SDK_ADAPTER` | 同上（通用别名） | — | 同上 |
| `ROBINHOOD_MAINNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST` | `features.dex_arbitrage.allow_official_v4_broadcast` | **缺失，需新增** | 新增字段（**广播闸门**，须谨慎） |

### 3.2 Robinhood 测试网（chain_id 46630）→ `flashloan/chains/robinhood-testnet/chain.json`

| env 键 | 目标字段 | chain.json 现状 | 处置 |
|---|---|---|---|
| `ROBINHOOD_TESTNET_RPC_URL` | `rpc.primary` | 已存在 | env 降级为 override |
| `ROBINHOOD_TESTNET_RPCS` | `rpc.fallbacks` | 已存在空数组 | 追加其余 |
| `ROBINHOOD_TESTNET_ALCHEMY_RPC_URL` | `rpc.alchemy` | **缺失，需新增** | 新增字段 |
| `ROBINHOOD_TESTNET_OFFICIAL_V4_ARB_ADDRESS` | `officialV4.arb` | 已存在 | env 降级为 override |
| `ROBINHOOD_TESTNET_OFFICIAL_V4_COMPOSER_ADDRESS` | `officialV4.composer` | 已存在 | env 降级为 override |
| `ROBINHOOD_TESTNET_V4_QUOTE_ADAPTER` | `defaults.v4_quote_adapter` | 已存在 | env 降级为 override |
| `ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST` | `features.dex_arbitrage.allow_official_v4_broadcast` | **缺失，需新增** | 新增字段（**广播闸门**） |

### 3.3 Avalanche / BNB

| env 键 | 目标链 | 目标字段 | chain.json 现状 |
|---|---|---|---|
| `AVALANCHE_RPCS` | avalanche | `rpc.fallbacks` | 已存在 2 条 public |
| `BNB_RPCS` | bnb | `rpc.fallbacks` | 已存在 |
| `BNB_LOG_RPCS` | bnb | `rpc.log_fallbacks` | **缺失，需新增** |
| `FUJI_USDC_ADDRESS` | avalanche-testnet | `contracts.usdc` | 已存在 `0x54258902…`（大小写差异需统一） |
| `AAVE_POOL_ADDRESS` | avalanche | `contracts.aave_v3_pool` | **缺失，需新增** |
| `AAVE_PROTOCOL_DATA_PROVIDER_ADDRESS` | avalanche | `contracts.aave_v3_data_provider` | **缺失，需新增** |
| `AAVE_LIQUIDATION_DATA_PROVIDER_ADDRESS` | avalanche | `contracts.aave_liquidation_data_provider` | **缺失，需新增**（当前为空） |

> `AVALANCHE_RPCS` 在 `.env.example` 注释中已声明 "Public defaults come from
> `flashloan/src/chains/avalanche/chain.json`" —— 迁移只是把注释变成事实。

## 4. S-链专属但为密钥（5 项）——留在 .env

私钥**不得**写入 `chain.json`（该目录进版本库）。

| env 键 | 作用域 |
|---|---|
| `ROBINHOOD_MAINNET_DEX_PRIVATE_KEY` | robinhood 主网 |
| `ROBINHOOD_TESTNET_DEX_PRIVATE_KEY` | robinhood 测试网 |
| `DEX_EXECUTION_PRIVATE_KEY` | 当前活跃链 |
| `OFFICIAL_V4_PRIVATE_KEY` | 当前活跃链 |
| `LIQUIDATION_EXECUTION_PRIVATE_KEY` | 当前活跃链 |
| `COW_ORDER_SIGNER_PRIVATE_KEY` | CoW 链 |

处理原则：`.env.example` 中**留空占位**（不放真实值），`chain.json` 中**不放私钥字段**。

## 5. 失效项（1 项）

| env 键 | 判定 | 处置 |
|---|---|---|
| `ROBINHOOD_HyperRPC_URL` | 全仓库 0 引用；是 `ROBINHOOD_HYPERRPC_URL` 的大小写笔误副本 | **移除**，避免同一语义两个键名 |

## 6. 下沉后的解析优先级（统一约定）

```
env (.env / 链级 .env)   ← 最高优先级，运维临时覆盖
      ↓ 未设置时
flashloan/chains/<network>/chain.json   ← 链的权威配置（进版本库）
      ↓ 未设置时
代码内默认值
```

这与既有 `dex/intent_trade/chains/robinhood/config.py::_chain_value()` /
`_chain_contract_value()` 的既有语义**完全一致**（env 优先 → chain.json → 默认值），
因此下沉 **不改变任何现有解析结果**，只消除"同一语义散落在两处"的问题。

## 7. 与"统一路径信息"的关系

| 类别 | 权威位置 | 说明 |
|---|---|---|
| 链级配置（地址/RPC/适配器/闸门） | `flashloan/chains/<network>/chain.json` | 本轮下沉目标 |
| 链注册表 | `flashloan/chains/registry.json` | 已有 |
| 跨链全局配置 | `flashloan/app_runtime/.env` | 本轮保留 |
| Foundry 生成配置 | `flashloan/config/foundry.chain.toml` | 第 20 轮已从 `runtime/external/` 迁移至此 |
| 权威运行时数据目录 | `flashloan/src/runtime/` | 2391 文件 / 69.2 MB，含权威 `parameters/*.json` |
| 孤儿运行时目录 | `flashloan/app_runtime/runtime/` | 完全未跟踪、内容已与权威副本发散，建议删除 |

## 8. 本轮不做（安全边界）

- 不修改主网库 `flashloan/db/local_store_4663.sqlite3`（size 3,507,453,952 / mtime 不变）。
- 不改合约字节码，不部署，不广播，不切网络，不切广播 Authority。
- `manager_v4_reviews` 保持 0；healer 保持 `review_stalled`、`safeToAutoBroadcast=false`。
- 广播闸门类键（`*_ALLOW_OFFICIAL_V4_ARB_BROADCAST`）下沉时**保持现有 env 值优先**，
  不改默认值语义，避免意外开启主网广播。
