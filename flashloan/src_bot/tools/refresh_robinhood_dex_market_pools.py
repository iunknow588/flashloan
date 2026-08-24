"""Refresh Robinhood DEX market pool cache from read-only market APIs."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
import sys
import re

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from intent_trade.chains.robinhood import config as robinhood


DEXSCREENER_BASE = "https://api.dexscreener.com"
DEFAULT_SEARCH_QUERIES = (
    "robinhood weth",
    "robinhood usdg",
)
DEFAULT_TOKEN_ADDRESSES = (
    robinhood.WETH_ADDRESS,
    "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168",
)
SYMBOL_RE = re.compile(r"^[A-Z0-9.$_\-\u4e00-\u9fff()]+$")
MAX_SYMBOL_LENGTH = 24


def _fetch_json(url: str, *, timeout: float = 30) -> object:
    request = Request(url, headers={"accept": "application/json", "user-agent": "flashloan-robinhood-dex-market/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _pairs_from_payload(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        pairs = payload.get("pairs")
        if isinstance(pairs, list):
            return [row for row in pairs if isinstance(row, dict)]
    return []


def _number(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else None


def _symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _symbol_reject_reason(symbol: str) -> str | None:
    if not symbol:
        return "token_symbol_missing"
    if len(symbol) > MAX_SYMBOL_LENGTH:
        return "token_symbol_too_long"
    if not SYMBOL_RE.fullmatch(symbol):
        return "token_symbol_contains_unsupported_chars"
    return None


def _protocol_from_pair(pair: dict) -> str:
    labels = {str(item or "").strip().lower() for item in pair.get("labels") or []}
    dex_id = str(pair.get("dexId") or "").strip().lower()
    if "v2" in labels:
        return "uniswap_v2" if dex_id == "uniswap" else f"{dex_id}_v2"
    if "v4" in labels:
        return "uniswap_v4" if dex_id == "uniswap" else f"{dex_id}_v4"
    if "v3" in labels or dex_id == "uniswap":
        return "uniswap_v3"
    return dex_id or "unknown_dex"


def _fee_from_pair(pair: dict) -> str:
    labels = [str(item or "").strip().lower() for item in pair.get("labels") or []]
    return ",".join(item for item in labels if item) or ""


def _pool_row(pair: dict) -> dict:
    base = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
    quote_token = pair.get("quoteToken") if isinstance(pair.get("quoteToken"), dict) else {}
    liquidity = pair.get("liquidity") if isinstance(pair.get("liquidity"), dict) else {}
    volume = pair.get("volume") if isinstance(pair.get("volume"), dict) else {}
    tvl_usd = _number(liquidity.get("usd"))
    volume_h24 = _number(volume.get("h24"))
    protocol = _protocol_from_pair(pair)
    base_symbol = _symbol(base.get("symbol"))
    quote_symbol = _symbol(quote_token.get("symbol"))
    reject_reasons = [
        reason
        for reason in (_symbol_reject_reason(base_symbol), _symbol_reject_reason(quote_symbol))
        if reason
    ]
    if (tvl_usd or 0) < 5_000:
        reject_reasons.append("insufficient_liquidity_usd")
    if (volume_h24 or 0) <= 0:
        reject_reasons.append("missing_24h_volume")
    quality = "pass" if not reject_reasons else "rejected"
    return {
        "network": "robinhood",
        "chainId": robinhood.CHAIN_ID,
        "dex": str(pair.get("dexId") or "unknown").strip().lower(),
        "protocol": protocol,
        "pool": pair.get("pairAddress"),
        "url": pair.get("url"),
        "token0": base.get("address"),
        "token1": quote_token.get("address"),
        "token0Symbol": base_symbol,
        "token1Symbol": quote_symbol,
        "symbolPair": f"{base_symbol}:{quote_symbol}" if base_symbol and quote_symbol else "",
        "fee": _fee_from_pair(pair),
        "quality": quality,
        "status": "observable" if quality == "pass" else "rejected",
        "rejectReasons": reject_reasons,
        "tvlUsd": tvl_usd,
        "token0Amount": liquidity.get("base"),
        "token1Amount": liquidity.get("quote"),
        "volumeH24Usd": volume_h24,
        "market": {
            "source": "dexscreener",
            "pairCreatedAt": pair.get("pairCreatedAt"),
            "volumeH24Usd": volume_h24,
            "txns": pair.get("txns"),
            "priceUsd": pair.get("priceUsd"),
            "priceNative": pair.get("priceNative"),
        },
        "evidence": {
            "sourceUrl": pair.get("url"),
            "marketApi": "dexscreener",
            "liquidityUsd": tvl_usd,
            "volumeH24Usd": volume_h24,
        },
    }


def _collect_pairs(search_queries: tuple[str, ...], token_addresses: tuple[str, ...]) -> tuple[list[dict], list[str]]:
    seen: set[str] = set()
    rows: list[dict] = []
    sources: list[str] = []
    for token in token_addresses:
        url = f"{DEXSCREENER_BASE}/token-pairs/v1/robinhood/{token}"
        sources.append(url)
        for pair in _pairs_from_payload(_fetch_json(url)):
            if str(pair.get("chainId") or "").lower() != "robinhood":
                continue
            key = str(pair.get("pairAddress") or "").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append(_pool_row(pair))
    for query in search_queries:
        url = f"{DEXSCREENER_BASE}/latest/dex/search?q={quote(query)}"
        sources.append(url)
        for pair in _pairs_from_payload(_fetch_json(url)):
            if str(pair.get("chainId") or "").lower() != "robinhood":
                continue
            key = str(pair.get("pairAddress") or "").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append(_pool_row(pair))
    rows.sort(key=lambda row: (-(row.get("tvlUsd") or 0), -(row.get("volumeH24Usd") or 0), str(row.get("symbolPair") or "")))
    return rows, sources


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=SRC_ROOT / "runtime" / "cache" / "robinhood_dex_market_pools.json")
    parser.add_argument("--search-query", action="append", default=[])
    parser.add_argument("--token", action="append", default=[])
    args = parser.parse_args()

    search_queries = tuple(args.search_query or DEFAULT_SEARCH_QUERIES)
    token_addresses = tuple(args.token or DEFAULT_TOKEN_ADDRESSES)
    rows, sources = _collect_pairs(search_queries, token_addresses)
    payload = {
        "schemaVersion": "robinhood-dex-market-cache-v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "network": "robinhood",
        "chainId": robinhood.CHAIN_ID,
        "source": "dexscreener",
        "sources": sources,
        "pools": rows,
        "summary": {
            "poolCount": len(rows),
            "pairCount": len({row.get("symbolPair") for row in rows if row.get("symbolPair")}),
            "liquidPoolCount": sum(1 for row in rows if (row.get("tvlUsd") or 0) >= 5_000),
            "volumePoolCount": sum(1 for row in rows if (row.get("volumeH24Usd") or 0) > 0),
        },
    }
    args.cache.parent.mkdir(parents=True, exist_ok=True)
    args.cache.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "cache": str(args.cache),
                "poolCount": payload["summary"]["poolCount"],
                "pairCount": payload["summary"]["pairCount"],
                "liquidPoolCount": payload["summary"]["liquidPoolCount"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
