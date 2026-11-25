"""Bring the P-01 decomposition table (D-002 section 2.1) up to date.

The table still listed 19 secondary tasks while 22 are registered: the
two guard tasks (T-方.01, T-闭.01) and the new snapshot-consistency task
(T-池复.01) were never added, and most of the statuses were still the
initial placeholder.

Every row is replaced by its leading marker, so the edit is independent of
the line endings and of the surrounding table.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdpatch import NL, insert_before, read, replace_row, write  # noqa: E402

PLAN = (
    Path(__file__).resolve().parents[3]
    / "plan"
    / "tasks"
    / "P-01-池子重构"
    / "D-002-PLAN.md"
)

ROWS = [
    (
        "| @T-方授储.01@ |",
        "| @T-方授储.01@ | 方向层落地 | @AC-方授储.01@ | @M-3@ ＋ @M-9@ ＋ @M-10@ | — | @W-0@ | "
        "✅ **已闭合 · 已归档**（2026-11-24，**32** 项全闭合，方向池实盘落库 **352** 行） |",
    ),
    (
        "| @T-方储.01@ |",
        "| @T-方储.01@ | 旧方向键与旧排序降级下线 | @AC-方储.01@ | @M-3@ ＋ @M-10@（**阶段 2 扩范围**：@M-5@ ＋ @M-1@ ＋ @M-9@，"
        "见 @D-073@ §4「先扩后跑」） | M-4 | @W-4@ | "
        "🔄 **阶段 1 已闭合 3 项**（消费者清单 / 最后读取时间戳 / 逐列决策）；**剩 5 项全部属阶段 2**（删除动作） |",
    ),
    (
        "| @T-方闭储.01@ |",
        "| @T-方闭储.01@ | 闭环层切换到方向池 | @AC-方闭储.01@ | @M-4@ ＋ @M-3@ ＋ @M-10@ | M-3 | @W-1@ | "
        "✅ **已闭合 · 已归档**（2026-11-24，**38** 项全闭合，实盘已切到方向池） |",
    ),
    (
        "| @T-方闭.01@ |",
        "| @T-方闭.01@ | 备用池接线 | @AC-方闭.01@ | @M-4@ ＋ @M-3@ | M-3 | @W-1@ | "
        "✅ **已闭合 · 已归档**（2026-11-24，**20** 项全闭合，实盘已接线） |",
    ),
    (
        "| @T-证候储.01@ |",
        "| @T-证候储.01@ | 「最近一周有交易」数据源 | @AC-证候储.01@ | @M-2@ ＋ @M-5@ ＋ @M-10@ | — | @W-6@ | "
        "✅ **已闭合 · 已归档**（2026-11-25，**6** 项全闭合） |",
    ),
    (
        "| @T-候.01@ |",
        "| @T-候.01@ | USDG 估值单位与覆盖 | @AC-候.01@ | @M-5@ | M-5 | @W-6@ | "
        "🔄 主网侧已闭合；**剩 1 项** ＝ 测试网 @loops>0@ ⏸（测试网 Manager 未发布闭环 ⇒ **未测量**） |",
    ),
    (
        "| @T-候.02@ |",
        "| @T-候.02@ | 实盘锚点字段核验（收尾） | @AC-候.02@ | @M-5@ | M-7 | @W-6@ | "
        "✅ **已闭合 · 已归档**（2026-11-24，**4** 项全闭合） |",
    ),
    (
        "| @T-复编.01@ |",
        "| @T-复编.01@ | Rust 生产侧复核发布链路 | @AC-复编.01@ | @M-6@ ＋ @M-8@ | — | @W-2@ | "
        "✅ **已闭合 · 已归档**（2026-11-24，**29** 项全闭合，发布链路已交付） |",
    ),
    (
        "| @T-复编.02@ |",
        "| @T-复编.02@ | Review 金额与利润完整性 | @AC-复编.02@ | @M-6@ ＋ @M-8@（**扩范围 · 事后补记**） | M-6 | @W-2@ | "
        "🔄 **23** 项闭合、**7** 项剩 —— 残项**全部**是复核层金额 / 容量证据缺口与广播层口径 ⇒ 归 @M-6@ / @M-7@ |",
    ),
    (
        "| @T-复.02@ |",
        "| @T-复.02@ | 主网复核闭环身份读数 | @AC-复.02@ | @M-6@ | M-6 | @W-2@ | "
        "✅ **已闭合 · 已归档**（2026-11-25，**6** 项全闭合） |",
    ),
    (
        "| @T-复广.01@ |",
        "| @T-复广.01@ | 主网持续套利广播产出 | @AC-复广.01@ | @M-7@ ＋ @M-6@ | M-6 | @W-3@ | "
        "🔄 **第 1–4 环已闭合**（生成谓词 / @loop_key@ 精确关联 / **不**广播 / dry-run 零写入 ＋ 四类拒绝路径留证）；"
        "**剩 7 项全部需要「实盘真实广播」** ⇒ **市场条件** |",
    ),
    (
        "| @T-广.01@ |",
        "| @T-广.01@ | 广播门禁与错误自愈 | @AC-广.01@ | @M-7@ | M-7 | @W-3@ | "
        "✅ **已闭合 · 已归档**（2026-11-25，**4** 项全闭合） |",
    ),
    (
        "| @T-复广.02@ |",
        "| @T-复广.02@ | 广播准入门槛与审计留痕 | @AC-复广.02@ | @M-6@ ＋ @M-7@ ＋ @M-12@ | — | @W-3@ | "
        "✅ **已闭合 · 已归档**（2026-11-25，@BC-1..BC-5@ 全部落地，**13** 项闭合 ＋ **1** 项「未测量」） |",
    ),
    (
        "| @T-编.01@ |",
        "| @T-编.01@ | 双链准入与稳定窗口 | @AC-编.01@ | @M-8@ | M-7 | @W-5@ | "
        "🔄 **剩 3 项** ＝ ① **主网窗口**（三个连续 ≥30 分钟窗口，每个 @blockers=[]@、@missingValue=0@、@positive>0@ 且回执可关联）"
        "② **窗口证据包** ③ **测试网同构验收**（⏸ 随测试网任务暂停）⇒ **市场条件** |",
    ),
    (
        "| @T-候编储.01@ |",
        "| @T-候编储.01@ | 测试网候选池与闭环恢复 | @AC-候编储.01@ | @M-5@ ＋ @M-8@ ＋ @M-10@ | — | ⏸ | "
        "⏸ **整项暂停**（**4** 项；五步有序重建序列已备好，**不删除、不改判为完成**） |",
    ),
    (
        "| @T-储.01@ |",
        "| @T-储.01@ | 测试套件生产库零写入 | @AC-储.01@ | @M-10@ | — | @W-4@ | "
        "✅ **已闭合 · 已归档**（2026-11-25，**11** 项全闭合，三层守卫落地） |",
    ),
    (
        "| @T-储.02@ |",
        "| @T-储.02@ | 记录存储剥离（剩余项） | @AC-储.02@ | @M-10@ | — | @W-4@ | "
        "⏸ **裁定后置** —— **功能范围已全部落地**（复核记录 JSONL ＋ 广播交易月库 SQLite，均已实盘落库）；"
        "**剩 6 项全部是故障注入矩阵 @F-1@…@F-10@** ⇒ **不计入待办**（见 @D-136@ §2.4 C 类） |",
    ),
    (
        "| @T-储.03@ |",
        "| @T-储.03@ | 复核库分离（复核记录独立成库） | @AC-储.03@ | @M-10@ | — | @W-4@ | "
        "✅ **已闭合 · 已归档**（2026-11-24，**9** 项全闭合） |",
    ),
    (
        "| @T-储.04@ |",
        "| @T-储.04@ | 广播交易记录分离（交易记录独立成库） | @AC-储.04@ | @M-10@ | — | @W-4@ | "
        "✅ **已闭合 · 已归档**（2026-11-24，**9** 项全闭合） |",
    ),
]

NEW_ROWS = [
    (
        "| @T-池复.01@ |",
        "| @T-池复.01@ | 单 tick 门禁的快照一致性判定 | @AC-池复.01@ | @M-1@ ＋ @M-6@ | — | @W-2@ | "
        "🔄 **代码已落地 ＋ 回归全绿**（@pool-manager@ **167**、@review-manager@ **161**、@rs-src@ **220**）；"
        "**剩 3 项全部是实盘复测**（读数以重启后为准） |",
    ),
]

# Rows the table never carried: the two guard tasks and the snapshot
# consistency task.  They are inserted in module order.
INSERTED_ROWS = [
    (
        "| @T-方储.01@ |",
        "| @T-方.01@ | 方向层投影不变式守卫（I-9 / I-7 / I-12） | @AC-方.01@ | @M-3@ | — | @W-0@ | "
        "✅ **已闭合 · 已归档**（2026-11-25，**16** 项全闭合，实盘只读审计零失败项） |",
    ),
    (
        "| @T-证候储.01@ |",
        "| @T-闭.01@ | 方向层主序接管闭环主池 | @AC-闭.01@ | @M-4@ | M-3 | @W-1@ | "
        "✅ **已闭合 · 已归档**（2026-11-25，**23** 项全闭合，实盘生效复测完成） |",
    ),
    (
        "| @T-复广.01@ |",
        "| @T-池复.01@ | 单 tick 门禁的快照一致性判定 | @AC-池复.01@ | @M-1@ ＋ @M-6@ | — | @W-2@ | "
        "🔄 **代码已落地 ＋ 回归全绿**（@pool-manager@ **167**、@review-manager@ **161**、@rs-src@ **220**）；"
        "**剩 3 项全部是实盘复测**（读数以重启后为准） |",
    ),
]

# The module occupancy table of section 1.0.  The module names come from
# the registry (D-001 section 1.1); the lists follow the module sets the
# registry declares for each task, so a task appears under every module it
# touches.
OCCUPANCY = [
    (
        "| @M-3@ | 方向层 |",
        "| @M-3@ | 方向层 | 5 | @T-方授储.01@、@T-方.01@、@T-方储.01@、@T-方闭储.01@、@T-方闭.01@ |",
    ),
    (
        "| @M-4@ | 闭环层 |",
        "| @M-4@ | 闭环层 | 9 | @T-方闭储.01@、@T-方闭.01@、@T-闭.01@、@T-复编.01@、@T-复编.02@、"
        "@T-复广.01@、@T-复广.02@、@T-编.01@、@T-候编储.01@ |",
    ),
    (
        "| @M-5@ | 估值与数据源 |",
        "| @M-5@ | 候选层 | 4 | @T-证候储.01@、@T-候.01@、@T-候.02@、@T-候编储.01@ |",
    ),
    (
        "| @M-6@ | 复核发布层 |",
        "| @M-6@ | 复核层 | 6 | @T-复编.01@、@T-复编.02@、@T-复.02@、@T-复广.01@、@T-复广.02@、@T-池复.01@ |",
    ),
    (
        "| @M-7@ | 广播层 |",
        "| @M-7@ | 广播层 | 3 | @T-复广.01@、@T-广.01@、@T-复广.02@ |",
    ),
    (
        "| @M-8@ | 候选池层 |",
        "| @M-8@ | 编排层 | 2 | @T-编.01@、@T-候编储.01@ |",
    ),
    (
        "| @M-10@ | 存储层 |",
        NL.join(
            [
                "| @M-10@ | 存储层 | 9 | @T-方储.01@、@T-方闭储.01@、@T-证候储.01@、@T-复广.02@、"
                "@T-候编储.01@、@T-储.01@、@T-储.02@、@T-储.03@、@T-储.04@ |",
                "| @M-1@ | 池管理器（链上池状态 / 深度 / 边界） | 1 | @T-池复.01@ |",
                "| @M-2@ | 证据通道（证据落库 / 清理） | 1 | @T-证候储.01@ |",
                "| @M-9@ | 授权与回滚（授权维度 / 守卫 / 回滚脚本） | 1 | @T-方授储.01@ |",
                "| @M-12@ | 接口层（HTTP 端点 / 运行时配置接线） | 1 | @T-复广.02@ |",
            ]
        ),
    ),
]


def main() -> None:
    text = read(PLAN)
    for marker, row in ROWS:
        text = replace_row(text, marker, row, marker)

    for anchor, row in INSERTED_ROWS:
        text = insert_before(text, anchor, row, anchor)

    # The occupancy table of section 1.0 named two modules differently from
    # the registry (D-001 section 1.1 is the single authority): it called
    # M-5 "valuation and data source" and M-8 "candidate pool layer", while
    # the registry calls them "candidate layer" and "orchestration layer".
    # A module number with two names is a second interpretation, so the
    # table is rebuilt from the registry's names and lists every task under
    # each module it touches.
    for marker, row in OCCUPANCY:
        text = replace_row(text, marker, row, marker)

    text = text.replace(
        "| 模块 | 名称 | 任务数 | 已占用编号 |",
        "| 模块 | 名称（**以 @D-001@ §1.1 为准**） | 触及该模块的任务数 | 触及该模块的任务 |",
    )
    text = text.replace(
        "**各模块号段占用情况**：",
        "**各模块占用情况**（**以 @D-001@ §1.1 的模块名为唯一口径** —— 本表按**模块**归集，"
        "一个跨模块任务在它触及的每个模块下**各列一次**；"
        "**每个任务的改动模块集合以 §2.1 为准**）：",
    )
    write(PLAN, text)
    print("D-002 decomposition table updated")


if __name__ == "__main__":
    main()
