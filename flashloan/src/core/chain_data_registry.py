from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.market_identity import canonical_market_id, normalize_feature


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


from core.chain_data_registry_summary import (
    _chain_handler_prefix,
    _data_independence_summary,
    _flow_handler_is_chain_owned,
    _isolation_policy,
    _processing_independence_summary,
    chain_config_summary,
    chain_data_file_path,
)


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


def _normalize_feature_name(feature: object) -> str:
    try:
        return normalize_feature(feature)
    except ValueError:
        return _text(feature).lower()


def _feature_scope_with_canonical_market_id(
    row: dict[str, Any],
    feature_name: str,
    feature_scope: dict[str, Any],
    *,
    protocols: list[str] | None = None,
) -> dict[str, Any]:
    payload = dict(feature_scope)
    protocol = _text(payload.get("protocol")) or ((protocols or [""])[0] if protocols else "")
    configured_market_id = _text(payload.get("default_market_id"))
    legacy_market_id = _text(payload.get("legacy_default_market_id")) or configured_market_id
    if protocol:
        canonical_id = canonical_market_id(
            feature_name,
            int(row["chain_id"]),
            protocol,
            network=row.get("network"),
        )
        payload["default_market_id"] = canonical_id
        if legacy_market_id and legacy_market_id != canonical_id:
            payload["legacy_default_market_id"] = legacy_market_id
    return payload


def _canonical_feature_scopes(row: dict[str, Any]) -> dict[str, Any]:
    protocols = list(_as_dict(row.get("flash_loan")).get("default_protocols") or [])
    result: dict[str, Any] = {}
    for raw_feature_name, raw_scope in _as_dict(row.get("features")).items():
        feature_name = _normalize_feature_name(raw_feature_name)
        result[feature_name] = _feature_scope_with_canonical_market_id(
            row,
            feature_name,
            _as_dict(raw_scope),
            protocols=protocols,
        )
    return result


def default_chain_scope(feature: str, *, root: Path | None = None) -> dict[str, Any]:
    chain_root = root or CHAIN_DATA_ROOT
    normalized_feature = _normalize_feature_name(feature)
    registry = load_chain_registry(root=chain_root)
    chain_id = _registry_default_chain_id(_as_dict(registry.get("defaults")), normalized_feature)
    if chain_id is None:
        raise ValueError(f"no default chain is configured for feature {normalized_feature}")
    row = chain_data_by_chain_id(chain_id, root=chain_root)
    if not row:
        raise ValueError(f"default chain {chain_id} is not registered")
    feature = _feature_scope_with_canonical_market_id(
        row,
        normalized_feature,
        _as_dict(_as_dict(row.get("features")).get(normalized_feature)),
        protocols=list(row["flash_loan"]["default_protocols"] or []),
    )
    protocols = row["flash_loan"]["default_protocols"]
    return {
        "network": row["network"],
        "chain_id": int(row["chain_id"]),
        "market_id": _text(feature.get("default_market_id")),
        "legacy_market_id": _text(feature.get("legacy_default_market_id")),
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
    feature = _feature_scope_with_canonical_market_id(
        row,
        "dex_arbitrage",
        _as_dict(_as_dict(row.get("features")).get("dex_arbitrage")),
        protocols=list(row["flash_loan"]["default_protocols"] or []),
    )
    protocols = row["flash_loan"]["default_protocols"]
    return {
        "network": row["network"],
        "chain_id": int(row["chain_id"]),
        "market_id": _text(feature.get("default_market_id")),
        "legacy_market_id": _text(feature.get("legacy_default_market_id")),
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


from core.chain_data_audit import chain_data_audit


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
