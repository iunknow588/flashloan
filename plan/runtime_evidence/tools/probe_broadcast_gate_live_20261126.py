"""Read-only probe: the live broadcast admission, as the operator sees it.

The overview carries the *result* readout (`summary.broadcastEnabled` —
"did the broadcast library ever publish a state row") and the control
panel carries the *decision* readout (`broadcast_gate`).  This probe
prints both, plus the review counters, so a deployment can be judged by
what the system says rather than by what it is expected to say.
"""

from __future__ import annotations

import json
import sys
import urllib.request


def fetch(url: str, timeout: int = 120) -> object:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    base = "http://127.0.0.1:5000"
    out: dict[str, object] = {}
    try:
        overview = fetch(f"{base}/api/dex/overview")
    except Exception as exc:  # noqa: BLE001 - diagnostic path
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    summary = overview.get("summary") if isinstance(overview, dict) else {}
    if not isinstance(summary, dict):
        summary = {}
    keys = [
        "broadcastEnabled",
        "broadcastAttemptCount",
        "broadcastPendingCount",
        "broadcastSuccessCount",
        "broadcastFailedCount",
        "broadcastTerminalCount",
        "reviewCompletedCount",
        "reviewNotExecutableCount",
        "reviewDataIncompleteCount",
        "broadcastRequestCount",
    ]
    out["summary"] = {key: summary.get(key) for key in keys if key in summary}
    gate = overview.get("broadcast_gate") or overview.get("broadcastGate")
    if gate is not None:
        out["broadcast_gate"] = gate
    try:
        groups = fetch(f"{base}/api/dex/closed-loop-groups")
    except Exception as exc:  # noqa: BLE001 - diagnostic path
        groups = {"error": f"{type(exc).__name__}: {exc}"}
    rows: list[dict[str, object]] = []
    items = groups.get("groups") if isinstance(groups, dict) else None
    for item in items or []:
        if not isinstance(item, dict):
            continue
        worker = item.get("worker") or {}
        if not isinstance(worker, dict):
            continue
        rows.append(
            {
                "group_key": item.get("group_key") or worker.get("group_key"),
                "broadcastGate": worker.get("broadcastGate"),
                "broadcast_count": worker.get("broadcast_count"),
                "queued_count": worker.get("queued_count"),
                "state": worker.get("state"),
            }
        )
    out["groups"] = rows[:5]
    out["groupCount"] = len(rows)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
