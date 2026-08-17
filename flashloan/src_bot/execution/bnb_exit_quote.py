from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from web3 import Web3

from intent_trade.chains.bnb import config as bnb
from intent_trade.chains.bnb.quote import ROUTER_ABI, V3_QUOTER_ABI


DEFAULT_INTERMEDIATE_SYMBOLS = ("WBNB", "USDT", "USDC")


@dataclass(frozen=True)
class BnbExitQuoteConfig:
    router_address: str = "0x10ED43C718714eb63d5aA57B78B54704E256024E"
    v3_quoter_address: str = "0x78D78E420Da98ad378D7799bE8f4AF69033EB077"
    slippage_bps: int = 50


def _checksum(value: str) -> str:
    return Web3.to_checksum_address(str(value))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _known_intermediates(symbols: Iterable[str] = DEFAULT_INTERMEDIATE_SYMBOLS) -> list[str]:
    addresses = []
    for symbol in symbols:
        token = bnb.TOKENS.get(str(symbol).upper())
        if token is None:
            continue
        checksum = _checksum(token.address)
        if checksum not in addresses:
            addresses.append(checksum)
    return addresses


def build_bnb_exit_paths(collateral_asset: str, debt_asset: str, *, intermediates: Iterable[str] | None = None) -> list[list[str]]:
    collateral = _checksum(collateral_asset)
    debt = _checksum(debt_asset)
    if collateral == debt:
        return [[collateral]]
    middle_assets = [address for address in _known_intermediates(intermediates or DEFAULT_INTERMEDIATE_SYMBOLS) if address not in {collateral, debt}]
    paths: list[list[str]] = [[collateral, debt]]
    for middle in middle_assets:
        paths.append([collateral, middle, debt])
    wbnb = _checksum(bnb.TOKENS["WBNB"].address)
    stables = [_checksum(bnb.TOKENS[symbol].address) for symbol in ("USDT", "USDC") if symbol in bnb.TOKENS]
    for stable in stables:
        if len({collateral, wbnb, stable, debt}) == 4:
            paths.append([collateral, wbnb, stable, debt])
    unique: list[list[str]] = []
    seen = set()
    for path in paths:
        key = tuple(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _min_amount_out(amount_out: int, slippage_bps: int) -> int:
    bps = max(0, min(10_000, int(slippage_bps or 0)))
    return int(int(amount_out) * (10_000 - bps) // 10_000)


def quote_pancake_v2_path(w3: Web3, router_address: str, amount_in_raw: int, path: list[str]) -> dict[str, Any]:
    if len(path) == 1:
        return {
            "ok": True,
            "dex": "pancakeswap_v2",
            "quote_method": "same_asset",
            "path": path,
            "amount_in_raw": int(amount_in_raw),
            "amount_out_raw": int(amount_in_raw),
            "amounts_raw": [int(amount_in_raw)],
            "error": None,
        }
    router = w3.eth.contract(address=_checksum(router_address), abi=ROUTER_ABI)
    amounts = router.functions.getAmountsOut(int(amount_in_raw), [_checksum(item) for item in path]).call()
    return {
        "ok": True,
        "dex": "pancakeswap_v2",
        "quote_method": "router_getAmountsOut",
        "path": [_checksum(item) for item in path],
        "amount_in_raw": int(amount_in_raw),
        "amount_out_raw": int(amounts[-1]),
        "amounts_raw": [int(item) for item in amounts],
        "error": None,
    }


def quote_pancake_v3_path(
    w3: Web3,
    quoter_address: str,
    amount_in_raw: int,
    path: list[str],
    fee_tier: int = 500,
    *,
    check_reverse: bool = True,
) -> dict[str, Any]:
    if len(path) != 2:
        raise ValueError("pancakeswap v3 exit quoting only supports direct single-hop paths")
    quoter = w3.eth.contract(address=_checksum(quoter_address), abi=V3_QUOTER_ABI)
    amount_out, sqrt_after, ticks_crossed, gas_estimate = quoter.functions.quoteExactInputSingle(
        {
            "tokenIn": _checksum(path[0]),
            "tokenOut": _checksum(path[1]),
            "amountIn": int(amount_in_raw),
            "fee": int(fee_tier),
            "sqrtPriceLimitX96": 0,
        }
    ).call()
    reverse_quote_ok = None
    reverse_amount_out = None
    if check_reverse and int(amount_out or 0) > 0:
        try:
            reverse_amount_out, _reverse_sqrt, _reverse_ticks, _reverse_gas = quoter.functions.quoteExactInputSingle(
                {
                    "tokenIn": _checksum(path[1]),
                    "tokenOut": _checksum(path[0]),
                    "amountIn": int(amount_out),
                    "fee": int(fee_tier),
                    "sqrtPriceLimitX96": 0,
                }
            ).call()
            reverse_quote_ok = int(reverse_amount_out or 0) > 0
        except Exception:
            reverse_quote_ok = False
    return {
        "ok": True,
        "dex": "pancakeswap_v3",
        "quote_method": "quoter_quoteExactInputSingle",
        "path": [_checksum(item) for item in path],
        "amount_in_raw": int(amount_in_raw),
        "amount_out_raw": int(amount_out),
        "amounts_raw": [int(amount_in_raw), int(amount_out)],
        "fee_tier": int(fee_tier),
        "fee_bps": float(fee_tier) / 100.0,
        "reverse_quote_ok": reverse_quote_ok,
        "reverse_amount_out_raw": int(reverse_amount_out) if reverse_amount_out is not None else None,
        "sqrt_price_x96_after": str(sqrt_after),
        "initialized_ticks_crossed": int(ticks_crossed),
        "quoter_gas_estimate": int(gas_estimate),
        "error": None,
    }


def quote_bnb_exit_paths(
    w3: Web3,
    *,
    collateral_asset: str,
    debt_asset: str,
    amount_in_raw: int,
    router_address: str = BnbExitQuoteConfig.router_address,
    v3_quoter_address: str = BnbExitQuoteConfig.v3_quoter_address,
    slippage_bps: int = BnbExitQuoteConfig.slippage_bps,
    quote_block: int | None = None,
) -> dict[str, Any]:
    paths = build_bnb_exit_paths(collateral_asset, debt_asset)
    quotes = []
    quoted_at = _utc_now()
    for path in paths:
        try:
            quote = quote_pancake_v2_path(w3, router_address, int(amount_in_raw), path)
            quote.update(
                {
                    "quote_at": quoted_at,
                    "quote_block": int(quote_block if quote_block is not None else getattr(w3.eth, "block_number", 0) or 0),
                    "fee_bps": 25,
                    "price_impact_bps": None,
                    "min_amount_out_raw": _min_amount_out(int(quote["amount_out_raw"]), slippage_bps),
                }
            )
        except Exception as exc:
            quote = {
                "ok": False,
                "dex": "pancakeswap_v2",
                "quote_method": "router_getAmountsOut",
                "path": path,
                "amount_in_raw": int(amount_in_raw),
                "amount_out_raw": 0,
                "min_amount_out_raw": 0,
                "quote_at": quoted_at,
                "quote_block": int(quote_block if quote_block is not None else getattr(w3.eth, "block_number", 0) or 0),
                "fee_bps": 25,
                "price_impact_bps": None,
                "error": str(exc),
            }
        quotes.append(quote)
        if len(path) == 2:
            try:
                v3_quote = quote_pancake_v3_path(w3, v3_quoter_address, int(amount_in_raw), path)
                v3_quote.update(
                    {
                        "quote_at": quoted_at,
                        "quote_block": int(quote_block if quote_block is not None else getattr(w3.eth, "block_number", 0) or 0),
                        "fee_bps": float(v3_quote.get("fee_bps") or 0.0),
                        "price_impact_bps": None,
                        "min_amount_out_raw": _min_amount_out(int(v3_quote["amount_out_raw"]), slippage_bps),
                    }
                )
                quotes.append(v3_quote)
            except Exception as exc:
                quotes.append(
                    {
                        "ok": False,
                        "dex": "pancakeswap_v3",
                        "quote_method": "quoter_quoteExactInputSingle",
                        "path": path,
                        "amount_in_raw": int(amount_in_raw),
                        "amount_out_raw": 0,
                        "min_amount_out_raw": 0,
                        "quote_at": quoted_at,
                        "quote_block": int(quote_block if quote_block is not None else getattr(w3.eth, "block_number", 0) or 0),
                        "fee_bps": 500,
                        "price_impact_bps": None,
                        "error": str(exc),
                    }
                )
    best = max((row for row in quotes if row.get("ok")), key=lambda row: int(row.get("amount_out_raw") or 0), default=None)
    return {
        "network": "bnb",
        "chain_id": 56,
        "dex": "pancakeswap_v2",
        "router_address": _checksum(router_address),
        "v3_quoter_address": _checksum(v3_quoter_address),
        "quote_block": int(quote_block if quote_block is not None else getattr(w3.eth, "block_number", 0) or 0),
        "slippage_bps": int(slippage_bps),
        "collateral_asset": _checksum(collateral_asset),
        "debt_asset": _checksum(debt_asset),
        "amount_in_raw": int(amount_in_raw),
        "quote_at": quoted_at,
        "quotes": quotes,
        "best_quote": best,
        "rejected_reason": None if best else "exit_quote_failed",
    }


def attach_exit_quotes_to_candidates(
    w3: Web3,
    candidates: Iterable[dict[str, Any]],
    *,
    router_address: str = BnbExitQuoteConfig.router_address,
    v3_quoter_address: str = BnbExitQuoteConfig.v3_quoter_address,
    slippage_bps: int = BnbExitQuoteConfig.slippage_bps,
) -> list[dict[str, Any]]:
    rows = []
    for candidate in candidates:
        row = dict(candidate)
        if row.get("status") != "candidate":
            rows.append(row)
            continue
        collateral_asset = str(row.get("collateral_asset") or "")
        debt_asset = str(row.get("debt_asset") or "")
        amount_in = int(row.get("seize_tokens_raw") or 0)
        if not collateral_asset or not debt_asset or amount_in <= 0:
            row["status"] = "rejected"
            row["rejected_reason"] = "missing_exit_quote_input"
            rows.append(row)
            continue
        quote = quote_bnb_exit_paths(
            w3,
            collateral_asset=collateral_asset,
            debt_asset=debt_asset,
            amount_in_raw=amount_in,
            router_address=router_address,
            v3_quoter_address=v3_quoter_address,
            slippage_bps=slippage_bps,
        )
        row["exit_quote"] = quote
        if not quote.get("best_quote"):
            row["status"] = "rejected"
            row["rejected_reason"] = "exit_quote_failed"
        rows.append(row)
    return rows
