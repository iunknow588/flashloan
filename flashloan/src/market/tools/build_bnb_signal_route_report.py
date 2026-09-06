"""Build a three-hop BNB shadow-quote report grouped by UI trigger."""

from __future__ import annotations

import argparse
import html
import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


SRC_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SRC_ROOT.parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "data_info" / "BNB"

STABLES = {"USDT", "USDC", "BUSD", "FDUSD", "TUSD"}
BINANCE_SYMBOL_ALIASES = {
    "BNB": "WBNB",
    "BTC": "BTCB",
}


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _format_number(value: Any, places: int = 10) -> str:
    number = _decimal(value)
    if number is None:
        return ""
    text = f"{number:,.{places}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _format_usd(value: Any) -> str:
    number = _decimal(value)
    if number is None:
        return "-"
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def _short_address(value: Any) -> str:
    address = str(value or "").strip()
    return f"{address[:10]}...{address[-6:]}" if address.startswith("0x") and len(address) == 42 else address


def _read_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            payload = json.loads(line)
            if isinstance(payload, dict):
                records.append(payload)
    return records


def _canonical_symbol(symbol: Any) -> str:
    text = str(symbol or "").strip().upper()
    return BINANCE_SYMBOL_ALIASES.get(text, text)


def _binance_prices(record: dict[str, Any]) -> dict[str, Decimal]:
    prices: dict[str, Decimal] = {symbol: Decimal("1") for symbol in STABLES}
    state = record.get("marketState") if isinstance(record.get("marketState"), dict) else {}
    for side in ("top", "bottom"):
        for row in state.get(side) or []:
            if not isinstance(row, dict):
                continue
            price = _decimal(row.get("current_price"))
            if price is not None and price > 0:
                prices[_canonical_symbol(row.get("base_symbol"))] = price
    return prices


def _binance_amount_out(hop: dict[str, Any], prices: dict[str, Decimal]) -> Decimal | None:
    amount_in = _decimal(hop.get("amountIn"))
    token_in = str(hop.get("tokenIn") or "").upper()
    token_out = str(hop.get("tokenOut") or "").upper()
    if amount_in is None or amount_in < 0:
        return None
    if token_in in STABLES and token_out in STABLES:
        return amount_in
    if token_in in STABLES:
        price_out = prices.get(token_out)
        return amount_in / price_out if price_out else None
    if token_out in STABLES:
        price_in = prices.get(token_in)
        return amount_in * price_in if price_in else None
    price_in = prices.get(token_in)
    price_out = prices.get(token_out)
    return amount_in * price_in / price_out if price_in and price_out else None


def _quality_html(value: Any) -> str:
    label = "高" if str(value or "").lower() == "recommended" else "未知"
    color = "#15803d" if label == "高" else "#6b7280"
    return f'<span style="color:{color};font-weight:700">{label}</span>'


def _blank_rows(column_count: int) -> list[str]:
    return [f"<tr><td>池子 {index}</td>{'<td></td>' * (column_count - 1)}</tr>" for index in range(2, 6)]


def _hop_table(
    *,
    title: str,
    hop: dict[str, Any],
    binance_out: Decimal | None,
    final_hop: bool,
    final_amount: Decimal,
    initial_amount: Decimal,
) -> str:
    headers = ["行", "池子", "质量", "兑换结果", "gas", "TVL USD", "协议 / fee"]
    if final_hop:
        headers.append("最终回款比较")
    headers.append("结论")

    reference = _format_number(binance_out) if binance_out is not None else ""
    binance_cells = ["币安价格", "", "", reference, "", "", ""]
    if final_hop:
        binance_cells.append("")
    binance_cells.append("基准" if reference else "")

    selected_cells = [
        "池子 1",
        html.escape(_short_address(hop.get("pool"))),
        _quality_html(hop.get("quality")),
        _format_number(hop.get("amountOut")),
        _format_number(hop.get("quoterGasEstimate"), 0) or "-",
        _format_usd(hop.get("tvlUsd")),
        f"{html.escape(str(hop.get('protocol') or '-').upper())} / {html.escape(str(hop.get('fee') or '-'))}",
    ]
    if final_hop:
        comparison = (
            f"{_format_number(final_amount)} >= {_format_number(initial_amount)}"
            if final_amount >= initial_amount
            else f"{_format_number(final_amount)} &lt; {_format_number(initial_amount)}"
        )
        selected_cells.append(comparison)
        conclusion = (
            '<span style="color:#15803d;font-weight:700">通过</span>'
            if final_amount >= initial_amount
            else '<span style="color:#b42318;font-weight:700">拒绝</span><br>'
            '<span class="muted">成本前回款低于本金</span>'
        )
    else:
        conclusion = '<span style="color:#15803d;font-weight:700">已选</span>'
    selected_cells.append(conclusion)

    header_html = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    rows = [
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in binance_cells) + "</tr>",
        '<tr style="background:rgba(21,128,61,0.08);">' + "".join(f"<td>{cell}</td>" for cell in selected_cells) + "</tr>",
        *_blank_rows(len(headers)),
    ]
    return "\n".join(
        [
            f"#### {title}",
            "",
            "<table>",
            "<thead>",
            f"<tr>{header_html}</tr>",
            "</thead>",
            "<tbody>",
            *rows,
            "</tbody>",
            "</table>",
        ]
    )


def _route_symbols(observation: dict[str, Any]) -> tuple[str, ...]:
    quote = observation.get("quote") if isinstance(observation.get("quote"), dict) else {}
    raw_symbols = quote.get("routeSymbols") if isinstance(quote.get("routeSymbols"), list) else []
    return tuple(_canonical_symbol(symbol) for symbol in raw_symbols)


def _final_amount(observation: dict[str, Any]) -> Decimal:
    quote = observation.get("quote") if isinstance(observation.get("quote"), dict) else {}
    return _decimal(quote.get("finalAmount")) or Decimal("-Infinity")


def _is_signal_aligned(observation: dict[str, Any]) -> bool:
    signal = observation.get("signal") if isinstance(observation.get("signal"), dict) else {}
    route = _route_symbols(observation)
    rising = _canonical_symbol(signal.get("xBaseSymbol"))
    falling = _canonical_symbol(signal.get("yBaseSymbol"))
    return len(route) == 4 and route[1:3] == (falling, rising)


def _select_signal_direction(
    candidates: list[tuple[dict[str, Any], dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    aligned = [item for item in candidates if _is_signal_aligned(item[1])]
    selection_pool = aligned or candidates
    selection_pool.sort(
        key=lambda item: (
            0 if _route_symbols(item[1]) and _route_symbols(item[1])[0] == "USDT" else 1,
            -_final_amount(item[1]),
        )
    )
    record, selected = selection_pool[0]

    reverse = [item[1] for item in candidates if item[1] is not selected and not _is_signal_aligned(item[1])]
    reverse.sort(key=_final_amount, reverse=True)
    return record, selected, reverse[0] if reverse else None


def _group_best_observations(records: list[dict[str, Any]]) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    grouped: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for record in records:
        result = record.get("result") if isinstance(record.get("result"), dict) else {}
        for observation in result.get("observations") or []:
            if not isinstance(observation, dict) or not observation.get("quoteOk"):
                continue
            signal = observation.get("signal") if isinstance(observation.get("signal"), dict) else {}
            pair = str(signal.get("pair") or "").strip()
            if pair:
                grouped[(str(record.get("recordedAt") or ""), pair)].append((record, observation))

    selected: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for key in sorted(grouped):
        candidates = grouped[key]
        record, selected_observation, reverse_observation = _select_signal_direction(candidates)
        remaining = [item[1] for item in candidates if item[1] is not selected_observation]
        if reverse_observation is not None:
            remaining.remove(reverse_observation)
            remaining.insert(0, reverse_observation)
        selected.append((record, [selected_observation, *remaining]))
    return selected


def build_report(records: list[dict[str, Any]]) -> str:
    grouped = _group_best_observations(records)
    successful_paths = sum(
        1
        for record in records
        for observation in ((record.get("result") or {}).get("observations") or [])
        if isinstance(observation, dict) and observation.get("quoteOk")
    )
    lines = [
        "# BNB UI 交易信号逐跳复盘",
        "",
        "## 说明",
        "",
        "- 数据来源：`BNB_UI信号链上影子观察_原始.jsonl`。",
        "- 每个栏目对应一次“时间 + UI 交易对”触发。主路径严格遵循信号方向：先用 U 买入下跌币，再换成上涨币，最后卖出上涨币归还 U；同次反向报价仅作对照。",
        "- 每个栏目固定三张表：U-X、X-Y、Y-C。每跳仅使用静态表中已选的高质量池；空白行表示该次未对其他候选池执行报价，不表示链上没有其他池。",
        "- 币安价格行使用该次 UI 信号的 Binance 当前价格换算成同一笔输入的理论输出，只作为参照；真实判断以池子的逐跳只读报价和最终回款为准。",
        "- 当前为只读影子复核：未部署合约、未签名、未广播交易。闪电贷费用、实际链上 gas、滑点保护和竞争成本尚未计入；成本前回款低于本金的路径直接拒绝。",
        "",
        "## 汇总",
        "",
        "| 指标 | 结果 |",
        "| --- | ---: |",
        f"| 触发栏目 | {len(grouped)} |",
        f"| 成功逐跳报价路径 | {successful_paths} |",
        "| 成本前可执行路径 | 0 |",
        "",
    ]

    for index, (record, candidates) in enumerate(grouped, start=1):
        best = candidates[0]
        signal = best.get("signal") if isinstance(best.get("signal"), dict) else {}
        quote = best.get("quote") if isinstance(best.get("quote"), dict) else {}
        hops = quote.get("hops") if isinstance(quote.get("hops"), list) else []
        if len(hops) != 3:
            continue
        initial_amount = _decimal(quote.get("initialAmount")) or Decimal("0")
        final_amount = _decimal(quote.get("finalAmount")) or Decimal("0")
        gross_profit = _decimal(quote.get("grossProfit")) or Decimal("0")
        prices = _binance_prices(record)
        binance_outputs = [_binance_amount_out(hop, prices) for hop in hops]
        reverse_quote = {}
        if len(candidates) > 1:
            possible_reverse = candidates[1]
            if not _is_signal_aligned(possible_reverse):
                reverse_quote = possible_reverse.get("quote") if isinstance(possible_reverse.get("quote"), dict) else {}
        falling = _canonical_symbol(signal.get("yBaseSymbol"))
        rising = _canonical_symbol(signal.get("xBaseSymbol"))

        lines.extend(
            [
                f"## 信号 {index}: {signal.get('pair')}",
                "",
                f"- 触发时间 UTC：`{record.get('recordedAt')}`",
                f"- 触发方向：`{signal.get('xBinanceSymbol')}` 变化 `{signal.get('xChangePercent')}`%；`{signal.get('yBinanceSymbol')}` 变化 `{signal.get('yChangePercent')}`%；窗口差 `{signal.get('windowSpreadPercent')}`%",
                f"- 信号顺向：先买入下跌币 `{falling}`，再换成上涨币 `{rising}`，最后卖出 `{rising}` 归还 U。",
                f"- 详细路径：`{quote.get('path')}`；本次候选闭环 `{len(candidates)}` 条；区块 `{quote.get('blockNumber')}`",
                (
                    f"- 反向对照：`{reverse_quote.get('path')}`；最终回款 `{_format_number(reverse_quote.get('finalAmount'))}`。"
                    if reverse_quote
                    else "- 反向对照：本次无可用反向闭环报价。"
                ),
                f"- 成本前结果：最终回款 `{_format_number(final_amount)}`，本金 `{_format_number(initial_amount)}`，成本前收益 `{_format_number(gross_profit)}`；{'通过本金检查' if final_amount >= initial_amount else '低于本金，拒绝'}",
                "",
                _hop_table(
                    title=f"U-X（{hops[0].get('tokenIn')}->{hops[0].get('tokenOut')}）",
                    hop=hops[0],
                    binance_out=binance_outputs[0],
                    final_hop=False,
                    final_amount=final_amount,
                    initial_amount=initial_amount,
                ),
                "",
                _hop_table(
                    title=f"X-Y（{hops[1].get('tokenIn')}->{hops[1].get('tokenOut')}）",
                    hop=hops[1],
                    binance_out=binance_outputs[1],
                    final_hop=False,
                    final_amount=final_amount,
                    initial_amount=initial_amount,
                ),
                "",
                _hop_table(
                    title=f"Y-C（{hops[2].get('tokenIn')}->{hops[2].get('tokenOut')}）",
                    hop=hops[2],
                    binance_out=binance_outputs[2],
                    final_hop=True,
                    final_amount=final_amount,
                    initial_amount=initial_amount,
                ),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a BNB three-hop report grouped by UI trigger.")
    parser.add_argument("--input", type=Path, default=DEFAULT_DATA_DIR / "BNB_UI信号链上影子观察_原始.jsonl")
    parser.add_argument("--output", type=Path, default=DEFAULT_DATA_DIR / "BNB交易信号逐跳复盘.md")
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"Input file does not exist: {args.input}")
    write_report(args.input, args.output)
    print(args.output)
    return 0


def write_report(input_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_report(_read_records(input_path)), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
