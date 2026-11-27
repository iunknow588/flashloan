"""Register T-核.03 and T-方.03 in the remaining work list (D-014).

Both tasks carry **closed** checklists, so the number of unchecked boxes
does not move; what moves is the number of registered and on-disk tasks.
The rows are added with the same helper the earlier rounds used
(@mdpatch@), anchored on a **unique row prefix**.

The script is idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdpatch import NL, insert_after, read, replace_once, restore, write  # noqa: E402


ROOT = Path(__file__).resolve().parents[3]
P01 = ROOT / "plan" / "tasks" / "P-01-池子重构"
D014 = P01 / "docs" / "D-014-未完成任务清单_20261123.md"


TABLE_ANCHOR = "| **27** | **@T-闭.03@** |"
ROWS = (
    "| **28** | **@T-核.03@** | 流动性快照写入路径接线 | @AC-核.03@ | **P0** | "
    "@M-13@ ＋ @M-1@ | **0** | ✅ **代码已闭合 · 16 / 16 项**（2026-11-26）：快照表 "
    "@v4_exchange_pool_liquidity_snapshots@ 的**唯一写入方**（池管理器）**只能由命令行调用** ⇒ "
    "运行时**量到了却写不进去**；worker 却因「裁决都在」报 @complete@ ⇒ **陈旧被当成完整**"
    "（快照行最新 **2026-11-25T00:44Z**，比 worker 游标的区块号旧 **2,580,566** 个区块）。"
    "**落地**：新增 Rust 入口 @refresh_v4_pool_liquidity@ ＋ 维护槽调用（入口不可用或拒绝 ⇒ "
    "**整槽失败**、**不降级**）＋ 池管理器回执改报**真正改动的行数**；"
    "**新鲜度只做观测量**，**未**进任何选池判据。**回归** @pool-manager@ **167** ／ "
    "@flashloan-rs@ **222** ／ pytest **19 ＋ 3**。**未勾选 0 项**，但**部署闸门未完成**"
    "（实盘扩展**未重建**、runtime **未重启**）⇒ 与 @T-闭.02@ / @T-闭.03@ / @T-核.02@ "
    "**同窗口** ⇒ **C 类 · 阻塞** | @M-1@ | 否（纯代码；实盘生效属部署闸门） | "
    "[@T-核.03/@](../tasks/T-核.03/) |"
)
ROW_29 = (
    "| **29** | **@T-方.03@** | 方向池的价格排序方向守卫 | @AC-方.03@ | **P0** | @M-3@ | "
    "**0** | ✅ **已闭合 · 11 / 11 项**（2026-11-26）：用户口径「价格排序方向与池子方向是固定的」"
    "**已核实成立** —— 两种写法（**方向决定升降序** ／ **方向决定目标代币 ＋ 固定降序**）"
    "**数学上是同一个序**，本系统四处口径**本就是后者**（方向只决定目标代币、排序恒为降序）⇒ "
    "**无需修正行为**，交付的是**不变式注释 ＋ 三条守卫单测**（**J-1** 方向反了会红 ／ "
    "**J-2** 同价时低费率在前 ／ **J-3** 动态费率池不进排名）＋ **变异测试**证明 J-1 不空转。"
    "**回归** @directional-pool-manager@ **30 passed / 0 failed**（基线 **27** ＋ 新增 **3**）"
    "＋ @manager-contracts@ **17 passed / 0 failed**。**产品代码的改动只有注释** ⇒ "
    "**无部署闸门**、实盘**零**写入。**遗留**：R-1（跨费率档的**价格加权排序**，用户口径「后续」）"
    "已留方案未实现 | @M-1@ | 否（纯代码 ＋ 单测） | [@T-方.03/@](../tasks/T-方.03/) |"
)
ROWS = ROWS + NL + ROW_29

COUNT_EDITS = [
    (
        "实测（**28** ＋ **8** ＋ **51**）；复算见 §1.3（@recount_remaining_tasks_20261125.py@，"
        "**2026-11-26** 实测）",
        "实测（**28** ＋ **8** ＋ **51**）；复算见 §1.3（@recount_remaining_tasks_20261125.py@，"
        "**2026-11-26** 实测）；**2026-11-26** 再登记 @T-核.03@ / @T-方.03@ —— "
        "两者的 checklist **全闭合** ⇒ 未勾选框读数**不变**（**87**）",
        "unchecked total note",
    ),
    (
        "见 §1.2（登记 **27** 个二级任务，在盘 **11** ＋ 已闭合归档 **16**）",
        "见 §1.2（登记 **29** 个二级任务，在盘 **13** ＋ 已闭合归档 **16**）",
        "registered task count",
    ),
]


def main() -> None:
    text = read(D014)
    if restore("**28** | **@T-核.03@**") not in text:
        text = insert_after(text, TABLE_ANCHOR, ROWS, "task rows")
    else:
        print("update_d014_fang03: task rows already there")
    for old, new, label in COUNT_EDITS:
        if restore(old) in text:
            text = replace_once(text, old, new, label)
    write(D014, text)
    print("update_d014_fang03: ok")


if __name__ == "__main__":
    main()
