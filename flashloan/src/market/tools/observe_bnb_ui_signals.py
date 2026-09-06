"""Observe BNB UI-triggered signals and verify them with read-only Pancake quotes."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SRC_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SRC_ROOT.parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dex.intent_trade.chains.bnb import config as bnb
from dex.intent_trade.chains.bnb.observer import quote_ui_signal_routes
from dex.intent_trade.chains.bnb.quote import _connect
from market.binance_market.service import (
    build_binance_market_state,
    build_binance_rest_market_snapshot,
    fetch_binance_current_price_rows,
)
from market.strategy.arbitrage import ArbitrageConfig
from market.tools.build_bnb_signal_route_report import write_report as write_route_report


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _percent(numerator: int, denominator: int) -> str:
    return f"{(100 * numerator / denominator):.4f}%" if denominator else "0.0000%"


def _report(summary: dict[str, Any]) -> str:
    lines = [
        "# BNB UI 信号链上影子观察",
        "",
        f"- 开始：`{summary['startedAt']}`",
        f"- 结束：`{summary['endedAt']}`",
        f"- 运行秒数：`{summary['durationSeconds']}`",
        f"- 采样间隔：`{summary['intervalSeconds']}` 秒",
        f"- Binance 快照轮数：`{summary['snapshotCount']}`",
        f"- UI 信号数：`{summary['uiSignalCount']}`",
        f"- 可组装信号数：`{summary['assembledSignalCount']}`（UI 信号命中率 `{summary['uiSignalHitRate']}`）",
        f"- 可组装三跳路径数：`{summary['assembledRouteCount']}`",
        f"- 链上成功报价路径数：`{summary['successfulQuoteCount']}`（可报价率 `{summary['quoteSuccessRate']}`）",
        f"- 成本前正收益路径数：`{summary['profitableBeforeCostsCount']}`（成本前盈利率 `{summary['profitabilityBeforeCostsRate']}`）",
        "",
        "## 解释",
        "",
        "本次只使用 BNB 静态表中的高质量池，并按 UI 的涨跌信号组装三跳闭环。收益仅是当前 Pancake V2 Router / V3 Quoter 的逐跳只读回款减初始本金，未扣除闪电贷费用、链上 gas、滑点保护和交易竞争成本；因此成本前正收益不等于可执行套利。",
        "",
        "## 产物",
        "",
        f"- 原始记录：`{summary['rawJsonlPath']}`",
        f"- 汇总：`{summary['summaryJsonPath']}`",
        f"- 逐跳复盘：`{summary['routeReportPath']}`",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Observe BNB UI signals with read-only Pancake V2/V3 route quotes.")
    parser.add_argument("--duration-seconds", type=int, default=1200)
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    parser.add_argument("--amount", default="1000")
    parser.add_argument("--side-limit", type=int, default=50)
    parser.add_argument("--pair-side-limit", type=int, default=5)
    parser.add_argument("--binance-rest-base", default="https://data-api.binance.vision")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "data_info" / "BNB")
    parser.add_argument("--rpc-url", action="append", default=[])
    args = parser.parse_args()

    duration = max(1, int(args.duration_seconds))
    interval = max(0.5, float(args.interval_seconds))
    output_dir = args.output_dir
    raw_path = output_dir / "BNB_UI信号链上影子观察_原始.jsonl"
    summary_path = output_dir / "BNB_UI信号链上影子观察_汇总.json"
    report_path = output_dir / "BNB_UI信号链上影子观察.md"
    route_report_path = output_dir / "BNB交易信号逐跳复盘.md"
    raw_path.unlink(missing_ok=True)

    w3, rpc_url = _connect(args.rpc_url or list(bnb.DEFAULT_RPC_URLS))
    previous_snapshot = None
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    totals = {
        "snapshotCount": 0,
        "uiSignalCount": 0,
        "assembledSignalCount": 0,
        "assembledRouteCount": 0,
        "successfulQuoteCount": 0,
        "profitableBeforeCostsCount": 0,
    }
    config = ArbitrageConfig(notional_usd=float(args.amount), trade_fee_percent=0.0, flashloan_fee_percent=0.0)

    while time.monotonic() - started_monotonic < duration:
        loop_started = time.monotonic()
        current_rows = fetch_binance_current_price_rows([args.binance_rest_base])
        snapshot = build_binance_rest_market_snapshot(
            side_limit=max(50, int(args.side_limit)),
            previous_snapshot=previous_snapshot,
            current_rows=current_rows,
        )
        previous_snapshot = snapshot
        market_state = build_binance_market_state(
            snapshot,
            aave_symbols=[],
            arbitrage_config=config,
            top_limit=max(1, int(args.side_limit)),
            bottom_limit=max(1, int(args.side_limit)),
            pair_side_limit=max(1, min(5, int(args.pair_side_limit))),
            cow_display_limit=max(1, int(args.side_limit)),
            cow_network="bnb",
            min_spread_percent=0.0,
            min_side_change_percent=0.0,
            min_token_price_usd=0.01,
        )
        result = quote_ui_signal_routes(w3, market_state, amount=args.amount)
        record = {
            "recordedAt": _utc_now(),
            "rpcUrl": rpc_url,
            "snapshot": {
                "observedAt": snapshot.get("observed_at"),
                "windowSeconds": snapshot.get("window_seconds"),
                "sampleCount": snapshot.get("sample_count"),
                "priceSource": snapshot.get("price_source"),
            },
            "marketState": {
                "top": market_state.get("top"),
                "bottom": market_state.get("bottom"),
                "pairCount": market_state.get("pair_count"),
                "cowFilter": market_state.get("cow_filter"),
            },
            "result": result,
        }
        _append_jsonl(raw_path, record)
        totals["snapshotCount"] += 1
        for key in (
            "uiSignalCount",
            "assembledSignalCount",
            "assembledRouteCount",
            "successfulQuoteCount",
            "profitableBeforeCostsCount",
        ):
            totals[key] += int(result.get(key) or 0)
        print(
            "BNB shadow observation",
            f"snapshot={totals['snapshotCount']}",
            f"signals={result['uiSignalCount']}",
            f"assembled={result['assembledRouteCount']}",
            f"quoted={result['successfulQuoteCount']}",
            f"gross_positive={result['profitableBeforeCostsCount']}",
            flush=True,
        )
        remaining = interval - (time.monotonic() - loop_started)
        if remaining > 0:
            time.sleep(remaining)

    summary = {
        "network": "bnb",
        "chainId": 56,
        "mode": "shadow_quote_only",
        "executionEnabled": False,
        "broadcastEnabled": False,
        "amount": str(args.amount),
        "rpcUrl": rpc_url,
        "startedAt": started_at,
        "endedAt": _utc_now(),
        "durationSeconds": duration,
        "intervalSeconds": interval,
        **totals,
        "uiSignalHitRate": _percent(totals["assembledSignalCount"], totals["uiSignalCount"]),
        "quoteSuccessRate": _percent(totals["successfulQuoteCount"], totals["assembledRouteCount"]),
        "profitabilityBeforeCostsRate": _percent(totals["profitableBeforeCostsCount"], totals["successfulQuoteCount"]),
        "rawJsonlPath": str(raw_path),
        "summaryJsonPath": str(summary_path),
        "reportPath": str(report_path),
        "routeReportPath": str(route_report_path),
        "costStatus": "flashloan_gas_slippage_and_competition_not_included",
    }
    _write_json(summary_path, summary)
    report_path.write_text(_report(summary), encoding="utf-8")
    write_route_report(raw_path, route_report_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
