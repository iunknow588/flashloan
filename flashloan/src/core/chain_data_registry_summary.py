from __future__ import annotations

from pathlib import Path
from typing import Any

from core.chain_data_registry import (
    CHAIN_DATA_ROOT,
    SRC_ROOT,
    _as_dict,
    _int_or_none,
    _text,
    chain_data_by_chain_id,
    chain_data_by_network,
    chain_data_files,
    chain_flow_spec_by_chain_id,
    global_chain_id,
    load_chain_data,
)


def _other_chain_rows(row: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chain_id = int(row["chain_id"])
    network = str(row["network"])
    return [
        item
        for item in rows
        if int(item["chain_id"]) != chain_id and str(item["network"]) != network
    ]


def _flow_data_keys(flows: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for flow_payload in flows.values():
        flow = _as_dict(flow_payload)
        for key in flow.get("data_file_keys") or []:
            normalized = _text(key)
            if normalized and normalized not in keys:
                keys.append(normalized)
    return sorted(keys)


def _flow_scope_complete(flow: dict[str, Any]) -> bool:
    return bool(flow) and flow.get("requires_scope_match") is True


def _chain_handler_prefix(network: str) -> str:
    return f"intent_trade.chains.{str(network).strip().lower().replace('-', '_')}."


def _flow_handler_is_chain_owned(network: str, handler: object) -> bool:
    value = _text(handler)
    return value == "chain_specific_placeholder" or value.startswith(_chain_handler_prefix(network))


def _isolation_policy(
    row: dict[str, Any],
    flow_spec: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    isolation = _as_dict(flow_spec.get("isolation"))
    other_rows = _other_chain_rows(row, rows)
    expected_networks = sorted(str(item["network"]) for item in other_rows)
    expected_chain_ids = sorted(int(item["chain_id"]) for item in other_rows)
    configured_networks = sorted(
        _text(item).lower()
        for item in isolation.get("forbidden_source_networks") or []
        if _text(item)
    )
    configured_chain_ids = sorted(
        item
        for item in (_int_or_none(value) for value in isolation.get("forbidden_source_chain_ids") or [])
        if item is not None
    )
    scope_fields = sorted(
        _text(item).lower()
        for item in isolation.get("scope_fields") or []
        if _text(item)
    )
    return {
        "scope_fields": scope_fields,
        "required_scope_fields": ["chain_id", "market_id", "network"],
        "forbid_cross_chain_fallback": isolation.get("forbid_cross_chain_fallback") is True,
        "forbidden_source_networks": configured_networks,
        "expected_forbidden_source_networks": expected_networks,
        "forbidden_source_chain_ids": configured_chain_ids,
        "expected_forbidden_source_chain_ids": expected_chain_ids,
        "forbidden_networks_complete": configured_networks == expected_networks,
        "forbidden_chain_ids_complete": configured_chain_ids == expected_chain_ids,
    }


def _data_independence_summary(
    row: dict[str, Any],
    flow_spec: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    flows = _as_dict(flow_spec.get("flows"))
    data_files = _as_dict(row.get("data_files"))
    flow_data_keys = _flow_data_keys(flows)
    isolation = _isolation_policy(row, flow_spec, rows)
    return {
        "owner_network": row["network"],
        "owner_chain_id": int(row["chain_id"]),
        "global_chain_id": row["global_chain_id"],
        "chain_directory": row.get("chain_dir"),
        "chain_file": row.get("data_file"),
        "flow_file": flow_spec.get("flow_file"),
        "data_file_keys": sorted(str(key) for key in data_files),
        "flow_data_file_keys": flow_data_keys,
        "all_flow_data_files_declared": set(flow_data_keys) <= set(data_files),
        "isolation": isolation,
    }


def _processing_independence_summary(
    row: dict[str, Any],
    flow_spec: dict[str, Any],
) -> dict[str, Any]:
    flows = _as_dict(flow_spec.get("flows"))
    handlers = _as_dict(row.get("processing_handlers"))
    flow_names = sorted(flows)
    handler_names = sorted(handlers)
    scope_matched = {
        flow_name: _flow_scope_complete(_as_dict(flow_payload))
        for flow_name, flow_payload in flows.items()
    }
    return {
        "owner_network": row["network"],
        "owner_chain_id": int(row["chain_id"]),
        "global_chain_id": row["global_chain_id"],
        "flow_names": flow_names,
        "handler_names": handler_names,
        "all_flows_have_handlers": set(flow_names) <= set(handler_names),
        "all_handlers_are_chain_declared": set(handler_names) <= set(flow_names),
        "handler_prefix": _chain_handler_prefix(row["network"]),
        "all_flow_handlers_are_chain_owned": all(
            _flow_handler_is_chain_owned(row["network"], _as_dict(flow_payload).get("handler"))
            for flow_payload in flows.values()
        )
        if flows
        else False,
        "requires_scope_match_by_flow": scope_matched,
        "all_flows_require_scope_match": all(scope_matched.values()) if scope_matched else False,
    }


def chain_config_summary(
    network: str | None = None,
    *,
    chain_id: int | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    row = chain_data_by_chain_id(chain_id, root=root) if chain_id is not None else chain_data_by_network(network, root=root)
    if not row:
        return {}
    from core.chain_data_registry import _canonical_feature_scopes
    flow_spec = chain_flow_spec_by_chain_id(int(row["chain_id"]), root=root) or {}
    flow_file = _text(flow_spec.get("flow_file"))
    flows = _as_dict(flow_spec.get("flows"))
    flash_loan = _as_dict(row.get("flash_loan"))
    gid = global_chain_id(chain_id=int(row["chain_id"]), network=str(row["network"]))
    return {
        "schema_version": row.get("schema_version") or "chain-data-v1",
        "global_chain_id": gid,
        "chain_key": gid,
        "network": row["network"],
        "chain_id": int(row["chain_id"]),
        "chainId": int(row["chain_id"]),
        "display_name": _text(row.get("display_name")) or row["network"],
        "native_symbol": _text(row.get("native_symbol")),
        "rpc_url": _text(row.get("rpc_url")),
        "explorer_url": _text(row.get("explorer_url")),
        "defaults": _as_dict(row.get("defaults")),
        "features": _canonical_feature_scopes(row),
        "flash_loan": {
            "default_protocols": list(flash_loan.get("default_protocols") or []),
            "candidate_protocols": list(flash_loan.get("candidate_protocols") or []),
            "supported_protocol_count": int(flash_loan.get("supported_protocol_count") or 0),
            "supports_aave_or_morpho_flash_loans": flash_loan.get("supports_aave_or_morpho_flash_loans"),
        },
        "data_files": _as_dict(row.get("data_files")),
        "contracts": _as_dict(row.get("contracts")),
        "processing_handlers": _as_dict(row.get("processing_handlers")),
        "processing_flows": flows,
        "data_independence": _data_independence_summary(row, flow_spec, load_chain_data(root=root)),
        "processing_independence": _processing_independence_summary(row, flow_spec),
        "flow_names": sorted(flows),
        "data_file": row.get("data_file"),
        "chain_file": row.get("data_file"),
        "flow_file": flow_file,
    }


def chain_data_file_path(
    network: str | None,
    key: str,
    *,
    chain_id: int | None = None,
    root: Path | None = None,
) -> Path | None:
    value = chain_data_files(network, chain_id=chain_id, root=root).get(key)
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return (SRC_ROOT / path).resolve()


