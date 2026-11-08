from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = REPO_ROOT / "scripts" / "audit_closed_loop_runtime_sync.py"
SPEC = importlib.util.spec_from_file_location(
    "audit_closed_loop_runtime_sync",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)

ETH = "0x" + "1" * 40
AAA = "0x" + "2" * 40
BBB = "0x" + "3" * 40

SCOPE_ARGS = [
    "--network",
    "robinhood",
    "--chain-id",
    "4663",
    "--protocol",
    "uniswap_v4",
    "--market-id",
    "dex-arbitrage-4663-uniswap-v4",
]


def _create_schema(path: Path, *, runtime_table: bool = True) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE v4_closed_loops (
                network TEXT NOT NULL,
                chain_id INTEGER NOT NULL,
                protocol TEXT NOT NULL,
                market_id TEXT NOT NULL,
                loop_key TEXT NOT NULL,
                path_addresses_json TEXT,
                path_symbols_json TEXT,
                status TEXT
            );
            """
        )
        if runtime_table:
            connection.executescript(
                """
                CREATE TABLE v4_closed_loop_runtime_state (
                    network TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    loop_key TEXT NOT NULL,
                    state TEXT NOT NULL,
                    updated_at TEXT
                );
                """
            )
        connection.commit()
    finally:
        connection.close()


def _insert_loop(
    path: Path,
    *,
    network: str = "robinhood",
    chain_id: int = 4663,
    market_id: str = "dex-arbitrage-4663-uniswap-v4",
    loop_key: str = f"{ETH}:{AAA}:{BBB}",
    addresses: list[str] | None = None,
    symbols: list[str] | None = None,
) -> None:
    addresses = addresses or [ETH, AAA, BBB, ETH]
    symbols = symbols or ["ETH", "AAA", "BBB", "ETH"]
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            INSERT INTO v4_closed_loops (
                network, chain_id, protocol, market_id, loop_key,
                path_addresses_json, path_symbols_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
            """,
            (
                network,
                chain_id,
                "uniswap_v4",
                market_id,
                loop_key,
                json.dumps(addresses),
                json.dumps(symbols),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _insert_runtime(
    path: Path,
    loop_key: str,
    *,
    network: str = "robinhood",
    chain_id: int = 4663,
    market_id: str = "dex-arbitrage-4663-uniswap-v4",
    state: str = "offchain_pending",
) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            INSERT INTO v4_closed_loop_runtime_state (
                network, chain_id, protocol, market_id, loop_key, state, updated_at
            ) VALUES (?, ?, 'uniswap_v4', ?, ?, ?, '2026-11-08T00:00:00Z')
            """,
            (network, chain_id, market_id, loop_key, state),
        )
        connection.commit()
    finally:
        connection.close()


def _run(tmp_path: Path, key_mode: str) -> tuple[int, dict]:
    output = tmp_path / f"report-{key_mode}.json"
    result = audit.main(
        [
            "--store",
            str(tmp_path / "store.sqlite3"),
            *SCOPE_ARGS,
            "--key-mode",
            key_mode,
            "--output",
            str(output),
        ]
    )
    return result, json.loads(output.read_text(encoding="utf-8"))


def test_directed_symbol_audit_preserves_direction_and_scope(tmp_path) -> None:
    store = tmp_path / "store.sqlite3"
    _create_schema(store)
    _insert_loop(store)
    _insert_loop(
        store,
        loop_key=f"{ETH}:{BBB}:{AAA}",
        addresses=[ETH, BBB, AAA, ETH],
        symbols=["ETH", "BBB", "AAA", "ETH"],
    )
    _insert_runtime(store, "eth|aaa|bbb")
    _insert_runtime(
        store,
        "eth|testnet|only",
        network="robinhood-testnet",
        chain_id=46630,
        market_id="dex-arbitrage-46630-uniswap-v4",
        state="execution_success",
    )

    result, report = _run(tmp_path, "directed-symbol")

    assert result == 2
    coverage = report["coverage"]
    assert coverage["directedClosed"] == 2
    assert coverage["directedRuntime"] == 1
    assert coverage["exactMatched"] == 1
    assert coverage["exactMissing"] == 1
    assert coverage["orphan"] == 0
    assert coverage["yzGroupMatched"] == 1
    assert coverage["yzGroupMissing"] == 0
    assert coverage["physicalLoopKeyMatched"] == 0
    assert coverage["physicalLoopKeyOrphan"] == 1
    assert coverage["outOfScopeRuntime"] == 1
    assert report["runtimeStates"]["rowsInScope"] == 1
    assert report["outOfScope"][0]["scope"]["chainId"] == 46630
    assert "DIRECTION_COLLAPSE" in report["findingCodes"]


def test_loop_key_mode_accepts_physical_address_identity(tmp_path) -> None:
    store = tmp_path / "store.sqlite3"
    _create_schema(store)
    _insert_loop(store)
    _insert_loop(
        store,
        loop_key=f"{ETH}:{BBB}:{AAA}",
        addresses=[ETH, BBB, AAA, ETH],
        symbols=["ETH", "BBB", "AAA", "ETH"],
    )
    _insert_runtime(store, f"{ETH}:{AAA}:{BBB}")
    _insert_runtime(store, f"{ETH}:{BBB}:{AAA}")

    result, report = _run(tmp_path, "loop-key")

    assert result == 0
    coverage = report["coverage"]
    assert coverage["exactMatched"] == 2
    assert coverage["exactMissing"] == 0
    assert coverage["orphan"] == 0
    assert coverage["physicalLoopKeyMatched"] == 2
    assert report["diagnosticCoverage"]["directedSymbol"]["exactMatched"] == 0


def test_route_key_mode_supports_stored_route_identity(tmp_path) -> None:
    store = tmp_path / "store.sqlite3"
    _create_schema(store)
    _insert_loop(store)
    route_key = audit._route_key_id(
        audit._canonical_scope(
            {
                "network": "robinhood",
                "chain_id": 4663,
                "protocol": "uniswap_v4",
                "market_id": "dex-arbitrage-4663-uniswap-v4",
            }
        ),
        [ETH, AAA, BBB, ETH],
    )
    assert route_key
    _insert_runtime(store, route_key)

    result, report = _run(tmp_path, "route-key-id")

    assert result == 0
    assert report["coverage"]["exactMatched"] == 1
    assert report["coverage"]["exactMissing"] == 0
    assert report["coverage"]["orphan"] == 0


def test_missing_runtime_table_returns_contract_error_without_creating_table(
    tmp_path,
) -> None:
    store = tmp_path / "store.sqlite3"
    _create_schema(store, runtime_table=False)
    _insert_loop(store)

    result, report = _run(tmp_path, "loop-key")

    assert result == 3
    assert report["exitCode"] == 3
    connection = sqlite3.connect(store)
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='v4_closed_loop_runtime_state'"
        ).fetchone()
    finally:
        connection.close()
    assert table is None
