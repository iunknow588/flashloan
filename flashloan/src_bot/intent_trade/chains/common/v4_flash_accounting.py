from __future__ import annotations

import os
from typing import Any

from intent_trade.chains.common.dex_candidate_tables import _stable_symbol


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
DEFAULT_MIN_CANDIDATE_POOL_COUNT = 5
DEFAULT_MIN_POOLMANAGER_BALANCE_UNITS = 1.0


def as_int_units(value: object) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return int(text)
    except ValueError:
        try:
            return int(float(text))
        except (TypeError, ValueError, OverflowError):
            return 0


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return default
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else default


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(str(os.getenv(name, default)).strip()))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        value = float(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        return default
    return max(0.0, value) if value == value and value not in (float("inf"), float("-inf")) else default


def _actual_pool_ids(pool_ids: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for pool_id in pool_ids:
        text = str(pool_id or "").strip()
        if not (text.startswith("0x") and len(text) == 66):
            continue
        normalized = text.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(text)
    return result


def _metadata_balance_raw(metadata: dict[str, Any]) -> int:
    raw = metadata.get("poolManagerBalanceRaw")
    if raw is not None:
        return as_int_units(raw)
    balance = metadata.get("poolManagerBalance")
    decimals = metadata.get("decimals")
    if balance is None or decimals is None:
        return 0
    try:
        return int(float(balance) * (10 ** int(decimals)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _metadata_balance_units(metadata: dict[str, Any], raw: int) -> float:
    value = metadata.get("poolManagerBalance")
    if value is not None:
        return _as_float(value)
    try:
        scale = 10 ** int(metadata.get("decimals") or 0)
    except (TypeError, ValueError, OverflowError):
        scale = 1
    return raw / scale if scale else float(raw)


def _pool_key_balance_sources(liquidity: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for pool_key in liquidity.get("pool_keys") or []:
        if isinstance(pool_key, dict):
            sources.append(pool_key)
    for pool in liquidity.get("pools") or []:
        if isinstance(pool, dict) and isinstance(pool.get("poolKey"), dict):
            sources.append(pool["poolKey"])
    return sources


def _configured_flash_sources(liquidity: dict[str, Any]) -> list[dict[str, Any]]:
    payload = liquidity.get("payload") if isinstance(liquidity.get("payload"), dict) else {}
    rows = payload.get("flashAccountingSources") or payload.get("flash_accounting_sources") or []
    return [item for item in rows if isinstance(item, dict)]


def _native_flash_source(liquidity: dict[str, Any]) -> dict[str, Any] | None:
    payload = liquidity.get("payload") if isinstance(liquidity.get("payload"), dict) else {}
    raw = as_int_units(payload.get("nativePoolManagerBalanceWei"))
    if raw <= 0:
        return None
    return {
        "address": ZERO_ADDRESS,
        "symbol": "ETH",
        "decimals": 18,
        "poolManagerBalanceRaw": str(raw),
        "poolManagerBalance": raw / 10**18,
        "metadataOk": True,
        "source": "nativePoolManagerBalanceWei",
    }


def _record_currency(
    by_address: dict[str, dict[str, Any]],
    *,
    address: str,
    metadata: dict[str, Any],
    pool_id: str = "",
    source: str,
) -> None:
    normalized = address.lower()
    raw_balance = _metadata_balance_raw(metadata)
    if raw_balance <= 0:
        return
    current = by_address.setdefault(
        normalized,
        {
            "address": address,
            "symbol": str(metadata.get("symbol") or ("ETH" if normalized == ZERO_ADDRESS else address[:8])).strip(),
            "decimals": metadata.get("decimals"),
            "balance_raw": raw_balance,
            "metadata_ok": bool(metadata.get("metadataOk", True)),
            "pool_ids": [],
            "sources": [],
        },
    )
    if raw_balance > int(current.get("balance_raw") or 0):
        current["balance_raw"] = raw_balance
        current["symbol"] = str(metadata.get("symbol") or current.get("symbol") or address[:8]).strip()
        current["decimals"] = metadata.get("decimals", current.get("decimals"))
    current["metadata_ok"] = bool(current.get("metadata_ok")) and bool(metadata.get("metadataOk", True))
    if pool_id and pool_id not in current["pool_ids"]:
        current["pool_ids"].append(pool_id)
    if source and source not in current["sources"]:
        current["sources"].append(source)


def _collect_pool_key_sources(liquidity: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_address: dict[str, dict[str, Any]] = {}
    for pool_key in _pool_key_balance_sources(liquidity):
        pool_id = str(pool_key.get("poolId") or "").strip()
        for currency_key, metadata_key in (("currency0", "currency0Metadata"), ("currency1", "currency1Metadata")):
            metadata = pool_key.get(metadata_key) if isinstance(pool_key.get(metadata_key), dict) else {}
            address = str(pool_key.get(currency_key) or metadata.get("address") or "").strip()
            if not address:
                continue
            _record_currency(
                by_address,
                address=address,
                metadata=metadata,
                pool_id=pool_id,
                source="decoded_pool_key_metadata",
            )
    native = _native_flash_source(liquidity)
    if native is not None:
        _record_currency(
            by_address,
            address=ZERO_ADDRESS,
            metadata=native,
            source=str(native.get("source") or "nativePoolManagerBalanceWei"),
        )
    return by_address


def _collect_configured_sources(liquidity: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_address: dict[str, dict[str, Any]] = {}
    for item in _configured_flash_sources(liquidity):
        address = str(item.get("address") or item.get("currency") or "").strip()
        if not address:
            continue
        metadata = {
            "address": address,
            "symbol": item.get("symbol") or item.get("token_symbol"),
            "decimals": item.get("decimals"),
            "poolManagerBalanceRaw": item.get("poolManagerBalanceRaw") or item.get("poolmanager_balance_raw"),
            "poolManagerBalance": item.get("poolManagerBalance") or item.get("poolmanager_balance"),
            "metadataOk": item.get("metadataOk", item.get("metadata_ok", True)),
        }
        _record_currency(
            by_address,
            address=address,
            metadata=metadata,
            source=str(item.get("source") or "configured_flash_accounting_sources"),
        )
        current = by_address.get(address.lower())
        if current is not None:
            for pool_id in item.get("poolIds") or item.get("pool_ids") or []:
                pool_id_text = str(pool_id or "").strip()
                if pool_id_text and pool_id_text not in current["pool_ids"]:
                    current["pool_ids"].append(pool_id_text)
    return by_address


def _merge_sources(*source_maps: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for source_map in source_maps:
        for normalized, item in source_map.items():
            current = merged.setdefault(
                normalized,
                {
                    "address": str(item.get("address") or normalized),
                    "symbol": str(item.get("symbol") or "").strip(),
                    "decimals": item.get("decimals"),
                    "balance_raw": int(item.get("balance_raw") or 0),
                    "metadata_ok": bool(item.get("metadata_ok", True)),
                    "pool_ids": [],
                    "sources": [],
                },
            )
            if int(item.get("balance_raw") or 0) > int(current.get("balance_raw") or 0):
                current["balance_raw"] = int(item.get("balance_raw") or 0)
                current["symbol"] = str(item.get("symbol") or current.get("symbol") or "").strip()
                current["decimals"] = item.get("decimals", current.get("decimals"))
            elif not current.get("symbol") and item.get("symbol"):
                current["symbol"] = str(item.get("symbol") or "").strip()
                current["decimals"] = item.get("decimals", current.get("decimals"))
            current["metadata_ok"] = bool(current.get("metadata_ok")) and bool(item.get("metadata_ok", True))
            for pool_id in item.get("pool_ids") or []:
                if pool_id not in current["pool_ids"]:
                    current["pool_ids"].append(pool_id)
            for source in item.get("sources") or []:
                if source not in current["sources"]:
                    current["sources"].append(source)
    return merged


def _binance_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not normalized:
        return ""
    if normalized == "USDT":
        return "USDT"
    return f"{normalized}/USDT"


def v4_flash_accounting_source_rows(
    liquidity: dict[str, Any],
    *,
    pool_manager: str,
    min_candidate_pool_count: int | None = None,
    min_poolmanager_balance_units: float | None = None,
    include_ineffective: bool = False,
) -> list[dict[str, Any]]:
    by_address = _merge_sources(_collect_configured_sources(liquidity), _collect_pool_key_sources(liquidity))
    min_pool_count = (
        _env_int("ROBINHOOD_TESTNET_V4_MIN_CANDIDATE_POOL_COUNT", DEFAULT_MIN_CANDIDATE_POOL_COUNT)
        if min_candidate_pool_count is None
        else max(0, int(min_candidate_pool_count))
    )
    min_balance_units = (
        _env_float("ROBINHOOD_TESTNET_V4_MIN_POOLMANAGER_BALANCE", DEFAULT_MIN_POOLMANAGER_BALANCE_UNITS)
        if min_poolmanager_balance_units is None
        else max(0.0, float(min_poolmanager_balance_units))
    )
    rows: list[dict[str, Any]] = []
    for item in by_address.values():
        raw_balance = int(item.get("balance_raw") or 0)
        balance_units = _metadata_balance_units({"decimals": item.get("decimals")}, raw_balance)
        symbol = str(item.get("symbol") or "").strip()
        address = str(item.get("address") or "").strip()
        is_native = address.lower() == ZERO_ADDRESS.lower()
        executor_supported = not is_native
        actual_pool_ids = _actual_pool_ids(item.get("pool_ids") or [])
        meets_pool_count = len(actual_pool_ids) >= min_pool_count
        meets_balance_floor = balance_units >= min_balance_units
        effective_flash_asset = meets_pool_count or meets_balance_floor
        if not effective_flash_asset and not include_ineffective:
            continue
        row = {
            "token_symbol": symbol or ("ETH" if is_native else address[:8]),
            "binance_symbol": _binance_symbol(symbol),
            "is_stable": _stable_symbol(symbol),
            "depth_score_usd": 0,
            "available_liquidity": balance_units,
            "available_liquidity_raw": str(raw_balance),
            "poolmanager_balance": balance_units,
            "poolmanager_balance_raw": str(raw_balance),
            "reserve_data_liquidity": 0,
            "a_token_total_supply": 0,
            "oracle_price": "",
            "token_address": "" if is_native else address,
            "currency_address": address,
            "pool_address": "",
            "provider_address": pool_manager,
            "pool_manager_address": pool_manager,
            "pool_key_count": len(actual_pool_ids),
            "pool_ids": actual_pool_ids,
            "raw_pool_ids": item.get("pool_ids") or [],
            "pool_identifier": pool_manager,
            "depth_level": "poolmanager_balance",
            "depth_label": "PoolManager balance",
            "depth_class": "ok" if executor_supported else "warn",
            "borrowable": True,
            "lending_borrowable": False,
            "flash_capable": True,
            "flash_borrowable": True,
            "flash_accounting_balance_available": True,
            "effective_flash_accounting_asset": effective_flash_asset,
            "effective_flash_asset": effective_flash_asset,
            "candidate_pool_count": len(actual_pool_ids),
            "min_candidate_pool_count": min_pool_count,
            "min_poolmanager_balance": min_balance_units,
            "meets_candidate_pool_count": meets_pool_count,
            "meets_poolmanager_balance_floor": meets_balance_floor,
            "execution_verified": False,
            "executor_supported": executor_supported,
            "flash_borrowable_reason": "eth_call_simulation_required",
            "borrowable_basis": "poolmanager_balance",
            "pool_role": "v4_poolmanager_flash_accounting_source",
            "source": ";".join(item.get("sources") or []),
        }
        row["effective_flash_asset_reason"] = (
            "candidate_pool_count_and_poolmanager_balance_floor"
            if meets_pool_count and meets_balance_floor
            else ("candidate_pool_count" if meets_pool_count else "poolmanager_balance_floor")
        )
        if is_native:
            row["flash_borrowable_reason"] = "native_currency_requires_executor_support_and_eth_call"
        rows.append(row)
    rows.sort(
        key=lambda row: (
            not bool(row.get("is_stable")),
            str(row.get("token_symbol") or ""),
            -int(row.get("available_liquidity_raw") or 0),
        )
    )
    return rows


__all__ = [
    "ZERO_ADDRESS",
    "_as_int_units",
    "v4_flash_accounting_source_rows",
]
