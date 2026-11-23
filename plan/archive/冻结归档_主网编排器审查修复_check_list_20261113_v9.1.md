# 主网编排器审查修复 Checklist v9.1（2026-11-13）

> 本 checklist 与 `未完成任务清单_20261113.md` 一一对应。`[x]` 仅表示已有可复核证据；无证据不得勾选。

## 0. 架构与口径硬约束

- [x] 权威候选排序位于 Rust `candidate_edge_pool_manager`。
- [x] 精确数学复用 Rust `pool_manager` / Uniswap SDK。
- [x] fixed fee 上限为 30000；动态费率池排除。
- [x] single-tick 版本 1 不跨 tick；跨 tick 接口只保留。
- [x] USDG 替代 USD 计价作用，但不作为池深信号。
- [x] 股票代币受 USDG 可兑换性约束；核心/测试豁免；扩展代币遵守深度与传递关系。
- [ ] RuntimeCore/Manager API 最终调用链审计完成。
- [ ] Python 重复业务判断已退役，且未长期双写。

## 1. P0 只读收口

### 1.1 影子对照

- [x] live state 对照启用。
- [x] `arbiterVersion=single_tick`。
- [x] `untrustedChainCount=0`。
- [ ] 2 条链端缺 `sqrtPriceX96/priceImpact` 完成归因。
- [ ] 1 条 Quoter 回滚完成归因。
- [ ] 1 条显著 amountOut 偏差完成归因。
- [ ] 所有异常台账含 firstSeen/lastSeen/field/magnitude/attribution/fix/regression。
- [ ] 内存差异阻断次数=0；RPC 不可信阻断逻辑验证。
- [ ] 采集未劣化主链路延迟和广播成功率。

### 1.2 候选排序副本物化

- [x] 每方向最多 5 个候选、1 个推荐的代码与测试已完成。
- [x] 方向化 tick 排序与 single-tick 精确报价已实现。
- [x] 无价格池错误高分回归测试已完成。
- [ ] 副本 TOP-5/TOP-1 差异报告完成。
- [ ] 有报价池存在时，无价格池 TOP-1=0。
- [ ] 连跑两次 checksum/排序完全一致。
- [ ] `is_first_hop_recommended` / `is_third_hop_recommended` 与推荐表一致。

### 1.3 30000 门槛审计

- [x] pool/token/evidence Rust 路径已同步。
- [x] Python 兼容入口读取共享策略。
- [x] `fee>30000` 不写池表的副本 prune 已验证。
- [ ] 全仓活动门禁不存在 `fee <= 3000` 旧逻辑。
- [ ] 正常示例费率 3000 未被误改。

### 1.4 版本触发重算与 VAL

- [ ] token/pool/scope 版本变化到全链重算完成的实窗延迟报告。
- [ ] USDG 估值 TTL/过期/fail-closed 口径定案。
- [ ] 本地 no-route 近似是否采用完成决策；近似结果不得作为真负。
- [ ] 长期手册口径更新完成。

## 2. P1 生产池库收敛（授权）

- [ ] 用户显式授权 production prune。
- [ ] 生产 DB 备份、hash、schema、行数、空间记录完成。
- [ ] 回滚脚本保存并在副本真实演练。
- [ ] 子表优先 prune 完成。
- [ ] `overCeiling=0`、动态费率池=0、orphan=0。
- [ ] pool scope 重新物化完成。
- [ ] candidate edge/exchange 重新物化完成。
- [ ] `selection_basis=direction_tick_ordering_then_fee_pool_id`。
- [ ] 幂等重跑 `changedRows=0`。

## 3. ARCH-2b / D4-d

- [ ] 剩余偏差逐条归因，无伪造零差异。
- [ ] D4-d `unit_profit` 切到 Rust 精确口径。
- [ ] 切换前后收益/gas/fee/single-tick bound 可回溯。
- [ ] `best_amount_ladder_incomplete` fail-closed 未放宽。
- [ ] `eth_call` 回退开关与演练通过。

## 4. ARCH-3 链上保护

- [ ] 合约传入 expected price/state version。
- [ ] 主参数/降级参数/revert 分档完成。
- [ ] V4 `sqrtPriceLimitX96` 正确使用。
- [ ] 状态陈旧 fail-closed 测试通过。
- [ ] 降级数组顺序、上限和回放证据完成。

## 5. ARCH-4-B/C

### 5.1 影子旁路

- [ ] 内存主算、`eth_call` 影子。
- [ ] 连续 7 天无不可归因差异。
- [ ] 一键回切及演练通过。

### 5.2 纯内存

- [ ] 累计观测至少 30 天。
- [ ] executable 不一致=0。
- [ ] amountOut 不可归因差异=0。
- [ ] 异常台账清零。
- [ ] ARCH-3 已上线。
- [ ] 内存状态新鲜度门禁生效。
- [ ] Multicall 补数据验证。
- [ ] 手动回切保留至少一个发布周期。

## 6. W6 生产铺开

- [ ] 用户显式授权 W6 apply。
- [ ] schema/index/dry-run/apply 报告齐全。
- [ ] `depthSnapshot.stale=false`。
- [ ] `pool_scope_drift` 消除。
- [ ] `probeRecords>=2`。
- [ ] `ladderIncomplete=0`。
- [ ] `best_amount_missing` 解除。
- [ ] `bestAmountBacktrace` 非 unresolved。
- [ ] `DEX_V4_CAPACITY_GATE_REQUIRE_MEASURED=1`。
- [ ] capacity unknown 升级为阻断。
- [ ] 未测量 hop 清单和补测路径完成。

## 7. W7 闭环重建

- [ ] 停机窗口批准。
- [ ] cleanup / refresh-key / scope apply / composite apply 成功。
- [ ] 闭环 100% 命中候选集。
- [ ] hop 100% 命中候选集。
- [ ] 四方 `pool_scope_checksum` 一致。
- [ ] `missingPoolHopCount=0`。
- [ ] `consumers_need_rebuild=0`。
- [ ] single-tick bound / capacity gate 生效。
- [ ] 未引用 TVL、`market_depth_exhausted`、相关性或 `li<1` 下架规则。

## 8. W8 Authority 与广播

- [ ] M0-P migration。
- [ ] M1-P token authority。
- [ ] M2-P pool authority。
- [ ] M3-P edge authority。
- [ ] M4-P exchange authority。
- [ ] M5-P closed-loop authority。
- [ ] M6-P review authority。
- [ ] M7-P broadcast authority。
- [ ] M8-P composite authority。
- [ ] `manager_v4_reviews` 非零物化。
- [ ] legacy 只读交叉校验，未双写。
- [ ] `reviewSources.sourcesDisagree=false`。
- [ ] 连续 3 个 ≥30 分钟观察窗口通过。
- [ ] `safeToAutoBroadcast` 判定依据明确。
- [ ] GET/command API/UI/fail-closed/架构守卫回归通过。

## 9. 池生命周期、WSS 与上限

- [ ] 备用池三日活跃清理开关默认关闭。
- [ ] 正式运行且数据量充分后，三日 24:00 调度验证。
- [ ] 候选池/推荐池不参与 TTL 清理。
- [ ] WSS 缺失时清理 fail-closed 停用。
- [ ] 监听→状态→活跃→晋升/降级/淘汰一体化验证。
- [ ] RPC 总速率 ≤15 req/s；manager 目标 8 req/s、batch 50。
- [ ] 生产池表总量 ≤3000。
- [ ] 超限淘汰规则确定性、角色保护和幂等性验证。

## 10. 长期统计与运维

- [ ] token/route/window expected/realized/gas/success/failed 统计完成。
- [ ] `pnl_*.jsonl` 长期视图完成。
- [ ] 快照新鲜度、工具版本、容量实测门禁常开。
- [ ] 连续观察期未依据单次快照下架 token 对。
- [ ] `manager-api.exe` 停机替换完成。
- [ ] 守护器与 `run.py` 重启后 health/version/页面验证。

## 11. 回归与文件格式

- [x] candidate-edge-pool-manager：12 passed。
- [x] Rust tools workspace：全绿。
- [x] rs-src：107 passed；release DLL 构建成功。
- [x] 受影响 Python：130 passed / 1 skipped。
- [x] bridge/ARCH-2b/wiring：45 passed。
- [ ] healer 测试达到当前基线。
- [ ] binary-source-match 测试和脚本退出码 0。
- [ ] capacity + pool-index 合并套件达到当前基线。
- [ ] `app_runtime/tests/unit` 既有失败集合未扩大。
- [ ] `mainnet_arbitrage_healer --mode check` 全程只读。
- [x] 本轮关键变更文件 UTF-8、无 BOM、LF。

## 12. 生产写入最终确认

- [ ] 命令明确显示目标为生产库。
- [ ] 用户授权文本已记录。
- [ ] 备份与回滚点已验证。
- [ ] 写入期间 RPC/worker/authority 状态可控。
- [ ] 写入后 checksum/row-count/orphan/幂等验证完成。
