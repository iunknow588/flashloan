from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SRC_ROOT = Path(__file__).resolve().parents[1]
CHAIN_DATA_ROOT = SRC_ROOT / "chains"
CHAIN_REGISTRY_FILE = "registry.json"
DEX_CANDIDATE_PROTOCOLS = {
    "balancer_v2",
    "biswap_v2",
    "curve",
    "pancakeswap_v2",
    "pancakeswap_v3",
    "quickswap_v2",
    "traderjoe_v2",
    "uniswap_v2",
    "uniswap_v3",
    "uniswap_v4",
}


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _normalize_protocols(values: object) -> list[str]:
    result: list[str] = []
    for value in values if isinstance(values, list) else []:
        protocol = _text(value).lower()
        if protocol and protocol not in result:
            result.append(protocol)
    return result


def global_chain_id(
    *,
    chain_id: int | None = None,
    network: str | None = None,
    market_id: str | None = None,
) -> str:
    if chain_id is not None:
        return f"chain:{int(chain_id)}"
    normalized_network = _text(network).lower()
    if normalized_network:
        return f"network:{normalized_network}"
    normalized_market = _text(market_id).lower()
    if normalized_market:
        return f"market:{normalized_market}"
    return "chain:unknown"


def _normalize_chain_file(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    network = _text(payload.get("network")).lower()
    chain_id = _int_or_none(payload.get("chain_id"))
    if not network:
        raise ValueError(f"{path} missing network")
    if path.parent.name.lower() != network:
        raise ValueError(f"{path} directory name must match network {network}")
    if chain_id is None:
        raise ValueError(f"{path} missing integer chain_id")
    flash_loan = _as_dict(payload.get("flash_loan"))
    payload["network"] = network
    payload["chain_id"] = chain_id
    payload["global_chain_id"] = global_chain_id(chain_id=chain_id, network=network)
    payload["chain_key"] = payload["global_chain_id"]
    candidate_protocols = _normalize_protocols(flash_loan.get("candidate_protocols"))
    default_protocols = _normalize_protocols(flash_loan.get("default_protocols"))
    configured_count = _int_or_none(flash_loan.get("supported_protocol_count"))
    payload["flash_loan"] = {
        **flash_loan,
        "default_protocols": default_protocols,
        "candidate_protocols": candidate_protocols,
        "supported_protocol_count": configured_count if configured_count is not None else len(candidate_protocols),
    }
    payload["data_file"] = str(path)
    return payload


def load_chain_registry(*, root: Path | None = None) -> dict[str, Any]:
    chain_root = root or CHAIN_DATA_ROOT
    path = chain_root / CHAIN_REGISTRY_FILE
    payload = _read_json(path)
    if payload.get("primary_key") != "chain_id":
        raise ValueError(f"{path} must declare primary_key=chain_id")
    chains = _as_dict(payload.get("chains"))
    if not chains:
        raise ValueError(f"{path} missing chains")
    normalized: dict[int, dict[str, Any]] = {}
    seen_networks: set[str] = set()
    for raw_chain_id, item in chains.items():
        chain_id = _int_or_none(raw_chain_id)
        row = _as_dict(item)
        network = _text(row.get("network")).lower()
        directory = _text(row.get("directory"))
        if chain_id is None:
            raise ValueError(f"{path} has non-integer chain id key {raw_chain_id}")
        if not network:
            raise ValueError(f"{path} chain {chain_id} missing network")
        if not directory:
            raise ValueError(f"{path} chain {chain_id} missing directory")
        if network in seen_networks:
            raise ValueError(f"{path} duplicate network {network}")
        normalized[chain_id] = {
            "chain_id": chain_id,
            "global_chain_id": global_chain_id(chain_id=chain_id, network=network),
            "chain_key": global_chain_id(chain_id=chain_id, network=network),
            "network": network,
            "directory": directory,
            "chain_file": str(chain_root / directory / "chain.json"),
            "flow_file": str(chain_root / directory / "flows.json"),
        }
        seen_networks.add(network)
    return {
        **payload,
        "registry_file": str(path),
        "chains_by_id": dict(sorted(normalized.items())),
    }


def _chain_dirs(chain_root: Path) -> list[Path]:
    registry = load_chain_registry(root=chain_root)
    return [chain_root / row["directory"] for row in registry["chains_by_id"].values()]


def load_chain_data(*, root: Path | None = None) -> list[dict[str, Any]]:
    chain_root = root or CHAIN_DATA_ROOT
    rows = []
    registry = load_chain_registry(root=chain_root)
    for indexed in registry["chains_by_id"].values():
        row = _normalize_chain_file(Path(indexed["chain_file"]))
        if int(row["chain_id"]) != int(indexed["chain_id"]):
            raise ValueError(f"{indexed['chain_file']} chain_id does not match registry chain_id {indexed['chain_id']}")
        if row["network"] != indexed["network"]:
            raise ValueError(f"{indexed['chain_file']} network does not match registry network {indexed['network']}")
        row["registry_file"] = registry["registry_file"]
        row["registry_chain_id"] = indexed["chain_id"]
        row["chain_dir"] = str(chain_root / indexed["directory"])
        rows.append(row)
    seen_chain_ids: dict[int, str] = {}
    seen_networks: set[str] = set()
    for row in rows:
        chain_id = int(row["chain_id"])
        network = str(row["network"])
        if chain_id in seen_chain_ids:
            raise ValueError(f"duplicate chain_id {chain_id}: {seen_chain_ids[chain_id]} and {network}")
        if network in seen_networks:
            raise ValueError(f"duplicate network {network}")
        seen_chain_ids[chain_id] = network
        seen_networks.add(network)
    return rows


def _normalize_flow_file(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    network = _text(payload.get("network")).lower()
    chain_id = _int_or_none(payload.get("chain_id"))
    if not network:
        raise ValueError(f"{path} missing network")
    if path.parent.name.lower() != network:
        raise ValueError(f"{path} directory name must match flow network {network}")
    if chain_id is None:
        raise ValueError(f"{path} missing integer chain_id")
    flows = _as_dict(payload.get("flows"))
    if not flows:
        raise ValueError(f"{path} missing flows")
    payload["network"] = network
    payload["chain_id"] = chain_id
    payload["global_chain_id"] = global_chain_id(chain_id=chain_id, network=network)
    payload["chain_key"] = payload["global_chain_id"]
    payload["flows"] = flows
    payload["flow_file"] = str(path)
    return payload


def load_chain_flow_specs(*, root: Path | None = None) -> list[dict[str, Any]]:
    chain_root = root or CHAIN_DATA_ROOT
    rows = []
    registry = load_chain_registry(root=chain_root)
    for indexed in registry["chains_by_id"].values():
        flow_path = Path(indexed["flow_file"])
        if flow_path.exists():
            row = _normalize_flow_file(flow_path)
            if int(row["chain_id"]) != int(indexed["chain_id"]):
                raise ValueError(f"{flow_path} chain_id does not match registry chain_id {indexed['chain_id']}")
            if row["network"] != indexed["network"]:
                raise ValueError(f"{flow_path} network does not match registry network {indexed['network']}")
            row["registry_file"] = registry["registry_file"]
            row["registry_chain_id"] = indexed["chain_id"]
            row["chain_dir"] = str(chain_root / indexed["directory"])
            rows.append(row)
    seen_chain_ids: dict[int, str] = {}
    seen_networks: set[str] = set()
    for row in rows:
        chain_id = int(row["chain_id"])
        network = str(row["network"])
        if chain_id in seen_chain_ids:
            raise ValueError(f"duplicate flow chain_id {chain_id}: {seen_chain_ids[chain_id]} and {network}")
        if network in seen_networks:
            raise ValueError(f"duplicate flow network {network}")
        seen_chain_ids[chain_id] = network
        seen_networks.add(network)
    return rows


def chain_flow_spec_by_chain_id(chain_id: int | None, *, root: Path | None = None) -> dict[str, Any] | None:
    if chain_id is None:
        return None
    wanted = int(chain_id)
    return next((row for row in load_chain_flow_specs(root=root) if int(row["chain_id"]) == wanted), None)


def chain_flow_spec_by_network(network: str | None, *, root: Path | None = None) -> dict[str, Any] | None:
    wanted = _text(network).lower()
    if not wanted:
        return None
    return next((row for row in load_chain_flow_specs(root=root) if row["network"] == wanted), None)


def chain_data_by_chain_id(chain_id: int | None, *, root: Path | None = None) -> dict[str, Any] | None:
    if chain_id is None:
        return None
    wanted = int(chain_id)
    return next((row for row in load_chain_data(root=root) if int(row["chain_id"]) == wanted), None)


def chain_data_by_network(network: str | None, *, root: Path | None = None) -> dict[str, Any] | None:
    wanted = _text(network).lower()
    if not wanted:
        return None
    return next((row for row in load_chain_data(root=root) if row["network"] == wanted), None)


def chain_data_files(network: str | None = None, *, chain_id: int | None = None, root: Path | None = None) -> dict[str, str]:
    row = chain_data_by_chain_id(chain_id, root=root) if chain_id is not None else chain_data_by_network(network, root=root)
    if not row:
        return {}
    return {str(key): _text(value) for key, value in _as_dict(row.get("data_files")).items() if _text(value)}


def chain_contracts(network: str | None = None, *, chain_id: int | None = None, root: Path | None = None) -> dict[str, str]:
    row = chain_data_by_chain_id(chain_id, root=root) if chain_id is not None else chain_data_by_network(network, root=root)
    if not row:
        return {}
    return {str(key): _text(value) for key, value in _as_dict(row.get("contracts")).items() if _text(value)}


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
        "features": _as_dict(row.get("features")),
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


def chain_protocol_candidate_map(*, root: Path | None = None) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for row in load_chain_data(root=root):
        protocols = row["flash_loan"]["candidate_protocols"]
        result[int(row["chain_id"])] = {
            "global_chain_id": row["global_chain_id"],
            "chain_key": row["chain_key"],
            "network": row["network"],
            "protocols": protocols,
            "protocol_count": int(row["flash_loan"]["supported_protocol_count"]),
            "data_file": row["data_file"],
        }
    return result


def chain_supported_protocols(
    network: str | None = None,
    *,
    chain_id: int | None = None,
    protocol_set: str = "candidate",
    root: Path | None = None,
) -> list[str]:
    row = chain_data_by_chain_id(chain_id, root=root) if chain_id is not None else chain_data_by_network(network, root=root)
    if not row:
        return []
    flash_loan = _as_dict(row.get("flash_loan"))
    key = "default_protocols" if str(protocol_set or "").strip().lower() == "default" else "candidate_protocols"
    return list(flash_loan.get(key) or [])


def chain_supported_protocol_count(
    network: str | None = None,
    *,
    chain_id: int | None = None,
    root: Path | None = None,
) -> int:
    row = chain_data_by_chain_id(chain_id, root=root) if chain_id is not None else chain_data_by_network(network, root=root)
    if not row:
        return 0
    return int(_as_dict(row.get("flash_loan")).get("supported_protocol_count") or 0)


def chain_protocol_inventory(*, root: Path | None = None) -> list[dict[str, Any]]:
    rows = []
    for row in load_chain_data(root=root):
        flash_loan = _as_dict(row.get("flash_loan"))
        protocols = list(flash_loan.get("candidate_protocols") or [])
        rows.append(
            {
                "network": row["network"],
                "chainId": int(row["chain_id"]),
                "globalChainId": row["global_chain_id"],
                "chainKey": row["chain_key"],
                "supportedProtocolCount": int(flash_loan.get("supported_protocol_count") or len(protocols)),
                "candidateProtocols": protocols,
                "defaultProtocols": list(flash_loan.get("default_protocols") or []),
                "dataFile": row["data_file"],
            }
        )
    return rows


def chain_default_protocol_preferences(*, root: Path | None = None) -> dict[int, list[str]]:
    return {
        int(row["chain_id"]): row["flash_loan"]["default_protocols"]
        for row in load_chain_data(root=root)
        if row["flash_loan"]["default_protocols"]
    }


def _registry_default_chain_id(defaults: dict[str, Any], key: str) -> int | None:
    default_chain_ids = _as_dict(defaults.get("default_chain_ids"))
    return _int_or_none(default_chain_ids.get(key)) or _int_or_none(defaults.get(f"{key}_chain_id"))


def default_chain_scope(feature: str, *, root: Path | None = None) -> dict[str, Any]:
    chain_root = root or CHAIN_DATA_ROOT
    normalized_feature = _text(feature).lower()
    registry = load_chain_registry(root=chain_root)
    chain_id = _registry_default_chain_id(_as_dict(registry.get("defaults")), normalized_feature)
    if chain_id is None:
        raise ValueError(f"no default chain is configured for feature {normalized_feature}")
    row = chain_data_by_chain_id(chain_id, root=chain_root)
    if not row:
        raise ValueError(f"default chain {chain_id} is not registered")
    feature = _as_dict(_as_dict(row.get("features")).get(normalized_feature))
    protocols = row["flash_loan"]["default_protocols"]
    return {
        "network": row["network"],
        "chain_id": int(row["chain_id"]),
        "market_id": _text(feature.get("default_market_id")),
        "protocol": _text(feature.get("protocol")) or (protocols[0] if protocols else ""),
    }


def default_market_id_for_feature(feature: str, *, root: Path | None = None) -> str:
    return _text(default_chain_scope(feature, root=root).get("market_id"))


def default_flash_loan_pool_scope(*, root: Path | None = None) -> dict[str, Any]:
    chain_root = root or CHAIN_DATA_ROOT
    registry = load_chain_registry(root=chain_root)
    defaults = _as_dict(registry.get("defaults"))
    chain_id = _registry_default_chain_id(defaults, "flash_loan_pool_page")
    if chain_id is None:
        for row in load_chain_data(root=chain_root):
            if _as_dict(row.get("defaults")).get("flash_loan_pool_page") is True:
                chain_id = int(row["chain_id"])
                break
    if chain_id is None:
        raise ValueError("no default chain is configured for flash_loan_pool_page")
    row = chain_data_by_chain_id(chain_id, root=chain_root)
    if not row:
        raise ValueError(f"default flash loan pool chain {chain_id} is not registered")
    feature = _as_dict(_as_dict(row.get("features")).get("dex_arbitrage"))
    protocols = row["flash_loan"]["default_protocols"]
    return {
        "network": row["network"],
        "chain_id": int(row["chain_id"]),
        "market_id": _text(feature.get("default_market_id")),
        "protocol": protocols[0] if protocols else _text(feature.get("protocol")),
    }


def processing_handler_for_chain(chain_id: int, flow: str, *, root: Path | None = None) -> str:
    flow_spec = processing_flow_for_chain(chain_id, flow, root=root)
    if flow_spec:
        handler = _text(flow_spec.get("handler"))
        if handler:
            return handler
    row = chain_data_by_chain_id(chain_id, root=root)
    if not row:
        return ""
    handlers = _as_dict(row.get("processing_handlers"))
    return _text(handlers.get(flow))


def processing_flow_for_chain(chain_id: int, flow: str, *, root: Path | None = None) -> dict[str, Any]:
    spec = chain_flow_spec_by_chain_id(chain_id, root=root)
    if not spec:
        return {}
    flows = _as_dict(spec.get("flows"))
    return _as_dict(flows.get(flow))


def chain_data_audit(*, root: Path | None = None) -> dict[str, Any]:
    chain_root = root or CHAIN_DATA_ROOT
    directories = _chain_dirs(chain_root)
    rows = load_chain_data(root=root)
    flow_specs = load_chain_flow_specs(root=root)
    data_by_network = {row["network"]: row for row in rows}
    flows_by_network = {row["network"]: row for row in flow_specs}
    issues: list[dict[str, Any]] = []
    for directory in directories:
        network = directory.name.lower()
        if not (directory / "chain.json").exists():
            issues.append({"severity": "error", "code": "chain_data_config_missing", "network": network})
        if not (directory / "flows.json").exists():
            issues.append({"severity": "error", "code": "processing_flow_spec_missing", "network": network})
    robinhood_family = {
        str(row["network"]): int(row["chain_id"])
        for row in rows
        if str(row["network"]).startswith("robinhood")
    }
    if robinhood_family.get("robinhood") == robinhood_family.get("robinhood-testnet"):
        issues.append({"severity": "error", "code": "robinhood_mainnet_testnet_chain_id_collision", "network": "robinhood"})
    for row in rows:
        flow_spec = flows_by_network.get(row["network"])
        flash_loan = _as_dict(row.get("flash_loan"))
        candidate_protocols = set(flash_loan.get("candidate_protocols") or [])
        default_protocols = set(flash_loan.get("default_protocols") or [])
        configured_count = int(flash_loan.get("supported_protocol_count") or 0)
        if flow_spec and int(flow_spec["chain_id"]) != int(row["chain_id"]):
            issues.append({"severity": "error", "code": "flow_spec_chain_id_mismatch", "network": row["network"]})
        if flow_spec:
            independence = _isolation_policy(row, flow_spec, rows)
            missing_scope_fields = sorted(
                set(independence["required_scope_fields"]) - set(independence["scope_fields"])
            )
            if missing_scope_fields:
                issues.append(
                    {
                        "severity": "error",
                        "code": "isolation_scope_fields_incomplete",
                        "network": row["network"],
                        "missing": missing_scope_fields,
                    }
                )
            if not independence["forbid_cross_chain_fallback"]:
                issues.append({"severity": "error", "code": "cross_chain_fallback_not_forbidden", "network": row["network"]})
            if not independence["forbidden_networks_complete"]:
                missing = sorted(
                    set(independence["expected_forbidden_source_networks"])
                    - set(independence["forbidden_source_networks"])
                )
                extra = sorted(
                    set(independence["forbidden_source_networks"])
                    - set(independence["expected_forbidden_source_networks"])
                )
                issues.append(
                    {
                        "severity": "error",
                        "code": "forbidden_source_networks_incomplete",
                        "network": row["network"],
                        "missing": missing,
                        "extra": extra,
                    }
                )
            if not independence["forbidden_chain_ids_complete"]:
                missing_ids = sorted(
                    set(independence["expected_forbidden_source_chain_ids"])
                    - set(independence["forbidden_source_chain_ids"])
                )
                extra_ids = sorted(
                    set(independence["forbidden_source_chain_ids"])
                    - set(independence["expected_forbidden_source_chain_ids"])
                )
                issues.append(
                    {
                        "severity": "error",
                        "code": "forbidden_source_chain_ids_incomplete",
                        "network": row["network"],
                        "missing": missing_ids,
                        "extra": extra_ids,
                    }
                )
        if not candidate_protocols:
            issues.append({"severity": "error", "code": "candidate_protocols_missing", "network": row["network"]})
        if configured_count != len(candidate_protocols):
            issues.append({"severity": "error", "code": "supported_protocol_count_mismatch", "network": row["network"]})
        for protocol in sorted(default_protocols - candidate_protocols):
            issues.append({"severity": "error", "code": f"default_protocol_not_supported:{protocol}", "network": row["network"]})
        handlers = _as_dict(row.get("processing_handlers"))
        if not handlers:
            issues.append({"severity": "error", "code": "processing_handlers_missing", "network": row["network"]})
        data_files = _as_dict(row.get("data_files"))
        if not data_files:
            issues.append({"severity": "error", "code": "data_files_missing", "network": row["network"]})
        if flow_spec:
            flows = _as_dict(flow_spec.get("flows"))
            dex_feature = _as_dict(_as_dict(row.get("features")).get("dex_arbitrage"))
            dex_protocols = candidate_protocols & DEX_CANDIDATE_PROTOCOLS
            if dex_feature and dex_protocols and "dex_pool_candidates" not in flows:
                issues.append(
                    {
                        "severity": "error",
                        "code": "dex_pool_candidates_flow_missing",
                        "network": row["network"],
                        "protocols": sorted(dex_protocols),
                    }
                )
            flow_names = set(flows)
            missing_handlers = sorted(flow_names - set(handlers))
            for flow_name in missing_handlers:
                issues.append({"severity": "error", "code": f"flow_handler_missing:{flow_name}", "network": row["network"]})
            extra_handlers = sorted(set(handlers) - flow_names)
            for flow_name in extra_handlers:
                issues.append({"severity": "error", "code": f"flow_handler_without_flow:{flow_name}", "network": row["network"]})
            for flow_name, flow_payload in flows.items():
                flow = _as_dict(flow_payload)
                if flow.get("requires_scope_match") is not True:
                    issues.append({"severity": "error", "code": f"flow_requires_scope_match_missing:{flow_name}", "network": row["network"]})
                if not _flow_handler_is_chain_owned(row["network"], flow.get("handler")):
                    issues.append(
                        {
                            "severity": "error",
                            "code": f"flow_handler_not_chain_owned:{flow_name}",
                            "network": row["network"],
                            "handler": _text(flow.get("handler")),
                            "expected_prefix": _chain_handler_prefix(row["network"]),
                        }
                    )
                if _text(flow.get("handler")) and _text(flow.get("handler")) != _text(handlers.get(flow_name)):
                    issues.append({"severity": "error", "code": f"flow_handler_mismatch:{flow_name}", "network": row["network"]})
                flow_protocols = {_text(protocol).lower() for protocol in flow.get("allowed_protocols") or [] if _text(protocol)}
                for protocol in sorted(flow_protocols - candidate_protocols):
                    issues.append({"severity": "error", "code": f"flow_protocol_not_supported:{flow_name}:{protocol}", "network": row["network"]})
                for key in flow.get("data_file_keys") or []:
                    if _text(key) and _text(key) not in data_files:
                        issues.append({"severity": "error", "code": f"flow_data_file_key_missing:{flow_name}:{key}", "network": row["network"]})
        elif (chain_root / str(row["network"]) / "flows.json").exists():
            issues.append({"severity": "error", "code": "flow_spec_unreadable", "network": row["network"]})
        if row["network"] == "robinhood-testnet":
            protocols = set(row["flash_loan"]["candidate_protocols"])
            if protocols != {"uniswap_v4"}:
                issues.append({"severity": "error", "code": "robinhood_testnet_protocol_scope_not_isolated", "network": row["network"]})
            if _as_dict(row["flash_loan"]).get("supports_aave_or_morpho_flash_loans") is not False:
                issues.append({"severity": "error", "code": "robinhood_testnet_lending_support_not_closed", "network": row["network"]})
            if not _as_dict(row.get("contracts")).get("v4_pool_manager"):
                issues.append({"severity": "error", "code": "robinhood_testnet_poolmanager_missing", "network": row["network"]})
            if flow_spec:
                flash_flow = _as_dict(_as_dict(flow_spec.get("flows")).get("flash_loan_pools"))
                allowed_protocols = {str(item).strip().lower() for item in flash_flow.get("allowed_protocols") or []}
                if allowed_protocols != {"uniswap_v4"}:
                    issues.append({"severity": "error", "code": "robinhood_testnet_flow_protocol_scope_not_isolated", "network": row["network"]})
                if flash_flow.get("requires_lending_pool") is not False:
                    issues.append({"severity": "error", "code": "robinhood_testnet_flow_requires_lending_pool", "network": row["network"]})
    for flow_spec in flow_specs:
        if flow_spec["network"] not in data_by_network:
            issues.append({"severity": "error", "code": "flow_spec_without_chain_data", "network": flow_spec["network"]})
    error_count = sum(1 for issue in issues if issue["severity"] == "error")
    independence_rows = [
        {
            "globalChainId": row["global_chain_id"],
            "network": row["network"],
            "chainId": int(row["chain_id"]),
            "data": _data_independence_summary(row, _as_dict(flows_by_network.get(row["network"])), rows),
            "processing": _processing_independence_summary(row, _as_dict(flows_by_network.get(row["network"]))),
        }
        for row in rows
    ]
    return {
        "ok": error_count == 0,
        "independenceOk": error_count == 0,
        "chainCount": len(rows),
        "flowSpecCount": len(flow_specs),
        "chainIds": [int(row["chain_id"]) for row in rows],
        "networks": [row["network"] for row in rows],
        "protocolInventory": chain_protocol_inventory(root=root),
        "independence": independence_rows,
        "chains": [
            {
                "network": row["network"],
                "chainId": int(row["chain_id"]),
                "globalChainId": row["global_chain_id"],
                "chainKey": row["chain_key"],
                "supportedProtocolCount": int(_as_dict(row.get("flash_loan")).get("supported_protocol_count") or 0),
                "supportedProtocols": list(_as_dict(row.get("flash_loan")).get("candidate_protocols") or []),
                "dataFile": row["data_file"],
                "flowFile": _as_dict(flows_by_network.get(row["network"])).get("flow_file"),
                "dataFiles": _as_dict(row.get("data_files")),
                "processingHandlers": _as_dict(row.get("processing_handlers")),
                "processingFlows": _as_dict(_as_dict(flows_by_network.get(row["network"])).get("flows")),
                "dataIndependence": _data_independence_summary(row, _as_dict(flows_by_network.get(row["network"])), rows),
                "processingIndependence": _processing_independence_summary(row, _as_dict(flows_by_network.get(row["network"]))),
            }
            for row in rows
        ],
        "issues": issues,
    }


__all__ = [
    "chain_data_audit",
    "chain_contracts",
    "chain_config_summary",
    "chain_data_by_chain_id",
    "chain_data_by_network",
    "chain_data_file_path",
    "chain_data_files",
    "chain_default_protocol_preferences",
    "default_chain_scope",
    "default_market_id_for_feature",
    "chain_flow_spec_by_chain_id",
    "chain_flow_spec_by_network",
    "chain_protocol_inventory",
    "chain_protocol_candidate_map",
    "global_chain_id",
    "chain_supported_protocol_count",
    "chain_supported_protocols",
    "default_flash_loan_pool_scope",
    "load_chain_data",
    "load_chain_flow_specs",
    "processing_flow_for_chain",
    "processing_handler_for_chain",
]
