"""Import existing Robinhood V4 on-chain evidence into SQLite.

This is the DB-first bootstrap path: it reads only the V4 pool index and V4
liquidity evidence JSON, seeds chain tokens/markets and writes the raw V4
exchange tables. Candidate pools, market observation and closed loops are
rebuilt from SQLite by :func:`core.db.builders.build_all_anchor_data`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.db.storage_chain_core import (
    seed_chain_markets_from_builtin,
    seed_chain_tokens,
    upsert_initialization,
)
from core.db.storage_v4_exchange import (
    upsert_v4_exchange_pools,
    upsert_v4_liquidity_snapshots,
)
from dex.intent_trade.chains.robinhood_common.chain_config import (
    MAINNET_ANCHOR_ADDRESS_BY_SYMBOL,
    ROBINHOOD_MARKET_ID,
    ROBINHOOD_TESTNET_MARKET_ID,
    ROBINHOOD_TESTNET_NETWORK,
    TESTNET_ANCHOR_ADDRESS_BY_SYMBOL,
    TESTNET_ANCHOR_SYMBOLS,
)


ROBINHOOD_NETWORK = "robinhood"
ROBINHOOD_CHAIN_ID = 4663
ANCHOR_SYMBOLS = tuple(MAINNET_ANCHOR_ADDRESS_BY_SYMBOL)
ROBINHOOD_TESTNET_CHAIN_ID = 46630


def _network_config(network: str) -> dict[str, Any]:
    wanted = _text(network)
    if wanted == ROBINHOOD_TESTNET_NETWORK:
        return {
            "network": ROBINHOOD_TESTNET_NETWORK,
            "chain_id": ROBINHOOD_TESTNET_CHAIN_ID,
            "market_id": ROBINHOOD_TESTNET_MARKET_ID,
            "anchor_symbols": TESTNET_ANCHOR_SYMBOLS,
            "anchor_addresses": TESTNET_ANCHOR_ADDRESS_BY_SYMBOL,
            "stable_symbols": {"USDG", "USDE", "TZ"},
        }
    return {
        "network": ROBINHOOD_NETWORK,
        "chain_id": ROBINHOOD_CHAIN_ID,
        "market_id": ROBINHOOD_MARKET_ID,
        "anchor_symbols": ANCHOR_SYMBOLS,
        "anchor_addresses": MAINNET_ANCHOR_ADDRESS_BY_SYMBOL,
        "stable_symbols": {"USDG", "USDE", "TZ"},
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _number(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _observation_pool_allowed(pool: dict[str, Any]) -> bool:
    """Market observation only stores fixed fee <= 3000 pools with no hook."""
    from core.db.storage_dex_candidate import MAX_USABLE_CANDIDATE_FIXED_FEE

    raw_fee = pool.get("fee")
    try:
        fee = int(raw_fee)
    except (TypeError, ValueError):
        return False
    if fee <= 0 or fee > MAX_USABLE_CANDIDATE_FIXED_FEE or fee & 8388608:
        return False
    return _text(pool.get("hooks")) in {
        "",
        "0x0000000000000000000000000000000000000000",
    }


def _pool_id(pool: dict[str, Any]) -> str:
    return _text(pool.get("pool") or pool.get("poolId") or pool.get("pool_id") or pool.get("id"))


def _token_rows_from_v4_index(
    index: dict[str, Any],
    *,
    anchor_symbols: tuple[str, ...],
    stable_symbols: set[str],
) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for raw in index.get("poolKeys") or index.get("pools") or []:
        if not isinstance(raw, dict):
            continue
        pool = raw.get("poolKey") if isinstance(raw.get("poolKey"), dict) else raw
        for side in (0, 1):
            address = _text(pool.get(f"token{side}") or pool.get(f"currency{side}"))
            symbol = _symbol(pool.get(f"token{side}Symbol"))
            decimals = pool.get(f"token{side}Decimals")
            if not address or not symbol:
                continue
            key = address
            existing = rows.get(key)
            if existing:
                continue
            rows[key] = {
                "token_address": address,
                "token_symbol": symbol,
                "token_decimals": decimals,
                "is_native": 1 if address == "0x0000000000000000000000000000000000000000" else 0,
                "is_wrapped_native": 1 if symbol == "WETH" else 0,
                "is_stable": 1 if symbol in stable_symbols else 0,
                "anchor_symbol": symbol if symbol in anchor_symbols else None,
                "token_kind": "native" if address == "0x0000000000000000000000000000000000000000" else ("wrapped_native" if symbol == "WETH" else "erc20"),
            }
    return list(rows.values())


def _liquidity_rows(v4_liquidity: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for raw in v4_liquidity.get("pools") or []:
        if not isinstance(raw, dict):
            continue
        observed_at = v4_liquidity.get("generatedAt") or v4_liquidity.get("liquidityScanAt")
        has_liquidity = raw.get("has_liquidity")
        if has_liquidity is None:
            has_liquidity = raw.get("hasLiquidity")
        if has_liquidity is None and raw.get("liquidity"):
            has_liquidity = True
        liquidity_index = _number(raw.get("liquidityIndex"))
        if liquidity_index is None and raw.get("liquidity") not in (None, ""):
            try:
                liquidity_index = float(raw.get("liquidity"))
            except (TypeError, ValueError):
                liquidity_index = None
        rows.append(
            {
                "pool_id": _pool_id(raw),
                "has_liquidity": has_liquidity,
                "liquidity": raw.get("liquidity") or "",
                "liquidity_index": liquidity_index,
                "estimated_tvl_usd": _number(
                    raw.get("estimatedTvlUsd")
                    or (raw.get("balances") or {}).get("tvlUsd")
                    if isinstance(raw.get("balances"), dict)
                    else raw.get("estimatedTvlUsd")
                ),
                "balances": raw.get("balances") or {},
                "depth_status": raw.get("depthStatus") or "",
                "depth_band": raw.get("depthBand") or {},
                "sqrt_price_x96": raw.get("sqrtPriceX96") or "",
                "tick": raw.get("tick"),
                "liquidity_read_at": raw.get("liquidityReadAt"),
                "liquidity_block_number": raw.get("liquidityBlockNumber"),
                "recent_swap_active": raw.get("recentSwapActive") or False,
                "observed_at": observed_at,
            }
        )
    return rows


def _v4_pool_rows_from_index(index: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for raw in index.get("pools") or []:
        if not isinstance(raw, dict):
            continue
        key = raw.get("poolKey") if isinstance(raw.get("poolKey"), dict) else raw
        rows.append(
            {
                "pool_id": _pool_id(raw),
                "pool_manager": raw.get("poolManager") or "",
                "pool_key": key or {},
                "token0": _text(raw.get("token0") or raw.get("currency0") or key.get("token0") or key.get("currency0")),
                "token1": _text(raw.get("token1") or raw.get("currency1") or key.get("token1") or key.get("currency1")),
                "fee": raw.get("fee") if raw.get("fee") is not None else key.get("fee"),
                "tick_spacing": (
                    raw.get("tickSpacing")
                    if raw.get("tickSpacing") is not None
                    else key.get("tickSpacing")
                ),
                "hooks": raw.get("hooks") or key.get("hooks") or "",
                "sqrt_price_x96": raw.get("sqrtPriceX96") or key.get("sqrtPriceX96") or "",
                "tick": raw.get("tick") if raw.get("tick") is not None else key.get("tick"),
            }
        )
    return rows


def _anchor_address_from_symbol(
    symbol: str,
    *,
    anchor_addresses: dict[str, str] | None = None,
) -> str:
    """Resolve an anchor from the exact per-network address set."""
    return (anchor_addresses or MAINNET_ANCHOR_ADDRESS_BY_SYMBOL).get(
        _symbol(symbol),
        "",
    )


def _build_loops(
    *,
    anchor_symbol: str,
    anchor_address: str,
    candidates: dict[str, Any],
    observation: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build executable loops only when YC, Y-Z and ZC all have a pool."""
    token_rows = candidates.get("tokens") or []
    token_addresses = [
        _text(row.get("token_address") or row.get("address"))
        for row in token_rows
        if _text(row.get("token_address") or row.get("address"))
    ]
    token_addresses = sorted(set(address for address in token_addresses if address != anchor_address))
    pools_by_edge: dict[tuple[str, str], dict[str, Any]] = {}
    for row in observation.get("pools") or []:
        token0 = _text(row.get("token0"))
        token1 = _text(row.get("token1"))
        if token0 and token1:
            pools_by_edge.setdefault(tuple(sorted((token0, token1))), row)
    loops: list[dict[str, Any]] = []
    hops: list[dict[str, Any]] = []

    def _representative_pool(input_address: str, output_address: str) -> str:
        edge = tuple(sorted((_text(input_address), _text(output_address))))
        row = pools_by_edge.get(edge)
        return _pool_id(row) if row else ""

    for y_address in token_addresses:
        for z_address in token_addresses:
            if y_address == z_address:
                continue
            yc_pool = _representative_pool(anchor_address, y_address)
            yz_pool = _representative_pool(y_address, z_address)
            zc_pool = _representative_pool(z_address, anchor_address)
            if not (yc_pool and yz_pool and zc_pool):
                continue
            path = [anchor_address, y_address, z_address, anchor_address]
            loop_key = f"{anchor_address}:{y_address}:{z_address}"
            loops.append(
                {
                    "loop_key": loop_key,
                    "anchor_address": anchor_address,
                    "anchor_symbol": anchor_symbol,
                    "path_text": " -> ".join(path),
                    "path_addresses": path,
                    "path_symbols": [anchor_symbol, y_address, z_address, anchor_symbol],
                    "pool_path_key": ":".join(path),
                    "pool_ids": [yc_pool, yz_pool, zc_pool],
                    "hop_candidate_counts": {"1": 1, "2": 1, "3": 1},
                    "hop_candidate_combination_count": 1,
                    "recommendation_basis": "imported_existing_json",
                    "recommended_first_priority": 1,
                    "status": "active",
                    "source": "existing_json_import",
                }
            )
            for hop_index, (input_address, output_address, pool_id) in enumerate(
                [
                    (anchor_address, y_address, yc_pool),
                    (y_address, z_address, yz_pool),
                    (z_address, anchor_address, zc_pool),
                ],
                start=1,
            ):
                hops.append(
                    {
                        "loop_key": loop_key,
                        "hop_index": hop_index,
                        "hop_name": "yc" if hop_index == 1 else ("y-z" if hop_index == 2 else "zc"),
                        "input_address": input_address,
                        "output_address": output_address,
                        "pool_id": pool_id,
                        "base_pool_id": pool_id,
                        "pool_selection_basis": "imported_existing_json",
                        "temporary_override": 0,
                    }
                )
    return loops, hops


def _find_pool_for_hop(
    pools_by_id: dict[str, dict[str, Any]],
    input_address: str,
    output_address: str,
) -> str:
    edge = tuple(sorted((_text(input_address), _text(output_address))))
    for pool in pools_by_id.values():
        token0 = _text(pool.get("token0"))
        token1 = _text(pool.get("token1"))
        if tuple(sorted((token0, token1))) == edge:
            return _pool_id(pool)
    return ""


def import_existing_json(
    database_url: str,
    *,
    runtime_root: Path | str | None = None,
    network: str = ROBINHOOD_NETWORK,
) -> dict[str, Any]:
    config = _network_config(network)
    wanted_network = str(config["network"])
    wanted_chain_id = int(config["chain_id"])
    anchor_symbols = tuple(config["anchor_symbols"])
    anchor_addresses = config["anchor_addresses"]
    stable_symbols = config["stable_symbols"]
    root = Path(runtime_root) if runtime_root is not None else Path(__file__).resolve().parents[2] / "runtime"
    v4_index_path = root / "evidence" / wanted_network / "v4_pool_index" / "latest.json"
    v4_liquidity_path = root / "evidence" / wanted_network / "v4_pool_liquidity" / "latest.json"

    index = _read_json(v4_index_path)
    liquidity = _read_json(v4_liquidity_path)
    token_rows = _token_rows_from_v4_index(
        index,
        anchor_symbols=anchor_symbols,
        stable_symbols=stable_symbols,
    )
    seed_chain_tokens(
        database_url,
        network=wanted_network,
        chain_id=wanted_chain_id,
        token_rows=token_rows,
    )
    seed_chain_markets_from_builtin(
        database_url,
        network=wanted_network,
        chain_id=wanted_chain_id,
    )
    v4_pool_count = upsert_v4_exchange_pools(
        database_url,
        network=wanted_network,
        chain_id=wanted_chain_id,
        protocol="uniswap_v4",
        pool_rows=_v4_pool_rows_from_index(index),
    )
    upsert_v4_liquidity_snapshots(
        database_url,
        network=wanted_network,
        chain_id=wanted_chain_id,
        protocol="uniswap_v4",
        rows=_liquidity_rows(liquidity),
    )

    upsert_initialization(
        database_url,
        network=wanted_network,
        chain_id=wanted_chain_id,
        protocol="uniswap_v4",
        domain="v4_exchange_pool_index",
        status="complete",
        completeness={
            "initialize_log_count": index.get("initializeLogCount") or 0,
            "pool_count": v4_pool_count,
        },
    )
    return {
        "ok": True,
        "network": wanted_network,
        "chainId": wanted_chain_id,
        "v4PoolCount": v4_pool_count,
        "tokenCount": len(token_rows),
        "source": "v4_index_and_liquidity_json",
        "detail": "candidate/observation/loop tables are rebuilt by builders.build_all_anchor_data",
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="")
    parser.add_argument("--runtime-root", default="")
    parser.add_argument(
        "--network",
        default=ROBINHOOD_NETWORK,
        choices=(ROBINHOOD_NETWORK, ROBINHOOD_TESTNET_NETWORK),
    )
    args = parser.parse_args()
    database = args.database or str(Path(__file__).resolve().parents[2] / "runtime" / "local_store.sqlite3")
    result = import_existing_json(
        database,
        runtime_root=args.runtime_root or None,
        network=args.network,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
