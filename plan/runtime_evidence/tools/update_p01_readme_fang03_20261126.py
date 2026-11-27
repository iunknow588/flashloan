"""Add the two new secondary tasks to the P-01 task table.

The table rows are anchored on a **unique row prefix**, because the
header of the table is not unique in the document.

The script is idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdpatch import NL, insert_after, insert_before, read, restore, write  # noqa: E402


ROOT = Path(__file__).resolve().parents[3]
README = ROOT / "plan" / "tasks" / "P-01-池子重构" / "README.md"

TABLE_ANCHOR = "| [@T-闭.03@](./tasks/T-闭.03/) |"
ROWS = (
    "| [@T-核.03@](./tasks/T-核.03/) | 流动性快照写入路径接线 | @AC-核.03@ | "
    "@M-13@ ＋ @M-1@ | M-1 | @@W-1@@ | **0** | ✅ **代码已闭合 · 16 / 16 项**（2026-11-26）："
    "快照表 @v4_exchange_pool_liquidity_snapshots@ 的**唯一写入方**（池管理器）"
    "**只能由命令行调用** ⇒ 运行时**量到了却写不进去**；worker 却因「裁决都在」报 "
    "@complete@ ⇒ **陈旧被当成完整**（快照行最新 **2026-11-25T00:44Z**，比 worker 游标的区块号旧 "
    "**2,580,566** 个区块）。**落地**：新增 Rust 入口 @refresh_v4_pool_liquidity@ ＋ 维护槽调用"
    "（入口不可用或拒绝 ⇒ **整槽失败**、**不降级**）＋ 池管理器回执改报**真正改动的行数**；"
    "**新鲜度只做观测量**，**未**进任何选池判据。**回归** @pool-manager@ **167** ／ "
    "@flashloan-rs@ **222** ／ pytest **19 ＋ 3**。**部署闸门待办**（实盘扩展**未重建**、"
    "runtime **未重启**）⇒ 与 @T-闭.02@ / @T-闭.03@ / @T-核.02@ **同窗口** ⇒ **C 类 · 阻塞** |"
)
ROW_FANG = (
    "| [@T-方.03@](./tasks/T-方.03/) | 方向池的价格排序方向守卫 | @AC-方.03@ | @M-3@ | "
    "M-1 | @@W-1@@ | **0** | ✅ **已闭合 · 11 / 11 项**（2026-11-26）：用户口径"
    "「价格排序方向与池子方向是固定的」**已核实成立** —— 两种写法"
    "（**方向决定升降序** ／ **方向决定目标代币 ＋ 固定降序**）**数学上是同一个序**，"
    "本系统四处口径**本就是后者**（方向只决定目标代币、排序恒为降序）⇒ **无需修正行为**，"
    "交付的是**不变式注释 ＋ 三条守卫单测**（**J-1** 方向反了会红 ／ **J-2** 同价时低费率在前 ／ "
    "**J-3** 动态费率池不进排名）＋ **变异测试**证明 J-1 不空转。**回归** "
    "@directional-pool-manager@ **30 passed / 0 failed**（基线 **27** ＋ 新增 **3**）＋ "
    "@manager-contracts@ **17 passed / 0 failed**。**产品代码的改动只有注释** ⇒ "
    "**无部署闸门**、实盘**零**写入。**遗留**：R-1（跨费率档的**价格加权排序**，用户口径「后续」）"
    "已留方案未实现 |"
)

NOTE_ANCHOR = "> **本轮（2026-11-25 11:45 +08 · 剩余任务总结与分类）**："
NOTE = NL.join(
    [
        "> **本轮（2026-11-26 · 方向 ↔ 价格排序方向守卫 ＋ 快照写入路径登记）**：",
        "> ① 新登记 **2** 个二级任务（@T-核.03@ / @T-方.03@）⇒ 登记二级任务 **27 → 29**、"
        "在盘 **11 → 13**；",
        "> ② 两者的 checklist **全闭合** ⇒ **未勾选框读数不变**（**28 ＋ 8 ＋ 51 ＝ 87**，"
        "复算见 [@D-014@](./docs/D-014-未完成任务清单_20261123.md) `1.3）；",
        "> ③ 用户口径「价格排序方向与池子方向是固定的」**已核实成立**：方向只决定**目标代币**、"
        "排序**恒为降序**，四处口径一致 ⇒ **无需修正行为**，补的是**守卫**"
        "（**J-1** 方向反了会红 ／ **J-2** 同价低费率在前 ／ **J-3** 动态费率池不进排名）"
        "＋ **变异测试**；",
        "> ④ 产品代码改动**只有注释** ⇒ **无部署闸门**；遗留 **R-1**（跨费率档的**价格加权排序**，"
        "用户口径「后续」）已留方案未实现。",
    ]
)


def main() -> None:
    text = read(README)
    if restore("| [@T-核.03@](./tasks/T-核.03/) |") not in text:
        text = insert_after(text, TABLE_ANCHOR, ROWS + NL + ROW_FANG, "task rows")
    if restore(NOTE) not in text:
        text = insert_before(text, NOTE_ANCHOR, NOTE + NL, "round note")
    write(README, text)
    print("update_p01_readme_fang03: ok")


if __name__ == "__main__":
    main()
