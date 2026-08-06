"""Run a live quote-only CoW flash-loan probe from fresh system market data."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SRC_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = SRC_ROOT.parents[1]
CONTRACTS_ROOT = WORKSPACE_ROOT / "contract" / "contracts-dex"
LATEST_EXTREMES_PATH = SRC_ROOT / "runtime" / "state" / "latest_extremes.json"
DEFAULT_OUTPUT_PATH = CONTRACTS_ROOT / "deployments" / "cow-flashloans-probe-live-avalanche.json"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files


def _parse_observed_at(value: Any) -> float | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _latest_extremes_age(path: Path = LATEST_EXTREMES_PATH) -> float | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    observed_ts = _parse_observed_at(payload.get("observed_at"))
    return None if observed_ts is None else max(0.0, datetime.now(timezone.utc).timestamp() - observed_ts)


def _run_observer_window(seconds: int) -> None:
    env = os.environ.copy()
    env.update(
        {
            "RUN_SECONDS": str(max(1, int(seconds))),
            "OBSERVER_REQUIRE_DB_LOCK": "false",
            "SKIP_DATABASE_SCHEMA": "true",
            "OBSERVATION_DB_WRITES": "false",
            "BINANCE_PAIR_HISTORY_WRITES": "false",
            "AAVE_VERIFICATION_ENABLED": "false",
            "REPORT_ONLY_ALERTS": "true",
            "BINANCE_SYMBOL_SELECTION": env.get("BINANCE_SYMBOL_SELECTION", "velocity"),
            "BINANCE_TOP_SYMBOL_LIMIT": env.get("BINANCE_TOP_SYMBOL_LIMIT", "0"),
            "BINANCE_VELOCITY_SIDE_LIMIT": env.get("BINANCE_VELOCITY_SIDE_LIMIT", "100"),
            "BINANCE_CHANGE_WINDOW_SECONDS": env.get("BINANCE_CHANGE_WINDOW_SECONDS", "5"),
            "BINANCE_EXTREME_WRITE_SECONDS": env.get("BINANCE_EXTREME_WRITE_SECONDS", "1"),
        }
    )
    process = subprocess.Popen(
        [sys.executable, "tools/run_market_observer_daemon.py"],
        cwd=SRC_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        process.wait(timeout=max(5, seconds + 30))
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def _run_probe(args: argparse.Namespace) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(
        {
            "COW_FLASHLOAN_PROBE_NETWORK": args.network,
            "COW_FLASHLOAN_PROBE_SOURCE": "live",
            "COW_FLASHLOAN_PROBE_AMOUNT": args.amount,
            "COW_FLASHLOAN_PROBE_ENV": args.sdk_env,
            "COW_FLASHLOAN_LIVE_WAIT_SECONDS": str(args.wait_seconds),
            "COW_FLASHLOAN_LIVE_MAX_AGE_SECONDS": str(args.max_age_seconds),
            "COW_FLASHLOAN_MIN_SIDE_CHANGE_PERCENT": str(args.min_side_change_percent),
            "COW_FLASHLOAN_MIN_SPREAD_PERCENT": str(args.min_spread_percent),
            "COW_FLASHLOAN_PROBE_SLIPPAGE_BPS": str(args.slippage_bps),
            "COW_FLASHLOAN_PROBE_OUTPUT": str(args.output),
        }
    )
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        return {"ok": False, "error": "npm executable not found", "processExitCode": 127}
    completed = subprocess.run(
        [npm, "run", "probe:cow-flashloans", "--silent"],
        cwd=CONTRACTS_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=max(30, args.wait_seconds + 90),
    )
    try:
        report = json.loads(args.output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        report = {
            "ok": False,
            "error": completed.stderr.strip() or completed.stdout.strip() or "probe did not write a JSON report",
        }
    report["processExitCode"] = completed.returncode
    return report


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    routes = report.get("routes") if isinstance(report.get("routes"), list) else []
    first = routes[0] if routes and isinstance(routes[0], dict) else {}
    return {
        "ok": report.get("ok"),
        "network": report.get("network"),
        "sdkEnv": report.get("sdkEnv"),
        "liveStatus": report.get("liveStatus"),
        "route": " -> ".join(first.get("route") or []),
        "routeOk": first.get("ok"),
        "classification": first.get("classification"),
        "error": first.get("error"),
        "singleSolverSettlement": first.get("singleSolverSettlement"),
        "atomicityProof": (report.get("probeReliability") or {}).get("atomicityProof"),
        "output": str(Path(report.get("outputPath") or "").resolve()) if report.get("outputPath") else None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default="avalanche")
    parser.add_argument("--sdk-env", default="prod", choices=["prod", "staging"])
    parser.add_argument("--amount", default="1")
    parser.add_argument("--wait-seconds", type=int, default=60)
    parser.add_argument("--max-age-seconds", type=int, default=180)
    parser.add_argument("--observer-seconds", type=int, default=75)
    parser.add_argument("--min-side-change-percent", default="0.01")
    parser.add_argument("--min-spread-percent", default="0.1")
    parser.add_argument("--slippage-bps", type=int, default=50)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--skip-observer", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_files(SRC_ROOT, override=False)
    args.output = args.output if args.output.is_absolute() else CONTRACTS_ROOT / args.output
    owner_var = f"COW_OWNER_{args.network.upper()}"
    if not os.getenv(owner_var, "").strip():
        print(json.dumps({"ok": False, "error": f"{owner_var} is required"}, indent=2), file=sys.stderr)
        return 2

    age = _latest_extremes_age()
    if not args.skip_observer and (age is None or age > args.max_age_seconds):
        _run_observer_window(args.observer_seconds)
    report = _run_probe(args)
    summary = _summary(report)
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0 if report.get("processExitCode") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
