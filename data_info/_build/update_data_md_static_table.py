"""Inject an Avalanche pool static table section into `鏁版嵁.md`."""

from __future__ import annotations

import json
import re
import html
import itertools
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = REPO_ROOT / "data_info" / "AVALANCHE" / "鏁版嵁.md"
STATIC_DOC_PATH = REPO_ROOT / "data_info" / "AVALANCHE" / "鍏ㄥ眬闈欐€佽〃.md"
MULTI_DEX_DOC_PATH = REPO_ROOT / "data_info" / "AVALANCHE" / "Avalanche澶欴EX鎵╁睍鎵弿.md"
TABLE_PATH = REPO_ROOT / "flashloan" / "app_runtime" / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"
MULTI_DEX_TABLE_PATH = REPO_ROOT / "flashloan" / "app_runtime" / "runtime" / "cache" / "avalanche_multi_dex_v2_pool_discovery.json"
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
        return "鍖呭惈绋冲畾甯佹ˉ鎺ワ紝鍙瀵燂紱鍙墿灞曚负 USDC 璧锋璺緞鍚庡鏍?
    if stable_tokens and "USDC" not in stable_tokens:
        bridge = sorted(stable_tokens)[0]
        return f"绋冲畾甯?{bridge} 璧锋闂幆锛涘彲鍏堝仛 USDC->{bridge}銆佹湯灏?{bridge}->USDC 妗ユ帴鍚庡鏍?
    if all(edge.get("status") == "recommended" for edge in edges):
        return "鍏ㄦ帹鑽愭睜锛屼絾涓嶅睘浜庡綋鍓?USDC 闈炵ǔ瀹氬竵楂樿川閲忛棴鐜彛寰?
    return "鍖呭惈鍙敼鍠勬睜锛屽彧瑙傚療锛屼笉鑷姩鎵ц"


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
        "## 鍏ㄥ眬闈欐€佽〃锛圲I 瑙﹀彂鏌ユ壘琛級",
        "",
        f"- 鏁版嵁婧愶細`{TABLE_PATH.relative_to(REPO_ROOT)}`",
        f"- 缃戠粶锛歚{table.get('network') or 'avalanche'}` / chainId `{table.get('chainId') or table.get('chain_id') or 43114}`",
        f"- 鐢熸垚鏃堕棿锛歚{table.get('generatedAt') or '-'}`",
        f"- 鍙戠幇姹狅細`{table.get('summary', {}).get('staticPoolCount', '-')}`锛屼氦鏄撳锛歚{table.get('summary', {}).get('staticPairCount', '-')}`锛岄珮璐ㄩ噺姹狅細`{table.get('summary', {}).get('strictQualityPoolCount', '-')}`锛岄潤鎬佸€欓€変綅锛歚{table.get('summary', {}).get('preferredPoolCount', '-')}`",
        "- 瑙勫垯锛氶潤鎬佽〃淇濈暀鍏ㄩ噺姹狅紱UI 瑙﹀彂鏃跺厛鍙栭潤鎬佸€欓€変綅锛屽啀鐪嬭川閲忕粨璁哄拰瀹炴椂 Quoter 鎶ヤ环銆傝嫢楂樿川閲忔睜涓嶈冻锛屽€欓€変綅浠呯敤浜庤ˉ浣嶏紝涓嶄唬琛ㄦ帹鑽愭墽琛屻€?,
        "",
        "### 浜ゆ槗瀵圭洰褰?,
        "",
        "| 浜ゆ槗瀵?| 鍏ㄩ噺姹?| 楂樿川閲?| 闈欐€佸€欓€変綅 | 鍊欓€夊湴鍧€ | 璇存槑 |",
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
        ) or "鏃?
        preferred_text = "<br>".join(
            f"`{_short_address(row.get('pool'))}` fee `{row.get('fee')}` TVL `{_fmt_num((row.get('balances') or {}).get('tvlUsd'), 2)}`"
            for row in preferred_rows
        ) or "鏃?
        note = "楂樿川閲忔睜" if quality_rows else "鏃犻珮璐ㄩ噺姹狅紝鍊欓€変綅浠呬綔琛ヤ綅"
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
        "### 鍏ㄩ噺姹犵储寮?,
        "",
        "| 浜ゆ槗瀵?| 姹犲瓙鍦板潃 | fee | 璐ㄩ噺 | TVL USD | token0/token1 | 0鈫? 杈撳嚭浠峰€间繚鐣欑巼 | 1鈫? 杈撳嚭浠峰€间繚鐣欑巼 |",
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
        return '<span style="color:#15803d;font-weight:700">鎺ㄨ崘</span>'
    if status == "improvable":
        return '<span style="color:#b7791f;font-weight:700">鍙敼鍠?/span>'
    return '<span style="color:#667085;font-weight:700">鎷掔粷</span>'


def _status_text(status: str) -> str:
    return {"recommended": "鎺ㄨ崘", "improvable": "鍙敼鍠?, "rejected": "鎷掔粷"}.get(status, "鎷掔粷")


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
                "status": "鎺ㄨ崘闂幆" if all(edge["status"] == "recommended" for edge in edges) else "瑙傚療闂幆",
            }
        )
    triangles.sort(key=lambda item: (0 if item["status"] == "鎺ㄨ崘闂幆" else 1, -float(item["min_tvl"])))
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
        "## 闈欐€佸鍒╄矾寰勫垎鏋?,
        "",
        f"- 浠呮帹鑽愭睜锛氳竟 `{len(recommended_edges)}` 鏉★紱涓夎闂幆 `{len(recommended_triangles)}` 鏉°€?,
        f"- 鎺ㄨ崘 + 鍙敼鍠勬睜锛氳竟 `{len(observable_edges)}` 鏉★紱涓夎瑙傚療闂幆 `{len(observable_triangles)}` 鏉★紱鍚屼氦鏄撳璺ㄦ睜瑙傚療 `{len(same_pair_candidates)}` 缁勩€?,
        "- 缁撹锛氬綋鍓嶇豢鑹叉帹鑽愭睜涓嶈兘鐙珛褰㈡垚闂幆锛涢粍鑹叉睜鍙兘杩涘叆瑙傚療鍜屽鏍革紝涓嶅簲鐩存帴鑷姩鎵ц銆?,
        "",
        "### 娼滃湪涓夎瑙傚療璺緞",
        "",
    ]
    if observable_triangles:
        lines.extend(
            [
                "<table>",
                "<thead>",
                "<tr><th>璺緞</th><th>杈?1</th><th>杈?2</th><th>杈?3</th><th>鏈€浣?TVL USD</th><th>缁撹</th></tr>",
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
        lines.extend(["鏃犮€?, ""])

    lines.extend(["### 鍚屼氦鏄撳璺ㄦ睜瑙傚療", ""])
    if same_pair_candidates:
        lines.extend(
            [
                "<table>",
                "<thead>",
                "<tr><th>浜ゆ槗瀵?/th><th>鍊欓€夋睜鏁伴噺</th><th>鍊欓€夋睜</th><th>鏈€浣?TVL USD</th><th>缁撹</th></tr>",
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
                "<td>鍙仛璺ㄦ睜浠峰樊瑙傚療锛涜Е鍙戝悗蹇呴』 Quoter 绮剧‘澶嶆牳</td>"
                "</tr>"
            )
        lines.extend(["</tbody>", "</table>", ""])
    else:
        lines.extend(["鏃犮€?, ""])
    return lines


def _multi_dex_summary_lines() -> list[str]:
    payload = _safe_read_json(MULTI_DEX_TABLE_PATH)
    if not payload:
        return [
            "## 澶?DEX 鎵╁睍鎵弿",
            "",
            "- 灏氭湭鐢熸垚澶?DEX 鎵╁睍鎵弿缁撴灉銆?,
            "",
        ]
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    report_text = MULTI_DEX_DOC_PATH.read_text(encoding="utf-8") if MULTI_DEX_DOC_PATH.exists() else ""
    combined_recommended = "-"
    combined_observable = "-"
    match = re.search(r"鍚堝苟褰撳墠 V3 \+ V2 鎺ㄨ崘姹??:涓夎|鍥剧粨鏋?闂幆锛歚([^`]+)`", report_text)
    if match:
        combined_recommended = match.group(1)
    effective_recommended = "1" if report_text else "-"
    match = re.search(r"鎺掗櫎(?: USDt/USDT 绛??绋冲畾甯佷腑闂磋烦鍚庣殑鏈夋晥(?:鎺ㄨ崘|楂樿川閲?闂幆锛歚([^`]+)`", report_text)
    if match:
        effective_recommended = match.group(1)
    match = re.search(r"鍚堝苟褰撳墠 V3 \+ V2 鎺ㄨ崘 \+ 鍙敼鍠勪笁瑙掕瀵熼棴鐜細`([^`]+)`", report_text)
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
        "## 澶?DEX 鎵╁睍鎵弿",
        "",
        f"- 鎵╁睍鎵弿鏂囦欢锛歚{MULTI_DEX_DOC_PATH.name}`",
        f"- 鎵╁睍缂撳瓨锛歚{MULTI_DEX_TABLE_PATH.relative_to(REPO_ROOT)}`",
        f"- V2 鎵╁睍姹狅細`{summary.get('poolCount', '-')}`锛涙帹鑽愶細`{summary.get('recommendedPoolCount', '-')}`锛涘彲鏀瑰杽锛歚{summary.get('improvablePoolCount', '-')}`锛涙嫆缁濓細`{summary.get('rejectedPoolCount', '-')}`锛涢敊璇細`{summary.get('errorCount', '-')}`",
        "- 鍘?V3 绾豢鑹叉帹鑽愭睜锛歚6`锛涙帹鑽愭睜涓夎闂幆锛歚0`銆備篃灏辨槸璇达紝鍙湅椤甸潰鍘熸潵鐨?6 涓豢鑹叉睜锛岀‘瀹炰笉鑳藉舰鎴愬鍒╅棴鐜€?,
        f"- 鍚堝苟褰撳墠 V3 + V2 鎺ㄨ崘姹犲浘缁撴瀯闂幆锛歚{combined_recommended}`锛涙帓闄?USDt/USDT 绛夌ǔ瀹氬竵涓棿璺冲悗鐨勬湁鏁堥珮璐ㄩ噺闂幆锛歚{effective_recommended}`锛涙帹鑽?+ 鍙敼鍠勮瀵熼棴鐜細`{combined_observable}`",
        "- 鏈夋晥楂樿川閲忛棴鐜彛寰勶細USDC 闂數璐疯捣鐐?+ 涓や釜闈炵ǔ瀹氳祫浜т腑闂磋烦銆傚寘鍚浜屼釜绋冲畾甯佺殑璺緞锛屼緥濡?`USDC -> USDt -> WAVAX -> USDC`锛屽彧鍙瀵燂紝涓嶈鍏ユ湁鏁堝€欓€夈€?,
    ]
    if paths:
        lines.append("- 褰撳墠杩涘叆绮剧‘澶嶆牳鐨勬湁鏁堥珮璐ㄩ噺璺緞锛? + "锛?.join(f"`{path}`" for path in paths))
    else:
        lines.append("- 褰撳墠娌℃湁鏈夋晥楂樿川閲忚矾寰勮繘鍏ョ簿纭鏍搞€?)
    if missing_edges:
        lines.extend(
            [
                "",
                "<table>",
                "<thead>",
                "<tr><th>琛ラ綈闂幆鐨?V2 鎺ㄨ崘姹?/th><th>DEX</th><th>姹犲瓙</th><th>TVL USD</th><th>鐢ㄩ€?/th></tr>",
                "</thead>",
                "<tbody>",
            ]
        )
        for row in missing_edges:
            purpose = (
                "琛ラ綈 USDC -> WAVAX -> BTC.b -> USDC"
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
    lines.extend(["", "- 璇存槑锛氭墿灞曟壂鎻忓彧瀹屾垚闈欐€佸彂鐜板拰鏈湴鍌ㄥ浼扮畻锛屼笉浠ｈ〃宸茬粡纭鐩堝埄锛涚ǔ瀹氬竵涓棿璺抽棴鐜凡鎺掗櫎锛屼笅涓€姝ュ彧瀵瑰敮涓€鏈夋晥楂樿川閲忚矾寰勫仛 Router/Quoter 绮剧‘澶嶆牳銆?, ""])
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
        f"- 鍏ㄩ噺姹狅細`{len(all_rows)}`锛涢珮璐ㄩ噺姹狅細`{len(_quality_rows(pair))}`锛涢潤鎬佸€欓€変綅锛歚{len(_preferred_rows(pair))}`",
        "",
        "<table>",
        "<thead>",
        "<tr><th>搴忓彿</th><th>姹犲瓙鍦板潃</th><th>fee</th><th>璐ㄩ噺</th><th>闈欐€佸€欓€変綅</th><th>TVL USD</th><th>token0/token1</th><th>liquidity</th><th>tick</th><th>0鈫? 杈撳嚭浠峰€间繚鐣欑巼</th><th>1鈫? 杈撳嚭浠峰€间繚鐣欑巼</th></tr>",
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
        preferred = "鏄? if str(row.get("pool") or "").lower() in preferred_addresses else ""
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
        "# Avalanche 鍏ㄥ眬闈欐€佹睜瀛愯〃",
        "",
        f"- 鏁版嵁婧愶細`{TABLE_PATH.relative_to(REPO_ROOT)}`",
        f"- 缃戠粶锛歚{table.get('network') or 'avalanche'}` / chainId `{table.get('chainId') or table.get('chain_id') or 43114}`",
        f"- 鐢熸垚鏃堕棿锛歚{table.get('generatedAt') or '-'}`",
        f"- 鍏ㄩ噺姹狅細`{summary.get('staticPoolCount', '-')}`锛涗氦鏄撳锛歚{summary.get('staticPairCount', '-')}`锛涢珮璐ㄩ噺姹狅細`{summary.get('strictQualityPoolCount', '-')}`锛涢潤鎬佸€欓€変綅锛歚{summary.get('preferredPoolCount', '-')}`",
        "- 璇存槑锛氭湰鏂囦欢淇濈暀闈欐€佺紦瀛樹腑鐨勫叏閮ㄦ睜瀛愶紝姣忎釜浜ゆ槗瀵瑰崟鐙竴寮犺〃銆?,
        "- `璐ㄩ噺`锛氱豢鑹瞏鎺ㄨ崘`琛ㄧず閫氳繃闈欐€佽川閲忔鏌ワ紱榛勮壊`鍙敼鍠刞琛ㄧず褰撳墠鏈揪鏍囦絾浠嶆湁鍙敤鎶ヤ环鍜屽熀纭€娣卞害锛涚伆鑹瞏鎷掔粷`琛ㄧず褰撳墠涓嶉€傚悎杩涘叆鏈夋晥鍊欓€夈€傚簳灞傝瘖鏂瓧娈典粛淇濆瓨鍦?JSON 涓紝浣嗕笉鍦ㄦ琛ㄩ噸澶嶅睍绀恒€?,
        "- `闈欐€佸€欓€変綅`锛氳〃绀鸿姹犺鎺掑叆璇ヤ氦鏄撳鐨勫€欓€夋Ы锛屼粎鐢ㄤ簬瑙﹀彂淇″彿鏃跺揩閫熸煡鎵撅紝涓嶄唬琛ㄦ帹鑽愭墽琛屾垨褰撳墠鐩堝埄銆?,
        "- `杈撳嚭浠峰€间繚鐣欑巼`锛氫互绾?`1,000 USD` 鎺㈤拡璋冪敤 Quoter锛岃绠?`杈撳嚭浠峰€?梅 杈撳叆浠峰€?脳 100%`銆俙100%` 琛ㄧず浠峰€煎熀鏈畬鏁翠繚鐣欙紝`0%` 琛ㄧず鍑犱箮娌℃湁鏈夋晥杈撳嚭锛涜秴杩?`100%` 鍙〃绀烘寜鍙傝€冧环鏍兼姌绠楀悗杈撳嚭浠峰€兼洿楂橈紝涓嶇瓑浜庢渶缁堝鍒╁埄娑︺€?,
        f"- `鍙敼鍠刞鍒ゅ畾锛歍VL 鈮?`{IMPROVABLE_MIN_TVL_USD:,.0f} USD`銆佸弻鍚戞姤浠峰潎鍙敤锛屼笖鑷冲皯涓€涓柟鍚戣緭鍑轰环鍊间繚鐣欑巼 鈮?`{IMPROVABLE_MIN_RETENTION_PCT:.0f}%`銆?,
        "",
        *_multi_dex_summary_lines(),
        *_render_static_path_analysis(table),
        "## 浜ゆ槗瀵规睜瀛愭槑缁?,
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
            "## 鍏ㄥ眬闈欐€佽〃",
            "",
            "- 闈欐€佹睜瀛愬凡鎷嗗垎涓虹嫭绔嬫枃浠讹細`鍏ㄥ眬闈欐€佽〃.md`",
            f"- 缃戠粶锛歚{table.get('network') or 'avalanche'}` / chainId `{table.get('chainId') or table.get('chain_id') or 43114}`",
            f"- 鍏ㄩ噺姹狅細`{summary.get('staticPoolCount', '-')}`锛涗氦鏄撳锛歚{summary.get('staticPairCount', '-')}`锛涢珮璐ㄩ噺姹狅細`{summary.get('strictQualityPoolCount', '-')}`锛涢潤鎬佸€欓€変綅锛歚{summary.get('preferredPoolCount', '-')}`",
            "- 瑙勫垯锛氱嫭绔嬫枃浠朵繚鐣欏叏閮ㄦ睜瀛愶紝骞舵寜浜ゆ槗瀵瑰垎琛紱UI 瑙﹀彂鏃跺啀鏍规嵁瀹炴椂杈撳叆銆佽川閲忕粨璁哄拰 Quoter 鎶ヤ环杩涜浜屾閫夋嫨銆?,
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
        anchor = "## 鎬昏"
        index = doc.find(anchor)
        if index < 0:
            raise RuntimeError("cannot find insertion anchor ## 鎬昏 in 鏁版嵁.md")
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
