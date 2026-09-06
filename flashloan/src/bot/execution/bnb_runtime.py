from __future__ import annotations

import time
from typing import Any, Callable, Iterable

from dex.intent_trade.chains.bnb import config as bnb
from dex.intent_trade.chains.bnb.quote import _connect
from bot.execution.bnb_exit_quote import BnbExitQuoteConfig, quote_bnb_exit_paths


def _looks_rate_limited(error: Any) -> bool:
    text = str(error or "").lower()
    return "429" in text or "rate limit" in text or "too many requests" in text


def connect_bnb_rpc_with_retries(
    rpc_urls: Iterable[str] | None = None,
    *,
    attempts: int = 3,
    backoff_seconds: float = 1.0,
    connector: Callable[[list[str]], tuple[Any, str]] = _connect,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[Any, str, dict[str, Any]]:
    urls = list(rpc_urls or bnb.DEFAULT_RPC_URLS)
    max_attempts = max(1, int(attempts or 1))
    failures: list[str] = []
    rate_limited = False
    for attempt in range(1, max_attempts + 1):
        try:
            w3, url = connector(urls)
            return w3, url, {"attempts": attempt, "failures": failures, "rate_limited": rate_limited}
        except Exception as exc:
            failures.append(str(exc))
            rate_limited = rate_limited or _looks_rate_limited(exc)
            if attempt >= max_attempts:
                break
            delay = max(0.0, float(backoff_seconds)) * (2 ** (attempt - 1) if rate_limited else 1)
            if delay > 0:
                sleeper(delay)
    raise RuntimeError("no usable BNB RPC after retries: " + "; ".join(failures[-3:]))


def read_bnb_gas_price(w3: Any) -> dict[str, Any]:
    try:
        gas_price_wei = int(w3.eth.gas_price)
    except Exception as exc:
        return {"ok": False, "source": "eth_gasPrice", "gas_price_wei": None, "gas_price_gwei": None, "error": str(exc)}
    return {
        "ok": True,
        "source": "eth_gasPrice",
        "gas_price_wei": gas_price_wei,
        "gas_price_gwei": gas_price_wei / 1e9,
        "error": None,
    }


def read_bnb_native_usd_price(
    w3: Any,
    *,
    router_address: str = BnbExitQuoteConfig.router_address,
    amount_in_wei: int = 10**18,
) -> dict[str, Any]:
    wbnb = bnb.TOKENS["WBNB"].address
    usdt = bnb.TOKENS["USDT"].address
    try:
        quote = quote_bnb_exit_paths(
            w3,
            collateral_asset=wbnb,
            debt_asset=usdt,
            amount_in_raw=int(amount_in_wei),
            router_address=router_address,
        )
        best = quote.get("best_quote") or {}
        amount_out = int(best.get("amount_out_raw") or 0)
        decimals = int(bnb.TOKENS["USDT"].decimals)
        return {
            "ok": amount_out > 0,
            "source": "pancakeswap_wbnb_usdt",
            "bnb_usd": amount_out / float(10**decimals) if amount_out > 0 else None,
            "quote": quote,
            "error": None if amount_out > 0 else "missing WBNB->USDT quote",
        }
    except Exception as exc:
        return {"ok": False, "source": "pancakeswap_wbnb_usdt", "bnb_usd": None, "quote": None, "error": str(exc)}


def read_bnb_runtime_metrics(w3: Any, *, router_address: str = BnbExitQuoteConfig.router_address) -> dict[str, Any]:
    return {
        "gas_price": read_bnb_gas_price(w3),
        "native_usd_price": read_bnb_native_usd_price(w3, router_address=router_address),
    }
