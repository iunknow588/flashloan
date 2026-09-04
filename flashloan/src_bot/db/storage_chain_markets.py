from __future__ import annotations

import json
from typing import Any

from core.chain_data_registry import default_market_id_for_feature
from core.market_scan_policy import builtin_market_scan_policies
from core.market_identity import (
    canonical_market_id_from_legacy,
    market_key_from_summary,
)
from core.market_config import (
    KNOWN_EVM_MARKETS,
    PROTOCOL_CAPABILITIES,
)
from core.config_schema import parse_env_int
from db.storage_common import db_connection, db_transaction, require_psycopg


def _json_text(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True)


def _chain_market_connect_timeout() -> int:
    value, _ = parse_env_int("CHAIN_MARKET_DB_CONNECT_TIMEOUT_SECONDS", 1, minimum=1)
    return max(1, min(int(value), 5))


def _builtin_chain_market_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    default_market_ids: dict[str, str] = {}
    for market_id, profile in KNOWN_EVM_MARKETS.items():
        key = market_key_from_summary({"market_id": market_id, **dict(profile)})
        feature = key.feature
        if feature not in default_market_ids:
            try:
                configured = default_market_id_for_feature(feature)
                default_market_ids[feature] = canonical_market_id_from_legacy(configured) or configured
            except Exception:
                default_market_ids[feature] = ""
        protocol = key.protocol_id
        capabilities = PROTOCOL_CAPABILITIES.get(protocol, {})
        rows.append(
            {
                "market_id": key.market_id,
                "canonical_market_id": key.market_id,
                "legacy_market_id": key.legacy_market_id or market_id,
                "chain_id": key.chain_id,
                "network": profile.get("network"),
                "feature": feature,
                "protocol": protocol,
                "protocol_id": protocol,
                "display_name": profile.get("display_name") or key.market_id,
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
                "is_default": key.market_id == default_market_ids.get(feature),
                "chain_default": bool(profile.get("chain_default", False)),
                "metadata_json": {
                    "source": "builtin_fallback",
                    "migration_type": profile.get("migration_type"),
                    "market_type": profile.get("market_type"),
                },
            }
        )
    return rows


def ensure_chain_market_registry_schema(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS chain_markets (
            market_id TEXT PRIMARY KEY,
            legacy_market_id TEXT,
            chain_id BIGINT,
            network TEXT NOT NULL,
            feature TEXT NOT NULL,
            protocol TEXT NOT NULL,
            protocol_id TEXT,
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
    cursor.execute("ALTER TABLE chain_markets ALTER COLUMN chain_id DROP NOT NULL")
    cursor.execute("ALTER TABLE chain_markets ADD COLUMN IF NOT EXISTS legacy_market_id TEXT")
    cursor.execute("ALTER TABLE chain_markets ADD COLUMN IF NOT EXISTS protocol_id TEXT")
    cursor.execute("ALTER TABLE chain_markets ADD COLUMN IF NOT EXISTS chain_default BOOLEAN NOT NULL DEFAULT FALSE")
    cursor.execute("UPDATE chain_markets SET protocol_id = protocol WHERE protocol_id IS NULL OR protocol_id = ''")
    cursor.execute("UPDATE chain_markets SET legacy_market_id = market_id WHERE legacy_market_id IS NULL OR legacy_market_id = ''")
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
        CREATE UNIQUE INDEX IF NOT EXISTS chain_markets_feature_chain_protocol_idx
        ON chain_markets(feature, COALESCE(chain_id, -1), protocol_id)
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
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS dex_arbitrage_attempts (
            id BIGSERIAL PRIMARY KEY,
            market_id TEXT NOT NULL REFERENCES chain_markets(market_id),
            chain_id BIGINT NOT NULL,
            network TEXT NOT NULL,
            route_key TEXT,
            phase TEXT NOT NULL,
            status TEXT NOT NULL,
            evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS dex_arbitrage_attempts_market_time_idx
        ON dex_arbitrage_attempts(market_id, created_at DESC)
        """
    )


def seed_builtin_chain_markets(cursor) -> None:
    configured_default_dex = default_market_id_for_feature("dex_arbitrage")
    default_dex_market_id = canonical_market_id_from_legacy(configured_default_dex) or configured_default_dex
    cursor.execute(
        """
        UPDATE chain_markets
        SET is_default = FALSE, updated_at = NOW()
        WHERE feature = 'dex_arbitrage'
          AND market_id <> %s
          AND is_default = TRUE
        """,
        (default_dex_market_id,),
    )
    for row in _builtin_chain_market_rows():
        cursor.execute(
            """
            INSERT INTO chain_markets (
                market_id, legacy_market_id, chain_id, network, feature, protocol,
                protocol_id, display_name,
                native_symbol, wrapped_native_address, rpc_urls_json, ws_urls_json,
                explorer_url, multicall_address, protocol_addresses_json,
                dex_addresses_json, flashloan_provider, executor_address,
                protocol_adapter, execution_adapter, gas_policy_json,
                aa_policy_json, observation_only, enabled, is_default, chain_default,
                metadata_json, updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s::jsonb, %s::jsonb,
                %s, %s, %s::jsonb,
                %s::jsonb, %s, %s,
                %s, %s, %s::jsonb,
                %s::jsonb, %s, %s, %s, %s,
                %s::jsonb,
                NOW()
            )
            ON CONFLICT (market_id) DO UPDATE SET
                legacy_market_id = EXCLUDED.legacy_market_id,
                chain_id = EXCLUDED.chain_id,
                network = EXCLUDED.network,
                feature = EXCLUDED.feature,
                protocol = EXCLUDED.protocol,
                protocol_id = EXCLUDED.protocol_id,
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
                chain_default = EXCLUDED.chain_default,
                metadata_json = chain_markets.metadata_json || EXCLUDED.metadata_json,
                updated_at = NOW()
            """,
            (
                row["market_id"],
                row["legacy_market_id"],
                row["chain_id"],
                row["network"],
                row["feature"],
                row["protocol"],
                row["protocol_id"],
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
                row["chain_default"],
                _json_text(row["metadata_json"]),
            ),
        )
    for row in _builtin_chain_market_rows():
        for policy in builtin_market_scan_policies(row["market_id"], feature=str(row.get("feature") or "")):
            cursor.execute(
                """
                INSERT INTO market_scan_policies (
                    market_id, feature, pool_tier, metric_name, metric_min, metric_max,
                    refresh_interval_seconds, page_size, enabled, metadata_json, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
                ON CONFLICT (market_id, feature, pool_tier) DO UPDATE SET
                    metric_name = EXCLUDED.metric_name,
                    metric_min = EXCLUDED.metric_min,
                    metric_max = EXCLUDED.metric_max,
                    refresh_interval_seconds = EXCLUDED.refresh_interval_seconds,
                    page_size = EXCLUDED.page_size,
                    enabled = EXCLUDED.enabled,
                    metadata_json = market_scan_policies.metadata_json || EXCLUDED.metadata_json,
                    updated_at = NOW()
                """,
                (
                    policy["market_id"],
                    policy["feature"],
                    policy["pool_tier"],
                    policy["metric_name"],
                    policy["metric_min"],
                    policy["metric_max"],
                    policy["refresh_interval_seconds"],
                    policy["page_size"],
                    policy["enabled"],
                    _json_text(policy["metadata"]),
                ),
            )


def load_chain_market_summaries(database_url: str) -> list[dict[str, Any]]:
    require_psycopg()
    with db_connection(database_url, connect_timeout=_chain_market_connect_timeout()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT market_id, legacy_market_id, chain_id, network, feature,
                       COALESCE(protocol_id, protocol) AS protocol_id, protocol,
                       display_name,
                       native_symbol, observation_only, enabled, is_default,
                       chain_default, protocol_adapter, execution_adapter, metadata_json
                FROM chain_markets
                WHERE enabled = TRUE
                ORDER BY is_default DESC, feature, network, protocol_id, market_id
                """
            )
            rows = cursor.fetchall()
    summaries: list[dict[str, Any]] = []
    for row in rows:
        metadata = row[15] if isinstance(row[15], dict) else {}
        legacy_market_id = str(row[1] or "").strip() or None
        protocol_id = str(row[5] or row[6] or "").strip()
        summaries.append(
            {
                "market_id": str(row[0]),
                "legacy_market_id": legacy_market_id,
                "chain_id": int(row[2]) if row[2] is not None else None,
                "network": str(row[3]),
                "feature": str(row[4]),
                "protocol": protocol_id,
                "protocol_id": protocol_id,
                "display_name": str(row[7]),
                "native_symbol": str(row[8]),
                "observation_only": bool(row[9]),
                "enabled": bool(row[10]),
                "is_default": bool(row[11]),
                "chain_default": bool(row[12]),
                "protocol_adapter": str(row[13]),
                "executor_adapter": str(row[14]),
                "migration_type": str(metadata.get("migration_type") or "database_market_registry"),
                "market_type": str(metadata.get("market_type") or (
                    "execution_rail"
                    if str(row[0]) == "dex-arbitrage-0-cow-protocol" or legacy_market_id == "cow-dex-arbitrage"
                    else "shadow_chain"
                    if str(row[0]) == "dex-arbitrage-4663-uniswap-v3" or legacy_market_id == "robinhood-dex-shadow"
                    else "native_chain"
                )),
            }
        )
    return summaries


def load_market_scan_policies(
    database_url: str,
    *,
    market_id: str,
    feature: str = "liquidation",
) -> list[dict[str, Any]]:
    require_psycopg()
    with db_connection(database_url, connect_timeout=_chain_market_connect_timeout()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT market_id, feature, pool_tier, metric_name, metric_min, metric_max,
                       refresh_interval_seconds, page_size, enabled, metadata_json
                FROM market_scan_policies
                WHERE market_id = %s AND feature = %s AND enabled = TRUE
                ORDER BY CASE pool_tier
                    WHEN 'normal' THEN 1
                    WHEN 'high_frequency' THEN 2
                    WHEN 'core' THEN 3
                    ELSE 99
                END
                """,
                (str(market_id), str(feature)),
            )
            rows = cursor.fetchall()
    return [
        {
            "market_id": str(row[0]),
            "feature": str(row[1]),
            "pool_tier": str(row[2]),
            "metric_name": str(row[3]),
            "metric_min": float(row[4]) if row[4] is not None else None,
            "metric_max": float(row[5]) if row[5] is not None else None,
            "refresh_interval_seconds": int(row[6]),
            "page_size": int(row[7]),
            "enabled": bool(row[8]),
            "metadata": row[9] if isinstance(row[9], dict) else {},
        }
        for row in rows
    ]


def load_current_dex_arbitrage_candidates(
    database_url: str,
    *,
    market_id: str,
    chain_id: int,
    limit: int = 100,
) -> list[dict[str, Any]]:
    require_psycopg()
    with db_connection(database_url, connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT route_key, route_symbols_json, pool_addresses_json, status,
                       evidence_json, observed_block, expires_at, updated_at
                FROM dex_arbitrage_candidates
                WHERE market_id = %s
                  AND chain_id = %s
                  AND status IN ('observed', 'quoted', 'simulation_ready')
                  AND (expires_at IS NULL OR expires_at > NOW())
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (str(market_id), int(chain_id), max(1, min(int(limit), 500))),
            )
            rows = cursor.fetchall()
    return [
        {
            "route_key": str(row[0]),
            "route_symbols": row[1] if isinstance(row[1], list) else [],
            "pool_addresses": row[2] if isinstance(row[2], list) else [],
            "status": str(row[3]),
            "evidence": row[4] if isinstance(row[4], dict) else {},
            "observed_block": int(row[5]) if row[5] is not None else None,
            "expires_at": row[6].isoformat() if hasattr(row[6], "isoformat") else row[6],
            "updated_at": row[7].isoformat() if hasattr(row[7], "isoformat") else row[7],
        }
        for row in rows
    ]


def replace_current_dex_arbitrage_candidates(
    database_url: str,
    *,
    market_id: str,
    chain_id: int,
    network: str,
    candidates: list[dict[str, Any]],
    expires_at: Any = None,
) -> int:
    require_psycopg()
    with db_connection(database_url, connect_timeout=5) as connection:
        with db_transaction(connection, operation="market_registry.replace_candidates"):
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM dex_arbitrage_candidates
                    WHERE market_id = %s AND chain_id = %s AND status IN ('observed', 'quoted', 'simulation_ready')
                    """,
                    (str(market_id), int(chain_id)),
                )
                for candidate in candidates:
                    cursor.execute(
                        """
                        INSERT INTO dex_arbitrage_candidates (
                            market_id, chain_id, network, route_key, route_symbols_json,
                            pool_addresses_json, status, gross_profit_usd, estimated_cost_usd,
                            net_profit_usd, evidence_json, observed_block, expires_at, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s, %s, NOW())
                        """,
                        (
                            str(market_id),
                            int(chain_id),
                            str(network),
                            str(candidate.get("route_key") or ""),
                            _json_text(candidate.get("route_symbols") or []),
                            _json_text(candidate.get("pool_addresses") or []),
                            str(candidate.get("status") or "observed"),
                            candidate.get("gross_profit_usd"),
                            candidate.get("estimated_cost_usd"),
                            candidate.get("net_profit_usd"),
                            _json_text(candidate.get("evidence") or {}),
                            candidate.get("observed_block"),
                            expires_at,
                        ),
                    )
    return len(candidates)


def record_dex_arbitrage_attempt(
    database_url: str,
    *,
    market_id: str,
    chain_id: int,
    network: str,
    phase: str,
    status: str,
    route_key: str | None = None,
    evidence: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    require_psycopg()
    with db_connection(database_url, connect_timeout=5) as connection:
        with db_transaction(connection, operation="market_registry.seed"):
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO dex_arbitrage_attempts (
                        market_id, chain_id, network, route_key, phase, status, evidence_json, error
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                    """,
                    (
                        str(market_id),
                        int(chain_id),
                        str(network),
                        route_key,
                        str(phase),
                        str(status),
                        _json_text(evidence or {}),
                        error,
                    ),
                )
