#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read the live mainnet broadcast gate and the worker's view of it.

Usage:
    python probe_t401_gate_status_20261124.py [chain_id]
"""

import json
import os
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"E:\2026OPC大赛\flashLoan")
sys.path.insert(0, r"E:\2026OPC大赛\flashLoan\flashloan\src")

CHAIN_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 4663
ENV_FILE = r"E:\2026OPC大赛\flashLoan\flashloan\app_runtime\.env"


def load_app_env():
    """Load the app's own env file, the way the runtime does.

    Without this the gate reads the **bare process** defaults and reports
    "not true" for every switch, which is not what the running system sees.
    """
    if not os.path.isfile(ENV_FILE):
        return 0
    loaded = 0
    with open(ENV_FILE, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)
            loaded += 1
    return loaded


def main():
    loaded = load_app_env()
    print("app env loaded: %d keys" % loaded)
    from dex.runtime import rust_closed_loop

    gate = rust_closed_loop.broadcast_gate_status(chain_id=CHAIN_ID)
    print("broadcast_gate_status(chain_id=%d):" % CHAIN_ID)
    print(json.dumps(gate, ensure_ascii=False, indent=2))
    with urllib.request.urlopen(
        "http://127.0.0.1:5000/api/dex/opportunity-scanner/snapshot", timeout=60
    ) as handle:
        payload = json.loads(handle.read().decode("utf-8", errors="replace"))
    workers = payload.get("scanner", {}).get("closed_loop_rust_workers", {})
    gates = [
        item.get("broadcastGate")
        for item in (workers.get("workers") or [])
        if isinstance(item, dict) and item.get("broadcastGate")
    ]
    print("\nworker broadcastGate samples: %d" % len(gates))
    for item in gates[:2]:
        print(json.dumps(item, ensure_ascii=False))


if __name__ == "__main__":
    main()
