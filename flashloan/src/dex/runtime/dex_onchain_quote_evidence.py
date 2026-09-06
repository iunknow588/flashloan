from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import importlib
import os
from typing import Any, Callable

from web3 import Web3

from bot.execution.uniswap_v4 import build_v4_quote_request, quote_v4_exact_input_single
from bot.execution.uniswap_v4_sdk_adapter import load_v4_sdk_adapter
from bot.execution.uniswap_v3 import quote_v3_exact_input_single, snapshot_v3_pool
from dex.intent_trade.chains.robinhood import config as robinhood
from dex.intent_trade.chains.robinhood.chain_test_policy import ROBINHOOD_TESTNET_CHAIN_ID, ROBINHOOD_TESTNET_NETWORK
from market.binance_market.dex_surfaces import (
    DEFAULT_FLASH_LOAN_FEE_BPS,
    DEFAULT_GAS_RESERVE_USD,
)
from market.strategy.limits import DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD


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

ROBINHOOD_DEFAULT_V3_QUOTER = "0x33e885eD0Ec9bF04EcfB19341582aADCb4c8A9E7"
_STABLE_DECIMALS = {"AUSD", "BUSD", "DAI", "EURC", "FDUSD", "FRAX", "PYUSD", "RLUSD", "USDC", "USDD", "USDE", "USDG", "USDT"}
_TOKEN_DECIMALS_FALLBACK = {"WETH": 18, "ETH": 18, "WBTC": 8, "BTC": 8, **{symbol: 6 for symbol in _STABLE_DECIMALS}}
DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS = 10
DEFAULT_ROUTE_SIZE_PROBE_ATTEMPTS = 10


QuoteHopProvider = Callable[..., dict[str, Any]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _text(value: object) -> str:
    return str(value or "").strip()


def _is_missing_historical_state_error(exc: BaseException) -> bool:
    message = _text(exc).lower()
    return "missing trie node" in message or "state is not available" in message or "state .* not found" in message


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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
        return f"第 {step.get('hop')} 跳协议 {step.get('protocol') or step.get('dex') or step.get('fee')} 尚未接入链上复核"
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
    reference = (
        os.getenv("UNISWAP_V4_QUOTE_ADAPTER")
        or os.getenv("DEX_V4_QUOTE_ADAPTER")
        or os.getenv("ROBINHOOD_TESTNET_V4_QUOTE_ADAPTER")
        or os.getenv("ROBINHOOD_V4_QUOTE_ADAPTER")
    )
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
    snapshot = snapshot_v3_pool(w3, pool_address, block_identifier=block_number)
    token_in = _checksum(token_in)
    token_out = _checksum(token_out)
    if {token_in.lower(), token_out.lower()} != {snapshot.token0.lower(), snapshot.token1.lower()}:
        raise ValueError("V3 pool token addresses do not match route step")
    amount_out, sqrt_after, ticks_crossed, gas_estimate = quote_v3_exact_input_single(
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
    request = build_v4_quote_request(
        network=str(network or pool.get("network") or robinhood.NETWORK).strip().lower(),
        chain_id=chain_id or robinhood.CHAIN_ID,
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
    quote = quote_v4_exact_input_single(
        adapter=_load_v4_quote_adapter(network=network, chain_id=chain_id),
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
    for url in robinhood.rpc_urls_from_env():
        w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": float(os.getenv("DEX_ONCHAIN_RPC_TIMEOUT_SECONDS", "6") or 6)}))
        try:
            if w3.is_connected() and int(w3.eth.chain_id) == robinhood.CHAIN_ID:
                return w3, url
        except Exception as exc:
            failures.append(f"{url}: {exc}")
    raise RuntimeError("Robinhood RPC 不可用: " + "; ".join(failures[:3]))


def _connect_robinhood_family(chain_id: int | None, network: str) -> tuple[Any, str]:
    normalized_network = _text(network).lower()
    if int(chain_id or 0) == ROBINHOOD_TESTNET_CHAIN_ID or normalized_network == ROBINHOOD_TESTNET_NETWORK:
        failures: list[str] = []
        timeout = float(os.getenv("DEX_ONCHAIN_RPC_TIMEOUT_SECONDS", "6") or 6)
        for url in robinhood.testnet_rpc_urls_from_env():
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": timeout}))
            try:
                if w3.is_connected() and int(w3.eth.chain_id) == ROBINHOOD_TESTNET_CHAIN_ID:
                    return w3, url
            except Exception as exc:
                failures.append(f"{url}: {exc}")
        raise RuntimeError("Robinhood Testnet RPC unavailable: " + "; ".join(failures[:3]))
    return _connect_robinhood()


def _is_robinhood_family(chain_id: int | None, network: str) -> bool:
    normalized_network = _text(network).lower()
    return bool(
        int(chain_id or 0) in {robinhood.CHAIN_ID, ROBINHOOD_TESTNET_CHAIN_ID}
        or normalized_network in {robinhood.NETWORK, ROBINHOOD_TESTNET_NETWORK}
    )


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


def capture_onchain_trigger_context(
    *,
    chain_id: int | None,
    network: str,
    observed_at: str | None = None,
) -> dict[str, Any]:
    observed = observed_at or _now_iso()
    normalized_network = _text(network or ("robinhood" if chain_id == robinhood.CHAIN_ID else f"chain_{chain_id}")).lower()
    context: dict[str, Any] = {
        "source": "scan_trigger_chain_head",
        "observedAt": observed,
        "network": normalized_network,
        "chainId": chain_id,
        "available": False,
        "blockNumber": None,
        "triggerBlockNumber": None,
        "blockTimestamp": None,
        "rpcUrl": None,
        "error": None,
    }
    if int(chain_id or 0) == ROBINHOOD_TESTNET_CHAIN_ID or normalized_network == ROBINHOOD_TESTNET_NETWORK:
        from dex.intent_trade.chains.robinhood_testnet.trigger_context import capture_robinhood_testnet_trigger_context

        return capture_robinhood_testnet_trigger_context(
            chain_id=chain_id,
            network=ROBINHOOD_TESTNET_NETWORK,
            observed_at=observed,
        )
    if chain_id != robinhood.CHAIN_ID and normalized_network != robinhood.NETWORK:
        context["error"] = "当前链路尚未接入触发区块捕获适配器"
        return context
    try:
        w3, rpc_url = _connect_robinhood()
        block_number = int(w3.eth.block_number)
        context.update(
            {
                "available": True,
                "blockNumber": block_number,
                "triggerBlockNumber": block_number,
                "blockTimestamp": _block_timestamp(w3, block_number),
                "rpcUrl": rpc_url,
            }
        )
    except Exception as exc:
        context["error"] = str(exc)
    return context


def _unsupported_evidence(
    *,
    chain_id: int | None,
    network: str,
    route: dict[str, Any],
    observed_at: str,
    reasons: list[str],
    trigger_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trigger = _trigger_context_from_route(route, trigger_context)
    return {
        "evidenceVersion": 1,
        "observedAt": observed_at,
        "network": network,
        "chainId": chain_id,
        "status": "not_live_verified",
        "executionTrust": "static_reference_only",
        "trustedForExecution": False,
        "blockNumber": None,
        "triggerContext": trigger,
        "triggerBlockNumber": _trigger_block_number(trigger),
        "quoteBlockSource": "unavailable",
        "routeSymbols": route.get("routeSymbols") or [],
        "routeAddresses": route.get("routeAddresses") or [],
        "hops": [],
        "costModel": {},
        "blockers": reasons,
        "transactionDevelopment": _transaction_development(False, reasons),
    }


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
        size_analysis.get("recommendedBorrowInputAmountRaw")
        or execution_probe.get("recommendedBorrowInputAmountRaw")
        or route.get("recommendedBorrowInputAmountRaw")
    )
    return execution_probe, max_executable_raw, first_failure_raw, recommended_raw


def _transaction_development(ready: bool, blockers: list[str]) -> dict[str, Any]:
    if ready:
        return {
            "status": "ready_for_transaction_preflight_development",
            "executionEnabled": False,
            "nextModules": [
                "构建闪电贷借款入口",
                "按链上复核路径生成 AMM swap calldata",
                "加入最小回款与滑点保护",
                "先执行 fork/static-call 仿真，再允许签名发送",
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
    max_attempts: int | None = None,
    min_probe_amount_raw: int | None = None,
) -> dict[str, Any]:
    max_attempts = max(1, min(10, int(max_attempts or os.getenv("DEX_V4_EXECUTION_SIZE_MAX_PROBE_ATTEMPTS", str(DEFAULT_ROUTE_SIZE_PROBE_ATTEMPTS)) or DEFAULT_ROUTE_SIZE_PROBE_ATTEMPTS)))
    min_probe_amount_raw = max(1, int(min_probe_amount_raw or os.getenv("DEX_V4_EXECUTION_SIZE_MIN_PROBE_AMOUNT_RAW", "1") or 1))
    max_probe_multiplier = max(1, int(os.getenv("DEX_V4_EXECUTION_SIZE_MAX_PROBE_MULTIPLIER", "64") or 64))
    target_amount_raw = max(1, int(requested_amount_raw))
    max_probe_amount_raw = min((1 << 128) - 1, max(target_amount_raw, min_probe_amount_raw) * max_probe_multiplier)
    attempts = 0
    candidate_rows: list[dict[str, Any]] = []

    def _passes(evidence: dict[str, Any]) -> bool:
        validation = _as_dict(evidence.get("validation"))
        # The real evidence builder always emits these gates. Keep injected legacy
        # providers usable in tests while never weakening the live evidence path.
        price_impact_gate = validation.get("allHopsWithinPriceImpactLimit", True)
        profit_gate = validation.get("profitableAfterCosts", True)
        return bool(
            _text(evidence.get("status")).lower() == "live_verified"
            and validation.get("allHopsQuoted", True) is True
            and validation.get("routeClosed", True) is True
            and validation.get("tokenContinuity", True) is True
            and profit_gate is True
            and price_impact_gate is True
        )

    def _failure_reason(evidence: dict[str, Any]) -> str | None:
        validation = _as_dict(evidence.get("validation"))
        status = _text(evidence.get("status")).lower()
        if validation.get("allHopsWithinPriceImpactLimit") is False and validation.get("allHopsQuoted") is True:
            return "price_impact_limit_exceeded"
        if validation.get("profitableAfterCosts") is False and validation.get("allHopsQuoted") is True:
            return "not_profitable_after_costs"
        if status == "slippage_limit_exceeded":
            return "price_impact_limit_exceeded"
        if status != "live_verified":
            return status or "live_quote_failed"
        if validation.get("allHopsQuoted") is not True:
            return "not_all_hops_quoted"
        if validation.get("routeClosed") is not True:
            return "route_not_closed"
        if validation.get("tokenContinuity") is not True:
            return "token_continuity_failed"
        if validation.get("allHopsWithinPriceImpactLimit") is not True:
            return "price_impact_limit_exceeded"
        if validation.get("profitableAfterCosts") is not True:
            return "not_profitable_after_costs"
        return None

    def _evaluate(amount_raw: int) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
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
            }
        )
        return passed, reason

    def _stop_without_size(reason: str) -> dict[str, Any]:
        search_status = (
            "pool_insufficient_for_profitable_execution"
            if reason == "price_impact_limit_exceeded"
            else "not_profitable_at_low_slippage"
            if reason == "not_profitable_after_costs"
            else reason
        )
        return {
            "status": "route_execution_size_probe_unexecutable",
            "searchStatus": search_status,
            "probeAttempts": attempts,
            "maxProbeAttempts": max_attempts,
            "requestedAmountRaw": str(target_amount_raw),
            "selectedProfitableAmountRaw": None,
            "maxExecutableAmountRaw": None,
            "firstFailureAmountRaw": str(target_amount_raw),
            "attempts": candidate_rows,
            "failureReason": reason,
        }

    lower_bound: int | None = None
    upper_bound: int | None = None
    first_ineligible_amount_raw: int | None = None
    expansion_stop_reason: str | None = None
    probe = target_amount_raw
    while attempts < max_attempts and probe >= min_probe_amount_raw:
        try:
            evidence = _evaluate(probe)
            passed, reason = _record(probe, evidence)
            if passed:
                lower_bound = probe
                break
            if reason != "price_impact_limit_exceeded":
                return _stop_without_size(reason or "not_eligible")
            upper_bound = probe
            first_ineligible_amount_raw = probe
        except Exception as exc:
            candidate_rows.append(
                {
                    "attempt": attempts,
                    "amountRaw": str(probe),
                    "status": "quote_failed",
                    "error": str(exc),
                }
            )
            return _stop_without_size("live_quote_failed")
        if lower_bound is not None:
            break
        next_probe = max(min_probe_amount_raw, probe // 2)
        if next_probe == probe:
            break
        probe = next_probe

    if lower_bound is None:
        return {
            "status": "route_execution_size_probe_unexecutable",
            "searchStatus": "pool_insufficient_for_profitable_execution",
            "probeAttempts": attempts,
            "maxProbeAttempts": max_attempts,
            "requestedAmountRaw": str(target_amount_raw),
            "selectedProfitableAmountRaw": None,
            "maxExecutableAmountRaw": None,
            "firstFailureAmountRaw": str(target_amount_raw),
            "attempts": candidate_rows,
            "failureReason": "after up to 10 half-size attempts, no amount passed live quote, 10 bps per-hop impact, and positive net-profit gates",
        }

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
            upper_bound = probe if reason == "price_impact_limit_exceeded" else None
            break
        except Exception as exc:
            candidate_rows.append(
                {
                    "attempt": attempts,
                    "amountRaw": str(probe),
                    "status": "quote_failed",
                        "error": str(exc),
                    }
            )
            first_ineligible_amount_raw = probe
            expansion_stop_reason = "live_quote_failed"
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
                candidate_rows.append(
                    {
                        "attempt": attempts,
                        "amountRaw": str(mid),
                        "status": "quote_failed",
                        "error": str(exc),
                    }
                )
                upper_bound = mid

    return {
        "status": "route_execution_size_probe_complete",
        "searchStatus": "profitable_amount_found",
        "probeAttempts": attempts,
        "maxProbeAttempts": max_attempts,
        "requestedAmountRaw": str(target_amount_raw),
        "selectedProfitableAmountRaw": str(lower_bound),
        "maxExecutableAmountRaw": str(lower_bound),
        "firstFailureAmountRaw": str(upper_bound or first_ineligible_amount_raw) if (upper_bound or first_ineligible_amount_raw) is not None else (str(target_amount_raw) if lower_bound != target_amount_raw else None),
        "attempts": candidate_rows,
        "failureReason": None,
        "expansionStopReason": expansion_stop_reason,
    }


def build_route_onchain_evidence(
    route: dict[str, Any],
    *,
    payload: dict[str, Any] | None = None,
    chain_id: int | None = None,
    observed_at: str | None = None,
    trigger_context: dict[str, Any] | None = None,
    quote_hop_provider: QuoteHopProvider | None = None,
    borrow_input_amount_override_raw: int | None = None,
    skip_execution_size_probe: bool = False,
) -> dict[str, Any]:
    observed = observed_at or _now_iso()
    table = payload.get("poolStaticTable") if isinstance(payload, dict) and isinstance(payload.get("poolStaticTable"), dict) else {}
    network = _text(table.get("network") or route.get("network") or ("robinhood" if chain_id == robinhood.CHAIN_ID else f"chain_{chain_id}")).lower()
    trigger = _trigger_context_from_route(route, trigger_context)
    route_symbols = [str(item) for item in route.get("routeSymbols") or [] if str(item).strip()]
    analysis = route.get("quoteAnalysis") if isinstance(route.get("quoteAnalysis"), dict) else {}
    steps = [step for step in analysis.get("steps") or [] if isinstance(step, dict)]
    if not _is_robinhood_family(chain_id, network):
        return _unsupported_evidence(
            chain_id=chain_id,
            network=network,
            route=route,
            observed_at=observed,
            reasons=["current chain quoter review adapter is not connected; record is static observation only."],
            trigger_context=trigger,
        )
    unsupported = [
        reason
        for step in steps
        for reason in [_unsupported_step_reason(step, _pool_for_step(step, _pool_map(route)))]
        if reason
    ]
    if unsupported:
        return _unsupported_evidence(chain_id=chain_id, network=network, route=route, observed_at=observed, reasons=unsupported, trigger_context=trigger)

    trigger_block_number = _trigger_block_number(trigger)
    if trigger_block_number is None:
        return _unsupported_evidence(
            chain_id=chain_id,
            network=network,
            route=route,
            observed_at=observed,
            reasons=["missing trigger block context; later latest price cannot replace the trigger-time price."],
            trigger_context=trigger,
        )

    pools = _pool_map(route)
    quoter = _text(table.get("quoter") or robinhood.default_v3_quoter_address(ROBINHOOD_DEFAULT_V3_QUOTER))
    token_cache: dict[str, int] = {}
    try:
        w3, rpc_url = _connect_robinhood_family(chain_id, network)
        block_number = trigger_block_number
    except Exception as exc:
        return _unsupported_evidence(
            chain_id=chain_id,
            network=network,
            route=route,
            observed_at=observed,
            reasons=[f"链上 RPC 复核失败：{exc}"],
            trigger_context=trigger,
        )

    input_symbol = _norm_symbol(route_symbols[0] if route_symbols else analysis.get("inputSymbol"))
    first_pool = _pool_for_step(steps[0], pools) if steps else {}
    input_address, _, input_decimals = _pool_side(first_pool, input_symbol)
    input_decimals = input_decimals if input_decimals is not None else _token_decimals(w3, input_address, input_symbol, token_cache)
    route_closed = bool(route_symbols and route_symbols[0] == route_symbols[-1])
    route_size_execution_probe, route_max_executable_raw, route_first_failure_raw, route_recommended_raw = _route_size_advice(route)
    reference_input_amount = _positive_decimal_or_none(analysis.get("referenceInputAmount"))
    borrow_input_amount = (
        reference_input_amount
        if route_closed and reference_input_amount is not None
        else _positive_decimal_or_none(analysis.get("notionalUsd"))
        or Decimal("1000")
    )
    requested_borrow_input_amount_raw = (
        int(borrow_input_amount_override_raw)
        if borrow_input_amount_override_raw is not None and int(borrow_input_amount_override_raw) > 0
        else _raw_from_human(borrow_input_amount, input_decimals)
    )
    if borrow_input_amount_override_raw is not None and int(borrow_input_amount_override_raw) > 0:
        selected_borrow_input_amount_raw = int(borrow_input_amount_override_raw)
    elif route_recommended_raw is not None and route_recommended_raw > 0:
        selected_borrow_input_amount_raw = int(route_recommended_raw)
    elif route_max_executable_raw is not None and route_max_executable_raw > 0:
        selected_borrow_input_amount_raw = int(route_max_executable_raw)
    else:
        selected_borrow_input_amount_raw = int(requested_borrow_input_amount_raw)
    borrow_input_amount = Decimal(int(selected_borrow_input_amount_raw)) / (Decimal(10) ** int(input_decimals))
    execution_size_probe: dict[str, Any] | None = route_size_execution_probe or None
    route_has_size_metadata = bool(route_max_executable_raw or route_first_failure_raw or route_size_execution_probe)
    all_v4_route = bool(steps) and all(
        str(step.get("protocol") or step.get("dex") or "").strip().lower() == "uniswap_v4"
        for step in steps
    )
    testnet_closed_loop_v4_route = bool(
        chain_id == ROBINHOOD_TESTNET_CHAIN_ID and route_closed and all_v4_route
    )
    if (
        not skip_execution_size_probe
        and chain_id == ROBINHOOD_TESTNET_CHAIN_ID
        and selected_borrow_input_amount_raw > 0
        and (route_has_size_metadata or testnet_closed_loop_v4_route)
    ):
        probe_amount_raw = selected_borrow_input_amount_raw
        # Chain-offchain coarse sizing already chose the best starting point.
        # On-chain verification begins there, then halves only if needed.
        execution_size_probe = _probe_robinhood_testnet_route_execution_size(
            route,
            payload=payload,
            chain_id=chain_id,
            observed_at=observed,
            trigger_context=trigger,
            quote_hop_provider=quote_hop_provider,
            requested_amount_raw=probe_amount_raw,
        )
        try:
            probed_amount_raw = int(execution_size_probe.get("maxExecutableAmountRaw") or 0)
        except (TypeError, ValueError):
            probed_amount_raw = None
        if probed_amount_raw is not None and probed_amount_raw > 0:
            selected_borrow_input_amount_raw = probed_amount_raw
            borrow_input_amount = Decimal(probed_amount_raw) / (Decimal(10) ** int(input_decimals))

    hop_provider = quote_hop_provider
    hop_rows: list[dict[str, Any]] = []
    amount_in_raw: int | None = None
    blockers: list[str] = []
    amount_in_raw = selected_borrow_input_amount_raw
    for step in steps:
        pool = _pool_for_step(step, pools)
        protocol = _protocol_key(step.get("protocol"), step.get("dex"), step.get("fee"), pool.get("protocol"), pool.get("dex"))
        hop_number = int(step.get("hop") or len(hop_rows) + 1)
        try:
            if hop_provider is not None:
                hop = hop_provider(w3=w3, quoter=quoter, step=step, pool=pool, amount_in_raw=amount_in_raw, block_number=block_number, token_cache=token_cache)
            elif protocol == "uniswap_v2":
                hop = _quote_v2_hop(w3=w3, step=step, pool=pool, amount_in_raw=amount_in_raw, block_number=block_number, token_cache=token_cache)
            elif protocol == "uniswap_v4":
                hop = _quote_v4_hop(
                    w3=w3,
                    step=step,
                    pool=pool,
                    amount_in_raw=amount_in_raw,
                    block_number=block_number,
                    token_cache=token_cache,
                    chain_id=chain_id,
                    network=network,
                )
            else:
                hop = _quote_v3_hop(w3=w3, quoter=quoter, step=step, pool=pool, amount_in_raw=amount_in_raw, block_number=block_number, token_cache=token_cache)
        except Exception as exc:
            if _is_missing_historical_state_error(exc):
                hop_rows.append(
                    {
                        "hop": hop_number,
                        "status": "live_evidence_deferred",
                        "executionTrust": "static_reference_only",
                        "quoteSource": "historical_state_unavailable",
                        "quoteMethod": f"{protocol}_exact_input",
                        "protocol": protocol,
                        "pool": step.get("pool") or pool.get("pool"),
                        "fromSymbol": _norm_symbol(step.get("fromSymbol")),
                        "toSymbol": _norm_symbol(step.get("toSymbol")),
                        "amountInRaw": str(amount_in_raw),
                        "amountOutRaw": None,
                        "liveQuoteError": str(exc),
                    }
                )
                blockers.append(
                    f"第 {hop_number} 跳历史区块 {block_number} 的 trie 状态不可用，已延后复核，不判定为路线失败：{exc}"
                )
                break
            hop_rows.append(
                {
                    "hop": hop_number,
                    "status": "live_quote_failed",
                    "executionTrust": "live_quote_failed",
                    "quoteSource": "chain_live_quote_failed",
                    "quoteMethod": f"{protocol}_exact_input",
                    "protocol": protocol,
                    "pool": step.get("pool") or pool.get("pool"),
                    "fromSymbol": _norm_symbol(step.get("fromSymbol")),
                    "toSymbol": _norm_symbol(step.get("toSymbol")),
                    "tokenIn": step.get("tokenIn") or _pool_side(pool, _norm_symbol(step.get("fromSymbol")))[0],
                    "tokenOut": step.get("tokenOut") or _pool_side(pool, _norm_symbol(step.get("toSymbol")))[0],
                    "amountInRaw": str(amount_in_raw),
                    "amountOutRaw": None,
                    "liveQuoteError": str(exc),
                    "staticQuote": {
                        "outputAmount": step.get("outputAmount"),
                        "outputUsd": step.get("outputUsd"),
                        "quoteSource": step.get("quoteSource"),
                        "executionTrust": step.get("executionTrust"),
                    },
                }
            )
            blockers.append(f"第 {hop_number} 跳链上逐跳复核失败：{exc}")
            break
        hop["hop"] = hop_number
        hop["fromSymbol"] = _norm_symbol(step.get("fromSymbol"))
        hop["toSymbol"] = _norm_symbol(step.get("toSymbol"))
        hop["staticQuote"] = {
            "outputAmount": step.get("outputAmount"),
            "outputUsd": step.get("outputUsd"),
            "quoteSource": step.get("quoteSource"),
            "executionTrust": step.get("executionTrust"),
        }
        hop = _annotate_hop_price_impact(
            hop,
            step=step,
            pool=pool,
            limit_bps=DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS,
        )
        hop_rows.append(hop)
        amount_in_raw = int(hop["amountOutRaw"])

    token_continuity_issues = _token_continuity_issues(hop_rows)
    token_continuity_ok = not token_continuity_issues
    if token_continuity_issues:
        blockers.append("链上复核跳间 token 地址不连续：存在同名不同合约地址，不能进入交易预检。")
    reference_quote_only = any(
        _text(hop.get("sdkAdapter") or hop.get("quoteAdapter")).lower() == "reference_quote_adapter"
        or _text(hop.get("quoteMethod")).lower() == "uniswap_v4_reference_quote"
        for hop in hop_rows
    )
    if reference_quote_only:
        blockers.append("V4 路由仍使用 reference_quote_adapter；必须接入真实 V4 Quoter/SDK 或 swap static-call 后才能进入交易预检。")
    all_hops_verified = bool(steps) and len(hop_rows) == len(steps) and token_continuity_ok and not any(
        _text(hop.get("status")).lower() in {"live_quote_failed", "quote_failed"}
        for hop in hop_rows
    ) and not reference_quote_only
    hop_price_impact_rows = [
        hop for hop in hop_rows if isinstance(hop, dict) and hop.get("amountOutRaw") is not None
    ]
    all_hops_within_price_impact_limit = bool(hop_price_impact_rows) and len(hop_price_impact_rows) == len(steps) and all(
        hop.get("priceImpactWithinLimit") is True for hop in hop_price_impact_rows
    )
    price_impact_issues = [
        {
            "hop": hop.get("hop"),
            "fromSymbol": hop.get("fromSymbol"),
            "toSymbol": hop.get("toSymbol"),
            "priceImpactBps": hop.get("priceImpactBps"),
            "limitBps": DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS,
            "error": hop.get("priceImpactError"),
        }
        for hop in hop_rows
        if hop.get("priceImpactWithinLimit") is not True
    ]
    historical_state_unavailable = any(
        _text(hop.get("status")).lower() == "live_evidence_deferred"
        for hop in hop_rows
    )
    if all_hops_verified and not all_hops_within_price_impact_limit:
        blockers.append(
            f"至少一跳价格影响无法确认或超过 {DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS} bps，不能进入交易预检。"
        )
    final_symbol = _norm_symbol(route_symbols[-1] if route_symbols else analysis.get("outputSymbol"))
    final_hop = hop_rows[-1] if all_hops_verified and hop_rows else None
    final_decimals = int(final_hop["outputDecimals"]) if final_hop and final_hop.get("outputDecimals") is not None else None
    final_amount = Decimal(final_hop["amountOut"]) if final_hop and final_hop.get("amountOut") is not None else None
    borrow_asset_is_stable = bool(
        analysis.get("borrowAssetIsStable")
        if analysis.get("borrowAssetIsStable") is not None
        else input_symbol in _STABLE_DECIMALS
    )
    notional = (
        borrow_input_amount
        if borrow_asset_is_stable
        else (_positive_decimal_or_none(analysis.get("notionalUsd")) or Decimal("1000"))
    )
    token_gross_profit = (
        final_amount - borrow_input_amount
        if all_hops_verified and route_closed and final_symbol == input_symbol and final_amount is not None
        else None
    )
    token_flash_fee = borrow_input_amount * Decimal(str(DEFAULT_FLASH_LOAN_FEE_BPS)) / Decimal(10_000)
    token_submission_net_profit = token_gross_profit - token_flash_fee if token_gross_profit is not None else None
    token_net_profit = token_submission_net_profit
    token_net_profit_bps = (
        token_net_profit / borrow_input_amount * Decimal(10_000)
        if token_net_profit is not None and borrow_input_amount > 0
        else None
    )
    final_output_usd = final_amount if borrow_asset_is_stable and final_symbol in _STABLE_DECIMALS and final_amount is not None else None
    gross_profit_usd = final_output_usd - notional if final_output_usd is not None else None
    flash_fee = notional * Decimal(str(DEFAULT_FLASH_LOAN_FEE_BPS)) / Decimal(10_000)
    gas_reserve = Decimal(str(DEFAULT_GAS_RESERVE_USD))
    submission_net_profit_usd = gross_profit_usd - flash_fee if gross_profit_usd is not None else None
    net_profit_usd = submission_net_profit_usd - gas_reserve if submission_net_profit_usd is not None else None
    net_profit_bps = net_profit_usd / notional * Decimal(10_000) if net_profit_usd is not None and notional > 0 else None
    min_profit_usd = Decimal(str(DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD))
    net_positive_after_costs = bool(
        all_hops_verified
        and all_hops_within_price_impact_limit
        and (
            (borrow_asset_is_stable and submission_net_profit_usd is not None and submission_net_profit_usd > 0)
            or (not borrow_asset_is_stable and token_submission_net_profit is not None and token_submission_net_profit > 0)
        )
    )
    meets_submission_profit_threshold = bool(
        all_hops_verified
        and all_hops_within_price_impact_limit
        and (
            (borrow_asset_is_stable and net_profit_usd is not None and net_profit_usd >= min_profit_usd)
            or (not borrow_asset_is_stable and token_submission_net_profit is not None and token_submission_net_profit > 0)
        )
    )
    if not all_hops_verified and not blockers:
        blockers.append("链上复核尚未覆盖全部跳数，无法进入预检。")
    if all_hops_verified and not net_positive_after_costs:
        blockers.append("链上核算扣除闪电贷费用后仍未转正，不能进入预检。")
    elif all_hops_verified and borrow_asset_is_stable and not meets_submission_profit_threshold:
        blockers.append("稳定币资金池路线的链上净利润未达到 6U 门槛。")
    if borrow_asset_is_stable and final_output_usd is None:
        blockers.append("最终输出不是稳定币或缺少可信 USD 计价，不能进入自动交易预检。")
    pool_insufficient_for_profitable_execution = bool(
        execution_size_probe
        and execution_size_probe.get("searchStatus") == "pool_insufficient_for_profitable_execution"
    )
    if pool_insufficient_for_profitable_execution:
        blockers.append("当前池子深度不足以支持满足单跳 10 bps 限制且扣除闪电贷费用后盈利的借贷规模。")
    evidence_status = (
        "live_evidence_deferred"
        if historical_state_unavailable
        else (
            "pool_insufficient_for_profitable_execution"
        if pool_insufficient_for_profitable_execution
        else (
            "slippage_limit_exceeded"
            if all_hops_verified and not all_hops_within_price_impact_limit
            else (
                "route_unprofitable"
                if all_hops_verified and all_hops_within_price_impact_limit and not net_positive_after_costs
                else ("live_verified" if all_hops_verified else ("live_quote_reference_only" if reference_quote_only else "live_quote_failed"))
            )
        )
        )
    )
    return {
        "evidenceVersion": 1,
        "observedAt": observed,
        "network": network,
        "chainId": chain_id,
        "rpcUrl": rpc_url,
        "blockNumber": block_number,
        "blockTimestamp": _block_timestamp(w3, block_number),
        "triggerContext": trigger,
        "triggerBlockNumber": trigger_block_number,
        "quoteBlockSource": "trigger_block",
        "quoter": quoter,
        "routeSymbols": route_symbols,
        "routeAddresses": route.get("routeAddresses") or [],
        "requestedBorrowInputAmountRaw": str(requested_borrow_input_amount_raw),
        "selectedBorrowInputAmountRaw": str(selected_borrow_input_amount_raw),
        "executionSizeProbe": execution_size_probe,
        "status": evidence_status,
        "executionTrust": "live_quoter_verified_readonly" if all_hops_verified else "static_reference_only",
        "trustedForExecution": False,
        "priceEvidenceMode": "live_verified" if all_hops_verified else "live_required",
        "priceEvidenceSource": "chain_live_quote" if all_hops_verified else "chain_live_quote_required",
        "livePriceVerified": bool(all_hops_verified),
        "readyForTransactionPreflight": meets_submission_profit_threshold,
        "hops": hop_rows,
        "costModel": {
            "notionalUsd": float(notional),
            "requestedBorrowInputAmountRaw": int(requested_borrow_input_amount_raw),
            "requestedBorrowInputAmount": float(Decimal(int(requested_borrow_input_amount_raw)) / (Decimal(10) ** int(input_decimals))),
            "selectedBorrowInputAmountRaw": int(selected_borrow_input_amount_raw),
            "selectedBorrowInputAmount": float(borrow_input_amount),
            "borrowInputAmount": float(borrow_input_amount),
            "borrowInputSymbol": input_symbol,
            "finalOutputAmount": float(final_amount) if final_amount is not None else None,
            "finalOutputSymbol": final_symbol,
            "grossProfitAmount": float(token_gross_profit) if token_gross_profit is not None else None,
            "flashLoanFeeAmount": float(token_flash_fee),
            "submissionNetProfitAmount": float(token_submission_net_profit) if token_submission_net_profit is not None else None,
            "netProfitAmount": float(token_net_profit) if token_net_profit is not None else None,
            "netProfitAmountSymbol": input_symbol,
            "netProfitAmountBps": float(token_net_profit_bps) if token_net_profit_bps is not None else None,
            "profitBasis": "usd" if borrow_asset_is_stable else "borrow_token",
            "tokenGasExcluded": not borrow_asset_is_stable,
            "finalOutputUsd": float(final_output_usd) if final_output_usd is not None else None,
            "grossProfitUsd": float(gross_profit_usd) if gross_profit_usd is not None else None,
            "flashLoanFeeBps": DEFAULT_FLASH_LOAN_FEE_BPS,
            "flashLoanFeeUsd": float(flash_fee),
            "gasReserveUsd": float(gas_reserve),
            "offchainMinProfitUsd": float(min_profit_usd),
            "minProfitUsd": float(min_profit_usd),
            "submissionMinProfitUsd": float(flash_fee),
            "submissionNetProfitUsd": float(submission_net_profit_usd) if submission_net_profit_usd is not None else None,
            "offchainNetProfitUsd": float(net_profit_usd) if net_profit_usd is not None else None,
            "netProfitUsd": float(net_profit_usd) if net_profit_usd is not None else None,
            "netProfitBps": float(net_profit_bps if borrow_asset_is_stable else token_net_profit_bps)
            if (net_profit_bps if borrow_asset_is_stable else token_net_profit_bps) is not None
            else None,
            "hopPriceImpactLimitBps": DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS,
            "maxHopPriceImpactBps": max(
                (float(hop.get("priceImpactBps")) for hop in hop_rows if _number(hop.get("priceImpactBps")) is not None),
                default=None,
            ),
            "priceImpactIssues": price_impact_issues,
        },
        "validation": {
            "allHopsQuoted": all_hops_verified,
            "allHopsWithinPriceImpactLimit": all_hops_within_price_impact_limit,
            "hopPriceImpactLimitBps": DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS,
            "priceImpactIssues": price_impact_issues,
            "routeClosed": route_closed,
            "tokenContinuity": token_continuity_ok,
            "tokenContinuityIssues": token_continuity_issues,
            "finalOutputSymbol": final_symbol,
            "finalOutputDecimals": final_decimals,
            "profitableAfterCosts": net_positive_after_costs,
            "netPositiveAfterCosts": net_positive_after_costs,
            "meetsMinProfitThreshold": meets_submission_profit_threshold,
            "meetsSubmissionProfitThreshold": meets_submission_profit_threshold,
            "meetsOffchainProfitThreshold": bool(
                all_hops_verified and net_profit_usd is not None and net_profit_usd >= min_profit_usd
            ),
            "borrowAssetClass": "stable" if borrow_asset_is_stable else "non_stable",
            "borrowAssetIsStable": borrow_asset_is_stable,
            "borrowInputAmount": float(borrow_input_amount),
            "requestedBorrowInputAmount": float(Decimal(int(requested_borrow_input_amount_raw)) / (Decimal(10) ** int(input_decimals))),
            "borrowInputSymbol": input_symbol,
            "finalOutputAmount": float(final_amount) if final_amount is not None else None,
            "netProfitAmount": float(token_net_profit) if token_net_profit is not None else None,
            "netProfitAmountSymbol": input_symbol,
            "nonStablePositiveXProfitPolicy": bool(not borrow_asset_is_stable),
            "executionSizeSearchStatus": execution_size_probe.get("searchStatus") if execution_size_probe else None,
            "poolInsufficientForProfitableExecution": pool_insufficient_for_profitable_execution,
        },
        "blockers": blockers,
        "transactionDevelopment": _transaction_development(meets_submission_profit_threshold, blockers),
    }


__all__ = ["build_route_onchain_evidence", "capture_onchain_trigger_context"]
