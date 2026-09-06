from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from bot.execution.venus_accounts import estimate_start_block_by_lookback_days
from bot.execution.venus_markets import DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR
from dex.intent_trade.chains.bnb import config as bnb
from bot.tools.rebuild_bnb_venus_borrowers import _connect_rpc_pool, _filter_log_capable_rpc_pool, main as rebuild_borrowers_main


def _load_markets(path: Path) -> list[str]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("markets") or []
    rows = sorted(rows, key=lambda row: (not bool(row.get("high_liquidity")), bool(row.get("collateral_disabled")), not bool(row.get("stable")), str(row.get("vtoken_symbol") or "")))
    return [str(row.get("vtoken_address") or "").strip() for row in rows if str(row.get("vtoken_address") or "").strip()]


def _write_progress(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _batch_markets(markets: list[str], batch_size: int) -> list[list[str]]:
    size = max(1, int(batch_size or 8))
    return [markets[offset : offset + size] for offset in range(0, len(markets), size)]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resume BNB Venus two-month account discovery market by market.")
    parser.add_argument("--rpc-url", action="append", default=[])
    parser.add_argument("--market-table", type=Path, default=DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR / "venus_market_static_table.json")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR)
    parser.add_argument("--from-block", type=int, default=None)
    parser.add_argument("--to-block", type=int, default=None)
    parser.add_argument("--lookback-days", type=int, default=60)
    parser.add_argument("--recent-blocks", type=int, default=0, help="Limit each market scan to the latest N blocks inside the lookback window.")
    parser.add_argument("--chunk-size", type=int, default=250)
    parser.add_argument("--min-chunk-size", type=int, default=10)
    parser.add_argument("--adaptive-chunk", action="store_true", default=True)
    parser.add_argument("--no-adaptive-chunk", action="store_false", dest="adaptive_chunk")
    parser.add_argument("--newest-first", action="store_true", default=True)
    parser.add_argument("--oldest-first", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true", default=True)
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--address-batch-size", type=int, default=8)
    parser.add_argument("--request-sleep-seconds", type=float, default=0.05)
    parser.add_argument("--error-retry-attempts", type=int, default=2)
    parser.add_argument("--error-retry-sleep-seconds", type=float, default=5.0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--start-index", type=int, default=1, help="1-based market index in the sorted market list.")
    parser.add_argument("--max-markets", type=int, default=0)
    parser.add_argument("--progress-file", type=Path, default=DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR / "venus_two_month_market_scan_progress.json")
    parser.add_argument("--events-jsonl", type=Path, default=DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR / "venus_borrower_events_2mo.jsonl")
    parser.add_argument("--event", action="append", default=["Borrow"])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_env_files(__file__)
    args = parse_args(list(argv or sys.argv[1:]))
    markets = _load_markets(args.market_table)
    if not markets:
        raise SystemExit("no markets found; build venus_market_static_table.json first")
    start_index = max(1, int(args.start_index or 1))
    selected_markets = markets[start_index - 1 :]
    if int(args.max_markets or 0) > 0:
        selected_markets = selected_markets[: int(args.max_markets)]

    rpc_inputs = args.rpc_url or list(bnb.log_rpc_urls_from_env())
    w3, rpc_pool, rpc_urls = _connect_rpc_pool(rpc_inputs)
    rpc_pool, rpc_urls, log_probe_failures = _filter_log_capable_rpc_pool(rpc_pool, rpc_urls, probe_vtoken=markets[0])
    if not rpc_pool:
        raise RuntimeError("no BNB RPC supports Venus eth_getLogs probe: " + " | ".join(log_probe_failures[-3:]))
    w3 = rpc_pool[0]
    from_block = int(args.from_block) if args.from_block is not None else estimate_start_block_by_lookback_days(w3, args.lookback_days)
    to_block = int(args.to_block) if args.to_block is not None else int(w3.eth.block_number)
    effective_from_block = from_block
    if int(args.recent_blocks or 0) > 0:
        effective_from_block = max(from_block, to_block - int(args.recent_blocks) + 1)

    _write_progress(
        args.progress_file,
        {
            "status": "started",
            "market_count": len(markets),
            "selected_market_count": len(selected_markets),
            "start_index": start_index,
            "from_block": effective_from_block,
            "lookback_from_block": from_block,
            "to_block": to_block,
            "lookback_days": int(args.lookback_days),
            "recent_blocks": int(args.recent_blocks or 0),
            "rpc_urls": rpc_urls,
            "log_rpc_probe_failures": log_probe_failures[-20:],
        },
    )

    newest_first = bool(args.newest_first and not args.oldest_first)
    continue_on_error = bool(args.continue_on_error and not args.stop_on_error)
    errors: list[dict[str, Any]] = []
    market_batches = _batch_markets(selected_markets, args.address_batch_size)
    for batch_offset, market_batch in enumerate(market_batches):
        index = start_index + batch_offset * max(1, int(args.address_batch_size))
        end_index = index + len(market_batch) - 1
        child_args = [
            "--output-dir",
            str(args.output_dir),
            "--from-block",
            str(effective_from_block),
            "--to-block",
            str(to_block),
            "--chunk-size",
            str(args.chunk_size),
            "--min-chunk-size",
            str(args.min_chunk_size),
            "--address-batch-size",
            str(args.address_batch_size),
            "--request-sleep-seconds",
            str(args.request_sleep_seconds),
            "--error-retry-attempts",
            str(args.error_retry_attempts),
            "--error-retry-sleep-seconds",
            str(args.error_retry_sleep_seconds),
            "--limit",
            str(args.limit),
            "--events-jsonl",
            str(args.events_jsonl),
        ]
        for market in market_batch:
            child_args.extend(["--vtoken", market])
        for event_name in args.event:
            child_args.extend(["--event", event_name])
        for url in rpc_urls:
            child_args.extend(["--rpc-url", url])
        if args.adaptive_chunk:
            child_args.append("--adaptive-chunk")
        if newest_first:
            child_args.append("--newest-first")
        if continue_on_error:
            child_args.append("--continue-on-error")
        progress = {
            "status": "running_market",
            "market_index": index,
            "market_end_index": end_index,
            "market_count": len(markets),
            "selected_market_count": len(selected_markets),
            "vtoken": market_batch[0],
            "vtoken_count": len(market_batch),
            "vtokens": market_batch,
            "from_block": effective_from_block,
            "lookback_from_block": from_block,
            "to_block": to_block,
            "recent_blocks": int(args.recent_blocks or 0),
            "rpc_urls": rpc_urls,
            "log_rpc_probe_failures": log_probe_failures[-20:],
        }
        _write_progress(args.progress_file, progress)
        print(json.dumps(progress, ensure_ascii=False), flush=True)
        try:
            code = rebuild_borrowers_main(child_args)
        except Exception as exc:
            code = 1
            errors.append({"market_index": index, "market_end_index": end_index, "vtokens": market_batch, "error": str(exc)})
            if not continue_on_error:
                raise
        if code != 0:
            errors.append({"market_index": index, "market_end_index": end_index, "vtokens": market_batch, "exit_code": code})
            if not continue_on_error:
                return code
        _write_progress(
            args.progress_file,
            {
                "status": "market_completed",
                "market_index": index,
                "market_end_index": end_index,
                "market_count": len(markets),
                "selected_market_count": len(selected_markets),
                "vtoken": market_batch[0],
                "vtoken_count": len(market_batch),
                "vtokens": market_batch,
                "from_block": effective_from_block,
                "lookback_from_block": from_block,
                "to_block": to_block,
                "recent_blocks": int(args.recent_blocks or 0),
                "errors": errors,
                "rpc_urls": rpc_urls,
                "log_rpc_probe_failures": log_probe_failures[-20:],
            },
        )
    _write_progress(
        args.progress_file,
        {
            "status": "completed",
            "market_count": len(markets),
            "selected_market_count": len(selected_markets),
            "from_block": effective_from_block,
            "lookback_from_block": from_block,
            "to_block": to_block,
            "recent_blocks": int(args.recent_blocks or 0),
            "errors": errors,
            "rpc_urls": rpc_urls,
            "log_rpc_probe_failures": log_probe_failures[-20:],
        },
    )
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
