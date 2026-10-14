"""Read-only verification for Robinhood Testnet ETH/stock pool depth.

The historical "stock pools have no liquidity" conclusion was derived from
Swap logs, which skipped initialized pools without recent swaps and produced
false negatives. This tool re-checks the same pools directly against
PoolManager.extsload and reports both live liquidity and usable depth without
writing any evidence file.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dex.intent_trade.chains.robinhood_testnet import v4_liquidity_metrics as liquidity_metrics  # noqa: E402
from dex.intent_trade.chains.robinhood_testnet import v4_quote_adapter as quote_adapter  # noqa: E402
from dex.intent_trade.chains.robinhood_testnet.v4_pool_liquidity import (  # noqa: E402
    load_robinhood_testnet_v4_pool_liquidity,
)
from dex.intent_trade.chains.robinhood_testnet.v4_pool_rpc import (  # noqa: E402
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
    ROBINHOOD_TESTNET_V4_POOL_MANAGER,
    ZERO_ADDRESS,
    _rpc_url_from_env_or_default,
)


ETH = ZERO_ADDRESS.lower()


def _load_stock_registry(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tokens = payload.get("tokens") or []
    if isinstance(tokens, dict):
        tokens = list(tokens.values())
    return {
        str(token.get("address") or "").lower(): str(token.get("symbol") or "").upper()
        for token in tokens
        if isinstance(token, dict) and token.get("address") and token.get("symbol")
    }


def select_eth_stock_pools(pools: list[dict[str, Any]], stock_symbols: dict[str, str]) -> list[dict[str, Any]]:
    """Return evidence rows where ETH is paired with an official stock token."""
    selected: list[dict[str, Any]] = []
    for pool in pools:
        currency0 = str(pool.get("currency0") or "").lower()
        currency1 = str(pool.get("currency1") or "").lower()
        if currency0 == ETH and currency1 in stock_symbols:
            symbol = stock_symbols[currency1]
        elif currency1 == ETH and currency0 in stock_symbols:
            symbol = stock_symbols[currency0]
        else:
            continue
        row = dict(pool)
        row["stockSymbol"] = symbol
        selected.append(row)
    return selected


def _pool_row_for_metrics(pool: dict[str, Any]) -> dict[str, Any]:
    c0 = str(pool.get("currency0") or "").lower()
    c1 = str(pool.get("currency1") or "").lower()
    metadata0 = pool.get("currency0Metadata") or {}
    metadata1 = pool.get("currency1Metadata") or {}
    return {
        "token0": c0,
        "token1": c1,
        "token0Decimals": metadata0.get("decimals") or 18,
        "token1Decimals": metadata1.get("decimals") or 18,
        "fee": pool.get("fee"),
        "hooks": pool.get("hooks"),
    }


def verify_stock_pool_liquidity(
    *,
    pool_rows: list[dict[str, Any]],
    rpc_url: str,
    w3: Any = None,
    batch_func: Any = None,
    connect_func: Any = None,
) -> dict[str, Any]:
    """Read live PoolManager state and classify every ETH/stock pool.

    ``w3`` may be injected for tests; otherwise the default testnet connection
    is used. ``batch_func`` and ``connect_func`` are also injectable so unit
    tests can avoid the network.
    """
    resolved_w3 = w3
    resolved_rpc_url = rpc_url
    if resolved_w3 is None:
        connector = connect_func or quote_adapter._connect_chain_rpc
        resolved_w3, resolved_rpc_url = connector(ROBINHOOD_TESTNET_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID)
    reader = batch_func or quote_adapter._batch_read_live_pool_states
    states = reader(
        w3=resolved_w3,
        rpc_url=resolved_rpc_url,
        pool_manager=ROBINHOOD_TESTNET_V4_POOL_MANAGER,
        pool_ids=[str(pool.get("poolId") or "") for pool in pool_rows],
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )

    rows: list[dict[str, Any]] = []
    for pool in pool_rows:
        pool_id = str(pool.get("poolId") or "")
        state = states.get(pool_id.lower(), {})
        symbol = str(pool.get("stockSymbol") or "").strip()
        if not symbol:
            c0 = str(pool.get("currency0") or "").lower()
            c1 = str(pool.get("currency1") or "").lower()
            symbol = (
                ((pool.get("currency1Metadata") or {}).get("symbol") if c0 == ETH else (pool.get("currency0Metadata") or {}).get("symbol"))
                or symbol
            )
        metric_pool = _pool_row_for_metrics(pool)
        metrics = liquidity_metrics._liquidity_reserve_metrics(metric_pool, state)
        metric_pool["liquidityIndex"] = metrics.get("liquidityIndex")
        metric_pool["estimatedTvlUsd"] = metrics.get("estimatedTvlUsd")
        depth = liquidity_metrics._liquidity_depth_status(metric_pool)
        rows.append(
            {
                "symbol": symbol,
                "poolId": pool_id,
                "fee": pool.get("fee"),
                "hooks": pool.get("hooks"),
                "evidenceHasLiquidity": pool.get("hasLiquidity"),
                "liveError": state.get("error"),
                "liquidityRaw": state.get("liquidity"),
                "liquidityIndex": metrics.get("liquidityIndex"),
                "token0Reserve": metrics.get("token0Reserve"),
                "token1Reserve": metrics.get("token1Reserve"),
                "token0ReserveUsd": metrics.get("token0ReserveUsd"),
                "estimatedTvlUsd": metrics.get("estimatedTvlUsd"),
                "depthStatus": depth.get("label"),
                "depthReason": depth.get("reason"),
                "sqrtPriceX96": state.get("sqrtPriceX96"),
                "tick": state.get("tick"),
            }
        )

    candidates = [
        row
        for row in rows
        if int(row.get("fee") or 0) <= 3000
        and not int(row.get("fee") or 0) & 8388608
        and str(row.get("hooks") or "").lower() in {"", ETH}
    ]
    live_nonzero = [row for row in candidates if row.get("liquidityRaw")]
    evidence_stale_false_negative = [
        row
        for row in rows
        if row.get("liquidityRaw")
        and row.get("evidenceHasLiquidity") is False
    ]
    usable_depth = [
        row
        for row in candidates
        if _usd_float(row.get("token0ReserveUsd")) >= 1000
    ]
    return {
        "network": ROBINHOOD_TESTNET_NETWORK,
        "chainId": ROBINHOOD_TESTNET_CHAIN_ID,
        "poolManager": ROBINHOOD_TESTNET_V4_POOL_MANAGER,
        "rpcUrl": resolved_rpc_url,
        "poolCount": len(rows),
        "candidatePoolCount": len(candidates),
        "liveNonZeroCandidateCount": len(live_nonzero),
        "usableDepthCandidateCount": len(usable_depth),
        "evidenceStaleFalseNegativeCount": len(evidence_stale_false_negative),
        "pools": rows,
        "usableDepthPools": [
            {
                "symbol": row["symbol"],
                "fee": row["fee"],
                "poolId": row["poolId"],
                "token0Reserve": row["token0Reserve"],
                "token0ReserveUsd": row["token0ReserveUsd"],
                "estimatedTvlUsd": row["estimatedTvlUsd"],
            }
            for row in usable_depth
        ],
        "staleEvidencePools": [
            {
                "symbol": row["symbol"],
                "fee": row["fee"],
                "poolId": row["poolId"],
            }
            for row in evidence_stale_false_negative
        ],
    }


def _usd_float(value: object) -> float:
    if value in (None, "", "None"):
        return 0.0
    try:
        return float(Decimal(str(value)))
    except (TypeError, ValueError):
        return 0.0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only live verification of Robinhood Testnet ETH/stock pool depth."
    )
    parser.add_argument("--rpc-url", default="", help="Optional RPC URL override.")
    parser.add_argument(
        "--evidence",
        default="",
        help="Optional v4_pool_liquidity latest.json path.",
    )
    args = parser.parse_args()

    evidence_path = Path(args.evidence) if args.evidence else None
    evidence = load_robinhood_testnet_v4_pool_liquidity(evidence_path)
    pools = evidence.get("pools") or []
    stock_path = SRC_ROOT / "chains" / "robinhood-testnet" / "robinhood_stock_tokens.json"
    stock_symbols = _load_stock_registry(stock_path)
    selected = select_eth_stock_pools(pools, stock_symbols)
    if not selected:
        print(json.dumps({"error": "no ETH/stock pools found in evidence"}, ensure_ascii=False, indent=2))
        return 2

    result = verify_stock_pool_liquidity(
        pool_rows=selected,
        rpc_url=args.rpc_url or _rpc_url_from_env_or_default(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["evidenceStaleFalseNegativeCount"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
