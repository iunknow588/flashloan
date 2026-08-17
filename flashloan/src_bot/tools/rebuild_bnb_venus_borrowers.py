from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from execution.venus_accounts import (
    BORROWER_DISCOVERY_EVENTS,
    BORROWER_EVENTS,
    build_venus_borrower_registry_from_events,
    dedupe_venus_events,
    discover_venus_borrower_accounts,
    estimate_start_block_by_lookback_days,
    load_venus_event_journal,
    scan_venus_account_events,
    venus_event_topic,
    write_venus_borrower_registry,
    write_venus_event_journal,
)
from execution.venus_markets import DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR
from intent_trade.chains.bnb import config as bnb
from intent_trade.chains.bnb.quote import _connect
from tools.audit_bnb_venus_file_state import audit_file_state, write_audit_report


def _load_market_addresses(path: Path) -> list[str]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [row["vtoken_address"] for row in data.get("markets") or [] if row.get("vtoken_address")]


def _load_registry(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _connect_rpc_pool(rpc_urls: list[str]) -> tuple[object, list[object], list[str]]:
    clients: list[object] = []
    connected_urls: list[str] = []
    failures: list[str] = []
    for url in list(dict.fromkeys(rpc_urls)):
        try:
            client, connected_url = _connect([url])
        except Exception as exc:
            failures.append(f"{url}: {exc}")
            continue
        if connected_url in connected_urls:
            continue
        clients.append(client)
        connected_urls.append(connected_url)
    if not clients:
        raise RuntimeError("no usable BNB RPC for historical scan: " + " | ".join(failures[-3:]))
    return clients[0], clients, connected_urls


def _filter_log_capable_rpc_pool(
    rpc_pool: list[Any],
    rpc_urls: list[str],
    *,
    probe_vtoken: str,
    event_name: str = "Borrow",
    probe_blocks: int = 1000,
) -> tuple[list[Any], list[str], list[str]]:
    usable_clients: list[Any] = []
    usable_urls: list[str] = []
    failures: list[str] = []
    topic = venus_event_topic(event_name)
    for client, url in zip(rpc_pool, rpc_urls):
        try:
            probe_block = int(client.eth.block_number)
            from_block = max(0, probe_block - max(1, int(probe_blocks or 1000)) + 1)
            params = {
                "address": [probe_vtoken],
                "fromBlock": from_block,
                "toBlock": probe_block,
                "topics": [topic],
            }
            list(client.eth.get_logs(params))
        except Exception as exc:
            failures.append(f"{url}: {exc}")
            continue
        usable_clients.append(client)
        usable_urls.append(url)
    return usable_clients, usable_urls, failures


def _merge_registries(previous: dict, current: dict) -> dict:
    previous_accounts = previous.get("accounts") if isinstance(previous.get("accounts"), list) else []
    current_accounts = current.get("accounts") if isinstance(current.get("accounts"), list) else []
    by_account = {str(row.get("account") or "").lower(): dict(row) for row in previous_accounts if row.get("account")}
    for row in current_accounts:
        key = str(row.get("account") or "").lower()
        if not key:
            continue
        existing = by_account.get(key)
        if not existing:
            by_account[key] = dict(row)
            continue
        merged = dict(existing)
        merged["first_block"] = min(int(existing.get("first_block") or row.get("first_block") or 0), int(row.get("first_block") or existing.get("first_block") or 0))
        merged["last_block"] = max(int(existing.get("last_block") or row.get("last_block") or 0), int(row.get("last_block") or existing.get("last_block") or 0))
        merged["event_count"] = int(existing.get("event_count") or 0) + int(row.get("event_count") or 0)
        for key_name in ("markets_touched", "borrow_markets", "collateral_markets"):
            merged[key_name] = sorted(set(existing.get(key_name) or []) | set(row.get(key_name) or []))
        event_counts = dict(existing.get("event_counts") or {})
        for event_name, count in (row.get("event_counts") or {}).items():
            event_counts[event_name] = int(event_counts.get(event_name) or 0) + int(count or 0)
        merged["event_counts"] = dict(sorted(event_counts.items()))
        merged["tier"] = row.get("tier") or existing.get("tier")
        by_account[key] = merged
    rows = sorted(by_account.values(), key=lambda item: (-int(item.get("last_block") or 0), item.get("account") or ""))
    merged_registry = {**previous, **current, "accounts": rows, "account_count": len(rows)}
    return merged_registry


def _progress_writer(path: Path | None):
    if path is None:
        return None

    def write_progress(payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return write_progress


def _incremental_start_block(previous_registry: dict[str, Any]) -> int:
    if bool(previous_registry.get("scan_complete", True)):
        return int(previous_registry.get("to_block") or previous_registry.get("latest_block") or 0) + 1
    last_discovered_block = max(
        (int(row.get("last_block") or 0) for row in previous_registry.get("accounts") or [] if isinstance(row, dict)),
        default=0,
    )
    return last_discovered_block + 1


def _write_registry_checkpoint(
    path: Path,
    events: list[dict],
    *,
    previous_registry: dict,
    events_path: Path,
    discovery_events: tuple[str, ...],
    from_block: int,
    to_block: int,
    latest_block: int,
    scan_mode: str,
    complete: bool,
) -> None:
    current = build_venus_borrower_registry_from_events(events, latest_block=latest_block)
    current["from_block"] = int(from_block)
    current["to_block"] = int(to_block)
    current["event_count"] = len(events)
    current["scan_mode"] = scan_mode
    current["discovery_mode"] = "borrow_events_only" if discovery_events == BORROWER_DISCOVERY_EVENTS else "custom_event_audit"
    current["discovery_events"] = list(discovery_events)
    current["events_jsonl"] = str(events_path)
    current["scan_complete"] = bool(complete)
    if previous_registry and scan_mode == "incremental" and not events:
        current = _merge_registries(previous_registry, current)
        current["from_block"] = int(previous_registry.get("from_block") or from_block)
        current["to_block"] = int(to_block)
        current["scan_mode"] = scan_mode
        current["discovery_mode"] = "borrow_events_only" if discovery_events == BORROWER_DISCOVERY_EVENTS else "custom_event_audit"
        current["discovery_events"] = list(discovery_events)
        current["events_jsonl"] = str(events_path)
        current["scan_complete"] = bool(complete)
    write_venus_borrower_registry(current, path.parent)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover BNB Venus borrower accounts from Borrow events.")
    parser.add_argument("--rpc-url", action="append", default=[])
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR)
    parser.add_argument("--market-table", type=Path, default=DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR / "venus_market_static_table.json")
    parser.add_argument("--vtoken", action="append", default=[])
    parser.add_argument("--from-block", type=int, default=None)
    parser.add_argument("--to-block", type=int, default=None)
    parser.add_argument("--lookback-days", type=int, default=None, help="Resolve the start block from the latest chain timestamp.")
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--event", action="append", choices=BORROWER_EVENTS, default=[])
    parser.add_argument("--progress-file", type=Path, default=None)
    parser.add_argument("--events-jsonl", type=Path, default=None, help="Append discovered Venus events here for resume/debugging.")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--adaptive-chunk", action="store_true", default=True)
    parser.add_argument("--no-adaptive-chunk", action="store_false", dest="adaptive_chunk")
    parser.add_argument("--min-chunk-size", type=int, default=10)
    parser.add_argument("--address-batch-size", type=int, default=8)
    parser.add_argument("--request-sleep-seconds", type=float, default=0.0)
    parser.add_argument("--error-retry-attempts", type=int, default=0)
    parser.add_argument("--error-retry-sleep-seconds", type=float, default=0.0)
    parser.add_argument("--newest-first", action="store_true")
    parser.add_argument("--chunk-size", type=int, default=20_000)
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_env_files(__file__)
    args = parse_args(list(argv or sys.argv[1:]))
    vtokens = list(dict.fromkeys(args.vtoken or _load_market_addresses(args.market_table)))
    if not vtokens:
        raise SystemExit("no vToken addresses; pass --vtoken or build venus_market_static_table.json first")
    w3, rpc_pool, rpc_urls = _connect_rpc_pool(args.rpc_url or list(bnb.log_rpc_urls_from_env()))
    rpc_pool, rpc_urls, log_probe_failures = _filter_log_capable_rpc_pool(rpc_pool, rpc_urls, probe_vtoken=vtokens[0])
    if not rpc_pool:
        raise RuntimeError("no BNB RPC supports Venus eth_getLogs probe: " + " | ".join(log_probe_failures[-3:]))
    w3 = rpc_pool[0]
    registry_path = args.output_dir / "venus_borrower_registry.json"
    previous_registry = _load_registry(registry_path)
    default_events_name = "venus_borrower_events_2mo.jsonl" if args.lookback_days is not None else "venus_borrower_events.jsonl"
    events_path = args.events_jsonl or args.output_dir / default_events_name
    if args.progress_file is None:
        args.progress_file = args.output_dir / "venus_borrow_scan_progress.json"
    stored_events = dedupe_venus_events(load_venus_event_journal(events_path))
    from_block = args.from_block
    lookback_days = args.lookback_days
    if from_block is None and lookback_days is not None:
        from_block = estimate_start_block_by_lookback_days(w3, lookback_days)
    if from_block is None and args.incremental:
        from_block = _incremental_start_block(previous_registry)
    if from_block is None:
        raise SystemExit("--from-block, --lookback-days, or --incremental is required")
    to_block = int(args.to_block if args.to_block is not None else w3.eth.block_number)
    event_keys = {":".join([
        str(row.get("vtoken") or "").lower(),
        str(row.get("event") or ""),
        str(row.get("transaction_hash") or "").lower(),
        str(int(row.get("log_index") or 0)),
    ]) for row in stored_events}
    scan_errors: list[dict] = []
    selected_events = tuple(args.event or BORROWER_DISCOVERY_EVENTS)

    def on_event(row: dict) -> None:
        key = ":".join([
            str(row.get("vtoken") or "").lower(),
            str(row.get("event") or ""),
            str(row.get("transaction_hash") or "").lower(),
            str(int(row.get("log_index") or 0)),
        ])
        if key in event_keys:
            return
        event_keys.add(key)
        stored_events.append(dict(row))
        write_venus_event_journal(events_path, [row])

    progress_callback = _progress_writer(args.progress_file)

    def on_progress(payload: dict) -> None:
        if payload.get("error"):
            scan_errors.append(dict(payload))
        if progress_callback is not None:
            payload = {**payload, "events_jsonl": str(events_path), "persisted_event_count": len(stored_events)}
            progress_callback(payload)
        if not payload.get("error"):
            _write_registry_checkpoint(
                registry_path,
                dedupe_venus_events(stored_events),
                previous_registry=previous_registry,
                events_path=events_path,
                discovery_events=selected_events,
                from_block=int(from_block),
                to_block=to_block,
                latest_block=int(w3.eth.block_number),
                scan_mode="incremental" if args.incremental else "historical_backfill",
                complete=False,
            )

    if selected_events == BORROWER_DISCOVERY_EVENTS:
        discovery = discover_venus_borrower_accounts(
            w3,
            vtokens,
            from_block=from_block,
            to_block=args.to_block,
            chunk_size=args.chunk_size,
            limit=args.limit,
            continue_on_error=args.continue_on_error,
            adaptive_chunk=args.adaptive_chunk,
            min_chunk_size=args.min_chunk_size,
            address_batch_size=args.address_batch_size,
            newest_first=args.newest_first,
            request_sleep_seconds=args.request_sleep_seconds,
            error_retry_attempts=args.error_retry_attempts,
            error_retry_sleep_seconds=args.error_retry_sleep_seconds,
            progress_callback=on_progress,
            event_callback=on_event,
            rpc_pool=rpc_pool,
        )
        events = list(discovery.get("events") or [])
    else:
        events = scan_venus_account_events(
            w3,
            vtokens,
            from_block=from_block,
            to_block=args.to_block,
            chunk_size=args.chunk_size,
            events=selected_events,
            limit=args.limit,
            continue_on_error=args.continue_on_error,
            adaptive_chunk=args.adaptive_chunk,
            min_chunk_size=args.min_chunk_size,
            address_batch_size=args.address_batch_size,
            newest_first=args.newest_first,
            request_sleep_seconds=args.request_sleep_seconds,
            error_retry_attempts=args.error_retry_attempts,
            error_retry_sleep_seconds=args.error_retry_sleep_seconds,
            progress_callback=on_progress,
            event_callback=on_event,
            rpc_pool=rpc_pool,
        )
    all_events = dedupe_venus_events(stored_events + events)
    registry = build_venus_borrower_registry_from_events(all_events, latest_block=int(w3.eth.block_number))
    registry["from_block"] = int(from_block)
    registry["to_block"] = to_block
    registry["event_count"] = len(all_events)
    registry["scan_mode"] = "incremental" if args.incremental else "historical_backfill"
    registry["discovery_mode"] = "borrow_events_only" if selected_events == BORROWER_DISCOVERY_EVENTS else "custom_event_audit"
    registry["discovery_events"] = list(selected_events)
    registry["scan_complete"] = not scan_errors
    registry["scan_errors"] = scan_errors[-100:]
    registry["events_jsonl"] = str(events_path)
    registry["rpc_urls"] = rpc_urls
    registry["log_rpc_probe_failures"] = log_probe_failures[-20:]
    if lookback_days is not None:
        registry["lookback_days"] = int(lookback_days)
    if args.incremental and previous_registry and not stored_events:
        registry = _merge_registries(previous_registry, registry)
        registry["from_block"] = int(previous_registry.get("from_block") or from_block)
        registry["to_block"] = to_block
        registry["scan_mode"] = "incremental"
        registry["scan_complete"] = not scan_errors
    paths = write_venus_borrower_registry(registry, args.output_dir)
    audit_result = audit_file_state(args.output_dir)
    audit_paths = write_audit_report(args.output_dir, audit_result)
    scan_complete = not scan_errors
    print(
        json.dumps(
            {
                "ok": scan_complete,
                "event_count": len(all_events),
                "new_event_count": len(events),
                "account_count": registry["account_count"],
                "scan_complete": scan_complete,
                "from_block": int(from_block),
                "to_block": to_block,
                "lookback_days": lookback_days,
                "rpc_urls": rpc_urls,
                "paths": {**paths, "events_jsonl": str(events_path)},
                "audit": {"status": audit_result.get("status"), "paths": audit_paths},
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if scan_complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
