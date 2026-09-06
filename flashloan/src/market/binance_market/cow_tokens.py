from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.db.storage_cow_tokens import load_cow_supported_tokens, replace_cow_supported_tokens
from dex.cow_flashloan.routes import CowToken, cow_network_config, load_cow_token_list, resolve_token
from dex.cow_flashloan.network_scope import DEFAULT_COW_NETWORK


SRC_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COW_TEST_NETWORK = DEFAULT_COW_NETWORK
DEFAULT_COW_TOKEN_CACHE_PATH = SRC_ROOT / "runtime" / "cache" / "cow_supported_tokens.json"
WRAPPED_SYMBOL_ALIASES = {
    "ETH": ("WETH",),
    "AVAX": ("WAVAX", "WAVAX.E"),
    "BTC": ("BTC.B", "BTCB"),
    "BTC.B": ("BTC",),
    "WBTC": ("WBTC.E",),
    "WBTC.E": ("WBTC",),
    "LINK": ("LINK.E",),
    "AAVE": ("AAVE.E",),
    "MATIC": ("WPOL", "WMATIC", "POL"),
    "POL": ("WPOL", "WMATIC", "MATIC"),
    "BNB": ("WBNB",),
    "XDAI": ("WXDAI", "WDAI"),
}

_COW_TOKEN_CACHE_LOCK = threading.Lock()
_COW_TOKEN_MEMORY_CACHE: dict[str, dict[str, Any]] = {}


def _cow_symbol_candidates(symbol: str) -> tuple[str, ...]:
    value = str(symbol or "").strip().upper()
    aliases = WRAPPED_SYMBOL_ALIASES.get(value, ())
    return (value, *aliases)


def _resolve_cow_market_symbol(symbol: str, registry: dict[str, Any]) -> dict[str, Any] | None:
    for candidate in _cow_symbol_candidates(symbol):
        try:
            token = resolve_token(candidate, registry)
            return {
                "symbol": token.symbol,
                "address": token.address,
                "decimals": token.decimals,
                "source": token.source,
                "matched_from": symbol,
            }
        except Exception:
            continue
    return None


def _token_to_dict(token: CowToken | dict[str, Any]) -> dict[str, Any]:
    if isinstance(token, CowToken) or all(hasattr(token, field) for field in ("symbol", "address", "decimals", "source")):
        return {
            "symbol": token.symbol,
            "address": token.address,
            "decimals": token.decimals,
            "source": token.source,
        }
    return {
        "symbol": str(token.get("symbol") or "").upper(),
        "address": str(token.get("address") or "").lower(),
        "decimals": int(token.get("decimals") or 0),
        "source": str(token.get("source") or ""),
    }


def _tokens_to_registry(tokens: list[dict[str, Any]]) -> dict[str, CowToken]:
    by_address: dict[str, CowToken] = {}
    for item in tokens:
        try:
            token = CowToken(
                str(item["symbol"]).upper(),
                str(item["address"]).lower(),
                int(item["decimals"]),
                str(item.get("source") or "cow_supported_cache"),
            )
        except (KeyError, TypeError, ValueError):
            continue
        by_address.setdefault(token.address, token)
    symbol_counts: dict[str, int] = {}
    for token in by_address.values():
        symbol_counts[token.symbol.upper()] = symbol_counts.get(token.symbol.upper(), 0) + 1
    registry = dict(by_address)
    for token in by_address.values():
        if symbol_counts.get(token.symbol.upper()) == 1:
            registry[token.symbol.upper()] = token
    return registry


def _bnb_static_market_tokens() -> list[dict[str, Any]]:
    """Return only the exact token identities included in the BNB static table."""
    from dex.intent_trade.chains.bnb import config as bnb

    return [
        {
            "symbol": token.symbol,
            "address": token.address,
            "decimals": token.decimals,
            "source": "bnb_static_pool_table",
        }
        for token in bnb.TOKENS.values()
    ]


def _bnb_static_market_registry() -> dict[str, CowToken]:
    """Use the BNB static-table token identities for local DEX route assembly."""
    return _tokens_to_registry(_bnb_static_market_tokens())


def _market_registry_for_network(network: str, registry: dict[str, Any] | None) -> dict[str, Any]:
    if registry:
        return registry
    if str(network or "").lower() == "bnb":
        return _bnb_static_market_registry()
    return {}


def _dedupe_tokens_by_address(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped = []
    seen: set[str] = set()
    for token in tokens:
        address = str(token.get("address") or "").strip().lower()
        if not address or address in seen:
            continue
        copied = dict(token)
        copied["address"] = address
        deduped.append(copied)
        seen.add(address)
    return deduped


def _read_cow_token_file(cache_path: Path, network: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    networks = payload.get("networks") if isinstance(payload, dict) else {}
    item = networks.get(network) if isinstance(networks, dict) else None
    tokens = item.get("tokens") if isinstance(item, dict) else None
    return tokens if isinstance(tokens, list) else []


def _write_cow_token_file(cache_path: Path, *, network: str, chain_id: int, tokens: list[dict[str, Any]]) -> None:
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        payload = {}
    networks = payload.setdefault("networks", {})
    networks[network] = {
        "network": network,
        "chain_id": int(chain_id),
        "refreshed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "token_count": len(tokens),
        "tokens": tokens,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


def _memory_cow_tokens(network: str) -> list[dict[str, Any]]:
    with _COW_TOKEN_CACHE_LOCK:
        item = _COW_TOKEN_MEMORY_CACHE.get(network)
        if not item:
            return []
        return list(item.get("tokens") or [])


def _store_memory_cow_tokens(network: str, tokens: list[dict[str, Any]], *, source: str) -> None:
    with _COW_TOKEN_CACHE_LOCK:
        _COW_TOKEN_MEMORY_CACHE[network] = {
            "tokens": list(tokens),
            "source": source,
            "loaded_at": time.time(),
        }


def _load_cow_token_rows(network: str) -> list[dict[str, Any]]:
    try:
        from market.binance_market import service as service_module

        loader = getattr(service_module, "load_cow_token_list", load_cow_token_list)
    except Exception:
        loader = load_cow_token_list
    return loader(network=network)


def refresh_cow_supported_token_cache(
    *,
    cow_network: str | None = DEFAULT_COW_TEST_NETWORK,
    database_url: str | None = None,
    cache_path: Path = DEFAULT_COW_TOKEN_CACHE_PATH,
) -> dict[str, Any]:
    network_config = cow_network_config(network=cow_network)
    if network_config.network == "bnb":
        tokens = _bnb_static_market_tokens()
        _write_cow_token_file(cache_path, network=network_config.network, chain_id=network_config.chain_id, tokens=tokens)
        _store_memory_cow_tokens(network_config.network, tokens, source="bnb_static_pool_table")
        return {
            "network": network_config.network,
            "chain_id": network_config.chain_id,
            "token_count": len(tokens),
            "database_saved": False,
            "file_cache": str(cache_path),
            "source": "bnb_static_pool_table",
        }
    tokens = _dedupe_tokens_by_address([_token_to_dict(token) for token in _load_cow_token_rows(network_config.network)])
    if database_url:
        replace_cow_supported_tokens(
            database_url,
            network=network_config.network,
            chain_id=network_config.chain_id,
            tokens=tokens,
        )
    _write_cow_token_file(cache_path, network=network_config.network, chain_id=network_config.chain_id, tokens=tokens)
    _store_memory_cow_tokens(network_config.network, tokens, source="refresh")
    return {
        "network": network_config.network,
        "chain_id": network_config.chain_id,
        "token_count": len(tokens),
        "database_saved": bool(database_url),
        "file_cache": str(cache_path),
        "source": "cow_token_list",
    }


def load_cow_supported_token_registry(
    *,
    cow_network: str | None = DEFAULT_COW_TEST_NETWORK,
    database_url: str | None = None,
    cache_path: Path = DEFAULT_COW_TOKEN_CACHE_PATH,
    allow_live_fallback: bool = True,
) -> dict[str, Any]:
    network_config = cow_network_config(network=cow_network)
    if network_config.network == "bnb":
        tokens = _bnb_static_market_tokens()
        return {
            "network": network_config.network,
            "chain_id": network_config.chain_id,
            "source": "bnb_static_pool_table",
            "token_count": len(tokens),
            "tokens": tokens,
            "registry": _bnb_static_market_registry(),
        }
    source = "memory"
    tokens = _memory_cow_tokens(network_config.network)
    if not tokens and database_url:
        try:
            tokens = load_cow_supported_tokens(database_url, network=network_config.network)
            source = "database"
        except Exception:
            tokens = []
    if not tokens:
        tokens = _read_cow_token_file(cache_path, network_config.network)
        source = "file"
    if not tokens and allow_live_fallback:
        refreshed = refresh_cow_supported_token_cache(
            cow_network=network_config.network,
            database_url=database_url,
            cache_path=cache_path,
        )
        tokens = _memory_cow_tokens(network_config.network)
        source = f"live_refresh:{refreshed['source']}"
    if tokens:
        _store_memory_cow_tokens(network_config.network, tokens, source=source)
    return {
        "network": network_config.network,
        "chain_id": network_config.chain_id,
        "source": source if tokens else "empty",
        "token_count": len(tokens),
        "tokens": tokens,
        "registry": _tokens_to_registry(tokens),
    }
