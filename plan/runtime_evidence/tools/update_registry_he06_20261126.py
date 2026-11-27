"""Register T-核.06 in the task registry and the two task READMEs.

The rows are inserted with the same helper the earlier rounds used
(@mdpatch@), anchored on **unique row prefixes**.  Every edit is
idempotent: a row that is already there is left alone.

The text uses @ for a backtick, exactly like the earlier rounds.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdpatch import NL, insert_after, insert_before, read, replace_row, restore, write  # noqa: E402


ROOT = Path(__file__).resolve().parents[3]
P01 = ROOT / "plan" / "tasks" / "P-01-池子重构"
REGISTRY = ROOT / "plan" / "D-001-TASK_REGISTRY.md"
README = P01 / "README.md"
DOCS_README = P01 / "docs" / "README.md"


# ---------------------------------------------------------------- registry

REG_MODULE_ROW = (
    "| **@核@** | @M-13@（运行时内核与运行时接线） | **6** | @T-核.01@（已闭合）、"
    "@T-核.02@（另一线程，只监测）、**@T-核.03@**（代码已落地 · **16 / 16** · 部署闸门待办）、"
    "**@T-核.04@**（代码已落地 · **部署闸门读数待取**）、**@T-核.05@**（代码已落地 · **部署闸门读数待取**）、"
    "**@T-核.06@**（代码已落地 · **实盘槽已恢复**） | **@T-核.07@** |"
)

REG_TASK_ROW = (
    "| @T-核.06@ | 流动性维护槽的价格源依赖 | **2026-11-26** | "
    "@M-13@（@dex/intent_trade/chains/robinhood/v4_pool_index.py@） | "
    "**代码项全部闭合，剩 2 项实盘读数待取**（复核新一代 / 广播记录）。"
    "**触发（实测 · 只读）**：网络修复后复测 ⇒ 链上 RPC **活 2 / 5**（publicnode **200 / 1280 ms**、"
    "QuickNode **200 / 797 ms**），但**外部价格源活 1 / 12**（**只有 @api.gateio.ws@ 200 / 2030 ms**；"
    "binance.us **16077 ms 超时**、kraken **16077 ms 超时**、coingecko **16061 ms 超时**、"
    "coinbase **15250 ms 超时**、bybit **12875 ms 超时**、bitfinex **12375 ms 超时**、"
    "mexc **12282 ms 超时**、bitstamp **12233 ms 超时**、exodus **12171 ms 超时**、okx **DNS 失败**）"
    "⇒ **部署网络的出口只放行链上 RPC 主机与 @api.gateio.ws@**。"
    "**根因**（@py-spy dump --pid 29224 --locals@）：流动性槽**不是失败，是卡住** —— 线程停在 "
    "@_fetch_json (v4_pool_index.py:141)@ → @eth_price_usd (v4_pool_index.py:189)@，"
    "@url = \"https://api.kraken.com/0/public/Ticker?pair=ETHUSD\"@、"
    "@errors = [\"binance_us: <urlopen error timed out>\"]@；"
    "槽的 @refreshed_at@ 停在 **2026-11-26T01:16:42Z** ＝ 09:16:42，"
    "槽间隔 **900 s** ⇒ **此后 35 min 没有第二次写入**。"
    "**为什么是卡住而不是失败**：@eth_price_usd()@ 失败时清空缓存 ⇒ **下一个池子**重抓全部源 "
    "⇒ 每源超时 **10 s** × 3 源 ≈ **30 s / 池** ⇒ 实测 **363 池** ⇒ **≈ 3 h / tick**；"
    "且 **Python 段（refresh_v4_live）排在 Rust 段前面** ⇒ "
    "**快照表的唯一写者（Rust 池管理器）根本没机会执行**。"
    "**落地**：@ETH_PRICE_SOURCES@ **首位加 @gateio@**（原三源**降为后备链**，不是删除）；"
    "@_extract_eth_price()@ **新增 gateio 分支**（只认 @currency_pair@ 为 @ETH_USDT@ / @ETH_USDC@ 的行，"
    "取 @last@；列表里别的交易对**不会被当成 ETH**）；@eth_price_usd()@ **命中缓存时额外校验新鲜度**"
    "（**陈旧读数自我刷新**）＋ **新增失败冷却** @ETH_PRICE_RETRY_COOLDOWN_SECONDS@（默认 **60 s**）"
    "＋ 私有 helper @_recent_price_failure()@ ⇒ 一次失败被记住，冷却窗内**不再按池重抓**。"
    "**口径**：用户「**要么成功要么失败，没有降级处理**」⇒ **未新增**任何兜底分支 —— "
    "价格拿不到就是**拿不到**（池子**未定价**、@estimatedTvlUsd = None@），"
    "**绝不**用过期价 / 编造价算 TVL；改的是「**卡 3 小时**」⇒「**失败一次**」；"
    "用户「**Python 是遗留旧代码**」⇒ 本任务**只**改遗留 Python 里的**价格发现**（不碰测量），"
    "「测量职责整体迁到 Rust 池管理器」作为 **R1** 如实登记、**不代别的任务关闭**；"
    "用户「**异常路径先留占位**」⇒ **USDT/USD 基差（R2）**、**跨窗重试（R3）** 只登记占位。"
    "**回归**：目标单测 **23 passed / 0 failed**（基线 **20** ＋ 新 **3**）；"
    "相关 **58 passed / 0 failed**。"
    "**实盘闸门（已完成）**：Python 侧改动 ⇒ runtime 重启（看护 **24676** ＋ runtime **35440**，"
    "**10:01:29** 起，端口 5000；Rust DLL 未改 ⇒ 无需重建、无新旧二进制中间态）⇒ "
    "**★ 流动性槽已恢复**：@status = ok@、@mode = complete@、"
    "@refreshed_at = 2026-11-26T02:07:12Z@、@scanned_pool_count = 363@、@snapshot_rows_written = 363@；"
    "快照表 @MAX(liquidity_read_at)@ 由 **2026-11-25T23:50:02Z** ⇒ **2026-11-26T02:07:09Z**；"
    "候选池槽由 **2026-11-25T23:53:15Z** ⇒ **2026-11-26T02:09:51Z**（此前一直 @blocked_upstream@）。"
    "**剩 2 项** ＝ ① **复核层发布新一代**（@review_updated_at_max@ 仍停在 **2026-11-25T23:07:30Z**、"
    "174 条全 @data_incomplete@ ⇒ 归 @M-6@ 代际）② **广播记录出现**"
    "（@broadcast_transaction_db@ 仍 **0 文件 / 0 字节**、@manager_v4_review_broadcast_requests = 0@ "
    "⇒ **必须先有复核正向利润**）⇒ **C 类 · 阻塞**。"
    "文档：@plan/tasks/P-01-池子重构/tasks/T-核.06/@（@D-188@ / @D-189@ / @D-190@ / @D-191@） |"
)

REG_DOC_ROWS = (
    "| @D-188@ | PLAN | P-01 T-核.06 | @plan/tasks/P-01-池子重构/tasks/T-核.06/D-188-PLAN.md@ |\n"
    "| @D-189@ | SCOPE_GUARD | P-01 T-核.06 | @plan/tasks/P-01-池子重构/tasks/T-核.06/D-189-SCOPE_GUARD.md@ |\n"
    "| @D-190@ | checklist | P-01 T-核.06 | @plan/tasks/P-01-池子重构/tasks/T-核.06/D-190-checklist.md@ |\n"
    "| @D-191@ | 流动性维护槽的价格源依赖_20261126 | P-01 T-核.06 | "
    "@plan/tasks/P-01-池子重构/tasks/T-核.06/docs/D-191-流动性维护槽的价格源依赖_20261126.md@ |"
)

REG_MIGRATION_ROW = (
    "| 2026-11-26 | **流动性维护槽的价格源依赖（@T-核.06@ · @M-13@）**："
    "网络修复后复测 ⇒ 链上 RPC **活 2 / 5**，但**外部价格源活 1 / 12**"
    "（**只有 @api.gateio.ws@ 200 / 2030 ms@**）⇒ **部署网络的出口只放行链上 RPC 主机与 @api.gateio.ws@**。"
    "@py-spy@ 读到流动性槽线程停在 @eth_price_usd()@ → @api.kraken.com@ **TCP 连接挂起** ⇒ "
    "**槽不是失败，是卡住**（@refreshed_at@ 停在 **2026-11-26T01:16:42Z**，此后 **35 min** 无第二次写入，"
    "槽间隔 **900 s**）。**根因**：@eth_price_usd()@ 失败时**清空缓存** ⇒ **下一个池子**重抓全部源 "
    "（每源 **10 s**）⇒ **≈ 3 h / tick**；且 **Python 段排在 Rust 段前面** ⇒ "
    "**快照表的唯一写者（Rust 池管理器）根本没机会执行**。"
    "**落地**：@ETH_PRICE_SOURCES@ **首位加 @gateio@**（原三源降为后备链）；"
    "@_extract_eth_price()@ **新增 gateio 分支**（只认 ETH 交易对）；"
    "@eth_price_usd()@ **命中缓存时校验新鲜度**（陈旧读数自我刷新）＋ **新增失败冷却**"
    "（@ETH_PRICE_RETRY_COOLDOWN_SECONDS@ 默认 **60 s**）。"
    "**口径**：**未新增**任何兜底分支（价格拿不到就是拿不到 ⇒ 池子未定价）；"
    "**Rust 侧零改动**（与 @T-核.05@ 正交）；广播 / 复核 / 利润口径**一字未改**。"
    "**回归**：目标单测 **23 passed**（基线 **20** ＋ **3**）、相关 **58 passed / 0 failed**。"
    "**实盘**：runtime 重启（**24676** ／ **35440**，**10:01:29** 起，端口 5000）⇒ "
    "**★ 流动性槽已恢复**（@status = ok@、@mode = complete@、@snapshot_rows_written = 363@、"
    "@refreshed_at = 2026-11-26T02:07:12Z@）；快照表 @MAX(liquidity_read_at)@ "
    "**2026-11-25T23:50:02Z** ⇒ **2026-11-26T02:07:09Z**；候选池槽 **23:53:15Z** ⇒ **02:09:51Z**。"
    "**剩 2 项**（复核新一代 / 广播记录）⇒ 归 @M-6@ / @M-7@，**不代任何其它任务关闭**。"
    "文档：@plan/tasks/P-01-池子重构/tasks/T-核.06/@（@D-188@ / @D-189@ / @D-190@ / @D-191@） |"
)


# ------------------------------------------------------------------ README

README_TASK_ROW = (
    "| [@T-核.06@](./tasks/T-核.06/) | 流动性维护槽的价格源依赖 | @AC-核.06@ | @M-13@ | — | "
    "@@W-1@@ | **2** | ⬜ **代码已闭合**（2026-11-26）：网络修复后复测 ⇒ 链上 RPC **活 2 / 5**，"
    "但**外部价格源活 1 / 12**（**只有 @api.gateio.ws@**）⇒ **槽不是失败，是卡住**"
    "（@py-spy@ 读到线程停在 @eth_price_usd()@ → @api.kraken.com@ **TCP 挂起**；"
    "@refreshed_at@ 停在 **2026-11-26T01:16:42Z**，**35 min** 无第二次写入）。"
    "**根因**：失败时**清空缓存** ⇒ **下一个池子**重抓全部源（每源 **10 s**）⇒ **≈ 3 h / tick**；"
    "且 **Python 段排在 Rust 段前面** ⇒ **快照表的唯一写者根本没机会执行**。"
    "**落地**：@ETH_PRICE_SOURCES@ **首位加 @gateio@**（原三源降为后备链）＋ "
    "@_extract_eth_price()@ **新增 gateio 分支**（只认 ETH 交易对）＋ "
    "@eth_price_usd()@ **缓存新鲜度校验**（陈旧读数自我刷新）＋ **失败冷却**（默认 **60 s**）。"
    "**未新增**任何兜底分支（价格拿不到就是拿不到 ⇒ 池子未定价）；**Rust 侧零改动**。"
    "**回归**：目标单测 **23 passed**（＋3）、相关 **58 passed**。"
    "**实盘**：runtime 重启（**24676** ／ **35440**，**10:01:29** 起）⇒ "
    "**★ 流动性槽已恢复**（@status = ok@、@mode = complete@、@snapshot_rows_written = 363@、"
    "@refreshed_at = 2026-11-26T02:07:12Z@）；快照表 @MAX(liquidity_read_at)@ "
    "**2026-11-25T23:50:02Z** ⇒ **2026-11-26T02:07:09Z**；候选池槽 **23:53:15Z** ⇒ **02:09:51Z**。"
    "**剩 2 项** ＝ 复核层发布新一代 ＋ 广播记录出现（归 @M-6@ / @M-7@） |"
)

README_COUNTS = (
    "**合计未完成验收项**：**46**（二级任务 · **逐目录实测**，2026-11-26）＋ **8**（一级特有）＋ "
    "**0**（实盘读数指针 · **4 项已闭合**）＋ **51**（横切）＝ **105**"
)

README_NOTES = (
    "> **本轮（2026-11-26 · 流动性维护槽的价格源依赖）**：\n"
    "> ① 新登记 **1** 个二级任务（@T-核.06@）⇒ 登记二级任务 **33 → 34**、在盘 **17 → 18**；\n"
    "> ② 其 checklist **25** 框中 **23** 框已闭合、**2** 框未勾（**复核新一代** ＋ **广播记录出现**）"
    "⇒ **未勾选框读数 103 → 105**（复算见 [@D-014](./docs/D-014-未完成任务清单_20261123.md) @1.3@）；\n"
    "> ③ **本轮新根因（@T-核.06@）**：网络修复后复测 ⇒ 链上 RPC **活 2 / 5**，"
    "但**外部价格源活 1 / 12**（**只有 @api.gateio.ws@ 200 / 2030 ms@**）⇒ "
    "**槽不是失败，是卡住**（@py-spy@ 读到线程停在 @eth_price_usd()@ → @api.kraken.com@ **TCP 挂起**）"
    "⇒ **失败的价格抓取按池重试**（失败清空缓存 ⇒ 下一个池子重抓全部源 ⇒ **≈ 3 h / tick**）"
    "⇒ 槽的 @refreshed_at@ 停在 **2026-11-26T01:16:42Z**，**35 min** 没有第二次写入；\n"
    "> ④ **连带效应**：@T-核.05@ 只解了**端点**，**价格源**这一层还没解 ⇒ "
    "**快照表的唯一写者（Rust 池管理器）根本没机会执行** ⇒ 候选层 / 方向层 / 闭环层仍旧拿陈旧证据；\n"
    "> ⑤ **落地**：@ETH_PRICE_SOURCES@ **首位加 @gateio@** ＋ **失败冷却**（默认 **60 s**）＋ "
    "**陈旧读数自我刷新** ⇒ **未新增**任何兜底分支（价格拿不到就是拿不到 ⇒ 池子未定价）；\n"
    "> ⑥ **实盘已恢复**：runtime 重启（**24676** ／ **35440**，**10:01:29** 起）⇒ "
    "**流动性槽 @status = ok@ / @mode = complete@ / @snapshot_rows_written = 363@ / "
    "@refreshed_at = 2026-11-26T02:07:12Z@**；快照表 @MAX(liquidity_read_at)@ "
    "**2026-11-25T23:50:02Z** ⇒ **2026-11-26T02:07:09Z**；候选池槽 **23:53:15Z** ⇒ **02:09:51Z**；\n"
    "> ⑦ **诚实边界**：复核层仍是旧一代（**174** 条全 @data_incomplete@）⇒ "
    "**广播记录仍为 0** ⇒ **必须先有复核正向利润才广播**（用户口径）⇒ 这 **2** 项归 @M-6@ / @M-7@，"
    "**不代任何其它任务关闭**。"
)


# ----------------------------------------------------------- docs README

DOCS_DOC_ROW = (
    "| [@流动性维护槽的价格源依赖_20261126.md@](../tasks/T-核.06/docs/D-191-流动性维护槽的价格源依赖_20261126.md) | "
    "**流动性维护槽的价格源依赖**（@T-核.06@）：复测读数（链上 RPC **活 2 / 5** ／ 外部价格源 **活 1 / 12**）＋ "
    "@py-spy@ 的**卡住**读数（@eth_price_usd()@ → @api.kraken.com@ **TCP 挂起**）＋ "
    "**「失败按池重试 ⇒ ≈ 3 h / tick」**的推算 ＋ 三条修法（可达源 / 失败冷却 / 陈旧刷新）＋ "
    "**实盘槽恢复读数**（@snapshot_rows_written = 363@） |"
)

DOCS_TASK_ROW = (
    "| [@T-核.06@](../tasks/T-核.06/) | 流动性维护槽的价格源依赖 | @AC-核.06@ | @M-13@ | — | @@W-1@@ | "
    "**2** | ⬜ 网络修复后复测 ⇒ 链上 RPC **活 2 / 5**、**外部价格源活 1 / 12**（**只有 @api.gateio.ws@**）"
    "⇒ **槽不是失败，是卡住**（@py-spy@：@eth_price_usd()@ → @api.kraken.com@ **TCP 挂起**；"
    "@refreshed_at@ 停在 **2026-11-26T01:16:42Z**，**35 min** 无第二次写入）"
    "⇒ **失败的价格抓取按池重试**（**≈ 3 h / tick**）⇒ **快照表的唯一写者根本没机会执行**。"
    "**落地**：@ETH_PRICE_SOURCES@ **首位加 @gateio@**（原三源降为后备链）＋ @_extract_eth_price()@ "
    "**新增 gateio 分支**（只认 ETH 交易对）＋ @eth_price_usd()@ **缓存新鲜度校验**（陈旧读数自我刷新）"
    "＋ **失败冷却**（默认 **60 s**）。**未新增**任何兜底分支；**Rust 侧零改动**。"
    "**回归**：目标单测 **23 passed**（＋3）、相关 **58 passed**。"
    "**实盘**：runtime 重启（**24676** ／ **35440**，**10:01:29** 起）⇒ "
    "**★ 流动性槽已恢复**（@status = ok@、@mode = complete@、@snapshot_rows_written = 363@、"
    "@refreshed_at = 2026-11-26T02:07:12Z@）；快照表 @MAX(liquidity_read_at)@ "
    "**2026-11-25T23:50:02Z** ⇒ **2026-11-26T02:07:09Z**；候选池槽 **23:53:15Z** ⇒ **02:09:51Z**。"
    "**剩 2 项** ＝ 复核层发布新一代 ＋ 广播记录出现（归 @M-6@ / @M-7@） |"
)


def main() -> None:
    text = read(REGISTRY)
    if restore("| @T-核.06@ | 流动性维护槽的价格源依赖 |") not in text:
        text = replace_row(text, "| **@核@** |", REG_MODULE_ROW, "module row")
        text = insert_after(
            text,
            "| @T-核.05@ | 流动性维护槽的 RPC 故障转移 |",
            REG_TASK_ROW,
            "task row",
        )
    if restore("| @D-191@ | 流动性维护槽的价格源依赖_20261126 |") not in text:
        text = insert_after(
            text,
            "| @D-187@ | 流动性维护槽的RPC故障转移_20261126 |",
            REG_DOC_ROWS,
            "document rows",
        )
    if "**流动性维护槽的价格源依赖（@T-核.06@" not in text:
        text = insert_after(
            text,
            "| 2026-11-26 | **流动性维护槽的 RPC 故障转移（@T-核.05@",
            REG_MIGRATION_ROW,
            "migration row",
        )
    write(REGISTRY, text)

    text = read(README)
    if restore("| [@T-核.06@](./tasks/T-核.06/)") not in text:
        text = insert_after(
            text,
            "| [@T-核.05@](./tasks/T-核.05/)",
            README_TASK_ROW,
            "readme task row",
        )
    text = replace_row(
        text,
        "**合计未完成验收项**",
        README_COUNTS,
        "readme counts",
    )
    if "**本轮（2026-11-26 · 流动性维护槽的价格源依赖）**" not in text:
        text = insert_before(
            text,
            "> **本轮（2026-11-26 · 流动性维护槽的 RPC 故障转移 ＋ 部署批次）**：",
            README_NOTES,
            "readme notes",
        )
    write(README, text)

    text = read(DOCS_README)
    if restore("[@流动性维护槽的价格源依赖_20261126.md@]") not in text:
        text = insert_after(
            text,
            "| [@流动性维护槽的RPC故障转移_20261126.md@]",
            DOCS_DOC_ROW,
            "docs doc row",
        )
    if restore("| [@T-核.06@](../tasks/T-核.06/)") not in text:
        text = insert_after(
            text,
            "| [@T-核.05@](../tasks/T-核.05/)",
            DOCS_TASK_ROW,
            "docs task row",
        )
    write(DOCS_README, text)
    print("update_registry_he06: ok")


if __name__ == "__main__":
    main()
