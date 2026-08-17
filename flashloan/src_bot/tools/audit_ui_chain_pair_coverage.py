"""Audit whether UI-visible chain pairs can assemble pools and live quotes."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MARKET_STATE = SRC_ROOT / "runtime" / "state" / "latest_extremes.json"
DEFAULT_OUTPUT = SRC_ROOT / "runtime" / "state" / "latest_ui_chain_pair_coverage.json"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from market.binance_market.service import (
    build_binance_market_state,
    load_cow_supported_token_registry,
)
from strategy.arbitrage import ArbitrageConfig
from tools.assemble_ui_target_contract_params import build_report as build_param_report


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _base_symbol(symbol: Any) -> str:
    text = str(symbol or "").strip().upper()
    for suffix in ("USDT", "USDC"):
        if text.endswith(suffix) and text not in {"USDT", "USDC"}:
            return text[: -len(suffix)]
    return text


def _route_path(pair: dict[str, Any]) -> list[str]:
    route = pair.get("route")
    if isinstance(route, list) and len(route) >= 4:
        return [_base_symbol(item) for item in route[:4]]
    y_symbol = _base_symbol(pair.get("y_base_symbol") or pair.get("y_symbol"))
    x_symbol = _base_symbol(pair.get("x_base_symbol") or pair.get("x_symbol"))
    return ["USDC", y_symbol, x_symbol, "USDC"]


def _compact_param_result(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "assemblyOk": bool(report.get("assemblyOk")),
        "blockedReasons": report.get("blockedReasons") or [],
        "contractCallShape": report.get("contractCallShape") or {},
        "directStaticTableAssembly": report.get("directStaticTableAssembly") or {},
        "runtimeTrades": [
            {
                "strategyStatus": item.get("strategyStatus"),
                "routeSymbols": item.get("routeSymbols"),
                "effectivePoolCount": item.get("effectivePoolCount"),
                "poolSlotCount": item.get("poolSlotCount"),
            }
            for item in report.get("runtimeTrades") or []
        ],
    }


def build_report(
    *,
    market_state_path: Path = DEFAULT_MARKET_STATE,
    pair_limit: int = 25,
    quote_sample_limit: int = 5,
) -> dict[str, Any]:
    load_env_files(__file__)
    extremes = _read_json(market_state_path)
    token_cache = load_cow_supported_token_registry(cow_network="avalanche", database_url=None)
    market_state = build_binance_market_state(
        extremes,
        aave_symbols=[],
        arbitrage_config=ArbitrageConfig(
            notional_usd=1000.0,
            trade_fee_percent=0.1,
            flashloan_fee_percent=0.05,
        ),
        cow_network="avalanche",
        registry=token_cache.get("registry") or {},
        top_limit=10,
        bottom_limit=10,
        pair_side_limit=10,
        cow_display_limit=10,
    )
    pairs = list(market_state.get("pairs") or [])[: max(1, int(pair_limit))]
    results = []
    for pair in pairs:
        route = _route_path(pair)
        param_report = build_param_report(
            route,
            pool_cache=SRC_ROOT / "runtime" / "cache" / "avalanche_l1_pool_static_table.json",
        )
        results.append(
            {
                "pair": pair.get("pair"),
                "routePath": route,
                "candidateBasis": pair.get("candidate_basis"),
                "triggerSource": pair.get("trigger_source"),
                "quoteRequired": pair.get("quote_required"),
                "paramAssembly": _compact_param_result(param_report),
            }
        )

    quotable = [
        item
        for item in results
        if int((item["paramAssembly"].get("contractCallShape") or {}).get("runtimeTradeSpecs") or 0) == 4
        and int((item["paramAssembly"].get("contractCallShape") or {}).get("effectivePoolSlots") or 0) > 0
    ]
    live_quote_samples = []
    for item in quotable[: max(0, int(quote_sample_limit))]:
        output = SRC_ROOT / "runtime" / "state" / f"ui_chain_pair_quote_{item['routePath'][1]}_{item['routePath'][2]}.json"
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SRC_ROOT / "tools" / "probe_signal_pool_quotes.py"),
                    "--signal",
                    str(_synthetic_signal_file(item["routePath"])),
                    "--output",
                    str(output),
                ],
                cwd=str(SRC_ROOT.parent.parent),
                text=True,
                capture_output=True,
                timeout=120,
            )
            live_quote_samples.append(
                {
                    "routePath": item["routePath"],
                    "ok": proc.returncode == 0,
                    "output": str(output),
                    "stdout": proc.stdout[-1000:],
                    "stderr": proc.stderr[-1000:],
                }
            )
        except Exception as exc:
            live_quote_samples.append({"routePath": item["routePath"], "ok": False, "error": str(exc)})

    return {
        "mode": "ui_chain_pair_coverage_audit",
        "marketStatePath": str(market_state_path),
        "cowFilter": market_state.get("cow_filter"),
        "uiPairCountAudited": str(len(results)),
        "paramAssembledCount": str(sum(1 for item in results if item["paramAssembly"]["assemblyOk"])),
        "runtimeTradeShapeCount": str(
            sum(
                1
                for item in results
                if int((item["paramAssembly"].get("contractCallShape") or {}).get("runtimeTradeSpecs") or 0) == 4
            )
        ),
        "liveQuoteSampleCount": str(len(live_quote_samples)),
        "results": results,
        "liveQuoteSamples": live_quote_samples,
    }


def _synthetic_signal_file(route_path: list[str]) -> Path:
    safe = "_".join(route_path[1:3]).replace(".", "_")
    path = SRC_ROOT / "runtime" / "state" / f"synthetic_signal_{safe}.json"
    payload = {
        "inputObservedAt": None,
        "intentTradeDraft": {"route_path": route_path},
        "profitEstimate": {"paperSignalProfitUsdc": None},
    }
    _write_json_atomic(path, payload)
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-state", type=Path, default=DEFAULT_MARKET_STATE)
    parser.add_argument("--pair-limit", type=int, default=25)
    parser.add_argument("--quote-sample-limit", type=int, default=5)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_report(
        market_state_path=args.market_state,
        pair_limit=args.pair_limit,
        quote_sample_limit=args.quote_sample_limit,
    )
    _write_json_atomic(args.output, report)
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output),
                "uiPairCountAudited": report["uiPairCountAudited"],
                "paramAssembledCount": report["paramAssembledCount"],
                "runtimeTradeShapeCount": report["runtimeTradeShapeCount"],
                "liveQuoteSampleCount": report["liveQuoteSampleCount"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
