# FlashLoan 未完成重构任务执行计划

更新时间：2026-09-05

本计划依据 `docs/manage/任务分解列表_20260904.md`、`docs/manage/待完成任务清单_20260904.md`、`docs/manage/重构check_list.md` 和当前代码证据整理，只保留尚未完成、待验证和外部阻断任务。已完成的 scope validator、JSON/JSONL adapter、runtime state、已迁移 DB/cache 校验、API route policy、响应 schema 基础设施和 snapshot replay 基础设施不重复列为待办。

## 执行原则

- `blocked_external` 不得标记为 `completed`。
- `execution_enabled` 和 `broadcast_enabled` 始终 fail-closed。
- 主网 `4663` 与测试网 `46630` 的配置、证据和 readiness 必须隔离。
- 每项完成记录责任人、代码或配置路径、执行时间、测试命令及结果、schema/generation/hash、异常和回滚记录。
- 保留用户已有修改，不修改 `docs/manage/back` 中的归档文档。
- 本轮最高优先级：将 `flashloan/src_bot` 按功能迁移到 `flashloan/src/{bot,core,dex,market}`；迁移与 P0 安全门禁可并行，但不能削弱 scope、evidence、fail-closed 和网络隔离语义。

## Remaining Work

### P0-01 基线与入口冻结（active）

- 已核对 `flashloan/src_bot` 主代码目录、`flashloan/src` 目标目录、`flashloan/run.py`、`flashloan/run_local_preview.py`、`flashloan/.replit`、`flashloan/replit.nix` 和根目录 pytest 入口。
- 已生成 [入口矩阵](docs/manage/back/入口矩阵_20260904.md)、[废弃目录清单](docs/manage/back/废弃目录清单_20260904.md) 和 [基线证据](docs/manage/back/基线证据_20260904.md)，并归档至 `docs/manage/back/`。
- 已处理 pytest 同名模块收集冲突：根目录 `pytest.ini` 使用 `--import-mode=importlib`；已完成全量收集验证，共 1304 个测试。

完成条件：文档、入口和测试配置一致；收集通过；基线 evidence 可追溯。

### P0-02 API scope 与成功响应 schema 收口（active）

- 补齐所有公共 API 的成功响应 schema registry 和真实 payload contract tests。
- 重点覆盖 DEX observation/overview/quote、flash-loan pools、liquidation accounts/samples/failures/attempts、Binance、Robinhood 和 CoW API。
- 校验响应 scope 与请求 scope 一致，统一 validation、unsupported、scope mismatch 和 response contract 错误码。
- 保持 scope-free metadata 白名单，并执行 route map、method-aware metadata、schema registry 双向检查。

完成条件：每个公共 API 有成功 schema 测试；非法 scope/参数和错误响应结构均失败；registry 无遗漏。

### P0-03 多存储 scope 一致性收口（active）

- 继续审计尚未接入 `validate_scoped_row(s)` 的 DB、清算历史、扫描恢复、聚合和 legacy 查询。
- 校验数据库 JSON 嵌套 scope 与外层 DB scope；检查 cache key 是否包含 market、chain、network、protocol。
- 继续审计 daemon、临时文件、runtime 目录和 evidence package 的读写路径。
- 为每个迁移路径补 valid、missing scope、wrong scope、cross-market isolation 测试。

完成条件：错误 scope 不得进入 overview、quote 或 execution；DB、cache、JSONL、runtime state、evidence 契约一致；跨存储链路有测试。

### P0-04 至 P0-12 执行安全与发布门禁（active）

- 补齐 PoolManager 的 liquidity、marketability、profitability、executability 四层输出和字段契约。
- 分离余额、候选池、价格来源和 freshness；stale 数据自动阻断；无可靠报价返回 `requires_live_quoter`。
- 执行前复核 scope、evidence freshness、target hash、deadline、nonce、幂等键、最低利润、gas、滑点、flash-loan fee、MEV 和余额，并生成可审计 `GateDecision`。
- 隔离 liquidation 与 CoW 的 context、队列、锁、payload、feature flag；隔离主网与测试网配置和 evidence。
- 完成生产 fail-closed 自检和 dry-run manifest、日志、schema、generation、hash evidence。

完成条件：任一安全条件失败即不提交或广播；fault-injection、隔离和 dry-run evidence 测试通过。

### P1-01 至 P1-07 代码拆分与依赖收敛（active）

- 按 bot、dex、market、core 批次迁移 `src_bot`，保留并逐步收缩兼容 shim。
- 拆分 `dex_surfaces.py`、market service、control panel 和 storage adapter；API 层只负责解析、鉴权和 DTO 编排。
- 统一超时、取消、重试和状态迁移；清理 silent pass，补 trace/request/workflow/generation/evidence 信息。
- 维护 architecture guard、依赖图、循环依赖负向测试和 API diff。

完成条件：新包导入、兼容 shim、compileall、pytest 收集、静态依赖和行为回归均通过。

### P1-08/P1-09 Workflow Replay（active）

- 将真实 observation → overview → quote → gate 流程接入 replay fixture。
- 所有阶段共享 scope 和 `generation_id`；使用 canonical snapshot hash 生成稳定 `target_hash`。
- 覆盖输入变化、scope/generation 变化、过期 evidence、错误 schema 和 target hash 不一致。
- 持久化 fixture、manifest 和审计结果。

完成条件：相同输入 hash 稳定；scope/generation 不一致直接失败；真实 fixture 可读、可验、可重放。

### P1-10 指标与可观测性（active）

- 建立 RPC、quote、gate、revert、lock、timeout、retry、fallback、stale evidence 指标。
- 统一 trace/request/workflow/generation/evidence/target hash；结构化记录异常、重试、回滚和 gate 阻断原因。
- 明确指标来源、告警阈值和审计保留周期。

完成条件：指标和告警有测试或 schema；失败请求可追溯；silent pass 扫描为零或有例外记录。

### P2-01 至 P2-03 Schema、迁移与 legacy 清理（active/deferred）

- 定义 schema version、兼容策略、迁移脚本和回滚脚本。
- 清理空目录、运行时废弃目录和临时入口，保留删除 evidence。
- canonical `market_id` 迁移窗口结束前，P2-03 保持 `deferred`，不得移除 `legacy_market_id` 和 `/legacy` 兼容路径。

## External Blockers

以下任务保持 `blocked_external`：

- P0-13 Testnet canary。
- P0-14 生产 readiness 评审和 `production_readiness` 状态变更。
- P1-11 Robinhood Testnet V4 余额、PoolKey、quoter/SDK 真实只读验证。
- P1-12 主网 `4663` shadow/read-only 观察期。
- P2-04 SLO、告警、值班、升级路径和演练记录。

解除阻断必须具备责任人、环境配置摘要、时间窗、原始 evidence 路径、schema/generation/hash、测试结果、异常、回滚和审批记录。

## Execution Order

1. 推进代码重构迁移：`src_bot` → `src/{bot,core,dex,market}`，先完成归属映射和首批迁移。
2. 并行收口 P0-01 基线、P0-02 API schema、P0-03 存储 scope 审计。
3. 完成 P0-04 至 P0-12 安全门禁、dry-run 和网络隔离。
4. 接入真实 workflow replay。
5. 继续代码拆分、依赖收敛和可观测性。
6. 最后执行 schema migration、legacy 清理和废弃目录删除。
7. 外部任务仅在完整 evidence 到位后单独解除。

## Validation Matrix

- Unit：scope、schema、能力状态、hash、gate、错误码和状态迁移。
- Integration：observation → overview → quote → gate → replay。
- Functional：BOT、DEX、Market、Control Panel 页面和 API payload。
- Fault injection：RPC、quote、DB、锁、过期 evidence、错误 scope/generation、revert。
- Static/release：compileall、pytest collect-only、architecture guard、依赖图、API diff、manifest、配置 hash、schema version、evidence hash 和回滚指令。

## Current Evidence

- 已完成的 DB scope 读取校验和相关测试记录在现有测试文件及此前执行记录中。
- 本轮将 pytest 收集冲突修复为 `importlib` 导入模式；`python -m pytest flashloan/src_bot/tests --collect-only -q` 结果为 1304 tests collected。
- 本轮完善 `docs/manage/重构check_list.md`：已勾选有代码/测试证据的 scope、错误分类、报价 fail-closed、网络隔离和启动 fail-closed 项；PoolManager 业务四层 evidence 接入、完整 API schema、外部验证和发布验收仍保持未完成。
- 本轮继续更新 `docs/manage/重构check_list.md`，补充入口基线、route policy、DB scope 迁移、fail-closed 测试证据及剩余未完成项；真实 API 成功响应样本、剩余存储审计和执行门禁继续保持未完成。
- 本轮推进 P1-08/P1-09：`core/snapshot_replay.py` 复用统一 `validate_scope_contract` 校验 replay 阶段 scope，并新增缺少 protocol、字符串 chain_id 规范化测试；真实业务 workflow fixture 仍未伪造完成。
- 本轮继续推进 P1-08/P1-09：replay fixture 新增顶层规范化 scope，并在读取/验证时检查其与三个阶段 scope 一致；新增顶层 scope 漂移负向测试。
- 本轮继续推进 P0-02：新增 `missing_response_schema_paths`，显式报告 route map 中尚未登记成功响应 schema 的业务路由，并补单元测试；真实业务 payload schema 仍保持未完成。
- 本轮继续推进 P0-02：登记 market-analysis、DEX strategy、strategy config 和 velocity timepoints 的最小成功响应 schema，并补批量 contract test；refresh、scanner、liquidation action 和 CoW admin 路由仍待补齐。
- 本轮继续推进 P0-02：登记控制/执行计划、DEX refresh/scanner/cost、CoW admin 和清算池查询的稳定成功响应外层字段，并新增批量 contract test；真实成功 payload 与动态错误响应仍待验证。
- 本轮完成 schema contract 回归修正：测试夹具已与执行计划 `payload`、CoW config `network` 实际返回字段对齐；控制/执行/DEX/CoW/清算池 schema 回归通过。
- 本轮推进 P0-03：历史清算报告读取增加可选 `expected_scope`，清算账户恢复和池快照已传入当前 scope；显式携带错误 scope 的历史 JSON 会拒绝，同时保留无嵌套 scope legacy 三列数据兼容性。
- 本轮先更新 `docs/manage/重构check_list.md` 的持续推进状态，明确已验证基础能力、下一批无外部卡点任务和保持 `blocked_external` 的任务；随后继续按 P0-02/P0-03/P0-04 优先级推进。
- 本地测试不能解除外部环境阻断。
- 本轮完成 P0-02 第二批/第三批 schema 收口：route map 中业务 API 已无 `missing_response_schema_paths`；已补齐 velocity candidates、db-summary、debt-pool decision、candidate refresh、submissions、flash-loan refresh、liquidation account/backfill/daemon/reports/preflight 等 contract test，并修正 `control-summary`、`liquidation/account/payload` 契约与真实返回一致。
- 本轮推进 P0-04：`core/flash_loan_pool_store.py` 新增 `pool_evidence_from_view`，静态池快照输出 `liquidity`、`marketability`、`profitability`、`executability` 四层契约；默认 fail-closed，profitability 无实时报价时保持 `requires_live_quoter`；`test_flash_loan_pool_store.py` 与 `test_flash_loan_pools.py` 已通过。
- P0-04 业务缺口已补：`web/flash_loan_pools.py` 的 `protocol_views` 已暴露 `pool_evidence`，`static_store_metadata` 同步携带 `pool_evidence` 与 `pool_evidence_schema_version`；下一步接入 dry-run manifest 和 GateDecision 消费。
- 当前 `python -m pytest flashloan/src_bot/tests --collect-only -q` 为 `1315 tests collected`。
- P0-05/P0-08 freshness 门禁已补：`execution/profit_guard.py` 支持 `max_quote_age_seconds`，缺失时间戳或过期报价均 fail-closed；`test_profit_guard.py` 4 项通过。
- 本轮整理 `docs/manage/`：已完成任务和历史文档归档到 `docs/manage/back/`，根目录仅保留待完成任务清单、任务分解列表和详细 check list；新增 `docs/manage/back/flashLoan_已完成任务归档_20260905.md`。
- 当前测试收集最新记录为 `1317 tests collected`。
- 本轮调整优先级：`docs/manage/任务分解列表_20260904.md`、`docs/manage/重构check_list.md` 和本计划已将 `src_bot → src/{bot,core,dex,market}` 迁移列为最高优先执行项。
