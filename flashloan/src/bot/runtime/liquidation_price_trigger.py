from __future__ import annotations

from typing import Any


STABLE_SYMBOLS = {"USDC", "USDC.E", "USDT", "DAI", "FRAX", "USDCE", "USDE"}


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _base_symbol(symbol: Any) -> str:
    value = str(symbol or "").strip().upper()
    for quote in ("USDT", "USDC", "USD"):
        if value.endswith(quote) and len(value) > len(quote):
            value = value[: -len(quote)]
            break
    aliases = {
        "WAVAX": "AVAX",
        "WETH": "ETH",
        "WETH.E": "ETH",
        "WBTC": "BTC",
        "BTC.B": "BTC",
        "USDCE": "USDC",
    }
    return aliases.get(value, value)


def _market_symbol(symbol: Any, price_snapshot: dict[str, float]) -> str | None:
    base = _base_symbol(symbol)
    if not base:
        return None
    if base in STABLE_SYMBOLS:
        return f"{base.replace('.', '')}USDT"
    for candidate in (f"{base}USDT", f"{base}USDC", base):
        if candidate in price_snapshot:
            return candidate
    return f"{base}USDT"


def _liquidation_threshold(position: dict[str, Any], fallback: float | None = None) -> float | None:
    for key in (
        "liquidation_threshold",
        "liquidation_threshold_fraction",
        "reserve_liquidation_threshold",
        "current_liquidation_threshold",
    ):
        value = _float_or_none(position.get(key))
        if value is None:
            continue
        if value > 100:
            return value / 10000.0
        if value > 1:
            return value / 100.0
        if value > 0:
            return value
    return fallback


def _position_symbol(position: dict[str, Any]) -> str:
    for key in ("token_symbol", "symbol", "collateral_symbol", "debt_symbol", "asset_symbol"):
        value = str(position.get(key) or "").strip()
        if value:
            return value
    return ""


def _value(position: dict[str, Any], keys: tuple[str, ...]) -> float:
    for key in keys:
        value = _float_or_none(position.get(key))
        if value is not None and value > 0:
            return value
    return 0.0


def _report_summary(report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary")
    return summary if isinstance(summary, dict) else {}


def build_liquidation_price_triggers(
    report: dict[str, Any],
    price_snapshot: dict[str, float],
    *,
    target_health_factor: float = 1.0,
    buffer_bps: int = 25,
) -> dict[str, Any]:
    """Build account-level price triggers; final liquidation still needs on-chain Aave checks."""
    if not isinstance(report, dict) or not isinstance(price_snapshot, dict):
        return {"enabled": False, "reason": "missing_report_or_prices", "triggers": []}
    positions = [position for position in report.get("positions") or [] if isinstance(position, dict)]
    summary = _report_summary(report)
    health_factor = _float_or_none(summary.get("health_factor") or report.get("health_factor"))
    total_debt = _float_or_none(summary.get("total_debt_base") or report.get("total_debt_base") or report.get("total_debt_in_base_currency"))
    fallback_lt = _liquidation_threshold(
        {
            "current_liquidation_threshold": summary.get("current_liquidation_threshold")
            or report.get("current_liquidation_threshold")
        }
    )
    if total_debt is None or total_debt <= 0:
        return {"enabled": False, "reason": "missing_total_debt", "triggers": []}

    collateral_rows: list[dict[str, Any]] = []
    debt_rows: list[dict[str, Any]] = []
    weighted_collateral = 0.0
    total_debt_by_positions = 0.0
    for position in positions:
        symbol = _position_symbol(position)
        market_symbol = _market_symbol(symbol, price_snapshot)
        current_price = _float_or_none(price_snapshot.get(market_symbol or ""))
        collateral_value = _value(position, ("collateral_value_base", "collateral_value_usd", "collateral_base"))
        debt_value = _value(position, ("debt_value_base", "total_debt_value_base", "debt_value_usd"))
        lt = _liquidation_threshold(position, fallback_lt)
        if collateral_value > 0 and lt and lt > 0:
            contribution = collateral_value * lt
            weighted_collateral += contribution
            collateral_rows.append(
                {
                    "asset": symbol,
                    "market_symbol": market_symbol,
                    "current_price": current_price,
                    "collateral_value_base": collateral_value,
                    "liquidation_threshold": lt,
                    "weighted_contribution_base": contribution,
                }
            )
        if debt_value > 0:
            total_debt_by_positions += debt_value
            debt_rows.append(
                {
                    "asset": symbol,
                    "market_symbol": market_symbol,
                    "current_price": current_price,
                    "debt_value_base": debt_value,
                }
            )

    if weighted_collateral <= 0 and health_factor and health_factor > 0:
        weighted_collateral = health_factor * total_debt
    if weighted_collateral <= 0:
        return {"enabled": False, "reason": "missing_weighted_collateral", "triggers": []}

    effective_hf = weighted_collateral / total_debt if total_debt > 0 else None
    if health_factor is None:
        health_factor = effective_hf
    triggers: list[dict[str, Any]] = []
    buffer_multiplier = 1.0 + max(0, int(buffer_bps)) / 10000.0

    for row in collateral_rows:
        current_price = row.get("current_price")
        contribution = float(row["weighted_contribution_base"])
        if not current_price or current_price <= 0 or contribution <= 0:
            continue
        other_weighted = weighted_collateral - contribution
        required_from_asset = target_health_factor * total_debt - other_weighted
        if required_from_asset <= 0:
            continue
        trigger_price = current_price * required_from_asset / contribution
        if trigger_price <= 0:
            continue
        trigger_with_buffer = trigger_price * buffer_multiplier
        distance_percent = (current_price - trigger_price) / trigger_price * 100.0
        triggers.append(
            {
                **row,
                "direction": "down",
                "trigger_price": trigger_price,
                "buffered_trigger_price": trigger_with_buffer,
                "distance_percent": distance_percent,
                "triggered": current_price <= trigger_with_buffer or (health_factor is not None and health_factor <= target_health_factor),
            }
        )

    debt_total_for_split = total_debt_by_positions if total_debt_by_positions > 0 else total_debt
    for row in debt_rows:
        current_price = row.get("current_price")
        debt_value = float(row["debt_value_base"])
        if not current_price or current_price <= 0 or debt_value <= 0:
            continue
        scaled_debt_value = debt_value * total_debt / debt_total_for_split
        other_debt = total_debt - scaled_debt_value
        max_debt_from_asset = weighted_collateral / target_health_factor - other_debt
        if max_debt_from_asset <= 0:
            continue
        trigger_price = current_price * max_debt_from_asset / scaled_debt_value
        if trigger_price <= 0:
            continue
        trigger_with_buffer = trigger_price / buffer_multiplier
        distance_percent = (trigger_price - current_price) / trigger_price * 100.0
        triggers.append(
            {
                **row,
                "direction": "up",
                "trigger_price": trigger_price,
                "buffered_trigger_price": trigger_with_buffer,
                "distance_percent": distance_percent,
                "triggered": current_price >= trigger_with_buffer or (health_factor is not None and health_factor <= target_health_factor),
            }
        )

    if not triggers:
        return {
            "enabled": False,
            "reason": "no_supported_price_trigger_assets",
            "health_factor": health_factor,
            "effective_health_factor": effective_hf,
            "triggers": [],
        }
    nearest = sorted(triggers, key=lambda item: abs(float(item.get("distance_percent") or 0.0)))[0]
    return {
        "enabled": True,
        "target_health_factor": target_health_factor,
        "buffer_bps": int(buffer_bps),
        "health_factor": health_factor,
        "effective_health_factor": effective_hf,
        "triggered": any(bool(item.get("triggered")) for item in triggers),
        "nearest": nearest,
        "triggers": triggers,
    }


def accounts_triggered_by_prices(
    rows: list[dict[str, Any]],
    price_snapshot: dict[str, float],
    *,
    target_health_factor: float = 1.0,
    buffer_bps: int = 25,
) -> list[str]:
    accounts: list[str] = []
    for row in rows:
        report = row.get("report") if isinstance(row.get("report"), dict) else {}
        if not report and isinstance(row.get("metadata"), dict):
            metadata = row.get("metadata") or {}
            if isinstance(metadata.get("report"), dict):
                report = metadata.get("report") or {}
        if not report and isinstance(row.get("summary"), dict):
            report = row
        trigger = build_liquidation_price_triggers(
            report,
            price_snapshot,
            target_health_factor=target_health_factor,
            buffer_bps=buffer_bps,
        )
        account = str(row.get("account") or report.get("account") or "").strip()
        if account and trigger.get("triggered") and account not in accounts:
            accounts.append(account)
    return accounts
