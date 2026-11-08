from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from core.db.storage_chain_core import (
    project_chain_tokens_from_manager,
    seed_chain_tokens,
)
from core.db.storage_business_io import query
from core.db.token_authority import (
    ROLLBACK_REASON_ENV,
    TokenAuthorityViolation,
    authority_context,
)
from dex.tools.resolve_robinhood_testnet_token_metadata import (
    _repair_source_json,
)


NETWORK = "robinhood"
CHAIN_ID = 4663
USDG = "0x" + "1" * 40
EXTENSION_ONLY = "0x" + "2" * 40
BLOCKED = "0x" + "3" * 40
PENDING = "0x" + "4" * 40
ORPHAN = "0x" + "5" * 40
FLASHLOAN_ROOT = Path(__file__).resolve().parents[3]
MIGRATION = (
    FLASHLOAN_ROOT
    / "tools"
    / "manager_db"
    / "migrations"
    / "20261103_001_manager_schema.sql"
)


def _create_database(path: Path, *, manager_schema: bool = False) -> None:
    connection = sqlite3.connect(str(path))
    connection.execute(
        """
        CREATE TABLE chain_tokens (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            token_address TEXT NOT NULL,
            token_symbol TEXT NOT NULL,
            token_name TEXT,
            token_decimals INTEGER,
            token_kind TEXT NOT NULL DEFAULT 'erc20',
            is_native INTEGER NOT NULL DEFAULT 0,
            is_wrapped_native INTEGER NOT NULL DEFAULT 0,
            is_stable INTEGER NOT NULL DEFAULT 0,
            anchor_symbol TEXT,
            token_metadata_json TEXT,
            source_json TEXT,
            first_seen_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (network, chain_id, token_address)
        )
        """
    )
    if manager_schema:
        connection.executescript(MIGRATION.read_text(encoding="utf-8"))
    connection.commit()
    connection.close()


def _insert_manager_token(
    path: Path,
    *,
    table: str,
    library: str,
    address: str,
    symbol: str,
    status: str,
    decimals: int = 18,
) -> None:
    connection = sqlite3.connect(str(path))
    connection.execute(
        f"""
        INSERT INTO {table} (
            network, chain_id, token_key_id, v4_currency_key, token_address,
            token_symbol, token_name, token_decimals, token_kind, is_native,
            is_wrapped_native, is_stable, admission_status, admission_source,
            metadata_source, metadata_checksum, source_json, first_seen_at,
            first_admitted_at, updated_at, blocked_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'erc20', 0, 0, 0, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?)
        """,
        (
            NETWORK,
            CHAIN_ID,
            f"{NETWORK}:{CHAIN_ID}:{address}",
            address,
            address,
            symbol,
            f"{symbol} Token",
            decimals,
            status,
            "fixture",
            "fixture_rpc",
            f"sha256:{symbol.lower():0<64}",
            json.dumps({"fixture": True, "library": library}),
            "2026-11-01T00:00:00Z",
            "2026-11-01T00:00:00Z" if status == "admitted" else None,
            "2026-11-04T00:00:00Z",
            "fixture block" if status == "blocked" else None,
        ),
    )
    connection.commit()
    connection.close()


def _seed_manager_libraries(path: Path) -> None:
    _insert_manager_token(
        path,
        table="token_core",
        library="core",
        address=USDG,
        symbol="USDG",
        status="admitted",
        decimals=6,
    )
    _insert_manager_token(
        path,
        table="token_extension",
        library="extension",
        address=USDG,
        symbol="USDG",
        status="candidate_only",
        decimals=6,
    )
    _insert_manager_token(
        path,
        table="token_extension",
        library="extension",
        address=EXTENSION_ONLY,
        symbol="EXT",
        status="candidate_only",
    )
    _insert_manager_token(
        path,
        table="token_extension",
        library="extension",
        address=BLOCKED,
        symbol="BLOCKED",
        status="blocked",
    )
    _insert_manager_token(
        path,
        table="token_extension",
        library="extension",
        address=PENDING,
        symbol="PENDING",
        status="pending_review",
    )
    connection = sqlite3.connect(str(path))
    connection.executemany(
        """
        INSERT INTO token_library_state (
            network, chain_id, library_key, library_version, token_count,
            admitted_count, metadata_checksum, source_checkpoint,
            generated_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                NETWORK,
                CHAIN_ID,
                "core",
                7,
                1,
                1,
                "sha256:core",
                "fixture:core:v7",
                "2026-11-04T00:00:00Z",
                "2026-11-04T00:00:00Z",
            ),
            (
                NETWORK,
                CHAIN_ID,
                "extension",
                11,
                4,
                0,
                "sha256:extension",
                "fixture:extension:v11",
                "2026-11-04T00:00:00Z",
                "2026-11-04T00:00:00Z",
            ),
        ],
    )
    connection.execute(
        """
        INSERT INTO token_admission_events (
            event_id, network, chain_id, token_key_id, library_key,
            previous_status, new_status, reason, actor, evidence_json, created_at
        ) VALUES (
            'fixture-event', ?, ?, ?, 'core', 'candidate_only', 'admitted',
            'fixture', 'manager_api', '{"fixture":true}', '2026-11-04T00:00:00Z'
        )
        """,
        (NETWORK, CHAIN_ID, f"{NETWORK}:{CHAIN_ID}:{USDG}"),
    )
    connection.commit()
    connection.close()


def _seed_legacy_token_db(path: Path) -> None:
    seed_chain_tokens(
        str(path),
        network=NETWORK,
        chain_id=CHAIN_ID,
        token_rows=[
            {
                "token_address": USDG,
                "token_symbol": "LEGACY_USDG",
                "token_name": "Legacy USDG",
                "token_decimals": 18,
                "metadata": {"category": "legacy"},
                "source": {"source": "legacy_fixture"},
            },
            {
                "token_address": ORPHAN,
                "token_symbol": "ORPHAN",
                "token_name": "Legacy orphan kept for foreign keys",
                "token_decimals": 18,
                "metadata": {"category": "legacy_orphan"},
                "source": {"source": "legacy_fixture"},
            },
        ],
    )


def test_seed_chain_tokens_fails_closed_in_manager_and_requires_rollback_reason(
    tmp_path, monkeypatch
):
    path = tmp_path / "authority.sqlite3"
    _create_database(path)
    monkeypatch.setenv("TOKEN_MANAGER_AUTHORITY", "legacy")
    _seed_legacy_token_db(path)

    monkeypatch.setenv("TOKEN_MANAGER_AUTHORITY", "manager")
    with pytest.raises(TokenAuthorityViolation) as denied:
        _seed_legacy_token_db(path)
    assert denied.value.code == "TOKEN_AUTHORITY_WRITE_DENIED"
    assert query(
        str(path),
        "SELECT token_symbol FROM chain_tokens WHERE token_address = ?",
        (USDG,),
    )[0]["token_symbol"] == "LEGACY_USDG"

    monkeypatch.delenv(ROLLBACK_REASON_ENV, raising=False)
    monkeypatch.setenv("TOKEN_MANAGER_AUTHORITY", "rollback")
    with pytest.raises(TokenAuthorityViolation) as missing_reason:
        _seed_legacy_token_db(path)
    assert missing_reason.value.code == "TOKEN_AUTHORITY_ROLLBACK_REASON_REQUIRED"

    monkeypatch.setenv(ROLLBACK_REASON_ENV, "D1.2.4 temporary database drill")
    _seed_legacy_token_db(path)


def test_manager_projection_switch_rollback_and_restart_are_auditable(
    tmp_path, monkeypatch
):
    path = tmp_path / "switch.sqlite3"
    _create_database(path, manager_schema=True)
    monkeypatch.setenv("TOKEN_MANAGER_AUTHORITY", "legacy")
    _seed_legacy_token_db(path)
    _seed_manager_libraries(path)

    monkeypatch.setenv("TOKEN_MANAGER_AUTHORITY", "manager")
    dry_run = project_chain_tokens_from_manager(
        str(path),
        network=NETWORK,
        chain_id=CHAIN_ID,
        dry_run=True,
    )
    rows_before = query(
        str(path),
        """
        SELECT token_address, token_symbol, token_metadata_json, source_json,
               updated_at
        FROM chain_tokens ORDER BY token_address
        """,
    )
    assert dry_run["projectedRows"] == 2
    assert dry_run["changedRows"] == 2
    assert dry_run["extensionRowsDeduplicated"] == 1
    assert dry_run["excludedStatusCounts"] == {
        "blocked": 1,
        "pending_review": 1,
    }
    assert dry_run["legacyRowsForExcludedStatus"] == 0
    assert dry_run["readyForProjection"] is True

    applied = project_chain_tokens_from_manager(
        str(path),
        network=NETWORK,
        chain_id=CHAIN_ID,
        dry_run=False,
    )
    assert applied["checksum"] == dry_run["checksum"]
    rows_after = query(
        str(path),
        """
        SELECT token_address, token_symbol, token_metadata_json, source_json,
               updated_at
        FROM chain_tokens ORDER BY token_address
        """,
    )
    assert len(rows_after) == 3
    assert rows_after != rows_before
    projected = {row["token_address"]: row for row in rows_after}
    assert projected[USDG]["token_symbol"] == "USDG"
    assert projected[EXTENSION_ONLY]["token_symbol"] == "EXT"
    assert projected[ORPHAN]["token_symbol"] == "ORPHAN"
    assert BLOCKED not in projected
    assert PENDING not in projected
    assert json.loads(projected[USDG]["source_json"])["managerProjection"][
        "libraryKey"
    ] == "core"

    manager_counts_before = query(
        str(path),
        """
        SELECT
          (SELECT COUNT(*) FROM token_core) AS core_count,
          (SELECT COUNT(*) FROM token_extension) AS extension_count,
          (SELECT COUNT(*) FROM token_admission_events) AS event_count
        """,
    )[0]
    monkeypatch.setenv(ROLLBACK_REASON_ENV, "D1.2.4 switch rollback drill")
    monkeypatch.setenv("TOKEN_MANAGER_AUTHORITY", "rollback")
    _seed_legacy_token_db(path)
    manager_counts_after = query(
        str(path),
        """
        SELECT
          (SELECT COUNT(*) FROM token_core) AS core_count,
          (SELECT COUNT(*) FROM token_extension) AS extension_count,
          (SELECT COUNT(*) FROM token_admission_events) AS event_count
        """,
    )[0]
    assert manager_counts_after == manager_counts_before

    monkeypatch.setenv("TOKEN_MANAGER_AUTHORITY", "manager")
    repeated = project_chain_tokens_from_manager(
        str(path),
        network=NETWORK,
        chain_id=CHAIN_ID,
        dry_run=False,
    )
    assert repeated["checksum"] == applied["checksum"]
    assert repeated["changedRows"] == 1
    idempotent = project_chain_tokens_from_manager(
        str(path),
        network=NETWORK,
        chain_id=CHAIN_ID,
        dry_run=False,
    )
    assert idempotent["checksum"] == applied["checksum"]
    assert idempotent["changedRows"] == 0


def test_metadata_repair_dry_run_is_allowed_but_apply_is_denied(
    tmp_path, monkeypatch
):
    path = tmp_path / "metadata.sqlite3"
    _create_database(path)
    monkeypatch.setenv("TOKEN_MANAGER_AUTHORITY", "legacy")
    seed_chain_tokens(
        str(path),
        network=NETWORK,
        chain_id=CHAIN_ID,
        token_rows=[
            {
                "token_address": USDG,
                "token_symbol": "USDG",
                "token_name": "USDG",
                "token_decimals": 6,
                "metadata": {"nameMissing": True},
                "source": {"source": "legacy", "nameMissing": False},
            }
        ],
    )

    monkeypatch.setenv("TOKEN_MANAGER_AUTHORITY", "manager")
    changed, missing, inconsistent = _repair_source_json(
        str(path),
        [USDG],
        network=NETWORK,
        chain_id=CHAIN_ID,
        dry_run=True,
    )
    assert changed == [USDG]
    assert missing == []
    assert inconsistent == []
    with pytest.raises(TokenAuthorityViolation) as denied:
        _repair_source_json(
            str(path),
            [USDG],
            network=NETWORK,
            chain_id=CHAIN_ID,
            dry_run=False,
        )
    assert denied.value.code == "TOKEN_AUTHORITY_WRITE_DENIED"


def test_invalid_authority_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("TOKEN_MANAGER_AUTHORITY", "dual-write")
    with pytest.raises(TokenAuthorityViolation) as invalid:
        authority_context()
    assert invalid.value.code == "TOKEN_AUTHORITY_MODE_INVALID"


def test_projection_fails_closed_when_blocked_legacy_row_would_remain_visible(
    tmp_path, monkeypatch
):
    path = tmp_path / "blocked-projection.sqlite3"
    _create_database(path, manager_schema=True)
    monkeypatch.setenv("TOKEN_MANAGER_AUTHORITY", "legacy")
    seed_chain_tokens(
        str(path),
        network=NETWORK,
        chain_id=CHAIN_ID,
        token_rows=[
            {
                "token_address": BLOCKED,
                "token_symbol": "BLOCKED",
                "token_name": "Blocked legacy row",
                "token_decimals": 18,
                "metadata": {},
                "source": {"source": "legacy"},
            }
        ],
    )
    _insert_manager_token(
        path,
        table="token_extension",
        library="extension",
        address=BLOCKED,
        symbol="BLOCKED",
        status="blocked",
    )
    monkeypatch.setenv("TOKEN_MANAGER_AUTHORITY", "manager")
    preview = project_chain_tokens_from_manager(
        str(path),
        network=NETWORK,
        chain_id=CHAIN_ID,
        dry_run=True,
    )
    assert preview["legacyRowsForExcludedStatus"] == 1
    assert preview["readyForProjection"] is False
    with pytest.raises(RuntimeError, match="TOKEN_PROJECTION_EXCLUDED_ROWS_PRESENT"):
        project_chain_tokens_from_manager(
            str(path),
            network=NETWORK,
            chain_id=CHAIN_ID,
            dry_run=False,
        )
