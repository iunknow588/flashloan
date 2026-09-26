from __future__ import annotations

import json

from core.local_kv import count, meta_get
from core.storage_seed import seed_local_kv_from_runtime_files


def _make_runtime_dir(tmp_path):
    runtime = tmp_path / "runtime"
    params = runtime / "parameters"
    cache = runtime / "cache"
    state = runtime / "state"
    params.mkdir(parents=True)
    cache.mkdir(parents=True)
    state.mkdir(parents=True)

    (params / "dex_arbitrage.json").write_text(
        json.dumps({"enabled": True, "cooldown_seconds": 0.25}),
        encoding="utf-8",
    )
    (cache / "liquidation_accounts.txt").write_text(
        "0xabc\n# ignored\n0xdef\n",
        encoding="utf-8",
    )
    (state / "latest_extremes.json").write_text(
        json.dumps({"observed_at": "2026-01-01T00:00:00Z", "top": [], "bottom": []}),
        encoding="utf-8",
    )
    (state / "unrelated.json").write_text(
        json.dumps({"ignored": True}),
        encoding="utf-8",
    )
    return runtime


def test_seed_local_kv_from_runtime_files_is_idempotent(tmp_path):
    kv_path = str(tmp_path / "store.sqlite3")
    runtime = _make_runtime_dir(tmp_path)

    first = seed_local_kv_from_runtime_files(kv_path, runtime_dir=runtime)

    assert first["imported"] is True
    assert first["parameter_files"] == 1
    assert first["liquidation_accounts"] == 2
    assert first["state_files"] == 1
    assert count(kv_path, "parameters") == 2
    assert count(kv_path, "arbitrage_simulations") == 0
    assert count(kv_path, "binance_window_extremes") == 1
    assert meta_get(kv_path, "runtime_files_imported") is not None

    second = seed_local_kv_from_runtime_files(kv_path, runtime_dir=runtime)
    assert second["imported"] is False

    forced = seed_local_kv_from_runtime_files(kv_path, runtime_dir=runtime, force=True)
    assert forced["imported"] is True
