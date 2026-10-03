from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dex.intent_trade.chains.robinhood_common.eth_pair_tokens_core import (  # noqa: E402
    MAINNET_CHAIN_ID,
    NETWORK,
    ZERO_ADDRESS,
    default_eth_pair_tokens_path,
    default_pool_index_path,
    eth_tokens_from_index,
    eth_tokens_from_rpc,
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_address(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("0x") and len(text) == 42:
        return text
    return ""


def _token_matches(token: dict[str, Any], needle: str) -> bool:
    normalized_needle = str(needle or "").strip()
    if not normalized_needle:
        return False
    needle_lower = normalized_needle.lower()
    return (
        needle_lower == str(token.get("address") or "").lower()
        or needle_lower == _normalize_address(token.get("address"))
        or str(token.get("symbol") or "").strip().lower() == needle_lower
        or str(token.get("symbol") or "").strip().upper() == normalized_needle.upper()
    )


def _eth_pool_tokens(index: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for token in eth_tokens_from_index(index):
        address = str(token.get("address") or "").strip().lower()
        key = address
        if key == ZERO_ADDRESS or key in seen:
            continue
        seen.add(key)
        rows.append({"address": token.get("address"), "symbol": str(token.get("symbol") or address[:10])})
    return rows


def _pools_by_edge(index: dict[str, Any]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for pool in index.get("pools") or []:
        if not isinstance(pool, dict):
            continue
        pool_key = pool.get("poolKey") if isinstance(pool.get("poolKey"), dict) else {}
        token0 = _normalize_address(pool.get("token0") or pool_key.get("currency0") or pool_key.get("token0"))
        token1 = _normalize_address(pool.get("token1") or pool_key.get("currency1") or pool_key.get("token1"))
        if not token0 or not token1 or token0 == token1:
            continue
        edge = tuple(sorted((token0, token1)))
        grouped.setdefault(edge, []).append(pool)
    return grouped


def _closed_loop_checks(
    index: dict[str, Any],
    requested_tokens: list[str],
    *,
    eth_pair_token_payload: dict[str, Any] | None = None,
    route_limit: int = 500,
) -> dict[str, Any]:
    eth_tokens = _eth_pool_tokens(index)
    candidate_tokens = list(eth_pair_token_payload.get("ycZc") or []) if eth_pair_token_payload else []
    candidate_by_address = {
        str(token.get("address") or "").lower(): token
        for token in candidate_tokens
        if _normalize_address(token.get("address"))
    }
    pools_by_edge = _pools_by_edge(index)
    found: list[dict[str, Any]] = []
    matched: dict[str, dict[str, Any]] = {}
    for token in eth_tokens:
        address = str(token.get("address") or "").lower()
        for needle in requested_tokens:
            if _token_matches(token, needle):
                matched.setdefault(needle.lower(), {"needle": needle, **token, "ethPoolCount": 0})
                matched[needle.lower()]["ethPoolCount"] += 1
        found.append(token)

    eth_address = _normalize_address(ZERO_ADDRESS)
    matched_items = list(matched.values())
    checks: list[dict[str, Any]] = []
    for item in matched_items:
        token_address = str(item.get("address") or "").lower()
        eth_edge = tuple(sorted((eth_address, token_address)))
        eth_pool_count = len(pools_by_edge.get(eth_edge) or [])
        checks.append(
            {
                "requested": item["needle"],
                "matchedAddress": item.get("address") or "",
                "matchedSymbol": item.get("symbol") or "",
                "ethPoolCount": eth_pool_count,
                "inEthPairCandidate": bool(candidate_by_address.get(token_address)),
                "closedLoopVerified": False,
            }
        )

    matched_keys = {
        str(item.get("address") or "").lower()
        for item in matched_items
        for needle in requested_tokens
        if _token_matches(item, needle)
    }
    candidate_matched_keys = {
        str(token.get("address") or "").lower()
        for token in candidate_tokens
        for needle in requested_tokens
        if _token_matches(token, needle)
    }
    missing_requested: list[dict[str, Any]] = []
    for needle in requested_tokens:
        normalized_needle = str(needle or "").strip().lower()
        if not normalized_needle:
            continue
        if normalized_needle in matched_keys or normalized_needle in candidate_matched_keys:
            continue
        if normalized_needle.startswith("0x") and len(normalized_needle) == 42:
            missing_requested.append(
                {
                    "requested": needle,
                    "reason": "address_not_found_in_pool_index",
                    "hint": "No ETH-paired V4 pool in the current v4_pool_index references this address.",
                }
            )
        else:
            missing_requested.append(
                {
                    "requested": needle,
                    "reason": "symbol_not_found_in_indexed_evidence",
                    "hint": "Symbol lookups only work when the real token symbol is recorded in v4_pool_index "
                    "metadata. Current indexed ETH-pair symbols are "
                    f"{sorted({item.get('symbol') for item in eth_tokens if item.get('symbol')})}. "
                    "Pass a token address instead, or refresh the evidence snapshot with token metadata.",
                }
            )

    candidate_hits: list[dict[str, Any]] = []
    candidate_seen: set[str] = set()
    for token in candidate_tokens:
        address = str(token.get("address") or "").lower()
        if address in candidate_seen:
            continue
        candidate_seen.add(address)
        for needle in requested_tokens:
            if _token_matches(token, needle):
                candidate_hits.append(
                    {
                        "requested": needle,
                        "matchedAddress": token.get("address") or "",
                        "matchedSymbol": token.get("symbol") or "",
                        "inEthPairCandidate": True,
                        "ethPoolCount": 0,
                        "closedLoopVerified": False,
                        "note": "Present in full ETH-pair candidate set but not yet linked to a verified y-z bridge pool in v4_pool_index.",
                    }
                )
                break

    for x in range(len(matched_items)):
        for y in range(x + 1, len(matched_items)):
            a = str(matched_items[x].get("address") or "").lower()
            b = str(matched_items[y].get("address") or "").lower()
            edge = tuple(sorted((a, b)))
            bridge_pool_count = len(pools_by_edge.get(edge) or [])
            if bridge_pool_count:
                checks.append(
                    {
                        "requested": f"{matched_items[x]['needle']},{matched_items[y]['needle']}",
                        "matchedAddress": f"{a}:{b}",
                        "matchedSymbol": f"{matched_items[x].get('symbol') or ''}:{matched_items[y].get('symbol') or ''}",
                        "bridgePoolCount": bridge_pool_count,
                        "closedLoopVerified": True,
                    }
                )

    return {
        "requestedTokens": requested_tokens,
        "currentIndexedEthPairTokens": eth_tokens,
        "missingRequestedTokens": missing_requested,
        "matchedEthPoolTokenCount": len(matched_items),
        "candidateHitCount": len(candidate_hits),
        "candidateHits": candidate_hits[: route_limit],
        "matchedEthPoolTokenSample": checks[: route_limit],
        "allVerifiedTokenPairSlots": len(matched_items) * max(0, len(matched_items) - 1),
        "verifiedBridgePoolCount": sum(1 for check in checks if check.get("closedLoopVerified")),
        "notes": [
            "Token matching is case-insensitive on symbol and address.",
            "A closed loop is only reported when ETH-y, y-z and z-ETH pools all exist in the v4_pool_index.",
            "The full ETH-pair candidate set stores symbol as an address prefix when metadata was not fetched; "
            "symbol-only lookups such as BTC/BNB therefore require either recorded metadata or an explicit address.",
            "Missing bridge pool evidence is reported as unmatched rather than synthesizing a fake pool.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build Robinhood mainnet ETH-paired yc/zc token sets from Uniswap V4 pool evidence."
    )
    parser.add_argument("--idx", default="", help="Optional path to a Robinhood mainnet v4_pool_index/latest.json; default uses chain registry.")
    parser.add_argument("--rpc-url", default="", help="Optional RPC URL. When set, scan PoolManager Initialize topics directly.")
    parser.add_argument("--pool-manager", default="", help="Optional PoolManager address for RPC scanning.")
    parser.add_argument("--from-block", type=int, default=0)
    parser.add_argument("--to-block", type=int, default=0, help="0 means latest block.")
    parser.add_argument("--no-metadata", action="store_true", help="Do not fetch ERC20 symbols during RPC scan.")
    parser.add_argument("--stable-only", action="store_true", help="Optional: keep only stablecoin yc/zc tokens.")
    parser.add_argument("--chunk-size", type=int, default=10_000_000, help="Initial RPC log chunk size.")
    parser.add_argument("--no-write", action="store_true", help="Print JSON without writing the generated payload.")
    parser.add_argument("--output", default="", help="Optional output path; default prints JSON to stdout.")
    parser.add_argument("--tokens", default="", help="Comma-separated token symbols/addresses to check for real ETH-pool and bridge-pool evidence.")
    args = parser.parse_args()

    payload: dict[str, Any]
    if args.rpc_url:
        if not args.pool_manager:
            raise SystemExit("--pool-manager is required when --rpc-url is set")
        payload = eth_tokens_from_rpc(
            rpc_url=args.rpc_url,
            pool_manager=args.pool_manager,
            from_block=args.from_block,
            to_block=int(args.to_block or 0) or None,
            include_metadata=not args.no_metadata,
            stable_only=args.stable_only,
        )
    else:
        path = Path(args.idx) if args.idx else default_pool_index_path()
        index = _read_json(path)
        if args.tokens:
            requested = [item.strip() for item in args.tokens.split(",") if item.strip()]
            eth_pair_payload = _read_json(default_eth_pair_tokens_path())
            payload = {
                "network": index.get("network") or NETWORK,
                "chainId": index.get("chainId") or MAINNET_CHAIN_ID,
                "sourcePath": str(path),
                "pools": len(index.get("pools") or []),
                "ethPairTokenCount": len(eth_tokens_from_index(index, stable_only=args.stable_only)),
                "ethPairCandidateTokenCount": int(eth_pair_payload.get("ethPairTokenCount") or len(eth_pair_payload.get("ycZc") or [])),
                "tokenChecks": _closed_loop_checks(index, requested, eth_pair_token_payload=eth_pair_payload),
            }
        else:
            payload = {
                "network": index.get("network") or NETWORK,
                "chainId": index.get("chainId") or MAINNET_CHAIN_ID,
                "sourcePath": str(path),
                "pools": len(index.get("pools") or []),
                "ethPairTokenCount": len(eth_tokens_from_index(index, stable_only=args.stable_only)),
                "ycZc": eth_tokens_from_index(index, stable_only=args.stable_only),
            }

    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
