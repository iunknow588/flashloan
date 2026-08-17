from __future__ import annotations

import json
from typing import Any

from core.market_config import KNOWN_EVM_MARKETS, PROTOCOL_CAPABILITIES
from db.storage_common import db_connection, require_psycopg


def _json_text(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True)


def _builtin_chain_market_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for market_id, profile in KNOWN_EVM_MARKETS.items():
        protocol = str(profile.get("protocol") or "")
        capabilities = PROTOCOL_CAPABILITIES.get(protocol, {})
        rows.append(
            {
                "market_id": market_id,
                "chain_id": profile.get("chain_id"),
                "network": profile.get("network"),
                "feature": profile.get("feature") or "liquidation",
                "protocol": protocol,
                "display_name": profile.get("display_name") or market_id,
                "native_symbol": profile.get("native_symbol"),
                "wrapped_native_address": profile.get("wrapped_native_address") or "",
                "rpc_urls_json": [],
                "ws_urls_json": [],
                "explorer_url": profile.get("explorer_url") or "",
                "multicall_address": profile.get("multicall3_address") or "",
                "protocol_addresses_json": {},
                "dex_addresses_json": {},
                "flashloan_provider": "",
                "executor_address": "",
                "protocol_adapter": capabilities.get("adapter") or "ProtocolAdapter",
                "execution_adapter": capabilities.get("executor") or "ExecutionAdapter",
                "gas_policy_json": {},
                "aa_policy_json": {},
                "observation_only": bool(profile.get("observation_only")) or not bool(capabilities.get("supported")),
                "enabled": bool(profile.get("enabled", True)),
                "is_default": market_id == "avalanche-aave-v3",
                "metadata_json": {"source": "builtin_fallback", "migration_type": profile.get("migration_type")},
            }
        )
    return rows


def ensure_chain_market_registry_schema(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS chain_markets (
            market_id TEXT PRIMARY KEY,
            chain_id BIGINT NOT NULL,
            network TEXT NOT NULL,
            feature TEXT NOT NULL,
            protocol TEXT NOT NULL,
            display_name TEXT NOT NULL,
            native_symbol TEXT NOT NULL,
            wrapped_native_address TEXT,
            rpc_urls_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            ws_urls_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            explorer_url TEXT,
            multicall_address TEXT,
            protocol_addresses_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            dex_addresses_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            flashloan_provider TEXT,
            executor_address TEXT,
            protocol_adapter TEXT NOT NULL,
            execution_adapter TEXT NOT NULL,
            gas_policy_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            aa_policy_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            observation_only BOOLEAN NOT NULL DEFAULT TRUE,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            is_default BOOLEAN NOT NULL DEFAULT FALSE,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS chain_markets_default_feature_idx
        ON chain_markets(feature)
        WHERE is_default
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS chain_markets_chain_feature_idx
        ON chain_markets(chain_id, feature)
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS market_scan_policies (
            id BIGSERIAL PRIMARY KEY,
            market_id TEXT NOT NULL REFERENCES chain_markets(market_id),
            feature TEXT NOT NULL,
            pool_tier TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            metric_min NUMERIC,
            metric_max NUMERIC,
            refresh_interval_seconds INTEGER NOT NULL,
            page_size INTEGER NOT NULL DEFAULT 20,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (market_id, feature, pool_tier)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS dex_arbitrage_candidates (
            id BIGSERIAL PRIMARY KEY,
            market_id TEXT NOT NULL REFERENCES chain_markets(market_id),
            chain_id BIGINT NOT NULL,
            network TEXT NOT NULL,
            route_key TEXT NOT NULL,
            route_symbols_json JSONB NOT NULL,
            pool_addresses_json JSONB NOT NULL,
            status TEXT NOT NULL,
            gross_profit_usd NUMERIC,
            estimated_cost_usd NUMERIC,
            net_profit_usd NUMERIC,
            evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            observed_block BIGINT,
            expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS dex_arbitrage_candidates_current_idx
        ON dex_arbitrage_candidates(market_id, status, expires_at)
        """
    )


def seed_builtin_chain_markets(cursor) -> None:
    for row in _builtin_chain_market_rows():
        cursor.execute(
            """
            INSERT INTO chain_markets (
                market_id, chain_id, network, feature, protocol, display_name,
                native_symbol, wrapped_native_address, rpc_urls_json, ws_urls_json,
                explorer_url, multicall_address, protocol_addresses_json,
                dex_addresses_json, flashloan_provider, executor_address,
                protocol_adapter, execution_adapter, gas_policy_json,
                aa_policy_json, observation_only, enabled, is_default, metadata_json,
                updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s::jsonb, %s::jsonb,
                %s, %s, %s::jsonb,
                %s::jsonb, %s, %s,
                %s, %s, %s::jsonb,
                %s::jsonb, %s, %s, %s, %s::jsonb,
                NOW()
            )
            ON CONFLICT (market_id) DO UPDATE SET
                chain_id = EXCLUDED.chain_id,
                network = EXCLUDED.network,
                feature = EXCLUDED.feature,
                protocol = EXCLUDED.protocol,
                display_name = EXCLUDED.display_name,
                native_symbol = EXCLUDED.native_symbol,
                wrapped_native_address = EXCLUDED.wrapped_native_address,
                explorer_url = EXCLUDED.explorer_url,
                multicall_address = EXCLUDED.multicall_address,
                protocol_adapter = EXCLUDED.protocol_adapter,
                execution_adapter = EXCLUDED.execution_adapter,
                observation_only = EXCLUDED.observation_only,
                enabled = EXCLUDED.enabled,
                is_default = EXCLUDED.is_default,
                metadata_json = chain_markets.metadata_json || EXCLUDED.metadata_json,
                updated_at = NOW()
            """,
            (
                row["market_id"],
                row["chain_id"],
                row["network"],
                row["feature"],
                row["protocol"],
                row["display_name"],
                row["native_symbol"],
                row["wrapped_native_address"],
                _json_text(row["rpc_urls_json"]),
                _json_text(row["ws_urls_json"]),
                row["explorer_url"],
                row["multicall_address"],
                _json_text(row["protocol_addresses_json"]),
                _json_text(row["dex_addresses_json"]),
                row["flashloan_provider"],
                row["executor_address"],
                row["protocol_adapter"],
                row["execution_adapter"],
                _json_text(row["gas_policy_json"]),
                _json_text(row["aa_policy_json"]),
                row["observation_only"],
                row["enabled"],
                row["is_default"],
                _json_text(row["metadata_json"]),
            ),
        )


def load_chain_market_summaries(database_url: str) -> list[dict[str, Any]]:
    require_psycopg()
    with db_connection(database_url, connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT market_id, chain_id, network, feature, protocol, display_name,
                       native_symbol, observation_only, enabled, is_default,
                       protocol_adapter, execution_adapter, metadata_json
                FROM chain_markets
                WHERE enabled = TRUE
                ORDER BY is_default DESC, feature, network, protocol, market_id
                """
            )
            rows = cursor.fetchall()
    summaries: list[dict[str, Any]] = []
    for row in rows:
        metadata = row[12] if isinstance(row[12], dict) else {}
        summaries.append(
            {
                "market_id": str(row[0]),
                "chain_id": int(row[1]) if row[1] is not None else None,
                "network": str(row[2]),
                "feature": str(row[3]),
                "protocol": str(row[4]),
                "display_name": str(row[5]),
                "native_symbol": str(row[6]),
                "observation_only": bool(row[7]),
                "enabled": bool(row[8]),
                "is_default": bool(row[9]),
                "protocol_adapter": str(row[10]),
                "executor_adapter": str(row[11]),
                "migration_type": str(metadata.get("migration_type") or "database_market_registry"),
            }
        )
    return summaries

