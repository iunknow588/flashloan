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

## `T-复编.02` 复核完整性实测（2026-11-24）

> **只读证据**。采集器以 `mode=ro` 打开主网实库并 `PRAGMA query_only = ON`；
> 本任务**一次也没有写**复核库。

| 文件 | 内容 |
|---|---|
| `tools/collect_t401_review_integrity_20261124.py` | **采集器**：A1–A8 / B1–B7 / C1–C5 / D1–D4 共 **24** 项 |
| `tools/render_t401_readings_20261124.py` | **渲染器**：**只格式化**采集器输出，**不自己**判定任何一项 |
| `t401_review_integrity_baseline.json` | **发布前基线**（复核库停在闭环 **v14** / **164** 闭环时的**同口径**读数，用作前后对照） |
| `t401_review_integrity.json` | **发布后读数**（由采集器写入；发布前为空缺 ⇒ **不得**据此勾项） |
| `t401_publish_watch.jsonl` | 常驻 worker 数 / 复核库状态的**持续观测** |
| `logs/t401_wait_publish.log` | **发布等待器**的逐次读数（超时即如实记 `TIMEOUT`） |
| `logs/t401_m8_regression.log` | `M-8` 编排层回归（pytest **119 passed**） |
| `logs/t401_review_manager_cargo.log` | `M-6` 复核层回归（`cargo test --offline -p review-manager` **158 passed / 0 failed**） |
| `tools/probe_t401_*_20261124.py` | **只读探针**：目录/扫描器**方向组集合**差分、**物理闭合键**差分、观察集覆盖、RuntimeCore 状态、发布覆盖度 —— 全部**不写**任何表 |

### 第五层 / 第六层修复后的采集（2026-11-24 晚）

> **发布层丢证据**的两层根因（发布层第一次把金额整块丢在链路外；
> 第二次把「阶梯为什么没跑」的诊断留在 route 的**子对象**里）修完并部署后，
> 采集**必须在复核代次发布后的第一时间做** —— 否则读到的只是**放旧**的复核，
> 120s 的自动广播门禁会**必然**全部拒答，测不出它对新复核的真实判定。

| 文件 | 内容 |
|---|---|
| `tools/watch_and_collect_t401_20261124.py` | **发布即采集**：轮询 `manager_review_library_state`（**5s**），**一发现新代次就立刻**起采集器 ⇒ 读数落在「复核刚发布」的时刻，120s 门禁的判定才有意义 |
| `tools/watch_t401_review_version_20261124.py` | **纯监视**：只在 `review_version` / `generated_at` 变化时打一行（20s 轮询） |
| `tools/scan_backtick_balance_20261124.py` | **文档自检**：逐行数反引号配对，报出**配对错位**的行（渲染会坏） |
| `tools/probe_t401_gate_status_20261124.py` | **广播闸门只读探针**：① 先加载 app 自己的 `.env` 再调 `broadcast_gate_status()`；② 读运行中实时运行时 **178** 个常驻 worker 的 `broadcastGate` ⇒ **以运行态快照为准**（裸 python 进程读不到 app 的 `.env`，会误报「三道全关」） |
| `logs/t401_watch_collect.log` | **发布即采集**的逐次读数（含采集器退出码与耗时） |
| `logs/t401_gate_status.log` | 广播闸门的**直调读数** ＋ **运行态 worker 读数** |
| `logs/t401_review_version_watch.log` | 复核代次的**逐次**读数（变化才记） |
| `logs/t401_overview_state_now.log` | `overview` 的 `runtimeCore` 读数（`snapshotAvailable` / `generation` / `lastError`） |
| `t401_review_integrity_after_layer5.json` | **第五层修后**的读数快照（`review_version=30`，2026-11-24 19:51:50）—— **不被后续采集覆盖**，用于三层对照 |
| `t401_review_integrity_after_layer6.json` | **第六层修后**的读数快照（`review_version=34`，2026-11-24 21:05:34，**发布后 2 秒**取）—— 同上 |

> **两个时间点必须分清**：`B1` / `B2` 测的是**采集时刻**的复核年龄
> ⇒ 采集**必须**紧跟代次发布（`watch_and_collect` 就是为此写的）。
> `B7` 测的是**门禁自己的判定**（发布那一刻逐条判定）⇒ **与采集时刻无关**。

### 钉住快照与回归日志（2026-11-24 收尾）

> **为什么需要「钉住快照」**：主网**每 6～8 分钟**发布一代复核，同一批复核的条数**逐代浮动**
> ⇒ **滚动文件**里的读数会在几分钟内**对不上**文档里写的数字。
> ⇒ 规矩：**文档引用的每一个读数，都必须能指回某个钉住快照**；
> **滚动文件**与**钉住快照**的读数**不可混用**（同一项数值不同是**正常的**，必须**各自标注代次**）。

| 文件 | 性质 | 内容 |
|---|---|---|
| `t401_review_integrity.json` | **滚动**（会被覆盖） | 采集器在**发现新代次后 5 s 内**自动重采并**覆盖**它 ⇒ 只能用来「发现新代次」，**不可**作为引用出处 |
| `t401_review_integrity_v39.json` | **钉住**（不可变） | **本波次文档引用出处**：`review_version = 39`、闭环 **174**、采集于 `2026-11-24T21:41:06.023923+08:00`（**紧跟该代次发布的同一秒**）；**C3 / C4 全项测得** |
| `t401_review_integrity_v38.json` | **钉住**（不可变） | 上一代快照（`review_version = 38`）—— 该代采集时 `overview` API **超时** ⇒ `C3` 记为「未测量」（**如实登记，不补写**） |
| `t401_review_integrity_after_layer5.json` | **钉住**（不可变） | **第五层修后**（`review_version = 30`）—— 用于 **§2.10** 的三测点对照 |
| `t401_review_integrity_after_layer6.json` | **钉住**（不可变） | **第六层修后**（`review_version = 34`）—— 同上 |
| `t401_review_integrity_baseline.json` | **钉住**（不可变） | **发布前基线**（复核库停在闭环 **v14** / **164** 闭环时的同口径读数） |

| 工具 | 用途 |
|---|---|
| `tools/render_t401_layer_compare_20261124.py` | 生成 `D-104` **§2.10 的三测点对照表**（发布前 / 第五层 / 第六层） |
| `tools/append_d104_span_row_20261124.py` | 往 `D-104` 的「边界与诚实登记」表追加**代次跨度逼近 600s** 的预警行 |
| `tools/normalize_md_crlf_20261124.py` | 受控 md 的 **CRLF / 无 BOM** 归一 |
| `tools/check_broadcast_env_guard_20261124.py` | **广播闸门守卫的负向校验**：把 env **复制到临时目录**再跑守卫（**从不碰真实 env**）⇒ **6 个组合**逐一断言判定（干净＝ACCEPTED、operator 主网 arb＝ACCEPTED、其余 4 组＝REJECTED） |
| `tools/detect_stray_placeholder_20261124.py` | **文档自检**：找出**上一轮 patch 残留的章节号占位符**（应为反引号、却写成 § 字符）—— 只读，不写文件 |
| `tools/gen_placeholder_fix_patch_20261124.py` | 按规则把占位符还原成反引号并**生成 apply_patch 载荷**（**只打印补丁，不改文件**）；规则：**章节号前缀**（A2 / D-048 / 4.2.2）**不动**，成对的代码段定界符**改回反引号** |

| 回归日志（2026-11-24 收尾） | 读数 |
|---|---|
| `rs_release_test_20261124.log` | `cargo test --offline --release`（`flashloan/rs-src`）⇒ **220 passed / 0 failed** |
| `rs_src_workspace_test_20261124.log` | `cargo test --offline`（`flashloan/rs-src` debug）⇒ **220 passed / 0 failed**（**与 release 同读数**） |
| `tools_review_composite_test_20261124.log` | `cargo test --offline -p review-manager -p composite-manager` ⇒ **158 ＋ 26 passed / 0 failed** |
| `tools_workspace_build_20261124.log` | `cargo build --offline --workspace`（`flashloan/tools`）⇒ **exit 0** |
| `tools_workspace_test_20261124.log` | `cargo test --offline --workspace`（`flashloan/tools`）⇒ **650 passed / 0 failed**（逐 crate 求和） |
| `py_t401_7files_20261124.log` | `M-8` 编排层 **7 个 Python 文件** ⇒ **209 passed** |
| `py_arch_guards_20261124.log` | 架构守卫 ⇒ **80 passed** |
| `py_full_regression_20261124.log` | Python **全量第 1 次**（`pytest flashloan scripts -q`）⇒ **1 failed / 3250 passed / 3 skipped**（1783.32s）—— 唯一红例＝**架构守卫与已入库广播模板互相矛盾**，已收窄守卫修掉（见 [`D-104`](../tasks/P-01-池子重构/tasks/T-复编.02/docs/D-104-复核完整性实测.md) §2.9.1）⇒ **不得再作基线引用** |
| `py_full_regression_20261124_run2.log` | Python **全量第 2 次**（守卫收窄后重跑）⇒ **3251 passed / 3 skipped / 0 failed**（1479.66s）⇒ **全绿**；**本波基线以它为准** |
| `py_full_regression_20261125_run3.log` | Python **全量第 3 次**（**修复「测试会杀掉运行时」之后**重跑）⇒ **3251 passed / 3 skipped / 0 failed**（1395.02s）⇒ **全绿**（用例数与第 2 次一致，**未增减**）；**监督日志全程零增长** ⇒ 运行时**全程未被杀** ⇒ **本波基线以它为准** |

### 运行时重启的根因定位（2026-11-25）

> **为什么要有这一节**：本波实测期间，运行时被监督进程重启 **3** 次；
> 早前登记为「其它修复进程抢端口」——**该归因被本节的实测推翻并更正**。

| 文件 | 性质 | 内容 |
|---|---|---|
| `logs/t401_supervisor_r6.log` | **钉住**（追加） | 监督进程的重启史：`22:23:11`（PID 29384，`exit_code=1`）→ `22:23:26` 起 PID **22404**；`23:00:06`（PID 22404，`exit_code=1`）→ `23:00:21` 起 PID **29832**；`23:55:57`（PID 29832，`exit_code=1`，**受控复现**）→ `23:56:12` 起 PID **27084** |
| `logs/t401_runtime_sup/runtime_stderr.log` | **钉住**（追加） | 被监督实例的 stderr：两个退出点**都没有 traceback、没有自诊断信息**（HTTP 访问日志直接中断）⇒ 与「被 `taskkill` 强杀」一致 |

| 工具 | 用途 |
|---|---|
| `tools/probe_runtime_instances_20261124.py` | **只读**探针：按 `instance_id` 分组链库里的运行事件（`runtime-core-<pid>-<chain>-<n>`）⇒ 用于判断「有没有第三方实例」。**结论**：运行事件**只在内存、未持久化**，链库里查不到实例级事件 ⇒ 归属**不可**由数据库证明 |
| `tools/check_markdown_links_20261125.py` | **只读**链接检查：把 `plan/` 非归档区每份 md 的相对链接逐条解析到磁盘。**本波读数**：**308** 条相对链接、**3** 条断链——**全部**在 `T-候编储.01/D-030-checklist.md`（指向 `../../T-候.01/docs/…`，应为 `../T-候.01/docs/…`），**属另一修复线程的在途文件，本轮未触碰**；**本轮改动自身 0 断链** |

**根因（已受控复现）**：`flashloan/app_runtime/tests/unit/test_run_port.py` 的
`test_resolve_port_auto_advances_when_default_is_occupied` **只 mock 了 `_port_in_use`**，
**未 mock** `_try_close_port_owner` 内部的 `netstat` 与 `taskkill` ⇒ 执行该测试会
**真的 `taskkill /F` 掉 5000 端口的持有者**（即被监督的运行时），被杀进程**静默退出码 1**。
两次现场重启都落在**本线程的全量回归窗口内**（第 1 次 **21:57:02–22:26:45**、第 2 次
**22:38:11–23:02:50**），且**两次签名一致**（`exit_code=1`、无 traceback）。

| 动作 | 观测 |
|---|---|
| 修复前：单独执行该测试文件 | **3 passed / 4.64s**，运行时 PID **29832 被杀**（监督日志记 `23:55:57` / `exit_code=1`） |
| 修复后：同一命令 | **3 passed / 7.39s**，运行时 PID **27084 未变**、监督日志**零增长** |
| 修复后：**全量**回归 | **3251 passed / 3 skipped / 0 failed**（1395.02s），运行时 PID **27084 未变**、监督日志**零增长** ⇒ **规模级验证通过** |

**修复**：`test_run_port.py` 新增 autouse fixture，同时 mock `_port_process_ids` 与
`subprocess.run` ⇒ **保留**「端口被占用 → 顺延」的断言覆盖、**去掉**真实 kill 副作用。

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

## 2026-11-24 增量报告索引

- `diagnostics/hop_direction_consistency_20261124.json`：主网只读探针
  `tools/probe_hop_direction_consistency_20261124.py` 的输出，绑定
  `flashloan/db/local_store_4663.sqlite3`（`ro` 打开，**零写入**）。
  结论 `passed = true`：`hops = 522`、`loops = 174`，方向不变量
  `i7 / i12 / i14 / i16` **全部 0 违规** ⇒ 复核层方向交叉校验的
  `direction_mismatch` **== 0**。`temporary_override_hops = 6` 单列，
  **不计入违规** —— 那 **6** 跳是**备用池接管**（`pool_id` = 生效池、
  `base_pool_id` = 原推荐池，见 `D-001` §7 `T-方闭.01` 迁移记录），
  探针本轮补上了该分支（原口径会误报 **6** 条）。

## 2026-11-25 增量报告索引（`T-储.01` 测试套件生产库零写入）

> **目的**：把「测试套件把生产库的闸门键改回默认值」这个缺陷
> **用实测读数钉死**，并给出**可复算**的守卫证据。
> **落点**：`flashloan/app_runtime/tests/**`（**产品代码零改动**）。

### 探针与工具

| 文件 | 作用 |
|---|---|
| `tools/probe_live_db_writes_20261125.py` | 只读探针：以 `mode=ro` 打开活链库，窗口前后**逐字节**比对生产闸门键（`value_json` ＋ `updated_at`）、`kv_meta` 与两张业务表的行数 |
| `tools/probe_gate_keys_snapshot_20261125.py` | 同一口径的**独立**快照／比对工具（用于交叉核对） |
| `tools/probe_store_path_binding_20261125.py` | 列出**仍持有未改道路径解析函数**的模块（漏网绑定点自检） |
| `tools/probe_readonly_uri_modes_20261125.py` | 实测 `mode=ro` 打开活链库成功、写入被 SQLite 层拒绝 |
| `tools/probe_sandbox_schema_cost_20261125.py` | 实测会话沙箱的建库成本 |
| `tools/probe_rust_bridge_live_cost_20261125.py` | 实测 **Rust 桥的默认落点就是主链库**、且 Rust 侧迁移**真的读写生产** |
| `tools/probe_full_store_snapshot_20261125.py` | **全库快照**探针：以 `mode=ro` 遍历活链库的**每一张表**，逐表记行数 ＋ 内容 §sha256§，另加 `kv_entries` / `kv_meta` 的**整表摘要**与 5 个**测试残留**定向探针 ⇒ 把判据从「19 个闸门键」扩到**整库字节级** |
| `tools/probe_full_store_window_20261125.py` | 把**全库快照**包在一个命令的**前后**，产出「窗口前／窗口后／差异」三合一报告（用于把**全量回归**和**同时长静默对照**各跑一遍整库比对） |
| `tools/cmd_full_regression_20261125.txt` | 探针 `--command-file` 用的全量回归命令 |
| `tools/cmd_silent_control_20261125.txt` | 探针 `--command-file` 用的**静默对照**命令（同时长、无 pytest） |
| `tools/cmd_silent_control_v4_20261125.txt` | 第二轮**静默对照**命令（同时长 2100 s、无 pytest） |
| `tools/cmd_scoped_retest_20261125.txt` | **范围复测**命令（28 用例）—— 缺陷复现时它改写了 **4** 个闸门键，守卫装好后改写 **0** 个 |

### 证据文件

| 文件 | 内容 |
|---|---|
| `probe_live_db_writes_20261125_control.json` | **缺陷存在时**的静默对照窗（30 s，无 pytest）⇒ 闸门键改写 **0** 个 |
| `probe_live_db_writes_20261125_scoped.json` | **缺陷存在时**的范围复测窗（28 用例）⇒ 闸门键改写 **4** 个（全是 `page_state\|*`） |
| `probe_live_db_writes_20261125_guard_v1.json` / `_guard_v2.json` | 第一版守卫后的同一范围复测 ⇒ 改写 **0** 个 |
| `probe_live_db_writes_20261125_full_guard_v3.json` | 第一轮**全量回归窗**（**3 failed** —— 3 个红例都是「断言默认库＝生产库」这条**被守卫改掉的规则**，已用 `unguarded_default_store()` 处置，见 `D-032` §3.2.5） |
| `probe_live_db_writes_20261125_full_control_v3.json` | 第一轮**同时长**的**静默对照窗**（无 pytest） |
| `probe_live_db_writes_20261125_full_guard_v4.json` | **最终**守卫版的**全量回归窗**：**3267 passed / 3 skipped / 0 failed**（**1289.65 s**），闸门键 **19** 个**逐字节 changedCount = 0**、`kv_meta` **0** |
| `probe_live_db_writes_20261125_full_control_v4.json` | **最终**的**同时长静默对照窗**（**2102.234 s**，无 pytest）：闸门键 **0**、`kv_meta` **0**，且主库文件涨得**更多**（**87.6 vs 70.6 MB/1000 s**）⇒ **归因成立** |
| `probe_gate_keys_20261125_midrun.json` / `_final.json` | 全量回归**中途**与**结束**时的闸门键独立快照（交叉核对） |
| `probe_full_store_window_20261125_guard_v5.json` | **第二轮**：**整库摘要**包住的全量回归窗（**81** 张表行数 ＋ 内容 `sha256`，＋ `kv_entries` / `kv_meta` **整表摘要**，＋ **5** 个测试残留定向探针） |
| `probe_full_store_window_20261125_control_v5.json` | **第二轮**：**同时长**静默对照窗的整库摘要（用于把「运行时自身写入」从整库差异里分离出来） |
| `t401_guard_functional_unified.log` | 当前版的功能子集回归（**296** 例） |
| `t401_guard_selftest_v2.log` | 守卫自测（**13** 项；随后新增 1 项，现为 **14** 项，见 `D-034` §f.1） |
| `cargo_manager_db_20261125_t401.log` | `M-10` 的 Rust 侧回归（`cargo test --offline -p manager-db`） |

> **方法学订正（如实登记）**：WAL 模式下 `PRAGMA data_version` **不与每次提交一一对应**
> —— 缺陷复现的 **9.10 s** 短窗里闸门键被改写 **4** 个，它仍 **2 → 2**；
> `TASK-184` 的 **943.705 s** 长窗里 **185** 次提交才 **2 → 187**；
> 守卫后的 **2102.234 s** 对照窗（运行时自身写 **1124** 行）也只跳 **2** 次
> ⇒ **闸门键逐字节比对才是可靠判据**，`data_version` **降级为辅助读数**。
> **不推翻** `TASK-184` 的**语句级**计数结论（**185** 次提交 ／ **362** 条 DML）。
> 详见 `T-储.01` 的 `D-035` §2.6。

### 最终读数（**2026-11-25 · `T-储.01` 闭合**）

> **一句话**：**测试套件对活链库的写入 = 0**（归因后）。

| 判据层 | 守卫窗（全量回归在窗口内） | 同时长静默对照窗（无 pytest） |
|---|---|---|
| pytest 结果 | **3267 passed / 3 skipped / 0 failed**（**1289.65 s**） | — |
| **闸门键逐字节**（`parameters` **19** 行 ＝ **全集合**） | **changedCount = 0** | **changedCount = 0** |
| **`kv_meta` 逐字节** | **changedCount = 0** | **changedCount = 0** |
| **`kv_entries` 整表摘要**（**6068** 行） | **完全相同** | **完全相同** |
| **`kv_meta` 整表摘要** | **完全相同** | **完全相同** |
| **81 张表**内容摘要 | **76 张完全相同**；余 **6** 张为**运行时产出表** | **同样变化**（差分归因） |
| **测试残留**定向探针（**4** 个） | 全部**不变**（`attempt\|local\|%` 复核 **1 → 1**、尝试 **0 → 0**、`chain_id=0` 复核 **1 → 1**、尝试 **0 → 0**） | 全部**不变** |
| 主库文件 size | **+92 MB / 1304 s ＝ 70.6 MB/1000 s** | **+184 MB / 2102 s ＝ 87.6 MB/1000 s**（**无 pytest 却涨得更多**） |

**判据订正（如实登记，不美化）**：活链库**同时**被实盘运行时写入 ⇒
「**活链库文件字节级零变化**」**字面不成立**（对照窗涨得**更多**即为证）。
本任务证明的是「**测试套件写入 = 0**」（**归因后**），**不是**「活链库文件不动」。

**完整判据设计、读数与诚实边界**：见 `T-储.01` 的 `D-032` §3.2 ＋ `D-035` §7。

---

## 2026-11-25 增量报告索引（`T-闭.01` 方向层主序接管闭环主池）

> **目的**：把「方向层的序位能不能接管闭环三跳的主池」这件事**用实测读数钉死**，
> 并给出**可复算**的对照（旧序 vs 新序）证据。
> **落点**：`flashloan/tools/closed_loop_manager/src/lib.rs`（**唯一改动文件**）。
> **实盘库零写入**：全程 `mode=ro` ＋ `PRAGMA query_only = ON`；复算在**瘦副本**上做。

### 探针与工具

| 文件 | 作用 |
|---|---|
| `tools/clone_closed_loop_slice_20261125.py` | 把 **11.93 GB** 的实盘库切成**瘦副本**：逐表复制 schema ＋ 只复制闭环层会读的表 ⇒ 得到一个 **58 MB**、**83** 张表的库（方向池 **692** 行、闭环跳 **522** 行、闭环 **174** 行） |
| `tools/compare_directional_primary_order_20261125.py` | **只读**影子比对：方向层序位 vs 旧口径主序（换池率、序位分布、选池依据分布、截断窗口越界数） |
| `tools/compare_directional_primary_order_effect_20261125.py` | 对**同一份切片重物化两次**的结果做差异量化（闭环数 / 跳数 / 成员集 / 容量 / 价格质量 / 优化状态 / 跳过令牌） |
| `tools/probe_legacy_cutover_comparison_20261125.py` | 复算闭环层的「旧口径切换」诊断位（`readyForCutover`），**只读** |

### 证据文件

| 文件 | 内容 |
|---|---|
| `reports/directional_primary_order_shadow_20261125.json` / `.md` | 影子比对读数：方向池 **692** 行（有 rank **342**）、已发布跳 **522**、跳维度换池率 **23.7548 %**（124 / 522）、受影响闭环 **99 / 174**、**方向层 rank-1 在旧候选层截断窗口内 `notInTier` 0 / 216 ＋ 0 / 386**、当前主池的序位分布 `{1: 404, 2: 61, 3: 51, None: 6}` |
| `reports/directional_primary_order_effect_20261125.json` | 旧序 vs 新序的**差异量化**：闭环 **174 → 174**（新增 0 / 移除 0）、跳 **522 → 522**、跳过令牌 **1100 → 1100 且逐条相同**、**候选成员集逐跳完全一致**、换池跳 **118 / 522 = 22.6054 %**、容量 **37 变好 / 46 变差 / 91 不变**（**−0.5963 %**）、**价格质量 89 变好 / 0 变差 / 79 不变**（**+0.4197 %**）、`temporary` **6 → 0** |
| `reports/legacy_cutover_comparison_20261125.json` | 旧口径切换诊断位：`readyForCutover` 改动**前已是 `false`**（闭环 174 vs 104、跳 522 vs 312、`fieldMismatch` **225** 行）⇒ 本改动**不使该诊断位变差** |
| `closed_loop_slice_4663.sqlite3` | **58,511,360 B** 瘦副本（**证据 · 保留**；须确认 `.gitignore` 不误提交） |
| `slice_off.sqlite3` / `slice_on.sqlite3` | 同一切片的**旧序**（`changedRows = 0`、`closedLoopVersion = 19` ⇒ 与实盘逐行一致）与**新序**（`changedRows = 2442`、`closedLoopVersion = 20`）两次物化的产物（**证据 · 保留**） |

### 最终读数（**2026-11-25 · `T-闭.01` 部分闭合**）

> **一句话**：方向层的主序可以在**不引入任何新池、不放宽任何门禁**的前提下接管闭环主池；
> 代价是**折叠容量 −0.60 %**，收益是**价格质量 +0.42 %（单向占优：89 变好 / 0 变差）**。

| 判据 | 读数 | 结论 |
|---|---|---|
| 闭环身份 | **174 → 174**（新增 **0** / 移除 **0**） | **不变** |
| 跳数 / 跳过令牌 | **522 → 522** ／ **1100 → 1100 且逐条相同** | **不变** |
| 候选成员集（主池 ∪ 备选） | **逐跳完全一致** | **纯置换 ⇒ 不引入新池** |
| 门禁 | 容量门禁仍生效；`temporary` **6 → 0** | **未放宽**（6 条换池接管被方向层 rank-1 **直接取代**） |
| 折叠容量 | **37 变好 / 46 变差 / 91 不变**，合计 **−0.5963 %** | **轻微下降**（方向层是**价格**序，旧序是**深度**序） |
| 价格质量（利润代理） | **89 变好 / 0 变差 / 79 不变**，合计 **+0.4197 %** | **单向占优** |
| 旧序可回滚 | 开关 OFF ⇒ `changedRows = 0`、`closedLoopVersion = 19` | **与实盘逐行一致** |
| 实盘生效 | **未做**（运行时 pid **27084** 未重启） | **须随下次运行期重启生效** |

**完整方法、代码改动与诚实边界**：见 `T-闭.01` 的 `D-113` §3 ＋ `D-116`。

---

## 2026-11-25 增量报告索引（`T-方.01` 方向层投影不变式守卫）

> **目的**：把「方向层是否仍然是基础池的**忠实投影**」这件事**用实测读数钉死**，
> 并补上**一条写进停止条件、却没有检测器**的缺口（**I-9**）。
> **落点**：`flashloan/tools/directional_pool_manager/`（**唯一改动 crate**）。
> **实盘库零写入**：全程 `mode=ro` ＋ `PRAGMA query_only = ON`。

### 探针与工具

| 文件 | 作用 |
|---|---|
| `tools/audit_directional_projection_20261125.py` | **只读**审计：逐作用域核对 **I-9**（方向行 vs 基础池行，**9 列**）、方向语义、身份唯一性、序位唯一/无缺口、「**未测量 ≠ 零**」、**固定费率 · 无 hook** 约定，以及闭环引用（**I-7 / I-12**）与闭环体自洽性；退出码 **0** ＝ 零失败项 |

### 证据文件

| 文件 | 内容 |
|---|---|
| `reports/directional_projection_audit_20261125.json` / `.md` | 方向层投影不变式审计读数：**零失败项** —— 方向池 **692** 行 ＝ **346** 基础池 × 2（`core4` **488/340/170**、`candidate_exchange` **494/352/176**）；**I-9 与基础池不一致 0**、**无基础池行 0**、方向语义错 **0**、序位重复组/缺口组 **0/0**、身份重复 **0**、「未测量≠零」标记错 **0/0**、**已投影池带动态费率 0 / 带 hook 0**、**I-7 0**、**I-12 0**、闭环体与跳序列不一致 **0**（跳 **522**） |

### 最终读数（**2026-11-25 · `T-方.01` 部分闭合 15/16**）

> **一句话**：数据面上「**V4 池子 ＋ 方向值 ＝ 方向池**」**成立**（与基础池逐列不一致 **0** 行）；
> 但**原先没有任何代码在守护它** —— 旧比较只看**两个方向行彼此**，两个方向行**可以一起过期**而比较仍绿。

| 判据 | 读数 | 结论 |
|---|---|---|
| 方向池 vs 基础池（**I-9**，9 列逐列） | **不一致 0** ／ **无基础池行 0** | **成立**（实盘数据面） |
| 方向语义 / 身份 / 序位 | 语义错 **0**、身份重复 **0**、序位重复组 **0**、缺口组 **0** | **成立** |
| 「未测量 ≠ 零」 | 容量标记错 **0**、输出标记错 **0** | **成立** |
| 固定费率 · 无 hook | 已投影池带动态费率 **0**、带 hook **0** | **约定未被突破** |
| 闭环引用（**I-7 / I-12**） | 无法解析 **0**、方向不一致 **0**、闭环体不一致 **0**（跳 **522**） | **引用完整且自洽** |
| 守卫本身 | 改前**零检测器**；改后 `base_pool_mismatches` / `rows_without_base_pool` ＋ 报告**点名**不一致的列 | **缺口已补** |
| 回归 | `directional-pool-manager` **25** ／ `manager-api` **33** ／ 方向层架构守卫 **7**（**0 failed**） | **全绿** |
| 实盘生效 | **未做**（二进制 mtime **2026-11-24 08:06**；并行进程在途 ⇒ 不重建、不重启） | **须随下次运行期重建 ＋ 重启生效** |

**完整方法、代码改动与诚实边界**：见 `T-方.01` 的 `D-122` §1.3/§1.5 ＋ `D-125`。

## 2026-11-25 增量报告索引（剩余任务总结与分类裁定 · `D-136`）

> 本节登记本轮（2026-11-25 11:45 +08）新增的工具、脚本与实测读数。
> 结论文档见 `plan/tasks/P-01-池子重构/docs/D-136-剩余任务清单总结与分类裁定_20261125.md`。

### 新增工具

| 文件 | 内容 |
|---|---|
| `tools/update_d014_readings_20261125.py` | 重算 `D-014` §1.1 总读数 ＋ §1.2 任务表（**22** 行，未完成列合计 **36**） |
| `tools/update_d014_class_20261125.py` | 写入 `D-014` §1.4 状态分类表 ＋ §1.3 对账块 |
| `tools/update_d001_migration_20261125.py` | 修 `D-001` §7 迁移记录表头（散落的中段表头移回首行）＋ 登记 `D-136` ＋ 本轮两条迁移记录 |
| `tools/update_p01_readme_table_20261125.py` | 重算 `P-01/README.md` 二级任务表（**22** 行）与合计读数 |
| `tools/update_d004_readings_20261125.py` | 按实测读数重写 `D-004`（勾选 §1 **5** 项 ＋ §3 **3** 项，回填 §4 整体读数） |
| `tools/mdpatch.py` | Markdown 补丁助手（`@` 占位反引号，避免脚本里转义反引号） |

### 回归与读数

| 文件 | 内容 |
|---|---|
| `py_arch_guards_only_20261125.log` | 架构守卫 **80 passed / 0 failed**（120.70 s） |
| `py_arch_guards_20261125b.log` | 架构守卫 ＋ 记录层守卫 **90 passed / 0 failed**（125.61 s） |
| `tools_m1m6_test_20261125.log` | `pool-manager` **167 passed** ＋ `review-manager` **161 passed** |
| `tools_build_20261125c.log` | `cargo build --offline --workspace` ⇒ **exit 0**（3m 54s） |
| `t401_publish_watch.jsonl` | 复核代次时间线：**rv 103 → 115**（08:39 → 10:35），此后**停在 115** |

### 阻塞证据（`D-136` §3.3）

| 文件 | 内容 |
|---|---|
| `logs/t401_runtime_sup/runtime_stderr.log` | 自 **11:00:50 +08** 起 **764** 条 `pool_manager_refresh_required` / **`refreshCause = candidate_pool_key_missing`** |

**读数口径**：实盘全部**只读**（`mode=ro` ＋ `PRAGMA query_only = ON`）⇒ 实盘库写入 **0**。

---

## 2026-11-25 第 3 轮增量索引（两项任务闭合 ＋ `M-4` 决策 · `D-138` / `D-139`）

> 本节登记本轮（2026-11-25 13:35 – 13:53 +08）新增的工具、脚本与实测读数。
> 结论文档见 `plan/tasks/P-01-池子重构/docs/D-138-剩余任务清单总结第3轮与任务闭合_20261125.md`
> ＋ `plan/tasks/P-01-池子重构/docs/D-139-M4决策_rank列保留与删列豁免_20261125.md`。

### 新增工具

| 文件 | 内容 |
|---|---|
| `tools/recount_remaining_tasks_20261125.py` | **可复算**剩余读数（三层口径统一）：二级任务归属 **25** ＋ 一级特有 **8** ＋ 横切 **55** = **88** |
| `tools/probe_rank_column_ruling_20261125.py` | 只读探针（`M-4` 决策的实测依据）：`rank` 形状 / `depth_score` 可替代性 / PRIMARY KEY 归属 |
| `../flashloan/tools/review_manager/examples/fault_injection_matrix.rs` | 故障注入矩阵（**11 场景**）：独立 example、不入生产路径、全部跑在 `tempfile` 临时目录 |

### 实测读数

| 文件 | 内容 |
|---|---|
| `reports/rank_column_ruling_20261125.json` | `M-4` 决策读数：`rank` 在 PRIMARY KEY **true**；稠密 1..N **216/216** 组；`depth_score` 与 `rank` 相等 **0/340** 行、**排序不一致 82/216** 组；`rank = 0` 的行 **0** |
| `reports/task182_fault_injection_matrix_20261125.json` | 故障注入矩阵结果：**`scenarios=11 passed=11 redFailures=0`**（2026-11-25T05:30:04Z） |
| `reports/task182_fault_injection_matrix_20261125.md` | 同上的可读版（逐场景读数） |
| `t401_publish_watch.jsonl` | 复核代次时间线（续）：至 **13:52:47** 仍**停在 `review_version = 115`**、`completed_count = 0`、`not_executable_count = 154` |

### 阻塞指纹（`D-138` §4.1）

| 观测项 | 读数（只读 HTTP overview · `/api/dex/overview?chainId=4663`） |
|---|---|
| `candidateExchange.poolScopeChecksum` | **`2bea9bb4…`** |
| `closedLoop.poolScopeChecksum` | **`ae6b14ac…`** |
| `runtimeCore.generation` | **473** |

⇒ **两个池域校验和仍然不同** ⇒ **缺陷 B 未消除** ⇒ 剩余 **25** 项 C 类阻塞**全部**挂在同一个解锁条件上。

**读数口径**：实盘全部**只读**（`mode=ro` ＋ `PRAGMA query_only = ON`）⇒ 实盘库写入 **0**。
