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

## Robinhood 完善进度

- `docs/关于Robinhood/待完善任务清单_20261023.md` 是当前 Robinhood V4
  后续完善清单与观察入口。
- `docs/关于Robinhood/后续完善开发计划_20261023.md` 是当前开发计划，
  包含候选下限 1、Rust 维护队列与单独 SQLite 消费线程、preview 缺失降级。
- 2026-10-22 及更早计划/清单文档已归档为历史参考，不再作为执行入口；
  多链扩展暂不纳入本轮。
- 2026-10-21 最终全量回归已完成：`pytest flashloan/app_runtime/tests -q` 2115 passed、
  1 skipped；架构守卫 37 passed；manifest 一致性 3 passed；`compileall` 通过；
  `cargo test` 43 passed；主网/测试网 integrity 初始与 `20261021_recheck` 复测均
  `0 violations`。
- P3-3 已推进到真实链上只读证据：`npm run test:robinhood` 48 passed（原 45 +
  新增 3 个 readiness 单测）、两份
  offchain-normal evidence（loopback / executor）、chain_test 包重建与审计 0 error；
  fork/static-call 与真实 paused readback 均已落盘（`broadcastEnabled=false`）；
  预部署 `fork_staticcall_not_passed` 已解除（`--no-write` 复检），live-testing
  readiness 保持 `readyForLiveTest=true` 且广播仍为 `false`。
- P3-2 live quote evidence 已收口：`npm run evidence:robinhood:testnet:v4-live-quote`
  在 block 140216320 三跳全 `ok=true`、`blockingReasons=[]`、`broadcastEnabled=false`；
  单测 3 passed。P3-4 paused redeploy 预算/余额证据已落盘；2026-10-22 01:21Z
  技术阻断已解除，具体见下方 P3-4 行。
- P4-1 主网地址证据已落盘（block 94441118、5/5 hasCode、fail-closed）；P0-14 审批
  材料模板与 P2-04 SLO/值班模板已产出，负责人与签署待业务侧确认。
- P3-4 技术阻断已解除：2026-10-22 18:01Z preflight 通过
  （`readyForPausedDeploy=true`、`blockingReasons=[]`、
  `broadcastEnabled=false`），18:02Z paused redeploy plan
  `technicalReadyForPausedRedeploy=true`、`broadcastAttempted=false`；
  `readyForIntentionalPausedRedeploy=false`，canary 与显式 redeploy 仍需
  stage freeze 审批、`ROBINHOOD_TESTNET_ALLOW_PAUSED_DEPLOY=true`、真实测试
  资产与时间窗。
- P9-7 已完成 30 分钟可重复验收并追加 25/45 分钟续观察：23:09:23Z - 23:39:53Z
  0 新成功/224 blocked；23:39:53Z - 00:05:24Z 0/128 blocked；00:05:24Z - 00:51:00Z
  0 unique 成功/0 failed/320 blocked；测试网广播 true、主网 false。
- 最近 10 小时提交记录统计已纳入状态汇总：测试网 46630 共 1231 条 attempts，
  4 条 `execution_success` 日志行对应 2 个 unique tx hash、0 条 `execution_failed`、
  1227 条 fail-closed 阻断；主网同时段 1284 条候选、0 成功/0 失败。
- 仍被外部条件阻断的任务为 P3-1 方向回填（SQLite-only 复核
  `satisfiedDirectionCount=51`、`remainingDirections={}`；完整 `eth_getLogs`
  回填仍受 Alchemy Free tier 10 块限制与 SQLite 39195/475 不完整快照阻断）、
  P3-4 剩余 canary（stage freeze 审批/显式 paused deploy 授权/真实测试资产）、
  P3-5 readiness 写盘；主网 fork/static-call、paused executor readback 与
  审批签署待业务窗口。这些任务不会用代码回归代替链上 evidence。
