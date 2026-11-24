#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check whether the resident review workers can cover the closed-loop catalog.

The Rust production publisher only writes a review generation when the
evidence it accumulated covers **every** loop of the closed-loop catalog.
This probe answers the question that decides whether a publication can
happen at all: do the resident workers' routes cover the catalog, and which
loops are they missing?

Everything is read-only: the store is opened with ``mode=ro`` and the
closed-loop catalog is read through the manager API, which is its owner.

Usage:
    python probe_t401_publish_coverage_20261124.py <store.db> [manager_api]
"""

import json
import os
import sqlite3
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"
SCOPE = "core4"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def worker_group_key(symbols):
    """The resident-worker key of one route, as the runtime computes it.

    The runtime compacts a four-symbol path (anchor ... anchor) down to its
    three distinct symbols, then joins them with ``|``.  The same helper the
    runtime uses is imported here so the probe cannot drift from it.
    """
    try:
        sys.path.insert(0, os.path.join(REPO_ROOT, "flashloan"))
        from dex.runtime.closed_loop_identity import worker_group_key_from_symbols

        return worker_group_key_from_symbols(symbols)
    except Exception:
        normalized = [
            str(item).strip().lower() for item in (symbols or []) if str(item).strip()
        ]
        if len(normalized) == 4 and normalized[0] == normalized[-1]:
            normalized = normalized[:3]
        if len(normalized) != 3 or len(set(normalized)) != 3:
            return None
        return "|".join(normalized)


def main():
    store_path = sys.argv[1]
    base = sys.argv[3] if len(sys.argv) > 3 else os.getenv(
        "RUNTIME_BASE", "http://127.0.0.1:5000"
    )
    manager_api = sys.argv[2] if len(sys.argv) > 2 else os.getenv(
        "MANAGER_API_BASE", "http://127.0.0.1:8091"
    )

    connection = sqlite3.connect(
        "file:" + os.path.abspath(store_path).replace("\\", "/") + "?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    catalog = [
        dict(row)
        for row in connection.execute(
            "SELECT loop_key, path_text, path_symbols_json, path_addresses_json, status"
            " FROM manager_v4_closed_loops"
            " WHERE network=? AND chain_id=? AND protocol=? AND scope_key=?",
            (NETWORK, CHAIN_ID, PROTOCOL, SCOPE),
        )
    ]
    connection.close()

    groups = {}
    loop_group = {}
    for row in catalog:
        try:
            symbols = json.loads(row["path_symbols_json"] or "[]")
        except (TypeError, ValueError):
            symbols = []
        key = worker_group_key(symbols)
        groups.setdefault(key, []).append(row["loop_key"])
        loop_group[row["loop_key"]] = key

    with urllib.request.urlopen(
        manager_api.rstrip("/") + "/v1/closed-loops?chainId=%d&limit=1000" % CHAIN_ID,
        timeout=120,
    ) as handle:
        published = json.loads(handle.read().decode("utf-8", errors="replace"))
    data = published.get("data") or {}
    status = data.get("status") or {}

    live_groups = {}
    try:
        with urllib.request.urlopen(
            base.rstrip("/") + "/api/dex/opportunity-scanner/snapshot", timeout=120
        ) as handle:
            snapshot = json.loads(handle.read().decode("utf-8", errors="replace"))
        scanner = snapshot.get("scanner") or (snapshot.get("data") or {}).get("scanner") or {}
        rust = scanner.get("closed_loop_rust_workers") or {}
        for worker in rust.get("workers") or []:
            key = str(worker.get("group_key") or worker.get("groupKey") or "").strip().lower()
            if key:
                live_groups[key] = worker
    except Exception as error:  # pragma: no cover - diagnostic only
        live_groups = {}
        print("# live worker read failed: %s" % error)

    covered = {}
    uncovered = []
    for loop_key, key in loop_group.items():
        if key in live_groups:
            covered.setdefault(key, []).append(loop_key)
        else:
            uncovered.append((loop_key, key))

    runtime_state_loops = {}
    try:
        with urllib.request.urlopen(
            base.rstrip("/") + "/api/dex/opportunity-scanner/snapshot", timeout=120
        ) as handle:
            snapshot = json.loads(handle.read().decode("utf-8", errors="replace"))
        scanner = snapshot.get("scanner") or (snapshot.get("data") or {}).get("scanner") or {}
        queue = scanner.get("closed_loop_review_queue") or {}
        summary = queue.get("loopStateSummary") or {}
        runtime_state_loops = summary.get("loops") or {}
    except Exception as error:  # pragma: no cover - diagnostic only
        print("# runtime loop state read failed: %s" % error)

    catalog_keys = {row["loop_key"] for row in catalog}
    state_in_catalog = [key for key in runtime_state_loops if key in catalog_keys]
    state_out_catalog = [key for key in runtime_state_loops if key not in catalog_keys]

    report = {
        "catalog": {
            "loopCount": len(catalog),
            "distinctGroupKeys": len(groups),
            "groupsWithMoreThanOneLoop": {
                key: len(value) for key, value in sorted(groups.items()) if len(value) > 1
            },
            "emptyGroupKey": len(groups.get(None, [])),
        },
        "liveWorkers": {
            "count": len(live_groups),
            "keys": sorted(live_groups),
            "probeCounts": {
                key: live_groups[key].get("probe_count") for key in sorted(live_groups)
            },
        },
        "coverage": {
            "loopsWithLiveWorker": sum(len(value) for value in covered.values()),
            "loopsWithoutLiveWorker": len(uncovered),
            "catalogGroupsWithoutLiveWorker": sorted(
                key for key in groups if key not in live_groups
            ),
        },
        "runtimeLoopState": {
            "total": len(runtime_state_loops),
            "inCatalog": len(state_in_catalog),
            "notInCatalog": len(state_out_catalog),
            "catalogLoopsWithoutRuntimeState": len(catalog_keys - set(runtime_state_loops)),
            "notInCatalogSample": state_out_catalog[:10],
        },
        "managerApi": {
            "base": manager_api,
            "ok": published.get("ok"),
            "loopCount": status.get("loopCount"),
            "consumersNeedRebuild": status.get("consumersNeedRebuild"),
            "closedLoopVersion": status.get("closedLoopVersion"),
            "materializedChecksum": status.get("materializedChecksum"),
            "publishedLoops": len(data.get("loops") or []),
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
