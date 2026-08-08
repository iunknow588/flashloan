# 测试目录结构说明

## 目录组织

测试文件按类型分为四个目录：

### 1. unit/ - 单元测试
测试单个函数、类或模块的纯逻辑，不依赖外部服务。

**主要测试内容：**
- `test_liquidation_amounts.py` - 清算金额计算（token_amount单位转换）
- `test_liquidation_priority.py` - 清算优先级评分（健康因子分层、优先级排序）
- `test_liquidation_preflight.py` - 清算预检逻辑（blockers分类、提交评估）
- `test_profit_guard.py` - 利润保护计算（滑点、安全边际扣除）
- `test_gas_estimator.py` - Gas费用估算
- `test_nonce_manager.py` - Nonce管理
- `test_private_tx.py` - 私有交易发送
- `test_parallel_submitter.py` - 并行提交器
- `cow_flashloan/test_routes.py` - CoW路由评估
- `cow_flashloan/test_capabilities.py` - 闪电贷能力评估
- `test_market_config.py` - 市场配置
- `test_config_schema.py` - 配置模式验证
- `test_observer_window_extremes.py` - 价格窗口极值
- `test_liquidation_samples.py` - 清算样本管理
- `test_liquidation_evidence_audit.py` - 审计证据
- `test_architecture_guards.py` - 架构守卫（模块行数限制）
- `test_secret_leakage_guards.py` - 敏感信息泄漏检查
- `account_pool/test_account_pool_state.py` - 账户池状态评估
- `debt_pool/test_debt_pool_workflow.py` - 债务池分层决策
- `intent_trade/test_builder.py` - 意图交易构造与差价额度
- `config/test_intent_trade_config.py` - 意图交易链路成本配置
- `market_events/test_volatility.py` - 市场波动事件构造与跨页面路由
- `market_events/test_store.py` - 市场波动事件存储与消费状态

### 2. integration/ - 集成测试
测试多个组件之间的交互和协作。

**主要测试内容：**
- `test_liquidation_engine.py` - 清算引擎（运行时依赖注入）
- `test_liquidation_scan.py` - 清算扫描（完整扫描流程）
- `test_liquidation_discovery_workflow.py` - 发现工作流
- `test_external_liquidation_index.py` - 外部清算索引
- `test_liquidation_audit_service.py` - 审计服务
- `liquidation/test_liquidation_discovery_service.py` - 清算发现窗口服务
- `liquidation/test_liquidation_discovery_workflow.py` - 清算发现与账户同步工作流
- `liquidation/test_liquidation_execution_service.py` - 清算执行载荷服务
- `cow_flashloan/test_candidate_queue.py` - CoW候选队列
- `test_market_observer_daemon.py` - 市场观察守护进程
- `test_binance_market_snapshot_daemon.py` - 币安市场快照

### 3. functional/ - 功能测试
测试Web界面、API端点和页面状态。

**主要测试内容：**
- `test_web_app_assembly.py` - Web应用组装（路由注册、页面渲染）
- `test_navigation_flow.py` - 导航流程
- `test_control_panel_status.py` - 控制面板状态
- `test_control_panel_liquidation_actions.py` - 清算操作
- `test_exchange_matrix_page.py` - 交易所矩阵页面
- `page_state/test_service.py` - 跨页面状态服务
- `test_control_panel_data_extremes.py` - 数据极值展示
- `binance_market/test_service.py` - 币安市场服务
- `observer_runtime/test_service.py` - 观察器运行时服务（启动进度、心跳）
- `liquidation/test_liquidation_web_helpers.py` - 清算展示与账户回填辅助

### 4. e2e/ - 端到端测试（预留）
用于完整的端到端测试场景，目前为空目录。

## 运行测试

### 运行所有测试
```bash
python -m pytest flashloan/src_bot/tests -q
```

### 运行特定类型测试
```bash
# 单元测试
python -m pytest flashloan/src_bot/tests/unit -q

# 集成测试
python -m pytest flashloan/src_bot/tests/integration -q

# 功能测试
python -m pytest flashloan/src_bot/tests/functional -q
```

### 运行特定模块测试
```bash
# 清算相关（排除网络测试）
python -m pytest flashloan/src_bot/tests -k "liquidation and not network" -q

# DEX回归测试
python -m pytest flashloan/srcs_dex/tests -q
```

## 测试约定

1. **优先使用 monkeypatch/fake**，不打真实RPC
2. **每个测试只验证一个关键行为**
3. **环境变量检查**：`int/float(os.getenv(...))` 应无命中；有命中优先迁移到 `parse_env_int/parse_env_float`
4. **敏感信息处理**：用户可见字段/日志/API/attempt/failure sample/报告字段必须使用 `redact_sensitive_text`
5. **模块行数限制**：关键模块行数 < 300（如 scanner.py, health_scanner.py, prioritizer.py）
6. **新增测试优先补**：状态归一、失败样本、退化路径、闭环测试
7. **功能归档**：跨页面复用功能放在同名包的测试目录中，例如 `unit/account_pool/`、`unit/debt_pool/`、`unit/intent_trade/`、`unit/config/`、`unit/market_events/`、`integration/liquidation/`、`functional/liquidation/`、`functional/page_state/`、`functional/observer_runtime/`

## 回归报告归档

归档路径：`docs/清理机器人/evidence/regression/`
命名格式：`YYYYMMDD-HHMMSS_regression_<scope>.md`

记录内容：
- 执行时间/人
- 版本
- 是否联网/真实RPC/Fuji
- 各命令结果
- 敏感信息检查
- 结论 pass/fail/blocked
