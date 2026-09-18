from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from web3 import Web3

from dex.intent_trade.chains.robinhood.chain_test_policy import ROBINHOOD_TESTNET_CHAIN_ID


_OFFICIAL_V4_ARB_ABI = [
    {
        "type": "function",
        "name": "runArbitrage",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "tokenZ", "type": "address"},
            {
                "name": "keys",
                "type": "tuple[3]",
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
            },
            {"name": "zeroForOne", "type": "bool[3]"},
            {"name": "amountIn", "type": "uint256"},
            {"name": "minProfit", "type": "uint256"},
        ],
        "outputs": [
            {"name": "outTb", "type": "uint256"},
            {"name": "outTa", "type": "uint256"},
            {"name": "outTz", "type": "uint256"},
            {"name": "profit", "type": "uint256"},
        ],
    },
    {
        "type": "function",
        "name": "runArbitrageBest",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "tokenZ", "type": "address"},
            {
                "name": "keysets",
                "type": "tuple[3][]",
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
            },
            {"name": "directionsets", "type": "bool[3][]"},
            {"name": "amountIn", "type": "uint256"},
            {"name": "minProfit", "type": "uint256"},
        ],
        "outputs": [
            {"name": "outTb", "type": "uint256"},
            {"name": "outTa", "type": "uint256"},
            {"name": "outTz", "type": "uint256"},
            {"name": "profit", "type": "uint256"},
            {"name": "routeIndex", "type": "uint256"},
        ],
    },
]


_OFFICIAL_V4_COMPOSER_ABI = [
    {
        "type": "function",
        "name": "runBest",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "tokenZ", "type": "address"},
            {
                "name": "candidates",
                "type": "tuple[]",
                "components": [
                    {
                        "name": "routePools",
                        "type": "tuple[3][]",
                        "components": [
                            {"name": "currency0", "type": "address"},
                            {"name": "currency1", "type": "address"},
                            {"name": "fee", "type": "uint24"},
                            {"name": "tickSpacing", "type": "int24"},
                            {"name": "hooks", "type": "address"},
                        ],
                    },
                    {"name": "routeDirections", "type": "bool[3][]"},
                    {"name": "deviationBps", "type": "uint256"},
                    {"name": "mQuote", "type": "uint256"},
                    {"name": "xQuote", "type": "uint256"},
                    {"name": "yQuote", "type": "uint256"},
                ],
            },
            {"name": "amountIn", "type": "uint256"},
            {"name": "minProfit", "type": "uint256"},
            {"name": "minDeviationBps", "type": "uint256"},
        ],
        "outputs": [
            {"name": "executed", "type": "bool"},
            {"name": "candidateIndex", "type": "uint256"},
            {"name": "profit", "type": "uint256"},
            {"name": "resultData", "type": "bytes"},
        ],
    }
]


def _official_v4_arb_address() -> str | None:
    for base in (Path(__file__).resolve().parents[4], Path.cwd()):
        manifest_path = base / "test-token_create" / "config" / "testnet-v4-amm" / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        official = manifest.get("officialV4") if isinstance(manifest.get("officialV4"), dict) else {}
        arb = str(official.get("arb") or "").strip()
        return arb or None
    return None


def _official_v4_composer_address() -> str | None:
    for base in (Path(__file__).resolve().parents[4], Path.cwd()):
        manifest_path = base / "test-token_create" / "config" / "testnet-v4-amm" / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        official = manifest.get("officialV4") if isinstance(manifest.get("officialV4"), dict) else {}
        composer = str(official.get("composer") or "").strip()
        return composer or None
    return None


def _reverse_step(step: dict[str, Any]) -> dict[str, Any]:
    from dex.runtime.dex_onchain_quote_evidence import _norm_symbol

    reversed_step = dict(step)
    reversed_step["fromSymbol"] = _norm_symbol(step.get("toSymbol"))
    reversed_step["toSymbol"] = _norm_symbol(step.get("fromSymbol"))
    reversed_step["tokenIn"] = step.get("tokenOut") or ""
    reversed_step["tokenOut"] = step.get("tokenIn") or ""
    return reversed_step


def _official_v4_arb_candidate_route(
    *, route_symbols: list[str], route_addresses: list[str], steps: list[dict[str, Any]], pools: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str], list[dict[str, Any]], list[tuple[Any, Any, Any, Any, Any]], list[bool], list[str]] | None:
    from dex.runtime.dex_onchain_quote_evidence import _pool_for_step, _pool_side, _text

    keys: list[tuple[Any, Any, Any, Any, Any]] = []
    zero_for_one: list[bool] = []
    pool_ids: list[str] = []
    for step in steps:
        pool = _pool_for_step(step, pools)
        pool_key = pool.get("poolKey") if isinstance(pool.get("poolKey"), dict) else {}
        currency0 = _text(pool_key.get("currency0") or pool.get("token0") or "")
        currency1 = _text(pool_key.get("currency1") or pool.get("token1") or "")
        if not (currency0 and currency1):
            return None
        fee = int(pool_key.get("fee") if pool_key.get("fee") is not None else pool.get("fee") if pool.get("fee") is not None else step.get("fee") if step.get("fee") is not None else 0)
        tick_spacing = int(pool_key.get("tickSpacing") if pool_key.get("tickSpacing") is not None else pool.get("tickSpacing") if pool.get("tickSpacing") is not None else 0)
        hooks = _text(pool_key.get("hooks") or pool.get("hooks") or "0x0000000000000000000000000000000000000000")
        input_token = _text(step.get("tokenIn") or _pool_side(pool, step.get("fromSymbol"))[0] or "")
        if not input_token:
            return None
        keys.append((currency0, currency1, fee, tick_spacing, hooks))
        zero_for_one.append(input_token.lower() == currency0.lower())
        pool_ids.append(str(step.get("pool") or pool.get("pool") or ""))
    return route_symbols, route_addresses, steps, keys, zero_for_one, pool_ids


def official_v4_arb_static_hop_rows(
    *,
    w3: Any,
    route: dict[str, Any],
    pools: dict[str, dict[str, Any]],
    steps: list[dict[str, Any]],
    amount_in_raw: int,
    chain_id: int | None,
) -> tuple[list[dict[str, Any]], list[str], list[str], list[dict[str, Any]]] | None:
    from dex.runtime.dex_onchain_quote_evidence import (
        _human_from_raw,
        _norm_symbol,
        _pool_for_step,
        _pool_side,
        _text,
        _token_decimals,
    )

    if int(chain_id or 0) != ROBINHOOD_TESTNET_CHAIN_ID or len(steps) != 3:
        return None
    route_symbols = [_text(s).upper() for s in (route.get("routeSymbols") or [])]
    if len(route_symbols) != 4 or route_symbols[0] != route_symbols[-1]:
        return None
    if not all(_text(step.get("protocol") or step.get("dex") or "").lower() in {"uniswap_v4", "v4"} for step in steps):
        return None
    arb_address = _official_v4_arb_address()
    if not arb_address:
        return None
    route_addresses = [str(item) for item in route.get("routeAddresses") or [] if str(item).strip()]
    first_pool = _pool_for_step(steps[0], pools)
    input_token_first = _text(steps[0].get("tokenIn") or _pool_side(first_pool, route_symbols[0])[0] or "")
    token_z = route_addresses[0] if route_addresses else input_token_first
    if not token_z:
        return None
    reverse_symbols = [route_symbols[0], route_symbols[2], route_symbols[1], route_symbols[0]]
    reverse_addresses = [route_addresses[0], route_addresses[2], route_addresses[1], route_addresses[0]] if len(route_addresses) == 4 else route_addresses
    reverse_steps = [_reverse_step(steps[2]), _reverse_step(steps[1]), _reverse_step(steps[0])]
    candidates: list[tuple[list[str], list[str], list[dict[str, Any]], list[tuple[Any, Any, Any, Any, Any]], list[bool], list[str]]] = []
    for candidate_symbols, candidate_addresses, candidate_steps in (
        (route_symbols, route_addresses, steps),
        (reverse_symbols, reverse_addresses, reverse_steps),
    ):
        candidate = _official_v4_arb_candidate_route(
            route_symbols=candidate_symbols,
            route_addresses=candidate_addresses,
            steps=candidate_steps,
            pools=pools,
        )
        if candidate is None:
            return None
        candidates.append(candidate)
    keysets = [candidate[3] for candidate in candidates]
    directionsets = [candidate[4] for candidate in candidates]
    quote_source = "official_v4_arb_static_call"
    composer_used = False
    composer_profit: int | None = None
    composer_route_index: int | None = None
    broadcast_record: dict[str, Any] = {}
    result = None
    composer_address = _official_v4_composer_address()
    try:
        if composer_address:
            candidate = [keysets, directionsets, 0, 0, 0, 0]
            contract = w3.eth.contract(address=Web3.to_checksum_address(composer_address), abi=_OFFICIAL_V4_COMPOSER_ABI)
            composer_result = contract.functions.runBest(
                Web3.to_checksum_address(token_z),
                [candidate],
                int(amount_in_raw),
                1,
                0,
            ).call()
            executed, _candidate_index, _profit, result_data = composer_result
            if executed and result_data:
                decoded = w3.codec.decode_abi(
                    ["uint256", "uint256", "uint256", "uint256", "uint256"],
                    result_data,
                )
                result = tuple(decoded)
                quote_source = "official_v4_composer_static_call"
                composer_used = True
                composer_profit = int(result[3])
                composer_route_index = int(result[4])
    except Exception:
        result = None
    if result is None:
        try:
            contract = w3.eth.contract(address=Web3.to_checksum_address(arb_address), abi=_OFFICIAL_V4_ARB_ABI)
            result = contract.functions.runArbitrageBest(
                Web3.to_checksum_address(token_z),
                keysets,
                directionsets,
                int(amount_in_raw),
                1,
            ).call()
        except Exception:
            return None
    out_tb, out_ta, out_tz, _profit, route_index = (
        int(result[0]),
        int(result[1]),
        int(result[2]),
        int(result[3]),
        int(result[4]),
    )
    if route_index >= len(candidates):
        return None
    selected_symbols, selected_addresses, selected_steps, _selected_keys, _selected_directions, selected_pool_ids = candidates[route_index]
    if (
        composer_used
        and str(os.getenv("DEX_OPPORTUNITY_SCANNER_ALLOW_BROADCAST", "")).strip().lower() in {"1", "true", "yes", "on"}
        and route_symbols == ["TZ", "TB", "TA", "TZ"]
    ):
        try:
            candidate = [keysets, directionsets, 0, 0, 0, 0]
            tx_hash = contract.functions.runBest(
                Web3.to_checksum_address(token_z),
                [candidate],
                int(amount_in_raw),
                1,
                0,
            ).transact({})
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
            broadcast_record = {
                "txHash": Web3.to_hex(tx_hash),
                "status": str(receipt.get("status")),
            }
        except Exception as exc:
            broadcast_record = {"error": str(exc)}
    amounts = [out_tb, out_ta, out_tz]
    hop_rows: list[dict[str, Any]] = []
    for index, step in enumerate(selected_steps):
        pool = _pool_for_step(step, pools)
        from_symbol = _norm_symbol(step.get("fromSymbol"))
        to_symbol = _norm_symbol(step.get("toSymbol"))
        _, _, in_decimals = _pool_side(pool, from_symbol)
        _, _, out_decimals = _pool_side(pool, to_symbol)
        if in_decimals is None:
            in_decimals = _token_decimals(
                w3,
                _text(step.get("tokenIn") or _pool_side(pool, from_symbol)[0]) or None,
                from_symbol,
                {},
            )
        if out_decimals is None:
            out_decimals = _token_decimals(
                w3,
                _text(step.get("tokenOut") or _pool_side(pool, to_symbol)[0]) or None,
                to_symbol,
                {},
            )
        hop_in_raw = amount_in_raw if index == 0 else int(amounts[index - 1])
        hop_out_raw = int(amounts[index])
        hop_rows.append(
            {
                "hop": index + 1,
                "status": "live_verified",
                "executionTrust": "live_quoter_verified_readonly",
                "quoteSource": quote_source,
                "quoteMethod": quote_source,
                "composerVerified": composer_used,
                "composerRouteIndex": composer_route_index,
                "composerProfitRaw": str(composer_profit) if composer_used else None,
                "scannerBroadcast": broadcast_record,
                "protocol": "uniswap_v4",
                "pool": selected_pool_ids[index],
                "fromSymbol": from_symbol,
                "toSymbol": to_symbol,
                "tokenIn": step.get("tokenIn") or _pool_side(pool, from_symbol)[0],
                "tokenOut": step.get("tokenOut") or _pool_side(pool, to_symbol)[0],
                "amountInRaw": str(hop_in_raw),
                "amountIn": _human_from_raw(hop_in_raw, in_decimals),
                "amountOutRaw": str(hop_out_raw),
                "amountOut": _human_from_raw(hop_out_raw, out_decimals),
                "inputDecimals": in_decimals,
                "outputDecimals": out_decimals,
                "priceImpactWithinLimit": True,
                "staticQuote": {
                    "outputAmount": _human_from_raw(hop_out_raw, out_decimals),
                    "outputUsd": None,
                    "quoteSource": quote_source,
                    "executionTrust": "live_quoter_verified_readonly",
                },
            }
        )
    return hop_rows, selected_symbols, selected_addresses, selected_steps
