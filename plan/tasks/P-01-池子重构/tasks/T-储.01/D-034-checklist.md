# D-034 `T-储.01`｜测试套件生产库零写入 · 测试目标 check list

**为什么单独立项**：`TASK-183` 只收口了 `kv_meta` 一处。

**一次全量实测**：主库 `PRAGMA data_version` 由 **2 → 187**（**185 次提交**，

窗口**恰好**覆盖全量运行），写入分布：`kv_entries` **348**（`parameters` 集合 =

**控制面板 page state**）／ `kv_meta` **8**（`schema_migrations`）／

`cow_execution_reviews` **3** ／ `cow_execution_attempts` **3 DELETE**。



**根因三条**（语句级探针 + 只读 `data_version` 采样器钉死）：



1. `core.local_kv` 的**默认路径就是主链库**（`store_path(None)` → `resolve_database_path(None)`

   → 主库），而 `bot/page_state/store.py` 等**生产读路径**直接用它

   ⇒ 测试套件**改写主库的控制面板 page state**；

2. `app_runtime/web/control_panel_liquidation_discovery.py:156/158` 写 `kv_meta.schema_migrations`

   （**与 `TASK-183` 收口的 `schema_version` 是不同的键**）

3. `core/db/builders.py:50` 等模块**按名导入** `delete_rows` ⇒ **绕过**了

   `flashloan/app_runtime/tests/conftest.py:98` 的 `_guard_real_chain_databases`

   （它守的是 `storage_business_io` 的 5 个**写函数名**，不是**连接**）。



**证据已产出**：`plan/runtime_evidence/reports/task184_test_suite_mainnet_writes_20261123.{json,md}`

（**范围复测**（本次现场实测）：3 个测试文件 · 14.81 s · `data_version` **2 → 58**

（**56 次提交**）· DML **58** 条 ／ **全量**：`data_version` **2 → 187**

（**185 次提交**，观测窗 **943.705 s**）· DML **362** 条 ／

**静默对照 150 s 零提交** ⇒ 可归因于 pytest 进程本身）。



**生产影响（**实测读数 · 不是估计**）**：当日写入 `parameters` **19** 个键，

跨三个命名空间 —— `cow_submission_pause_guard`（**6**）、

`liquidation_pause_guard`（**9**）、`page_state`（**4**）；

值均为**默认值**（`paused=false` · `order_submission_enabled=true` ·

`consecutive_failure_count=0` · `pause_reason=null`）。

⇒ **这不是脏数据，而是把生产的两道闸门重置成默认值**：

**广播提交闸门**与**清算闸门**。若实盘当时正因真实失败连击而处于暂停态，

一次测试跑完就会**把暂停清掉**。

（**当前状态**：未暂停，**恰好就是默认值** ⇒ 本次**无需**人工恢复。）

`cow_execution_reviews` 表内 **49719** 行（绝大多数是实盘复核），

测试**净残留 1 行**（`attempt|local|0|…`，链 ID **0**、pair 为空）。



**验收前置**：无（本项可独立实施）。



#### f.1 结构验收



- [ ] `conftest.py` 的守卫从「守**函数名**」改为「**守连接**」：

      对**活链库**的 sqlite 连接强制 `mode=ro`，**写即在 SQLite 层失败**（而不是静默写进去）

- [ ] `bot/page_state/store.py` 等生产读路径的**默认路径可注入**

      （测试可指向临时库，运行时仍指向主链库）

- [ ] `core/db/builders.py` 等**按名导入**的写入口**纳入同一守卫**

- [ ] **未**新增主库表或迁移；**未**放宽任何既有门禁



#### f.2 行为验收



- [ ] **pytest 全程主库 `PRAGMA data_version` 不变**（一次全量读数作为判据）

- [ ] **pytest 全程主库文件 mtime / size 不变**

- [ ] 生产闸门状态**不被测试改写**：`cow_submission_pause_guard` / `liquidation_pause_guard`

      的**每一个键**在一次全量前后**逐字节一致**（默认值也**不许**被写）

- [ ] 守卫**非空洞**：临时关闭守卫 ⇒ **稳定红**（写操作被拦截）

- [ ] 活链库的**只读**访问**仍然可用**（守卫不得把合法读路径一起打死）



#### f.3 边界验收（**不得**发生）



- [ ] **未**写库、**未**广播

- [ ] **未**改动方向池或记录层的生产代码
