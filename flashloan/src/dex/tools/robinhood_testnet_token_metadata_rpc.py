"""Robinhood testnet ERC20 metadata collection over JSON-RPC.

This module owns the live RPC reading path used by
``resolve_robinhood_testnet_token_metadata``.  It is kept separate from the
SQLite write path so each module stays below the architecture line budget and
so the network-facing code can be audited independently of the persistence
code.

Only ``eth_call`` reads are performed against the configured chain RPC.  No
transaction is ever signed or broadcast from this module.
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from eth_abi import decode
import requests
import urllib3
from web3 import Web3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.db.token_symbols import is_unresolved_placeholder_symbol  # noqa: E402


DEFAULT_NETWORK = "robinhood-testnet"
DEFAULT_CHAIN_ID = 46630
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
BATCH_CALL_SIZE = 8
HTTP_RETRY_COUNT = 3

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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _chain_rpc_urls(network: str | None = None) -> list[str]:
    resolved_network = str(network or DEFAULT_NETWORK).strip().lower()
    path = SRC_ROOT.parent / "chains" / resolved_network / "chain.json"
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    rpc = payload.get("rpc") or {}
    urls = [_text(rpc.get("primary"))]
    urls.extend(_text(value) for value in rpc.get("fallbacks") or [])
    urls = [url for url in urls if url]
    if not urls:
        raise RuntimeError(f"chain.json has no rpc endpoints: {path}")
    return urls


def _chain_rpc_url(network: str | None = None) -> str:
    return _chain_rpc_urls(network)[0]


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


def _decimals_int(value: Any) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
