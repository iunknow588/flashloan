"""Chain data audit implementation kept separate from the registry lookup API."""

from __future__ import annotations

from pathlib import Path
from typing import Any


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


def _bind_chain_data_registry_globals() -> None:
    from core.chain_data_registry import (
        CHAIN_DATA_ROOT,
        DEX_CANDIDATE_PROTOCOLS,
        _as_dict,
        _chain_dirs,
        _chain_handler_prefix,
        _data_independence_summary,
        _flow_handler_is_chain_owned,
        _isolation_policy,
        _processing_independence_summary,
        _text,
        chain_protocol_inventory,
        load_chain_data,
        load_chain_flow_specs,
    )
    globals().update(
        {
            "CHAIN_DATA_ROOT": CHAIN_DATA_ROOT,
            "DEX_CANDIDATE_PROTOCOLS": DEX_CANDIDATE_PROTOCOLS,
            "_as_dict": _as_dict,
            "_chain_dirs": _chain_dirs,
            "_chain_handler_prefix": _chain_handler_prefix,
            "_data_independence_summary": _data_independence_summary,
            "_flow_handler_is_chain_owned": _flow_handler_is_chain_owned,
            "_isolation_policy": _isolation_policy,
            "_processing_independence_summary": _processing_independence_summary,
            "_text": _text,
            "chain_protocol_inventory": chain_protocol_inventory,
            "load_chain_data": load_chain_data,
            "load_chain_flow_specs": load_chain_flow_specs,
        }
    )


_bind_chain_data_registry_globals()


__all__ = ["chain_data_audit"]
