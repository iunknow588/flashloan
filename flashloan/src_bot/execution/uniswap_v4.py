from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


V4QuoteAdapter = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class V4QuoteRequest:
    network: str
    chain_id: int | None
    pool: str
    token_in: str
    token_out: str
    amount_in_raw: int
    block_number: int | None = None
    fee: object = None
    route_symbols: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "network": self.network,
            "chainId": self.chain_id,
            "pool": self.pool,
            "tokenIn": self.token_in,
            "tokenOut": self.token_out,
            "amountInRaw": str(int(self.amount_in_raw)),
            "blockNumber": self.block_number,
            "fee": self.fee,
            "routeSymbols": list(self.route_symbols),
            "quoteMode": "uniswap_v4_sdk_exact_input_single",
            "adapterHints": [
                "pool_manager_state_required",
                "hook_aware_quote_required",
                "sdk_or_adapter_must_return_amount_out_raw",
            ],
        }


def build_v4_quote_request(
    *,
    network: str,
    chain_id: int | None,
    pool: str,
    token_in: str,
    token_out: str,
    amount_in_raw: int,
    block_number: int | None = None,
    fee: object = None,
    route_symbols: tuple[str, ...] = (),
) -> dict[str, Any]:
    return V4QuoteRequest(
        network=network,
        chain_id=chain_id,
        pool=pool,
        token_in=token_in,
        token_out=token_out,
        amount_in_raw=amount_in_raw,
        block_number=block_number,
        fee=fee,
        route_symbols=route_symbols,
    ).as_dict()


def quote_v4_exact_input_single(*, adapter: V4QuoteAdapter | None, request: dict[str, Any]) -> dict[str, Any]:
    if adapter is None:
        raise RuntimeError("uniswap v4 quote adapter is not configured")
    response = adapter(dict(request))
    if not isinstance(response, dict):
        raise TypeError("uniswap v4 quote adapter must return a dict")
    amount_out_raw = response.get("amountOutRaw")
    if amount_out_raw in (None, ""):
        raise ValueError("uniswap v4 quote adapter response is missing amountOutRaw")
    normalized = dict(response)
    normalized.setdefault("status", "quoted")
    normalized.setdefault("quoteMethod", "uniswap_v4_sdk_exact_input_single")
    normalized.setdefault("quoteMode", request.get("quoteMode") or "uniswap_v4_sdk_exact_input_single")
    normalized.setdefault("pool", request.get("pool"))
    normalized.setdefault("tokenIn", request.get("tokenIn"))
    normalized.setdefault("tokenOut", request.get("tokenOut"))
    normalized.setdefault("amountInRaw", request.get("amountInRaw"))
    normalized["amountOutRaw"] = str(amount_out_raw)
    return normalized


__all__ = ["V4QuoteAdapter", "V4QuoteRequest", "build_v4_quote_request", "quote_v4_exact_input_single"]
