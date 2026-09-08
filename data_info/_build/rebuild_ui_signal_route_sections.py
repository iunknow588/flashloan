"""Rebuild the UI signal route sections in `data_info/鏁版嵁.md`.

This keeps the existing static-table block intact, but rewrites the signal
sections into three-hop tables per route so the comparison is easier to scan.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = REPO_ROOT / "data_info" / "AVALANCHE" / "鏁版嵁.md"
POOL_CACHE_PATH = REPO_ROOT / "flashloan" / "app_runtime" / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"
EVIDENCE_ROOT = REPO_ROOT / "flashloan" / "app_runtime" / "runtime" / "evidence" / "ui_signal_pool_quotes"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _fmt_num(value: Any, places: int = 6) -> str:
    parsed = _decimal(value)
    if parsed is None:
        return "-"
    if abs(parsed) >= Decimal("1000"):
        return f"{float(parsed):,.2f}".rstrip("0").rstrip(".")
    text = f"{float(parsed):.{places}f}".rstrip("0").rstrip(".")
    return text or "0"


def _fmt_pct(value: Any) -> str:
    parsed = _decimal(value)
    if parsed is None:
        return "-"
    return f"{float(parsed):+.4f}%"


def _short_address(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("0x") and len(text) == 42:
        return f"{text[:10]}...{text[-6:]}"
    return text or "-"


def _safe_md(text: Any) -> str:
    value = str(text or "").replace("\n", " ").strip()
    return value.replace("|", "&#124;")


def _safe_html(text: Any) -> str:
    return html.escape(str(text or "").replace("\n", " ").strip(), quote=True)


def _symbol_aliases(symbol: Any) -> set[str]:
    value = str(symbol or "").strip().upper()
    if not value:
        return set()
    aliases = {value}
    if "." in value:
        aliases.add(value.split(".", 1)[0])
    aliases.update(
        {
            "AVAX": {"WAVAX"},
            "WAVAX": {"AVAX"},
            "BTC": {"BTC.B", "WBTC", "WBTC.E"},
            "BTC.B": {"BTC", "WBTC", "WBTC.E"},
            "WBTC": {"BTC", "BTC.B", "WBTC.E"},
            "WBTC.E": {"BTC", "BTC.B", "WBTC"},
            "ETH": {"WETH", "WETH.E"},
            "WETH": {"ETH", "WETH.E"},
            "WETH.E": {"ETH", "WETH"},
            "USDT": {"USDT.E"},
            "USDT.E": {"USDT"},
            "USDC": {"USDC.E"},
            "USDC.E": {"USDC"},
        }.get(value, set())
    )
    return aliases


def _match_symbol(left: Any, right: Any) -> bool:
    return bool(_symbol_aliases(left) & _symbol_aliases(right))


def _pool_address(row: dict[str, Any]) -> str:
    return str(row.get("pool") or row.get("pool_address") or row.get("address") or "").strip()


def _build_pool_index(cache: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for pair in cache.get("pairs") or []:
        if not isinstance(pair, dict):
            continue
        for row in pair.get("allPools") or []:
            if not isinstance(row, dict):
                continue
            pool = _pool_address(row)
            if pool:
                index[pool.lower()] = row
    return index


def _pool_tvl_usd(row: dict[str, Any]) -> Any:
    balances = row.get("balances") if isinstance(row.get("balances"), dict) else {}
    return balances.get("tvlUsd") if isinstance(balances, dict) else None


def _pool_token_decimals(pool_row: dict[str, Any], symbol: str) -> int | None:
    token0_symbol = str(pool_row.get("token0Symbol") or pool_row.get("token0_symbol") or "").strip()
    token1_symbol = str(pool_row.get("token1Symbol") or pool_row.get("token1_symbol") or "").strip()
    token0_decimals = pool_row.get("token0Decimals") or pool_row.get("token0_decimals")
    token1_decimals = pool_row.get("token1Decimals") or pool_row.get("token1_decimals")
    if _match_symbol(symbol, token0_symbol):
        try:
            return int(token0_decimals)
        except (TypeError, ValueError):
            return None
    if _match_symbol(symbol, token1_symbol):
        try:
            return int(token1_decimals)
        except (TypeError, ValueError):
            return None
    return None


def _amount_to_units(raw: Any, decimals: int | None) -> Decimal | None:
    parsed = _decimal(raw)
    if parsed is None:
        return None
    if decimals is None:
        return parsed
    try:
        return parsed / (Decimal(10) ** int(decimals))
    except (InvalidOperation, TypeError, ValueError):
        return parsed


def _fmt_usdc_raw(raw: Any, places: int = 6) -> str:
    return _fmt_num(_amount_to_units(raw, 6), places)


def _fmt_exact_num(value: Any, places: int = 6) -> str:
    parsed = _decimal(value)
    if parsed is None:
        return "-"
    text = f"{parsed:,.{places}f}".rstrip("0").rstrip(".")
    return text or "0"


def _final_comparison_text(amount_out: Decimal | None, final_threshold: dict[str, Any]) -> str:
    left = _fmt_exact_num(amount_out, 6) if isinstance(amount_out, Decimal) else "-"
    right = str(final_threshold.get("text") or "-")
    threshold_value = final_threshold.get("value")
    threshold_known = bool(final_threshold.get("known"))
    if threshold_known and isinstance(amount_out, Decimal) and isinstance(threshold_value, Decimal):
        operator = ">" if amount_out > threshold_value else "<="
    else:
        operator = "<"
    return f"{left} {operator} {right}"


def _slot_quote_for_direction(slot: dict[str, Any], token_in: str, token_out: str) -> dict[str, Any] | None:
    chained_quote = slot.get("chainedQuote")
    if (
        isinstance(chained_quote, dict)
        and _match_symbol(token_in, chained_quote.get("tokenInSymbol"))
        and _match_symbol(token_out, chained_quote.get("tokenOutSymbol"))
    ):
        return chained_quote
    for key in ("quoteTokenXToTokenY", "quoteTokenYToTokenX"):
        quote = slot.get(key)
        if not isinstance(quote, dict):
            continue
        if _match_symbol(token_in, quote.get("tokenInSymbol")) and _match_symbol(token_out, quote.get("tokenOutSymbol")):
            return quote
    quote = slot.get("quoteTokenXToTokenY")
    if isinstance(quote, dict):
        return quote
    quote = slot.get("quoteTokenYToTokenX")
    return quote if isinstance(quote, dict) else None


def _quality_tag(row: dict[str, Any]) -> str:
    quality = str(row.get("quality") or "").lower()
    if quality == "pass":
        return '<span style="color:#15803d;font-weight:700">楂?/span>'
    if quality == "reject":
        return '<span style="color:#a16207;font-weight:700">浣?/span>'
    return '<span class="muted">鏈煡</span>'


def _slot_is_selected(slot_pool: str, selected_pool: str) -> bool:
    left = str(slot_pool or "").strip().lower()
    right = str(selected_pool or "").strip().lower()
    return bool(left and right and left == right)


def _render_hop_table(
    *,
    title: str,
    token_in: str,
    token_out: str,
    base_rate: Any,
    trade: dict[str, Any],
    selected_pool: str,
    pool_index: dict[str, dict[str, Any]],
    final_threshold: dict[str, Any] | None = None,
) -> list[str]:
    rows = trade.get("slots") if isinstance(trade.get("slots"), list) else []
    benchmark_row = next(
        (
            row
            for row in rows
            if isinstance(row, dict) and str(row.get("quoteStatus") or "") == "quoted"
        ),
        None,
    )
    benchmark_quote = _slot_quote_for_direction(benchmark_row or {}, token_in, token_out) if benchmark_row else None
    benchmark_pool = _pool_address(benchmark_row or {})
    benchmark_pool_row = pool_index.get(benchmark_pool.lower()) if benchmark_pool else None
    benchmark_in = None
    if isinstance(benchmark_quote, dict):
        benchmark_in = _amount_to_units(
            benchmark_quote.get("amountIn"),
            _pool_token_decimals(benchmark_pool_row or {}, token_in),
        )
    if benchmark_in is None:
        benchmark_in = Decimal("1")
    parsed_base_rate = _decimal(base_rate)
    benchmark_out = (
        benchmark_in * parsed_base_rate
        if parsed_base_rate is not None and parsed_base_rate > 0
        else None
    )
    show_benchmark = benchmark_out is not None or not title.startswith("X-Y")

    def _quality_cell(pool_row: dict[str, Any] | None, selected: bool) -> str:
        return _quality_tag(pool_row or {})

    row_infos: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        pool = _pool_address(row) if isinstance(row, dict) else ""
        pool_row = pool_index.get(pool.lower()) if pool else None
        quote = _slot_quote_for_direction(row if isinstance(row, dict) else {}, token_in, token_out)
        token_in_decimals = _pool_token_decimals(pool_row or {}, token_in)
        token_out_decimals = _pool_token_decimals(pool_row or {}, token_out)
        amount_out = None
        amount_in = None
        if isinstance(row, dict) and str(row.get("quoteStatus") or "") == "quoted" and isinstance(quote, dict):
            amount_in = _amount_to_units(quote.get("amountIn"), token_in_decimals)
            amount_out = _amount_to_units(quote.get("amountOut"), token_out_decimals)
        row_infos.append(
            {
                "index": index,
                "row": row,
                "pool": pool,
                "pool_row": pool_row,
                "quote": quote,
                "amount_in": amount_in,
                "amount_out": amount_out,
            }
        )

    quoted_row_infos = [
        info
        for info in row_infos
        if isinstance(info["amount_out"], Decimal)
    ]
    best_info = max(
        quoted_row_infos,
        key=lambda info: (
            info["amount_out"],
            -int(_decimal((info.get("quote") or {}).get("quoterGasEstimate")) or Decimal("0")),
        ),
        default=None,
    )
    best_amount_out = best_info["amount_out"] if isinstance(best_info, dict) else None
    absolute_best_row_ids = {best_info["index"]} if isinstance(best_info, dict) else set()
    benchmark_best_row_ids = absolute_best_row_ids

    is_final_hop = isinstance(final_threshold, dict)
    threshold_header = "<th>鏈€缁堝洖娆炬瘮杈?/th>" if is_final_hop else ""
    threshold_empty = "<td></td>" if is_final_hop else ""
    lines = [
        f"#### {title}",
        "",
        "<table>",
        "<thead>",
        f"<tr><th>琛?/th><th>姹犲瓙</th><th>璐ㄩ噺</th><th>鍏戞崲缁撴灉</th><th>gas</th><th>TVL</th><th>tick</th>{threshold_header}<th>缁撹</th></tr>",
        "</thead>",
        "<tbody>",
    ]
    if show_benchmark:
        lines.append(
            "<tr>"
            f"<td>甯佸畨浠锋牸</td>"
            f"<td></td>"
            f"<td></td>"
            f"<td>{_safe_html(_fmt_num(benchmark_out, 8))}</td>"
            f"<td></td><td></td><td></td>{threshold_empty}<td>鍩哄噯</td>"
            "</tr>"
        )
    for info in row_infos:
        index = info["index"]
        row = info["row"]
        pool = info["pool"]
        pool_row = info["pool_row"]
        quote = info["quote"]
        amount_out = info["amount_out"]
        selected = _slot_is_selected(pool, selected_pool)
        pool_text = _short_address(pool) if pool else ""
        quality = _quality_cell(pool_row, selected)
        if not isinstance(row, dict) or str(row.get("quoteStatus") or "") != "quoted" or not isinstance(quote, dict):
            lines.append(
                "<tr>"
                f"<td>姹犲瓙 {index}</td>"
                f"<td>{pool_text}</td>"
                f"<td></td>"
                f"<td></td>"
                f"<td></td><td></td><td></td>{threshold_empty}<td></td>"
                "</tr>"
            )
            continue

        gas = _fmt_num(quote.get("quoterGasEstimate") or quote.get("gas"), 0)
        tvl = _fmt_num(_pool_tvl_usd(pool_row or {}), 2)
        tick = str((pool_row or {}).get("tick") or row.get("cacheTick") or "")
        threshold_value = None
        threshold_known = False
        threshold_cell = ""
        if is_final_hop:
            threshold_value = final_threshold.get("value")
            threshold_known = bool(final_threshold.get("known"))
            threshold_cell = _final_comparison_text(amount_out, final_threshold)

        verdict = "鎷掔粷"
        if is_final_hop:
            if (
                threshold_known
                and isinstance(threshold_value, Decimal)
                and isinstance(amount_out, Decimal)
                and amount_out > threshold_value
                and index in absolute_best_row_ids
            ):
                verdict = "鎺ㄨ崘"
        elif index in benchmark_best_row_ids:
            verdict = "鎺ㄨ崘"
        if is_final_hop:
            if verdict == "鎺ㄨ崘":
                verdict_reason = f"{threshold_cell}锛涘ぇ浜庢寚瀹氶槇鍊硷紝鎺ㄨ崘"
            else:
                verdict_reason = f"{threshold_cell}锛涘皬浜庢寚瀹氶槇鍊硷紝鎷掔粷"
        elif verdict == "鎺ㄨ崘":
            verdict_reason = "鏈烦鏈€澶ц緭鍑?
            if isinstance(benchmark_out, Decimal) and isinstance(amount_out, Decimal) and amount_out < benchmark_out:
                verdict_reason += f"锛涗綆浜?Binance 鍩哄噯 {_fmt_num(benchmark_out, 8)}"
        else:
            verdict_reason = (
                f"浣庝簬鏈烦鏈€澶ц緭鍑?{_fmt_num(best_amount_out, 8)}"
                if isinstance(best_amount_out, Decimal)
                else "鏃犲彲鐢ㄦ渶澶ц緭鍑?
            )
        row_style = ' style="background:rgba(179,36,24,0.06);"' if verdict == "鎺ㄨ崘" else ""
        threshold_td = f"<td>{_safe_html(threshold_cell)}</td>" if is_final_hop else ""
        conclusion_html = (
            f'<span style="color:#15803d;font-weight:700">鎺ㄨ崘</span><br>'
            f'<span class="muted">{_safe_html(verdict_reason)}</span>'
            if verdict == "鎺ㄨ崘"
            else f'<span style="color:#b42318;font-weight:700">鎷掔粷</span><br>'
            f'<span class="muted">{_safe_html(verdict_reason)}</span>'
        )
        lines.append(
            f"<tr{row_style}>"
            f"<td>姹犲瓙 {index}</td>"
            f"<td>{pool_text}</td>"
            f"<td>{quality}</td>"
            f"<td>{_safe_html(_fmt_num(amount_out, 8))}</td>"
            f"<td>{_safe_html(gas)}</td>"
            f"<td>{_safe_html(tvl)}</td>"
            f"<td>{_safe_html(tick)}</td>"
            f"{threshold_td}"
            f"<td>{conclusion_html}</td>"
            "</tr>"
        )
    if len(rows) < 5:
        for index in range(len(rows) + 1, 6):
            lines.append(
                "<tr>"
                f"<td>姹犲瓙 {index}</td>"
                f"<td></td><td></td><td></td><td></td><td></td><td></td>{threshold_empty}<td></td>"
                "</tr>"
            )
    lines.extend(["</tbody>", "</table>", ""])
    return lines


def _route_base_rates(signal: dict[str, Any], route_path: list[str]) -> tuple[Any, Any, Any]:
    x_symbol = str((signal.get("x") or {}).get("baseSymbol") or (signal.get("x") or {}).get("symbol") or "").strip()
    y_symbol = str((signal.get("y") or {}).get("baseSymbol") or (signal.get("y") or {}).get("symbol") or "").strip()
    x_price = _decimal((signal.get("x") or {}).get("currentPrice")) or Decimal("0")
    y_price = _decimal((signal.get("y") or {}).get("currentPrice")) or Decimal("0")
    price_map = {
        x_symbol.upper(): x_price,
        y_symbol.upper(): y_price,
    }
    hop1_token = str(route_path[1] or "").strip().upper()
    hop2_token = str(route_path[2] or "").strip().upper()
    hop1_price = price_map.get(hop1_token, x_price if _match_symbol(hop1_token, x_symbol) else y_price)
    hop2_price = price_map.get(hop2_token, y_price if _match_symbol(hop2_token, y_symbol) else x_price)
    hop1 = (Decimal("1") / hop1_price) if hop1_price and hop1_price > 0 else None
    hop2 = (hop1_price / hop2_price) if hop1_price and hop1_price > 0 and hop2_price and hop2_price > 0 else None
    hop3 = hop2_price if hop2_price and hop2_price > 0 else None
    return hop1, hop2, hop3


def _final_hop_threshold(contract: dict[str, Any]) -> dict[str, Any]:
    required_raw = _decimal(contract.get("requiredFinalUsdc"))
    if required_raw is None:
        required_raw = _decimal(contract.get("amountUsdc")) or Decimal("0")
        required_raw += _decimal(contract.get("premiumUsdc")) or Decimal("0")
        required_raw += _decimal(contract.get("minProfitUsdc")) or Decimal("0")

    gas_raw = _decimal(contract.get("gasCostUsdc"))
    if gas_raw is None:
        net_profit_model = contract.get("netProfitModel")
        if isinstance(net_profit_model, dict):
            gas_raw = _decimal(net_profit_model.get("gasCostUsdc"))

    threshold_raw = required_raw + (gas_raw or Decimal("0"))
    threshold_value = _amount_to_units(threshold_raw, 6)
    required_value = _amount_to_units(required_raw, 6)
    gas_value = _amount_to_units(gas_raw, 6) if gas_raw is not None else None

    if gas_value is None:
        text = f"{_fmt_exact_num(required_value, 6)} + gas锛堟湭璁板綍锛?
    else:
        text = f"{_fmt_exact_num(threshold_value, 6)}"

    return {
        "value": threshold_value,
        "known": gas_value is not None,
        "text": text,
        "required": required_value,
        "gas": gas_value,
    }


def _fmt_chain_amount(
    raw: Any,
    symbol: str,
    pool: Any,
    pool_index: dict[str, dict[str, Any]],
) -> str:
    pool_row = pool_index.get(str(pool or "").lower()) if pool else None
    decimals = _pool_token_decimals(pool_row or {}, symbol)
    return _fmt_exact_num(_amount_to_units(raw, decimals), 8)


def _render_route_section(
    *,
    sample: dict[str, Any],
    evidence: dict[str, Any],
    pool_index: dict[str, dict[str, Any]],
) -> list[str]:
    candidate = (evidence.get("candidates") or [{}])[0]
    signal = candidate.get("binanceSignal") if isinstance(candidate, dict) else {}
    signal = signal if isinstance(signal, dict) else {}
    routes = candidate.get("routes") if isinstance(candidate, dict) else []
    routes = routes if isinstance(routes, list) else []
    lines: list[str] = [
        f"## 淇″彿 {sample.get('index')}: {signal.get('pair') or '-'}",
        "",
        f"- 瑙﹀彂鏃堕棿 UTC锛歚{sample.get('uiObservedAt') or ''}`",
        f"- Binance 浠锋牸锛歚{_safe_md((signal.get('x') or {}).get('symbol') or '-')}` {_fmt_num((signal.get('x') or {}).get('currentPrice'))}锛沗{_safe_md((signal.get('y') or {}).get('symbol') or '-')}` {_fmt_num((signal.get('y') or {}).get('currentPrice'))}",
        f"- 璇佹嵁鏂囦欢锛歚{Path(str(sample.get('evidencePath') or '')).name}`",
        "",
    ]
    for route in routes:
        route_no = route.get("routeNo") or route.get("route_no") or ""
        route_path = route.get("routePath") if isinstance(route.get("routePath"), list) else route.get("route_path")
        route_path = route_path if isinstance(route_path, list) else []
        route_path_text = "->".join(str(item) for item in route_path if item)
        matrix = route.get("poolQuoteMatrix") if isinstance(route.get("poolQuoteMatrix"), dict) else {}
        contract = matrix.get("contractPathProfit") if isinstance(matrix.get("contractPathProfit"), dict) else {}
        selected_pools = contract.get("selectedPools") if isinstance(contract.get("selectedPools"), dict) else {}
        chained = matrix.get("chainedRoute") if isinstance(matrix.get("chainedRoute"), dict) else {}
        trades = matrix.get("trades") if isinstance(matrix.get("trades"), list) else []
        trade_by_label = {
            str(trade.get("label") or ""): trade
            for trade in trades
            if isinstance(trade, dict)
        }
        ux_trade = trade_by_label.get("ux_usdc_to_x")
        xy_trade = trade_by_label.get("xy_x_to_y")
        uy_trade = trade_by_label.get("uy_usdc_to_y")
        hop1, hop2, hop3 = _route_base_rates(signal, route_path)
        chained_lines: list[str] = []
        if chained.get("status") == "quoted":
            ux_chain = chained.get("uX") if isinstance(chained.get("uX"), dict) else {}
            xy_chain = chained.get("xY") if isinstance(chained.get("xY"), dict) else {}
            yc_chain = chained.get("yC") if isinstance(chained.get("yC"), dict) else {}
            ux_pool = ux_chain.get("recommendedPool")
            xy_pool = xy_chain.get("recommendedPool")
            yc_pool = yc_chain.get("recommendedPool")
            x_symbol = str(route_path[1] if len(route_path) > 1 else "-")
            y_symbol = str(route_path[2] if len(route_path) > 2 else "-")
            chained_lines = [
                f"- 涓茶仈澶嶆牳锛歎-X 杈撳嚭 `{_fmt_chain_amount(ux_chain.get('recommendedAmountOut'), x_symbol, ux_pool, pool_index)} {x_symbol}`锛涜鍊间綔涓?X-Y 杈撳叆锛孹-Y 杈撳嚭 `{_fmt_chain_amount(xy_chain.get('recommendedAmountOut'), y_symbol, xy_pool, pool_index)} {y_symbol}`锛涜鍊间綔涓?Y-C 杈撳叆锛孻-C 鏈€缁堣緭鍑?`{_fmt_chain_amount(yc_chain.get('recommendedAmountOut'), 'USDC', yc_pool, pool_index)} USDC`",
                f"- 涓茶仈鎺ㄨ崘姹狅細U-X `{_short_address(ux_pool)}`锛沊-Y `{_short_address(xy_pool)}`锛沋-C `{_short_address(yc_pool)}`",
            ]
            final_threshold = _final_hop_threshold(contract)
            final_value = _amount_to_units(yc_chain.get("recommendedAmountOut"), 6)
            if (
                final_threshold.get("known")
                and isinstance(final_value, Decimal)
                and isinstance(final_threshold.get("value"), Decimal)
                and final_value > final_threshold["value"]
            ):
                route_verdict = "鎺ㄨ崘"
                route_reason = f"Y-C 鏈€缁堝洖娆炬瘮杈冿細{_final_comparison_text(final_value, final_threshold)}锛涘ぇ浜庢寚瀹氶槇鍊硷紝鎺ㄨ崘"
            else:
                route_verdict = "鎷掔粷"
                route_reason = f"Y-C 鏈€缁堝洖娆炬瘮杈冿細{_final_comparison_text(final_value, final_threshold)}锛涘皬浜庢寚瀹氶槇鍊硷紝鎷掔粷"
            chained_lines.append(f"- 璺敱缁撹锛?*{route_verdict}**锛泏route_reason}")
        lines.extend(
            [
                f"### 璺敱 {route_no}: {route_path_text}",
                "",
                f"- 鍚堢害璇曠畻锛氭姇鍏?`{_fmt_usdc_raw(contract.get('amountUsdc') or 1000000000)} USDC`锛岄棯鐢佃捶璐圭敤 `{_fmt_usdc_raw(contract.get('premiumUsdc') or 500000)} USDC`锛岃姹傛渶缁堣嚦灏?`{_fmt_usdc_raw(contract.get('requiredFinalUsdc'))} USDC`锛屽疄闄?Quoter 浼扮畻鏈€缁?`{_fmt_usdc_raw(contract.get('quotedFinalUsdc'))} USDC`锛岄鏈熺粨鏋?`{_fmt_usdc_raw(contract.get('expectedProfitUsdc'))} USDC`锛坮aw `{contract.get('expectedProfitUsdc') or '-'}`锛夛紝profitableBeforeGas锛歚{str(contract.get('profitableBeforeGas')).lower()}`",
                f"- 鍚堢害閫変腑姹狅細USDC->`{_safe_md(route_path[1] if len(route_path) > 1 else '-')}`锛歚{_short_address(selected_pools.get('usdcToX'))}`锛沗{_safe_md(route_path[1] if len(route_path) > 1 else '-')}`->`{_safe_md(route_path[2] if len(route_path) > 2 else '-')}`锛歚{_short_address(selected_pools.get('xToY'))}`锛沗{_safe_md(route_path[2] if len(route_path) > 2 else '-')}`->USDC锛歚{_short_address(selected_pools.get('yToUsdc'))}`",
                *chained_lines,
                "",
            ]
        )
        if isinstance(ux_trade, dict):
            lines.extend(
                _render_hop_table(
                    title=f"U-X锛圲SDC->{_safe_md(route_path[1] if len(route_path) > 1 else '-') }锛?,
                    token_in="USDC",
                    token_out=str(route_path[1] or ""),
                    base_rate=hop1,
                    trade=ux_trade,
                    selected_pool=str(selected_pools.get("usdcToX") or ""),
                    pool_index=pool_index,
                )
            )
        if isinstance(xy_trade, dict):
            lines.extend(
                _render_hop_table(
                    title=f"X-Y锛坽_safe_md(route_path[1] if len(route_path) > 1 else '-') }->{_safe_md(route_path[2] if len(route_path) > 2 else '-') }锛?,
                    token_in=str(route_path[1] or ""),
                    token_out=str(route_path[2] or ""),
                    base_rate=hop2,
                    trade=xy_trade,
                    selected_pool=str(selected_pools.get("xToY") or ""),
                    pool_index=pool_index,
                )
            )
        if isinstance(uy_trade, dict):
            lines.extend(
                _render_hop_table(
                    title=f"Y-C锛坽_safe_md(route_path[2] if len(route_path) > 2 else '-') }->USDC锛?,
                    token_in=str(route_path[2] or ""),
                    token_out="USDC",
                    base_rate=hop3,
                    trade=uy_trade,
                    selected_pool=str(selected_pools.get("yToUsdc") or ""),
                    pool_index=pool_index,
                    final_threshold=_final_hop_threshold(contract),
                )
            )
    return lines


def _latest_collection_index() -> Path:
    if not EVIDENCE_ROOT.exists():
        raise FileNotFoundError(f"missing evidence root: {EVIDENCE_ROOT}")
    candidates = [path for path in EVIDENCE_ROOT.glob("*/index.json") if path.is_file() and "_test" not in str(path)]
    if not candidates:
        raise FileNotFoundError(f"no evidence collection index found under {EVIDENCE_ROOT}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _update_intro(text: str) -> str:
    pattern = re.compile(
        r"^璇存槑锛?*$",
        re.MULTILINE,
    )
    replacement = (
        "璇存槑锛氭寜姣忔 UI/Binance 瑙﹀彂淇″彿鍒嗙粍灞曠ず銆傛瘡涓Е鍙戜俊鍙蜂笅闈㈡寜鍚堢害鍊欓€夎矾鐢辨媶鎴愪笁寮犺〃锛圲-X / X-Y / Y-C锛夛紱Binance 琛屼粎浣滀负鍚屾柟鍚戜环鏍煎鐓с€俇-X 涓?X-Y 鎺ㄨ崘璇ヨ烦鍙墽琛屾睜涓殑鏈€澶ц緭鍑猴紝骞舵妸鎺ㄨ崘杈撳嚭浣滀负涓嬩竴璺宠緭鍏ワ紱Y-C 鍙湁鍦ㄦ渶缁堝洖鏀?USDC 楂樹簬鍒濆鏈噾銆侀棯鐢佃捶璐圭敤涓?gas 璐圭敤鍚堣闃堝€兼椂鎵嶆帹鑽愩€?
    )
    return pattern.sub(replacement, text, count=1)


def _render_document(doc: str, collection: dict[str, Any], pool_index: dict[str, dict[str, Any]]) -> str:
    intro_updated = _update_intro(doc)
    signal_anchor = re.search(r"\n## 淇″彿\s+1\b", intro_updated)
    if not signal_anchor:
        signal_anchor = re.search(r"\n## .*?1\b", intro_updated)
    if not signal_anchor:
        raise RuntimeError("cannot locate first signal section in 鏁版嵁.md")
    prefix = intro_updated[: signal_anchor.start() + 1]
    sections = []
    for sample in collection.get("samples") or []:
        if not isinstance(sample, dict):
            continue
        evidence_path = Path(str(sample.get("evidencePath") or ""))
        if not evidence_path.is_file():
            continue
        evidence = _read_json(evidence_path)
        sections.extend(_render_route_section(sample=sample, evidence=evidence, pool_index=pool_index))
        sections.append("")
    return prefix + "\n".join(sections).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection-index", type=Path, default=_latest_collection_index())
    parser.add_argument("--doc-path", type=Path, default=DOC_PATH)
    parser.add_argument("--pool-cache", type=Path, default=POOL_CACHE_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    collection = _read_json(args.collection_index if args.collection_index.is_absolute() else REPO_ROOT / args.collection_index)
    pool_cache = _read_json(args.pool_cache if args.pool_cache.is_absolute() else REPO_ROOT / args.pool_cache)
    pool_index = _build_pool_index(pool_cache)
    doc_path = args.doc_path if args.doc_path.is_absolute() else REPO_ROOT / args.doc_path
    original = doc_path.read_text(encoding="utf-8")
    updated = _render_document(original, collection, pool_index)
    _write_text(doc_path, updated)
    print(json.dumps({"ok": True, "docPath": str(doc_path), "collectionIndex": str(args.collection_index)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
