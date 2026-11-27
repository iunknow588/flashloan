#!/usr/bin/env python3
"""Ask every candidate ETH/USD price source for one price.

The liquidity tick prices the ETH side of a pool, so an unreachable price
feed stalls the tick.  The probe only reads public ticker endpoints; it
changes nothing.  It answers one question: which price sources answer from
this machine right now.

Usage:

    python plan/runtime_evidence/tools/probe_price_sources_20261126.py
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

CANDIDATES = [
    ("binance_us", "https://api.binance.us/api/v3/ticker/price?symbol=ETHUSDT"),
    ("binance_com", "https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT"),
    ("kraken", "https://api.kraken.com/0/public/Ticker?pair=ETHUSD"),
    ("coingecko", "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd"),
    ("coinbase", "https://api.coinbase.com/v2/prices/ETH-USD/spot"),
    ("okx", "https://www.okx.com/api/v5/market/ticker?instId=ETH-USDT"),
    ("gate", "https://api.gateio.ws/api/v4/spot/tickers?currency_pair=ETH_USDT"),
    ("bybit", "https://api.bybit.com/v5/market/tickers?category=spot&symbol=ETHUSDT"),
    ("bitfinex", "https://api.bitfinex.com/v1/pubticker/ethusd"),
    ("mexc", "https://api.mexc.com/api/v3/ticker/price?symbol=ETHUSDT"),
    ("bitstamp", "https://www.bitstamp.net/api/v2/ticker/ethusd/"),
    ("exodus", "https://api.exodus.com/price?pair=ETHUSD"),
]

TIMEOUT = 12


def probe(name: str, url: str) -> dict[str, object]:
    request = urllib.request.Request(
        url, headers={"User-Agent": "flashloan-pool-index/1.0"}
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read(400).decode("utf-8", errors="replace")
            return {
                "source": name,
                "url": url,
                "http": response.status,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "ok": response.status == 200,
                "sample": body[:120],
            }
    except urllib.error.HTTPError as error:
        return {
            "source": name,
            "url": url,
            "http": error.code,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "ok": False,
            "error": str(error.reason)[:120],
        }
    except Exception as error:
        return {
            "source": name,
            "url": url,
            "http": None,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "ok": False,
            "error": f"{type(error).__name__}: {str(error)[:120]}",
        }


def main() -> None:
    with ThreadPoolExecutor(max_workers=len(CANDIDATES)) as pool:
        results = list(pool.map(lambda item: probe(*item), CANDIDATES))
    report = {
        "at": time.strftime("%H:%M:%S"),
        "results": results,
        "alive_count": sum(1 for item in results if item["ok"]),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
