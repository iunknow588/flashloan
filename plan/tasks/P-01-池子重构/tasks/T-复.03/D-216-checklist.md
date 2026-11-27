# D-216 `T-复.03`｜验收清单（**checklist**）

> 验收编号 `AC-复.03`。**每一条都必须带实测读数**。

## A. 代码项

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| A1 | 文件名带小时桶 | `<干名>_<YYYYMMDD-HH>.jsonl` | `…_c3b6a501483b_20261126-20.jsonl` | ✅ |
| A2 | **日期目录保留** | 目录名仍是 `<YYYY-MM-DD>` | `…/robinhood/4663/2026-11-26/` | ✅ |
| A3 | 小时桶唯一生成处 | `review_hour_stem(day, hour)` | `20261126-20` | ✅ |
| A4 | 行归属小时由行自己的时间戳决定 | `line_hour(line)` | `observedAt=2026-11-26T20:38:04Z` → 桶 `20` | ✅ |
| A5 | 写者句柄按 `(天, 小时, 闭环)` | `JsonlWriter.files` 键 | 三元组 | ✅ |
| A6 | 只保留当前小时的句柄 | `rotate(day, hour)` | 按 `(day, hour)` 过滤 | ✅ |
| A7 | **保留期清理逻辑未改** | `review_day_folder` / `review_day_from_folder` / `collect_day_folders` / `review_day_is_removable` / `purge_expired_review_files` | **5 个函数原样** | ✅ |
| A8 | 单测覆盖「一天多个小时」 | `one_closed_loop_writes_one_file_per_hour_of_the_day` | 新增 | ✅ |
| A9 | 单测钉死「小时不进目录名」 | `test_the_hour_never_enters_the_folder_name` | 新增 | ✅ |
| A10 | 回归绿 | `cargo test -p review-manager` | **175 passed / 0 failed** | ✅ |
| A11 | 守卫测试绿 | `pytest test_record_layer_guards.py` | **11 passed** | ✅ |

## B. 部署闸门

| # | 验收项 | 读数 | 状态 |
|---|---|---|:---:|
| B1 | `flashloan_rs.dll` 已发布（承载记录层） | **2026-11-26T20:23:54Z** | ✅ |
| B2 | `manager-api.exe` 已发布（承载记录层） | **2026-11-26T20:24:40Z** | ✅ |
| B3 | 实盘出现小时文件 | **10** 个 `…_20261126-20.jsonl` | ✅ |
| B4 | 文件大小可控 | **4 576 B – 17 373 B**（改前 **190 305 B** 且持续增长） | ✅ |

## C. 诚实边界（**未闭合项**）

| # | 项 | 说明 | 状态 |
|---|---|---|:---:|
| C1 | 旧命名的 **176** 个文件 | **不改名、不删除**，随**整日目录**在保留期到期时一起走 | ✅ 已说明 |
| C2 | 「整日目录被删」的**实盘**读数 | 保留期 **30 天**，最新目录是今天 ⇒ 实盘未触发 | ⬜ 由**单测**证明（`purge_expired_review_files`） |

## 未闭合项（**`- [ ]` 口径** · 供 `recount_remaining_tasks_20261125.py` 逐目录计数）

- [ ] **C2** 「整日目录被删」的**实盘**读数：保留期 **30 天**，最新目录是今天 ⇒ 实盘未触发（由**单测** `purge_expired_review_files` 证明）

> **C1 已说明**：旧命名的 **176** 个文件**不改名、不删除**，随**整日目录**在保留期到期时一起走 ⇒ **不占**未完成项。

