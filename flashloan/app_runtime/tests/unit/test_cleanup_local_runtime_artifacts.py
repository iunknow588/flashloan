from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]
TOOL_DIR = ROOT / "scripts" / "tool"
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

import cleanup_local_runtime_artifacts as cleanup  # noqa: E402


def _write(path: Path, content: bytes = b"artifact") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _manifest(
    tmp_path: Path,
    path: str,
    *,
    sha256: str,
    reason: str = "test artifact",
) -> Path:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        __import__("json").dumps(
            {
                "schema": cleanup.MANIFEST_SCHEMA,
                "approvalId": "TEST-APPROVAL-001",
                "items": [
                    {
                        "path": path,
                        "reason": reason,
                        "sha256": sha256,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_dry_run_refuses_registered_chain_database_and_sidecar(tmp_path):
    formal = tmp_path / "db" / "local_store_4663.sqlite3"
    _write(formal)
    formal_sha = hashlib.sha256(formal.read_bytes()).hexdigest()

    report = cleanup.plan_cleanup(
        _manifest(tmp_path, str(formal), sha256=formal_sha),
        formal_paths={formal},
    )
    assert report["refusedCount"] == 1
    assert "registered chain database" in report["refused"][0]
    assert formal.exists()

    wal = Path(f"{formal}-wal")
    _write(wal)
    wal_sha = hashlib.sha256(wal.read_bytes()).hexdigest()
    report = cleanup.plan_cleanup(
        _manifest(tmp_path, str(wal), sha256=wal_sha),
        formal_paths={formal},
    )
    assert report["refusedCount"] == 1
    assert wal.exists()


def test_glob_path_is_refused(tmp_path):
    report = cleanup.plan_cleanup(
        _manifest(
            tmp_path,
            str(tmp_path / "*.sqlite3"),
            sha256="a" * 64,
            reason="glob test",
        ),
        formal_paths={Path(tmp_path / "local_store_4663.sqlite3")},
    )
    assert report["refusedCount"] == 1
    assert "glob path is not allowed" in report["refused"][0]


def test_temporary_sqlite_can_be_cleaned_with_matching_checksum(tmp_path):
    target = tmp_path / "temp" / "redundant.sqlite3"
    _write(target, b"temporary data")
    digest = hashlib.sha256(b"temporary data").hexdigest()

    dry = cleanup.plan_cleanup(
        _manifest(tmp_path, str(target), sha256=digest),
        apply=False,
        formal_paths={Path(tmp_path / "main.sqlite3")},
    )
    assert dry["items"][0]["status"] == "dry_run_ready"
    assert target.exists()

    report = cleanup.plan_cleanup(
        _manifest(tmp_path, str(target), sha256=digest),
        apply=True,
        formal_paths={Path(tmp_path / "main.sqlite3")},
    )
    assert report["deletedCount"] == 1
    assert not target.exists()


def test_checksum_mismatch_refuses_delete(tmp_path):
    target = tmp_path / "temp.sqlite3"
    _write(target, b"one")
    report = cleanup.plan_cleanup(
        _manifest(tmp_path, str(target), sha256="b" * 64),
        apply=True,
        formal_paths={Path(tmp_path / "main.sqlite3")},
    )
    assert report["refusedCount"] == 1
    assert "checksum mismatch" in report["refused"][0]
    assert target.exists()
