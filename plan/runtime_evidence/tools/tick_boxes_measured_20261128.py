"""Tick the checklist boxes whose own criterion is met by a measured reading.

A box is ticked only when its own wording is satisfied by a reading that the
probe took from the live store.  The status note under the box is rewritten
to carry that reading, so the checklist states the measurement instead of an
opinion.  Every replacement is asserted, so a drifted source aborts the run.
"""

from __future__ import annotations

from pathlib import Path


def patch(path: Path, replacements: list[tuple[str, str]]) -> int:
    # ``newline=""`` keeps the CRLF line endings the checklists are written
    # in; the default text mode would fold them into ``\n`` and the patterns
    # below would never match.
    with path.open("r", encoding="utf-8", newline="") as handle:
        text = handle.read()
    changed = 0
    for old, new in replacements:
        if text.count(old) != 1:
            raise SystemExit(f"{path}: expected exactly one match, got {text.count(old)}")
        text = text.replace(old, new)
        changed += 1
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return changed


D178 = Path("plan/tasks/P-01-池子重构/tasks/T-广.03/D-178-checklist.md")
D201 = Path("plan/tasks/P-01-池子重构/tasks/T-广核.01/D-201-checklist.md")


D178_REPLACEMENTS: list[tuple[str, str]] = [
    (
        "- [ ] 实盘读数：复核行 `pool_ids_json` ＝ **方向池 id**（`dpool:v1:…:<base>:<dir>`）\r\n"
        "- [ ] 实盘读数：`state_transition_gate_failed:expected_pool_state_unavailable` **归零**\r\n"
        "- [ ] 实盘读数：灰尘那一行**不再**进 `manager_v4_review_broadcast_requests`\r\n"
        "- [ ] 实盘读数：**首笔真实广播**落 `manager_cow_execution_attempts` ＋ 月度库\r\n"
        "\r\n"
        "      > **状态**：**未做**。前三框是**部署项**（新二进制上线后即可读）；\r\n"
        "      > 第四框是**市场条件**（`F-2` 已于第四轮闭合，见 §5.3）。",
        "- [x] 实盘读数：复核行 `pool_ids_json` ＝ **方向池 id**（`dpool:v1:…:<base>:<dir>`）\r\n"
        "- [x] 实盘读数：`state_transition_gate_failed:expected_pool_state_unavailable` **归零**\r\n"
        "- [x] 实盘读数：灰尘那一行**不再**进 `manager_v4_review_broadcast_requests`\r\n"
        "- [ ] 实盘读数：**首笔真实广播**落 `manager_cow_execution_attempts` ＋ 月度库\r\n"
        "\r\n"
        "      > **状态**：**前三框已读**（2026-11-28，生产库只读 `mode=ro` ＋ `query_only=ON`，\r\n"
        "      > 探针 `plan/runtime_evidence/tools/probe_checklist_readings_20261128.py`）：\r\n"
        "      > 当前目录 **8** 条复核行的 `pool_ids_json` **全部**是 `dpool:v1:` 方向池 id、\r\n"
        "      > **全部 3 跳**（`hopCountHistogram = {3: 8}`、`directionalRows = 8`、`notDirectionalRows = []`）；\r\n"
        "      > `manager_v4_review_diagnostics` 全表 `expected_pool_state_unavailable` **0** 行；\r\n"
        "      > `manager_v4_review_broadcast_requests` **0** 行，而**灰尘级正利润复核行有 7 条**\r\n"
        "      > （`loop_options_unavailable`，量级 **1.2e-05 … 7.67e-09**）⇒ 灰尘行确实没进广播请求表。\r\n"
        "      > 第四框**仍是市场条件**：`runArbitrage` 正收益复核 **0** 条 ⇒ 链上没有可广播的盈利。",
    ),
    (
        "- [ ] 实盘读数：阶梯载荷出现 `searchCeilingRaw` ／ `searchCeilingBasis`\r\n"
        "\r\n"
        "      > **状态**：**投影已落地**（`review_publish.rs::static_preview` 把内核阶梯载荷的\r\n"
        "      > `searchCeilingRaw` ／ `searchCeilingBasis` 带进复核行；新单测\r\n"
        "      > `the_published_review_carries_the_ceiling_the_ladder_searched_inside` **通过**）\r\n"
        "      > ⇒ **待第二次部署**后即可读。",
        "- [x] 实盘读数：阶梯载荷出现 `searchCeilingRaw` ／ `searchCeilingBasis`\r\n"
        "\r\n"
        "      > **状态**：**已读**（2026-11-28）：`manager_v4_reviews` **176** 行里 **174** 行的\r\n"
        "      > `static_preview_json` 同时带 `searchCeilingRaw` ＋ `searchCeilingBasis`（**11** 键）；\r\n"
        "      > **当前目录 8 行全部带**。缺的 **2** 行是**遗留闭环** `0x0000…0000` 的历史行\r\n"
        "      > （2026-11-27T14:41/14:42Z，编排器**摘要**证据，**9** 键），不在当前目录内。",
    ),
    (
        "- [ ] 实盘读数：`expected_amount_crosses_tick` **归零**（`F-3` 落地后的直接读数）\r\n"
        "\r\n"
        "      > **状态**：**当前代次本就没有这一行**（新二进制写的 **10** 行理由是\r\n"
        "      > `runArbitrage returned ok=false` **9** ＋ PnL 缺失 **1**）⇒ **触发条件未出现**，\r\n"
        "      > **不能**据此说「归零」。",
        "- [x] 实盘读数：`expected_amount_crosses_tick` **归零**（`F-3` 落地后的直接读数）\r\n"
        "\r\n"
        "      > **状态**：**已读**（2026-11-28）：`manager_v4_review_diagnostics` 全表\r\n"
        "      > `expected_amount_crosses_tick` **0** 行。**这次不是空档**——`F-3` 之后**真的走到过**\r\n"
        "      > 状态迁移闸门的那条诊断（闭环 `0xdf0992e4…`，跳 1 带 `amountInRaw`）把理由判成\r\n"
        "      > `hop_2_failed:tick_range_changed`，**不是** `expected_amount_crosses_tick`\r\n"
        "      > ⇒ 闸门**被行使过**且没判成它。当前目录 8 条复核停在\r\n"
        "      > `runArbitrage returned ok=false`，本轮闸门未触发。",
    ),
    (
        "- [ ] 实盘读数：`final_input_exceeds_single_tick_capacity` **归零**（`F-2` 落地后的直接读数）\r\n"
        "\r\n"
        "      > **状态**：同上，**触发条件未出现**（没有一行走到容量门禁）。\r\n"
        "      > 「选出的金额 > 容量」的读数是 **0** 行，但那是**旧二进制**也有的读数。",
        "- [x] 实盘读数：`final_input_exceeds_single_tick_capacity` **归零**（`F-2` 落地后的直接读数）\r\n"
        "\r\n"
        "      > **状态**：**已读**（2026-11-28）：全表 `final_input_exceeds_single_tick_capacity`\r\n"
        "      > **0** 行。同样**不是空档**——`F-3` 之后走到单跳闸门的那条路由，跳 1 的\r\n"
        "      > `candidateCapacityRaw = 511287708386`、`capacityBasis = ExpectedState` 都算出来了，\r\n"
        "      > 却**没有**判成「金额超容量」。`review_manager` 的分支顺序是\r\n"
        "      > `single_tick_capacity_unavailable` → **`final_input_exceeds_single_tick_capacity`**\r\n"
        "      > → `route_state_unavailable`，容量比较排在 `route_state_unavailable` **之前**\r\n"
        "      > ⇒ 「没判成容量超限」来自**比较本身**，不是提前返回。",
    ),
]


def main() -> None:
    total = patch(D178, D178_REPLACEMENTS)
    print(f"patched {total} blocks")


if __name__ == "__main__":
    main()
