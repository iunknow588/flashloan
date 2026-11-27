"""Summarise the per-sample layer verdicts of a supervisor journal.

The supervisor writes one ``healing_cycle`` record per sample into a jsonl
journal.  Each record carries the whole audit report, so the interesting part
-- which layer is blocking, what the broadcast layer saw, how many reviews
carried a value -- is buried in a very long line.  This tool prints the
verdict of every layer for every sample so that a regression (a layer that
turned red between two samples) is visible at a glance.

Usage::

    python summarize_supervisor_layers_20261127.py \
        --journal plan/runtime_evidence/diagnostics/mainnet_self_healing_orchestrator/supervisor_20261127T145611Z.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _layer(report: dict[str, Any], name: str) -> dict[str, Any]:
    layers = report.get("layers") or {}
    layer = layers.get(name) or {}
    return layer if isinstance(layer, dict) else {}


def _legacy(layer: dict[str, Any]) -> dict[str, Any]:
    legacy = layer.get("legacy") or {}
    return legacy if isinstance(legacy, dict) else {}


def _line(label: str, value: Any) -> str:
    return "    %-22s %s" % (label + ":", value)


def summarize(journal: Path) -> None:
    for raw in journal.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if record.get("event") != "healing_cycle":
            continue
        after = record.get("after") or record.get("before") or {}
        if not after:
            continue
        reviews = _legacy(_layer(after, "reviews"))
        broadcast = _legacy(_layer(after, "broadcast"))
        closed = _layer(after, "closedLoops")
        pools = _layer(after, "pools")
        production = after.get("transactionProduction") or {}
        print("sample=%s observedAt=%s actions=%s" % (
            record.get("sample"), record.get("stability", {}).get("observedAt"),
            ",".join(record.get("actions") or []) or "none"))
        print(_line("blockers", ",".join(after.get("blockers") or []) or "none"))
        print(_line("production", "%s healthy=%s" % (
            production.get("state"), production.get("healthy"))))
        print(_line("reviews legacy", "total=%s valued=%s missing=%s positive=%s" % (
            reviews.get("total"), reviews.get("valued"),
            reviews.get("missingValue"), reviews.get("positive"))))
        freshness = reviews.get("currentLoopFreshness") or {}
        print(_line("loop freshness", "basis=%s checked=%s current=%s notCurrent=%s" % (
            freshness.get("basis"), freshness.get("checkedLoops"),
            freshness.get("currentLoops"), freshness.get("notCurrentLoops"))))
        print(_line("broadcast legacy", "total=%s attempts=%s success=%s failed=%s pending=%s" % (
            broadcast.get("total"), broadcast.get("attempts"),
            broadcast.get("success"), broadcast.get("failed"),
            broadcast.get("pending"))))
        print(_line("closed loops", json.dumps(closed, ensure_ascii=False)[:400]))
        print(_line("pools", json.dumps(pools, ensure_ascii=False)[:400]))
        print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", type=Path, required=True)
    arguments = parser.parse_args()
    summarize(arguments.journal)


if __name__ == "__main__":
    main()
