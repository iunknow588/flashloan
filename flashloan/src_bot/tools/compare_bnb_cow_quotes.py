"""Compare BNB Pancake route quotes with diagnostic CoW per-hop quotes."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from web3 import Web3


SRC_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SRC_ROOT.parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from cow_flashloan.routes import CowToken, evaluate_cow_route
from intent_trade.chains.bnb import config as bnb
from intent_trade.chains.bnb.quote import _connect, quote_route


DEFAULT_OUTPUT_DIR = REPO_ROOT / "data_info" / "BNB"
DEFAULT_ROUTES = (
    ("USDT", "WBNB", "ETH", "USDT"),
    ("USDT", "ETH", "WBNB", "USDT"),
    ("USDT", "BTCB", "WBNB", "USDT"),
    ("USDT", "WBNB", "BTCB", "USDT"),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _fmt(value: Any, places: int = 10) -> str:
    number = _decimal(value)
    if number is None:
        return "-"
    text = f"{number:,.{places}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _bnb_cow_registry() -> dict[str, CowToken]:
    registry: dict[str, CowToken] = {}
    for symbol, token in bnb.TOKENS.items():
        cow = CowToken(symbol=symbol, address=token.address.lower(), decimals=token.decimals, source="bnb_config")
        registry[symbol.upper()] = cow
        registry[cow.address] = cow
    return registry


def _parse_route(raw: str) -> tuple[str, str, str, str]:
    parts = tuple(piece.strip().upper() for piece in raw.split("->") if piece.strip())
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("route must contain exactly four symbols, e.g. USDT->BTCB->WBNB->USDT")
    return parts  # type: ignore[return-value]


def _compare_route(
    *,
    w3: Web3,
    route: tuple[str, str, str, str],
    amount: str,
    registry: dict[str, CowToken],
    timeout_seconds: float,
) -> dict[str, Any]:
    path = " -> ".join(route)
    pancake: dict[str, Any]
    cow: dict[str, Any]
    try:
        pancake = {"ok": True, **quote_route(w3, route, amount=amount)}
    except Exception as exc:
        pancake = {"ok": False, "path": path, "error": str(exc)}

    cow = evaluate_cow_route(
        {"name": path, "path": list(route)},
        registry=registry,
        default_amount=amount,
        cow_network="bnb",
        price_quality="fast",
        valid_for=60,
        quote_timeout_seconds=timeout_seconds,
    )
    cow["ok"] = bool(cow.get("viable")) and not cow.get("error")

    initial = _decimal(amount) or Decimal("0")
    pancake_final = _decimal(pancake.get("finalAmount"))
    cow_final = _decimal(cow.get("final_amount"))
    pancake_profit = pancake_final - initial if pancake_final is not None else None
    cow_profit = cow_final - initial if cow_final is not None else None
    delta = cow_final - pancake_final if cow_final is not None and pancake_final is not None else None

    if pancake_profit is not None and pancake_profit > 0:
        conclusion = "pancake_positive_before_costs"
    elif cow_profit is not None and cow_profit > 0:
        conclusion = "cow_positive_before_costs_diagnostic_only"
    elif pancake.get("ok") and cow.get("ok"):
        conclusion = "both_negative_before_costs"
    else:
        conclusion = "quote_incomplete"

    return {
        "path": path,
        "routeSymbols": list(route),
        "amount": amount,
        "pancake": pancake,
        "cow": cow,
        "comparison": {
            "pancakeFinal": str(pancake_final) if pancake_final is not None else None,
            "cowFinal": str(cow_final) if cow_final is not None else None,
            "pancakeGrossProfit": str(pancake_profit) if pancake_profit is not None else None,
            "cowGrossProfit": str(cow_profit) if cow_profit is not None else None,
            "cowMinusPancake": str(delta) if delta is not None else None,
            "conclusion": conclusion,
        },
    }


def _build_report(payload: dict[str, Any]) -> str:
    lines = [
        "# BNB CoW 与 Pancake 逐跳报价对比",
        "",
        "## 说明",
        "",
        "- Pancake 报价来自当前静态池路径的 V2 Router / V3 Quoter 只读调用。",
        "- CoW 报价来自 CoW quote API 的逐跳 sell quote，用于价格一致性诊断。",
        "- CoW 逐跳报价不是同一笔原子闭环交易，也不等于 CoW solver 会执行三跳套利；只有当后续能构造同一原子执行路径时，才可进入链上模拟。",
        "- 当前结果均未扣除闪电贷费用、实际 gas、滑点保护和竞争成本。",
        "",
        "## 汇总",
        "",
        f"- 生成时间 UTC：`{payload.get('generatedAt')}`",
        f"- 网络：`BNB` / chainId `56`",
        f"- 输入金额：`{payload.get('amount')}`",
        "",
        "| 路径 | Pancake 最终回款 | CoW 逐跳最终回款 | CoW-Pancake | Pancake 成本前收益 | CoW 成本前收益 | 结论 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    labels = {
        "pancake_positive_before_costs": "Pancake 成本前为正，需继续模拟",
        "cow_positive_before_costs_diagnostic_only": "CoW 逐跳成本前为正，仅诊断",
        "both_negative_before_costs": "两者成本前均亏损",
        "quote_incomplete": "报价不完整",
    }
    for item in payload.get("routes") or []:
        comparison = item.get("comparison") if isinstance(item.get("comparison"), dict) else {}
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{item.get('path')}`",
                    _fmt(comparison.get("pancakeFinal")),
                    _fmt(comparison.get("cowFinal")),
                    _fmt(comparison.get("cowMinusPancake")),
                    _fmt(comparison.get("pancakeGrossProfit")),
                    _fmt(comparison.get("cowGrossProfit")),
                    labels.get(str(comparison.get("conclusion") or ""), str(comparison.get("conclusion") or "-")),
                ]
            )
            + " |"
        )

    lines.extend(["", "## 逐跳明细", ""])
    for item in payload.get("routes") or []:
        lines.extend([f"### {item.get('path')}", ""])
        pancake = item.get("pancake") if isinstance(item.get("pancake"), dict) else {}
        cow = item.get("cow") if isinstance(item.get("cow"), dict) else {}
        lines.extend(
            [
                "| 跳 | Pancake 输入 | Pancake 输出 | Pancake 池/方法 | CoW 输入 | CoW 输出 | CoW fee |",
                "| ---: | ---: | ---: | --- | ---: | ---: | ---: |",
            ]
        )
        pancake_hops = pancake.get("hops") if isinstance(pancake.get("hops"), list) else []
        cow_hops = cow.get("hops") if isinstance(cow.get("hops"), list) else []
        for index in range(max(len(pancake_hops), len(cow_hops), 3)):
            p_hop = pancake_hops[index] if index < len(pancake_hops) else {}
            c_hop = cow_hops[index] if index < len(cow_hops) else {}
            method = str(p_hop.get("quoteMethod") or p_hop.get("protocol") or "")
            pool = str(p_hop.get("pool") or "")
            if pool.startswith("0x") and len(pool) == 42:
                pool = f"{pool[:10]}...{pool[-6:]}"
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(index + 1),
                        _fmt(p_hop.get("amountIn")),
                        _fmt(p_hop.get("amountOut")),
                        f"{pool}<br>{method}" if pool or method else "",
                        _fmt(c_hop.get("sell_amount")),
                        _fmt(c_hop.get("buy_amount")),
                        _fmt(c_hop.get("fee_amount")),
                    ]
                )
                + " |"
            )
        if not pancake.get("ok"):
            lines.append(f"- Pancake 报价错误：`{pancake.get('error')}`")
        if not cow.get("ok"):
            lines.append(f"- CoW 报价错误：`{cow.get('error')}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare BNB Pancake route quotes with diagnostic CoW per-hop quotes.")
    parser.add_argument("--amount", default="1000")
    parser.add_argument("--route", action="append", type=_parse_route, default=[])
    parser.add_argument("--rpc-url", action="append", default=[])
    parser.add_argument("--quote-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    w3, rpc_url = _connect(args.rpc_url or list(bnb.DEFAULT_RPC_URLS))
    routes = args.route or list(DEFAULT_ROUTES)
    registry = _bnb_cow_registry()
    payload = {
        "network": "bnb",
        "chainId": 56,
        "mode": "shadow_quote_only",
        "executionEnabled": False,
        "broadcastEnabled": False,
        "generatedAt": _utc_now(),
        "rpcUrl": rpc_url,
        "amount": str(args.amount),
        "quoteWarning": "CoW per-hop quotes are diagnostic and not an atomic triangular arbitrage proof.",
        "routes": [
            _compare_route(
                w3=w3,
                route=route,
                amount=str(args.amount),
                registry=registry,
                timeout_seconds=max(1.0, float(args.quote_timeout_seconds)),
            )
            for route in routes
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "BNB_CoW_Pancake报价对比.json"
    md_path = args.output_dir / "BNB_CoW_Pancake报价对比.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_build_report(payload), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "report": str(md_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
