"""Discover Avalanche V2-style pools and analyze static arbitrage coverage."""

from __future__ import annotations

import itertools
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from web3 import Web3


REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "flashloan" / "app_runtime"
CURRENT_STATIC_TABLE = SRC_ROOT / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"
OUTPUT_JSON = SRC_ROOT / "runtime" / "cache" / "avalanche_multi_dex_v2_pool_discovery.json"
OUTPUT_MD = REPO_ROOT / "data_info" / "AVALANCHE" / "Avalanche多DEX扩展扫描.md"

ZERO = "0x0000000000000000000000000000000000000000"
PROBE_USD = Decimal("1000")
MIN_RECOMMENDED_TVL_USD = Decimal("50000")
MIN_RECOMMENDED_SIDE_USD = Decimal("5000")
MIN_IMPROVABLE_TVL_USD = Decimal("1000")
MIN_IMPROVABLE_RETENTION_PCT = Decimal("50")
STABLE_SYMBOLS = {"USDC", "USDC.E", "USDT", "USDT.E", "USDT", "USDt", "DAI", "DAI.E", "EURC", "FRAX", "USDE", "USDe", "SUSDE", "sUSDe"}

V2_FACTORY_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "tokenA", "type": "address"}, {"name": "tokenB", "type": "address"}],
        "name": "getPair",
        "outputs": [{"name": "pair", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

V2_PAIR_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "token0",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "token1",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"name": "_reserve0", "type": "uint112"},
            {"name": "_reserve1", "type": "uint112"},
            {"name": "_blockTimestampLast", "type": "uint32"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

DEX_FACTORIES = [
    {
        "name": "trader_joe_v1",
        "factory": "0x9Ad6C38BE94206cA50bb0d90783181662f0Cfa10",
        "feeBps": 30,
    },
    {
        "name": "pangolin_v2",
        "factory": "0xefa94DE7a4656D787667C749f7E1223D71E9FD88",
        "feeBps": 30,
    },
    {
        "name": "sushi_v2",
        "factory": "0xc35DADB65012eC5796536bD9864eD8773aBc74C4",
        "feeBps": 30,
    },
]


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


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
        return f"{parsed:,.2f}".rstrip("0").rstrip(".")
    text = f"{parsed:.{places}f}".rstrip("0").rstrip(".")
    return text or "0"


def _short_address(value: Any) -> str:
    text = str(value or "").strip()
    return f"{text[:10]}...{text[-6:]}" if text.startswith("0x") and len(text) == 42 else text


def _amount(raw: int | Decimal, decimals: int) -> Decimal:
    return Decimal(str(raw)) / (Decimal(10) ** decimals)


def _load_token_universe(table: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tokens: dict[str, dict[str, Any]] = {}
    prices = table.get("priceReferences") if isinstance(table.get("priceReferences"), dict) else {}

    def remember(address: Any, symbol: Any, decimals: Any) -> None:
        text = str(address or "").strip()
        if not text.startswith("0x") or len(text) != 42:
            return
        sym = str(symbol or "").strip()
        if not sym:
            return
        try:
            dec = int(decimals)
        except (TypeError, ValueError):
            dec = 18
        price = _decimal(prices.get(sym.upper()))
        tokens[text.lower()] = {
            "address": Web3.to_checksum_address(text),
            "symbol": sym,
            "decimals": dec,
            "priceUsd": price,
        }

    for pair in table.get("pairs") or []:
        if not isinstance(pair, dict):
            continue
        remember(pair.get("tokenX"), pair.get("tokenXSymbol") or pair.get("tokenX_symbol"), 18)
        remember(pair.get("tokenY"), pair.get("tokenYSymbol") or pair.get("tokenY_symbol"), 18)
        for row in pair.get("allPools") or []:
            if not isinstance(row, dict):
                continue
            remember(row.get("token0"), row.get("token0Symbol") or row.get("token0_symbol"), row.get("token0Decimals") or row.get("token0_decimals"))
            remember(row.get("token1"), row.get("token1Symbol") or row.get("token1_symbol"), row.get("token1Decimals") or row.get("token1_decimals"))
    return tokens


def _quote_retention_pct(
    *,
    reserve_in: Decimal,
    reserve_out: Decimal,
    price_in: Decimal,
    price_out: Decimal,
    fee_bps: int,
) -> Decimal | None:
    if reserve_in <= 0 or reserve_out <= 0 or price_in <= 0 or price_out <= 0:
        return None
    amount_in = PROBE_USD / price_in
    fee_denominator = Decimal("10000")
    amount_in_with_fee = amount_in * (fee_denominator - Decimal(fee_bps))
    amount_out = (amount_in_with_fee * reserve_out) / (reserve_in * fee_denominator + amount_in_with_fee)
    output_usd = amount_out * price_out
    return (output_usd / PROBE_USD) * Decimal("100")


def _pool_status(tvl: Decimal | None, side0: Decimal | None, side1: Decimal | None, r01: Decimal | None, r10: Decimal | None) -> str:
    if (
        tvl is not None
        and side0 is not None
        and side1 is not None
        and r01 is not None
        and r10 is not None
        and tvl >= MIN_RECOMMENDED_TVL_USD
        and side0 >= MIN_RECOMMENDED_SIDE_USD
        and side1 >= MIN_RECOMMENDED_SIDE_USD
        and r01 >= Decimal("95")
        and r10 >= Decimal("95")
    ):
        return "recommended"
    if (
        tvl is not None
        and r01 is not None
        and r10 is not None
        and tvl >= MIN_IMPROVABLE_TVL_USD
        and max(r01, r10) >= MIN_IMPROVABLE_RETENTION_PCT
    ):
        return "improvable"
    return "rejected"


def discover() -> dict[str, Any]:
    table = _read_json(CURRENT_STATIC_TABLE)
    tokens = _load_token_universe(table)
    rpc_url = str(table.get("rpcUrl") or "https://api.avax.network/ext/bc/C/rpc")
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
    if not w3.is_connected():
        raise RuntimeError(f"cannot connect rpc: {rpc_url}")

    token_items = list(tokens.values())
    pools: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    for dex in DEX_FACTORIES:
        factory_address = Web3.to_checksum_address(dex["factory"])
        factory = w3.eth.contract(address=factory_address, abi=V2_FACTORY_ABI)
        for left, right in itertools.combinations(token_items, 2):
            try:
                pair_address = factory.functions.getPair(left["address"], right["address"]).call()
            except Exception as exc:
                errors.append({"dex": dex["name"], "pair": f"{left['symbol']}:{right['symbol']}", "error": str(exc)[:160]})
                continue
            if not pair_address or pair_address.lower() == ZERO.lower():
                continue
            pair_address = Web3.to_checksum_address(pair_address)
            if pair_address.lower() in pools:
                continue
            pair = w3.eth.contract(address=pair_address, abi=V2_PAIR_ABI)
            try:
                token0 = Web3.to_checksum_address(pair.functions.token0().call())
                token1 = Web3.to_checksum_address(pair.functions.token1().call())
                reserve0_raw, reserve1_raw, _ = pair.functions.getReserves().call()
            except Exception as exc:
                errors.append({"dex": dex["name"], "pool": pair_address, "error": str(exc)[:160]})
                continue
            token0_meta = tokens.get(token0.lower())
            token1_meta = tokens.get(token1.lower())
            if not token0_meta or not token1_meta:
                continue
            reserve0 = _amount(int(reserve0_raw), int(token0_meta["decimals"]))
            reserve1 = _amount(int(reserve1_raw), int(token1_meta["decimals"]))
            price0 = token0_meta.get("priceUsd")
            price1 = token1_meta.get("priceUsd")
            side0 = reserve0 * price0 if isinstance(price0, Decimal) else None
            side1 = reserve1 * price1 if isinstance(price1, Decimal) else None
            tvl = side0 + side1 if side0 is not None and side1 is not None else None
            r01 = _quote_retention_pct(
                reserve_in=reserve0,
                reserve_out=reserve1,
                price_in=price0,
                price_out=price1,
                fee_bps=int(dex["feeBps"]),
            ) if isinstance(price0, Decimal) and isinstance(price1, Decimal) else None
            r10 = _quote_retention_pct(
                reserve_in=reserve1,
                reserve_out=reserve0,
                price_in=price1,
                price_out=price0,
                fee_bps=int(dex["feeBps"]),
            ) if isinstance(price0, Decimal) and isinstance(price1, Decimal) else None
            status = _pool_status(tvl, side0, side1, r01, r10)
            pools[pair_address.lower()] = {
                "dex": dex["name"],
                "protocol": "v2",
                "factory": factory_address,
                "pool": pair_address,
                "feeBps": dex["feeBps"],
                "token0": token0,
                "token1": token1,
                "token0Symbol": token0_meta["symbol"],
                "token1Symbol": token1_meta["symbol"],
                "token0Decimals": token0_meta["decimals"],
                "token1Decimals": token1_meta["decimals"],
                "reserve0": str(reserve0),
                "reserve1": str(reserve1),
                "token0Usd": str(side0) if side0 is not None else None,
                "token1Usd": str(side1) if side1 is not None else None,
                "tvlUsd": str(tvl) if tvl is not None else None,
                "retention0To1Pct": str(r01) if r01 is not None else None,
                "retention1To0Pct": str(r10) if r10 is not None else None,
                "status": status,
                "symbolPair": ":".join(sorted([token0_meta["symbol"], token1_meta["symbol"]])),
            }

    payload = {
        "chainId": 43114,
        "network": "avalanche",
        "mode": "multi_dex_v2_extension_discovery",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "rpcUrl": rpc_url,
        "sourceStaticTable": str(CURRENT_STATIC_TABLE.relative_to(REPO_ROOT)),
        "tokenCount": len(tokens),
        "dexFactories": DEX_FACTORIES,
        "pools": sorted(pools.values(), key=lambda row: (row["symbolPair"], row["dex"], row["pool"])),
        "errors": errors[:50],
    }
    counts = {"recommended": 0, "improvable": 0, "rejected": 0}
    for row in payload["pools"]:
        counts[str(row["status"])] = counts.get(str(row["status"]), 0) + 1
    payload["summary"] = {
        "poolCount": len(payload["pools"]),
        "recommendedPoolCount": counts.get("recommended", 0),
        "improvablePoolCount": counts.get("improvable", 0),
        "rejectedPoolCount": counts.get("rejected", 0),
        "errorCount": len(errors),
    }
    return payload


def _edge_candidates(payload: dict[str, Any], allowed: set[str]) -> list[dict[str, Any]]:
    return [
        row
        for row in payload.get("pools") or []
        if isinstance(row, dict) and str(row.get("status")) in allowed
    ]


def _triangles(rows: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    edges = {tuple(sorted(str(row.get("symbolPair") or "").split(":"))) for row in rows if ":" in str(row.get("symbolPair") or "")}
    nodes = sorted({node for edge in edges for node in edge})
    found = []
    for a, b, c in itertools.combinations(nodes, 3):
        if all(tuple(sorted(pair)) in edges for pair in [(a, b), (b, c), (a, c)]):
            found.append((a, b, c))
    return found


def _current_v3_status(row: dict[str, Any]) -> str:
    if row.get("quality") == "pass":
        return "recommended"
    balances = row.get("balances") if isinstance(row.get("balances"), dict) else {}
    try:
        tvl = Decimal(str(balances.get("tvlUsd")))
    except (InvalidOperation, TypeError, ValueError):
        tvl = Decimal("0")
    quotes = row.get("quotes") if isinstance(row.get("quotes"), dict) else {}

    def retention(key: str) -> Decimal | None:
        quote = quotes.get(key) if isinstance(quotes.get(key), dict) else {}
        loss = _decimal(quote.get("outputLossPct"))
        return Decimal("100") - loss if loss is not None else None

    r01 = retention("token0ToToken1")
    r10 = retention("token1ToToken0")
    if tvl >= MIN_IMPROVABLE_TVL_USD and r01 is not None and r10 is not None and max(r01, r10) >= MIN_IMPROVABLE_RETENTION_PCT:
        return "improvable"
    return "rejected"


def _current_v3_edges() -> list[dict[str, Any]]:
    table = _read_json(CURRENT_STATIC_TABLE)
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
            balances = row.get("balances") if isinstance(row.get("balances"), dict) else {}
            edges.append(
                {
                    "a": tokens[0],
                    "b": tokens[1],
                    "symbolPair": symbol_pair,
                    "dex": "current_v3",
                    "pool": row.get("pool"),
                    "fee": row.get("fee"),
                    "status": _current_v3_status(row),
                    "tvlUsd": balances.get("tvlUsd"),
                }
            )
    return edges


def _combined_edges(payload: dict[str, Any]) -> list[dict[str, Any]]:
    edges = _current_v3_edges()
    for row in payload.get("pools") or []:
        if not isinstance(row, dict):
            continue
        tokens = [item.strip() for item in str(row.get("symbolPair") or "").split(":") if item.strip()]
        if len(tokens) != 2:
            continue
        edges.append(
            {
                "a": tokens[0],
                "b": tokens[1],
                "symbolPair": row.get("symbolPair"),
                "dex": row.get("dex"),
                "pool": row.get("pool"),
                "fee": f"{row.get('feeBps')} bps",
                "status": row.get("status"),
                "tvlUsd": row.get("tvlUsd"),
            }
        )
    return edges


def _best_edges(edges: list[dict[str, Any]], allowed: set[str]) -> dict[tuple[str, str], dict[str, Any]]:
    rank = {"recommended": 0, "improvable": 1, "rejected": 2}
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for edge in edges:
        if str(edge.get("status")) not in allowed:
            continue
        key = tuple(sorted([str(edge["a"]), str(edge["b"])]))
        previous = best.get(key)
        tvl = _decimal(edge.get("tvlUsd")) or Decimal("0")
        score = (rank.get(str(edge.get("status")), 9), -tvl)
        if previous is None:
            best[key] = edge
            continue
        previous_tvl = _decimal(previous.get("tvlUsd")) or Decimal("0")
        previous_score = (rank.get(str(previous.get("status")), 9), -previous_tvl)
        if score < previous_score:
            best[key] = edge
    return best


def _triangle_details(best_edges: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    nodes = sorted({node for key in best_edges for node in key})
    triangles: list[dict[str, Any]] = []
    for a, b, c in itertools.combinations(nodes, 3):
        keys = [tuple(sorted(pair)) for pair in [(a, b), (b, c), (a, c)]]
        if not all(key in best_edges for key in keys):
            continue
        edges = [best_edges[key] for key in keys]
        min_tvl = min((_decimal(edge.get("tvlUsd")) or Decimal("0")) for edge in edges)
        triangles.append({"path": f"{a} -> {b} -> {c} -> {a}", "edges": edges, "minTvlUsd": min_tvl})
    triangles.sort(key=lambda item: -item["minTvlUsd"])
    return triangles


def _edge_text(edge: dict[str, Any]) -> str:
    status_text = {"recommended": "推荐", "improvable": "可改善", "rejected": "拒绝"}.get(str(edge.get("status")), str(edge.get("status")))
    return (
        f"{edge.get('symbolPair')}<br>"
        f"{edge.get('dex')} / {status_text} / fee {edge.get('fee') or '-'}<br>"
        f"TVL {_fmt_num(edge.get('tvlUsd'), 2)} / {_short_address(edge.get('pool'))}"
    )


def _path_tokens(path: Any) -> list[str]:
    return [token.strip() for token in str(path or "").replace("->", " -> ").split(" -> ") if token.strip()]


def _stable_token_set(tokens: list[str]) -> set[str]:
    stable_upper = {item.upper() for item in STABLE_SYMBOLS}
    return {token for token in dict.fromkeys(tokens) if token.upper() in stable_upper}


def _is_effective_recommended_triangle(item: dict[str, Any]) -> bool:
    unique_tokens = list(dict.fromkeys(_path_tokens(item.get("path"))))
    stable_tokens = _stable_token_set(unique_tokens)
    non_stable_count = len([token for token in unique_tokens if token not in stable_tokens])
    # The flash-loan route is USDC based. A second stablecoin such as USDt/USDT
    # is a stable-stable bridge, not a real high-quality arbitrage triangle.
    return "USDC" in stable_tokens and len(stable_tokens) == 1 and non_stable_count >= 2


def _effective_recommended_triangles(triangles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in triangles if _is_effective_recommended_triangle(item)]


def _observation_conclusion(item: dict[str, Any]) -> str:
    stable_tokens = _stable_token_set(_path_tokens(item.get("path")))
    if len(stable_tokens) >= 2:
        return "包含稳定币桥接，只观察；可扩展为 USDC 起止路径后复核"
    if stable_tokens and "USDC" not in stable_tokens:
        bridge = sorted(stable_tokens)[0]
        return f"稳定币 {bridge} 起止闭环；可先做 USDC->{bridge}、末尾 {bridge}->USDC 桥接后复核"
    if all(edge.get("status") == "recommended" for edge in item["edges"]):
        return "全推荐池，但不属于当前 USDC 非稳定币高质量闭环口径"
    return "包含可改善池，只观察，不自动执行"


def _display_loop_path(path: str) -> str:
    tokens = [token.strip() for token in path.split(" -> ") if token.strip()]
    token_set = set(tokens)
    if {"USDC", "WAVAX", "BTC.b"}.issubset(token_set):
        return "USDC -> WAVAX -> BTC.b -> USDC"
    return path


def build_report(payload: dict[str, Any]) -> str:
    rows = payload.get("pools") if isinstance(payload.get("pools"), list) else []
    recommended = _edge_candidates(payload, {"recommended"})
    observable = _edge_candidates(payload, {"recommended", "improvable"})
    recommended_triangles = _triangles(recommended)
    observable_triangles = _triangles(observable)
    combined = _combined_edges(payload)
    combined_recommended = _triangle_details(_best_edges(combined, {"recommended"}))
    effective_combined_recommended = _effective_recommended_triangles(combined_recommended)
    combined_observable = _triangle_details(_best_edges(combined, {"recommended", "improvable"}))
    lines = [
        "# Avalanche 多 DEX 扩展扫描",
        "",
        f"- 生成时间：`{payload.get('generatedAt')}`",
        f"- 代币数量：`{payload.get('tokenCount')}`",
        f"- V2 扩展池：`{payload.get('summary', {}).get('poolCount')}`",
        f"- 推荐：`{payload.get('summary', {}).get('recommendedPoolCount')}`；可改善：`{payload.get('summary', {}).get('improvablePoolCount')}`；拒绝：`{payload.get('summary', {}).get('rejectedPoolCount')}`",
        f"- 仅 V2 推荐池三角闭环：`{len(recommended_triangles)}`",
        f"- V2 推荐 + 可改善三角观察闭环：`{len(observable_triangles)}`",
        f"- 合并当前 V3 + V2 推荐池图结构闭环：`{len(combined_recommended)}`",
        f"- 排除 USDt/USDT 等稳定币中间跳后的有效高质量闭环：`{len(effective_combined_recommended)}`",
        f"- 合并当前 V3 + V2 推荐 + 可改善三角观察闭环：`{len(combined_observable)}`",
        "",
        "## 扫描结论",
        "",
    ]
    if recommended_triangles:
        lines.append("- V2 扩展扫描已发现推荐池闭环，可以进入 Quoter/Router 精确复核。")
    elif observable_triangles:
        lines.append("- V2 扩展扫描暂未发现推荐池闭环，但存在观察闭环；需要合并 V3/LB 后再判断。")
    else:
        lines.append("- V2 扩展扫描未发现可用闭环；应继续补 Trader Joe LB/V3 或考虑换链。")
    if effective_combined_recommended:
        lines.append("- 合并当前 V3 与 V2 后已出现有效推荐闭环，下一步应对这些路径做精确报价复核。")
    lines.extend(["", "## 合并 V3 + V2 有效高质量闭环", ""])
    if effective_combined_recommended:
        lines.extend(["<table>", "<thead>", "<tr><th>路径</th><th>边 1</th><th>边 2</th><th>边 3</th><th>最低 TVL USD</th><th>下一步</th></tr>", "</thead>", "<tbody>"])
        for item in effective_combined_recommended:
            edge_cells = [_edge_text(edge) for edge in item["edges"]]
            lines.append(
                "<tr>"
                f"<td>{_display_loop_path(item['path'])}</td>"
                f"<td>{edge_cells[0]}</td>"
                f"<td>{edge_cells[1]}</td>"
                f"<td>{edge_cells[2]}</td>"
                f"<td>{_fmt_num(item['minTvlUsd'], 2)}</td>"
                "<td>进入 Router/Quoter 精确复核，不直接执行</td>"
                "</tr>"
            )
        lines.extend(["</tbody>", "</table>"])
    else:
        lines.append("无。")

    effective_paths = {_display_loop_path(str(item.get("path") or "")) for item in effective_combined_recommended}
    observation_only = [
        item
        for item in combined_observable
        if _display_loop_path(str(item.get("path") or "")) not in effective_paths
    ]
    lines.extend(
        [
            "",
            "## 合并 V3 + V2 观察路径（不计入有效高质量闭环）",
            "",
            "- 本节只用于人工观察。包含 USDt/USDT/USDe/DAI.e 等稳定币中间跳，或包含黄色可改善池的路径，都不计入有效高质量闭环。",
            "",
        ]
    )
    if observation_only:
        lines.extend(["<table>", "<thead>", "<tr><th>路径</th><th>边 1</th><th>边 2</th><th>边 3</th><th>最低 TVL USD</th><th>结论</th></tr>", "</thead>", "<tbody>"])
        for item in observation_only:
            edge_cells = [_edge_text(edge) for edge in item["edges"]]
            lines.append(
                "<tr>"
                f"<td>{item['path']}</td>"
                f"<td>{edge_cells[0]}</td>"
                f"<td>{edge_cells[1]}</td>"
                f"<td>{edge_cells[2]}</td>"
                f"<td>{_fmt_num(item['minTvlUsd'], 2)}</td>"
                f"<td>{_observation_conclusion(item)}</td>"
                "</tr>"
            )
        lines.extend(["</tbody>", "</table>"])
    else:
        lines.append("无。")
    lines.extend(["", "## V2 扩展池明细", "", "<table>", "<thead>", "<tr><th>交易对</th><th>DEX</th><th>池子</th><th>状态</th><th>TVL USD</th><th>token0 USD</th><th>token1 USD</th><th>0→1 保留率</th><th>1→0 保留率</th></tr>", "</thead>", "<tbody>"])
    status_text = {"recommended": "推荐", "improvable": "可改善", "rejected": "拒绝"}
    for row in sorted(rows, key=lambda item: ({"recommended": 0, "improvable": 1, "rejected": 2}.get(str(item.get("status")), 9), -float(item.get("tvlUsd") or 0))):
        lines.append(
            "<tr>"
            f"<td>{row.get('symbolPair')}</td>"
            f"<td>{row.get('dex')}</td>"
            f"<td>{_short_address(row.get('pool'))}</td>"
            f"<td>{status_text.get(str(row.get('status')), row.get('status'))}</td>"
            f"<td>{_fmt_num(row.get('tvlUsd'), 2)}</td>"
            f"<td>{_fmt_num(row.get('token0Usd'), 2)}</td>"
            f"<td>{_fmt_num(row.get('token1Usd'), 2)}</td>"
            f"<td>{_fmt_num(row.get('retention0To1Pct'), 4)}%</td>"
            f"<td>{_fmt_num(row.get('retention1To0Pct'), 4)}%</td>"
            "</tr>"
        )
    lines.extend(["</tbody>", "</table>", ""])
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    payload = discover()
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(build_report(payload), encoding="utf-8")
    print(json.dumps({"ok": True, "json": str(OUTPUT_JSON), "report": str(OUTPUT_MD), "summary": payload.get("summary")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
