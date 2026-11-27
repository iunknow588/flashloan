# D-213 `T-池编.01`｜验收清单（**checklist**）

> 验收编号 `AC-池编.01`（与任务编号**同号段**）。
> **每一条都必须带实测读数**；推断不算读数。

## A. 代码项

| # | 验收项 | 判据 | 读数 | 状态 |
|---|---|---|---|:---:|
| A1 | 加载模式默认收敛到核心库 | `default_for_scope()` → `Core` | `coreScopeConfiguredLibraries = ["core"]` | ✅ |
| A2 | 两个作用域**都**是核心库 | `coreScopePolicyMatches` ∧ `candidateScopePolicyMatches` | `true` / `true` | ✅ |
| A3 | 审计跟随同一默认值 | 审计读取 `default_for_scope()` | 代码级 | ✅ |
| A4 | 同一 issue 只报一次 | `core_scope_library_policy_stale` 不重复 | 修复前 **6** 次 → **1** 次 | ✅ |
| A5 | 审计不误报「正忙」 | `busy_timeout` **5 s → 60 s** | 代码级 | ✅ |
| A6 | 越界池为 0 | `candidateScopeOutOfPolicyPoolCount` | **0** | ✅ |
| A7 | 闭环引用无失效 | `invalidHopDirectionalReferenceCount` | **0** | ✅ |
| A8 | 闭环规模按预期收敛 | `loopCount` / `hopCount` | **10 / 30**（改前 **42 / 126**） | ✅ |
| A9 | 代币四库无缺字段 | `missingAddress/Decimals/Name/Symbol` | 全 **0** | ✅ |
| A10 | 回归绿 | `cargo test -p pool-manager` | **172 passed / 0 failed** | ✅ |
| A11 | 旧默认值断言已随新默认修正 | **5** 条 | 严格度**未放宽** | ✅ |

## B. 部署闸门

| # | 验收项 | 读数 | 状态 |
|---|---|---|:---:|
| B1 | `pool-manager.exe` 已发布 | 构建 **2026-11-26T20:19:49Z** | ✅ |
| B2 | `manager-api.exe` 已发布 | 构建 **2026-11-26T20:24:40Z** | ✅ |
| B3 | 运行时加载新扩展 | `flashloan_rs.dll` **2026-11-26T20:23:54Z** | ✅ |
| B4 | 编排器已带新口径运行 | `supervisor_20261126T205604Z.jsonl` | ✅ |

## C. 诚实边界（**未闭合项**）

| # | 项 | 说明 | 状态 |
|---|---|---|:---:|
| C1 | `extension_library_contains_unanchored_tokens`（**6** 个） | **覆盖事实**，无工具可清 ⇒ 只带计数，**不驱动修复** | ⬜ 带计数上报 |
| C2 | `candidate_pairs_missing_top1_recommendation`（**18** 对） | 同上 | ⬜ 带计数上报 |
| C3 | 核心库模式的生产化 | 用户明示为**测试期配置**（`D-211` §1.1） | ⬜ 按用户口径保持 |

## 未闭合项（**`- [ ]` 口径** · 供 `recount_remaining_tasks_20261125.py` 逐目录计数）

- [ ] **C1** `extension_library_contains_unanchored_tokens`（**6** 个）：**覆盖事实**，无工具可清 ⇒ 只带计数，**不驱动修复**

- [ ] **C2** `candidate_pairs_missing_top1_recommendation`（**18** 对）：同上

- [ ] **C3** 核心库模式的生产化：用户明示为**测试期配置**（`D-211` §1.1）⇒ 按用户口径保持

