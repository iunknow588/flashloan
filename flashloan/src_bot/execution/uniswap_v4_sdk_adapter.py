from __future__ import annotations

from importlib import import_module
import os
from typing import Any, Callable, Mapping

from core.chain_data_registry import chain_data_by_chain_id, chain_data_by_network
from execution.uniswap_v4 import quote_v4_exact_input_single


V4QuoteAdapter = Callable[[dict[str, Any]], dict[str, Any]]


def _resolve_callable(reference: str) -> Any:
    module_name, separator, attr_path = str(reference or "").strip().partition(":")
    if not module_name or not separator or not attr_path:
        raise ValueError("V4 adapter reference must use module:callable format")
    target: Any = import_module(module_name)
    for part in attr_path.split("."):
        target = getattr(target, part)
    return target


def _chain_default_reference(network: str | None, chain_id: int | None = None) -> str:
    if chain_id is not None:
        try:
            row = chain_data_by_chain_id(int(chain_id)) or {}
        except Exception:
            row = {}
        defaults = row.get("defaults") if isinstance(row.get("defaults"), dict) else {}
        for candidate in (
            defaults.get("v4_quote_adapter"),
            defaults.get("uniswap_v4_quote_adapter"),
        ):
            reference = str(candidate or "").strip()
            if reference:
                return reference
    normalized = str(network or "").strip().lower()
    if not normalized:
        return ""
    try:
        row = chain_data_by_network(normalized) or {}
    except Exception:
        row = {}
    defaults = row.get("defaults") if isinstance(row.get("defaults"), dict) else {}
    for candidate in (
        defaults.get("v4_quote_adapter"),
        defaults.get("uniswap_v4_quote_adapter"),
    ):
        reference = str(candidate or "").strip()
        if reference:
            return reference
    return ""


def _optional_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def reference_quote_adapter(request: dict[str, Any]) -> dict[str, Any]:
    reference_amount = request.get("referenceAmountOutRaw")
    if reference_amount in (None, ""):
        reference_amount = request.get("referenceOutputAmountRaw")
    amount_out_raw = _optional_int(reference_amount)
    if amount_out_raw is None:
        amount_in_raw = _optional_int(request.get("amountInRaw")) or 0
        fee = _optional_int(request.get("fee")) or 0
        amount_out_raw = (amount_in_raw * max(0, 10_000 - fee)) // 10_000
    return {
        "status": "quoted",
        "quoteMethod": "uniswap_v4_reference_quote",
        "quoteMode": request.get("quoteMode") or "uniswap_v4_sdk_exact_input_single",
        "amountOutRaw": str(int(amount_out_raw)),
        "sdkAdapter": "reference_quote_adapter",
        "quoteAdapter": "reference_quote_adapter",
    }


def load_v4_sdk_adapter(
    env: Mapping[str, str] | None = None,
    *,
    network: str | None = None,
    chain_id: int | None = None,
) -> V4QuoteAdapter | None:
    values = env or os.environ
    reference = str(
        values.get("UNISWAP_V4_SDK_ADAPTER")
        or values.get("DEX_V4_SDK_ADAPTER")
        or values.get("ROBINHOOD_TESTNET_UNISWAP_V4_SDK_ADAPTER")
        or values.get("ROBINHOOD_TESTNET_V4_QUOTE_ADAPTER")
        or values.get("ROBINHOOD_V4_QUOTE_ADAPTER")
        or values.get("ROBINHOOD_V4_SDK_ADAPTER")
        or _chain_default_reference(network, chain_id)
        or ""
    ).strip()
    if not reference:
        return None
    target = _resolve_callable(reference)
    if not callable(target):
        raise TypeError("UNISWAP_V4_SDK_ADAPTER must resolve to a callable")
    return target


def quote_v4_exact_input_single_from_env(request: dict[str, Any]) -> dict[str, Any]:
    adapter = load_v4_sdk_adapter(network=request.get("network"), chain_id=request.get("chainId")) or reference_quote_adapter
    return quote_v4_exact_input_single(adapter=adapter, request=request)


__all__ = ["V4QuoteAdapter", "load_v4_sdk_adapter", "quote_v4_exact_input_single_from_env", "reference_quote_adapter"]
