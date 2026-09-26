from __future__ import annotations

import json
import os
from typing import Any
from urllib import request as urllib_request

from web3 import Web3

from core.chain_data_registry import chain_data_by_chain_id
from dex.intent_trade.chains.robinhood import config as robinhood
from dex.intent_trade.chains.robinhood.shared import ROBINHOOD_TESTNET_CHAIN_ID


def fetch_dex_tx_receipt(*, chain_id: int, tx_hash: str) -> dict[str, Any]:
    """Fetch a DEX transaction receipt and merge chain metadata for review display."""
    tx_hash = str(tx_hash or "").strip()
    if not tx_hash:
        return {"ok": False, "error": "tx_hash_required", "detail": "tx_hash is required"}
    row = chain_data_by_chain_id(chain_id) or {}
    rpc_urls = [str(url).strip() for url in row.get("rpc_urls") or [] if str(url).strip()]
    if not rpc_urls:
        rpc_url = str(row.get("rpc_url") or "").strip()
        if rpc_url:
            rpc_urls = [rpc_url]
    errors: list[str] = []
    for rpc_url in rpc_urls:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
            receipt = w3.eth.get_transaction_receipt(tx_hash)
            transaction = None
            if receipt is None:
                transaction = w3.eth.get_transaction(tx_hash)
            return {
                "ok": True,
                "chain_id": int(chain_id),
                "network": row.get("network"),
                "explorer_url": row.get("explorer_url"),
                "explorer_tx_url": (
                    f"{str(row.get('explorer_url') or '').rstrip('/')}/tx/{tx_hash}"
                    if row.get("explorer_url")
                    else None
                ),
                "tx_hash": tx_hash,
                "rpc_url": rpc_url,
                "status": "confirmed" if receipt and int(receipt.get("status") or 0) == 1 else ("failed" if receipt else "pending"),
                "receipt": {
                    "transactionHash": tx_hash,
                    "blockNumber": receipt.get("blockNumber") if receipt else None,
                    "status": int(receipt.get("status") or 0) if receipt else None,
                    "gasUsed": receipt.get("gasUsed") if receipt else None,
                    "effectiveGasPrice": receipt.get("effectiveGasPrice") if receipt else None,
                    "from": receipt.get("from") if receipt else None,
                    "to": receipt.get("to") if receipt else None,
                    "logs": receipt.get("logs") if receipt else [],
                },
                "transaction": {
                    "hash": tx_hash,
                    "from": transaction.get("from") if transaction else None,
                    "to": transaction.get("to") if transaction else None,
                    "blockNumber": transaction.get("blockNumber") if transaction else None,
                    "value": transaction.get("value") if transaction else None,
                },
            }
        except Exception as exc:  # noqa: BLE001 - surface RPC failure details to caller
            errors.append(str(exc)[:200])
    return {
        "ok": False,
        "error": "tx_receipt_unavailable",
        "detail": "; ".join(errors) or "no RPC URL configured",
        "chain_id": int(chain_id),
        "tx_hash": tx_hash,
    }


def _probe_robinhood_testnet_rpc() -> tuple[str, int]:
    """Return a reachable Robinhood Testnet RPC URL and latest block number.

    This preflight deliberately avoids invoking the Node/Hardhat stack. A fast
    JSON-RPC batch probe is enough to surface ``UND_ERR_CONNECT_TIMEOUT`` and
    connection-refused failures before an official route test starts.
    """
    urls = robinhood.testnet_rpc_urls_from_env()
    if not urls:
        raise RuntimeError("Robinhood Testnet RPC unavailable: no RPC URLs configured")
    timeout = float(os.getenv("DEX_ONCHAIN_RPC_PROBE_TIMEOUT_SECONDS", "8") or 8)
    failures: list[str] = []
    for url in urls:
        try:
            body = json.dumps(
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []},
                    {"jsonrpc": "2.0", "id": 2, "method": "eth_blockNumber", "params": []},
                ]
            ).encode("utf-8")
            req = urllib_request.Request(
                url,
                data=body,
                headers={
                    "content-type": "application/json",
                    "user-agent": "flashloan-robinhood-testnet-route-probe/1.0",
                },
                method="POST",
            )
            with urllib_request.urlopen(req, timeout=timeout) as response:
                rows = json.loads(response.read().decode("utf-8"))
            if isinstance(rows, dict):
                rows = [rows]
            values: dict[int, Any] = {}
            for row in rows:
                if isinstance(row, dict):
                    values[int(row.get("id") or 0)] = row.get("result") or row.get("error")
            chain_id = int(str(values.get(1) or "0x0"), 16)
            block_number = int(str(values.get(2) or "0x0"), 16)
            if chain_id == ROBINHOOD_TESTNET_CHAIN_ID and block_number > 0:
                return url, block_number
            failures.append(f"{url}: chain/block mismatch chain={chain_id} block={block_number}")
        except Exception as exc:
            failures.append(f"{url}: {exc}")
    raise RuntimeError("Robinhood Testnet RPC unavailable: " + "; ".join(failures[:5]))
