#!/usr/bin/env python3
"""Clean local runtime artifacts only from an explicit manifest.

The previous cleanup mistake came from enumerating
``flashloan/db/*.sqlite3*`` and treating a file path as an exclusion prefix.
This script inverts that: every deletion must already be an explicit manifest
item with a reason and a SHA256 checksum.  Registered chain databases and
their SQLite sidecars are refused before any checksum read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "flashloan" / "src"
DB_ROOT = ROOT / "flashloan" / "db"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


MANIFEST_SCHEMA = "flashloan-runtime-artifact-cleanup-v1"
_SQLITE_SUFFIXES = (".sqlite3", ".sqlite", ".db")
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
_GLOB_CHARS = re.compile(r"[*?\[\]]")


class CleanupContractError(ValueError):
    """Manifest or path cannot be cleaned safely."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_base(path: str | Path) -> Path | None:
    text = str(_normalize(path)).lower()
    for sidecar in _SIDECAR_SUFFIXES:
        if text.endswith(sidecar):
            base_text = text[: -len(sidecar)]
            if any(base_text.endswith(suffix) for suffix in _SQLITE_SUFFIXES):
                return _normalize(base_text)
    if not any(text.endswith(suffix) for suffix in _SQLITE_SUFFIXES):
        return None
    return _normalize(text)


def formal_chain_database_paths() -> set[Path]:
    """Return exact registered chain databases plus the legacy DB path."""

    from core.chain_data_registry import load_chain_registry
    from core.db.chain_database_context import chain_database_path

    paths: set[Path] = {_normalize(DB_ROOT / "local_store.sqlite3")}
    registry = load_chain_registry()
    for row in registry["chains_by_id"].values():
        try:
            paths.add(
                _normalize(
                    chain_database_path(
                        int(row["chain_id"]),
                        str(row["network"]),
                    )
                )
            )
        except (OSError, ValueError):
            continue
    return paths


def is_formal_chain_database(path: str | Path, formal_paths: set[Path]) -> bool:
    base = _sqlite_base(path)
    if base is None:
        return False
    normalized = _normalize(base)
    for formal in formal_paths:
        formal_normalized = _normalize(formal)
        if str(normalized).lower() == str(formal_normalized).lower():
            return True
    return False


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CleanupContractError(f"manifest does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise CleanupContractError(f"manifest is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CleanupContractError("manifest root must be an object")
    if payload.get("schema") != MANIFEST_SCHEMA:
        raise CleanupContractError(
            f"manifest schema must be {MANIFEST_SCHEMA}"
        )
    approval_id = str(payload.get("approvalId") or "").strip()
    if not approval_id:
        raise CleanupContractError("manifest approvalId is required")
    items = payload.get("items")
    if not isinstance(items, list):
        raise CleanupContractError("manifest items must be an array")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise CleanupContractError(f"manifest item {index} must be an object")
        path_value = str(item.get("path") or "").strip()
        reason = str(item.get("reason") or "").strip()
        digest = str(item.get("sha256") or "").strip()
        if not path_value or not reason:
            raise CleanupContractError(
                f"manifest item {index} requires path and reason"
            )
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest.lower()
        ):
            raise CleanupContractError(
                f"manifest item {index} requires a valid SHA256"
            )
    return {
        "approvalId": approval_id,
        "items": items,
        "manifestPath": str(path),
    }


def plan_cleanup(
    manifest_path: Path,
    *,
    apply: bool = False,
    formal_paths: set[Path] | None = None,
) -> dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    formal = formal_paths if formal_paths is not None else formal_chain_database_paths()
    results: list[dict[str, Any]] = []
    refused: list[str] = []
    errors: list[str] = []

    for index, item in enumerate(manifest["items"]):
        raw_path = str(item.get("path") or "").strip()
        path = _normalize(raw_path)
        if _GLOB_CHARS.search(raw_path):
            refused.append(f"glob path is not allowed: {raw_path}")
            results.append(
                {
                    "index": index,
                    "path": raw_path,
                    "status": "refused",
                    "reason": "glob path is not allowed",
                }
            )
            continue
        if is_formal_chain_database(path, formal):
            refused.append(f"registered chain database: {path}")
            results.append(
                {
                    "index": index,
                    "path": str(path),
                    "status": "refused",
                    "reason": "registered chain database or sidecar",
                }
            )
            continue
        if not path.exists():
            if bool(item.get("allowMissing")):
                results.append(
                    {
                        "index": index,
                        "path": str(path),
                        "status": "skipped_missing",
                    }
                )
            else:
                errors.append(f"missing path: {path}")
                results.append(
                    {
                        "index": index,
                        "path": str(path),
                        "status": "error",
                        "reason": "path is missing and allowMissing is false",
                    }
                )
            continue
        actual = _sha256(path)
        supplied = str(item.get("sha256") or "").strip().lower()
        if actual.lower() != supplied:
            refused.append(f"checksum mismatch: {path}")
            results.append(
                {
                    "index": index,
                    "path": str(path),
                    "status": "refused",
                    "reason": "checksum mismatch",
                    "actualSha256": actual,
                }
            )
            continue
        if apply:
            try:
                path.unlink()
                results.append(
                    {
                        "index": index,
                        "path": str(path),
                        "status": "deleted",
                    }
                )
            except OSError as exc:
                errors.append(f"{path}: {exc}")
                results.append(
                    {
                        "index": index,
                        "path": str(path),
                        "status": "error",
                        "reason": str(exc),
                    }
                )
        else:
            results.append(
                {
                    "index": index,
                    "path": str(path),
                    "status": "dry_run_ready",
                }
            )

    return {
        "schema": MANIFEST_SCHEMA,
        "generatedAt": _utc_now(),
        "approvalId": manifest["approvalId"],
        "manifestPath": manifest["manifestPath"],
        "apply": bool(apply),
        "formalDatabaseCount": len(formal),
        "formalDatabases": sorted(str(path) for path in formal),
        "items": results,
        "deletedCount": sum(1 for item in results if item["status"] == "deleted"),
        "refusedCount": len(refused),
        "errorCount": len(errors),
        "refused": refused,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="Actually delete files.")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        report = plan_cleanup(args.manifest, apply=args.apply)
    except CleanupContractError as exc:
        report = {
            "schema": MANIFEST_SCHEMA,
            "generatedAt": _utc_now(),
            "error": str(exc),
            "refusedCount": 1,
            "errorCount": 1,
        }
        exit_code = 3
    else:
        exit_code = 0 if report["refusedCount"] == 0 and report["errorCount"] == 0 else 2
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    else:
        print(payload)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
