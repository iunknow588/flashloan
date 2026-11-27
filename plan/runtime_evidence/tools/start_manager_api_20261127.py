#!/usr/bin/env python3
"""Start the Manager API for one chain, with the environment it needs.

The Manager API is the HTTP boundary the mainnet self-healing orchestrator
drives: token admission, pool scope repair, closed-loop rebuild, review
publication and broadcast submission all live behind it.  The runtime
itself does not start it, so a deployment that only starts the runtime has
no manager command path at all.

The script is idempotent: if the bind address already answers ``/healthz``
the process is left alone and the existing one is reported.

Usage:
  start_manager_api_20261127.py [--chain-id 4663] [--tools-dir <release>]
                                [--stop]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TOOLS = ROOT / "flashloan" / "tools" / "target" / "release"
LOG_DIR = ROOT / "plan" / "runtime_evidence" / "diagnostics" / "manager_api"

CHAIN_DATABASES = {
    4663: ROOT / "flashloan" / "db" / "local_store_4663.sqlite3",
    46630: ROOT / "flashloan" / "db" / "local_store_46630.sqlite3",
}
CHAIN_NETWORKS = {4663: "robinhood", 46630: "robinhood-testnet"}

# Every manager module the orchestrator may ask to write is opened for
# ``manager`` authority.  ``legacy`` refuses every ``dryRun=false`` request,
# so a deployment that leaves the default in place would report a healthy
# repair while nothing was written.
AUTHORITY_VARIABLES = (
    "TOKEN_MANAGER_AUTHORITY",
    "CANDIDATE_MANAGER_AUTHORITY",
    "CANDIDATE_EXCHANGE_MANAGER_AUTHORITY",
    "CLOSED_LOOP_MANAGER_AUTHORITY",
    "REVIEW_MANAGER_AUTHORITY",
    "BROADCAST_MANAGER_AUTHORITY",
    "COMPOSITE_MANAGER_AUTHORITY",
    "DIRECTIONAL_POOL_MANAGER_AUTHORITY",
)


def health(base: str, timeout: float = 3.0) -> dict:
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/healthz", timeout=timeout) as response:
            return {"ok": 200 <= response.status < 300,
                    "status": response.status,
                    "body": response.read().decode("utf-8", errors="replace")[:2000]}
    except urllib.error.HTTPError as error:
        return {"ok": False, "status": error.code, "error": str(error)}
    except Exception as error:  # noqa: BLE001 - the probe reports failures
        return {"ok": False, "status": None, "error": str(error)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the Manager API for one chain")
    parser.add_argument("--chain-id", type=int, default=4663)
    parser.add_argument("--tools-dir", type=Path, default=DEFAULT_TOOLS)
    parser.add_argument("--bind", default="127.0.0.1:8091")
    parser.add_argument("--stop", action="store_true", help="stop a running instance")
    args = parser.parse_args()

    if args.chain_id not in CHAIN_DATABASES:
        parser.error(f"unsupported chain id: {args.chain_id}")
    base = f"http://{args.bind}"
    binary = args.tools_dir / "manager-api.exe"
    if not binary.exists():
        parser.error(f"manager-api binary is missing: {binary}")

    if args.stop:
        subprocess.run(["taskkill", "/F", "/IM", binary.name],
                       capture_output=True, text=True)
        print(json.dumps({"stopped": binary.name}, ensure_ascii=False))
        return 0

    current = health(base)
    if current.get("ok"):
        print(json.dumps({"alreadyRunning": True, "base": base,
                          "health": current}, ensure_ascii=False, indent=2))
        return 0

    environment = os.environ.copy()
    environment["MANAGER_DB_PATH"] = str(CHAIN_DATABASES[args.chain_id])
    environment["MANAGER_API_BIND"] = args.bind
    environment["MANAGER_NETWORK"] = CHAIN_NETWORKS[args.chain_id]
    for name in AUTHORITY_VARIABLES:
        environment[name] = "manager"

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    stdout_path = LOG_DIR / f"manager_api_{args.chain_id}_{stamp}.stdout.log"
    stderr_path = LOG_DIR / f"manager_api_{args.chain_id}_{stamp}.stderr.log"
    stdout_handle = stdout_path.open("a", encoding="utf-8")
    stderr_handle = stderr_path.open("a", encoding="utf-8")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    process = subprocess.Popen(
        [str(binary)], cwd=str(ROOT), env=environment,
        stdin=subprocess.DEVNULL, stdout=stdout_handle, stderr=stderr_handle,
        creationflags=creationflags,
    )
    stdout_handle.close()
    stderr_handle.close()

    deadline = time.monotonic() + 60
    last: dict = {}
    while time.monotonic() < deadline:
        if process.poll() is not None:
            print(json.dumps({"ok": False, "error": "manager_api_exited",
                              "returncode": process.returncode,
                              "stderr": str(stderr_path)}, ensure_ascii=False, indent=2))
            return 1
        last = health(base)
        if last.get("ok"):
            print(json.dumps({"ok": True, "pid": process.pid, "base": base,
                              "binary": str(binary),
                              "database": environment["MANAGER_DB_PATH"],
                              "network": environment["MANAGER_NETWORK"],
                              "authority": "manager",
                              "logs": {"stdout": str(stdout_path),
                                       "stderr": str(stderr_path)}},
                             ensure_ascii=False, indent=2))
            return 0
        time.sleep(1)
    print(json.dumps({"ok": False, "error": "manager_api_readiness_timeout",
                      "lastHealth": last}, ensure_ascii=False, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
