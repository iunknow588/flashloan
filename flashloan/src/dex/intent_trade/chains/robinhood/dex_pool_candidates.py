from __future__ import annotations

from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_file_path
from core.chain_data_registry import chain_data_by_network
from core.chain_flow_dispatcher import ChainFlowRequest
from dex.intent_trade.chains.common.dex_candidate_tables import build_robinhood_pool_candidates, read_json_object
from dex.intent_trade.chains.common.dex_candidate_tables import build_anchor_candidate_groups


APP_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROBINHOOD_DEX_CANDIDATE_PATH = APP_ROOT / "runtime" / "cache" / "robinhood_dex_candidates.json"
DEFAULT_ROBINHOOD_DEX_MARKET_POOL_PATH = APP_ROOT / "runtime" / "cache" / "robinhood_dex_market_pools.json"
DEFAULT_ROBINHOOD_DEX_EVIDENCE_ROOT = APP_ROOT / "runtime" / "evidence" / "robinhood" / "dex_discovery"

_ROBINHOOD_ROW = chain_data_by_network("robinhood") or {}
_ROBINHOOD_CONTRACTS = _ROBINHOOD_ROW.get("contracts") if isinstance(_ROBINHOOD_ROW.get("contracts"), dict) else {}
_WETH_ADDRESS = str(_ROBINHOOD_CONTRACTS.get("wrapped_native") or "").strip().lower()
_USDG_ADDRESS = str(_ROBINHOOD_CONTRACTS.get("usdg") or "").strip().lower()
_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _known_v4_token_metadata(address: str) -> tuple[str, int | None] | None:
    normalized = str(address or "").strip().lower()
    if normalized == _ZERO_ADDRESS:
        return "ETH", 18
    if normalized and normalized == _WETH_ADDRESS:
        return "WETH", 18
    if normalized and normalized == _USDG_ADDRESS:
        return "USDG", 6
    return None


def _v4_index_path(request: ChainFlowRequest) -> Path | None:
    return chain_data_file_path(request.network, "v4_pool_index", chain_id=request.chain_id)


def _v4_index_rows(path: Path | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = read_json_object(path)
    rows = [row for row in payload.get("pools") or [] if isinstance(row, dict)]
    return payload, rows


def _v4_symbol(row: dict[str, Any], index: int) -> str:
    value = str(row.get(f"token{index}Symbol") or "").strip().upper()
    address = str(row.get(f"token{index}") or row.get(f"currency{index}") or "").strip()
    known = _known_v4_token_metadata(address)
    if known is not None:
        return known[0]
    if value:
        return value
    if address.lower() == _ZERO_ADDRESS:
        return "ETH"
    return address[:10].upper() if address else ""


def _v4_pool_candidate(row: dict[str, Any], pool_manager: str) -> dict[str, Any] | None:
    pool_id = str(row.get("poolId") or row.get("pool_id") or row.get("pool") or "").strip()
    token0 = str(row.get("token0") or row.get("currency0") or "").strip()
    token1 = str(row.get("token1") or row.get("currency1") or "").strip()
    symbol0 = _v4_symbol(row, 0)
    symbol1 = _v4_symbol(row, 1)
    decimals0 = row.get("token0Decimals")
    decimals1 = row.get("token1Decimals")
    known0 = _known_v4_token_metadata(token0)
    known1 = _known_v4_token_metadata(token1)
    if known0 is not None and decimals0 in (None, ""):
        decimals0 = known0[1]
    if known1 is not None and decimals1 in (None, ""):
        decimals1 = known1[1]
    if decimals0 in (None, ""):
        decimals0 = 18
    if decimals1 in (None, ""):
        decimals1 = 18
    if not pool_id or not token0 or not token1 or not symbol0 or not symbol1:
        return None
    return {
        "pool": pool_id,
        "poolId": pool_id,
        "poolIdentityMode": "v4_pool_id",
        "poolManager": row.get("poolManager") or pool_manager,
        "poolKey": row.get("poolKey") or row,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": row.get("fee"),
        "tickSpacing": row.get("tickSpacing"),
        "hooks": row.get("hooks"),
        "quality": "pass",
        "status": row.get("status") or "initialized_pool",
        "rejectReasons": [],
        "token0": token0,
        "token1": token1,
        "token0Symbol": symbol0,
        "token1Symbol": symbol1,
        "token0Decimals": decimals0,
        "token1Decimals": decimals1,
        "sqrtPriceX96": row.get("sqrtPriceX96"),
        "tick": row.get("tick"),
        "liquidity": row.get("liquidity") or "",
        "balances": row.get("balances") or {"tvlUsd": row.get("tvlUsd") or 0},
        "tvlUsd": row.get("tvlUsd") or 0,
        "depthBand": row.get("depthBand") or {"level": "indexed", "label": "indexed V4 PoolKey"},
        "symbolPair": row.get("symbolPair") or f"{symbol0}:{symbol1}",
        "source": "robinhood_mainnet_v4_pool_index",
        "evidence": {
            **(row.get("evidence") or {}),
            "source": "robinhood_mainnet_v4_pool_index",
            "poolKeyDecoded": bool(row.get("poolKeyDecoded") or row.get("poolKey")),
            "initializeBlockNumber": row.get("initializeBlockNumber"),
            "initializeTransactionHash": row.get("initializeTransactionHash"),
        },
    }


def _mainnet_v4_payload(request: ChainFlowRequest, path: Path, index_payload: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    pool_manager = str(index_payload.get("poolManager") or "").strip()
    candidates = [candidate for row in rows if (candidate := _v4_pool_candidate(row, pool_manager)) is not None]
    grouped: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        token_pair = tuple(sorted((str(candidate.get("token0") or "").lower(), str(candidate.get("token1") or "").lower())))
        key = ":".join(token_pair) if all(token_pair) else str(candidate.get("symbolPair") or "")
        bucket = grouped.setdefault(
            key,
            {
                "symbolPair": candidate.get("symbolPair"),
                "addressPair": ":".join(token_pair),
                "identityMode": "v4_pool_id",
                "tokenX": candidate.get("token0"),
                "tokenY": candidate.get("token1"),
                "tokenXSymbol": candidate.get("token0Symbol"),
                "tokenYSymbol": candidate.get("token1Symbol"),
                "classification": {},
                "strictQualityPools": [],
                "preferredPools": [],
                "executionPools": [],
                "rejectedPools": [],
            },
        )
        bucket["strictQualityPools"].append(candidate)
        bucket["preferredPools"].append(candidate)
        if len(bucket["executionPools"]) < 5:
            bucket["executionPools"].append(candidate)

    pairs: list[dict[str, Any]] = []
    for pair in grouped.values():
        pair["allPoolCount"] = str(len(pair["preferredPools"]))
        pair["strictQualityPoolCount"] = str(len(pair["strictQualityPools"]))
        pair["preferredPoolCount"] = str(len(pair["preferredPools"]))
        pair["executionPoolCount"] = str(len(pair["executionPools"]))
        pair["rejectedPoolCount"] = "0"
        pair["hasStrictQualityPools"] = bool(pair["strictQualityPools"])
        pair["hasPreferredPools"] = bool(pair["preferredPools"])
        pair["rejectReasonCounts"] = {}
        pairs.append(pair)

    pairs.sort(key=lambda item: str(item.get("symbolPair") or ""))
    anchor_groups = build_anchor_candidate_groups(pairs)
    return {
        "mode": "dex_pool_candidates",
        "source": "mainnet_v4_pool_index",
        "fallbackReason": None if candidates else "mainnet_v4_pool_index_has_no_decoded_pools",
        "poolStaticTable": {
            "path": str(path),
            "exists": bool(candidates),
            "mode": index_payload.get("schemaVersion") or "robinhood-v4-pool-index-v1",
            "network": request.network,
            "chainId": request.chain_id,
            "generatedAt": index_payload.get("generatedAt"),
            "generatedBlockNumber": index_payload.get("generatedBlockNumber"),
            "poolManager": pool_manager,
        },
        "filters": {"source": "mainnet v4 pool index", "protocol": "uniswap_v4", "identity": "poolId/poolKey"},
        "summary": {
            "candidateRouteCount": str(len(candidates)),
            "staticPoolCount": str(len(candidates)),
            "staticPairCount": str(len(pairs)),
            "pairTableCount": str(len(pairs)),
            "anchorGroupCount": str(len(anchor_groups)),
            "preferredPoolCount": str(len(candidates)),
            "executionPoolCount": str(sum(len(pair["executionPools"]) for pair in pairs)),
            "strictQualityPoolCount": str(len(candidates)),
            "pairsWithStrictQualityPools": str(sum(1 for pair in pairs if pair["hasStrictQualityPools"])),
            "pairsWithPreferredPools": str(sum(1 for pair in pairs if pair["hasPreferredPools"])),
            "v4IndexedPoolCount": str(len(rows)),
        },
        "pairs": pairs,
        "anchorGroups": anchor_groups,
        "errors": [] if candidates else ["mainnet_v4_pool_index_has_no_decoded_pools"],
    }


def dex_pool_candidates_payload(
    *,
    request: ChainFlowRequest,
    pool_cache_path: Path | None = None,
    market_pool_path: Path | None = None,
    evidence_root: Path | None = None,
    **_: Any,
) -> dict[str, Any]:
    protocol_set = {str(item or "").strip().lower() for item in request.flow.get("allowed_protocols") or []}
    if not protocol_set or "uniswap_v4" in protocol_set:
        v4_path = _v4_index_path(request)
        index_payload, v4_rows = _v4_index_rows(v4_path)
        if v4_rows:
            return _mainnet_v4_payload(request, v4_path or Path(""), index_payload, v4_rows)
    path = (
        pool_cache_path
        or chain_data_file_path(request.network, "dex_candidates", chain_id=request.chain_id)
        or DEFAULT_ROBINHOOD_DEX_CANDIDATE_PATH
    )
    market_path = (
        market_pool_path
        or chain_data_file_path(request.network, "dex_market_pools", chain_id=request.chain_id)
        or DEFAULT_ROBINHOOD_DEX_MARKET_POOL_PATH
    )
    evidence_path = (
        evidence_root
        or chain_data_file_path(request.network, "dex_discovery", chain_id=request.chain_id)
        or DEFAULT_ROBINHOOD_DEX_EVIDENCE_ROOT
    )
    table = read_json_object(path)
    return build_robinhood_pool_candidates(
        table,
        path,
        market_pool_path=market_path,
        evidence_root=evidence_path,
        source="runtime_cache" if table else "local_json_missing",
        fallback_reason=None if table else "dex_pool_static_table_unavailable",
    )


__all__ = ["dex_pool_candidates_payload"]
