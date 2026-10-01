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
    {
        "type": "function",
        "name": "runArbitrageBestBridgePairs",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "tokenZ", "type": "address"},
            {
                "name": "entryKey",
                "type": "tuple",
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
            },
            {
                "name": "exitKey",
                "type": "tuple",
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
            },
            {"name": "entryZeroForOne", "type": "bool"},
            {"name": "exitZeroForOne", "type": "bool"},
            {
                "name": "bridgeKeys",
                "type": "tuple[]",
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
            },
            {"name": "bridgeZeroForOne", "type": "bool[]"},
            {"name": "amountIn", "type": "uint256"},
            {"name": "minProfit", "type": "uint256"},
        ],
        "outputs": [
            {"name": "outY", "type": "uint256"},
            {"name": "outZ", "type": "uint256"},
            {"name": "outTokenZ", "type": "uint256"},
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


def _env_official_v4_address(field: str) -> str | None:
    env_name = {
        "arb": "OFFICIAL_V4_ARB_ADDRESS",
        "composer": "OFFICIAL_V4_COMPOSER_ADDRESS",
    }.get(field)
    if not env_name:
        return None
    value = os.getenv(env_name, "").strip()
    return value or None


def _official_v4_chain_id() -> int | None:
    raw_chain_id = os.getenv("OFFICIAL_V4_CHAIN_ID", "").strip()
    if raw_chain_id:
        try:
            return int(raw_chain_id)
        except ValueError:
            return None
    try:
        from core.chain_data_registry import default_chain_scope

        scope = default_chain_scope("dex_arbitrage")
        return int(scope["chain_id"]) if scope.get("chain_id") is not None else None
    except Exception:
        return None


def _chain_data_official_v4_address(field: str) -> str | None:
    try:
        from core.chain_data_registry import chain_data_by_chain_id
    except Exception:
        return None
    chain_id = _official_v4_chain_id()
    if chain_id is None:
        return None
    chain = chain_data_by_chain_id(chain_id)
    if not chain:
        return None
    official = None
    for key in ("officialV4", "official_v4", "v4_arbitrage"):
        candidate = chain.get(key) if isinstance(chain.get(key), dict) else None
        if candidate:
            official = candidate
            break
    if official is None:
        contracts = chain.get("contracts") if isinstance(chain.get("contracts"), dict) else {}
        for key in ("officialV4", "official_v4", "v4_arbitrage"):
            candidate = contracts.get(key) if isinstance(contracts.get(key), dict) else None
            if candidate:
                official = candidate
                break
    if official is None:
        return None
    value = str(official.get(field) or "").strip()
    return value or None


def _manifest_official_v4_address(field: str, chain_id: int | None = None) -> str | None:
    if chain_id is not None and int(chain_id) != ROBINHOOD_TESTNET_CHAIN_ID:
        return None
    for base in (Path(__file__).resolve().parents[4], Path.cwd()):
        manifest_path = base / "test-token_create" / "config" / "testnet-v4-amm" / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        official = manifest.get("officialV4") if isinstance(manifest.get("officialV4"), dict) else {}
        value = str(official.get(field) or "").strip()
        return value or None
    return None


def _official_v4_arb_address() -> str | None:
    chain_id = _official_v4_chain_id()
    return (
        _env_official_v4_address("arb")
        or _chain_data_official_v4_address("arb")
        or _manifest_official_v4_address("arb", chain_id)
    )


def _official_v4_composer_address() -> str | None:
    chain_id = _official_v4_chain_id()
    return (
        _env_official_v4_address("composer")
        or _chain_data_official_v4_address("composer")
        or _manifest_official_v4_address("composer", chain_id)
    )


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
    for step_index, step in enumerate(steps):
        pool = _pool_for_step(step, pools)
        pool_key = pool.get("poolKey") if isinstance(pool.get("poolKey"), dict) else {}
        currency0 = _text(pool_key.get("currency0") or pool.get("token0") or "")
        currency1 = _text(pool_key.get("currency1") or pool.get("token1") or "")
        if not (currency0 and currency1):
            hop_addresses = [str(item).strip() for item in (pool.get("routeAddresses") or []) if str(item).strip()]
            if len(hop_addresses) >= 2:
                currency0, currency1 = hop_addresses[0], hop_addresses[1]
            elif step_index + 1 < len(route_addresses):
                currency0, currency1 = route_addresses[step_index], route_addresses[step_index + 1]
        if not (currency0 and currency1):
            return None
        fee = int(pool_key.get("fee") if pool_key.get("fee") is not None else pool.get("fee") if pool.get("fee") is not None else step.get("fee") if step.get("fee") is not None else 0)
        tick_spacing = int(pool_key.get("tickSpacing") if pool_key.get("tickSpacing") is not None else pool.get("tickSpacing") if pool.get("tickSpacing") is not None else 0)
        hooks = _text(pool_key.get("hooks") or pool.get("hooks") or "0x0000000000000000000000000000000000000000")
        input_token = _text(step.get("tokenIn") or _pool_side(pool, step.get("fromSymbol"))[0] or "")
        if not input_token and step_index < len(route_addresses):
            input_token = _text(route_addresses[step_index])
        if not input_token:
            return None
        keys.append((currency0, currency1, fee, tick_spacing, hooks))
        zero_for_one.append(input_token.lower() == currency0.lower())
        pool_ids.append(str(step.get("pool") or pool.get("pool") or ""))
    return route_symbols, route_addresses, steps, keys, zero_for_one, pool_ids


def _official_v4_route_components(
    route: dict[str, Any],
    *,
    fallback_pools: dict[str, dict[str, Any]],
    fallback_steps: list[dict[str, Any]],
) -> tuple[list[str], list[str], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    from dex.runtime.dex_onchain_quote_evidence import (
        _complete_route_steps,
        _norm_symbol,
        _pool_map,
        _text,
    )

    route_symbols = [_norm_symbol(item) for item in route.get("routeSymbols") or []]
    route_addresses = [str(item) for item in route.get("routeAddresses") or [] if str(item).strip()]
    analysis = route.get("quoteAnalysis") if isinstance(route.get("quoteAnalysis"), dict) else {}
    raw_steps = [step for step in analysis.get("steps") or [] if isinstance(step, dict)]
    if not raw_steps:
        raw_steps = fallback_steps or []
    route_pools = _pool_map(route)
    if not route_pools:
        route_pools = fallback_pools or {}
    steps = _complete_route_steps(route_symbols, route_addresses, raw_steps, route_pools)
    return route_symbols, route_addresses, steps, route_pools


def official_v4_arb_static_hop_rows(
    *,
    w3: Any,
    route: dict[str, Any],
    pools: dict[str, dict[str, Any]],
    steps: list[dict[str, Any]],
    amount_in_raw: int,
    chain_id: int | None,
    routes_group: list[dict[str, Any]] | None = None,
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
    candidates: list[tuple[list[str], list[str], list[dict[str, Any]], list[tuple[Any, Any, Any, Any, Any]], list[bool], list[str], dict[str, dict[str, Any]]]] = []
    # Aggregate every closed-loop route in the same flash-loan anchor group into
    # one Candidate so the composer can compare the full group and call the
    # executor once for that group instead of per route.
    for group_route in (routes_group or [route]):
        if not isinstance(group_route, dict):
            continue
        group_symbols, group_addresses, group_steps, group_pools = _official_v4_route_components(
            group_route,
            fallback_pools=pools,
            fallback_steps=steps if group_route is route else [],
        )
        if len(group_symbols) != 4 or group_symbols[0] != group_symbols[-1]:
            continue
        if len(group_steps) != 3:
            continue
        if not all(_text(step.get("protocol") or step.get("dex") or "").lower() in {"uniswap_v4", "v4"} for step in group_steps):
            continue
        reverse_symbols = [group_symbols[0], group_symbols[2], group_symbols[1], group_symbols[0]]
        reverse_addresses = [group_addresses[0], group_addresses[2], group_addresses[1], group_addresses[0]] if len(group_addresses) == 4 else group_addresses
        reverse_steps = [_reverse_step(group_steps[2]), _reverse_step(group_steps[1]), _reverse_step(group_steps[0])]
        for candidate_symbols, candidate_addresses, candidate_steps in (
            (group_symbols, group_addresses, group_steps),
            (reverse_symbols, reverse_addresses, reverse_steps),
        ):
            candidate = _official_v4_arb_candidate_route(
                route_symbols=candidate_symbols,
                route_addresses=candidate_addresses,
                steps=candidate_steps,
                pools=group_pools,
            )
            if candidate is None:
                continue
            candidates.append((*candidate, group_pools))
    if not candidates:
        return None
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
    selected_symbols, selected_addresses, selected_steps, _selected_keys, _selected_directions, selected_pool_ids, selected_pools = candidates[route_index]
    if (
        composer_used
        and str(os.getenv("DEX_OPPORTUNITY_SCANNER_ALLOW_BROADCAST", "")).strip().lower() in {"1", "true", "yes", "on"}
        and selected_symbols == ["TZ", "TB", "TA", "TZ"]
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
        pool = _pool_for_step(step, selected_pools)
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
