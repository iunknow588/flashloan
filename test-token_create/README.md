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

如果 owner 余额不足以支付本次追加流动性所需的代币，脚本会直接报错，不会为了保住 80% 而额外 mint。80% 是创建时预留的后续补流动性储备，不是注入后的余额红线。manifest 会写入 `arbOpportunity.injected`；重复执行不会重复注入，需要再次制造机会时设置：

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

`officialV4.hooks` 必须和实际部署的 PoolKey 一致。如果当前池子按普通零 hooks 方式创建，脚本也会使用 `0x0000000000000000000000000000000000000000`，不会强制要求非零 hook。诊断脚本会打印当前 manifest 使用到的三个池子 ID，可对照路由/扫描页面确认。

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

也可以完全手动指定单次注入量，`--amount` 表示每个池子的“主储备代币”注入量；例如一次按高储备代币锚定增加 `100`：

```powershell
cd contract/robinhood-testnet/DEX
$env:HARDHAT_NETWORK="robinhoodTestnet"
$env:ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_REPLENISH_BROADCAST="true"
node ..\..\..\test-token_create\scripts\testnet-v4-official-inject-liquidity-live.js --amount 100
```

`--amount` 传的是锚定代币数量，脚本会按池子原有 `reserveA/reserveB` 比例自动换算另一边代币，并基于全范围 V4 流动性公式算出应 mint 的 `liquidity`；`--broadcast` 可替代环境变量授权广播。

Python 入口（内部调用同一套 Hardhat/ethers.js 脚本，避免重写 V4 合约调用）：

```powershell
python test-token_create/scripts/manual_official_v4_replenish.py --amount 100 --broadcast
```

## 制作套利机会

“补充 LP 深度”和“制造套利机会”拆成两个独立脚本。制造套利机会脚本通过 `OfficialV4ImbalanceMaker` 执行一次小额的逆向闭环 swap，主动让池子偏离平衡，使后续 `TZ -> TB -> TA -> TZ` 出现可执行机会；这是独立的手动/主观操作，不读取链上套利程序的运行状态，也不由流动性深度兜底。默认金额 `0.001 TZ`：

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

`--amount` 传的是 TZ 数量，默认 `0.001`，是制造失衡时的主观参数；不依赖链上套利程序是否运行，也不会要求先注入 LP 深度来兜底。广播前脚本会先 `staticCall` 预检：如果当前池子深度无法承载该金额，或预检没有形成缺口（`spent=0`），脚本会直接失败且不广播。失败时会额外探测当前能通过预检的最大金额，并提示先补充三角池深度或改用该金额重试。

`staticCall` 预检需要 `OfficialV4ImbalanceMaker` 合约里先持有本次 `--amount` 对应的 `TZ` 余额；如果只读预检时余额不足，脚本会提示先执行一次 `--broadcast` 完成注资，再使用只读预检。这不是把“补充流动性”强行绑到“制造失衡”上，而是让制造失衡操作在没有广播前也能先做真实的链上只读返演。

例如当前测试网三角池深度很小，`TZ PoolManager` 约 `239.65 TZ`、`TA` 约 `11.98 TZ`、`TB` 约 `126.33 TZ`。此时用 `100 TZ` 制造失衡会因单笔 swap 超过当前深度回滚；`1 TZ` 或 `0.001 TZ` 可正常预检。这不是“等待链上复核”或链路问题，而是“制造失衡金额 > 当前池子可用深度”。要注入更大的失衡金额，应先执行补充流动性的手动操作提高深度，再重试制造失衡。

先补充流动性、再制造失衡的顺序是可执行的。只要先运行补池操作把 `TZ/TA/TB` 的 V4 池深度提高到能承载目标 swap 金额，`runImbalance.staticCall` 就会从“回滚”变成“形成缺口”，随后制造失衡广播才能落地。两个操作仍然互相独立：补池只负责增加深度，制造失衡只负责主动制造缺口，互不兜底。

Python 入口：

```powershell
python test-token_create/scripts/manual_official_v4_make_arb.py --amount 100 --broadcast
```

## 自动流程：先补池，再制造失衡

新增可复用编排脚本 `test-token_create/scripts/manual_official_v4_prepare_imbalance.py`，把“只读诊断深度 -> 补池 -> 制造失衡”串成一个入口，内部仍调用既有 Hardhat/ethers.js 脚本，不重写合约 calldata。

只读/预检模式（不广播）：

```powershell
python test-token_create/scripts/manual_official_v4_prepare_imbalance.py --target-amount 100 --replenish-amount 500
```

实际广播模式（会补池并制造失衡）：

```powershell
python test-token_create/scripts/manual_official_v4_prepare_imbalance.py --target-amount 100 --replenish-amount 500 --broadcast
```

参数说明：

```text
--target-amount      制造失衡时使用的 TZ 金额，默认 100
--replenish-amount   补池时每个池子的“主储备代币”锚定量，默认 500
--broadcast          允许真正发送链上交易；缺省时只做诊断/预检
--skip-replenish     跳过补池步骤，只诊断后直接制造失衡
```

如果只读预检显示目标金额仍回滚，说明当前池子深度还是不够，应提高 `--replenish-amount` 后再重试。补池与制造失衡仍然各自独立：脚本只负责按顺序先提高深度、再制造缺口，不把一个操作包装成另一个操作的兜底。

## 合约1（Composer）与合约2（Executor）分工

当前阶段把闭环决策拆成两层：

- 合约2 = `OfficialV4FlashArb`，是纯执行器。它接收一组闭合路径和方向，逐个用 `runArbitrageBest` 尝试，任一方向满足最小利润即返回该方向结果；全部失败则 `NoProfitableRoute`。
- 合约1 = `ArbitrageComposer`，是链上判断/调度器。它接收多个候选闭环，每个候选带 `deviationBps`，低于 `minDeviationBps` 的直接跳过；达到偏差阈值的候选调用合约2，回滚则继续尝试下一条。
- 链下只负责发现闭环候选，不再代替链上决定最终执行方向；最终是否执行由合约1调用合约2时通过 `eth_call` 复核。

新增测试：

```powershell
cd contract/robinhood-testnet/DEX
npx hardhat test test/ArbitrageComposer.test.js
```

测试覆盖三件事：低于阈值的候选被跳过、第一条候选回滚后继续尝试下一条、全部候选不可执行时返回 `NoProfitableCandidate`。

当前版本说明：第一版由链下候选传入 `deviationBps`，用于先把“筛选+调度+执行”的链路跑通；下一步再把 PoolManager 的 `slot0/liquidity` 读取放进合约1，由链上实时价格计算 `m/x/y` 比值与偏差，消除对链下偏差数值的依赖。

当前合约1已支持链上偏差计算：候选可携带 `mQuote`、`xQuote`、`yQuote`，合约内按 `m / (x/y)` 与 1 的差距计算 `deviationBps`；只有报价字段未提供时才回退到链下传入的 `deviationBps`。

合约1进一步支持链上读取 PoolManager 存储：通过 `extsload` 读取池 `slot0` 的 `sqrtPriceX96`，按三条有向跳计算三角形偏差。只要 `extsload` 可用，偏差判断不再依赖链下报价；只有链上读取失败时才回退到 `mQuote/xQuote/yQuote` 或 `deviationBps`。

合约1 的可复用测试网入口：

```powershell
cd contract/robinhood-testnet/DEX
npm run testnet:v4-official-composer:preflight
npm run testnet:v4-official-composer:live
```

`preflight` 会优先复用 manifest 中已部署的 `officialV4.composer`；如果尚未部署，需要先执行 `live` 或手动允许 `ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_COMPOSER_BROADCAST=true` 完成部署。`live` 会对合约1调用 `runBest.staticCall`，通过后再广播一笔测试网闭环交易，并把 `lastComposerLiveTest` 写入 manifest。

周期执行入口：

```powershell
cd contract/robinhood-testnet/DEX
python ../../../test-token_create/scripts/periodic_official_v4_composer.py --interval 60 --broadcast
```

`--once` 表示只跑一轮；不加 `--broadcast` 时只做 `runBest.staticCall`，不会广播链上交易。

当前已在 Robinhood Testnet 跑通一次合约1 -> 合约2 的闭环：

```text
composer        : 0xff426b28c373dd5f1FCA870FF7Ce7276A15FE563
executor        : 0xBa54dADE2cC483Bfe1277E2CD8D5762757a59A0c
poolManager     : 0x8366a39CC670B4001A1121B8F6A443A643e40951
route           : TZ -> TB -> TA -> TZ
staticProfit    : 0.002200341007504074 TZ
txHash          : 0x94d35113a101c9bbac435eb148b61074fd7306d5195226aea8c3f0a1eb961ea3
```

合约1新增 `runBestDetailed`，返回实际选中的三个池 `selectedKeys`、方向 `selectedDirections`、逐跳输出和 `routeIndex`，可直接用于交易后复盘。

链上报价证据层已接入合约1：当 manifest 中存在 `officialV4.composer` 时，路由复核会先调用 `ArbitrageComposer.runBest.staticCall`，失败或合约未部署时才回退到 `OfficialV4FlashArb.runArbitrageBest`。

scanner 广播门禁：默认只做 `runBest.staticCall`；设置 `DEX_OPPORTUNITY_SCANNER_ALLOW_BROADCAST=1` 后，只在 `routeSymbols == ["TZ", "TB", "TA", "TZ"]` 且 Composer 静态利润为正时广播测试网交易。


## 路由页面验证不平衡与执行套利

制造失衡后，可以启动 Web 控制台并打开路由页面，让后台扫描器用链上实时报价复核闭环路由：

路由页可以先返回静态候选并保持页面可用；后台 scanner 会在当前扫描轮次对全部可记录闭环路线执行链上复核，不设置“每轮只复核 N 条”的预算，也不会把未完成路线安排到下一次 idle scan。若需要网页同步复核路线，可启动前设置 `DEX_QUOTE_ROUTE_LIVE_ROUTE_LIMIT=999`；设为 `0` 则只使用静态快照。

```powershell
python flashloan/app_runtime/run.py --port 5050
```

然后访问：

```text
http://127.0.0.1:5050/dex/quotes?chain_id=46630&protocols=uniswap_v4
```

页面会调用 `/api/dex/opportunity-scanner/snapshot`。第一轮扫描需要真实调用 `eth_call` / V4 Quoter，可能耗时较久；快照完成后，如果 `TZ -> TB -> TA -> TZ` 显示 `live_verified` 且净利润为正，说明链上已确认不平衡。

路由卡片上还提供三类操作：`链上估算` 会通过后端 `/api/dex/quote-route/test` 用当前链上 V4 Quoter 按你输入的金额逐步报价并展示每跳输出；`只读链上预检` 对 `TA/TB/TZ` 三角闭环调用官方 `OfficialV4FlashArb` 的 `staticCall`；`广播测试` 在页面确认后才会调用同一脚本真正发送测试网交易。广播仍受脚本自身的 `ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST` 环境开关约束。
每条可记录路线都必须在本轮得到链上复核结果。RPC 超时、Quoter 回滚、池缺失、历史区块不可读等情况必须显示为具体失败原因，例如 `rpc_verification_failed` 或 `live_quote_failed`，不得显示“本轮未复核”“等待下一轮”或“链上验证已延后”。`/api/dex/quote-routes?live_estimate=1` 仍可用于网页同步估算和结果预热。

后端接口示例：

```text
POST /api/dex/quote-route/test
{
  "routeSymbols": ["TZ", "TB", "TA", "TZ"],
  "amount": "1",
  "mode": "estimate"
}
```

返回每条链上报价步骤、净收益参考、`liveVerified`，以及执行脚本时的 `staticCall` / `txHash` 结果。

闭环路由候选不只限定在 manifest 的 `tz/ta/tb` 之间。Robinhood Testnet 路由发现会读取系统已知/自建的代币地址（v4_token_allowlist、testnet-v4-amm manifest、本地证据），再与 V4 PoolIndex 中真实存在的池子做三角形闭环枚举，只要三个跳都能组成闭环就会进入候选；链上 Quoter 一旦执行过并给出“流动性不足/池未初始化/报价失败”等业务结果，页面也会直接标记为链上复核结论，不再显示成“等待链上复核”或“静态参考”。

确认后执行套利：

```powershell
cd contract/robinhood-testnet/DEX
npm run testnet:v4-official-arb:live
```

脚本会先 staticCall，检测到正利润后再广播一笔闭环闪贷套利，并把 `lastArbLiveTest` 写入 manifest。

## 代币与流动性说明

默认创建三个 18 decimals 测试代币：`tz`、`ta`、`tb`，初始每个总量 `10,000,000`。创建时在 owner 账户预留 80%，是为了之后补充流动性时有代币可用；每次注入会正常消耗这笔预留，不要求注入后 owner 仍保留 80%。

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
- 注入前从创建预留中消耗代币，不要求注入后 owner 每种代币仍保留 80%。

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
3. 检测深度（只读/不广播）
4. 检测三角平衡（只读/不广播）
5. 增加 TZ 不平衡性 100（广播）
6. 增加 TZ 不平衡性 500（广播）
7. 检测深度与三角平衡（只读/不广播）
0. 退出
```

`只读/不广播`：只读取链上状态或执行 `staticCall`，不发送链上交易。诊断会先列出 manifest 中已知的代币和池子，再检测深度或三角平衡。`广播`：允许脚本真正发送链上交易。

也可以直接运行只读诊断脚本：

```powershell
cd contract/robinhood-testnet/DEX
npm run testnet:v4-official-diagnose:depth
npm run testnet:v4-official-diagnose:balance
npm run testnet:v4-official-diagnose
```

也可以非交互指定编号执行：

```powershell
python test-token_create/scripts/manual_official_v4_menu.py --choice 3
```

菜单项只负责人工注入流动性或制造失衡，不包含套利发现、pending 信号读取或套利静态模拟。套利发现与执行仍由套利程序统一处理。

菜单脚本默认是交互模式，执行完一次操作后不会直接退出；会打印结果摘要并回到菜单，便于连续补充流动性、制造失衡或诊断深度。

## 页面看到过期/旧证据时先查端口

如果 `http://127.0.0.1:5000/dex?...` 页面持续显示旧的 `missing_effective_pool`、`route_unprofitable` 或 `live_quote_failed`，但代码已经完成修正，先确认 5000 端口是否被旧的 Python 进程占用。

```powershell
netstat -ano | findstr :5000
taskkill /PID <pid> /F
```

也可以用一个干净端口启动验证：

```powershell
python -m uvicorn app_runtime.web.app:app --host 127.0.0.1 --port 5010
```

此时访问 `http://127.0.0.1:5010/dex?chain_id=46630&market_id=dex-arbitrage-46630-uniswap-v4&protocols=uniswap_v4&dex_strategy=closed_loop_routes`，应显示较新的闭环路由集合，而不是旧进程回放的 scanner 结论。

如果页面长时间停在“链上复核状态未确认”，先看 scanner 是否卡在链上 Quoter 复核。扫描器默认开启链上复核超时，避免一条慢 RPC 阻塞整个页面：

```powershell
$env:DEX_OPPORTUNITY_SCANNER_EVIDENCE_TIMEOUT_SECONDS="45"
$env:DEX_OPPORTUNITY_SCANNER_EVIDENCE_WORKERS="4"
$env:DEX_OPPORTUNITY_SCANNER_SKIP_EXECUTION_SIZE_PROBE="1"
```

`DEX_OPPORTUNITY_SCANNER_EVIDENCE_TIMEOUT_SECONDS` 控制每条路线链上复核的最长等待时间；超过后返回明确的 `rpc_verification_failed`，不会转为延后状态。`DEX_OPPORTUNITY_SCANNER_EVIDENCE_WORKERS` 只控制并行复核数量，不影响本轮必须复核全部路线。`DEX_OPPORTUNITY_SCANNER_SKIP_EXECUTION_SIZE_PROBE=1` 会跳过全量执行规模探测，先快速给出单跳链上报价结论；需要专门做规模探测时再设为 `0`。

Robinhood Testnet 的小额测试池没有稳定 USD 深度表，若直接按 `notionalUsd=1000` 报价会把池子整个撞穿，导致绝大多数路线显示为“流动性不足/不盈利”。scanner 在没有参考金额、参考规模和路线规模建议时，默认会从 `0.1` 个基础代币开始链上复核；官方 V4 套利脚本用 `0.0001` 代币验证利润也是同一个思路。这样小池子也能返回真实 Quoter 结果，而不是把所有路线都变成 1000 代币下的假性亏损。

对于 `TZ -> TB -> TA -> TZ` 这类官方 `TA/TB/TZ` 闭环，链上证据会优先尝试调用已部署的 `OfficialV4FlashArb.runArbitrage.staticCall`，把 3 跳逐笔 Quoter 调用合并为一次 `eth_call`。该路径成功后，hop 的 `quoteMethod` / `quoteSource` 会显示 `official_v4_arb_static_call`；若静默失败，代码会自动回退到原来的逐跳 V4 Quoter 复核，不影响其它路线。
当链上证据需要同时确认两条闭环方向时，合约新增 `OfficialV4FlashArb.runArbitrageBest`：调用方一次传入多条 `keys + zeroForOne`，合约逐个尝试，返回第一个可盈利方向；失败方向会被 `try/catch` 隔离，不会把整次 `eth_call` 弄回滚。这样链下只负责发现闭环机会，链上负责复核并选择方向。

### Scanner 配置审计

当前 scanner 的设计约束是“全量候选、当轮复核、明确失败”。`DEX_OPPORTUNITY_ONCHAIN_EVIDENCE_ROUTES`、`DEX_OPPORTUNITY_TESTNET_ONCHAIN_EVIDENCE_ROUTES`、`DEX_OPPORTUNITY_TESTNET_FULL_SCAN` 和 `DEX_OPPORTUNITY_SCAN_ROUTE_LIMIT` 曾被用于限制候选或轮转复核，但它们没有产品需求依据，现已停用；设置这些变量不会减少当前扫描轮次的链上复核数量。`max_recordable_routes` 仅作为内部测试/API 兼容参数，生产入口默认不限制路线。

超时和 worker 参数只解决单条 RPC 的最长等待与并发调度问题。任何路线都会在本轮返回 `live_verified`、`route_unprofitable`、`not_enough_liquidity`、`pool_not_initialized`、`live_quote_failed`、`rpc_verification_failed` 等明确结果；历史缓存中的 `live_evidence_deferred` / `not_reviewed_this_scan` 只按历史待复核数据展示，不代表当前扫描仍在等待。

## 端口自动避让

`flashloan/app_runtime/run.py` 默认仍以 `PORT` 或 `5000` 为首选端口，但当首选端口已被占用时，会自动向后查找一个空闲端口：

```powershell
python flashloan/app_runtime/run.py
```

如果 `5000` 被占用，会打印类似信息并启动在下一个可用端口：

```text
port 5000 is occupied; selected port 5001 instead
opportunity console listening on 0.0.0.0:5001
control panel: http://127.0.0.1:5001
```

显式指定端口仍保持严格模式，方便自动化脚本明确失败而不是悄悄换端口：

```powershell
python flashloan/app_runtime/run.py --port 5000
```
