"""Build a high-quality-only Avalanche pool summary document."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
V3_TABLE_PATH = REPO_ROOT / "flashloan" / "src_bot" / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"
V2_TABLE_PATH = REPO_ROOT / "flashloan" / "src_bot" / "runtime" / "cache" / "avalanche_multi_dex_v2_pool_discovery.json"
OUTPUT_PATH = REPO_ROOT / "data_info" / "AVALANCHE" / "高质量池子总结表.md"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _safe(value: Any) -> str:
    return html.escape(str(value if value is not None else "-").replace("\n", " "), quote=True)


def _short_address(value: Any) -> str:
    text = str(value or "").strip()
    return f"{text[:10]}...{text[-6:]}" if text.startswith("0x") and len(text) == 42 else text


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


def _retention_from_loss(value: Any) -> str:
    try:
        loss_pct = float(value)
    except (TypeError, ValueError):
        return "-"
    if loss_pct != loss_pct:
        return "-"
    return f"{100 - loss_pct:.4f}%"


def _v3_high_quality_rows(table: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pair in table.get("pairs") or []:
        if not isinstance(pair, dict):
            continue
        symbol_pair = str(pair.get("symbolPair") or "")
        for row in pair.get("allPools") or []:
            if not isinstance(row, dict) or str(row.get("quality") or "").lower() != "pass":
                continue
            balances = row.get("balances") if isinstance(row.get("balances"), dict) else {}
            quotes = row.get("quotes") if isinstance(row.get("quotes"), dict) else {}
            q01 = quotes.get("token0ToToken1") if isinstance(quotes.get("token0ToToken1"), dict) else {}
            q10 = quotes.get("token1ToToken0") if isinstance(quotes.get("token1ToToken0"), dict) else {}
            rows.append(
                {
                    "pair": symbol_pair,
                    "dex": "current_v3",
                    "protocol": "v3",
                    "pool": row.get("pool"),
                    "fee": row.get("fee"),
                    "tvlUsd": balances.get("tvlUsd"),
                    "token0Usd": balances.get("token0Usd"),
                    "token1Usd": balances.get("token1Usd"),
                    "token0Symbol": row.get("token0Symbol") or row.get("token0_symbol"),
                    "token1Symbol": row.get("token1Symbol") or row.get("token1_symbol"),
                    "retention0To1": _retention_from_loss(q01.get("outputLossPct")),
                    "retention1To0": _retention_from_loss(q10.get("outputLossPct")),
                }
            )
    return rows


def _v2_high_quality_rows(table: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in table.get("pools") or []:
        if not isinstance(row, dict) or str(row.get("status")) != "recommended":
            continue
        rows.append(
            {
                "pair": row.get("symbolPair"),
                "dex": row.get("dex"),
                "protocol": row.get("protocol") or "v2",
                "pool": row.get("pool"),
                "fee": f"{row.get('feeBps')} bps",
                "tvlUsd": row.get("tvlUsd"),
                "token0Usd": row.get("token0Usd"),
                "token1Usd": row.get("token1Usd"),
                "token0Symbol": row.get("token0Symbol"),
                "token1Symbol": row.get("token1Symbol"),
                "retention0To1": f"{_fmt_num(row.get('retention0To1Pct'), 4)}%",
                "retention1To0": f"{_fmt_num(row.get('retention1To0Pct'), 4)}%",
            }
        )
    return rows


def _loop_membership(row: dict[str, Any]) -> str:
    pair = str(row.get("pair") or "")
    dex = str(row.get("dex") or "")
    loops = []
    if pair in {"BTC.b:USDC", "WAVAX:USDC"} or (pair == "BTC.b:WAVAX" and dex == "trader_joe_v1"):
        loops.append("USDC->WAVAX->BTC.b->USDC")
    return "<br>".join(_safe(loop) for loop in loops) or "-"


def build_document() -> str:
    v3 = _read_json(V3_TABLE_PATH)
    v2 = _read_json(V2_TABLE_PATH)
    rows = _v3_high_quality_rows(v3) + _v2_high_quality_rows(v2)
    rows.sort(key=lambda row: (str(row.get("pair")), str(row.get("dex")), -float(row.get("tvlUsd") or 0)))
    v3_count = sum(1 for row in rows if row.get("protocol") == "v3")
    v2_count = sum(1 for row in rows if row.get("protocol") == "v2")
    lines = [
        "# Avalanche 高质量池子总结表",
        "",
        "- 本表只包含绿色推荐/高质量池子。",
        f"- 高质量池总数：`{len(rows)}`；V3：`{v3_count}`；V2 扩展：`{v2_count}`。",
        "- 当前有效高质量闭环：`1` 条，且只有 `USDC->WAVAX->BTC.b->USDC`。",
        "- 有效闭环口径：USDC 闪电贷起点 + 两个非稳定资产中间跳；`USDC->USDt->WAVAX->USDC` 这类包含第二个稳定币的路径已排除。",
        "- 这里仍只是静态池图结构成立，需要 Router/Quoter 精确复核后才能判断是否盈利。",
        "",
        "## 唯一有效高质量闭环",
        "",
        "<table>",
        "<thead><tr><th>闭环</th><th>边 1</th><th>边 2</th><th>边 3</th><th>状态</th></tr></thead>",
        "<tbody>",
        "<tr><td>USDC->WAVAX->BTC.b->USDC</td><td>WAVAX:USDC / current_v3</td><td>BTC.b:WAVAX / trader_joe_v1</td><td>BTC.b:USDC / current_v3</td><td>待精确复核</td></tr>",
        "</tbody>",
        "</table>",
        "",
        "## 高质量池子明细",
        "",
        "<table>",
        "<thead>",
        "<tr><th>序号</th><th>交易对</th><th>DEX</th><th>协议</th><th>池子</th><th>fee</th><th>TVL USD</th><th>token0/token1</th><th>token0 USD</th><th>token1 USD</th><th>0→1 保留率</th><th>1→0 保留率</th><th>参与闭环</th></tr>",
        "</thead>",
        "<tbody>",
    ]
    for index, row in enumerate(rows, start=1):
        token_pair = f"{row.get('token0Symbol') or '-'} / {row.get('token1Symbol') or '-'}"
        lines.append(
            "<tr>"
            f"<td>{index}</td>"
            f"<td>{_safe(row.get('pair'))}</td>"
            f"<td>{_safe(row.get('dex'))}</td>"
            f"<td>{_safe(row.get('protocol'))}</td>"
            f"<td>{_safe(_short_address(row.get('pool')))}</td>"
            f"<td>{_safe(row.get('fee'))}</td>"
            f"<td>{_safe(_fmt_num(row.get('tvlUsd'), 2))}</td>"
            f"<td>{_safe(token_pair)}</td>"
            f"<td>{_safe(_fmt_num(row.get('token0Usd'), 2))}</td>"
            f"<td>{_safe(_fmt_num(row.get('token1Usd'), 2))}</td>"
            f"<td>{_safe(row.get('retention0To1'))}</td>"
            f"<td>{_safe(row.get('retention1To0'))}</td>"
            f"<td>{_loop_membership(row)}</td>"
            "</tr>"
        )
    lines.extend(["</tbody>", "</table>", ""])
    return "\n".join(lines)


def main() -> int:
    OUTPUT_PATH.write_text(build_document(), encoding="utf-8")
    print(OUTPUT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
