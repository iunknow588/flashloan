#!/usr/bin/env python3
"""Live, read-only probe of the deployed liquidity refresh path.

The probe calls the **deployed** Rust extension exactly the way the runtime
does: the endpoint the caller names first, the chain's own endpoints behind
it, ``apply=False`` so the live store is opened read-only.  It answers one
question: does the shipped binary still fail to reach the chain, or is the
network the only thing that was broken?

Usage:

    python plan/runtime_evidence/tools/probe_liquidity_refresh_live_20261126.py
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
STORE = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"
ENV_FILE = REPO / "flashloan" / "app_runtime" / ".env"
EXTENSION = REPO / "flashloan" / "rs-src" / "target" / "release" / "flashloan_rs.dll"
CHAIN_ID = 4663
NETWORK = "robinhood"


def env_urls() -> list[str]:
    raw = ""
    for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().startswith("ROBINHOOD_MAINNET_RPC_URLS="):
            raw = line.split("=", 1)[1].strip()
            break
    return [part.strip() for part in raw.split(",") if part.strip()]


def sample_pool_ids(limit: int = 5) -> list[str]:
    uri = "file:" + STORE.as_posix() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only = ON")
    try:
        rows = connection.execute(
            "SELECT pool_id FROM v4_exchange_pool_liquidity_snapshots"
            " WHERE network = ? AND chain_id = ? ORDER BY pool_id LIMIT ?",
            (NETWORK, CHAIN_ID, limit),
        ).fetchall()
    finally:
        connection.close()
    return [row[0] for row in rows]


def load_extension():
    loader = importlib.machinery.ExtensionFileLoader("flashloan_rs", str(EXTENSION))
    spec = importlib.util.spec_from_file_location("flashloan_rs", str(EXTENSION), loader=loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_extension()
    urls = env_urls()
    pool_ids = sample_pool_ids()
    report: dict[str, object] = {
        "at": time.strftime("%H:%M:%S"),
        "extension": {
            "path": str(EXTENSION),
            "mtime": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(EXTENSION.stat().st_mtime)
            ),
        },
        "endpointCount": len(urls),
        "poolIds": pool_ids,
    }
    try:
        receipt = module.refresh_v4_pool_liquidity(
            NETWORK,
            CHAIN_ID,
            json.dumps(pool_ids),
            rpc_url=urls[0] if urls else None,
            batch_size=5,
            timeout_seconds=20,
            apply=False,
            database_path=str(STORE),
        )
    except Exception as error:  # noqa: BLE001 - the probe reports every failure
        report["exception"] = f"{type(error).__name__}: {error}"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    report["ok"] = bool(receipt.get("ok")) if isinstance(receipt, dict) else False
    report["error"] = (
        str(receipt.get("error") or "")[:600] if isinstance(receipt, dict) else str(receipt)[:600]
    )
    if isinstance(receipt, dict):
        report["receiptKeys"] = sorted(receipt.keys())
        report["receipt"] = {
            key: receipt.get(key)
            for key in (
                "ok",
                "apply",
                "network",
                "poolCount",
                "refreshedCount",
                "writtenCount",
                "errorCount",
                "manager",
            )
            if key in receipt
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
