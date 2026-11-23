# 主网编排器审查修复 Checklist v9.3（2026-11-13）

> 本 checklist 与 `未完成任务清单_20261113.md` 一一对应，只保留待验收项。v9.1 已完成项及 v9.2 活动快照已归档到 `back/`；完成一项后应将证据移入归档并从本文件删除。

## 0. 架构与口径硬约束

- 权威业务逻辑位于对应 Rust manager；Python 只通过 RuntimeCore/Manager API 间接工作。
- 精确数学复用 Rust `pool_manager` / Uniswap SDK；版本 1 每跳只允许 single-tick，跨 tick 接口仅保留。
- fixed fee 范围为 `0 < fee <= 30000`；动态费率池在所有池角色中排除。
- 候选排序为 normalized single-tick 精确输出降序 → 方向 tick 顺序 → fee → pool id。
- USDG 在经济计价上替代 USD，但外部 fiat 价格源和 TVL 不得进入池深准入。
- 股票代币受 USDG 可兑换性约束；核心/测试豁免；扩展代币受深度与传递 USDG 关系约束。
- 成熟 SDK 可复用，但一个生产结论只能有一个 Rust authority；Python 不得成为第二 authority。
- [ ] RuntimeCore/Manager API 最终调用链审计完成。
- [ ] Python 重复业务判断已退役，且未长期双写。

## 1. P0 只读收口

### 1.1 管理器边界、`app_runtime` 与 SDK

- [ ] token/pool/edge/exchange/closed-loop/review/broadcast/composite 的责任矩阵与代码路径一致。
- [ ] `flashloan/app_runtime` 页面、后台任务、脚本和测试调用链清单完成。
- [ ] Python 中剩余权威业务判断均有迁移任务、责任 manager 和删除时机。
- [ ] RuntimeCore manager view 与 command API 覆盖目标 Python 入口。
- [ ] `uniswap-v4-sdk` 使用范围、版本、Rust 1.91+ 和 lockfile 门禁记录完成。
- [ ] `PoolManagerLens` / `SimpleTickDataProvider` 的状态查询与 tick 数据来源可追溯。
- [ ] `Dex-Math-Core-rs` parity、舍入、性能、许可证和维护性评估完成。
- [ ] 若采用新数学 SDK，生产数学 authority 唯一且回滚路径明确；若不采用，ADR 记录理由。
- [ ] `arena-core` 被限制为后续离线压力测试/回测，不进入版本 1 实时依赖。
- [ ] P0-0 全程只读，无生产 DB 写入。

### 1.2 影子对照

- [ ] 2 条链端缺 `sqrtPriceX96/priceImpact` 完成归因。
- [ ] 1 条 Quoter 回滚完成归因。
- [ ] 1 条显著 amountOut 偏差完成归因。
- [ ] 所有异常台账含 firstSeen/lastSeen/field/magnitude/attribution/fix/regression。
- [ ] 内存差异阻断次数=0；RPC 不可信阻断逻辑验证。
- [ ] 采集未劣化主链路延迟和广播成功率。

### 1.3 候选排序副本物化

- [ ] 旧 `selection_basis` 已迁移为 `direction_single_tick_exact_output_then_tick_fee_pool_id`，测试、报告和 checksum 同步。
- [ ] 副本 TOP-5/TOP-1 差异报告完成。
- [ ] 有报价池存在时，无价格池 TOP-1=0。
- [ ] 连跑两次 checksum/排序完全一致。
- [ ] `is_first_hop_recommended` / `is_third_hop_recommended` 与推荐表一致。
- [ ] Rust manager、RuntimeCore view、Manager API 和报告使用相同的新 label 与 checksum 口径。

### 1.4 30000 门槛审计

- [ ] 全仓活动门禁不存在 `fee <= 3000` 旧逻辑。
- [ ] 正常示例费率 3000 未被误改。

### 1.5 版本触发重算与 VAL

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
- [ ] `selection_basis=direction_single_tick_exact_output_then_tick_fee_pool_id`。
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
- [ ] 正式运行且数据量达到配置阈值后，每 3 个自然日的当日 24:00 调度验证。
- [ ] 候选池/推荐池不参与 TTL 清理。
- [ ] 动态费率池未进入备用/候选/推荐角色。
- [ ] 清理配置含默认关闭开关和数据量启用阈值。
- [ ] 清理配置含 3 日窗口、Asia/Shanghai 时区、单次最大删除量和 dry-run/apply。
- [ ] 候选/推荐刷新与备用清理使用同一 pool state version，竞态测试通过。
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

- [ ] healer 测试达到当前基线。
- [ ] binary-source-match 测试和脚本退出码 0。
- [ ] capacity + pool-index 合并套件达到当前基线。
- [ ] `app_runtime/tests/unit` 既有失败集合未扩大。
- [ ] `mainnet_arbitrage_healer --mode check` 全程只读。
- [ ] `cargo test --locked` 和 release 构建通过。
- [ ] SDK 升级附 parity、性能、许可证和回滚差异报告。
- [ ] `flashloan/app_runtime` 架构守卫阻止新增 Python 权威业务逻辑。
- [ ] 本轮新增和修改文件 UTF-8、无 BOM、LF。

## 12. 生产写入最终确认

- [ ] 命令明确显示目标为生产库。
- [ ] 用户授权文本已记录。
- [ ] 备份与回滚点已验证。
- [ ] 写入期间 RPC/worker/authority 状态可控。
- [ ] 写入后 checksum/row-count/orphan/幂等验证完成。
