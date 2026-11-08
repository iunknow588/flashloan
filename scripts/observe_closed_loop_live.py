"""
Observe the newest Robinhood V4 mainnet closed-loop evidence after a reset.

Reads only SQLite customer evidence tables plus the runtime loop state; it
never writes the database.  Each sample writes one compact JSON line so the
operator can watch the live flow without re-reading historical rows.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STORE = r"E:\2026OPC大赛\flashLoan\flashloan\db\local_store_4663.sqlite3"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sample(store: str) -> dict:
    con = sqlite3.connect(store)
    con.row_factory = sqlite3.Row
    rows = list(
        con.execute(
            "select id, created_at, pair, selected_profit_amount, "
            "selected_profit_missing_reason from cow_execution_reviews "
            "order by id desc limit 25"
        )
    )
    attempts = list(
        con.execute(
            "select id, created_at, pair, tx_hash, selected_profit_amount "
            "from cow_execution_attempts order by id desc limit 10"
        )
    )
    loop_states = list(
        con.execute(
            "select state, count(*) as cnt from v4_closed_loop_runtime_state "
            "group by state order by cnt desc"
        )
    )
    summary = {str(r["state"]): int(r["cnt"]) for r in loop_states}
    latest_rows = []
    for row in rows:
        latest_rows.append(
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "pair": row["pair"],
                "profit": row["selected_profit_amount"],
                "missing": (row["selected_profit_missing_reason"] or "")[:220],
            }
        )
    attempt_rows = [
        {
            "id": row["id"],
            "created_at": row["created_at"],
            "pair": row["pair"],
            "tx_hash": row["tx_hash"],
            "profit": row["selected_profit_amount"],
        }
        for row in attempts
    ]
    con.close()
    latest = latest_rows[0] if latest_rows else None
    return {
        "observed_at": _iso_now(),
        "review_count": len(rows),
        "newest_review": latest,
        "attempts": attempt_rows,
        "loop_state_counts": summary,
        "loop_total": sum(summary.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default=DEFAULT_STORE)
    parser.add_argument("--minutes", type=float, default=30.0)
    parser.add_argument(
        "--windows",
        type=int,
        default=1,
        help="Number of sequential 30-minute observation windows.",
    )
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    base_out = (
        Path(args.out)
        if args.out
        else (
            Path(__file__).resolve().parents[1]
            / "plan"
            / "runtime_evidence"
            / "runtime_watch"
            / "mainnet"
            / "runtime_watch_20261101_mainnet.jsonl"
        )
    )
    base_out.parent.mkdir(parents=True, exist_ok=True)
    windows = max(1, int(args.windows))
    for window_index in range(windows):
        if windows == 1:
            out = base_out
        else:
            out = base_out.with_name(
                f"{base_out.stem}.{window_index + 1:02d}.jsonl"
            )
        started = time.monotonic()
        deadline = started + max(0.0, float(args.minutes)) * 60.0
        samples = 0
        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            sample = _sample(args.store)
            sample["minutes_remaining"] = round((deadline - now) / 60.0, 1)
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(sample, ensure_ascii=False, sort_keys=True) + "\n")
            samples += 1
            print(json.dumps(sample, ensure_ascii=False, sort_keys=True), flush=True)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(max(0.0, float(args.interval)), remaining))
        print(
            json.dumps(
                {
                    "samples": samples,
                    "out": str(out),
                    "window": window_index + 1,
                    "finished_at": _iso_now(),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
