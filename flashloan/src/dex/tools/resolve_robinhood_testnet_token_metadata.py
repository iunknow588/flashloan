"""Backfill unresolved Robinhood ERC20 metadata into SQLite.

The V4 factory deploys tokens whose on-chain ``symbol()`` is
``0X`` plus eight hex digits.  Those values are storage placeholders, not
display symbols.  This tool explicitly resolves the page-facing token set
from the chain RPC and writes only ``chain_tokens`` columns for rows whose
stored symbol is still an unresolved placeholder.

It never imports JSON snapshots and never replaces rows that already carry a
resolved symbol.  ``--include-exchange`` is opted into by the operator and
extends the target set only from executable V4 pools connected to one funding
library endpoint (core, stocks, or test).  The opposite endpoint has no
allowlist, but it must still satisfy the token admission minimums: symbol,
decimals, and no placeholder metadata.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.db.database_path import absolute_database_path  # noqa: E402
from core.config.fee_policy import MAX_CANDIDATE_FIXED_FEE  # noqa: E402
from core.db.storage_business_io import execute, query  # noqa: E402
from core.db.token_authority import require_legacy_token_write  # noqa: E402
from core.db.token_symbols import is_unresolved_placeholder_symbol  # noqa: E402

from dex.tools.robinhood_testnet_token_metadata_rpc import (  # noqa: E402
    _chain_rpc_url,
    _chain_rpc_urls,
    _connect,
    _decimals_int,
    _fetch_metadata,
    _metadata_for_address,
)


DEFAULT_NETWORK = "robinhood-testnet"
DEFAULT_CHAIN_ID = 46630
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
DYNAMIC_FEE_FLAG = 8388608
CORE_LIBRARY_ADDRESSES = {
    ZERO_ADDRESS,
    "0x0bd7d308f8e1639fab988df18a8011f41eacad73",
    "0x5fc5360d0400a0fd4f2af552add042d716f1d168",
    "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34",
}
DEFAULT_REPAIR_ADDRESSES = [
    "0x450d9bc42be9a45269678669a786d2c3c5221bdf",
    "0x4e5c427499c43b222fec1ef60f5e2164b904d84e",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_address(value: Any) -> str:
    text = _text(value).lower()
    return text if text.startswith("0x") and len(text) == 42 else ""


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _funding_library_tokens(
    database: str,
    *,
    network: str,
    chain_id: int,
) -> set[str]:
    """Return tokens owned by the core, stocks, or test funding libraries.

    The legacy projection uses category/anchor metadata in ``chain_tokens``.
    Stock and test categories take precedence over the anchor marker because
    stocks and TZ may also carry an anchor symbol.
    """

    rows = query(
        database,
        """
        SELECT token_address, anchor_symbol, token_metadata_json
        FROM chain_tokens
        WHERE network = ? AND chain_id = ?
        """,
        (network, chain_id),
    )
    addresses: set[str] = {address for address in CORE_LIBRARY_ADDRESSES}
    for row in rows:
        address = _normalized_address(row.get("token_address"))
        if not address:
            continue
        metadata = _json_loads(row.get("token_metadata_json") or {})
        category = (
            _text(metadata.get("category")).lower()
            if isinstance(metadata, dict)
            else ""
        )
        if category in {"robinhood_stock", "test_tokens"}:
            addresses.add(address)
            continue
        if _text(row.get("anchor_symbol")) or address in CORE_LIBRARY_ADDRESSES:
            addresses.add(address)
    return addresses


def _exchange_extension_addresses(
    database: str,
    *,
    network: str,
    chain_id: int,
    funding_tokens: set[str],
) -> set[str]:
    """Return both endpoints of executable pools touching a funding token."""

    funding = sorted(
        address
        for address in (_normalized_address(value) for value in funding_tokens)
        if address
    )
    if not funding:
        return set()
    placeholders = ",".join("?" for _ in funding)
    rows = query(
        database,
        f"""
        SELECT token0, token1
        FROM v4_exchange_pools
        WHERE network = ? AND chain_id = ?
          AND fee > 0 AND fee <= ?
          AND (fee & ?) = 0
          AND (
            COALESCE(hooks, '') = ''
            OR lower(hooks) = ?
          )
          AND (
            lower(token0) IN ({placeholders})
            OR lower(token1) IN ({placeholders})
          )
        """,
        (
            network,
            chain_id,
            MAX_CANDIDATE_FIXED_FEE,
            DYNAMIC_FEE_FLAG,
            ZERO_ADDRESS,
            *funding,
            *funding,
        ),
    )
    addresses: set[str] = set()
    for row in rows:
        for value in (row.get("token0"), row.get("token1")):
            address = _normalized_address(value)
            if address:
                addresses.add(address)
    return addresses


def _scoped_addresses(
    database: str,
    *,
    network: str,
    chain_id: int,
    include_exchange: bool,
) -> set[str]:
    queries = [
        "SELECT token0 AS address FROM dex_candidate_pools",
        "SELECT token1 AS address FROM dex_candidate_pools",
        "SELECT anchor_address AS address FROM dex_candidate_pool_pairs",
        "SELECT counter_address AS address FROM dex_candidate_pool_pairs",
        "SELECT token_address AS address FROM dex_candidate_anchor_tokens",
        "SELECT token0 AS address FROM market_observation_intermediate_pools",
        "SELECT token1 AS address FROM market_observation_intermediate_pools",
        "SELECT token_x_address AS address FROM market_observation_yz_pairs",
        "SELECT token_y_address AS address FROM market_observation_yz_pairs",
        "SELECT token_x_address AS address FROM market_observation_abnormal_yz_pairs",
        "SELECT token_y_address AS address FROM market_observation_abnormal_yz_pairs",
        "SELECT anchor_address AS address FROM v4_closed_loops",
        "SELECT input_address AS address FROM v4_closed_loop_hops",
        "SELECT output_address AS address FROM v4_closed_loop_hops",
    ]
    addresses: set[str] = set()
    for sql in queries:
        rows = query(
            database,
            f"""
            {sql}
            WHERE network = ? AND chain_id = ?
            """,
            (network, chain_id),
        )
        for row in rows:
            address = _normalized_address(row.get("address"))
            if address:
                addresses.add(address)
    if include_exchange:
        funding_tokens = _funding_library_tokens(
            database,
            network=network,
            chain_id=chain_id,
        )
        addresses.update(funding_tokens)
        addresses.update(
            _exchange_extension_addresses(
                database,
                network=network,
                chain_id=chain_id,
                funding_tokens=funding_tokens,
            )
        )
    return addresses


def _existing_token_rows(
    database: str,
    *,
    network: str,
    chain_id: int,
) -> dict[str, dict[str, Any]]:
    rows = query(
        database,
        """
        SELECT token_address, token_symbol, token_name, token_decimals,
               token_metadata_json, source_json
        FROM chain_tokens
        WHERE network = ? AND chain_id = ?
        """,
        (network, chain_id),
    )
    return {
        _normalized_address(row.get("token_address")): row
        for row in rows
        if _normalized_address(row.get("token_address"))
    }


def _candidate_scoped_rows(
    database: str,
    *,
    network: str,
    chain_id: int,
    include_exchange: bool,
    all_tokens: bool = False,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    existing = _existing_token_rows(
        database,
        network=network,
        chain_id=chain_id,
    )
    if all_tokens:
        addresses = sorted(
            _normalized_address(value)
            for value in existing
            if _normalized_address(value)
        )
    else:
        addresses = sorted(_scoped_addresses(
            database,
            network=network,
            chain_id=chain_id,
            include_exchange=include_exchange,
        ))
    return _select_target_addresses(addresses, existing), existing


def _select_target_addresses(
    addresses: Iterable[str],
    existing: dict[str, dict[str, Any]],
) -> list[str]:
    """Return rows that still need symbol, name, or decimals resolution.

    A row is a target when any required metadata field is empty, unresolved,
    or has an invalid decimals value.  Rows that already carry a resolved
    symbol, a real name, and valid decimals are left untouched.
    """
    targets: list[str] = []
    for address in addresses:
        address = _normalized_address(address)
        if not address:
            continue
        row = existing.get(address)
        symbol = _text(row.get("token_symbol") if row else "")
        name = _text(row.get("token_name") if row else "")
        if (
            row is None
            or not symbol
            or is_unresolved_placeholder_symbol(symbol)
            or is_unresolved_placeholder_symbol(name)
            or not name
            or _decimals_int(row.get("token_decimals")) is None
        ):
            targets.append(address)
    return targets


def _mark_remaining_placeholders(
    database: str,
    existing: dict[str, dict[str, Any]],
    targets: list[str],
    *,
    network: str,
    chain_id: int,
) -> list[str]:
    """Persist name-missing markers for unresolved targets whose RPC failed.

    The read layer must be able to distinguish a real ERC20 symbol from a
    factory-deployed placeholder that has no name.  Storing the fallback symbol
    and the missing flag in SQLite makes that state explicit and lets later
    metadata refreshes target the same addresses without guessing.
    """
    rows: list[dict[str, Any]] = []
    changed: list[str] = []
    for address in targets:
        row = existing.get(address)
        if not row:
            continue
        metadata = _json_loads(row.get("token_metadata_json") or {})
        if not isinstance(metadata, dict):
            metadata = {}
        if metadata.get("nameMissing") or metadata.get("nameFallbackSymbol"):
            continue
        symbol = _text(row.get("token_symbol"))
        metadata["nameMissing"] = True
        metadata["nameFallbackSymbol"] = symbol
        rows.append(
            {
                "address": address,
                "symbol": symbol,
                "name": symbol,
                "decimals": row.get("token_decimals"),
                "ok": False,
                "source": "chain_live_erc20_unresolved",
                "rpcUrl": None,
                "metadata": metadata,
            }
        )
        changed.append(address)
    if rows:
        _write_rows(
            database,
            rows,
            network=network,
            chain_id=chain_id,
        )
    return changed


def _desired_source_json(metadata: dict[str, Any], symbol: str) -> dict[str, Any]:
    name_missing = bool(metadata.get("nameMissing") or metadata.get("nameFallbackSymbol"))
    fallback = _text(metadata.get("nameFallbackSymbol")) or (symbol if name_missing else "")
    return {
        "nameMissing": name_missing,
        "nameFallbackSymbol": fallback if name_missing else None,
    }


def _repair_source_json(
    database: str,
    addresses: list[str],
    *,
    network: str,
    chain_id: int,
    dry_run: bool,
) -> tuple[list[str], list[str], list[str]]:
    """Rewrite source_json markers so they match token_metadata_json.

    Only ``source_json.nameMissing/nameFallbackSymbol/resolvedAt`` change;
    symbol/name/decimals are never touched.  Returns ``(changed, missing,
    inconsistent)`` address lists for the CLI report.
    """

    normalized = sorted(
        {address for address in (_normalized_address(value) for value in addresses) if address}
    )
    missing: list[str] = []
    changed: list[str] = []
    inconsistent: list[str] = []
    if not normalized:
        return [], missing, inconsistent
    placeholders = ",".join("?" for _ in normalized)
    rows = query(
        database,
        f"""
        SELECT token_address, token_symbol, token_name, token_decimals,
               token_metadata_json, source_json, updated_at
        FROM chain_tokens
        WHERE network = ? AND chain_id = ? AND token_address IN ({placeholders})
        """,
        (network, chain_id, *normalized),
    )
    by_address = {_normalized_address(row.get("token_address")): row for row in rows}
    now = _utc_now_iso()
    for address in normalized:
        row = by_address.get(address)
        if not row:
            missing.append(address)
            continue
        metadata = _json_loads(row.get("token_metadata_json") or {})
        if not isinstance(metadata, dict):
            metadata = {}
        symbol = _text(row.get("token_symbol"))
        desired = _desired_source_json(metadata, symbol)
        source = _json_loads(row.get("source_json") or {})
        if not isinstance(source, dict):
            source = {}
        current_missing = bool(
            source.get("nameMissing") or source.get("nameFallbackSymbol")
        )
        current_fallback = _text(source.get("nameFallbackSymbol"))
        already_aligned = (
            bool(current_missing) == bool(desired["nameMissing"])
            and (current_fallback or "") == (desired["nameFallbackSymbol"] or "")
            and bool(source.get("resolvedAt"))
        )
        if already_aligned:
            continue
        repaired = {
            "source": _text(source.get("source"))
            or _text(metadata.get("source"))
            or "chain_live_erc20_unresolved",
            "rpcUrl": _text(source.get("rpcUrl"))
            or _text(metadata.get("rpcUrl")),
            "resolvedAt": now,
            "nameMissing": bool(desired["nameMissing"]),
            "nameFallbackSymbol": desired["nameFallbackSymbol"],
        }
        if dry_run:
            changed.append(address)
            continue
        require_legacy_token_write("chain_tokens.metadata.repair_source", database)
        execute(
            database,
            """
            UPDATE chain_tokens
            SET source_json = ?, updated_at = ?
            WHERE network = ? AND chain_id = ? AND token_address = ?
            """,
            (_json_dumps(repaired), now, network, chain_id, address),
        )
        changed.append(address)
    if dry_run:
        return changed, missing, inconsistent
    rows = query(
        database,
        f"""
        SELECT token_address, token_symbol, token_metadata_json, source_json
        FROM chain_tokens
        WHERE network = ? AND chain_id = ? AND token_address IN ({placeholders})
        """,
        (network, chain_id, *normalized),
    )
    by_address = {_normalized_address(row.get("token_address")): row for row in rows}
    for address in normalized:
        row = by_address.get(address)
        if not row:
            continue
        metadata = _json_loads(row.get("token_metadata_json") or {})
        if not isinstance(metadata, dict):
            metadata = {}
        source = _json_loads(row.get("source_json") or {})
        if not isinstance(source, dict):
            source = {}
        desired = _desired_source_json(metadata, _text(row.get("token_symbol")))
        if bool(source.get("nameMissing")) != bool(desired["nameMissing"]) or (
            _text(source.get("nameFallbackSymbol"))
            != (desired["nameFallbackSymbol"] or "")
        ):
            inconsistent.append(address)
    return changed, missing, inconsistent


def _repair_columns_from_metadata(
    database: str,
    *,
    network: str,
    chain_id: int,
    dry_run: bool,
) -> tuple[list[str], int]:
    """Replay resolved metadata_json fields into stale chain_tokens columns.

    Pool-index refreshes can innocently carry factory placeholder symbols and
    no decimals for addresses whose authoritative metadata was already fetched
    from the chain.  That V4 seed path must never replace valid fields with
    invalid ones, and this repair only fills invalid fields from the existing
    metadata payload.  It never changes a resolved symbol/name/decimals, so it
    is safe to run as part of incremental maintenance without RPC.
    """

    rows = query(
        database,
        """
        SELECT token_address, token_symbol, token_name, token_decimals,
               token_metadata_json, source_json, updated_at
        FROM chain_tokens
        WHERE network = ? AND chain_id = ?
        ORDER BY token_address
        """,
        (network, chain_id),
    )
    now = _utc_now_iso()
    changed: list[str] = []
    unchanged = 0
    for row in rows:
        address = _normalized_address(row.get("token_address"))
        if not address:
            unchanged += 1
            continue
        metadata = _json_loads(row.get("token_metadata_json") or {})
        if not isinstance(metadata, dict):
            unchanged += 1
            continue
        symbol = _text(row.get("token_symbol"))
        name = _text(row.get("token_name"))
        decimals = row.get("token_decimals")
        desired_symbol = _text(metadata.get("symbol"))
        desired_name = _text(metadata.get("name"))
        desired_decimals = _decimals_int(metadata.get("decimals"))
        next_symbol = symbol
        if (
            (not symbol or is_unresolved_placeholder_symbol(symbol))
            and desired_symbol
            and not is_unresolved_placeholder_symbol(desired_symbol)
        ):
            next_symbol = desired_symbol
        next_name = name
        if (
            (not name or is_unresolved_placeholder_symbol(name))
            and desired_name
            and not is_unresolved_placeholder_symbol(desired_name)
        ):
            next_name = desired_name
        next_decimals = decimals
        if _decimals_int(decimals) is None and desired_decimals is not None:
            next_decimals = desired_decimals
        values = (next_symbol, next_name, next_decimals)
        current = (symbol, name, decimals)
        if values == current:
            unchanged += 1
            continue
        changed.append(address)
        if dry_run:
            continue
        require_legacy_token_write(
            "chain_tokens.metadata.repair_columns",
            database,
        )
        execute(
            database,
            """
            UPDATE chain_tokens
            SET token_symbol = ?, token_name = ?, token_decimals = ?,
                updated_at = ?
            WHERE network = ? AND chain_id = ? AND token_address = ?
            """,
            (
                next_symbol,
                next_name,
                next_decimals,
                now,
                network,
                chain_id,
                address,
            ),
        )
    return changed, unchanged


def _write_rows(
    database: str,
    rows: list[dict[str, Any]],
    *,
    network: str,
    chain_id: int,
) -> int:
    if not rows:
        return 0
    require_legacy_token_write("chain_tokens.metadata.resolve", database)
    now = _utc_now_iso()
    updated = 0
    for row in rows:
        address = _normalized_address(row.get("address"))
        symbol = _text(row.get("symbol"))
        if not address or not symbol:
            continue
        raw_name = _text(row.get("name"))
        row_name_missing = bool(row.get("nameMissing"))
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {
                "symbol": symbol,
                "name": raw_name or symbol,
                "decimals": row.get("decimals"),
                "category": None,
                "source": row.get("source") or "chain_live_erc20",
                "rpcUrl": row.get("rpcUrl"),
                "resolvedAt": now,
                "nameMissing": (
                    row_name_missing
                    or not raw_name
                    or is_unresolved_placeholder_symbol(raw_name)
                ),
                "nameFallbackSymbol": (
                    symbol
                    if (
                        row_name_missing
                        or not raw_name
                        or is_unresolved_placeholder_symbol(raw_name)
                    )
                    else None
                ),
            }
        name = _text(metadata.get("name") or raw_name or symbol)
        name_missing = bool(
            metadata.get("nameMissing") or metadata.get("nameFallbackSymbol")
        )
        fallback_symbol = _text(metadata.get("nameFallbackSymbol"))
        if name_missing and not fallback_symbol:
            fallback_symbol = symbol
        source = {
            "source": row.get("source") or "chain_live_erc20",
            "rpcUrl": row.get("rpcUrl"),
            "resolvedAt": now,
            "nameMissing": name_missing,
            "nameFallbackSymbol": fallback_symbol if name_missing else None,
        }
        updated += execute(
            database,
            """
            UPDATE chain_tokens
            SET token_symbol = ?, token_name = ?, token_decimals = ?,
                token_metadata_json = ?, source_json = ?, updated_at = ?
            WHERE network = ? AND chain_id = ? AND token_address = ?
            """,
            (
                symbol,
                name,
                row.get("decimals"),
                _json_dumps(metadata),
                _json_dumps(source),
                now,
                network,
                chain_id,
                address,
            ),
        )
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve Robinhood Testnet ERC20 metadata from chain RPC and "
            "write only unresolved chain_tokens rows in SQLite."
        )
    )
    parser.add_argument("--database", default=None, help="SQLite database path")
    parser.add_argument("--network", default=DEFAULT_NETWORK)
    parser.add_argument("--chain-id", type=int, default=DEFAULT_CHAIN_ID)
    parser.add_argument("--rpc-url", default=None, help="Override chain.json RPC")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None, help="Max target count")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--include-exchange",
        action="store_true",
        help=(
            "Include the other endpoint of executable V4 pools connected to "
            "one core/stocks/test funding token."
        ),
    )
    parser.add_argument(
        "--all-tokens",
        action="store_true",
        help=(
            "Resolve every chain_tokens row in this network/chain that still "
            "has an unresolved symbol or a missing name, not just the "
            "candidate/exchange scope."
        ),
    )
    parser.add_argument(
        "--repair-source-json",
        action="store_true",
        help="Align source_json name markers with token_metadata_json without changing token metadata",
    )
    parser.add_argument(
        "--repair-columns-from-metadata",
        action="store_true",
        help=(
            "Replay valid token_metadata_json fields into stale chain_tokens "
            "columns without RPC; never overwrites already resolved fields"
        ),
    )
    parser.add_argument(
        "--addresses",
        default=None,
        help="Comma-separated addresses for --repair-source-json; replaces the two default residual addresses",
    )
    args = parser.parse_args()

    database = str(absolute_database_path(args.database))
    if args.repair_columns_from_metadata:
        changed, unchanged = _repair_columns_from_metadata(
            database,
            network=args.network,
            chain_id=args.chain_id,
            dry_run=args.dry_run,
        )
        action = "wouldRepair" if args.dry_run else "repaired"
        print(
            f"metadata-column-repair: network={args.network} chainId={args.chain_id} "
            f"{action}={len(changed)} unchanged={unchanged} dryRun={args.dry_run}"
        )
        return 0
    if args.repair_source_json:
        addresses = (
            [value.strip() for value in args.addresses.split(",") if value.strip()]
            if args.addresses
            else list(DEFAULT_REPAIR_ADDRESSES)
        )
        changed, missing, inconsistent = _repair_source_json(
            database,
            addresses,
            network=args.network,
            chain_id=args.chain_id,
            dry_run=args.dry_run,
        )
        action = "wouldRepair" if args.dry_run else "repaired"
        print(
            f"source-json-repair: network={args.network} chainId={args.chain_id} "
            f"{action}={len(changed)} missing={len(missing)} "
            f"inconsistent={len(inconsistent)} dryRun={args.dry_run}"
        )
        for address in missing:
            print(f"  missing: {address}")
        for address in inconsistent:
            print(f"  inconsistent: {address}")
        return 0 if not missing and not inconsistent else 1

    targets, existing = _candidate_scoped_rows(
        database,
        network=args.network,
        chain_id=args.chain_id,
        include_exchange=args.include_exchange,
        all_tokens=args.all_tokens,
    )
    if args.limit is not None:
        targets = targets[: max(0, int(args.limit))]
    remaining = [
        address
        for address in targets
        if is_unresolved_placeholder_symbol(
            _text(existing.get(address, {}).get("token_symbol"))
        )
    ]
    print(
        f"scope: network={args.network} chainId={args.chain_id} "
        f"targets={len(targets)} existingResolved={len(existing) - len(targets)} "
        f"remainingPlaceholders={len(remaining)}"
    )
    if args.dry_run or not targets:
        print("dry-run: no writes performed")
        return 0
    marked = _mark_remaining_placeholders(
        database,
        existing,
        remaining,
        network=args.network,
        chain_id=args.chain_id,
    )
    print(f"markedUnresolved={len(marked)}")
    metadata, errors = _fetch_metadata(
        targets,
        rpc_url=args.rpc_url,
        network=args.network,
        expected_chain_id=args.chain_id,
        workers=args.workers,
    )
    rows = [
        metadata[address]
        for address in targets
        if metadata.get(address, {}).get("ok")
    ]
    updated = _write_rows(
        database,
        rows,
        network=args.network,
        chain_id=args.chain_id,
    )
    print(
        f"resolved={len(rows)} updated={updated} failed={len(errors)} "
        f"remaining={len(targets) - len(rows)}"
    )
    for error in errors[:20]:
        print(f"  error: {error}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
