from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import importlib
import os
import sys
import time
from typing import Any, Callable

from web3 import Web3

from core.error_stack import ErrorStack, ModuleError
from core.error_registry import ErrorCode
from bot.execution.uniswap_v4 import build_v4_quote_request, quote_v4_exact_input_single
from bot.execution.uniswap_v4_sdk_adapter import load_v4_sdk_adapter
from bot.execution.uniswap_v3 import quote_v3_exact_input_single, snapshot_v3_pool
from dex.intent_trade.chains.robinhood import config as robinhood
from dex.intent_trade.chains.robinhood.chain_test_policy import ROBINHOOD_TESTNET_CHAIN_ID, ROBINHOOD_TESTNET_NETWORK
from market.binance_market.dex_surfaces import (
    DEFAULT_FLASH_LOAN_FEE_BPS,
    DEFAULT_GAS_RESERVE_USD,
)


ERC20_ABI = [
    {"inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "symbol", "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
]

V2_PAIR_ABI = [
    {"inputs": [], "name": "token0", "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "token1", "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
    {
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"name": "reserve0", "type": "uint112"},
            {"name": "reserve1", "type": "uint112"},
            {"name": "blockTimestampLast", "type": "uint32"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

_STABLE_DECIMALS = {"AUSD", "BUSD", "DAI", "EURC", "FDUSD", "FRAX", "PYUSD", "RLUSD", "USDC", "USDD", "USDE", "USDG", "USDT"}
_TOKEN_DECIMALS_FALLBACK = {"WETH": 18, "ETH": 18, "WBTC": 8, "BTC": 8, **{symbol: 6 for symbol in _STABLE_DECIMALS}}
DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS = 10
DEFAULT_ROUTE_SIZE_PROBE_ATTEMPTS = 24


QuoteHopProvider = Callable[..., dict[str, Any]]


def _evidence_module_or_none() -> Any | None:
    for module_name in ("runtime.dex_onchain_quote_evidence", "dex.runtime.dex_onchain_quote_evidence"):
        if sys.modules.get(module_name) is not None:
            return sys.modules[module_name]
    return None


def _evidence_attr_or_default(name: str, default: Any) -> Any:
    module = _evidence_module_or_none()
    if module is not None and hasattr(module, name):
        return getattr(module, name)
    return default


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _text(value: object) -> str:
    return str(value or "").strip()


def _robinhood_network_for_chain_id(chain_id: int | None, network: object = "") -> str:
    """Return a canonical Robinhood network name for the given chain/network pair.

    chain_id is authoritative inside the Robinhood family because stale route
    metadata sometimes says ``robinhood`` even when the route was captured on
    Robinhood testnet (46630).
    """
    normalized = _text(network).lower()
    parsed_chain_id = int(chain_id) if chain_id is not None and str(chain_id).strip() else None
    if parsed_chain_id == ROBINHOOD_TESTNET_CHAIN_ID:
        return ROBINHOOD_TESTNET_NETWORK
    if parsed_chain_id == robinhood.CHAIN_ID:
        return robinhood.NETWORK
    if normalized in {
        ROBINHOOD_TESTNET_NETWORK,
        "robinhood_testnet",
        "robinhood-sepolia",
        "chain_46630",
    }:
        return ROBINHOOD_TESTNET_NETWORK
    if normalized in {
        robinhood.NETWORK,
        "robinhood-mainnet",
        "robinhood_mainnet",
        "chain_4663",
    }:
        return robinhood.NETWORK
    return normalized


def _is_missing_historical_state_error(exc: BaseException) -> bool:
    message = _text(exc).lower()
    return (
        "missing trie node" in message
        or "metadata is not found" in message
        or ("historical state" in message and "is not available" in message)
        or "state is not available" in message
        or ("state is not" in message and "available" in message)
        or "state .* not found" in message
    )


def _is_pool_not_initialized_error(exc: BaseException) -> bool:
    message = _text(exc).lower()
    if "poolnotinitialized" in message.replace("_", "").replace("(", "").replace(")", "") or "pool not initialized" in message:
        return True
    cause = getattr(exc, "__cause__", None)
    return bool(cause is not None and _is_pool_not_initialized_error(cause))


def _is_missing_v4_adapter_error(exc: BaseException) -> bool:
    message = _text(exc).lower()
    if any(
        marker in message
        for marker in (
            "quote adapter is required",
            "adapter_required",
            "adapter required",
            "v4 pool manager address missing",
            "v4 quoter address missing",
            "v4 pool index missing",
            "pool index missing requested pool",
            "pool index data file",
            "pool key decode",
            "requested hop tokens do not match",
        )
    ):
        return True
    cause = getattr(exc, "__cause__", None)
    return bool(cause is not None and _is_missing_v4_adapter_error(cause))


def _is_not_enough_liquidity_error(exc: BaseException) -> bool:
    message = _text(exc).lower()
    if (
        "notenoughliquidity" in message.replace("_", "").replace("(", "").replace(")", "")
        or "not enough liquidity" in message
        or "insufficient liquidity" in message
        or "live liquidity is zero" in message
        or "liquidity is zero" in message
    ):
        return True
    cause = getattr(exc, "__cause__", None)
    return bool(cause is not None and _is_not_enough_liquidity_error(cause))


def _pool_not_initialized_error(exc: BaseException) -> str | None:
    if _is_pool_not_initialized_error(exc):
        return str(exc)
    return None


DEX_STATUS_ERROR_KEYS = {
    "live_quote_failed": "DEX.QUOTE.LIVE_QUOTE_FAILED",
    "pool_not_initialized": "DEX.ONCHAIN.HOP_QUOTE.POOL_NOT_INITIALIZED",
    "not_enough_liquidity": "DEX.ONCHAIN.HOP_QUOTE.NOT_ENOUGH_LIQUIDITY",
    "rpc_verification_failed": "DEX.ONCHAIN.RPC_FAILED",
    "rpc_verification_timeout": "DEX.ONCHAIN.RPC_TIMEOUT",
    "adapter_required": "DEX.ONCHAIN.ADAPTER_REQUIRED",
    "missing_trigger_block": "DEX.ONCHAIN.TRIGGER_BLOCK_MISSING",
    "not_profitable_after_costs": "DEX.SIZE_PROBE.NOT_PROFITABLE_AFTER_COSTS",
    "route_unprofitable": "DEX.SIZE_PROBE.NOT_PROFITABLE_AFTER_COSTS",
    "pool_insufficient_for_profitable_execution": "DEX.GATE.NOT_READY",
    "slippage_limit_exceeded": "DEX.GATE.NOT_READY",
    "live_evidence_deferred": "DEX.GATE.NOT_READY",
    "live_quote_reference_only": "DEX.GATE.NOT_READY",
    "missing_effective_pool": "DEX.GATE.NOT_READY",
    "depth_insufficient": "DEX.GATE.NOT_READY",
}

DEX_ERROR_KEY_CODES = {
    "DEX.QUOTE.LIVE_QUOTE_FAILED": ErrorCode.DEX_LIVE_QUOTE_FAILED,
    "DEX.ONCHAIN.HOP_QUOTE.POOL_NOT_INITIALIZED": ErrorCode.DEX_POOL_NOT_INITIALIZED,
    "DEX.ONCHAIN.HOP_QUOTE.NOT_ENOUGH_LIQUIDITY": ErrorCode.DEX_NOT_ENOUGH_LIQUIDITY,
    "DEX.ONCHAIN.RPC_FAILED": ErrorCode.DEX_RPC_FAILED,
    "DEX.ONCHAIN.RPC_TIMEOUT": ErrorCode.DEX_RPC_FAILED,
    "DEX.ONCHAIN.ADAPTER_REQUIRED": ErrorCode.DEX_GATE_NOT_READY,
    "DEX.ONCHAIN.TRIGGER_BLOCK_MISSING": ErrorCode.DEX_GATE_NOT_READY,
    "DEX.SIZE_PROBE.POOL_NOT_INITIALIZED": ErrorCode.DEX_SIZE_POOL_NOT_INITIALIZED,
    "DEX.SIZE_PROBE.NOT_PROFITABLE_AFTER_COSTS": ErrorCode.DEX_SIZE_NOT_PROFITABLE,
    "DEX.GATE.NOT_READY": ErrorCode.DEX_GATE_NOT_READY,
    "DEX.ROUTE.INVALID": ErrorCode.DEX_ROUTE_INVALID,
    "DEX.RPC.FAILED": ErrorCode.DEX_RPC_FAILED,
    "DEX.INTERNAL": ErrorCode.DEX_INTERNAL,
}


def dex_error_stack_from_state(
    status: object,
    *,
    message: object = None,
    details: dict[str, Any] | None = None,
    flow: str = "dex_route",
) -> list[dict[str, Any]]:
    return _dex_error_stack_from_status(
        status,
        message=message,
        raw_error=message,
        details=details,
        flow=flow,
        node_id=str((details or {}).get("hop") or ""),
    )


def _dex_error_stack_from_status(
    status: object,
    *,
    message: object = None,
    raw_error: object = None,
    details: dict[str, Any] | None = None,
    flow: str = "onchain_quote",
    node_id: str = "evidence",
) -> list[dict[str, Any]]:
    status_text = str(status or "").strip().lower()
    detail_values = dict(details or {})
    error_key = DEX_STATUS_ERROR_KEYS.get(status_text, "DEX.GATE.NOT_READY" if status_text and status_text != "live_verified" else "CORE.OK")
    stack = ErrorStack()
    if status_text and status_text != "live_verified":
        node_label = str(detail_values.get("hop") or node_id or "")
        reason = str(message or error_key)
        if node_label and node_label not in reason:
            reason = f"[{flow}:{node_label}] {reason}"
        stack.push(
            ModuleError(
                module="DEX",
                code=int(DEX_ERROR_KEY_CODES.get(error_key, ErrorCode.DEX_INTERNAL)),
                message=reason,
            )
        )
    else:
        stack.mark_ok(ModuleError(module="DEX", code=0, message="OK"))
    return stack.as_list()


def _dex_error_stack_from_hop(hop: dict[str, Any], *, index: int = 0, flow: str = "onchain_quote") -> list[dict[str, Any]]:
    status = str(hop.get("status") or "").strip().lower()
    raw = hop.get("liveQuoteError")
    message = raw or status
    key = DEX_STATUS_ERROR_KEYS.get(
        "not_enough_liquidity"
        if _is_not_enough_liquidity_error(ValueError(str(raw)))
        else "pool_not_initialized"
        if _is_pool_not_initialized_error(ValueError(str(raw)))
        else "adapter_required"
        if _is_missing_v4_adapter_error(ValueError(str(raw)))
        else status,
        "DEX.QUOTE.LIVE_QUOTE_FAILED" if status in {"live_quote_failed", "quote_failed"} else "CORE.OK",
    )
    stack = None
    if status in {"live_quote_failed", "quote_failed", "live_evidence_deferred", "pool_not_initialized", "not_enough_liquidity", "adapter_required"}:
        stack = ErrorStack()
        hop_num = hop.get("hop") or index
        symbols = f"{_norm_symbol(hop.get('fromSymbol'))}->{_norm_symbol(hop.get('toSymbol'))}"
        code = int(DEX_ERROR_KEY_CODES.get(key, ErrorCode.DEX_LIVE_QUOTE_FAILED))
        stack.push(
            ModuleError(
                module="DEX",
                code=code,
                message=f"hop {hop_num} {symbols} verification failed",
            )
        )
        raw_text = str(message or "").strip()
        if raw_text.startswith(f"hop {hop_num} {symbols}:"):
            raw_text = raw_text.split(":", 1)[1].strip()
        layers: list[str] = []
        for segment in raw_text.split(";"):
            segment = segment.strip()
            if not segment:
                continue
            lowered = segment.lower()
            if "reverted" in lowered and not segment.endswith("reverted"):
                before, after = segment.split("reverted", 1)
                layers.append((before + "reverted").strip())
                if after.lstrip(": ").strip():
                    layers.extend(part.strip() for part in after.lstrip(": ").split("->") if part.strip())
            elif "exact-input call failed" in lowered or "reverted" in lowered:
                layers.append(segment)
            else:
                layers.extend(part.strip() for part in segment.split("->") if part.strip())
        for layer in layers:
            if layer.startswith("hop ") and "verification failed" in layer.lower():
                continue
            stack.push(
                ModuleError(
                    module="DEX",
                    code=code,
                    message=layer,
                )
            )
    return stack.as_list() if stack is not None else []


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _has_not_enough_liquidity_blocker(evidence: dict[str, Any]) -> bool:
    hops = evidence.get("hops") or []
    if any(_text(hop.get("status")).lower() == "not_enough_liquidity" for hop in hops if isinstance(hop, dict)):
        return True
    blockers = [str(item) for item in evidence.get("blockers") or [] if str(item).strip()]
    if not blockers:
        return False
    return _is_not_enough_liquidity_error(ValueError(" ".join(blockers)))


def _number(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal amount: {value!r}") from exc


def _raw_from_human(value: object, decimals: int) -> int:
    amount = _decimal(value)
    if amount <= 0:
        raise ValueError("amount must be positive")
    return int((amount * (Decimal(10) ** int(decimals))).to_integral_value())


def _human_from_raw(value: int, decimals: int) -> str:
    return format((Decimal(int(value)) / (Decimal(10) ** int(decimals))).normalize(), "f")


def _positive_decimal_or_none(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    parsed = _decimal(value)
    return parsed if parsed > 0 else None


def _checksum(value: object) -> str:
    return Web3.to_checksum_address(str(value))


def _protocol_key(*values: object) -> str:
    text = " ".join(_text(value).lower() for value in values)
    if "v4" in text:
        return "uniswap_v4"
    if "v3" in text:
        return "uniswap_v3"
    if "v2" in text:
        return "uniswap_v2"
    return text.strip() or "unknown"


def _unsupported_step_reason(step: dict[str, Any], pool: dict[str, Any]) -> str | None:
    protocol = _protocol_key(step.get("protocol"), step.get("dex"), step.get("fee"), pool.get("protocol"), pool.get("dex"))
    if protocol not in {"uniswap_v2", "uniswap_v3", "uniswap_v4"}:
        return f"绗?{step.get('hop')} 璺冲崗璁?{step.get('protocol') or step.get('dex') or step.get('fee')} 灏氭湭鎺ュ叆閾句笂澶嶆牳"
    return None


def _fee_bps(value: object) -> int:
    text = _text(value).lower().replace("bps", "")
    try:
        parsed = float(text)
    except ValueError:
        return 30
    if parsed >= 100:
        return int(parsed / 100)
    return int(parsed)


def _pool_map(route: dict[str, Any]) -> dict[str, dict[str, Any]]:
    quote_inputs = route.get("quoteInputs") if isinstance(route.get("quoteInputs"), dict) else {}
    pools = []
    for key in ("routePools", "candidatePools"):
        if isinstance(quote_inputs.get(key), list):
            pools.extend(quote_inputs.get(key) or [])
    for key in ("executionPools", "preferredPools", "strictQualityPools"):
        if isinstance(route.get(key), list):
            pools.extend(route.get(key) or [])
    mapping: dict[str, dict[str, Any]] = {}
    for pool in pools:
        if isinstance(pool, dict) and _text(pool.get("pool")):
            mapping[_text(pool.get("pool")).lower()] = pool
    return mapping


def _pool_for_step(step: dict[str, Any], pools: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return pools.get(_text(step.get("pool")).lower(), {})


def _load_v4_quote_adapter(*, network: str | None = None, chain_id: int | None = None) -> Any | None:
    normalized_network = _robinhood_network_for_chain_id(chain_id, network)
    is_testnet = bool(
        normalized_network in {ROBINHOOD_TESTNET_NETWORK, "robinhood_sepolia", "robinhood_testnet"}
        or int(chain_id or 0) == ROBINHOOD_TESTNET_CHAIN_ID
    )
    is_mainnet = bool(
        normalized_network in {"robinhood", "robinhood-mainnet", "robinhood_mainnet"}
        or int(chain_id or 0) == robinhood.CHAIN_ID
    )
    if is_testnet:
        preferred_names = (
            "ROBINHOOD_TESTNET_V4_QUOTE_ADAPTER",
            "ROBINHOOD_V4_QUOTE_ADAPTER",
            "UNISWAP_V4_QUOTE_ADAPTER",
            "DEX_V4_QUOTE_ADAPTER",
        )
    elif is_mainnet:
        preferred_names = (
            "ROBINHOOD_MAINNET_V4_QUOTE_ADAPTER",
            "ROBINHOOD_V4_QUOTE_ADAPTER",
            "UNISWAP_V4_QUOTE_ADAPTER",
            "DEX_V4_QUOTE_ADAPTER",
        )
    else:
        preferred_names = (
            "ROBINHOOD_V4_QUOTE_ADAPTER",
            "UNISWAP_V4_QUOTE_ADAPTER",
            "DEX_V4_QUOTE_ADAPTER",
        )
    reference = ""
    for name in preferred_names:
        candidate = str(os.getenv(name) or "").strip()
        if candidate:
            reference = candidate
            break
    if reference:
        module_name, separator, attr_path = str(reference).strip().partition(":")
        if not module_name or not separator or not attr_path:
            raise ValueError("UNISWAP_V4_QUOTE_ADAPTER must use module:callable format")
        target: Any = importlib.import_module(module_name)
        for part in attr_path.split("."):
            target = getattr(target, part)
        return target
    return load_v4_sdk_adapter(network=network, chain_id=chain_id)


def _pool_side(pool: dict[str, Any], symbol: str) -> tuple[str | None, str | None, int | None]:
    symbol = _norm_symbol(symbol)
    for index in (0, 1):
        if _norm_symbol(pool.get(f"token{index}Symbol")) == symbol:
            address = _text(pool.get(f"token{index}"))
            decimals = pool.get(f"token{index}Decimals") or pool.get(f"token{index}_decimals")
            return address or None, _norm_symbol(pool.get(f"token{index}Symbol")), int(decimals) if decimals not in (None, "") else None
    return None, symbol, None


def _pool_side_by_address(pool: dict[str, Any], address: object, symbol: str) -> tuple[str | None, str | None, int | None]:
    wanted = _text(address).lower()
    if not wanted:
        return _pool_side(pool, symbol)
    for index in (0, 1):
        candidate = _text(pool.get(f"token{index}")).lower()
        if candidate == wanted:
            decimals = pool.get(f"token{index}Decimals") or pool.get(f"token{index}_decimals")
            return _text(pool.get(f"token{index}")) or None, _norm_symbol(pool.get(f"token{index}Symbol")), int(decimals) if decimals not in (None, "") else None
    return _text(address), _norm_symbol(symbol), None


def _token_decimals(w3: Any, address: str | None, symbol: str, cache: dict[str, int]) -> int:
    symbol = _norm_symbol(symbol)
    key = (address or symbol).lower()
    if key in cache:
        return cache[key]
    if symbol in _TOKEN_DECIMALS_FALLBACK:
        cache[key] = _TOKEN_DECIMALS_FALLBACK[symbol]
        return cache[key]
    if address:
        contract = w3.eth.contract(address=_checksum(address), abi=ERC20_ABI)
        cache[key] = int(contract.functions.decimals().call())
        return cache[key]
    cache[key] = 18
    return cache[key]


def _pool_state_v2(w3: Any, pool_address: str, block_number: int) -> dict[str, Any]:
    contract = w3.eth.contract(address=_checksum(pool_address), abi=V2_PAIR_ABI)
    token0 = _checksum(contract.functions.token0().call(block_identifier=block_number))
    token1 = _checksum(contract.functions.token1().call(block_identifier=block_number))
    reserve0, reserve1, block_timestamp = contract.functions.getReserves().call(block_identifier=block_number)
    return {
        "protocol": "uniswap_v2",
        "pool": _checksum(pool_address),
        "token0": token0,
        "token1": token1,
        "reserve0Raw": str(int(reserve0)),
        "reserve1Raw": str(int(reserve1)),
        "blockTimestampLast": int(block_timestamp),
        "blockNumber": int(block_number),
    }


def _quote_v2_hop(*, w3: Any, step: dict[str, Any], pool: dict[str, Any], amount_in_raw: int, block_number: int, token_cache: dict[str, int]) -> dict[str, Any]:
    pool_address = _text(step.get("pool"))
    from_symbol = _norm_symbol(step.get("fromSymbol"))
    to_symbol = _norm_symbol(step.get("toSymbol"))
    token_in, _, in_decimals = _pool_side_by_address(pool, step.get("tokenIn"), from_symbol)
    token_out, _, out_decimals = _pool_side_by_address(pool, step.get("tokenOut"), to_symbol)
    state = _pool_state_v2(w3, pool_address, block_number)
    if not token_in or not token_out:
        raise ValueError(f"missing token address for {from_symbol}->{to_symbol}")
    token_in = _checksum(token_in)
    token_out = _checksum(token_out)
    if {token_in.lower(), token_out.lower()} != {state["token0"].lower(), state["token1"].lower()}:
        raise ValueError("V2 pool token addresses do not match route step")
    reserve_in = int(state["reserve0Raw"] if token_in.lower() == state["token0"].lower() else state["reserve1Raw"])
    reserve_out = int(state["reserve1Raw"] if token_in.lower() == state["token0"].lower() else state["reserve0Raw"])
    fee_bps = _fee_bps(step.get("feeBps") or step.get("fee") or pool.get("fee"))
    fee_denominator = 10_000
    amount_in_after_fee = int(amount_in_raw) * max(0, fee_denominator - fee_bps)
    amount_out_raw = (reserve_out * amount_in_after_fee) // (reserve_in * fee_denominator + amount_in_after_fee)
    in_decimals = in_decimals if in_decimals is not None else _token_decimals(w3, token_in, from_symbol, token_cache)
    out_decimals = out_decimals if out_decimals is not None else _token_decimals(w3, token_out, to_symbol, token_cache)
    return {
        "status": "live_verified",
        "quoteMethod": "v2_pair_reserves_constant_product_at_block",
        "poolState": state,
        "protocol": "uniswap_v2",
        "pool": state["pool"],
        "tokenIn": token_in,
        "tokenOut": token_out,
        "feeBps": fee_bps,
        "amountInRaw": str(int(amount_in_raw)),
        "amountOutRaw": str(int(amount_out_raw)),
        "amountIn": _human_from_raw(amount_in_raw, in_decimals),
        "amountOut": _human_from_raw(amount_out_raw, out_decimals),
        "inputDecimals": in_decimals,
        "outputDecimals": out_decimals,
        "quoterGasEstimate": None,
    }


def _quote_v3_hop(*, w3: Any, quoter: str, step: dict[str, Any], pool: dict[str, Any], amount_in_raw: int, block_number: int, token_cache: dict[str, int]) -> dict[str, Any]:
    pool_address = _text(step.get("pool"))
    from_symbol = _norm_symbol(step.get("fromSymbol"))
    to_symbol = _norm_symbol(step.get("toSymbol"))
    token_in, _, in_decimals = _pool_side_by_address(pool, step.get("tokenIn"), from_symbol)
    token_out, _, out_decimals = _pool_side_by_address(pool, step.get("tokenOut"), to_symbol)
    if not token_in or not token_out:
        raise ValueError(f"missing token address for {from_symbol}->{to_symbol}")
    snapshot_fn = _evidence_attr_or_default("snapshot_v3_pool", snapshot_v3_pool)
    quote_fn = _evidence_attr_or_default("quote_v3_exact_input_single", quote_v3_exact_input_single)
    snapshot = snapshot_fn(w3, pool_address, block_identifier=block_number)
    token_in = _checksum(token_in)
    token_out = _checksum(token_out)
    if {token_in.lower(), token_out.lower()} != {snapshot.token0.lower(), snapshot.token1.lower()}:
        raise ValueError("V3 pool token addresses do not match route step")
    amount_out, sqrt_after, ticks_crossed, gas_estimate = quote_fn(
        w3,
        quoter,
        token_in,
        token_out,
        snapshot.fee,
        int(amount_in_raw),
        block_identifier=block_number,
    )
    in_decimals = in_decimals if in_decimals is not None else _token_decimals(w3, token_in, from_symbol, token_cache)
    out_decimals = out_decimals if out_decimals is not None else _token_decimals(w3, token_out, to_symbol, token_cache)
    return {
        "status": "live_verified",
        "quoteMethod": "v3_quoter_quoteExactInputSingle_eth_call",
        "poolState": {
            "protocol": "uniswap_v3",
            "pool": snapshot.pool_address,
            "token0": snapshot.token0,
            "token1": snapshot.token1,
            "fee": snapshot.fee,
            "liquidity": str(snapshot.liquidity),
            "sqrtPriceX96": str(snapshot.sqrt_price_x96),
            "tick": snapshot.tick,
            "blockNumber": int(block_number),
        },
        "protocol": "uniswap_v3",
        "pool": snapshot.pool_address,
        "tokenIn": token_in,
        "tokenOut": token_out,
        "fee": snapshot.fee,
        "amountInRaw": str(int(amount_in_raw)),
        "amountOutRaw": str(int(amount_out)),
        "amountIn": _human_from_raw(amount_in_raw, in_decimals),
        "amountOut": _human_from_raw(amount_out, out_decimals),
        "inputDecimals": in_decimals,
        "outputDecimals": out_decimals,
        "sqrtPriceX96After": str(sqrt_after),
        "initializedTicksCrossed": int(ticks_crossed),
        "quoterGasEstimate": int(gas_estimate),
    }


def _quote_v4_hop(
    *,
    w3: Any,
    step: dict[str, Any],
    pool: dict[str, Any],
    amount_in_raw: int,
    block_number: int,
    token_cache: dict[str, int],
    chain_id: int | None = None,
    network: str | None = None,
) -> dict[str, Any]:
    pool_address = _text(step.get("pool"))
    from_symbol = _norm_symbol(step.get("fromSymbol"))
    to_symbol = _norm_symbol(step.get("toSymbol"))
    token_in, _, in_decimals = _pool_side_by_address(pool, step.get("tokenIn"), from_symbol)
    token_out, _, out_decimals = _pool_side_by_address(pool, step.get("tokenOut"), to_symbol)
    if not token_in or not token_out:
        raise ValueError(f"missing token address for {from_symbol}->{to_symbol}")
    in_decimals = in_decimals if in_decimals is not None else _token_decimals(w3, token_in, from_symbol, token_cache)
    out_decimals = out_decimals if out_decimals is not None else _token_decimals(w3, token_out, to_symbol, token_cache)
    reference_amount_out_raw = None
    reference_output_amount = step.get("referenceOutputAmount")
    if reference_output_amount not in (None, ""):
        try:
            reference_amount_out_raw = _raw_from_human(reference_output_amount, out_decimals)
        except Exception:
            reference_amount_out_raw = None
    raw_network = _text(network or pool.get("network") or "")
    request_network = _robinhood_network_for_chain_id(chain_id, raw_network or robinhood.NETWORK)
    if chain_id is None and request_network == ROBINHOOD_TESTNET_NETWORK:
        chain_id = ROBINHOOD_TESTNET_CHAIN_ID
    elif chain_id is None and request_network == robinhood.NETWORK:
        chain_id = robinhood.CHAIN_ID
    request = build_v4_quote_request(
        network=request_network,
        chain_id=chain_id,
        pool=pool_address,
        token_in=_checksum(token_in),
        token_out=_checksum(token_out),
        amount_in_raw=int(amount_in_raw),
        block_number=block_number,
        fee=step.get("fee") or pool.get("fee"),
        route_symbols=(from_symbol, to_symbol),
        reference_amount_out_raw=reference_amount_out_raw,
    )
    pool_token0, pool_token0_symbol, pool_token0_decimals = _pool_side(pool, _norm_symbol(pool.get("token0Symbol")))
    pool_token1, pool_token1_symbol, pool_token1_decimals = _pool_side(pool, _norm_symbol(pool.get("token1Symbol")))
    request["poolState"] = {
        "pool": pool_address,
        "token0": _checksum(pool_token0 or token_in),
        "token1": _checksum(pool_token1 or token_out),
        "token0Symbol": pool_token0_symbol or from_symbol,
        "token1Symbol": pool_token1_symbol or to_symbol,
        "token0Decimals": pool_token0_decimals if pool_token0_decimals is not None else in_decimals,
        "token1Decimals": pool_token1_decimals if pool_token1_decimals is not None else out_decimals,
        "tickSpacing": pool.get("tickSpacing"),
        "sqrtPriceX96": pool.get("sqrtPriceX96") or pool.get("sqrt_price_x96"),
        "liquidity": pool.get("liquidity"),
    }
    adapter_loader = _evidence_attr_or_default("_load_v4_quote_adapter", _load_v4_quote_adapter)
    adapter = adapter_loader(network=request_network, chain_id=chain_id)
    if adapter is None:
        chain_label = network or (_text(chain_id) or "robinhood")
        raise RuntimeError(
            f"{chain_label} V4 quote adapter is required; missing pool manager/quoter configuration"
        )
    quote = quote_v4_exact_input_single(
        adapter=adapter,
        request=request,
    )
    amount_out = int(quote["amountOutRaw"])
    quote_pool_state = quote.get("poolState") if isinstance(quote.get("poolState"), dict) else {}
    merged_pool_state = {**request["poolState"], **quote_pool_state}
    return {
        "status": "live_verified",
        "quoteMethod": quote.get("quoteMethod") or "uniswap_v4_sdk_exact_input_single",
        "quoteMode": quote.get("quoteMode") or "uniswap_v4_sdk_exact_input_single",
        "poolState": merged_pool_state,
        "protocol": "uniswap_v4",
        "pool": pool_address,
        "tokenIn": _checksum(token_in),
        "tokenOut": _checksum(token_out),
        "fee": step.get("fee") or pool.get("fee"),
        "amountInRaw": str(int(amount_in_raw)),
        "amountOutRaw": str(amount_out),
        "amountIn": _human_from_raw(amount_in_raw, in_decimals),
        "amountOut": _human_from_raw(amount_out, out_decimals),
        "inputDecimals": in_decimals,
        "outputDecimals": out_decimals,
        "quoterGasEstimate": int(quote.get("quoterGasEstimate") or quote.get("gasEstimate") or 0),
        "sdkAdapter": quote.get("sdkAdapter") or quote.get("quoteAdapter"),
        "priceImpactBps": quote.get("priceImpactBps"),
        "priceImpactSource": quote.get("priceImpactSource"),
    }


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


def _connect_robinhood_family(chain_id: int | None, network: str) -> tuple[Any, str]:
    normalized_network = _robinhood_network_for_chain_id(chain_id, network)
    if normalized_network == ROBINHOOD_TESTNET_NETWORK:
        failures: list[str] = []
        timeout = float(os.getenv("DEX_ONCHAIN_RPC_TIMEOUT_SECONDS", "12") or 12)
        attempts_per_url = max(1, int(os.getenv("DEX_ONCHAIN_RPC_CONNECT_ATTEMPTS", "3") or 3))
        for url in robinhood.testnet_rpc_urls_from_env():
            for attempt in range(attempts_per_url):
                w3 = Web3(
                    Web3.HTTPProvider(
                        url,
                        request_kwargs={
                            "timeout": timeout,
                            "headers": {
                                "Content-Type": "application/json",
                                "User-Agent": "flashloan-robinhood-testnet/1.1",
                                "Connection": "close",
                            },
                        },
                    )
                )
                try:
                    if not w3.is_connected():
                        failures.append(f"{url} attempt={attempt + 1}: not connected")
                    else:
                        actual_chain_id = int(w3.eth.chain_id)
                        if actual_chain_id == ROBINHOOD_TESTNET_CHAIN_ID:
                            return w3, url
                        failures.append(f"{url} attempt={attempt + 1}: chain_id mismatch {actual_chain_id}, expected {ROBINHOOD_TESTNET_CHAIN_ID}")
                except Exception as exc:
                    failures.append(f"{url} attempt={attempt + 1}: {exc}")
                if attempt + 1 < attempts_per_url:
                    time.sleep(0.75 * (attempt + 1))
        raise RuntimeError("Robinhood Testnet RPC unavailable: " + "; ".join(failures[:3]))
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


def _token_continuity_issues(hops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for index in range(1, len(hops)):
        previous = hops[index - 1]
        current = hops[index]
        previous_token_out = _text(previous.get("tokenOut"))
        current_token_in = _text(current.get("tokenIn"))
        if previous_token_out.lower() != current_token_in.lower():
            issues.append(
                {
                    "fromHop": previous.get("hop") or index,
                    "toHop": current.get("hop") or index + 1,
                    "fromSymbol": previous.get("toSymbol"),
                    "toSymbol": current.get("fromSymbol"),
                    "previousTokenOut": previous_token_out,
                    "currentTokenIn": current_token_in,
                }
            )
    return issues


def _spot_amount_out_raw(hop: dict[str, Any]) -> int | None:
    """Return the zero-size spot output in raw units from the live pool state."""
    state = hop.get("poolState") if isinstance(hop.get("poolState"), dict) else {}
    token_in = _text(hop.get("tokenIn")).lower()
    token_out = _text(hop.get("tokenOut")).lower()
    token0 = _text(state.get("token0")).lower()
    token1 = _text(state.get("token1")).lower()
    if not token_in or not token_out or {token_in, token_out} != {token0, token1}:
        return None
    amount_in_raw = _int_or_none(hop.get("amountInRaw"))
    if amount_in_raw is None or amount_in_raw <= 0:
        return None
    if "reserve0Raw" in state and "reserve1Raw" in state:
        reserve_in = _int_or_none(state.get("reserve0Raw" if token_in == token0 else "reserve1Raw"))
        reserve_out = _int_or_none(state.get("reserve1Raw" if token_in == token0 else "reserve0Raw"))
        if reserve_in and reserve_out and reserve_in > 0:
            return (amount_in_raw * reserve_out) // reserve_in
        return None
    sqrt_price_x96 = _int_or_none(state.get("sqrtPriceX96"))
    if sqrt_price_x96 is None or sqrt_price_x96 <= 0:
        return None
    q192 = 1 << 192
    if token_in == token0:
        return (amount_in_raw * sqrt_price_x96 * sqrt_price_x96) // q192
    return (amount_in_raw * q192) // (sqrt_price_x96 * sqrt_price_x96)


def _hop_fee_bps(hop: dict[str, Any], step: dict[str, Any], pool: dict[str, Any]) -> int:
    return _fee_bps(
        hop.get("feeBps")
        or hop.get("fee")
        or step.get("feeBps")
        or step.get("fee")
        or pool.get("feeBps")
        or pool.get("fee")
    )


def _annotate_hop_price_impact(
    hop: dict[str, Any],
    *,
    step: dict[str, Any],
    pool: dict[str, Any],
    limit_bps: int = DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS,
) -> dict[str, Any]:
    """Measure price impact against live spot, excluding the explicitly reported LP fee."""
    annotated = dict(hop)
    amount_out_raw = _int_or_none(hop.get("amountOutRaw"))
    spot_amount_out_raw = _spot_amount_out_raw(hop)
    supplied_impact = _number(hop.get("priceImpactBps"))
    fee_bps = _hop_fee_bps(hop, step, pool)
    annotated["feeBps"] = fee_bps
    annotated["priceImpactLimitBps"] = int(limit_bps)
    annotated["priceImpactSource"] = "live_pool_spot_price_excluding_lp_fee"
    annotated["priceImpactFeeExcluded"] = True
    if supplied_impact is not None:
        annotated["priceImpactBps"] = supplied_impact
        annotated["priceImpactWithinLimit"] = supplied_impact <= limit_bps
        annotated["priceImpactSource"] = hop.get("priceImpactSource") or "quote_provider"
        return annotated
    if amount_out_raw is None or spot_amount_out_raw is None or spot_amount_out_raw <= 0:
        annotated.update(
            {
                "spotAmountOutRaw": str(spot_amount_out_raw) if spot_amount_out_raw is not None else None,
                "priceImpactBps": None,
                "priceImpactWithinLimit": False,
                "priceImpactError": "live pool spot price is unavailable for this hop",
            }
        )
        return annotated
    expected_after_fee_raw = spot_amount_out_raw * max(0, 10_000 - fee_bps) // 10_000
    if expected_after_fee_raw <= 0:
        annotated.update(
            {
                "spotAmountOutRaw": str(spot_amount_out_raw),
                "expectedAmountOutAfterFeeRaw": str(expected_after_fee_raw),
                "priceImpactBps": None,
                "priceImpactWithinLimit": False,
                "priceImpactError": "live spot output after fee is non-positive",
            }
        )
        return annotated
    impact_bps = max(
        Decimal("0"),
        (Decimal(expected_after_fee_raw) - Decimal(amount_out_raw))
        / Decimal(expected_after_fee_raw)
        * Decimal(10_000),
    )
    annotated.update(
        {
            "spotAmountOutRaw": str(spot_amount_out_raw),
            "expectedAmountOutAfterFeeRaw": str(expected_after_fee_raw),
            "priceImpactBps": float(impact_bps),
            "priceImpactWithinLimit": bool(impact_bps <= Decimal(int(limit_bps))),
        }
    )
    return annotated


def _offchain_initial_probe_amount_raw(
    route: dict[str, Any],
    *,
    requested_amount_raw: int,
    input_decimals: int,
    analysis: dict[str, Any] | None = None,
    pools: dict[str, dict[str, Any]] | None = None,
    price_map: dict[str, float] | None = None,
) -> tuple[int, str, str, list[str]]:
    """Estimate a conservative on-chain probe start in borrow-token raw units.

    The estimate runs off-chain so the Solidity-facing path can simply accept
    ``initialProbeAmountRaw`` and then halve or double from that starting point.
    """

    reasons: list[str] = []
    candidates: list[int] = []
    route_analysis = analysis if isinstance(analysis, dict) else _as_dict(route.get("quoteAnalysis"))
    size_analysis = _as_dict(route_analysis.get("sizeAnalysis")) or _as_dict(route.get("sizeAnalysis"))
    pools = pools if isinstance(pools, dict) else _pool_map(route)
    price_map = price_map or {}
    input_symbol = _norm_symbol(
        route_analysis.get("inputSymbol")
        or (route.get("routeSymbols") or [None])[0]
        or route.get("tokenXSymbol")
    )
    steps = [step for step in route_analysis.get("steps") or [] if isinstance(step, dict)]

    explicit_raw = _int_or_none(
        size_analysis.get("offchainInitialProbeAmountRaw")
        or size_analysis.get("initialProbeAmountRaw")
        or route_analysis.get("offchainInitialProbeAmountRaw")
        or route_analysis.get("initialProbeAmountRaw")
        or route.get("offchainInitialProbeAmountRaw")
        or route.get("initialProbeAmountRaw")
    )
    if explicit_raw is not None and explicit_raw > 0:
        candidates.append(explicit_raw)
        reasons.append("explicit_offchain_initial_probe_amount")

    recommended_raw = _int_or_none(
        size_analysis.get("recommendedBorrowInputAmountRaw")
        or route_analysis.get("recommendedBorrowInputAmountRaw")
        or route.get("recommendedBorrowInputAmountRaw")
    )
    if recommended_raw is not None and recommended_raw > 0:
        candidates.append(recommended_raw)
        reasons.append("recommended_borrow_ladder")

    execution_probe = _as_dict(
        size_analysis.get("executionSizeProbe")
        or size_analysis.get("routeExecutionSizeProbe")
        or route_analysis.get("executionSizeProbe")
    )

    # When an off-chain size probe has already bounded the executable borrow
    # amount, start the on-chain probe just above that bound so the doubling
    # search can quickly re-confirm the boundary instead of blindly halving
    # from a fixed 1-token fallback.
    max_executable_raw = _int_or_none(
        size_analysis.get("maxExecutableAmountRaw")
        or (execution_probe.get("maxExecutableAmountRaw") if execution_probe else None)
        or route.get("maxExecutableAmountRaw")
    )
    if max_executable_raw is not None and max_executable_raw > 0:
        candidates.append(max(1, int(max_executable_raw) * 2))
        reasons.append("size_analysis_max_executable_double")

    # If per-hop execution probes already know the maximum executable input for
    # each pool, convert those into borrow-token raw units and keep the minimum.
    step_probes = [row for row in execution_probe.get("stepProbes") or [] if isinstance(row, dict)]
    if step_probes:
        cumulative_rate: Decimal | None = None
        for index, step in enumerate(steps):
            hop_number = int(step.get("hop") or index + 1)
            probe_row = next((row for row in step_probes if int(row.get("step") or 0) == hop_number), None)
            if not probe_row:
                continue
            max_in_raw = _int_or_none(probe_row.get("maxExecutableAmountRaw"))
            if max_in_raw is None or max_in_raw <= 0:
                continue
            step_input_decimals = int(step.get("inputDecimals") or 18)
            borrow_equivalent: Decimal | None = Decimal(int(max_in_raw))
            if input_decimals != step_input_decimals:
                borrow_equivalent *= Decimal(10) ** int(input_decimals - step_input_decimals)
            if index > 0 and cumulative_rate is not None and cumulative_rate > 0:
                borrow_equivalent = borrow_equivalent / cumulative_rate
            candidates.append(max(1, int(borrow_equivalent)))
            reasons.append(f"per_hop_execution_probe_step_{hop_number}")

            step_rate = _step_token_rate(step)
            if step_rate is not None:
                cumulative_rate = step_rate if cumulative_rate is None else cumulative_rate * step_rate

    # Fall back to a conservative slice of the smallest pool's total liquidity.
    # For pools that directly hold the borrow token we can use the token side
    # balance; otherwise use USD TVL and the borrow-token price when available.
    min_tvl_usd = None
    for index, step in enumerate(steps):
        pool = _pool_for_step(step, pools)
        if not pool:
            pool = step.get("poolState") if isinstance(step.get("poolState"), dict) else {}
        tvl = _number((pool.get("balances") if isinstance(pool.get("balances"), dict) else {}).get("tvlUsd"))
        if tvl is None:
            tvl = _number(pool.get("tvlUsd") or pool.get("tvl_usd") or pool.get("depth_score_usd"))
        if tvl is not None and tvl > 0 and (min_tvl_usd is None or tvl < min_tvl_usd):
            min_tvl_usd = tvl

        borrow_side_amount = _pool_borrow_side_amount(pool, input_symbol)
        if borrow_side_amount is not None and borrow_side_amount > 0:
            candidate_raw = _raw_from_human(borrow_side_amount, input_decimals) // 100
            candidates.append(max(1, candidate_raw))
            reasons.append(f"pool_{index + 1}_borrow_side_reserve_1pct")
            continue
        token0_symbol = _norm_symbol(pool.get("token0Symbol"))
        token1_symbol = _norm_symbol(pool.get("token1Symbol"))
        token0_usd = _number((pool.get("balances") if isinstance(pool.get("balances"), dict) else {}).get("token0Usd"))
        token1_usd = _number((pool.get("balances") if isinstance(pool.get("balances"), dict) else {}).get("token1Usd"))
        if tvl is not None and tvl > 0:
            input_usd = token0_usd if _norm_symbol(token0_symbol) == input_symbol else token1_usd
            input_price = price_map.get(input_symbol) if input_symbol else None
            if input_usd is not None and input_price is not None and input_price > 0:
                candidates.append(max(1, _raw_from_human(Decimal(str(input_usd / 100 / input_price)), input_decimals)))
                reasons.append(f"pool_{index + 1}_usd_side_1pct")
            elif input_symbol and input_price is not None and input_price > 0:
                candidates.append(max(1, _raw_from_human(Decimal(str(tvl / 100 / price_map[input_symbol])), input_decimals)))
                reasons.append(f"pool_{index + 1}_tvl_1pct")

    if min_tvl_usd is not None and min_tvl_usd > 0 and input_symbol and price_map.get(input_symbol):
        candidates.append(max(1, _raw_from_human(Decimal(str(min_tvl_usd / 100 / price_map[input_symbol])), input_decimals)))
        reasons.append("minimum_pool_tvl_1pct")

    if candidates:
        selected = min(candidates)
        basis = "borrow_token"
        # The candidate may intentionally exceed the original requested size
        # (for example when it is the doubled size-analysis boundary). Keep a
        # generous safety ceiling instead of forcing every probe to start from
        # the original 1-ETH style requested amount.
        max_initial_probe_raw = max(int(requested_amount_raw) or 1, selected) * 64
        capped = max(1, min(max_initial_probe_raw, selected))
        return capped, basis, "; ".join(reasons) or "offchain_pool_estimate", reasons
    return max(1, int(requested_amount_raw)), "borrow_token", "requested_borrow_amount_fallback", ["requested_borrow_amount_fallback"]


def _step_token_rate(step: dict[str, Any]) -> Decimal | None:
    input_amount = _positive_decimal_or_none(step.get("referenceInputAmount") or step.get("inputAmount") or step.get("amountIn"))
    output_amount = _positive_decimal_or_none(step.get("referenceOutputAmount") or step.get("outputAmount") or step.get("amountOut"))
    if input_amount is None or output_amount is None or input_amount <= 0:
        return None
    return output_amount / input_amount


def _pool_borrow_side_amount(pool: dict[str, Any], input_symbol: str) -> Decimal | None:
    balances = pool.get("balances") if isinstance(pool.get("balances"), dict) else {}
    symbol = _norm_symbol(input_symbol)
    for index in (0, 1):
        if _norm_symbol(pool.get(f"token{index}Symbol")) == symbol:
            amount = balances.get(f"token{index}") if balances else pool.get(f"token{index}Amount")
            return _positive_decimal_or_none(amount)
    return None


def _route_size_advice(route: dict[str, Any]) -> tuple[dict[str, Any], int | None, int | None, int | None]:
    size_analysis = _as_dict(route.get("sizeAnalysis"))
    if not size_analysis:
        size_analysis = _as_dict(_as_dict(route.get("quoteAnalysis")).get("sizeAnalysis"))
    if not size_analysis:
        size_analysis = _as_dict(_as_dict(route.get("overviewRoute")).get("sizeAnalysis"))
    execution_probe = _as_dict(
        size_analysis.get("executionSizeProbe")
        or size_analysis.get("routeExecutionSizeProbe")
    )
    input_amount_unit = _text(size_analysis.get("inputAmountUnit")).lower()
    max_executable_raw = _int_or_none(
        (
            size_analysis.get("maxExecutableAmountRaw")
            or execution_probe.get("maxExecutableAmountRaw")
            or route.get("maxExecutableAmountRaw")
        )
        if input_amount_unit != "per_hop"
        else None
    )
    first_failure_raw = _int_or_none(
        (
            size_analysis.get("firstFailureAmountRaw")
            or execution_probe.get("firstFailureAmountRaw")
            or route.get("firstFailureAmountRaw")
        )
        if input_amount_unit != "per_hop"
        else None
    )
    recommended_raw = _int_or_none(
        size_analysis.get("offchainInitialProbeAmountRaw")
        or size_analysis.get("initialProbeAmountRaw")
        or size_analysis.get("recommendedBorrowInputAmountRaw")
        or execution_probe.get("recommendedBorrowInputAmountRaw")
        or execution_probe.get("offchainInitialProbeAmountRaw")
        or route.get("recommendedBorrowInputAmountRaw")
    )
    return execution_probe, max_executable_raw, first_failure_raw, recommended_raw


def _transaction_development(ready: bool, blockers: list[str]) -> dict[str, Any]:
    if ready:
        return {
            "status": "ready_for_transaction_preflight_development",
            "executionEnabled": False,
            "nextModules": [
                "build flash loan borrow entry",
                "verify route and generate AMM swap calldata",
                "add minimum repayment and slippage protection",
                "run fork/static-call simulation before allowing signed broadcast",
            ],
        }
    return {
        "status": "blocked_before_transaction_development",
        "executionEnabled": False,
        "blockers": blockers,
    }


def _probe_robinhood_testnet_route_execution_size(
    route: dict[str, Any],
    *,
    payload: dict[str, Any] | None,
    chain_id: int | None,
    observed_at: str,
    trigger_context: dict[str, Any],
    quote_hop_provider: QuoteHopProvider | None,
    requested_amount_raw: int,
    initial_probe_amount_raw: int | None = None,
    max_attempts: int | None = None,
    min_probe_amount_raw: int | None = None,
) -> dict[str, Any]:
    max_attempts = max(1, int(max_attempts or os.getenv("DEX_V4_EXECUTION_SIZE_MAX_PROBE_ATTEMPTS", str(DEFAULT_ROUTE_SIZE_PROBE_ATTEMPTS)) or DEFAULT_ROUTE_SIZE_PROBE_ATTEMPTS))
    min_probe_amount_raw = max(1, int(min_probe_amount_raw or os.getenv("DEX_V4_EXECUTION_SIZE_MIN_PROBE_AMOUNT_RAW", "1") or 1))
    max_probe_multiplier = max(1, int(os.getenv("DEX_V4_EXECUTION_SIZE_MAX_PROBE_MULTIPLIER", "64") or 64))
    target_amount_raw = max(1, int(requested_amount_raw))
    initial_probe_amount_raw = max(1, int(initial_probe_amount_raw or target_amount_raw))
    max_probe_amount_raw = min((1 << 128) - 1, max(target_amount_raw, min_probe_amount_raw) * max_probe_multiplier)
    attempts = 0
    candidate_rows: list[dict[str, Any]] = []

    def _passes(evidence: dict[str, Any]) -> bool:
        validation = _as_dict(evidence.get("validation"))
        profit_gate = validation.get("profitableAfterCosts", True)
        return bool(
            _text(evidence.get("status")).lower()
            in {"live_verified", "slippage_limit_exceeded", "route_unprofitable"}
            and validation.get("allHopsQuoted", True) is True
            and validation.get("routeClosed", True) is True
            and validation.get("tokenContinuity", True) is True
            and profit_gate is True
        )

    def _failure_reason(evidence: dict[str, Any]) -> str | None:
        validation = _as_dict(evidence.get("validation"))
        status = _text(evidence.get("status")).lower()
        if validation.get("profitableAfterCosts") is False and validation.get("allHopsQuoted") is True:
            return "not_profitable_after_costs"
        if status not in {"live_verified", "slippage_limit_exceeded", "route_unprofitable"}:
            return status or "live_quote_failed"
        if validation.get("allHopsQuoted") is not True:
            return "not_all_hops_quoted"
        if validation.get("routeClosed") is not True:
            return "route_not_closed"
        if validation.get("tokenContinuity") is not True:
            return "token_continuity_failed"
        if validation.get("profitableAfterCosts") is not True:
            return "not_profitable_after_costs"
        return None

    def _evaluate(amount_raw: int) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        from dex.runtime.dex_onchain_quote_evidence import build_route_onchain_evidence

        return build_route_onchain_evidence(
            route,
            payload=payload,
            chain_id=chain_id,
            observed_at=observed_at,
            trigger_context=trigger_context,
            quote_hop_provider=quote_hop_provider,
            borrow_input_amount_override_raw=int(amount_raw),
            skip_execution_size_probe=True,
        )

    def _record(amount_raw: int, evidence: dict[str, Any]) -> tuple[bool, str | None]:
        validation = _as_dict(evidence.get("validation"))
        costs = _as_dict(evidence.get("costModel"))
        passed = _passes(evidence)
        reason = None if passed else _failure_reason(evidence)
        candidate_rows.append(
            {
                "attempt": attempts,
                "amountRaw": str(amount_raw),
                "status": "profitable_and_within_limits" if passed else "quoted_but_not_eligible",
                "allHopsQuoted": validation.get("allHopsQuoted") is True,
                "allHopsWithinPriceImpactLimit": validation.get("allHopsWithinPriceImpactLimit") is True,
                "profitableAfterCosts": validation.get("profitableAfterCosts") is True,
                "netProfitAmount": costs.get("netProfitAmount"),
                "netProfitAmountBps": costs.get("netProfitAmountBps"),
                "blockers": list(evidence.get("blockers") or []),
                "failureReason": reason,
                "errorStack": evidence.get("errorStack") or _dex_error_stack_from_status(
                    "live_verified" if passed else (reason or "not_profitable_after_costs"),
                    message=reason or "OK",
                    details={"attempt": attempts, "amountRaw": str(amount_raw)},
                    flow="size_probe",
                    node_id="probe_attempt",
                ),
            }
        )
        return passed, reason

    def _stop_without_size(reason: str, *, raw_error: str | None = None) -> dict[str, Any]:
        search_status = (
            "not_profitable_after_costs"
            if reason == "not_profitable_after_costs"
            else reason
        )
        failure_reason = reason
        if raw_error:
            failure_reason = f"{reason}: {raw_error}"
        return {
            "status": "route_execution_size_probe_unexecutable",
            "searchStatus": search_status,
            "probeAttempts": attempts,
            "maxProbeAttempts": max_attempts,
            "requestedAmountRaw": str(target_amount_raw),
            "initialProbeAmountRaw": str(initial_probe_amount_raw),
            "selectedProfitableAmountRaw": None,
            "maxExecutableAmountRaw": None,
            "firstFailureAmountRaw": str(target_amount_raw),
            "attempts": candidate_rows,
            "failureReason": failure_reason,
            "errorStack": _dex_error_stack_from_status(
                search_status,
                message=failure_reason,
                raw_error=raw_error,
                details={"reason": reason, "searchStatus": search_status},
                flow="size_probe",
                node_id="probe_quote",
            ),
        }

    lower_bound: int | None = None
    upper_bound: int | None = None
    first_ineligible_amount_raw: int | None = None
    expansion_stop_reason: str | None = None
    last_failure_reason: str | None = None
    probe = initial_probe_amount_raw
    while attempts < max_attempts and probe >= min_probe_amount_raw:
        try:
            evidence = _evaluate(probe)
            passed, reason = _record(probe, evidence)
            if passed:
                lower_bound = probe
                break
            last_failure_reason = reason
            if reason not in {"not_profitable_after_costs"}:
                if reason == "not_enough_liquidity" or (reason == "live_quote_failed" and _has_not_enough_liquidity_blocker(evidence)):
                    first_ineligible_amount_raw = probe
                else:
                    return _stop_without_size(reason or "not_eligible")
            first_ineligible_amount_raw = probe
        except Exception as exc:
            quote_failure_reason = "pool_not_initialized" if _is_pool_not_initialized_error(exc) else ("not_enough_liquidity" if _is_not_enough_liquidity_error(exc) else "live_quote_failed")
            candidate_rows.append(
                {
                    "attempt": attempts,
                    "amountRaw": str(probe),
                    "status": quote_failure_reason,
                    "error": str(exc),
                    "errorStack": _dex_error_stack_from_status(
                        quote_failure_reason,
                        message=str(exc),
                        raw_error=str(exc),
                        details={"attempt": attempts, "amountRaw": str(probe)},
                        flow="size_probe",
                        node_id="probe_attempt",
                    ),
                }
            )
            if quote_failure_reason in {"live_quote_failed", "not_enough_liquidity"} and _is_not_enough_liquidity_error(exc):
                first_ineligible_amount_raw = probe
                last_failure_reason = quote_failure_reason
            else:
                return _stop_without_size(quote_failure_reason, raw_error=str(exc))
        if lower_bound is not None:
            break
        next_probe = max(min_probe_amount_raw, probe // 2)
        if next_probe == probe:
            break
        probe = next_probe

    if lower_bound is None:
        search_status = (
            "not_profitable_after_costs"
            if last_failure_reason == "not_profitable_after_costs"
            else "pool_not_initialized"
            if last_failure_reason == "pool_not_initialized"
            else "not_enough_liquidity"
            if last_failure_reason == "not_enough_liquidity"
            else "live_quote_failed"
        )
        return {
            "status": "route_execution_size_probe_unexecutable",
            "searchStatus": search_status,
            "probeAttempts": attempts,
            "maxProbeAttempts": max_attempts,
            "requestedAmountRaw": str(target_amount_raw),
            "initialProbeAmountRaw": str(initial_probe_amount_raw),
            "selectedProfitableAmountRaw": None,
            "maxExecutableAmountRaw": None,
            "firstFailureAmountRaw": str(target_amount_raw),
            "attempts": candidate_rows,
            "failureReason": f"after up to {max_attempts} attempts, no amount passed live quote and positive net-profit gates",
            "errorStack": _dex_error_stack_from_status(
                search_status,
                message=search_status,
                details={"lastFailureReason": last_failure_reason, "searchStatus": search_status},
                flow="size_probe",
                node_id="probe_quote",
            ),
        }

    if upper_bound is None and first_ineligible_amount_raw is not None and first_ineligible_amount_raw > lower_bound:
        upper_bound = first_ineligible_amount_raw

    probe = min(max_probe_amount_raw, max(lower_bound * 2, lower_bound + 1))
    while attempts < max_attempts and probe <= max_probe_amount_raw:
        try:
            evidence = _evaluate(probe)
            passed, reason = _record(probe, evidence)
            if passed:
                lower_bound = probe
                if probe >= max_probe_amount_raw:
                    break
                next_probe = min(max_probe_amount_raw, max(probe * 2, probe + 1))
                if next_probe == probe:
                    break
                probe = next_probe
                continue
            first_ineligible_amount_raw = probe
            expansion_stop_reason = reason or "not_eligible"
            if reason in {"not_enough_liquidity", "not_profitable_after_costs"} or (reason == "live_quote_failed" and _has_not_enough_liquidity_blocker(evidence)):
                upper_bound = probe
            else:
                upper_bound = None
            break
        except Exception as exc:
            quote_failure_reason = "pool_not_initialized" if _is_pool_not_initialized_error(exc) else ("not_enough_liquidity" if _is_not_enough_liquidity_error(exc) else "live_quote_failed")
            candidate_rows.append(
                {
                    "attempt": attempts,
                    "amountRaw": str(probe),
                    "status": quote_failure_reason,
                    "error": str(exc),
                    "errorStack": _dex_error_stack_from_status(
                        quote_failure_reason,
                        message=str(exc),
                        raw_error=str(exc),
                        details={"attempt": attempts, "amountRaw": str(probe)},
                        flow="size_probe",
                        node_id="probe_attempt",
                    ),
                }
            )
            first_ineligible_amount_raw = probe
            expansion_stop_reason = quote_failure_reason
            if quote_failure_reason in {"live_quote_failed", "not_enough_liquidity"} and _is_not_enough_liquidity_error(exc):
                upper_bound = probe
            else:
                upper_bound = None
            break

    if upper_bound is not None and lower_bound is not None:
        while upper_bound - lower_bound > 1 and attempts < max_attempts:
            mid = (upper_bound + lower_bound) // 2
            try:
                evidence = _evaluate(mid)
                passed, _ = _record(mid, evidence)
                if passed:
                    lower_bound = mid
                else:
                    upper_bound = mid
            except Exception as exc:
                quote_failure_reason = "pool_not_initialized" if _is_pool_not_initialized_error(exc) else ("not_enough_liquidity" if _is_not_enough_liquidity_error(exc) else "live_quote_failed")
                candidate_rows.append(
                    {
                        "attempt": attempts,
                        "amountRaw": str(mid),
                        "status": quote_failure_reason,
                        "error": str(exc),
                        "errorStack": _dex_error_stack_from_status(
                            quote_failure_reason,
                            message=str(exc),
                            raw_error=str(exc),
                            details={"attempt": attempts, "amountRaw": str(mid)},
                            flow="size_probe",
                            node_id="probe_attempt",
                        ),
                    }
                )
                upper_bound = mid

    return {
        "status": "route_execution_size_probe_complete",
        "searchStatus": "profitable_amount_found",
        "probeAttempts": attempts,
        "maxProbeAttempts": max_attempts,
        "requestedAmountRaw": str(target_amount_raw),
        "initialProbeAmountRaw": str(initial_probe_amount_raw),
        "selectedProfitableAmountRaw": str(lower_bound),
        "maxExecutableAmountRaw": str(lower_bound),
        "firstFailureAmountRaw": str(upper_bound or first_ineligible_amount_raw) if (upper_bound or first_ineligible_amount_raw) is not None else (str(target_amount_raw) if lower_bound != target_amount_raw else None),
        "attempts": candidate_rows,
        "failureReason": None,
        "expansionStopReason": expansion_stop_reason,
        "errorStack": _dex_error_stack_from_status(
            "live_verified",
            message="OK",
            flow="size_probe",
            node_id="complete",
        ),
    }
