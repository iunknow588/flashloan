# ADR: P0-8 单 Tick 容量折算与池状态突变处理口径

- 日期：2026-11-14
- 状态：P0-8-a / P0-8-b 已完成；P0-8-c / P0-8-e 待推进
- 范围：只读代码、单元测试与计划文档；生产库写入 0，Authority 切换 0，广播 0

## 背景

原闭环门禁把三跳 single-tick 上界直接取最小值，但每跳上界分别以 A、B、C 计价，量纲不同。直接比较会得到不可信的 M_cap。另一方面，链上 FCFS 执行时池状态可能突变，必须区分跨 Tick、状态变坏与状态变好，不能无条件沿用链下旧容量。

## 决策

1. 每跳容量使用 SDK 逐 token delta：token1 侧为 `L·|Δ√P|`，token0 侧为 `L·|Δ√P|/(s_cur·s_b)`。
2. 三跳容量按前跳累积即期汇率折算到锚定代币；平方根价对应的线性率折叠两次。
3. 所有中间计算使用 U256 定点算术；累积因子保存为分子/分母对，避免连续除法截断。
4. `closed_loop_manager` 优先使用折算后的路径 M_cap；旧原始上界只保留“方向容量为 0”的不可用信号，禁止跨 token 比较正值。
5. 既有最佳交易量阶梯算法保留为候选跨 Tick 参考；单 Tick 在线计算使用专用 O(1) 距离比例逻辑。
6. 池状态突变采用三态处理：跨 Tick/流动性不可证 → revert；状态变坏 → revert；状态变好 → 按当前距离边界比例重算并受硬上限约束。
7. token0 与 token1 的容量比例必须分别计算；token0 比例额外包含 `s₁/s₂`，禁止套用统一距离比例。
8. 链上执行必须使用当前 Tick 的 `sqrtPriceLimitX96`、保留 `amountOutMinimum`，并隔离批内单闭环失败。

## 后果

- 修正了三跳 M_cap 的量纲错误，闭环路径容量可以可信地作为后续单 Tick 交易量硬上界。
- 当前生产候选池表具备 `sqrt_price_x96`、`tick`、`liquidity`、`fee`、`tick_spacing` 与 token 顺序字段，代码已具备读取能力。
- 生产快照表当前尚无完整 single-tick 边界物化值；生产重物化必须等待 P1 备份与显式授权。
- 合约侧三态仲裁、比例上限、边界价格与利润底线尚未实现，不能把文档公式视为已落地合约行为。

## 验证

- `cargo test -p pool-manager single_tick_capacity`：14 passed。
- `cargo test -p closed-loop-manager`：18 passed。
- `cargo test --locked --workspace`：通过。

## 后续

1. P0-8-c：将 M_cap 接入单 Tick 最佳交易量选择器，输出稳定截断/拒绝原因码。
2. P0-8-e：冻结 ABI 与失败码，完成链上状态突变回放、批内隔离和 `amountOutMinimum` 测试。
3. P1-1/P1-3：获得授权后备份生产库，发布 tick 状态与 single-tick 边界，dry-run 后重物化候选/闭环。
