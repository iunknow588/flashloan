from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_file_path
from dex.intent_trade.chains.robinhood_testnet.v4_pool_rpc import (
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
    TOKEN_BALANCE_SCAN_CACHE_SCHEMA,
    ZERO_ADDRESS,
    _as_list,
    _read_json,
    _stamp,
)


def _workspace_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "contract").exists() and (parent / "flashloan").exists():
            return parent
    return current.parents[5]


def _resolve_workspace_path(value: object, *, base: Path | None = None) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    if path.is_absolute():
        return path
    return (base or _workspace_root()) / path


def default_contract_metadata_path() -> Path:
    return _workspace_root() / "contract" / "robinhood-testnet" / "DEX" / "config" / "contracts.json"


def _preflight_report_candidates(base: Path | None = None) -> list[Path]:
    evidence_root = (base or _workspace_root()) / "contract" / "robinhood-testnet" / "DEX" / "deployments" / "evidence"
    if not evidence_root.exists():
        return []
    return sorted(evidence_root.glob("*robinhoodTestnet-robinhood-testnet-preflight/report.json"))


def _flash_pool_ids(report: dict[str, Any]) -> list[str]:
    flash = report.get("flashAccountingLiquidity") if isinstance(report.get("flashAccountingLiquidity"), dict) else {}
    return [
        str(pool_id)
        for pool_id in flash.get("activePoolIds") or []
        if isinstance(pool_id, str) and pool_id.startswith("0x") and len(pool_id) == 66
    ]


def _report_chain_id(report: dict[str, Any]) -> int:
    rpc = report.get("rpc") if isinstance(report.get("rpc"), dict) else {}
    return int(rpc.get("chainId") or report.get("chainId") or report.get("expectedChainId") or 0)


def _balance_raw(metadata: dict[str, Any]) -> int:
    try:
        return int(str(metadata.get("poolManagerBalanceRaw") or "0"))
    except (TypeError, ValueError):
        return 0


def _flash_accounting_sources(
    pool_keys: list[dict[str, Any]],
    *,
    native_balance_wei: object,
) -> list[dict[str, Any]]:
    by_address: dict[str, dict[str, Any]] = {}
    try:
        native_raw = int(str(native_balance_wei or "0"))
    except (TypeError, ValueError):
        native_raw = 0
    if native_raw > 0:
        by_address[ZERO_ADDRESS] = {
            "address": ZERO_ADDRESS,
            "symbol": "ETH",
            "decimals": 18,
            "poolManagerBalanceRaw": str(native_raw),
            "poolManagerBalance": native_raw / 10**18,
            "poolIds": [],
            "source": "nativePoolManagerBalanceWei",
        }
    for pool_key in pool_keys:
        pool_id = str(pool_key.get("poolId") or "").strip()
        for currency_key, metadata_key in (("currency0", "currency0Metadata"), ("currency1", "currency1Metadata")):
            metadata = pool_key.get(metadata_key) if isinstance(pool_key.get(metadata_key), dict) else {}
            address = str(pool_key.get(currency_key) or metadata.get("address") or "").strip()
            if not address:
                continue
            raw = _balance_raw(metadata)
            if raw <= 0:
                continue
            current = by_address.setdefault(
                address.lower(),
                {
                    "address": address,
                    "symbol": str(metadata.get("symbol") or address[:8]).strip(),
                    "decimals": metadata.get("decimals"),
                    "poolManagerBalanceRaw": str(raw),
                    "poolManagerBalance": metadata.get("poolManagerBalance"),
                    "poolIds": [],
                    "source": "decoded_pool_key_metadata",
                },
            )
            if raw > _balance_raw(current):
                current["poolManagerBalanceRaw"] = str(raw)
                current["poolManagerBalance"] = metadata.get("poolManagerBalance")
                current["symbol"] = str(metadata.get("symbol") or current.get("symbol") or address[:8]).strip()
                current["decimals"] = metadata.get("decimals", current.get("decimals"))
            if pool_id and pool_id not in current["poolIds"]:
                current["poolIds"].append(pool_id)
    return sorted(
        by_address.values(),
        key=lambda item: (
            str(item.get("symbol") or ""),
            str(item.get("address") or ""),
        ),
    )


def _pool_key_currency_address(pool_key: dict[str, Any], currency_key: str) -> str:
    token_key = "token0" if currency_key == "currency0" else "token1"
    metadata_key = "currency0Metadata" if currency_key == "currency0" else "currency1Metadata"
    metadata = pool_key.get(metadata_key) if isinstance(pool_key.get(metadata_key), dict) else {}
    return str(pool_key.get(currency_key) or pool_key.get(token_key) or metadata.get("address") or "").strip()


def _index_pool_keys(active_pool_ids: list[str] | None = None) -> tuple[list[dict[str, Any]], str | None]:
    try:
        from dex.intent_trade.chains.robinhood_testnet.v4_pool_index import load_robinhood_testnet_v4_pool_index

        index = load_robinhood_testnet_v4_pool_index()
    except Exception:
        return [], None
    if not index.get("ok"):
        return [], index.get("source")
    active = {str(pool_id or "").lower() for pool_id in active_pool_ids or []}
    rows = []
    for pool in _as_list(index.get("pools")):
        pool_id = str(pool.get("poolId") or pool.get("pool_id") or "").strip()
        if active and pool_id.lower() not in active:
            continue
        pool_key = pool.get("poolKey") if isinstance(pool.get("poolKey"), dict) else pool
        row = {**pool_key, "poolId": pool_id}
        for extra_key in (
            "recentSwapActive",
            "status",
            "latestSwapAmount0",
            "latestSwapAmount1",
            "latestSwapBlockNumber",
            "latestSwapFee",
            "latestSwapLiquidity",
            "latestSwapSqrtPriceX96",
            "latestSwapTick",
            "latestSwapTransactionHash",
        ):
            if pool.get(extra_key) is not None:
                row[extra_key] = pool[extra_key]
        rows.append(row)
    return rows, index.get("source")


def _token_balance_scan_cache_path() -> Path | None:
    latest_path = chain_data_file_path(ROBINHOOD_TESTNET_NETWORK, "v4_pool_liquidity")
    if latest_path is None:
        return None
    return latest_path.parent / "token_balance_scan_cache.json"


def _load_token_balance_scan_cache(*, pool_manager: str) -> dict[str, Any]:
    path = _token_balance_scan_cache_path()
    if path is None:
        return {"entries": {}}
    payload = _read_json(path)
    if not payload or payload.get("_error"):
        return {"path": str(path), "entries": {}}
    if str(payload.get("schemaVersion") or "") != TOKEN_BALANCE_SCAN_CACHE_SCHEMA:
        return {"path": str(path), "entries": {}}
    if str(payload.get("poolManager") or "").lower() != str(pool_manager or "").lower():
        return {"path": str(path), "entries": {}}
    entries = payload.get("entries") if isinstance(payload.get("entries"), dict) else {}
    return {**payload, "path": str(path), "entries": entries}


def _write_token_balance_scan_cache(
    *,
    pool_manager: str,
    pool_index_source: str | None,
    entries: dict[str, dict[str, Any]],
    scan_scope: str,
) -> str | None:
    path = _token_balance_scan_cache_path()
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": TOKEN_BALANCE_SCAN_CACHE_SCHEMA,
        "network": ROBINHOOD_TESTNET_NETWORK,
        "chainId": ROBINHOOD_TESTNET_CHAIN_ID,
        "poolManager": pool_manager,
        "poolIndexSource": pool_index_source,
        "lastScanScope": scan_scope,
        "refreshedAt": _stamp(),
        "entryCount": len(entries),
        "entries": entries,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)
