# TASK-186 · 费率标签口径订正 —— 选池宇宙不变对照（2026-11-23）

**结论**：费率标签阈值由私有的 `3_000` 改为与准入口径**同源**的
`MAX_CANDIDATE_FIXED_FEE = 30_000`；**选池宇宙逐行相等**，
异常层**成员**与**标签文本**亦无变化（本次订正在主网存量数据上为 **no-op**，
修正的是一条**潜在**缺陷：今后若费率落在 (3_000, 30_000] 的池被判为异常，
不再被误贴 `fee_not_fixed_candidate_range`）。

## 1. 只读性

- 连接串：`file:E:\2026OPC大赛\flashLoan\flashloan\db\local_store_4663.sqlite3?mode=ro`
- 观测表行数前后**一致**：**True**

## 2. 复刻校验（OLD 规则 vs 库内已物化 `reasons_json`）

| 项 | 值 |
|---|---|
| 逐条一致 | **2362** |
| 不一致 | **0** |
| scope 中找不到 | **0** |

⇒ Python 复刻与 Rust `abnormal_reasons` **同构**，差分结论可信。

## 3. 选池宇宙（中间跳候选集合）逐行对照

| 项 | OLD(3_000) | NEW(30_000) | 相同 |
|---|---|---|---|
| 行数 | 348 | 348 | —— |
| 摘要 sha256 | `d6ed9ce11d5a6afb…` | `d6ed9ce11d5a6afb…` | **True** |

- 推导结果与库内已物化 `manager_market_observation_intermediate_pools`
  **逐行相等**：**True**
  （物化行数 348）
- 异常层**成员**摘要相同：**True**
- 异常层**标签**摘要相同：**True**；差异行数 **0**

## 4. 费率落在 (3_000, 30_000] 的池

| 层 | 数量 |
|---|---|
| `active`（进选池宇宙） | **33** |
| 非 active（进异常层，会被打标签） | **0** |

⇒ 存量数据里**没有**一个「会被误贴标签」的池 ⇒ 本次订正对存量是 **no-op**；
被订正的是**未来**可能出现的误贴（费率合规但因其它原因判异常的池）。

## 5. 报告摘要

`86facec1dc14d8e5af8f78f09cafcdd42805b782e7e07728a2413e776bd3d3e9`

## 6. 复算命令

```powershell
python plan/runtime_evidence/tools/compare_task186_fee_label_universe_20261123.py
```
