"""Collect UI/Binance signal pool evidence until enough live samples exist."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXTREMES_PATH = SRC_ROOT / "runtime" / "state" / "latest_extremes.json"
DEFAULT_POOL_CACHE = SRC_ROOT / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"
DEFAULT_EVIDENCE_ROOT = SRC_ROOT / "runtime" / "evidence" / "ui_signal_pool_quotes"
DEFAULT_LATEST_OUTPUT = SRC_ROOT / "runtime" / "state" / "latest_ui_signal_pool_evidence.json"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from market.binance_market.service import build_binance_market_state, load_cow_supported_token_registry
from market.observer_common import write_json_atomic
from market.runtime.ui_signal_pool_evidence import build_ui_signal_pool_evidence, write_ui_signal_pool_evidence
from market.strategy.arbitrage import ArbitrageConfig


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _run_id() -> str:
    return _utc_now().strftime("%Y%m%dT%H%M%S%fZ_ui-signal-pool-collection")


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _age_seconds(value: Any) -> float | None:
    parsed = _parse_utc(value)
    if parsed is None:
        return None
    return max(0.0, (_utc_now() - parsed).total_seconds())


def _build_current_ui_market_state(
    extremes: dict[str, Any],
    *,
    pair_limit: int,
    min_spread_percent: float,
    min_side_change_percent: float,
    min_token_price_usd: float,
) -> dict[str, Any]:
    token_cache = load_cow_supported_token_registry(cow_network="avalanche", database_url=None)
    return build_binance_market_state(
        extremes,
        aave_symbols=[],
        arbitrage_config=ArbitrageConfig(
            notional_usd=1000.0,
            trade_fee_percent=0.1,
            flashloan_fee_percent=0.05,
        ),
        top_limit=50,
        bottom_limit=50,
        pair_side_limit=max(1, min(int(pair_limit), 5)),
        cow_display_limit=50,
        cow_network="avalanche",
        registry=token_cache.get("registry") or {},
        min_spread_percent=max(0.0, float(min_spread_percent)),
        min_side_change_percent=max(0.0, float(min_side_change_percent)),
        min_token_price_usd=max(0.0, float(min_token_price_usd)),
    )


def _canonical_signal_key(market_state: dict[str, Any]) -> str:
    pairs = []
    for pair in market_state.get("pairs") or []:
        if not isinstance(pair, dict):
            continue
        route_keys = []
        for route in pair.get("route_results") or []:
            if isinstance(route, dict):
                path = route.get("route") or route.get("path") or route.get("route_symbols") or []
                route_keys.append(">".join(str(item) for item in path))
        pairs.append(
            "|".join(
                [
                    str(pair.get("pair") or ""),
                    str(pair.get("x_current_price") or pair.get("x_end_price") or ""),
                    str(pair.get("y_current_price") or pair.get("y_end_price") or ""),
                    ",".join(route_keys),
                ]
            )
        )
    return "||".join(pairs)


def _profit_summary(route: dict[str, Any]) -> dict[str, Any]:
    matrix = route.get("poolQuoteMatrix") if isinstance(route.get("poolQuoteMatrix"), dict) else {}
    profit = matrix.get("contractPathProfit") if isinstance(matrix.get("contractPathProfit"), dict) else {}
    return {
        "routePath": matrix.get("routePath") or route.get("routePath"),
        "priorityReason": route.get("priorityReason"),
        "effectivePoolSlots": matrix.get("effectivePoolSlots"),
        "allTradesHaveFiveEffectivePools": matrix.get("allTradesHaveFiveEffectivePools"),
        "profitStatus": profit.get("status"),
        "profitReason": profit.get("reason"),
        "quotedFinalUsdc": profit.get("quotedFinalUsdc"),
        "requiredFinalUsdc": profit.get("requiredFinalUsdc"),
        "expectedProfitUsdc": profit.get("expectedProfitUsdc"),
        "profitableBeforeGas": profit.get("profitableBeforeGas"),
    }


def _pool_quote_counts(evidence: dict[str, Any]) -> dict[str, int]:
    quoted = failed = empty = effective = 0
    for candidate in evidence.get("candidates") or []:
        for route in candidate.get("routes") or []:
            matrix = route.get("poolQuoteMatrix") if isinstance(route.get("poolQuoteMatrix"), dict) else {}
            for trade in matrix.get("trades") or []:
                try:
                    effective += int(trade.get("effectivePoolCount") or 0)
                except (TypeError, ValueError):
                    pass
                for slot in trade.get("slots") or []:
                    status = slot.get("quoteStatus")
                    quoted += int(status == "quoted")
                    failed += int(status == "quote_failed")
                    empty += int(status == "empty_slot")
    return {
        "effectivePoolSlots": effective,
        "quotedSlots": quoted,
        "quoteFailedSlots": failed,
        "emptySlots": empty,
    }


def _sample_record(
    *,
    index: int,
    evidence: dict[str, Any],
    output: str,
    signal_key: str,
) -> dict[str, Any]:
    first_candidate = (evidence.get("candidates") or [{}])[0]
    signal = first_candidate.get("binanceSignal") if isinstance(first_candidate, dict) else {}
    routes = [
        _profit_summary(route)
        for candidate in evidence.get("candidates") or []
        for route in candidate.get("routes") or []
        if isinstance(route, dict)
    ]
    counts = _pool_quote_counts(evidence)
    return {
        "index": index,
        "recordedAt": _utc_now_iso(),
        "evidencePath": output,
        "signalKey": signal_key,
        "uiObservedAt": (evidence.get("uiSignal") or {}).get("observedAt"),
        "binancePair": signal.get("pair"),
        "windowSpreadPercent": signal.get("windowSpreadPercent"),
        "x": signal.get("x"),
        "y": signal.get("y"),
        "candidateCount": evidence.get("candidateCount"),
        "routeCount": evidence.get("routeCount"),
        "blockNumber": (evidence.get("quoteContext") or {}).get("blockNumber"),
        "rpcError": (evidence.get("quoteContext") or {}).get("rpcError"),
        **counts,
        "routes": routes,
    }


def _write_summary_md(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# UI Signal Pool Evidence Collection",
        "",
        f"- runId: `{report.get('runId')}`",
        f"- startedAt: `{report.get('startedAt')}`",
        f"- stoppedAt: `{report.get('stoppedAt') or ''}`",
        f"- targetSignals: `{report.get('targetSignals')}`",
        f"- collectedSignals: `{report.get('collectedSignals')}`",
        f"- safety: read-only quote collection; no signer, no deployment, no broadcast",
        "",
        "## Samples",
        "",
        "| # | UI observed | Binance pair | routes | quoted slots | expected profit raw USDC | evidence |",
        "|---:|---|---|---:|---:|---|---|",
    ]
    for sample in report.get("samples") or []:
        profits = []
        for route in sample.get("routes") or []:
            value = route.get("expectedProfitUsdc")
            if value is not None:
                profits.append(str(value))
            elif route.get("profitReason"):
                profits.append(str(route.get("profitReason")))
            elif route.get("profitStatus"):
                profits.append(str(route.get("profitStatus")))
        rel = Path(str(sample.get("evidencePath") or "")).name
        lines.append(
            "| {index} | `{observed}` | `{pair}` | {routes} | {quoted} | `{profit}` | `{evidence}` |".format(
                index=sample.get("index"),
                observed=sample.get("uiObservedAt") or "",
                pair=sample.get("binancePair") or "",
                routes=sample.get("routeCount") or "",
                quoted=sample.get("quotedSlots") or 0,
                profit=", ".join(profits[:4]) or "",
                evidence=rel,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _collection_report(
    *,
    run_id: str,
    started_at: str,
    stopped_at: str | None,
    args: argparse.Namespace,
    run_dir: Path,
    samples: list[dict[str, Any]],
    attempts: int,
    skipped: dict[str, int],
) -> dict[str, Any]:
    return {
        "runId": run_id,
        "mode": "ui_signal_pool_quote_collection",
        "startedAt": started_at,
        "stoppedAt": stopped_at,
        "targetSignals": max(1, int(args.target_count)),
        "collectedSignals": len(samples),
        "attemptedSnapshots": attempts,
        "skipped": dict(skipped),
        "paths": {
            "runDir": str(run_dir),
            "index": str(run_dir / "index.json"),
            "summary": str(run_dir / "summary.md"),
            "latestOutput": str(args.latest_output),
            "extremes": str(args.extremes),
            "poolCache": str(args.pool_cache),
        },
        "parameters": {
            "pairLimit": max(1, min(int(args.pair_limit), 5)),
            "pollMs": max(100, int(args.poll_ms)),
            "maxSeconds": max(0.0, float(args.max_seconds)),
            "maxExtremesAgeSeconds": max(0.0, float(args.max_extremes_age_seconds)),
            "minSpreadPercent": max(0.0, float(args.min_spread_percent)),
            "minSideChangePercent": max(0.0, float(args.min_side_change_percent)),
            "minTokenPriceUsd": max(0.0, float(args.min_token_price_usd)),
            "usdcAmount": max(1, int(args.usdc_amount)),
            "minProfitUsdc": max(0, int(args.min_profit_usdc)),
            "premiumBps": max(0, int(args.premium_bps)),
        },
        "safety": {
            "loadsSigner": False,
            "buildsTransaction": False,
            "broadcastsTransaction": False,
            "deploysContract": False,
        },
        "samples": samples,
    }


def _write_report(run_dir: Path, report: dict[str, Any]) -> None:
    write_json_atomic(str(run_dir / "index.json"), report)
    _write_summary_md(run_dir / "summary.md", report)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-count", type=int, default=10)
    parser.add_argument("--extremes", type=Path, default=DEFAULT_EXTREMES_PATH)
    parser.add_argument("--pool-cache", type=Path, default=DEFAULT_POOL_CACHE)
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    parser.add_argument("--latest-output", type=Path, default=DEFAULT_LATEST_OUTPUT)
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--pair-limit", type=int, default=5)
    parser.add_argument("--poll-ms", type=int, default=1_000)
    parser.add_argument("--max-seconds", type=float, default=900.0)
    parser.add_argument("--max-extremes-age-seconds", type=float, default=10.0)
    parser.add_argument("--min-spread-percent", type=float, default=0.0)
    parser.add_argument("--min-side-change-percent", type=float, default=0.0)
    parser.add_argument("--min-token-price-usd", type=float, default=0.01)
    parser.add_argument("--rpc-url", default="")
    parser.add_argument("--usdc-amount", type=int, default=1_000_000_000)
    parser.add_argument("--min-profit-usdc", type=int, default=1)
    parser.add_argument("--premium-bps", type=int, default=5)
    parser.add_argument("--allow-stale", action="store_true")
    return parser.parse_args()


def main() -> int:
    load_env_files(__file__, override=False)
    args = parse_args()
    target_count = max(1, int(args.target_count))
    poll_seconds = max(100, int(args.poll_ms)) / 1000
    max_seconds = max(0.0, float(args.max_seconds))
    run_id = _run_id()
    cwd = Path.cwd()

    def _resolve(path: Path) -> Path:
        return path if path.is_absolute() else (cwd / path).resolve()

    evidence_root = _resolve(args.evidence_root)
    run_dir = _resolve(args.run_dir) if args.run_dir else evidence_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, Any]] = []
    seen_observed: set[str] = set()
    seen_signal_keys: set[str] = set()
    skipped: dict[str, int] = {}
    attempts = 0
    started_at = _utc_now_iso()
    deadline = time.monotonic() + max_seconds if max_seconds > 0 else None

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    while len(samples) < target_count:
        if deadline is not None and time.monotonic() >= deadline:
            break
        extremes = _read_json_object(args.extremes)
        observed_at = str(extremes.get("observed_at") or "")
        if not observed_at:
            skip("extremes_missing_observed_at")
            time.sleep(poll_seconds)
            continue
        if observed_at in seen_observed:
            skip("duplicate_observed_at")
            time.sleep(poll_seconds)
            continue
        age = _age_seconds(observed_at)
        if not args.allow_stale and (age is None or age > max(0.0, float(args.max_extremes_age_seconds))):
            skip("extremes_stale")
            time.sleep(poll_seconds)
            continue

        attempts += 1
        seen_observed.add(observed_at)
        market_state = _build_current_ui_market_state(
            extremes,
            pair_limit=args.pair_limit,
            min_spread_percent=args.min_spread_percent,
            min_side_change_percent=args.min_side_change_percent,
            min_token_price_usd=args.min_token_price_usd,
        )
        if not market_state.get("pairs"):
            skip("no_ui_signal_candidate")
            time.sleep(poll_seconds)
            continue
        signal_key = _canonical_signal_key(market_state)
        if signal_key in seen_signal_keys:
            skip("duplicate_signal_key")
            time.sleep(poll_seconds)
            continue
        seen_signal_keys.add(signal_key)
        evidence = build_ui_signal_pool_evidence(
            market_state=market_state,
            extremes=extremes,
            pool_cache_path=args.pool_cache,
            rpc_url=args.rpc_url,
            usdc_amount=max(1, int(args.usdc_amount)),
            min_profit_usdc=max(0, int(args.min_profit_usdc)),
            premium_bps=max(0, int(args.premium_bps)),
        )
        if evidence.get("candidateCount") == "0":
            skip("evidence_no_candidate")
            time.sleep(poll_seconds)
            continue
        result = write_ui_signal_pool_evidence(
            evidence,
            evidence_dir=run_dir,
            latest_output=args.latest_output,
        )
        sample = _sample_record(
            index=len(samples) + 1,
            evidence=evidence,
            output=str(result.get("output") or ""),
            signal_key=signal_key,
        )
        samples.append(sample)
        report = _collection_report(
            run_id=run_id,
            started_at=started_at,
            stopped_at=None,
            args=args,
            run_dir=run_dir,
            samples=samples,
            attempts=attempts,
            skipped=skipped,
        )
        _write_report(run_dir, report)
        print(
            json.dumps(
                {
                    "collected": len(samples),
                    "target": target_count,
                    "observedAt": observed_at,
                    "pair": sample.get("binancePair"),
                    "routeCount": sample.get("routeCount"),
                    "quotedSlots": sample.get("quotedSlots"),
                    "evidence": sample.get("evidencePath"),
                },
                ensure_ascii=True,
            ),
            flush=True,
        )
        if len(samples) < target_count:
            time.sleep(poll_seconds)

    final_report = _collection_report(
        run_id=run_id,
        started_at=started_at,
        stopped_at=_utc_now_iso(),
        args=args,
        run_dir=run_dir,
        samples=samples,
        attempts=attempts,
        skipped=skipped,
    )
    _write_report(run_dir, final_report)
    print(json.dumps({"ok": len(samples) >= target_count, **final_report}, ensure_ascii=True))
    return 0 if len(samples) >= target_count else 2


if __name__ == "__main__":
    raise SystemExit(main())
