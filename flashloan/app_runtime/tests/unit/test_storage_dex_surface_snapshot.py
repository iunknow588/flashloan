from __future__ import annotations

from core.db.storage_chain_core import seed_chain_tokens
from core.db.storage_dex_surface_snapshot import (
    clear_dex_surface_snapshots,
    load_dex_overview_snapshot,
    save_dex_overview_snapshot,
)
from core.db.storage_schema import ensure_database_schema


def _snapshot() -> dict:
    return {
        "exists": True,
        "path": "sqlite_db",
        "source": "sqlite_db",
        "chain_id": 46630,
        "protocols": ["uniswap_v4"],
        "summary": {"closedLoopRouteCount": 1},
        "closedLoopRouteSummaries": [
            {
                "anchorSymbol": "ETH",
                "pathSymbols": ["ETH", "TZ", "TB", "ETH"],
                "pathText": "ETH -> TZ -> TB -> ETH",
                "routePools": [],
                "poolIds": [],
            }
        ],
        "chainSpecificObservationPairs": [
            {
                "anchorSymbol": "ETH",
                "counterAnchorSymbol": "TZ",
                "pathSymbols": ["ETH", "TZ", "TB", "ETH"],
            }
        ],
        "poolRegistry": {},
    }


def test_dex_overview_snapshot_roundtrip_and_fingerprint_invalidation(tmp_path):
    db = str(tmp_path / "snapshot.sqlite3")
    ensure_database_schema(db)
    network = "robinhood-testnet"
    chain_id = 46630

    saved = save_dex_overview_snapshot(
        _snapshot(),
        network=network,
        chain_id=chain_id,
        database_url=db,
    )
    assert saved["ok"] is True
    assert saved["payloadBytes"] > 0

    loaded = load_dex_overview_snapshot(
        network=network,
        chain_id=chain_id,
        database_url=db,
    )
    assert loaded is not None
    assert loaded["path"] == "sqlite_db"
    assert loaded["summary"]["closedLoopRouteCount"] == 1
    assert loaded["chainSpecificObservationPairs"][0]["anchorSymbol"] == "ETH"

    # Any business-table mutation changes the source fingerprint and therefore
    # invalidates the persisted compact snapshot.
    seed_chain_tokens(
        db,
        network=network,
        chain_id=chain_id,
        token_rows=[
            {
                "token_address": "0x0000000000000000000000000000000000000001",
                "token_symbol": "TZ",
                "token_decimals": 18,
            }
        ],
    )
    assert (
        load_dex_overview_snapshot(
            network=network,
            chain_id=chain_id,
            database_url=db,
        )
        is None
    )


def test_dex_overview_snapshot_clear_is_scoped(tmp_path):
    db = str(tmp_path / "clear.sqlite3")
    ensure_database_schema(db)
    network = "robinhood-testnet"
    chain_id = 46630
    save_dex_overview_snapshot(
        _snapshot(),
        network=network,
        chain_id=chain_id,
        database_url=db,
    )
    save_dex_overview_snapshot(
        _snapshot(),
        network="robinhood",
        chain_id=4663,
        database_url=db,
    )

    cleared = clear_dex_surface_snapshots(
        network=network,
        chain_id=chain_id,
        database_url=db,
    )
    assert cleared == 1
    assert (
        load_dex_overview_snapshot(
            network=network,
            chain_id=chain_id,
            database_url=db,
        )
        is None
    )
    assert (
        load_dex_overview_snapshot(
            network="robinhood",
            chain_id=4663,
            database_url=db,
        )
        is not None
    )
