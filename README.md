# flashLoan

这是 flashLoan 项目的仓库根目录。

## 入口与结构

- `scripts/tool/multi_chain_cli.py` 是统一 CLI / 交互入口。直接运行即可进入菜单；默认链为 Robinhood 主网 `4663`，可用 `--chain` 切换到任何已注册链（例如 `46630` 测试网、`31337` 本地 Hardhat、`1` Ethereum、`56` BNB、`137` Polygon、`43114` Avalanche）。
- `test-token_create/main.py` 是同一套统一 CLI 在 `test-token_create` 工作区内的入口，运行时会默认启用中文菜单（设置 `FLASHLOAN_MENU_LANG=chinese`）。
- 交互菜单会随当前链只展示对应的二级操作；主网、测试网、本地 Hardhat 的操作不会跨链交叉出现。主网二级菜单可直接输入 `3.1`/`3.2`/`4.1`/`5.1` 执行，测试网/本地可输入 `6.x`/`7.x` 直接执行。未提供专项链上工具菜单的注册链仍可切换并启动 Web 控制台、查看链配置、运行定向测试或注册表审计。
- `scripts/tool/chain_config.py` 集中处理链配置解析：按链解析 `chain.json`、`contracts.json`、`manifest.json`、RPC URL、Hardhat network 与合约项目路径，并通过 `FLASHLOAN_CHAIN_ID`、`FLASHLOAN_RPC_URL`、`FLASHLOAN_MANIFEST_PATH`、`FLASHLOAN_CHAIN_JSON`、`FLASHLOAN_CONTRACTS_JSON`、`FLASHLOAN_CONTRACT_PROJECT`、`FLASHLOAN_HARDHAT_NETWORK`、`HARDHAT_NETWORK` 等环境变量注入到子进程。
- `flashloan/app_runtime/run.py` 是 Web 控制面板运行时。
- `test-token_create/scripts/tool/` 包含 Robinhood V4 运维脚本（创建池、注入流动性、诊断、套利、菜单辅助等）。
- `test-token_create/scripts/lib/` 包含工具脚本共享的 Hardhat / ethers 辅助库。
- `contract/robinhood/DEX` 与 `contract/robinhood-testnet/DEX` 分别对应主网和测试网 Hardhat 项目。

## 常用命令

```powershell
# 交互菜单，默认主网
python scripts/tool/multi_chain_cli.py

# 直接打开中文菜单
python scripts/tool/multi_chain_cli.py --menu chinese

# 查看当前链配置
python scripts/tool/multi_chain_cli.py --chain 1 --action chain-info

# 启动主网 Web 控制台
python scripts/tool/multi_chain_cli.py --action web

# Robinhood 主网预检 + 就绪检查
python scripts/tool/multi_chain_cli.py --action mainnet-preflight

# 主网只读桥接交易对报价预检
python scripts/tool/multi_chain_cli.py --action mainnet-bridge-pairs

# 刷新主网 V4 池索引（观测范围）
python scripts/tool/multi_chain_cli.py --action mainnet-refresh-v4-index

# 打开测试网 V4 操作菜单
python scripts/tool/multi_chain_cli.py --chain 46630 --action testnet-menu

# 打开本地 Hardhat V4 操作菜单（需先启动 hardhat 节点，端口 8545）
python scripts/tool/multi_chain_cli.py --chain 31337 --action local-menu

# 运行定向测试
python scripts/tool/multi_chain_cli.py --action tests

# 运行链数据注册表审计
python scripts/tool/multi_chain_cli.py --action audit

# 查看指定链配置
python scripts/tool/multi_chain_cli.py --chain 46630 --action chain-info
```

在 `test-token_create` 目录内也可以使用：

```powershell
python main.py
python main.py --chain 46630 --action testnet-menu
python main.py --chain 1 --action chain-info
```

## 默认链与网络

默认链是 Robinhood 主网 `4663`。使用 `--chain 46630` 或 Web 控制台中的链选择器可切换到 Robinhood 测试网。

现有 V4 工具脚本已改为从共享环境读取链上下文，不再硬编码测试网路径。脱离 CLI 单独复用这些脚本时，需要先设置 `FLASHLOAN_CHAIN_ID`、`FLASHLOAN_CONTRACT_PROJECT`、`FLASHLOAN_MANIFEST_PATH`，并按需要设置 RPC URL 与 Hardhat network，再运行对应 npm / Python 命令。

广播操作默认不会执行。主网 / 测试网的真实广播仍需要通过环境变量或命令参数显式开启，并配置好签名者。
