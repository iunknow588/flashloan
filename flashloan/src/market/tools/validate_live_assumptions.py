from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.market_config import liquidation_market_config
from core.validation.cow_settlement import summarize_cow_attempts
from core.validation.data_loader import load_records
from core.validation.db_contention import run_live_contention
from core.validation.liquidation_profit import calculate_liquidation_profit, profit_input_from_event
from core.validation.rpc_probe import probe_rpc_urls
from core.validation.scan_windows import summarize_opportunity_windows
from core.db.storage_common import database_url as storage_database_url
from core.db.storage_cow_execution import (
    load_cow_execution_attempt_detail,
    load_recent_cow_execution_attempt_validation_rows,
)


def _default_path(*parts: str) -> Path:
    return SRC_ROOT.joinpath(*parts)


def _load_cow_execution_rows(source: Path, limit: int) -> list[dict[str, Any]]:
    if source.suffix.lower() not in {".sqlite3", ".sqlite", ".db"}:
        raise ValueError(
            "live assumption validation requires the SQLite store; "
            f"legacy JSONL is no longer an online source: {source}"
        )
    rows = load_recent_cow_execution_attempt_validation_rows(str(source), limit=limit)
    return _with_payload_fallback(str(source), rows)


def _summary_needs_full_payload(row: dict[str, Any]) -> bool:
    state = str(row.get("state") or row.get("status") or "").strip().lower()
    category = str(
        row.get("review_category")
        or row.get("category")
        or ""
    ).strip().lower()
    lifecycle_states = {
        "submitted",
        "submitted_success",
        "submission_success",
        "submitted_failed",
        "settled",
        "settlement_success",
        "confirmed_success",
        "execution_success",
        "execution_failed",
        "success",
        "failed",
    }
    lifecycle_category = category in {"execution_success", "execution_failed"}
    return (
        lifecycle_category
        or state in lifecycle_states
        or bool(row.get("tx_hash"))
        or (not str(row.get("state") or "").strip() and not str(row.get("status") or "").strip())
    )


def _with_payload_fallback(database_url: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fallback_ids = [
        int(row["id"])
        for row in rows
        if isinstance(row.get("id"), int) and _summary_needs_full_payload(row)
    ]
    if not fallback_ids:
        return rows
    full_by_id: dict[int, dict[str, Any]] = {}
    for attempt_id in fallback_ids:
        detail = load_cow_execution_attempt_detail(database_url, attempt_id)
        if isinstance(detail, dict):
            full_by_id[attempt_id] = detail
    return [full_by_id.get(int(row.get("id")), row) for row in rows]


def build_validation_report(
    *,
    liquidation_path: Path,
    cow_path: Path,
    scan_path: Path,
    live_rpc_probe: bool = False,
    live_db_contention: bool = False,
    db_workers: int = 4,
) -> dict[str, Any]:
    market = liquidation_market_config()
    liquidation_rows = load_records(liquidation_path)
    cow_rows = _load_cow_execution_rows(cow_path, limit=5000)
    scan_rows = load_records(scan_path)
    profit_rows = [calculate_liquidation_profit(profit_input_from_event(row)) for row in liquidation_rows]
    calculated = [row for row in profit_rows if row.get("status") == "calculated"]
    profit_summary = {
        "status": "calculated" if calculated else ("no_data" if not liquidation_rows else "missing_price_inputs"),
        "event_count": len(liquidation_rows),
        "calculated_count": len(calculated),
        "positive_count": sum(1 for row in calculated if (row.get("net_profit_usd") or 0) > 0),
        "average_net_profit_usd": _average([row.get("net_profit_usd") for row in calculated]),
        "p50_net_profit_usd": _percentile([row.get("net_profit_usd") for row in calculated], 0.5),
        "p90_net_profit_usd": _percentile([row.get("net_profit_usd") for row in calculated], 0.9),
        "missing_input_counts": _missing_inputs(profit_rows),
    }
    rpc = {"status": "not_probed", "configured_count": len(market.rpc_urls), "urls": list(market.rpc_urls)}
    if live_rpc_probe:
        rpc = {"configured_count": len(market.rpc_urls), **probe_rpc_urls(list(market.rpc_urls))}
    database_url = storage_database_url()
    database = {
        "local_store_path": str(database_url or ""),
        "transaction_boundary": "db_connection context commits on normal exit and rolls back on exception",
        "live_lock_benchmark": "not_run",
    }
    if live_db_contention and database_url:
        try:
            database["live_lock_benchmark"] = run_live_contention(database_url, workers=db_workers)
        except Exception as exc:
            database["live_lock_benchmark"] = {"status": "failed", "error": str(exc)[:240]}
    elif live_db_contention:
        database["live_lock_benchmark"] = {"status": "not_configured", "reason": "local KV store unavailable"}
    return {
        "schema_version": 1,
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "market": market.as_dict(),
        "inputs": {
            "liquidation_path": str(liquidation_path),
            "cow_path": str(cow_path),
            "scan_path": str(scan_path),
        },
        "liquidation_profit": profit_summary,
        "cow_settlement": summarize_cow_attempts(cow_rows),
        "scan_windows": summarize_opportunity_windows(scan_rows),
        "rpc": rpc,
        "database": database,
    }


def _average(values: list[Any]) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    return sum(numbers) / len(numbers) if numbers else None


def _percentile(values: list[Any], q: float) -> float | None:
    numbers = sorted(float(value) for value in values if value is not None)
    if not numbers:
        return None
    return numbers[min(len(numbers) - 1, int(round((len(numbers) - 1) * q)))]


def _resolve_input_paths(liquidation: Path, cow: Path, scan: Path) -> tuple[Path, Path, Path]:
    """Resolve CLI paths against the current working directory."""
    return tuple(
        Path(path).expanduser().resolve()
        for path in (liquidation, cow, scan)
    )  # type: ignore[return-value]


def _missing_inputs(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for name in row.get("missing_inputs") or []:
            counts[name] = counts.get(name, 0) + 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate liquidation, CoW, RPC, scan-window, and DB assumptions.")
    parser.add_argument("--liquidation", type=Path, default=_default_path("runtime", "logs", "liquidation_events.jsonl"))
    parser.add_argument(
        "--cow",
        type=Path,
        default=SRC_ROOT.parent / "db" / "local_store.sqlite3",
        help="Attempt source: SQLite store by default, or a legacy JSONL directory.",
    )
    parser.add_argument("--scan", type=Path, default=_default_path("runtime", "logs", "liquidation_health_scans.jsonl"))
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--live-rpc-probe", action="store_true")
    parser.add_argument("--live-db-contention", action="store_true")
    parser.add_argument("--db-workers", type=int, default=4)
    args = parser.parse_args()
    args.liquidation, args.cow, args.scan = _resolve_input_paths(
        args.liquidation,
        args.cow,
        args.scan,
    )
    report = build_validation_report(
        liquidation_path=args.liquidation,
        cow_path=args.cow,
        scan_path=args.scan,
        live_rpc_probe=args.live_rpc_probe,
        live_db_contention=args.live_db_contention,
        db_workers=args.db_workers,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
