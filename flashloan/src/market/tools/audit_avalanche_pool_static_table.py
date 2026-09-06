"""Audit the local Avalanche pool static table used for contract param assembly."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POOL_CACHE = SRC_ROOT / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"
DEFAULT_OUTPUT = SRC_ROOT / "runtime" / "state" / "latest_avalanche_pool_static_table_audit.json"
STABLE_SYMBOLS = {"USDC", "USDC.E", "USDT", "USDT.E", "USDT", "USDE", "SUSDE", "DAI", "DAI.E", "FRAX", "EURC"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_symbol(symbol: Any) -> str:
    text = str(symbol or "").strip().upper()
    return text


def _is_stable(symbol: Any) -> bool:
    return _base_symbol(symbol) in STABLE_SYMBOLS


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _effective_pools(entry: dict[str, Any]) -> list[dict[str, Any]]:
    rows = entry.get("pools") if isinstance(entry.get("pools"), list) else [entry]
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if int(row.get("adapterKind", row.get("adapter_kind", 1)) or 0) != 1:
            continue
        if not str(row.get("pool") or row.get("pool_address") or row.get("address") or "").strip():
            continue
        result.append(row)
    return result


def build_report(pool_cache: Path = DEFAULT_POOL_CACHE) -> dict[str, Any]:
    cache = _read_json(pool_cache)
    pairs = []
    for entry in cache.get("pools") or []:
        if not isinstance(entry, dict):
            continue
        x_symbol = _base_symbol(entry.get("tokenX_symbol") or entry.get("token0_symbol"))
        y_symbol = _base_symbol(entry.get("tokenY_symbol") or entry.get("token1_symbol"))
        pools = _effective_pools(entry)
        quality_pools = [
            pool for pool in pools
            if str(pool.get("quality") or "").lower() == "pass"
        ]
        kind = (
            "stable_stable"
            if _is_stable(x_symbol) and _is_stable(y_symbol)
            else "stable_nonstable"
            if _is_stable(x_symbol) or _is_stable(y_symbol)
            else "nonstable_nonstable"
        )
        pairs.append(
            {
                "pair": f"{x_symbol}:{y_symbol}",
                "tokenXSymbol": x_symbol,
                "tokenYSymbol": y_symbol,
                "kind": kind,
                "effectivePoolCount": str(len(pools)),
                "qualityPoolCount": str(len(quality_pools)) if any("quality" in pool for pool in pools) else "",
                "hasFiveEffectivePools": len(pools) >= 5,
                "hasFiveQualityPools": len(quality_pools) >= 5 if any("quality" in pool for pool in pools) else False,
                "fees": [str(pool.get("fee") or "") for pool in pools],
                "qualityFees": [str(pool.get("fee") or "") for pool in quality_pools],
                "pools": [str(pool.get("pool") or pool.get("pool_address") or pool.get("address") or "") for pool in pools],
                "qualityPools": [
                    str(pool.get("pool") or pool.get("pool_address") or pool.get("address") or "")
                    for pool in quality_pools
                ],
            }
        )
    nonstable_pairs = [item for item in pairs if item["kind"] == "nonstable_nonstable"]
    stable_nonstable_pairs = [item for item in pairs if item["kind"] == "stable_nonstable"]
    five_pool_pairs = [item for item in pairs if item["hasFiveEffectivePools"]]
    five_quality_pairs = [item for item in pairs if item["hasFiveQualityPools"]]
    return {
        "generatedAt": _utc_now_iso(),
        "mode": "avalanche_pool_static_table_audit",
        "poolStaticTable": {
            "path": str(pool_cache),
            "exists": pool_cache.exists(),
            "network": str(cache.get("network") or ""),
            "chainId": str(cache.get("chain_id") or ""),
            "factory": str(cache.get("factory") or ""),
            "router": str(cache.get("router") or ""),
            "quoter": str(cache.get("quoter") or ""),
            "cacheBlockNumber": str(cache.get("block_number") or ""),
            "fetchedAt": str(cache.get("fetched_at") or ""),
        },
        "summary": {
            "pairCount": str(len(pairs)),
            "stableNonstablePairCount": str(len(stable_nonstable_pairs)),
            "nonstableNonstablePairCount": str(len(nonstable_pairs)),
            "pairsWithFiveEffectivePools": str(len(five_pool_pairs)),
            "pairsWithFiveQualityPools": str(len(five_quality_pairs)),
            "maxPoolsPerPair": str(max((int(item["effectivePoolCount"]) for item in pairs), default=0)),
            "maxQualityPoolsPerPair": str(
                max((int(item["qualityPoolCount"] or 0) for item in pairs), default=0)
            ),
        },
        "contractAssemblyImplication": {
            "requiredPoolSlotsPerTrade": "5",
            "requiredTradeSpecsPerTriangularTarget": "4",
            "requiredPoolSlotsPerTarget": "20",
            "note": "A route can be assembled with fewer than five effective pools because empty slots are padded, but every missing pool reduces price-spread evidence.",
        },
        "pairs": pairs,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool-cache", type=Path, default=DEFAULT_POOL_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pool_cache = args.pool_cache if args.pool_cache.is_absolute() else SRC_ROOT / args.pool_cache
    report = build_report(pool_cache)
    _write_json_atomic(args.output, report)
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output),
                "summary": report["summary"],
                "poolStaticTable": report["poolStaticTable"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
