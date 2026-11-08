#!/usr/bin/env python3
"""Analyze Robinhood testnet closed-loop attempt P&L from runtime evidence.

The script is read-only for the online SQLite store. It deduplicates
successful broadcasts by unique ``txHash``, keeps the most concrete signed
borrow-token profit value per record, and reports failed or blocked closed
loops with their expected profit/loss when a numeric value exists.  Rows
without a signed value stay ``null`` and are never replaced by a zero.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "flashloan" / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
from core.db.database_path import resolve_database_path

DEFAULT_STORE = resolve_database_path()
DEFAULT_ENV = REPO_ROOT / "flashloan" / "app_runtime" / ".env"
DEFAULT_USD_PRICE_JSON = (
    REPO_ROOT / "flashloan" / "src" / "runtime" / "state" / "binance_market_snapshot.json"
)
DEFAULT_USD_MAX_AGE_SECONDS = 6 * 3600
DEFAULT_USD_MAX_REFERENCE_LAG_SECONDS = 24 * 3600
SCHEMA_VERSION = "robinhood-testnet-closed-loop-pnl-v1"
MAINNET_SCHEMA_VERSION = "robinhood-mainnet-closed-loop-pnl-v1"
TESTNET_NETWORK = "robinhood-testnet"
TESTNET_CHAIN_ID = 46630
MAINNET_NETWORK = "robinhood"
MAINNET_CHAIN_ID = 4663
FIELD_KEYS = (
    "netProductDeltaRaw",
    "ArbitrageNotProfitable",
    "netProfitAmountMissingReason",
)


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

PNL_PATHS = (
    "quote.live_onchain_evidence.costModel.netProfitAmount",
    "quote_analysis.referenceNetProfitAmount",
    "precheck.reference_token_profit_amount",
    "reference_token_delta_amount",
    "final_delta_amount",
    "live_token_profit_amount",
    "auto_execute.result.referenceNetProfitAmount",
)

SYMBOL_PATHS = (
    "final_symbol",
    "live_token_profit_symbol",
    "precheck.live_token_profit_symbol",
    "quote.live_onchain_evidence.costModel.netProfitAmountSymbol",
    "quote_analysis.referenceNetProfitAmountSymbol",
    "auto_execute.result.referenceNetProfitSymbol",
    "auto_execute.result.amountSymbol",
)


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


def local_iso(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parse_iso(str(value))
    if parsed is None:
        return None
    return parsed.astimezone().isoformat(timespec="seconds")


def nested(obj: Any, path: str) -> Any:
    current = obj
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


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


def load_binance_usd_prices(path: Path) -> tuple[dict[str, Decimal], dict[str, Any]]:
    """Read current USDT prices from the local Binance market snapshot.

    The snapshot is evidence already produced by the market daemon; this
    function never calls a remote price API.  A missing or unreadable file is
    reported as ``available=false`` so the report stays fail-closed.
    """
    meta: dict[str, Any] = {"available": False}
    prices: dict[str, Decimal] = {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        meta["unavailableReason"] = f"snapshot_unreadable: {type(exc).__name__}"
        return prices, meta
    observed_at = data.get("observed_at")
    meta["observedAt"] = observed_at
    meta["priceSource"] = data.get("price_source")
    for group in ("top", "bottom", "basket"):
        for item in data.get(group) or []:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").upper().strip()
            price = as_decimal(item.get("current_price"))
            if symbol and price is not None:
                prices[symbol] = price
    meta["priceCount"] = len(prices)
    meta["available"] = len(prices) > 0 and bool(observed_at)
    if not meta["available"]:
        meta["unavailableReason"] = "snapshot_empty_or_missing_observed_at"
    return prices, meta


def resolve_usd_price(symbol: str | None, prices: dict[str, Decimal]) -> tuple[Decimal | None, str | None]:
    key = str(symbol or "").upper().strip()
    if not key:
        return None, None
    for candidate in (f"{key}BUSDT", f"{key}USDT"):
        price = prices.get(candidate)
        if price is not None:
            return price, candidate
    return None, None


def usd_price_quote(
    symbol: str | None,
    prices: dict[str, Decimal],
    snapshot_observed_at: str | None,
    row_observed_at: str | None,
    *,
    now_utc: datetime,
    max_age_seconds: int,
    max_reference_lag_seconds: int,
) -> dict[str, Any]:
    price, snapshot_symbol = resolve_usd_price(symbol, prices)
    if price is None:
        return {
            "symbol": str(symbol or ""),
            "priceUsd": None,
            "snapshotSymbol": None,
            "fresh": False,
            "unavailableReason": "no_usd_price_for_symbol",
        }
    observed = parse_iso(str(snapshot_observed_at or "")) if snapshot_observed_at else None
    row_observed = parse_iso(str(row_observed_at or "")) if row_observed_at else None
    quote: dict[str, Any] = {
        "symbol": str(symbol or ""),
        "priceUsd": str(price),
        "snapshotSymbol": snapshot_symbol,
        "snapshotObservedAt": snapshot_observed_at,
        "fresh": False,
    }
    if observed is None:
        quote["unavailableReason"] = "snapshot_observed_at_missing"
        return quote
    snapshot_age = (now_utc - observed).total_seconds()
    if snapshot_age > max_age_seconds:
        quote["snapshotAgeSeconds"] = int(snapshot_age)
        quote["unavailableReason"] = "usd_snapshot_too_old"
        return quote
    if row_observed is not None:
        reference_lag = abs((row_observed - observed).total_seconds())
        quote["referenceLagSeconds"] = int(reference_lag)
        if reference_lag > max_reference_lag_seconds:
            quote["unavailableReason"] = "usd_reference_lag_exceeds_limit"
            return quote
    quote["fresh"] = True
    return quote


def env_broadcast_flags(path: Path) -> dict[str, bool | None]:
    result = {
        "ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST": None,
        "ROBINHOOD_MAINNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST": None,
    }
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in result:
            result[key] = value.strip().lower() in {"1", "true", "yes", "on"}
    return result


def load_chain_token_decimals(
    store_path: Path,
    *,
    network: str = "robinhood-testnet",
    chain_id: int = 46630,
) -> tuple[dict[str, int | None], dict[str, Any]]:
    """Read on-chain ERC20 decimals from the local SQLite store.

    The store is written by the runtime/metadata tools and is used here
    read-only to turn a decimals placeholder into evidence-backed metadata.
    """
    meta: dict[str, Any] = {"available": False}
    if store_path is None or not store_path.exists():
        meta["unavailableReason"] = "store_missing"
        return {}, meta
    try:
        import sqlite3

        connection = sqlite3.connect(str(store_path))
        try:
            rows = connection.execute(
                """
                SELECT token_address, token_decimals, token_metadata_json
                FROM chain_tokens
                WHERE network = ? AND chain_id = ?
                """,
                (network, chain_id),
            ).fetchall()
        finally:
            connection.close()
    except Exception as exc:
        meta["unavailableReason"] = f"store_unreadable: {type(exc).__name__}"
        return {}, meta
    decimals: dict[str, int | None] = {}
    trusted_count = 0
    for address, value, metadata_json in rows:
        key = str(address or "").lower()
        if not key.startswith("0x") or len(key) != 42:
            continue
        try:
            parsed = None if value is None else int(value)
        except (TypeError, ValueError):
            parsed = None
        decimals[key] = parsed
        if parsed is not None:
            trusted_count += 1
    meta["available"] = len(decimals) > 0
    meta["rowCount"] = len(decimals)
    meta["trustedDecimalsCount"] = trusted_count
    return decimals, meta


def route_token_addresses(row: dict[str, Any]) -> list[str]:
    result = nested(row, "auto_execute.result.routeAddresses")
    if not result:
        result = nested(row, "quote.live_onchain_evidence.routeAddresses")
    return [
        str(address or "").lower()
        for address in (result or [])
        if str(address or "").startswith("0x") and len(str(address)) == 42
    ]


def row_symbol(row: dict[str, Any], pair: str) -> str | None:
    for path in SYMBOL_PATHS:
        value = nested(row, path)
        text = str(value or "").strip()
        if text and text.lower() not in {"none", "null", "unknown"}:
            return text
    first = str(pair or "").split("->", 1)[0].strip()
    return first or None


def missing_profit_evidence(row: dict[str, Any]) -> dict[str, Any]:
    """Classify why an attempt carries no signed borrow-token profit value.

    A missing value is not automatically "confirmed unprofitable".  The
    classification separates contract-confirmed no-profit routes from quote
    failures that simply did not produce a numeric result.
    """
    blocker_parts = [
        str(item)
        for item in (row.get("blocked_reasons") or [])
        if str(item).strip()
    ]
    evidence_blockers = nested(row, "quote.live_onchain_evidence.blockers")
    if isinstance(evidence_blockers, list):
        blocker_parts.extend(str(item) for item in evidence_blockers if str(item).strip())
    blocker_text = " | ".join(blocker_parts)
    native_reason = nested(
        row,
        "quote.live_onchain_evidence.costModel.netProfitAmountMissingReason",
    )
    native_reason_text = str(native_reason or "").strip()
    lowered_blocker = blocker_text.lower()
    if "NoProfitableRoute" in blocker_text:
        category = "no_profitable_route"
        reliability = "confirmed_no_profit"
        summary = "chain_contract_confirms_no_profitable_route"
    elif (
        "rpc request failed" in lowered_blocker
        or "error sending request" in lowered_blocker
        or "rpc_verification_failed" in lowered_blocker
        or "rpc_verification_timeout" in lowered_blocker
        or "rpc error" in lowered_blocker
        or "rpc timeout" in lowered_blocker
        or "connection refused" in lowered_blocker
        or "connection timeout" in lowered_blocker
        or "endpoint unreachable" in lowered_blocker
    ):
        category = "rpc_transport_error"
        reliability = "quote_failed"
        summary = "rpc_transport_error_without_signed_value"
    elif (
        "not_enough_liquidity" in lowered_blocker
        or "notenoughliquidity" in lowered_blocker
        or "not enough liquidity" in lowered_blocker
        or "insufficient liquidity" in lowered_blocker
        or "liquidity is zero" in lowered_blocker
        or "received" in lowered_blocker
    ):
        category = "quoter_insufficient_liquidity"
        reliability = "quote_failed"
        summary = "live_quote_insufficient_liquidity_without_signed_value"
    elif "hop_1_live_quote_failed" in blocker_text or (
        "hop 1" in lowered_blocker and "execution reverted" in lowered_blocker
    ):
        category = "quoter_revert"
        reliability = "quote_failed"
        summary = "first_hop_live_quote_reverted"
    elif "live_quote_failed" in blocker_text and "execution reverted" in blocker_text:
        category = "quoter_revert"
        reliability = "quote_failed"
        summary = "first_hop_live_quote_reverted"
    elif "previewBestPool:eth_call: execution reverted" in blocker_text:
        category = "quoter_revert"
        reliability = "quote_failed"
        summary = "preview_best_pool_reverted_without_numeric_result"
    elif "previewBestPoolDetailed:eth_call: execution reverted" in blocker_text:
        category = "quoter_revert"
        reliability = "quote_failed"
        summary = "preview_best_pool_detailed_reverted_without_numeric_result"
    elif "previewBestPool unavailable" in blocker_text or (
        "previewBestPoolDetailed unavailable" in blocker_text
    ):
        category = "quoter_unavailable"
        reliability = "quote_failed"
        summary = "preview_unavailable_without_numeric_result"
    elif native_reason_text:
        category = "native_missing_signed_value"
        reliability = "not_verified"
        summary = "native_ladder_returned_without_signed_value"
    else:
        category = "manual_review_not_profitable"
        reliability = "not_verified"
        summary = "manual_review_without_signed_value"
    return {
        "code": category,
        "reliability": reliability,
        "summary": summary,
        "nativeReason": native_reason_text or None,
        "blockerText": blocker_text[:500] or None,
    }


def row_pnl(row: dict[str, Any], pair: str) -> dict[str, Any]:
    numeric = None
    source_path = None
    for path in PNL_PATHS:
        parsed = as_decimal(nested(row, path))
        if parsed is not None:
            numeric = parsed
            source_path = path
            break
    return {
        "expectedSignedProfit": None if numeric is None else str(numeric),
        "expectedProfitSymbol": row_symbol(row, pair),
        "missingReason": (
            None
            if numeric is not None
            else "attempt record has no signed borrow-token profit value; zero is not used as a placeholder"
        ),
        "missingEvidence": (
            None if numeric is not None else missing_profit_evidence(row)
        ),
        "pnlSourcePath": source_path,
        "reviewConsistent": _row_review_consistent(row),
    }


def _row_review_consistent(row: dict[str, Any]) -> bool:
    """Return whether one attempt binds positive cost-model profit to a live review.

    The positive estimate is only treated as executable when the same evidence
    also carries a live-verified review, an open transaction preflight, no
    blockers, and positive cost-model net profit.
    """
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    evidence = quote.get("live_onchain_evidence")
    if not isinstance(evidence, dict) or not evidence:
        analysis = quote.get("quote_analysis") if isinstance(quote.get("quote_analysis"), dict) else {}
        evidence = analysis.get("live_onchain_evidence")
    if not isinstance(evidence, dict) or not evidence:
        return False
    if str(evidence.get("status") or "").strip().lower() != "live_verified":
        return False
    if not bool(evidence.get("readyForTransactionPreflight")):
        return False
    blockers = [str(item) for item in evidence.get("blockers") or [] if str(item).strip()]
    if blockers:
        return False
    costs = evidence.get("costModel") if isinstance(evidence.get("costModel"), dict) else {}
    try:
        profit = float(
            costs.get("netProfitAmount")
            or evidence.get("referenceNetProfitAmount")
            or evidence.get("profitableAmount")
            or evidence.get("profitHuman")
            or evidence.get("profit")
            or 0
        )
    except (TypeError, ValueError):
        return False
    return profit > 0


def closed_loop_group_key(symbols: list[str]) -> str | None:
    """Return the canonical A|B|C group key for a closed-loop route."""
    normalized = [str(item or "").strip().upper() for item in symbols]
    if len(normalized) != 4 or normalized[0] != normalized[-1]:
        return None
    anchor = normalized[0]
    middle = sorted([normalized[1], normalized[2]])
    return "|".join([anchor, middle[0], middle[1]])


def row_closed_loop_group(row: dict[str, Any]) -> tuple[str | None, list[str] | None]:
    """Return ``(group_key, direction_symbols)`` for one attempt row."""
    symbols = [
        str(item).strip().upper()
        for item in (row.get("route_path") or [])
        if str(item or "").strip()
    ]
    if not symbols:
        pair_text = str(row.get("pair") or "").strip()
        symbols = [
            str(item).strip().upper()
            for item in pair_text.replace("->", "|").split("|")
            if str(item).strip()
        ]
    group_key = closed_loop_group_key(symbols)
    if group_key is None:
        return None, None
    return group_key, symbols


def group_pnl_by_closed_loop(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate expected P&L by closed-loop group and both directions.

    A group is the unordered ``A|B|C`` set.  Every reviewed attempt belongs
    to one of the two mirror directions around the group (A->B->C->A or
    A->C->B->A); numeric values are kept signed and rows without a signed
    value stay fail-closed with ``missingReason``.
    """
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        group_key, symbols = row_closed_loop_group(row)
        if group_key is None or symbols is None:
            continue
        direction = "|".join(symbols[:4])
        group = groups.setdefault(
            group_key,
            {
                "groupKey": group_key,
                "routeSymbols": symbols,
                "directions": {},
                "rowCount": 0,
                "signedRowCount": 0,
                "missingPnlRowCount": 0,
                "missingEvidenceBreakdown": {},
            },
        )
        group["rowCount"] += 1
        pnl = row_pnl(row, str(row.get("pair") or ""))
        direction_item = group["directions"].setdefault(
            direction,
            {
                "direction": direction,
                "routePath": " -> ".join(symbols),
                "rowCount": 0,
                "signedRowCount": 0,
                "missingRowCount": 0,
                "expectedSignedProfit": None,
                "expectedProfitSymbol": None,
                "pnlSourcePath": None,
                "missingReason": None,
                "missingEvidenceBreakdown": {},
                "sum": None,
                "total": None,
            },
        )
        direction_item["rowCount"] += 1
        decimal = as_decimal(pnl["expectedSignedProfit"])
        if decimal is None:
            if direction_item["signedRowCount"] > 0:
                continue
            group["missingPnlRowCount"] += 1
            direction_item["missingRowCount"] += 1
            evidence = pnl.get("missingEvidence") or {}
            category = evidence.get("code") or "native_missing_signed_value"
            direction_item["missingEvidenceBreakdown"][category] = (
                direction_item["missingEvidenceBreakdown"].get(category, 0) + 1
            )
            group["missingEvidenceBreakdown"][category] = (
                group["missingEvidenceBreakdown"].get(category, 0) + 1
            )
            continue
        group["signedRowCount"] += 1
        direction_item["signedRowCount"] += 1
        direction_item["missingReason"] = None
        direction_item["missingRowCount"] = 0
        direction_item["missingEvidenceBreakdown"] = {}
        direction_item["expectedProfitSymbol"] = (
            direction_item["expectedProfitSymbol"] or pnl["expectedProfitSymbol"]
        )
        direction_item["pnlSourcePath"] = (
            direction_item["pnlSourcePath"] or pnl["pnlSourcePath"]
        )
        direction_item["sum"] = str(
            as_decimal(direction_item["sum"] or "0") + decimal
        )
        direction_item["total"] = direction_item["sum"]
        direction_item["expectedSignedProfit"] = direction_item["sum"]
    result: list[dict[str, Any]] = []
    for group_key in sorted(groups):
        group = groups[group_key]
        direction_items = list(group["directions"].values())
        direction_items.sort(key=lambda item: str(item["direction"]))
        for item in direction_items:
            if item["missingRowCount"] == 0:
                item["missingReason"] = None
            elif item["signedRowCount"] == 0:
                item["missingReason"] = (
                    item["missingReason"]
                    or "attempt record has no signed borrow-token profit value; zero is not used as a placeholder"
                )
            else:
                item["missingReason"] = (
                    f"{item['missingRowCount']} row(s) without signed borrow-token "
                    f"profit value alongside {item['signedRowCount']} signed row(s)"
                )
        missing_row_count = sum(
            item["missingRowCount"]
            for item in direction_items
            if item["missingRowCount"] > 0
        )
        missing_evidence_breakdown: dict[str, int] = {}
        for item in direction_items:
            for category, count in item["missingEvidenceBreakdown"].items():
                missing_evidence_breakdown[category] = (
                    missing_evidence_breakdown.get(category, 0) + count
                )
        signed_row_count = sum(
            item["signedRowCount"]
            for item in direction_items
        )
        profitable = [
            item
            for item in direction_items
            if as_decimal(item.get("sum")) is not None
            and as_decimal(item.get("sum")) > 0
        ]
        unprofitable = [
            item
            for item in direction_items
            if as_decimal(item.get("sum")) is not None
            and as_decimal(item.get("sum")) <= 0
        ]
        result.append(
            {
                "groupKey": group_key,
                "routeSymbols": group["routeSymbols"],
                "rowCount": group["rowCount"],
                "signedRowCount": signed_row_count,
                "missingPnlRowCount": missing_row_count,
                "missingEvidenceBreakdown": dict(
                    sorted(
                        missing_evidence_breakdown.items(),
                        key=lambda item: (-item[1], item[0]),
                    )
                ),
                "directions": direction_items,
                "directionCount": len(direction_items),
                "profitableDirectionCount": len(profitable),
                "unprofitableDirectionCount": len(unprofitable),
                "missingPnlDirectionCount": sum(
                    1 for item in direction_items if item["missingRowCount"] > 0
                ),
                "profitableDirections": [
                    {
                        "direction": item["direction"],
                        "sum": item["sum"],
                        "symbol": item["expectedProfitSymbol"],
                    }
                    for item in profitable
                ],
                "unprofitableDirections": [
                    {
                        "direction": item["direction"],
                        "sum": item["sum"],
                        "symbol": item["expectedProfitSymbol"],
                    }
                    for item in unprofitable
                ],
                "hasPositivePnl": bool(profitable),
                "hasMissingPnl": missing_row_count > 0,
            }
        )
    return result


def funnel_counts(
    rows: list[dict[str, Any]],
    *,
    unique_success_count: int | None = None,
    profit_by_symbol: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Count the live-testing funnel for a bounded observation window.

    The funnel mirrors the runtime switch described by the user: after test
    success the testnet broadcast gate is opened, and each window report
    counts reviewed attempts, rows carrying a signed expected profit,
    broadcast attempts, success tx rows, and unique confirmed tx hashes.
    Rows without a signed value are never replaced by a zero placeholder.
    """
    expected_profit_count = 0
    broadcast_count = 0
    success_tx_count = 0
    unique_tx: set[str] = set()
    unique_success_with_expected_profit: set[str] = set()
    for row in rows:
        pnl = row_pnl(row, str(row.get("pair") or ""))
        if pnl["expectedSignedProfit"] is not None:
            expected_profit_count += 1
        if bool(row.get("broadcast")):
            broadcast_count += 1
        tx_hash = row.get("tx_hash")
        if (
            row.get("state") == "execution_success"
            and tx_hash
            and bool(row.get("broadcast"))
        ):
            success_tx_count += 1
            tx_key = str(tx_hash).strip().lower()
            unique_tx.add(tx_key)
            if pnl["expectedSignedProfit"] is not None:
                unique_success_with_expected_profit.add(tx_key)
    return {
        "reviewedCount": len(rows),
        "withExpectedProfitCount": expected_profit_count,
        "withoutExpectedProfitCount": len(rows) - expected_profit_count,
        "broadcastCount": broadcast_count,
        "successTxCount": success_tx_count,
        "uniqueSuccessCount": (
            len(unique_tx)
            if unique_success_count is None
            else unique_success_count
        ),
        "uniqueSuccessWithExpectedProfitCount": len(unique_success_with_expected_profit),
        "realizedProfitBySymbol": profit_by_symbol or [],
    }


def hex_to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value), 16)
    except ValueError:
        return None


def count_nested_keys(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[str, int]:
    counts = {key: 0 for key in keys}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in counts:
                    counts[key] += 1
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for row in rows:
        walk(row)
    return counts


def row_gas(row: dict[str, Any]) -> dict[str, Any]:
    receipt = nested(row, "auto_execute.result.receipt.receipt") or {}
    gas_used = hex_to_int(receipt.get("gasUsed"))
    gas_price = hex_to_int(receipt.get("effectiveGasPrice"))
    return {
        "gasUsedInt": gas_used,
        "effectiveGasPriceInt": gas_price,
        "gasUsed": receipt.get("gasUsed"),
        "effectiveGasPrice": receipt.get("effectiveGasPrice"),
        "blockNumber": receipt.get("blockNumber"),
    }


def assess_success_gas(
    successes: list[dict[str, Any]],
    usd_prices: dict[str, Decimal] | None = None,
    usd_snapshot: dict[str, Any] | None = None,
    *,
    now_utc: datetime | None = None,
    max_age_seconds: int = DEFAULT_USD_MAX_AGE_SECONDS,
    max_reference_lag_seconds: int = DEFAULT_USD_MAX_REFERENCE_LAG_SECONDS,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    now = now_utc or datetime.now(timezone.utc)
    for item in successes:
        gas_used = item.get("gasUsedInt")
        gas_price = item.get("effectiveGasPriceInt")
        symbol = item.get("expectedProfitSymbol")
        profit = as_decimal(item.get("expectedSignedProfit"))
        token_usd = usd_price_quote(
            symbol,
            usd_prices or {},
            (usd_snapshot or {}).get("observedAt"),
            item.get("observedAt"),
            now_utc=now,
            max_age_seconds=max_age_seconds,
            max_reference_lag_seconds=max_reference_lag_seconds,
        )
        eth_usd = usd_price_quote(
            "ETH",
            usd_prices or {},
            (usd_snapshot or {}).get("observedAt"),
            item.get("observedAt"),
            now_utc=now,
            max_age_seconds=max_age_seconds,
            max_reference_lag_seconds=max_reference_lag_seconds,
        )
        row: dict[str, Any] = {
            "pair": item.get("pair"),
            "expectedProfitSymbol": symbol,
            "expectedSignedProfit": str(profit) if profit is not None else None,
            "gasUsedInt": gas_used,
            "effectiveGasPriceInt": gas_price,
            "usdReference": {
                "profitSymbol": token_usd,
                "eth": eth_usd,
            }
            if usd_prices
            else None,
        }
        if gas_used is None or gas_price is None:
            row["assessment"] = "missing_gas_evidence"
            row["missingReason"] = "receipt gasUsed/effectiveGasPrice not available"
        elif symbol == "ETH" and profit is not None:
            gas_cost = Decimal(gas_used) * Decimal(gas_price) / Decimal(10**18)
            net_after_gas = profit - gas_cost
            row["gasCostEth"] = str(gas_cost)
            row["netAfterGasEth"] = str(net_after_gas)
            row["assessment"] = (
                "net_positive_after_gas" if net_after_gas > 0 else "below_gas_cost"
            )
            if token_usd.get("fresh"):
                eth_price = as_decimal(token_usd["priceUsd"])
                if eth_price is not None:
                    row["gasCostUsd"] = str(gas_cost * eth_price)
                    row["netAfterGasUsd"] = str(net_after_gas * eth_price)
        elif symbol != "ETH" and profit is not None:
            token_price = as_decimal(token_usd.get("priceUsd"))
            eth_price = as_decimal(eth_usd.get("priceUsd"))
            if token_usd.get("fresh") and eth_usd.get("fresh") and token_price is not None and eth_price is not None:
                gas_cost = Decimal(gas_used) * Decimal(gas_price) / Decimal(10**18)
                token_per_eth = token_price / eth_price
                net_after_gas = profit * token_per_eth - gas_cost
                row["tokenPerEth"] = str(token_per_eth)
                row["gasCostEth"] = str(gas_cost)
                row["netAfterGasEth"] = str(net_after_gas)
                row["gasCostUsd"] = str(gas_cost * eth_price)
                row["netAfterGasUsd"] = str(net_after_gas * eth_price)
                row["assessment"] = (
                    "net_positive_after_gas" if net_after_gas > 0 else "below_gas_cost"
                )
            else:
                row["assessment"] = "needs_token_price_quote"
                row["missingReason"] = (
                    "no fresh ETH/token price quote; profit symbol differs from ETH gas currency"
                )
        elif symbol != "ETH":
            row["assessment"] = "needs_token_price_quote"
            row["missingReason"] = (
                "profit symbol differs from ETH gas currency; "
                "no ETH/token price quote in this report"
            )
        else:
            row["assessment"] = "missing_profit_value"
            row["missingReason"] = "no signed profit value"
        items.append(row)
    return {
        "evaluableEthCount": sum(
            1 for item in items if item["assessment"] in ("net_positive_after_gas", "below_gas_cost")
        ),
        "netPositiveAfterGasCount": sum(
            1 for item in items if item["assessment"] == "net_positive_after_gas"
        ),
        "belowGasCostCount": sum(
            1 for item in items if item["assessment"] == "below_gas_cost"
        ),
        "needsTokenPriceQuoteCount": sum(
            1 for item in items if item["assessment"] == "needs_token_price_quote"
        ),
        "missingGasCount": sum(
            1 for item in items if item["assessment"] == "missing_gas_evidence"
        ),
        "items": items,
    }


def row_precision(
    row: dict[str, Any],
    usd_prices: dict[str, Decimal] | None = None,
    usd_snapshot: dict[str, Any] | None = None,
    token_decimals: dict[str, int | None] | None = None,
    token_decimals_meta: dict[str, Any] | None = None,
    *,
    now_utc: datetime | None = None,
    max_age_seconds: int = DEFAULT_USD_MAX_AGE_SECONDS,
    max_reference_lag_seconds: int = DEFAULT_USD_MAX_REFERENCE_LAG_SECONDS,
) -> dict[str, Any]:
    """Return signed profit precision and amount-selection evidence per row."""
    quote = nested(row, "quote") or {}
    live = quote.get("live_onchain_evidence") or {}
    cost = live.get("costModel") or {}
    quote_analysis = quote.get("quote_analysis") or {}
    auto_result = nested(row, "auto_execute.result") or {}
    best_calc = auto_result.get("bestAmountCalculation") or row.get("bestAmountCalculation") or {}
    pnl = row_pnl(row, str(row.get("pair") or ""))
    profit = as_decimal(pnl["expectedSignedProfit"])
    bps = as_decimal(cost.get("netProfitBps"))
    decimals_trusted = quote_analysis.get("tokenDecimalsTrusted")
    decimals_source: str | None = None
    route_addresses = route_token_addresses(row)
    if token_decimals is not None:
        found = [
            token_decimals[address]
            for address in route_addresses
            if address in token_decimals
        ]
        if (
            found
            and len(found) == len(route_addresses)
            and all(value is not None and value == 18 for value in found)
        ):
            decimals_trusted = True
            decimals_source = "sqlite_chain_tokens"
        elif decimals_trusted is not True:
            decimals_trusted = False
            decimals_source = "sqlite_chain_tokens"
    live_price_verified = quote_analysis.get("livePriceVerified")
    trusted_usd = quote_analysis.get("trustedUsdReference")
    net_profit_usd = quote_analysis.get("netProfitUsd")
    now = now_utc or datetime.now(timezone.utc)
    usd_quote = (
        usd_price_quote(
            pnl["expectedProfitSymbol"],
            usd_prices or {},
            (usd_snapshot or {}).get("observedAt"),
            row.get("observed_at") or row.get("created_at"),
            now_utc=now,
            max_age_seconds=max_age_seconds,
            max_reference_lag_seconds=max_reference_lag_seconds,
        )
        if usd_prices
        else None
    )
    usd_reference_source: str | None = None
    if trusted_usd is not True and usd_quote is not None and usd_quote.get("fresh") and profit is not None:
        usd_price = as_decimal(usd_quote["priceUsd"])
        if usd_price is not None:
            net_profit_usd = str(profit * usd_price)
            trusted_usd = True
            usd_reference_source = "binance_market_snapshot"
    elif trusted_usd is True:
        usd_reference_source = "quote_analysis"
    selection_confirmed = best_calc.get("selectionConfirmed")
    if selection_confirmed is None:
        selection_confirmed = best_calc.get("bestAmountConfirmed")
    selection_reason = best_calc.get("selectionReason")
    if selection_reason is None:
        selection_reason = best_calc.get("bestAmountConfirmationReason")
    assessment: list[str] = []
    if profit is None:
        assessment.append("missing_signed_profit")
    if bps is not None and bps < Decimal("1"):
        assessment.append("micro_profit_below_1_bps")
    if decimals_trusted is not True:
        assessment.append("token_decimals_unverified")
    if selection_confirmed is not True:
        assessment.append("amount_selection_unconfirmed")
    if net_profit_usd is None or trusted_usd is not True:
        assessment.append("usd_reference_unavailable")
    return {
        "precision": {
            "symbol": pnl["expectedProfitSymbol"],
            "netProfitAmount": pnl["expectedSignedProfit"],
            "netProfitBps": None if bps is None else str(bps),
            "borrowInputAmount": cost.get("borrowInputAmount"),
            "borrowInputSymbol": cost.get("borrowInputSymbol"),
            "finalOutputAmount": cost.get("finalOutputAmount"),
            "finalOutputSymbol": cost.get("finalOutputSymbol"),
            "selectedBorrowInputAmountRaw": cost.get("selectedBorrowInputAmountRaw"),
            "amountSelectionMethod": best_calc.get("method")
            or auto_result.get("bestAmountMethod"),
            "amountOptimized": best_calc.get("amountOptimized"),
            "amountSelectionConfirmed": selection_confirmed,
            "selectionReason": selection_reason,
            "tokenDecimalsTrusted": decimals_trusted,
            "tokenDecimalsSource": decimals_source,
            "routeTokenAddresses": route_addresses,
            "livePriceVerified": live_price_verified,
            "trustedUsdReference": trusted_usd,
            "netProfitUsd": net_profit_usd,
            "usdReferenceSource": usd_reference_source,
            "usdReference": usd_quote,
            "assessment": assessment,
        }
    }


def assess_precision_summary(successes: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize precision/amount-selection evidence across unique successes."""
    items = [item.get("precision") for item in successes]
    counts: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        for label in item.get("assessment") or []:
            counts[label] = counts.get(label, 0) + 1
    return {
        "rowCount": len(items),
        "assessmentCounts": counts,
        "usdNetProfitAvailableCount": sum(
            1
            for item in items
            if isinstance(item, dict) and item.get("netProfitUsd") is not None
        ),
        "items": items,
    }


def row_public(row: dict[str, Any], *, with_pnl: bool) -> dict[str, Any]:
    pair = str(row.get("pair") or "")
    blockers: list[str] = []
    live_evidence = nested(row, "quote.live_onchain_evidence") or {}
    blockers.extend(str(item) for item in live_evidence.get("blockers") or [])
    blockers.extend(
        str(item)
        for item in nested(row, "transaction_development.blockers")
        or nested(row, "quote.live_onchain_evidence.transactionDevelopment.blockers")
        or []
    )
    blockers.extend(str(item) for item in row.get("blocked_reasons") or [])
    unique_blockers: list[str] = []
    for item in blockers:
        if item and item not in unique_blockers:
            unique_blockers.append(item)
    item: dict[str, Any] = {
        "observedAt": row.get("observed_at"),
        "observedAtLocal": local_iso(row.get("observed_at")),
        "createdAt": row.get("created_at"),
        "createdAtLocal": local_iso(row.get("created_at")),
        "pair": pair,
        "state": row.get("state"),
        "broadcast": row.get("broadcast"),
        "blockers": unique_blockers[:10],
    }
    if with_pnl:
        pnl = row_pnl(row, pair)
        item["expectedSignedProfit"] = pnl["expectedSignedProfit"]
        item["expectedProfitSymbol"] = pnl["expectedProfitSymbol"]
        item["pnlSourcePath"] = pnl["pnlSourcePath"]
        item["pnlMissingReason"] = pnl["missingReason"]
    return item


def sum_by_symbol(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, Decimal] = {}
    counts: dict[str, int] = {}
    for item in items:
        if item.get("expectedSignedProfit") is None:
            continue
        symbol = item.get("expectedProfitSymbol")
        decimal = as_decimal(item["expectedSignedProfit"])
        if decimal is None:
            continue
        key = symbol or "unknown"
        totals[key] = totals.get(key, Decimal("0")) + decimal
        counts[key] = counts.get(key, 0) + 1
    return [
        {
            "symbol": key,
            "count": counts[key],
            "total": str(totals[key]),
        }
        for key in sorted(totals, key=lambda item: (counts[item], item))
    ]


def read_rows(
    path: Path,
    start: datetime | None = None,
    end: datetime | None = None,
    *,
    network: str = TESTNET_NETWORK,
    chain_id: int = TESTNET_CHAIN_ID,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    first_stamp: str | None = None
    last_stamp: str | None = None
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
            if not isinstance(row, dict):
                continue
            stamp = str(row.get("observed_at") or row.get("created_at") or "")
            observed = parse_iso(stamp)
            if observed is None:
                continue
            if first_stamp is None or observed < parse_iso(first_stamp):
                first_stamp = stamp
            if last_stamp is None or observed > parse_iso(last_stamp):
                last_stamp = stamp
            if start is not None and observed < start:
                continue
            if end is not None and observed > end:
                continue
            rows.append(row)
    return rows, {
        "firstObservedAt": first_stamp,
        "lastObservedAt": last_stamp,
    }


def _read_rows_sqlite(
    path: Path,
    start: datetime | None = None,
    end: datetime | None = None,
    *,
    network: str = TESTNET_NETWORK,
    chain_id: int = TESTNET_CHAIN_ID,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if str(SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(SRC_ROOT))
    from core.db.storage_cow_execution import load_recent_cow_execution_attempts

    rows = load_recent_cow_execution_attempts(
        str(path),
        limit=1_000_000,
        networks=[network],
        chain_ids=[chain_id],
        start_at=start.isoformat() if start is not None else None,
        end_at=end.isoformat() if end is not None else None,
        retention_days=3650,
    )
    stamps = [
        str(row.get("observed_at") or row.get("created_at") or "")
        for row in rows
        if str(row.get("observed_at") or row.get("created_at") or "")
    ]
    return rows, {
        "firstObservedAt": min(stamps) if stamps else None,
        "lastObservedAt": max(stamps) if stamps else None,
    }


def analyze(
    rows: list[dict[str, Any]],
    usd_prices: dict[str, Decimal] | None = None,
    usd_snapshot: dict[str, Any] | None = None,
    token_decimals: dict[str, int | None] | None = None,
    token_decimals_meta: dict[str, Any] | None = None,
    *,
    now_utc: datetime | None = None,
    max_age_seconds: int = DEFAULT_USD_MAX_AGE_SECONDS,
    max_reference_lag_seconds: int = DEFAULT_USD_MAX_REFERENCE_LAG_SECONDS,
) -> dict[str, Any]:
    successes: dict[str, dict[str, Any]] = {}
    failed: list[dict[str, Any]] = []
    ready: list[dict[str, Any]] = []
    blocked_with_pnl: list[dict[str, Any]] = []
    blocked_missing_pnl = 0
    blocked_missing_evidence: collections.Counter[str] = collections.Counter()
    review_consistent_count = 0
    review_inconsistent_positive_count = 0
    for row in rows:
        pnl = row_pnl(row, str(row.get("pair") or ""))
        if pnl.get("reviewConsistent"):
            review_consistent_count += 1
        elif pnl.get("expectedSignedProfit") is not None and as_decimal(pnl["expectedSignedProfit"]) > 0:
            review_inconsistent_positive_count += 1
        state = row.get("state")
        tx_hash = str(row.get("tx_hash") or "").strip().lower()
        broadcast = bool(row.get("broadcast"))
        if state == "execution_success" and tx_hash.startswith("0x"):
            public = row_public(row, with_pnl=True)
            existing = successes.get(tx_hash)
            if existing is None or existing.get("expectedSignedProfit") is None:
                if existing is not None and existing.get("expectedSignedProfit") is not None:
                    continue
                gas = row_gas(row)
                public.update(gas)
                public["precision"] = row_precision(
                    row,
                    usd_prices,
                    usd_snapshot,
                    token_decimals,
                    token_decimals_meta,
                    now_utc=now_utc,
                    max_age_seconds=max_age_seconds,
                    max_reference_lag_seconds=max_reference_lag_seconds,
                )["precision"]
                successes[tx_hash] = public
            continue
        if state in ("execution_failed", "submission_failed"):
            failed.append(row_public(row, with_pnl=True))
            continue
        if state == "ready_for_transaction_development":
            public = row_public(row, with_pnl=True)
            key = (
                public.get("pair"),
                public.get("expectedSignedProfit"),
            )
            if not any(
                existing.get("pair") == public.get("pair")
                and existing.get("expectedSignedProfit") == public.get("expectedSignedProfit")
                for existing in ready
            ):
                ready.append(public)
            continue
        if state == "quote_required":
            pnl = row_pnl(row, str(row.get("pair") or ""))
            if pnl["expectedSignedProfit"] is None:
                blocked_missing_pnl += 1
                evidence = pnl.get("missingEvidence") or {}
                blocked_missing_evidence[evidence.get("code") or "native_missing_signed_value"] += 1
            else:
                blocked_with_pnl.append(row_public(row, with_pnl=True))
            continue

    unique_successes = list(successes.values())
    unique_successes.sort(key=lambda item: str(item.get("observedAt") or ""))
    blocked_samples = sorted(
        blocked_with_pnl,
        key=lambda item: str(item.get("observedAt") or ""),
        reverse=True,
    )[:30]
    ready_samples = ready[:40]
    blocked_confirmed_no_profit = sum(
        count
        for category, count in blocked_missing_evidence.items()
        if category == "no_profitable_route"
    )
    blocked_quote_failed = sum(
        count
        for category, count in blocked_missing_evidence.items()
        if category in {
            "quoter_revert",
            "quoter_unavailable",
            "quoter_insufficient_liquidity",
        }
    )
    blocked_rpc_transport_error = sum(
        count
        for category, count in blocked_missing_evidence.items()
        if category == "rpc_transport_error"
    )
    blocked_quote_insufficient_liquidity = sum(
        count
        for category, count in blocked_missing_evidence.items()
        if category == "quoter_insufficient_liquidity"
    )
    blocked_not_verified = sum(
        count
        for category, count in blocked_missing_evidence.items()
        if category not in {
            "no_profitable_route",
            "quoter_revert",
            "quoter_unavailable",
            "quoter_insufficient_liquidity",
            "rpc_transport_error",
        }
    )
    funnel = funnel_counts(
        rows,
        unique_success_count=len(unique_successes),
        profit_by_symbol=sum_by_symbol(unique_successes),
    )

    return {
        "success": {
            "uniqueSuccessCount": len(unique_successes),
            "successes": unique_successes,
            "profitBySymbol": sum_by_symbol(unique_successes),
            "gasAssessment": assess_success_gas(
                unique_successes,
                usd_prices,
                usd_snapshot,
                now_utc=now_utc,
                max_age_seconds=max_age_seconds,
                max_reference_lag_seconds=max_reference_lag_seconds,
            ),
            "precisionAssessment": assess_precision_summary(unique_successes),
        },
        "failed": {
            "failedCount": len(failed),
            "failedClosedLoops": failed,
            "expectedPnlBySymbol": sum_by_symbol(failed),
        },
        "ready": {
            "readyCount": len(ready),
            "readySamples": ready_samples,
        },
        "blocked": {
            "blockedCount": len(blocked_with_pnl) + blocked_missing_pnl,
            "blockedWithExpectedPnlCount": len(blocked_with_pnl),
            "blockedWithMissingPnlCount": blocked_missing_pnl,
            "blockedMissingEvidenceBreakdown": dict(
                sorted(
                    blocked_missing_evidence.items(),
                    key=lambda item: (-item[1], item[0]),
                )
            ),
            "blockedConfirmedNoProfitCount": blocked_confirmed_no_profit,
            "blockedQuoteFailedCount": blocked_quote_failed,
            "blockedQuoteInsufficientLiquidityCount": blocked_quote_insufficient_liquidity,
            "blockedRpcTransportErrorCount": blocked_rpc_transport_error,
            "blockedNotVerifiedCount": blocked_not_verified,
            "blockedSamplesWithExpectedPnl": blocked_samples,
        },
        "funnel": funnel,
        "groupPnlByClosedLoopGroup": group_pnl_by_closed_loop(rows),
        "t21FieldCounts": count_nested_keys(rows, FIELD_KEYS),
        "reviewConsistency": {
            "reviewConsistentCount": review_consistent_count,
            "reviewInconsistentPositiveEstimateCount": review_inconsistent_positive_count,
            "totalReviewedCount": len(rows),
        },
    }


def conclusion(analysis: dict[str, Any]) -> str:
    success = analysis["success"]
    failed = analysis["failed"]
    blocked = analysis["blocked"]
    profit_text = ", ".join(
        f"{item['total']} {item['symbol']} across {item['count']} tx"
        for item in success["profitBySymbol"]
    ) or "no signed profit value"
    failed_text = ", ".join(
        f"{item['total']} {item['symbol']} across {item['count']} rows"
        for item in failed["expectedPnlBySymbol"]
    ) or "no expected numeric value"
    return (
        f"{success['uniqueSuccessCount']} unique on-chain success broadcasts with signed "
        f"profit by symbol: {profit_text}; {failed['failedCount']} failed broadcasts with "
        f"expected P&L by symbol: {failed_text}; {blocked['blockedCount']} blocked/quote rows "
        f"present, of which {blocked['blockedWithExpectedPnlCount']} carry an expected numeric P&L."
    )


def _kw(tests: list[tuple[str, Any, Any]]) -> None:
    for name, actual, expected in tests:
        if actual != expected:
            raise AssertionError(f"{name}: expected {expected!r}, got {actual!r}")


def _self_test() -> None:
    _kw(
        [
            ("decimal-from-int", as_decimal("3.25"), Decimal("3.25")),
            ("decimal-from-float", as_decimal(5.5), Decimal("5.5")),
            ("reject-bool", as_decimal(True), None),
            ("reject-hex", as_decimal("0x123"), None),
            ("nested-missing", nested({}, "a.b.c"), None),
            ("nested-present", nested({"a": {"b": {"c": 1}}}, "a.b.c"), 1),
            (
                "resolve-stock-usd",
                resolve_usd_price("TSLA", {"TSLABUSDT": Decimal("383.16")}),
                (Decimal("383.16"), "TSLABUSDT"),
            ),
            (
                "resolve-native-usd",
                resolve_usd_price("ETH", {"ETHUSDT": Decimal("2398.28")}),
                (Decimal("2398.28"), "ETHUSDT"),
            ),
            (
                "resolve-missing-usd",
                resolve_usd_price("TZ", {"ETHUSDT": Decimal("2398.28")}),
                (None, None),
            ),
        ]
    )
    _kw(
        [
            (
                "t21-field-counts",
                count_nested_keys(
                    [
                        {
                            "quote": {
                                "costModel": {
                                    "netProductDeltaRaw": "-0x14",
                                    "netProfitAmountMissingReason": "missing",
                                }
                            }
                        },
                        {"blockers": ["ArbitrageNotProfitable(received, required)"]},
                    ],
                    FIELD_KEYS,
                ),
                {
                    "netProductDeltaRaw": 1,
                    "ArbitrageNotProfitable": 0,
                    "netProfitAmountMissingReason": 1,
                },
            )
        ]
    )
    row = {
        "pair": "A -> B -> C -> A",
        "observed_at": "2026-10-20T04:13:56Z",
        "created_at": "2026-10-20T04:14:01Z",
        "state": "execution_success",
        "tx_hash": "0xaaaa",
        "broadcast": True,
        "blocked_reasons": ["root"],
        "quote": {
            "live_onchain_evidence": {
                "blockers": ["onchain"],
                "costModel": {
                    "netProfitAmount": "1.25",
                    "netProfitAmountSymbol": "A",
                    "netProfitBps": "0.5",
                },
            }
        },
    }
    public = row_public(row, with_pnl=True)
    _kw(
        [
            ("success-pnl", public["expectedSignedProfit"], "1.25"),
            ("success-symbol", public["expectedProfitSymbol"], "A"),
            ("success-blockers", public["blockers"], ["onchain", "root"]),
        ]
    )
    reverted_row = {
        **row,
        "blocked_reasons": [
            "official_v4:previewBestPool:eth_call: execution reverted: arithmetic underflow or overflow"
        ],
        "state": "quote_required",
        "quote": {
            "live_onchain_evidence": {
                "costModel": {
                    "netProfitAmount": None,
                    "netProfitAmountMissingReason": (
                        "native result has no signed borrow-token profit value; "
                        "zero placeholder does not prove a zero or negative loss"
                    ),
                }
            }
        },
    }
    reverted_pnl = row_pnl(reverted_row, str(row.get("pair") or ""))
    _kw(
        [
            ("reverted-missing-pnl", reverted_pnl["expectedSignedProfit"], None),
            (
                "reverted-missing-code",
                reverted_pnl["missingEvidence"]["code"],
                "quoter_revert",
            ),
            (
                "reverted-missing-reliability",
                reverted_pnl["missingEvidence"]["reliability"],
                "quote_failed",
            ),
        ]
    )
    no_profit_row = {
        **row,
        "blocked_reasons": [
            "chain review failed: NoProfitableRoute()",
            "closed loop currently has no positive pool-token profit",
        ],
        "state": "quote_required",
        "quote": {
            "live_onchain_evidence": {
                "costModel": {
                    "netProfitAmount": None,
                    "netProfitAmountMissingReason": (
                        "native result has no signed borrow-token profit value; "
                        "zero placeholder does not prove a zero or negative loss"
                    ),
                }
            }
        },
    }
    no_profit_pnl = row_pnl(no_profit_row, str(row.get("pair") or ""))
    _kw(
        [
            ("no-profit-missing-code", no_profit_pnl["missingEvidence"]["code"], "no_profitable_route"),
            (
                "no-profit-missing-reliability",
                no_profit_pnl["missingEvidence"]["reliability"],
                "confirmed_no_profit",
            ),
        ]
    )
    rpc_failed_row = {
        **row,
        "blocked_reasons": [
            "official_v4:previewArbitrageBestAmount:eth_call:attempt=3: "
            "eth_call RPC request failed: error sending request for url (https://example.invalid/)"
        ],
        "state": "quote_required",
        "quote": {
            "live_onchain_evidence": {
                "blockers": [
                    "official_v4:previewArbitrageBestAmount:eth_call:attempt=3: "
                    "eth_call RPC request failed: error sending request for url (https://example.invalid/)"
                ],
                "costModel": {
                    "netProfitAmount": None,
                    "netProfitAmountMissingReason": (
                        "native result has no signed borrow-token profit value; "
                        "zero placeholder does not prove a zero or negative loss"
                    ),
                },
            }
        },
    }
    rpc_failed_pnl = row_pnl(rpc_failed_row, str(row.get("pair") or ""))
    _kw(
        [
            ("rpc-failed-missing-code", rpc_failed_pnl["missingEvidence"]["code"], "rpc_transport_error"),
            (
                "rpc-failed-missing-reliability",
                rpc_failed_pnl["missingEvidence"]["reliability"],
                "quote_failed",
            ),
        ]
    )
    rows = [
        row,
        {
            **row,
            "quote": {
                "live_onchain_evidence": {
                    "blockers": [],
                    "costModel": {"netProfitAmount": None},
                }
            },
            "state": "execution_failed",
        },
        {
            **row,
            "state": "quote_required",
            "pair": "B -> C -> A -> B",
            "precheck": {"reference_token_profit_amount": "-0.5"},
            "quote": None,
        },
        {
            **no_profit_row,
            "pair": "A -> B -> C -> A",
        },
    ]
    report = analyze(rows)
    _kw(
        [
            ("unique-success", report["success"]["uniqueSuccessCount"], 1),
            ("failed-count", report["failed"]["failedCount"], 1),
            ("failed-bound", report["failed"]["failedClosedLoops"][0]["expectedSignedProfit"], None),
            ("blocked-with-pnl", report["blocked"]["blockedWithExpectedPnlCount"], 1),
            ("blocked-missing", report["blocked"]["blockedWithMissingPnlCount"], 1),
            (
                "blocked-missing-breakdown",
                report["blocked"]["blockedMissingEvidenceBreakdown"],
                {"no_profitable_route": 1},
            ),
            (
                "blocked-confirmed-no-profit",
                report["blocked"]["blockedConfirmedNoProfitCount"],
                1,
            ),
            (
                "blocked-quote-failed",
                report["blocked"]["blockedQuoteFailedCount"],
                0,
            ),
            (
                "funnel-without-expected",
                report["funnel"]["withoutExpectedProfitCount"],
                2,
            ),
            (
                "t21-missing-field-count",
                report["t21FieldCounts"]["netProfitAmountMissingReason"],
                1,
            ),
            (
                "success-gas-missing-count",
                report["success"]["gasAssessment"]["missingGasCount"],
                1,
            ),
            (
                "precision-row-count",
                report["success"]["precisionAssessment"]["rowCount"],
                1,
            ),
            (
                "precision-micro-profit",
                report["success"]["precisionAssessment"]["assessmentCounts"].get(
                    "micro_profit_below_1_bps"
                ),
                1,
            ),
            (
                "precision-amount-unconfirmed",
                report["success"]["precisionAssessment"]["assessmentCounts"].get(
                    "amount_selection_unconfirmed"
                ),
                1,
            ),
            (
                "precision-usd-unavailable",
                report["success"]["precisionAssessment"]["assessmentCounts"].get(
                    "usd_reference_unavailable"
                ),
                1,
            ),
            (
                "precision-usd-available-count",
                report["success"]["precisionAssessment"]["usdNetProfitAvailableCount"],
                0,
            ),
        ]
    )
    fresh_quote = usd_price_quote(
        "TSLA",
        {"TSLABUSDT": Decimal("383.16")},
        "2026-10-22T15:49:44Z",
        "2026-10-22T16:00:00Z",
        now_utc=datetime(2026, 10, 22, 16, 0, tzinfo=timezone.utc),
        max_age_seconds=DEFAULT_USD_MAX_AGE_SECONDS,
        max_reference_lag_seconds=DEFAULT_USD_MAX_REFERENCE_LAG_SECONDS,
    )
    _kw(
        [
            ("usd-quote-fresh", fresh_quote["fresh"], True),
            ("usd-quote-symbol", fresh_quote["snapshotSymbol"], "TSLABUSDT"),
            ("usd-quote-price", fresh_quote["priceUsd"], "383.16"),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-start", default=None, help="ISO-8601 UTC start")
    parser.add_argument("--window-end", default=None, help="ISO-8601 UTC end")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--skip-latest", action="store_true")
    parser.add_argument("--usd-price-json", default=str(DEFAULT_USD_PRICE_JSON))
    parser.add_argument("--usd-max-age-seconds", type=int, default=DEFAULT_USD_MAX_AGE_SECONDS)
    parser.add_argument(
        "--usd-max-reference-lag-seconds",
        type=int,
        default=DEFAULT_USD_MAX_REFERENCE_LAG_SECONDS,
    )
    parser.add_argument("--skip-usd-reference", action="store_true")
    parser.add_argument("--no-chain-token-decimals", action="store_true")
    parser.add_argument(
        "--store",
        default=str(DEFAULT_STORE),
        help="SQLite store path for attempts and chain token metadata.",
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
    parser.add_argument("--self-test", action="store_true", help="run internal checks and exit")
    args = parser.parse_args()

    if args.self_test:
        _self_test()
        print("self-test ok")
        return 0

    start = parse_iso(args.window_start) if args.window_start else None
    end = parse_iso(args.window_end) if args.window_end else None
    if start is not None and end is not None and start > end:
        parser.error("--window-start must not be after --window-end")

    attempts_path = Path(args.store)
    network, chain_id = infer_chain_scope(attempts_path, args.network, args.chain_id)
    rows, range_info = read_rows(
        attempts_path,
        start=start,
        end=end,
        network=network,
        chain_id=chain_id,
    )
    flags = env_broadcast_flags(DEFAULT_ENV)
    now_utc = datetime.now(timezone.utc)
    usd_prices: dict[str, Decimal] | None = None
    usd_snapshot: dict[str, Any] | None = None
    token_decimals: dict[str, int | None] | None = None
    token_decimals_meta: dict[str, Any] | None = None
    chain_token_meta: dict[str, Any] = {"available": False}
    if not args.no_chain_token_decimals:
        token_decimals, token_decimals_meta = load_chain_token_decimals(
            Path(args.store),
            network=network,
            chain_id=chain_id,
        )
        chain_token_meta = {
            "source": "sqlite_chain_tokens",
            "available": bool(token_decimals_meta.get("available")),
            "rowCount": token_decimals_meta.get("rowCount"),
            "trustedDecimalsCount": token_decimals_meta.get("trustedDecimalsCount"),
            "unavailableReason": token_decimals_meta.get("unavailableReason"),
        }
    usd_reference: dict[str, Any] = {
        "source": "binance_market_snapshot",
        "available": False,
    }
    if not args.skip_usd_reference:
        usd_prices, usd_snapshot = load_binance_usd_prices(Path(args.usd_price_json))
        usd_reference = {
            "source": "binance_market_snapshot",
            "available": bool(usd_snapshot.get("available")),
            "observedAt": usd_snapshot.get("observedAt"),
            "priceCount": usd_snapshot.get("priceCount"),
            "maxAgeSeconds": args.usd_max_age_seconds,
            "maxReferenceLagSeconds": args.usd_max_reference_lag_seconds,
            "unavailableReason": usd_snapshot.get("unavailableReason"),
        }
    else:
        usd_reference["unavailableReason"] = "skipped_by_flag"
    analysis = analyze(
        rows,
        usd_prices,
        usd_snapshot,
        token_decimals,
        token_decimals_meta,
        now_utc=now_utc,
        max_age_seconds=args.usd_max_age_seconds,
        max_reference_lag_seconds=args.usd_max_reference_lag_seconds,
    )

    output_dir = Path(args.output_dir) if args.output_dir else None
    if output_dir is None:
        stamp = (end or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
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
            / "closed_loop_pnl"
            / stamp
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_utc = datetime.now(timezone.utc)
    report = {
        "schemaVersion": schema_version_for(network),
        "network": network,
        "chainId": chain_id,
        "generatedAt": generated_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generatedAtLocal": generated_utc.astimezone().isoformat(timespec="seconds"),
        "source": str(attempts_path),
        "rowCount": len(rows),
        "range": {
            "startUtc": args.window_start or range_info.get("firstObservedAt"),
            "endUtc": args.window_end or range_info.get("lastObservedAt"),
            "startLocal": local_iso(args.window_start or range_info.get("firstObservedAt")),
            "endLocal": local_iso(args.window_end or range_info.get("lastObservedAt")),
            "firstObservedAt": range_info.get("firstObservedAt"),
            "lastObservedAt": range_info.get("lastObservedAt"),
            "firstObservedAtLocal": local_iso(range_info.get("firstObservedAt")),
            "lastObservedAtLocal": local_iso(range_info.get("lastObservedAt")),
        },
        "broadcastGates": flags,
        "usdReference": usd_reference,
        "chainTokenMetadata": chain_token_meta,
        "analysis": analysis,
        "conclusion": conclusion(analysis),
        "safety": {
            "mainnetBroadcastEnabled": flags["ROBINHOOD_MAINNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST"],
            "testnetBroadcastEnabled": flags["ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST"],
            "writesSqlite": False,
        },
    }
    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if not args.skip_latest:
        latest_path = output_dir.parent / "latest.json"
        latest_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
