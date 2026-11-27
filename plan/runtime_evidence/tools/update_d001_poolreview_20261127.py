"""Register @T-池编.01@, @T-复.03@ and @T-编.02@ in the registry and the README.

The three tasks are the ones this round delivered: the core-library load
mode, the hourly review record files and the orchestrator convergence
scope.  Their code, their deployment and their live readings are done;
what was left is the bookkeeping.

The rows are long, so they are replaced through @mdpatch@ anchored on
**unique row prefixes** instead of a context based patch.  The script is
idempotent: a second run finds its own row and does nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdpatch import (  # noqa: E402
    NL,
    insert_after,
    insert_before,
    read,
    replace_row,
    write,
)


ROOT = Path(__file__).resolve().parents[3]
REGISTRY = ROOT / "plan" / "D-001-TASK_REGISTRY.md"
P01 = ROOT / "plan" / "tasks" / "P-01-池子重构"
README = P01 / "README.md"


# --------------------------------------------------------------- registry

REG_INDEX_FU_ROW = (
    "| **@复@** | @M-6@（复核层） | 2 | @T-复.02@、**@T-复.03@**"
    "（复核审计流按小时分文件 · **已实盘生效**） | **@T-复.04@** |"
)

REG_INDEX_BIAN_ROW = (
    "| **@编@** | @M-8@（编排层） | 2 | @T-编.01@、**@T-编.02@**"
    "（复核收敛口径 · 一轮复核耗时归因） | **@T-编.03@** |"
)

REG_INDEX_CHIBIAN_ROW = (
    "| **@池编@** | @M-1@（池管理器）＋ @M-8@（编排层） | 1 | "
    "**@T-池编.01@**（核心库加载模式 · **已实盘生效**） | **@T-池编.02@** |"
)

REG_TASK_CHIBIAN_ROW = (
    "| @T-池编.01@ | 核心库加载模式（测试期收敛闭环规模） | **2026-11-27** | "
    "@M-1@（@tools/pool_manager@ 加载模式唯一来源）＋ @M-8@（@scripts/mainnet_arbitrage_healer.py@） | "
    "**代码项全部闭合 · 已部署 · 实盘读数齐**。**用户口径**：「系统启动按照资金池加载方式，"
    "可以有选择的加载核心代币与股票代币、测试代币……为了便于测试，请把加载模式限制为**核心库**」"
    "⇒ 启动时资金库**只**加载核心库，降低闭环数量，便于确定问题。**落地**：加载模式只有"
    "**一个来源**（@TokenLoadMode::default_for_scope()@ ⇒ @Core@），审计跟随它、编排器把审计判定的"
    "口径传给它 ⇒ 同一 issue 只报一次（修复前 **6** 次 ⇒ **1** 次）；审计的 @busy_timeout@ 由 "
    "**5 s** 改 **60 s**（运行时正忙时不再误报）。**★ 实盘读数（编排器首轮周期 · "
    "@supervisor_20261126T205604Z.jsonl@）**：`coreScopeConfiguredLibraries = [\"core\"]`、"
    "`candidateScopeConfiguredLibraries = [\"core\"]`、`coreScopePolicyMatches = true`、"
    "`candidateScopePolicyMatches = true`、`candidateScopeOutOfPolicyPoolCount = **0**`、"
    "`invalidHopDirectionalReferenceCount = **0**`；闭环 **42 → 10** 条、跳 **126 → 30**；"
    "代币四库（core/stocks/extension/test）缺地址／缺位数／缺名称／缺符号**全为 0**。"
    "**部署闸门**：`pool-manager.exe` **2026-11-26T20:19:49Z**、`manager-api.exe` "
    "**2026-11-26T20:24:40Z**、`flashloan_rs.dll` **2026-11-26T20:23:54Z** ⇒ 新旧二进制**无中间态**。"
    "**回归**：`pool-manager` **172 passed / 0 failed**。**诚实边界**：核心库加载是**用户明示的"
    "测试期配置**（非生产策略）；基线审计剩 **2** 条**覆盖事实**（`extension_library_contains_unanchored_tokens` "
    "**6** 个、`candidate_pairs_missing_top1_recommendation` **18** 对）—— **无工具可清 ⇒ 只带计数、"
    "不驱动修复**；另有 **2** 条代币**身份**缺陷（@funding_address_mismatch@ · NET／DJT）待处置。"
    "文档：`D-211` / `D-212` / `D-213` |"
)

REG_TASK_FU_ROW = (
    "| @T-复.03@ | 复核审计流按小时分文件（日期目录与保留期清理不变） | **2026-11-27** | "
    "@M-6@（@tools/review_manager/src/records.rs@） | "
    "**代码项全部闭合 · 已部署 · 实盘读数齐**。**用户口径（两轮更正 · 最终口径）**：「复核数据太多，"
    "不便于分析，请复核管理器的工作方式，把按照日期存储文件的方式修改为**按照小时生成新文件库**"
    "（名字同样包括年月日），这样每个复核文件的大小都可控」；**更正**：「**按照小时存文件，不影响按照日期"
    "生成目录，因此，清理按照日期清理目录的功能不需要修改**，实现需要做调整」。**★ 落法**：小时**只进文件名**"
    "—— 文件名 `<干名>_<YYYYMMDD-HH>.jsonl`，**目录仍是日期目录** `<YYYY-MM-DD>`；因此保留期清理的"
    "**5 个函数**（@review_day_folder@ / @review_day_from_folder@ / @collect_day_folders@ / "
    "@review_day_is_removable@ / @purge_expired_review_files@）**一行未改** —— 它们本来就是**按日目录整目录删除**、"
    "**从不看文件名**。行归属小时由**行自己的时间戳**决定（@line_hour@）；写者句柄键 = "
    "`(天, 小时, 闭环)`；只保留**当前小时**的句柄。**★ 实盘读数**："
    "`flashloan/db/review_record_jsonl/robinhood/4663/2026-11-26/` 下 **10** 个 `…_20261126-20.jsonl`，"
    "单文件 **4 576 B – 17 373 B**（改前单文件 **190 305 B** 且**持续增长**）⇒ 每个文件的大小**可控**。"
    "旧命名的 **176** 个文件**不改名、不删除**，随**整日目录**在保留期到期时一起走。**回归**："
    "`review-manager` **175 passed / 0 failed**、`test_record_layer_guards.py` **11 passed**。"
    "**诚实边界**：「整日目录被删」的**实盘**读数未取（保留期 **30 天**，最新目录是今天 ⇒ 实盘未触发）"
    "⇒ 由**单测** @purge_expired_review_files@ 证明。文档：`D-214` / `D-215` / `D-216` |"
)

REG_TASK_BIAN_ROW = (
    "| @T-编.02@ | 复核收敛口径（当前闭环目录）＋ 一轮复核耗时归因 ＋ 存活探测误判修正 | **2026-11-27** | "
    "@M-8@（@scripts/mainnet_arbitrage_healer.py@） | "
    "**代码项全部闭合 · 已部署 · 实盘读数齐**。**★ 一轮复核慢的真因**：不是并发度 —— "
    "旧表 `cow_execution_reviews` 扫描 **18.2 s / 2.13 GB** ＋ `json.loads` **52.2 s**，一轮跑 **5–7** 次；"
    "`review_rows()` 改为「有管理器行即返回」（不再白扫旧表）⇒ **一轮 17 min 27 s → 7 min 27 s**。"
    "**★ 收敛永远 stalled 的真因是口径**：那 **19** 条缺值**全部**来自**已离开目录**的历史闭环；改按"
    "**当前目录**统计后（@current_catalog_loop_keys@，**读不到就不缩范围**）⇒ "
    "**@converged=true@、@stalled=false@、@remainingMissingValue=0@**，目录外的 **166** 条**单独计数上报**"
    "（`outsideCatalogReviews=166`、`outsideCatalogMissingValue=19`）。**★ 存活探测误判**：19:52:24 运行时"
    "正忙复核时 `/healthz` **5 s** 超时被判死并重启（PID **9704 → 3392 → 19492**）⇒ `api_get` 增加"
    "**异常分类**（`timeout`/`connect`/`http`/`io`）、超时返回「忙」而不是「死」、忙看门狗 **1800 s**。"
    "**★ 首轮实盘周期（@supervisor_20261126T205604Z.jsonl@ · 2026-11-26T21:03:57Z@）暴露并修掉三处口径缺陷**："
    "① **复核新鲜度读不到** —— Manager 只发布 `reviewedAt`、**不发布** `createdAt`，审计读 `created_at` ⇒ "
    "`latestReviewAt` 恒 `null` ⇒ 生产态恒 `review_stalled`（**新读** `reviewed_at` ⇒ 取**最大值**，不依赖行序）；"
    "② **级联层判定与审计互相矛盾** —— 级联读**整窗口**（`missingReviewValue=19`）而审计读**当前目录**"
    "（`valid=true`、`missingValue=0`）⇒ 级联改读 `currentLoopStats`；③ **稳定窗口的 `productionHealthy` 恒为假**"
    "—— 拿**状态串**和 `@healthy@` 比较，而状态串永远不是 `@healthy@` ⇒ 改读生产态**发布的布尔** `healthy`"
    "**并**要求状态串属于健康状态集 ⇒ **不放宽**任何门禁。**回归**：`test_mainnet_arbitrage_healer.py`、"
    "`test_mainnet_arbitrage_healer_repair_chain.py` 全绿（**0 failed**）。**诚实边界**："
    "@manager_v4_review_broadcast_requests@ = **0** 行、@manager_cow_execution_attempts@ = **0** 行 ⇒ "
    "「**持续产生链上广播交易记录**」**未达成**；缺口**不在**广播准入（闸门已开、私钥已配），"
    "**在上游没有一条正收益复核**（当前目录 **10/10** 全部 `not_executable` / `NoProfitableRoute()`，"
    "每条闭环已探 **7** 个额度 **0.001 – 0.053 ETH** ⇒ 亏损来自**价差本身 + 成本**）。"
    "文档：`D-217` / `D-218` / `D-219` |"
)

REG_DOC_ROWS = (
    "| @D-211@ | PLAN | P-01 T-池编.01 | @plan/tasks/P-01-池子重构/tasks/T-池编.01/D-211-PLAN.md@ |" + NL +
    "| @D-212@ | SCOPE_GUARD | P-01 T-池编.01 | @plan/tasks/P-01-池子重构/tasks/T-池编.01/D-212-SCOPE_GUARD.md@ |" + NL +
    "| @D-213@ | checklist | P-01 T-池编.01 | @plan/tasks/P-01-池子重构/tasks/T-池编.01/D-213-checklist.md@ |" + NL +
    "| @D-214@ | PLAN | P-01 T-复.03 | @plan/tasks/P-01-池子重构/tasks/T-复.03/D-214-PLAN.md@ |" + NL +
    "| @D-215@ | SCOPE_GUARD | P-01 T-复.03 | @plan/tasks/P-01-池子重构/tasks/T-复.03/D-215-SCOPE_GUARD.md@ |" + NL +
    "| @D-216@ | checklist | P-01 T-复.03 | @plan/tasks/P-01-池子重构/tasks/T-复.03/D-216-checklist.md@ |" + NL +
    "| @D-217@ | PLAN | P-01 T-编.02 | @plan/tasks/P-01-池子重构/tasks/T-编.02/D-217-PLAN.md@ |" + NL +
    "| @D-218@ | SCOPE_GUARD | P-01 T-编.02 | @plan/tasks/P-01-池子重构/tasks/T-编.02/D-218-SCOPE_GUARD.md@ |" + NL +
    "| @D-219@ | checklist | P-01 T-编.02 | @plan/tasks/P-01-池子重构/tasks/T-编.02/D-219-checklist.md@ |"
)

REG_MIGRATION_ROWS = (
    "| 2026-11-27 | **核心库加载模式（`T-池编.01` · `M-1` ＋ `M-8`）**：用户口径「系统启动按照资金池加载方式，"
    "可以有选择的加载核心代币与股票代币、测试代币……**为了便于测试，请把加载模式限制为核心库**」⇒ "
    "加载模式只有**一个来源**（`TokenLoadMode::default_for_scope()` ⇒ `Core`），审计跟随它、编排器把口径传给它 "
    "⇒ 同一 issue 只报一次（**6** ⇒ **1**）。**实盘**：闭环 **42 → 10**、跳 **126 → 30**，"
    "`coreScopeConfiguredLibraries = [\"core\"]`、`candidateScopeOutOfPolicyPoolCount = 0`、"
    "`invalidHopDirectionalReferenceCount = 0`；回归 `pool-manager` **172 / 0**。**诚实边界**：核心库加载是"
    "**用户明示的测试期配置**；基线审计剩 **2** 条**覆盖事实**（无工具可清 ⇒ 只带计数）。"
    "文档：`D-211` / `D-212` / `D-213` |" + NL +
    "| 2026-11-27 | **复核审计流按小时分文件（`T-复.03` · `M-6`）**：用户口径「复核数据太多……改为**按照小时生成"
    "新文件库**（名字同样包括年月日），这样每个复核文件的大小都可控」；**更正**「**按照小时存文件，不影响按照日期"
    "生成目录**，因此**清理按照日期清理目录的功能不需要修改**，实现需要做调整」⇒ 小时**只进文件名**、目录仍是"
    "日期目录 ⇒ **保留期清理 5 个函数一行未改**。**实盘**：**10** 个 `…_20261126-20.jsonl`、单文件 "
    "**4 576 B – 17 373 B**（改前 **190 305 B** 且持续增长）；回归 `review-manager` **175 / 0**、"
    "`test_record_layer_guards.py` **11**。文档：`D-214` / `D-215` / `D-216` |" + NL +
    "| 2026-11-27 | **复核收敛口径 ＋ 一轮复核耗时归因 ＋ 存活探测误判修正（`T-编.02` · `M-8`）**："
    "**★ 一轮慢的真因不是并发度** —— 旧表扫描 **18.2 s / 2.13 GB** ＋ 解析 **52.2 s**，一轮 **5–7** 次 "
    "⇒ 修完 **17 min 27 s → 7 min 27 s**；**★ 收敛永久 stalled 的真因是口径** —— 那 **19** 条缺值全部来自"
    "**已离开目录**的历史闭环 ⇒ 改按当前目录统计后 `converged=true` / `remainingMissingValue=0`，目录外的 **166** 条"
    "**单独计数上报**；**★ 首轮实盘周期暴露三处口径缺陷并修掉**：复核新鲜度读不到（Manager 只发布 `reviewedAt`）"
    "⇒ 生产态不再恒为 `review_stalled`；级联层改按当前目录判定（不再与审计互相矛盾）；稳定窗口的 "
    "`productionHealthy` 改读**发布的布尔**（不再恒为假）⇒ **不放宽**任何门禁。**诚实边界**：广播请求 **0** 行、"
    "执行记录 **0** 行 ⇒ 「持续产生链上广播交易记录」**未达成**（缺口在**上游没有正收益复核**，不在广播准入）。"
    "文档：`D-217` / `D-218` / `D-219` |"
)


# ------------------------------------------------------------------- P-01

P01_TASK_ROWS = (
    "| [@T-池编.01@](./tasks/T-池编.01/) | 核心库加载模式（测试期收敛闭环规模） | @AC-池编.01@ | "
    "@M-1@ ＋ @M-8@ | M-1 | ``W-5`` | **3** | "
    "⬜ **代码项全部闭合 · 已部署 · 实盘读数齐**（2026-11-27）：加载模式只有**一个来源**"
    "（`TokenLoadMode::default_for_scope()`），审计跟随它 ⇒ 同一 issue 只报一次（**6** ⇒ **1**）。"
    "**实盘**：闭环 **42 → 10**、跳 **126 → 30**；`coreScopeConfiguredLibraries = [\"core\"]`、"
    "`candidateScopeOutOfPolicyPoolCount = 0`、`invalidHopDirectionalReferenceCount = 0`；"
    "回归 `pool-manager` **172 / 0**。**剩 3 项** ＝ **2** 条**覆盖事实**（无工具可清 ⇒ 只带计数）"
    "＋ **核心库模式的生产化**（**用户明示的测试期配置**）⇒ **C 类 · 阻塞**。"
    "文档：`D-211` / `D-212` / `D-213` |" + NL +
    "| [@T-复.03@](./tasks/T-复.03/) | 复核审计流按小时分文件（日期目录与保留期清理不变） | @AC-复.03@ | "
    "@M-6@ | M-6 | ``W-4`` | **1** | "
    "⬜ **代码项全部闭合 · 已部署 · 实盘读数齐**（2026-11-27）：小时**只进文件名**、**目录仍是日期目录** "
    "⇒ **保留期清理 5 个函数一行未改**。**实盘**：**10** 个 `…_20261126-20.jsonl`、单文件 "
    "**4 576 B – 17 373 B**（改前 **190 305 B** 且持续增长）；回归 `review-manager` **175 / 0**、"
    "`test_record_layer_guards.py` **11**。**剩 1 项** ＝ 「整日目录被删」的**实盘**读数（保留期 **30 天** ⇒ "
    "实盘未触发 ⇒ 由**单测**证明）⇒ **C 类 · 阻塞**。文档：`D-214` / `D-215` / `D-216` |" + NL +
    "| [@T-编.02@](./tasks/T-编.02/) | 复核收敛口径（当前闭环目录）＋ 一轮复核耗时归因 ＋ 存活探测误判修正 | "
    "@AC-编.02@ | @M-8@ | M-6 ＋ M-13 | ``W-5`` | **1** | "
    "⬜ **代码项全部闭合 · 已部署 · 实盘读数齐**（2026-11-27）：**一轮慢的真因不是并发度** "
    "（旧表 **18.2 s / 2.13 GB** ⇒ 一轮 **17 min 27 s → 7 min 27 s**）；**收敛永久 stalled 的真因是口径** "
    "（那 **19** 条缺值全来自**已离开目录**的历史闭环 ⇒ `converged=true` / `remainingMissingValue=0`，"
    "目录外 **166** 条**单独计数上报**）；**首轮实盘周期暴露三处口径缺陷并修掉**（新鲜度读不到、级联层读整窗口、"
    "`productionHealthy` 恒为假）⇒ **不放宽**任何门禁。**剩 1 项** ＝ 「**持续产生链上广播交易记录**」"
    "（广播请求 **0** 行 ⇒ **市场条件**）⇒ **C 类 · 阻塞**。文档：`D-217` / `D-218` / `D-219` |"
)

P01_AGGREGATE = (
    "**合计未完成验收项**：**56**（二级任务 · **逐目录实测**，2026-11-27 05:4x · **24** 个在盘目录逐个读 "
    "@*-checklist.md@；本轮新登记 @T-池编.01@ **3** 项、@T-复.03@ **1** 项、@T-编.02@ **1** 项）"
    "＋ **8**（一级特有）＋ **0**（实盘读数指针 · **4 项已闭合**）＋ **51**（横切）＝ **115**"
)

P01_NOTE = (
    "> **本轮（2026-11-27 05:4x · 核心库加载模式 ＋ 复核审计流按小时分文件 ＋ 复核收敛口径 · "
    "`T-池编.01` / `T-复.03` / `T-编.02`）**：",
    "> ① **核心库加载模式**（`T-池编.01`）：加载模式只有**一个来源**，审计跟随它 ⇒ 同一 issue 只报一次；",
    "> **实盘**闭环 **42 → 10**、跳 **126 → 30**，越界池 **0**、失效引用 **0**；",
    "> ② **复核审计流按小时分文件**（`T-复.03`）：小时**只进文件名**、**目录仍是日期目录** ⇒ "
    "> **保留期清理一行未改**；**实盘**单文件 **4 576 B – 17 373 B**（改前 **190 305 B** 且持续增长）；",
    "> ③ **复核收敛口径**（`T-编.02`）：**一轮慢的真因不是并发度**（旧表 **18.2 s / 2.13 GB** ⇒ "
    "> **17 min 27 s → 7 min 27 s**）；**收敛永久 stalled 的真因是口径** ⇒ `converged=true`、"
    "> `remainingMissingValue=0`，目录外的 **166** 条**单独计数上报**；",
    "> ④ **首轮实盘周期暴露的三处口径缺陷已修**（`T-编.02`）：复核新鲜度读不到（Manager 只发布 `reviewedAt`）、"
    "> 级联层读整窗口、`productionHealthy` 恒为假 ⇒ **不放宽**任何门禁；",
    "> ⑤ **诚实边界**：广播请求 **0** 行、执行记录 **0** 行 ⇒ 「持续产生链上广播交易记录」**未达成** "
    "> （缺口在**上游没有一条正收益复核**，**不在**广播准入）；**未碰 git**；",
    "> ⑥ **未完成项复算**：逐目录实测 ⇒ 二级 **51 → 56**、合计 **110 → 115**（新登记 **5** 项）；",
    ">",
)


def replace_line(text: str, prefix: str, new_line: str, label: str) -> str:
    """Replace the single line that starts with prefix."""
    lines = text.split(NL)
    hits = [i for i, line in enumerate(lines) if line.startswith(prefix)]
    if len(hits) != 1:
        raise SystemExit(
            "update_d001_poolreview: line anchor count %d for %s"
            % (len(hits), label)
        )
    lines[hits[0]] = new_line.replace("@", chr(96))
    return NL.join(lines)


def main() -> None:
    text = read(REGISTRY)
    if "D-219" in text:
        print("update_d001_poolreview: already registered")
        return

    text = replace_row(text, "| **@复@** |", REG_INDEX_FU_ROW, "registry 复 index row")
    text = replace_row(text, "| **@编@** |", REG_INDEX_BIAN_ROW, "registry 编 index row")
    text = insert_after(text, "| **@池复@** |", REG_INDEX_CHIBIAN_ROW,
                        "registry 池编 index row")
    text = insert_after(text, "| @T-广核.02@ |",
                        REG_TASK_CHIBIAN_ROW + NL + REG_TASK_FU_ROW + NL
                        + REG_TASK_BIAN_ROW,
                        "registry task rows")
    doc_anchor = "| @D-210@ |" if "| @D-210@ |" in text else "| @D-206@ |"
    text = insert_after(text, doc_anchor, REG_DOC_ROWS, "registry doc rows")
    text = insert_before(
        text,
        "> **重编不改范围、不改验收、不改测试。**",
        REG_MIGRATION_ROWS + NL,
        "registry migration rows",
    )
    write(REGISTRY, text)

    text = read(README)
    text = insert_after(text, "| [@T-广核.02@](./tasks/T-广核.02/) |", P01_TASK_ROWS,
                        "P-01 task rows")
    text = replace_line(text, "**合计未完成验收项**", P01_AGGREGATE,
                        "P-01 aggregate line")
    text = insert_before(text, "> **本轮（2026-11-27 00:0x", NL.join(P01_NOTE),
                        "P-01 round note")
    write(README, text)
    print("update_d001_poolreview: ok")


if __name__ == "__main__":
    main()
