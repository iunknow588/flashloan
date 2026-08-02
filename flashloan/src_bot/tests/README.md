# `src_bot/tests` 测试说明

这份文档用来给后续测试、补测和回归提供统一参照。这里的测试以本地离线单测为主，核心原则是：

1. 尽量用 `monkeypatch`、fake、静态样本覆盖分支。
2. 涉及链上状态时，优先验证“流程是否会继续调用链上复核”，不要把外部索引或缓存当成最终结论。
3. 页面状态、执行状态、失败样本态要分别测，避免只测主路径。

## 目录结构

```text
src_bot/tests/
  conftest.py                  pytest 路径注入
  test_*.py                    业务单测
  README.md                    本说明
```

`conftest.py` 只做了一件事：把 `src_bot/` 加入 `sys.path`，方便直接 `python -m pytest flashloan/src_bot/tests`。

## 测试分层

### 1. 基础与架构守卫

关注配置、依赖边界、目录约束、启动行为是否稳定。

常见文件：

- `test_architecture_guards.py`
- `test_config_schema.py`
- `test_database_schema_status.py`
- `test_startup_behavior.py`

适合验证：

- 新增文件是否落在正确目录
- 环境变量是否缺失或冲突
- Schema / 配置健康度是否可读

### 2. 控制台页面与路由

关注 `/`、`/liquidation`、`/account-scan`、`/execution`、`/audit`、`/config` 等页面，以及 API 是否返回稳定状态。

常见文件：

- `test_web_app_assembly.py`
- `test_navigation_flow.py`
- `test_control_panel_status.py`
- `test_exchange_matrix_page.py`
- `test_page_state_service.py`
- `test_control_panel_liquidation_actions.py`

适合验证：

- 页面跳转是否存在闭环
- 页面 embedded / 非 embedded 模式是否一致
- `/api/status`、`/api/liquidation/*` 的状态字段是否回归
- 执行链路中的 `submission_failed`、`static_call_failed`、`confirmed_failed` 是否归一

### 3. 清算发现与扫描

这是当前最重要的一层，负责把“候选账号”变成“可执行前的链上复核结果”。

常见文件：

- `test_liquidation_discovery_service.py`
- `test_liquidation_discovery_workflow.py`
- `test_liquidation_scan.py`
- `test_liquidation_scan_modules.py`
- `test_external_liquidation_index.py`
- `test_liquidation_priority.py`
- `test_liquidation_amounts.py`

推荐关注的链路顺序：

```text
外部粗筛 -> 链上 Borrow 日志发现 -> 账户健康度扫描 -> 候选排序 -> 执行前置校验
```

这里的测试重点不是“外部索引准不准”，而是：

- 外部索引是否只作为候选来源
- 候选是否会去重合并
- 最终是否仍走 `scan_account_health()`
- `Multicall3` 失败时是否能退回单账户 RPC
- 账户数上限、批次、并发是否保持稳定

### 4. 执行、预检、盈利兜底

关注从 report 到 payload，再到 static call / submit / receipt / failure sample 的完整闭环。

常见文件：

- `test_liquidation_preflight.py`
- `test_liquidation_execution_service.py`
- `test_liquidation_engine.py`
- `test_liquidation_pause_guard.py`
- `test_profit_guard.py`
- `test_nonce_manager.py`
- `test_parallel_submitter.py`
- `test_private_tx.py`

适合验证：

- `minProfitAmount` 是否严格生效
- `ProfitTooLow` 是否能阻断亏损执行
- `static_call_required`、`static_call_passed`、`execution_phase` 是否一致
- `receipt.status == 0` 是否进入失败样本态
- 强制执行是否只绕过软阻断，不绕过硬阻断

### 5. 市场与观察

关注行情监控、阈值触发、观察器运行态。

常见文件：

- `test_observer_config.py`
- `test_observer_runtime_service.py`
- `test_observer_window_extremes.py`
- `test_market_volatility_event_service.py`
- `test_trigger_signal.py`
- `test_arbitrage_strategy.py`
- `test_dynamic_quote.py`
- `test_gas_estimator.py`

### 6. 工具和分析

用于结果复盘、窗口分析、阈值分析、可执行信号构建。

常见文件：

- `test_analyze_thresholds.py`
- `test_analyze_trade_results.py`
- `test_build_executable_signal.py`
- `test_collect_liquidation_history.py`
- `test_check_manual_prereqs.py`
- `test_aave_hit_stats.py`

## 常用命令

```powershell
python -m pytest flashloan/src_bot/tests -q
python -m pytest flashloan/src_bot/tests/test_liquidation_scan.py -q
python -m pytest flashloan/src_bot/tests/test_control_panel_status.py -q
python -m pytest flashloan/src_bot/tests -k "liquidation and not network" -q
python -m pytest flashloan/src_bot/tests -x --maxfail=1
```

合约侧联动回归单独跑：

```powershell
npm test -- --grep "ProfitTooLow"
npx hardhat test test/AaveV3LiquidationExecutor.test.js
```

## 变更速查

改这些地方时，优先跑对应测试：

1. 路由、页面跳转、埋入态
   - `test_navigation_flow.py`
   - `test_web_app_assembly.py`
   - `test_exchange_matrix_page.py`
2. 控制台状态、执行态、失败样本态
   - `test_control_panel_status.py`
   - `test_control_panel_liquidation_actions.py`
   - `test_liquidation_audit_service.py`
3. 清算发现、外部索引、链上复核
   - `test_external_liquidation_index.py`
   - `test_liquidation_discovery_service.py`
   - `test_liquidation_discovery_workflow.py`
   - `test_liquidation_scan.py`
4. 预检、报价、payload、盈利兜底
   - `test_liquidation_preflight.py`
   - `test_execution_payload.py`
   - `test_profit_guard.py`
   - `test_liquidation_amounts.py`
5. 合约盈利保护和回执闭环
   - `contracts-bot/test/AaveV3LiquidationExecutor.test.js`

## 新增测试建议

新增测试时，优先补下面几类：

1. 状态归一测试
   - 例如 `route_failure_state()`、`execution_phase`、`receipt.status`
2. 失败样本测试
   - 例如提交失败、链上回执失败、静态调用失败
3. 退化路径测试
   - 例如 Multicall 不可用、外部索引不可用、RPC 单点失败
4. 闭环测试
   - 例如发现 -> 复核 -> 排序 -> payload -> preflight -> submit

## 编写约定

- 优先使用 `monkeypatch` 或 fake 对象，不直接打真实 RPC。
- 每个测试只验证一个关键行为。
- 名称尽量把场景写清楚。
- 涉及清算时，优先断言 `health_factor`、`status`、`execution_phase`、`receipt.status`、`profit`、`failure_type`。
- 如果改了页面跳转或接口状态，优先补 `navigation` / `status` / `actions` 三类测试。

## 回归顺序建议

1. 先跑基础守卫。
2. 再跑页面和路由。
3. 再跑清算发现与扫描。
4. 再跑执行、预检、盈利兜底。
5. 最后跑合约测试。

一个比较稳的组合是：

```powershell
python -m pytest flashloan/src_bot/tests/test_architecture_guards.py
python -m pytest flashloan/src_bot/tests/test_control_panel_status.py flashloan/src_bot/tests/test_navigation_flow.py
python -m pytest flashloan/src_bot/tests/test_liquidation_scan.py flashloan/src_bot/tests/test_liquidation_discovery_workflow.py flashloan/src_bot/tests/test_external_liquidation_index.py
python -m pytest flashloan/src_bot/tests/test_control_panel_liquidation_actions.py
npx hardhat test test/AaveV3LiquidationExecutor.test.js
```

## 当前最值得优先维护的测试点

- `/execution` 的提交态、receipt 态、失败样本态
- 清算发现链路里的“外部索引只做粗筛”
- 链上健康度扫描必须是最终裁决
- `minProfitAmount` 与 `ProfitTooLow`
- 页面路由和嵌入态跳转闭环

## 最近新增

- `test_external_liquidation_index.py`
- `test_liquidation_discovery_workflow.py`
- `test_navigation_flow.py`

