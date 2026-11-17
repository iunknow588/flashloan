"""USDG carrying-capacity banding for the Robinhood candidate pools.

VAL-4-9
--------

The candidate set is ninety-four pools. For every one of them the operator
needs to know a single thing: **can this pool carry a position?** The answer
must be expressed in the venue's own quote asset (USDG), because a USD
threshold cannot express tradable value inside this arbitrage environment.

Three bands, not four
---------------------

The plan calls for exactly three bands:

``carryable``
    The pool was measured on chain, one of its sides has a known USDG
    valuation, and the binding-side capacity is worth at least the
    economic floor. The pool can carry a position of that size.

``dust``
    The pool was measured and valued, but the binding-side capacity is
    worth less than the economic floor. The pool exists and the
    measurement is real; it is simply too small to trade through.

``unmeasured``
    At least one piece of the measurement is missing: no measured
    capacity, no token decimals, or no USDG valuation for the binding
    side. The absence of a measurement is a **different statement** from
    "measured as zero", so an unmeasured pool never lands in ``dust``.

Why the binding side
--------------------

A round-trip arbitrage sends a position through a pool and back, so both
sides have to absorb it. The binding side is the smaller of the two
one-sided capacities after each side is converted into its own token
units. The pool is then converted to USDG through the valuation of the
token on that binding side.

The two reference valuations
----------------------------

Two tokens appear as a binding side and are not part of the stock
registry:

``ETH``
    The native asset, represented on chain by the zero address. It is
    valued by the same Trading API probe as every other token, so its
    valuation has to be supplied in the valuation report.

``USDG``
    The quote asset of the venue. A stablecoin has no route to itself,
    so the Trading API reports ``no_route`` for a USDG/USDG quote. That
    is not a missing measurement: one USDG is worth one USDG by
    definition, so the value is supplied here as a reference constant.

The tool is strictly read-only: it opens the database in read-only mode,
reads a ``pool-manager depth`` report and a ``token-manager value-tokens``
report, and writes three JSON lists plus a count summary.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dex.intent_trade.chains.robinhood.v4_pool_capacity import (  # noqa: E402
    load_economic_policy,
)

NETWORK = "robinhood"
CHAIN_ID = 4663
USDG_DECIMALS = 6

# The native asset, represented on chain by the zero address.
NATIVE_TOKEN_ADDRESS = "0x0000000000000000000000000000000000000000"

# The quote asset of the venue.
CANONICAL_USDG_ADDRESS = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"

# One USDG is worth one USDG. The Trading API has no route from a
# stablecoin to itself, so the value is a definition, not a measurement.
USDG_REFERENCE_VALUE_RAW = 10**USDG_DECIMALS

BAND_CARRYABLE = "carryable"
BAND_DUST = "dust"
BAND_UNMEASURED = "unmeasured"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return document


def _read_valuations(path: Path) -> dict[str, int]:
    """Read the USDG valuations from a ``value-tokens`` report."""
    document = _read_json(path)
    report = document.get("report")
    tokens = report.get("tokens") if isinstance(report, dict) else document.get("tokens")
    if not isinstance(tokens, list):
        raise ValueError(f"{path} carries no token list")
    valuations: dict[str, int] = {}
    for token in tokens:
        if not isinstance(token, dict):
            continue
        if token.get("verdict") != "route_found":
            continue
        address = str(token.get("token_address") or "").strip().lower()
        raw = token.get("usdg_amount_raw")
        if not address or raw is None:
            continue
        try:
            valuations[address] = int(str(raw))
        except (TypeError, ValueError):
            continue
    return valuations


def _read_depth_rows(path: Path) -> dict[str, dict[str, Any]]:
    """Read the measured capacity of every pool from a depth report."""
    document = _read_json(path)
    rows = document.get("refreshed")
    if not isinstance(rows, list):
        raise ValueError(f"{path} carries no refreshed rows")
    measured: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        pool_id = str(row.get("poolId") or "").strip().lower()
        if not pool_id:
            continue
        measured[pool_id] = row
    return measured


def _read_pool_tokens(
    connection: sqlite3.Connection,
) -> dict[str, dict[str, str]]:
    """Read the token pair of every candidate pool."""
    pairs: dict[str, dict[str, str]] = {}
    for table in ("dex_candidate_pools", "manager_dex_candidate_pools"):
        try:
            cursor = connection.execute(
                f"SELECT pool_id, token0, token1 FROM {table} "
                "WHERE network = ? AND chain_id = ?",
                (NETWORK, CHAIN_ID),
            )
        except sqlite3.Error:
            continue
        for pool_id, token0, token1 in cursor:
            key = str(pool_id or "").strip().lower()
            if not key:
                continue
            entry = pairs.setdefault(
                key,
                {
                    "token0": str(token0 or "").strip().lower(),
                    "token1": str(token1 or "").strip().lower(),
                    "sources": [],
                },
            )
            if table not in entry["sources"]:
                entry["sources"].append(table)
            if not entry["token0"] and token0:
                entry["token0"] = str(token0).strip().lower()
            if not entry["token1"] and token1:
                entry["token1"] = str(token1).strip().lower()
    return pairs


def _read_token_decimals(
    connection: sqlite3.Connection,
) -> dict[str, int]:
    """Read the decimals of every known token."""
    decimals: dict[str, int] = {}
    try:
        cursor = connection.execute(
            "SELECT token_address, token_decimals FROM chain_tokens "
            "WHERE network = ? AND chain_id = ?",
            (NETWORK, CHAIN_ID),
        )
    except sqlite3.Error:
        return decimals
    for address, value in cursor:
        if value is None:
            continue
        key = str(address or "").strip().lower()
        if key:
            decimals[key] = int(value)
    return decimals


def _read_valuations_from_db(
    connection: sqlite3.Connection,
) -> dict[str, int]:
    """Read the USDG valuations from the durable valuation store.

    The report file is a transient artifact; the store is the statement the
    database actually holds.  Reading the store directly means the banding
    cannot drift away from the valuations the admission gate actually
    used.

    Only rows with a ``route_found`` verdict and a stored value are
    returned.  A ``no_route`` verdict is a real negative and carries no
    value; an ``api_error`` or ``api_unavailable`` verdict is an unknown
    and carries no value either.  Both are simply absent, so a pool whose
    binding token has no valuation lands in ``unmeasured`` rather than
    being silently treated as worthless.
    """
    valuations: dict[str, int] = {}
    try:
        cursor = connection.execute(
            "SELECT token_address, usdg_amount_raw "
            "FROM token_usdg_valuations "
            "WHERE network = ? AND chain_id = ? "
            "AND verdict = 'route_found' AND usdg_amount_raw IS NOT NULL",
            (NETWORK, CHAIN_ID),
        )
    except sqlite3.Error:
        return valuations
    for address, raw in cursor:
        key = str(address or "").strip().lower()
        if not key:
            continue
        try:
            valuations[key] = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
    return valuations


def _read_depth_rows_from_db(
    connection: sqlite3.Connection,
) -> dict[str, dict[str, Any]]:
    """Read the measured capacity of every pool from the database.

    The pool manager writes the measured capacity into the pool snapshot
    table.  The banding needs the same numbers the admission gate reads,
    so it reads them from the same place instead of from a report file
    that may describe an older measurement.
    """
    measured: dict[str, dict[str, Any]] = {}
    try:
        cursor = connection.execute(
            "SELECT pool_id, has_liquidity, capacity_token0_raw, "
            "capacity_token1_raw, price_impact_bps "
            "FROM v4_exchange_pool_liquidity_snapshots "
            "WHERE network = ? AND chain_id = ? AND capacity_raw IS NOT NULL",
            (NETWORK, CHAIN_ID),
        )
    except sqlite3.Error:
        return measured
    for pool_id, has_liquidity, capacity0, capacity1, price_impact in cursor:
        key = str(pool_id or "").strip().lower()
        if not key:
            continue
        measured[key] = {
            "poolId": key,
            "hasLiquidity": bool(has_liquidity),
            "capacity": {
                "capacityToken0Raw": capacity0,
                "capacityToken1Raw": capacity1,
            },
            "priceImpactBps": price_impact,
            "source": "v4_exchange_pool_liquidity_snapshots",
        }
    return measured


def _candidate_pool_ids(connection: sqlite3.Connection) -> list[str]:
    cursor = connection.execute(
        "SELECT pool_id FROM dex_candidate_pools WHERE network = ? AND chain_id = ? "
        "UNION SELECT pool_id FROM manager_dex_candidate_pools "
        "WHERE network = ? AND chain_id = ?",
        (NETWORK, CHAIN_ID, NETWORK, CHAIN_ID),
    )
    return sorted({str(row[0] or "").strip().lower() for row in cursor if row[0]})


def _decimal(raw: int) -> str:
    """Render a raw USDG amount as a decimal string."""
    sign = "-" if raw < 0 else ""
    value = abs(raw)
    return f"{sign}{value // 10**USDG_DECIMALS}.{value % 10**USDG_DECIMALS:0{USDG_DECIMALS}d}"


def _band_pool(
    pool_id: str,
    depth_row: dict[str, Any] | None,
    pair: dict[str, str] | None,
    decimals: dict[str, int],
    valuations: dict[str, int],
    minimum_usdg_raw: int,
) -> dict[str, Any]:
    """Classify one candidate pool into exactly one of the three bands."""
    record: dict[str, Any] = {
        "poolId": pool_id,
        "band": BAND_UNMEASURED,
        "reason": "",
        "token0": pair.get("token0", "") if pair else "",
        "token1": pair.get("token1", "") if pair else "",
        "bindingToken": "",
        "bindingSide": "",
        "bindingCapacityRaw": None,
        "bindingTokenDecimals": None,
        "valuationUsdgRaw": None,
        "valuationSource": "",
        "depthUsdgRaw": None,
        "depthUsdg": None,
        "minimumEconomicPositionUsdgRaw": minimum_usdg_raw,
    }
    if depth_row is None:
        record["reason"] = "no measured depth row"
        return record
    if pair is None or not pair.get("token0") or not pair.get("token1"):
        record["reason"] = "candidate pool has no token pair"
        return record

    capacity = depth_row.get("capacity")
    if not isinstance(capacity, dict):
        record["reason"] = "depth row carries no capacity"
        return record
    # A pool with no live liquidity IS measured: the measurement says the
    # pool is empty. It carries no depth, but it is not an unknown, so it
    # belongs in `dust` with an explicit reason rather than in
    # `unmeasured`. The distinction matters because "unmeasured" asks for
    # another measurement, while "empty" will never change.
    if not depth_row.get("hasLiquidity"):
        record["band"] = BAND_DUST
        record["reason"] = "pool has no live liquidity"
        record["depthUsdgRaw"] = 0
        record["depthUsdg"] = _decimal(0)
        return record
    try:
        capacity0 = int(str(capacity.get("capacityToken0Raw") or "0"))
        capacity1 = int(str(capacity.get("capacityToken1Raw") or "0"))
    except (TypeError, ValueError):
        record["reason"] = "depth row carries an unreadable capacity"
        return record

    sides = (
        ("token0", pair["token0"], capacity0, depth_row.get("token0Decimals")),
        ("token1", pair["token1"], capacity1, depth_row.get("token1Decimals")),
    )
    # The binding side is the one with the smaller decimals-normalised
    # capacity. A side without decimals cannot be compared, so it cannot
    # be the binding side either: the measurement is incomplete.
    candidates: list[tuple[float, str, str, int, int | None]] = []
    for side_name, token_address, capacity_raw, raw_decimals in sides:
        token_decimals = decimals.get(token_address)
        if token_decimals is None and raw_decimals is not None:
            try:
                token_decimals = int(raw_decimals)
            except (TypeError, ValueError):
                token_decimals = None
        if token_decimals is None:
            continue
        if capacity_raw <= 0:
            continue
        normalised = capacity_raw / (10**token_decimals)
        candidates.append((normalised, side_name, token_address, capacity_raw, token_decimals))
    if not candidates:
        record["reason"] = "no side has both a measured capacity and known decimals"
        return record
    candidates.sort(key=lambda item: (item[0], item[1]))
    _, side_name, token_address, capacity_raw, token_decimals = candidates[0]

    record["bindingSide"] = side_name
    record["bindingToken"] = token_address
    record["bindingCapacityRaw"] = capacity_raw
    record["bindingTokenDecimals"] = token_decimals

    # The quote asset is its own unit of account, so one USDG is worth one
    # USDG by definition. The Trading API has no route from a stablecoin
    # to itself, so that value is a reference constant rather than a
    # measurement. Supplying it here keeps this function self-contained:
    # a caller cannot accidentally produce an `unmeasured` pool merely by
    # forgetting to seed the reference valuation.
    if token_address == CANONICAL_USDG_ADDRESS:
        valuation = USDG_REFERENCE_VALUE_RAW
    else:
        valuation = valuations.get(token_address)
    if valuation is None:
        record["reason"] = "binding token has no USDG valuation"
        return record
    record["valuationUsdgRaw"] = valuation
    record["valuationSource"] = (
        "reference_definition_usdg_quote_asset"
        if token_address == CANONICAL_USDG_ADDRESS
        else "uniswap_trading_api_quote"
    )

    # depth_usdg_raw = capacity_raw * valuation_usdg_raw / 10^token_decimals
    numerator = capacity_raw * valuation
    depth_raw = numerator // (10**token_decimals)
    record["depthUsdgRaw"] = depth_raw
    record["depthUsdg"] = _decimal(depth_raw)
    if depth_raw <= 0:
        record["reason"] = "binding side converts to a zero USDG depth"
        record["band"] = BAND_DUST
        return record

    if depth_raw >= minimum_usdg_raw:
        record["band"] = BAND_CARRYABLE
        record["reason"] = "binding side carries the minimum economic position"
    else:
        record["band"] = BAND_DUST
        record["reason"] = "binding side is below the minimum economic position"
    return record


def build_report(
    database: Path,
    depth_report: Path | None,
    valuation_report: Path | None,
    valuation_database: Path | None = None,
    depth_database: Path | None = None,
) -> dict[str, Any]:
    """Build the three-band report and the count summary.

    The valuations and the measured capacities can come from either a
    report file or the durable database.  When a database is given it
    wins: the store is the statement the rest of the system actually
    holds, and a report file that has been copied around since the last
    landing can describe an older observation.
    """
    policy = load_economic_policy()
    minimum_usdg_raw = int(policy["minPoolDepthUsdgRaw"])

    if valuation_database is not None:
        valuation_connection = sqlite3.connect(
            f"file:{valuation_database}?mode=ro", uri=True
        )
        try:
            valuations = _read_valuations_from_db(valuation_connection)
        finally:
            valuation_connection.close()
    elif valuation_report is not None:
        valuations = _read_valuations(valuation_report)
    else:
        raise ValueError("a valuation report or a valuation database is required")

    if depth_database is not None:
        depth_connection = sqlite3.connect(f"file:{depth_database}?mode=ro", uri=True)
        try:
            measured = _read_depth_rows_from_db(depth_connection)
        finally:
            depth_connection.close()
    elif depth_report is not None:
        measured = _read_depth_rows(depth_report)
    else:
        raise ValueError("a depth report or a depth database is required")

    # The quote asset is its own unit of account. It is a definition, not a
    # measurement, so it is supplied as a reference constant rather than
    # being probed: a stablecoin has no route to itself.
    # The USDG reference valuation is also supplied inside `_band_pool`
    # itself, so seeding it here is belt-and-braces rather than the only
    # place the definition can enter.
    valuations.setdefault(CANONICAL_USDG_ADDRESS, USDG_REFERENCE_VALUE_RAW)

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        pool_ids = _candidate_pool_ids(connection)
        pairs = _read_pool_tokens(connection)
        decimals = _read_token_decimals(connection)
    finally:
        connection.close()

    records = [
        _band_pool(
            pool_id,
            measured.get(pool_id),
            pairs.get(pool_id),
            decimals,
            valuations,
            minimum_usdg_raw,
        )
        for pool_id in pool_ids
    ]
    records.sort(key=lambda record: record["poolId"])

    bands: dict[str, list[dict[str, Any]]] = {
        BAND_CARRYABLE: [],
        BAND_DUST: [],
        BAND_UNMEASURED: [],
    }
    for record in records:
        bands[record["band"]].append(record)

    return {
        "generatedAt": _utc_now_iso(),
        "mode": "read-only",
        "network": NETWORK,
        "chainId": CHAIN_ID,
        "database": str(database),
        "depthReport": str(depth_report) if depth_report is not None else None,
        "valuationReport": str(valuation_report) if valuation_report is not None else None,
        "depthDatabase": str(depth_database) if depth_database is not None else None,
        "valuationDatabase": (
            str(valuation_database) if valuation_database is not None else None
        ),
        "usdgDecimals": USDG_DECIMALS,
        "minimumPoolDepthUsdgRaw": minimum_usdg_raw,
        "minimumPoolDepthUsdg": _decimal(minimum_usdg_raw),
        "bandBasis": "binding_side_decimal_normalised_then_usdg",
        "referenceValuations": {
            CANONICAL_USDG_ADDRESS: {
                "usdgRaw": USDG_REFERENCE_VALUE_RAW,
                "source": "reference_definition_usdg_quote_asset",
                "note": "one USDG is worth one USDG; the quote asset has no route to itself",
            },
        },
        "counts": {
            "candidatePoolCount": len(records),
            "carryableCount": len(bands[BAND_CARRYABLE]),
            "dustCount": len(bands[BAND_DUST]),
            "unmeasuredCount": len(bands[BAND_UNMEASURED]),
            "valuedTokenCount": len(valuations),
            "measuredPoolCount": len(measured),
        },
        "bands": bands,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Band the candidate pools by USDG carrying capacity.",
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument(
        "--depth-report",
        type=Path,
        default=None,
        help="A pool-manager depth report. Optional when --depth-database is given.",
    )
    parser.add_argument(
        "--valuation-report",
        type=Path,
        default=None,
        help="A token-manager value-tokens report. Optional when --valuation-database is given.",
    )
    parser.add_argument(
        "--depth-database",
        type=Path,
        default=None,
        help=(
            "The database holding the measured pool capacities. The durable "
            "store wins over a depth report: it is the statement the "
            "admission gate actually reads."
        ),
    )
    parser.add_argument(
        "--valuation-database",
        type=Path,
        default=None,
        help=(
            "The database holding the durable USDG valuations. The durable "
            "store wins over a valuation report."
        ),
    )
    parser.add_argument(
        "--extra-valuation-report",
        type=Path,
        default=None,
        help=(
            "An additional value-tokens report, for example the native-asset "
            "probe, merged into the valuations."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("plan/runtime_evidence/reports"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Summary report path. The three band lists are written next to it.",
    )
    arguments = parser.parse_args(argv)

    valuation_report = arguments.valuation_report
    if arguments.extra_valuation_report:
        # The two reports are merged by the caller into one document; the
        # tool only ever reads a single document.
        valuation_report = arguments.extra_valuation_report

    if arguments.depth_report is None and arguments.depth_database is None:
        parser.error("one of --depth-report or --depth-database is required")
    if valuation_report is None and arguments.valuation_database is None:
        parser.error("one of --valuation-report or --valuation-database is required")

    report = build_report(
        arguments.database,
        arguments.depth_report,
        valuation_report,
        valuation_database=arguments.valuation_database,
        depth_database=arguments.depth_database,
    )
    output_dir = arguments.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    summary_path = arguments.output or (output_dir / f"val4_9_usdg_capacity_bands_{stamp}.json")
    _write_json(summary_path, report)

    counts = report["counts"]
    band_paths: dict[str, str] = {}
    for band, records in report["bands"].items():
        band_path = output_dir / f"val4_9_usdg_capacity_{band}_{stamp}.json"
        _write_json(
            band_path,
            {
                "generatedAt": report["generatedAt"],
                "mode": "read-only",
                "network": NETWORK,
                "chainId": CHAIN_ID,
                "band": band,
                "minimumPoolDepthUsdgRaw": report["minimumPoolDepthUsdgRaw"],
                "minimumPoolDepthUsdg": report["minimumPoolDepthUsdg"],
                "count": len(records),
                "pools": records,
            },
        )
        band_paths[band] = str(band_path)

    print(
        json.dumps(
            {
                "ok": True,
                "summary": str(summary_path),
                "bandReports": band_paths,
                "counts": counts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
