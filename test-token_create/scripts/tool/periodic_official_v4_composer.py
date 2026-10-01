"""Periodically invoke ArbitrageComposer on a Robinhood V4 chain.

The script shells out to the existing Hardhat composer live script so the
composer deployment/preflight/broadcast logic stays in one place.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SHARED_TOOL_DIR = REPO_ROOT / "scripts" / "tool"
if str(SHARED_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_TOOL_DIR))

from chain_config import chain_config, set_chain_env  # noqa: E402

COMPOSER_REL = "../../../test-token_create/scripts/tool/testnet-v4-official-composer-live.js"
NPX = shutil.which("npx.cmd") or shutil.which("npx") or ("npx.cmd" if os.name == "nt" else "npx")


def run_once(*, chain_id: int, broadcast: bool, timeout_seconds: float, rpc_url: str | None = None, manifest_path: str | None = None, dex_dir: str | None = None, hardhat_network: str | None = None) -> dict:
    cfg = chain_config(
        chain_id,
        rpc_url_override=rpc_url,
        manifest_override=manifest_path,
        hardhat_network_override=hardhat_network,
    )
    env = set_chain_env(
        chain_id,
        rpc_url_override=rpc_url,
        manifest_override=manifest_path,
        hardhat_network_override=hardhat_network,
    )
    project = Path(dex_dir).expanduser().resolve() if dex_dir else cfg["contract_project"]
    env["ROBINHOOD_TESTNET_ALLOW_V4_AMM_LIVE_TEST"] = "true"
    if broadcast:
        env["ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_COMPOSER_BROADCAST"] = "true"
    command = [NPX, "hardhat", "run", COMPOSER_REL, "--network", cfg["hardhat_network"]]
    proc = subprocess.run(
        command,
        cwd=str(project),
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
    parser.add_argument("--chain", type=int, default=int(os.getenv("FLASHLOAN_CHAIN_ID", os.getenv("FLASHLOAN_DEFAULT_CHAIN_ID", "46630"))), help="chain id; default 46630")
    parser.add_argument("--rpc-url", default=None, help="RPC URL override")
    parser.add_argument("--manifest-path", default=None, help="manifest.json path override")
    parser.add_argument("--dex-dir", default=None, help="Hardhat DEX project directory override")
    parser.add_argument("--hardhat-network", default=None, help="Hardhat network name override")
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
        result = run_once(
            chain_id=args.chain,
            broadcast=args.broadcast,
            timeout_seconds=args.timeout,
            rpc_url=args.rpc_url,
            manifest_path=args.manifest_path,
            dex_dir=args.dex_dir,
            hardhat_network=args.hardhat_network,
        )
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
