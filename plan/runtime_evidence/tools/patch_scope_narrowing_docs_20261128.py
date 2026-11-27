#!/usr/bin/env python3
'''Correct the plan documents after the scope-narrowing defect was found.

The previous round concluded that the pools of the 19 value-less reviews
had **left the market catalog**.  A read-only probe proved the opposite:
every one of them is still in the master pool table, and what was missing
was the **materialized scope**.  The documents that carried the wrong
conclusion are corrected here, and the new task is registered.

The edits go through @mdpatch.py@, the repository's own helper for the plan
documents: it keeps the CRLF line endings, it restores the backticks from
the @ placeholder, and it refuses to patch when an anchor is missing or
ambiguous.
'''

from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parents[2]
sys.path.insert(0, str(TOOLS))

import mdpatch  # noqa: E402 - the helper lives next to this script

TASKS = REPO / "plan" / "tasks" / "P-01-池子重构"


def edit(path: Path, edits: list) -> None:
    text = mdpatch.read(path)
    for kind, args in edits:
        if kind == "replace_once":
            text = mdpatch.replace_once(text, args[0], args[1], args[2])
        elif kind == "replace_row":
            text = mdpatch.replace_row(text, args[0], args[1], args[2])
        elif kind == "insert_before":
            text = mdpatch.insert_before(text, args[0], args[1], args[2])
        elif kind == "insert_after":
            text = mdpatch.insert_after(text, args[0], args[1], args[2])
        else:
            raise SystemExit("patch_scope_narrowing_docs: unknown edit " + kind)
    mdpatch.write(path, text)
    print("patched " + str(path.relative_to(REPO)))


HISTORY_ROW = '''| 2026-11-28 | **作用域加载模式不得收窄 ＋ 上一轮结论的推翻与更正（@T-池编.02@ · @M-1@ ＋ @M-8@）**：**用户口径**「**系统启动状态为核心库加股票库加测试库的状态，所有池子都是正常能用的，改为资金池启动只带核心库，池子就缺失了……修改资金池启动模式，不应该修改现有资金池的数据，所有的池子都不会变化，只是启动加载到内存中的池子变少，但是绝对不会缺池子**」。**★ 上一轮结论被推翻**：原「19 条目录外复核 **0 / 19** 可复价、其跳的池**已从市场目录消失**」**是错的** —— 只读探针 @probe_scope_narrowing_20261128.py@ 证明：去重 **15** 个池 hash **全部仍在主池表 @v4_exchange_pools@（2550 行）**、**19 / 19** 条路由的**每一跳**都还在池表 ⇒ **池从未消失，缺的是作用域的物化范围**；旧探针**只查作用域表**是错因。**缺陷链三处**：@scope.rs@ 的 @default_for_scope()@ **默认比声明更窄**、@baseline_audit.rs@ 的策略判定用**精确相等**（把正常的宽作用域判成 stale）、@mainnet_arbitrage_healer.py@ 的 @AUDITED_SCOPE_TOKEN_LOAD_MODE = "core"@ **硬编码**并 @apply=True@ 写进持久化作用域。**台账时间线**（@manager_write_requests@）：11-18 22:50／11-19 00:10 ＝ @["core","stocks","test"]@（**用户所述可用状态**）→ **11-26 19:29 healer 写入 @["core"]@（@deletedRows=14@）** ⇒ **缺陷发生点**；19 条复核写于 **19:06–19:11**，早 **18** 分钟 ⇒ **被收窄孤儿化**。**修复**：默认按**作用域声明**取值（**默认永不窄于声明**）、审计判定改为「**声明 ⊆ 已配置**」（宽于声明**不算** stale）、healer **不再自带模式**并加**收窄守卫**。**回归**：@pool-manager@ **175 / 0**、healer **126 / 0**、repair-chain **44 / 0**。**部署与恢复**：release 重建 @pool-manager@ ＋ @manager-api@ ⇒ 重启 manager-api 与 healer ⇒ 两个作用域由 @["core"]@ 恢复为 @["core","stocks"]@（core4 **414 → 428**，**删 0 加 14**）⇒ 级联重建（candidateEdge 52 → candidateExchange 216 → directionalPools → closedLoops 8 闭环 / 24 跳）⇒ 审计两条 @*_scope_library_policy_stale@ **消失**、@candidateScopeOutOfPolicyPoolCount = 0@。**诚实边界**：剩下的 **9** 个池被**深度判据**挡住（@depth_status = 'unavailable'@ ＝ **测量缺失**，非池坏）⇒ 交池子工具**深度重测**，**能否复价不预判**；**不伪造任何盈亏**、不填 0、不放宽门禁、不删历史行。文档：@D-239@ / @D-240@ / @D-241@ ＋ @docs/D-242@ |'''


def main() -> int:
    # ------------------------------------------------------------------
    # D-001: the change history gains this round.
    # ------------------------------------------------------------------
    edit(REPO / "plan" / "D-001-TASK_REGISTRY.md", [
        ("insert_after", (
            "| 2026-11-28 | **任务完成度分析",
            HISTORY_ROW,
            "d001-history",
        )),
    ])

    # ------------------------------------------------------------------
    # D-234: the plan of the task whose premise was wrong.
    # ------------------------------------------------------------------
    edit(TASKS / "tasks" / "T-复编储.01" / "D-234-PLAN.md", [
        ("replace_once", (
            "# D-234 @T-复编储.01@｜目录外复核缺值的收口：**退役语义** · 开发计划（**独立 · 自包含**）",
            "# D-234 @T-复编储.01@｜目录外复核缺值的收口：**先复价，退役降为兜底** · 开发计划（**独立 · 自包含**）",
            "d234-title",
        )),
        ("replace_once", (
            "| **标题** | 目录外复核缺值的收口：**退役语义**（**不是**补值）—— 让「已离开目录的闭环」其复核行给出**明确结论**，而不是永远挂着「缺值」 |",
            "| **标题** | 目录外复核缺值的收口：**先复价，退役降为兜底**（**不是**补值）—— 让那 19 条复核行**重新可定价**；只有恢复作用域后**仍不可定价**的行才给「明确退役结论」 |",
            "d234-heading-row",
        )),
        ("replace_once", (
            "| 把池子**重新塞回**作用域去凑复价 | 那 15 个池中 **12** 个已从市场目录消失；把它们放回去＝**造市场** |",
            "| 把池子**重新塞回**作用域去凑复价 | **更正（2026-11-28）**：那 15 个池**全部仍在主池表**（@v4_exchange_pools@，2550 行）⇒ 把它们纳回作用域**不是造市场**，而是**恢复被误收窄的物化范围**；真正的越界是**收窄**（删掉已在作用域里的池） |",
            "d234-scope-row",
        )),
        ("replace_once", (
            "### 3.2 为什么**补不上**（**这是本任务最关键的读数**）",
            "### 3.2 为什么**补不上**（**★ 本节已于 2026-11-28 更正 —— 下面是被推翻的原读数**）",
            "d234-s32-heading",
        )),
        ("replace_once", (
            "> **一句话**：那 19 条路由**每一跳都至少有一跳的池子已经不在市场目录里** ⇒\n> **要复价就必须把已下架的池子重新塞回作用域** ⇒ 那是**造市场**，不是修复核。",
            "> **★ 原结论（已推翻）**：那 19 条路由**每一跳都至少有一跳的池子已经不在市场目录里** ⇒\n> **要复价就必须把已下架的池子重新塞回作用域** ⇒ 那是**造市场**，不是修复核。\n>\n> **更正**：原探针 @probe_departed_review_value_20261128.py@ **只查作用域表**、**从不查主池表** ⇒\n> 把「不再被作用域物化」误读成「已从市场目录消失」。实测（@probe_scope_narrowing_20261128.py@）：\n> **15 / 15** 个池 hash **全部仍在 @v4_exchange_pools@**，**19 / 19** 条路由的**每一跳**都还在池表。\n> **根因是作用域收窄**（2026-11-26 19:29 healer 写入 @core@），**不是**池消失 ⇒ 详见 @docs/D-242@。",
            "d234-s32-quote",
        )),
        ("replace_once", (
            "| 唯一说谎的地方 | 库状态把 **19** 条已退役行算成「**缺值**」；PnL 窗口也把它们算成 @missingValue = 19@ | ❌ **本任务修的就是这个** |",
            "| 唯一说谎的地方 | 库状态把 **19** 条**目录外**行算成「**缺值**」；PnL 窗口也把它们算成 `missingValue = 19` | ❌ **本任务修的就是这个**（**更正**：它们不是「已退役」，是**被作用域收窄孤儿化**的合法路由 ⇒ **先复价**） |",
            "d234-s33-row",
        )),
    ])

    # ------------------------------------------------------------------
    # D-235: the boundary of the task.
    # ------------------------------------------------------------------
    edit(TASKS / "tasks" / "T-复编储.01" / "D-235-SCOPE_GUARD.md", [
        ("insert_after", (
            "| 把**退役**与**缺值**分开计数 | 把退役行**删掉**（抹掉历史） |",
            "| **先复价**，退役只作兜底（**更正 2026-11-28**） | 把**仍可复价**的行提前判成「退役」（那是**放弃真实读数**） |",
            "d235-boundary-row",
        )),
        ("insert_before", (
            "1. **不把缺值说成 0**",
            "0. **不把「可复价」说成「不可复价」**（**更正 2026-11-28**）：那 19 条路由的池**全部仍在主池表**，\n"
            "   它们**缺的是作用域的物化范围** ⇒ 先由 @T-池编.02@ **恢复作用域**让它们重新可定价，\n"
            "   **退役**只对恢复后**仍不可定价**的行使用。",
            "d235-item0",
        )),
    ])

    # ------------------------------------------------------------------
    # D-236: the acceptance boxes of the task.
    # ------------------------------------------------------------------
    edit(TASKS / "tasks" / "T-复编储.01" / "D-236-checklist.md", [
        ("replace_once", (
            "| A4 | **可复价性**（关键读数） | 逐条闭环看 3 跳的池**是否还在池目录里** | **0 / 19** 条是 **3 / 3**（**16** 条 **1 / 3**、**3** 条 **0 / 3**）；19 条引用的 **28** 个池 id 在当前候选作用域里 **0** 命中 | ✅ |",
            "| A4 | **可复价性**（关键读数 · **★ 2026-11-28 更正**） | 逐条闭环看 3 跳的池**是否还在主池表** @v4_exchange_pools@ 里 | **19 / 19** 条的**每一跳**都还在池表（去重 **15** 个池 hash **全部仍在**，主池表 2550 行）⇒ **可复价**；被作用域覆盖的只有 **6 / 15** ⇒ 缺的是**作用域的物化范围**，**不是**池消失 | ✅ |",
            "d236-a4",
        )),
        ("replace_once", (
            "| A5 | 系统**今天没有填 0** | @signed_pnl_amount@ 留空 ＋ 写明缺值诊断 | **未填 0** ⇒ 今天的行为**正确**，**必须保留** | ✅ |",
            "| A5 | 系统**今天没有填 0** | @signed_pnl_amount@ 留空 ＋ 写明缺值诊断 | **未填 0** ⇒ 今天的行为**正确**，**必须保留**。**★ 更正**：这 19 条**不是**「测了不赚」，而是**被作用域收窄孤儿化** ⇒ 收口是**恢复作用域让它们复价**，**不是**填数、**也不是**提前退役 | ✅ |",
            "d236-a5",
        )),
        ("replace_once", (
            "## B. 代码项（**未实现**）",
            "## B. 代码项（**未实现** · **退役语义降为兜底**）",
            "d236-b-heading",
        )),
        ("replace_once", (
            "- [ ] **D1** **「每一个复核都有明确的盈亏数值」**在**目录外**不可达成：那 19 条的跳池已从市场目录消失（**0 / 19** 可复价）⇒ **填数即伪造** ⇒ 收口口径只能是「**给出明确退役结论**」；本框**不代为关闭**",
            "- [ ] **D1** **「每一个复核都有明确的盈亏数值」**在**目录外**能否达成**取决于恢复作用域后的深度重测**：那 19 条的跳池**全部仍在主池表**（**19 / 19** 可复价）⇒ **填数仍是伪造**，但**也不必**提前判退役 ⇒ 口径＝**先复价，退役兜底**；剩下 **9** 个池当前被**深度判据**挡住（@depth_status = 'unavailable'@ ＝ 测量缺失）⇒ 交池子工具重测，**不预判**；本框**不代为关闭**",
            "d236-d1",
        )),
    ])

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
