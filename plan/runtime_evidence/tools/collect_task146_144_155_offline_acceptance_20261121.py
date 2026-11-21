# -*- coding: utf-8 -*-
"""Offline functional-acceptance evidence collector (read-only, no chain access).

This collector closes the *evidence* half of three open tasks whose code is
already implemented, without touching any chain, any production database, or
any broadcast path:

  TASK-146  five-layer responsibility routing drill (no cross-chain, no
            layer crossing, a failed repair keeps the candidate)
  TASK-144  positive-profit broadcast gate and error self-healing (dry-run
            only; a real broadcast stays a separately authorised action)
  TASK-155  the executor request is bound to the anchor of its own closed
            loop (ETH and WETH are two independent funding anchors)

The drill is a *replay* of the repository's own offline regression suites.
It therefore proves the behaviour contract, not the market. Every number in
the report is a test outcome, and the report says so explicitly, so that a
later reader can never mistake an offline drill for a live measurement.

Nothing here writes to a production database, sends a transaction, or reads
a chain. The only outputs are the JSON report and the captured log.
"""
import datetime
import hashlib
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REPORTS = os.path.join(ROOT, "plan", "runtime_evidence", "reports")
LOGS = os.path.join(REPORTS, "logs")
OUT = os.path.join(REPORTS, "task146_144_155_offline_functional_acceptance_20261121.json")

# Each suite is pinned to the task whose acceptance it serves. A suite that
# disappears or is renamed must fail the drill loudly rather than silently
# reduce the covered surface.
SUITES = [
    {
        "task": "TASK-146",
        "purpose": "five-layer responsibility routing, candidate retention, chain isolation",
        "paths": [
            "flashloan/app_runtime/tests/unit/test_mainnet_arbitrage_healer_repair_chain.py",
            "scripts/tests/test_mainnet_arbitrage_healer.py",
        ],
    },
    {
        "task": "TASK-144",
        "purpose": "dry-run broadcast gate and the fail-closed gate decision",
        "paths": [
            "flashloan/app_runtime/tests/unit/test_dry_run_gate.py",
            "flashloan/app_runtime/tests/unit/test_gate_decision.py",
        ],
    },
    {
        "task": "TASK-155",
        "purpose": "the executor request is bound to the anchor of its own closed loop",
        "paths": [],
        "cargo": {
            "workspace": "flashloan/rs-src",
            "filter": "worker_pool",
        },
    },
]


def run(argv, cwd=None, log_name=None):
    """Run a command and capture its output, returning a structured record."""
    started = datetime.datetime.now(datetime.timezone.utc)
    proc = subprocess.run(
        argv,
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    finished = datetime.datetime.now(datetime.timezone.utc)
    combined = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if log_name:
        os.makedirs(LOGS, exist_ok=True)
        with open(os.path.join(LOGS, log_name), "w", encoding="utf-8") as handle:
            handle.write(combined)
    return {
        "argv": argv,
        "cwd": os.path.relpath(cwd, ROOT) if cwd else ".",
        "exitCode": proc.returncode,
        "startedAt": started.isoformat(),
        "finishedAt": finished.isoformat(),
        "durationMs": int((finished - started).total_seconds() * 1000),
        "log": f"plan/runtime_evidence/reports/logs/{log_name}" if log_name else None,
        "outputTail": combined[-4000:],
    }


def parse_pytest_summary(text):
    """Parse the final pytest summary line into a counter dict."""
    import re

    counters = {"passed": 0, "failed": 0, "skipped": 0, "error": 0, "xfailed": 0, "xpassed": 0}
    matches = re.findall(r"(\d+)\s+(passed|failed|skipped|error|xfailed|xpassed)\b", text)
    for value, label in matches:
        counters[label] = int(value)
    return counters


def parse_cargo_summary(text):
    """Parse cargo's per-target test result lines."""
    import re

    passed = failed = ignored = 0
    for line in text.splitlines():
        match = re.match(r"^test result:\s+ok\.\s+(\d+) passed;\s+(\d+) failed;\s+(\d+) ignored", line)
        if match:
            passed += int(match.group(1))
            failed += int(match.group(2))
            ignored += int(match.group(3))
        match = re.match(r"^test result:\s+FAILED\.\s+(\d+) passed;\s+(\d+) failed;", line)
        if match:
            passed += int(match.group(1))
            failed += int(match.group(2))
    return {"passed": passed, "failed": failed, "skipped": ignored, "error": 0}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    results = []
    all_passed = True

    for suite in SUITES:
        entry = {
            "task": suite["task"],
            "purpose": suite["purpose"],
            "mode": "offline-replay",
            "runs": [],
        }
        if suite.get("paths"):
            for index, rel in enumerate(suite["paths"]):
                absolute = os.path.join(ROOT, rel.replace("/", os.sep))
                if not os.path.exists(absolute):
                    entry["runs"].append({
                        "path": rel,
                        "status": "missing",
                        "detail": "the pinned offline suite is absent; the drill fails closed",
                    })
                    all_passed = False
                    continue
                record = run(
                    [sys.executable, "-m", "pytest", rel, "-q"],
                    log_name=f"task146_144_155_offline_{suite['task'].lower().replace('-', '_')}_{index}.log",
                )
                counters = parse_pytest_summary(record["outputTail"])
                record.update({
                    "path": rel,
                    "counters": counters,
                    "status": "passed" if record["exitCode"] == 0 and counters["failed"] == 0 else "failed",
                })
                if record["status"] != "passed":
                    all_passed = False
                entry["runs"].append(record)
        if suite.get("cargo"):
            cargo = suite["cargo"]
            workspace = os.path.join(ROOT, cargo["workspace"].replace("/", os.sep))
            record = run(
                ["cargo", "test", "--offline", "--lib", cargo["filter"]],
                cwd=workspace,
                log_name=f"task146_144_155_offline_{suite['task'].lower().replace('-', '_')}_cargo.log",
            )
            counters = parse_cargo_summary(record["outputTail"])
            record.update({
                "workspace": cargo["workspace"],
                "filter": cargo["filter"],
                "counters": counters,
                "status": "passed" if record["exitCode"] == 0 and counters["failed"] == 0 else "failed",
            })
            if record["status"] != "passed":
                all_passed = False
            entry["runs"].append(record)
        results.append(entry)

    package = {
        "task": "TASK-146 / TASK-144 / TASK-155",
        "title": "offline functional acceptance drill",
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mode": "offline-replay",
        "policy": (
            "read-only and offline: no chain access, no production database write, no "
            "broadcast, no historical depth or business data backfill. Every number "
            "below is a test outcome of the repository's own regression suites, not a "
            "market measurement."
        ),
        "whatThisProves": (
            "the responsibility routing, the dry-run gate and the anchor binding are "
            "implemented and enforced in the current tree, and the enforcement is "
            "reproducible from a clean checkout"
        ),
        "whatThisDoesNotProve": [
            "any live-market profitability, depth, capacity or PnL figure",
            "that a real mainnet broadcast would succeed",
            "review freshness on the mainnet store (TASK-143)",
            "the testnet Manager loop prerequisite (TASK-147)",
        ],
        "allPassed": all_passed,
        "suites": results,
        "remainingAuthorisedActions": [
            {
                "task": "TASK-143",
                "action": "regenerate the mainnet reviews with --apply to obtain a fresh state",
                "needsExplicitAuthorisation": True,
                "performedInThisRun": False,
            },
            {
                "task": "TASK-147",
                "action": "re-pin the candidate_exchange domain and rebuild the testnet observation",
                "needsExplicitAuthorisation": True,
                "performedInThisRun": False,
            },
            {
                "task": "TASK-144",
                "action": "a real mainnet broadcast",
                "needsExplicitAuthorisation": True,
                "performedInThisRun": False,
            },
        ],
    }

    os.makedirs(REPORTS, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(package, handle, ensure_ascii=False, indent=1)

    totals = {"passed": 0, "failed": 0, "skipped": 0}
    for suite in results:
        for record in suite["runs"]:
            counters = record.get("counters") or {}
            totals["passed"] += counters.get("passed", 0)
            totals["failed"] += counters.get("failed", 0)
            totals["skipped"] += counters.get("skipped", 0)

    print("wrote", os.path.relpath(OUT, ROOT))
    print(json.dumps({"allPassed": all_passed, **totals}, ensure_ascii=False))
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
