"""Fix the D-001 migration-log table header and register this round."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mdpatch

NL = chr(10)
ROOT = Path(__file__).resolve().parents[3]
DOC = ROOT / "plan" / "D-001-TASK_REGISTRY.md"

ENTRIES = "| 2026-11-25 | **剩余任务总结与分类裁定（`D-136`）**：新增 `plan/tasks/P-01-池子重构/docs/D-136-剩余任务清单总结与分类裁定_20261125.md` —— 把剩余清单按「**能不能现在做完**」分四类（**B 待做 5** / **C 阻塞 25** / **D 裁定后置 6** / **E 常设守卫 63** ＝ **99**），并给出解锁条件与下一轮动作序列。**本轮闭合 `D-004` §1 分解完整性 5 项 ＋ §3 整体回归 3 项**（架构守卫 **80 passed**、记录层 **90 passed**、`cargo build --offline --workspace` **exit 0**）；**口径订正**：任务归属未完成数 **75 → 36**（旧 **75** 与旧逐行合计 **48** 本就不相等 ⇒ 本轮统一为「逐任务目录 `checklist.md` 的 `- [ ]` 计数」，**§1.1 与 §1.2 逐行合计现在相等**）；**5** 个已归档任务的旧读数合计 **14** 项**归零**（归档件内条目**均已勾选**）；**文档断链 4 → 0**（checked **298** / broken **0**） |\n| 2026-11-25 | **复核发布阻塞的实测登记（`D-136` §3.3）**：复核代次**停在 `review_version = 115`**（生成于 **10:35:31 +08**），运行时自 **11:00:50** 起持续报 `pool_manager_refresh_required` / **`refreshCause = candidate_pool_key_missing`**（至 11:38 共 **764** 条）⇒ `T-池复.01`(3) / `T-复编.02`(7) / `T-编.01`(3) 共 **13** 项同时被挡住。**已测实**：闭环第 2 跳引用 **`candidate_exchange` 作用域**的方向池，而该作用域 `pool_scope_checksum = 2bea9bb4` 与实盘 `core4` 池域 `ae6b14ac` **不一致**；候选兑换池对闭环 **80** 个 token 对的**配对覆盖缺口 = 0** ⇒ 缺的是**池键解析**而非配对。**未测实**：究竟是作用域池域过期还是池刷新后池域变小 ⇒ **如实登记，不代为勾框** |\n"


def main() -> None:
    text = mdpatch.read(DOC)
    lines = text.split(NL)

    # drop the misplaced table header / separator
    kept = []
    for line in lines:
        if line.strip() == "| 日期 | 变更 |" or line.strip() == "|---|---|":
            continue
        kept.append(line)
    lines = kept

    head = [i for i, line in enumerate(lines) if line.strip() == "## 7. 迁移记录"]
    if len(head) != 1:
        raise SystemExit("update_d001: section 7 anchor count %d" % len(head))
    start = head[0]
    lines[start + 1 : start + 1] = ["", "| 日期 | 变更 |", "|---|---|---"] + ENTRIES.split(NL)
    text = NL.join(lines)

    text = mdpatch.insert_after(
        text,
        "| `D-135` | 单 tick 门禁版本漂移根因与实测 |",
        "| `D-136` | 剩余任务清单总结与分类裁定 | P-01 汇总 | `plan/tasks/P-01-池子重构/docs/D-136-剩余任务清单总结与分类裁定_20261125.md` |",
        "d136 row",
    )

    mdpatch.write(DOC, text)
    print("update_d001: ok")


if __name__ == "__main__":
    main()

