"""Split the round note of the P-01 README into the lines it should have.

The note was written as one Python string, so the block quotes ran into
one line.  The repair splits it back into one line per point.

The script is idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mdpatch import NL, read, restore, write  # noqa: E402


ROOT = Path(__file__).resolve().parents[3]
README = ROOT / "plan" / "tasks" / "P-01-池子重构" / "README.md"

HEAD = "> **本轮（2026-11-26 · 方向 ↔ 价格排序方向守卫 ＋ 快照写入路径登记）**："
POINTS = [
    "> ① 新登记 **2** 个二级任务（@T-核.03@ / @T-方.03@）⇒ 登记二级任务 **27 → 29**、"
    "在盘 **11 → 13**；",
    "> ② 两者的 checklist **全闭合** ⇒ **未勾选框读数不变**（**28 ＋ 8 ＋ 51 ＝ 87**，"
    "复算见 [@D-014@](./docs/D-014-未完成任务清单_20261123.md) `1.3）；",
    "> ③ 用户口径「价格排序方向与池子方向是固定的」**已核实成立**：方向只决定**目标代币**、"
    "排序**恒为降序**，四处口径一致 ⇒ **无需修正行为**，补的是**守卫**"
    "（**J-1** 方向反了会红 ／ **J-2** 同价低费率在前 ／ **J-3** 动态费率池不进排名）＋ **变异测试**；",
    "> ④ 产品代码改动**只有注释** ⇒ **无部署闸门**；遗留 **R-1**（跨费率档的**价格加权排序**，"
    "用户口径「后续」）已留方案未实现。",
]


def main() -> None:
    text = read(README)
    lines = text.split(NL)
    out: list[str] = []
    changed = False
    for line in lines:
        if line.startswith(HEAD) and line != HEAD:
            out.append(HEAD)
            out.extend(restore(point) for point in POINTS)
            changed = True
            continue
        out.append(line)
    if changed:
        write(README, NL.join(out))
    print("repair_p01_readme_fang03_note: %s" % ("ok" if changed else "already repaired"))


if __name__ == "__main__":
    main()
