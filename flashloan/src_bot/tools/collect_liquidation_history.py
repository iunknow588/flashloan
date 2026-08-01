from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from web3 import Web3

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from execution.liquidation_accounts import topic_to_address
from execution.liquidation_realtime_params import read_aave_flashloan_premium


load_env_files(__file__)

LIQUIDATION_CALL_TOPIC = Web3.keccak(
    text="LiquidationCall(address,address,address,uint256,uint256,address,bool)"
).hex()
AVALANCHE_BLOCK_SECONDS = 2.0


@dataclass(frozen=True)
class LiquidationHistoryConfig:
    rpc_url: str
    pool_address: str
    days: int = 30
    chunk_size: int = 4000
    include_receipts: bool = False
    competition_window_blocks: int = 20
    fallback_flashloan_premium_percent: float = 0.05


def _hex_data_words(data: Any) -> list[int]:
    raw = data.hex() if isinstance(data, (bytes, bytearray)) else str(data or "")
    raw = raw[2:] if raw.startswith("0x") else raw
    words = []
    for offset in range(0, len(raw), 64):
        word = raw[offset : offset + 64]
        if len(word) == 64:
            words.append(int(word, 16))
    return words


def _word_address(value: int) -> str:
    return Web3.to_checksum_address(f"0x{value & ((1 << 160) - 1):040x}")


def decode_liquidation_call_log(log: dict[str, Any]) -> dict[str, Any]:
    topics = list(log.get("topics") or [])
    if len(topics) < 4:
        raise ValueError("LiquidationCall log is missing indexed topics")
    words = _hex_data_words(log.get("data"))
    if len(words) < 4:
        raise ValueError("LiquidationCall log is missing event data")
    return {
        "block_number": int(log.get("blockNumber") or 0),
        "transaction_hash": log.get("transactionHash").hex() if hasattr(log.get("transactionHash"), "hex") else str(log.get("transactionHash") or ""),
        "log_index": int(log.get("logIndex") or 0),
        "collateral_asset": topic_to_address(topics[1]),
        "debt_asset": topic_to_address(topics[2]),
        "user": topic_to_address(topics[3]),
        "debt_to_cover": int(words[0]),
        "liquidated_collateral_amount": int(words[1]),
        "liquidator": _word_address(words[2]),
        "receive_a_token": bool(words[3]),
    }


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * max(0.0, min(1.0, float(q)))
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize_liquidation_events(
    events: list[dict[str, Any]],
    *,
    days: int,
    competition_window_blocks: int = 20,
) -> dict[str, Any]:
    users = {event["user"] for event in events}
    liquidators = {event["liquidator"] for event in events}
    collateral_assets = Counter(event["collateral_asset"] for event in events)
    debt_assets = Counter(event["debt_asset"] for event in events)
    gas_used = [float(event.get("gas_used") or 0) for event in events if event.get("gas_used")]
    effective_gas_price = [float(event.get("effective_gas_price") or 0) for event in events if event.get("effective_gas_price")]
    net_profit_usd = [
        float(event.get("estimated_net_profit_usd"))
        for event in events
        if event.get("estimated_net_profit_usd") is not None
    ]
    slippage_bps = [
        float(event.get("estimated_slippage_bps"))
        for event in events
        if event.get("estimated_slippage_bps") is not None
    ]

    by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_user[event["user"]].append(event)
    competition_counts: list[int] = []
    for rows in by_user.values():
        ordered = sorted(rows, key=lambda item: (int(item["block_number"]), int(item.get("log_index") or 0)))
        for index, event in enumerate(ordered):
            start = int(event["block_number"])
            competitors = {
                row["liquidator"]
                for row in ordered[index:]
                if int(row["block_number"]) - start <= int(competition_window_blocks)
            }
            competition_counts.append(len(competitors))

    return {
        "event_count": len(events),
        "unique_user_count": len(users),
        "unique_liquidator_count": len(liquidators),
        "daily_average": len(events) / max(1, int(days)),
        "competition": {
            "window_blocks": int(competition_window_blocks),
            "avg_competitors": sum(competition_counts) / len(competition_counts) if competition_counts else 0.0,
            "p90_competitors": percentile([float(value) for value in competition_counts], 0.9),
        },
        "gas": {
            "p50_gas_used": percentile(gas_used, 0.5),
            "p90_gas_used": percentile(gas_used, 0.9),
            "p50_effective_gas_price_wei": percentile(effective_gas_price, 0.5),
            "p90_effective_gas_price_wei": percentile(effective_gas_price, 0.9),
        },
        "profit_usd": {
            "status": "calculated" if net_profit_usd else "missing_price_inputs",
            "average": sum(net_profit_usd) / len(net_profit_usd) if net_profit_usd else 0.0,
            "p50": percentile(net_profit_usd, 0.5),
            "p90": percentile(net_profit_usd, 0.9),
            "sample_count": len(net_profit_usd),
        },
        "slippage_bps": {
            "status": "calculated" if slippage_bps else "missing_quote_inputs",
            "average": sum(slippage_bps) / len(slippage_bps) if slippage_bps else 0.0,
            "p50": percentile(slippage_bps, 0.5),
            "p90": percentile(slippage_bps, 0.9),
            "sample_count": len(slippage_bps),
        },
        "top_collateral_assets": collateral_assets.most_common(10),
        "top_debt_assets": debt_assets.most_common(10),
    }


def estimate_start_block(latest_block: int, days: int, block_seconds: float = AVALANCHE_BLOCK_SECONDS) -> int:
    blocks = int(max(1, float(days)) * 24 * 60 * 60 / max(0.1, float(block_seconds)))
    return max(0, int(latest_block) - blocks)


def collect_liquidation_events(w3: Web3, config: LiquidationHistoryConfig) -> list[dict[str, Any]]:
    latest_block = int(w3.eth.block_number)
    from_block = estimate_start_block(latest_block, config.days)
    events: list[dict[str, Any]] = []
    def get_logs_with_retry(chunk_start: int, chunk_end: int) -> list[dict[str, Any]]:
        params = {
            "address": Web3.to_checksum_address(config.pool_address),
            "fromBlock": chunk_start,
            "toBlock": chunk_end,
            "topics": [LIQUIDATION_CALL_TOPIC],
        }
        try:
            return list(w3.eth.get_logs(params))
        except ValueError as exc:
            message = str(exc).lower()
            too_many_blocks = "too many blocks" in message or "maximum is set to" in message
            if not too_many_blocks or chunk_start >= chunk_end:
                raise
            midpoint = (chunk_start + chunk_end) // 2
            return get_logs_with_retry(chunk_start, midpoint) + get_logs_with_retry(midpoint + 1, chunk_end)

    for chunk_start in range(from_block, latest_block + 1, max(1, int(config.chunk_size))):
        chunk_end = min(latest_block, chunk_start + max(1, int(config.chunk_size)) - 1)
        logs = get_logs_with_retry(chunk_start, chunk_end)
        for log in logs:
            event = decode_liquidation_call_log(log)
            if config.include_receipts and event["transaction_hash"]:
                try:
                    receipt = w3.eth.get_transaction_receipt(event["transaction_hash"])
                    event["gas_used"] = int(receipt.get("gasUsed") or 0)
                    event["effective_gas_price"] = int(receipt.get("effectiveGasPrice") or 0)
                except Exception as exc:
                    event["receipt_error"] = str(exc)
            events.append(event)
    events.sort(key=lambda item: (item["block_number"], item["log_index"]))
    return events


def build_liquidation_history_report(w3: Web3, config: LiquidationHistoryConfig) -> dict[str, Any]:
    events = collect_liquidation_events(w3, config)
    premium = read_aave_flashloan_premium(
        config.rpc_url,
        config.pool_address,
        fallback_percent=config.fallback_flashloan_premium_percent,
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": {
            "days": config.days,
            "pool_address": Web3.to_checksum_address(config.pool_address),
            "chunk_size": config.chunk_size,
            "include_receipts": config.include_receipts,
            "competition_window_blocks": config.competition_window_blocks,
        },
        "flashloan_premium": premium,
        "summary": summarize_liquidation_events(
            events,
            days=config.days,
            competition_window_blocks=config.competition_window_blocks,
        ),
        "events": events,
    }


def report_result_table_row(label: str, report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    profit = summary.get("profit_usd") or {}
    slippage = summary.get("slippage_bps") or {}
    gas = summary.get("gas") or {}
    return (
        f"| {label} | {summary.get('event_count', 0)} | "
        f"{summary.get('daily_average', 0.0):.2f} | "
        f"{profit.get('average', 0.0):.2f} | {profit.get('p50', 0.0):.2f} | {profit.get('p90', 0.0):.2f} | "
        f"{summary.get('competition', {}).get('avg_competitors', 0.0):.2f} | "
        f"{slippage.get('p50', 0.0):.2f}/{slippage.get('p90', 0.0):.2f} | "
        f"{gas.get('p50_gas_used', 0.0):.0f}/{gas.get('p90_gas_used', 0.0):.0f} | "
        f"{profit.get('status')} / {slippage.get('status')} |"
    )


def build_markdown_summary(reports: Iterable[dict[str, Any]]) -> str:
    rows = [
        "| 窗口 | 事件数 | 日均机会 | 平均净利润USD | P50净利润 | P90净利润 | 平均竞争数 | 滑点P50/P90 BPS | Gas P50/P90 | 价格/报价状态 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for report in reports:
        days = int((report.get("config") or {}).get("days") or 0)
        rows.append(report_result_table_row(f"{days} 天", report))
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return "\n".join(
        [
            f"### {generated_at} 链上事件采集结果",
            "",
            *rows,
            "",
            "备注：净利润与滑点只在报告包含可审计价格/DEX报价字段时标记为 calculated；否则保持缺失状态，避免用日志事件推导不可验证的利润结论。",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Avalanche Aave V3 LiquidationCall history.")
    parser.add_argument("--rpc-url", default=os.getenv("AVALANCHE_RPC", os.getenv("AVALANCHE_RPC_URL", "")).strip())
    parser.add_argument("--pool", default=os.getenv("AAVE_POOL_ADDRESS", "").strip())
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--chunk-size", type=int, default=4000)
    parser.add_argument("--include-receipts", action="store_true")
    parser.add_argument("--competition-window-blocks", type=int, default=20)
    parser.add_argument("--output", default="")
    parser.add_argument("--markdown-output", default="")
    args = parser.parse_args()
    if not args.rpc_url or not args.pool:
        raise SystemExit("AVALANCHE_RPC/AVALANCHE_RPC_URL and AAVE_POOL_ADDRESS are required")
    w3 = Web3(Web3.HTTPProvider(args.rpc_url, request_kwargs={"timeout": 20}))
    report = build_liquidation_history_report(
        w3,
        LiquidationHistoryConfig(
            rpc_url=args.rpc_url,
            pool_address=args.pool,
            days=args.days,
            chunk_size=args.chunk_size,
            include_receipts=args.include_receipts,
            competition_window_blocks=args.competition_window_blocks,
        ),
    )
    output = args.output.strip()
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.markdown_output.strip():
        path = Path(args.markdown_output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(build_markdown_summary([report]), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("generated_at", "config", "flashloan_premium", "summary")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
