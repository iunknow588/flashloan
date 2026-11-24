#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render the T-复.01 review-integrity readings into a markdown section.

The collector writes machine readings; this script only formats them.  It
never computes an acceptance verdict of its own -- every "met" flag comes
from the collector output.

Usage:
    python render_t401_readings_20261124.py <current.json> [<baseline.json>]
"""

import json
import sys


LIST_LIMIT = 6
TEXT_LIMIT = 160


def short(value, limit=LIST_LIMIT):
    if isinstance(value, list):
        if len(value) <= limit:
            return "、".join(short(item, limit) for item in value)
        head = "、".join(short(item, limit) for item in value[:limit])
        return "%s …… 共 **%d** 条" % (head, len(value))
    if isinstance(value, dict):
        return "、".join(
            "`%s`=%s" % (key, short(value[key], limit)) for key in sorted(value)
        )
    if isinstance(value, str) and len(value) > TEXT_LIMIT:
        return value[:TEXT_LIMIT] + "……（截断）"
    return str(value)


def load(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def item_table(report):
    rows = [
        "| 项 | 验收要求 | 判定 | 实测观测 |",
        "|---|---|---|---|",
    ]
    for item in report.get("checklist", []):
        observations = item.get("observations", {}) or {}
        rows.append(
            "| `%s` | %s | %s | %s |"
            % (
                item.get("name"),
                item.get("requirement"),
                "**通过**" if item.get("met") else "**未通过**",
                short(observations),
            )
        )
    return "\n".join(rows)


def comparison(current, baseline):
    if not baseline:
        return ""
    cur = {item["name"]: item for item in current.get("checklist", [])}
    base = {item["name"]: item for item in baseline.get("checklist", [])}
    rows = [
        "| 项 | 发布前基线 | 本次读数 | 变化 |",
        "|---|---|---|---|",
    ]
    changed = 0
    for name in sorted(cur):
        before = base.get(name, {}).get("met")
        after = cur[name].get("met")
        if before == after:
            mark = "—"
        else:
            mark = "**变化**"
            changed += 1
        rows.append(
            "| `%s` | %s | %s | %s |"
            % (
                name,
                "通过" if before else ("**未通过**" if before is not None else "—"),
                "通过" if after else "**未通过**",
                mark,
            )
        )
    rows.append("")
    rows.append("> 共 **%d** 项判定发生变化。" % changed)
    return "\n".join(rows)


def main():
    current = load(sys.argv[1])
    baseline = load(sys.argv[2]) if len(sys.argv) > 2 else None

    out = []
    out.append("### 3.1 采集器一次性输出（**%d** 项）" % len(current.get("checklist", [])))
    out.append("")
    out.append("采集时间：`%s`；判定口径：`%s`；存储：`%s`"
               % (current.get("generatedAt"), current.get("mode"), current.get("store")))
    out.append("")
    out.append(item_table(current))
    out.append("")

    out.append("### 3.2 上下游一致性（**复核库 vs 上游管理器**）")
    out.append("")
    out.append("| 字段 | 上游 | 复核库 | 一致 |")
    out.append("|---|---|---|---|")
    upstream = current.get("upstream", {}) or {}
    pairs = [
        ("闭环代次", "upstreamClosedLoopVersion", "reviewClosedLoopVersion", "closedLoopVersionMatches"),
        ("闭环校验和", "upstreamClosedLoopChecksum", "reviewClosedLoopChecksum", "closedLoopChecksumMatches"),
        ("候选边代次", "upstreamCandidateEdgeVersion", "reviewCandidateEdgeVersion", "candidateEdgeVersionMatches"),
        ("候选边校验和", None, None, "candidateEdgeChecksumMatches"),
        ("池域校验和", "upstreamPoolScopeChecksum", "reviewPoolScopeChecksum", "poolScopeChecksumMatches"),
    ]
    for label, up_key, rev_key, match_key in pairs:
        out.append("| %s | `%s` | `%s` | %s |" % (
            label,
            upstream.get(up_key, "—") if up_key else "—",
            upstream.get(rev_key, "—") if rev_key else "—",
            "是" if upstream.get(match_key) else "**否**",
        ))
    out.append("| 闭环数 | %s | %s | %s |" % (
        upstream.get("upstreamLoopCount"), upstream.get("reviewLoopCount"),
        "是" if upstream.get("upstreamLoopCount") == upstream.get("reviewLoopCount") else "**否**",
    ))
    out.append("")
    out.append("- `consumers_need_rebuild` = **%s**（自然清除：**%s**）"
               % (upstream.get("consumersNeedRebuild"),
                  "是" if upstream.get("consumersNeedRebuildCleared") else "否"))
    out.append("")

    out.append("### 3.3 `runtime_core` 快照")
    out.append("")
    core = current.get("runtimeCore", {}) or {}
    out.append("| 字段 | 读数 |")
    out.append("|---|---|")
    for key in ("measured", "state", "snapshotAvailable", "generation", "accepted", "source"):
        out.append("| `%s` | `%s` |" % (key, core.get(key)))
    out.append("")
    if core.get("stateVocabulary"):
        vocabulary = core["stateVocabulary"]
        out.append("> **状态词汇（源码核对）**：来源 `%s`；取值 `%s`；**没有 `ok`**；"
                   "`ready` 才是「快照存在且链条完整」的取值。"
                   % (vocabulary.get("source"), "、".join(vocabulary.get("values", []))))
        out.append("")
    panel = core.get("panelRuntimeCore", {}) or {}
    if panel.get("lastError"):
        out.append("> **`lastError`（原文）**：`%s`" % panel.get("lastError"))
        out.append("")

    out.append("### 3.4 新鲜度双阈值（**两个都测**）")
    out.append("")
    thresholds = current.get("freshnessThresholds", {}) or {}
    observation = thresholds.get("observation", {}) or {}
    gate = thresholds.get("automaticBroadcastGate", {}) or {}
    out.append("| 阈值 | 来源 | 秒 | 读数 | 判定 |")
    out.append("|---|---|---:|---|---|")
    out.append("| 观测阈值 | 观测口径 | %s | fresh=%s / stale=%s | — |" % (
        observation.get("freshnessThresholdSeconds"),
        observation.get("freshReviews"), observation.get("staleReviews")))
    out.append("| 自动广播门禁 | Rust 常量 `MAX_AUTOMATIC_BROADCAST_REVIEW_AGE_SECONDS` | %s | %s | %s |" % (
        gate.get("thresholdSeconds"), short(gate.get("detail", {}) or {}),
        "通过" if gate.get("met") else "**未通过**"))
    out.append("")
    out.append("> **两者不可互换**：600s 是**观测与报告口径**，120s 是**自动广播门禁**；"
               "**广播与否由门禁判定**，不由观测阈值判定。")
    out.append("")

    out.append("### 3.5 边界与诊断")
    out.append("")
    diagnostics = current.get("diagnostics", {}) or {}
    out.append("- 诊断分布：`%s`" % short(diagnostics.get("byCode", {}) or {}))
    out.append("- 广播请求数：**%s**" % current.get("broadcastRequests"))
    out.append("- 方向层（`directional`）：`%s`" % short(current.get("directional", {}) or {}))
    out.append("- healer：`%s`" % short(current.get("healer", {}) or {}))
    out.append("")

    acceptance = current.get("acceptance", {}) or {}
    out.append("**采集器总裁定**：`%s`；未通过项：%s"
               % (acceptance.get("verdict"),
                  short(acceptance.get("notMetItems", []), limit=30)))
    out.append("")

    if baseline:
        out.append("### 3.6 与发布前基线的逐项对照")
        out.append("")
        out.append("基线（复核库停在闭环 v14 / 164 闭环）：`%s`"
                   % baseline.get("generatedAt"))
        out.append("")
        out.append(comparison(current, baseline))

    sys.stdout.reconfigure(encoding="utf-8")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
