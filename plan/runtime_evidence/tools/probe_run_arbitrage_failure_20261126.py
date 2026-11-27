"""Read-only probe: why does `runArbitrage` report ok=false?

The review layer records the `runArbitrage` verdict as JSON.  The summary
lines only say "ok=false"; the reason lives inside that JSON.  This probe
opens the chain store read-only and groups the reasons so the real blocker
for "no broadcast ever happens" becomes visible.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path


def _group(values: list[str], limit: int = 12) -> list[list[str]]:
    counter = Counter(v for v in values if v)
    return [[name, str(count)] for name, count in counter.most_common(limit)]


def main() -> int:
    db_path = Path(
        sys.argv[1]
        if len(sys.argv) > 1
        else "flashloan/db/local_store_4663.sqlite3"
    ).resolve()
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=60)
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    out: dict[str, object] = {"db": str(db_path)}

    try:
        rows = conn.execute(
            "SELECT review_status, review_reason, run_arbitrage_json, signed_pnl_raw "
            "FROM manager_v4_reviews"
        ).fetchall()
    except sqlite3.Error as exc:  # pragma: no cover - diagnostic path
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    out["reviewRows"] = len(rows)
    out["statusCounts"] = _group([str(r["review_status"]) for r in rows])
    out["reasonCounts"] = _group([str(r["review_reason"]) for r in rows], limit=20)

    errors: list[str] = []
    error_kinds: list[str] = []
    blocked: list[str] = []
    ok_values: list[str] = []
    executable_values: list[str] = []
    reasons: list[str] = []
    with_payload = 0
    keys_seen: Counter[str] = Counter()

    for row in rows:
        raw = row["run_arbitrage_json"]
        if not raw:
            continue
        with_payload += 1
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        for key in payload:
            keys_seen[key] += 1
        ok_values.append(json.dumps(payload.get("ok"), default=str))
        executable_values.append(json.dumps(payload.get("executable"), default=str))
        for source, sink in (
            ("error", errors),
            ("errorKind", error_kinds),
            ("ladderBlockedReason", blocked),
            ("reason", reasons),
        ):
            value = payload.get(source)
            if value:
                sink.append(json.dumps(value, default=str))

    out["withRunArbitragePayload"] = with_payload
    out["okValues"] = _group(ok_values)
    out["executableValues"] = _group(executable_values)
    out["errorCounts"] = _group(errors)
    out["errorKindCounts"] = _group(error_kinds)
    out["ladderBlockedReasonCounts"] = _group(blocked)
    out["reasonFieldCounts"] = _group(reasons, limit=20)
    out["payloadKeys"] = [[k, str(v)] for k, v in keys_seen.most_common(40)]

    for table in (
        "manager_broadcast_library_state",
        "manager_broadcast_attempts",
        "manager_broadcast_requests",
    ):
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.Error as exc:
            count = f"error: {exc}"
        out[f"table_{table}"] = count

    # A positive-profit review is the trigger for a broadcast; show the pnl
    # distribution so we know whether any review ever reported a profit.
    pnl_rows = conn.execute(
        "SELECT signed_pnl_raw FROM manager_v4_reviews WHERE signed_pnl_raw IS NOT NULL"
    ).fetchall()
    positives = 0
    negatives = 0
    zeroes = 0
    for row in pnl_rows:
        value = str(row[0])
        try:
            number = int(value, 16) if value.startswith(("0x", "-0x")) else int(value)
        except ValueError:
            continue
        if number > 0:
            positives += 1
        elif number < 0:
            negatives += 1
        else:
            zeroes += 1
    out["signedPnl"] = {
        "rows": len(pnl_rows),
        "positive": positives,
        "negative": negatives,
        "zero": zeroes,
    }

    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
