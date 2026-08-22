"""Gas, delivery-cost, price-evidence, and net-profit guards."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

DEFAULT_GAS_TOKEN_PRICE_MAX_AGE_SECONDS = 120
DEFAULT_GAS_TOKEN_PRICE_MAX_DEVIATION_BPS = 100


def _impl():
    from intent_trade import direct_impl

    return direct_impl


def relay_cost_wei(
    protocol: dict[str, Any] | None = None,
    opportunity: dict[str, Any] | None = None,
) -> int:
    impl = _impl()
    raw = impl._first_value(
        protocol if isinstance(protocol, dict) else {},
        opportunity if isinstance(opportunity, dict) else {},
        names=("delivery_cost_wei", "deliveryCostWei", "relay_cost_wei", "relayCostWei"),
    )
    if raw in (None, ""):
        raw = impl._env(
            "UNIFIED_EXECUTOR_DELIVERY_COST_WEI",
            "TRIANGULAR_DELIVERY_COST_WEI",
            "UNIFIED_EXECUTOR_RELAY_COST_WEI",
            "TRIANGULAR_RELAY_COST_WEI",
        )
    try:
        return impl._positive_int_value(raw)
    except ValueError:
        return 0


def cache_risk_penalty_usdc_base_units(
    protocol: dict[str, Any] | None = None,
    opportunity: dict[str, Any] | None = None,
    cache_reports: dict[str, dict[str, Any]] | None = None,
) -> int:
    impl = _impl()
    raw = impl._first_value(
        protocol if isinstance(protocol, dict) else {},
        opportunity if isinstance(opportunity, dict) else {},
        names=("cache_risk_penalty_usdc", "cacheRiskPenaltyUsdc"),
    )
    if raw in (None, ""):
        raw = impl._env("UNIFIED_EXECUTOR_CACHE_RISK_PENALTY_USDC", "TRIANGULAR_CACHE_RISK_PENALTY_USDC")
    try:
        static_penalty = impl._usdc_decimal_to_base_units(raw)
    except ValueError:
        static_penalty = 0
    per_block = impl._first_value(
        protocol if isinstance(protocol, dict) else {},
        opportunity if isinstance(opportunity, dict) else {},
        names=("cache_risk_penalty_per_block_usdc", "cacheRiskPenaltyPerBlockUsdc"),
    )
    if per_block in (None, ""):
        per_block = impl._env(
            "UNIFIED_EXECUTOR_CACHE_RISK_PENALTY_PER_BLOCK_USDC",
            "TRIANGULAR_CACHE_RISK_PENALTY_PER_BLOCK_USDC",
        )
    try:
        per_block_penalty = impl._usdc_decimal_to_base_units(per_block)
    except ValueError:
        per_block_penalty = 0
    age_blocks = 0
    for report in (cache_reports or {}).values():
        if not isinstance(report, dict) or not report.get("ok"):
            continue
        try:
            age = impl._positive_int_value(report.get("ageBlocks"))
        except ValueError:
            age = 0
        if age == 0:
            try:
                current = impl._positive_int_value(report.get("currentBlock"))
                cache_block = impl._positive_int_value(report.get("cacheBlock"))
                age = max(0, current - cache_block) if current and cache_block else 0
            except ValueError:
                age = 0
        age_blocks = max(age_blocks, age)
    return static_penalty + per_block_penalty * age_blocks


def public_mempool_risk_penalty_usdc_base_units(
    protocol: dict[str, Any] | None = None,
    opportunity: dict[str, Any] | None = None,
) -> int:
    impl = _impl()
    raw = impl._first_value(
        protocol if isinstance(protocol, dict) else {},
        opportunity if isinstance(opportunity, dict) else {},
        names=(
            "public_mempool_risk_penalty_usdc",
            "publicMempoolRiskPenaltyUsdc",
            "mempool_risk_penalty_usdc",
            "mempoolRiskPenaltyUsdc",
        ),
    )
    if raw in (None, ""):
        raw = impl._env(
            "UNIFIED_EXECUTOR_PUBLIC_MEMPOOL_RISK_PENALTY_USDC",
            "TRIANGULAR_PUBLIC_MEMPOOL_RISK_PENALTY_USDC",
        )
    try:
        return impl._usdc_decimal_to_base_units(raw)
    except ValueError:
        return 0


def public_mempool_risk_penalty_bps(
    protocol: dict[str, Any] | None = None,
    opportunity: dict[str, Any] | None = None,
) -> int:
    impl = _impl()
    raw = impl._first_value(
        protocol if isinstance(protocol, dict) else {},
        opportunity if isinstance(opportunity, dict) else {},
        names=(
            "public_mempool_risk_penalty_bps",
            "publicMempoolRiskPenaltyBps",
            "mempool_risk_penalty_bps",
            "mempoolRiskPenaltyBps",
        ),
    )
    if raw in (None, ""):
        raw = impl._env(
            "UNIFIED_EXECUTOR_PUBLIC_MEMPOOL_RISK_PENALTY_BPS",
            "TRIANGULAR_PUBLIC_MEMPOOL_RISK_PENALTY_BPS",
        )
    try:
        return min(10_000, impl._positive_int_value(raw))
    except ValueError:
        return 0


def gas_token_price_max_age_seconds(
    protocol: dict[str, Any] | None = None,
    opportunity: dict[str, Any] | None = None,
) -> int:
    impl = _impl()
    raw = impl._first_value(
        protocol if isinstance(protocol, dict) else {},
        opportunity if isinstance(opportunity, dict) else {},
        names=("gas_token_price_max_age_seconds", "gasTokenPriceMaxAgeSeconds", "avax_usdc_price_max_age_seconds"),
    )
    if raw in (None, ""):
        raw = impl._env("AVAX_USDC_PRICE_MAX_AGE_SECONDS", "GAS_TOKEN_USDC_PRICE_MAX_AGE_SECONDS")
    try:
        return impl._positive_int_value(raw, default=DEFAULT_GAS_TOKEN_PRICE_MAX_AGE_SECONDS)
    except ValueError:
        return DEFAULT_GAS_TOKEN_PRICE_MAX_AGE_SECONDS


def gas_token_price_updated_at(
    protocol: dict[str, Any] | None = None,
    opportunity: dict[str, Any] | None = None,
) -> datetime | None:
    impl = _impl()
    raw = impl._first_value(
        protocol if isinstance(protocol, dict) else {},
        opportunity if isinstance(opportunity, dict) else {},
        names=(
            "avax_usdc_price_updated_at",
            "avaxUsdcPriceUpdatedAt",
            "gas_token_usdc_price_updated_at",
            "gasTokenUsdcPriceUpdatedAt",
        ),
    )
    if raw in (None, ""):
        raw = impl._env("AVAX_USDC_PRICE_UPDATED_AT", "GAS_TOKEN_USDC_PRICE_UPDATED_AT")
    if raw in (None, ""):
        return None
    try:
        numeric = Decimal(str(raw))
        if numeric > 0:
            return datetime.fromtimestamp(float(numeric), tz=timezone.utc)
    except (InvalidOperation, ValueError, OSError, OverflowError):
        pass
    return impl._parse_utc_datetime(raw)


def avax_usdc_price_micro(
    protocol: dict[str, Any] | None = None,
    opportunity: dict[str, Any] | None = None,
) -> int:
    impl = _impl()
    raw = impl._first_value(
        protocol if isinstance(protocol, dict) else {},
        opportunity if isinstance(opportunity, dict) else {},
        names=("avax_usdc_price", "avaxUsdcPrice", "gas_token_usdc_price", "gasTokenUsdcPrice"),
    )
    if raw in (None, ""):
        raw = impl._env("AVAX_USDC_PRICE", "GAS_TOKEN_USDC_PRICE")
    try:
        return impl._usdc_decimal_to_base_units(raw)
    except ValueError:
        return 0


def gas_token_usdc_price_report(
    protocol: dict[str, Any] | None = None,
    opportunity: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    require_production_sources: bool = False,
) -> dict[str, Any]:
    impl = _impl()

    def price_sources() -> list[dict[str, Any]]:
        raw = impl._first_value(
            protocol if isinstance(protocol, dict) else {},
            opportunity if isinstance(opportunity, dict) else {},
            names=("price_sources", "priceSources", "gas_token_price_sources", "gasTokenPriceSources"),
        )
        return [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    def max_deviation_bps() -> int:
        raw = impl._first_value(
            protocol if isinstance(protocol, dict) else {},
            opportunity if isinstance(opportunity, dict) else {},
            names=("price_max_deviation_bps", "priceMaxDeviationBps"),
        )
        if raw in (None, ""):
            raw = impl._env("AVAX_USDC_PRICE_MAX_DEVIATION_BPS", "GAS_TOKEN_USDC_PRICE_MAX_DEVIATION_BPS")
        try:
            return impl._positive_int_value(raw, default=DEFAULT_GAS_TOKEN_PRICE_MAX_DEVIATION_BPS)
        except ValueError:
            return DEFAULT_GAS_TOKEN_PRICE_MAX_DEVIATION_BPS

    def blacklist_path() -> Path:
        raw = impl._env(
            "UNIFIED_EXECUTOR_PRICE_SOURCE_BLACKLIST_FILE",
            "TRIANGULAR_PRICE_SOURCE_BLACKLIST_FILE",
            default="runtime/cache/unified_price_source_blacklist.json",
        )
        value = Path(raw)
        return value if value.is_absolute() else impl.SRC_ROOT / value

    def blacklist() -> dict[str, dict[str, Any]]:
        payload = impl._read_json_file(blacklist_path())
        sources = payload.get("sources")
        return sources if isinstance(sources, dict) else {}

    def record_source_issue(source_id: str, reason: str) -> None:
        if not source_id:
            return
        path = blacklist_path()
        payload = impl._read_json_file(path)
        sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
        entry = sources.get(source_id) if isinstance(sources.get(source_id), dict) else {}
        now_text = (now or impl._utc_now()).isoformat()
        sources[source_id] = {
            **entry,
            "reason": reason,
            "failureCount": int(entry.get("failureCount") or 0) + 1,
            "firstFailureAt": str(entry.get("firstFailureAt") or now_text),
            "lastFailureAt": now_text,
            "recoveryState": str(entry.get("recoveryState") or "manual_review_required"),
        }
        impl._write_json_file(path, {"schemaVersion": 1, "updatedAt": now_text, "sources": sources})

    price = avax_usdc_price_micro(protocol, opportunity)
    updated_at = gas_token_price_updated_at(protocol, opportunity)
    max_age = gas_token_price_max_age_seconds(protocol, opportunity)
    current = now or impl._utc_now()
    blocked_sources = blacklist()
    usable_sources: list[dict[str, Any]] = []
    source_errors: list[dict[str, str]] = []
    for index, source in enumerate(price_sources()):
        source_id = str(source.get("id") or source.get("source") or f"source_{index + 1}")
        if source_id in blocked_sources:
            source_errors.append({"source": source_id, "reason": "blacklisted"})
            continue
        try:
            source_price = impl._usdc_decimal_to_base_units(source.get("priceUsdc", source.get("price_usdc")))
        except ValueError:
            source_price = 0
        source_updated_at = impl._parse_utc_datetime(source.get("updatedAt") or source.get("updated_at"))
        if source_price <= 0 or source_updated_at is None:
            source_errors.append({"source": source_id, "reason": "invalid_price_source"})
            record_source_issue(source_id, "invalid_price_source")
            continue
        source_age = max(0, int((current - source_updated_at).total_seconds()))
        if max_age > 0 and source_age > max_age:
            source_errors.append({"source": source_id, "reason": "stale_price_source"})
            record_source_issue(source_id, "stale_price_source")
            continue
        usable_sources.append(
            {
                "id": source_id,
                "priceMicro": source_price,
                "updatedAt": source_updated_at.isoformat(),
                "ageSeconds": source_age,
                "kind": str(source.get("kind") or source.get("policy") or "unknown"),
            }
        )

    configured_policy = str(
        impl._first_value(
            protocol if isinstance(protocol, dict) else {},
            opportunity if isinstance(opportunity, dict) else {},
            names=("price_source_policy", "priceSourcePolicy"),
        )
        or ""
    ).strip()
    fallback_policy = ""
    if usable_sources:
        values = sorted(int(item["priceMicro"]) for item in usable_sources)
        price = values[len(values) // 2]
        updated_at = min(impl._parse_utc_datetime(item["updatedAt"]) for item in usable_sources)
        deviation_bps = max(
            abs(int(item["priceMicro"]) - price) * 10_000 // max(1, price)
            for item in usable_sources
        )
        if not configured_policy:
            configured_policy = "multi-source-median" if len(usable_sources) >= 2 else "diagnostic-single-source"
    else:
        deviation_bps = 0
        if not configured_policy:
            configured_policy = "legacy-single-source"

    report: dict[str, Any] = {
        "ok": False,
        "priceMicro": str(price),
        "updatedAt": updated_at.isoformat() if updated_at else "",
        "maxAgeSeconds": str(max_age),
        "priceSourcePolicy": configured_policy,
        "priceSourceCount": str(len(usable_sources)),
        "priceDeviationBps": str(deviation_bps),
        "priceSourceErrors": source_errors,
        "priceSourceBlacklist": {
            "file": str(blacklist_path()),
            "blockedSourceCount": str(len(blocked_sources)),
            "blockedSources": sorted(blocked_sources),
        },
        "priceSources": [
            {
                "id": item["id"],
                "priceMicro": str(item["priceMicro"]),
                "updatedAt": item["updatedAt"],
                "ageSeconds": str(item["ageSeconds"]),
                "kind": item["kind"],
            }
            for item in usable_sources
        ],
    }
    if price <= 0:
        report["reason"] = "gas_token_usdc_price_missing"
        return report
    if updated_at is None:
        report["reason"] = "gas_token_usdc_price_timestamp_missing"
        return report
    age = max(0, int((current - updated_at).total_seconds()))
    report["ageSeconds"] = str(age)
    if max_age > 0 and age > max_age:
        report["reason"] = "gas_token_usdc_price_stale"
        return report
    production_eligible = False
    if configured_policy in {"multi-source-median", "chainlink-derived"}:
        production_eligible = len(usable_sources) >= 2 and deviation_bps <= max_deviation_bps()
        if configured_policy == "chainlink-derived":
            fallback_policy = "chainlink-derived"
    report["priceSourceFallback"] = {
        "policy": fallback_policy,
        "productionEligible": production_eligible,
    }
    report["maxDeviationBps"] = str(max_deviation_bps())
    if require_production_sources and not production_eligible:
        report["reason"] = "gas_token_price_source_health_failed"
        return report
    report["ok"] = True
    report["reason"] = "gas_token_usdc_price_fresh"
    return report


def wei_cost_to_usdc_base_units(cost_wei: int, avax_usdc_price_micro: int) -> int:
    if cost_wei <= 0 or avax_usdc_price_micro <= 0:
        return 0
    return (int(cost_wei) * int(avax_usdc_price_micro) + 10**18 - 1) // 10**18


def unified_net_profit_report(
    *,
    expected_profit: int,
    gas_units: int,
    gas_price_wei: int,
    relay_cost_wei: int,
    cache_risk_penalty_usdc: int,
    public_mempool_risk_penalty_usdc: int = 0,
    public_mempool_risk_penalty_bps: int = 0,
    slippage_risk_penalty_usdc: int = 0,
    route_correlation_penalty_usdc: int = 0,
    avax_usdc_price_micro: int,
) -> dict[str, str]:
    gas_cost_wei = max(0, int(gas_units)) * max(0, int(gas_price_wei))
    relay_cost_usdc = wei_cost_to_usdc_base_units(max(0, int(relay_cost_wei)), avax_usdc_price_micro)
    gas_cost_usdc = wei_cost_to_usdc_base_units(gas_cost_wei, avax_usdc_price_micro)
    mempool_fixed_penalty = max(0, int(public_mempool_risk_penalty_usdc))
    mempool_bps = min(10_000, max(0, int(public_mempool_risk_penalty_bps)))
    mempool_bps_penalty = max(0, int(expected_profit)) * mempool_bps // 10_000
    mempool_penalty = mempool_fixed_penalty + mempool_bps_penalty
    net_profit = (
        int(expected_profit)
        - gas_cost_usdc
        - relay_cost_usdc
        - max(0, int(cache_risk_penalty_usdc))
        - mempool_penalty
        - max(0, int(slippage_risk_penalty_usdc))
        - max(0, int(route_correlation_penalty_usdc))
    )
    return {
        "expectedProfit": str(expected_profit),
        "gasUnits": str(gas_units),
        "gasPriceWei": str(gas_price_wei),
        "gasCostWei": str(gas_cost_wei),
        "gasCostUsdc": str(gas_cost_usdc),
        "relayCostWei": str(relay_cost_wei),
        "relayCostUsdc": str(relay_cost_usdc),
        "deliveryCostWei": str(relay_cost_wei),
        "deliveryCostUsdc": str(relay_cost_usdc),
        "cacheRiskPenaltyUsdc": str(max(0, int(cache_risk_penalty_usdc))),
        "publicMempoolRiskPenaltyUsdc": str(mempool_penalty),
        "publicMempoolRiskPenaltyFixedUsdc": str(mempool_fixed_penalty),
        "publicMempoolRiskPenaltyBps": str(mempool_bps),
        "publicMempoolRiskPenaltyBpsUsdc": str(mempool_bps_penalty),
        "slippageRiskPenaltyUsdc": str(max(0, int(slippage_risk_penalty_usdc))),
        "routeCorrelationPenaltyUsdc": str(max(0, int(route_correlation_penalty_usdc))),
        "avaxUsdcPriceMicro": str(avax_usdc_price_micro),
        "netProfit": str(net_profit),
    }
