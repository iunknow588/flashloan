from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal, getcontext
from pathlib import Path
from typing import Any
import json

from core.chain_data_registry import chain_data_file_path
from intent_trade.chains.robinhood.shared import ROBINHOOD_TESTNET_NETWORK


getcontext().prec = 80


def _text(value: object) -> str:
    return str(value or "").strip()


def _normalize_address(value: object) -> str:
    text = _text(value)
    return text.lower() if text.startswith("0x") and len(text) == 42 else ""


def _normalize_pool_id(value: object) -> str:
    text = _text(value)
    return text.lower() if text.startswith("0x") and len(text) == 66 else _normalize_address(value)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _pool_index_path() -> Path | None:
    path = chain_data_file_path(ROBINHOOD_TESTNET_NETWORK, "v4_pool_index")
    if path is not None:
        return path
    fallback = Path(__file__).resolve().parents[3] / "runtime" / "evidence" / ROBINHOOD_TESTNET_NETWORK / "v4_pool_index" / "latest.json"
    return fallback


def _pool_rows() -> list[dict[str, Any]]:
    path = _pool_index_path()
    if path is None:
        return []
    payload = _read_json(path)
    rows = payload.get("poolKeys")
    return [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []


def _pool_sqrt_price_x96(pool: dict[str, Any]) -> int | None:
    for key in ("sqrtPriceX96", "sqrt_price_x96"):
        try:
            parsed = int(str(pool.get(key) or "").strip())
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    pool_key = pool.get("poolKey") if isinstance(pool.get("poolKey"), dict) else {}
    for key in ("sqrtPriceX96", "sqrt_price_x96"):
        try:
            parsed = int(str(pool_key.get(key) or "").strip())
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _pool_lookup(pool_address: str) -> dict[str, Any]:
    wanted = _normalize_pool_id(pool_address)
    for pool in _pool_rows():
        if _normalize_pool_id(pool.get("pool")) == wanted or _normalize_pool_id(pool.get("poolId")) == wanted:
            return pool
    return {}


def _request_pool_state(request: dict[str, Any]) -> dict[str, Any]:
    pool_state = request.get("poolState") if isinstance(request.get("poolState"), dict) else {}
    return {
        "pool": _normalize_pool_id(pool_state.get("pool") or request.get("pool")),
        "token0": _normalize_address(pool_state.get("token0") or request.get("tokenIn")),
        "token1": _normalize_address(pool_state.get("token1") or request.get("tokenOut")),
        "token0Symbol": _text(pool_state.get("token0Symbol") or request.get("tokenInSymbol")),
        "token1Symbol": _text(pool_state.get("token1Symbol") or request.get("tokenOutSymbol")),
        "token0Decimals": pool_state.get("token0Decimals"),
        "token1Decimals": pool_state.get("token1Decimals"),
        "tickSpacing": pool_state.get("tickSpacing"),
        "sqrtPriceX96": pool_state.get("sqrtPriceX96") or request.get("sqrtPriceX96"),
    }


def _fee_bps(value: object) -> int:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def quote_v4_exact_input_single_from_pool_index(request: dict[str, Any]) -> dict[str, Any]:
    pool = _pool_lookup(request.get("pool"))
    if not pool:
        pool = _request_pool_state(request)
    if not pool:
        raise ValueError("robinhood testnet v4 pool index missing requested pool")

    token_in = _normalize_address(request.get("tokenIn")) or _normalize_address(pool.get("token0"))
    token_out = _normalize_address(request.get("tokenOut")) or _normalize_address(pool.get("token1"))
    pool_token0 = _normalize_address(pool.get("token0"))
    pool_token1 = _normalize_address(pool.get("token1"))
    if {token_in, token_out} != {pool_token0, pool_token1}:
        raise ValueError("requested hop tokens do not match robinhood testnet v4 pool index")

    sqrt_price_x96 = _pool_sqrt_price_x96(pool)
    if sqrt_price_x96 is None:
        raise ValueError("robinhood testnet v4 pool index missing sqrtPriceX96")

    fee_bps = _fee_bps(request.get("fee") or pool.get("fee"))
    fee_factor = Decimal(10_000 - fee_bps) / Decimal(10_000)
    amount_in_raw = Decimal(int(str(request.get("amountInRaw") or "0")))
    if amount_in_raw <= 0:
        raise ValueError("amountInRaw must be positive")

    raw_price = Decimal(sqrt_price_x96) * Decimal(sqrt_price_x96) / Decimal(2**192)
    if token_in == pool_token0 and token_out == pool_token1:
        amount_out_raw = int((amount_in_raw * raw_price * fee_factor).to_integral_value(rounding=ROUND_FLOOR))
    else:
        amount_out_raw = int((amount_in_raw / raw_price * fee_factor).to_integral_value(rounding=ROUND_FLOOR))

    return {
        "status": "quoted",
        "quoteMethod": "robinhood_testnet_v4_pool_index_spot_quote",
        "quoteMode": request.get("quoteMode") or "uniswap_v4_sdk_exact_input_single",
        "amountOutRaw": str(amount_out_raw),
        "sdkAdapter": "intent_trade.chains.robinhood_testnet.v4_quote_adapter:quote_v4_exact_input_single_from_pool_index",
        "quoteAdapter": "intent_trade.chains.robinhood_testnet.v4_quote_adapter:quote_v4_exact_input_single_from_pool_index",
        "poolState": {
            "protocol": "uniswap_v4",
            "pool": _normalize_pool_id(pool.get("pool") or pool.get("poolId")),
            "token0": pool_token0,
            "token1": pool_token1,
            "fee": int(pool.get("fee") or 0),
            "tickSpacing": int(pool.get("tickSpacing") or 0),
            "sqrtPriceX96": str(sqrt_price_x96),
        },
        "feeBps": fee_bps,
    }


__all__ = ["quote_v4_exact_input_single_from_pool_index"]
