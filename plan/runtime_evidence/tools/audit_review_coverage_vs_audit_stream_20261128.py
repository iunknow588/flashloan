#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare the authoritative review rows with the hourly audit stream.

The store keeps the rows of the current closed loop generation, the audit
stream keeps one JSONL file per loop and per hour.  A loop that is active but
has no review row of the current generation is either

* not covered by the scan pass at all, or
* a review that was measured and then lost when the store could not take its
  write lock (the row is committed first, the audit line is published after
  it, so a failed commit loses both).

The audit stream can tell the two apart: a loop that has an audit line for
the day but no row of the current generation was reviewed before and lost on
the way into the store.

Usage::

    python audit_review_coverage_vs_audit_stream_20261128.py --generation 73
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
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def current_generation(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT MAX(closed_loop_version) AS generation FROM manager_v4_reviews"
    ).fetchone()
    return int(row["generation"] or 0)


def active_loops(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT loop_key FROM manager_v4_closed_loops"
        " WHERE status = 'active' ORDER BY loop_key"
    ).fetchall()
    return [str(row["loop_key"]) for row in rows]


def generation_reviews(
    connection: sqlite3.Connection, generation: int
) -> dict[str, dict]:
    rows = connection.execute(
        "SELECT loop_key, review_status, signed_pnl_raw, reviewed_at"
        " FROM manager_v4_reviews WHERE closed_loop_version = ?1",
        (generation,),
    ).fetchall()
    return {
        str(row["loop_key"]): {
            "reviewStatus": row["review_status"],
            "signedPnlRaw": row["signed_pnl_raw"],
            "reviewedAt": row["reviewed_at"],
        }
        for row in rows
    }


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
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                payload["_file"] = path.name
                lines.append(payload)
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--audit-dir", default=str(DEFAULT_AUDIT))
    parser.add_argument("--network", default="robinhood")
    parser.add_argument("--chain-id", type=int, default=4663)
    parser.add_argument("--generation", type=int, default=0)
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    connection = open_store(Path(args.db))
    try:
        generation = args.generation or current_generation(connection)
        loops = active_loops(connection)
        reviews = generation_reviews(connection, generation)
    finally:
        connection.close()

    lines = audit_lines(Path(args.audit_dir), args.network, args.chain_id)
    audited_loops = {str(line.get("loopKey")) for line in lines}
    audited_today = {
        str(line.get("loopKey"))
        for line in lines
        if str(line.get("observedAt", ""))[:10]
        == dt.datetime.now().strftime("%Y-%m-%d")
    }

    # The audit line carries the generation it was measured for.  A loop that
    # has an audit line of the current generation but no row of that
    # generation was measured and then lost on the way into the store.
    audit_generation: dict[str, int] = {}
    audit_lines_of_generation: dict[str, int] = {}
    for line in lines:
        loop = str(line.get("loopKey"))
        try:
            version = int(line.get("closedLoopVersion") or 0)
        except (TypeError, ValueError):
            version = 0
        audit_generation[loop] = max(audit_generation.get(loop, 0), version)
        if version == generation:
            audit_lines_of_generation[loop] = audit_lines_of_generation.get(loop, 0) + 1

    with_row = [loop for loop in loops if loop in reviews]
    without_row = [loop for loop in loops if loop not in reviews]
    null_pnl = [
        loop for loop in with_row if not reviews[loop].get("signedPnlRaw")
    ]
    positive = [
        loop
        for loop in with_row
        if str(reviews[loop].get("signedPnlRaw") or "").startswith("0x")
        and not str(reviews[loop]["signedPnlRaw"]).startswith("-0x")
        and int(str(reviews[loop]["signedPnlRaw"]), 16) > 0
    ]
    lost_with_audit = [loop for loop in without_row if loop in audited_today]
    never_touched = [loop for loop in without_row if loop not in audited_loops]
    measured_but_lost = [
        loop for loop in without_row if audit_generation.get(loop, 0) >= generation
    ]

    report = {
        "generation": generation,
        "activeLoops": len(loops),
        "reviewsInGeneration": len(reviews),
        "auditLines": len(lines),
        "auditLoops": len(audited_loops),
        "covered": len(with_row),
        "nullPnl": len(null_pnl),
        "positivePnl": len(positive),
        "missingRow": len(without_row),
        "missingRowButAuditedToday": len(lost_with_audit),
        "missingRowNeverAudited": len(never_touched),
        "measuredForThisGenerationButNoRow": len(measured_but_lost),
        "missingRowButAuditedTodayLoops": lost_with_audit,
        "missingRowNeverAuditedLoops": never_touched,
        "measuredForThisGenerationButNoRowLoops": measured_but_lost,
        "auditGenerationPerMissingLoop": {
            loop: audit_generation.get(loop, 0) for loop in without_row
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
