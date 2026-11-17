# plan 运行证据目录

## 目的

`plan/` 根目录不再直接堆放运行时观察文件。新生成的运行证据必须先写入本目录，
再在必要处更新 README 索引。

## 目录分类

| 目录 | 内容 |
|---|---|
| `runtime_watch/mainnet/` | Mainnet 30 分钟或长期观察 JSONL |
| `runtime_watch/testnet/` | Testnet 观察 JSONL |
| `reports/` | PnL、expected vs actual 等报告产物 |
| `logs/` | 观察驱动的 stdout/stderr 日志 |
| `diagnostics/` | 探测、比对和非链路运行诊断 |
| `history/` | 历史大量 JSONL 审计，例如 `cow_execution_4663_*.jsonl` |

## D0/M0 证据

- `reports/d0_m0_workspace_20261103_0810.md`
- `reports/d0_m0_contracts_20261103_0810.json`
- `reports/d0_m0_migration_20261103_0810.json`

## M1 只读基线

- `reports/d1_m1_baseline_20261103.json`

## M1 Token Manager

- `reports/d1_1_2_token_store_20261103.json`
- `reports/d1_1_3_metadata_normalization_20261103.json`
- `reports/d1_1_4_library_classification_20261103.json`
- `reports/d1_1_4_no_usd_depth_gate_20261103.json`
- `reports/d1_1_6_library_admission_conflict_audit_20261103.json`
- `reports/d1_1_7_bootstrap_dry_run_apply_20261103.json`
- `reports/d1_1_7_bootstrap_simulate_raw.txt`
- `reports/d1_2_1_token_read_api_20261103.json`
- `reports/d1_2_2_token_admission_write_api_20261103.json`
- `reports/d1_2_3_token_audit_api_20261103.json`
- `reports/d1_2_4_token_authority_switch_rollback_20261103.json`

## M2 Pool Manager

- `reports/d2_1_core4_scope_materialization_20261103.json`
- `reports/d2_2_pool_key_id_golden_20261103.json`
- `reports/d2_3_pool_scope_write_contract_20261104.json`

## M9 UI 兼容层

- `reports/d9_1_ui_manager_compat_20261104.json`
- `reports/d9_2_ui_closed_loop_shadow_20261105.json`
- `reports/d9_3_ui_market_observation_shadow_20261105.json`
- `reports/d9_4_ui_submission_shadow_20261105.json`
- `reports/d9_5_ui_overview_shadow_20261105.json`
- `reports/d9_6_ui_submission_detail_shadow_20261105.json`
- `reports/d9_7_ui_submission_detail_manager_read_20261105.json`
- `reports/d9_8_ui_submission_clear_manager_only_20261105.json`
- `reports/d9_9_ui_review_lists_manager_only_20261105.json`
- `reports/d9_10_ui_submission_overview_manager_only_20261105.json`
- `reports/d9_11_ui_overview_manager_only_20261105.json`
- `reports/d9_11_ui_overview_manager_only_correction_20261105.json`
- `reports/d9_32_runtime_core_manual_review_command_20261106.json`

## 存档规则

- 文件保留原有文件名或增加时间戳，不覆盖同一窗口的旧证据。
- 证据是验收事实，不能为了测试通过或文档一致而修改。
- 运行工具默认输出路径由代码引用本目录，例如
  `plan/runtime_evidence/runtime_watch/mainnet/`。
- 只有明确无效且已审计的零字节文件才能删除；删除前记录文件名和原因。

## 2026-11-03 归位记录

以下 14 个散落在 `plan/` 根目录的文件已按分类移动到本目录：

- 12 个 `runtime_watch_*.jsonl` 和 `runtime_watch_probe_*.jsonl` 到 `runtime_watch/mainnet|testnet/`。
- `closed_loop_pnl_20261102T0700Z_0740Z.json` 和
  `expected_vs_actual_20261102T0700Z_0740Z.json` 到 `reports/`。
- `runtime_watch_driver_stdout.log` 到 `logs/`。
- `analyzer_selfcheck_testnet_expected_vs_actual/` 到 `diagnostics/`。
- `expected_vs_actual_refresh_20261102/` 到 `reports/`。
- `history/cow_execution_4663_20261031T190035Z.jsonl` 随 `history/` 整体移入
  `runtime_evidence/history/`。

后续不得再向 `plan/` 根目录写入同类型文件。

## 2026-11-07 报告索引

- 完整已完成任务与证据落点见 `../back/已完成任务归档_20261107.md`。
- `reports/robinhood_api_status_20261107T072620Z_blocked.json`：P0-1.0
  主网 Web 运行态阻塞探测。`5000` 无监听，`/healthz`、`/api/status` 和
  提交记录 API 不可达；严格模式退出码 `2`。`8080` 为 `sub2api` Docker/WSL
  监听，不计入主网服务。
- `reports/p0_1_runtime_availability_blocker_20261107T072620Z.md`：上述阻塞的
  事实、复现命令、证据校验和恢复条件。
- `reports/p0_1_runtime_scope_partial_20261107T091859Z.json`：P0-1 当前短窗口
  测量。`run.py` PID `22664` 为 `5000` owner，`/healthz`/`/api/status` 返回
  `200`，4 并发 p95 `80.479ms`；但 `control_status=null` 导致状态契约失败，
  提交记录 API 返回 `503 manager_read_failed`，且该进程尚未完成纯 V4 DEX
  scope 隔离。结论为 failed，不得当作 P0-1 通过证据。
- `reports/p0_1_non_dex_background_blocker_20261107T091859Z.md`：同窗口
  非 DEX 后台隔离记录。启动日志显示 CoW 实时报价开启，通用入口仍调用 CoW/
  清算初始化，BNB 采集器未被 `FLASHLOAN_BNB_SHARED_DATABASE=0` 自身阻断。
- `reports/robinhood_mainnet_runtime_scope_audit_20261107T024000Z.json`：最新
  主网 `4663` 历史范围审计。确认当时 scope 有 192 条有向闭环、96 条运行态，
  有向符号精确匹配 96、缺失 96、孤儿 0，无序 Y/Z 组覆盖 96/96；运行态
  `loop_key` 为符号组而非闭环地址键，物理主键匹配 0、缺失 192、孤儿 96。
  当时另有 1 条 Testnet 记录在 scope 外；当前库已无该行，不能据此覆盖历史事实。
- `reports/robinhood_mainnet_baseline_20261107.json`：较早的只读计划基线，
  保留不改写；其中 `runtime=97`、`closedMatched=112`、`missing=80` 等派生运行态
  覆盖字段已被上述范围审计替代。
- D9.23-D9.41 证据以 `reports/d9_23_*` 至 `reports/d9_41_*` 保留，其中
  `d9_24_review_broadcast_file_reassembly_20261106.json` 记录的是被拒绝的
  Python Manager 重组方案，不作为完成项。
- `reports/d9_41_status_cache_isolation_20261107.json` 记录 D9.41 状态接口
  测试缓存顺序修复，不修改原始 D9.41 证据。
- 当前计划只从 `../开发计划_20261108.md` 派生任务；历史证据不改写。

## 2026-11-08 增量报告索引

- `reports/p0_1_r1_incremental_20261108T032402Z.json`：P0-1-R1 正式增量
  复验。绑定 `run.py` PID `24036`；`allPassed=true`、退出码 `0`，4 并发
  p95 `1230.886ms`、错误率 `0`、30 分钟 RSS 增量 `-269.461 MiB`、线程增量
  `0`，健康和提交 API/dry-run 均通过。
- `reports/p0_1_r1_incremental_20261108T031936Z.json`：同任务失败探测。
  `--process-marker` 被当作字面 substring，未匹配到进程；该报告保留为失败
  证据，不得当作通过。
- `reports/closed_loop_runtime_sync_20261108T041955Z_robinhood-4663-uniswap_v4.json`：
  P0-2.1 canonical 审计当前基线。只读、显式 scope、退出码 `2`；
  `directedClosed=192`、`directedRuntime=96`、`exactMatched=96`、
  `exactMissing=96`、`physicalLoopKeyMatched=0`、`physicalLoopKeyMissing=192`、
  `physicalLoopKeyOrphan=96`、`outOfScopeRuntime=0`、`scopeLeak=0`。
  这是待修复 finding，不是通过报告。
- `reports/p0_2_3_directional_identity_regression_20261108T174638Z.json`：
  P0-2.2/P0-2.3 工程归档证据。身份、状态机、Consumer、Rust worker 和
  Manager overview 专项回归 `138 passed`，相关 Python 文件 `py_compile`
  退出码 `0`。该证据明确记录未执行生产数据库 apply、未重启服务，生产仍为
  `directedClosed=192`、`directedRuntime=96`、`physicalLoopKeyMatched=0`；
  不得作为 P0-2.4 至 P0-2.7 的生产验收。
- `reports/p0_2_5_residue_scope_regression_20261108T183621Z.json`：
  P0-2.5 残留保护和观察 scope 工程回归，`20 passed`。覆盖已知 Testnet
  残留默认保护、显式清理审批/快照门禁、维护 worker 保留保护记录和观察脚本
  强制 scope。该证据未重启服务、未采集生产窗口，且记录
  `migrationScriptExists=false`；只证明工程行为，不证明 P0-2.5 已完成。
  后续 P0-2.5 只在 copy DB/隔离进程验证维护和观察，生产重启后移到 P0-2.6
  受控 apply 之后，避免 `v4_closed_loop_worker` 启动时绕过迁移计划自动回填。
- 当前 scope 外 Testnet 行为 0 条；历史 2026-11-07 报告中的 1 条
  `aaa|bbb|ccc` 记录仍作为历史证据保留。代码核查确认例行维护曾自动删除该行，
  新保护逻辑与 `scripts/observe_dex_closed_loops.py` 强制 scope 的验证由
  `test_20261101_test_residue_prune.py`、`test_v4_maintenance_workers.py` 和
  `test_observe_dex_closed_loops.py` 覆盖。

## 2026-11-09 增量报告索引

- `reports/mainnet_4663_restore_audit_20261108T214733Z.json`：主网资料库恢复
  后的只读审计，绑定恢复快照 SHA256
  `aff2aa735262ec83b6102b8b1347140d9781b9f89ea0cbf9d5ab71aac0b987d9`；
  退出码 `2` 表示方向级运行态仍待迁移，不是通过报告。
- `reports/mainnet_4663_current_audit_20261108T222953Z.json`：主网当前只读
  审计，绑定当前文件 SHA256
  `6ad458357e1d46e287615b289baa189d70e74f702b1d6e889ac7e91d1deafc11`。
  WAL/checkpoint 导致文件 SHA 与恢复快照不同，但逻辑审计一致。
- `reports/oops1_cleanup_refusal_manifest.json` 与
  `reports/oops1_cleanup_refusal_20261109T000000Z.json`：OPS-1 清理工具
  修正证据。显式清单 dry-run 对正式链库 `local_store_4663.sqlite3`、
  `local_store_4663.sqlite3-wal` 和测试网 `local_store_46630.sqlite3` 均
  返回 refuse，退出码 `2`；没有执行删除。
- `reports/mainnet_4663_restored_p0_2_4_dryrun_20261108T220739Z.json`、
  `reports/mainnet_4663_restored_p0_2_4_dryrun_v2_20261108T222647Z.json`：
  P0-2.4 早期 dry-run 失败证据，存在 `SOURCE_REPORT_DATABASE_CHECKSUM_MISMATCH`，
  不得当作通过。
- `reports/mainnet_4663_restored_p0_2_4_dryrun_v3_20261108T223042Z.json`：
  P0-2.4 当前通过报告。绑定
  `reports/mainnet_4663_current_audit_20261108T222953Z.json`；
  `closedLoops=192`、`proposedDirectionMappings=192`、`proposedRuntimeStates=192`、
  `archivePlans=96`、`rekeyPlans=96`、`reverseInsertPlans=96`、
  `sourceExactDirectedMatches=96`、`unresolvedDirections=0`、
  `duplicateDirectedKey=0`、`unresolvedAddress=0`、`crossScopeConflict=0`；
  唯一 finding 为 `OUT_OF_SCOPE_ROWS_EXCLUDED`。
- `reports/mainnet_4663_p0_2_5_source_audit_20261108T224112Z.json`：
  P0-2.5 source snapshot 的只读审计，绑定快照 SHA256
  `3b9c84eb66df11a78a59e961d75cad2ddfbd041b229faf4173d25bde7bf56bf7`；
  退出码 `2` 是方向级运行态待迁移基线。
- `reports/mainnet_4663_restored_p0_2_4_dryrun_v4_20261108T224159Z.json`：
  绑定 P0-2.5 source snapshot 的最新 P0-2.4 dry-run，所有必需计数通过，
  唯一 finding 为 `OUT_OF_SCOPE_ROWS_EXCLUDED`。
- `reports/p0_2_5_copy_validation_20261108T154352Z.json`、
  `reports/p0_2_5_copy_validation_20261108T161532Z.json`：
  P0-2.5 早期失败证据，保留不覆盖。
- `reports/p0_2_5_copy_validation_20261108T162812Z.json` 是零字节空文件；
  按 `reports/` 规则属于无效产物，后续清理时记录文件名和原因。
- `reports/p0_2_5_copy_validation_20261108T163525Z.json`：P0-2.5 早期通过的
  copy DB 隔离验证，基于旧 P0-2.4 报告；结论 `passed`、`exitCode=0`。
  后续在最新 P0-2.4 dry-run 上重跑，旧报告保留为历史证据。
- `reports/p0_2_5_copy_validation_20261108T224336Z.json` 与
  `diagnostics/p0_2_5_copy_validation_20261108T224336Z/observe.jsonl`：
  P0-2.5 最新通过证据。运行态 `96 -> 192` 方向级 `offchain_pending`，
  旧 96 条符号组状态归档、Testnet 残留 checksum 不变、观察窗口完整且
  生产库未访问。
