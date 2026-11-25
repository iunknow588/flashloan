#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Append the generation-span warning row to the D-104 honest-register table.

The row is written from segments so the source file needs no backtick
escaping, and the target keeps CRLF and stays BOM-free.
"""

import io
import os
import sys

ROOT = r"E:\2026OPC大赛\flashLoan"
DOC = os.path.join(
    ROOT,
    r"plan\tasks\P-01-池子重构\tasks\T-复编.02\docs\D-104-复核完整性实测.md",
)
BT = chr(96)

ROW = (
    "| **⚠️ 代次跨度在增长，逼近 600s 观测窗** | "
    "钉住快照的 " + BT + "B1" + BT + " 读数：跨度 **370.8s**（" + BT + "review_version" + BT + " 36）/ "
    "**366.3s**（38）/ **484.8s**（39）⇒ **484.8s ＝ 600s 的 81 %**。⇒ 一旦**超过 600s**，"
    "同一代的**最早几条**复核在**代次写完时**即成 " + BT + "stale" + BT + " ⇒ " + BT + "B1" + BT +
    " 会由 MET 翻成 NOT MET（**这是观测窗的口径，不是发布层缺陷**）。**120s 广播门禁不受影响**"
    "（它**逐条在发布时刻**判定）。⇒ 归 " + BT + "M-7" + BT + "（广播层），**如实登记**，**不代为修复** |\n"
)


def main():
    with io.open(DOC, "r", encoding="utf-8", newline="") as handle:
        text = handle.read()
    normalized = text.replace("\r\n", "\n")
    if "代次跨度在增长，逼近 600s 观测窗" in normalized:
        print("[D-104] the span row is already present")
        return 0
    lines = normalized.split("\n")
    index = None
    for position in range(len(lines) - 1, -1, -1):
        if lines[position].startswith("| **广播门禁**"):
            index = position
            break
    if index is None:
        raise SystemExit("[D-104] the broadcast row was not found")
    lines.insert(index + 1, ROW.rstrip("\n"))
    out = "\n".join(lines).replace("\n", "\r\n")
    with io.open(DOC, "w", encoding="utf-8", newline="") as handle:
        handle.write(out)
    print("[D-104] the span row was appended")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
