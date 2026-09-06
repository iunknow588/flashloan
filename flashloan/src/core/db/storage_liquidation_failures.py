import json
from typing import Any

from core.market_config import liquidation_market_config, liquidation_market_config_for
from core.db.scoped_adapter import validate_scoped_rows
from core.db.storage_common import db_connection, db_transaction, require_psycopg
from core.db.liquidation_failures_mapping import (
    failure_row as _failure_row,
    json_or_default as _json_or_default,
)


def _market_scope(market_id: str | None = None, chain_id: int | None = None) -> dict[str, Any]:
    market = (
        liquidation_market_config_for(market_id, chain_id=chain_id)
        if market_id is not None or chain_id is not None
        else liquidation_market_config()
    )
    return {
        "market_id": market.market_id,
        "chain_id": market.chain_id,
        "network": market.network,
        "protocol": market.protocol,
    }


def record_liquidation_failure_sample(
    database_url: str,
    *,
    account: str | None = None,
    block_number: int | None = None,
    collateral_asset: str | None = None,
    debt_asset: str | None = None,
    failure_type: str,
    failure_reason: str | None = None,
    payload: dict[str, Any] | None = None,
    source: str = "execution_attempt",
    market_id: str | None = None,
    chain_id: int | None = None,
) -> int:
    require_psycopg()
    scope = _market_scope(market_id, chain_id)
    with db_connection(database_url, connect_timeout=8) as connection:
        with db_transaction(connection, operation="liquidation.failure_sample"):
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO liquidation_failure_samples (
                        market_id, chain_id, network, protocol,
                        account, block_number, collateral_asset, debt_asset,
                        failure_type, failure_reason, payload_json, source, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    RETURNING id
                    """,
                    (
                        scope["market_id"], scope["chain_id"], scope["network"], scope["protocol"],
                        account, block_number, collateral_asset, debt_asset, failure_type,
                        failure_reason, json.dumps(payload or {}, ensure_ascii=True, separators=(",", ":")), source,
                    ),
                )
                row = cursor.fetchone()
                return int(row[0]) if row else 0


def _load_failure_samples(
    database_url: str,
    limit: int,
    *,
    account: str | None = None,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    require_psycopg()
    scope = _market_scope(market_id, chain_id)
    account_clause = "AND lower(account) = lower(%s)" if account else ""
    params: list[Any] = [scope["market_id"], scope["chain_id"]]
    if account:
        params.insert(0, account)
    params.append(max(1, int(limit)))
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT market_id, chain_id, network, protocol,
                       id, account, block_number, collateral_asset, debt_asset,
                       failure_type, failure_reason, payload_json, source, created_at
                FROM liquidation_failure_samples
                WHERE {('lower(account) = lower(%s) AND ' if account else '')}
                      market_id = %s AND chain_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                tuple(params),
            )
            column_names = (
                "market_id", "chain_id", "network", "protocol", "id", "account",
                "block_number", "collateral_asset", "debt_asset", "failure_type",
                "failure_reason", "payload_json", "source", "created_at",
            )
            rows = validate_scoped_rows(
                [dict(zip(column_names, row)) for row in cursor.fetchall()],
                scope,
            )
            return [_failure_row(tuple(row[name] for name in column_names), scope) for row in rows]


def load_recent_liquidation_failure_samples(
    database_url: str,
    limit: int = 20,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    return _load_failure_samples(database_url, limit, market_id=market_id, chain_id=chain_id)


def load_liquidation_failure_samples_for_account(
    database_url: str,
    account: str,
    limit: int = 20,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    return _load_failure_samples(
        database_url,
        limit,
        account=account,
        market_id=market_id,
        chain_id=chain_id,
    )
