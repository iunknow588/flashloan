"""Tests for the pool manager write path of the liquidity snapshot table.

``v4_exchange_pool_liquidity_snapshots`` has exactly one writer: the Rust
pool manager.  The evidence channel deliberately does not write it, so the
runtime has to reach the pool manager itself.  These tests cover the
runtime's own behaviour: that the tick calls the pool manager, that the
receipt is stored, that the freshness of the table is published, and that
a refused write fails the tick instead of being swallowed.
"""

from __future__ import annotations

from typing import Any

from core.db.storage_business_io import execute
from core.db.storage_business_schema import ensure_business_schema
from core.db.v4_pool_liquidity_worker import load_v4_pool_liquidity_cursor
from dex.intent_trade.chains.robinhood_common.chain_config import (
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
)
from dex.runtime import robinhood_v4_idle_maintenance as maintenance


PROTOCOL = "uniswap_v4"
TOKEN = "0x1111111111111111111111111111111111111111"
POOL = "0x2222222222222222222222222222222222222222222222222222222222222222"
ZERO = "0x0000000000000000000000000000000000000000"


def _seed(database: str, *, liquidity_read_at: str = "2026-01-01T00:00:00Z") -> None:
    """Seed one fixed-fee, no-hook, candidate-eligible pool."""
    execute(
        database,
        """
        INSERT INTO chain_tokens (
            network, chain_id, token_address, token_symbol,
            token_metadata_json, first_seen_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ROBINHOOD_TESTNET_NETWORK,
            ROBINHOOD_TESTNET_CHAIN_ID,
            TOKEN,
            "TST",
            '{"category": "stock"}',
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
        ),
    )
    execute(
        database,
        """
        INSERT INTO v4_exchange_pools (
            network, chain_id, protocol, pool_id, pool_manager, pool_key_json,
            token0, token1, fee, dynamic_fee, special_fee, tick_spacing, hooks,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 60, ?, ?, ?)
        """,
        (
            ROBINHOOD_TESTNET_NETWORK,
            ROBINHOOD_TESTNET_CHAIN_ID,
            PROTOCOL,
            POOL,
            ZERO,
            "{}",
            TOKEN,
            TOKEN,
            3000,
            ZERO,
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
        ),
    )
    execute(
        database,
        """
        INSERT INTO v4_exchange_pool_liquidity_snapshots (
            network, chain_id, protocol, pool_id, has_liquidity, liquidity,
            liquidity_index, liquidity_read_at, liquidity_block_number,
            observed_at, refreshed_at, evidence_source
        ) VALUES (?, ?, ?, ?, 1, '1', '1', ?, 1, ?, ?, 'seed')
        """,
        (
            ROBINHOOD_TESTNET_NETWORK,
            ROBINHOOD_TESTNET_CHAIN_ID,
            PROTOCOL,
            POOL,
            liquidity_read_at,
            liquidity_read_at,
            liquidity_read_at,
        ),
    )


def _stub_index_refresh(monkeypatch) -> None:
    """Replace the index refresh: the test is about the snapshot write."""

    def fake_refresh_v4_live(**kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "errors": [], "liquidity": {"blockNumber": 4242}}

    monkeypatch.setattr(
        "dex.tools.refresh_robinhood_v4_live.refresh_v4_live",
        fake_refresh_v4_live,
    )


def _stub_pool_manager(
    monkeypatch,
    receipt: dict[str, Any],
    calls: list[dict[str, Any]],
    *,
    on_call=None,
):
    def fake_refresh_v4_pool_liquidity(network, chain_id, pool_ids, **kwargs: Any):
        calls.append({"network": network, "chain_id": chain_id, "pool_ids": list(pool_ids)})
        if on_call is not None:
            on_call(kwargs)
        return dict(receipt)

    monkeypatch.setattr(
        "core.rs_bridge.refresh_v4_pool_liquidity",
        fake_refresh_v4_pool_liquidity,
    )
    monkeypatch.setattr("core.rs_bridge.rust_available", lambda: True)


def _tick(database: str) -> dict[str, Any]:
    return maintenance._run_pool_liquidity_tick_locked(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )


def test_the_tick_materialises_the_snapshot_rows_through_the_pool_manager(
    tmp_path,
    monkeypatch,
) -> None:
    database = str((tmp_path / "snapshot.sqlite3").resolve())
    ensure_business_schema(database)
    _seed(database)
    _stub_index_refresh(monkeypatch)
    calls: list[dict[str, Any]] = []

    def fake_write(kwargs: dict[str, Any]) -> None:
        # The pool manager writes the row; the runtime reads the table back.
        execute(
            kwargs["database_url"],
            """
            UPDATE v4_exchange_pool_liquidity_snapshots
               SET liquidity_read_at = ?
             WHERE pool_id = ?
            """,
            ("2026-11-26T00:00:00Z", POOL),
        )

    _stub_pool_manager(
        monkeypatch,
        {
            "ok": True,
            "manager": "pool_manager",
            "writtenCount": 1,
            "rowsWithoutSnapshotCount": 0,
            "incompleteReadCount": 0,
        },
        calls,
        on_call=fake_write,
    )

    result = _tick(database)

    assert result["ok"] is True
    assert calls == [
        {
            "network": ROBINHOOD_TESTNET_NETWORK,
            "chain_id": ROBINHOOD_TESTNET_CHAIN_ID,
            "pool_ids": [POOL],
        }
    ]
    materialization = result["snapshotMaterialization"]
    assert materialization["writtenCount"] == 1
    freshness = result["snapshotFreshness"]
    assert freshness["newestReadAt"] == "2026-11-26T00:00:00Z"
    assert freshness["ageSeconds"] is not None

    cursor = load_v4_pool_liquidity_cursor(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )
    assert cursor["snapshot_read_at"] == "2026-11-26T00:00:00Z"
    assert cursor["snapshot_rows_written"] == 1


def test_a_refused_pool_manager_write_fails_the_tick(tmp_path, monkeypatch) -> None:
    database = str((tmp_path / "refused.sqlite3").resolve())
    ensure_business_schema(database)
    _seed(database)
    _stub_index_refresh(monkeypatch)
    _stub_pool_manager(
        monkeypatch,
        {"ok": False, "error": "the pool manager refused the refresh"},
        [],
    )

    result = _tick(database)

    assert result["ok"] is False
    assert result["phase"] == "snapshot_materialization_failed"
    assert "refused" in result["error"]
    cursor = load_v4_pool_liquidity_cursor(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )
    assert cursor["mode"] == "error"
    assert "refused" in (cursor["error"] or "")


def test_a_missing_rust_extension_fails_the_tick(tmp_path, monkeypatch) -> None:
    database = str((tmp_path / "missing.sqlite3").resolve())
    ensure_business_schema(database)
    _seed(database)
    _stub_index_refresh(monkeypatch)
    monkeypatch.setattr("core.rs_bridge.rust_available", lambda: False)

    result = _tick(database)

    assert result["ok"] is False
    assert result["phase"] == "snapshot_materialization_failed"
    assert result["errorCode"] == "BRIDGE_UNAVAILABLE"
