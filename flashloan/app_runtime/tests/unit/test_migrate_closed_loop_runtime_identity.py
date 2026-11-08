from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = REPO_ROOT / "scripts" / "migrate_closed_loop_runtime_identity.py"
SPEC = importlib.util.spec_from_file_location(
    "migrate_closed_loop_runtime_identity",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)

ETH = "0x" + "1" * 40
AAA = "0x" + "2" * 40
BBB = "0x" + "3" * 40
SCOPE = {
    "network": "robinhood",
    "chainId": 4663,
    "protocol": "uniswap_v4",
    "marketId": "dex-arbitrage-4663-uniswap-v4",
}
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


def _create_schema(path: Path) -> None:
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
                anchor_address TEXT NOT NULL,
                anchor_symbol TEXT NOT NULL,
                path_text TEXT,
                path_addresses_json TEXT,
                path_symbols_json TEXT,
                status TEXT,
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (network, chain_id, protocol, market_id, loop_key)
            );
            CREATE TABLE v4_closed_loop_runtime_state (
                network TEXT NOT NULL,
                chain_id INTEGER NOT NULL,
                protocol TEXT NOT NULL,
                market_id TEXT NOT NULL,
                loop_key TEXT NOT NULL,
                state TEXT NOT NULL,
                generation INTEGER NOT NULL DEFAULT 1,
                owner_thread TEXT,
                tx_hash TEXT,
                last_error TEXT,
                updated_at TEXT NOT NULL,
                review_cycle_id TEXT,
                claimed_at TEXT,
                heartbeat_at TEXT,
                lease_until TEXT,
                finished_at TEXT,
                persisted_at TEXT,
                PRIMARY KEY (network, chain_id, protocol, market_id, loop_key)
            );
            """
        )
        connection.commit()
    finally:
        connection.close()


def _insert_loop(
    path: Path,
    *,
    addresses: list[str],
    symbols: list[str],
    loop_key: str | None = None,
    network: str = "robinhood",
    chain_id: int = 4663,
    market_id: str = "dex-arbitrage-4663-uniswap-v4",
) -> None:
    physical_key = loop_key or ":".join(addresses[:3])
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            INSERT INTO v4_closed_loops (
                network, chain_id, protocol, market_id, loop_key,
                anchor_address, anchor_symbol, path_addresses_json,
                path_symbols_json, status, created_at, updated_at
            ) VALUES (?, ?, 'uniswap_v4', ?, ?, ?, ?, ?, ?, 'active',
                      '2026-11-08T00:00:00Z', '2026-11-08T00:00:00Z')
            """,
            (
                network,
                chain_id,
                market_id,
                physical_key,
                addresses[0],
                symbols[0],
                json.dumps(addresses),
                json.dumps(symbols),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _insert_runtime(
    path: Path,
    *,
    loop_key: str,
    state: str = "preflight_failed",
    network: str = "robinhood",
    chain_id: int = 4663,
    market_id: str = "dex-arbitrage-4663-uniswap-v4",
) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            INSERT INTO v4_closed_loop_runtime_state (
                network, chain_id, protocol, market_id, loop_key, state,
                generation, owner_thread, tx_hash, last_error, updated_at
            ) VALUES (?, ?, 'uniswap_v4', ?, ?, ?, 3, 'worker-a', NULL,
                      'legacy diagnostic', '2026-11-05T04:13:11Z')
            """,
            (network, chain_id, market_id, loop_key, state),
        )
        connection.commit()
    finally:
        connection.close()


def _seed_forward_reverse(path: Path) -> None:
    _create_schema(path)
    _insert_loop(
        path,
        addresses=[ETH, AAA, BBB, ETH],
        symbols=["ETH", "AAA", "BBB", "ETH"],
    )
    _insert_loop(
        path,
        addresses=[ETH, BBB, AAA, ETH],
        symbols=["ETH", "BBB", "AAA", "ETH"],
    )
    _insert_runtime(path, loop_key="eth|aaa|bbb")


def _write_source_report(
    path: Path,
    store: Path,
    *,
    matched_keys: list[str] | None = None,
    missing_keys: list[str] | None = None,
    sha256: str | None = None,
) -> None:
    matched_keys = matched_keys if matched_keys is not None else ["eth|aaa|bbb"]
    missing_keys = missing_keys if missing_keys is not None else ["eth|bbb|aaa"]
    report = {
        "schema": migration.AUDIT_SCHEMA,
        "generatedAt": "2026-11-08T00:00:00Z",
        "scope": SCOPE,
        "keyMode": "directed-symbol",
        "databaseMetadata": {
            "path": str(store),
            "sha256": sha256 or migration._file_sha256(store),
        },
        "coverage": {
            "directedClosed": 2,
            "directedRuntime": 1,
            "exactMatched": 1,
            "exactMissing": 1,
            "orphan": 0,
            "yzGroupMatched": 1,
            "yzGroupMissing": 0,
            "yzGroupOrphan": 0,
            "physicalLoopKeyMatched": 0,
            "physicalLoopKeyMissing": 2,
            "physicalLoopKeyOrphan": 1,
        },
        "selectedCoverage": {
            "matchedKeys": matched_keys,
            "missingKeys": missing_keys,
            "orphanKeys": [],
        },
    }
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _run_plan(tmp_path: Path) -> dict:
    store = tmp_path / "store.sqlite3"
    source_report = tmp_path / "source.json"
    _write_source_report(source_report, store)
    return migration._build_plan(
        store=store,
        source_report_path=source_report,
        scope=SCOPE,
        expected_closed_loops=2,
    )


def test_dry_run_builds_rekey_and_reverse_insert_plan(tmp_path) -> None:
    store = tmp_path / "store.sqlite3"
    _seed_forward_reverse(store)

    report = _run_plan(tmp_path)

    assert report["exitCode"] == 0
    assert report["findingCodes"] == []
    assert report["counts"] == {
        "closedLoops": 2,
        "proposedDirectionMappings": 2,
        "proposedRuntimeStates": 2,
        "archivePlans": 1,
        "rekeyPlans": 1,
        "reverseInsertPlans": 1,
        "sourceRuntimeGroups": 1,
        "sourceExactDirectedMatches": 1,
        "unresolvedDirections": 0,
        "duplicateDirectedKey": 0,
        "unresolvedAddress": 0,
        "crossScopeConflict": 0,
    }
    assert {mapping["migrationAction"] for mapping in report["directionMappings"]} == {
        "rekey",
        "insert",
    }
    assert all(
        mapping["proposedState"] == "offchain_pending"
        for mapping in report["directionMappings"]
    )
    assert all(
        mapping["stateSource"] == "identity_migration_reset"
        for mapping in report["directionMappings"]
    )
    rekey = report["rekeyPlans"][0]
    reverse = report["reverseInsertPlans"][0]
    assert rekey["proposedRuntimeKey"] == f"{ETH}:{AAA}:{BBB}"
    assert rekey["reverseLoopKey"] == f"{ETH}:{BBB}:{AAA}"
    assert reverse["proposedRuntimeKey"] == f"{ETH}:{BBB}:{AAA}"
    assert reverse["sourceMatchedClosedLoopKey"] == f"{ETH}:{AAA}:{BBB}"
    assert report["archivePlans"][0]["sourceRuntimeState"] == "preflight_failed"
    assert all(
        row["state"] == "offchain_pending"
        for row in report["proposedRuntimeStates"]
    )


def test_source_report_checksum_mismatch_stops_migration(tmp_path) -> None:
    store = tmp_path / "store.sqlite3"
    _seed_forward_reverse(store)
    source_report = tmp_path / "source.json"
    _write_source_report(source_report, store, sha256="0" * 64)

    report = migration._build_plan(
        store=store,
        source_report_path=source_report,
        scope=SCOPE,
        expected_closed_loops=2,
    )

    assert report["exitCode"] == 2
    assert "SOURCE_REPORT_DATABASE_CHECKSUM_MISMATCH" in report["findingCodes"]


def test_missing_exact_direction_is_not_guessed(tmp_path) -> None:
    store = tmp_path / "store.sqlite3"
    _seed_forward_reverse(store)
    source_report = tmp_path / "source.json"
    _write_source_report(
        source_report,
        store,
        matched_keys=["eth|bbb|aaa"],
        missing_keys=["eth|aaa|bbb"],
    )

    report = migration._build_plan(
        store=store,
        source_report_path=source_report,
        scope=SCOPE,
        expected_closed_loops=2,
    )

    assert report["exitCode"] == 2
    assert "SOURCE_REPORT_MATCHED_KEYS_MISMATCH" in report["findingCodes"]
    assert "SOURCE_REPORT_MISSING_KEYS_MISMATCH" in report["findingCodes"]
    assert report["directionMappings"]


def test_invalid_address_path_is_reported(tmp_path) -> None:
    store = tmp_path / "store.sqlite3"
    _create_schema(store)
    _insert_loop(
        store,
        addresses=[ETH, AAA, BBB, ETH],
        symbols=["ETH", "AAA", "BBB", "ETH"],
    )
    _insert_loop(
        store,
        addresses=[ETH, BBB, "0xdead", ETH],
        symbols=["ETH", "BBB", "AAA", "ETH"],
        loop_key=f"{ETH}:{BBB}:{'0x' + '4' * 40}",
    )
    _insert_runtime(store, loop_key="eth|aaa|bbb")
    source_report = tmp_path / "source.json"
    _write_source_report(source_report, store)

    report = migration._build_plan(
        store=store,
        source_report_path=source_report,
        scope=SCOPE,
        expected_closed_loops=2,
    )

    assert report["exitCode"] == 2
    assert "CLOSED_LOOP_IDENTITY_INVALID" in report["findingCodes"]
    assert report["counts"]["unresolvedAddress"] > 0


def test_runtime_rows_outside_scope_are_excluded(tmp_path) -> None:
    store = tmp_path / "store.sqlite3"
    _seed_forward_reverse(store)
    _insert_runtime(
        store,
        loop_key="aaa|bbb|ccc",
        state="execution_success",
        network="robinhood-testnet",
        chain_id=46630,
        market_id="dex-arbitrage-46630-uniswap-v4",
    )

    report = _run_plan(tmp_path)

    assert report["exitCode"] == 0
    assert report["outOfScope"]["runtimeStates"] == 1
    assert report["counts"]["closedLoops"] == 2
    assert report["counts"]["sourceRuntimeGroups"] == 1


def test_cli_writes_new_report_and_does_not_modify_database(tmp_path) -> None:
    store = tmp_path / "store.sqlite3"
    _seed_forward_reverse(store)
    before = migration._file_sha256(store)
    source_report = tmp_path / "source.json"
    _write_source_report(source_report, store)
    output = tmp_path / "migration.json"

    result = migration.main(
        [
            "--store",
            str(store),
            "--source-report",
            str(source_report),
            *SCOPE_ARGS,
            "--expected-closed-loops",
            "2",
            "--dry-run",
            "--output",
            str(output),
        ]
    )

    assert result == 0
    assert output.is_file()
    assert migration._file_sha256(store) == before
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["mode"] == "dry-run"
    assert report["task"] == "P0-2.4"
