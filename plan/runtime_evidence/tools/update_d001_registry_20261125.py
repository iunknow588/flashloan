"""Bring the task registry (D-001) in line with the tasks on disk.

Four things were stale:

* the number-index table still described the pre-expansion module sets of
  T-方储.01 and T-复广.02, and the new snapshot-consistency task
  (T-池复.01) was not registered at all;
* five closed tasks were missing from the closed-task table while two of
  them were still listed as "partially closed";
* the document table stopped at D-125 although D-126..D-135 exist;
* the change log had lost its table header.
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
    restore,
    write,
)

REGISTRY = Path(__file__).resolve().parents[3] / "plan" / "D-001-TASK_REGISTRY.md"


def main() -> None:
    text = read(REGISTRY)

    # --- section 4.2: the number-index table ------------------------------
    text = replace_row(
        text,
        "| **@方储@** |",
        "| **@方储@** | @M-1@（池管理器）＋ @M-3@（方向层）＋ @M-5@（候选层）＋ @M-9@（授权与回滚）＋ "
        "@M-10@（存储层）<br>（**阶段 2 扩范围**，见 @D-073@ §1） | 1 | @T-方储.01@ | **@T-方储.02@** |",
        "方储 index row",
    )
    text = replace_row(
        text,
        "| **@复广@** |",
        "| **@复广@** | @M-6@（复核层）＋ @M-7@（广播层） | 2 | "
        "@T-复广.01@、@T-复广.02@ | **@T-复广.03@** |",
        "复广 index row",
    )
    text = insert_before(
        text,
        "| **@接@** |",
        "| **@池复@** | @M-1@（池管理器）＋ @M-6@（复核层） | 1 | @T-池复.01@ | **@T-池复.02@** |",
        "池复 index row",
    )

    # --- section 4.2.1: the closed tasks ----------------------------------
    closed_rows = [
        "| @T-方.01@ | 方向层投影不变式守卫（I-9 / I-7 / I-12） | **2026-11-25** | @M-3@ | "
        "**16** 项全闭合；@DirectionalPoolComparison@ **新增** @base_pool_mismatches@ / "
        "@rows_without_base_pool@ / @projected_base_pool_count@ / @base_fact_differences@（逐列比对 "
        "**9** 列基础事实）＋ @ready_for_cutover@ **只加严**（**未放宽任何门禁**）；回归 "
        "@directional-pool-manager@ **25**、@manager-api@ **33**；**实盘只读审计零失败项**"
        "（方向池 **692** 行、I-9 不一致 **0**、I-7 **0**、I-12 **0**）；**运行期生效复测**已由 "
        "**08:27:19** 重启后审计闭合（@directional_projection_audit_after_restart_20261125.json@）。"
        "**已归档** |",
        "| @T-闭.01@ | 方向层主序接管闭环主池 | **2026-11-25** | @M-4@ | "
        "**23** 项全闭合；@order_hop_candidates_by_direction_rank@ ＋ 回滚开关 "
        "@FLASHLOAN_CLOSED_LOOP_DIRECTIONAL_PRIMARY@（**缺省 ON**）；**影子复算**：闭环 **174 → 174**"
        "（新增 **0** / 移除 **0**）、跳 **522 → 522**、**候选成员集逐跳完全一致** ⇒ 纯置换；"
        "**实盘生效复测**完成（@closed_loop_version@ **19 → 20**、@pool_selection_basis@ **522 / 522**）；"
        "回归 @closed-loop-manager@ **46 passed / 0 failed**。**已归档** |",
        "| @T-证候储.01@ | 「最近一周有交易」数据源 | **2026-11-25** | @M-2@ ＋ @M-5@ ＋ @M-10@ | "
        "**6** 项全闭合；窗口常量 **60 天**、**标记与深度测量相互独立**；活动表 DDL 由 @include_str!@ "
        "**编译进 Rust 扩展** ⇒ 证据通道（@M-2@）**必须**列进回归范围；受影响 **7** 文件 "
        "**77 passed / 0 failed**；窄窗命中 **19** 个库内池 ⇒ **漏报 0**。**已归档** |",
        "| @T-复.02@ | 主网复核闭环身份读数 | **2026-11-25** | @M-6@ | "
        "**6** 项全闭合（**未完成验收项 = 0**）；四列**全部非空**、生产库只读交叉验证成立"
        "（读数见 @D-127@）。**已归档** |",
        "| @T-广.01@ | 广播门禁与错误自愈 | **2026-11-25** | @M-7@ | "
        "**4** 项全闭合；**8** 项里 **7** 项逐项一致且有实测读数；**7 / 7** 用例经**真实 "
        "@manager-api@ 二进制 ＋ HTTP** 走通；回归 @broadcast-manager@ "
        "**32 passed / 0 failed / 0 ignored**（@D-128@ §7）。**已归档** |",
    ]
    text = insert_before(
        text,
        "| @T-复广.02@ | 广播准入门槛与审计留痕 |",
        NL.join(closed_rows),
        "closed task rows",
    )

    # --- section 4.2.2: the partially closed tasks ------------------------
    # T-闭.01 and T-方.01 were closed and archived on 2026-11-25; their rows
    # must not stay in the "partially closed" table.
    lines = text.split(NL)
    kept = []
    removed = []
    in_partial = False
    for line in lines:
        if line.startswith("### 4.2.2"):
            in_partial = True
        elif line.startswith("## 5.") or line.startswith("## 4.3"):
            in_partial = False
        if in_partial and line.startswith(restore("| @T-")) and (
            line.startswith(restore("| @T-闭.01@"))
            or line.startswith(restore("| @T-方.01@"))
        ):
            removed.append(line)
            continue
        kept.append(line)
    if len(removed) != 2:
        raise SystemExit("expected two partially closed rows, got %d" % len(removed))
    text = NL.join(kept)

    # --- section 5.5: the document table ---------------------------------
    documents = [
        "| @D-126@ | 第 6/7 层判据读数与 @recent_swap_active@ 接线实测 | P-01 T-证候储.01 | "
        "@plan/archive/任务归档_T-证候储.01_「最近一周有交易」数据源_20261125.md#d126@ |",
        "| @D-127@ | 主网复核闭环身份四列读数 | P-01 T-复.02 | "
        "@plan/archive/任务归档_T-复.02_主网复核闭环身份读数_20261125.md#d127@ |",
        "| @D-128@ | 广播门禁与错误自愈实测读数 | P-01 T-广.01 | "
        "@plan/archive/任务归档_T-广.01_广播门禁与错误自愈_20261125.md#d128@ |",
        "| @D-129@ | 第 4 环接线与四类拒绝路径闭合实测 | P-01 T-复广.01 | "
        "@plan/tasks/P-01-池子重构/tasks/T-复广.01/docs/D-129-第4环接线与四类拒绝路径闭合实测_20261125.md@ |",
        "| @D-130@ | 复核完整性最新代次读数与剩余项阻塞登记 | P-01 T-复编.02 | "
        "@plan/tasks/P-01-池子重构/tasks/T-复编.02/docs/D-130-复核完整性最新代次读数与剩余项阻塞登记_20261125.md@ |",
        "| @D-131@ | 阶段 2 前置状态复核与施工清单 | P-01 T-方储.01 | "
        "@plan/tasks/P-01-池子重构/tasks/T-方储.01/docs/D-131-阶段2前置状态复核与施工清单_20261125.md@ |",
        "| @D-132@ | PLAN | P-01 T-池复.01 | "
        "@plan/tasks/P-01-池子重构/tasks/T-池复.01/D-132-PLAN.md@ |",
        "| @D-133@ | SCOPE_GUARD | P-01 T-池复.01 | "
        "@plan/tasks/P-01-池子重构/tasks/T-池复.01/D-133-SCOPE_GUARD.md@ |",
        "| @D-134@ | checklist | P-01 T-池复.01 | "
        "@plan/tasks/P-01-池子重构/tasks/T-池复.01/D-134-checklist.md@ |",
        "| @D-135@ | 单 tick 门禁版本漂移根因与实测 | P-01 T-池复.01 | "
        "@plan/tasks/P-01-池子重构/tasks/T-池复.01/docs/D-135-单tick门禁版本漂移根因与实测_20261125.md@ |",
    ]
    text = insert_after(
        text,
        "| @D-125@ |",
        NL.join(documents),
        "document table",
    )

    write(REGISTRY, text)
    print("D-001 registry updated")


if __name__ == "__main__":
    main()
