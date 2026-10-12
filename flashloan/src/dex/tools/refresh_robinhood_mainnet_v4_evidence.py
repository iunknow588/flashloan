"""One-command Robinhood mainnet V4 evidence refresh.

The pipeline runs in a fixed order:

1. Rebuild the full V4 pool index from PoolManager Initialize logs
   (HyperRPC first, standard RPC fallback, adaptive sharding).
2. Read live PoolManager liquidity for every indexed pool.
3. Import the index/liquidity evidence into SQLite and rebuild candidate
   pools, market observation and closed loops directly from the business
   tables, then clear in-memory DEX surface caches.

The old candidate shards, intermediate pool files and market observation
static snapshots are removed from the runtime tree. This command imports only
the on-chain index/liquidity evidence into SQLite and rebuilds every business
table from the database.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.db.database_path import absolute_database_path  # noqa: E402
from core.db.storage_schema import ensure_database_schema  # noqa: E402
from core.db.import_existing_json import import_existing_json  # noqa: E402
from core.db import builders  # noqa: E402
from core.env_loader import load_chain_env_files  # noqa: E402
from dex.intent_trade.chains.robinhood.v4_pool_index import (  # noqa: E402
    write_v4_pool_index_from_initialize_logs,
)
from dex.intent_trade.chains.robinhood.v4_pool_liquidity import (  # noqa: E402
    write_v4_pool_liquidity_from_contract_evidence,
)
from market.binance_market.dex_surface_cache import _clear_dex_surface_cache  # noqa: E402


def _step(name: str, result: dict) -> dict:
    ok = bool(result.get("ok", result.get("outputPath")))
    return {"step": name, "ok": ok, "summary": result}


def _normalize_database_arg(value: str, *, repo_root: Path = SRC_ROOT.parent) -> str:
    """Return an absolute database path, defaulting to the repository root."""
    if not value:
        return ""
    raw = Path(value).expanduser()
    if raw.is_absolute():
        return str(raw)
    return str((repo_root / raw).resolve())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default="",
        help="SQLite path; relative paths are resolved from the repository root.",
    )
    parser.add_argument("--runtime-root", default="")
    parser.add_argument("--rpc-url", default="")
    parser.add_argument("--hyperrpc-url", default="")
    parser.add_argument("--skip-index", action="store_true")
    parser.add_argument("--skip-liquidity", action="store_true")
    parser.add_argument("--pool-limit", type=int, default=0)
    parser.add_argument("--max-per-pair", type=int, default=6)
    parser.add_argument("--skip-import", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()

    load_chain_env_files(Path(__file__).resolve())
    database = absolute_database_path(_normalize_database_arg(args.database))
    ensure_database_schema(str(database))
    runtime_root = Path(args.runtime_root) if args.runtime_root else None
    steps: list[dict] = []
    errors: list[str] = []

    if not args.skip_index:
        try:
            index_result = write_v4_pool_index_from_initialize_logs(
                from_block=0,
                to_block=None,
                include_metadata=True,
                mark_recent_swaps=True,
                pool_limit=max(0, args.pool_limit),
                rpc_url=args.rpc_url or None,
                hyperrpc_url=args.hyperrpc_url or None,
            )
            steps.append(_step("v4_pool_index", index_result))
            print(json.dumps({"step": "v4_pool_index", "poolCount": len(index_result.get("pools") or []), "outputPath": index_result.get("outputPath")}, ensure_ascii=False))
        except Exception as exc:
            errors.append(f"v4_pool_index: {exc}")
            steps.append({"step": "v4_pool_index", "ok": False, "error": str(exc)})

    if not errors and not args.skip_liquidity:
        try:
            liquidity_result = write_v4_pool_liquidity_from_contract_evidence(
                rpc_url=args.rpc_url or None,
                hyperrpc_url=args.hyperrpc_url or None,
            )
            steps.append(_step("v4_pool_liquidity", liquidity_result))
            print(json.dumps({"step": "v4_pool_liquidity", "poolCount": len(liquidity_result.get("pools") or []), "outputPath": liquidity_result.get("outputPath")}, ensure_ascii=False))
        except Exception as exc:
            errors.append(f"v4_pool_liquidity: {exc}")
            steps.append({"step": "v4_pool_liquidity", "ok": False, "error": str(exc)})

    if not errors and not args.skip_import:
        try:
            import_result = import_existing_json(
                str(database),
                runtime_root=runtime_root,
            )
            steps.append(_step("import_existing_json", import_result))
            print(json.dumps({"step": "import_existing_json", **import_result}, ensure_ascii=False))
        except Exception as exc:
            errors.append(f"import_existing_json: {exc}")
            steps.append({"step": "import_existing_json", "ok": False, "error": str(exc)})

    if not errors and not args.skip_build:
        try:
            build_result = builders.build_all_anchor_data(str(database))
            steps.append(_step("build_all_anchor_data", build_result))
            print(json.dumps({"step": "build_all_anchor_data", "ok": True}, ensure_ascii=False))
        except Exception as exc:
            errors.append(f"build_all_anchor_data: {exc}")
            steps.append({"step": "build_all_anchor_data", "ok": False, "error": str(exc)})

    cleared = 0
    try:
        cleared = _clear_dex_surface_cache(chain_id=4663, protocols=["uniswap_v4"])
    except Exception as exc:
        errors.append(f"dex_surface_cache: {exc}")

    report = {
        "ok": not errors,
        "database": str(database),
        "steps": steps,
        "errors": errors,
        "cacheEntriesCleared": cleared,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
