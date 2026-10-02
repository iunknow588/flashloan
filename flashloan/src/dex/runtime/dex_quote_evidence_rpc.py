from __future__ import annotations

import json
import os
from typing import Any
from urllib import request as urllib_request

from web3 import Web3

from dex.intent_trade.chains.robinhood import config as robinhood
from dex.intent_trade.chains.robinhood.chain_test_policy import ROBINHOOD_TESTNET_CHAIN_ID, ROBINHOOD_TESTNET_NETWORK

from dex.runtime.dex_quote_evidence_common import (
    _evidence_attr_or_default,
    _robinhood_network_for_chain_id,
    _text,
)

def _connect_robinhood() -> tuple[Any, str]:
    failures: list[str] = []
    timeout = float(os.getenv("DEX_ONCHAIN_RPC_TIMEOUT_SECONDS", "12") or 12)
    urls = robinhood.rpc_urls_from_env()
    if not urls:
        raise RuntimeError("Robinhood RPC unavailable: no RPC URLs configured")

    def _probe(url: str) -> tuple[Any, str]:
        w3 = Web3(
            Web3.HTTPProvider(
                url,
                request_kwargs={
                    "timeout": timeout,
                    "headers": {
                        "Content-Type": "application/json",
                        "User-Agent": "flashloan-robinhood-mainnet/1.1",
                        "Connection": "close",
                    },
                },
            )
        )
        if not w3.is_connected():
            raise RuntimeError("HTTP endpoint did not answer")
        actual_chain_id = int(w3.eth.chain_id)
        if actual_chain_id != robinhood.CHAIN_ID:
            raise RuntimeError(f"wrong chain id {actual_chain_id}, expected {robinhood.CHAIN_ID}")
        return w3, url

    if len(urls) == 1:
        try:
            return _probe(urls[0])
        except Exception as exc:
            raise RuntimeError("Robinhood RPC unavailable: " + str(exc))
    for url in urls:
        try:
            return _probe(url)
        except Exception as exc:
            failures.append(f"{url}: {exc}")
            continue
    raise RuntimeError("Robinhood RPC unavailable: " + "; ".join(failures[:3]))

def _probe_robinhood_testnet_rpc_fast() -> str | None:
    """使用快速 JSON-RPC 探测返回可达的 Robinhood 测试网 RPC 地址。

    这样链上复核可以快速失败并给出可读的 RPC 错误，
    而不是把扫描器的完整看门狗预算浪费在 Node/undici 或 Web3 连接超时上。
    """
    urls = robinhood.testnet_rpc_urls_from_env()
    if not urls:
        return None
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
                    "user-agent": "flashloan-robinhood-testnet-evidence-probe/1.0",
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
                return url
            failures.append(f"{url}: chain/block mismatch chain={chain_id} block={block_number}")
        except Exception as exc:
            failures.append(f"{url}: {exc}")
    if failures:
        raise RuntimeError("Robinhood Testnet RPC unavailable: " + "; ".join(failures[:5]))
    return None

def _connect_robinhood_family(chain_id: int | None, network: str) -> tuple[Any, str]:
    normalized_network = _robinhood_network_for_chain_id(chain_id, network)
    if normalized_network == ROBINHOOD_TESTNET_NETWORK:
        failures: list[str] = []
        try:
            probed_url = _probe_robinhood_testnet_rpc_fast()
        except Exception as exc:
            probed_url = None
            failures.append(f"preflight: {exc}")
        if probed_url:
            w3 = Web3(
                Web3.HTTPProvider(
                    probed_url,
                    request_kwargs={
                        "timeout": float(os.getenv("DEX_ONCHAIN_RPC_TIMEOUT_SECONDS", "12") or 12),
                        "headers": {
                            "Content-Type": "application/json",
                            "User-Agent": "flashloan-robinhood-testnet/1.1",
                            "Connection": "close",
                        },
                    },
                )
            )
            try:
                if int(w3.eth.chain_id) == ROBINHOOD_TESTNET_CHAIN_ID:
                    return w3, probed_url
                failures.append(f"{probed_url}: post-probe chain id mismatch")
            except Exception as exc:
                failures.append(f"{probed_url}: post-probe web3 handshake failed: {exc}")
        if not probed_url and not failures:
            failures.append("no Robinhood Testnet RPC URLs configured")
        raise RuntimeError(
            "Robinhood Testnet RPC unavailable after fast probe: "
            + "; ".join(failures[:5])
        )
    connect_robinhood = _evidence_attr_or_default("_connect_robinhood", _connect_robinhood)
    return connect_robinhood()

def _is_robinhood_family(chain_id: int | None, network: str) -> bool:
    normalized_network = _robinhood_network_for_chain_id(chain_id, network)
    return normalized_network in {robinhood.NETWORK, ROBINHOOD_TESTNET_NETWORK}

def _block_timestamp(w3: Any, block_number: int) -> int | None:
    try:
        block = w3.eth.get_block(block_number)
        if isinstance(block, dict):
            return int(block.get("timestamp"))
        return int(getattr(block, "timestamp"))
    except Exception:
        return None

def _trigger_context_from_route(route: dict[str, Any], trigger_context: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(trigger_context, dict) and trigger_context:
        return dict(trigger_context)
    quote_inputs = route.get("quoteInputs") if isinstance(route.get("quoteInputs"), dict) else {}
    route_context = quote_inputs.get("triggerContext") if isinstance(quote_inputs.get("triggerContext"), dict) else {}
    return dict(route_context)

def _trigger_block_number(trigger_context: dict[str, Any]) -> int | None:
    for key in ("triggerBlockNumber", "blockNumber", "block_number"):
        value = trigger_context.get(key)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None
