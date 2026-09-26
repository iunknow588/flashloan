from __future__ import annotations

from typing import Any

from market.binance_market.dex_surface_types import _token_address
from market.strategy.limits import DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD


def route_address_identity_issues(route_symbols: list[str], route_addresses: list[str]) -> list[str]:
    """Return terminal reasons when a route cannot be quoted because token identity is incomplete."""
    if not route_symbols:
        return []
    if len(route_addresses) != len(route_symbols):
        return ["route_lacks_complete_token_address_identity"]
    issues: list[str] = []
    for index, (symbol, address) in enumerate(zip(route_symbols, route_addresses)):
        if _token_address(address):
            continue
        label = symbol or f"token#{index}"
        issues.append(f"route_missing_address_for_symbol:{label}")
    if issues:
        issues.insert(0, "route_lacks_complete_token_address_identity")
    return issues


def missing_route_identity_analysis(
    *,
    route_symbols: list[str],
    route_addresses: list[str],
    warnings: list[str],
    notional_usd: float = 1000.0,
) -> dict[str, Any]:
    issues = route_address_identity_issues(route_symbols, route_addresses)
    combined_warnings = list(warnings)
    combined_warnings.extend(issues)
    min_profit_usd = float(DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD)
    flash_fee_usd = notional_usd * 5.0 / 10_000.0
    return {
        "method": "static_amm_depth_probe",
        "notionalUsd": notional_usd,
        "inputSymbol": route_symbols[0] if route_symbols else None,
        "outputSymbol": route_symbols[-1] if route_symbols else None,
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "terminalStatus": "missing_route_identity",
        "complete": False,
        "requiresLiveQuoter": False,
        "adapterRequired": False,
        "stepCount": 0,
        "steps": [],
        "finalOutputUsd": None,
        "referenceFinalOutputUsd": None,
        "grossProfitUsd": None,
        "referenceGrossProfitUsd": None,
        "flashLoanFeeBps": 5.0,
        "flashLoanFeeUsd": flash_fee_usd,
        "gasReserveUsd": 1.0,
        "minProfitUsd": min_profit_usd,
        "offchainMinProfitUsd": min_profit_usd,
        "submissionMinProfitUsd": flash_fee_usd,
        "submissionNetProfitUsd": None,
        "offchainNetProfitUsd": None,
        "netProfitUsd": None,
        "referenceNetProfitUsd": None,
        "netProfitBps": None,
        "referenceNetProfitBps": None,
        "profitable": False,
        "executableCandidate": False,
        "sdkAdapterRequiredStepCount": 0,
        "recommendation": {
            "level": "blocked",
            "action": "route identity incomplete; provide exact token addresses before chain quote",
            "reasons": [
                "route_lacks_complete_token_address_identity",
                "symbol only is display-only; on-chain quote requires exact token address",
            ],
        },
        "warnings": combined_warnings,
    }
