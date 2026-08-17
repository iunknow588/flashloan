"""Observe two fixed Avalanche arbitrage loops and write local evidence files."""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from web3 import Web3


REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_INFO_DIR = REPO_ROOT / "data_info" / "AVALANCHE"

RPC_URL = "https://api.avax.network/ext/bc/C/rpc"
QUOTER = "0xbe0F5544EC67e9B3b2D979aaA43f18Fd87E6257F"
TRADER_JOE_ROUTER = "0x60aE616a2155Ee3d9A68541Ba4544862310933d4"
PANGOLIN_ROUTER = "0xE54Ca86531e17Ef3616d22Ca28b0D458b6C89106"

FLASHLOAN_PREMIUM_BPS = 5

TOKENS = {
    "USDC": {"address": "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E", "decimals": 6},
    "USDt": {"address": "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7", "decimals": 6},
    "WAVAX": {"address": "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7", "decimals": 18},
    "BTC.b": {"address": "0x152b9d0FdC40C096757F570A51E494bd4b943E50", "decimals": 8},
    "WETH.e": {"address": "0x49D5c2BdFfac6CE2BFdB6640F4F80f226bc10bAB", "decimals": 18},
}

ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
        ],
        "name": "getAmountsOut",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function",
    }
]

QUOTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "internalType": "struct IQuoterV2.QuoteExactInputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
            {"internalType": "uint160", "name": "sqrtPriceX96After", "type": "uint160"},
            {"internalType": "uint32", "name": "initializedTicksCrossed", "type": "uint32"},
            {"internalType": "uint256", "name": "gasEstimate", "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


@dataclass(frozen=True)
class Hop:
    protocol: str
    dex: str
    token_in: str
    token_out: str
    fee: int | str
    router: str | None = None


@dataclass(frozen=True)
class Route:
    key: str
    target_pair: str
    start_symbol: str
    amount_human: float
    hops: tuple[Hop, ...]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _token_address(symbol: str) -> str:
    return Web3.to_checksum_address(TOKENS[symbol]["address"])


def _decimals(symbol: str) -> int:
    return int(TOKENS[symbol]["decimals"])


def _to_units(amount: float, symbol: str) -> int:
    return int(round(float(amount) * (10 ** _decimals(symbol))))


def _from_units(amount: int, symbol: str) -> float:
    return int(amount) / float(10 ** _decimals(symbol))


def _fmt(value: float, places: int = 8) -> str:
    if not math.isfinite(value):
        return "-"
    text = f"{value:,.{places}f}".rstrip("0").rstrip(".")
    return text or "0"


def _short(value: str) -> str:
    return f"{value[:10]}...{value[-6:]}" if value.startswith("0x") and len(value) == 42 else value


def _quote_v3(w3: Web3, quoter, hop: Hop, amount_in: int) -> tuple[int, dict[str, Any]]:
    params = {
        "tokenIn": _token_address(hop.token_in),
        "tokenOut": _token_address(hop.token_out),
        "amountIn": int(amount_in),
        "fee": int(hop.fee),
        "sqrtPriceLimitX96": 0,
    }
    amount_out, sqrt_after, ticks_crossed, gas_estimate = quoter.functions.quoteExactInputSingle(params).call()
    return int(amount_out), {
        "protocol": "v3",
        "dex": hop.dex,
        "tokenIn": hop.token_in,
        "tokenOut": hop.token_out,
        "fee": hop.fee,
        "amountInRaw": str(amount_in),
        "amountOutRaw": str(amount_out),
        "amountIn": _from_units(amount_in, hop.token_in),
        "amountOut": _from_units(int(amount_out), hop.token_out),
        "sqrtPriceX96After": str(sqrt_after),
        "initializedTicksCrossed": int(ticks_crossed),
        "quoterGasEstimate": int(gas_estimate),
    }


def _quote_v2(w3: Web3, routers: dict[str, Any], hop: Hop, amount_in: int) -> tuple[int, dict[str, Any]]:
    if hop.router is None:
        raise ValueError(f"missing router for {hop}")
    router = routers[hop.router]
    path = [_token_address(hop.token_in), _token_address(hop.token_out)]
    amounts = router.functions.getAmountsOut(int(amount_in), path).call()
    amount_out = int(amounts[-1])
    return amount_out, {
        "protocol": "v2",
        "dex": hop.dex,
        "router": hop.router,
        "tokenIn": hop.token_in,
        "tokenOut": hop.token_out,
        "fee": hop.fee,
        "amountInRaw": str(amount_in),
        "amountOutRaw": str(amount_out),
        "amountIn": _from_units(amount_in, hop.token_in),
        "amountOut": _from_units(amount_out, hop.token_out),
    }


def _quote_route(w3: Web3, quoter, routers: dict[str, Any], route: Route) -> dict[str, Any]:
    amount = _to_units(route.amount_human, route.start_symbol)
    start_raw = amount
    hops: list[dict[str, Any]] = []
    for hop in route.hops:
        if hop.protocol == "v3":
            amount, hop_record = _quote_v3(w3, quoter, hop, amount)
        elif hop.protocol == "v2":
            amount, hop_record = _quote_v2(w3, routers, hop, amount)
        else:
            raise ValueError(f"unsupported protocol {hop.protocol}")
        hops.append(hop_record)
    final_human = _from_units(amount, route.start_symbol)
    required_raw = start_raw + ((start_raw * FLASHLOAN_PREMIUM_BPS + 9999) // 10000)
    required_human = _from_units(required_raw, route.start_symbol)
    net_raw = amount - required_raw
    net_human = _from_units(net_raw, route.start_symbol)
    gross_human = final_human - route.amount_human
    return {
        "routeKey": route.key,
        "targetPair": route.target_pair,
        "path": " -> ".join([route.hops[0].token_in, *[hop.token_out for hop in route.hops]]),
        "startSymbol": route.start_symbol,
        "startAmount": route.amount_human,
        "finalAmount": final_human,
        "requiredFinalAmount": required_human,
        "grossProfit": gross_human,
        "netBeforeGas": net_human,
        "profitableBeforeGas": net_raw > 0,
        "flashloanPremiumBps": FLASHLOAN_PREMIUM_BPS,
        "hops": hops,
    }


def _routes(amount_usdc: float, amount_usdt: float) -> list[Route]:
    return [
        Route(
            key="usdc_btc_wavax_usdc",
            target_pair="BTC.b/WAVAX",
            start_symbol="USDC",
            amount_human=amount_usdc,
            hops=(
                Hop("v3", "current_v3", "USDC", "BTC.b", 500),
                Hop("v2", "trader_joe_v1", "BTC.b", "WAVAX", "30 bps", TRADER_JOE_ROUTER),
                Hop("v3", "current_v3", "WAVAX", "USDC", 500),
            ),
        ),
        Route(
            key="usdc_wavax_btc_usdc",
            target_pair="BTC.b/WAVAX",
            start_symbol="USDC",
            amount_human=amount_usdc,
            hops=(
                Hop("v3", "current_v3", "USDC", "WAVAX", 500),
                Hop("v2", "trader_joe_v1", "WAVAX", "BTC.b", "30 bps", TRADER_JOE_ROUTER),
                Hop("v3", "current_v3", "BTC.b", "USDC", 500),
            ),
        ),
        Route(
            key="usdt_wavax_weth_usdt",
            target_pair="WAVAX/WETH.e",
            start_symbol="USDt",
            amount_human=amount_usdt,
            hops=(
                Hop("v2", "pangolin_v2", "USDt", "WAVAX", "30 bps", PANGOLIN_ROUTER),
                Hop("v2", "trader_joe_v1", "WAVAX", "WETH.e", "30 bps", TRADER_JOE_ROUTER),
                Hop("v3", "current_v3", "WETH.e", "USDt", 10000),
            ),
        ),
        Route(
            key="usdt_weth_wavax_usdt",
            target_pair="WAVAX/WETH.e",
            start_symbol="USDt",
            amount_human=amount_usdt,
            hops=(
                Hop("v3", "current_v3", "USDt", "WETH.e", 10000),
                Hop("v2", "trader_joe_v1", "WETH.e", "WAVAX", "30 bps", TRADER_JOE_ROUTER),
                Hop("v2", "pangolin_v2", "WAVAX", "USDt", "30 bps", PANGOLIN_ROUTER),
            ),
        ),
    ]


def _build_markdown(summary: dict[str, Any], jsonl_path: Path) -> str:
    lines = [
        "# 两条目标闭环 10 分钟探测结果",
        "",
        f"- 运行开始：`{summary['startedAt']}`",
        f"- 运行结束：`{summary['endedAt']}`",
        f"- 目标时长秒：`{summary['durationSeconds']}`；目标间隔秒：`{summary['intervalSeconds']}`",
        f"- 采样次数：`{summary['sampleCount']}`；错误次数：`{summary['errorCount']}`",
        f"- 原始 JSONL：`{jsonl_path.name}`",
        f"- 闪电贷费用估算：`{FLASHLOAN_PREMIUM_BPS}` bps；gas 未扣除。",
        "",
        "## 路径汇总",
        "",
        "<table>",
        "<thead><tr><th>路径</th><th>目标交易对</th><th>输入</th><th>最大最终输出</th><th>最大净利润 before gas</th><th>盈利样本数</th><th>最好样本时间</th></tr></thead>",
        "<tbody>",
    ]
    for item in summary["routes"]:
        lines.append(
            "<tr>"
            f"<td>{item['path']}</td>"
            f"<td>{item['targetPair']}</td>"
            f"<td>{_fmt(item['startAmount'])} {item['startSymbol']}</td>"
            f"<td>{_fmt(item['maxFinalAmount'])} {item['startSymbol']}</td>"
            f"<td>{_fmt(item['maxNetBeforeGas'])} {item['startSymbol']}</td>"
            f"<td>{item['profitableBeforeGasCount']}</td>"
            f"<td>{item.get('bestObservedAt') or '-'}</td>"
            "</tr>"
        )
    lines.extend(
        [
            "</tbody>",
            "</table>",
            "",
            "## 结论",
            "",
        ]
    )
    winners = [item for item in summary["routes"] if item["profitableBeforeGasCount"] > 0]
    if winners:
        lines.append("- 本次探测出现过 before gas 为正的样本，需要回放对应 JSONL 样本并加入 gas/slippage/MEV 安全边际后再判断。")
    else:
        lines.append("- 本次探测未出现扣除 Aave premium 后仍为正的样本；没有发现可直接进入链上测试的套利机会。")
    lines.append("- 该测试没有广播交易，只做本地报价与证据记录。")
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    DATA_INFO_DIR.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    jsonl_path = DATA_INFO_DIR / f"two_loop_observation_{stamp}.jsonl"
    md_path = DATA_INFO_DIR / "两条目标闭环探测结果.md"

    w3 = Web3(Web3.HTTPProvider(args.rpc_url, request_kwargs={"timeout": args.rpc_timeout}))
    if not w3.is_connected():
        raise RuntimeError(f"RPC not connected: {args.rpc_url}")
    quoter = w3.eth.contract(address=Web3.to_checksum_address(args.quoter), abi=QUOTER_ABI)
    routers = {
        TRADER_JOE_ROUTER: w3.eth.contract(address=Web3.to_checksum_address(TRADER_JOE_ROUTER), abi=ROUTER_ABI),
        PANGOLIN_ROUTER: w3.eth.contract(address=Web3.to_checksum_address(PANGOLIN_ROUTER), abi=ROUTER_ABI),
    }

    routes = _routes(args.amount_usdc, args.amount_usdt)
    route_stats: dict[str, dict[str, Any]] = {
        route.key: {
            "routeKey": route.key,
            "path": " -> ".join([route.hops[0].token_in, *[hop.token_out for hop in route.hops]]),
            "targetPair": route.target_pair,
            "startSymbol": route.start_symbol,
            "startAmount": route.amount_human,
            "maxFinalAmount": float("-inf"),
            "maxNetBeforeGas": float("-inf"),
            "profitableBeforeGasCount": 0,
            "bestObservedAt": None,
        }
        for route in routes
    }

    sample_count = 0
    error_count = 0
    started_at = _utc_now()
    end_at = time.monotonic() + args.duration_seconds
    next_sample = time.monotonic()

    with jsonl_path.open("w", encoding="utf-8") as handle:
        while time.monotonic() < end_at:
            now_monotonic = time.monotonic()
            if now_monotonic < next_sample:
                time.sleep(next_sample - now_monotonic)
            observed_at = _utc_now()
            sample_started = time.perf_counter()
            block_number = None
            try:
                block_number = int(w3.eth.block_number)
            except Exception:
                error_count += 1
            route_results = []
            for route in routes:
                try:
                    result = _quote_route(w3, quoter, routers, route)
                    stat = route_stats[route.key]
                    if result["finalAmount"] > stat["maxFinalAmount"]:
                        stat["maxFinalAmount"] = result["finalAmount"]
                    if result["netBeforeGas"] > stat["maxNetBeforeGas"]:
                        stat["maxNetBeforeGas"] = result["netBeforeGas"]
                        stat["bestObservedAt"] = observed_at
                    if result["profitableBeforeGas"]:
                        stat["profitableBeforeGasCount"] += 1
                    route_results.append(result)
                except Exception as exc:
                    error_count += 1
                    route_results.append(
                        {
                            "routeKey": route.key,
                            "targetPair": route.target_pair,
                            "path": " -> ".join([route.hops[0].token_in, *[hop.token_out for hop in route.hops]]),
                            "error": repr(exc),
                        }
                    )
            elapsed_ms = (time.perf_counter() - sample_started) * 1000
            sample = {
                "observedAt": observed_at,
                "blockNumber": block_number,
                "sampleIndex": sample_count + 1,
                "elapsedMs": elapsed_ms,
                "routes": route_results,
            }
            handle.write(json.dumps(sample, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            sample_count += 1
            next_sample += args.interval_seconds

    ended_at = _utc_now()
    routes_summary = []
    for stat in route_stats.values():
        if stat["maxFinalAmount"] == float("-inf"):
            stat["maxFinalAmount"] = 0.0
        if stat["maxNetBeforeGas"] == float("-inf"):
            stat["maxNetBeforeGas"] = 0.0
        routes_summary.append(stat)
    summary = {
        "startedAt": started_at,
        "endedAt": ended_at,
        "durationSeconds": args.duration_seconds,
        "intervalSeconds": args.interval_seconds,
        "sampleCount": sample_count,
        "errorCount": error_count,
        "jsonlPath": str(jsonl_path),
        "routes": routes_summary,
    }
    md_path.write_text(_build_markdown(summary, jsonl_path), encoding="utf-8")
    summary_path = DATA_INFO_DIR / f"two_loop_observation_summary_{stamp}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "summary": summary, "markdown": str(md_path), "jsonl": str(jsonl_path)}, ensure_ascii=False, indent=2))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Observe two fixed Avalanche arbitrage loops.")
    parser.add_argument("--duration-seconds", type=float, default=600.0)
    parser.add_argument("--interval-seconds", type=float, default=0.2)
    parser.add_argument("--amount-usdc", type=float, default=1000.0)
    parser.add_argument("--amount-usdt", type=float, default=100.0)
    parser.add_argument("--rpc-url", default=RPC_URL)
    parser.add_argument("--rpc-timeout", type=float, default=10.0)
    parser.add_argument("--quoter", default=QUOTER)
    args = parser.parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
