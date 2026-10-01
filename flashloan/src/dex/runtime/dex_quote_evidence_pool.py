from __future__ import annotations

from decimal import Decimal
from typing import Any

from web3 import Web3

from bot.execution.uniswap_v4 import build_v4_quote_request, quote_v4_exact_input_single
from bot.execution.uniswap_v3 import quote_v3_exact_input_single, snapshot_v3_pool
from dex.intent_trade.chains.robinhood import config as robinhood
from dex.intent_trade.chains.robinhood.chain_test_policy import ROBINHOOD_TESTNET_CHAIN_ID, ROBINHOOD_TESTNET_NETWORK

from dex.runtime.dex_quote_evidence_common import (
    _checksum,
    _evidence_attr_or_default,
    _fee_bps,
    _human_from_raw,
    _int_or_none,
    _load_v4_quote_adapter,
    _norm_symbol,
    _number,
    _raw_from_human,
    _robinhood_network_for_chain_id,
    _text,
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

DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS = 10

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
    normalized_address = _text(address).lower()
    if normalized_address == "0x0000000000000000000000000000000000000000":
        cache[key] = 18
        return cache[key]
    if not normalized_address.startswith("0x") or len(normalized_address) != 42:
        raise ValueError(
            f"missing exact token decimals for symbol={symbol}; provide a token address or fetch "
            "decimals from a trusted on-chain/off-chain source"
        )
    try:
        contract = w3.eth.contract(address=_checksum(address), abi=ERC20_ABI)
        decimals = int(contract.functions.decimals().call())
    except Exception as exc:
        raise ValueError(
            f"missing exact token decimals for {normalized_address} symbol={symbol}; "
            f"provide token metadata or fetch decimals from a trusted source ({exc})"
        ) from exc
    cache[key] = decimals
    return decimals

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
    rpc_url: str | None = None,
    live_pool_state: dict[str, Any] | None = None,
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
    if live_pool_state:
        for key in (
            "sqrtPriceX96",
            "liquidity",
            "tick",
            "protocolFee",
            "lpFee",
            "source",
            "poolManager",
            "rpcUrl",
            "blockNumber",
            "stateSlot",
        ):
            if live_pool_state.get(key) is not None:
                request["poolState"][key] = live_pool_state[key]
        request["poolState"]["rpcUrl"] = rpc_url or request["poolState"].get("rpcUrl") or ""
    request["w3"] = w3
    if rpc_url:
        request["rpcUrl"] = rpc_url
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
    """从实时池状态返回零滑点现货输出，单位为原始数量。"""
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
    """对照实时现货价格度量价格影响，并排除明确上报的 LP 手续费。"""
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
