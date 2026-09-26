"""Robinhood mainnet V4 pool RPC helpers.

These helpers are intentionally mainnet-scoped. They read chain-owned registry
data and never fall back to robinhood-testnet contracts or RPC endpoints.
"""

from __future__ import annotations

import os
from typing import Any

from web3 import Web3

from dex.intent_trade.chains.robinhood import config as robinhood


ERC20_ABI = [
    {"inputs": [], "name": "symbol", "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"},
]


def _rpc_url_from_env_or_default() -> str:
    urls = robinhood.rpc_urls_from_env()
    if not urls:
        raise RuntimeError("Robinhood mainnet RPC is not configured")
    return urls[0]


def _web3(rpc_url: str, timeout: float | None = None) -> Web3:
    selected_timeout = timeout or float(os.getenv("DEX_ONCHAIN_RPC_TIMEOUT_SECONDS", "12") or 12)
    w3 = Web3(
        Web3.HTTPProvider(
            rpc_url,
            request_kwargs={
                "timeout": selected_timeout,
                "headers": {
                    "Content-Type": "application/json",
                    "User-Agent": "flashloan-robinhood-mainnet-v4-rpc/1.0",
                    "Connection": "close",
                },
            },
        )
    )
    if not w3.is_connected():
        raise RuntimeError("Robinhood mainnet RPC endpoint did not answer")
    return w3


def _connect_chain_rpc() -> tuple[Any, str]:
    failures: list[str] = []
    for url in robinhood.rpc_urls_from_env():
        for timeout in robinhood.mainnet_rpc_connect_timeouts():
            try:
                w3 = _web3(url, timeout=timeout)
                actual_chain_id = int(w3.eth.chain_id)
                if actual_chain_id != int(robinhood.CHAIN_ID):
                    raise RuntimeError(f"wrong chain id {actual_chain_id}, expected {robinhood.CHAIN_ID}")
                return w3, url
            except Exception as exc:
                failures.append(f"{url}: {exc}")
    raise RuntimeError("Robinhood mainnet RPC unavailable: " + "; ".join(failures[:3]))


def _topic_address(topic: object) -> str:
    raw = bytes(topic) if not isinstance(topic, str) else bytes.fromhex(topic[2:] if topic.startswith("0x") else topic)
    return Web3.to_checksum_address("0x" + raw[-20:].hex())


def _currency_metadata(w3: Web3, currency: str, pool_manager: str) -> dict[str, Any]:
    address = str(currency or "").strip()
    if not address.startswith("0x") or len(address) != 42:
        return {"address": address, "symbol": address[:10].upper(), "decimals": None, "metadataOk": False}
    if address.lower() == "0x0000000000000000000000000000000000000000":
        return {"address": address, "symbol": "ETH", "decimals": 18, "metadataOk": True}
    contract = w3.eth.contract(address=Web3.to_checksum_address(address), abi=ERC20_ABI)
    symbol = ""
    decimals = None
    ok = True
    try:
        symbol = str(contract.functions.symbol().call()).strip()
    except Exception:
        ok = False
    try:
        decimals = int(contract.functions.decimals().call())
    except Exception:
        ok = False
    return {
        "address": Web3.to_checksum_address(address),
        "symbol": symbol or address[:10].upper(),
        "decimals": decimals,
        "metadataOk": ok,
        "poolManager": pool_manager,
    }


__all__ = [
    "_connect_chain_rpc",
    "_currency_metadata",
    "_rpc_url_from_env_or_default",
    "_topic_address",
    "_web3",
]
