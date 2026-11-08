from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]


def _load_script(name: str, relative_path: str):
    path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_script(
    "migrate_closed_loop_runtime_identity",
    "scripts/migrate_closed_loop_runtime_identity.py",
)
verify = _load_script(
    "verify_closed_loop_runtime_identity_isolation",
    "scripts/verify_closed_loop_runtime_identity_isolation.py",
)


ETH = "0x" + "1" * 40
AAA = "0x" + "2" * 40
BBB = "0x" + "3" * 40
FORWARD = f"{ETH}:{AAA}:{BBB}"
REVERSE = f"{ETH}:{BBB}:{AAA}"
SCOPE = {
    "network": "robinhood",
    "chainId": 4663,
    "protocol": "uniswap_v4",
    "marketId": "dex-arbitrage-4663-uniswap-v4",
}
TESTNET_SCOPE = {
    "network": "robinhood-testnet",
    "chainId": 46630,
    "protocol": "uniswap_v4",
    "marketId": "dex-arbitrage-46630-uniswap-v4",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


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
                path_addresses_json TEXT,
                path_symbols_json TEXT,
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
            """
        )
        connection.commit()
    finally:
        connection.close()


def _insert_closed(
    path: Path,
    *,
    addresses: list[str],
    symbols: list[str],
) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            INSERT INTO v4_closed_loops (
                network, chain_id, protocol, market_id, loop_key,
                path_addresses_json, path_symbols_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                SCOPE["network"],
                SCOPE["chainId"],
                SCOPE["protocol"],
                SCOPE["marketId"],
                ":".join(addresses[:3]),
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
    scope: dict,
    loop_key: str,
    state: str = "preflight_failed",
) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            INSERT INTO v4_closed_loop_runtime_state (
                network, chain_id, protocol, market_id, loop_key,
                state, generation, owner_thread, last_error, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 3, NULL, 'legacy diagnostic',
                      '2026-11-05T04:13:11Z')
            """,
            (
                scope["network"],
                scope["chainId"],
                scope["protocol"],
                scope["marketId"],
                loop_key,
                state,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _seed_store(path: Path) -> None:
    _create_schema(path)
    _insert_closed(
        path,
        addresses=[ETH, AAA, BBB, ETH],
        symbols=["ETH", "AAA", "BBB", "ETH"],
    )
    _insert_closed(
        path,
        addresses=[ETH, BBB, AAA, ETH],
        symbols=["ETH", "BBB", "AAA", "ETH"],
    )
    _insert_runtime(path, scope=SCOPE, loop_key="eth|aaa|bbb")
    _insert_runtime(path, scope=TESTNET_SCOPE, loop_key=FORWARD)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            INSERT INTO cow_execution_attempts (
                network, chain_id, market_id, created_at
            ) VALUES ('robinhood-testnet', 46630,
                      'dex-arbitrage-46630-uniswap-v4',
                      '2026-11-08T06:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO cow_execution_reviews (
                network, chain_id, market_id, created_at
            ) VALUES ('robinhood-testnet', 46630,
                      'dex-arbitrage-46630-uniswap-v4',
                      '2026-11-08T06:00:00Z')
            """
        )
        connection.commit()
    finally:
        connection.close()


def _write_source_report(path: Path, store: Path) -> None:
    report = {
        "schema": migration.AUDIT_SCHEMA,
        "generatedAt": "2026-11-08T00:00:00Z",
        "scope": SCOPE,
        "keyMode": "directed-symbol",
        "databaseMetadata": {"path": str(store), "sha256": _sha256(store)},
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
            "matchedKeys": ["eth|aaa|bbb"],
            "missingKeys": ["eth|bbb|aaa"],
            "orphanKeys": [],
        },
    }
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_migration_report(tmp_path: Path) -> tuple[Path, Path, dict]:
    store = tmp_path / "source.sqlite3"
    _seed_store(store)
    source_report = tmp_path / "canonical.json"
    _write_source_report(source_report, store)
    plan = migration._build_plan(
        store=store,
        source_report_path=source_report,
        scope=SCOPE,
        expected_closed_loops=2,
    )
    assert plan["exitCode"] == 0
    migration_report = tmp_path / "migration.json"
    migration_report.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return store, migration_report, plan


def _copy_store(source: Path, target: Path) -> None:
    target.write_bytes(source.read_bytes())


def test_guard_checks_preserve_legacy_rows_and_testnet_residue(tmp_path) -> None:
    source, _, _ = _write_migration_report(tmp_path)
    working = tmp_path / "working.sqlite3"
    _copy_store(source, working)

    result = verify._run_guard_checks(
        working,
        scope=SCOPE,
        testnet_scope=TESTNET_SCOPE,
        expected_legacy_rows=1,
    )

    assert result["legacyIdentityPrune"]["legacySymbolIdentityPreservedCount"] == 1
    assert result["legacyIdentityPrune"]["deletedCount"] == 0
    assert result["testnetResiduePreserve"]["residueDetected"] == 1
    assert result["testnetResiduePreserve"]["residualPruned"] == 0
    assert result["testnetOrphanPreserve"]["protectedTestResidueCount"] == 1
    assert result["approvalGateEnforced"] is True
    assert result["testnetBefore"]["rowChecksum"] == result["testnetAfter"]["rowChecksum"]


def test_rehearsal_archives_legacy_and_inserts_physical_rows(tmp_path) -> None:
    source, _, plan = _write_migration_report(tmp_path)
    working = tmp_path / "working.sqlite3"
    _copy_store(source, working)
    verify._run_guard_checks(
        working,
        scope=SCOPE,
        testnet_scope=TESTNET_SCOPE,
        expected_legacy_rows=1,
    )

    result = verify._apply_rehearsal_plan(
        working,
        report=plan,
        scope=SCOPE,
        migration_report_checksum="a" * 64,
    )

    assert result["archivedRows"] == 1
    assert result["deletedLegacyRows"] == 1
    assert result["insertedPhysicalRows"] == 2
    connection = sqlite3.connect(working)
    try:
        runtime = connection.execute(
            """
            SELECT loop_key, state FROM v4_closed_loop_runtime_state
            WHERE network = 'robinhood' ORDER BY loop_key
            """
        ).fetchall()
        archive = connection.execute(
            "SELECT loop_key, state FROM v4_closed_loop_runtime_state_archive"
        ).fetchall()
    finally:
        connection.close()
    assert runtime == [(FORWARD, "offchain_pending"), (REVERSE, "offchain_pending")]
    assert archive == [("eth|aaa|bbb", "preflight_failed")]


def test_post_migration_maintenance_is_idempotent(tmp_path) -> None:
    source, _, plan = _write_migration_report(tmp_path)
    working = tmp_path / "working.sqlite3"
    _copy_store(source, working)
    verify._apply_rehearsal_plan(
        working,
        report=plan,
        scope=SCOPE,
        migration_report_checksum="b" * 64,
    )

    result = verify._run_post_migration_maintenance(
        working,
        scope=SCOPE,
        expected_rows=2,
    )

    assert result["orphanPrune"]["deletedCount"] == 0
    assert result["orphanPrune"]["matchedClosedLoopCount"] == 2
    assert result["backfill"]["created"] == 0
    assert result["backfill"]["existing"] == 2


def test_migration_report_checksum_mismatch_is_rejected(tmp_path) -> None:
    source, migration_report, _ = _write_migration_report(tmp_path)

    try:
        verify._load_migration_report(
            path=migration_report,
            source_store_sha256="0" * 64,
            scope=SCOPE,
            expected_closed_loops=2,
        )
    except verify.IsolationContractError as exc:
        assert "SHA256" in str(exc)
    else:
        raise AssertionError("expected checksum mismatch")
    assert source.is_file()


def test_path_guard_rejects_production_store(tmp_path) -> None:
    production = tmp_path / "production.sqlite3"

    try:
        verify._ensure_paths_are_isolated(
            source_store=production,
            working_store=tmp_path / "working.sqlite3",
            production_store=production,
        )
    except verify.IsolationContractError as exc:
        assert "production" in str(exc)
    else:
        raise AssertionError("expected production path rejection")


def test_parse_observation_requires_scope_and_testnet_boundary(tmp_path) -> None:
    path = tmp_path / "observation.jsonl"
    samples = [
        {
            "schema": verify.OBSERVATION_SCHEMA,
            "utc": "2026-11-08T00:00:00Z",
            "scope": SCOPE,
            "scopeApplied": True,
            "runtimeStates": {"offchain_pending": 2},
            "outOfScope": {
                "total": 1,
                "rows": [
                    {
                        "table": "v4_closed_loop_runtime_state",
                        "chainId": 46630,
                        "count": 1,
                    }
                ],
            },
        },
        {
            "schema": verify.OBSERVATION_SCHEMA,
            "utc": "2026-11-08T00:00:02Z",
            "scope": SCOPE,
            "scopeApplied": True,
            "runtimeStates": {"offchain_pending": 2},
            "outOfScope": {
                "total": 1,
                "rows": [
                    {
                        "table": "v4_closed_loop_runtime_state",
                        "chainId": 46630,
                        "count": 1,
                    }
                ],
            },
            "windowComplete": True,
        },
    ]
    path.write_text(
        "\n".join(json.dumps(sample) for sample in samples) + "\n",
        encoding="utf-8",
    )

    result = verify._parse_observation(path, SCOPE, 2)

    assert result["sampleCount"] == 2
    assert result["runtimeStates"] == {"offchain_pending": 2}
    assert result["windowComplete"] is True


def test_full_verify_runs_on_isolated_fixture(tmp_path, monkeypatch) -> None:
    source, migration_report, _ = _write_migration_report(tmp_path)
    working = tmp_path / "working.sqlite3"
    observation_output = tmp_path / "observation.jsonl"
    output = tmp_path / "result.json"
    monkeypatch.setattr(
        verify,
        "_run_observation",
        lambda **_kwargs: {
            "exitCode": 0,
            "sampleCount": 2,
            "runtimeStates": {"offchain_pending": 2},
            "windowComplete": True,
        },
    )
    args = argparse.Namespace(
        source_store=source,
        working_store=working,
        migration_report=migration_report,
        network="robinhood",
        chain_id=4663,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-4663-uniswap-v4",
        production_store=tmp_path / "production.sqlite3",
        expected_closed_loops=2,
        duration_seconds=1,
        interval_seconds=1,
        output=output,
        observation_output=observation_output,
    )

    report = verify._verify(args)
    verify._write_report(output, report)
    persisted = json.loads(output.read_text(encoding="utf-8"))

    assert report["conclusion"] == "passed"
    assert persisted["conclusion"] == "passed"
    assert report["before"]["runtime"]["legacySymbolOrOtherKeys"] == 1
    assert report["after"]["runtime"]["physicalDirectionKeys"] == 2
    assert report["archive"]["rows"] == 1
    assert report["testnetPreservation"]["unchanged"] is True
    assert report["productionBoundary"]["productionStoreWritten"] is False
