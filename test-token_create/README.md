# Robinhood 测试代币创建与部署

这个目录保存 Robinhood 测试代币相关的创建、注入流动性和链上套利测试脚本。脚本统一依赖 `contract/robinhood-testnet/DEX` 的 Hardhat 项目、合约源码和 `node_modules`。

## 目录结构

```text
test-token_create/
  README.md
  config/
    local-v4-amm/
      manifest.json
    testnet-v4-amm/
      manifest.json
  scripts/
    lib/
      hardhat.js
      local-v4-amm-common.js
    local-v4-amm-create-tokens.js
    local-v4-amm-inject-liquidity.js
    local-v4-amm-create-arb-opportunity.js
    local-v4-amm-run-arb.js
    local-v4-amm-run-arb-100.js
    simulate-imbalanced-triangle.js
    testnet-v4-amm-live-test.js
    testnet-v4-official-pool-create-live.js
    testnet-v4-official-arb-live.js
```

## 本地 Hardhat 流程

先启动持久节点：

```powershell
cd E:\2026OPC大赛\flashLoan\contract\robinhood-testnet\DEX
npx hardhat node --port 8545
```

再执行：

```powershell
npm run compile
npm run local:v4-amm:create-tokens
npm run local:v4-amm:inject-liquidity
npm run local:v4-amm:create-arb-opportunity
npm run local:v4-amm:run-arb-100
```

`local:v4-amm:create-arb-opportunity` 会在三条现有池子上追加一层不平衡流动性，制造 `tz -> tb -> ta -> tz` 的套利机会。默认追加量为：

```text
tz/ta: +100,000 tz : +10,000 ta
ta/tb: +10,000 ta : +100,000 tb
tb/tz: +100,000 tb : +10,000 tz
```

如果 owner 余额不足以在追加流动性后仍保留至少 80% 的每种代币，脚本会先给 owner 补充性 mint 测试代币，再执行注入。manifest 会写入 `arbOpportunity.injected`；重复执行不会重复注入，需要再次制造机会时设置：

```powershell
$env:ROBINHOOD_LOCAL_ARB_OPP_FORCE="true"
npm run local:v4-amm:create-arb-opportunity
```

`local:v4-amm:run-arb-100` 使用 100 TZ 作为 V4 `unlock/take/settle` 闪电借贷金额，先执行 `staticCall`，再广播真实本地交易，最多循环 200 轮直到报价不再盈利或价格指标收敛。

## 非闪电贷闭环模拟

```powershell
npm run simulate:robinhood:imbalanced
```

## Robinhood 测试网部署

测试网脚本默认禁止广播，需要显式开启环境变量：

```powershell
npm run deploy:robinhood:testnet:v4-amm-live
npm run deploy:robinhood:testnet:v4-official-pool
npm run testnet:v4-official-arb:preflight
```

只有 static-call 验证通过后，才允许使用受控广播：

```powershell
npm run testnet:v4-official-arb:live
```

测试网 manifest 写入 `test-token_create/config/testnet-v4-amm/manifest.json`。

如果要在官方 V4 池创建时启用 `TzTieredInjectionHook`，需要显式开启：

```powershell
$env:ROBINHOOD_V4_TESTNET_USE_TZ_INJECTION_HOOK="true"
npm run deploy:robinhood:testnet:v4-official-pool
```

该 hook 会在每次 swap 回调用记录 `tz` 被换出的累计数量，保存为 `pendingReplenishAmount` / `totalObservedSwappedAmount`，代币保留在 hook 内，不在 swap 回调里转账或捐赠。后续由延迟补仓/制造套利机会的脚本读取信号，再调用 `PositionManager` 增加实际 swap 深度或执行失衡操作。对应合约测试：

```powershell
npm run test:robinhood:tz-injection-hook
```

官方 V4 通过 hook 地址最低 14 位决定回调权限，因此脚本会使用 `Create2Deployer` 将 hook 部署到只设置 `afterSwap`（0x40）的地址，避免意外触发 initialize/addLiquidity 等回调导致建池失败。

当前测试网启用成功的延迟记录模式 hook 为 `0x995324d98aDaf57E4a7E3e436D8E8A2fF6454040`，最近一笔带 hook 的官方 V4 小额实盘广播为 `0x39692d41d3d608dff33b3de0564d5d6ac80372ba817f9c8c97767e2f4204b70d`。

## 延迟注入流动性

新增独立脚本不会在 swap 回调里做重活；只有 `pendingReplenishAmount` 达到阈值且距离上次注入超过冷却时间时才广播一笔 `PositionManager.modifyLiquidities` 增加 LP 深度：

```powershell
npm run testnet:v4-official-replenish:preflight
```

```powershell
$env:ROBINHOOD_TESTNET_REPLENISH_THRESHOLD="1000000000000000"
$env:ROBINHOOD_TESTNET_REPLENISH_MIN_INTERVAL_SECONDS="300"
npm run testnet:v4-official-replenish:live
```

默认阈值 `0.001 TZ`，默认冷却时间 `300s`。脚本会把 `lastReplenishAt` 写入 `test-token_create/config/testnet-v4-amm/replenish_state.json`，避免频繁触发导致多余 gas。

也可以完全手动指定单次注入量，例如一次增加 `100` 个 liquidity 单位：

```powershell
cd contract/robinhood-testnet/DEX
$env:HARDHAT_NETWORK="robinhoodTestnet"
$env:ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_REPLENISH_BROADCAST="true"
node ..\..\..\test-token_create\scripts\testnet-v4-official-inject-liquidity-live.js --amount 100
```

`--amount` 传的是流动性数量，不是精确的 token 数量；`--broadcast` 可替代环境变量授权广播。

Python 入口（内部调用同一套 Hardhat/ethers.js 脚本，避免重写 V4 合约调用）：

```powershell
python test-token_create/scripts/manual_official_v4_replenish.py --amount 100 --broadcast
```

## 制作套利机会

“补充 LP 深度”和“制造套利机会”拆成两个独立脚本。制造套利机会脚本通过 `OfficialV4ImbalanceMaker` 执行一次小额的逆向闭环 swap，主动让池子偏离平衡，使后续 `TZ -> TB -> TA -> TZ` 出现可执行机会；同样有阈值和冷却时间控制，默认金额 `0.001 TZ`：

```powershell
npm run testnet:v4-official-make-arb:preflight
```

```powershell
$env:ROBINHOOD_TESTNET_MAKE_ARB_AMOUNT="1000000000000000"
npm run testnet:v4-official-make-arb:live
```

也可以完全手动指定单次失衡金额，例如一次用 `100 TZ` 制造失衡：

```powershell
cd contract/robinhood-testnet/DEX
$env:HARDHAT_NETWORK="robinhoodTestnet"
node ..\..\..\test-token_create\scripts\testnet-v4-official-make-arb-opportunity-live.js --amount 100 --broadcast
```

`--amount` 传的是 TZ 数量，默认 `0.001`；当池子深度不足时，`100 TZ` 可能会 revert，建议先注入 LP 深度再用较小金额。

Python 入口：

```powershell
python test-token_create/scripts/manual_official_v4_make_arb.py --amount 100 --broadcast
```

## 代币与流动性说明

默认创建三个 18 decimals 测试代币：`tz`、`ta`、`tb`，初始每个总量 `10,000,000`。每次注入流动性都会优先保证部署账户至少保留 80% 的代币，避免把全部测试代币锁进池子。

本地 mock 池基础配置：

```text
tz/ta: 1,000,000 tz : 100,000 ta
ta/tb: 100,000 ta : 1,000,000 tb
tb/tz: 1,000,000 tb : 100,000 tz
```

## 成功标准

本地套利测试通过的标准：

- 至少一笔真实本地交易成功，receipt 存在且 gas > 0。
- `currencyDebt(arb, tz/ta/tb)` 全部为 0，闪电贷债务已归还。
- 套利利润大于 0，利润保留在套利合约中。
- 三角价格指标在套利后下降，池子向平衡方向修正。
- owner 每种代币仍保留至少 80%。

## 常见问题

1. 运行脚本提示找不到 `TestERC20` 或 `MockV4AmmPoolManager`：先在 `contract/robinhood-testnet/DEX` 下执行 `npm run compile`。
2. manifest 路径找不到：使用 `contract/robinhood-testnet/DEX` 下的 package 命令运行，脚本会按自身路径解析 `../config` 或 `../../config`。
3. localhost 报错：先启动 `npx hardhat node --port 8545`，再运行 create/inject/create-arb/run-arb。

## 常用操作菜单

新增 `manual_official_v4_menu.py`，把常见链上操作做成固定选择项，不需要每次传参数：

```powershell
python test-token_create/scripts/manual_official_v4_menu.py
```

菜单包含：

```text
1. 增加流动性 100（广播）
2. 增加流动性 500（广播）
3. 增加流动性 100（仅预检）
4. 增加流动性 500（仅预检）
5. 增加 TZ 不平衡性 100（广播）
6. 增加 TZ 不平衡性 500（广播）
7. 增加 TZ 不平衡性 100（仅预检）
8. 增加 TZ 不平衡性 500（仅预检）
0. 退出
```

也可以非交互指定编号执行：

```powershell
python test-token_create/scripts/manual_official_v4_menu.py --choice 3
```

菜单项只负责人工注入流动性或制造失衡，不包含套利发现、pending 信号读取或套利静态模拟。套利发现与执行仍由套利程序统一处理。
