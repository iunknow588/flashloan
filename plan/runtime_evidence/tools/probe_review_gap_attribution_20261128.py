#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Attribute every closed loop that has no review row of the current generation.

The boxes ``T-复储.01 / A1`` and ``A3`` ask for two readings:

* **A1** -- a per-loop attribution of the missing rows: current generation,
  audit-stream generation, why the row is missing, and whether the loop is
  still in the active catalog;
* **A3** -- the baseline of ``runArbitrage ok = false`` rows.

Everything is read only: the store is opened ``mode=ro`` and the audit stream
is read as plain JSONL files.

Usage::

    python probe_review_gap_attribution_20261128.py [--json <path>]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"
DEFAULT_AUDIT = REPO / "flashloan" / "db" / "review_record_jsonl"


def open_store(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect("file:%s?mode=ro" % database, uri=True, timeout=60)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def audit_lines(directory: Path, network: str, chain_id: int) -> list[dict]:
    folder = directory / network / str(chain_id)
    if not folder.is_dir():
        return []
    lines: list[dict] = []
    for path in sorted(folder.rglob("*.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    lines.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--audit-dir", default=str(DEFAULT_AUDIT))
    parser.add_argument("--network", default="robinhood")
    parser.add_argument("--chain-id", type=int, default=4663)
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    connection = open_store(Path(args.db))
    try:
        generation = int(connection.execute(
            "SELECT MAX(closed_loop_version) AS g FROM manager_v4_reviews"
        ).fetchone()["g"] or 0)

        active = {
            str(row["loop_key"]): row
            for row in connection.execute(
                "SELECT loop_key, scope_key, anchor_symbol FROM manager_v4_closed_loops "
                "WHERE status = 'active'"
            )
        }

        latest: dict[str, dict] = {}
        for row in connection.execute(
            "SELECT loop_key, closed_loop_version, review_status, review_reason, "
            "signed_pnl_raw, run_arbitrage_json, reviewed_at FROM manager_v4_reviews "
            "ORDER BY closed_loop_version"
        ):
            latest[str(row["loop_key"])] = dict(row)

        in_generation = {
            str(row["loop_key"])
            for row in connection.execute(
                "SELECT loop_key FROM manager_v4_reviews WHERE closed_loop_version = ?1",
                (generation,),
            )
        }

        # --- A3: the ok=false baseline ---------------------------------
        ok_false_by_generation: dict[int, dict] = {}
        for row in connection.execute(
            "SELECT closed_loop_version, review_status, run_arbitrage_json "
            "FROM manager_v4_reviews"
        ):
            payload = row["run_arbitrage_json"]
            ok = None
            if payload:
                try:
                    ok = bool(json.loads(payload).get("ok"))
                except (json.JSONDecodeError, AttributeError, TypeError):
                    ok = None
            bucket = ok_false_by_generation.setdefault(
                int(row["closed_loop_version"] or 0),
                {"rows": 0, "okFalse": 0, "okTrue": 0, "okUnknown": 0,
                 "status": {}},
            )
            bucket["rows"] += 1
            status = str(row["review_status"])
            bucket["status"][status] = bucket["status"].get(status, 0) + 1
            if ok is True:
                bucket["okTrue"] += 1
            elif ok is False:
                bucket["okFalse"] += 1
            else:
                bucket["okUnknown"] += 1

        # --- the library state counters --------------------------------
        library = connection.execute(
            "SELECT * FROM manager_review_library_state WHERE scope_key = 'core4'"
        ).fetchone()

        # --- the rows that expect a positive profit --------------------
        positive_rows = []
        for row in connection.execute(
            "SELECT loop_key, closed_loop_version, review_status, review_reason, "
            "signed_pnl_raw, reviewed_at FROM manager_v4_reviews "
            "WHERE signed_pnl_raw IS NOT NULL"
        ):
            raw = str(row["signed_pnl_raw"])
            try:
                value = int(raw, 16) if raw.startswith(("0x", "-0x")) else int(raw)
            except ValueError:
                continue
            if value <= 0:
                continue
            positive_rows.append({
                "loopKey": str(row["loop_key"]),
                "generation": int(row["closed_loop_version"] or 0),
                "reviewStatus": row["review_status"],
                "reviewReason": row["review_reason"],
                "signedPnlRaw": raw,
                "reviewedAt": row["reviewed_at"],
            })
    finally:
        connection.close()

    lines = audit_lines(Path(args.audit_dir), args.network, args.chain_id)
    today = dt.datetime.now().strftime("%Y-%m-%d")
    audit_generation: dict[str, int] = {}
    audit_today: dict[str, int] = {}
    audit_of_generation: dict[str, int] = {}
    for line in lines:
        loop = str(line.get("loopKey"))
        try:
            version = int(line.get("closedLoopVersion") or 0)
        except (TypeError, ValueError):
            version = 0
        audit_generation[loop] = max(audit_generation.get(loop, 0), version)
        if str(line.get("observedAt", ""))[:10] == today:
            audit_today[loop] = audit_today.get(loop, 0) + 1
        if version == generation:
            audit_of_generation[loop] = audit_of_generation.get(loop, 0) + 1

    attribution = []
    for loop, row in sorted(active.items()):
        if loop in in_generation:
            continue
        previous = latest.get(loop)
        attribution.append({
            "loopKey": loop,
            "anchorSymbol": row["anchor_symbol"],
            "scopeKey": row["scope_key"],
            "inActiveCatalog": True,
            "missingGeneration": generation,
            "auditStreamGeneration": audit_generation.get(loop, 0),
            "auditLinesToday": audit_today.get(loop, 0),
            "auditLinesOfMissingGeneration": audit_of_generation.get(loop, 0),
            "lastStoredGeneration": (previous or {}).get("closed_loop_version"),
            "lastStoredStatus": (previous or {}).get("review_status"),
            "lastStoredReason": (previous or {}).get("review_reason"),
            "lastStoredPnlIsNull": (
                None if previous is None else previous.get("signed_pnl_raw") is None
            ),
            "lastReviewedAt": (previous or {}).get("reviewed_at"),
        })

    report = {
        "generation": generation,
        "activeLoops": len(active),
        "coveredLoops": len(in_generation),
        "missingLoops": len(attribution),
        "attribution": attribution,
        "positiveRows": positive_rows,
        "okFalseBaseline": {
            str(key): value for key, value in sorted(ok_false_by_generation.items())
        },
        "libraryState": dict(library) if library is not None else None,
        "sampledAt": dt.datetime.now().astimezone().isoformat(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
