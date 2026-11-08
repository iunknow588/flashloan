#!/usr/bin/env python3
"""Audit persisted Robinhood testnet closed-loop broadcast failures.

Reads the JSONL appended by the Rust worker bridge and writes a compact
evidence summary. The script is read-only for SQLite and never replays or
re-broadcasts anything.
"""

from __future__ import annotations

import argparse
import collections
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from core.broadcast_failure_audit import (
    SCHEMA_VERSION,
    broadcast_failure_audit_path,
    load_broadcast_failure_audit_records,
)


DEFAULT_JSONL = broadcast_failure_audit_path()


def _latest_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(rows, key=lambda row: str(row.get("observedAt") or ""))


def _audit_payload(path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_group = collections.Counter(
        str(row.get("groupKey") or "unknown") for row in rows
    )
    error_prefixes = collections.Counter()
    for row in rows:
        error = str(row.get("broadcastError") or row.get("broadcastStatus") or "")
        error_prefixes[error[:120] or "missing_error"] += 1
    latest = _latest_row(rows)
    unique_attempts = {
        str(row.get("attemptFingerprint") or "")
        for row in rows
        if row.get("attemptFingerprint")
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": str(path),
        "available": path.exists(),
        "recordCount": len(rows),
        "uniqueAttemptCount": len(unique_attempts),
        "byGroup": dict(sorted(by_group.items())),
        "errorPrefixCounts": dict(error_prefixes.most_common(20)),
        "latest": latest,
        "samples": rows[-10:],
        "conclusion": (
            f"{len(rows)} broadcast failure audit record(s); "
            f"{len(unique_attempts)} unique attempt(s)."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL)
    parser.add_argument("--output", type=Path, default=DEFAULT_JSONL.parent / "latest.json")
    args = parser.parse_args()

    rows = load_broadcast_failure_audit_records(args.jsonl)
    payload = _audit_payload(args.jsonl, rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"recordCount": payload["recordCount"], "available": payload["available"]}))


if __name__ == "__main__":
    main()
