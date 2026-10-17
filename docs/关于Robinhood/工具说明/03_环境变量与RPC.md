# 环境变量与 RPC

## Live Quote Gate

统一入口在
`flashloan/src/dex/intent_trade/chains/robinhood_common/rpc_gate.py`：

- `DEX_V4_LIVE_QUOTE_ENABLED=1|true|yes|on`：启用自动 live quote。
- `DEX_V4_LIVE_QUOTE_ENABLED=0`：自动路径不打 RPC，走静态参考报价，并标记
  `quoteSource=uniswap_v4_reference_only`。
- 未设置时：Robinhood `4663`/`46630` 默认启用，其他链默认关闭。

显式运维刷新命令（例如带 `--rpc-url` 的 `refresh_v4_live`）是独立入口，不受页面
gate 限制，但仍要保留调用计数日志。`DEX_V4_LIVE_QUOTE_ENABLED=0` 下，quote、
池状态 batch、Quoter、ERC20 metadata 等自动 RPC 都不允许发生。

## RPC 环境变量

主网优先读取：

```text
ROBINHOOD_MAINNET_RPC_URLS
ROBINHOOD_RPCS
ROBINHOOD_RPC_URLS
ROBINHOOD_RPC_URL
ROBINHOOD_RPC
ROBINHOOD_ALCHEMY_RPC_URL
ROBINHOOD_MAINNET_RPC_URL
```

测试网优先读取：

```text
ROBINHOOD_TESTNET_RPCS
ROBINHOOD_TESTNET_RPC_URLS
ROBINHOOD_TESTNET_RPC_URL
ROBINHOOD_TESTNET_RPC
ROBINHOOD_TESTNET_ALCHEMY_RPC_URL
```

未设置时，落到 `flashloan/src/chains/robinhood/chain.json` 或
`flashloan/src/chains/robinhood-testnet/chain.json`。

## 超时与重试

- `DEX_ONCHAIN_RPC_TIMEOUT_SECONDS`：单次 JSON-RPC 超时，默认 10。
- `DEX_ONCHAIN_RPC_CONNECT_TIMEOUTS_SECONDS`：逗号分隔的逐次连接超时，
  默认 `10,20,30`。例如 `10,20,30` 表示三次尝试。
- `DEX_ONCHAIN_RPC_CONNECT_ATTEMPTS`：只控制尝试次数。
- `ROBINHOOD_MAINNET_RPC_CONNECT_TIMEOUTS_SECONDS`：主网专用覆盖，防止主网
  继承测试网策略。

## 2026-10-17 RPC 实况

测试网官方/publicnode 多次出现 HTTP 429 与 TLS 握手/读超时：

```text
https://rpc.testnet.chain.robinhood.com/rpc
https://robinhood-sepolia-rpc.publicnode.com
https://rpc.testnet.chain.robinhood.com
```

Alchemy 免费层只允许很小的 block range，不能作为回填数据源。遇到 RPC 不稳定，
先做一次轻量 `eth_blockNumber` 探测，再决定是否跑重扫描；不要把失败当成代码
死循环的重试条件。

