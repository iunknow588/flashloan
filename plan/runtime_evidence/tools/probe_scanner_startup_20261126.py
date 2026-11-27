"""Read the scanner's own view of the closed-loop submission.

The question the tool answers is the **startup wait**: an operator sets the
per-scan admission override to ``0`` ("no wait"), and the scanner has to
prove that it really admitted the **whole** catalog in one scan instead of
trickling the groups in over several scans.

The tool prints three things:

* ``capacity``   -- the admission decision of the last submission
  (``batchLimit`` / ``workerGroupCount`` / ``totalGroupLimit``);
* ``submitted``  -- how many groups the last submission actually handed to
  the pool, and how many it had to skip;
* ``timeline``   -- when the scanner last scanned, so the latency between a
  runtime start and the first review can be read instead of estimated.

Usage:
    python probe_scanner_startup_20261126.py [port]
"""

from __future__ import annotations

import json
import sys
import urllib.request


def fetch(url: str, timeout: float = 180.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    port = int(sys.argv[1] if len(sys.argv) > 1 else 5000)
    base = f"http://127.0.0.1:{port}"
    status = fetch(f"{base}/api/status?chain_id=4663")
    scanner = status.get("dex_opportunity_scanner") or {}
    closed_loop = scanner.get("closedLoop") or {}
    print("== closed loop pool ==")
    for key in (
        "engine",
        "enabled",
        "maxWorkers",
        "residentWorkers",
        "createdTotal",
        "pendingTasks",
        "concurrencyLimit",
        "activeConcurrency",
        "completed",
        "idleExits",
    ):
        print(f"  {key}: {closed_loop.get(key)!r}")
    queue = scanner.get("reviewQueue") or {}
    print("== review queue ==")
    for key in ("enabled", "claimed", "running", "completed", "failed"):
        print(f"  {key}: {queue.get(key)!r}")
    print("== scanner timeline ==")
    for key in sorted(scanner):
        value = scanner[key]
        if isinstance(value, str) and any(
            token in key.lower() for token in ("at", "time", "scan", "cycle", "updated")
        ):
            print(f"  {key}: {value}")
    for key in ("lastScan", "scan", "timing", "submission", "state", "cycle"):
        if key in scanner:
            print(f"  {key}: {json.dumps(scanner[key], ensure_ascii=False)[:2000]}")
    try:
        snapshot = fetch(f"{base}/api/dex/opportunity-scanner/snapshot")
    except Exception as error:  # noqa: BLE001 - the snapshot is optional
        print(f"snapshot unavailable: {error}")
        return 0
    print("== snapshot keys ==")
    print(" ", sorted(snapshot)[:60])
    for key in sorted(snapshot):
        if any(
            token in key.lower()
            for token in ("capacity", "submission", "batch", "scan", "timing", "closed")
        ):
            print(f"  {key}: {json.dumps(snapshot[key], ensure_ascii=False)[:2000]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
