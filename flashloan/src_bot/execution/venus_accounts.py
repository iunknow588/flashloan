from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from web3 import Web3

from execution.venus_abis import VENUS_EVENT_SPECS
from execution.venus_markets import DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR, utc_now


BORROWER_DISCOVERY_EVENTS = ("Borrow",)
BORROWER_EVENTS = ("Borrow", "RepayBorrow", "Mint", "Redeem", "LiquidateBorrow")


def _topic_hex(topic: Any) -> str:
    value = topic.hex() if hasattr(topic, "hex") else str(topic)
    return value if value.startswith("0x") else "0x" + value


def _topic_to_address(topic: Any) -> str:
    raw = _topic_hex(topic)[2:]
    if len(raw) < 40:
        return ""
    try:
        return Web3.to_checksum_address("0x" + raw[-40:])
    except Exception:
        return ""


def venus_event_id(row: dict[str, Any]) -> str:
    return ":".join(
        [
            str(row.get("vtoken") or "").lower(),
            str(row.get("event") or ""),
            str(row.get("transaction_hash") or "").lower(),
            str(int(row.get("log_index") or 0)),
        ]
    )


def load_venus_event_journal(path: str | Path) -> list[dict[str, Any]]:
    raw_path = Path(path)
    if not raw_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
        except Exception:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def write_venus_event_journal(path: str | Path, events: Iterable[dict[str, Any]]) -> Path:
    raw_path = Path(path)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with raw_path.open("a", encoding="utf-8") as handle:
        for row in events:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return raw_path


def dedupe_venus_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in events:
        key = venus_event_id(row)
        if not key:
            continue
        rows[key] = dict(row)
    deduped = list(rows.values())
    deduped.sort(key=lambda item: (int(item.get("block_number") or 0), int(item.get("log_index") or 0), item.get("transaction_hash") or ""))
    return deduped


def find_block_by_timestamp(
    w3: Web3,
    target_timestamp: int,
    *,
    low_block: int = 0,
    high_block: int | None = None,
) -> int:
    latest = int(w3.eth.block_number)
    low = max(0, int(low_block))
    high = latest if high_block is None else min(latest, int(high_block))
    if low >= high:
        return low

    low_ts = int(w3.eth.get_block(low).get("timestamp") or 0)
    high_ts = int(w3.eth.get_block(high).get("timestamp") or 0)
    if target_timestamp <= low_ts:
        return low
    if target_timestamp >= high_ts:
        return high

    while low < high:
        mid = (low + high + 1) // 2
        mid_ts = int(w3.eth.get_block(mid).get("timestamp") or 0)
        if mid_ts <= target_timestamp:
            low = mid
        else:
            high = mid - 1
    return low


def estimate_start_block_by_lookback_days(w3: Web3, days: int) -> int:
    lookback = max(1, int(days or 1))
    latest_block = int(w3.eth.block_number)
    latest_ts = int(w3.eth.get_block(latest_block).get("timestamp") or 0)
    target_ts = int((datetime.fromtimestamp(latest_ts, tz=timezone.utc) - timedelta(days=lookback)).timestamp())
    return find_block_by_timestamp(w3, target_ts, low_block=0, high_block=latest_block)


def _decode_log_data(w3: Web3, types: list[str], data: Any) -> tuple[Any, ...]:
    if not types:
        return ()
    raw = data
    if isinstance(raw, str):
        raw = raw[2:] if raw.startswith("0x") else raw
        raw = bytes.fromhex(raw) if raw else b""
    try:
        return tuple(w3.codec.decode(types, raw))
    except Exception:
        return ()


def venus_event_topic(event_name: str) -> str:
    spec = VENUS_EVENT_SPECS[str(event_name)]
    return str(spec["topic"])


def extract_venus_event_account(w3: Web3, log: dict[str, Any], event_name: str) -> str:
    spec = VENUS_EVENT_SPECS.get(str(event_name))
    if not spec:
        return ""
    topics = list(log.get("topics") or [])
    if len(topics) > 1:
        topic_account = _topic_to_address(topics[1])
        if topic_account:
            return topic_account
    decoded = _decode_log_data(w3, list(spec["types"]), log.get("data") or b"")
    account_index = int(spec["account_index"])
    if len(decoded) <= account_index:
        return ""
    try:
        return Web3.to_checksum_address(str(decoded[account_index]))
    except Exception:
        return ""


def empty_venus_registry(*, from_block: int = 0, to_block: int = 0) -> dict[str, Any]:
    return {
        "network": "bnb",
        "chain_id": 56,
        "protocol": "venus_core",
        "storage_mode": "file_only",
        "generated_at": utc_now(),
        "from_block": int(from_block),
        "to_block": int(to_block),
        "account_count": 0,
        "accounts": [],
    }


def _account_tier(first_block: int, last_block: int, event_count: int, latest_block: int) -> str:
    if event_count >= 5 or latest_block - last_block <= 30_000:
        return "hot"
    if latest_block - last_block <= 300_000:
        return "warm"
    return "cold"


def build_venus_borrower_registry_from_events(events: Iterable[dict[str, Any]], *, latest_block: int = 0) -> dict[str, Any]:
    accounts: dict[str, dict[str, Any]] = {}
    for event in events:
        account = str(event.get("account") or "").strip()
        try:
            checksum = Web3.to_checksum_address(account)
        except Exception:
            continue
        block_number = int(event.get("block_number") or 0)
        event_name = str(event.get("event") or "")
        vtoken = str(event.get("vtoken") or "")
        row = accounts.setdefault(
            checksum,
            {
                "account": checksum,
                "first_block": block_number,
                "last_block": block_number,
                "event_count": 0,
                "event_counts": defaultdict(int),
                "markets_touched": set(),
                "borrow_markets": set(),
                "collateral_markets": set(),
            },
        )
        row["first_block"] = min(int(row["first_block"]), block_number)
        row["last_block"] = max(int(row["last_block"]), block_number)
        row["event_count"] = int(row["event_count"]) + 1
        row["event_counts"][event_name] += 1
        if vtoken:
            row["markets_touched"].add(vtoken)
            if event_name in {"Borrow", "RepayBorrow", "LiquidateBorrow"}:
                row["borrow_markets"].add(vtoken)
            if event_name in {"Mint", "Redeem"}:
                row["collateral_markets"].add(vtoken)
    rows = []
    latest = int(latest_block or max((int(row["last_block"]) for row in accounts.values()), default=0))
    for row in accounts.values():
        serialized = {
            "account": row["account"],
            "first_block": int(row["first_block"]),
            "last_block": int(row["last_block"]),
            "event_count": int(row["event_count"]),
            "event_counts": dict(sorted(row["event_counts"].items())),
            "markets_touched": sorted(row["markets_touched"]),
            "borrow_markets": sorted(row["borrow_markets"]),
            "collateral_markets": sorted(row["collateral_markets"]),
            "tier": _account_tier(int(row["first_block"]), int(row["last_block"]), int(row["event_count"]), latest),
        }
        rows.append(serialized)
    rows.sort(key=lambda item: (item["tier"] != "hot", -int(item["last_block"]), item["account"]))
    return {
        "network": "bnb",
        "chain_id": 56,
        "protocol": "venus_core",
        "storage_mode": "file_only",
        "generated_at": utc_now(),
        "latest_block": latest,
        "account_count": len(rows),
        "accounts": rows,
    }


def validate_venus_borrower_registry(registry: dict[str, Any] | None) -> dict[str, Any]:
    data = registry if isinstance(registry, dict) else {}
    rows = data.get("accounts") if isinstance(data.get("accounts"), list) else []
    valid_accounts: list[str] = []
    invalid_count = 0
    for row in rows:
        value = row.get("account") if isinstance(row, dict) else row
        try:
            account = Web3.to_checksum_address(str(value))
        except Exception:
            invalid_count += 1
            continue
        if account not in valid_accounts:
            valid_accounts.append(account)
    declared_count = int(data.get("account_count") or 0)
    errors: list[str] = []
    warnings: list[str] = []
    if declared_count != len(valid_accounts):
        errors.append("account_count_mismatch")
    if invalid_count:
        errors.append("invalid_account_rows")
    if str(data.get("storage_mode") or "") != "file_only":
        warnings.append("storage_mode_missing_or_not_file_only")
    if not data.get("scan_complete", True):
        warnings.append("registry_scan_incomplete")
    if not data.get("discovery_mode"):
        warnings.append("discovery_mode_missing")
    if str(data.get("event_storage_mode") or "") != "account_only" and not data.get("events_jsonl"):
        warnings.append("events_jsonl_missing")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "account_count": len(valid_accounts),
        "declared_account_count": declared_count,
        "scan_complete": bool(data.get("scan_complete", True)),
        "discovery_mode": data.get("discovery_mode"),
        "storage_mode": data.get("storage_mode"),
    }


def scan_venus_account_events(
    w3: Web3,
    vtoken_addresses: Iterable[str],
    *,
    from_block: int,
    to_block: int | None = None,
    chunk_size: int = 20_000,
    events: Iterable[str] = BORROWER_DISCOVERY_EVENTS,
    limit: int = 0,
    continue_on_error: bool = False,
    adaptive_chunk: bool = False,
    min_chunk_size: int = 10,
    address_batch_size: int = 8,
    newest_first: bool = False,
    request_sleep_seconds: float = 0.0,
    error_retry_attempts: int = 0,
    error_retry_sleep_seconds: float = 0.0,
    stop_event: threading.Event | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    rpc_pool: Iterable[Web3] | None = None,
) -> list[dict[str, Any]]:
    latest_block = int(w3.eth.block_number)
    start = max(0, int(from_block))
    end_limit = latest_block if to_block is None else min(latest_block, int(to_block))
    if start > end_limit:
        return []
    event_names = [name for name in events if name in VENUS_EVENT_SPECS]
    chunk = max(1, min(int(chunk_size or 20_000), 50_000))
    rows: list[dict[str, Any]] = []
    result_limit = max(0, int(limit or 0))
    rpc_clients = list(rpc_pool or [w3])
    if not rpc_clients:
        rpc_clients = [w3]
    rpc_cursor = 0
    vtokens: list[str] = []
    for raw_address in vtoken_addresses:
        try:
            vtoken = Web3.to_checksum_address(str(raw_address))
        except Exception:
            continue
        if vtoken not in vtokens:
            vtokens.append(vtoken)
    if not vtokens:
        return []
    batch_size = max(1, int(address_batch_size or 8))
    vtoken_batches = [vtokens[offset : offset + batch_size] for offset in range(0, len(vtokens), batch_size)]

    def get_logs_for_range(vtoken_batch: list[str], event_name: str, topic: str, left: int, right: int) -> list[dict[str, Any]]:
        nonlocal rpc_cursor
        params = {
            "address": vtoken_batch,
            "fromBlock": left,
            "toBlock": right,
            "topics": [topic],
        }
        failures: list[str] = []
        attempts = max(1, int(error_retry_attempts or 0) + 1)
        for attempt in range(attempts):
            failures = []
            for offset in range(len(rpc_clients)):
                index = (rpc_cursor + offset) % len(rpc_clients)
                client = rpc_clients[index]
                try:
                    logs = list(client.eth.get_logs(params))
                except Exception as exc:
                    failures.append(f"rpc[{index}]: {exc}")
                    continue
                rpc_cursor = index
                sleep_seconds = max(0.0, float(request_sleep_seconds or 0.0))
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
                return logs
            if attempt < attempts - 1:
                retry_sleep = max(0.0, float(error_retry_sleep_seconds or 0.0))
                if progress_callback is not None:
                    progress_callback(
                        {
                            "vtoken": None,
                            "vtoken_count": len(vtoken_batch),
                            "address_batch_size": batch_size,
                            "event": event_name,
                            "from_block": start,
                            "to_block": end_limit,
                            "current_from_block": left,
                            "current_to_block": right,
                            "event_count": len(rows),
                            "error": "all BNB RPCs failed; retrying",
                            "rpc_failures": failures,
                            "retry_attempt": attempt + 1,
                            "retry_after_seconds": retry_sleep,
                        }
                    )
                if retry_sleep > 0:
                    if stop_event is not None and stop_event.wait(retry_sleep):
                        return []
                    if stop_event is None:
                        time.sleep(retry_sleep)

        error = RuntimeError("all BNB RPCs failed: " + " | ".join(failures[-3:]))
        width = right - left + 1
        if adaptive_chunk and width > max(1, int(min_chunk_size or 10)):
            mid = left + width // 2 - 1
            return get_logs_for_range(vtoken_batch, event_name, topic, left, mid) + get_logs_for_range(vtoken_batch, event_name, topic, mid + 1, right)
        if adaptive_chunk and len(vtoken_batch) > 1:
            midpoint = len(vtoken_batch) // 2
            return get_logs_for_range(vtoken_batch[:midpoint], event_name, topic, left, right) + get_logs_for_range(vtoken_batch[midpoint:], event_name, topic, left, right)
        if progress_callback is not None:
            progress_callback(
                {
                    "vtoken": None,
                    "vtoken_count": len(vtoken_batch),
                    "address_batch_size": batch_size,
                    "event": event_name,
                    "from_block": start,
                    "to_block": end_limit,
                    "current_from_block": left,
                    "current_to_block": right,
                    "event_count": len(rows),
                    "error": str(error),
                    "rpc_failures": failures,
                }
            )
        if not continue_on_error:
            raise error
        return []

    for vtoken_batch in vtoken_batches:
        if stop_event is not None and stop_event.is_set():
            break
        for event_name in event_names:
            if stop_event is not None and stop_event.is_set():
                break
            current = end_limit if newest_first else start
            topic = venus_event_topic(event_name)
            while (current >= start if newest_first else current <= end_limit):
                if stop_event is not None and stop_event.is_set():
                    break
                if newest_first:
                    current_from = max(start, current - chunk + 1)
                    current_to = current
                else:
                    current_from = current
                    current_to = min(end_limit, current + chunk - 1)
                logs = get_logs_for_range(vtoken_batch, event_name, topic, current_from, current_to)
                for log in logs:
                    account = extract_venus_event_account(w3, log, event_name)
                    if not account:
                        continue
                    try:
                        vtoken = Web3.to_checksum_address(str(log.get("address") or ""))
                    except Exception:
                        vtoken = vtoken_batch[0] if len(vtoken_batch) == 1 else ""
                    if not vtoken:
                        continue
                    row = {
                        "account": account,
                        "event": event_name,
                        "vtoken": vtoken,
                        "block_number": int(log.get("blockNumber") or current_to),
                        "transaction_hash": _topic_hex(log.get("transactionHash") or ""),
                        "log_index": int(log.get("logIndex") or 0),
                    }
                    rows.append(row)
                    if event_callback is not None:
                        event_callback(row)
                    if result_limit > 0 and len(rows) >= result_limit:
                        return rows
                if progress_callback is not None:
                    progress_callback(
                        {
                            "vtoken": None,
                            "vtoken_count": len(vtoken_batch),
                            "address_batch_size": batch_size,
                            "event": event_name,
                            "from_block": start,
                            "to_block": end_limit,
                            "current_from_block": current_from,
                            "current_to_block": current_to,
                            "event_count": len(rows),
                        }
                    )
                current = current_from - 1 if newest_first else current_to + 1
    return rows


def discover_venus_borrower_accounts(
    w3: Web3,
    vtoken_addresses: Iterable[str],
    *,
    from_block: int,
    to_block: int | None = None,
    chunk_size: int = 20_000,
    limit: int = 0,
    continue_on_error: bool = False,
    adaptive_chunk: bool = False,
    min_chunk_size: int = 10,
    address_batch_size: int = 8,
    newest_first: bool = False,
    request_sleep_seconds: float = 0.0,
    error_retry_attempts: int = 0,
    error_retry_sleep_seconds: float = 0.0,
    stop_event: threading.Event | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    rpc_pool: Iterable[Web3] | None = None,
) -> dict[str, Any]:
    events = scan_venus_account_events(
        w3,
        vtoken_addresses,
        from_block=from_block,
        to_block=to_block,
        chunk_size=chunk_size,
        events=BORROWER_DISCOVERY_EVENTS,
        limit=limit,
        continue_on_error=continue_on_error,
        adaptive_chunk=adaptive_chunk,
        min_chunk_size=min_chunk_size,
        address_batch_size=address_batch_size,
        newest_first=newest_first,
        request_sleep_seconds=request_sleep_seconds,
        error_retry_attempts=error_retry_attempts,
        error_retry_sleep_seconds=error_retry_sleep_seconds,
        stop_event=stop_event,
        progress_callback=progress_callback,
        event_callback=event_callback,
        rpc_pool=rpc_pool,
    )
    registry = build_venus_borrower_registry_from_events(events, latest_block=int(getattr(w3.eth, "block_number", 0) or 0))
    return {
        "network": "bnb",
        "chain_id": 56,
        "protocol": "venus_core",
        "discovery_mode": "borrow_events_only",
        "events": events,
        "event_count": len(events),
        "accounts": [row["account"] for row in registry.get("accounts") or []],
        "account_count": int(registry.get("account_count") or 0),
        "registry": registry,
    }


def write_venus_borrower_registry(registry: dict[str, Any], output_dir: str | Path = DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR) -> dict[str, str]:
    raw_dir = Path(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    json_path = raw_dir / "venus_borrower_registry.json"
    accounts_path = raw_dir / "venus_borrower_accounts.txt"
    json_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    accounts_path.write_text("\n".join(row["account"] for row in registry.get("accounts") or []) + "\n", encoding="utf-8")
    return {"json": str(json_path), "accounts": str(accounts_path)}
