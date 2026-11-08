#!/usr/bin/env python3
"""Compare Robinhood V4 expected vs actual closed-loop outcomes.

Read-only analysis of the Robinhood testnet attempts from the local SQLite
store.  For every ``execution_success`` broadcast it decodes PoolManager Swap logs from the
stored receipt, matches each hop to the candidate pools recorded in the
attempt, and compares the on-chain realized borrow-token net delta with the
expected ``costModel.netProfitAmount``.  Rows without a decodable receipt are
kept with an explicit missing-reason; a zero is never used as a placeholder.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from web3 import Web3


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "flashloan" / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
from core.db.database_path import resolve_database_path

DEFAULT_STORE = resolve_database_path()
DEFAULT_OUTPUT_DIR = (
    REPO_ROOT
    / "flashloan"
    / "src"
    / "runtime"
    / "evidence"
    / "robinhood-testnet"
    / "expected_vs_actual"
)
SCHEMA_VERSION = "robinhood-testnet-expected-vs-actual-v2"
MAINNET_SCHEMA_VERSION = "robinhood-mainnet-expected-vs-actual-v2"
TESTNET_NETWORK = "robinhood-testnet"
TESTNET_CHAIN_ID = 46630
MAINNET_NETWORK = "robinhood"
MAINNET_CHAIN_ID = 4663
V4_SWAP_TOPIC = Web3.keccak(
    text="Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)"
).hex()


def infer_chain_scope(
    store_path: Path,
    network: str | None = None,
    chain_id: int | None = None,
) -> tuple[str, int]:
    """Return ``(network, chain_id)`` from explicit args or the store filename."""
    resolved_network = str(network or "").strip().lower()
    resolved_chain = chain_id
    store_text = str(store_path).lower().replace("\\", "/")
    store_looks_testnet = "46630" in store_text or "/robinhood-testnet/" in store_text
    store_looks_mainnet = (not store_looks_testnet) and (
        "4663" in store_text or "/robinhood/" in store_text
    )
    if not resolved_network:
        resolved_network = (
            MAINNET_NETWORK if store_looks_mainnet else TESTNET_NETWORK
        )
    if resolved_chain is None:
        resolved_chain = (
            MAINNET_CHAIN_ID if store_looks_mainnet else TESTNET_CHAIN_ID
        )
    if resolved_network == MAINNET_NETWORK:
        resolved_chain = MAINNET_CHAIN_ID
    if resolved_network == TESTNET_NETWORK:
        resolved_chain = TESTNET_CHAIN_ID
    return resolved_network, resolved_chain


def schema_version_for(network: str) -> str:
    if network == MAINNET_NETWORK:
        return MAINNET_SCHEMA_VERSION
    return SCHEMA_VERSION


def as_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not parsed.is_finite():
        return None
    return parsed


def parse_iso(value: str) -> datetime | None:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def hex_to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value), 16)
    except ValueError:
        return None


def nested(obj: Any, path: str) -> Any:
    current = obj
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def parse_swap_log(log: dict[str, Any]) -> dict[str, Any] | None:
    topics = log.get("topics") or []
    if not topics or str(topics[0]).lower() != V4_SWAP_TOPIC:
        return None
    if len(topics) < 2:
        return None
    data = str(log.get("data") or "")
    if not data.startswith("0x"):
        return None
    try:
        amount0, amount1, _sqrt, _liquidity, _tick, fee = Web3().codec.decode(
            ["int128", "int128", "uint160", "uint128", "int24", "uint24"],
            bytes.fromhex(data[2:]),
        )
    except Exception:
        return None
    return {
        "poolId": str(topics[1]).lower(),
        "amount0": str(amount0),
        "amount1": str(amount1),
        "fee": int(fee),
    }


def candidate_pool_by_pool_id(
    row: dict[str, Any],
    authoritative: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for candidate in (row.get("quote") or {}).get("candidate_pools") or []:
        pool_id = str(candidate.get("poolId") or "").lower()
        if pool_id:
            result[pool_id] = candidate
    # The pool-manager-owned table is authoritative. The attempt snapshot is
    # retained only for historical pool metadata that has since been pruned.
    for pool_id, candidate in (authoritative or {}).items():
        normalized = str(pool_id or "").lower()
        if normalized:
            result[normalized] = candidate
    return result


def _candidate_pool_key(candidate: dict[str, Any]) -> dict[str, Any]:
    pool_key = candidate.get("poolKey")
    return pool_key if isinstance(pool_key, dict) else {}


def candidate_currency(candidate: dict[str, Any], key: str) -> str:
    """Return a currency address from the candidate or its nested poolKey."""
    value = candidate.get(key)
    if value is None:
        value = _candidate_pool_key(candidate).get(key)
    return str(value or "").lower()


def candidate_token_symbol(candidate: dict[str, Any], index: int) -> str:
    key = f"token{index}Symbol"
    for container in (candidate, _candidate_pool_key(candidate)):
        value = container.get(key)
        if str(value or "").strip():
            return str(value)
    return ""


def candidate_currency_decimals(candidate: dict[str, Any], currency_key: str) -> int | None:
    index = "1" if currency_key == "currency1" else "0"
    for container in (candidate, _candidate_pool_key(candidate)):
        meta = container.get(f"{currency_key}Metadata")
        if isinstance(meta, dict) and meta.get("decimals") is not None:
            try:
                return int(meta["decimals"])
            except (TypeError, ValueError):
                pass
        value = container.get(f"token{index}Decimals")
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
    return None


def load_candidate_pools(
    store_path: Path,
    *,
    network: str,
    chain_id: int,
) -> dict[str, dict[str, Any]]:
    """Load the current authoritative PoolKey metadata for receipt decoding."""
    if not store_path.exists():
        return {}
    try:
        connection = sqlite3.connect(f"file:{store_path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT pool_id, pool_key_json, pool_manager, token0, token1,
                       fee
                FROM dex_candidate_pools
                WHERE network = ? AND chain_id = ? AND protocol = 'uniswap_v4'
                """,
                (network, chain_id),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            pool_key = json.loads(row["pool_key_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(pool_key, dict):
            continue
        pool_id = str(row["pool_id"] or pool_key.get("poolId") or "").strip().lower()
        if not pool_id:
            continue
        token0 = str(
            row["token0"] or pool_key.get("token0") or pool_key.get("currency0") or ""
        ).strip().lower()
        token1 = str(
            row["token1"] or pool_key.get("token1") or pool_key.get("currency1") or ""
        ).strip().lower()
        if not token0 or not token1:
            continue
        candidate = dict(pool_key)
        candidate.update(
            {
                "poolId": pool_id,
                "poolManager": row["pool_manager"] or pool_key.get("poolManager"),
                "currency0": token0,
                "currency1": token1,
                "token0": token0,
                "token1": token1,
                "fee": int(row["fee"] if row["fee"] is not None else pool_key.get("fee") or 0),
            }
        )
        result[pool_id] = candidate
    return result


def hop_symbols(
    candidate: dict[str, Any],
    route_addresses: list[str],
    route_symbols: list[str],
    input_currency: str,
) -> tuple[str | None, str | None]:
    """Return input/output symbols for a candidate pool given route addresses."""
    route_lower = [str(address or "").lower() for address in route_addresses]
    symbol_lower = [str(value or "") for value in route_symbols]
    currency0 = candidate_currency(candidate, "currency0")
    currency1 = candidate_currency(candidate, "currency1")
    symbol0 = candidate_token_symbol(candidate, 0)
    symbol1 = candidate_token_symbol(candidate, 1)
    if not input_currency:
        return None, None
    if input_currency == currency0:
        return symbol0 or None, symbol1 or None
    if input_currency == currency1:
        return symbol1 or None, symbol0 or None
    if input_currency in route_lower:
        in_index = route_lower.index(input_currency)
        out_index = in_index + 1 if in_index + 1 < len(route_lower) else 0
        if in_index < len(symbol_lower) and out_index < len(symbol_lower):
            return symbol_lower[in_index] or None, symbol_lower[out_index] or None
    return None, None


def actual_swap_amounts(
    swap: dict[str, Any],
    candidate: dict[str, Any],
    route_addresses: list[str],
) -> dict[str, Any]:
    """Derive actual input/output raw for a decoded swap log."""
    amount0 = int(swap["amount0"])
    amount1 = int(swap["amount1"])
    currency0 = candidate_currency(candidate, "currency0")
    currency1 = candidate_currency(candidate, "currency1")
    route_lower = [str(address or "").lower() for address in route_addresses]
    if not currency0 or not currency1:
        return {"decodable": False, "reason": "candidate_pool_missing_currency"}
    # amount0/amount1 are signed deltas: input is negative, output positive.
    # The input currency is the one whose delta is negative.
    if amount0 < 0 and amount1 > 0:
        input_currency = currency0
        output_currency = currency1
        input_raw = -amount0
        output_raw = amount1
    elif amount1 < 0 and amount0 > 0:
        input_currency = currency1
        output_currency = currency0
        input_raw = -amount1
        output_raw = amount0
    else:
        return {
            "decodable": False,
            "reason": "swap_sign_mismatch",
            "amount0": swap["amount0"],
            "amount1": swap["amount1"],
        }
    # Validate against route adjacency when both currencies are present.
    if input_currency in route_lower:
        in_index = route_lower.index(input_currency)
        out_index = in_index + 1 if in_index + 1 < len(route_lower) else 0
        expected_out = route_lower[out_index]
        if expected_out != output_currency:
            return {
                "decodable": False,
                "reason": "swap_not_on_route_adjacency",
                "amount0": swap["amount0"],
                "amount1": swap["amount1"],
            }
    return {
        "decodable": True,
        "inputRaw": str(input_raw),
        "outputRaw": str(output_raw),
        "inputCurrency": input_currency,
        "outputCurrency": output_currency,
    }


def decimals_for_addresses(
    row: dict[str, Any],
) -> dict[str, int]:
    result: dict[str, int] = {}
    for candidate in (row.get("quote") or {}).get("candidate_pools") or []:
        for key in ("currency0", "currency1"):
            address = candidate_currency(candidate, key)
            if not address:
                continue
            decimals = candidate_currency_decimals(candidate, key)
            if decimals is not None:
                result[address] = decimals
    return result


def load_chain_token_decimals(
    store_path: Path,
    *,
    network: str = TESTNET_NETWORK,
    chain_id: int = TESTNET_CHAIN_ID,
) -> dict[str, int]:
    """Read ERC20 decimals from the local SQLite store (read-only)."""
    result: dict[str, int] = {}
    if store_path is None or not store_path.exists():
        return result
    try:
        import sqlite3

        connection = sqlite3.connect(str(store_path))
        try:
            rows = connection.execute(
                """
                SELECT token_address, token_decimals
                FROM chain_tokens
                WHERE network = ? AND chain_id = ?
                """,
                (network, chain_id),
            ).fetchall()
        finally:
            connection.close()
    except Exception:
        return result
    for address, value in rows:
        key = str(address or "").lower()
        if key.startswith("0x") and len(key) == 42:
            try:
                parsed = None if value is None else int(value)
            except (TypeError, ValueError):
                parsed = None
            if parsed is not None:
                result[key] = parsed
    return result


def human_amount(raw: int | None, decimals: int | None) -> str | None:
    if raw is None or decimals is None:
        return None
    try:
        return str(Decimal(raw) / (Decimal(10) ** decimals))
    except Exception:
        return None


def row_actual(
    row: dict[str, Any],
    *,
    chain_decimals: dict[str, int] | None = None,
    authoritative_candidate_pools: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    tx_hash = str(row.get("tx_hash") or "").strip()
    route_addresses = [
        str(address or "").lower()
        for address in (
            nested(row, "quote.live_onchain_evidence.routeAddresses")
            or row.get("route_addresses")
            or []
        )
        if str(address or "").startswith("0x")
    ]
    route_symbols = (
        nested(row, "quote.live_onchain_evidence.routeSymbols")
        or row.get("route_symbols")
        or []
    )
    receipt = nested(row, "auto_execute.result.receipt.receipt") or {}
    logs = receipt.get("logs") or []
    swaps = []
    for log in logs:
        parsed = parse_swap_log(log)
        if parsed is not None:
            swaps.append(parsed)
    candidates = candidate_pool_by_pool_id(row, authoritative_candidate_pools)
    decimals = decimals_for_addresses(row)
    decimals.update(chain_decimals or {})
    if not swaps:
        return {
            "txHash": tx_hash,
            "pair": row.get("pair"),
            "routeSymbols": route_symbols,
            "decodable": False,
            "missingReason": "receipt_has_no_pool_manager_swap_logs",
            "profitStatus": "unavailable",
            "actualNetProfit": None,
        }

    decoded_swaps = []
    unmatched_swaps = []
    for swap in swaps:
        candidate = candidates.get(swap["poolId"])
        if candidate is None:
            unmatched_swaps.append(
                {
                    "poolId": swap["poolId"],
                    "decodable": False,
                    "reason": "pool_not_in_candidate_pools",
                    "amount0": swap["amount0"],
                    "amount1": swap["amount1"],
                }
            )
            continue
        parsed = actual_swap_amounts(swap, candidate, route_addresses)
        input_symbol, output_symbol = hop_symbols(
            candidate,
            route_addresses,
            route_symbols,
            parsed.get("inputCurrency") or "",
        )
        if parsed.get("decodable"):
            input_raw = int(parsed["inputRaw"])
            output_raw = int(parsed["outputRaw"])
            decoded_swaps.append(
                {
                    "poolId": swap["poolId"],
                    "decodable": True,
                    "inputCurrency": parsed["inputCurrency"],
                    "outputCurrency": parsed["outputCurrency"],
                    "inputSymbol": input_symbol,
                    "outputSymbol": output_symbol,
                    "inputRaw": parsed["inputRaw"],
                    "outputRaw": parsed["outputRaw"],
                    "inputHuman": human_amount(input_raw, decimals.get(parsed["inputCurrency"])),
                    "outputHuman": human_amount(output_raw, decimals.get(parsed["outputCurrency"])),
                }
            )
        else:
            unmatched_swaps.append(
                {
                    "poolId": swap["poolId"],
                    "decodable": False,
                    "reason": parsed.get("reason"),
                    "amount0": swap["amount0"],
                    "amount1": swap["amount1"],
                }
            )

    hop_results = []
    used_indexes: set[int] = set()
    for hop_index, (from_address, to_address) in enumerate(
        zip(route_addresses, route_addresses[1:])
    ):
        matched_index = next(
            (
                index
                for index, hop in enumerate(decoded_swaps)
                if index not in used_indexes
                and hop.get("inputCurrency") == from_address
                and hop.get("outputCurrency") == to_address
            ),
            None,
        )
        if matched_index is None:
            hop_results.append(
                {
                    "hop": hop_index + 1,
                    "inputCurrency": from_address,
                    "outputCurrency": to_address,
                    "decodable": False,
                    "reason": "route_hop_swap_not_decodable",
                }
            )
            continue
        used_indexes.add(matched_index)
        hop_results.append({"hop": hop_index + 1, **decoded_swaps[matched_index]})

    borrow_address = str(route_addresses[0]).lower() if route_addresses else ""
    borrow_symbol = str(route_symbols[0] or "") if route_symbols else ""
    borrow_decimals = decimals.get(borrow_address)
    full_route_decodable = (
        bool(route_addresses)
        and len(hop_results) == len(route_addresses) - 1
        and all(hop.get("decodable") for hop in hop_results)
    )
    actual_net_raw = None
    actual_net_human = None
    if full_route_decodable:
        actual_in = int(hop_results[0]["inputRaw"])
        actual_out = int(hop_results[-1]["outputRaw"])
        actual_net_raw = actual_out - actual_in
        actual_net_human = human_amount(actual_net_raw, borrow_decimals)
    cost_model = nested(row, "quote.live_onchain_evidence.costModel") or {}
    expected = as_decimal(cost_model.get("netProfitAmount"))
    expected_human = str(expected) if expected is not None else None
    diff = None
    diff_reasons = []
    if expected is not None and actual_net_human is not None:
        actual_dec = as_decimal(actual_net_human)
        if actual_dec is not None:
            diff = actual_dec - expected
    if not full_route_decodable:
        diff_reasons.append("full_route_undecodable")
    if actual_net_human is None:
        diff_reasons.append("actual_net_profit_missing")
    if expected_human is None:
        diff_reasons.append("expected_net_profit_missing")
    if not diff_reasons and diff is not None and diff != 0:
        diff_reasons.append("realized_slippage_or_fee_vs_preview")

    gas_used = hex_to_int(receipt.get("gasUsed"))
    gas_price = hex_to_int(receipt.get("effectiveGasPrice"))
    gas_cost_native = None
    if gas_used is not None and gas_price is not None:
        gas_cost_native = str(
            Decimal(gas_used) * Decimal(gas_price) / (Decimal(10) ** 18)
        )
    actual_after_gas = None
    actual_after_gas_reason = None
    if actual_net_human is not None:
        if str(cost_model.get("netProfitAmountSymbol") or borrow_symbol).upper() in {
            "ETH",
            "WETH",
        }:
            gas_cost_decimal = as_decimal(gas_cost_native)
            actual_decimal = as_decimal(actual_net_human)
            if gas_cost_decimal is not None and actual_decimal is not None:
                actual_after_gas = str(actual_decimal - gas_cost_decimal)
        else:
            actual_after_gas_reason = "gas_is_native_token_but_profit_basis_is_not_eth"
    actual_decimal = as_decimal(actual_net_human)
    profit_status = (
        "unavailable"
        if actual_decimal is None
        else "realized_positive_before_gas"
        if actual_decimal > 0
        else "realized_negative_before_gas"
        if actual_decimal < 0
        else "realized_zero_before_gas"
    )

    return {
        "txHash": tx_hash,
        "pair": row.get("pair"),
        "routeSymbols": route_symbols,
        "decodable": full_route_decodable,
        "expectedNetProfit": expected_human,
        "expectedProfitSymbol": cost_model.get("netProfitAmountSymbol") or borrow_symbol,
        "actualNetProfit": actual_net_human,
        "actualNetProfitRaw": str(actual_net_raw) if actual_net_raw is not None else None,
        "actualNetProfitBasis": "swaps_before_gas",
        "actualNetProfitAfterGas": actual_after_gas,
        "actualNetProfitAfterGasUnavailableReason": actual_after_gas_reason,
        "actualProfitSymbol": borrow_symbol,
        "profitStatus": profit_status,
        "gasCostNative": gas_cost_native,
        "gasCostNativeSymbol": "ETH",
        "netProfitDifference": str(diff) if diff is not None else None,
        "differenceReasons": diff_reasons,
        "hops": hop_results,
        "unmatchedSwaps": unmatched_swaps,
        "receipt": {
            "txHash": receipt.get("hash") or receipt.get("transactionHash") or tx_hash,
            "blockNumber": receipt.get("blockNumber"),
            "blockNumberInt": hex_to_int(receipt.get("blockNumber")),
            "gasUsed": receipt.get("gasUsed"),
            "gasUsedInt": gas_used,
            "effectiveGasPrice": receipt.get("effectiveGasPrice"),
            "effectiveGasPriceInt": gas_price,
            "status": receipt.get("status"),
        },
    }


def read_rows(
    path: Path,
    start: datetime | None = None,
    end: datetime | None = None,
    *,
    network: str = TESTNET_NETWORK,
    chain_id: int = TESTNET_CHAIN_ID,
) -> list[dict[str, Any]]:
    if path.suffix.lower() in {".sqlite3", ".sqlite", ".db"}:
        return _read_rows_sqlite(
            path,
            start=start,
            end=end,
            network=network,
            chain_id=chain_id,
        )
    return _read_rows_jsonl(path, start=start, end=end)


def _read_rows_jsonl(
    path: Path,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(row, dict)
                and row.get("state") == "execution_success"
                and row.get("tx_hash")
            ):
                stamp = str(row.get("observed_at") or row.get("created_at") or "")
                observed = parse_iso(stamp)
                if observed is None:
                    continue
                if start is not None and observed < start:
                    continue
                if end is not None and observed > end:
                    continue
                rows.append(row)
    return rows


def _read_rows_sqlite(
    path: Path,
    start: datetime | None = None,
    end: datetime | None = None,
    *,
    network: str = TESTNET_NETWORK,
    chain_id: int = TESTNET_CHAIN_ID,
) -> list[dict[str, Any]]:
    if str(SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(SRC_ROOT))
    from core.db.storage_cow_execution import load_recent_cow_execution_attempts

    rows = load_recent_cow_execution_attempts(
        str(path),
        limit=1_000_000,
        networks=[network],
        chain_ids=[chain_id],
        category="execution_success",
        start_at=start.isoformat() if start is not None else None,
        end_at=end.isoformat() if end is not None else None,
        retention_days=3650,
    )
    return [
        row for row in rows
        if str(row.get("state") or "").strip().lower() == "execution_success"
        and str(row.get("tx_hash") or (row.get("quote") or {}).get("tx_hash") or "").strip()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--store",
        default=str(DEFAULT_STORE),
        help="SQLite store path for attempts.",
    )
    parser.add_argument(
        "--network",
        default=None,
        help="robinhood or robinhood-testnet; auto-derived from the store filename when omitted.",
    )
    parser.add_argument(
        "--chain-id",
        type=int,
        default=None,
        help="4663 or 46630; auto-derived from the store filename when omitted.",
    )
    parser.add_argument(
        "--no-chain-token-decimals",
        action="store_true",
        help="Do not load ERC20 decimals from the SQLite chain_tokens table.",
    )
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--window-start", default=None, help="ISO-8601 UTC start")
    parser.add_argument("--window-end", default=None, help="ISO-8601 UTC end")
    args = parser.parse_args()

    start = parse_iso(args.window_start) if args.window_start else None
    end = parse_iso(args.window_end) if args.window_end else None
    if start is not None and end is not None and start > end:
        parser.error("--window-start must not be after --window-end")
    store_path = Path(args.store)
    network, chain_id = infer_chain_scope(store_path, args.network, args.chain_id)
    rows = read_rows(store_path, start=start, end=end, network=network, chain_id=chain_id)
    chain_decimals: dict[str, int] = {}
    if (
        not args.no_chain_token_decimals
        and store_path.suffix.lower() in {".sqlite3", ".sqlite", ".db"}
    ):
        chain_decimals = load_chain_token_decimals(
            store_path,
            network=network,
            chain_id=chain_id,
        )
    candidate_pools = (
        load_candidate_pools(store_path, network=network, chain_id=chain_id)
        if store_path.suffix.lower() in {".sqlite3", ".sqlite", ".db"}
        else {}
    )
    actual_rows = [
        row_actual(
            row,
            chain_decimals=chain_decimals,
            authoritative_candidate_pools=candidate_pools,
        )
        for row in rows
    ]

    def item_score(item: dict[str, Any]) -> tuple[int, int, int]:
        hops = item.get("hops") or []
        all_on_route = all(
            hop.get("decodable")
            and hop.get("reason") != "swap_not_on_route_adjacency"
            for hop in hops
        )
        return (
            1 if item.get("decodable") else 0,
            1 if all_on_route else 0,
            1 if item.get("expectedNetProfit") is not None else 0,
        )

    unique: dict[str, dict[str, Any]] = {}
    for item in actual_rows:
        existing = unique.get(item["txHash"])
        if existing is None or item_score(item) > item_score(existing):
            unique[item["txHash"]] = item
    items = sorted(unique.values(), key=lambda item: str(item.get("pair") or ""))
    decodable = [item for item in items if item.get("decodable")]
    undecodable = [item for item in items if not item.get("decodable")]
    diff_summary = {
        "decodableCount": len(decodable),
        "undecodableCount": len(undecodable),
        "actualPositiveBeforeGasCount": sum(
            1 for item in decodable if item.get("profitStatus") == "realized_positive_before_gas"
        ),
        "actualNegativeBeforeGasCount": sum(
            1 for item in decodable if item.get("profitStatus") == "realized_negative_before_gas"
        ),
        "actualZeroBeforeGasCount": sum(
            1 for item in decodable if item.get("profitStatus") == "realized_zero_before_gas"
        ),
        "zeroDifferenceCount": sum(
            1
            for item in decodable
            if item.get("netProfitDifference") is not None
            and as_decimal(item["netProfitDifference"]) == 0
        ),
        "nonZeroDifferenceCount": sum(
            1
            for item in decodable
            if item.get("netProfitDifference") is not None
            and as_decimal(item["netProfitDifference"]) != 0
        ),
        "negativeRealizedCount": sum(
            1
            for item in decodable
            if item.get("actualNetProfit") is not None
            and as_decimal(item["actualNetProfit"]) is not None
            and as_decimal(item["actualNetProfit"]) < 0
        ),
    }
    report = {
        "schemaVersion": schema_version_for(network),
        "network": network,
        "chainId": chain_id,
        "chainTokenDecimalsCount": len(chain_decimals),
        "authoritativeCandidatePoolCount": len(candidate_pools),
        "candidatePoolSource": "dex_candidate_pools",
        "actualProfitBasis": "swaps_before_gas",
        "source": str(Path(args.store)),
        "range": {
            "startUtc": args.window_start,
            "endUtc": args.window_end,
        },
        "uniqueSuccessCount": len(items),
        "summary": diff_summary,
        "items": items,
        "conclusion": (
            f"{len(decodable)} unique success receipts decodable, "
            f"{len(undecodable)} missing receipt/swap evidence; "
            f"{diff_summary['actualPositiveBeforeGasCount']} positive before gas, "
            f"{diff_summary['actualNegativeBeforeGasCount']} negative before gas; "
            f"{diff_summary['nonZeroDifferenceCount']} show expected-vs-actual "
            f"net profit difference."
        ),
    }
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        network_dir = (
            "robinhood" if network == MAINNET_NETWORK else "robinhood-testnet"
        )
        output_dir = (
            REPO_ROOT
            / "flashloan"
            / "src"
            / "runtime"
            / "evidence"
            / network_dir
            / "expected_vs_actual"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "latest.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"evidence={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
