"""ARCH-2b: compare the off-chain simulation with the chain, trade by trade.

What this is
------------

ARCH-2 delivered an off-chain three-hop simulator and proved it equivalent
to the chain with twenty-three parity regressions. ARCH-2b is the step that
turns that proof into a fact about **production**: the simulator has to be
run against the routes the chain actually saw, and the two answers have to
be compared one route at a time.

This script is a **thin CLI over the Rust managers**. It does not read any
table, does not implement the simulation and does not perform the
comparison. It asks the in-memory ``RuntimeCore`` to run the
``review_parity_replay`` command, and Review Manager answers:

* the recorded reviews are Review Manager's own table;
* the pool state is Pool Manager's data, read through Pool Manager's
  published API;
* the simulation and the verdict are Review Manager's, because a parity
  verdict is a piece of review evidence.

An earlier version of this script read four manager-owned tables directly
and performed the off-chain arithmetic in Python. That was an
architectural violation: it gave Python a second implementation of the
Uniswap arithmetic and a second set of table ownership rules. It is
retired. The Rust implementation is locked down by the same test vectors
and by a cross-language equivalence test.

The gate
--------

The rule the plan sets is deliberately strict: the deviation must be zero
before the chain-side preview may be closed and the off-chain computation
may take over.

Two kinds of deviation are counted separately, because they mean different
things:

``executable_mismatch``
    The off-chain simulation says the route pays and the chain says it does
    not, or the other way round. This is the **blocking** defect: the
    off-chain computation cannot be trusted to decide executability.

``amount_mismatch``
    Both sides agree on whether the route pays, but the amounts differ
    beyond tolerance. This is a **precision** defect. It does not by
    itself make the off-chain answer unsafe, but it still has to be driven
    to zero before the chain-side preview is closed.

A route where one side simply produced no number is **not comparable**. It
is counted separately and never treated as a deviation, because "we could
not measure this" is missing evidence, not a disagreement.

Safety
------

The script is **read-only**. The replay only reads; it never writes, and it
never touches the network. The database path is handed to the Rust
managers, which open it read-only.

The script also **never fails a review**. It is a measurement tool. It
reports; it does not gate. The gate belongs to the operator who reads the
report and decides whether the deviation is zero.

Usage
-----

::

    python scripts/arch2b_offchain_parity_replay.py \
        --database /path/to/replica.sqlite3 \
        --output plan/runtime_evidence/reports/arch2b_parity_replay.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any


DEFAULT_CHAIN_ID = 4663
DEFAULT_NETWORK = "robinhood"
DEFAULT_PROTOCOL = "uniswap_v4"


# The flash-loan fee the contract charges, in basis points. It is part of
# the chain's own arithmetic, not a market assumption.
DEFAULT_FLASH_FEE_BPS = 5


# The tolerance applied to the profit comparison. The chain may settle at a
# marginally different amount for reasons that have nothing to do with the
# arithmetic under test, so an exact equality requirement would produce
# noise rather than signal.
DEFAULT_PROFIT_RELATIVE_TOLERANCE = 1e-9


# Which recorded routes the replay covers.
#
# ``newestPerEvaluableRoute`` is the default: a parity check measures a route
# as it stands, so the current attempt of every route the local pool state can
# evaluate is the evidence. ``newestPerRoute`` also covers routes the local
# state cannot evaluate, and ``allAttempts`` replays the whole recorded
# history.
DEFAULT_SELECTION = "newestPerEvaluableRoute"
SELECTIONS = ("newestPerEvaluableRoute", "newestPerRoute", "allAttempts")


def _runtime_core(
    chain_id: int,
    *,
    database_path: str,
    network: str,
    protocol: str,
):
    """Returns the in-memory RuntimeCore for the chain.

    The runtime core is the **only** channel between Python and the Rust
    managers. Python never opens a manager-owned table and never performs a
    manager's work.
    """
    from core.runtime.chain_runtime import chain_runtime_registry

    try:
        runtime = chain_runtime_registry().get(chain_id)
    except KeyError:
        runtime = chain_runtime_registry().register_chain(
            chain_id,
            network=network,
            protocol=protocol,
            database_url=database_path,
            make_active=False,
        )
    return runtime


def _execute_replay(
    *,
    chain_id: int,
    database_path: str,
    network: str,
    protocol: str,
    limit: int | None,
    tolerance: float,
    selection: str = DEFAULT_SELECTION,
) -> dict[str, Any]:
    """Runs the replay through the Rust managers and returns the report."""
    runtime = _runtime_core(
        chain_id,
        database_path=database_path,
        network=network,
        protocol=protocol,
    )
    if runtime is None:
        raise RuntimeError(f"no runtime is registered for chain {chain_id}")
    payload = runtime.execute_command(
        "review_parity_replay",
        request_id=f"arch2b-parity-replay-{chain_id}",
        idempotency_key=(
            f"arch2b-parity-replay-{chain_id}-{database_path}-{limit or 'all'}"
        ),
        dry_run=True,
        options={
            "network": network,
            "protocol": protocol,
            "databasePath": database_path,
            "limit": limit,
            "tolerance": tolerance,
            "selection": selection,
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("the runtime returned a malformed replay payload")
    if payload.get("ok") is False:
        raise RuntimeError(
            f"the runtime rejected the replay: {payload.get('detail') or payload}"
        )
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Replay the recorded reviews against the local pool state and "
            "compare the result with what the chain reported. The work is "
            "done by the Rust managers; this script only asks for the report."
        )
    )
    parser.add_argument(
        "--database",
        default=os.environ.get("DEX_LOCAL_DB_PATH", ""),
        help=(
            "Path to the database replica the managers read. Defaults to "
            "DEX_LOCAL_DB_PATH."
        ),
    )
    parser.add_argument(
        "--chain-id",
        type=int,
        default=int(os.environ.get("DEX_ACTIVE_CHAIN_ID", DEFAULT_CHAIN_ID)),
        help="The chain to replay against. Defaults to DEX_ACTIVE_CHAIN_ID.",
    )
    parser.add_argument(
        "--network",
        default=os.environ.get("DEX_ACTIVE_NETWORK", DEFAULT_NETWORK),
        help="The network label the managers filter on.",
    )
    parser.add_argument(
        "--protocol",
        default=DEFAULT_PROTOCOL,
        help="The protocol label the managers filter on.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Replay at most this many recorded reviews.",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_PROFIT_RELATIVE_TOLERANCE,
        help="The relative tolerance applied to the profit comparison.",
    )
    parser.add_argument(
        "--selection",
        choices=SELECTIONS,
        default=DEFAULT_SELECTION,
        help=(
            "Which recorded routes the replay covers. Defaults to "
            "newestPerEvaluableRoute."
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write the JSON report to this path instead of stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.database:
        parser.error(
            "--database is required (or set DEX_LOCAL_DB_PATH to the replica)"
        )

    report = _execute_replay(
        chain_id=args.chain_id,
        database_path=os.path.abspath(args.database),
        network=args.network,
        protocol=args.protocol,
        limit=args.limit,
        tolerance=args.tolerance,
        selection=args.selection,
    )

    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        directory = os.path.dirname(os.path.abspath(args.output))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.write("\n")
        print(
            json.dumps(
                {
                    "selection": report.get("selection"),
                    "routeCount": report.get("routeCount"),
                    "comparableCount": report.get("comparableCount"),
                    "matchCount": report.get("matchCount"),
                    "deviationCount": report.get("deviationCount"),
                    "evidenceAvailable": report.get("evidenceAvailable"),
                    "evidenceComplete": report.get("evidenceComplete"),
                    "gatePassed": report.get("gatePassed"),
                    "gateReason": report.get("gateReason"),
                    "evidenceBreakdown": report.get("evidenceBreakdown"),
                    "output": os.path.abspath(args.output),
                },
                ensure_ascii=False,
            )
        )
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
