from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_by_chain_id
from core.contracts import validate_scoped_payload
from core.pool_evidence_contract import POOL_EVIDENCE_FIELDS, validate_pool_evidence_contract


APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE_ROOT = APP_ROOT / "runtime" / "flash_loan_pools"
SCHEMA_VERSION = "flash-loan-pool-static-store-v1"
POOL_SECTIONS = (
    "stable_borrow_pools",
    "non_stable_borrow_pools",
    "stable_pool_key_rows",
    "non_stable_pool_key_rows",
    "v4_pool_key_rows",
    "stable_v4_flash_source_rows",
    "non_stable_v4_flash_source_rows",
    "v4_flash_accounting_source_rows",
)


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: object) -> str:
    return str(value or "").strip()


def _protocol(value: object) -> str:
    return _text(value).lower()


def _as_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _network_for_chain(chain_id: int | None, fallback: object = "") -> str:
    if chain_id is not None:
        row = chain_data_by_chain_id(int(chain_id))
        if row and row.get("network"):
            return _text(row["network"]).lower()
    return _text(fallback).lower() or "unknown"


def flash_loan_pool_store_root(root: Path | str | None = None) -> Path:
    return Path(root) if root is not None else DEFAULT_STORE_ROOT


def flash_loan_pool_static_path(
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocol: str,
    root: Path | str | None = None,
) -> Path:
    normalized_chain_id = _as_int(chain_id)
    normalized_network = _network_for_chain(normalized_chain_id, network)
    normalized_protocol = _protocol(protocol) or "unknown"
    chain_part = str(normalized_chain_id) if normalized_chain_id is not None else "no-chain-id"
    return flash_loan_pool_store_root(root) / normalized_network / chain_part / normalized_protocol / "pools.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_flash_loan_pool_static(
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocol: str,
    root: Path | str | None = None,
    expected_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = flash_loan_pool_static_path(network=network, chain_id=chain_id, protocol=protocol, root=root)
    payload = _read_json(path)
    if payload:
        if expected_scope is not None:
            if isinstance(payload.get("scope"), dict):
                validate_scoped_payload({"scope": payload["scope"]}, expected_scope)
            embedded = {
                key: payload.get(key)
                for key in ("market_id", "chain_id", "network", "protocol")
            }
            if all(embedded.get(key) not in (None, "") for key in ("market_id", "chain_id", "network", "protocol")):
                validate_scoped_payload(embedded, expected_scope)
            validate_scoped_payload(payload, expected_scope)
        return {**payload, "path": str(path), "exists": True}
    evidence = {
        "ok": False,
        "schema_version": SCHEMA_VERSION,
        "path": str(path),
        "exists": False,
        "network": _network_for_chain(_as_int(chain_id), network),
        "chain_id": _as_int(chain_id),
        "protocol": _protocol(protocol),
        "summary": {},
        "sections": {},
        "protocol_view": {},
    }
    valid, contract_reasons = validate_pool_evidence_contract(evidence)
    evidence["contract_status"] = "valid" if valid else "invalid"
    if not valid:
        evidence["contract_blocked_reasons"] = list(contract_reasons)
        for field in POOL_EVIDENCE_FIELDS:
            value = evidence.get(field)
            if isinstance(value, dict):
                value["allowed"] = False
                value["blocked_reasons"] = sorted(
                    set(value.get("blocked_reasons") or []) | {"pool_evidence_contract_invalid"}
                )
    return evidence


def load_chain_flash_loan_pool_static(
    *,
    network: str | None = None,
    chain_id: int | None = None,
    root: Path | str | None = None,
) -> dict[str, Any]:
    normalized_chain_id = _as_int(chain_id)
    normalized_network = _network_for_chain(normalized_chain_id, network)
    chain_part = str(normalized_chain_id) if normalized_chain_id is not None else "no-chain-id"
    base = flash_loan_pool_store_root(root) / normalized_network / chain_part
    snapshots = []
    for path in sorted(base.glob("*/pools.json")) if base.exists() else []:
        payload = _read_json(path)
        if payload:
            snapshots.append({**payload, "path": str(path), "exists": True})
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "network": normalized_network,
        "chain_id": normalized_chain_id,
        "path": str(base),
        "protocol_count": len(snapshots),
        "protocols": snapshots,
    }


def _row_identity(row: dict[str, Any]) -> str:
    for key in (
        "pool_id",
        "pool",
        "pool_identifier",
        "provider_address",
        "pool_manager_address",
        "token_address",
        "currency_address",
    ):
        value = _text(row.get(key))
        if value:
            return f"{key}:{value.lower()}"
    symbol = _text(row.get("token_symbol") or row.get("symbolPair") or row.get("symbol_pair")).upper()
    protocol = _protocol(row.get("protocol"))
    return f"symbol:{protocol}:{symbol}" if symbol else json.dumps(row, sort_keys=True, ensure_ascii=False)


def _merge_rows(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in [*existing, *incoming]:
        if not isinstance(row, dict):
            continue
        key = _row_identity(row)
        if key not in merged:
            order.append(key)
        merged[key] = {**merged.get(key, {}), **row}
    return [merged[key] for key in order]


def _sections_from_view(view: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {
        section: [row for row in view.get(section) or [] if isinstance(row, dict)]
        for section in POOL_SECTIONS
        if isinstance(view.get(section), list)
    }


def _section_counts(sections: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    return {section: len(rows) for section, rows in sections.items()}


def _positive_number(value: object) -> bool:
    if value is None or value == "":
        return False
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return False
    return parsed > 0 and parsed == parsed and parsed not in (float("inf"), float("-inf"))


def _evidence_rows(view: dict[str, Any], sections: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for section_rows in sections.values():
        rows.extend(section_rows)
    for section in POOL_SECTIONS:
        rows.extend(row for row in view.get(section) or [] if isinstance(row, dict))
    return rows


def pool_evidence_from_view(
    view: dict[str, Any],
    *,
    sections: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    sections = sections if sections is not None else _sections_from_view(view)
    rows = _evidence_rows(view, sections)
    cache = view.get("cache") if isinstance(view.get("cache"), dict) else {}
    summary = view.get("summary") if isinstance(view.get("summary"), dict) else {}
    capabilities = cache.get("capabilities") if isinstance(cache.get("capabilities"), dict) else {}
    liquidity_reasons: list[str] = []
    if not rows:
        liquidity_reasons.append("no_pool_rows")
    elif not any(
        _positive_number(row.get(key))
        for row in rows
        for key in (
            "available_liquidity",
            "reserve_data_liquidity",
            "a_token_total_supply",
            "liquidity",
            "tvlUsd",
            "depth_score_usd",
            "pool_liquidity_units",
            "pool_manager_balance0",
            "pool_manager_balance1",
        )
    ):
        liquidity_reasons.append("no_positive_liquidity_evidence")
    liquidity_blocked = bool(liquidity_reasons)

    marketability_reasons: list[str] = []
    borrow_target_count = int(summary.get("borrow_target_count") or 0)
    borrow_asset_count = int(summary.get("borrow_asset_count") or 0)
    if not rows:
        marketability_reasons.append("no_borrow_pools")
    elif borrow_target_count <= 0 and borrow_asset_count <= 0:
        marketability_reasons.append("no_borrow_targets")
    marketability_blocked = bool(marketability_reasons)

    profitability_reasons = [
        "profitability_requires_live_quoter",
    ]
    executability_reasons: list[str]
    if bool(capabilities.get("execution_enabled")):
        executability_reasons = []
    else:
        executability_reasons = ["execution_enabled_false_or_missing"]

    def _field(status: str, allowed: bool, reasons: list[str]) -> dict[str, Any]:
        return {"status": status, "allowed": allowed, "blocked_reasons": sorted(reasons)}

    return {
        "liquidity": _field("blocked" if liquidity_blocked else "ready", not liquidity_blocked, liquidity_reasons),
        "marketability": _field(
            "blocked" if marketability_blocked else "ready",
            not marketability_blocked,
            marketability_reasons,
        ),
        "profitability": _field("requires_live_quoter", False, profitability_reasons),
        "executability": _field(
            "blocked" if executability_reasons else "ready",
            not executability_reasons,
            executability_reasons,
        ),
    }


def static_snapshot_from_protocol_view(
    view: dict[str, Any],
    *,
    network: str | None = None,
    chain_id: int | None = None,
    refresh_mode: str = "incremental",
) -> dict[str, Any]:
    cache = view.get("cache") if isinstance(view.get("cache"), dict) else {}
    normalized_chain_id = _as_int(chain_id if chain_id is not None else cache.get("chain_id"))
    normalized_network = _network_for_chain(normalized_chain_id, network or cache.get("network"))
    protocol = _protocol(view.get("protocol"))
    sections = _sections_from_view(view)
    generated_at = _stamp()
    return {
        "ok": "status" not in view,
        "schema_version": SCHEMA_VERSION,
        "pool_evidence_schema_version": "flash-loan-pool-evidence-v1",
        "network": normalized_network,
        "chain_id": normalized_chain_id,
        "protocol": protocol,
        "display_name": view.get("display_name") or protocol,
        "generated_at": generated_at,
        "pool_evidence_observed_at": generated_at,
        "refresh_mode": refresh_mode,
        "source": cache.get("source") or view.get("source") or "flash_loan_pools_payload",
        "summary": dict(view.get("summary") if isinstance(view.get("summary"), dict) else {}),
        "cache": cache,
        "sections": sections,
        "section_counts": _section_counts(sections),
        "borrow_target_universe": view.get("borrow_target_universe"),
        "chain_isolation": view.get("chainIsolation") if isinstance(view.get("chainIsolation"), dict) else {},
        "pool_evidence": pool_evidence_from_view(view, sections=sections),
        "protocol_view": view,
    }


def merge_flash_loan_pool_static(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    existing_sections = existing.get("sections") if isinstance(existing.get("sections"), dict) else {}
    incoming_sections = incoming.get("sections") if isinstance(incoming.get("sections"), dict) else {}
    sections: dict[str, list[dict[str, Any]]] = {}
    for section in sorted(set(existing_sections) | set(incoming_sections)):
        sections[section] = _merge_rows(
            [row for row in existing_sections.get(section) or [] if isinstance(row, dict)],
            [row for row in incoming_sections.get(section) or [] if isinstance(row, dict)],
        )
    merged = {
        **existing,
        **incoming,
        "first_generated_at": existing.get("first_generated_at") or existing.get("generated_at") or incoming.get("generated_at"),
        "generated_at": _stamp(),
        "refresh_mode": "incremental",
        "previous_path": existing.get("path"),
        "sections": sections,
        "section_counts": _section_counts(sections),
    }
    return merged


def write_flash_loan_pool_static(
    view: dict[str, Any],
    *,
    network: str | None = None,
    chain_id: int | None = None,
    refresh_mode: str = "incremental",
    root: Path | str | None = None,
    expected_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    incoming = static_snapshot_from_protocol_view(
        view,
        network=network,
        chain_id=chain_id,
        refresh_mode=refresh_mode,
    )
    path = flash_loan_pool_static_path(
        network=incoming["network"],
        chain_id=incoming["chain_id"],
        protocol=incoming["protocol"],
        root=root,
    )
    if refresh_mode == "full":
        stored = incoming
    else:
        existing = _read_json(path)
        stored = merge_flash_loan_pool_static(existing, incoming) if existing else incoming
    stored = {**stored, "path": str(path), "exists": True}
    if expected_scope is not None:
        embedded = {
            "market_id": stored.get("market_id") or expected_scope["market_id"],
            "chain_id": stored.get("chain_id") if stored.get("chain_id") is not None else expected_scope["chain_id"],
            "network": stored.get("network") or expected_scope["network"],
            "protocol": stored.get("protocol") or expected_scope["protocol"],
        }
        validate_scoped_payload(embedded, expected_scope)
        stored["market_id"] = str(expected_scope["market_id"])
        stored["scope"] = dict(expected_scope)
    _write_json(path, stored)
    return stored


def static_store_metadata(
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocol: str,
    root: Path | str | None = None,
    include_protocol_view: bool = False,
) -> dict[str, Any]:
    payload = load_flash_loan_pool_static(network=network, chain_id=chain_id, protocol=protocol, root=root)
    metadata = {
        "path": payload["path"],
        "exists": bool(payload.get("exists")),
        "network": payload.get("network"),
        "chain_id": payload.get("chain_id"),
        "protocol": payload.get("protocol"),
        "generated_at": payload.get("generated_at"),
        "refresh_mode": payload.get("refresh_mode"),
        "section_counts": payload.get("section_counts") or {},
        "pool_evidence_schema_version": payload.get("pool_evidence_schema_version"),
        "pool_evidence_observed_at": payload.get("generated_at"),
        "pool_evidence": payload.get("pool_evidence") or {},
    }
    if include_protocol_view:
        metadata["protocol_view"] = payload.get("protocol_view") or {}
        metadata["summary"] = payload.get("summary") or {}
        metadata["sections"] = payload.get("sections") or {}
        metadata["borrow_target_universe"] = payload.get("borrow_target_universe")
        metadata["cache"] = payload.get("cache") or {}
    return metadata


__all__ = [
    "DEFAULT_STORE_ROOT",
    "SCHEMA_VERSION",
    "flash_loan_pool_static_path",
    "flash_loan_pool_store_root",
    "load_chain_flash_loan_pool_static",
    "load_flash_loan_pool_static",
    "merge_flash_loan_pool_static",
    "pool_evidence_from_view",
    "static_snapshot_from_protocol_view",
    "static_store_metadata",
    "write_flash_loan_pool_static",
]
