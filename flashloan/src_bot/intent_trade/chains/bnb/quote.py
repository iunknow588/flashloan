"""Quote BNB Chain PancakeSwap V2 triangular routes without signing or sending."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from web3 import Web3
from web3.middleware import geth_poa_middleware


SRC_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = SRC_ROOT.parents[1]

from intent_trade.chains.bnb import config as bnb
from intent_trade.chains.bnb.intent import build_bnb_intent_draft


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

V3_QUOTER_ABI = [
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

DEFAULT_ROUTES = (
    ("USDT", "BTCB", "WBNB", "USDT"),
    ("USDT", "ETH", "WBNB", "USDT"),
    ("USDT", "CAKE", "WBNB", "USDT"),
    ("USDC", "USDT", "WBNB", "USDC"),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _connect(rpc_urls: list[str]) -> tuple[Web3, str]:
    failures: list[str] = []
    for url in rpc_urls:
        w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 15}))
        try:
            try:
                w3.middleware_onion.inject(geth_poa_middleware, layer=0)
            except Exception:
                pass
            if w3.is_connected() and int(w3.eth.chain_id) == bnb.CHAIN_ID:
                return w3, url
        except Exception as exc:
            failures.append(f"{url}: {exc!r}")
    raise RuntimeError("no usable BNB RPC: " + "; ".join(failures[:3]))


def _to_units(value: str | float | int, symbol: str) -> int:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid quote amount: {value!r}") from exc
    if amount <= 0:
        raise ValueError("quote amount must be positive")
    return int(amount * Decimal(10 ** bnb.TOKENS[symbol].decimals))


def _from_units(value: int, symbol: str) -> str:
    decimals = bnb.TOKENS[symbol].decimals
    result = Decimal(int(value)) / Decimal(10 ** decimals)
    return format(result.normalize(), "f")


def _quote_hop(w3: Web3, hop: dict[str, Any], amount_in: int) -> tuple[int, dict[str, Any]]:
    token_in = str(hop["tokenIn"])
    token_out = str(hop["tokenOut"])
    protocol = str(hop.get("protocol") or "")
    if protocol == "v2":
        router = w3.eth.contract(address=Web3.to_checksum_address(str(hop["router"])), abi=ROUTER_ABI)
        amounts = router.functions.getAmountsOut(
            int(amount_in),
            [
                Web3.to_checksum_address(bnb.TOKENS[token_in].address),
                Web3.to_checksum_address(bnb.TOKENS[token_out].address),
            ],
        ).call()
        return int(amounts[-1]), {"quoteMethod": "pancakeswap_v2_router_getAmountsOut"}
    if protocol == "v3":
        quoter_address = str(hop.get("quoter") or "")
        if not quoter_address:
            raise ValueError(f"V3 pool has no quoter for {token_in}->{token_out}")
        quoter = w3.eth.contract(address=Web3.to_checksum_address(quoter_address), abi=V3_QUOTER_ABI)
        amount_out, sqrt_after, ticks_crossed, gas_estimate = quoter.functions.quoteExactInputSingle(
            {
                "tokenIn": Web3.to_checksum_address(bnb.TOKENS[token_in].address),
                "tokenOut": Web3.to_checksum_address(bnb.TOKENS[token_out].address),
                "amountIn": int(amount_in),
                "fee": int(hop["fee"]),
                "sqrtPriceLimitX96": 0,
            }
        ).call()
        return int(amount_out), {
            "quoteMethod": "pancakeswap_v3_quoter_quoteExactInputSingle",
            "sqrtPriceX96After": str(sqrt_after),
            "initializedTicksCrossed": int(ticks_crossed),
            "quoterGasEstimate": int(gas_estimate),
        }
    raise ValueError(f"unsupported BNB pool protocol: {protocol}")


def quote_route(
    w3: Web3,
    route_symbols: tuple[str, str, str, str],
    *,
    amount: str | float | int,
    static_table_path: Path | None = None,
    allowed_statuses: set[str] | None = None,
) -> dict[str, Any]:
    draft = build_bnb_intent_draft(
        route_symbols,
        amount=amount,
        static_table_path=static_table_path,
        allowed_statuses=allowed_statuses,
    )
    amount_in = _to_units(amount, route_symbols[0])
    hops = []
    for index, hop in enumerate(draft["hops"]):
        token_in = route_symbols[index]
        token_out = route_symbols[index + 1]
        amount_out, quote_metadata = _quote_hop(w3, hop, amount_in)
        hops.append(
            {
                **hop,
                **quote_metadata,
                "amountInRaw": str(amount_in),
                "amountOutRaw": str(amount_out),
                "amountIn": _from_units(amount_in, token_in),
                "amountOut": _from_units(amount_out, token_out),
            }
        )
        amount_in = amount_out
    final_amount = Decimal(_from_units(amount_in, route_symbols[0]))
    initial_amount = Decimal(str(amount))
    return {
        "network": bnb.NETWORK,
        "chainId": bnb.CHAIN_ID,
        "quotedAt": _utc_now(),
        "blockNumber": int(w3.eth.block_number),
        "mode": "shadow_quote_only",
        "executionEnabled": False,
        "routeSymbols": list(route_symbols),
        "path": " -> ".join(route_symbols),
        "adapters": [{"protocol": hop["protocol"], "router": hop.get("router"), "quoter": hop.get("quoter")} for hop in draft["hops"]],
        "initialAmount": format(initial_amount.normalize(), "f"),
        "finalAmount": format(final_amount.normalize(), "f"),
        "grossProfit": format((final_amount - initial_amount).normalize(), "f"),
        "profitableBeforeFlashloanAndGas": final_amount > initial_amount,
        "costStatus": "flashloan_and_gas_unverified",
        "intentDraft": draft,
        "hops": hops,
    }


def _parse_route(raw: str) -> tuple[str, str, str, str]:
    parts = tuple(piece.strip().upper() for piece in raw.split("->") if piece.strip())
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("route must contain exactly four symbols, e.g. USDT->BTCB->WBNB->USDT")
    return parts  # type: ignore[return-value]


def main() -> int:
    parser = argparse.ArgumentParser(description="Quote BNB Chain triangular routes without signing or broadcasting.")
    parser.add_argument("--rpc-url", action="append", default=[])
    parser.add_argument("--amount", default="1000")
    parser.add_argument("--route", action="append", type=_parse_route, default=[])
    parser.add_argument("--static-table", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "data_info" / "BNB" / "BNB链实时逐跳报价.json")
    args = parser.parse_args()

    w3, rpc_url = _connect(args.rpc_url or list(bnb.DEFAULT_RPC_URLS))
    routes = args.route or list(DEFAULT_ROUTES)
    reports = []
    for route in routes:
        try:
            reports.append({"ok": True, **quote_route(w3, route, amount=args.amount, static_table_path=args.static_table)})
        except Exception as exc:
            reports.append({"ok": False, "routeSymbols": list(route), "path": " -> ".join(route), "error": str(exc)})
    payload = {
        "network": bnb.NETWORK,
        "chainId": bnb.CHAIN_ID,
        "rpcUrl": rpc_url,
        "generatedAt": _utc_now(),
        "mode": "shadow_quote_only",
        "executionEnabled": False,
        "reports": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
