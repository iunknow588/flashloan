# TASK-183 · KV schema 初始化只读化（已闭合）

生成时间：2026-11-23T03:10:00+00:00

## 缺陷

- **位置**：`flashloan/src/core/local_kv.py::_ensure_schema`
- **原写法**：`INSERT OR REPLACE INTO kv_meta (meta_key, value_json) VALUES ('schema_version', ?)` **无条件执行** + `commit()`
- **触发路径**：`dex/intent_trade/chains/robinhood/market_observation.py:59` 的**导入期副作用** → `storage_chain_core.load_chain_tokens` → `storage_business_io.query` → `Database.ensure` → `local_kv.ensure_store`
- **后果**：**每次 pytest 都向活的主网链库提交一笔**，只读访问被变成写事务

## 修法

- **先读后比，值不同才写**；新增 `_schema_version_is_current()`
- **兼容旧存储形态**：裸数字 `1` 与 JSON 字符串 `"1"` 都算「当前」 ⇒ 不会每次重写

## 守卫

- 文件：`flashloan/app_runtime/tests/unit/test_local_kv_schema_write_guard.py`（**5** 项）
- 读数：**5 passed**
- **非空洞**：还原旧写法 ⇒ **稳定红 3 项**；恢复修法 ⇒ 重新变绿
- **指纹口径**：字节一致性用例比对了**库文件 + WAL 侧文件**（`-wal`）
  —— 只落在 WAL 里、尚未回写主库文件的写，在活链库上**就是一次写**

| 用例 | 断言 |
|---|---|
| 已是最新 | 零 DML |
| 新库 | 仍建出 schema |
| 版本过期 | 仍刷新 |
| 存的是裸数字 | 不重写 |
| 新进程读库 | 库文件 **+ WAL 侧文件** 双指纹一致 |

## 现场读数（**探针只读采样**）

| 范围 | `kv_meta` 写入 | 其中 `schema_version` | `schema_migrations` |
|---|---|---|---|
| 范围复测（3 个测试文件 · 14.81 s） | 8 | **0** | 8 |
| 全量（943.705 s 观测窗） | 8 | **0** | 8 |

- **静默对照**：同形采样器在 **150 s** 静默窗口内 **0 次提交** ⇒ 运行期观测到的提交**可归因于 pytest 进程本身**
- 回归：**3129 passed / 3 skipped / 0 failed**（**987.94 s**；本轮复跑，
  与第 68 轮首读 **1084.82 s** 一致 ⇒ 只增不减）

## 边界（**不可越界解读**）

- 本项**只**收口 `kv_meta.schema_version` **一处**写入路径
- 其余写入路径（`kv_entries` / `kv_meta.schema_migrations` / `cow_execution_*`）见 **`TASK-184`（开放）**
- **不得**据此宣称「测试套件不再写生产库」
