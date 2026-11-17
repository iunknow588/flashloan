# P0-8 状态突变仲裁阶段证据（2026-11-14）

## 结论

- 结论：`passed`。
- 模式：本地测试，`productionWrite=false`。
- 范围：Rust `pool_manager` 状态突变仲裁、缺 tick fail-closed、方向/流动性/倍率测试。
- 未覆盖：生产广播请求切换、链上状态读取/三态仲裁、批内单闭环隔离、生产重物化与链上 parity，因此 P0-8 尚未整体完成。

## 实现

- 新增 `pool_manager::state_transition`，按同输入 exact-output 判定 `fatal/adverse/unchanged/favorable`。
- 当前 Tick 区间与预期区间不一致时返回 `tick_range_changed`，禁止广播。
- 缺 tick 时 `Capacity::effective_max_raw` 返回 `None`，不再回退到普通深度容量。
- token1 侧使用线性 sqrtPrice 距离比例；token0 侧额外使用 `expected/current` 倒数价格修正。
- 活跃流动性变化时使用当前状态精确重算容量，不复用旧 `M_cap`。
- 候选容量同时受当前精确容量和最大放大倍率约束。
- 输出当前 Tick 的 `sqrtPriceLimitX96` 候选、稳定原因码和 `broadcastAllowed`。
- 主网执行器新增兼容的 `buildV4PoolManagerBoundedExactInputRouteCalldata`，可把每跳当前 Tick limit 编码到 PoolManager `SwapParams`；既有 Universal Router ABI 与旧直连构建器保持不变。
- 有界构建器拒绝缺失 limit 和 V4 全局极值，避免新路径退化为无 Tick 粒度保护。

## 机器证据

- `plan/runtime_evidence/reports/p0_8_state_transition_probe_20261114.json`
- `plan/runtime_evidence/reports/p0_8_bounded_calldata_20261114.json`
- 6 个证据场景全部符合预期：unchanged、adverse、token0 favorable、token1 favorable、liquidity changed、crossed tick。

## 回归

- `cargo test --locked -p pool-manager`：131 passed。
- `cargo test --locked -p review-manager`：114 passed。
- `cargo test --locked -p closed-loop-manager`：18 passed。
- 新增状态仲裁专项：9 passed。
- `npx hardhat test test/RobinhoodPausedFlashLoanExecutor.test.js`：24 passed。
- 既有编译 warning 未增加为错误；未执行生产写入、Authority 切换或广播。

## 后续阻塞

1. 将生产广播请求切换到有界 PoolManager 构建器，并传递 Rust 仲裁的逐 hop limit。
2. 实现合约侧链上状态读取与三态检查，不能只信任链下 limit。
3. 完成 `amountOutMinimum` 利润锁策略和批内单闭环失败隔离。
4. 将最终 `M_cap` 接入最佳交易量选择器，并完成三跳最终输入量回放与链上/链下 parity。
