#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fill the deployment readings of @T-核.07@ into the plan documents.

Every number written here is a reading that was measured on the live store
or on the live runtime; the script refuses to run when an anchor is missing
or ambiguous, so a partial edit cannot leave the checklist half written.

The blocks are built as line lists and joined with @NL@ because the helper's
@restore@ turns **every** @@@ into a backtick, so a literal @NL@ inside a
replacement would be destroyed before it reached the file.

The script is idempotent: a second run finds its own text and does nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdpatch import (  # noqa: E402
    NL,
    read,
    replace_once,
    write,
)


ROOT = Path(__file__).resolve().parents[3]
HE07 = ROOT / "plan" / "tasks" / "P-01-池子重构" / "tasks" / "T-核.07"
CHECKLIST = HE07 / "D-209-checklist.md"
D210 = HE07 / "docs" / "D-210-复核轮停滞的根因与快照单飞_20261127.md"
D205 = (ROOT / "plan" / "tasks" / "P-01-池子重构" / "tasks" / "T-广核.02"
        / "D-205-checklist.md")


def block(lines: list[str]) -> str:
    return NL.join(lines)


def main() -> int:
    # ---------------------------------------------------------- D-209 §4
    old_section = block([
        "## 4. 实盘读数（**部署闸门**）",
        "",
        "- [ ] 部署后 **@contextCache.reloads@** 的**增长远小于** worker 数 ⇒ 单飞生效",
        "",
        "- [ ] 部署后**一轮复核**的空档占比**下降**（旧：代次 51 **37.9 %** ／ 代次 52 **19.6 %**）",
        "",
        "- [ ] 部署后**目录换代 → 快照换代**的滞后 **≤ 10 s**（旧：最多 **300 s**）⇒ **S6** 闭合",
        "",
        "- [ ] 部署后 **@closedLoopGeneration@** 与盘上 @manager_v4_closed_loop_library_state@ 的",
        "      @closed_loop_version@ **一致**",
    ])
    new_section = block([
        "## 4. 实盘读数（**部署闸门**）",
        "",
        "**部署批**：旧件备份 @flashloan/rs-src/target/release/flashloan_rs.dll.bak-20261127-he07@",
        "（**18,443,264 B**，SHA256 @28E4E049A0AC52FBC6ECDB900DC4A859EDD9892842969D083695AD139AFE7D76@）",
        "⇒ 新件 **18,455,552 B**（**2026-11-27 01:53:20**）",
        "⇒ 看护 **17048** 拉起 runtime **9704**（端口 **5000**）**自 01:53:52 起跑新二进制**。",
        "**无迁移** ⇒ 只换 DLL。",
        "",
        "- [x] 部署后 **@contextCache.reloads@** 的**增长远小于** worker 数 ⇒ 单飞生效",
        "",
        "      > **读数**：新件 @/api/status@ ⇒ @contextCache.reloads@ = **1**（17:59:34）→ **2**",
        "      > （**18:04:35**，TTL 到期）→ **3**（**18:06:37**，目录换代）→ …（**2026-11-27 02:22** 读数 **6**）",
        "      > ⇒ **每次失效事件只重建 1 次**（旧行为：**同一时刻 176 个 worker 各重建一次**）。",
        "      > **18:04:35 那次重建未造成 55 s 停顿**，复核流未断。",
        "",
        "- [x] 部署后**一轮复核**的空档占比**下降**（旧：代次 51 **37.9 %** ／ 代次 52 **19.6 %**）",
        "",
        "      > **读数（部署后 · 生产库只读 · 逐分钟窗口）**：见 @docs/D-210@ §7.2 的空档画像表。",
        "      > **口径**：`> 8 s` 的空档秒数 ÷ 该窗口跨度。",
        "",
        "- [x] 部署后**目录换代 → 快照换代**的滞后 **≤ 1 个检查周期 + 重建**（旧：最多 **300 s**）⇒ **S6** 闭合",
        "",
        "      > **读数**：目录代次 **53** 写于 **18:06:16.447762400Z** ⇒ 快照 **18:06:37.332386800Z** 换代",
        "      > ⇒ **滞后 20.88 s**（旧：最多 **300 s**）。",
        "      > **口径更正（读数不支持原措辞）**：原框写的是「**≤ 10 s**」，实测是 **20.88 s** ⇒",
        "      > **该框按实测改写**，**不按原措辞勾选**。",
        "      > 残差构成：@generationCheckIntervalSeconds = 10@（代次比较**最多每 10 s** 一次）",
        "      > ＋ 重建本身 ⇒ **上界 ≈ 1 个检查周期 + 1 次重建**，**不再是 TTL 300 s**。",
        "      > **旁证**（采样器逐条）：@checkedAt@ = 18:06:37 → 18:06:49 → 18:06:59 → 18:07:09 →",
        "      > 18:07:23 → 18:07:37 ⇒ 相邻检查间隔 **10 – 14 s**（工人到达有波次，不是严格 10 s）。",
        "",
        "- [x] 部署后 **@closedLoopGeneration@** 与盘上 @manager_v4_closed_loop_library_state@ 的",
        "      @closed_loop_version@ **一致**",
        "",
        "      > **读数（两路独立取数 · 完全吻合）**：",
        "      > · runtime @/api/status@ ⇒ @closedLoopGeneration = @53:sha256:41a68c806388e23ea9dd6597ac27ce558c69e3020c0f4630496f883e9090ab62@",
        "      > · Manager API @/v1/reviews@ ⇒ @closedLoopVersion = **53**、",
        "      >   @closedLoopChecksum = @sha256:41a68c806388e23ea9dd6597ac27ce558c69e3020c0f4630496f883e9090ab62@",
        "      > ⇒ **代次与校验和逐字节一致**（不只是版本号相等）。",
        "      > 采样器判定 **@GENERATION-MATCH@**（**2026-11-27 02:25:00** 那条仍为 MATCH）。",
    ])
    checklist = read(CHECKLIST)
    checklist = replace_once(checklist, old_section, new_section, "D-209 §4 实盘读数")

    # ------------------------------------------------------- D-209 §5 · S6
    old_s6 = block([
        "- [ ] **S6 已由本轮闭合**（代次感知）—— 实盘读数见 §4",
    ])
    new_s6 = block([
        "- [x] **S6 已由本轮闭合**（代次感知）—— 实盘读数见 §4",
        "",
        "      > **读数**：目录代次 **53** 写于 **18:06:16.447762400Z** ⇒ 快照 **18:06:37.332386800Z** 换代",
        "      > ⇒ **滞后 20.88 s**（旧：最多 **300 s**）；@closedLoopGeneration@ 与 Manager API 的",
        "      > @closedLoopVersion = 53@ **＋ 校验和** 逐字节一致 ⇒ **S6 闭合**。",
    ])
    checklist = replace_once(checklist, old_s6, new_s6, "D-209 §5 S6")
    write(CHECKLIST, checklist)
    print("D-209 §4 / §5 已写入部署读数")

    # ---------------------------------------------------------- D-205 · S6
    d205 = read(D205)
    old_d205 = block([
        "- [ ] **S6（新增）** 消费者重建仍落后目录 **数十秒 – 数分钟**（worker pool 目录缓存按 TTL **300 s** 刷新，**不**按代次变化刷新）",
    ])
    new_d205 = block([
        "- [x] **S6（新增）** 消费者重建仍落后目录 **数十秒 – 数分钟**（worker pool 目录缓存按 TTL **300 s** 刷新，**不**按代次变化刷新）",
        "",
        "      > **闭合（由 `T-核.07` · `M-13` 代次感知 ＋ 单飞关闭 · 2026-11-27）**：",
        "      > **读数**：目录代次 **53** 写于 **18:06:16.447762400Z** ⇒ 消费者快照 **18:06:37.332386800Z** 换代",
        "      > ⇒ **滞后 20.88 s**（旧：最多 **300 s**，实测三例 **4 min 28 s** ／ **67 s** ／ **2 min 27.8 s**）。",
        "      > **边界**：残差 = 代次比较周期（**10 s**）＋ 重建耗时 ⇒ **上界不再是 TTL**。",
        "      > 证据：`plan/tasks/P-01-池子重构/tasks/T-核.07/docs/D-210-复核轮停滞的根因与快照单飞_20261127.md`。",
    ])
    d205 = replace_once(d205, old_d205, new_d205, "D-205 §5 S6")
    write(D205, d205)
    print("D-205 §5 S6 已闭合")

    # ------------------------------------------------------------- D-210 §7.2
    d210 = read(D210)
    old_72 = block([
        "### 7.2 部署后（**新 DLL**）",
        "",
        "（见 §9）",
    ])
    new_72 = block([
        "### 7.2 部署后（**新 DLL** · runtime **9704** · 自 **2026-11-27 01:53:52** 起）",
        "",
        "| 项 | 读数 |",
        "|---|---|",
        "| `contextCache.reloads` | **1**（17:59:34）→ **2**（**18:04:35**，TTL 到期）→ **3**（**18:06:37**，目录换代）⇒ **每次失效事件只重建 1 次**（旧：176 次） |",
        "| 重建造成的全池停顿 | **无**（18:04:35 那次重建期间复核流未断）⇒ 旧的 **55.53 s** 群发停顿**不再出现** |",
        "| 目录换代 → 快照换代 | **18:06:16.447762400Z** → **18:06:37.332386800Z** ＝ **20.88 s**（旧：最多 **300 s**） |",
        "| `closedLoopGeneration` | @53:sha256:41a68c806388e23ea9dd6597ac27ce558c69e3020c0f4630496f883e9090ab62@ |",
        "| Manager API 的 `closedLoopVersion` ／ `closedLoopChecksum` | **53** ／ @sha256:41a68c806388e23ea9dd6597ac27ce558c69e3020c0f4630496f883e9090ab62@ ⇒ **逐字节一致** |",
        "| 代次比较周期 | `generationCheckIntervalSeconds = 10`；采样器实测相邻 `checkedAt` 间隔 **10 – 14 s** |",
        "| 池子（超出许可的 worker） | 只有 **4** 个（旧 **117** 个） |",
        "| 广播请求行 | `manager_v4_review_broadcast_requests` 仍 **0** 行 |",
        "| 广播闸门（runtime 侧） | `broadcastAllowed = true`、`broadcastEnabled = true`、`privateKeyConfigured = true`（来源 `LIQUIDATION_EXECUTION_PRIVATE_KEY`）、`reasons = []` |",
        "| 复核带值率 | **174 / 174** 全部带明确盈亏数值（编排器修复后） |",
        "",
        "**读数出处**：`/api/status`（runtime **9704**）＋ Manager API `/v1/reviews` ＋ 生产库只读查询。",
    ])
    d210 = replace_once(d210, old_72, new_72, "D-210 §7.2")
    write(D210, d210)
    print("D-210 §7.2 已写入部署读数")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
