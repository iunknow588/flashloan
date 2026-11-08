from core.db.storage_cow_execution import record_cow_execution_attempts
from dex.runtime.closed_loop_state_machine import (
    STATE_EXECUTION_NOT_REQUESTED,
    STATE_OFFCHAIN_ESTIMATE,
    upsert_loop_state,
)
from web.data_routes.dex_surface_routes_ops import _closed_loop_monitor_summary


def _review_row(pair, created_at, status="route_unprofitable", reason=""):
    return {
        "network": "robinhood-testnet",
        "chain_id": 46630,
        "market_id": "dex-arbitrage-46630-uniswap_v4",
        "pair": pair,
        "category": "not_executable",
        "created_at": created_at,
        "route_path": [part.strip() for part in pair.split("->")],
        "state": "quote_failed" if status == "rpc_unavailable" else "not_profitable",
        "quote": {
            "live_onchain_evidence": {
                "status": status,
                "reviewFailureKind": "rpc_verification_failed"
                if status == "rpc_unavailable"
                else "",
                "blockers": [] if status == "route_unprofitable" else [reason],
            }
        },
        "auto_execute": {},
    }


def _execution_row(pair, created_at, category, tx_hash):
    return {
        "network": "robinhood-testnet",
        "chain_id": 46630,
        "market_id": "dex-arbitrage-46630-uniswap_v4",
        "pair": pair,
        "category": category,
        "review_category": category,
        "created_at": created_at,
        "state": category,
        "tx_hash": tx_hash,
        "transaction_hash": tx_hash,
        "quote": {},
        "auto_execute": {},
    }


def test_closed_loop_monitor_separates_review_records_from_loop_health(tmp_path):
    database = str(tmp_path / "cow.db")
    record_cow_execution_attempts(
        database,
        [
            _review_row("TZ -> TA -> TB -> TZ", "2026-10-30T00:00:00+00:00", "route_unprofitable"),
            _review_row(
                "WETH -> TZ -> TA -> WETH",
                "2026-10-30T00:01:00+00:00",
                "rpc_unavailable",
                reason="previewBestPool unavailable",
            ),
        ],
    )
    upsert_loop_state(
        database,
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-46630-uniswap_v4",
        loop_key="tz|ta|tb",
        state=STATE_EXECUTION_NOT_REQUESTED,
    )
    upsert_loop_state(
        database,
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-46630-uniswap_v4",
        loop_key="weth|ta|tz",
        state=STATE_OFFCHAIN_ESTIMATE,
    )

    monitor = _closed_loop_monitor_summary(
        database_url=database,
        networks=["robinhood-testnet"],
        chain_ids=[46630],
    )

    # Two direction records for one loop, but health is counted per loop.
    assert monitor["closedLoopTotal"] == 2
    assert monitor["reviewSuccessLoops"] == 1
    assert monitor["reviewFailedLoops"] == 1
    assert monitor["reviewMissingLoops"] == 0
    assert monitor["broadcastLoops"] == 0
    assert monitor["loopStateSummary"]["total"] == 2


def test_closed_loop_monitor_counts_missing_review_and_broadcast_loops(tmp_path):
    database = str(tmp_path / "cow.db")
    record_cow_execution_attempts(
        database,
        [
            _review_row("TZ -> TA -> TB -> TZ", "2026-10-30T00:00:00+00:00", "route_unprofitable"),
            _execution_row(
                "TZ -> TA -> TB -> TZ",
                "2026-10-30T00:02:00+00:00",
                "execution_success",
                "0x" + "ab" * 32,
            ),
            _execution_row(
                "WETH -> TZ -> TA -> WETH",
                "2026-10-30T00:03:00+00:00",
                "execution_failed",
                "0x" + "cd" * 32,
            ),
        ],
    )
    upsert_loop_state(
        database,
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-46630-uniswap_v4",
        loop_key="tz|ta|tb",
        state=STATE_EXECUTION_NOT_REQUESTED,
    )
    for loop_key in ("weth|ta|tz", "tz|tb|tc"):
        upsert_loop_state(
            database,
            network="robinhood-testnet",
            chain_id=46630,
            protocol="uniswap_v4",
            market_id="dex-arbitrage-46630-uniswap_v4",
            loop_key=loop_key,
            state=STATE_OFFCHAIN_ESTIMATE,
        )

    monitor = _closed_loop_monitor_summary(
        database_url=database,
        networks=["robinhood-testnet"],
        chain_ids=[46630],
    )

    assert monitor["closedLoopTotal"] == 3
    # tz|ta|tb has a review; weth|ta|tz only has an execution record and
    # tz|tb|tc has neither, so one loop is missing a review.
    assert monitor["reviewSuccessLoops"] == 1
    assert monitor["reviewMissingLoops"] == 2
    assert monitor["broadcastLoops"] == 2
    assert monitor["broadcastSuccessLoops"] == 1
    assert monitor["broadcastFailedLoops"] == 1
