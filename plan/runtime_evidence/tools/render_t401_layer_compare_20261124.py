#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render the layer-by-layer before/after comparison into D-104.

The publish layer dropped the amount evidence twice: the first time it left
the chain entirely, the second time it left the **reason** inside a sub
object.  This renders the three measured states side by side so the reader can
see which layer moved which number -- and see that a number that did not move
is a chain fact, not a publishing defect.

Usage:
    python render_t401_layer_compare_20261124.py <baseline.json> <after_layer5.json> <after_layer6.json>
"""

import io
import json
import os
import sys

ROOT = r"E:\2026OPC大赛\flashLoan"
DOC = os.path.join(
    ROOT, r"plan\tasks\P-01-池子重构\tasks\T-复编.02\docs\D-104-复核完整性实测.md"
)
START = "### 2.10 第五层 ＋ 第六层修前/修后对照（**三个测点 · 逐项**）"
END = "## 3. 读数"


def load(path):
    with io.open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def items(report):
    return {item["name"]: item for item in report.get("checklist", [])}


def observation(report, name, key, default=None):
    item = items(report).get(name)
    if not item:
        return default
    value = item.get("observations", {}).get(key, default)
    if isinstance(value, list):
        return len(value)
    return value


ROWS = (
    ("A1", "缺字段的复核条数", "incomplete", "条"),
    ("A2", "金额状态 = missing", "missing", "条"),
    ("A2", "其中**说得出原因**（`missingWithAReason`）", "missingWithAReason", "条"),
    ("A2", "其中**说不出原因**（`missingWithoutAnyReason`）", "missingWithoutAnyReason", "条"),
    ("A3", "PnL 缺失", "missing", "条"),
    ("A4", "`bestAmountMissing` 标志", "bestAmountMissingFlags", "条"),
    ("A8", "只读 healer：missing", "missing", "条"),
    ("A8", "只读 healer：ladderIncomplete", "ladderIncomplete", "条"),
    ("A8", "只读 healer：confirmed", "confirmed", "条"),
    ("B1", "600s 观测窗内 fresh", "freshReviews", "条"),
    ("B1", "600s 观测窗外 stale", "staleReviews", "条"),
    ("B2", "120s 广播门禁**放行**", "reviewsWithinAutomaticBroadcastAgeLimit", "条"),
    ("B2", "120s 广播门禁**拒答**", "reviewsOutsideAutomaticBroadcastAgeLimit", "条"),
)


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    baseline, layer5, layer6 = (load(path) for path in sys.argv[1:4])
    reports = (
        ("① 发布前基线", baseline),
        ("② 第五层修后", layer5),
        ("③ 第六层修后", layer6),
    )

    out = [START, ""]
    out.append("> **三个测点**（**同一口径** · 同一采集器 · 只读生产库）：")
    out.append(">")
    for label, report in reports:
        out.append(
            "> - **%s** —— 采集于 `%s`；`review_version` = **%s**，闭环数 = **%s**"
            % (
                label,
                report.get("generatedAt"),
                (report.get("reviewLibraryState") or {}).get("review_version"),
                (report.get("reviewLibraryState") or {}).get("loop_count"),
            )
        )
    out.append("")
    out.append("| 读数 | 单位 | %s | %s | %s |" % tuple(label for label, _ in reports))
    out.append("|---|---|---:|---:|---:|")
    for name, label, key, unit in ROWS:
        cells = []
        for _, report in reports:
            value = observation(report, name, key)
            cells.append("—" if value is None else str(value))
        out.append("| `%s` %s | %s | %s |" % (name, label, unit, " | ".join(cells)))
    out.append("")
    out.append("**读法**（**先看口径，再看数字**）：")
    out.append("")
    out.append(
        "- **① → ②** 是**第五层**的账：发布层第一次把金额证据丢在链路外 ⇒ "
        "`A1`/`A2`/`A8` 从「全量缺失」变成「只剩链上事实」。"
    )
    out.append(
        "- **② → ③** 是**第六层**的账：发布层第二次把「阶梯为什么没跑」的诊断"
        "留在 route 的**子对象**里 ⇒ **缺失条数不变**（链上事实不变），"
        "变的是**每一条缺失都带得说原因**。"
    )
    out.append(
        "- ⇒ **「缺失多少」与「缺的说不说得出原因」是两问**：后者才是缺陷；"
        "前者里**有原因**的部分是**链上事实**（revert / 状态闸门），按业务事实如实登记。"
    )
    out.append(
        "- `B1` / `B2` 三个测点**不是同一时刻**取的 ⇒ 只能读**同一行内**的"
        "①→②→③ 变化，**不可**跨行相减（采集时机不同 ⇒ 新鲜度不同）。"
    )
    out.append(
        "- **① 的分母是 164 条复核**（当时闭环目录只有 **164** 个闭环）；②③ 的分母是 "
        "**174** ⇒ ① 与 ②③ **不可**直接比**条数**，只能比**性质**与**比例**。"
    )
    out.append(
        "- ① 的 `A4` 有个**签名式**特征：`bestAmountMissingFlags=0` 而 "
        "`healerBestAmountMissing=150` ⇒ 发布层**整块丢了金额字段**，连标志位都没带出来。"
    )

    with io.open(DOC, "r", encoding="utf-8", newline="") as handle:
        text = handle.read()
    normalized = text.replace("\r\n", "\n")
    end = normalized.find(END)
    if end < 0:
        raise SystemExit("[D-104] the readings anchor was not found")
    start = normalized.find(START)
    if start >= 0:
        normalized = normalized[:start] + "\n".join(out) + "\n\n---\n\n" + normalized[end:]
    else:
        normalized = normalized[:end] + "\n".join(out) + "\n\n---\n\n" + normalized[end:]
    normalized = normalized.replace("\n", "\r\n")
    with io.open(DOC, "w", encoding="utf-8", newline="") as handle:
        handle.write(normalized)
    print("[D-104] layer comparison written")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
