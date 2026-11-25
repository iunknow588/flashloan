#!/usr/bin/env python3
"""Probe why live reviews are not executable (read-only, HTTP only).

Reads the live ``manager-api`` review list over HTTP and aggregates
``reviewStatus`` x ``runArbitrage.executable`` together with the reason
distribution.  No database is opened, nothing is written.
"""

from __future__ import annotations

import json
import urllib.request
from collections import Counter

URL = "http://127.0.0.1:8091/v1/reviews?chainId=4663&limit=500"


def main() -> None:
    with urllib.request.urlopen(URL, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    data = payload["data"]
    reviews = data["reviews"]

    status_counter: Counter[str] = Counter()
    reason_counter: Counter[str] = Counter()
    positives: list[dict[str, object]] = []

    for row in reviews:
        status = str(row.get("reviewStatus"))
        run = row.get("runArbitrage") or {}
        ok = bool(run.get("ok"))
        executable = bool(run.get("executable"))
        status_counter[f"{status}|ok={ok}|exec={executable}"] += 1

        pnl = run.get("signedPnl") or {}
        raw = None
        if isinstance(pnl, dict):
            raw = pnl.get("signedPnlRaw") or pnl.get("signed_pnl_raw") or pnl.get("amountRaw")
        else:
            raw = pnl
        if raw not in (None, ""):
            try:
                raw_int = int(raw)
            except (TypeError, ValueError):
                raw_int = None
        else:
            raw_int = None
        if raw_int is not None and raw_int > 0:
            positives.append(
                {
                    "loopKey": row.get("loopKey"),
                    "pathText": row.get("pathText"),
                    "reviewStatus": status,
                    "ok": ok,
                    "executable": executable,
                    "signedPnlRaw": raw_int,
                    "signedPnl": pnl,
                    "reviewReason": row.get("reviewReason"),
                    "runReason": run.get("reason"),
                    "runBlockers": run.get("blockers"),
                    "runError": run.get("error"),
                    "runRevert": run.get("revertSelector"),
                    "noProfitableRoute": run.get("noProfitableRoute"),
                    "selectedAmountRaw": run.get("selectedAmountRaw"),
                    "diagnostics": run.get("diagnostics"),
                    "runKeys": sorted(run.keys()),
                }
            )

        reason = row.get("reviewReason") or run.get("reason") or "<none>"
        reason_counter[f"{status}|{reason}"] += 1

    print("== status x ok x executable ==")
    for key, count in status_counter.most_common():
        print(f"  {count:>4}  {key}")
    print()
    print("== reason distribution ==")
    for key, count in reason_counter.most_common(40):
        print(f"  {count:>4}  {key}")
    print()
    print(f"== positive signed pnl rows: {len(positives)} ==")
    for row in positives:
        print(json.dumps(row, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
