"""Seed and read global chain tables."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from core.chain_data_registry import chain_data_by_chain_id, chain_data_by_network
from core.db.storage_business_io import execute, query, query_one, replace_rows
from core.db.storage_business_schema import ensure_business_schema
from core.db.token_authority import (
    require_legacy_token_write,
    require_manager_projection,
)
from core.local_kv import store_path
from core.market_profiles import BUILTIN_MARKET_SUMMARIES


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: object, default: str = "") -> str:
    return str(value or default).strip()


def _int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def ensure_global_chain_tables(database_url: str) -> None:
    ensure_business_schema(database_url)


def seed_chain_registry_meta(database_url: str) -> None:
    payload = {
        "schema_version": "1.0",
        "primary_key": "chain_id",
        "registry_file": "src/chains/registry.json",
        "defaults": {"default_chain_ids": {"dex_arbitrage": 4663, "flash_loan_pool_page": 4663, "liquidation": 4663}},
        "updated_at": _stamp(),
    }
    execute(
        database_url,
        """
        INSERT INTO chain_registry_meta (
            id, schema_version, primary_key, registry_file, defaults_json, updated_at
        ) VALUES (1, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            schema_version = excluded.schema_version,
            registry_file = excluded.registry_file,
            defaults_json = excluded.defaults_json,
            updated_at = excluded.updated_at
        """,
        (
            payload["schema_version"],
            payload["primary_key"],
            payload["registry_file"],
            payload["defaults"],
            payload["updated_at"],
        ),
    )


def seed_chain_registry(database_url: str, chain_rows: list[dict[str, Any]]) -> None:
    rows = []
    now = _stamp()
    for row in chain_rows:
        rows.append(
            (
                _int(row.get("chain_id")),
                _text(row.get("network") or row.get("network_name")).lower(),
                _text(row.get("directory") or row.get("network_name")).lower(),
                _text(row.get("chain_file")),
                _text(row.get("flow_file")),
                _text(row.get("registry_file")),
                1 if bool(row.get("is_enabled", True)) else 0,
                now,
                now,
            )
        )
    if rows:
        replace_rows(
            database_url,
            """
            INSERT INTO chain_registry (
                chain_id, network, directory, chain_file, flow_file,
                registry_file, is_enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chain_id) DO UPDATE SET
                network = excluded.network,
                directory = excluded.directory,
                chain_file = excluded.chain_file,
                flow_file = excluded.flow_file,
                registry_file = excluded.registry_file,
                is_enabled = excluded.is_enabled,
                updated_at = excluded.updated_at
            """,
            rows,
        )


def seed_chain_tokens(
    database_url: str,
    *,
    network: str = "robinhood",
    chain_id: int = 4663,
    token_rows: list[dict[str, Any]],
) -> None:
    require_legacy_token_write("chain_tokens.seed", database_url)
    addresses: list[str] = []
    for token in token_rows:
        address = _text(token.get("token_address") or token.get("address")).lower()
        if address and address not in addresses:
            addresses.append(address)
    existing_metadata: dict[str, dict[str, Any]] = {}
    existing_source: dict[str, dict[str, Any]] = {}
    if addresses:
        placeholders = ",".join("?" for _ in addresses)
        for row in query(
            database_url,
            f"""
            SELECT token_address, token_metadata_json, source_json
            FROM chain_tokens
            WHERE network = ? AND chain_id = ?
              AND lower(token_address) IN ({placeholders})
            """,
            (network, chain_id, *addresses),
        ):
            key = _text(row.get("token_address")).lower()
            existing_metadata[key] = _json_loads(row.get("token_metadata_json"))
            existing_source[key] = _json_loads(row.get("source_json"))
    rows = []
    now = _stamp()
    for token in token_rows:
        address = _text(token.get("token_address") or token.get("address")).lower()
        symbol = _text(token.get("token_symbol") or token.get("symbol"))
        if not address or not symbol:
            continue
        metadata = _json_loads(
            token.get("token_metadata")
            or token.get("metadata")
            or token.get("token_metadata_json")
            or {}
        )
        existing_meta = existing_metadata.get(address) or {}
        if not str(metadata.get("category") or "").strip():
            for keep_key, keep_value in existing_meta.items():
                if keep_value in (None, ""):
                    continue
                current = metadata.get(keep_key)
                if current in (None, ""):
                    metadata[keep_key] = keep_value
        source = _json_loads(token.get("source") or {})
        if not source:
            source = dict(existing_source.get(address) or {})
        rows.append(
            (
                network,
                chain_id,
                address,
                symbol,
                token.get("token_name") or token.get("name"),
                token.get("token_decimals") or token.get("decimals"),
                _text(token.get("token_kind") or "erc20"),
                1 if bool(token.get("is_native")) else 0,
                1 if bool(token.get("is_wrapped_native")) else 0,
                1 if bool(token.get("is_stable")) else 0,
                token.get("anchor_symbol"),
                _json_dumps(metadata),
                _json_dumps(source),
                now,
                now,
            )
        )
    if rows:
        replace_rows(
            database_url,
            """
            INSERT INTO chain_tokens (
                network, chain_id, token_address, token_symbol, token_name,
                token_decimals, token_kind, is_native, is_wrapped_native,
                is_stable, anchor_symbol, token_metadata_json, source_json,
                first_seen_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, token_address) DO UPDATE SET
                token_symbol = excluded.token_symbol,
                token_name = excluded.token_name,
                token_decimals = excluded.token_decimals,
                token_kind = excluded.token_kind,
                is_native = excluded.is_native,
                is_wrapped_native = excluded.is_wrapped_native,
                is_stable = excluded.is_stable,
                anchor_symbol = excluded.anchor_symbol,
                token_metadata_json = excluded.token_metadata_json,
                source_json = excluded.source_json,
                updated_at = excluded.updated_at
            """,
            rows,
        )
        from core.db.storage_dex_surface_snapshot import (
            clear_dex_surface_snapshot_fingerprint_cache,
        )

        clear_dex_surface_snapshot_fingerprint_cache(
            network=network,
            chain_id=chain_id,
        )


_PROJECTION_LIBRARY_PRIORITY = {
    "core": 0,
    "stocks": 1,
    "test": 2,
    "extension": 3,
}


def _projection_checksum(rows: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        rows,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def project_chain_tokens_from_manager(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    dry_run: bool,
) -> dict[str, Any]:
    """Publish Token Manager rows as a non-destructive compatibility projection.

    Only ``admitted`` and ``candidate_only`` tokens are projected. Blocked and
    pending-review rows remain invisible to Python consumers. Existing
    ``chain_tokens`` rows are never deleted because legacy pool rows may still
    reference them during the migration window.
    """

    if not isinstance(dry_run, bool):
        raise ValueError("dryRun must be an explicit Boolean")
    require_manager_projection("chain_tokens.project_from_manager", database_url)
    rows = query(
        database_url,
        """
        SELECT library_key, network, chain_id, token_key_id, v4_currency_key,
               token_address, token_symbol, token_name, token_decimals,
               token_kind, is_native, is_wrapped_native, is_stable,
               admission_status, admission_source, metadata_source,
               metadata_checksum, source_json, first_seen_at, updated_at,
               blocked_reason
        FROM token_library_catalog
        WHERE network = ? AND chain_id = ?
        ORDER BY token_key_id, library_key
        """,
        (network, chain_id),
    )
    state_rows = query(
        database_url,
        """
        SELECT library_key, library_version, metadata_checksum
        FROM token_library_state
        WHERE network = ? AND chain_id = ?
        """,
        (network, chain_id),
    )
    library_state = {str(row["library_key"]): row for row in state_rows}
    excluded_counts: dict[str, int] = {}
    excluded_addresses: set[str] = set()
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        status = _text(row.get("admission_status"))
        if status not in {"admitted", "candidate_only"}:
            excluded_counts[status or "unknown"] = (
                excluded_counts.get(status or "unknown", 0) + 1
            )
            excluded_address = _text(row.get("token_address")).lower()
            if excluded_address:
                excluded_addresses.add(excluded_address)
            continue
        token_key_id = _text(row.get("token_key_id"))
        current = selected.get(token_key_id)
        current_priority = (
            _PROJECTION_LIBRARY_PRIORITY.get(
                _text(current.get("library_key")),
                len(_PROJECTION_LIBRARY_PRIORITY),
            )
            if current
            else len(_PROJECTION_LIBRARY_PRIORITY)
        )
        priority = _PROJECTION_LIBRARY_PRIORITY.get(
            _text(row.get("library_key")),
            len(_PROJECTION_LIBRARY_PRIORITY),
        )
        if current is None or priority < current_priority:
            selected[token_key_id] = row

    addresses = sorted(_text(row.get("token_address")).lower() for row in selected.values())
    existing_rows: dict[str, dict[str, Any]] = {}
    if addresses:
        placeholders = ",".join("?" for _ in addresses)
        for row in query(
            database_url,
            f"""
            SELECT token_address, token_symbol, token_name, token_decimals,
                   token_kind, is_native, is_wrapped_native, is_stable,
                   anchor_symbol, token_metadata_json, source_json,
                   first_seen_at, updated_at
            FROM chain_tokens
            WHERE network = ? AND chain_id = ?
              AND lower(token_address) IN ({placeholders})
            """,
            (network, chain_id, *addresses),
        ):
            existing_rows[_text(row.get("token_address")).lower()] = row

    excluded_legacy_rows: list[str] = []
    if excluded_addresses:
        excluded = sorted(excluded_addresses)
        placeholders = ",".join("?" for _ in excluded)
        excluded_legacy_rows = [
            _text(row.get("token_address")).lower()
            for row in query(
                database_url,
                f"""
                SELECT token_address
                FROM chain_tokens
                WHERE network = ? AND chain_id = ?
                  AND lower(token_address) IN ({placeholders})
                ORDER BY token_address
                """,
                (network, chain_id, *excluded),
            )
        ]
    if not dry_run and excluded_legacy_rows:
        raise RuntimeError(
            "TOKEN_PROJECTION_EXCLUDED_ROWS_PRESENT: "
            "blocked/pending manager tokens still exist in the legacy "
            f"chain_tokens projection: {', '.join(excluded_legacy_rows[:20])}"
        )

    now = _stamp()
    projected_rows: list[tuple[Any, ...]] = []
    canonical_rows: list[dict[str, Any]] = []
    changed_rows = 0
    for token_key_id in sorted(selected):
        manager_row = selected[token_key_id]
        address = _text(manager_row.get("token_address")).lower()
        existing = existing_rows.get(address) or {}
        symbol = _text(manager_row.get("token_symbol"))
        token_name = _text(manager_row.get("token_name")) or symbol
        library_key = _text(manager_row.get("library_key"))
        state = library_state.get(library_key) or {}
        manager_source = _json_loads(manager_row.get("source_json"))
        source = _json_loads(existing.get("source_json"))
        source.update(manager_source)
        source["managerProjection"] = {
            "libraryKey": library_key,
            "libraryVersion": state.get("library_version"),
            "libraryChecksum": state.get("metadata_checksum"),
            "tokenKeyId": token_key_id,
            "v4CurrencyKey": _text(manager_row.get("v4_currency_key")),
            "admissionStatus": _text(manager_row.get("admission_status")),
            "admissionSource": _text(manager_row.get("admission_source")),
            "metadataSource": _text(manager_row.get("metadata_source")),
            "metadataChecksum": _text(manager_row.get("metadata_checksum")),
        }
        metadata = _json_loads(existing.get("token_metadata_json"))
        metadata.update(
            {
                "symbol": symbol,
                "name": token_name,
                "decimals": _int(manager_row.get("token_decimals")),
                "source": _text(manager_row.get("metadata_source")),
            }
        )
        if library_key == "stocks":
            metadata["category"] = "robinhood_stock"
        metadata["managerProjection"] = source["managerProjection"]
        first_seen_at = (
            _text(existing.get("first_seen_at"))
            or _text(manager_row.get("first_seen_at"))
            or now
        )
        updated_at = _text(manager_row.get("updated_at")) or now
        values = {
            "network": network,
            "chain_id": chain_id,
            "token_address": address,
            "token_symbol": symbol,
            "token_name": token_name,
            "token_decimals": _int(manager_row.get("token_decimals")),
            "token_kind": _text(manager_row.get("token_kind")) or "erc20",
            "is_native": 1 if bool(manager_row.get("is_native")) else 0,
            "is_wrapped_native": (
                1 if bool(manager_row.get("is_wrapped_native")) else 0
            ),
            "is_stable": 1 if bool(manager_row.get("is_stable")) else 0,
            "anchor_symbol": existing.get("anchor_symbol"),
            "token_metadata_json": _json_dumps(metadata),
            "source_json": _json_dumps(source),
            "first_seen_at": first_seen_at,
            "updated_at": updated_at,
        }
        canonical_rows.append(values)
        if existing:
            comparable = (
                "token_symbol",
                "token_name",
                "token_decimals",
                "token_kind",
                "is_native",
                "is_wrapped_native",
                "is_stable",
                "token_metadata_json",
                "source_json",
                "updated_at",
            )
            if any(existing.get(key) != values[key] for key in comparable):
                changed_rows += 1
        else:
            changed_rows += 1
        projected_rows.append(
            (
                values["network"],
                values["chain_id"],
                values["token_address"],
                values["token_symbol"],
                values["token_name"],
                values["token_decimals"],
                values["token_kind"],
                values["is_native"],
                values["is_wrapped_native"],
                values["is_stable"],
                values["anchor_symbol"],
                values["token_metadata_json"],
                values["source_json"],
                values["first_seen_at"],
                values["updated_at"],
            )
        )

    checksum = _projection_checksum(canonical_rows)
    if not dry_run and projected_rows:
        replace_rows(
            database_url,
            """
            INSERT INTO chain_tokens (
                network, chain_id, token_address, token_symbol, token_name,
                token_decimals, token_kind, is_native, is_wrapped_native,
                is_stable, anchor_symbol, token_metadata_json, source_json,
                first_seen_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, chain_id, token_address) DO UPDATE SET
                token_symbol = excluded.token_symbol,
                token_name = excluded.token_name,
                token_decimals = excluded.token_decimals,
                token_kind = excluded.token_kind,
                is_native = excluded.is_native,
                is_wrapped_native = excluded.is_wrapped_native,
                is_stable = excluded.is_stable,
                token_metadata_json = excluded.token_metadata_json,
                source_json = excluded.source_json,
                updated_at = excluded.updated_at
            """,
            projected_rows,
        )
        from core.db.storage_dex_surface_snapshot import (
            clear_dex_surface_snapshot_fingerprint_cache,
        )

        clear_dex_surface_snapshot_fingerprint_cache(
            network=network,
            chain_id=chain_id,
        )
    return {
        "authorityMode": "manager",
        "dryRun": dry_run,
        "network": network,
        "chainId": chain_id,
        "sourceRows": len(rows),
        "projectedRows": len(projected_rows),
        "changedRows": changed_rows,
        "extensionRowsDeduplicated": len(
            [
                row
                for row in rows
                if _text(row.get("library_key")) == "extension"
                and _text(row.get("token_key_id")) in selected
                and _text(
                    (selected[_text(row.get("token_key_id"))] or {}).get("library_key")
                )
                != "extension"
            ]
        ),
        "excludedStatusCounts": dict(sorted(excluded_counts.items())),
        "legacyRowsForExcludedStatus": len(excluded_legacy_rows),
        "readyForProjection": not excluded_legacy_rows,
        "checksum": checksum,
        "nonDestructive": True,
    }


def load_chain_tokens(
    database_url: str,
    *,
    network: str,
    chain_id: int,
) -> list[dict[str, Any]]:
    return query(
        database_url,
        """
        SELECT network, chain_id, token_address, token_symbol, token_name,
               token_decimals, token_kind, is_native, is_wrapped_native,
               is_stable, anchor_symbol, token_metadata_json, source_json,
               first_seen_at, updated_at
        FROM chain_tokens
        WHERE network = ? AND chain_id = ?
        ORDER BY token_address
        """,
        (network, chain_id),
    )


def seed_chain_contracts(database_url: str, *, network: str, chain_id: int, contracts: dict[str, Any]) -> None:
    rows = []
    now = _stamp()
    for key, raw_address in contracts.items():
        address = _text(raw_address).lower()
        if not address:
            continue
        rows.append((network, chain_id, key, address, 1 if address == "0x0000000000000000000000000000000000000000" else 0, now))
    if rows:
        replace_rows(
            database_url,
            """
            INSERT INTO chain_contracts (
                network, chain_id, contract_key, contract_address, is_zero_address, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(network, contract_key) DO UPDATE SET
                contract_address = excluded.contract_address,
                is_zero_address = excluded.is_zero_address,
                updated_at = excluded.updated_at
            """,
            rows,
        )


def load_chain_contract(database_url: str, *, network: str, contract_key: str) -> dict[str, Any] | None:
    return query_one(
        database_url,
        """
        SELECT network, chain_id, contract_key, contract_address, is_zero_address, updated_at
        FROM chain_contracts
        WHERE network = ? AND contract_key = ?
        """,
        (network, contract_key),
    )


def seed_chain_markets_from_builtin(database_url: str, *, network: str, chain_id: int) -> None:
    rows = []
    now = _stamp()
    for market_id, summary in BUILTIN_MARKET_SUMMARIES.items():
        row = dict(summary)
        if int(row.get("chain_id") or -1) != chain_id:
            continue
        if str(row.get("network") or "").strip().lower() != network:
            continue
        row["market_id"] = market_id
        row["network"] = network
        row["chain_id"] = chain_id
        rows.append(
            (
                row.get("market_id"),
                row.get("canonical_market_id") or row.get("market_id"),
                row.get("legacy_market_id"),
                network,
                chain_id,
                _text(row.get("feature")),
                _text(row.get("protocol")),
                row.get("protocol_id"),
                row.get("display_name"),
                row.get("market_type"),
                row.get("native_symbol"),
                1 if bool(row.get("observation_only")) else 0,
                1 if bool(row.get("protocol_supported", True)) else 0,
                1 if bool(row.get("chain_default")) else 0,
                1 if bool(row.get("market_default")) else 0,
                _json_dumps(row.get("rpc_env_prefixes") or []),
                row.get("migration_type"),
                _json_dumps(row.get("market_config") or {}),
                now,
                now,
            )
        )
    if rows:
        replace_rows(
            database_url,
            """
            INSERT INTO chain_markets (
                market_id, canonical_market_id, legacy_market_id, network,
                chain_id, feature, protocol, protocol_id, display_name,
                market_type, native_symbol, observation_only,
                protocol_supported, chain_default, market_default,
                rpc_env_prefixes_json, migration_type, market_config_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(market_id) DO UPDATE SET
                canonical_market_id = excluded.canonical_market_id,
                legacy_market_id = excluded.legacy_market_id,
                network = excluded.network,
                chain_id = excluded.chain_id,
                feature = excluded.feature,
                protocol = excluded.protocol,
                protocol_id = excluded.protocol_id,
                display_name = excluded.display_name,
                market_type = excluded.market_type,
                native_symbol = excluded.native_symbol,
                observation_only = excluded.observation_only,
                protocol_supported = excluded.protocol_supported,
                chain_default = excluded.chain_default,
                market_default = excluded.market_default,
                rpc_env_prefixes_json = excluded.rpc_env_prefixes_json,
                migration_type = excluded.migration_type,
                market_config_json = excluded.market_config_json,
                updated_at = excluded.updated_at
            """,
            rows,
        )


def load_chain_markets(database_url: str, *, network: str, chain_id: int) -> list[dict[str, Any]]:
    return query(
        database_url,
        """
        SELECT market_id, canonical_market_id, legacy_market_id, network,
               chain_id, feature, protocol, protocol_id, display_name,
               market_type, native_symbol, observation_only,
               protocol_supported, chain_default, market_default,
               rpc_env_prefixes_json, migration_type, market_config_json,
               created_at, updated_at
        FROM chain_markets
        WHERE network = ? AND chain_id = ?
        ORDER BY market_id
        """,
        (network, chain_id),
    )


def load_chain_info(database_url: str, *, network: str, chain_id: int) -> dict[str, Any] | None:
    return query_one(
        database_url,
        """
        SELECT network, chain_id, global_chain_id, chain_key, schema_version,
               display_name, native_symbol, rpc_url, explorer_url, rpc_json,
               protocol_mask, protocols_json, default_protocols_json,
               features_json, flash_loan_json, contracts_json,
               processing_handlers_json, data_files_json, market_ids_json,
               default_market_id, chain_file, flow_file, data_file,
               ui_payload_json, checksum, updated_at
        FROM chain_info
        WHERE network = ? AND chain_id = ?
        """,
        (network, chain_id),
    )


def seed_chain_info(database_url: str, *, network: str, chain_id: int, payload: dict[str, Any]) -> None:
    now = _stamp()
    execute(
        database_url,
        """
        INSERT INTO chain_info (
            network, chain_id, global_chain_id, chain_key, schema_version,
            display_name, native_symbol, rpc_url, explorer_url, rpc_json,
            protocol_mask, protocols_json, default_protocols_json,
            features_json, flash_loan_json, contracts_json,
            processing_handlers_json, data_files_json, market_ids_json,
            default_market_id, chain_file, flow_file, data_file,
            ui_payload_json, checksum, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(network) DO UPDATE SET
            chain_id = excluded.chain_id,
            global_chain_id = excluded.global_chain_id,
            chain_key = excluded.chain_key,
            schema_version = excluded.schema_version,
            display_name = excluded.display_name,
            native_symbol = excluded.native_symbol,
            rpc_url = excluded.rpc_url,
            explorer_url = excluded.explorer_url,
            rpc_json = excluded.rpc_json,
            protocol_mask = excluded.protocol_mask,
            protocols_json = excluded.protocols_json,
            default_protocols_json = excluded.default_protocols_json,
            features_json = excluded.features_json,
            flash_loan_json = excluded.flash_loan_json,
            contracts_json = excluded.contracts_json,
            processing_handlers_json = excluded.processing_handlers_json,
            data_files_json = excluded.data_files_json,
            market_ids_json = excluded.market_ids_json,
            default_market_id = excluded.default_market_id,
            chain_file = excluded.chain_file,
            flow_file = excluded.flow_file,
            data_file = excluded.data_file,
            ui_payload_json = excluded.ui_payload_json,
            checksum = excluded.checksum,
            updated_at = excluded.updated_at
        """,
        (
            network,
            chain_id,
            _text(payload.get("global_chain_id") or f"chain:{chain_id}"),
            _text(payload.get("chain_key") or f"chain:{chain_id}"),
            payload.get("schema_version"),
            payload.get("display_name"),
            payload.get("native_symbol"),
            payload.get("rpc_url"),
            payload.get("explorer_url"),
            _json_dumps(payload.get("rpc") or {}),
            _int(payload.get("protocol_mask"), 1),
            _json_dumps(payload.get("protocols") or []),
            _json_dumps(payload.get("default_protocols") or []),
            _json_dumps(payload.get("features") or {}),
            _json_dumps(payload.get("flash_loan") or {}),
            _json_dumps(payload.get("contracts") or {}),
            _json_dumps(payload.get("processing_handlers") or {}),
            _json_dumps(payload.get("data_files") or {}),
            _json_dumps(payload.get("market_ids") or []),
            payload.get("default_market_id"),
            payload.get("chain_file"),
            payload.get("flow_file"),
            payload.get("data_file"),
            _json_dumps(payload.get("ui_payload") or {}),
            payload.get("checksum"),
            now,
        ),
    )


def seed_chain_info_from_registry(
    database_url: str,
    *,
    network: str,
    chain_id: int | None = None,
) -> None:
    """Seed chain_info from the local chain registry before evidence imports."""

    row = chain_data_by_network(network) or {}
    if not isinstance(row, dict) or not row:
        raise ValueError(f"chain_data_by_network({network}) returned no chain data")
    resolved_chain_id = _int(row.get("chain_id") or row.get("chainId"))
    if resolved_chain_id <= 0:
        raise ValueError(f"chain_data_by_network({network}) missing chain_id")
    if chain_id is not None and int(chain_id) != resolved_chain_id:
        raise ValueError(
            f"chain_info registry chain_id={resolved_chain_id} does not match requested {chain_id}"
        )
    seed_chain_info(
        database_url,
        network=str(row.get("network") or network).lower(),
        chain_id=resolved_chain_id,
        payload=row,
    )


def upsert_initialization(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    domain: str,
    status: str,
    completeness: dict[str, Any],
    error: dict[str, Any] | None = None,
) -> None:
    now = _stamp()
    execute(
        database_url,
        """
        INSERT INTO chain_business_initialization (
            network, chain_id, protocol, domain, status,
            completeness_json, error_json, initialized_at, refreshed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(network, chain_id, protocol, domain) DO UPDATE SET
            status = excluded.status,
            completeness_json = excluded.completeness_json,
            error_json = excluded.error_json,
            initialized_at = COALESCE(chain_business_initialization.initialized_at, excluded.initialized_at),
            refreshed_at = excluded.refreshed_at
        """,
        (
            network,
            chain_id,
            protocol,
            domain,
            status,
            _json_dumps(completeness),
            _json_dumps(error) if error else None,
            now,
            now,
        ),
    )


def load_initialization(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    domain: str,
) -> dict[str, Any] | None:
    return query_one(
        database_url,
        """
        SELECT network, chain_id, protocol, domain, status,
               completeness_json, error_json, initialized_at, refreshed_at
        FROM chain_business_initialization
        WHERE network = ? AND chain_id = ? AND protocol = ? AND domain = ?
        """,
        (network, chain_id, protocol, domain),
    )


__all__ = [
    "ensure_global_chain_tables",
    "load_chain_contract",
    "load_chain_info",
    "load_chain_markets",
    "load_chain_tokens",
    "load_initialization",
    "project_chain_tokens_from_manager",
    "seed_chain_contracts",
    "seed_chain_info",
    "seed_chain_info_from_registry",
    "seed_chain_markets_from_builtin",
    "seed_chain_registry",
    "seed_chain_registry_meta",
    "seed_chain_tokens",
    "upsert_initialization",
]
