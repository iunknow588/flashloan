from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from execution.venus_accounts import (
    BORROWER_DISCOVERY_EVENTS,
    build_venus_borrower_registry_from_events,
    dedupe_venus_events,
    load_venus_event_journal,
    venus_event_topic,
    write_venus_borrower_registry,
    write_venus_event_journal,
)
from execution.venus_markets import DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR
from tools.audit_bnb_venus_file_state import audit_file_state, write_audit_report


DEFAULT_DUNE_API_BASE_URL = "https://api.dune.com/api/v1"


def _load_market_addresses(path: Path) -> list[str]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [str(row["vtoken_address"]) for row in data.get("markets") or [] if row.get("vtoken_address")]


def _dune_request(
    base_url: str,
    api_key: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    body = None
    headers = {"X-DUNE-API-KEY": api_key}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=max(10.0, float(timeout_seconds or 180.0))) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Dune API HTTP {exc.code}: {raw[:500]}") from exc
    return json.loads(raw) if raw else {}


def _execute_sql(base_url: str, api_key: str, sql: str, *, performance: str = "medium") -> str:
    response = _dune_request(base_url, api_key, "POST", "/sql/execute", {"sql": sql, "performance": performance})
    execution_id = str(response.get("execution_id") or "")
    if not execution_id:
        raise RuntimeError(f"Dune SQL execution did not return execution_id: {response}")
    return execution_id


def _wait_for_execution(base_url: str, api_key: str, execution_id: str, *, poll_seconds: float, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + max(1.0, float(timeout_seconds or 1.0))
    while True:
        status = _dune_request(base_url, api_key, "GET", f"/execution/{execution_id}/status")
        state = str(status.get("state") or "")
        if state in {"QUERY_STATE_COMPLETED", "QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"}:
            return status
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Dune execution timed out: {execution_id} state={state}")
        time.sleep(max(1.0, float(poll_seconds or 1.0)))


def _fetch_execution_rows(
    base_url: str,
    api_key: str,
    execution_id: str,
    *,
    page_size: int,
    max_rows: int = 0,
    request_timeout_seconds: float = 180.0,
    request_retries: int = 3,
    progress_callback: Any = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    limit = max(1, min(int(page_size or 1000), 5000))
    while True:
        requested_limit = limit
        if max_rows > 0:
            requested_limit = min(limit, max_rows - len(rows))
            if requested_limit <= 0:
                break
        query = urllib.parse.urlencode({"limit": requested_limit, "offset": offset})
        response = None
        failures: list[str] = []
        for attempt in range(max(1, int(request_retries or 0) + 1)):
            try:
                response = _dune_request(
                    base_url,
                    api_key,
                    "GET",
                    f"/execution/{execution_id}/results?{query}",
                    timeout_seconds=request_timeout_seconds,
                )
                break
            except Exception as exc:
                failures.append(str(exc))
                if attempt >= max(1, int(request_retries or 0)):
                    raise
                if progress_callback is not None:
                    progress_callback({"status": "fetch_retrying", "offset": offset, "limit": requested_limit, "error": str(exc)})
                time.sleep(min(30.0, 2.0 * (attempt + 1)))
        if response is None:
            raise RuntimeError("Dune results response is empty: " + " | ".join(failures[-3:]))
        page_rows = list(((response.get("result") or {}).get("rows") or []))
        rows.extend(dict(row) for row in page_rows)
        if progress_callback is not None:
            progress_callback({"status": "fetching_results", "offset": offset, "limit": requested_limit, "fetched_rows": len(rows), "last_page_rows": len(page_rows)})
        if len(page_rows) < requested_limit:
            break
        offset += requested_limit
    return rows


def _sql_address(address: str) -> str:
    value = address.strip()
    if not value.startswith("0x"):
        raise ValueError(f"invalid address: {address}")
    return value.lower()


def build_dune_borrow_logs_sql(vtokens: list[str], *, lookback_days: int, event_topic: str | None = None, row_limit: int = 0) -> str:
    if not vtokens:
        raise ValueError("vtokens is required")
    topic = _sql_address(event_topic or venus_event_topic("Borrow"))
    addresses = ", ".join(_sql_address(address) for address in vtokens)
    limit_clause = f"\nLIMIT {int(row_limit)}" if int(row_limit or 0) > 0 else ""
    return f"""SELECT
  block_time,
  block_number,
  contract_address,
  tx_hash,
  index AS log_index,
  data
FROM bnb.logs
WHERE contract_address IN ({addresses})
  AND topic0 = {topic}
  AND block_time >= NOW() - INTERVAL '{int(lookback_days)}' DAY
ORDER BY block_number ASC, index ASC{limit_clause}"""


def build_dune_borrower_accounts_sql(vtokens: list[str], *, lookback_days: int, event_topic: str | None = None, row_limit: int = 0) -> str:
    if not vtokens:
        raise ValueError("vtokens is required")
    topic = _sql_address(event_topic or venus_event_topic("Borrow"))
    addresses = ", ".join(_sql_address(address) for address in vtokens)
    limit_clause = f"\nLIMIT {int(row_limit)}" if int(row_limit or 0) > 0 else ""
    return f"""SELECT
  varbinary_substring(data, 13, 20) AS account,
  COUNT(*) AS borrow_count,
  MIN(block_number) AS first_block,
  MAX(block_number) AS last_block,
  MIN(block_time) AS first_block_time,
  MAX(block_time) AS last_block_time,
  COUNT(DISTINCT contract_address) AS borrow_market_count
FROM bnb.logs
WHERE contract_address IN ({addresses})
  AND topic0 = {topic}
  AND block_time >= NOW() - INTERVAL '{int(lookback_days)}' DAY
GROUP BY 1
ORDER BY last_block DESC{limit_clause}"""


def _borrower_from_data(data: Any) -> str:
    text = str(data or "")
    if text.startswith("0x"):
        text = text[2:]
    if len(text) < 64:
        return ""
    raw_address = "0x" + text[:64][-40:]
    try:
        from web3 import Web3

        return Web3.to_checksum_address(raw_address)
    except Exception:
        return raw_address.lower()


def dune_rows_to_venus_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in rows:
        account = _borrower_from_data(row.get("data"))
        if not account:
            continue
        events.append(
            {
                "account": account,
                "event": "Borrow",
                "vtoken": str(row.get("contract_address") or ""),
                "block_number": int(row.get("block_number") or 0),
                "transaction_hash": str(row.get("tx_hash") or ""),
                "log_index": int(row.get("log_index") or 0),
                "source": "dune_bnb_logs",
                "block_time": row.get("block_time"),
            }
        )
    return events


def dune_account_rows_to_registry(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from web3 import Web3

    valid_rows: list[dict[str, Any]] = []
    latest_block = max((int(row.get("last_block") or 0) for row in rows), default=0)
    for row in rows:
        try:
            account = Web3.to_checksum_address(str(row.get("account") or ""))
        except Exception:
            continue
        first_block = int(row.get("first_block") or 0)
        last_block = int(row.get("last_block") or first_block)
        borrow_count = int(row.get("borrow_count") or 0)
        if borrow_count >= 5 or latest_block - last_block <= 30_000:
            tier = "hot"
        elif latest_block - last_block <= 300_000:
            tier = "warm"
        else:
            tier = "cold"
        valid_rows.append(
            {
                "account": account,
                "first_block": first_block,
                "last_block": last_block,
                "event_count": borrow_count,
                "event_counts": {"Borrow": borrow_count},
                "markets_touched": [],
                "borrow_markets": [],
                "collateral_markets": [],
                "borrow_market_count": int(row.get("borrow_market_count") or 0),
                "first_block_time": row.get("first_block_time"),
                "last_block_time": row.get("last_block_time"),
                "tier": tier,
            }
        )
    valid_rows.sort(key=lambda item: (item["tier"] != "hot", -int(item["last_block"]), item["account"]))
    return {
        "network": "bnb",
        "chain_id": 56,
        "protocol": "venus_core",
        "storage_mode": "file_only",
        "event_storage_mode": "account_only",
        "latest_block": latest_block,
        "account_count": len(valid_rows),
        "accounts": valid_rows,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import BNB Venus Borrow accounts from Dune indexed bnb.logs.")
    parser.add_argument("--api-key", default=os.getenv("DUNE_API_KEY", ""))
    parser.add_argument("--api-base-url", default=os.getenv("DUNE_API_BASE_URL", DEFAULT_DUNE_API_BASE_URL))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR)
    parser.add_argument("--market-table", type=Path, default=DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR / "venus_market_static_table.json")
    parser.add_argument("--vtoken", action="append", default=[])
    parser.add_argument("--lookback-days", type=int, default=60)
    parser.add_argument("--events-jsonl", type=Path, default=DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR / "venus_borrower_events_2mo.jsonl")
    parser.add_argument("--progress-file", type=Path, default=DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR / "venus_dune_borrow_scan_progress.json")
    parser.add_argument("--mode", choices=("accounts", "logs"), default="accounts", help="Use accounts to fetch distinct borrowers only; logs fetches every Borrow log.")
    parser.add_argument("--performance", default="medium")
    parser.add_argument("--execution-id", default="", help="Reuse an already completed Dune execution instead of submitting SQL.")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--request-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--request-retries", type=int, default=3)
    parser.add_argument("--page-size", type=int, default=250)
    parser.add_argument("--max-rows", type=int, default=0)
    parser.add_argument("--sql-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_env_files(__file__)
    args = parse_args(list(argv or sys.argv[1:]))
    api_key = args.api_key or os.getenv("DUNE_API_KEY", "")
    if not api_key:
        raise SystemExit("DUNE_API_KEY is required")
    vtokens = list(dict.fromkeys(args.vtoken or _load_market_addresses(args.market_table)))
    if not vtokens:
        raise SystemExit("no vToken addresses; pass --vtoken or build venus_market_static_table.json first")
    sql = (
        build_dune_borrower_accounts_sql(vtokens, lookback_days=args.lookback_days, row_limit=args.max_rows)
        if args.mode == "accounts"
        else build_dune_borrow_logs_sql(vtokens, lookback_days=args.lookback_days, row_limit=args.max_rows)
    )
    if args.sql_only:
        print(sql)
        return 0

    args.progress_file.parent.mkdir(parents=True, exist_ok=True)
    args.progress_file.write_text(
        json.dumps(
            {
                "status": "submitted",
                "source": "dune_bnb_logs",
                "mode": args.mode,
                "lookback_days": int(args.lookback_days),
                "vtoken_count": len(vtokens),
                "events_jsonl": str(args.events_jsonl),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    execution_id = str(args.execution_id or "")
    if not execution_id:
        execution_id = _execute_sql(args.api_base_url, api_key, sql, performance=args.performance)
    args.progress_file.write_text(
        json.dumps(
            {
                "status": "executing",
                "source": "dune_bnb_logs",
                "mode": args.mode,
                "execution_id": execution_id,
                "lookback_days": int(args.lookback_days),
                "vtoken_count": len(vtokens),
                "events_jsonl": str(args.events_jsonl),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    status = _wait_for_execution(args.api_base_url, api_key, execution_id, poll_seconds=args.poll_seconds, timeout_seconds=args.timeout_seconds)
    if str(status.get("state")) != "QUERY_STATE_COMPLETED":
        args.progress_file.write_text(json.dumps({"status": "failed", "execution_id": execution_id, "dune_status": status}, ensure_ascii=False, indent=2), encoding="utf-8")
        raise RuntimeError(f"Dune query failed: {status}")

    def write_progress(payload: dict[str, Any]) -> None:
        args.progress_file.write_text(
            json.dumps(
                {
                    "source": "dune_bnb_logs",
                    "mode": args.mode,
                    "execution_id": execution_id,
                    "lookback_days": int(args.lookback_days),
                    "vtoken_count": len(vtokens),
                    "events_jsonl": str(args.events_jsonl),
                    **payload,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    rows = _fetch_execution_rows(
        args.api_base_url,
        api_key,
        execution_id,
        page_size=args.page_size,
        max_rows=args.max_rows,
        request_timeout_seconds=args.request_timeout_seconds,
        request_retries=args.request_retries,
        progress_callback=write_progress,
    )
    if args.mode == "accounts":
        registry = dune_account_rows_to_registry(rows)
        registry["from_source"] = "dune_bnb_logs_distinct_borrowers"
        registry["scan_mode"] = "dune_indexed_account_backfill"
        registry["discovery_mode"] = "borrow_events_account_only"
        registry["discovery_events"] = list(BORROWER_DISCOVERY_EVENTS)
        registry["scan_complete"] = True
        registry["event_count"] = sum(int(row.get("event_count") or 0) for row in registry.get("accounts") or [])
        registry["dune_execution_id"] = execution_id
        registry["lookback_days"] = int(args.lookback_days)
        paths = write_venus_borrower_registry(registry, args.output_dir)
        audit_result = audit_file_state(args.output_dir)
        audit_paths = write_audit_report(args.output_dir, audit_result)
        summary = {
            "ok": audit_result.get("ok", False),
            "source": "dune_bnb_logs_distinct_borrowers",
            "mode": args.mode,
            "execution_id": execution_id,
            "row_count": len(rows),
            "event_count": registry["event_count"],
            "account_count": registry["account_count"],
            "paths": {**paths, "progress": str(args.progress_file)},
            "audit": {"status": audit_result.get("status"), "paths": audit_paths},
        }
        args.progress_file.write_text(json.dumps({"status": "completed", **summary}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if audit_result.get("ok", False) else 2

    new_events = dune_rows_to_venus_events(rows)
    stored_events = dedupe_venus_events(load_venus_event_journal(args.events_jsonl))
    existing_ids = {
        ":".join(
            [
                str(row.get("vtoken") or "").lower(),
                str(row.get("event") or ""),
                str(row.get("transaction_hash") or "").lower(),
                str(int(row.get("log_index") or 0)),
            ]
        )
        for row in stored_events
    }
    append_events = []
    for event in new_events:
        key = ":".join(
            [
                str(event.get("vtoken") or "").lower(),
                str(event.get("event") or ""),
                str(event.get("transaction_hash") or "").lower(),
                str(int(event.get("log_index") or 0)),
            ]
        )
        if key not in existing_ids:
            existing_ids.add(key)
            append_events.append(event)
    if append_events:
        write_venus_event_journal(args.events_jsonl, append_events)

    all_events = dedupe_venus_events(stored_events + new_events)
    registry = build_venus_borrower_registry_from_events(all_events)
    registry["from_source"] = "dune_bnb_logs"
    registry["scan_mode"] = "dune_indexed_backfill"
    registry["discovery_mode"] = "borrow_events_only"
    registry["discovery_events"] = list(BORROWER_DISCOVERY_EVENTS)
    registry["scan_complete"] = True
    registry["event_count"] = len(all_events)
    registry["events_jsonl"] = str(args.events_jsonl)
    registry["dune_execution_id"] = execution_id
    registry["lookback_days"] = int(args.lookback_days)
    paths = write_venus_borrower_registry(registry, args.output_dir)
    audit_result = audit_file_state(args.output_dir)
    audit_paths = write_audit_report(args.output_dir, audit_result)
    summary = {
        "ok": True,
        "source": "dune_bnb_logs",
        "execution_id": execution_id,
        "row_count": len(rows),
        "new_event_count": len(append_events),
        "event_count": len(all_events),
        "account_count": registry["account_count"],
        "paths": {**paths, "events_jsonl": str(args.events_jsonl), "progress": str(args.progress_file)},
        "audit": {"status": audit_result.get("status"), "paths": audit_paths},
    }
    args.progress_file.write_text(json.dumps({"status": "completed", **summary}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
