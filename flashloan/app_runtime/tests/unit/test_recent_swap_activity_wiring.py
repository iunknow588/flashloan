"""T-证候储.01: the recent-swap activity marker reaches its own table.

The marker is measured by the pool index scan and travels with the index
rows.  It is published in its own table, because the depth snapshot table
is the materialised boundary of one on-chain moment and the swap log is a
window that was scanned at a different time.  The Rust pool manager -- the
single writer of the depth snapshot table -- reads that table and
materialises the marker into ``recent_swap_active``.

The three states are kept apart on the way:

* ``1``  -- the pool traded inside the window,
* ``0``  -- the window was scanned and the pool did not trade,
* ``None`` -- the pool was not measured.

A pool that was not measured must never be published as a pool that was
measured and did not trade, because the consumer of this marker reads ``0``
as "scanned, no trade".
"""

from __future__ import annotations

from core.db.import_existing_json import _liquidity_rows_from_index
from core.db.storage_schema import ensure_database_schema
from core.db.storage_v4_exchange import upsert_v4_pool_recent_swap_activity


def _index_payload() -> dict:
    return {
        "network": "robinhood",
        "chainId": 4663,
        "recentSwapLookbackDays": 60,
        "recentSwapScanBlockNumber": 84_000_000,
        "pools": [
            {
                "poolId": "0x" + "a1" * 32,
                "hasLiquidity": True,
                "liquidity": "1000000000000000000000",
                "recentSwapActive": True,
            },
            {
                "poolId": "0x" + "b2" * 32,
                "hasLiquidity": True,
                "liquidity": "2000000000000000000000",
                "recentSwapActive": False,
            },
            {
                "poolId": "0x" + "c3" * 32,
                "hasLiquidity": False,
                "liquidity": "0",
            },
        ],
    }


def test_the_marker_travels_with_the_measured_window():
    """The window the marker was measured with travels with the marker."""
    rows = _liquidity_rows_from_index(_index_payload())
    by_pool = {row["pool_id"]: row for row in rows}

    traded = by_pool["0x" + "a1" * 32]
    assert traded["recent_swap_active"] is True
    assert traded["recent_swap_lookback_days"] == 60
    assert traded["recent_swap_scan_block_number"] == 84_000_000

    quiet = by_pool["0x" + "b2" * 32]
    assert quiet["recent_swap_active"] is False

    unmeasured = by_pool["0x" + "c3" * 32]
    assert "recent_swap_active" not in unmeasured


def _library_pools(database: str, pool_ids: list[str]) -> None:
    """Put the given pools into the pool library.

    The activity table references the pool table, and the pool writer
    only admits a pool whose fee is inside the library ceiling.  A marker
    for a pool outside the library is therefore not published.
    """
    import sqlite3

    connection = sqlite3.connect(database)
    try:
        for pool_id in pool_ids:
            connection.execute(
                "INSERT OR IGNORE INTO v4_exchange_pools "
                "(network, chain_id, protocol, pool_id, pool_manager, "
                "pool_key_json, token0, token1, fee, tick_spacing, hooks, "
                "address_pair, created_at, updated_at) "
                "VALUES ('robinhood', 4663, 'uniswap_v4', ?, '', '{}', "
                "'0x0', '0x1', 3000, 60, '', '', '2026-01-01', '2026-01-01')",
                (pool_id,),
            )
        connection.commit()
    finally:
        connection.close()


def test_the_marker_is_published_in_its_own_table(tmp_path):
    """The marker is written to the activity table, not to the snapshot table."""
    database = str(tmp_path / "local.sqlite3")
    ensure_database_schema(database)
    _library_pools(database, ["0x" + "a1" * 32, "0x" + "b2" * 32])

    rows = _liquidity_rows_from_index(_index_payload())
    written = upsert_v4_pool_recent_swap_activity(
        database,
        network="robinhood",
        chain_id=4663,
        protocol="uniswap_v4",
        rows=rows,
    )

    # Only the two measured pools are written; the unmeasured pool is skipped.
    assert written == 2

    import sqlite3

    connection = sqlite3.connect(database)
    try:
        stored = dict(
            connection.execute(
                "SELECT pool_id, recent_swap_active FROM "
                "v4_exchange_pool_recent_swap_activity"
            ).fetchall()
        )
    finally:
        connection.close()

    assert stored["0x" + "a1" * 32] == 1
    assert stored["0x" + "b2" * 32] == 0
    assert "0x" + "c3" * 32 not in stored


def test_the_depth_snapshot_table_is_not_written_by_the_evidence_channel(tmp_path):
    """The single-writer invariant of the depth snapshot table is kept."""
    database = str(tmp_path / "local.sqlite3")
    ensure_database_schema(database)
    _library_pools(database, ["0x" + "a1" * 32, "0x" + "b2" * 32])

    rows = _liquidity_rows_from_index(_index_payload())
    upsert_v4_pool_recent_swap_activity(
        database,
        network="robinhood",
        chain_id=4663,
        protocol="uniswap_v4",
        rows=rows,
    )

    import sqlite3

    connection = sqlite3.connect(database)
    try:
        snapshot_rows = connection.execute(
            "SELECT COUNT(*) FROM v4_exchange_pool_liquidity_snapshots"
        ).fetchone()[0]
    finally:
        connection.close()

    assert snapshot_rows == 0, (
        "the activity marker must not materialise a depth snapshot row; "
        "the Rust pool manager is the only writer of that table"
    )

def test_a_marker_for_a_pool_outside_the_library_is_not_published(tmp_path):
    """A pool outside the library gets no marker at all.

    The activity table references the pool table.  A pool that the
    library rule keeps out is not a candidate, not a recommendation and
    not a backup, so there is nothing for its marker to decide.
    """
    database = str(tmp_path / "local.sqlite3")
    ensure_database_schema(database)
    _library_pools(database, ["0x" + "a1" * 32])

    rows = _liquidity_rows_from_index(_index_payload())
    written = upsert_v4_pool_recent_swap_activity(
        database,
        network="robinhood",
        chain_id=4663,
        protocol="uniswap_v4",
        rows=rows,
    )

    assert written == 1, "only the pool inside the library is published"

    import sqlite3

    connection = sqlite3.connect(database)
    try:
        stored = dict(
            connection.execute(
                "SELECT pool_id, recent_swap_active FROM "
                "v4_exchange_pool_recent_swap_activity"
            ).fetchall()
        )
    finally:
        connection.close()

    assert "0x" + "a1" * 32 in stored
    assert "0x" + "b2" * 32 not in stored
