from __future__ import annotations

import os
import json
import re
from datetime import datetime, timezone
from typing import Any

from cow_flashloan.routes import cow_network_config
from core.sensitive_data import redact_sensitive_text
from execution.gas_estimator import build_gas_params, estimate_gas_price
from execution.private_tx import send_raw_transaction_private_first
from intent_trade.builder import (
    DEFAULT_INTENT_BORROW_SYMBOL,
    _intent_network,
    build_cow_intent_trade,
)
from intent_trade.direct_utils import (
    _bool_value,
    _execution_failure_report,
    _raw_signed_transaction,
    _route_decision_report,
)


_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$", re.IGNORECASE)
_USDC_PAIR_MEMORY_TABLE: list[dict[str, str]] = []
ROUTE_DECISION_COMPONENTS = [
    {"name": "viable", "type": "bool"},
    {"name": "reverse", "type": "bool"},
    {"name": "quotedFinalUsdc", "type": "uint256"},
    {"name": "profitUsdc", "type": "uint256"},
    {"name": "path", "type": "address[]"},
    {"name": "edgeBps", "type": "uint256"},
    {"name": "requiredEdgeBps", "type": "uint256"},
    {"name": "directComparableAmount", "type": "uint256"},
    {"name": "viaComparableAmount", "type": "uint256"},
    {"name": "failureCode", "type": "uint256"},
    {"name": "requiredFinalUsdc", "type": "uint256"},
    {"name": "minAfterSlippageUsdc", "type": "uint256"},
    {"name": "amountOutMinUsdc", "type": "uint256"},
    {"name": "selectedAmount", "type": "uint256"},
    {"name": "routeMaxBorrow", "type": "uint256"},
    {"name": "probeAmount", "type": "uint256"},
    {"name": "probeProfitUsdc", "type": "uint256"},
    {"name": "fundingCostUsdc", "type": "uint256"},
    {"name": "mBps", "type": "int256"},
]
USDC_PAIR_COMPONENTS = [
    {"name": "tokenX", "type": "address"},
    {"name": "tokenY", "type": "address"},
    {"name": "router", "type": "address"},
]
RUNTIME_POOL_COMPONENTS = [
    {"name": "adapterKind", "type": "uint8"},
    {"name": "pool", "type": "address"},
]
RUNTIME_TRADE_COMPONENTS = [
    {"name": "tradeIndex", "type": "uint256"},
    {"name": "tokenX", "type": "address"},
    {"name": "tokenY", "type": "address"},
    {"name": "pools", "type": "tuple[10]", "components": RUNTIME_POOL_COMPONENTS},
]
RUNTIME_DECISION_COMPONENTS = [
    {"name": "viable", "type": "bool"},
    {"name": "tradeIndex", "type": "uint256"},
    {"name": "tokenX", "type": "address"},
    {"name": "tokenY", "type": "address"},
    {"name": "lowPool", "type": "address"},
    {"name": "highPool", "type": "address"},
    {"name": "adapterKind", "type": "uint8"},
    {"name": "lowFee", "type": "uint24"},
    {"name": "highFee", "type": "uint24"},
    {"name": "lowLiquidity", "type": "uint128"},
    {"name": "highLiquidity", "type": "uint128"},
    {"name": "lowNormalizedTick", "type": "int24"},
    {"name": "highNormalizedTick", "type": "int24"},
    {"name": "tickDelta", "type": "int256"},
    {"name": "scannedPoolCount", "type": "uint256"},
    {"name": "validPoolCount", "type": "uint256"},
    {"name": "failureCode", "type": "uint256"},
]
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
        "name": "previewBestRuntimeTrades",
        "stateMutability": "view",
        "inputs": [{"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS}],
        "outputs": [
            {"name": "bestTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
        ],
    },
    {
        "type": "function",
        "name": "runBestRuntimeTrades",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS}],
        "outputs": [
            {"name": "bestTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
        ],
    },
]

def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value and value != "0x...":
            return value
    return default


def _env_bool(*names: str) -> bool:
    for name in names:
        value = os.getenv(name, "").strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
    return False


def _normalize_pair_address(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower() == "0x..." or not _ADDRESS_RE.match(text):
        return ""
    return "0x" + text[2:].lower()


def _parse_pair_id(raw: Any) -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        pair_id = int(str(raw).strip(), 10)
    except ValueError as exc:
        raise ValueError(f"pair_id must be a non-negative integer, got {raw!r}") from exc
    if pair_id < 0:
        raise ValueError(f"pair_id must be a non-negative integer, got {pair_id}")
    return pair_id


def _explicit_pair_id(protocol: dict[str, Any]) -> int | None:
    raw = protocol.get("pair_id")
    if raw is None:
        raw = protocol.get("usdc_pair_id")
    return _parse_pair_id(raw)


def _env_pair_id() -> int | None:
    return _parse_pair_id(_env("TRIANGULAR_USDC_PAIR_ID", "TRIANGULAR_PAIR_ID"))


def set_usdc_pair_memory_table(pairs: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, str]]:
    global _USDC_PAIR_MEMORY_TABLE
    _USDC_PAIR_MEMORY_TABLE = _normalize_usdc_pair_table(pairs)
    return list(_USDC_PAIR_MEMORY_TABLE)


def _normalize_usdc_pair_table(pairs: Any) -> list[dict[str, str]]:
    if not isinstance(pairs, (list, tuple)):
        raise ValueError("USDC pair table must be a list")
    normalized = []
    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            raise ValueError(f"USDC pair table entry {index} must be an object")
        token_x = _normalize_pair_address(pair.get("tokenX") or pair.get("token_x") or pair.get("tokex"))
        token_y = _normalize_pair_address(pair.get("tokenY") or pair.get("token_y") or pair.get("tokey"))
        router = _normalize_pair_address(pair.get("router"))
        if not token_x:
            raise ValueError(f"USDC pair table entry {index} tokenX must be a valid address")
        if not token_y:
            raise ValueError(f"USDC pair table entry {index} tokenY must be a valid address")
        if token_x == token_y:
            raise ValueError(f"USDC pair table entry {index} tokenX and tokenY must be different")
        entry = {"tokenX": token_x, "tokenY": token_y}
        if router:
            entry["router"] = router
        normalized.append(entry)
    return normalized


def _env_usdc_pair_table() -> list[dict[str, str]]:
    value = _env("TRIANGULAR_USDC_PAIRS_JSON")
    if value:
        return _normalize_usdc_pair_table(json.loads(value))
    token_x = _env("TRIANGULAR_TOKEN_X")
    token_y = _env("TRIANGULAR_TOKEN_Y")
    router = _env("TRIANGULAR_DEX_ROUTER", "DEX_ROUTER_ADDRESS", "FUJI_DEX_ROUTER")
    if token_x and token_y:
        return _normalize_usdc_pair_table([{"tokenX": token_x, "tokenY": token_y, "router": router}])
    return []


def local_usdc_pair_memory_table(protocol: dict[str, Any] | None = None) -> list[dict[str, str]]:
    if protocol and isinstance(protocol.get("usdc_pairs"), list):
        return _normalize_usdc_pair_table(protocol["usdc_pairs"])
    if _USDC_PAIR_MEMORY_TABLE:
        return list(_USDC_PAIR_MEMORY_TABLE)
    return _env_usdc_pair_table()


def _runtime_candidate_pairs(protocol: dict[str, Any], opportunity: dict[str, Any] | None = None) -> list[dict[str, str]]:
    pairs_source = None
    for source in (protocol, opportunity):
        if not isinstance(source, dict):
            continue
        for key in ("candidate_pairs", "usdc_candidate_pairs", "usdc_pairs"):
            if isinstance(source.get(key), list):
                pairs_source = source[key]
                break
        if pairs_source is not None:
            break
    if pairs_source is None:
        return []

    default_router = _normalize_pair_address(
        _first_value(protocol, opportunity, names=("router", "dex_router", "router_address"))
        or _env("TRIANGULAR_DEX_ROUTER", "DEX_ROUTER_ADDRESS", "FUJI_DEX_ROUTER")
    )
    candidates = []
    for index, pair in enumerate(_normalize_usdc_pair_table(pairs_source)):
        router = _normalize_pair_address(pair.get("router")) or default_router
        if not router:
            raise ValueError(f"runtime candidate pair {index} router must be a valid address")
        candidates.append({"tokenX": pair["tokenX"], "tokenY": pair["tokenY"], "router": router})
    return candidates


def _normalize_runtime_pool(pool: Any) -> dict[str, Any]:
    if not isinstance(pool, dict):
        raise ValueError("runtime pool must be an object")
    adapter_kind = int(pool.get("adapterKind", pool.get("adapter_kind", 0)) or 0)
    pool_address = _normalize_pair_address(pool.get("pool"))
    return {"adapterKind": adapter_kind, "pool": pool_address}


def _normalize_runtime_trade(trade: Any, trade_index: int) -> dict[str, Any]:
    if not isinstance(trade, dict):
        raise ValueError("runtime trade must be an object")
    token_x = _normalize_pair_address(trade.get("tokenX") or trade.get("token_x"))
    token_y = _normalize_pair_address(trade.get("tokenY") or trade.get("token_y"))
    if not token_x or not token_y:
        raise ValueError(f"runtime trade {trade_index} tokenX/tokenY must be valid addresses")
    pools = trade.get("pools") or trade.get("candidatePools") or trade.get("candidate_pools")
    if not isinstance(pools, list):
        raise ValueError(f"runtime trade {trade_index} pools must be a list")
    normalized_pools = [{"adapterKind": 0, "pool": ""} for _ in range(10)]
    for index, pool in enumerate(pools[:10]):
        normalized_pools[index] = _normalize_runtime_pool(pool)
    return {
        "tradeIndex": int(trade.get("tradeIndex", trade.get("trade_index", trade_index))),
        "tokenX": token_x,
        "tokenY": token_y,
        "pools": normalized_pools,
    }


def _runtime_trade_candidates(protocol: dict[str, Any], opportunity: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    source = None
    for payload in (protocol, opportunity):
        if not isinstance(payload, dict):
            continue
        for key in ("runtime_trades", "candidate_trades", "trades"):
            if isinstance(payload.get(key), list):
                source = payload[key]
                break
        if source is not None:
            break
    if source is None:
        env_value = _env("TRIANGULAR_RUNTIME_TRADES_JSON")
        if not env_value:
            return []
        source = json.loads(env_value)
    if not isinstance(source, list):
        raise ValueError("runtime trades must be a list")
    return [_normalize_runtime_trade(trade, index) for index, trade in enumerate(source)]


def _runtime_trade_decision_report(result: Any) -> dict[str, Any]:
    failure_code = int(result[16]) if result[16] else 0
    return {
        "ok": bool(result[0]),
        "viable": bool(result[0]),
        "tradeIndex": str(result[1]),
        "tokenX": result[2],
        "tokenY": result[3],
        "lowPool": result[4],
        "highPool": result[5],
        "adapterKind": str(result[6]),
        "lowFee": str(result[7]),
        "highFee": str(result[8]),
        "lowLiquidity": str(result[9]),
        "highLiquidity": str(result[10]),
        "lowNormalizedTick": str(result[11]),
        "highNormalizedTick": str(result[12]),
        "tickDelta": str(result[13]),
        "scannedPoolCount": str(result[14]),
        "validPoolCount": str(result[15]),
        "failureCode": str(failure_code),
        "failureReason": runtimeFailureReason(failure_code),
    }


def runtimeFailureReason(code: int) -> str:
    return {
        0: "none",
        101: "not_enough_valid_pools",
        102: "no_price_spread",
    }.get(int(code), f"unknown_failure_{code}")


def pair_index_from_tokenx_tokeny(
    token_x: Any,
    token_y: Any,
    pairs: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    *,
    router: Any = None,
) -> int | None:
    normalized_x = _normalize_pair_address(token_x)
    normalized_y = _normalize_pair_address(token_y)
    normalized_router = _normalize_pair_address(router)
    if not normalized_x or not normalized_y:
        return None
    table = _normalize_usdc_pair_table(pairs) if pairs is not None else local_usdc_pair_memory_table()
    for index, pair in enumerate(table):
        same_order = pair["tokenX"] == normalized_x and pair["tokenY"] == normalized_y
        reverse_order = pair["tokenX"] == normalized_y and pair["tokenY"] == normalized_x
        if not same_order and not reverse_order:
            continue
        if normalized_router and pair.get("router") and pair["router"] != normalized_router:
            continue
        return index
    return None


def _first_value(*objects: Any, names: tuple[str, ...]) -> Any:
    for source in objects:
        if not isinstance(source, dict):
            continue
        for name in names:
            value = source.get(name)
            if value not in (None, ""):
                return value
    return None


def _pair_tokens_from_path(path: Any) -> tuple[Any, Any]:
    if isinstance(path, (list, tuple)) and len(path) >= 4:
        return path[1], path[2]
    return None, None


def _resolve_pair_id(protocol: dict[str, Any], opportunity: dict[str, Any] | None = None) -> int | None:
    explicit = _explicit_pair_id(protocol)
    if explicit is not None:
        return explicit

    token_x = _first_value(protocol, opportunity, names=("tokenX", "token_x", "tokex"))
    token_y = _first_value(protocol, opportunity, names=("tokenY", "token_y", "tokey"))
    router = _first_value(protocol, opportunity, names=("router", "dex_router", "router_address"))
    if token_x is None or token_y is None:
        path_x, path_y = _pair_tokens_from_path(protocol.get("route_path"))
        token_x = token_x if token_x is not None else path_x
        token_y = token_y if token_y is not None else path_y
    if (token_x is None or token_y is None) and isinstance(opportunity, dict):
        path_x, path_y = _pair_tokens_from_path(opportunity.get("route_path") or opportunity.get("path"))
        token_x = token_x if token_x is not None else path_x
        token_y = token_y if token_y is not None else path_y
    inferred = pair_index_from_tokenx_tokeny(token_x, token_y, local_usdc_pair_memory_table(protocol), router=router)
    return inferred if inferred is not None else _env_pair_id()


def build_triangular_onchain_intent_trade(
    link_name: Any,
    expected_profit: Any,
    rising_tokens: Any,
    falling_tokens: Any,
) -> dict[str, Any]:
    intent = build_cow_intent_trade(link_name, expected_profit, rising_tokens, falling_tokens)
    route_path = list(intent.get("route_path") or [])
    runtime_trades = _runtime_trade_candidates({}, None)
    network, chain_id, testnet = _intent_network()
    protocol = {
        "kind": "triangular_route_controller_runtime_v1",
        "enabled": True,
        "network": network,
        "chain_id": chain_id,
        "testnet": testnet,
        "owner_address": _env("LIQUIDATION_EXECUTOR_OWNER_ADDRESS", "TRIANGULAR_CONTROLLER_OWNER_ADDRESS"),
        "controller_address": _env("TRIANGULAR_ROUTE_CONTROLLER_ADDRESS", "TRIANGULAR_CONTROLLER_ADDRESS"),
        "executor_address": _env("AAVE_TRIANGULAR_EXECUTOR_ADDRESS", "TRIANGULAR_EXECUTOR_ADDRESS"),
        "runtime_trades": runtime_trades,
        "borrow_symbol": intent.get("initial_symbol") or DEFAULT_INTENT_BORROW_SYMBOL,
        "route_path": route_path,
        "route_direction": intent.get("route_direction"),
        "expected_profit_usdc": intent.get("expected_profit_amount"),
    }
    intent["direct_onchain_protocol"] = protocol
    intent["intent_protocol"] = "direct_onchain"
    intent["submission_protocol"] = "direct_onchain"
    intent["submission_mode"] = "direct_onchain"
    intent["direct_onchain_ready"] = bool(protocol["controller_address"] and protocol["runtime_trades"])
    return intent


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
    owner_address = str(protocol.get("owner_address") or "").strip()
    if not controller_address:
        return {
            "ok": False,
            "submitted": False,
            "status": "direct_protocol_incomplete",
            "blocked_reason": "direct_protocol_incomplete",
            "error": "controller address is required",
        }

    network_config = cow_network_config(network=network)
    opportunity_context = opportunity if isinstance(opportunity, dict) else None
    runtime_trades = _runtime_trade_candidates(protocol, opportunity_context)
    use_runtime_trades = bool(runtime_trades)
    if not use_runtime_trades:
        return {
            "ok": False,
            "submitted": False,
            "status": "direct_protocol_incomplete",
            "blocked_reason": "direct_protocol_incomplete",
            "error": "runtime_trades is required",
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

    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": request_timeout}))
        controller = w3.eth.contract(address=Web3.to_checksum_address(controller_address), abi=TRIANGULAR_CONTROLLER_ABI)
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
        onchain_owner = Web3.to_checksum_address(controller.functions.owner().call())
        configured_owner = Web3.to_checksum_address(owner_address) if owner_address else onchain_owner
        if signer_address.lower() != onchain_owner.lower():
            return {
                "ok": False,
                "submitted": False,
                "status": "signer_not_owner",
                "blocked_reason": "signer_not_owner",
                "error": "signer does not match controller owner",
                "owner": onchain_owner,
                "configured_owner": configured_owner,
                "signer": signer_address,
            }
        candidate_trade_args = []
        for trade in runtime_trades:
            pools = []
            for pool in trade["pools"]:
                pool_address = pool["pool"] or "0x0000000000000000000000000000000000000000"
                pools.append({"adapterKind": int(pool["adapterKind"]), "pool": Web3.to_checksum_address(pool_address)})
            candidate_trade_args.append(
                {
                    "tradeIndex": int(trade["tradeIndex"]),
                    "tokenX": Web3.to_checksum_address(trade["tokenX"]),
                    "tokenY": Web3.to_checksum_address(trade["tokenY"]),
                    "pools": pools,
                }
            )
        best_trade_index, preflight = controller.functions.previewBestRuntimeTrades(candidate_trade_args).call({"from": signer_address})
        tx_builder = controller.functions.runBestRuntimeTrades(candidate_trade_args)
        request_payload = {
            "runtimeTrades": candidate_trade_args,
            "bestTradeArrayIndex": str(best_trade_index),
        }
        if not preflight[0]:
            result = {
                "ok": False,
                "submitted": False,
                "status": "no_viable_route",
                "blocked_reason": "no_viable_route",
                "error": "controller preview found no viable route",
                "network": network,
                "chain_id": chain_id,
                "controller_address": controller_address,
                "preflight": _runtime_trade_decision_report(preflight),
                "request": request_payload,
            }
            return result

        static_return = tx_builder.call({"from": signer_address})
        static_profit = static_return[1]
        gas_estimate = tx_builder.estimate_gas({"from": signer_address})
        static_report = {
            "ok": True,
            "profitReturned": str(static_profit),
            "gasEstimate": str(gas_estimate),
        }
        static_report["bestTradeArrayIndexReturned"] = str(static_return[0])
        broadcast_enabled = _env_bool("TRIANGULAR_DIRECT_BROADCAST_ENABLED", "TRIANGULAR_AB_BROADCAST_ENABLED")
        if not broadcast_enabled:
            result = {
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
                "preflight": _runtime_trade_decision_report(preflight),
                "static_call": static_report,
                "request": request_payload,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
            return result
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
        broadcast = send_raw_transaction_private_first(_raw_signed_transaction(signed_tx), public_w3=w3)
        tx_hash = broadcast.get("tx_hash")
        receipt_hash = tx_hash.hex() if hasattr(tx_hash, "hex") else tx_hash
        receipt = w3.eth.wait_for_transaction_receipt(receipt_hash, timeout=max(1, int(timeout_seconds or 180)))
        status = "submitted_success" if receipt and int(receipt.status or 0) == 1 else "submitted_failed"
        result = {
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
            "preflight": _runtime_trade_decision_report(preflight),
            "request": request_payload,
            "receipt": {
                "hash": receipt.hash.hex() if hasattr(receipt.hash, "hex") else str(receipt.hash),
                "status": receipt.status,
                "gasUsed": str(receipt.gasUsed) if receipt and receipt.gasUsed is not None else None,
            },
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        return result
    except Exception as exc:
        execution_error = _execution_failure_report(exc)
        return {
            "ok": False,
            "submitted": False,
            "status": "execution_preflight_failed" if execution_error else "submission_failed",
            "blocked_reason": execution_error.get("failureReason") if execution_error else "submission_failed",
            "error": execution_error.get("failureReason") if execution_error else redact_sensitive_text(exc),
            "execution_error": execution_error,
        }
