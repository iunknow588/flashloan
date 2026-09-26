"""One-time import of existing runtime JSON/text files into the local KV store.

This keeps a fresh local store useful without a legacy Postgres migration.  It
is idempotent: after the first import a ``runtime_files_imported`` meta key is
written and later calls become no-ops unless ``force=True``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.db.storage_control_parameters import save_control_panel_parameter_map
from core.db.storage_liquidation_legacy import upsert_liquidation_accounts
from core.kv_rows import upsert_row
from core.local_kv import count, ensure_store, meta_get, meta_put, store_path
from core.runtime_paths import resolve_runtime_dir


_PARAM_NAMESPACE_BY_FILE = {
    "config.json": "strategy_config",
    "liquidation.json": "liquidation_runtime_config",
    "dex_arbitrage.json": "cow_submission_pause_guard",
    "execution.json": "liquidation_pause_guard",
    "dex_strategy.json": "dex_strategy",
}

_STATE_COLLECTION_BY_FILE = {
    "latest_extremes.json": ("binance_window_extremes", "state|latest_extremes"),
    "latest_arbitrage.json": ("arbitrage_simulations", "state|latest_arbitrage"),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _seed_parameters(kv_path: Path, parameters_dir: Path) -> int:
    if not parameters_dir.exists() or not parameters_dir.is_dir():
        return 0
    imported_files = 0
    for path in sorted(parameters_dir.glob("*.json")):
        data = _read_json(path)
        if not data:
            continue
        namespace = _PARAM_NAMESPACE_BY_FILE.get(path.name, path.stem)
        save_control_panel_parameter_map(str(kv_path), namespace, data, connect_timeout=5)
        imported_files += 1
    return imported_files


def _seed_liquidation_accounts(kv_path: Path, accounts_path: Path | None) -> int:
    if accounts_path is None or not accounts_path.exists():
        return 0
    accounts: list[str] = []
    try:
        raw_lines = accounts_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    for line in raw_lines:
        account = line.strip()
        if account and not account.startswith("#"):
            accounts.append(account)
    if not accounts:
        return 0
    upsert_liquidation_accounts(str(kv_path), accounts, source="runtime_file_import")
    return len(accounts)


def _seed_state(kv_path: Path, state_dir: Path) -> int:
    if not state_dir.exists() or not state_dir.is_dir():
        return 0
    imported_files = 0
    for path in sorted(state_dir.glob("*.json")):
        mapping = _STATE_COLLECTION_BY_FILE.get(path.name)
        if mapping is None:
            continue
        data = _read_json(path)
        if not data:
            continue
        collection, key = mapping
        upsert_row(str(kv_path), collection, key, data)
        imported_files += 1
    return imported_files


def seed_local_kv_from_runtime_files(
    database_url: str | os.PathLike[str] | None = None,
    *,
    runtime_dir: str | os.PathLike[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Import runtime JSON/cache files into a local KV store once."""

    kv_path = store_path(database_url)
    ensure_store(kv_path)
    base = Path(runtime_dir) if runtime_dir is not None else resolve_runtime_dir()

    existing_count = (
        count(kv_path, "parameters")
        + count(kv_path, "liquidation_accounts")
        + count(kv_path, "arbitrage_simulations")
        + count(kv_path, "binance_window_extremes")
    )
    if not force and (meta_get(kv_path, "runtime_files_imported") or existing_count):
        return {"imported": False, "reason": "already_imported", "path": str(kv_path)}

    counts: dict[str, int] = {
        "parameter_files": _seed_parameters(kv_path, base / "parameters"),
        "liquidation_accounts": _seed_liquidation_accounts(kv_path, base / "cache" / "liquidation_accounts.txt"),
        "state_files": _seed_state(kv_path, base / "state"),
    }
    meta_put(
        kv_path,
        "runtime_files_imported",
        {
            "imported_at": _utc_now(),
            "runtime_dir": str(base),
            "counts": counts,
        },
    )
    counts["imported"] = True
    counts["path"] = str(kv_path)
    return counts


__all__ = ["seed_local_kv_from_runtime_files"]
