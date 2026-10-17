# Robinhood Morpho Blue 借贷清算调研

更新时间：2026-10-17

## 1. 结论

Robinhood 主网 `4663` 已配置一个 Morpho Blue Core 候选地址，且该地址在链上存在代码，`market(bytes32)`、`position(bytes32,address)`、`flashLoan(address,uint256,bytes)`、`owner()` 四个选择器均已在本轮只读探测中确认存在。当前只做只读调研与观察，不开放任何广播。

尚未确认的内容：

- 该地址的独立官方来源 URL 与源码核验记录。
- 该实现是否等价于官方 Morpho Blue v1 ABI（部分标准 v1 函数/事件未在字节码静态常量中探测到，不能直接套用标准清算参数）。
- 主网市场列表、oracle、IRM、LLTV 与清算参数。
- 主网可清算账户清单与真实健康度。
- Morpho 清算 incentive / close factor 在主网部署上的具体语义。

## 2. 2026-10-17 只读探测记录

命令（不写库、不广播）：

```powershell
python flashloan/src/dex/tools/probe_robinhood_morpho_blue.py --rpc-url https://robinhood-rpc.publicnode.com --no-write
python flashloan/src/dex/tools/probe_robinhood_morpho_blue.py --rpc-url https://rpc.mainnet.chain.robinhood.com --recent-blocks 10000 --no-write
```

结果快照：

| 项目 | 值 |
|---|---|
| chainId | `4663` |
| 最新区块 | `90704491` |
| Morpho Blue Core 配置地址 | `0x9D53d5E3bd5E8d4Cbfa6DB1ca238AEA02E651010` |
| codeBytes | `15582` |
| codeHash | `0x753b54fdb48c87c9c1ba1dcc3a330faac1a401a3bd23b43104b7b92409ecb424` |
| `market(bytes32)` | 选择器 `0x5c60e39a`，字节码中存在 |
| `position(bytes32,address)` | 选择器 `0x93c52062`，字节码中存在 |
| `flashLoan(address,uint256,bytes)` | 选择器 `0xe0232b42`，字节码中存在 |
| `owner()` | 选择器 `0x8da5cb5b`，字节码中存在 |

### 近期事件活动（2026-10-17 实测）

区块范围 `90715221` 到 `90725220`，从 `0x9D53d5E3bd5E8d4Cbfa6DB1ca238AEA02E651010` 拉到 `47` 条日志：

| 事件 | 数量 |
|---|---:|
| `AccrueInterest(bytes32,uint256,uint256,uint256)` | `21` |
| `Supply(bytes32,address,address,uint256,uint256)` | `12` |
| `FlashLoan(address,address,uint256)` | `9` |
| `SupplyCollateral(bytes32,address,address,uint256)` | `3` |
| `Borrow(bytes32,address,address,address,uint256,uint256)` | `2` |

这证明该地址在最近区块中存在真实供应、借款、计息和 flashloan 活动，但仍不代表已完成官方源码核验或可执行清算。

探测器始终输出：

```text
executionMode    = readonly_probe
observationOnly  = true
executionEnabled = false
broadcastEnabled = false
```

## 3. ABI 与标准 Morpho Blue v1 的差异

当前地址具备 Morpho Blue 风格的只读与 flashloan 入口，但字节码静态探测没有发现以下标准 v1 常量：

- `lltv(bytes32)`、`isDeprecated(bytes32)` 函数选择器。
- `CreateMarket(...)` 事件 topic 常量。
- 完整的 `supply/borrow/withdraw/repay/liquidate` 标准选择器集合。

因此结论是“兼容 Morpho Blue 风格的提供方”，尚未达到“已核验为官方 Morpho Blue v1”。在拿到源码、官方地址来源和链上 code hash 复核前，不能把标准 Morpho Blue 清算参数直接作为本项目参数。

## 4. 健康度读取方案

已实现的只读原语：

- `read_morpho_blue_market`：调用 `market(bytes32)`，读取 `totalSupplyAssets`、`totalSupplyShares`、`totalBorrowAssets`、`totalBorrowShares`、`lastUpdate`、`fee`。
- `read_morpho_blue_position`：调用 `position(bytes32,address)`，读取 `supplyShares`、`borrowShares`。

健康因子还不能计算，因为还需要：

- 每个市场的 oracle 地址与价格精度。
- 每个市场的 IRM 与 LLTV。
- 将 shares 换算成 assets 的当前市场状态。
- 清算阈值在部署实现中的真实语义。

当前 `healthFactorUsable=false`，原因统一标记为 `oracle prices and Morpho market parameters are not yet verified`。

## 5. Oracle、清算参数与 collateral 退出路径

### Oracle

尚未完成主网市场发现，因此没有可复核的 oracle 清单。下一步先确认 `CreateMarket` 事件签名与日志结构，再扫描市场参数，记录每个市场的 `oracle`、`irm`、`lltv`。

### Liquidation incentive / close factor

未确认。标准 Morpho Blue 依赖 `lltv` 与 oracle 健康检查，但具体清算激励和可清算比例需要以部署合约源码为准。在源码核验前保持 `robinhood_lending_protocol_unverified` 阻断。

### Flashloan

`flashLoan(address,uint256,bytes)` 选择器已确认存在，执行器接口中已有 `onMorphoFlashLoan` 回调骨架。是否真实可借仍取决于合约实现与市场资金，不能由选择器存在直接判定。

### Collateral 退出 DEX 路径

与 Robinhood V4 DEX 路径保持一致，只做只读候选池与报价观察；DEX 执行稳定前不进入清算 static-call 与自动执行。

## 6. 当前代码与测试

- `flashloan/src/dex/intent_trade/chains/robinhood/morpho_blue_readonly.py`：只读探针、市场/仓位 view 解码。
- `flashloan/src/dex/tools/probe_robinhood_morpho_blue.py`：CLI，支持 `--market-id`、`--accounts`、`--no-write`、`--strict-readonly`。
- `flashloan/app_runtime/tests/unit/test_robinhood_morpho_blue_readonly.py`：7 个单测覆盖链校验、代码/选择器探测、市场/仓位解码、账号池阻断与近期事件归类。

定向验收：

```powershell
python -m pytest flashloan/app_runtime/tests/unit/test_robinhood_morpho_blue_readonly.py -q
python -m pytest flashloan/app_runtime/tests/unit/test_architecture_guards.py -q
```

结果：新模块单测 `7 passed`，架构守卫 `36 passed`。

## 7. 下一步

1. 从官方文档、Morpho 官方部署记录或合约源码取得 `0x9D53d5E3bd5E8d4Cbfa6DB1ca238AEA02E651010` 的独立来源证据，并复核 code hash。
2. 确认部署合约的 `CreateMarket` 事件签名，扫描主网市场并记录 oracle/irm/lltv。
3. 用真实 RPC 读取市场总量与已知账户 position，验证 shares/assets 换算。
4. 完成 fork/static-call 模拟与 paused executor readback。
5. 全部证据到位前，清算保持 `observation_only=true`，不新增广播能力。
