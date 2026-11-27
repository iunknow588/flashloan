#!/usr/bin/env python3
"""Ask every published endpoint of the chain for its block number.

The probe only asks a public JSON-RPC endpoint for `eth_blockNumber`:
it reads the chain, it changes nothing.  The answer is the fact the review
path needs -- "how many of the published endpoints are alive right now".

Usage:

    python plan/runtime_evidence/tools/probe_rpc_endpoints_20261126.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ENV_FILE = REPO / "flashloan" / "app_runtime" / ".env"


def _load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_FILE.exists():
        return values
    for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _mask(url: str) -> str:
    if len(url) <= 46:
        return url
    return url[:38] + "..." + url[-8:]


def _probe(url: str) -> dict[str, object]:
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_blockNumber",
            "params": [],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(2048).decode("utf-8", errors="replace")
            elapsed = int((time.monotonic() - started) * 1000)
            try:
                number = json.loads(body).get("result")
            except ValueError:
                number = None
            return {
                "url": _mask(url),
                "http": response.status,
                "elapsed_ms": elapsed,
                "block": number,
                "ok": response.status == 200 and number is not None,
            }
    except urllib.error.HTTPError as error:
        return {
            "url": _mask(url),
            "http": error.code,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "block": None,
            "ok": False,
            "error": str(error.reason)[:120],
        }
    except Exception as error:
        return {
            "url": _mask(url),
            "http": None,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "block": None,
            "ok": False,
            "error": "%s: %s" % (type(error).__name__, str(error)[:140]),
        }


def main() -> None:
    env = _load_env()
    for key, value in os.environ.items():
        env.setdefault(key, value)
    variable = sys.argv[1] if len(sys.argv) > 1 else "ROBINHOOD_MAINNET_RPC_URLS"
    raw = env.get(variable, "")
    urls = [item.strip() for item in raw.split(",") if item.strip()]
    report = {
        "variable": variable,
        "endpoint_count": len(urls),
        "results": [_probe(url) for url in urls],
    }
    report["alive_count"] = sum(
        1 for item in report["results"] if item["ok"]
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
