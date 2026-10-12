"""Build Robinhood mainnet candidate evidence from RobinScreener anchors.

RobinScreener is used as the discovery reference for the five mainnet anchors
(ETH, WETH, USDG, USDE and the custom TZ/TA/TB set). The full Initialize-log
index remains the chain evidence source: this tool streams that index once,
keeps only pools referenced by the screener anchor lists or by the custom
TZ/TA/TB addresses, and overlays screener symbols/liquidity on top of the
on-chain PoolKey rows.

The output is intentionally a compact candidate surface, not a replacement
for the full historical index. The full index is kept beside it as
``full_latest.json`` when present.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

try:
    import ijson
except ImportError:  # pragma: no cover
    ijson = None


SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.chain_data_registry import chain_data_file_path  # noqa: E402
from core.env_loader import load_chain_env_files  # noqa: E402
from dex.intent_trade.chains.robinhood.dex_pool_candidates import (  # noqa: E402
    DEFAULT_ROBINHOOD_DEX_CANDIDATE_PATH,
    build_mainnet_static_candidate_payload,
)
from dex.intent_trade.chains.robinhood.v4_pool_index import default_v4_pool_index_path  # noqa: E402


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
ANCHOR_ADDRESSES = {
    ZERO_ADDRESS.lower(),
    "0x0bd7d308f8e1639fab988df18a8011f41eacad73".lower(),
    "0x5fc5360d0400a0fd4f2af552add042d716f1d168".lower(),
    "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34".lower(),
    "0xa8cf7c51eee671646ba440ffa180f970902fd2fb".lower(),
}
CUSTOM_ADDRESSES = {
    "0xa8cf7c51eee671646ba440ffa180f970902fd2fb".lower(),
    "0xd1b532ea8dbda84e6937fedabb2fd5c33a049830".lower(),
    "0x02a0bacc642d501422af804a462f98335b2bbf09".lower(),
}
CUSTOM_SYMBOLS = {
    "0xa8cf7c51eee671646ba440ffa180f970902fd2fb".lower(): "TZ",
    "0xd1b532ea8dbda84e6937fedabb2fd5c33a049830".lower(): "TA",
    "0x02a0bacc642d501422af804a462f98335b2bbf09".lower(): "TB",
}


def _lower_text(value: object) -> str:
    return str(value or "").strip().lower()


def _read_json(path: Path) -> Any:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload


def _read_saved_anchor_rows(path: Path) -> list[dict[str, Any]]:
    rows = _read_json(path)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _normalize_usde_rows(path: Path) -> list[dict[str, Any]]:
    rows = _read_json(path)
    if not isinstance(rows, list):
        return []
    usde = "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34".lower()
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        base = row.get("baseToken") if isinstance(row.get("baseToken"), dict) else {}
        quote = row.get("quoteToken") if isinstance(row.get("quoteToken"), dict) else {}
        token0 = _lower_text(base.get("address"))
        token1 = _lower_text(quote.get("address"))
        if not token0 or not token1 or usde not in {token0, token1}:
            continue
        fee = row.get("feeTier")
        try:
            fee = int(fee)
        except (TypeError, ValueError):
            fee = 0
        dynamic = bool(row.get("dynamicFee"))
        hook = _lower_text(row.get("hooksAddress"))
        liq = row.get("liquidityUsd")
        try:
            liq = float(liq or 0)
        except (TypeError, ValueError):
            liq = 0.0
        normalized.append(
            {
                "poolKey": _lower_text(row.get("poolKey")),
                "token0": token0,
                "token1": token1,
                "s0": str(base.get("symbol") or "").strip() or token0[:10].upper(),
                "s1": str(quote.get("symbol") or "").strip() or token1[:10].upper(),
                "fee": fee,
                "dynamic": dynamic,
                "hook": hook,
                "liq": liq,
                "normal": (not dynamic) and (0 < fee <= 3000) and hook in ("", ZERO_ADDRESS.lower()),
                "createdBlock": row.get("createdBlock"),
            }
        )
    return normalized


def _load_api_anchor_rows() -> list[dict[str, Any]]:
    root = Path(__file__).resolve().parents[2]
    saved = [
        root / "runtime" / "cache" / "robinscreener" / "robin_anchor_ETH.json",
        root / "runtime" / "cache" / "robinscreener" / "robin_anchor_WETH.json",
        root / "runtime" / "cache" / "robinscreener" / "robin_anchor_USDG.json",
    ]
    usde_raw = root / "runtime" / "cache" / "robinscreener" / "robin_anchor_USDE_raw.json"
    rows: list[dict[str, Any]] = []
    for path in saved:
        rows.extend(_read_saved_anchor_rows(path))
    rows.extend(_normalize_usde_rows(usde_raw))
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for row in rows:
        pool_id = _lower_text(row.get("poolKey"))
        if not pool_id or pool_id in seen:
            continue
        seen.add(pool_id)
        token0 = _lower_text(row.get("token0"))
        token1 = _lower_text(row.get("token1"))
        if token0 not in ANCHOR_ADDRESSES and token1 not in ANCHOR_ADDRESSES:
            continue
        merged.append(row)
    return merged


def _stream_full_index_pools(
    full_index: Path,
    *,
    normal_token_addresses: set[str] | None = None,
) -> list[dict[str, Any]]:
    if ijson is None:
        raise RuntimeError("ijson is required to stream the full Robinhood V4 index")
    wanted_ids = {
        _lower_text(row.get("poolKey"))
        for row in _load_api_anchor_rows()
    }
    wanted_ids = {pool_id for pool_id in wanted_ids if pool_id}
    normal_tokens = {address for address in normal_token_addresses or set() if address}
    rows: list[dict[str, Any]] = []
    with full_index.open("rb") as handle:
        for pool in ijson.items(handle, "pools.item"):
            if not isinstance(pool, dict):
                continue
            pool_id = _lower_text(pool.get("poolId") or pool.get("pool"))
            token0 = _lower_text(pool.get("token0") or pool.get("currency0"))
            token1 = _lower_text(pool.get("token1") or pool.get("currency1"))
            if (
                pool_id in wanted_ids
                or token0 in CUSTOM_ADDRESSES
                or token1 in CUSTOM_ADDRESSES
                or (
                    normal_tokens
                    and token0 in normal_tokens
                    and token1 in normal_tokens
                )
            ):
                rows.append(pool)
    return rows


def _pool_row_with_overlay(row: dict[str, Any], api: dict[str, Any] | None) -> dict[str, Any]:
    token0 = _lower_text(row.get("token0") or row.get("currency0"))
    token1 = _lower_text(row.get("token1") or row.get("currency1"))
    symbol0 = CUSTOM_SYMBOLS.get(token0)
    symbol1 = CUSTOM_SYMBOLS.get(token1)
    if api:
        symbol0 = str(api.get("s0") or "").strip().upper() or symbol0
        symbol1 = str(api.get("s1") or "").strip().upper() or symbol1
    row["token0Symbol"] = symbol0 or row.get("token0Symbol") or token0[:10].upper()
    row["token1Symbol"] = symbol1 or row.get("token1Symbol") or token1[:10].upper()
    row["symbolPair"] = f"{row['token0Symbol']}:{row['token1Symbol']}"
    if api:
        liq = float(api.get("liq") or 0)
        row["hasLiquidity"] = liq > 0
        row["liquidityIndex"] = liq
        row["estimatedTvlUsd"] = liq
        row["tvlUsd"] = liq
        row["balances"] = {"tvlUsd": liq}
        row["depthStatus"] = "api_liquid" if liq > 0 else "api_no_liquidity"
        row["liquidityReadAt"] = None
        row["liquiditySource"] = "robinscreener_anchor_reference"
        row["source"] = "poolmanager_initialize_logs_plus_robinscreener"
    return row


def _build_filtered_index(full_index: Path, output: Path) -> dict[str, Any]:
    api_rows = _load_api_anchor_rows()
    api_by_id = {
        _lower_text(row.get("poolKey")): row
        for row in api_rows
        if _lower_text(row.get("poolKey"))
    }
    normal_token_addresses: set[str] = set(CUSTOM_ADDRESSES)
    for row in api_rows:
        if not row.get("normal"):
            continue
        for key in ("token0", "token1"):
            address = _lower_text(row.get(key))
            if address:
                normal_token_addresses.add(address)
    streamed = _stream_full_index_pools(
        full_index,
        normal_token_addresses=normal_token_addresses,
    )
    seen: set[str] = set()
    pools: list[dict[str, Any]] = []
    for row in streamed:
        pool_id = _lower_text(row.get("poolId") or row.get("pool"))
        if not pool_id or pool_id in seen:
            continue
        seen.add(pool_id)
        pools.append(_pool_row_with_overlay(row, api_by_id.get(pool_id)))
    # The full index may be unavailable for an API pool (should not happen);
    # synthesize a row from the API metadata when needed.
    for pool_id, api in api_by_id.items():
        if pool_id in seen:
            continue
        token0 = _lower_text(api.get("token0"))
        token1 = _lower_text(api.get("token1"))
        if not token0 or not token1:
            continue
        pools.append(
            {
                "protocol": "uniswap_v4",
                "poolId": pool_id,
                "pool": pool_id,
                "poolKeyDecoded": True,
                "token0": token0,
                "token1": token1,
                "token0Symbol": str(api.get("s0") or token0[:10].upper()),
                "token1Symbol": str(api.get("s1") or token1[:10].upper()),
                "fee": api.get("fee"),
                "dynamicFee": bool(api.get("dynamic")),
                "hooks": api.get("hook") or ZERO_ADDRESS,
                "initializeBlockNumber": api.get("createdBlock"),
                "initializeTransactionHash": "",
                "hasLiquidity": float(api.get("liq") or 0) > 0,
                "liquidityIndex": float(api.get("liq") or 0),
                "estimatedTvlUsd": float(api.get("liq") or 0),
                "tvlUsd": float(api.get("liq") or 0),
                "balances": {"tvlUsd": float(api.get("liq") or 0)},
                "source": "robinscreener_anchor_reference_only",
            }
        )
        seen.add(pool_id)
    payload = {
        "schemaVersion": "robinhood-v4-pool-index-v1",
        "network": "robinhood",
        "chainId": 4663,
        "poolManager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
        "generatedAt": "",
        "generatedBlockNumber": None,
        "discoveryMethod": "poolmanager_initialize_logs_plus_robinscreener_anchor_surface",
        "initializeLogCount": len(pools),
        "initializedPoolCount": len(pools),
        "selectedPoolCount": len(pools),
        "apiAnchorPoolCount": len(api_rows),
        "poolKeys": [],
        "pools": pools,
        "blockingReasons": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    load_chain_env_files(Path(__file__).resolve())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-index", default="")
    parser.add_argument("--index-output", default="")
    parser.add_argument("--candidates-output", default="")
    parser.add_argument("--rpc-url", default="")
    parser.add_argument(
        "--max-per-pair",
        type=int,
        default=6,
        help="Maximum representative pools kept per token pair; 0 keeps every pool.",
    )
    args = parser.parse_args()

    full_index = Path(args.full_index or SRC_ROOT / "runtime" / "evidence" / "robinhood" / "v4_pool_index" / "full_latest.json")
    if not full_index.exists():
        full_index = Path(args.full_index or SRC_ROOT / "runtime" / "evidence" / "robinhood" / "v4_pool_index" / "latest.json")
    if not full_index.exists():
        raise SystemExit(f"full V4 pool index not found: {full_index}")

    index_output = Path(args.index_output or default_v4_pool_index_path() or SRC_ROOT / "runtime" / "evidence" / "robinhood" / "v4_pool_index" / "latest.json")
    payload = _build_filtered_index(full_index, index_output)

    candidate_output = Path(
        args.candidates_output
        or chain_data_file_path("robinhood", "dex_candidates", chain_id=4663)
        or DEFAULT_ROBINHOOD_DEX_CANDIDATE_PATH
    )
    candidate_payload = build_mainnet_static_candidate_payload(
        payload,
        list(payload.get("pools") or []),
        max_candidates_per_pair=max(0, args.max_per_pair),
        rpc_url=args.rpc_url or None,
    )
    candidate_output.parent.mkdir(parents=True, exist_ok=True)
    candidate_output.write_text(json.dumps(candidate_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "fullIndex": str(full_index),
                "indexOutput": str(index_output),
                "indexedPoolCount": len(payload.get("pools") or []),
                "candidateOutput": str(candidate_output),
                "candidatePoolCount": len(candidate_payload.get("pools") or []),
                "summary": candidate_payload.get("summary") or {},
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
