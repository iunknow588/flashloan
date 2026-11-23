# TASK-184 · 测试套件仍写生产主网库（确定缺陷 · **开放**）

生成时间：2026-11-23T03:10:00+00:00

## 结论

`pytest` **每次运行都会向活的主网链库 `flashloan/db/local_store_4663.sqlite3` 提交事务**。
`TASK-183` 只收口了 `kv_meta.schema_version` 一处，其余写入路径**仍然存在**。

## 读数一：范围复测（**本次现场实测**）

范围：3 个测试文件（page_state 回执 / 控制面板状态 / cow 候选队列），**14.81 s**，**51 passed**

| 项 | 值 |
|---|---|
| `data_version` | **2 → 58** |
| **提交次数** | **56** |
| 命中生产库的连接数 | **111** |
| DML 语句总数 | **58** |

| 语句 | 次数 | 键 |
|---|---|---|
| `INSERT INTO kv_entries` | 44 | `parameters` |
| `INSERT INTO kv_meta` | 8 | `schema_migrations` |
| `INSERT INTO cow_execution_reviews` | 3 | — |
| `DELETE FROM cow_execution_attempts` | 3 | — |

## 读数二：全量（**捕获产物的离线解析**）

观测窗 **943.705 s**

| 项 | 值 |
|---|---|
| `data_version` | **2 → 187** |
| **提交次数** | **185** |
| 命中生产库的连接数 | **1632** |
| DML 语句总数 | **362** |

| 语句 | 次数 | 键 |
|---|---|---|
| `INSERT INTO kv_entries` | 348 | `parameters` **346** + `flash_loan_pool_discovery` **2** |
| `INSERT INTO kv_meta` | 8 | `schema_migrations` |
| `INSERT INTO cow_execution_reviews` | 3 | — |
| `DELETE FROM cow_execution_attempts` | 3 | — |

## 读数三：静默对照

同形采样器在 **150 s** 静默窗口内 **0 次提交**
⇒ 运行窗口内观测到的提交**可归因于 pytest 进程本身**（不是 `manager-api` 之类的后台写者）

## 根因（三条，均已定位到代码）

| 编号 | 语句 | 根因 | 代码位点 |
|---|---|---|---|
| **RC-1** | `INSERT INTO kv_entries` **348** | `core.local_kv` **默认路径就是主链库** ⇒ 生产读路径把**控制面板 page state** 写进生产库 | `bot/page_state/store.py:126` → `page_state/service.py:42` → `liquidation/execution_state.py:104` |
| **RC-2** | `INSERT INTO kv_meta` **8** | 清算发现窗口写 `schema_migrations`（**与 `TASK-183` 收口的 `schema_version` 是不同的键**） | `app_runtime/web/control_panel_liquidation_discovery.py:156/158` |
| **RC-3** | `cow_execution_reviews` **3** + `cow_execution_attempts` **3 DELETE** | 套利守护进程写尝试日志；`core/db/builders.py:50` **按名导入** `delete_rows` ⇒ **绕过** `conftest.py:98` 的守卫（它守的是**函数名**，不是**连接**） | `dex/runtime/cow_attempt_log.py:99` → `cow_arbitrage_daemon.py:115` |

## 后果

- **主库字节比对**在 pytest 运行窗口内**不可取**（第 65 轮口径继续成立）
- 「测试不写生产库」这一既有假设**已被推翻**
- 生产库内已积存测试残留：**控制面板 page state** 行与 `cow_execution_*` 业务行

## 生产影响（**实测读数 · 不是估计**）

**写入的 19 个 `parameters` 键跨越了三个命名空间**（按当日 `updated_at` 只读统计）：

| 命名空间 | 键数 | 写入的值 |
|---|---|---|
| `cow_submission_pause_guard` | **6** | `paused=false` · `order_submission_enabled=true` · `pause_reason=null` |
| `liquidation_pause_guard` | **9** | `paused=false` · `consecutive_failure_count=0` · `pause_reason=null` |
| `page_state` | **4** | `market_observation` · `execution` · `debt_pool` · `account_scan` |

⇒ **这不是「脏数据」，而是把生产的两道闸门重置成默认值**：
**广播提交闸门**与**清算闸门**。若实盘当时正因真实失败连击而处于暂停态，
一次测试跑完就会**把暂停清掉**。

- **当前状态**：未暂停（**恰好就是默认值**）⇒ 本次**无需**人工恢复
- `cow_execution_reviews`：表内 **49719** 行（绝大多数是实盘复核），
  测试**净残留 1 行**（`attempt|local|0|…`，链 ID **0**、pair 为空）
  —— 其余测试行被测试自己的 `DELETE` 带走

## 剩余工作（**本项仍开放**）

1. 守卫从「守**函数名**」改为「守**连接**」—— 对活链库强制 `mode=ro`，写即在 SQLite 层失败
2. `core.local_kv` 与 `bot/page_state` 的默认路径改为**可注入** ⇒ 测试永远解析不到主链库
3. 补「pytest 全程主库 `data_version` **不变**」守卫

## 采集器

`plan/runtime_evidence/tools/analyze_task183_184_production_db_writes_20261123.py`
（--measure 在线复测 / 默认离线解析；**采集器自身对生产库只读**）

## 原始产物（**已随证据留存 · 可离线复跑**）

| 产物 | 路径 |
|---|---|
| 全量语句级探针（**gzip**） | `plan/runtime_evidence/logs/task184_fullrun_probe_20261123.log.gz` |
| 全量 `data_version` 时间线 | `plan/runtime_evidence/logs/task184_fullrun_dataversion_20261123.tsv` |
| 静默对照时间线 | `plan/runtime_evidence/logs/task184_silent_control_dataversion_20261123.tsv` |

复跑（**不碰数据库**）：

```bash
python plan/runtime_evidence/tools/analyze_task183_184_production_db_writes_20261123.py \
  --probe-log plan/runtime_evidence/logs/task184_fullrun_probe_20261123.log.gz \
  --dataversion plan/runtime_evidence/logs/task184_fullrun_dataversion_20261123.tsv \
  --label preserved_full_run --out-json <a.json> --out-md <a.md>
```

**已复跑验证**：由留存产物复算得 **185** 次提交、`data_version` **2 → 187**、
**1632** 个连接、**362** 条 DML —— **逐表分布与本报告完全一致**。
