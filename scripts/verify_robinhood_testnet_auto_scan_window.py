#!/usr/bin/env python3
"""Verify a bounded Robinhood testnet auto-scan window from runtime evidence.

The script is intentionally read-only for the SQLite data source: it reads
auto-scan attempts from the local SQLite store and
publishes a verification report under
``flashloan/src/runtime/evidence/robinhood-testnet/auto_scan_verification/``.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
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
SCHEMA_VERSION = "robinhood-testnet-auto-scan-window-v1"
FIELD_KEYS = (
    "netProductDeltaRaw",
    "ArbitrageNotProfitable",
    "netProfitAmountMissingReason",
)
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


def parse_iso(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def read_attempts(path: Path, start: datetime, end: datetime) -> list[dict[str, Any]]:
    if path.suffix.lower() not in {".sqlite3", ".sqlite", ".db"}:
        raise ValueError(
            "auto-scan verification requires the SQLite store; "
            f"legacy JSONL is no longer an online source: {path}"
        )
    return _read_attempts_sqlite(path, start, end)


def _read_attempts_sqlite(path: Path, start: datetime, end: datetime) -> list[dict[str, Any]]:
    if str(SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(SRC_ROOT))
    from core.db.storage_cow_execution import load_recent_cow_execution_attempts

    rows = load_recent_cow_execution_attempts(
        str(path),
        limit=1_000_000,
        networks=["robinhood-testnet"],
        chain_ids=[46630],
        start_at=start.isoformat(),
        end_at=end.isoformat(),
        retention_days=3650,
    )
    return rows


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


def nested(obj: dict[str, Any], *keys: str) -> Any:
    current: Any = obj
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def hex_to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value), 16)
    except ValueError:
        return None


def as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def row_symbol(row: dict[str, Any]) -> str | None:
    for path in SYMBOL_PATHS:
        value = nested(row, path)
        text = str(value or "").strip()
        if text and text.lower() not in {"none", "null", "unknown"}:
            return text
    pair = str(row.get("pair") or "")
    first = pair.split("->", 1)[0].strip()
    return first or None


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


def row_pnl(row: dict[str, Any]) -> Decimal | None:
    """Return the first signed borrow-token profit value, never a zero placeholder."""
    for path in PNL_PATHS:
        parsed = as_decimal(nested(row, *path.split(".")))
        if parsed is not None:
            return parsed
    return None


def row_review_consistent(row: dict[str, Any]) -> bool:
    """Return whether one attempt binds positive profit to a live verified review."""
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
    if [item for item in evidence.get("blockers") or [] if str(item).strip()]:
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


def row_review_inconsistent_positive(row: dict[str, Any]) -> bool:
    profit = row_pnl(row)
    return profit is not None and profit > 0 and not row_review_consistent(row)


def funnel_counts(
    rows: list[dict[str, Any]],
    *,
    unique_success_count: int | None = None,
    profit_by_symbol: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Count the live-testing funnel for a bounded observation window.

    The funnel is: reviewed attempts -> rows carrying a signed expected
    profit -> rows that broadcast -> successful broadcast tx rows -> unique
    confirmed tx hashes. Success requires ``broadcast=true`` so the counts
    stay comparable with ``summarize_success_rows``.
    """
    expected_profit_count = 0
    broadcast_count = 0
    success_tx_count = 0
    unique_tx: set[str] = set()
    unique_success_with_expected_profit: set[str] = set()
    for row in rows:
        if row_pnl(row) is not None:
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
            if row_pnl(row) is not None:
                unique_success_with_expected_profit.add(tx_key)
    return {
        "reviewedCount": len(rows),
        "withExpectedProfitCount": expected_profit_count,
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


def sum_by_symbol(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, Decimal] = {}
    counts: dict[str, int] = {}
    for item in items:
        profit = as_decimal(item.get("netProfitAmount"))
        if profit is None:
            continue
        symbol = item.get("profitSymbol")
        key = symbol or "unknown"
        totals[key] = totals.get(key, Decimal("0")) + profit
        counts[key] = counts.get(key, 0) + 1
    return [
        {
            "symbol": key,
            "count": counts[key],
            "total": str(totals[key]),
        }
        for key in sorted(totals, key=lambda item: (counts[item], item))
    ]


def fetch_json(url: str, timeout: float = 90.0) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def summarize_success_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        tx_hash = row.get("tx_hash")
        if not tx_hash or row.get("state") != "execution_success" or not row.get("broadcast"):
            continue
        existing = unique.get(tx_hash)
        if existing is None:
            unique[tx_hash] = {"row": row, "priority": 0}
            continue

        def row_score(candidate: dict[str, Any]) -> int:
            score = 0
            cm = nested(candidate, "quote", "live_onchain_evidence", "costModel") or {}
            if cm.get("netProfitAmount") is not None:
                score += 1
            if nested(candidate, "auto_execute", "result", "receipt", "receipt"):
                score += 1
            if candidate.get("final_delta_amount") is not None:
                score += 1
            return score

        if row_score(row) > row_score(existing["row"]):
            unique[tx_hash] = {"row": row, "priority": row_score(row)}

    successes = []
    gas_used = []
    gas_prices = []
    for tx_hash, entry in sorted(unique.items()):
        row = entry["row"]
        receipt = nested(row, "auto_execute", "result", "receipt", "receipt") or {}
        cost_model = nested(row, "quote", "live_onchain_evidence", "costModel") or {}
        auto_execute = row.get("auto_execute") or {}
        amount = (
            auto_execute.get("amount")
            if auto_execute.get("amount") is not None
            else row.get("best_amount")
        )
        gas_used_int = hex_to_int(receipt.get("gasUsed"))
        gas_price_int = hex_to_int(receipt.get("effectiveGasPrice"))
        if gas_used_int is not None:
            gas_used.append(gas_used_int)
        if gas_price_int is not None:
            gas_prices.append(gas_price_int)
        successes.append(
            {
                "txHash": tx_hash,
                "createdAt": row.get("created_at"),
                "observedAt": row.get("observed_at"),
                "pair": row.get("pair"),
                "amount": amount,
                "finalDeltaAmount": row.get("final_delta_amount"),
                "finalSymbol": row.get("final_symbol"),
                "netProfitAmount": cost_model.get("netProfitAmount"),
                "netProfitAmountBps": cost_model.get("netProfitAmountBps"),
                "profitSymbol": row_symbol(row),
                "blockNumber": receipt.get("blockNumber"),
                "blockNumberInt": hex_to_int(receipt.get("blockNumber")),
                "gasUsed": receipt.get("gasUsed"),
                "gasUsedInt": gas_used_int,
                "effectiveGasPrice": receipt.get("effectiveGasPrice"),
                "effectiveGasPriceInt": gas_price_int,
                "status": receipt.get("status"),
            }
        )
    return {
        "uniqueSuccessCount": len(successes),
        "successes": successes,
        "profitBySymbol": sum_by_symbol(successes),
        "gasSummary": {
            "gasUsedMin": min(gas_used) if gas_used else None,
            "gasUsedMax": max(gas_used) if gas_used else None,
            "gasUsedTotal": sum(gas_used) if gas_used else None,
            "effectiveGasPriceMin": min(gas_prices) if gas_prices else None,
            "effectiveGasPriceMax": max(gas_prices) if gas_prices else None,
        },
    }


def summarize_failures(rows: list[dict[str, Any]]) -> dict[str, Any]:
    failed: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for row in rows:
        state = row.get("state")
        broadcast = bool(row.get("broadcast"))
        tx_hash = row.get("tx_hash")
        error = row.get("error")
        error_stack = row.get("errorStack") or []
        blockers: list[str] = []
        live_evidence = nested(row, "quote", "live_onchain_evidence") or {}
        blockers.extend(str(item) for item in live_evidence.get("blockers") or [])
        blockers.extend(str(item) for item in row.get("blocked_reasons") or [])
        if state in ("execution_failed", "submission_failed") or (broadcast and not tx_hash):
            failed.append(
                {
                    "createdAt": row.get("created_at"),
                    "observedAt": row.get("observed_at"),
                    "pair": row.get("pair"),
                    "state": state,
                    "txHash": tx_hash,
                    "error": error,
                    "errorStack": error_stack[:5],
                    "blockers": blockers[:10],
                }
            )
        elif not broadcast and not tx_hash:
            blocked.append(
                {
                    "createdAt": row.get("created_at"),
                    "observedAt": row.get("observed_at"),
                    "pair": row.get("pair"),
                    "state": state,
                    "blockers": blockers[:10],
                }
            )
    return {
        "failedCount": len(failed),
        "blockedCount": len(blocked),
        "failedSample": failed[:10],
        "blockedSample": blocked[:10],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-start", required=True, help="ISO-8601 UTC start")
    parser.add_argument("--window-end", required=True, help="ISO-8601 UTC end")
    parser.add_argument(
        "--store",
        default=str(DEFAULT_STORE),
        help="SQLite store path for attempts.",
    )
    parser.add_argument("--output-dir", default=None, help="evidence directory; default auto timestamp")
    parser.add_argument("--schema-version", default=SCHEMA_VERSION)
    parser.add_argument("--base-url", default="http://127.0.0.1:5001")
    args = parser.parse_args()

    start = parse_iso(args.window_start)
    end = parse_iso(args.window_end)
    if not start <= end:
        parser.error("--window-start must not be after --window-end")

    snapshot_url = (
        args.base_url.rstrip("/")
        + "/api/dex/opportunity-scanner/snapshot?"
        + urllib.parse.urlencode(
            {
                "chain_id": "46630",
                "market_id": "dex-arbitrage-46630-uniswap-v4",
                "protocols": "uniswap_v4",
                "dex_strategy": "closed_loop_routes",
                "summary": "1",
            }
        )
    )
    snapshot = fetch_json(snapshot_url)
    scanner = (snapshot or {}).get("scanner") or {}
    runtime = None
    runtime_snapshot_retry = False
    if snapshot is not None:
        runtime = {
            key: scanner.get(key)
            for key in (
                "running",
                "auto_execute_enabled",
                "last_error",
                "closed_loop_scan_count",
                "closed_loop_review_count",
                "auto_execute_count",
                "auto_broadcast_count",
                "auto_execute_cooldown_seconds",
            )
        }

    attempts_path = Path(args.store)
    rows = read_attempts(attempts_path, start, end)
    success_summary = summarize_success_rows(rows)
    failure_summary = summarize_failures(rows)
    if runtime is None:
        retry = fetch_json(snapshot_url)
        if retry is not None:
            runtime_snapshot_retry = True
            scanner = (retry or {}).get("scanner") or {}
            runtime = {
                key: scanner.get(key)
                for key in (
                    "running",
                    "auto_execute_enabled",
                    "last_error",
                    "closed_loop_scan_count",
                    "closed_loop_review_count",
                    "auto_execute_count",
                    "auto_broadcast_count",
                    "auto_execute_cooldown_seconds",
                )
            }

    if args.output_dir is None:
        stamp = end.strftime("%Y%m%dT%H%M%SZ")
        output_dir = (
            REPO_ROOT
            / "flashloan"
            / "src"
            / "runtime"
            / "evidence"
            / "robinhood-testnet"
            / "auto_scan_verification"
            / stamp
        )
    else:
        output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "schemaVersion": args.schema_version,
        "window": {
            "startUtc": start.isoformat(),
            "endUtc": end.isoformat(),
            "durationSeconds": int((end - start).total_seconds()),
        },
        "source": str(attempts_path.resolve()),
        "attemptsInWindow": len(rows),
        "runtimeSnapshot": runtime,
        "runtimeSnapshotRetry": runtime_snapshot_retry,
        "broadcastGates": env_broadcast_flags(DEFAULT_ENV),
        "success": success_summary,
        "failure": failure_summary,
        "funnel": funnel_counts(
            rows,
            unique_success_count=success_summary["uniqueSuccessCount"],
            profit_by_symbol=success_summary["profitBySymbol"],
        ),
        "t21FieldCounts": count_nested_keys(rows, FIELD_KEYS),
        "reviewConsistency": {
            "reviewConsistentCount": sum(1 for row in rows if row_review_consistent(row)),
            "reviewInconsistentPositiveEstimateCount": sum(
                1 for row in rows if row_review_inconsistent_positive(row)
            ),
            "totalReviewedCount": len(rows),
        },
        "conclusion": (
            f"Observed {int((end - start).total_seconds()) // 60} minutes; "
            f"{success_summary['uniqueSuccessCount']} unique on-chain success tx "
            f"with {failure_summary['failedCount']} failed and "
            f"{failure_summary['blockedCount']} blocked attempts; "
            f"netProductDeltaRaw={count_nested_keys(rows, FIELD_KEYS)['netProductDeltaRaw']}, "
            f"ArbitrageNotProfitable={count_nested_keys(rows, FIELD_KEYS)['ArbitrageNotProfitable']}."
        ),
        "safety": {
            "mainnetBroadcastEnabled": None,
            "testnetBroadcastEnabled": None,
            "writesSqlite": False,
        },
    }
    gates = report["broadcastGates"]
    report["safety"]["mainnetBroadcastEnabled"] = gates[
        "ROBINHOOD_MAINNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST"
    ]
    report["safety"]["testnetBroadcastEnabled"] = gates[
        "ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST"
    ]

    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"evidence={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
