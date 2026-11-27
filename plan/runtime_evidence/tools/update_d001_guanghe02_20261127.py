"""Close out @T-广核.02@ in the task registry and in the @P-01@ README.

The code, the unit tests and the deployment of the task itself were done in
the previous round.  What was left was the bookkeeping: the registry still
said @根因已定位 · 代码项未做@ and the @P-01@ README still carried the old
@未完成@ count.  The rows are long, so they are replaced through @mdpatch@
anchored on **unique row prefixes** instead of a context based patch.

The script is idempotent: a second run finds its own row and does nothing.
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


ROOT = Path(__file__).resolve().parents[3]
REGISTRY = ROOT / "plan" / "D-001-TASK_REGISTRY.md"
P01 = ROOT / "plan" / "tasks" / "P-01-池子重构"
README = P01 / "README.md"


# --------------------------------------------------------------- registry

REG_INDEX_ROW = (
    "| **@广核@** | @M-6@（复核发布层） ＋ @M-7@（广播层） ＋ @M-13@（运行时内核与接线） | 2 | "
    "**@T-广核.01@**（代码已闭合 · **部署闸门 5 / 6** · 广播请求 **> 0 行**待市场条件）、"
    "**@T-广核.02@**（**代码项已闭合 · 已部署 · 实盘读数齐**） | **@T-广核.03@** |"
)

REG_TASK_ROW = (
    "| @T-广核.02@ | 闭环消费者的重建信号滞后 · RuntimeCore 快照被拒 | **2026-11-26** | "
    "@M-6@（@rs-src/src/review_publish.rs@ 复核发布层 · **消费者回执**）＋ "
    "@M-13@（@tools/closed_loop_manager/src/lib.rs@ 标志所有者 · **按代次清标志**） | "
    "**代码项全部闭合 · 已部署 · 实盘读数齐**。**★ 症状**：运行实例（看护 **19888** ／ runtime **20656**，**22:05:01** 起）"
    "@runtimeCore.state = partial@，@snapshot_rejected@ **每 15 s** 一条（**22:19:17 → 22:25:22**），"
    "最后一条 @snapshot_committed@ 停在 **22:17:06**（第 **21** 代）⇒ **运行时视图停在 21 代**。"
    "**★ ★ 根因订正（上一轮判断错了，本轮用读数推翻）**：@last_rebuilt_at@ **不是**消费者的重建时刻 —— 它由 "
    "@closed_loop_manager/src/lib.rs:4281 replace_closed_loop_rows@ → @:3571@ 在**写目录行的同一事务**写入 ⇒ "
    "实测它与 @manager_v4_closed_loops.updated_at@、@library_state.generated_at@ **三者恒等**"
    "（全部 **15:34:27.741556900Z**）⇒ 上一轮「消费者在目录写完的同一刻就重建」**错误**，"
    "据此定的修法（按 @last_rebuilt_at@ ≥ 目录写入时刻清标志）**同义反复、恒真** ⇒ **作废**。"
    "**★ 真正的凭据（已持久化 · 不需新表新列）**：复核行携带**实测代次**"
    "（@review_publish.rs:846-849 measured_generation@ → @manager_v4_reviews.closed_loop_version/checksum@）"
    "⇒ 实测消费者**落后目录 4 min 28 s**（目录 **15:34:27** → 首条代次 44 复核 **15:38:55**）"
    "⇒ 标志在**追上前**确实是 **1**（**没撒谎**），**追上后仍不清** ⇒ **滞后**。"
    "**★ 终修法（代次回执）**：@M-13@ 新增 @acknowledge_consumers_rebuilt(...)@（**删除**原 "
    "@clear_consumers_need_rebuild@ 与「下一次没变化就清」的分支），@M-6@ 在 @publish_review_loops@ 成功后"
    "用**实测代次**回执；SQL 护栏 @WHERE closed_loop_version = ?@（＋ @materialized_checksum@）"
    "⇒ **过期回执清不掉任何东西**。**★ 回归**：@closed-loop-manager@ **51 / 0**（新 **3**）、"
    "@composite-manager@ **20 + 6 / 0**（**未改**）、@flashloan-rs --lib@ **269 / 0**（新 **2**）。"
    "**★ 实盘**：部署后（看护 **18836** ／ runtime **3112**，**23:47:10** 起）"
    "@library_state.updated_at = 15:48:42.971665400Z@、@consumers_need_rebuild = 0@、@lastError = None@、"
    "快照代次 **21 → 32**（≈ **12.5 s** 一代）；**G2 实盘复现**（代次 45 换代窗口 · 采样每 ≈ 11 s）："
    "**00:02:13** 换代 ⇒ 标志 **1** 持续到 **00:03:16** ⇒ 消费者 **00:03:20** 追上 ⇒ **00:03:27** 起 **0**；"
    "追上前 @lastError@ **同时**挂**两条** issue（标志为 1 **＋** 复核还持代次 44）⇒ **混合代次的快照不收**"
    "（**哨兵没被拆**）。**★ 诚实边界**：广播请求仍 **0** 行（正利润灰尘级：**1.2e-05 USDG** ＝ 全表最大，"
    "另有 **4.5e-07 PFE** / **2.7e-07 CRCL** / **7.8e-09 ETH**）⇒ "
    "**市场侧**，**未放宽任何门禁**）；**未碰 git**（工作区 **400+** 文件在途改动）；"
    "**遗留 S6**：消费者重建仍落后目录数十秒 – 数分钟（目录缓存 TTL **300 s**）。"
    "文档：`D-203` / `D-204` / `D-205` ＋ `docs/D-206` |"
)

REG_DOC_ROW = (
    "| @D-206@ | 闭环消费者回执与标志去滞后_20261126 | P-01 T-广核.02 | "
    "@plan/tasks/P-01-池子重构/tasks/T-广核.02/docs/D-206-闭环消费者回执与标志去滞后_20261126.md@ |"
)

REG_MIGRATION_ROW = (
    "| 2026-11-27 | **闭环消费者回执 ⇒ 标志不再滞后（`T-广核.02` 收尾 · `M-6` ＋ `M-13`）**："
    "**★ 订正上一轮**：根因里的「消费者在目录写完的**同一刻**就重建」**判断错了** —— "
    "`last_rebuilt_at` 与目录写入**同一事务**写入、**恒等**（实测三者全等）⇒ 据此定的修法**同义反复** ⇒ **作废**；"
    "改用**实测代次**（复核行已持久化 ⇒ **不需新表新列**）。**★ 终修法**：`M-13` 新增 "
    "`acknowledge_consumers_rebuilt(...)`（**删**掉「下一次没变化就清」的旧清除路径）、`M-6` 发布成功后按"
    "**实测代次**回执、SQL 护栏只认**当前代次** ⇒ 追上前**继续拒收**、追上后**立刻清零**（**不猜、不降级**）。"
    "**★ 回归**：`closed-loop-manager` **51 / 0**（＋**3**）、`composite-manager` **20 + 6 / 0**（**未改**）、"
    "`flashloan-rs --lib` **269 / 0**（＋**2**）。**★ 实盘**：**23:47:10** 起（看护 **18836** ／ runtime **3112**）"
    "标志 **1 → 0**、`lastError = None`、快照代次 **21 → 32**；**G2 逐条采样**（代次 45 窗口）："
    "**00:02:13** 换代 ⇒ 标志 **1** 持续到 **00:03:16** ⇒ **00:03:20** 消费者追上 ⇒ **00:03:27** 起 **0**"
    "（追上前 `lastError` 挂**两条** issue ⇒ **混合代次的快照不收**）。**★ 诚实边界**：广播请求仍 **0** 行"
    "（正利润灰尘级 ⇒ **市场侧**，**未放宽任何门禁**）；**未碰 git**。文档：`D-203` / `D-204` / `D-205` ＋ `docs/D-206` |"
)

# ------------------------------------------------------------------- P-01

P01_TASK_ROW = (
    "| [@T-广核.02@](./tasks/T-广核.02/) | 闭环消费者的重建信号滞后 · RuntimeCore 快照被拒 | `AC-广核.02` | "
    "@M-6@ ＋ @M-13@ | M-13 | @@W-3@@ | **2** | "
    "⬜ **代码项全部闭合 · 已部署 · 实盘读数齐**（2026-11-27 00:0x）：**★ 订正上一轮**"
    "（`last_rebuilt_at` 与目录写入**同一事务**、**恒等** ⇒ 「写完同一刻就重建」**判断错误**、"
    "据此定的修法**作废**）⇒ 改用**实测代次**；**落地**：`M-13` 新增 "
    "`acknowledge_consumers_rebuilt(...)`（**删**旧清除路径）＋ `M-6` 发布后按**实测代次**回执、"
    "SQL 护栏只认**当前代次** ⇒ 追上前**继续拒收**、追上后**立刻清零**；**回归** **51** ＋ **20 + 6** ＋ **269** passed；"
    "**实盘**：标志 **1 → 0**、`lastError = None`、快照代次 **21 → 32**（≈ **12.5 s** 一代），"
    "**G2 逐条采样**复现（换代 ⇒ 标志回 **1** ⇒ 追上 ⇒ 清零 ⇒ 快照继续前进）。"
    "**剩 2 项** ＝ **S1–S5**（归 `T-广核.01`）＋ **S6**（目录缓存 TTL **300 s** ⇒ 消费者落后数十秒 – 数分钟）"
    "⇒ **C 类 · 阻塞**。文档：`D-203` / `D-204` / `D-205` ＋ `docs/D-206` |"
)

P01_AGGREGATE = (
    "**合计未完成验收项**：**49**（二级任务 · **逐目录实测**，2026-11-27 00:0x · **21** 个在盘目录逐个读 "
    "@*-checklist.md@；本轮 @T-广核.02@ **11 → 2**）＋ **8**（一级特有）＋ **0**（实盘读数指针 · **4 项已闭合**）"
    "＋ **51**（横切）＝ **108**"
)

P01_NOTE = (
    "> **本轮（2026-11-27 00:0x · 闭环消费者回执 ⇒ 标志不再滞后 · `T-广核.02` 收尾）**：",
    "> ① **★ 订正上一轮**：上一轮把根因写成「消费者在目录写完的**同一刻**就重建了」—— **错**。",
    "> `last_rebuilt_at` 与目录写入**同一事务**写入、**恒等**（实测三者全等）⇒ 据此定的修法**同义反复**、**作废**；",
    "> 改用**实测代次**（复核行已持久化 ⇒ **不需新表新列**）；",
    "> ② **落地**：`M-13` 新增 `acknowledge_consumers_rebuilt(...)`（**删**掉「下一次没变化就清」的旧清除路径）、",
    "> `M-6` 发布成功后按**实测代次**回执、SQL 护栏只认**当前代次** ⇒ 追上前**继续拒收**、追上后**立刻清零**",
    ">（**不猜、不降级**）；",
    "> ③ **回归**：`closed-loop-manager` **51 / 0**（＋**3**）、`composite-manager` **20 + 6 / 0**（**未改**）、",
    "> `flashloan-rs --lib` **269 / 0**（＋**2**）；",
    "> ④ **实盘**（**23:47:10** 起 · 看护 **18836** ／ runtime **3112**）：标志 **1 → 0**、`lastError = None`、",
    "> 快照代次 **21 → 32**（≈ **12.5 s** 一代）；**G2 逐条采样**（代次 45 窗口）：**00:02:13** 换代 ⇒ 标志 **1** ",
    "> 持续到 **00:03:16** ⇒ **00:03:20** 消费者追上 ⇒ **00:03:27** 起 **0**（追上前 `lastError` 挂**两条** issue ⇒ ",
    "> **混合代次的快照不收** ⇒ **哨兵没被拆**）；",
    "> **代次 46 窗口再次复现**（**00:20 – 00:24**：换代 ⇒ 标志 **1** ⇒ 追上 ⇒ 清零 ⇒ `lastError` 自愈）；",
    "> **00:26** 快照代次 **79**、`lastError = None**、代次 **46** 的复核行 **84** 条仍在增长；",
    "> ⑤ **诚实边界**：广播请求仍 **0** 行（正利润灰尘级：**1.2e-05 USDG** ＝ 全表最大，另有 **4.5e-07 PFE** / ",
    "> **2.7e-07 CRCL** / **7.8e-09 ETH**）⇒ **市场侧**，**未放宽任何门禁**）；",
    "> **未碰 git**（工作区 **400+** 文件在途改动）；**遗留 S6**：消费者重建仍落后目录数十秒 – 数分钟",
    ">（目录缓存 TTL **300 s**）；",
    "> ⑥ **未完成项复算**：逐目录实测 **21** 个在盘目录 ⇒ `T-广核.02` **11 → 2**（剩 **S1–S5** 归 `T-广核.01` ＋ **S6**）",
    "> ⇒ 二级 **58 → 49**、合计 **117 → 108**。",
    ">",
)


def replace_line(text: str, prefix: str, new_line: str, label: str) -> str:
    """Replace the single line that starts with prefix."""
    lines = text.split(NL)
    hits = [i for i, line in enumerate(lines) if line.startswith(prefix)]
    if len(hits) != 1:
        raise SystemExit(
            "update_d001_guanghe02: line anchor count %d for %s" % (len(hits), label)
        )
    lines[hits[0]] = restore(new_line)
    return NL.join(lines)


def main() -> None:
    text = read(REGISTRY)
    if "D-206" in text:
        print("update_d001_guanghe02: already registered")
        return

    text = replace_row(text, "| **@广核@** |", REG_INDEX_ROW, "registry index row")
    text = replace_row(text, "| @T-广核.02@ |", REG_TASK_ROW, "registry task row")
    text = insert_after(text, "| @D-202@ |", REG_DOC_ROW, "registry doc row")
    text = insert_before(
        text,
        "> **重编不改范围、不改验收、不改测试。**",
        REG_MIGRATION_ROW + NL,
        "registry migration row",
    )
    write(REGISTRY, text)

    text = read(README)
    text = replace_row(
        text, "| [@T-广核.02@](./tasks/T-广核.02/) |", P01_TASK_ROW, "P-01 task row"
    )
    text = replace_line(
        text, "**合计未完成验收项**", P01_AGGREGATE, "P-01 aggregate line"
    )
    text = insert_before(
        text,
        "> **本轮（2026-11-26 14:3x",
        NL.join(P01_NOTE),
        "P-01 round note",
    )
    write(README, text)
    print("update_d001_guanghe02: ok")


if __name__ == "__main__":
    main()
