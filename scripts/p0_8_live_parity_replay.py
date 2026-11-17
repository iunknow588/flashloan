"""P0-8: replay one **live** review payload against the chain, in memory.

What this is
------------

The parity replay that ``scripts/arch2b_offchain_parity_replay.py`` runs
reads the recorded reviews out of Review Manager's own table. That works
while the reviews exist, but the reviews are **regenerable transient
data**: they are cleaned on purpose, because a review row is a snapshot of
a market that has already moved on and has no lasting statistical value.
After the cleanup the replay has nothing to measure and correctly refuses
to pass its gate (``no_routes``).

The way out is **not** to restore the reviews, and **not** to persist the
reviews again. The way out is to replay the review **while it is being
produced**: the live review already knows the pool state it quoted, so the
parity input can be derived from that payload and held **in memory** for
the duration of one measurement.

This script is the entry point for that measurement. It

* reads one live review payload from a JSON file (or stdin),
* hands it to the in-memory ``RuntimeCore`` as the
  ``review_parity_replay_payload`` command, and
* writes the read-only verdict it gets back.

What this is **not**
-------------------

It is not a second parity implementation. Like every other harness in this
repository it is a thin CLI over the Rust managers:

* it never opens a database,
* it never names a manager-owned table,
* it never computes a Uniswap amount,
* it never writes the payload anywhere.

The route extraction, the review-time state extraction, the simulation, the
attribution and the verdict all live in Review Manager, and are locked down
by the Rust test suite.

Safety
------

The command is **read-only and non-persistent**. The payload lives in
memory for the duration of the call and is never written to the database,
so no long-term schema is added for transient data.

Usage
-----

::

    python scripts/p0_8_live_parity_replay.py \
        --database /path/to/replica.sqlite3 \
        --payload /path/to/live_review.json \
        --output plan/runtime_evidence/reports/p0_8_live_parity.json
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
DEFAULT_FLASH_FEE_BPS = 0


# The tolerance applied to the profit comparison.
DEFAULT_PROFIT_RELATIVE_TOLERANCE = 1e-9


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


def _load_payload(path: str | None) -> dict[str, Any]:
    """Reads the live review payload.

    The payload is the transient output of one live review. It is read from
    a file (or stdin) and handed straight to the manager; it is never
    stored.
    """
    if path:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    else:
        raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"the live review payload is not valid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError("the live review payload must be a JSON object")
    return payload


def _execute_payload_replay(
    *,
    chain_id: int,
    database_path: str,
    network: str,
    protocol: str,
    payload: dict[str, Any],
    flash_fee_bps: int = DEFAULT_FLASH_FEE_BPS,
    tolerance: float = DEFAULT_PROFIT_RELATIVE_TOLERANCE,
) -> dict[str, Any]:
    """Asks the RuntimeCore to replay one live payload.

    The command is a read-only measurement and is always issued as a dry
    run. The payload is carried in the request and is never persisted.
    """
    runtime = _runtime_core(
        chain_id,
        database_path=database_path,
        network=network,
        protocol=protocol,
    )
    if runtime is None:
        raise RuntimeError(f"no runtime is registered for chain {chain_id}")
    response = runtime.execute_command(
        "review_parity_replay_payload",
        request_id=f"p0-8-live-parity-{chain_id}",
        idempotency_key=(
            f"p0-8-live-parity-{chain_id}-{database_path}-"
            f"{payload.get('attemptKey') or payload.get('poolPathKey') or 'payload'}"
        ),
        dry_run=True,
        options={
            "network": network,
            "protocol": protocol,
            "flashFeeBps": flash_fee_bps,
            "tolerance": tolerance,
            "payload": payload,
        },
    )
    if not isinstance(response, dict):
        raise RuntimeError("the runtime returned a malformed replay payload")
    if response.get("ok") is False:
        raise RuntimeError(
            f"the runtime rejected the replay: {response.get('detail') or response}"
        )
    data = response.get("data")
    if isinstance(data, dict):
        return data
    return response


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Replay one live review payload against the chain, in memory. "
            "The work is done by the Rust managers; this script only asks for "
            "the report and writes it out."
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
        "--payload",
        default=None,
        help=(
            "Path to the live review payload. Reads stdin when omitted."
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
        "--flash-fee-bps",
        type=int,
        default=DEFAULT_FLASH_FEE_BPS,
        help="The flash-loan fee in basis points. Defaults to 0.",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_PROFIT_RELATIVE_TOLERANCE,
        help="The relative tolerance applied to the profit comparison.",
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

    payload = _load_payload(args.payload)
    report = _execute_payload_replay(
        chain_id=args.chain_id,
        database_path=os.path.abspath(args.database),
        network=args.network,
        protocol=args.protocol,
        payload=payload,
        flash_fee_bps=args.flash_fee_bps,
        tolerance=args.tolerance,
    )

    document = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        directory = os.path.dirname(os.path.abspath(args.output))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(document)
            handle.write("\n")
        print(
            json.dumps(
                {
                    "state": report.get("state"),
                    "profitOffchainRaw": report.get("profitOffchainRaw"),
                    "profitOnchainRaw": report.get("profitOnchainRaw"),
                    "profitRelativeDelta": report.get("profitRelativeDelta"),
                    "reviewTimeStateUsed": report.get("reviewTimeStateUsed"),
                    "reviewTimePoolCount": report.get("reviewTimePoolCount"),
                    "persisted": report.get("persisted", False),
                    "output": os.path.abspath(args.output),
                },
                ensure_ascii=False,
            )
        )
    else:
        print(document)
    return 0


if __name__ == "__main__":
    sys.exit(main())
