"""Benchmark the SQLite-first V4 rebuild and mainnet quote API latency.

The cold-import mode seeds a minimal testnet anchor pair in a fresh SQLite
database, then runs the same ``refresh_v4_live(..., skip_index=True,
skip_liquidity=True)`` path used for a pure SQLite rebuild.  The API mode
measures P95 request latency for ``/api/dex/quote-routes`` on the Robinhood
testnet V4 scope with live quoting disabled.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
APP_RUNTIME = SRC_ROOT.parent / "app_runtime"
if str(APP_RUNTIME) not in sys.path:
    sys.path.insert(0, str(APP_RUNTIME))


def _seed_testnet_anchor_pair(database: str) -> None:
    from core.db.storage_chain_core import seed_chain_tokens
    from core.db.storage_schema import ensure_database_schema
    from core.db.storage_v4_exchange import (
        upsert_v4_exchange_pools,
        upsert_v4_liquidity_snapshots,
    )
    from dex.intent_trade.chains.robinhood_common.chain_config import (
        ROBINHOOD_TESTNET_CHAIN_ID,
        ROBINHOOD_TESTNET_NETWORK,
        TESTNET_ANCHOR_ADDRESS_BY_SYMBOL,
    )

    ensure_database_schema(database)
    zero = "0x0000000000000000000000000000000000000000"
    tz = TESTNET_ANCHOR_ADDRESS_BY_SYMBOL["TZ"]
    usde = TESTNET_ANCHOR_ADDRESS_BY_SYMBOL["USDE"]
    seed_chain_tokens(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        token_rows=[
            {
                "token_address": tz,
                "token_symbol": "TZ",
                "token_decimals": 18,
                "token_kind": "erc20",
                "anchor_symbol": "TZ",
            },
            {
                "token_address": usde,
                "token_symbol": "USDE",
                "token_decimals": 18,
                "token_kind": "erc20",
                "anchor_symbol": "USDE",
            },
        ],
    )
    upsert_v4_exchange_pools(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        pool_rows=[
            {
                "pool_id": "pool-tz-usde",
                "pool_manager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
                "pool_key": {"currency0": tz, "currency1": usde, "fee": 3000, "tickSpacing": 60, "hooks": zero},
                "token0": tz,
                "token1": usde,
                "fee": 3000,
                "tick_spacing": 60,
                "hooks": zero,
            }
        ],
    )
    upsert_v4_liquidity_snapshots(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        rows=[
            {
                "pool_id": "pool-tz-usde",
                "has_liquidity": True,
                "liquidity_index": 1000.0,
                "estimated_tvl_usd": 1_000_000.0,
                "sqrt_price_x96": str(2**96),
                "recent_swap_active": True,
            }
        ],
    )


def benchmark_cold_import() -> dict:
    os.environ["DEX_V4_LIVE_QUOTE_ENABLED"] = "0"
    from dex.tools import refresh_robinhood_v4_live as refresh_module

    with tempfile.TemporaryDirectory(prefix="v4-cold-import-", ignore_cleanup_errors=True) as tmp:
        database = str(Path(tmp) / "local_store.sqlite3")
        _seed_testnet_anchor_pair(database)
        rpc_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        original_connect_rpc = refresh_module._connect_rpc

        def _counting_connect_rpc(*args: object, **kwargs: object) -> tuple[object, str]:
            rpc_calls.append((args, kwargs))
            return original_connect_rpc(*args, **kwargs)

        refresh_module._connect_rpc = _counting_connect_rpc
        started = time.perf_counter()
        try:
            result = refresh_module.refresh_v4_live(
                database=database,
                network="robinhood-testnet",
                chain_id=46630,
                skip_index=True,
                skip_liquidity=True,
                anchors="TZ,USDE",
                prune_zero_liquidity=False,
                remove_json_evidence=False,
                clear_cache=False,
            )
        finally:
            refresh_module._connect_rpc = original_connect_rpc
        elapsed = time.perf_counter() - started
    if not result.get("ok"):
        raise RuntimeError(f"cold import failed: {result.get('errors')}")
    return {
        "coldImportSeconds": round(elapsed, 4),
        "ok": bool(result.get("ok")),
        "anchorCount": int((result.get("build") or {}).get("anchorCount") or 0),
        "rpcCallCount": len(rpc_calls),
    }


def benchmark_build_only() -> dict:
    from core.db import builders
    from dex.intent_trade.chains.robinhood_common.chain_config import (
        ROBINHOOD_TESTNET_CHAIN_ID,
        ROBINHOOD_TESTNET_NETWORK,
        ROBINHOOD_TESTNET_MARKET_ID,
    )

    with tempfile.TemporaryDirectory(prefix="v4-build-only-", ignore_cleanup_errors=True) as tmp:
        database = str(Path(tmp) / "local_store.sqlite3")
        _seed_testnet_anchor_pair(database)
        started = time.perf_counter()
        result = builders.build_all_anchor_data(
            database,
            network=ROBINHOOD_TESTNET_NETWORK,
            chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
            market_id=ROBINHOOD_TESTNET_MARKET_ID,
            anchors=("TZ", "USDE"),
        )
        elapsed = time.perf_counter() - started
    if not result.get("ok"):
        raise RuntimeError("build_all_anchor_data failed")
    return {
        "buildSeconds": round(elapsed, 4),
        "ok": bool(result.get("ok")),
        "anchorCount": len(result.get("anchors") or []),
    }


def _p95(times: list[float]) -> float:
    if not times:
        return 0.0
    ordered = sorted(times)
    index = max(0, min(len(ordered) - 1, int(0.95 * len(ordered))))
    return ordered[index]


def benchmark_api_p95(*, requests: int, rounds: int) -> dict:
    os.environ["DEX_V4_LIVE_QUOTE_ENABLED"] = "0"
    from web.control_panel import app
    from market.binance_market import dex_route_test as route_test_module
    from market.binance_market import dex_surface_route_quotes as route_quotes_module
    from dex.runtime import dex_quote_evidence_rpc as evidence_rpc_module

    rpc_calls: list[str] = []
    originals = {
        route_test_module: {
            "_probe_robinhood_family_rpc": route_test_module._probe_robinhood_family_rpc,
        },
        route_quotes_module: {
            "load_v4_sdk_adapter": route_quotes_module.load_v4_sdk_adapter,
            "quote_v4_exact_input_single": route_quotes_module.quote_v4_exact_input_single,
        },
        evidence_rpc_module: {
            "_connect_robinhood_family": evidence_rpc_module._connect_robinhood_family,
            "_probe_robinhood_testnet_rpc_fast": evidence_rpc_module._probe_robinhood_testnet_rpc_fast,
        },
    }
    for module, names in originals.items():
        for name, original in names.items():
            label = f"{module.__name__}.{name}"

            def _counting_wrapper(
                *args: object,
                _original: object = original,
                _label: str = label,
                **kwargs: object,
            ) -> object:
                rpc_calls.append(_label)
                return _original(*args, **kwargs)

            setattr(module, name, _counting_wrapper)

    try:
        client = app.test_client()
        url = (
            "/api/dex/quote-routes?"
            "market_id=dex-arbitrage-46630-uniswap-v4&"
            "chain_id=46630&protocols=uniswap_v4"
        )
        summaries = []
        for round_index in range(1, int(rounds) + 1):
            timings: list[float] = []
            for _ in range(max(1, int(requests))):
                started = time.perf_counter()
                response = client.get(url)
                elapsed = time.perf_counter() - started
                if response.status_code != 200:
                    raise RuntimeError(f"quote route API returned {response.status_code}")
                timings.append(elapsed)
            summaries.append(
                {
                    "round": round_index,
                    "requests": len(timings),
                    "p95Seconds": round(_p95(timings), 4),
                    "meanSeconds": round(statistics.mean(timings), 4),
                    "minSeconds": round(min(timings), 4),
                    "maxSeconds": round(max(timings), 4),
                }
            )
        return {"rounds": summaries, "rpcCallCount": len(rpc_calls)}
    finally:
        for module, names in originals.items():
            for name, original in names.items():
                setattr(module, name, original)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cold-import", action="store_true")
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--api-p95", action="store_true")
    parser.add_argument("--requests", type=int, default=5)
    parser.add_argument("--rounds", type=int, default=2)
    args = parser.parse_args()

    import json

    if args.cold_import:
        print(json.dumps(benchmark_cold_import(), ensure_ascii=False, indent=2))
    if args.build_only:
        print(json.dumps(benchmark_build_only(), ensure_ascii=False, indent=2))
    if args.api_p95:
        print(json.dumps(benchmark_api_p95(requests=args.requests, rounds=args.rounds), ensure_ascii=False, indent=2))
    if not args.cold_import and not args.build_only and not args.api_p95:
        parser.error("choose --cold-import, --build-only and/or --api-p95")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
