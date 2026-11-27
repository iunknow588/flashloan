# D-217 `T-编.02`｜复核收敛口径与一轮复核耗时 · 开发计划（**独立 · 自包含**）

> **本目录是 T-编.02 的完整工作单元 —— 独立、自包含、不与其它任务耦合。**

---

## 1.0 编号逻辑（**本任务的编号从哪来**）

| 项 | 值 |
|---|---|
| **本任务编号** | **`T-编.02`** |
| **本任务验收编号** | **`AC-编.02`**（与任务编号**同号段**） |
| **编号作用域** | **模块内（单模块索引）** |
| **编号索引** | **`编`**（`M-8`（编排层（依赖图 / 路由 / 组合））） |
| **序号** | 第 `02` 个（该索引号段内递增，**不复用**） |
| **同索引其它任务** | `T-编.01`（双链准入与稳定窗口） |

**本任务只改动一个模块** `M-8`（编排层）⇒ 编号索引即该模块索引 **`编`**。
落点：`scripts/mainnet_arbitrage_healer.py`（编排器）＋ 其回归测试。

**本任务涉及的模块**（**这一列就是本任务的回归范围**）：

| 模块 | 名称 | 索引 |
|---|---|---|
| `M-8` | 编排层（依赖图 / 路由 / 组合） | `编` |

**登记**：[`plan/D-001-TASK_REGISTRY.md`](../../../../D-001-TASK_REGISTRY.md) §4.2。

---

## 1. 身份与范围

| 项 | 值 |
|---|---|
| **任务编号（纯 ID）** | **`T-编.02`** |
| **验收编号（纯 ID）** | **`AC-编.02`** |
| **标题** | 复核收敛口径（**当前闭环目录**）＋ 一轮复核耗时归因＋ 存活探测误判修正 |
| **优先级** | P0 |

### 1.1 用户口径（**本任务的全部依据**）

> 「任何一个闭环，第二次复核出现结果，必然覆盖前一次闭环的结果，这样，每个闭环仅仅有一个当前的复核结果。」
>
> 「盈利数据随时会失效，请注意时效问题。让系统启动时立即执行一次复核更新过期数据。」
>
> 「启动时等待时间置 **0**，清理数据自动会开始的。」
>
> 「复核数据太多，不便于分析……」（→ `T-复.03` 的按小时分文件）

---

## 2. 改动清单（**文件级**）

| # | 文件 | 改动 | 口径 |
|---|---|---|---|
| 1 | `scripts/mainnet_arbitrage_healer.py` | `review_rows()`：**管理器库有行就直接返回**，**不再**扫旧表 | 旧表扫描的结果**直接被丢弃** —— 白读 **2.13 GB** |
| 2 | 同上 | `reviews_missing_a_value(db, hours, manager_api=None)` 新增 `manager_api` 形参 | 以前**没传** ⇒ 被迫回退旧表 |
| 3 | 同上 | `converge_reviews()` 的**三处**调用传 `manager_api` | 收敛链**只读**管理器库 |
| 4 | 同上 | 新增 `current_catalog_loop_keys(manager_api)` | **当前**闭环目录的闭环键；读不到时返回 `None`（**不缩范围**） |
| 5 | 同上 | `reviews_missing_a_value()` 只统计**当前目录内**的闭环 | 离开目录的闭环是**历史**，不是**在产缺陷** |
| 6 | 同上 | 新增 `reviews_outside_the_current_catalog()` | 目录外的行**单独计数上报**，**不隐藏** |
| 7 | 同上 | `converge_reviews()` 返回值新增 `outsideCatalogReviews` / `outsideCatalogMissingValue` | 历史**看得见**，但不**卡住收敛** |
| 8 | 同上 | `healing_actions()` 的 `reviews_sound` / `positive_reviews` / `repair_reviews` 改读 `currentLoopStats` | 全窗口统计会把**已停交易的路线**算成在产缺陷 |
| 9 | 同上 | `transaction_production_status()` 改读 `currentLoopStats` / `missingCurrentLoop` / `currentLoopLatestReviewAt` | 生产态判定必须**只**看**在产**路线 |
| 10 | 同上 | `audit_reviews()` 新增 `currentLoopLatestReviewAt` | 「盈利数据会失效」⇒ 新鲜度按**在产**复核算 |
| 11 | 同上 | `stability_window()` 改读 `currentLoopStats` | 稳定窗口的判据同样按**在产**口径 |
| 12 | 同上 | `api_get()` 增加 `kind` 字段（`timeout` / `connect` / `http` / `io`） | 按**异常类型**判定，**不猜**消息文本 |
| 13 | 同上 | `LIVENESS_TIMEOUT_SECONDS` = **120**、`RUNTIME_BUSY_RESTART_SECONDS` = **1800** | 超时 ≠ 死亡；**忙**不等于**死** |
| 14 | 同上 | `runtime_business_health()` 超时返回 `{"ok":True,"stage":"busy","busy":True}` | 忙时**如实说忙** |
| 15 | 同上 | `supervise` 循环加**忙看门狗**（超 **1800 s** 才按 wedged 重启） | 复核轮**本身**要好几分钟，不能当死 |

---

## 3. 一轮复核耗时的**归因**（**实测，不是推断**）

| 读数 | 值 |
|---|---|
| 旧复核表 `cow_execution_reviews` 行数 | **116 804** |
| 编排器那条 SQL（`datetime(created_at)` 包住索引列）取 20 000 行 | **18.2 s**，载荷 **2.13 GB** |
| `json.loads` 再解析一遍 | **52.2 s** |
| 一轮里重复跑 **5 – 7** 次 | **≈ 70 s / 次** |
| **修复后**：一轮复核 | **17 min 27 s → 7 min 27 s**（旧码 19:32:52→19:50:19；新码 20:03:43→20:11:10） |

**根因不是线程并发度**：`review_rows()` **即使管理器库已有数据也照扫旧表**，
结果**直接丢弃**；`reviews_missing_a_value()` 还**没传** `manager_api`，被迫回退旧表。

### 3.1 存活探测的误判（**实测**）

| 读数 | 值 |
|---|---|
| 时刻 | **19:52:24** |
| 现象 | 运行时忙于复核轮，`GET /healthz` **5 s 超时** |
| 后果 | 编排器判定其**死亡**并**重启**运行时（PID **9704 → 3392 → 19492**），**在途复核作废** |

---

## 4. 回归范围

```powershell
# M-8 编排层
python -m pytest scripts/tests/test_mainnet_arbitrage_healer.py -q
python -m pytest flashloan/app_runtime/tests/unit/test_mainnet_arbitrage_healer_repair_chain.py -q
```

---

## 5. 实盘读数（**2026-11-27**）

### 5.1 收敛（**修口径后**）

```json
{"action":"converge_reviews","result":{"converged":true,"stalled":false,"remainingMissingValue":0,
 "outsideCatalogReviews":166,"outsideCatalogMissingValue":19}}
```

**修口径前**：同一动作 `converged=false, stalled=true, remainingMissingValue=19` ——
那 **19** 条全部来自**已离开目录的 166 行**（目录内 **10** 行缺值 **0**），
⇒ 收敛**永远 stalled**。

### 5.2 层级判定（**修口径后**）

| 层 | 判定 |
|---|---|
| 代币 `tokens` | **valid = true**（四库缺字段全 **0**） |
| 池 `pools` | **valid = true**（越界池 **0**） |
| 闭环 `closedLoops` | **valid = true**（**10** 闭环 / **30** 跳） |
| 复核 `reviews` | **valid = true**（`bestAmountMissingCurrentLoop = 0`，目录内 **10/10** 全部带明确盈亏） |
| 广播 `broadcast` | **valid = false**（`manager_v4_review_broadcast_requests` = **0**、`manager_cow_execution_attempts` = **0**） |

### 5.3 广播层为何仍红（**诚实边界**）

| 读数 | 值 |
|---|---|
| 当前 **10** 个闭环的复核状态 | **全部** `not_executable` |
| 失败原因 | `runArbitrage ok=false`，`error = NoProfitableRoute`，`revertData = 0x4f422847` |
| `0x4f422847` | **部署合约自己的** `NoProfitableRoute()`（`keccak256("NoProfitableRoute()")`） |
| 复核阶梯 | 每条闭环探测 **7** 个额度（**0.001 ETH – 0.053 ETH**），**没有一个**盈利 |
| 复核记录的盈亏 | 全部为负（**-9.8e-17 – -1.69 ETH** 区间内，具体见 §5.4） |

> **结论**：广播闸门**正确不放行** —— 上游**没有一条正收益复核**。
> 这不是闸门坏了，是**合约在链上判定这条路线不盈利**（价格差覆盖不了成本）。

### 5.4 当前闭环的复核盈亏（**逐条**）

| 闭环尾号 | 复核状态 | 盈亏（ETH） |
|---|---|---:|
| `…bb4b70a580adad4fe3` | `not_executable` | **-0.0001353350979** |
| `…3521c899c87ce02f35` | `not_executable` | **-1.687227928406e** |
| `…ac2a2c8af5320d9eec` | `not_executable` | **-0.0001604370227** |
| `…81314ae59d232d4afd` | `not_executable` | **-9.8e-17** |
| `…a7a74833232b3b4c0c` | `not_executable` | **-5.828640471623e** |
| `…a7a74833232b3b4c0c` | `not_executable` | **-4.337289981249e** |
| `…a7a74833232b3b4c0c` | `not_executable` | **-5.8548895891562** |
| `…81314ae59d232d4afd` | `not_executable` | **-6.14e-16** |
| `…a7a74833232b3b4c0c` | `not_executable` | **-4.8301455803908** |
| `…ac2a2c8af5320d9eec` | `not_executable` | **-0.0002506149923** |

### 5.5 广播闸门本身是**开**的

| 读数 | 值 |
|---|---|
| `broadcastAllowed` | **true** |
| 私钥 | **已配** |
| `manager_v4_review_broadcast_requests` | **0** 行 |
| `manager_cow_execution_attempts` | **0** 行 |

> ⇒ **「持续产生链上广播交易记录」尚未达成**；
> 缺口**不在**广播准入，而在**上游没有一条正收益复核**。

