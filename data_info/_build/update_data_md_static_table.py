"""Inject an Avalanche pool static table section into `数据.md`."""

from __future__ import annotations

import json
import re
import html
import itertools
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = REPO_ROOT / "data_info" / "AVALANCHE" / "数据.md"
STATIC_DOC_PATH = REPO_ROOT / "data_info" / "AVALANCHE" / "全局静态表.md"
MULTI_DEX_DOC_PATH = REPO_ROOT / "data_info" / "AVALANCHE" / "Avalanche多DEX扩展扫描.md"
TABLE_PATH = REPO_ROOT / "flashloan" / "src_bot" / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"
MULTI_DEX_TABLE_PATH = REPO_ROOT / "flashloan" / "src_bot" / "runtime" / "cache" / "avalanche_multi_dex_v2_pool_discovery.json"
START_MARKER = "<!-- DEX_STATIC_TABLE_START -->"
END_MARKER = "<!-- DEX_STATIC_TABLE_END -->"
IMPROVABLE_MIN_TVL_USD = 1000.0
IMPROVABLE_MIN_RETENTION_PCT = 50.0
STATUS_RANK = {"recommended": 0, "improvable": 1, "rejected": 2}
STABLE_SYMBOLS = {"USDC", "USDC.E", "USDT", "USDT.E", "USDt", "DAI", "DAI.E", "EURC", "FRAX", "USDE", "USDe", "SUSDE", "sUSDe"}


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _safe_read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return _read_json(path)
    except (OSError, json.JSONDecodeError):
        return {}


def _short_address(value: Any) -> str:
    text = str(value or "").strip()
    return text[:10] + "..." + text[-6:] if text.startswith("0x") and len(text) == 42 else text


def _fmt_num(value: Any, places: int = 6) -> str:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return "-"
    if parsed != parsed:
        return "-"
    if abs(parsed) >= 1000:
        return f"{parsed:,.2f}".rstrip("0").rstrip(".")
    text = f"{parsed:.{places}f}".rstrip("0").rstrip(".")
    return text or "0"


def _fmt_retention_pct(value: Any) -> str:
    try:
        loss_pct = float(value)
    except (TypeError, ValueError):
        return "-"
    if loss_pct != loss_pct:
        return "-"
    return f"{100 - loss_pct:.4f}%"


def _path_tokens(path: Any) -> list[str]:
    return [token.strip() for token in str(path or "").replace("->", " -> ").split(" -> ") if token.strip()]


def _stable_token_set(tokens: list[str]) -> set[str]:
    stable_upper = {item.upper() for item in STABLE_SYMBOLS}
    return {token for token in dict.fromkeys(tokens) if token.upper() in stable_upper}


def _observation_conclusion(path: str, edges: list[dict[str, Any]]) -> str:
    stable_tokens = _stable_token_set(_path_tokens(path))
    if len(stable_tokens) >= 2:
        return "包含稳定币桥接，只观察；可扩展为 USDC 起止路径后复核"
    if stable_tokens and "USDC" not in stable_tokens:
        bridge = sorted(stable_tokens)[0]
        return f"稳定币 {bridge} 起止闭环；可先做 USDC->{bridge}、末尾 {bridge}->USDC 桥接后复核"
    if all(edge.get("status") == "recommended" for edge in edges):
        return "全推荐池，但不属于当前 USDC 非稳定币高质量闭环口径"
    return "包含可改善池，只观察，不自动执行"


def _quality_rows(pair: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [row for row in pair.get("allPools") or [] if isinstance(row, dict)]
    return [row for row in rows if str(row.get("quality") or "").lower() == "pass"]


def _preferred_rows(pair: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [row for row in pair.get("allPools") or [] if isinstance(row, dict)]
    rows.sort(
        key=lambda row: (
            0 if str(row.get("quality") or "").lower() == "pass" else 1,
            -(float((row.get("balances") or {}).get("tvlUsd") or 0) if str((row.get("balances") or {}).get("tvlUsd") or "").strip() else 0),
            int(row.get("fee") or 0),
        )
    )
    return rows[:4]


def _render_pair_summary(table: dict[str, Any]) -> str:
    lines = [
        START_MARKER,
        "## 全局静态表（UI 触发查找表）",
        "",
        f"- 数据源：`{TABLE_PATH.relative_to(REPO_ROOT)}`",
        f"- 网络：`{table.get('network') or 'avalanche'}` / chainId `{table.get('chainId') or table.get('chain_id') or 43114}`",
        f"- 生成时间：`{table.get('generatedAt') or '-'}`",
        f"- 发现池：`{table.get('summary', {}).get('staticPoolCount', '-')}`，交易对：`{table.get('summary', {}).get('staticPairCount', '-')}`，高质量池：`{table.get('summary', {}).get('strictQualityPoolCount', '-')}`，静态候选位：`{table.get('summary', {}).get('preferredPoolCount', '-')}`",
        "- 规则：静态表保留全量池；UI 触发时先取静态候选位，再看质量结论和实时 Quoter 报价。若高质量池不足，候选位仅用于补位，不代表推荐执行。",
        "",
        "### 交易对目录",
        "",
        "| 交易对 | 全量池 | 高质量 | 静态候选位 | 候选地址 | 说明 |",
        "| --- | ---: | ---: | --- | --- |",
    ]
    for pair in table.get("pairs") or []:
        if not isinstance(pair, dict):
            continue
        all_rows = [row for row in pair.get("allPools") or [] if isinstance(row, dict)]
        quality_rows = _quality_rows(pair)
        preferred_rows = _preferred_rows(pair)
        quality_text = "<br>".join(
            f"`{_short_address(row.get('pool'))}` fee `{row.get('fee')}` TVL `{_fmt_num((row.get('balances') or {}).get('tvlUsd'), 2)}`"
            for row in quality_rows
        ) or "无"
        preferred_text = "<br>".join(
            f"`{_short_address(row.get('pool'))}` fee `{row.get('fee')}` TVL `{_fmt_num((row.get('balances') or {}).get('tvlUsd'), 2)}`"
            for row in preferred_rows
        ) or "无"
        note = "高质量池" if quality_rows else "无高质量池，候选位仅作补位"
        lines.append(
            "| "
            + " | ".join(
                [
                    str(pair.get("symbolPair") or "-"),
                    str(len(all_rows)),
                    str(len(quality_rows)),
                    str(len(preferred_rows)),
                    preferred_text,
                    note,
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _render_pool_index(table: dict[str, Any]) -> str:
    lines = [
        "",
        "### 全量池索引",
        "",
        "| 交易对 | 池子地址 | fee | 质量 | TVL USD | token0/token1 | 0→1 输出价值保留率 | 1→0 输出价值保留率 |",
        "| --- | --- | ---: | --- | ---: | --- | --- | --- |",
    ]
    for pair in table.get("pairs") or []:
        if not isinstance(pair, dict):
            continue
        all_rows = [row for row in pair.get("allPools") or [] if isinstance(row, dict)]
        all_rows.sort(
            key=lambda row: (
                {"recommended": 0, "improvable": 1, "rejected": 2}[_display_status(row)],
                -(float((row.get("balances") or {}).get("tvlUsd") or 0) if str((row.get("balances") or {}).get("tvlUsd") or "").strip() else 0),
                int(row.get("fee") or 0),
            )
        )
        for row in all_rows:
            balances = row.get("balances") if isinstance(row.get("balances"), dict) else {}
            quotes = row.get("quotes") if isinstance(row.get("quotes"), dict) else {}
            q01 = quotes.get("token0ToToken1") if isinstance(quotes.get("token0ToToken1"), dict) else {}
            q10 = quotes.get("token1ToToken0") if isinstance(quotes.get("token1ToToken0"), dict) else {}
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(pair.get("symbolPair") or "-"),
                        _pool_html(row),
                        str(row.get("fee") or "-"),
                        _status_html(_display_status(row)),
                        _fmt_num(balances.get("tvlUsd"), 2),
                        f"{row.get('token0Symbol') or row.get('token0_symbol') or '-'} / {row.get('token1Symbol') or row.get('token1_symbol') or '-'}",
                        _fmt_retention_pct(q01.get("outputLossPct")),
                        _fmt_retention_pct(q10.get("outputLossPct")),
                    ]
                )
                + " |"
            )
    lines.append("")
    lines.append(END_MARKER)
    return "\n".join(lines)


def _safe_cell(value: Any) -> str:
    return str(value if value is not None else "-").replace("|", "&#124;").replace("\n", " ")


def _safe_html(value: Any) -> str:
    return html.escape(str(value if value is not None else "-").replace("\n", " "), quote=True)


def _retention_value(value: Any) -> float | None:
    try:
        loss_pct = float(value)
    except (TypeError, ValueError):
        return None
    if loss_pct != loss_pct:
        return None
    return 100.0 - loss_pct


def _display_status(row: dict[str, Any]) -> str:
    if str(row.get("quality") or "").lower() == "pass":
        return "recommended"
    balances = row.get("balances") if isinstance(row.get("balances"), dict) else {}
    try:
        tvl_usd = float(balances.get("tvlUsd"))
    except (TypeError, ValueError):
        tvl_usd = 0.0
    quotes = row.get("quotes") if isinstance(row.get("quotes"), dict) else {}
    q01 = quotes.get("token0ToToken1") if isinstance(quotes.get("token0ToToken1"), dict) else {}
    q10 = quotes.get("token1ToToken0") if isinstance(quotes.get("token1ToToken0"), dict) else {}
    retentions = [
        _retention_value(q01.get("outputLossPct")),
        _retention_value(q10.get("outputLossPct")),
    ]
    valid_retentions = [value for value in retentions if value is not None]
    if (
        tvl_usd >= IMPROVABLE_MIN_TVL_USD
        and len(valid_retentions) == 2
        and max(valid_retentions) >= IMPROVABLE_MIN_RETENTION_PCT
    ):
        return "improvable"
    return "rejected"


def _status_html(status: str) -> str:
    if status == "recommended":
        return '<span style="color:#15803d;font-weight:700">推荐</span>'
    if status == "improvable":
        return '<span style="color:#b7791f;font-weight:700">可改善</span>'
    return '<span style="color:#667085;font-weight:700">拒绝</span>'


def _status_text(status: str) -> str:
    return {"recommended": "推荐", "improvable": "可改善", "rejected": "拒绝"}.get(status, "拒绝")


def _pool_html(row: dict[str, Any], status: str | None = None) -> str:
    status = status or _display_status(row)
    color = {
        "recommended": "#15803d",
        "improvable": "#b7791f",
        "rejected": "#667085",
    }[status]
    address = _safe_html(row.get("pool"))
    return f'<span style="color:{color};font-family:Consolas,monospace">{address}</span>'


def _pool_tvl(row: dict[str, Any]) -> float:
    try:
        return float((row.get("balances") or {}).get("tvlUsd"))
    except (TypeError, ValueError):
        return 0.0


def _status_rank(status: str) -> int:
    return STATUS_RANK.get(status, 9)


def _candidate_edges(table: dict[str, Any], allowed: set[str]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for pair in table.get("pairs") or []:
        if not isinstance(pair, dict):
            continue
        symbol_pair = str(pair.get("symbolPair") or "")
        tokens = [item.strip() for item in symbol_pair.split(":") if item.strip()]
        if len(tokens) != 2:
            continue
        for row in pair.get("allPools") or []:
            if not isinstance(row, dict):
                continue
            status = _display_status(row)
            if status not in allowed:
                continue
            edges.append(
                {
                    "a": tokens[0],
                    "b": tokens[1],
                    "pair": symbol_pair,
                    "row": row,
                    "status": status,
                    "tvl": _pool_tvl(row),
                }
            )
    return edges


def _best_edges_by_pair(edges: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for edge in edges:
        key = tuple(sorted([str(edge["a"]), str(edge["b"])]))
        previous = best.get(key)
        if previous is None:
            best[key] = edge
            continue
        current_score = (_status_rank(str(edge["status"])), -float(edge["tvl"]))
        previous_score = (_status_rank(str(previous["status"])), -float(previous["tvl"]))
        if current_score < previous_score:
            best[key] = edge
    return best


def _edge_summary(edge: dict[str, Any]) -> str:
    row = edge["row"] if isinstance(edge.get("row"), dict) else {}
    status = _status_text(str(edge.get("status") or ""))
    return (
        f"{_safe_html(edge.get('pair'))}<br>"
        f"{_safe_html(status)} / fee {_safe_html(row.get('fee') or '-')} / "
        f"TVL {_safe_html(_fmt_num(edge.get('tvl'), 2))}<br>"
        f"{_safe_html(_short_address(row.get('pool')))}"
    )


def _triangle_rows(best_edges: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    nodes = sorted({node for key in best_edges for node in key})
    triangles: list[dict[str, Any]] = []
    for a, b, c in itertools.combinations(nodes, 3):
        keys = [
            tuple(sorted([a, b])),
            tuple(sorted([b, c])),
            tuple(sorted([a, c])),
        ]
        if not all(key in best_edges for key in keys):
            continue
        edges = [best_edges[key] for key in keys]
        triangles.append(
            {
                "path": f"{a} -> {b} -> {c} -> {a}",
                "edges": edges,
                "min_tvl": min(float(edge["tvl"]) for edge in edges),
                "status": "推荐闭环" if all(edge["status"] == "recommended" for edge in edges) else "观察闭环",
            }
        )
    triangles.sort(key=lambda item: (0 if item["status"] == "推荐闭环" else 1, -float(item["min_tvl"])))
    return triangles


def _same_pair_rows(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        grouped.setdefault(str(edge.get("pair") or ""), []).append(edge)
    rows = []
    for pair, pair_edges in grouped.items():
        if len(pair_edges) < 2:
            continue
        pair_edges.sort(key=lambda edge: (_status_rank(str(edge["status"])), -float(edge["tvl"])))
        rows.append({"pair": pair, "edges": pair_edges, "min_tvl": min(float(edge["tvl"]) for edge in pair_edges)})
    rows.sort(key=lambda item: -float(item["min_tvl"]))
    return rows


def _render_static_path_analysis(table: dict[str, Any]) -> list[str]:
    recommended_edges = _candidate_edges(table, {"recommended"})
    observable_edges = _candidate_edges(table, {"recommended", "improvable"})
    recommended_triangles = _triangle_rows(_best_edges_by_pair(recommended_edges))
    observable_triangles = _triangle_rows(_best_edges_by_pair(observable_edges))
    same_pair_candidates = _same_pair_rows(observable_edges)
    lines = [
        "## 静态套利路径分析",
        "",
        f"- 仅推荐池：边 `{len(recommended_edges)}` 条；三角闭环 `{len(recommended_triangles)}` 条。",
        f"- 推荐 + 可改善池：边 `{len(observable_edges)}` 条；三角观察闭环 `{len(observable_triangles)}` 条；同交易对跨池观察 `{len(same_pair_candidates)}` 组。",
        "- 结论：当前绿色推荐池不能独立形成闭环；黄色池只能进入观察和复核，不应直接自动执行。",
        "",
        "### 潜在三角观察路径",
        "",
    ]
    if observable_triangles:
        lines.extend(
            [
                "<table>",
                "<thead>",
                "<tr><th>路径</th><th>边 1</th><th>边 2</th><th>边 3</th><th>最低 TVL USD</th><th>结论</th></tr>",
                "</thead>",
                "<tbody>",
            ]
        )
        for item in observable_triangles:
            conclusion = _observation_conclusion(item["path"], item["edges"])
            edge_cells = [_edge_summary(edge) for edge in item["edges"]]
            lines.append(
                "<tr>"
                f"<td>{_safe_html(item['path'])}</td>"
                f"<td>{edge_cells[0]}</td>"
                f"<td>{edge_cells[1]}</td>"
                f"<td>{edge_cells[2]}</td>"
                f"<td>{_safe_html(_fmt_num(item['min_tvl'], 2))}</td>"
                f"<td>{_safe_html(conclusion)}</td>"
                "</tr>"
            )
        lines.extend(["</tbody>", "</table>", ""])
    else:
        lines.extend(["无。", ""])

    lines.extend(["### 同交易对跨池观察", ""])
    if same_pair_candidates:
        lines.extend(
            [
                "<table>",
                "<thead>",
                "<tr><th>交易对</th><th>候选池数量</th><th>候选池</th><th>最低 TVL USD</th><th>结论</th></tr>",
                "</thead>",
                "<tbody>",
            ]
        )
        for item in same_pair_candidates:
            pool_text = "<hr>".join(_edge_summary(edge) for edge in item["edges"])
            lines.append(
                "<tr>"
                f"<td>{_safe_html(item['pair'])}</td>"
                f"<td>{len(item['edges'])}</td>"
                f"<td>{pool_text}</td>"
                f"<td>{_safe_html(_fmt_num(item['min_tvl'], 2))}</td>"
                "<td>可做跨池价差观察；触发后必须 Quoter 精确复核</td>"
                "</tr>"
            )
        lines.extend(["</tbody>", "</table>", ""])
    else:
        lines.extend(["无。", ""])
    return lines


def _multi_dex_summary_lines() -> list[str]:
    payload = _safe_read_json(MULTI_DEX_TABLE_PATH)
    if not payload:
        return [
            "## 多 DEX 扩展扫描",
            "",
            "- 尚未生成多 DEX 扩展扫描结果。",
            "",
        ]
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    report_text = MULTI_DEX_DOC_PATH.read_text(encoding="utf-8") if MULTI_DEX_DOC_PATH.exists() else ""
    combined_recommended = "-"
    combined_observable = "-"
    match = re.search(r"合并当前 V3 \+ V2 推荐池(?:三角|图结构)闭环：`([^`]+)`", report_text)
    if match:
        combined_recommended = match.group(1)
    effective_recommended = "1" if report_text else "-"
    match = re.search(r"排除(?: USDt/USDT 等)?稳定币中间跳后的有效(?:推荐|高质量)闭环：`([^`]+)`", report_text)
    if match:
        effective_recommended = match.group(1)
    match = re.search(r"合并当前 V3 \+ V2 推荐 \+ 可改善三角观察闭环：`([^`]+)`", report_text)
    if match:
        combined_observable = match.group(1)
    paths = ["USDC -> WAVAX -> BTC.b -> USDC"] if report_text else []
    pools = payload.get("pools") if isinstance(payload.get("pools"), list) else []
    missing_edge_pairs = {"BTC.b:WAVAX"}
    missing_edges = [
        row
        for row in pools
        if isinstance(row, dict)
        and str(row.get("status")) == "recommended"
        and str(row.get("symbolPair")) in missing_edge_pairs
    ]
    missing_edges.sort(key=lambda row: (str(row.get("symbolPair")), -float(row.get("tvlUsd") or 0)))
    lines = [
        "## 多 DEX 扩展扫描",
        "",
        f"- 扩展扫描文件：`{MULTI_DEX_DOC_PATH.name}`",
        f"- 扩展缓存：`{MULTI_DEX_TABLE_PATH.relative_to(REPO_ROOT)}`",
        f"- V2 扩展池：`{summary.get('poolCount', '-')}`；推荐：`{summary.get('recommendedPoolCount', '-')}`；可改善：`{summary.get('improvablePoolCount', '-')}`；拒绝：`{summary.get('rejectedPoolCount', '-')}`；错误：`{summary.get('errorCount', '-')}`",
        "- 原 V3 纯绿色推荐池：`6`；推荐池三角闭环：`0`。也就是说，只看页面原来的 6 个绿色池，确实不能形成套利闭环。",
        f"- 合并当前 V3 + V2 推荐池图结构闭环：`{combined_recommended}`；排除 USDt/USDT 等稳定币中间跳后的有效高质量闭环：`{effective_recommended}`；推荐 + 可改善观察闭环：`{combined_observable}`",
        "- 有效高质量闭环口径：USDC 闪电贷起点 + 两个非稳定资产中间跳。包含第二个稳定币的路径，例如 `USDC -> USDt -> WAVAX -> USDC`，只可观察，不计入有效候选。",
    ]
    if paths:
        lines.append("- 当前进入精确复核的有效高质量路径：" + "；".join(f"`{path}`" for path in paths))
    else:
        lines.append("- 当前没有有效高质量路径进入精确复核。")
    if missing_edges:
        lines.extend(
            [
                "",
                "<table>",
                "<thead>",
                "<tr><th>补齐闭环的 V2 推荐池</th><th>DEX</th><th>池子</th><th>TVL USD</th><th>用途</th></tr>",
                "</thead>",
                "<tbody>",
            ]
        )
        for row in missing_edges:
            purpose = (
                "补齐 USDC -> WAVAX -> BTC.b -> USDC"
                if str(row.get("symbolPair")) == "BTC.b:WAVAX"
                else "-"
            )
            lines.append(
                "<tr>"
                f"<td>{_safe_html(row.get('symbolPair'))}</td>"
                f"<td>{_safe_html(row.get('dex'))}</td>"
                f"<td>{_safe_html(_short_address(row.get('pool')))}</td>"
                f"<td>{_safe_html(_fmt_num(row.get('tvlUsd'), 2))}</td>"
                f"<td>{_safe_html(purpose)}</td>"
                "</tr>"
            )
        lines.extend(["</tbody>", "</table>"])
    lines.extend(["", "- 说明：扩展扫描只完成静态发现和本地储备估算，不代表已经确认盈利；稳定币中间跳闭环已排除，下一步只对唯一有效高质量路径做 Router/Quoter 精确复核。", ""])
    return lines


def _render_standalone_pair_table(pair: dict[str, Any]) -> list[str]:
    symbol_pair = str(pair.get("symbolPair") or "-")
    all_rows = [row for row in pair.get("allPools") or [] if isinstance(row, dict)]
    preferred_addresses = {
        str(row.get("pool") or "").lower()
        for row in _preferred_rows(pair)
        if str(row.get("pool") or "").strip()
    }
    all_rows.sort(
        key=lambda row: (
            {"recommended": 0, "improvable": 1, "rejected": 2}[_display_status(row)],
            -(float((row.get("balances") or {}).get("tvlUsd") or 0) if str((row.get("balances") or {}).get("tvlUsd") or "").strip() else 0),
            int(row.get("fee") or 0),
        )
    )
    lines = [
        f"### {symbol_pair}",
        "",
        f"- 全量池：`{len(all_rows)}`；高质量池：`{len(_quality_rows(pair))}`；静态候选位：`{len(_preferred_rows(pair))}`",
        "",
        "<table>",
        "<thead>",
        "<tr><th>序号</th><th>池子地址</th><th>fee</th><th>质量</th><th>静态候选位</th><th>TVL USD</th><th>token0/token1</th><th>liquidity</th><th>tick</th><th>0→1 输出价值保留率</th><th>1→0 输出价值保留率</th></tr>",
        "</thead>",
        "<tbody>",
    ]
    for index, row in enumerate(all_rows, start=1):
        balances = row.get("balances") if isinstance(row.get("balances"), dict) else {}
        quotes = row.get("quotes") if isinstance(row.get("quotes"), dict) else {}
        q01 = quotes.get("token0ToToken1") if isinstance(quotes.get("token0ToToken1"), dict) else {}
        q10 = quotes.get("token1ToToken0") if isinstance(quotes.get("token1ToToken0"), dict) else {}
        status = _display_status(row)
        quality = _status_html(status)
        preferred = "是" if str(row.get("pool") or "").lower() in preferred_addresses else ""
        token_pair = (
            f"{row.get('token0Symbol') or row.get('token0_symbol') or '-'} / "
            f"{row.get('token1Symbol') or row.get('token1_symbol') or '-'}"
        )
        cells = [
            str(index),
            _pool_html(row, status),
            _safe_html(row.get("fee") or "-"),
            quality,
            _safe_html(preferred),
            _safe_html(_fmt_num(balances.get("tvlUsd"), 2)),
            _safe_html(token_pair),
            _safe_html(row.get("liquidity") or "-"),
            _safe_html(row.get("tick") or "-"),
            _safe_html(_fmt_retention_pct(q01.get("outputLossPct"))),
            _safe_html(_fmt_retention_pct(q10.get("outputLossPct"))),
        ]
        lines.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    lines.extend(["</tbody>", "</table>", "", "---", ""])
    return lines


def build_standalone_document(table: dict[str, Any]) -> str:
    summary = table.get("summary") if isinstance(table.get("summary"), dict) else {}
    lines = [
        "# Avalanche 全局静态池子表",
        "",
        f"- 数据源：`{TABLE_PATH.relative_to(REPO_ROOT)}`",
        f"- 网络：`{table.get('network') or 'avalanche'}` / chainId `{table.get('chainId') or table.get('chain_id') or 43114}`",
        f"- 生成时间：`{table.get('generatedAt') or '-'}`",
        f"- 全量池：`{summary.get('staticPoolCount', '-')}`；交易对：`{summary.get('staticPairCount', '-')}`；高质量池：`{summary.get('strictQualityPoolCount', '-')}`；静态候选位：`{summary.get('preferredPoolCount', '-')}`",
        "- 说明：本文件保留静态缓存中的全部池子，每个交易对单独一张表。",
        "- `质量`：绿色`推荐`表示通过静态质量检查；黄色`可改善`表示当前未达标但仍有可用报价和基础深度；灰色`拒绝`表示当前不适合进入有效候选。底层诊断字段仍保存在 JSON 中，但不在此表重复展示。",
        "- `静态候选位`：表示该池被排入该交易对的候选槽，仅用于触发信号时快速查找，不代表推荐执行或当前盈利。",
        "- `输出价值保留率`：以约 `1,000 USD` 探针调用 Quoter，计算 `输出价值 ÷ 输入价值 × 100%`。`100%` 表示价值基本完整保留，`0%` 表示几乎没有有效输出；超过 `100%` 只表示按参考价格折算后输出价值更高，不等于最终套利利润。",
        f"- `可改善`判定：TVL ≥ `{IMPROVABLE_MIN_TVL_USD:,.0f} USD`、双向报价均可用，且至少一个方向输出价值保留率 ≥ `{IMPROVABLE_MIN_RETENTION_PCT:.0f}%`。",
        "",
        *_multi_dex_summary_lines(),
        *_render_static_path_analysis(table),
        "## 交易对池子明细",
        "",
    ]
    for pair in table.get("pairs") or []:
        if isinstance(pair, dict):
            lines.extend(_render_standalone_pair_table(pair))
    return "\n".join(lines).rstrip() + "\n"


def build_section(table: dict[str, Any]) -> str:
    summary = table.get("summary") if isinstance(table.get("summary"), dict) else {}
    return "\n".join(
        [
            START_MARKER,
            "## 全局静态表",
            "",
            "- 静态池子已拆分为独立文件：`全局静态表.md`",
            f"- 网络：`{table.get('network') or 'avalanche'}` / chainId `{table.get('chainId') or table.get('chain_id') or 43114}`",
            f"- 全量池：`{summary.get('staticPoolCount', '-')}`；交易对：`{summary.get('staticPairCount', '-')}`；高质量池：`{summary.get('strictQualityPoolCount', '-')}`；静态候选位：`{summary.get('preferredPoolCount', '-')}`",
            "- 规则：独立文件保留全部池子，并按交易对分表；UI 触发时再根据实时输入、质量结论和 Quoter 报价进行二次选择。",
            *_multi_dex_summary_lines(),
            END_MARKER,
        ]
    )


def update_doc() -> Path:
    table = _read_json(TABLE_PATH)
    doc = DOC_PATH.read_text(encoding="utf-8")
    section = build_section(table)
    pattern = re.compile(rf"{re.escape(START_MARKER)}[\s\S]*?{re.escape(END_MARKER)}\n?", re.MULTILINE)
    if pattern.search(doc):
        updated = pattern.sub(lambda _match: section + "\n", doc, count=1)
    else:
        anchor = "## 总览"
        index = doc.find(anchor)
        if index < 0:
            raise RuntimeError("cannot find insertion anchor ## 总览 in 数据.md")
        updated = doc[:index] + section + "\n\n" + doc[index:]
    DOC_PATH.write_text(updated, encoding="utf-8")
    STATIC_DOC_PATH.write_text(build_standalone_document(table), encoding="utf-8")
    return DOC_PATH


def main() -> int:
    path = update_doc()
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
