# BNB 小众代币分层静态表摘要

- 静态表: `data_info/BNB/bnb_niche_token_static_table.json`
- 区块: `116205883`
- 高质量门槛: V2 储备阈值、V3 active liquidity + unlocked + 双向 Quoter 可报价、1000 稳定币往返回款不少于 900
- PN 规则: X 与 Y 独立判断；两侧可以分别连接不同稳定币、不同 T1，或通过不同 T2 路径进入闭环。

## 分层结果

| 表 | 含义 | 数量 | 结果 |
| --- | --- | ---: | --- |
| W | 出现在高质量扫描池中的稳定币 | 4 | USDT, USDC, BUSD, DAI |
| PT | X-Y 池本身合格的小众代币 | 2 | VC, VINU |
| PT pairs | X-Y 池本身合格的小众交易对 | 1 | VC / VINU |
| T1 | PT 中有稳定币高质量出口的代币 | 0 | - |
| T2 | PT 中不在 T1、但有高质量池到 T1 的代币 | 0 | - |
| PN | PT 中两端均属于 T1 或 T2 的分析交易对 | 0 | - |

## 关键原因

此前看起来合格的多个 V3 小众 X-Y 池，只看 `activeLiquidity` 会出现假阳性。补上双向 Quoter 探针后，apyUSD/apxUSD、mubarak/TST、CDL/FORM、客服小何/哈基米 等池子都因为当前方向无法报价被排除。

VC/VINU 是当前唯一保留的 PT 交易对，但 VC 没有稳定币出口；VINU 虽然存在 VINU/USDT 可报价池，1000 USDT 往返预检最终只回到约 1.0975 USDT，低于 900 USDT 保底标准，因此不能进入 T1。

结论: 按当前候选集和高质量标准，BNB 链上小众 X-Y 套利闭环候选 PN 为 0。这个 0 不是因为要求必须到 USDC，而是因为真实可报价的 PT 太少，且剩余 PT 没有合格稳定币出口。

补充: T2 已改为全局扫描 `PT_nonT1 -> 任意 T1` 的高质量池，不再要求 T2 必须来自原始 PT 的同一条 X-Y 交易对。当前仍为 0，是因为本轮 PT 内没有 T1。
