"""Audit all triangular targets derivable from the Avalanche pool static table."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POOL_CACHE = SRC_ROOT / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"
DEFAULT_OUTPUT = SRC_ROOT / "runtime" / "state" / "latest_static_table_triangular_coverage.json"

if str(SRC_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SRC_ROOT))

from dex.tools.assemble_ui_target_contract_params import build_report as build_param_report


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _symbol(entry: dict[str, Any], side: str) -> str:
    return str(entry.get(f"token{side}_symbol") or "").strip().upper()


def _effective_count(entry: dict[str, Any]) -> int:
    rows = entry.get("pools") if isinstance(entry.get("pools"), list) else [entry]
    return sum(1 for row in rows if isinstance(row, dict) and row.get("pool"))


def _pair_key(a: str, b: str) -> tuple[str, str]:
    return tuple(sorted((a.upper(), b.upper())))


def _static_pairs(cache: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    pairs = {}
    for entry in cache.get("pools") or []:
        if not isinstance(entry, dict):
            continue
        x = _symbol(entry, "X")
        y = _symbol(entry, "Y")
        if x and y:
            pairs[_pair_key(x, y)] = entry
    return pairs


def _usdc_symbols(pairs: dict[tuple[str, str], dict[str, Any]]) -> list[str]:
    symbols = set()
    for a, b in pairs:
        if a == "USDC" and b != "USDC":
            symbols.add(b)
        elif b == "USDC" and a != "USDC":
            symbols.add(a)
    return sorted(symbols)


def build_report(pool_cache: Path = DEFAULT_POOL_CACHE) -> dict[str, Any]:
    cache = _read_json(pool_cache)
    pairs = _static_pairs(cache)
    symbols = _usdc_symbols(pairs)
    routes = []
    for index, x in enumerate(symbols):
        for y in symbols[index + 1 :]:
            if _pair_key(x, y) not in pairs:
                continue
            route_path = ["USDC", x, y, "USDC"]
            param = build_param_report(route_path, pool_cache=pool_cache)
            counts = {
                "usdcX": _effective_count(pairs[_pair_key("USDC", x)]),
                "usdcY": _effective_count(pairs[_pair_key("USDC", y)]),
                "xy": _effective_count(pairs[_pair_key(x, y)]),
            }
            routes.append(
                {
                    "routePath": route_path,
                    "pairPoolCounts": {key: str(value) for key, value in counts.items()},
                    "minPairPoolCount": str(min(counts.values())),
                    "paramAssemblyOk": bool(param.get("assemblyOk")),
                    "runtimeTradeSpecs": (param.get("contractCallShape") or {}).get("runtimeTradeSpecs"),
                    "poolSlots": (param.get("contractCallShape") or {}).get("poolSlots"),
                    "effectivePoolSlots": (param.get("contractCallShape") or {}).get("effectivePoolSlots"),
                    "blockedReasons": param.get("blockedReasons") or [],
                }
            )
    return {
        "mode": "static_table_triangular_coverage",
        "poolCache": {
            "path": str(pool_cache),
            "pairCount": str(len(pairs)),
            "usdcConnectedSymbolCount": str(len(symbols)),
        },
        "summary": {
            "triangularRouteCount": str(len(routes)),
            "runtimeTradeShapeCount": str(sum(1 for r in routes if r["runtimeTradeSpecs"] == "4")),
            "assemblyOkCount": str(sum(1 for r in routes if r["paramAssemblyOk"])),
            "routesWithAllPairsAtLeastTwoPools": str(sum(1 for r in routes if int(r["minPairPoolCount"]) >= 2)),
            "routesWithAllPairsAtLeastFivePools": str(sum(1 for r in routes if int(r["minPairPoolCount"]) >= 5)),
        },
        "routes": routes,
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
    print(json.dumps({"ok": True, "output": str(args.output), "summary": report["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
