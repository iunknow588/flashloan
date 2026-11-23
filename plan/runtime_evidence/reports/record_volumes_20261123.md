# 记录体量与盈亏格式实测（只读）· 2026-11-23

> **配套机器可读报告**：[`record_volumes_20261123.json`](./record_volumes_20261123.json)
> **测量工具**：[`../tools/measure_record_volumes_20261123.py`](../tools/measure_record_volumes_20261123.py)
> （可复跑：`python plan/runtime_evidence/tools/measure_record_volumes_20261123.py`）
>
> **用途**：为《记录存储剥离技术方案 v1.0》的三层权威、归档形态与精度口径提供**实测**依据，
> 而非推测。本报告**不含任何设计决策**，只含读数。

## 1. 读数口径（**只读**）

| 项 | 值 |
|---|---|
| 数据库 | `flashloan/db/local_store_4663.sqlite3` |
| 打开方式 | `mode=ro` + `PRAGMA query_only = ON`（**只发 SELECT**） |
| 测量时刻 | `2026-11-22T22:27:37Z` |
| 文件大小 | **8,601,968,640** 字节（8.01 GiB） |
| sha256（前） | `cfa1704980fdb3c819fcb0cb7a5f44c230a2fa53e21c38d514ed805aaab53522` |
| sha256（后） | **完全一致** |
| `externalWriterActivity` | **false**（窗口内无外部写者） |
| 同目录运行时文件 | `-wal` **0** 字节 / `-shm` **32,768** 字节（SQLite 自建，**不携带库数据**） |

## 2. 表体量

| 表 | 行数 | 文本字节 | 字节/行 |
|---|---:|---:|---:|
| `manager_review_library_state` | 1 | 0 | 0 |
| `manager_v4_reviews` | **164** | **102,846** | 627.11 |
| `manager_v4_review_diagnostics` | 164 | 328 | 2.00 |
| `manager_v4_review_broadcast_requests` | 0 | 0 | 0 |
| `manager_broadcast_library_state` | 0 | 0 | 0 |
| `manager_cow_execution_attempts` | 0 | 0 | 0 |
| `manager_broadcast_attempt_diagnostics` | 0 | 0 | 0 |
| `manager_write_requests` | 48 | 0 | 0 |

### 2.1 `manager_v4_reviews` 的文本列构成（共 102,846 B）

| 列 | 字节 |
|---|---:|
| `pool_ids_json` | 34,112 |
| `path_addresses_json` | 29,684 |
| `run_arbitrage_json` | 21,162 |
| `static_preview_json` | 13,134 |
| `path_symbols_json` | 4,426 |
| `hop_sqrt_price_limits_json` | 328 |

## 3. 复核代际（状态行）

| 字段 | 读数 |
|---|---|
| `reviewVersion` | **24** |
| `loopCount` | 164 |
| `reviewedCount` | **14** |
| `dataIncompleteCount` | 150 |
| `notExecutableCount` | 14 |
| `completedCount` / `failedCount` | 0 / 0 |
| `broadcastRequestCount` | **0** |

## 4. 盈亏字段格式（**14 条带盈亏的行**）

| 探针 | 读数 | 含义 |
|---|---:|---|
| `rowsWithPnl` | **14** | 与 `reviewedCount = 14` 一致 |
| `rawIsHexadecimal` | **14** | `signed_pnl_raw` 是**十六进制** |
| `rawIsDecimal` | **0** | **没有一行**是纯十进制 |
| `rawOtherShape` | 0 | 无第三种形态 |
| `signAgreesWithAmount` | **14** | raw 的符号与 `amount` **全部一致** |
| `signDisagreesWithAmount` | **0** | 无 |
| `observedScales` | `1e+18` × 13、`9.999999999999999e+17` × 1 | 定标 **≈1e18**（1 行系 f64 除法舍入） |

**币种分布**（`signed_pnl_currency`）：

| 币种 | 行数 |
|---|---:|
| SPY | **8** |
| WETH | **4** |
| TZ | **2** |

**样本**：

| `signed_pnl_raw` | `signed_pnl_amount` | 币种 |
|---|---|---|
| `-0x2ee726109c1` | -3.223144434113e-06 | WETH |
| `-0x83d28ee4d5a` | -9.058772733274e-06 | WETH |
| `-0x2f07ff79668` | -3.23196233892e-06 | WETH |
| `-0x553766c8be67d` | -0.001499142975907453 | WETH |
| `-0x3ffd672e0ca` | -4.397349396682e-06 | SPY |

## 5. 由读数直接得到的四条事实

1. **剥离是纯前向的**：套利 / 广播相关表 **0 行** ⇒ **零迁移、零回填**。
2. **复核记录不增长**：`replace_review_rows` 每代**全量替换** ⇒ 复核侧的问题
   **不是磁盘**，而是**历史不可追溯**（已到第 **24** 代 ⇒ 至少 **23** 代被物理销毁）。
3. **金额字段的格式与「十进制字符串」的设想不同**：raw 是**带符号十六进制**
   ⇒ 按十进制解析会**差 16 个数量级** ⇒ **必须原样保留**。
4. **多币种**：WETH / SPY / TZ ⇒ 「跨月总利润」这个口径**不成立**
   ⇒ 聚合**必须按币种分组**，且**禁止**在 SQL 里 `SUM()`。

## 6. 诚实边界

- 本报告的读数**只覆盖复核表**（主网库当前只有复核数据，广播表为空）；
- 主网库是**活的生产库**，`externalWriterActivity = false` **只说明本次测量窗口内**
  没有外部写者，**不代表**其它时刻也没有；
- `-wal` / `-shm` 是 SQLite **只读打开 WAL 库时自建**的运行时文件，
  **不携带库数据**，但**确实新增了两个同目录文件** —— 这一点如实记录。

