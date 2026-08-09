from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from cow_flashloan.routes import build_token_registry, cow_network_config, resolve_token, to_units
from core.sensitive_data import redact_sensitive_text
from execution.gas_estimator import build_gas_params, estimate_gas_price
from execution.private_tx import send_raw_transaction_private_first
from intent_trade.builder import (
    DEFAULT_INTENT_BORROW_SYMBOL,
    _decimal_text,
    _decimal_value,
    _intent_network,
    build_cow_intent_trade,
)


SRC_ROOT = Path(__file__).resolve().parents[1]
TRIANGULAR_CONTROLLER_ABI = [
    {
        "type": "function",
        "name": "owner",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    },
    {
        "type": "function",
        "name": "previewBestRoute",
        "stateMutability": "view",
        "inputs": [
            {
                "name": "request",
                "type": "tuple",
                "components": [
                    {"name": "tokenX", "type": "address"},
                    {"name": "tokenY", "type": "address"},
                    {"name": "router", "type": "address"},
                    {"name": "amount", "type": "uint256"},
                    {"name": "premiumBps", "type": "uint256"},
                    {"name": "minProfitUsdc", "type": "uint256"},
                    {"name": "deadline", "type": "uint256"},
                    {"name": "slippageBps", "type": "uint256"},
                    {"name": "allowReverse", "type": "bool"},
                ],
            }
        ],
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": [
                    {"name": "viable", "type": "bool"},
                    {"name": "reverse", "type": "bool"},
                    {"name": "quotedFinalUsdc", "type": "uint256"},
                    {"name": "profitUsdc", "type": "uint256"},
                    {"name": "path", "type": "address[]"},
                    {"name": "edgeBps", "type": "uint256"},
                    {"name": "requiredEdgeBps", "type": "uint256"},
                    {"name": "directComparableAmount", "type": "uint256"},
                    {"name": "viaComparableAmount", "type": "uint256"},
                ],
            }
        ],
    },
    {
        "type": "function",
        "name": "run",
        "stateMutability": "nonpayable",
        "inputs": [
            {
                "name": "request",
                "type": "tuple",
                "components": [
                    {"name": "tokenX", "type": "address"},
                    {"name": "tokenY", "type": "address"},
                    {"name": "router", "type": "address"},
                    {"name": "amount", "type": "uint256"},
                    {"name": "premiumBps", "type": "uint256"},
                    {"name": "minProfitUsdc", "type": "uint256"},
                    {"name": "deadline", "type": "uint256"},
                    {"name": "slippageBps", "type": "uint256"},
                    {"name": "allowReverse", "type": "bool"},
                ],
            }
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

POOL_ABI = [
    {
        "type": "function",
        "name": "FLASHLOAN_PREMIUM_TOTAL",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint128"}],
    }
]


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value and value != "0x...":
            return value
    return default


def _parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return int(default)


def _env_bool(*names: str) -> bool:
    for name in names:
        value = os.getenv(name, "").strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
    return False


def build_triangular_onchain_intent_trade(
    link_name: Any,
    expected_profit: Any,
    rising_tokens: Any,
    falling_tokens: Any,
) -> dict[str, Any]:
    intent = build_cow_intent_trade(link_name, expected_profit, rising_tokens, falling_tokens)
    route_path = list(intent.get("route_path") or [])
    token_x_symbol = route_path[1] if len(route_path) >= 3 else DEFAULT_INTENT_BORROW_SYMBOL
    token_y_symbol = route_path[2] if len(route_path) >= 4 else token_x_symbol
    network, chain_id, testnet = _intent_network()
    borrow_amount = _decimal_value(intent.get("borrow_token_amount") or intent.get("initial_amount")) or Decimal("1000")
    profit_amount = _decimal_value(intent.get("min_pure_profit_amount")) or Decimal("0")
    protocol = {
        "kind": "triangular_route_controller_v1",
        "enabled": True,
        "network": network,
        "chain_id": chain_id,
        "testnet": testnet,
        "owner_address": _env("LIQUIDATION_EXECUTOR_OWNER_ADDRESS", "TRIANGULAR_CONTROLLER_OWNER_ADDRESS"),
        "controller_address": _env("TRIANGULAR_ROUTE_CONTROLLER_ADDRESS", "TRIANGULAR_CONTROLLER_ADDRESS"),
        "executor_address": _env("AAVE_TRIANGULAR_EXECUTOR_ADDRESS", "TRIANGULAR_EXECUTOR_ADDRESS"),
        "pool_address": _env("TRIANGULAR_AAVE_POOL_ADDRESS", "AAVE_POOL_ADDRESS"),
        "router_address": _env("TRIANGULAR_DEX_ROUTER", "DEX_ROUTER_ADDRESS"),
        "borrow_symbol": intent.get("initial_symbol") or DEFAULT_INTENT_BORROW_SYMBOL,
        "token_x_symbol": token_x_symbol,
        "token_y_symbol": token_y_symbol,
        "allow_reverse": True,
        "amount": _decimal_text(borrow_amount),
        "min_profit_usdc": _decimal_text(profit_amount),
        "deadline_seconds": _parse_int(_env("TRIANGULAR_DEADLINE_SECONDS", "DYNAMIC_DEADLINE_SECONDS"), 60),
        "slippage_bps": _parse_int(_env("TRIANGULAR_SLIPPAGE_BPS", "DYNAMIC_SLIPPAGE_BPS"), 50),
        "route_path": route_path,
        "route_direction": intent.get("route_direction"),
        "expected_profit_usdc": intent.get("expected_profit_amount"),
    }
    intent["direct_onchain_protocol"] = protocol
    intent["intent_protocol"] = "direct_onchain"
    intent["submission_protocol"] = "direct_onchain"
    intent["submission_mode"] = "direct_onchain"
    intent["direct_onchain_ready"] = bool(
        protocol["controller_address"] and protocol["pool_address"] and protocol["router_address"]
    )
    return intent


def _resolve_token_symbol(*, registry: dict[str, Any], symbol: str):
    token = resolve_token(symbol, registry)
    return token


def submit_direct_onchain_trade(
    *,
    quote_payload: dict[str, Any],
    opportunity: dict[str, Any],
    timeout_seconds: int | float | None = None,
) -> dict[str, Any]:
    intent = quote_payload.get("cow_flashloan_intent") if isinstance(quote_payload, dict) else None
    protocol = intent.get("direct_onchain_protocol") if isinstance(intent, dict) else None
    if not isinstance(protocol, dict) or not protocol.get("enabled", True):
        return {
            "ok": False,
            "submitted": False,
            "status": "direct_protocol_missing",
            "blocked_reason": "direct_protocol_missing",
            "error": "direct_onchain_protocol is required",
        }

    network = str(protocol.get("network") or "avalanche").strip().lower()
    if network != "avalanche":
        return {
            "ok": False,
            "submitted": False,
            "status": "order_submission_network_unsupported",
            "blocked_reason": "order_submission_network_unsupported",
            "error": f"unsupported direct on-chain network: {network}",
        }

    controller_address = str(protocol.get("controller_address") or "").strip()
    pool_address = str(protocol.get("pool_address") or "").strip()
    router_address = str(protocol.get("router_address") or "").strip()
    owner_address = str(protocol.get("owner_address") or "").strip()
    if not controller_address or not pool_address or not router_address:
        return {
            "ok": False,
            "submitted": False,
            "status": "direct_protocol_incomplete",
            "blocked_reason": "direct_protocol_incomplete",
            "error": "controller, pool, and router addresses are required",
        }

    network_config = cow_network_config(network=network)
    default_cache = SRC_ROOT / "runtime" / "cache" / "aave_reserve_assets.json"
    aave_cache_path = Path(_env("AAVE_RESERVE_CACHE_FILE", default=str(default_cache)))
    registry = build_token_registry(
        aave_cache_path=aave_cache_path if aave_cache_path.exists() else None,
        cow_network=network_config.network,
    )
    borrow_symbol = str(protocol.get("borrow_symbol") or DEFAULT_INTENT_BORROW_SYMBOL).strip().upper()
    token_x_symbol = str(protocol.get("token_x_symbol") or "").strip().upper()
    token_y_symbol = str(protocol.get("token_y_symbol") or "").strip().upper()
    if not token_x_symbol or not token_y_symbol:
        return {
            "ok": False,
            "submitted": False,
            "status": "direct_protocol_incomplete",
            "blocked_reason": "direct_protocol_incomplete",
            "error": "token_x_symbol and token_y_symbol are required",
        }

    borrow_token = _resolve_token_symbol(registry=registry, symbol=borrow_symbol)
    token_x = _resolve_token_symbol(registry=registry, symbol=token_x_symbol)
    token_y = _resolve_token_symbol(registry=registry, symbol=token_y_symbol)
    amount_human = _decimal_value(protocol.get("amount")) or Decimal("0")
    min_profit_human = _decimal_value(protocol.get("min_profit_usdc")) or Decimal("0")
    if amount_human <= 0 or min_profit_human < 0:
        return {
            "ok": False,
            "submitted": False,
            "status": "direct_protocol_invalid",
            "blocked_reason": "direct_protocol_invalid",
            "error": "amount and min_profit_usdc must be non-negative",
        }

    rpc_url = _env("AVALANCHE_RPC_URL", "AVALANCHE_RPC", "FUJI_RPC_URL")
    if not rpc_url:
        return {
            "ok": False,
            "submitted": False,
            "status": "network_config_missing",
            "blocked_reason": "network_config_missing",
            "error": "AVALANCHE_RPC_URL is required",
        }

    from web3 import Web3
    from eth_account import Account

    request_timeout = max(1, int(timeout_seconds or 20))
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": request_timeout}))
    controller = w3.eth.contract(address=Web3.to_checksum_address(controller_address), abi=TRIANGULAR_CONTROLLER_ABI)
    pool = w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=POOL_ABI)

    try:
        chain_id = int(w3.eth.chain_id)
        if chain_id != int(network_config.chain_id):
            return {
                "ok": False,
                "submitted": False,
                "status": "network_mismatch",
                "blocked_reason": "network_mismatch",
                "error": f"chain id is {chain_id}, expected {network_config.chain_id}",
            }
        signer_key = _env("LIQUIDATION_EXECUTION_PRIVATE_KEY", "COW_ORDER_SIGNER_PRIVATE_KEY", "DEPLOYER_PRIVATE_KEY")
        if not signer_key:
            return {
                "ok": False,
                "submitted": False,
                "status": "signer_private_key_missing",
                "blocked_reason": "signer_private_key_missing",
                "error": "signer private key is required",
            }
        signer = Account.from_key(signer_key)
        signer_address = Web3.to_checksum_address(signer.address)
        onchain_owner = Web3.to_checksum_address(owner_address) if owner_address else Web3.to_checksum_address(controller.functions.owner().call())
        if signer_address.lower() != onchain_owner.lower():
            return {
                "ok": False,
                "submitted": False,
                "status": "signer_not_owner",
                "blocked_reason": "signer_not_owner",
                "error": "signer does not match controller owner",
                "owner": onchain_owner,
                "signer": signer_address,
            }

        amount_units = int(to_units(str(amount_human), borrow_token.decimals))
        profit_units = int(to_units(str(min_profit_human), borrow_token.decimals))
        latest_block = w3.eth.get_block("latest")
        deadline_seconds = _parse_int(protocol.get("deadline_seconds"), 60)
        deadline = int(latest_block.timestamp) + max(1, deadline_seconds)
        premium_bps = int(pool.functions.FLASHLOAN_PREMIUM_TOTAL().call())
        request = (
            Web3.to_checksum_address(token_x.address),
            Web3.to_checksum_address(token_y.address),
            Web3.to_checksum_address(router_address),
            amount_units,
            premium_bps,
            profit_units,
            deadline,
            int(protocol.get("slippage_bps") or 50),
            bool(protocol.get("allow_reverse", True)),
        )

        preflight = controller.functions.previewBestRoute(request).call({"from": signer_address})
        if not preflight[0]:
            return {
                "ok": False,
                "submitted": False,
                "status": "no_viable_route",
                "blocked_reason": "no_viable_route",
                "error": "controller preview found no viable route",
                "network": network,
                "chain_id": chain_id,
                "controller_address": controller_address,
                "preflight": {
                    "viable": bool(preflight[0]),
                    "reverse": bool(preflight[1]),
                    "quotedFinalUsdc": str(preflight[2]),
                    "profitUsdc": str(preflight[3]),
                    "path": preflight[4],
                },
                "request": {
                    "tokenX": token_x.address,
                    "tokenY": token_y.address,
                    "router": Web3.to_checksum_address(router_address),
                    "amount": amount_units,
                    "premiumBps": premium_bps,
                    "minProfitUsdc": profit_units,
                    "deadline": deadline,
                    "slippageBps": int(protocol.get("slippage_bps") or 50),
                    "allowReverse": bool(protocol.get("allow_reverse", True)),
                },
            }

        tx_builder = controller.functions.run(request)
        static_profit = tx_builder.call({"from": signer_address})
        gas_estimate = tx_builder.estimate_gas({"from": signer_address})
        static_report = {
            "ok": True,
            "profitReturned": str(static_profit),
            "gasEstimate": str(gas_estimate),
        }
        broadcast_enabled = _env_bool("TRIANGULAR_DIRECT_BROADCAST_ENABLED", "TRIANGULAR_AB_BROADCAST_ENABLED")
        if not broadcast_enabled:
            return {
                "ok": True,
                "submitted": False,
                "status": "static_call_passed",
                "blocked_reason": "broadcast_disabled",
                "error": None,
                "network": network,
                "chain_id": chain_id,
                "owner": onchain_owner,
                "signer": signer_address,
                "controller_address": controller_address,
                "tx_hash": None,
                "preflight": {
                    "viable": bool(preflight[0]),
                    "reverse": bool(preflight[1]),
                    "quotedFinalUsdc": str(preflight[2]),
                    "profitUsdc": str(preflight[3]),
                    "path": preflight[4],
                },
                "static_call": static_report,
                "request": {
                    "tokenX": token_x.address,
                    "tokenY": token_y.address,
                    "router": Web3.to_checksum_address(router_address),
                    "amount": amount_units,
                    "premiumBps": premium_bps,
                    "minProfitUsdc": profit_units,
                    "deadline": deadline,
                    "slippageBps": int(protocol.get("slippage_bps") or 50),
                    "allowReverse": bool(protocol.get("allow_reverse", True)),
                },
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        gas_estimate_info = estimate_gas_price(w3)
        tx_params = {
            "from": signer_address,
            "nonce": w3.eth.get_transaction_count(signer_address, "pending"),
            "chainId": chain_id,
            "gas": gas_estimate,
            **build_gas_params(gas_estimate_info),
        }
        built_tx = tx_builder.build_transaction(tx_params)
        signed_tx = Account.sign_transaction(built_tx, signer_key)
        broadcast = send_raw_transaction_private_first(signed_tx.raw_transaction, public_w3=w3)
        tx_hash = broadcast.get("tx_hash")
        receipt_hash = tx_hash.hex() if hasattr(tx_hash, "hex") else tx_hash
        receipt = w3.eth.wait_for_transaction_receipt(receipt_hash, timeout=max(1, int(timeout_seconds or 180)))
        status = "submitted_success" if receipt and int(receipt.status or 0) == 1 else "submitted_failed"
        return {
            "ok": bool(receipt and int(receipt.status or 0) == 1),
            "submitted": True,
            "status": status,
            "blocked_reason": None if status == "submitted_success" else "submission_failed",
            "error": None if status == "submitted_success" else "transaction reverted",
            "network": network,
            "chain_id": chain_id,
            "owner": onchain_owner,
            "signer": signer_address,
            "controller_address": controller_address,
            "tx_hash": broadcast.get("tx_hash"),
            "broadcast_channel": broadcast.get("broadcast_channel"),
            "relay": broadcast.get("relay"),
            "preflight": {
                "viable": bool(preflight[0]),
                "reverse": bool(preflight[1]),
                "quotedFinalUsdc": str(preflight[2]),
                "profitUsdc": str(preflight[3]),
                "path": preflight[4],
            },
            "request": {
                "tokenX": token_x.address,
                "tokenY": token_y.address,
                "router": Web3.to_checksum_address(router_address),
                "amount": amount_units,
                "premiumBps": premium_bps,
                "minProfitUsdc": profit_units,
                "deadline": deadline,
                "slippageBps": int(protocol.get("slippage_bps") or 50),
                "allowReverse": bool(protocol.get("allow_reverse", True)),
            },
            "receipt": {
                "hash": receipt.hash.hex() if hasattr(receipt.hash, "hex") else str(receipt.hash),
                "status": receipt.status,
                "gasUsed": str(receipt.gasUsed) if receipt and receipt.gasUsed is not None else None,
            },
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        return {
            "ok": False,
            "submitted": False,
            "status": "submission_failed",
            "blocked_reason": "submission_failed",
            "error": redact_sensitive_text(exc),
        }
