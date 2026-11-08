"""Backfill unresolved Robinhood ERC20 metadata into SQLite.

The V4 factory deploys tokens whose on-chain ``symbol()`` is
``0X`` plus eight hex digits.  Those values are storage placeholders, not
display symbols.  This tool explicitly resolves the page-facing token set
from the chain RPC and writes only ``chain_tokens`` columns for rows whose
stored symbol is still an unresolved placeholder.

It never imports JSON snapshots and never replaces rows that already carry a
resolved symbol.  ``--include-exchange`` is opt-in because the full exchange
pool table contains tens of thousands of currency addresses; the page surfaces
only need the candidate/observation/closed-loop scope by default.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from eth_abi import decode
import requests
import urllib3
from web3 import Web3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.db.database_path import absolute_database_path  # noqa: E402
from core.db.storage_business_io import execute, query  # noqa: E402
from core.db.token_authority import require_legacy_token_write  # noqa: E402
from core.db.token_symbols import is_unresolved_placeholder_symbol  # noqa: E402


DEFAULT_NETWORK = "robinhood-testnet"
DEFAULT_CHAIN_ID = 46630
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
BATCH_CALL_SIZE = 8
HTTP_RETRY_COUNT = 3
DEFAULT_REPAIR_ADDRESSES = [
    "0x450d9bc42be9a45269678669a786d2c3c5221bdf",
    "0x4e5c427499c43b222fec1ef60f5e2164b904d84e",
]

ERC20_METADATA_ABI = [
    {
        "inputs": [],
        "name": "symbol",
        "outputs": [{"type": "string", "name": ""}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "name",
        "outputs": [{"type": "string", "name": ""}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"type": "uint8", "name": ""}],
        "stateMutability": "view",
        "type": "function",
    },
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


def _chain_rpc_url(network: str | None = None) -> str:
    return _chain_rpc_urls(network)[0]


def _chain_rpc_urls(network: str | None = None) -> list[str]:
    resolved_network = str(network or DEFAULT_NETWORK).strip().lower()
    path = SRC_ROOT / "chains" / resolved_network / "chain.json"
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    rpc = payload.get("rpc") or {}
    urls = [_text(rpc.get("primary"))]
    urls.extend(_text(value) for value in rpc.get("fallbacks") or [])
    urls = [url for url in urls if url]
    if not urls:
        raise RuntimeError(f"chain.json has no rpc endpoints: {path}")
    return urls


def _connect(
    rpc_url: str | None,
    *,
    network: str | None = None,
    expected_chain_id: int | None = None,
) -> tuple[Web3, str]:
    url = _text(rpc_url) or _chain_rpc_url(network)
    w3 = Web3(
        Web3.HTTPProvider(
            url,
            request_kwargs={"timeout": 30, "verify": False},
        )
    )
    actual_chain_id = int(w3.eth.chain_id)
    resolved_expected = int(expected_chain_id or DEFAULT_CHAIN_ID)
    if actual_chain_id != resolved_expected:
        raise RuntimeError(
            f"RPC returned chainId {actual_chain_id}, expected {resolved_expected}"
        )
    return w3, url


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
    if include_exchange:
        queries.extend(
            [
                "SELECT token0 AS address FROM v4_exchange_pools",
                "SELECT token1 AS address FROM v4_exchange_pools",
            ]
        )
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
    """Return rows that still need symbol or name resolution.

    A row is a target when its symbol is empty/unresolved or its name is
    still missing.  Rows that already carry a resolved symbol and a real name
    are left untouched.
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


def _metadata_for_address(
    w3: Web3,
    address: str,
    *,
    rpc_url: str,
) -> dict[str, Any]:
    if address == ZERO_ADDRESS:
        return {
            "address": address,
            "symbol": "ETH",
            "name": "Ether",
            "decimals": 18,
            "ok": True,
            "source": "known_native",
        }
    checksum = Web3.to_checksum_address(address)
    contract = w3.eth.contract(address=checksum, abi=ERC20_METADATA_ABI)
    symbol = ""
    name = ""
    decimals = None
    try:
        symbol = _text(contract.functions.symbol().call())
    except Exception:
        symbol = ""
    try:
        name = _text(contract.functions.name().call())
    except Exception:
        name = ""
    try:
        decimals_raw = contract.functions.decimals().call()
        decimals = int(decimals_raw)
    except Exception:
        decimals = None
    ok = bool(symbol) and decimals is not None
    return {
        "address": address,
        "symbol": symbol or "",
        "name": name,
        "nameMissing": not name or is_unresolved_placeholder_symbol(name),
        "decimals": decimals,
        "ok": ok,
        "source": "chain_live_erc20",
        "rpcUrl": rpc_url,
    }


def _fetch_metadata(
    targets: list[str],
    *,
    rpc_url: str | None,
    network: str | None = None,
    expected_chain_id: int | None = None,
    workers: int,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    w3, resolved_url = _connect(
        rpc_url,
        network=network,
        expected_chain_id=expected_chain_id,
    )
    rpc_urls = [resolved_url] if rpc_url else _chain_rpc_urls(network)
    pages = [
        targets[index : index + BATCH_CALL_SIZE]
        for index in range(0, len(targets), BATCH_CALL_SIZE)
    ]
    metadata: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    def _worker(index: int, addresses: list[str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
        session = requests.Session()
        session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "web3.py/7",
        })
        batch: list[dict[str, Any]] = []
        slot_by_address: dict[str, list[int]] = {}
        for address in addresses:
            checksum = Web3.to_checksum_address(address)
            slot_by_address[address] = []
            for selector in ("0x95d89b41", "0x06fdde03", "0x313ce567"):
                slot_by_address[address].append(len(batch))
                batch.append(
                    {
                        "jsonrpc": "2.0",
                        "id": len(batch),
                        "method": "eth_call",
                        "params": [
                            {"to": checksum, "data": selector},
                            "latest",
                        ],
                    }
                )
        result_by_id: dict[int, dict[str, Any]] = {}
        last_error = "metadata_unavailable"
        attempts = len(rpc_urls) * (HTTP_RETRY_COUNT + 1)
        for attempt in range(attempts):
            endpoint = rpc_urls[(index + attempt) % len(rpc_urls)]
            try:
                response = session.post(
                    endpoint,
                    json=batch,
                    timeout=60,
                    verify=False,
                )
                if response.status_code in (429, 500, 502, 503, 504):
                    last_error = f"{response.status_code} {endpoint}"
                    time.sleep(min(1.0 + attempt * 0.5, 5.0))
                    continue
                response.raise_for_status()
                raw = response.json()
                for response in (raw if isinstance(raw, list) else []):
                    if isinstance(response, dict) and response.get("id") is not None:
                        result_by_id[int(response["id"])] = response
                if len(result_by_id) >= len(batch):
                    break
                last_error = (
                    "empty-rpc-response"
                    if not result_by_id
                    else "incomplete-rpc-response"
                )
                time.sleep(min(1.0 + attempt * 0.5, 5.0))
            except Exception as exc:
                last_error = str(exc)[:240]
                time.sleep(min(0.5 + attempt * 0.4, 3.0))
        rows: dict[str, dict[str, Any]] = {}
        batch_errors: list[str] = []
        page_resolved = 0
        for address in addresses:
            symbol = ""
            name = ""
            decimals = None
            slots = slot_by_address[address]
            for field, slot in zip(("symbol", "name", "decimals"), slots):
                response = result_by_id.get(slot) or {}
                value = response.get("result")
                if not value or value in ("0x", "0X"):
                    continue
                try:
                    raw = bytes.fromhex(str(value)[2:])
                    if field == "decimals":
                        decimals = int(decode(["uint256"], raw)[0])
                    else:
                        text = str(decode(["string"], raw)[0]).strip()
                        if field == "symbol":
                            symbol = text
                        else:
                            name = text
                except Exception:
                    continue
            ok = bool(symbol) and decimals is not None
            rows[address] = {
                "address": address,
                "symbol": symbol,
                "name": name,
                "nameMissing": not name or is_unresolved_placeholder_symbol(name),
                "decimals": decimals,
                "ok": ok,
                "source": "chain_live_erc20",
                "rpcUrl": endpoint,
            }
            if not ok:
                batch_errors.append(
                    f"{address}:metadata_unavailable"
                )
            else:
                page_resolved += 1

        # RPC batch calls can silently drop or truncate a few slots.  Re-run
        # every address that still failed as a tiny single-address batch so a
        # transient slot failure is not mistaken for missing metadata.
        failed_addresses = [
            address for address, row in rows.items() if not row["ok"]
        ]
        for address in failed_addresses:
            checksum = Web3.to_checksum_address(address)
            single_batch: list[dict[str, Any]] = []
            single_slots: dict[str, int] = {}
            for selector in ("0x95d89b41", "0x06fdde03", "0x313ce567"):
                single_slots[selector] = len(single_batch)
                single_batch.append(
                    {
                        "jsonrpc": "2.0",
                        "id": len(single_batch),
                        "method": "eth_call",
                        "params": [
                            {"to": checksum, "data": selector},
                            "latest",
                        ],
                    }
                )
            single_result: dict[int, dict[str, Any]] = {}
            for attempt in range(len(rpc_urls) * (HTTP_RETRY_COUNT + 1)):
                endpoint = rpc_urls[(index + attempt) % len(rpc_urls)]
                try:
                    response = session.post(
                        endpoint,
                        json=single_batch,
                        timeout=60,
                        verify=False,
                    )
                    if response.status_code in (429, 500, 502, 503, 504):
                        time.sleep(min(1.0 + attempt * 0.5, 5.0))
                        continue
                    response.raise_for_status()
                    raw = response.json()
                    single_result = {}
                    for item in (raw if isinstance(raw, list) else []):
                        if isinstance(item, dict) and item.get("id") is not None:
                            single_result[int(item["id"])] = item
                    if len(single_result) == len(single_batch):
                        break
                except Exception:
                    time.sleep(min(0.5 + attempt * 0.4, 3.0))
            symbol = ""
            name = ""
            decimals = None
            for selector, slot in single_slots.items():
                response = single_result.get(slot) or {}
                value = response.get("result")
                if not value or value in ("0x", "0X"):
                    continue
                try:
                    raw = bytes.fromhex(str(value)[2:])
                    if selector == "0x313ce567":
                        decimals = int(decode(["uint256"], raw)[0])
                    else:
                        text = str(decode(["string"], raw)[0]).strip()
                        if selector == "0x95d89b41":
                            symbol = text
                        else:
                            name = text
                except Exception:
                    continue
            ok = bool(symbol) and decimals is not None
            if ok:
                rows[address] = {
                    "address": address,
                    "symbol": symbol,
                    "name": name,
                    "nameMissing": not name or is_unresolved_placeholder_symbol(name),
                    "decimals": decimals,
                    "ok": True,
                    "source": "chain_live_erc20",
                    "rpcUrl": endpoint,
                }
                batch_errors = [
                    error
                    for error in batch_errors
                    if not error.startswith(f"{address}:")
                ]
                page_resolved += 1
        if not page_resolved:
            return {}, [f"{address}:{last_error}" for address in addresses]
        return rows, batch_errors

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [
            executor.submit(_worker, index, page)
            for index, page in enumerate(pages)
        ]
        for future in as_completed(futures):
            page_rows, page_errors = future.result()
            metadata.update(page_rows)
            errors.extend(page_errors)
    return metadata, errors


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
    parser.add_argument("--include-exchange", action="store_true")
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
        "--addresses",
        default=None,
        help="Comma-separated addresses for --repair-source-json; replaces the two default residual addresses",
    )
    args = parser.parse_args()

    database = str(absolute_database_path(args.database))
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
