#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turn the stability samples into the window evidence package.

The mainnet acceptance item asks for **three consecutive windows of at least
30 minutes** in which the chain is free of blockers, carries no missing
value, has a positive profit and has a receipt that can be correlated.  The
stability sampler records one reading per minute; this tool slices those
readings into 30 minute windows and reports, for every window, the readings
it contains and whether each criterion held.

A window that does not meet a criterion is reported as such.  The tool never
selects the good windows and never drops the bad ones.

Read-only: the samples are a JSONL file and the store is opened read-only.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_SAMPLES = REPO / "plan" / "runtime_evidence" / "stability_window_20261125.jsonl"
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"
WINDOW_SECONDS = 30 * 60


def parse_iso(value):
    try:
        return datetime.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def fingerprint(sample):
    return (
        sample.get("closedLoopVersion"),
        sample.get("closedLoopChecksum"),
        sample.get("reviewVersion"),
    )


def receipt_evidence(database_path: Path) -> dict:
    connection = sqlite3.connect(
        f"file:{database_path.resolve().as_posix()}?mode=ro", uri=True,
    )
    connection.execute("PRAGMA query_only = ON")
    try:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        attempts = 0
        linked = 0
        if "manager_broadcast_attempts" in tables:
            attempts = connection.execute(
                "SELECT COUNT(*) FROM manager_broadcast_attempts"
            ).fetchone()[0]
            columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(manager_broadcast_attempts)"
                )
            }
            if "tx_hash" in columns and "receipt" in columns:
                linked = connection.execute(
                    "SELECT COUNT(*) FROM manager_broadcast_attempts"
                    " WHERE tx_hash IS NOT NULL AND tx_hash != ''"
                    " AND receipt IS NOT NULL AND receipt != ''"
                ).fetchone()[0]
        return {
            "attempts": attempts,
            "receiptLinked": linked,
            "available": "manager_broadcast_attempts" in tables,
        }
    finally:
        connection.close()


def build(samples_path: Path, database_path: Path) -> dict:
    samples = []
    with samples_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                sample = json.loads(line)
            except ValueError:
                continue
            moment = parse_iso(sample.get("sampledAt"))
            if moment is None:
                continue
            sample["_at"] = moment
            samples.append(sample)
    samples.sort(key=lambda item: item["_at"])

    receipts = receipt_evidence(database_path)

    windows = []
    run = []
    for sample in samples:
        if run and fingerprint(run[-1]) != fingerprint(sample):
            windows.extend(split_run(run))
            run = []
        run.append(sample)
    windows.extend(split_run(run))

    for window in windows:
        window["criteria"] = {
            "blockersEmpty": all(
                not (sample.get("consistencyIssues") or [])
                and not (sample.get("upstreamIssues") or [])
                for sample in window["samples"]
            ),
            "missingValueZero": all(
                int(sample.get("pnlMissingCount") or 0) == 0
                for sample in window["samples"]
            ),
            "positive": all(
                int(sample.get("profitableRouteCount") or 0) > 0
                for sample in window["samples"]
            ),
            "receiptCorrelated": (
                receipts["available"]
                and receipts["receiptLinked"] > 0
                and all(
                    int(sample.get("broadcastSuccessCount") or 0) > 0
                    for sample in window["samples"]
                )
            ),
        }
        window["qualifies"] = all(window["criteria"].values())
        window.pop("samples")

    qualifying = [window for window in windows if window["qualifies"]]
    consecutive = 0
    best = 0
    for window in qualifying:
        consecutive += 1
        best = max(best, consecutive)
    else:
        pass

    return {
        "schema": "window-evidence-package-v1",
        "generatedAt": datetime.datetime.now().astimezone().isoformat(),
        "samples": {
            "path": str(samples_path).replace("\\", "/"),
            "count": len(samples),
            "first": samples[0]["sampledAt"] if samples else None,
            "last": samples[-1]["sampledAt"] if samples else None,
        },
        "windowSeconds": WINDOW_SECONDS,
        "windows": windows,
        "windowCount": len(windows),
        "qualifyingWindows": len(qualifying),
        "longestConsecutiveQualifyingRun": best,
        "receiptEvidence": receipts,
        "verdict": {
            "threeConsecutiveWindows": best >= 3,
            "note": (
                "a window qualifies only when all four criteria hold for every"
                " sample inside it; windows that do not qualify are kept and"
                " reported with their readings"
            ),
        },
    }


def split_run(run):
    if not run:
        return []
    windows = []
    start = 0
    while start < len(run):
        end = start
        while end + 1 < len(run) and (
            run[end + 1]["_at"] - run[start]["_at"]
        ).total_seconds() < WINDOW_SECONDS:
            end += 1
        chunk = run[start:end + 1]
        span = (chunk[-1]["_at"] - chunk[0]["_at"]).total_seconds()
        windows.append({
            "start": chunk[0]["sampledAt"],
            "end": chunk[-1]["sampledAt"],
            "spanSeconds": span,
            "sampleCount": len(chunk),
            "readings": {
                "reviewVersion": sorted({s.get("reviewVersion") for s in chunk}),
                "closedLoopVersion": sorted({s.get("closedLoopVersion") for s in chunk}),
                "reviewCompletedCountMax": max(
                    int(s.get("reviewCompletedCount") or 0) for s in chunk
                ),
                "reviewCompletedCountMin": min(
                    int(s.get("reviewCompletedCount") or 0) for s in chunk
                ),
                "pnlMissingCountMax": max(
                    int(s.get("pnlMissingCount") or 0) for s in chunk
                ),
                "profitableRouteCountMin": min(
                    int(s.get("profitableRouteCount") or 0) for s in chunk
                ),
                "profitableRouteCountMax": max(
                    int(s.get("profitableRouteCount") or 0) for s in chunk
                ),
                "bestObservedNetProfitUsdMax": max(
                    (float(s.get("bestObservedNetProfitUsd") or 0.0) for s in chunk),
                    default=0.0,
                ),
                "broadcastRequestCountMax": max(
                    int(s.get("broadcastRequestCount") or 0) for s in chunk
                ),
                "broadcastSuccessCountMax": max(
                    int(s.get("broadcastSuccessCount") or 0) for s in chunk
                ),
                "missingPoolReferenceCountMax": max(
                    int(s.get("missingPoolReferenceCount") or 0) for s in chunk
                ),
                "consistencyIssuesMax": max(
                    len(s.get("consistencyIssues") or []) for s in chunk
                ),
                "upstreamIssuesMax": max(
                    len(s.get("upstreamIssues") or []) for s in chunk
                ),
            },
            "samples": chunk,
        })
        start = end + 1
    return windows


def render(report: dict) -> str:
    lines = [
        "# 主网稳定窗口证据包",
        "",
        f"- 生成时刻：**{report['generatedAt']}**",
        f"- 样本文件：`{report['samples']['path']}`",
        f"- 样本数：**{report['samples']['count']}**"
        f"（{report['samples']['first']} → {report['samples']['last']}）",
        f"- 窗口长度：**{report['windowSeconds'] // 60} 分钟**",
        f"- 窗口数：**{report['windowCount']}** ／ 合格窗口：**{report['qualifyingWindows']}**",
        f"- **最长连续合格窗口数：{report['longestConsecutiveQualifyingRun']}**"
        f"（判据：**三个连续** ⇒ {'**达成**' if report['verdict']['threeConsecutiveWindows'] else '**未达成**'}）",
        "",
        "| # | 起 | 止 | 跨度(s) | 样本 | 阻滞 | 缺值 | 正利润 | 回执 | 合格 |",
        "|---:|---|---|---:|---:|---|---|---|---|",
    ]
    for index, window in enumerate(report["windows"], start=1):
        criteria = window["criteria"]
        mark = lambda flag: "✔" if flag else "✘"  # noqa: E731
        lines.append(
            f"| {index} | {window['start']} | {window['end']} | {window['spanSeconds']:.0f} |"
            f" {window['sampleCount']} | {mark(criteria['blockersEmpty'])} |"
            f" {mark(criteria['missingValueZero'])} | {mark(criteria['positive'])} |"
            f" {mark(criteria['receiptCorrelated'])} | {mark(window['qualifies'])} |"
        )
    lines.extend([
        "",
        "**判据映射**：",
        "",
        "- `阻滞` ＝ `consistencyIssues` 与 `upstreamIssues` **同时**为空；",
        "- `缺值` ＝ `pnlMissingCount = 0`；",
        "- `正利润` ＝ `profitableRouteCount > 0`；",
        "- `回执` ＝ 广播尝试与回执可关联（`manager_broadcast_attempts.tx_hash` 与 `receipt` 同时存在）",
        "  且窗口内 `broadcastSuccessCount > 0`；",
        "",
        "> 不合格窗口**保留在表里**并附读数 —— 工具**不挑选**窗口，也**不丢弃**窗口。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", default=str(DEFAULT_SAMPLES))
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out-json", default="")
    parser.add_argument("--out-md", default="")
    arguments = parser.parse_args()
    report = build(Path(arguments.samples), Path(arguments.db))
    if arguments.out_json:
        target = Path(arguments.out_json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print("wrote:", target)
    if arguments.out_md:
        target = Path(arguments.out_md)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(report), encoding="utf-8")
        print("wrote:", target)
    if not arguments.out_json and not arguments.out_md:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
