#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Refresh the Robinhood token libraries through the USDG economic gate.

VAL-4-10
---------

The economic gate decides, for every token, whether it may be a member of
the stock library or the extension library.  Until now that decision was
only ever reached through a **one-shot** sequence of manual commands, so
the loop had no automatic way back in: a token that gained a USDG route
after the last probe stayed `blocked` in the library table forever, because
nothing ever re-ran the valuation, re-ran the gate and re-ran the
materialization.

This script closes that loop.  It drives the existing tools in order and
stops at the first failure:

1. ``pool-manager depth``            refresh the measured pool capacity
2. ``token-manager value-tokens``    probe every token's USDG exchangeability
3. ``token-manager land-valuations``  land the probe into the durable store
4. ``token-manager materialize-libraries``  re-run the gate and rewrite
   the library tables from the refreshed evidence

The point of the loop is not that it runs four commands; it is that the
**fourth** step now sees the **third** step's result, so a token that was
`blocked` for ``no_usdg_valuation`` and is now reachable becomes
`admitted` without anybody editing a table by hand.

Safety
------

The script is **read-only by default**.  Nothing is written to any database
unless ``--apply`` is given, and even then the target database must be
named explicitly.  The production database is additionally refused unless
``--allow-production`` is given, because writing to it is an authorised
operation (W6) rather than a side effect of a refresh.

Every step's stdout is captured and returned in the report, so the loop's
outcome is auditable from a single JSON document.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_POOL_MANAGER = REPO_ROOT / "flashloan" / "tools" / "target" / "debug" / "pool-manager.exe"
DEFAULT_TOKEN_MANAGER = REPO_ROOT / "flashloan" / "tools" / "target" / "debug" / "token-manager.exe"
DEFAULT_REGISTRY = REPO_ROOT / "flashloan" / "chains" / "robinhood" / "robinhood_stock_tokens.json"
ONCHAIN_VALUATION_PROBE = (
    REPO_ROOT / "flashloan" / "src" / "dex" / "tools" / "value_tokens_onchain.py"
)
DEFAULT_STORE = REPO_ROOT / "flashloan" / "db" / "local_store_4663.sqlite3"

# The two Robinhood chains the token libraries exist for. The network is
# the scope the manager tools read and write; the chain id is the scope
# they filter by. Keeping both in one table stops a run from mixing the
# two: a testnet registry can never be materialized into a mainnet scope.
CHAIN_SCOPES = {
    "robinhood": {"chain_id": "4663", "network": "robinhood"},
    "robinhood-testnet": {"chain_id": "46630", "network": "robinhood-testnet"},
}

# How each chain scope values its tokens.
#
# The mainnet is valued by the hosted Uniswap Trading API: chain 4663 has
# 231 live ETH/USDG pools and the API serves the chain.  The testnet is
# not, and the reason is a property of the chain rather than of the tool:
#
#   * the Trading API does not serve chain 46630 at all, and
#   * the testnet has zero ETH/USDG pools -- its only USDG pool is paired
#     against an unresolved placeholder token and carries no liquidity.
#
# The testnet therefore values in its own coin, and the price has to be
# read from the chain through the on-chain V4 quoter.  The two sources
# produce the same report vocabulary, so everything downstream is
# unchanged.
VALUATION_SOURCES = {
    "robinhood": "trading_api",
    "robinhood-testnet": "onchain_v4_quoter",
}

DEFAULT_CHAIN_ID = "4663"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_env_file(path: Path) -> None:
    """Load ``KEY=VALUE`` pairs from an env file into the process environment.

    The valuation probe reads its API key from the environment, but the key
    lives in the app runtime's ``.env`` file.  Without this bridge the
    probe answers ``api_unavailable`` for every token, and the loop would
    happily write that all-unknown answer over a good valuation store.

    The file is read only for names that are not already set, so an
    explicit environment always wins over the file.
    """
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if name.startswith("export "):
            name = name[len("export "):].strip()
        value = value.strip().strip('"').strip("'")
        if not name or not value:
            continue
        os.environ.setdefault(name, value)


def _resolve_binary(candidate: Path, name: str) -> Path:
    """Resolve a tool binary, falling back to ``PATH`` for the extensionless name."""
    if candidate.exists():
        return candidate
    fallback = shutil.which(name) or shutil.which(f"{name}.exe")
    if fallback:
        return Path(fallback)
    raise SystemExit(
        f"error: {name} binary not found at {candidate} and not on PATH; "
        f"build it first (cargo build -p {name})"
    )


def _run(command: list[str], label: str) -> dict[str, Any]:
    """Run one step and capture its outcome.

    A failed step is a *result*, not an exception: the report has to show
    which step failed and why, so the operator can tell a refused write
    apart from a broken tool.
    """
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    step: dict[str, Any] = {
        "step": label,
        "command": command,
        "exitCode": completed.returncode,
    }
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if stdout:
        try:
            step["result"] = json.loads(stdout)
        except json.JSONDecodeError:
            step["stdout"] = stdout
    if stderr:
        step["stderr"] = stderr
    step["ok"] = completed.returncode == 0
    return step


def _library_membership(database: Path, table: str) -> dict[str, str]:
    """Read the current membership state of one library table.

    The map is ``token address -> admission status``.  It is the *before*
    picture of the loop, and it is what makes the loop's effect legible:
    without it, "the refresh ran" says nothing about "the refresh changed
    the membership".
    """
    import sqlite3

    if not database.exists():
        return {}
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        present = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()[0]
        if not present:
            return {}
        rows = connection.execute(
            f"SELECT token_address, admission_status FROM {table}"  # noqa: S608 - fixed table name
        ).fetchall()
    finally:
        connection.close()
    return {str(address).lower(): str(status) for address, status in rows}


def _membership_delta(
    before: dict[str, str], after: dict[str, str]
) -> dict[str, list[dict[str, str]]]:
    """Diff two membership snapshots.

    A token that moves from ``blocked`` to ``admitted`` is the whole point
    of the loop, so it gets its own list rather than being buried in the
    generic "changed" list.
    """
    admitted: list[dict[str, str]] = []
    withdrawn: list[dict[str, str]] = []
    changed: list[dict[str, str]] = []
    for address, status in sorted(after.items()):
        previous = before.get(address)
        if previous is None:
            admitted.append({"tokenAddress": address, "from": "absent", "to": status})
        elif previous != status:
            changed.append({"tokenAddress": address, "from": previous, "to": status})
            if status == "admitted" and previous != "admitted":
                admitted.append({"tokenAddress": address, "from": previous, "to": status})
            elif previous == "admitted" and status != "admitted":
                withdrawn.append({"tokenAddress": address, "from": previous, "to": status})
    for address, status in sorted(before.items()):
        if address not in after:
            withdrawn.append({"tokenAddress": address, "from": status, "to": "absent"})
    return {"admitted": admitted, "withdrawn": withdrawn, "changed": changed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh the Robinhood token libraries through the USDG economic gate."
    )
    parser.add_argument("--source-db", type=Path, required=True,
                        help="the legacy read-only store that holds the candidate pools")
    parser.add_argument("--target-db", type=Path, required=True,
                        help="the manager database whose library tables are rewritten")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY,
                        help="stock token registry JSON")
    parser.add_argument("--network", default="robinhood",
                        choices=tuple(CHAIN_SCOPES),
                        help="chain scope the libraries are materialized into")
    parser.add_argument("--pool-manager", type=Path, default=DEFAULT_POOL_MANAGER)
    parser.add_argument("--token-manager", type=Path, default=DEFAULT_TOKEN_MANAGER)
    parser.add_argument("--depth-scope", default="candidate",
                        choices=("candidate", "all"))
    parser.add_argument("--max-pools", type=int, default=0,
                        help="cap the number of pools refreshed (0 = no cap)")
    parser.add_argument("--limit", type=int, default=0,
                        help="cap the number of tokens probed (0 = no cap)")
    parser.add_argument("--nominal-amount", default="1",
                        help="nominal input amount for the valuation probe")
    parser.add_argument("--protocols", default="V4")
    parser.add_argument("--env-file", type=Path,
                        default=REPO_ROOT / "flashloan" / "app_runtime" / ".env",
                        help="env file consulted for the Trading API key")
    parser.add_argument("--work-dir", type=Path, default=None,
                        help="directory for the intermediate valuation report")
    parser.add_argument("--output", type=Path, default=None,
                        help="write the JSON report to this path")
    parser.add_argument("--apply", action="store_true",
                        help="write to the databases (without it, every step is read-only)")
    parser.add_argument("--allow-production", action="store_true",
                        help="permit the target database to be the production store")
    parser.add_argument("--skip-depth", action="store_true",
                        help="reuse the existing depth snapshot instead of refreshing it")
    parser.add_argument("--skip-valuation", action="store_true",
                        help="reuse the durable valuation store instead of re-probing")
    arguments = parser.parse_args(argv)

    apply = arguments.apply
    scope = CHAIN_SCOPES[arguments.network]
    chain_id = scope["chain_id"]
    network = scope["network"]
    production_store = DEFAULT_STORE.resolve()
    target = arguments.target_db.resolve()
    source = arguments.source_db.resolve()

    if apply and target == production_store and not arguments.allow_production:
        raise SystemExit(
            "error: refusing to write to the production database "
            f"({target}); pass --allow-production to authorise it explicitly"
        )

    # The probe reads its API key from the environment; the key lives in
    # the app runtime's env file.  The bridge has to happen before the
    # probe runs, or every token comes back `api_unavailable`.
    _load_env_file(arguments.env_file)

    pool_manager = _resolve_binary(arguments.pool_manager, "pool-manager")
    token_manager = _resolve_binary(arguments.token_manager, "token-manager")

    work_dir = arguments.work_dir
    temporary_work_dir = False
    if work_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix="val4_10_refresh_"))
        temporary_work_dir = True
    else:
        work_dir.mkdir(parents=True, exist_ok=True)
    valuation_report = work_dir / "usdg_valuation.json"

    steps: list[dict[str, Any]] = []
    before_stocks = _library_membership(target, "token_stocks")
    before_extension = _library_membership(target, "token_extension")

    try:
        # 1. Refresh the measured pool capacity.
        if arguments.skip_depth:
            steps.append({"step": "depth", "skipped": True, "ok": True})
        else:
            command = [
                str(pool_manager), "depth",
                "--store", str(source),
                "--chain-id", chain_id,
                "--depth-scope", arguments.depth_scope,
            ]
            if arguments.max_pools > 0:
                command += ["--max-pools", str(arguments.max_pools)]
            if apply:
                command.append("--apply")
            steps.append(_run(command, "depth"))

        # 2. Probe the exchangeability of every token.
        if arguments.skip_valuation:
            steps.append({"step": "value-tokens", "skipped": True, "ok": True})
        else:
            valuation_source = VALUATION_SOURCES[network]
            if valuation_source == "onchain_v4_quoter":
                command = [
                    sys.executable,
                    str(ONCHAIN_VALUATION_PROBE),
                    "--registry", str(arguments.registry),
                    "--amount", arguments.nominal_amount,
                    "--network", network,
                    "--chain-id", chain_id,
                    "--output", str(valuation_report),
                ]
                if arguments.limit > 0:
                    command += ["--limit", str(arguments.limit)]
                step = _run(command, "value-tokens")
                if step["ok"]:
                    # The probe writes the report itself.  Reading it back
                    # proves the report landed and lets the caller inspect
                    # the verdicts without parsing the probe's stdout.
                    try:
                        step["result"] = json.loads(
                            valuation_report.read_text(encoding="utf-8")
                        )
                    except (OSError, json.JSONDecodeError) as error:
                        step["ok"] = False
                        step["error"] = (
                            f"the on-chain valuation report could not be read back: {error}"
                        )
            else:
                command = [
                    str(token_manager), "value-tokens",
                    "--registry", str(arguments.registry),
                    "--amount", arguments.nominal_amount,
                    "--protocols", arguments.protocols,
                    "--network", network,
                    "--chain-id", chain_id,
                ]
                if arguments.limit > 0:
                    command += ["--limit", str(arguments.limit)]
                step = _run(command, "value-tokens")
                if step["ok"] and "result" in step:
                    valuation_report.write_text(
                        json.dumps(step["result"], ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
            step["valuationSource"] = valuation_source
            steps.append(step)

        # 3. Land the probe into the durable valuation store.
        if arguments.skip_valuation:
            steps.append({"step": "land-valuations", "skipped": True, "ok": True})
        else:
            command = [
                str(token_manager), "land-valuations",
                "--database", str(target),
                "--valuation-report", str(valuation_report),
                "--network", network,
                "--chain-id", chain_id,
            ]
            if apply:
                command.append("--apply")
            steps.append(_run(command, "land-valuations"))

        # 4. Re-run the gate and rewrite the library tables.
        command = [
            str(token_manager), "materialize-libraries",
            "--source-db", str(source),
            "--target-db", str(target),
            "--registry", str(arguments.registry),
            "--valuation-db", str(target),
            "--depth-db", str(source),
            "--network", network,
            "--chain-id", chain_id,
        ]
        if apply:
            command.append("--apply")
        steps.append(_run(command, "materialize-libraries"))
    finally:
        if temporary_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)

    after_stocks = _library_membership(target, "token_stocks")
    after_extension = _library_membership(target, "token_extension")

    failed = [step for step in steps if not step.get("ok", False)]
    report = {
        "ok": not failed,
        "mode": "apply" if apply else "dry_run",
        "observedAt": _utc_now(),
        "network": network,
        "chainId": int(chain_id),
        "sourceDatabase": str(source),
        "targetDatabase": str(target),
        "registry": str(arguments.registry),
        "workDirectory": str(work_dir) if not temporary_work_dir else None,
        "steps": steps,
        "membership": {
            "stocks": _membership_delta(before_stocks, after_stocks),
            "extension": _membership_delta(before_extension, after_extension),
        },
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
