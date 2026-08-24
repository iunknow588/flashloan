from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import importlib
import os
from typing import Any, Callable

from web3 import Web3

from execution.uniswap_v4 import build_v4_quote_request, quote_v4_exact_input_single
from execution.uniswap_v3 import quote_v3_exact_input_single, snapshot_v3_pool
from intent_trade.chains.robinhood import config as robinhood
from market.binance_market.dex_surfaces import (
    DEFAULT_FLASH_LOAN_FEE_BPS,
    DEFAULT_GAS_RESERVE_USD,
    DEFAULT_MIN_PROFIT_BPS,
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

ROBINHOOD_DEFAULT_V3_QUOTER = "0x33e885eD0Ec9bF04EcfB19341582aADCb4c8A9E7"
_STABLE_DECIMALS = {"AUSD", "BUSD", "DAI", "EURC", "FDUSD", "FRAX", "PYUSD", "RLUSD", "USDC", "USDD", "USDE", "USDG", "USDT"}
_TOKEN_DECIMALS_FALLBACK = {"WETH": 18, "ETH": 18, "WBTC": 8, "BTC": 8, **{symbol: 6 for symbol in _STABLE_DECIMALS}}


QuoteHopProvider = Callable[..., dict[str, Any]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _text(value: object) -> str:
    return str(value or "").strip()


def _number(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


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
    if parsed > 100:
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


def _load_v4_quote_adapter() -> Any | None:
    reference = str(os.getenv("UNISWAP_V4_QUOTE_ADAPTER") or os.getenv("DEX_V4_QUOTE_ADAPTER") or "").strip()
    if not reference:
        return None
    module_name, separator, attr_path = reference.partition(":")
    if not module_name or not separator or not attr_path:
        raise ValueError("UNISWAP_V4_QUOTE_ADAPTER must use module:callable format")
    target: Any = importlib.import_module(module_name)
    for part in attr_path.split("."):
        target = getattr(target, part)
    return target


def _pool_side(pool: dict[str, Any], symbol: str) -> tuple[str | None, str | None, int | None]:
    symbol = _norm_symbol(symbol)
    for index in (0, 1):
        if _norm_symbol(pool.get(f"token{index}Symbol")) == symbol:
            address = _text(pool.get(f"token{index}"))
            decimals = pool.get(f"token{index}Decimals") or pool.get(f"token{index}_decimals")
            return address or None, _norm_symbol(pool.get(f"token{index}Symbol")), int(decimals) if decimals not in (None, "") else None
    return None, symbol, None


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
    token_in, _, in_decimals = _pool_side(pool, from_symbol)
    token_out, _, out_decimals = _pool_side(pool, to_symbol)
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
    token_in, _, in_decimals = _pool_side(pool, from_symbol)
    token_out, _, out_decimals = _pool_side(pool, to_symbol)
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
) -> dict[str, Any]:
    pool_address = _text(step.get("pool"))
    from_symbol = _norm_symbol(step.get("fromSymbol"))
    to_symbol = _norm_symbol(step.get("toSymbol"))
    token_in, _, in_decimals = _pool_side(pool, from_symbol)
    token_out, _, out_decimals = _pool_side(pool, to_symbol)
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
        network=str(pool.get("network") or robinhood.NETWORK).strip().lower(),
        chain_id=robinhood.CHAIN_ID,
        pool=pool_address,
        token_in=_checksum(token_in),
        token_out=_checksum(token_out),
        amount_in_raw=int(amount_in_raw),
        block_number=block_number,
        fee=step.get("fee") or pool.get("fee"),
        route_symbols=(from_symbol, to_symbol),
        reference_amount_out_raw=reference_amount_out_raw,
    )
    quote = quote_v4_exact_input_single(adapter=_load_v4_quote_adapter(), request=request)
    amount_out = int(quote["amountOutRaw"])
    return {
        "status": "live_verified",
        "quoteMethod": quote.get("quoteMethod") or "uniswap_v4_sdk_exact_input_single",
        "quoteMode": quote.get("quoteMode") or "uniswap_v4_sdk_exact_input_single",
        "poolState": quote.get("poolState") or {"protocol": "uniswap_v4", "pool": pool_address, "blockNumber": int(block_number)},
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
        "hops": [],
        "costModel": {},
        "blockers": reasons,
        "transactionDevelopment": _transaction_development(False, reasons),
    }


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


def build_route_onchain_evidence(
    route: dict[str, Any],
    *,
    payload: dict[str, Any] | None = None,
    chain_id: int | None = None,
    observed_at: str | None = None,
    trigger_context: dict[str, Any] | None = None,
    quote_hop_provider: QuoteHopProvider | None = None,
) -> dict[str, Any]:
    observed = observed_at or _now_iso()
    table = payload.get("poolStaticTable") if isinstance(payload, dict) and isinstance(payload.get("poolStaticTable"), dict) else {}
    network = _text(table.get("network") or route.get("network") or ("robinhood" if chain_id == robinhood.CHAIN_ID else f"chain_{chain_id}")).lower()
    trigger = _trigger_context_from_route(route, trigger_context)
    route_symbols = [str(item) for item in route.get("routeSymbols") or [] if str(item).strip()]
    analysis = route.get("quoteAnalysis") if isinstance(route.get("quoteAnalysis"), dict) else {}
    steps = [step for step in analysis.get("steps") or [] if isinstance(step, dict)]
    if chain_id != robinhood.CHAIN_ID and network != robinhood.NETWORK:
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
        w3, rpc_url = _connect_robinhood()
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

    hop_provider = quote_hop_provider
    hop_rows: list[dict[str, Any]] = []
    amount_in_raw: int | None = None
    blockers: list[str] = []
    try:
        input_symbol = _norm_symbol(route_symbols[0] if route_symbols else analysis.get("inputSymbol"))
        first_pool = _pool_for_step(steps[0], pools) if steps else {}
        input_address, _, input_decimals = _pool_side(first_pool, input_symbol)
        input_decimals = input_decimals if input_decimals is not None else _token_decimals(w3, input_address, input_symbol, token_cache)
        amount_in_raw = _raw_from_human(analysis.get("notionalUsd") or 1000, input_decimals)
        for step in steps:
            pool = _pool_for_step(step, pools)
            protocol = _protocol_key(step.get("protocol"), step.get("dex"), step.get("fee"), pool.get("protocol"), pool.get("dex"))
            if hop_provider is not None:
                hop = hop_provider(w3=w3, quoter=quoter, step=step, pool=pool, amount_in_raw=amount_in_raw, block_number=block_number, token_cache=token_cache)
            elif protocol == "uniswap_v2":
                hop = _quote_v2_hop(w3=w3, step=step, pool=pool, amount_in_raw=amount_in_raw, block_number=block_number, token_cache=token_cache)
            elif protocol == "uniswap_v4":
                hop = _quote_v4_hop(w3=w3, step=step, pool=pool, amount_in_raw=amount_in_raw, block_number=block_number, token_cache=token_cache)
            else:
                hop = _quote_v3_hop(w3=w3, quoter=quoter, step=step, pool=pool, amount_in_raw=amount_in_raw, block_number=block_number, token_cache=token_cache)
            hop["hop"] = int(step.get("hop") or len(hop_rows) + 1)
            hop["fromSymbol"] = _norm_symbol(step.get("fromSymbol"))
            hop["toSymbol"] = _norm_symbol(step.get("toSymbol"))
            hop["staticQuote"] = {
                "outputAmount": step.get("outputAmount"),
                "outputUsd": step.get("outputUsd"),
                "quoteSource": step.get("quoteSource"),
                "executionTrust": step.get("executionTrust"),
            }
            hop_rows.append(hop)
            amount_in_raw = int(hop["amountOutRaw"])
    except Exception as exc:
        blockers.append(f"链上逐跳复核失败：{exc}")

    all_hops_verified = bool(steps) and len(hop_rows) == len(steps) and not blockers
    final_symbol = _norm_symbol(route_symbols[-1] if route_symbols else analysis.get("outputSymbol"))
    final_decimals = int(hop_rows[-1]["outputDecimals"]) if hop_rows else None
    final_amount = Decimal(hop_rows[-1]["amountOut"]) if hop_rows else None
    notional = _decimal(analysis.get("notionalUsd") or 1000)
    final_output_usd = final_amount if final_symbol in _STABLE_DECIMALS and final_amount is not None else None
    gross_profit_usd = final_output_usd - notional if final_output_usd is not None else None
    flash_fee = notional * Decimal(str(DEFAULT_FLASH_LOAN_FEE_BPS)) / Decimal(10_000)
    gas_reserve = Decimal(str(DEFAULT_GAS_RESERVE_USD))
    net_profit_usd = gross_profit_usd - flash_fee - gas_reserve if gross_profit_usd is not None else None
    net_profit_bps = net_profit_usd / notional * Decimal(10_000) if net_profit_usd is not None and notional > 0 else None
    min_profit_usd = max(Decimal("1"), notional * Decimal(str(DEFAULT_MIN_PROFIT_BPS)) / Decimal(10_000))
    profitable_after_costs = bool(all_hops_verified and net_profit_usd is not None and net_profit_usd >= min_profit_usd)
    if not all_hops_verified and not blockers:
        blockers.append("链上复核未覆盖全部路径跳数。")
    if all_hops_verified and not profitable_after_costs:
        blockers.append("链上复核后未覆盖闪电贷费用、gas 储备和最小利润阈值。")
    if final_output_usd is None:
        blockers.append("最终输出不是稳定币或缺少可信 USD 计价，不能进入自动交易预检。")

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
        "status": "live_verified" if all_hops_verified else "live_quote_failed",
        "executionTrust": "live_quoter_verified_readonly" if all_hops_verified else "static_reference_only",
        "trustedForExecution": False,
        "readyForTransactionPreflight": profitable_after_costs,
        "hops": hop_rows,
        "costModel": {
            "notionalUsd": float(notional),
            "finalOutputUsd": float(final_output_usd) if final_output_usd is not None else None,
            "grossProfitUsd": float(gross_profit_usd) if gross_profit_usd is not None else None,
            "flashLoanFeeBps": DEFAULT_FLASH_LOAN_FEE_BPS,
            "flashLoanFeeUsd": float(flash_fee),
            "gasReserveUsd": float(gas_reserve),
            "minProfitUsd": float(min_profit_usd),
            "netProfitUsd": float(net_profit_usd) if net_profit_usd is not None else None,
            "netProfitBps": float(net_profit_bps) if net_profit_bps is not None else None,
        },
        "validation": {
            "allHopsQuoted": all_hops_verified,
            "routeClosed": bool(route_symbols and route_symbols[0] == route_symbols[-1]),
            "finalOutputSymbol": final_symbol,
            "finalOutputDecimals": final_decimals,
            "profitableAfterCosts": profitable_after_costs,
        },
        "blockers": blockers,
        "transactionDevelopment": _transaction_development(profitable_after_costs, blockers),
    }


__all__ = ["build_route_onchain_evidence", "capture_onchain_trigger_context"]
