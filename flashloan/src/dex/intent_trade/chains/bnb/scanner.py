"""Scan BNB Chain PancakeSwap pools for local arbitrage route discovery."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from web3 import Web3


SRC_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = SRC_ROOT.parents[1]

from dex.intent_trade.chains.bnb import config as bnb


OUTPUT_JSON = SRC_ROOT / "runtime" / "cache" / "bnb_arbitrage_pool_static_table.json"
OUTPUT_MD = REPO_ROOT / "data_info" / "BNB" / "BNB链套利扫描.md"

V2_FACTORY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "", "type": "address"},
            {"internalType": "address", "name": "", "type": "address"},
        ],
        "name": "getPair",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]
V2_PAIR_ABI = [
    {
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"internalType": "uint112", "name": "_reserve0", "type": "uint112"},
            {"internalType": "uint112", "name": "_reserve1", "type": "uint112"},
            {"internalType": "uint32", "name": "_blockTimestampLast", "type": "uint32"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {"inputs": [], "name": "token0", "outputs": [{"internalType": "address", "name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "token1", "outputs": [{"internalType": "address", "name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
]
V3_FACTORY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"},
        ],
        "name": "getPool",
        "outputs": [{"internalType": "address", "name": "pool", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]
V3_POOL_ABI = [
    {"inputs": [], "name": "liquidity", "outputs": [{"internalType": "uint128", "name": "", "type": "uint128"}], "stateMutability": "view", "type": "function"},
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"internalType": "int24", "name": "tick", "type": "int24"},
            {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
            {"internalType": "uint32", "name": "feeProtocol", "type": "uint32"},
            {"internalType": "bool", "name": "unlocked", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {"inputs": [], "name": "token0", "outputs": [{"internalType": "address", "name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "token1", "outputs": [{"internalType": "address", "name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
]
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    }
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _connect(rpc_urls: list[str]) -> tuple[Web3, str]:
    errors = []
    for url in rpc_urls:
        w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 15}))
        try:
            if w3.is_connected() and int(w3.eth.chain_id) == bnb.CHAIN_ID:
                return w3, url
            errors.append(f"{url}: chain mismatch or not connected")
        except Exception as exc:
            errors.append(f"{url}: {exc!r}")
    raise RuntimeError("no usable BNB RPC: " + "; ".join(errors[:3]))


def _fetch_prices() -> dict[str, float]:
    prices: dict[str, float] = {"USDT": 1.0, "USDC": 1.0, "BUSD": 1.0, "DAI": 1.0}
    for symbol, token in bnb.TOKENS.items():
        if token.stable:
            continue
        if not token.binance_symbol:
            continue
        try:
            req = Request(
                f"https://api.binance.com/api/v3/ticker/price?symbol={token.binance_symbol}",
                headers={"User-Agent": "flashloan-bnb-scan/1.0"},
            )
            with urlopen(req, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
            prices[symbol] = float(payload["price"])
        except Exception:
            prices[symbol] = 0.0
    return prices


def _amount(raw: int, decimals: int) -> float:
    return int(raw) / float(10 ** decimals)


def _token_by_address() -> dict[str, bnb.TokenConfig]:
    return {Web3.to_checksum_address(token.address).lower(): token for token in bnb.TOKENS.values()}


def _status(tvl_usd: float, side0_usd: float, side1_usd: float) -> str:
    if tvl_usd >= 100_000 and min(side0_usd, side1_usd) >= 10_000:
        return "recommended"
    if tvl_usd >= 10_000 and min(side0_usd, side1_usd) >= 1_000:
        return "improvable"
    return "rejected"


def _safe_float(value: float) -> float:
    return value if math.isfinite(value) else 0.0


def _infer_prices_from_v2_rows(rows: list[dict], prices: dict[str, float]) -> dict[str, float]:
    """Use the deepest stable-token V2 pools when an external price feed is absent."""
    stable_symbols = bnb.stable_symbols()
    for _ in range(len(bnb.TOKENS)):
        changed = False
        candidates: dict[str, tuple[float, float]] = {}
        for row in rows:
            token0 = str(row.get("token0Symbol") or "")
            token1 = str(row.get("token1Symbol") or "")
            amount0 = float(row.get("token0Amount") or 0)
            amount1 = float(row.get("token1Amount") or 0)
            price0 = float(prices.get(token0) or 0)
            price1 = float(prices.get(token1) or 0)
            if token0 in stable_symbols and amount0 > 0 and amount1 > 0 and price1 <= 0:
                score = amount0 * price0
                candidates[token1] = max(candidates.get(token1, (0.0, 0.0)), (score, score / amount1))
            if token1 in stable_symbols and amount0 > 0 and amount1 > 0 and price0 <= 0:
                score = amount1 * price1
                candidates[token0] = max(candidates.get(token0, (0.0, 0.0)), (score, score / amount0))
        for symbol, (_, value) in candidates.items():
            if value > 0 and prices.get(symbol, 0.0) <= 0:
                prices[symbol] = value
                changed = True
        if not changed:
            break
    return prices


def _apply_prices(rows: list[dict], prices: dict[str, float]) -> None:
    for row in rows:
        amount0 = float(row.get("token0Amount") or 0)
        amount1 = float(row.get("token1Amount") or 0)
        side0_usd = amount0 * _safe_float(prices.get(str(row.get("token0Symbol") or ""), 0.0))
        side1_usd = amount1 * _safe_float(prices.get(str(row.get("token1Symbol") or ""), 0.0))
        tvl = side0_usd + side1_usd
        row["token0Usd"] = side0_usd
        row["token1Usd"] = side1_usd
        row["tvlUsd"] = tvl
        row["status"] = _status(tvl, side0_usd, side1_usd)


def _scan_v2(w3: Web3, dex: bnb.DexConfig, prices: dict[str, float]) -> list[dict]:
    factory = w3.eth.contract(address=Web3.to_checksum_address(dex.factory), abi=V2_FACTORY_ABI)
    by_address = _token_by_address()
    rows = []
    for left, right in bnb.token_pairs():
        left_token = bnb.TOKENS[left]
        right_token = bnb.TOKENS[right]
        pair_address = factory.functions.getPair(
            Web3.to_checksum_address(left_token.address),
            Web3.to_checksum_address(right_token.address),
        ).call()
        if int(pair_address, 16) == 0:
            continue
        pair = w3.eth.contract(address=Web3.to_checksum_address(pair_address), abi=V2_PAIR_ABI)
        reserve0_raw, reserve1_raw, _ = pair.functions.getReserves().call()
        token0_addr = Web3.to_checksum_address(pair.functions.token0().call()).lower()
        token1_addr = Web3.to_checksum_address(pair.functions.token1().call()).lower()
        token0 = by_address[token0_addr]
        token1 = by_address[token1_addr]
        reserve0 = _amount(int(reserve0_raw), token0.decimals)
        reserve1 = _amount(int(reserve1_raw), token1.decimals)
        rows.append(
            {
                "network": bnb.NETWORK,
                "chainId": bnb.CHAIN_ID,
                "dex": dex.name,
                "protocol": dex.protocol,
                "factory": dex.factory,
                "router": dex.router,
                "pool": Web3.to_checksum_address(pair_address),
                "fee": f"{dex.fee_bps} bps",
                "symbolPair": bnb.pair_key(token0.symbol, token1.symbol),
                "token0Symbol": token0.symbol,
                "token1Symbol": token1.symbol,
                "token0": token0.address,
                "token1": token1.address,
                "token0Amount": reserve0,
                "token1Amount": reserve1,
                "token0Usd": 0.0,
                "token1Usd": 0.0,
                "tvlUsd": 0.0,
                "status": "rejected",
            }
        )
    return rows


def _erc20_balance(w3: Web3, token: bnb.TokenConfig, owner: str) -> float:
    contract = w3.eth.contract(address=Web3.to_checksum_address(token.address), abi=ERC20_ABI)
    return _amount(int(contract.functions.balanceOf(Web3.to_checksum_address(owner)).call()), token.decimals)


def _scan_v3(w3: Web3, dex: bnb.DexConfig, prices: dict[str, float]) -> list[dict]:
    factory = w3.eth.contract(address=Web3.to_checksum_address(dex.factory), abi=V3_FACTORY_ABI)
    by_address = _token_by_address()
    rows = []
    for left, right in bnb.V3_CORE_PAIRS:
        left_token = bnb.TOKENS[left]
        right_token = bnb.TOKENS[right]
        for fee in dex.fee_tiers:
            pool_address = factory.functions.getPool(
                Web3.to_checksum_address(left_token.address),
                Web3.to_checksum_address(right_token.address),
                int(fee),
            ).call()
            if int(pool_address, 16) == 0:
                continue
            pool = w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=V3_POOL_ABI)
            token0_addr = Web3.to_checksum_address(pool.functions.token0().call()).lower()
            token1_addr = Web3.to_checksum_address(pool.functions.token1().call()).lower()
            token0 = by_address[token0_addr]
            token1 = by_address[token1_addr]
            amount0 = _erc20_balance(w3, token0, pool_address)
            amount1 = _erc20_balance(w3, token1, pool_address)
            side0_usd = amount0 * _safe_float(prices.get(token0.symbol, 0.0))
            side1_usd = amount1 * _safe_float(prices.get(token1.symbol, 0.0))
            tvl = side0_usd + side1_usd
            liquidity = int(pool.functions.liquidity().call())
            slot0 = pool.functions.slot0().call()
            rows.append(
                {
                    "network": bnb.NETWORK,
                    "chainId": bnb.CHAIN_ID,
                    "dex": dex.name,
                    "protocol": dex.protocol,
                    "factory": dex.factory,
                    "router": dex.router,
                    "quoter": dex.quoter,
                    "pool": Web3.to_checksum_address(pool_address),
                    "fee": int(fee),
                    "symbolPair": bnb.pair_key(token0.symbol, token1.symbol),
                    "token0Symbol": token0.symbol,
                    "token1Symbol": token1.symbol,
                    "token0": token0.address,
                    "token1": token1.address,
                    "token0Amount": amount0,
                    "token1Amount": amount1,
                    "token0Usd": side0_usd,
                    "token1Usd": side1_usd,
                    "tvlUsd": tvl,
                    "liquidity": liquidity,
                    "tick": int(slot0[1]),
                    "status": _status(tvl, side0_usd, side1_usd),
                }
            )
    return rows


def _fmt(value: float, places: int = 2) -> str:
    try:
        parsed = float(value)
    except Exception:
        return "-"
    if not math.isfinite(parsed):
        return "-"
    return f"{parsed:,.{places}f}".rstrip("0").rstrip(".")


def _short(address: str) -> str:
    return f"{address[:10]}...{address[-6:]}" if address.startswith("0x") and len(address) == 42 else address


def _edge_text(edge: dict) -> str:
    return (
        f"{edge.get('symbolPair')}<br>"
        f"{edge.get('dex')} / {edge.get('status')} / fee {edge.get('fee')}<br>"
        f"TVL {_fmt(float(edge.get('tvlUsd') or 0))} / {_short(str(edge.get('pool')))}"
    )


def _status_label(value: object) -> str:
    return {
        "recommended": "高质量",
        "improvable": "可改善",
        "rejected": "拒绝",
    }.get(str(value or ""), str(value or "-"))


def _build_report(payload: dict) -> str:
    pools = list(payload.get("pools") or [])
    recommended = bnb.triangle_routes(bnb.best_edges_by_pair(pools, {"recommended"}))
    observable = bnb.triangle_routes(bnb.best_edges_by_pair(pools, {"recommended", "improvable"}))
    lines = [
        "# BNB Chain 套利池扫描",
        "",
        f"- 生成时间：`{payload.get('generatedAt')}`",
        f"- RPC：`{payload.get('rpcUrl')}`",
        f"- 已扫描 DEX：`{', '.join(payload.get('scannedDexes') or [])}`",
        f"- 核心代币数：`{len(payload.get('tokens') or [])}`；池子数：`{len(pools)}`",
        f"- 高质量：`{payload['summary']['recommendedPoolCount']}`；可改善：`{payload['summary']['improvablePoolCount']}`；拒绝：`{payload['summary']['rejectedPoolCount']}`",
        f"- 全高质量三角闭环：`{len(recommended)}`；含可改善边的观察闭环：`{len(observable)}`",
        "- 说明：闭环仅表示池子图连通且通过静态 TVL 质量检查，不表示当前可套利；必须再按实际输入金额做逐跳链上报价、闪电贷成本和 gas 复核。",
        "",
        "## 全高质量三角闭环",
        "",
    ]
    if recommended:
        lines.extend([
            "<table>",
            "<thead><tr><th>路径</th><th>最小 TVL USD</th><th>边 1</th><th>边 2</th><th>边 3</th></tr></thead>",
            "<tbody>",
        ])
        for item in recommended:
            edge_cells = [_edge_text(edge) for edge in item["edges"]]
            lines.append(
                "<tr>"
                f"<td>{item['path']}</td><td>{_fmt(item['minTvlUsd'])}</td>"
                f"<td>{edge_cells[0]}</td><td>{edge_cells[1]}</td><td>{edge_cells[2]}</td>"
                "</tr>"
            )
        lines.extend(["</tbody>", "</table>", ""])
    else:
        lines.extend(["暂无。", ""])

    lines.extend(["## 高质量 + 可改善观察闭环（前 30 条）", ""])
    if observable:
        lines.extend([
            "<table>",
            "<thead><tr><th>路径</th><th>等级</th><th>最小 TVL USD</th><th>边 1</th><th>边 2</th><th>边 3</th></tr></thead>",
            "<tbody>",
        ])
        for item in observable[:30]:
            edge_cells = [_edge_text(edge) for edge in item["edges"]]
            lines.append(
                "<tr>"
                f"<td>{item['path']}</td><td>{'高质量' if item['status'] == 'recommended' else '观察'}</td>"
                f"<td>{_fmt(item['minTvlUsd'])}</td>"
                f"<td>{edge_cells[0]}</td><td>{edge_cells[1]}</td><td>{edge_cells[2]}</td>"
                "</tr>"
            )
        lines.extend(["</tbody>", "</table>", ""])
    else:
        lines.extend(["暂无。", ""])

    lines.extend([
        "## 池子明细（按质量、TVL 排序，前 40 条）",
        "",
        "<table>",
        "<thead><tr><th>交易对</th><th>DEX</th><th>协议</th><th>池子</th><th>fee</th><th>质量</th><th>TVL USD</th><th>token0/token1</th><th>token0 USD</th><th>token1 USD</th></tr></thead>",
        "<tbody>",
    ])
    rank = {"recommended": 0, "improvable": 1, "rejected": 2}
    for row in sorted(pools, key=lambda item: (rank.get(str(item.get("status")), 9), -float(item.get("tvlUsd") or 0)))[:40]:
        lines.append(
            "<tr>"
            f"<td>{row.get('symbolPair')}</td><td>{row.get('dex')}</td><td>{row.get('protocol')}</td>"
            f"<td>{_short(str(row.get('pool')))}</td><td>{row.get('fee')}</td>"
            f"<td>{_status_label(row.get('status'))}</td><td>{_fmt(float(row.get('tvlUsd') or 0))}</td>"
            f"<td>{row.get('token0Symbol')} / {row.get('token1Symbol')}</td>"
            f"<td>{_fmt(float(row.get('token0Usd') or 0))}</td><td>{_fmt(float(row.get('token1Usd') or 0))}</td>"
            "</tr>"
        )
    lines.extend(["</tbody>", "</table>", ""])
    return "\n".join(lines)


def build_report(payload: dict) -> str:
    return _build_report(payload)

    pools = payload.get("pools") or []
    recommended = bnb.triangle_routes(bnb.best_edges_by_pair(pools, {"recommended"}))
    observable = bnb.triangle_routes(bnb.best_edges_by_pair(pools, {"recommended", "improvable"}))
    lines = [
        "# BNB 链套利扫描",
        "",
        f"- 生成时间：`{payload.get('generatedAt')}`",
        f"- RPC：`{payload.get('rpcUrl')}`",
        f"- 扫描 DEX：`{', '.join(payload.get('scannedDexes') or [])}`",
        f"- token 数：`{len(payload.get('tokens') or [])}`；池子数：`{len(pools)}`",
        f"- 推荐池：`{payload['summary']['recommendedPoolCount']}`；可改善：`{payload['summary']['improvablePoolCount']}`；拒绝：`{payload['summary']['rejectedPoolCount']}`",
        f"- 全推荐三角闭环：`{len(recommended)}`；推荐+可改善观察闭环：`{len(observable)}`",
        "",
        "## 全推荐三角闭环",
        "",
    ]
    if recommended:
        lines.extend(["<table>", "<thead><tr><th>路径</th><th>最低 TVL USD</th><th>边 1</th><th>边 2</th><th>边 3</th></tr></thead>", "<tbody>"])
        for item in recommended:
            edge_cells = [_edge_text(edge) for edge in item["edges"]]
            lines.append(
                "<tr>"
                f"<td>{item['path']}</td>"
                f"<td>{_fmt(item['minTvlUsd'])}</td>"
                f"<td>{edge_cells[0]}</td><td>{edge_cells[1]}</td><td>{edge_cells[2]}</td>"
                "</tr>"
            )
        lines.extend(["</tbody>", "</table>", ""])
    else:
        lines.extend(["无。", ""])
    lines.extend(["## 推荐 + 可改善观察闭环（前 30 条）", ""])
    if observable:
        lines.extend(["<table>", "<thead><tr><th>路径</th><th>等级</th><th>最低 TVL USD</th><th>边 1</th><th>边 2</th><th>边 3</th></tr></thead>", "<tbody>"])
        for item in observable[:30]:
            edge_cells = [_edge_text(edge) for edge in item["edges"]]
            lines.append(
                "<tr>"
                f"<td>{item['path']}</td>"
                f"<td>{item['status']}</td>"
                f"<td>{_fmt(item['minTvlUsd'])}</td>"
                f"<td>{edge_cells[0]}</td><td>{edge_cells[1]}</td><td>{edge_cells[2]}</td>"
                "</tr>"
            )
        lines.extend(["</tbody>", "</table>", ""])
    else:
        lines.extend(["无。", ""])
    lines.extend(
        [
            "## 高质量池明细（前 40 条）",
            "",
            "<table>",
            "<thead><tr><th>交易对</th><th>DEX</th><th>池子</th><th>fee</th><th>质量</th><th>TVL USD</th><th>token0/token1</th><th>token0 USD</th><th>token1 USD</th></tr></thead>",
            "<tbody>",
        ]
    )
    for row in sorted(pools, key=lambda item: ({"recommended": 0, "improvable": 1, "rejected": 2}.get(str(item.get("status")), 9), -float(item.get("tvlUsd") or 0)))[:40]:
        lines.append(
            "<tr>"
            f"<td>{row.get('symbolPair')}</td>"
            f"<td>{row.get('dex')}</td>"
            f"<td>{_short(str(row.get('pool')))}</td>"
            f"<td>{row.get('fee')}</td>"
            f"<td>{row.get('status')}</td>"
            f"<td>{_fmt(float(row.get('tvlUsd') or 0))}</td>"
            f"<td>{row.get('token0Symbol')} / {row.get('token1Symbol')}</td>"
            f"<td>{_fmt(float(row.get('token0Usd') or 0))}</td>"
            f"<td>{_fmt(float(row.get('token1Usd') or 0))}</td>"
            "</tr>"
        )
    lines.extend(["</tbody>", "</table>", ""])
    return "\n".join(lines)


def scan(rpc_urls: list[str], *, include_v3: bool) -> dict:
    w3, rpc_url = _connect(rpc_urls)
    prices = _fetch_prices()
    pools: list[dict] = []
    scanned_dexes = []
    for dex in bnb.DEXES:
        if dex.protocol == "v3" and not include_v3:
            continue
        if dex.protocol == "v2":
            pools.extend(_scan_v2(w3, dex, prices))
            prices = _infer_prices_from_v2_rows(pools, prices)
            _apply_prices(pools, prices)
            scanned_dexes.append(dex.name)
        elif dex.protocol == "v3":
            pools.extend(_scan_v3(w3, dex, prices))
            scanned_dexes.append(dex.name)
    counts = {"recommended": 0, "improvable": 0, "rejected": 0}
    for pool in pools:
        counts[str(pool.get("status"))] = counts.get(str(pool.get("status")), 0) + 1
    return {
        "network": bnb.NETWORK,
        "chainId": bnb.CHAIN_ID,
        "generatedAt": _now(),
        "rpcUrl": rpc_url,
        "scannedDexes": scanned_dexes,
        "tokens": [token.__dict__ for token in bnb.TOKENS.values()],
        "dexes": [dex.__dict__ for dex in bnb.DEXES],
        "pricesUsd": prices,
        "pools": pools,
        "summary": {
            "poolCount": len(pools),
            "recommendedPoolCount": counts.get("recommended", 0),
            "improvablePoolCount": counts.get("improvable", 0),
            "rejectedPoolCount": counts.get("rejected", 0),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan BNB Chain arbitrage pools.")
    parser.add_argument("--rpc-url", action="append", default=[])
    parser.add_argument("--include-v3", action="store_true", help="Include slower PancakeSwap V3 balance/TVL scan.")
    parser.add_argument("--output-json", type=Path, default=OUTPUT_JSON)
    parser.add_argument("--output-md", type=Path, default=OUTPUT_MD)
    args = parser.parse_args()
    rpc_urls = args.rpc_url or list(bnb.DEFAULT_RPC_URLS)
    payload = scan(rpc_urls, include_v3=bool(args.include_v3))
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output_md.write_text(build_report(payload), encoding="utf-8")
    print(json.dumps({"ok": True, "json": str(args.output_json), "markdown": str(args.output_md), "summary": payload["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
