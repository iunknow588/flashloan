"""Read-only probe: does a live review payload carry the three hop limits?

The broadcast queue refuses a route whose payload does not carry exactly
three `hopSqrtPriceLimits`.  This probe reads the live runtime and counts
how many payloads carry them, so we know whether that gate can ever open.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from collections import Counter


ENDPOINTS = [
    "http://127.0.0.1:5000/api/dex/closed-loop-groups",
    "http://127.0.0.1:5000/api/dex/overview",
]


def fetch(url: str, timeout: int = 180) -> object:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def walk(node: object, path: str, found: list[dict[str, object]]) -> None:
    if isinstance(node, dict):
        if "hopSqrtPriceLimits" in node:
            limits = node.get("hopSqrtPriceLimits")
            count = len(limits) if isinstance(limits, list) else -1
            found.append(
                {
                    "path": path,
                    "limitCount": count,
                    "status": node.get("status"),
                    "hasOffchainGasGate": "offchainGasGate" in node,
                    "hasCapacityGate": "capacityGate" in node,
                    "hasEconomicFloorGate": "economicFloorGate" in node,
                    "selectedProfitRaw": node.get("selectedProfitRaw"),
                }
            )
        for key, value in node.items():
            walk(value, f"{path}.{key}" if path else str(key), found)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            walk(value, f"{path}[{index}]", found)


def main() -> int:
    out: dict[str, object] = {}
    for url in ENDPOINTS:
        name = url.rsplit("/", 1)[-1]
        try:
            payload = fetch(url)
        except Exception as exc:  # noqa: BLE001 - diagnostic path
            out[name] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        found: list[dict[str, object]] = []
        walk(payload, "", found)
        counts = Counter(str(item["limitCount"]) for item in found)
        statuses = Counter(str(item.get("status")) for item in found)
        out[name] = {
            "payloadsWithHopLimits": len(found),
            "limitCountDistribution": dict(counts),
            "statusDistribution": dict(statuses),
            "samples": found[:6],
        }
        if name == "overview":
            data = payload.get("data") if isinstance(payload, dict) else None
            summary = data.get("summary") if isinstance(data, dict) else None
            if isinstance(summary, dict):
                out["overviewSummary"] = {
                    key: summary.get(key)
                    for key in (
                        "broadcastEnabled",
                        "broadcastAttemptCount",
                        "broadcastRequestCount",
                        "reviewCompletedCount",
                        "reviewNotExecutableCount",
                        "reviewDataIncompleteCount",
                        "overviewProfitableRouteCount",
                        "overviewBestObservedNetProfitUsd",
                    )
                }
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
