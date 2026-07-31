from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Callable, Iterable, Optional

from web3 import Web3

from execution.liquidation_abis import BORROW_EVENT_TOPIC


def load_account_addresses(path: str | Path) -> list[str]:
    raw_path = Path(path)
    if not raw_path.exists():
        return []
    if raw_path.suffix.lower() == ".json":
        data = json.loads(raw_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("accounts") or data.get("addresses") or []
        items = data if isinstance(data, list) else []
    else:
        items = [line.strip() for line in raw_path.read_text(encoding="utf-8").splitlines()]
    addresses: list[str] = []
    for item in items:
        if not item:
            continue
        try:
            checksum = Web3.to_checksum_address(str(item))
        except ValueError:
            continue
        if checksum not in addresses:
            addresses.append(checksum)
    return addresses


def write_account_addresses(path: str | Path, addresses: Iterable[str]) -> list[str]:
    raw_path = Path(path)
    unique_addresses = []
    for item in addresses:
        try:
            checksum = Web3.to_checksum_address(str(item))
        except ValueError:
            continue
        if checksum not in unique_addresses:
            unique_addresses.append(checksum)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text("\n".join(unique_addresses) + ("\n" if unique_addresses else ""), encoding="utf-8")
    return unique_addresses


def topic_to_address(topic) -> str:
    raw = topic.hex() if hasattr(topic, "hex") else str(topic)
    raw = raw[2:] if raw.startswith("0x") else raw
    if len(raw) < 40:
        return ""
    return Web3.to_checksum_address("0x" + raw[-40:])


def discover_borrower_addresses(
    rpc_url: str,
    pool_address: str,
    from_block: int,
    to_block: Optional[int] = None,
    chunk_size: int = 50_000,
    limit: Optional[int] = 5000,
    event_topic: str | None = None,
    web3_class=Web3,
    stop_event: threading.Event | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> list[str]:
    w3 = web3_class(web3_class.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
    latest_block = int(w3.eth.block_number)
    raw_start_block = int(from_block)
    start_block = max(0, latest_block + raw_start_block) if raw_start_block < 0 else max(0, raw_start_block)
    raw_end_block = latest_block if to_block is None else int(to_block)
    end_limit = max(0, latest_block + raw_end_block) if raw_end_block < 0 else min(latest_block, raw_end_block)
    if start_block > end_limit:
        return []
    chunk = max(1, min(int(chunk_size), 50_000))
    unique_addresses: list[str] = []
    result_limit = max(0, int(limit or 0))
    current = start_block
    while current <= end_limit:
        if stop_event is not None and stop_event.is_set():
            break
        end_block = min(end_limit, current + chunk - 1)
        logs = w3.eth.get_logs(
            {
                "address": web3_class.to_checksum_address(pool_address),
                "fromBlock": current,
                "toBlock": end_block,
                "topics": [event_topic or BORROW_EVENT_TOPIC],
            }
        )
        for log in logs:
            topics = log.get("topics") or []
            if len(topics) < 3:
                continue
            borrower = topic_to_address(topics[2])
            if borrower and borrower not in unique_addresses and (result_limit <= 0 or len(unique_addresses) < result_limit):
                unique_addresses.append(borrower)
        if progress_callback is not None:
            progress_callback(
                {
                    "from_block": start_block,
                    "to_block": end_limit,
                    "current_from_block": current,
                    "current_to_block": end_block,
                    "discovered_count": len(unique_addresses),
                    "stopped": bool(stop_event is not None and stop_event.is_set()),
                }
            )
        current = end_block + 1
    return unique_addresses
