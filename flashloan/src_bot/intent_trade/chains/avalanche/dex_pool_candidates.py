from __future__ import annotations

from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_file_path
from core.chain_flow_dispatcher import ChainFlowRequest
from intent_trade.chains.common.dex_candidate_tables import build_pair_table_candidates, read_json_object


APP_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_AVALANCHE_DEX_POOL_STATIC_TABLE_PATH = APP_ROOT / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"


def dex_pool_candidates_payload(
    *,
    request: ChainFlowRequest,
    pool_cache_path: Path | None = None,
    **_: Any,
) -> dict[str, Any]:
    path = (
        pool_cache_path
        or chain_data_file_path(request.network, "uniswap_v3_pools", chain_id=request.chain_id)
        or DEFAULT_AVALANCHE_DEX_POOL_STATIC_TABLE_PATH
    )
    table = read_json_object(path)
    return build_pair_table_candidates(
        table,
        path,
        source="runtime_cache" if table else "local_json_missing",
        fallback_reason=None if table else "dex_pool_static_table_unavailable",
    )


__all__ = ["dex_pool_candidates_payload"]
