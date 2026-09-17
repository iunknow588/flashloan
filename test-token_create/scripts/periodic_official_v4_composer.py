"""Periodically invoke ArbitrageComposer on Robinhood Testnet.

The script shells out to the existing Hardhat composer live script so the
composer deployment/preflight/broadcast logic stays in one place.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEX_PROJECT = REPO_ROOT / "contract" / "robinhood-testnet" / "DEX"
COMPOSER_REL = "../../../test-token_create/scripts/testnet-v4-official-composer-live.js"


def run_once(*, broadcast: bool, timeout_seconds: float) -> dict:
    env = dict(os.environ)
    env["ROBINHOOD_TESTNET_ALLOW_V4_AMM_LIVE_TEST"] = "true"
    if broadcast:
        env["ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_COMPOSER_BROADCAST"] = "true"
    command = ["npx.cmd", "hardhat", "run", COMPOSER_REL, "--network", "robinhoodTestnet"]
    proc = subprocess.run(
        command,
        cwd=str(DEX_PROJECT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    output = "\n".join([proc.stdout or "", proc.stderr or ""])
    marker = "OFFICIAL_V4_COMPOSER_RESULT_JSON "
    for line in (proc.stdout or "").splitlines():
        idx = line.find(marker)
        if idx >= 0:
            payload = line[idx + len(marker) :].strip()
            try:
                return dict(payload=json.loads(payload), returncode=proc.returncode, output=output)
            except json.JSONDecodeError:
                break
    return {"payload": None, "returncode": proc.returncode, "output": output}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=60.0, help="seconds between composer calls")
    parser.add_argument("--timeout", type=float, default=180.0, help="per-loop hardhat timeout")
    parser.add_argument("--broadcast", action="store_true", help="allow real testnet broadcast")
    parser.add_argument("--once", action="store_true", help="run a single loop and exit")
    parser.add_argument("--max-runs", type=int, default=0, help="stop after N runs; 0 means forever")
    args = parser.parse_args()

    runs = 0
    while True:
        runs += 1
        print(f"[periodic-composer] run {runs} starting at {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
        result = run_once(broadcast=args.broadcast, timeout_seconds=args.timeout)
        if result.get("returncode") != 0:
            print(f"[periodic-composer] run {runs} failed rc={result.get('returncode')}", flush=True)
            print(result.get("output", "")[-4000:], flush=True)
        else:
            payload = result.get("payload") or {}
            print(
                f"[periodic-composer] run {runs} ok broadcast={payload.get('broadcast', False)} "
                f"profit={payload.get('profit')} tx={payload.get('txHash') or '-'}",
                flush=True,
            )
        if args.once:
            return 0
        if args.max_runs and runs >= args.max_runs:
            return 0
        time.sleep(max(0.5, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
