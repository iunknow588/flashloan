from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = REPO_ROOT / "scripts" / "observe_dex_closed_loops.py"
SPEC = importlib.util.spec_from_file_location(
    "observe_dex_closed_loops",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
observe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observe)


def _create_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE cow_execution_attempts (
                network TEXT NOT NULL,
                chain_id INTEGER NOT NULL,
                market_id TEXT NOT NULL,
                created_at TEXT
            );
            CREATE TABLE cow_execution_reviews (
                network TEXT NOT NULL,
                chain_id INTEGER NOT NULL,
                market_id TEXT NOT NULL,
                created_at TEXT
            );
            CREATE TABLE v4_closed_loop_runtime_state (
                network TEXT NOT NULL,
                chain_id INTEGER NOT NULL,
                protocol TEXT NOT NULL,
                market_id TEXT NOT NULL,
                loop_key TEXT NOT NULL,
                state TEXT NOT NULL,
                last_error TEXT,
                updated_at TEXT
            );
            """
        )
        connection.executemany(
            """
            INSERT INTO cow_execution_attempts (
                network, chain_id, market_id, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (
                    "robinhood",
                    4663,
                    "dex-arbitrage-4663-uniswap-v4",
                    "2026-11-08T01:00:00Z",
                ),
                (
                    "robinhood-testnet",
                    46630,
                    "dex-arbitrage-46630-uniswap-v4",
                    "2026-11-08T02:00:00Z",
                ),
            ],
        )
        connection.executemany(
            """
            INSERT INTO cow_execution_reviews (
                network, chain_id, market_id, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (
                    "robinhood",
                    4663,
                    "dex_arbitrage_4663_uniswap_v4",
                    "2026-11-08T03:00:00Z",
                ),
                (
                    "robinhood-testnet",
                    46630,
                    "dex-arbitrage-46630-uniswap-v4",
                    "2026-11-08T04:00:00Z",
                ),
            ],
        )
        connection.executemany(
            """
            INSERT INTO v4_closed_loop_runtime_state (
                network, chain_id, protocol, market_id, loop_key,
                state, last_error, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "robinhood",
                    4663,
                    "uniswap_v4",
                    "dex-arbitrage-4663-uniswap-v4",
                    "eth|spy|usdg",
                    "preflight_failed",
                    "rpc_busy",
                    "2026-11-08T05:00:00Z",
                ),
                (
                    "robinhood-testnet",
                    46630,
                    "uniswap_v4",
                    "dex-arbitrage-46630-uniswap-v4",
                    "aaa|bbb|ccc",
                    "execution_success",
                    "",
                    "2026-11-08T06:00:00Z",
                ),
            ],
        )
        connection.commit()
    finally:
        connection.close()


def test_sample_applies_explicit_scope_and_reports_out_of_scope(tmp_path) -> None:
    database = tmp_path / "store.sqlite3"
    _create_database(database)
    scope = observe._scope(
        network="robinhood",
        chain_id=4663,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-4663-uniswap-v4",
    )

    payload = observe._sample(str(database), scope)

    assert payload["scopeApplied"] is True
    assert payload["attemptsTotal"] == 1
    assert payload["reviewsTotal"] == 1
    assert payload["runtimeStates"] == {"preflight_failed": 1}
    assert payload["preflightFailedLastErrors"] == {"rpc_busy": 1}
    assert payload["outOfScope"]["total"] == 3
    assert {
        (row["table"], row["chainId"], row["count"])
        for row in payload["outOfScope"]["rows"]
    } == {
        ("cow_execution_attempts", 46630, 1),
        ("cow_execution_reviews", 46630, 1),
        ("v4_closed_loop_runtime_state", 46630, 1),
    }


def test_missing_scope_is_a_contract_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "observe_dex_closed_loops.py",
            "--database",
            str(tmp_path / "store.sqlite3"),
            "--out",
            "out.jsonl",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        observe.main()

    assert exc.value.code == 3
