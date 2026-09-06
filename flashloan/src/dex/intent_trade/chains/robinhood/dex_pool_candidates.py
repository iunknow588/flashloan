from __future__ import annotations

from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_file_path
from core.chain_flow_dispatcher import ChainFlowRequest
from dex.intent_trade.chains.common.dex_candidate_tables import build_robinhood_pool_candidates, read_json_object


APP_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROBINHOOD_DEX_CANDIDATE_PATH = APP_ROOT / "runtime" / "cache" / "robinhood_dex_candidates.json"
DEFAULT_ROBINHOOD_DEX_MARKET_POOL_PATH = APP_ROOT / "runtime" / "cache" / "robinhood_dex_market_pools.json"
DEFAULT_ROBINHOOD_DEX_EVIDENCE_ROOT = APP_ROOT / "runtime" / "evidence" / "robinhood" / "dex_discovery"


def dex_pool_candidates_payload(
    *,
    request: ChainFlowRequest,
    pool_cache_path: Path | None = None,
    market_pool_path: Path | None = None,
    evidence_root: Path | None = None,
    **_: Any,
) -> dict[str, Any]:
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
