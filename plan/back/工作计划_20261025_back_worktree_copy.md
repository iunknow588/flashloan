# Robinhood V4 全量后续工作计划（2026-10-26 第十轮收口重排 / 11:23Z 基线）

> 唯一任务源：[待完善任务清单_20261025.md](待完善任务清单_20261025.md) 与 [后续完善开发计划_20261025.md](后续完善开发计划_20261025.md)。
> 本计划是执行排期视图，不替代源文档；任务编号、状态、推进类型与验收以源文档为准。
> 生成时间：2026-10-26（Asia/Shanghai）。基线取用已有 evidence 文件的最新版本；实施推进记录见“本轮已实施”，不手工改 evidence、SQLite、`latest.json` 或运行时 API。

## 目标与执行口径

- 只推进 Robinhood V4 测试网实盘观察，不扩多链；执行依据必须是实时行情/链上 Quoter（`dex_quote_routes`、live Quoter 或 `eth_call`），历史快照只作审计。
- 测试网广播开关保持 `true`；主网、paused-deploy、executor canary 广播保持 `false`。
- 方向完成门槛为 1 条活跃候选，5 仅作推荐池数量上限；除 `TZ -> TA -> TB` 主动测试注入流动性外，其他闭环不自行注入流动性。
- readiness 只由 CLI 写入；无 signed 数值时 P&L 保持 `null` + `missingReason`，不用零占位；提交记录唯一在线存储为 SQLite。
- 所有结论必须来自命令输出或 evidence 文件；任何参数/搜索模式调整必须先有链上 evidence。
- 排期采用触发式而非固定日期：Phase A 立即持续，Phase B 等新 unique success，Phase C 等维护窗口，Phase D 等外部审批。

## 当前基线（2026-10-26 11:23Z）

| 项目 | evidence 路径 | 基线 |
|---|---|---|
| P&L | `flashloan/src/runtime/evidence/robinhood-testnet/closed_loop_pnl/20261026T112335Z/report.json`（`latest.json` 同源） | 生成 `2026-10-26T12:30:36Z`，窗口 `2026-10-25 20:04:18Z-2026-10-26 11:23:35Z`：`rowCount=1215`、0 unique success、1 failed、17 ready、1190 blocked、`reviewConsistentCount=24`、`reviewInconsistentPositiveEstimateCount=305`、`rpc_transport_error=0` |
| auto-scan | `flashloan/src/runtime/evidence/robinhood-testnet/auto_scan_verification/20261026T112335Z/report.json` | 窗口 `2026-10-25 20:04:18Z-2026-10-26 11:23:35Z`：`attemptsInWindow=1215`、0 unique success、1 failed、1214 blocked、`broadcastCount=1`（execution_failed 记录）、`runtimeSnapshot.running=true`、`auto_execute_enabled=true`、`auto_broadcast_count=0` |
| expected-vs-actual | `flashloan/src/runtime/evidence/robinhood-testnet/expected_vs_actual/latest.json` | 窗口 `2026-10-25 20:04:18Z-2026-10-26 11:23:35Z`：0 unique success、`decodableCount=0`、`zeroDifferenceCount=0`、无差异分析触发 |
| 零流动性核对 | `flashloan/src/runtime/evidence/robinhood-testnet/zero_liquidity_vs_candidates/latest.json` | 生成 `2026-10-26T11:34:22Z`：`explicitZeroLiquidityPoolCount=0`、`residualReferenceTotal=0`、`scanStateMatchesExplicitPools=true` |
| validate | `flashloan/src/runtime/evidence/robinhood-testnet/validation_assumptions_sqlite_latest.json` | 生成 `2026-10-26T11:34:50Z`：`attempt_count=5000`、`quote_success_count=4999`、`submitted_count=0`、`settled_count=0` |
| idle maintenance | `flashloan/src/runtime/evidence/robinhood-testnet/idle_maintenance/20261025T115246Z/report.json` | `generatedAt=11:52:46Z`、`ok=true`、`phase=directions_satisfied`、`neededDirectionCount=0`、`satisfiedDirectionCount=6`，来源 `rust_closed_loop_sqlite_maintenance_consumer` |
| 页面性能 | `flashloan/src/runtime/evidence/robinhood-testnet/page_perf_latest.json` | 静态面 warm 全部 <1s；live `dex_quote_routes` warm=41.73s 仅作实时链上 baseline |
| 快照性能 | `flashloan/src/runtime/evidence/robinhood-testnet/snapshot_perf_latest.json` | 已收口并保持复测：最新 `20:25Z` 6 次采样全部 200、`0.663-1.691s`，warm <10s 达成；快照快速路径与 compact status 已生效；当前 5000 无常驻进程，生产常驻由用户/运维受控重启 |
| readiness | `flashloan/src/runtime/evidence/robinhood-testnet/predeploy_readiness/latest.json` | `readyForPausedRedeploy=true`，仍列 `latest_executor_gate_not_ready`、`current_executor_mismatch_not_ready`、`broadcast_gate_not_closed` |
| 主网只读 | `flashloan/src/runtime/evidence/robinhood/mainnet_readonly_gate/latest.json` | 生成 `2026-10-25T20:47:44Z`、block `97875195`、`broadcastEnabled=false`、`readyForBroadcast=false`；2026-10-26 12:57 +08:00 `--no-write` 复检返回 block `98155295`（未写盘；历史 `01:19:25Z/97207308` 归档） |
| chain-test 实时性 | `flashloan/src/runtime/evidence/robinhood-testnet/chain_test/latest/plan.json` | `generatedAt=2026-10-22T22:54:30Z`；12:57Z `--strict-live --no-write` 复检仍 false（`chain_test_plan_stale_against_current_opportunity_snapshot`，`currentOpportunitySnapshotUpdatedAt=2026-10-25T20:03:50Z`、`currentLiveProfitableRouteCount=1`），未写盘；禁止用旧 route_capture 兜底当作当前可执行证据 |
| 生产 scanner | 运行状态观测 | `rust_native_closed_loop`、16 worker、`auto_execute_enabled=true`、`auto_broadcast_count=0`；生产 SQLite 已分表生效：`cow_execution_reviews=13614`（最新 `2026-10-26T11:23:35Z`）、`cow_execution_attempts=1`（`execution_failed`、无 `tx_hash`）；`rpcFailoverCount=1` 全库 86 条（03:58Z 前 21 条、11:50-20:04 窗口 33 条，最新 `15:46:55Z`），20:04:18Z 后无新增；当前 5000 无常驻 HTTP 进程，快照采样按受控窗口执行 |

历史自动扫描成功广播共 14 条（含 `2026-10-24 17:44:56Z-17:49:23Z` 的 4 条与更早归档 10 条；非 TZ 8 条、TZ 6 条）仅保留在 evidence/归档，链上 receipt 均 `0x1`；在线 SQLite 清理后当前 0 success，M2-2 等待新 unique success 自动落库。完整清单见 `docs/关于Robinhood/测试网稳定套利分析_20261026.md`。

## 本轮已实施（2026-10-26）
- 2026-10-26 +08:00 19:35 M6-7 已收口：`reviewConsistency` 字段、P&L/auto-scan 计数、记录页与报价页中文展示、归档报告统计与单测全部完成；相关测试 73 passed，在线页面渲染验证通过。
- 2026-10-26 +08:00 20:30 复核：最新 evidence 仍为 `closed_loop_pnl/20261026T112335Z`（`generatedAt=2026-10-26T12:30:36Z`），窗口、计数与 19:35 一致；M7-4（复核一致性具体数值展示与原生 signed 值落库）保留为待推进开发项。
- 2026-10-26 +08:00 19:35 M1-1 已刷新至 `2026-10-25 20:04:18Z-2026-10-26 11:23:35Z` 新窗口：P&L `rowCount=1215`、0 unique success、1 failed（`AMZN -> ETH -> AMD -> AMZN`、`execution_failed`/payload `submission_failed`、无 `tx_hash`）、17 ready、1190 blocked、`reviewConsistentCount=24`、`reviewInconsistentPositiveEstimateCount=305`；auto-scan `attemptsInWindow=1215`、`broadcastCount=1`（对应该 failed 记录）、0 success、`auto_broadcast_count=0`；expected-vs-actual 0 unique success；zero_liquidity 11:34:22Z 无显式池；validate 11:34:50Z；在线 SQLite `cow_execution_reviews=13614`（最新 `2026-10-26T11:23:35Z`）、`cow_execution_attempts=1`（`execution_failed`，无 tx_hash，`NoProfitableRoute()`）。
- 2026-10-26 +08:00 19:35 M6-7 已收口：`reviewConsistent` 字段、P&L/auto-scan `reviewConsistency` 计数、记录页与报价页中文展示、归档报告统计与单测全部完成；相关测试 73 passed，在线页面渲染验证通过。
- 2026-10-26 +08:00 04:41-04:48 执行开发计划：SQLite 无新窗口（最新 `2026-10-25T20:04:18Z`），`cow_execution_attempts=0`、`cow_execution_reviews=12400`；zero_liquidity 刷新至 `20:44:59Z`（0 显式池）、validate 刷新至 `20:45:17Z`（`attempt_count=5000`、`quote_success_count=4999`、`settled_count=0`）；preflight=true 累计 31 条均不可提交，无 execution_success / tx_hash；live strict 仍 false、predeploy 三项 blocker 不变、主网只读刷新至 block `97875195` fail-closed；P0-14/P2-04/M3-1 外部依赖未变化。
- 2026-10-26 +08:00 12:48 复核：仍为 `cow_execution_reviews=12400`、`cow_execution_attempts=0`、最新 `2026-10-25T20:04:18Z`；preflight=true 累计 31 条均不可提交；failover 全库 86 条 `rpcFailoverCount=1`（最新 15:46:55Z）、20:04:18Z 后无新增；无 tx_hash/success，M1-2/M2/M6-2 未触发，详见 `plan/back/开发计划_20261026.md` 第 30 节。
- 2026-10-26 +08:00 12:57 M5-2 只读复检：live `--strict-live --no-write` 仍 false（chain-test stale）；predeploy `--strict-fork --no-write` 仍 `readyForPausedRedeploy=true` 且三项 blocker 未变；主网只读 gate 刷新至 block `98155295` 且 fail-closed，均未写盘，详见 `plan/back/开发计划_20261026.md` 第 31 节。
- 2026-10-26 继续执行本轮（+08:00 04:13-04:25）：M1-1 已刷新至 2026-10-25 20:04:18Z 新窗口（11:50:00Z-20:04:18Z）：P&L `reviewedCount=1367`、0 success、0 failed、1358 blocked（`no_profitable_route=414`、`quoter_unavailable=766`、`rpc_transport_error=0`）、带数值 178 条、`readyCount=4`；auto-scan `attemptsInWindow=1367`、`broadcastCount=0`；expected-vs-actual 0 success；zero_liquidity 20:13:47Z 无显式池；validate `attempt_count=5000`、`quote_success_count=4999`、`settled_count=0`；M5-2 strict-live 20:20Z 仍 false、predeploy 三项 blocker 不变、主网只读 RPC 不可达 fail-closed；M6-6 快照 6 次采样全部 200、`0.663-1.691s`，`snapshot_perf_latest.json` 已更新。
- 2026-10-26 继续核对（+08:00 04:28-04:30）：SQLite 最新复核仍为 2026-10-25 20:04:18Z、`cow_execution_attempts=0`，无新窗口；zero_liquidity 已刷新至 20:28:13Z（0 显式零流动性池）、validate 已刷新至 20:28:17Z（`attempt_count=5000`、`quote_success_count=4999`、`submitted_count=0`、`settled_count=0`）；M5-2 20:28Z 复检 strict-live 仍 false、predeploy 三项 blocker 不变、主网只读 RPC 不可达保持 fail-closed；5000 无监听进程，M6-6 保持 20:25Z 采样基线，未重复采样。

- M7-1 已收口：`v4_sqlite_view.py` 为 index/liquidity payload 增加 15s 进程内 TTL 缓存与 `clear_v4_pool_payload_cache`，`flash_loan_pool_views.py` 刷新链路显式清缓存；在线实测 `/flash-loan/pools?dex_strategy=closed_loop_routes&chain_id=46630...` 四次均 200、`0.406s/0.370s/0.361s/0.800s`，证据见 `flashloan/src/runtime/logs/restart_m7_online_evidence_9452.json`。
- M7-2 已收口：`dex_route_test.py` 新增 `_offline_v4_route_sqlite_pnl_reference`，`rust_closed_loop.py` 无 signed 时写 SQLite 快速参考 P&L；实测 `AMD -> AMZN -> ETH -> AMD` 1 AMD 参考 `referenceNetProfitAmount=-0.9793313498080748`、`referenceNetReturnBps=-9793.313498080748`；无法计算保持 null + missingReason。
- M7-3 已收口：`dex_opportunity_scanner_rust_mixin.py` 新增 `_native_group_busy` / `_manual_native_closed_loop_review`，`dex_surface_routes_ops.py` 新增 POST `/api/dex/closed-loop-groups/manual-review`，忙碌返回 `closed_loop_group_busy`（409）；`worker_pool.rs` 输出 `poll_interval_seconds`/`next_scan_at`；`quotes_app.js`/`quotes_render.js` 展示忙碌/空闲、复核间隔与下一轮自动复核倒计时，忙碌时按钮 disabled。
- R1-R4 在线已生效：5000 服务加载新路由后，四新页与四新 API 均 200，旧 `/dex/submissions/success`、`/failed`、`/review` 及分类旧路由均 404；总览页包含四个文档全名且无旧 href。
- 回归验证：`test_web_app_assembly_dex_records.py` + `test_web_app_assembly.py` + `test_web_app_assembly_workflows.py` + `test_api_scope_policy.py` 85 passed；`test_rust_closed_loop_worker_bridge.py` + `test_dex_route_test.py` + `test_flash_loan_pools_robinhood.py` 135 passed。

- M6-6 快照快速路径已落地：`load_auto_execute_log_stats` 只读取轻量列与 `execution_success` 的 `summary_json`，同库实测约 0.125s vs 原 9.7s；快照接口 warm 目标仍为 <10s，如仍有瓶颈继续定位 worker 状态序列化。
- M6-6 Rust worker compact status 已落地：新增 `compact_status_json()` / `native_worker_status(include_payloads=False)`，快照 `_rust_worker_status()` 只取轻量 worker 字段，不再跨 PyO3 序列化 16 worker 的 `last_result` / `broadcast_history` / `direction_results`；cargo test 60 passed、worker bridge 33 passed；release wheel 已构建，17:00 +08:00 复核未发现监听 5000 的 python 进程，恢复服务需受控重启，生效状态以受控重启记录为准。
- M6-5 成交/复核底层物理分表已落地：`cow_execution_attempts` 只保存 `execution_success` / `execution_failed` 成交提交记录，新增 `cow_execution_reviews` 保存 `not_executable` 复核记录；写入按 category 自动路由，读取/计数/清理按 record_kind 隔离；旧 `not_executable` 行在首次 ensure 时自动迁入复核表；归档脚本同步统计两张表；分表专项测试 3 passed，相关存储/装配回归通过。
- M6-5 分表回归收口：`test_dex_submission_review_keeps_failed_broadcast_attempt_without_tx_hash` 更新为 `execution_failed` 无 `tx_hash` 仍属成交提交记录，并新增 broadcastDisabled 隐藏用例；`test_pool_candidates_scanner.py` 34 passed，相关存储/装配 91 passed 1 skipped。
- M1-1 已重跑至 `09:13:40Z-09:45:00Z`：P&L `reviewedCount=224`、0 unique success、0 failed、213 blocked（`no_profitable_route=68`、`quoter_unavailable=145`、`rpc_transport_error=0`）、`withExpectedProfitCount=11`、`readyCount=6`；auto-scan 224 attempts 全 blocked、`broadcastCount=0`；expected-vs-actual 0 success；zero_liquidity 09:47:34Z 无显式零流动性池；validate 09:47:39Z `attempt_count=5000`、`quote_success_count=5000`、`settled_count=0`；P&L evidence 已写盘为 `20261025T094500Z/report.json`，auto-scan evidence 为 `20261025T094500Z/report.json`。
- M1-2 观察：SQLite 精确扫描累计 15 条 `quote.live_onchain_evidence.readyForTransactionPreflight=true` 复核记录（06:38Z 起 4 条 id=33838/33840/33872/33904；09:14Z-09:43Z 新增 11 条 id=34083/34085/34092/34094/34107/34119/34263/34284/34286/34298/34300，含 `TSLA -> ETH -> AMZN -> TSLA`、`AMZN -> ETH -> TSLA -> AMZN`、`ETH -> AMZN -> TSLA -> ETH`、`TSLA -> AMZN -> ETH -> TSLA`）；15 条全部 `not_executable` / 顶层 `chain_submission_candidate=0` / `can_submit_order=0` / `checks_passed=0`，auto_execute 记录为 `native_broadcast_disabled`；09:47Z `--strict-live --no-write` 仍 false（`chain_test_plan_stale_against_current_opportunity_snapshot`，当前 opportunity snapshot 09:42:02Z、`currentLiveProfitableRouteCount=2`），本轮未触发 M1-2 重建。
- 生产 `flashloan/src/runtime/local_store.sqlite3` 已分表生效：`cow_execution_attempts=0`、`cow_execution_reviews` 持续增长（09:47Z 快照 10259，最新已到 09:43:12Z），M6-5 无需再等维护窗口手动迁移；进程 PID 21492（17:11 +08:00 启动）仍在写库，未监听 5000/5001。
- 本轮复核（17:47 +08:00）：SQLite 新增 09:13:40Z-09:43:12Z 复核窗口 224 条（`cow_execution_reviews` 已到 10259+），无新提交类记录；M1-2 `--strict-live --no-write` 09:47Z 仍 `readyForLiveTest=false`（opportunity snapshot 09:42:02Z、`currentLiveProfitableRouteCount=2`），M5-2 `--strict-fork --no-write` 09:47Z 仍 `readyForPausedRedeploy=true` 且三项 blocker 未变；进程 PID 21492 未监听 5000/5001，M6-6 HTTP 采样暂停。
- M1-1 补充刷新（09:47Z）：`verify_robinhood_v4_zero_liquidity_vs_candidates.py` 与 `validate_live_assumptions.py` 已复跑，zero_liquidity `generatedAt=09:47:34Z`、validate `generated_at=09:47:39Z`，与 09:13Z 版一致（0 显式零流动性池、`attempt_count=5000`、`quote_success_count=5000`、`settled_count=0`）；SQLite 精确扫描确认 `readyForTransactionPreflight=true` 累计 15 条，P&L `readyCount=6` 只计带 signed 预期利润的 09:14Z-09:43Z 行。
- M1-1 新窗口（11:20Z）已重跑并落盘：P&L `reviewedCount=192`、0 unique success、0 failed、192 blocked（`no_profitable_route=72`、`quoter_unavailable=120`、`rpc_transport_error=0`）、`withExpectedProfitCount=0`、`readyCount=0`；auto-scan `attemptsInWindow=192` 全 blocked、`broadcastCount=0`；expected-vs-actual 0 unique success；zero_liquidity 11:25:05Z 无显式零流动性池；validate 11:25:29Z `attempt_count=5000`、`quote_success_count=5000`、`settled_count=0`；evidence 已写盘为 `closed_loop_pnl/20261025T112000Z/report.json` 与 `auto_scan_verification/20261025T112000Z/report.json`；上一窗口 400 条保留在 `20261025T105000Z` 归档。
- M1-2 观察（11:53Z）：SQLite 精确扫描累计仍为 23 条 `quote.live_onchain_evidence.readyForTransactionPreflight=true` 复核记录（11:53Z 时点快照；2026-10-26 12:30 +08:00 精确复核累计 31 条、最新 `19:42:41Z`），11:20Z-11:50Z 新窗口新增 0 条；`--strict-live --no-write` 11:53Z 仍 `readyForLiveTest=false`（`chain_test_plan_stale_against_current_opportunity_snapshot`，opportunity snapshot 11:52:31Z、`currentLiveProfitableRouteCount=1`），本轮未触发 M1-2 重建。
- M1-2 观察（10:51Z）：SQLite 精确扫描累计 23 条 `quote.live_onchain_evidence.readyForTransactionPreflight=true` 复核记录，其中新窗口 09:50:28Z-09:58:42Z 新增 8 条（`ETH -> TSLA -> AMZN -> ETH`、`TSLA -> AMZN -> ETH -> TSLA`、`TSLA -> ETH -> AMZN -> TSLA`、`AMZN -> TSLA -> ETH -> AMZN`）；23 条全部顶层 `chain_submission_candidate=0` / `can_submit_order=0` / `checks_passed=0`，auto_execute 记录为 `native_broadcast_disabled`；`--strict-live --no-write` 10:51Z 仍 `readyForLiveTest=false`（`chain_test_plan_stale_against_current_opportunity_snapshot`，opportunity snapshot 10:51:08Z、`currentLiveProfitableRouteCount=1`），本轮未触发 M1-2 重建。
- M6-6 采样恢复并达标：发现 PID 19752 监听 5000 后，对快照接口完成 3 cold + 3 warm 只读采样，全部 200，`0.937-1.938s`，warm <10s 目标达成；`snapshot_perf_latest.json` 已更新（10:57:05Z），未写 SQLite、未重启生产。
- 回归验证（10:57Z）：`test_web_app_assembly_dex_records.py`、`test_web_app_assembly_workflows.py`、`test_web_app_assembly.py`、`test_api_scope_policy.py` 四文件 `84 passed`，R1-R4 与 API 隔离无回退。
- 线上进程核对（10:59Z）：PID 19752 启动于 17:48 +08:00，早于 R1-R4 路由/模板文件最后修改（18:05-18:19 +08:00）；在线请求 `/api/dex/submissions/review-success|review-failed|execution-success|execution-failed` 均返回 404，属旧进程未加载新路由；未擅自重启生产，待用户/运维确认受控窗口后生效。
- M1-1 最新窗口（11:50Z）已重跑并落盘：P&L `reviewedCount=180`、0 unique success、0 failed、180 blocked（`no_profitable_route=66`、`quoter_unavailable=114`、`rpc_transport_error=0`）、`withExpectedProfitCount=0`、`readyCount=0`；auto-scan `attemptsInWindow=180` 全 blocked、`broadcastCount=0`；expected-vs-actual 0 unique success；zero_liquidity 11:53:07Z 无显式零流动性池；validate 11:53:35Z `attempt_count=5000`、`quote_success_count=5000`、`settled_count=0`；idle maintenance 11:52:46Z `ok=true`、`phase=directions_satisfied`；evidence 已写盘为 `closed_loop_pnl/20261025T115000Z/report.json` 与 `auto_scan_verification/20261025T115000Z/report.json`；上一窗口 192 条保留在 `20261025T112000Z` 归档。
- 生产 SQLite 分表复核（首次整理时点）：`cow_execution_attempts=0`、`cow_execution_reviews=11107+`（最近一条 `2026-10-25T12:00:18Z`）；2026-10-26 12:30 +08:00 精确复核为 `cow_execution_reviews=12400`、最新 `2026-10-25T20:04:18Z`，证明 M6-5 分表在生产持续写入，`not_executable` 复核类记录仍在独立表累积。

## 新增需求：四个独立成交/复核记录接口

用户明确要求以下四个记录做成四个独立页面/接口，不复用 DEX 总览或报价与路由页面信息，不要求总览展示，统一纳入待完善工作列表计划开发：

| 编号 | 页面/接口 | 展示要求 |
|---|---|---|
| R1 | 复核成功记录（独立页/API） | 展示有利润、无利润数值估算 |
| R2 | 复核失败记录（独立页/API） | 展示失败原因 |
| R3 | 链上提交执行成功记录（独立页/API） | 展示实际盈利大小等信息 |
| R4 | 链上提交执行失败记录（独立页/API） | 展示失败原因 |

> 以上 R1-R4 已收口，完整明细见任务清单“已收口摘要”。

计划动作：
- 独立页面路由：`/dex/submissions/review-success`、`/dex/submissions/review-failed`、`/dex/submissions/execution-success`、`/dex/submissions/execution-failed`；原 `/dex/submissions` 总览仅作次要入口，不作为四页数据源。
- 独立 API：`/api/dex/submissions/review-success`、`/api/dex/submissions/review-failed`、`/api/dex/submissions/execution-success`、`/api/dex/submissions/execution-failed`，仅读对应 record_kind/category；不复用 `/api/dex/opportunity-scanner/snapshot` 与报价/路由页面数据。
- 页面/API 只读 SQLite；不新增 wire schema、不手工改 `latest.json`、不伪造 attempt。
- 验收：四页均可独立访问并展示各自要求字段（利润估算/失败原因/实际盈利/失败原因），与 DEX 总览、报价路由页无数据耦合；functional 测试覆盖四页与四接口。

## 新增需求：M7 链上复核与页面性能收口

用户补充以下开发项，统一纳入后续待完善任务清单与开发计划：

| 编号 | 任务 | 验收 |
|---|---|---|
| M7-1 | `/flash-loan/pools` 46630 慢加载优化：testnet flash pool view 去重懒加载 SQLite，并加进程内 TTL 缓存，刷新链路显式清缓存 | warm 请求显著提速（目标 <5s），刷新后缓存显式失效 |
| M7-2 | 链上复核无论盈亏正负都必须展示具体盈亏数值：新增直接查 SQLite 的快速参考 P&L，`route_unprofitable` 也写入正负 `netProfitAmount`/`finalOutputAmount`/`netProfitBps` | 复核记录页与路线卡不再显示 `盈亏=-`；无法计算时保持 null + missingReason |
| M7-3 | 手动链上复核统一投递 Rust 常驻线程，仅空闲态闭环允许，忙碌态拒绝；UI 展示闭环线程复核中/空闲状态与下一轮自动复核倒计时 | 点“链上复核”只投喂 Rust 线程；忙碌组返回 `closed_loop_group_busy`；worker 卡片显示状态与剩余秒数 |
| M7-4 | 复核一致性必须展示具体数值：把 Rust 合约 `eth_call`/preview 返回的原生 `selectedProfitRaw/selectedProfitAmount` 持久化到 SQLite，并在记录页与报价/路由页展示“链上复核数值 vs 参考/执行估算数值”及差值 | 复核记录显示两组具体数值与 `delta`；`NoProfitableRoute()`/preview 失败无原生数值时明确标注来源与 revert 原因，不把 SQLite 参考误标为 eth_call 结果 |
| M7-5 | 手动链上执行入口不因复核利润或参考建议灰化；“链上执行”始终允许点击，点击后先实时 `eth_call`/preview 复核并确定最佳交易量，再提交链上交易 | 不盈利/静态参考/最佳交易量未确认时仍可点击“链上执行”；线程 running/queued 时仅灰化“实时链上复核”按钮 |

> 以上 M7-1/M7-2/M7-3 均已收口，M7-4/M7-5 仍在待推进表；完整明细见任务清单“已收口摘要”。

## 全量任务矩阵

| 编号 | 任务 | 当前状态 / 卡点 | 推进类型 | 负责/配合 | 完成验收 |
|---|---|---|---|---|---|
| M1-1 | 持续刷新 P&L / auto-scan / expected-vs-actual / zero_liquidity evidence，并观察 `rpc_transport_error` 与 RPC failover 变化 | 已刷新至 `2026-10-25 20:04:18Z-2026-10-26 11:23:35Z`：`rowCount=1215`、0 success、1 failed、17 ready、1190 blocked、`reviewConsistentCount=24`、`reviewInconsistentPositiveEstimateCount=305`；auto-scan `attemptsInWindow=1215`、`broadcastCount=1`、0 unique success；expected-vs-actual 0 success；zero_liquidity 11:34:22Z 无显式池；validate 11:34:50Z 与既有基线一致；在线 SQLite `cow_execution_reviews=13614`、`cow_execution_attempts=1` | 持续 | 工程 | 新数据后四类 evidence 落盘且可复跑 |
| M1-2 | 出现新 `readyForTransactionPreflight=true` 后重建 chain-test 当前包 | strict-live 仍 false（12:57Z 复检，`chain_test_plan_stale_against_current_opportunity_snapshot`，`currentOpportunitySnapshotUpdatedAt=20:03:50Z`、`currentLiveProfitableRouteCount=1`）；2026-10-26 12:30 +08:00 精确复核累计 31 条 `readyForTransactionPreflight=true`（06:38Z 起 4 条 + 09:14Z-09:43Z 11 条 + 09:50Z-09:58Z 8 条 + 14:46Z-19:42Z 8 条），全部顶层 `chain_submission_candidate=0`、`can_submit_order=0`、`checks_passed=0`，auto_execute=`native_broadcast_disabled`，不满足可执行当前包条件；未触发重建 | 条件触发 | 工程 | `--strict-live --no-write` 不再报 stale |
| M2-1 | 观察注入流动性后的正向利润窗口 | 历史已出现 4 条正向 success；继续观察新窗口 | 条件触发 | 工程 | 新 signed 正向窗口落盘 P&L evidence |
| M2-2 | 测试网至少 1 条可复核 unique 成功广播 | 历史 14 条自动扫描成交仅存 evidence（含非 TZ 8 条）；在线 SQLite 当前 0 success | 条件触发 | 工程 | `broadcastCount>0` 且 `uniqueSuccessCount>=1` |
| M2-3 | 新 unique success 后运行 expected-vs-actual 复核 | 首轮 4/4 decodable、`zeroDifferenceCount=4`；后续无新 success | 条件触发 | 工程 | 复核报告落盘且与链上回执一致 |
| M2-4 | `nonZeroDifferenceCount>0` 时逐跳分析 slippage/fee/gas | 首轮差异为 0；后续出现差异再执行 | 条件触发 | 工程 | 逐跳分析 evidence 含金额、slippage、fee、gas |
| M3-1 | RPC 稳定后重跑 `refresh_robinhood_v4_live.py --candidate-backfill` | RPC 稳定；idle maintenance `neededDirectionCount=0`；需用户/运维确认维护窗口 | 外部窗口 | 工程/运维 | T21 出现 signed 数值 |
| M4-1 | 清理 `latest_executor_gate_not_ready` / `current_executor_mismatch_not_ready` | readiness 仍列三项 blocker | 外部证据 | 工程/业务 | 两项 blocker 清除且 canary 前三项广播均 `false` |
| M4-2 | 主网 `4663` 静态调用/readback，P0-14 业务侧登记 | 主网只读 gate 已刷新；业务登记待审批 | 外部审批 | 工程/业务 | 只读证据落盘、P0-14 状态登记 |
| M4-3 | P2-04 值班/告警、失败回滚演练、USD 盈利观察 | 待负责人登记 | 外部运营 | 运营 | SLO/告警配置与演练记录完成 |
| M5-1 | 参数/搜索模式调整前必须有链上 evidence | 未触发调整 | 约束 | 工程 | 任何参数调整均附 evidence 链 |
| M5-2 | readiness / 只读路径保持 fail-closed | live strict false、predeploy 保持 blocker、主网 fail-closed | 每日/按需 | 工程 | readiness 仅 CLI 写入，无证据不放开 |
| M6-2 | 排查并修正链上广播一直无法执行的问题 | 广播 gate 已全通过；最新窗口 `2026-10-25 20:04:18Z-2026-10-26 11:23:35Z` 1215 条 reviewed、0 success、1 failed（`AMZN -> ETH -> AMD -> AMZN`、无 `tx_hash`）、1214 blocked、`rpc_transport_error=0`；累计 `readyForTransactionPreflight=true` 复核行未转 `execution_success`、无新 tx_hash | 持续 | 工程 | 出现可解释 `execution_success` 或明确 `readyForTransactionPreflight=true` 落盘 |
| M7-4 | 复核一致性必须展示具体数值：Rust `eth_call` 原生 `selectedProfitRaw/selectedProfitAmount` 落 SQLite 并在记录页/报价路由页展示“链上复核数值 vs 参考/执行估算数值”及差值 | 代码侧已增加字段到 `_native_worker_evidence` 与 `costModel`/evidence 顶层；SQLite 落库、UI 对比展示、单测与在线验收未完成 | 可自主推进 | 工程 | 复核记录显示两组具体数值及 `delta`；无原生 signed 值时明确标注来源，不把 SQLite 参考误标为 eth_call 结果 |
| M7-5 | 手动链上执行入口不因复核利润或参考建议灰化；“链上执行”始终允许点击，点击后先实时 `eth_call`/preview 复核并确定最佳交易量，再提交链上交易 | 已修正路由卡按钮逻辑并确认 Rust worker 复核走 `previewBestPoolDetailed` eth_call；补回归测试与在线验收 | 可自主推进 | 工程/UI | 不盈利/静态参考/最佳交易量未确认时仍可点击“链上执行”；线程非空闲仅灰化“实时链上复核”；复核数值来自 eth_call 原生结果 |

> 已收口项（R1-R4、M6-6、M6-7、M7-1/M7-2/M7-3、单实例进程约束、cow 在线链路 SQLite-only、归档清理）已从本矩阵移除，完整明细见任务清单“已收口摘要”。

## Phase 排期

### Phase A 持续观察与可自主推进（立即开始）

- M1-1：新窗口触发后按“验收命令”重跑四类 evidence 与 validate；旧增量窗口保留在带时间戳的归档报告中，`latest.json` 只由脚本写入。
- M1-2：扫描 SQLite 是否出现新的 `readyForTransactionPreflight=true`；一旦出现，按 Step 1 顺序执行 build、audit、strict-live；禁止用旧 `live_verified` 记录或手工 attempt JSON 冒充当前包。2026-10-26 12:30 +08:00 精确复核累计 31 条 preflight=true 复核行（全部顶层 `chain_submission_candidate=0` / `can_submit_order=0` / `checks_passed=0`），未触发重建；继续等待可执行 live-ready attempt。触发重建时 build 需按 ready 行实际 pair 传参（如 `--pair "ETH -> TSLA -> AMZN -> ETH"`），避免默认 pair 选不到行。
- M5-1/M5-2：保持参数调整需链上 evidence、readiness 只能由 CLI 写入；复检一律使用 `--no-write`。
- M6-2：持续观察广播 gate、`readyForTransactionPreflight` 与 `execution_success` 落库；无利路径保持 fail-closed，不强行广播。
- M7-4：完成 `selectedProfitRaw/selectedProfitAmount` 的 SQLite 落库、记录页/报价路由页“链上复核数值 vs 参考/执行估算数值”及差值展示，并补单测与在线验收。
- M7-5：完成手动链上执行入口不被复核结果灰化的 UI 修正、实时 `eth_call` 复核口径回归与在线验收。
- M6-6（已收口，仅复测）：快照诊断与快速路径已落地，最新 `snapshot_perf_latest.json`（20:25Z）6 次全部 200、`0.663-1.691s`，warm <10s 达标；只在受控重启或新窗口后复测，不主动启动生产。
- R1-R4（已收口，按需回归）：四页四接口与旧路由停用已完成并在线验收；生产常驻由用户/运维受控窗口重启后继续按新证据回归。

### Phase B 链上成交验证（等待新 unique success）

- 按 M2-1 -> M2-2 -> M2-3 -> M2-4 顺序执行；新 success 出现后先确认 `broadcast_history` 自动落库为 `execution_success`/`tx_hash`，再重跑 P&L 与 auto-scan。
- 无新 success 时保持未勾选，只记录最近一次核对时间。

### Phase C 回填性能（等待维护窗口）

- 用户/运维确认维护窗口后按序执行：停止周期扫描/auto-execute -> 备份 `flashloan/src/runtime/local_store.sqlite3` -> 小 batch `--candidate-backfill --pool-ids <样本> --batch-size <小值>` 验证 TLS 与零流动性清理 -> 窗口内扩大批次 -> 恢复扫描 -> 重跑 M1-1 四类 evidence。
- 最终验收为 T21 出现 signed 数值；未确认窗口前不启动全量 backfill。

### Phase D 部署运营（等待外部审批/登记）

- M4-1：canary/executor evidence 齐备后重跑 predeploy readiness，确认两项 blocker 清除且 canary 广播 `false`。
- M4-2：P0-14 业务审批后补主网 static-call/readback evidence，readback 保持 fail-closed。
- M4-3：P2-04 负责人登记后完成 SLO/告警配置、失败回滚演练与 USD 盈利观察。

## 执行顺序与验收命令

### Step 0 可自主推进（立即/持续）

```powershell
python scripts/analyze_robinhood_testnet_closed_loop_pnl.py
python scripts/verify_robinhood_testnet_auto_scan_window.py --window-start <UTC> --window-end <UTC>
python scripts/analyze_robinhood_v4_expected_vs_actual.py --window-start <UTC> --window-end <UTC>
python scripts/verify_robinhood_v4_zero_liquidity_vs_candidates.py
python flashloan/src/market/tools/validate_live_assumptions.py --cow flashloan/src/runtime/local_store.sqlite3 --output flashloan/src/runtime/evidence/robinhood-testnet/validation_assumptions_sqlite_latest.json
```

M6-6 采样方式：对 `GET /api/dex/opportunity-scanner/snapshot?chain_id=46630&market_id=dex-arbitrage-46630-uniswap-v4&protocols=uniswap_v4&dex_strategy=closed_loop_routes&summary=1` 至少做 3 次 cold + 3 次 warm 采样，记录 `status`、`elapsedSeconds`、`bodyBytes`，并同时记录 worker 状态序列化与 SQLite 查询耗时线索。

### Step 1 条件触发（M1-2 / M3-1）

```powershell
python flashloan/src/dex/tools/build_robinhood_chain_test_package.py --network robinhood-testnet
python flashloan/src/dex/tools/audit_robinhood_chain_test_package.py --network robinhood-testnet
python flashloan/src/dex/tools/check_robinhood_live_testing_readiness.py --strict-live --no-write
python flashloan/src/dex/tools/refresh_robinhood_v4_live.py --network robinhood-testnet --chain-id 46630 --candidate-backfill
```

### Step 2 链上成交状态机（M2）

```powershell
python scripts/analyze_robinhood_v4_expected_vs_actual.py --window-start <UTC> --window-end <UTC>
```

### Step 3 外部审批/证据齐备后（M4）

```powershell
python flashloan/src/dex/tools/check_robinhood_predeploy_readiness.py --strict-fork --no-write
python flashloan/src/dex/tools/collect_robinhood_mainnet_readonly_gate.py
```

## 架构边界说明（2026-10-25）

设计文档描述“数据库操作和网络读取全部由 Rust 完成、Python 只负责 UI”与当前代码并不完全一致，准确边界如下：

- Rust 负责链上网络/RPC 读取、合约 preview/ladder/broadcast、只读 SQLite 页面读路径（`page -> rs_bridge -> flashloan_rs -> SQLite`），以及 Rust-first 执行原语。
- Python 仍负责 Web/UI 编排、任务配置组装、SQLite 写路径与 schema/迁移（`cow_execution_attempts`/`cow_execution_reviews` 的 DDL、写入、清理、读取统计）、evidence 脚本；Rust 不可用时仅 Python 只读兜底，不回退到 JSON 分片。
- `cow_*` 命名来自历史 CoW Protocol/CoW Swap 集成路径，`storage_cow_execution` 等模块随后被 Robinhood V4 闭环执行复用，`cow` 不是“牛”也不是“closed-loop work”；`load_auto_execute_log_stats` 只是该历史存储模块里的快速统计入口，属历史命名遗留。
- 中文路径读取失败不是架构缺陷，而是 PowerShell 管道向 `python -` 传“内嵌中文路径的源码”时，管道内容按本地代码页转码，`E:\2026OPC大赛\...` 被转成 `E:\2026OPC??\...`，导致 `sqlite3` 报 `unable to open database file`；同一路径作为命令行参数传入 Python、用 `sqlite3.exe` 或直接运行正式 `.py` 脚本均读取正常。

## 安全边界

- 测试网广播开关保持 `true`，主网、paused-deploy、executor canary 广播保持 `false`。
- readiness 只由 CLI 写入，禁止手工改 `latest.json`；无 signed 数值时保持 `null` + `missingReason`，不写零占位。
- SQLite 是提交记录唯一在线存储；成交提交类写入 `cow_execution_attempts`，复核类写入 `cow_execution_reviews`；旧 JSONL 只在迁移时读取。
- RPC failover 只用于只读复核；广播/提交不自动跨 RPC 重发。
- `readyForTransactionPreflight=true` 由链上报价复核自动计算，不手工设置。
- M7-4 未完成前，`selectedProfitRaw/selectedProfitAmount` 仅为 Rust 原生合约 preview 返回值；`NoProfitableRoute()` / `previewBestPoolDetailed unavailable` 没有 eth_call 净利数值，展示时必须与 SQLite 参考 P&L 分开标注，不把参考值当作链上复核结果。
- M6-6 只做只读请求与耗时采样，不重启生产、不 kill worker、不清理/压缩链路记录；压测或抓 profile 前先确认维护窗口。

## 更新机制

- 每次 evidence 有实质变化（新成功 `txHash`、T21 signed、cleanup 触发、RPC failover 切换、参数调整）后，同步更新源任务清单、开发计划与本计划。
- M7-4 完成时，同步更新源任务清单“已收口摘要”、开发计划排期表与本计划 Phase A。
- 已完成项从待推进矩阵移除并追加至源文档“已收口摘要”；本计划只保留未完成或持续推进任务。
- 若链上长期无新窗口，链上成交与回填任务保持未勾选，只更新最近核对时间。

## 假设与默认

- 本计划为全量路线图，文件名为 `工作计划_20261025.md`，使用中文与触发式排期。
- 计划文档基线不修改 evidence 或 `latest.json`；后续实施推进记录于“本轮已实施”；SQLite 物理分表已在生产生效，不新增任务编号。
- 四个独立记录接口（R1-R4）作为新增待推进开发项纳入 Phase A 排期；编号以源文档后续整理为准，若源文档未新增编号则沿用 R1-R4 临时工作编号。
- M7-4 记录在源任务清单与后续完善开发计划中；收口后从待推进表移除并转入“已收口摘要”。
- 若后续执行 Phase A，则按 Step 0 验收命令重跑证据并确认 `rpc_transport_error=0` 等安全指标未回退。


