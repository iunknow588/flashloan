from __future__ import annotations

from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_file_path
from core.chain_flow_dispatcher import ChainFlowRequest
from dex.intent_trade.chains.common.dex_candidate_tables import build_pair_table_candidates, read_json_object


APP_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_AVALANCHE_TESTNET_POOL_DISCOVERY_PATH = APP_ROOT / "runtime" / "evidence" / "avalanche-testnet" / "pool_discovery" / "latest.json"


def dex_pool_candidates_payload(
    *,
    request: ChainFlowRequest,
    pool_cache_path: Path | None = None,
    **_: Any,
) -> dict[str, Any]:
    path = (
        pool_cache_path
        or chain_data_file_path(request.network, "pool_discovery", chain_id=request.chain_id)
        or DEFAULT_AVALANCHE_TESTNET_POOL_DISCOVERY_PATH
    )
    table = read_json_object(path)
    payload = build_pair_table_candidates(
        table,
        path,
        source="chain_local_json" if table else "chain_local_json_missing",
        fallback_reason=None if table else "dex_pool_candidates_local_json_missing",
    )
    payload["poolStaticTable"]["network"] = request.network
    payload["poolStaticTable"]["chainId"] = request.chain_id
    return payload


__all__ = ["dex_pool_candidates_payload"]
