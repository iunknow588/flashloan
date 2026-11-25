#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure the performance baseline of the live chain.

The baseline is the *measurement* half of the acceptance item "establish a
performance baseline before optimising anything".  Every metric is either
measured from the running system or recorded as **not measured** together
with the reason; a missing metric is never reported as zero.

Read-only: the live store is opened with ``mode=ro`` + ``PRAGMA query_only``.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
import statistics
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"
REVIEW_RECORD_ROOT = REPO / "flashloan" / "db" / "review_record_jsonl"
BROADCAST_RECORD_ROOT = REPO / "flashloan" / "db" / "broadcast_transaction_db"


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def describe(samples):
    if not samples:
        return {"count": 0}
    ordered = sorted(samples)
    return {
        "count": len(ordered),
        "minSeconds": round(ordered[0], 3),
        "medianSeconds": round(statistics.median(ordered), 3),
        "maxSeconds": round(ordered[-1], 3),
        "meanSeconds": round(statistics.fmean(ordered), 3),
    }


def record_layer(root: Path) -> dict:
    if not root.exists():
        return {"present": False, "reason": "the record directory does not exist"}
    files = [path for path in root.rglob("*.jsonl") if path.is_file()]
    total_bytes = sum(path.stat().st_size for path in files)
    total_lines = 0
    newest = None
    for path in files:
        newest = max(newest or path.stat().st_mtime, path.stat().st_mtime)
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            total_lines += sum(1 for _ in handle)
    return {
        "present": True,
        "files": len(files),
        "bytes": total_bytes,
        "lines": total_lines,
        "newestWriteAt": (
            datetime.datetime.fromtimestamp(newest).astimezone().isoformat()
            if newest else None
        ),
    }


def directional_timing(timing_path: Path | None) -> dict:
    """Merge the directional-layer timings produced by the Rust probe.

    The projection runs in Rust (the directional-pool-manager crate) because
    the Python side owns no directional logic of its own.  The Rust probe
    writes its own JSON; this script only folds it in, so the numbers in the
    baseline are the ones the Rust layer actually produced.

    When the file is absent the metrics are recorded as **not measured** with
    the reason -- never as zero.
    """
    if timing_path is None or not timing_path.is_file():
        return {
            "measured": False,
            "reason": (
                "the directional timing probe was not supplied; run the"
                " directional-pool-manager materialization_timing example"
            ),
            "scopes": [],
        }
    payload = json.loads(timing_path.read_text(encoding="utf-8"))
    scopes = []
    for row in payload.get("scopes", []):
        projections = [float(value) for value in row.get("projectionSeconds", [])]
        scopes.append(
            {
                "scopeKey": row.get("scopeKey"),
                "measured": True,
                "projectionSeconds": {
                    "count": len(projections),
                    "minSeconds": round(min(projections), 3) if projections else None,
                    "medianSeconds": (
                        round(statistics.median(projections), 3) if projections else None
                    ),
                    "maxSeconds": round(max(projections), 3) if projections else None,
                },
                "tableLoadSeconds": row.get("tableLoadSeconds"),
                "directionalPoolCount": row.get("directionalPoolCount"),
                "basePoolCount": row.get("basePoolCount"),
                "direction0Count": row.get("direction0Count"),
                "direction1Count": row.get("direction1Count"),
                "snapshotChecksum": row.get("snapshotChecksum"),
                "poolScopeChecksum": row.get("poolScopeChecksum"),
            }
        )
    return {
        "measured": bool(scopes),
        "reason": None if scopes else "the probe produced no scope rows",
        "source": str(timing_path).replace(chr(92), "/"),
        "note": (
            "the projection is computed with apply = false against a"
            " read-only store; the table load is the read of the published"
            " directional pool rows"
        ),
        "scopes": scopes,
    }


def measure(database_path: Path, timing_path: Path | None = None) -> dict:
    now = datetime.datetime.now().astimezone()
    uri = "file:" + database_path.resolve().as_posix() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    try:
        state = connection.execute(
            "SELECT * FROM manager_review_library_state"
            " WHERE network = 'robinhood' AND chain_id = 4663"
            " AND protocol = 'uniswap_v4'"
        ).fetchone()
        review_rows = connection.execute(
            "SELECT reviewed_at, created_at, updated_at, static_preview_json"
            " FROM manager_v4_reviews"
        ).fetchall()
        closed_loop_state = connection.execute(
            "SELECT generated_at, updated_at, closed_loop_version"
            " FROM manager_v4_closed_loop_library_state"
        ).fetchone()
        candidate_state = connection.execute(
            "SELECT generated_at, updated_at, candidate_version"
            " FROM manager_dex_candidate_library_state"
        ).fetchone()
        directional_states = connection.execute(
            "SELECT scope_key, directional_pool_version, generated_at, updated_at"
            " FROM manager_v4_directional_pool_library_state"
        ).fetchall()
        broadcast_requests = connection.execute(
            "SELECT COUNT(*) FROM manager_v4_review_broadcast_requests"
        ).fetchone()[0]
        broadcast_state_rows = connection.execute(
            "SELECT COUNT(*) FROM manager_broadcast_library_state"
        ).fetchone()[0]
    finally:
        connection.close()

    reviewed = [value for value in (parse_iso(row["reviewed_at"]) for row in review_rows)
                if value is not None]
    created = [value for value in (parse_iso(row["created_at"]) for row in review_rows)
               if value is not None]

    latencies = []
    for row in review_rows:
        filed = parse_iso(row["reviewed_at"])
        try:
            preview = json.loads(row["static_preview_json"] or "{}")
        except (TypeError, ValueError):
            continue
        measured = parse_iso(preview.get("measuredAt"))
        if filed is None or measured is None:
            continue
        latencies.append((filed - measured).total_seconds())

    def span(first, second):
        if first is None or second is None:
            return []
        return [(second - first).total_seconds()]

    generated_at = parse_iso(state["generated_at"])
    report = {
        "schema": "performance-baseline-v1",
        "generatedAt": now.isoformat(),
        "mode": "read-only",
        "store": {
            "path": str(database_path).replace("\\", "/"),
            "openedReadOnly": True,
            "queryOnly": True,
            "writesByThisProbe": 0,
        },
        "generation": {
            "reviewVersion": state["review_version"],
            "loopCount": state["loop_count"],
            "completedCount": state["completed_count"],
            "dataIncompleteCount": state["data_incomplete_count"],
            "notExecutableCount": state["not_executable_count"],
            "generatedAt": state["generated_at"],
        },
        "reviewTiming": {
            "measured": True,
            "sweepSpanSeconds": describe(
                span(min(reviewed), max(reviewed)) if reviewed else []
            ),
            "publishLatencySeconds": describe(span(min(reviewed), generated_at)
                                              if reviewed else []),
            "writeWindowSeconds": describe(span(min(created), max(created))
                                            if created else []),
            "perRouteFileLatencySeconds": describe(latencies),
            "meaning": {
                "sweepSpanSeconds": "first route measured to last route measured",
                "publishLatencySeconds": "first route measured to the generation was written",
                "writeWindowSeconds": "first row written to last row written",
                "perRouteFileLatencySeconds": "route measured to the review row was filed",
            },
        },
        "materializationTiming": {
            "closedLoopRebuildSeconds": describe(
                span(parse_iso(closed_loop_state["generated_at"]),
                     parse_iso(closed_loop_state["updated_at"]))
                if closed_loop_state else []
            ),
            "candidateEdgeMaterializationSeconds": {
                "measured": False,
                "reason": (
                    "the candidate edge layer stamps one instant for the"
                    " whole materialization, so the duration is not separable"
                    " from the timestamp pair; the observable delta of 0.0 s"
                    " is **not** a measured zero duration"
                ),
                "observableDeltaSeconds": (
                    round(
                        (
                            parse_iso(candidate_state["updated_at"])
                            - parse_iso(candidate_state["generated_at"])
                        ).total_seconds(),
                        3,
                    )
                    if candidate_state
                    and parse_iso(candidate_state["updated_at"])
                    and parse_iso(candidate_state["generated_at"])
                    else None
                ),
            },
            "directionalLayer": directional_timing(timing_path),
            "directionalPoolRows": [
                {
                    "scopeKey": row["scope_key"],
                    "version": row["directional_pool_version"],
                    "generatedAt": row["generated_at"],
                    "updatedAt": row["updated_at"],
                }
                for row in directional_states
            ],
            "note": (
                "a materialization that wrote its state row in one instant reports"
                " a delta of 0.0 s; that is the observable value of the two"
                " timestamps, not a measured zero duration.  The real duration is"
                " only separable when the layer stamps a start instant as well."
            ),
        },
        "broadcastTiming": {
            "measured": False,
            "reason": (
                "no broadcast request has been published yet"
                if broadcast_requests == 0 else
                "broadcast requests exist but carry no duration field"
            ),
            "broadcastRequestRows": broadcast_requests,
            "managerBroadcastLibraryStateRows": broadcast_state_rows,
        },
        "recordLayer": {
            "reviewJsonl": record_layer(REVIEW_RECORD_ROOT),
            "broadcastSqlite": record_layer(BROADCAST_RECORD_ROOT),
        },
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out", default="")
    parser.add_argument(
        "--directional-timing",
        default="",
        help=(
            "JSON written by the Rust materialization_timing example;"
            " folded into the baseline so the directional-layer cost is not"
            " reported as an unmeasured zero"
        ),
    )
    arguments = parser.parse_args()
    timing = Path(arguments.directional_timing) if arguments.directional_timing else None
    report = measure(Path(arguments.db), timing)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if arguments.out:
        target = Path(arguments.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
        print("wrote:", target)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
