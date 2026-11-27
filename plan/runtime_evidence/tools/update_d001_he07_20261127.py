"""Register @T-核.07@ in the task registry and in the @P-01@ README.

The task itself (code, unit tests, deployment and the live readings) is
done; what is left is the bookkeeping.  The rows are long, so they are
replaced through @mdpatch@ anchored on **unique row prefixes** instead of
a context based patch.

The script is idempotent: a second run finds its own row and does
nothing.
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
    "| **@核@** | @M-13@（运行时内核与运行时接线） | **7** | "
    "@T-核.01@（已闭合）、@T-核.02@（另一线程，只监测）、"
    "**@T-核.03@**（代码已落地 · **16 / 16** · 部署闸门待办）、"
    "**@T-核.04@**（代码已落地 · **部署闸门读数待取**）、"
    "**@T-核.05@**（代码已落地 · **部署闸门读数待取**）、"
    "**@T-核.06@**（代码已落地 · **实盘槽已恢复**）、"
    "**@T-核.07@**（**代码项已闭合 · 已部署 · 实盘读数齐**） | **@T-核.08@** |"
)

REG_TASK_ROW = (
    "| @T-核.07@ | 复核轮停滞的根因 · 目录快照的单飞与代次感知 | **2026-11-27** | "
    "@M-13@（@rs-src/src/worker_pool.rs@ 运行时内核与接线 · **目录快照的所有者**） | "
    "**代码项全部闭合 · 已部署 · 实盘读数齐**。**★ ★ 先纠偏（上一轮判断错了）**："
    "「一轮复核要 **49 分钟**」**没有读数支撑** —— 实测（生产库 **17.58 GB** · 只读）"
    "@manager_v4_reviews@ 的 @reviewed_at@ 跨度：代次 **51** ⇒ **575.9 s**（**9 min 36 s**）、"
    "代次 **52** ⇒ **466.7 s**（**7 min 47 s**）、更早两轮 **738.6 s** ／ **523 s** "
    "⇒ **真实一轮是 7 min 47 s – 12 min 19 s**（**并发本来就够**，用户口径正确）。"
    "**★ 真根因（逐环有读数）**：① 空档画像（代次 51／52）中位 **1.925 / 1.844 s**、"
    "最大 **25.07 / 22.415 s**、**> 8 s 的空档占整轮 37.9 % / 19.6 %**；"
    "② runtime **3112** CPU **7262 s** ／ 墙钟 **6420 s** ＝ **1.13 核** ⇒ **不是算力不足，是等待**；"
    "③ @/api/status@ ⇒ @residentWorkers = 176@、@concurrencyLimit = 32@、"
    "@activeConcurrency = 32@、@summary = {idle: 27, running: 149}@ ⇒ **117** 个 worker "
    "「running 但没在复核」；④ 代码顺序 @worker_pool.rs:5554@（标 running）→ **@:5562@（读快照）** "
    "→ **@:5571@（拿复核许可）** ⇒ **许可在快照读之后才拿** ⇒ **176** 个 worker 可**同时**重读；"
    "⑤ 旧代码（HEAD @:1139@）**没有单飞** ⇒ 每个 worker **各读一遍 17.58 GB 的库**；"
    "⑥ **并发放大实测**（生产库只读）：**1** 读者 **0.21 s**、**32** 并发 **7.10 s**（**×33**）、"
    "**176** 并发 **55.53 s**（**×258**）⇒ **全池停 55.5 s**；⑦ 重建节奏实测 **每 303 s** 一次"
    "（TTL **300 s** ＋ 3 s）⇒ 一轮**至少 2 次**全池停顿 ⇒ **≈ 110 s** 纯等待；"
    "⑧ 失效**只看 TTL** ⇒ 复核层最多落后目录 **5 min** ⇒ 正是 @T-广核.02@ 的遗留 **S6**。"
    "**★ 修法（@M-13@ · @worker_pool.rs@）**：**单飞闸门**（@refresh_gate: Mutex<()>@，重建闭包"
    "**在闸门内**执行 ⇒ 后到者**醒来就看见**重建好的快照）＋ **代次指纹**（读 "
    "@manager_v4_closed_loop_library_state@ 的 @closed_loop_version@ ＋ @materialized_checksum@）"
    "＋ **代次感知的新鲜度表**（TTL 过期 ⇒ Stale；**代次读不到 ⇒ Current（不猜）**；"
    "代次不同 ⇒ Stale）＋ **比较有节奏**（**10 s**）＋ **失败退避**（**30 s** ＋ @eprintln!@）"
    "＋ **计数器**（@contextCache.reloads@）＋ **状态加代次**。"
    "**★ 回归**：@flashloan-rs --lib@ **273 passed / 0 failed**（基线 **269** ＋ 新 **4**："
    "@the_snapshot_freshness_table_is_pinned@ ／ @one_rebuild_serves_every_worker_that_arrives_together@ "
    "／ @a_directory_that_moved_on_rebuilds_the_snapshot@ ／ "
    "@a_generation_that_cannot_be_read_keeps_the_ttl_rule@）；@flashloan/tools@ 工作区 **35** 个套件"
    "**全绿 · 0 failed**（**未改**）。**★ 实盘（部署后）**：旧 DLL 备份 "
    "@flashloan_rs.dll.bak-20261127-he07@（**18,443,264 B**、SHA256 @28E4E049…@）、"
    "新 DLL **18,455,552 B**（**01:53:20**）⇒ runtime **9704**（看护 **17048**）**01:53:52** 起跑新二进制"
    "⇒ **新旧二进制无中间态**；**@contextCache.reloads = 1@**（启动至今**只重建了一次**，"
    "旧行为是 **176** 个 worker 各读一遍）＋ **@closedLoopGeneration@ 与盘上目录代次一致**"
    "（**GENERATION-MATCH**）⇒ **S6 闭合**。"
    "**★ 诚实边界**：广播请求仍 **0** 行、@cow_execution_reviews@ **113,949** 行**全部** "
    "@not_executable@、带 @tx_hash@ 的 **0** 行；当前正利润 **9** 行、最大 **8.5e-05 USDG**（**灰尘级**）"
    "⇒ **市场侧**，**未放宽任何门禁** ⇒ 「**持续产生链上广播交易记录**」**仍未达成**；**未碰 git**。"
    "文档：@D-207@ / @D-208@ / @D-209@ ＋ @docs/D-210@ |"
)

REG_DOC_ROWS = (
    "| @D-207@ | PLAN | P-01 T-核.07 | @plan/tasks/P-01-池子重构/tasks/T-核.07/D-207-PLAN.md@ |" + NL +
    "| @D-208@ | SCOPE_GUARD | P-01 T-核.07 | @plan/tasks/P-01-池子重构/tasks/T-核.07/D-208-SCOPE_GUARD.md@ |" + NL +
    "| @D-209@ | checklist | P-01 T-核.07 | @plan/tasks/P-01-池子重构/tasks/T-核.07/D-209-checklist.md@ |" + NL +
    "| @D-210@ | 复核轮停滞的根因与快照单飞_20261127 | P-01 T-核.07 | "
    "@plan/tasks/P-01-池子重构/tasks/T-核.07/docs/D-210-复核轮停滞的根因与快照单飞_20261127.md@ |"
)

REG_MIGRATION_ROW = (
    "| 2026-11-27 | **复核轮停滞的根因：目录快照的重建被群发（@T-核.07@ · @M-13@）**："
    "**★ 先纠偏**：上一轮的「一轮复核 **49 分钟**」**没有读数支撑** ⇒ 实测（生产库 **17.58 GB** · 只读）"
    "@manager_v4_reviews@ 的 @reviewed_at@ 跨度为 **466.7 s**（代次 52）／**575.9 s**（代次 51）"
    "⇒ **真实一轮 7 min 47 s – 12 min 19 s**（**并发本来就够**）。"
    "**★ 真根因（逐环有读数）**：@/api/status@ ⇒ @residentWorkers = 176@、@concurrencyLimit = 32@、"
    "@activeConcurrency = 32@、@summary = {idle: 27, running: 149}@ ⇒ **117** 个 worker「running 但没在复核」；"
    "代码顺序 @worker_pool.rs:5562@（读快照）**先于** @:5571@（拿复核许可）⇒ **176** 个 worker 可**同时**重读；"
    "旧代码（HEAD @:1139@）**没有单飞** ⇒ **并发放大实测**：**1** 读者 **0.21 s**、**32** 并发 **7.10 s**、"
    "**176** 并发 **55.53 s**（**×258**）⇒ **全池停 55.5 s**；重建**每 303 s** 一次 ⇒ 一轮**至少 2 次**。"
    "**★ 修法**：**单飞闸门** ＋ **代次感知**（目录换代 ⇒ 快照**立刻**失效，不等 TTL ⇒ 闭合 "
    "@T-广核.02@ 的 **S6**）＋ **计数器** ＋ **失败退避**（**不猜、不静默**）。"
    "**★ 回归**：@flashloan-rs --lib@ **273 / 0**（＋**4**）、@flashloan/tools@ 工作区 **35** 套件**全绿**。"
    "**★ 实盘**：新 DLL **18,455,552 B**（**01:53:20**）上线、runtime **9704**（看护 **17048**）"
    "**01:53:52** 起跑新二进制 ⇒ @contextCache.reloads = 1@（**只重建了一次**）＋ "
    "@closedLoopGeneration@ **与盘上目录代次一致**。"
    "**★ 诚实边界**：广播请求仍 **0** 行（正利润灰尘级，最大 **8.5e-05 USDG**）⇒ **市场侧**、"
    "**未放宽任何门禁** ⇒ 「**持续产生链上广播交易记录**」**仍未达成**；**未碰 git**。"
    "文档：@D-207@ / @D-208@ / @D-209@ ＋ @docs/D-210@ |"
)

# ------------------------------------------------------------------- P-01

P01_TASK_ROW = (
    "| [@T-核.07@](./tasks/T-核.07/) | 复核轮停滞的根因 · 目录快照的单飞与代次感知 | "
    "@AC-核.07@ | @M-13@ | M-13 | @@W-1@@ | **3** | "
    "⬜ **代码项全部闭合 · 已部署 · 实盘读数齐**（2026-11-27 02:0x）："
    "**★ 先纠偏**（「一轮复核 **49 分钟**」**没有读数支撑**）⇒ 实测 **466.7 s**（代次 52）／"
    "**575.9 s**（代次 51）⇒ **真实一轮 7 min 47 s – 12 min 19 s**（**并发本来就够**）。"
    "**★ 真根因**：@load_context_cache@（@worker_pool.rs:5562@）**在复核许可（@:5571@）之前**跑，"
    "而旧代码**没有单飞** ⇒ **176** 个常驻 worker **同时**各读一遍 **17.58 GB** 的库 "
    "⇒ **1** 读者 **0.21 s** ／ **32** 并发 **7.10 s** ／ **176** 并发 **55.53 s**（**×258**）"
    "⇒ **全池停 55.5 s**，重建**每 303 s** 一次 ⇒ 一轮**至少 2 次**"
    "（@summary = {idle: 27, running: 149}@ ⇒ **117** 个 worker「running 但没在复核」）。"
    "**★ 落地**（@M-13@）：**单飞闸门** ＋ **代次指纹** ＋ **代次感知的新鲜度表**"
    "（**代次读不到 ⇒ 不猜**）＋ **比较有节奏（10 s）** ＋ **失败退避（30 s）** ＋ **计数器**。"
    "**回归** @flashloan-rs --lib@ **273 passed / 0 failed**（＋**4**）、@flashloan/tools@ **35** 套件**全绿**。"
    "**实盘**：新 DLL **18,455,552 B**（**01:53:20**）上线、runtime **9704**（看护 **17048**）"
    "**01:53:52** 起跑新二进制 ⇒ **@contextCache.reloads = 1@**（**只重建了一次**）＋ "
    "**@closedLoopGeneration@ 与盘上目录代次一致** ⇒ **S6 闭合**。"
    "**剩 3 项** ＝ **S1／S2／S4／S5**（归 @T-广核.01@）＋ **S3**（正利润灰尘级 · **市场条件**）"
    "＋ **S7**（「**持续产生链上广播交易记录**」**未达成**）⇒ **C 类 · 阻塞**。"
    "文档：@D-207@ / @D-208@ / @D-209@ ＋ @docs/D-210@ |"
)

P01_AGGREGATE = (
    "**合计未完成验收项**：**51**（二级任务 · **逐目录实测**，2026-11-27 02:0x · **22** 个在盘目录逐个读 "
    "@*-checklist.md@；本轮 @T-核.07@ 新登记 **8** 项、闭合 **7** 项（**4** 项部署读数 ＋ "
    "**S6** × **2** 个目录））＋ **8**（一级特有）＋ **0**（实盘读数指针 · **4 项已闭合**）"
    "＋ **51**（横切）＝ **110**"
)

P01_NOTE = (
    "> **本轮（2026-11-27 02:0x · 目录快照的单飞与代次感知 · @T-核.07@）**：",
    "> ① **★ 先纠偏**：上一轮的「一轮复核 **49 分钟**」**没有读数支撑** —— 实测（生产库 **17.58 GB** · 只读）",
    "> @manager_v4_reviews@ 的 @reviewed_at@ 跨度为 **466.7 s**（代次 **52**）／**575.9 s**（代次 **51**）、",
    "> 更早两轮 **738.6 s** ／ **523 s** ⇒ **真实一轮 7 min 47 s – 12 min 19 s**（**并发本来就够**）；",
    "> ② **★ 真根因**：@load_context_cache@（@worker_pool.rs:5562@）**在复核许可（@:5571@）之前**跑，",
    "> 而旧代码**没有单飞** ⇒ **176** 个常驻 worker **同时**各读一遍 **17.58 GB** 的库",
    "> ⇒ **并发放大实测**：**1** 读者 **0.21 s** ／ **32** 并发 **7.10 s** ／ **176** 并发 **55.53 s**（**×258**）",
    "> ⇒ **全池停 55.5 s**；重建**每 303 s** 一次 ⇒ 一轮**至少 2 次**"
    "> （@summary = {idle: 27, running: 149}@ ⇒ **117** 个 worker「running 但没在复核」）；",
    "> ③ **★ 落地**（@M-13@）：**单飞闸门** ＋ **代次指纹** ＋ **代次感知的新鲜度表**（**代次读不到 ⇒ 不猜**）",
    "> ＋ **比较有节奏（10 s）** ＋ **失败退避（30 s** ＋ 响亮的 @eprintln!@）＋ **计数器**；",
    "> ④ **回归**：@flashloan-rs --lib@ **273 / 0**（＋**4**）、@flashloan/tools@ 工作区 **35** 套件**全绿**；",
    "> ⑤ **实盘**：新 DLL **18,455,552 B**（**01:53:20**）上线、runtime **9704**（看护 **17048**）**01:53:52** 起跑新二进制",
    "> ⇒ **@contextCache.reloads = 1@**（**只重建了一次**）＋ **@closedLoopGeneration@ 与盘上目录代次一致**",
    "> ⇒ **@T-广核.02@ 的 S6 闭合**（复核层不再落后目录 5 min）；",
    "> ⑥ **诚实边界**：广播请求仍 **0** 行、**带 @tx_hash@ 的执行记录 0** 行；当前正利润 **9** 行、",
    "> 最大 **8.5e-05 USDG**（**灰尘级**）⇒ **市场侧**、**未放宽任何门禁**",
    "> ⇒ 「**持续产生链上广播交易记录**」**仍未达成**，且本轮**不能**承诺达成；",
    "> ⑦ **未完成项复算**：逐目录实测 **22** 个在盘目录 ⇒ 二级 **49 → 51**、合计 **108 → 110**",
    "> （新登记 **8** 项、闭合 **7** 项：部署读数 **4** ＋ **S6** × **2** 个目录）；**未碰 git**；",
    ">",
)


def replace_line(text: str, prefix: str, new_line: str, label: str) -> str:
    """Replace the single line that starts with prefix."""
    lines = text.split(NL)
    hits = [i for i, line in enumerate(lines) if line.startswith(prefix)]
    if len(hits) != 1:
        raise SystemExit(
            "update_d001_he07: line anchor count %d for %s" % (len(hits), label)
        )
    lines[hits[0]] = restore(new_line)
    return NL.join(lines)


def main() -> None:
    text = read(REGISTRY)
    if "D-210" in text:
        print("update_d001_he07: already registered")
        return

    text = replace_row(text, "| **@核@** |", REG_INDEX_ROW, "registry index row")
    text = insert_after(text, "| @T-核.06@ |", REG_TASK_ROW, "registry task row")
    text = insert_after(text, "| @D-206@ |", REG_DOC_ROWS, "registry doc rows")
    text = insert_before(
        text,
        "> **重编不改范围、不改验收、不改测试。**",
        REG_MIGRATION_ROW + NL,
        "registry migration row",
    )
    write(REGISTRY, text)

    text = read(README)
    text = insert_after(
        text, "| [@T-广核.02@](./tasks/T-广核.02/) |", P01_TASK_ROW, "P-01 task row"
    )
    text = replace_line(
        text, "**合计未完成验收项**", P01_AGGREGATE, "P-01 aggregate line"
    )
    text = insert_before(
        text,
        "> **本轮（2026-11-27 00:0x",
        NL.join(P01_NOTE),
        "P-01 round note",
    )
    write(README, text)
    print("update_d001_he07: ok")


if __name__ == "__main__":
    main()
