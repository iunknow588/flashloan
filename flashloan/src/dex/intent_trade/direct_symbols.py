"""Symbol normalization and route-target policy helpers."""

from __future__ import annotations

from typing import Any


def _impl():
    from dex.intent_trade import direct_impl

    return direct_impl


def base_symbol_from_row(row: Any) -> str:
    if isinstance(row, str):
        value = row.strip().upper()
        if not value:
            return ""
        for suffix in ("USDT", "USDC", "FDUSD", "BUSD", "TUSD"):
            if value.endswith(suffix) and len(value) > len(suffix):
                return value[: -len(suffix)]
        return value
    if not isinstance(row, dict):
        return ""
    for key in ("base_symbol", "token_symbol", "symbol"):
        value = str(row.get(key) or "").strip().upper()
        if value:
            for suffix in ("USDT", "USDC", "FDUSD", "BUSD", "TUSD"):
                if value.endswith(suffix) and len(value) > len(suffix):
                    return value[: -len(suffix)]
            return value
    return ""


def normalized_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def is_stable_exercise_symbol(value: Any) -> bool:
    return normalized_symbol(value) in _impl().STABLE_EXERCISE_SYMBOLS


def allow_stable_exercise_targets(
    protocol: dict[str, Any] | None = None,
    opportunity: dict[str, Any] | None = None,
) -> bool:
    impl = _impl()
    raw = impl._first_value(
        protocol if isinstance(protocol, dict) else {},
        opportunity if isinstance(opportunity, dict) else {},
        names=("allow_stable_exercise_targets", "allowStableExerciseTargets"),
    )
    if raw is not None:
        return impl._bool_value(raw, False)
    return impl._env_bool("UNIFIED_EXECUTOR_ALLOW_STABLE_TARGETS", "TRIANGULAR_ALLOW_STABLE_TARGETS")


def allow_single_pair_diagnostic(
    protocol: dict[str, Any] | None = None,
    opportunity: dict[str, Any] | None = None,
) -> bool:
    impl = _impl()
    raw = impl._first_value(
        protocol if isinstance(protocol, dict) else {},
        opportunity if isinstance(opportunity, dict) else {},
        names=("allow_single_pair_diagnostic", "allowSinglePairDiagnostic"),
    )
    if raw is not None:
        return impl._bool_value(raw, False)
    return impl._env_bool(
        "UNIFIED_EXECUTOR_ALLOW_SINGLE_PAIR_DIAGNOSTIC",
        "TRIANGULAR_ALLOW_SINGLE_PAIR_DIAGNOSTIC",
    )


def symbol_aliases(symbol: str) -> set[str]:
    value = str(symbol or "").strip().upper()
    if not value:
        return set()
    aliases = {value}
    if "." in value:
        aliases.add(value.split(".", 1)[0])
    wrapped = {
        "AVAX": {"WAVAX"},
        "WAVAX": {"AVAX"},
        "ETH": {"WETH", "WETH.E"},
        "WETH": {"ETH", "WETH.E"},
        "BTC": {"BTC.B"},
        "BTC.B": {"BTC"},
        "WBTC": {"WBTC.E"},
        "WBTC.E": {"WBTC"},
        "AAVE": {"AAVE.E"},
    }
    aliases.update(wrapped.get(value, set()))
    return {item for item in aliases if item}


def float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
