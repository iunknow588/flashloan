"""Read-only Robinhood V3 market snapshot and arbitrage analysis.

This module deliberately stops at ``eth_call``/read methods.  It does not
load a signer, build a transaction, submit an approval, or broadcast a swap.
The addresses in ``DEFAULT_RESEARCH_MARKET`` are research inputs discovered
from the current chain state; they are not production execution config.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from web3 import Web3

from bot.execution.uniswap_v3 import (
    snapshot_v3_pool,
    quote_v3_exact_input_single,
)
from dex.intent_trade.chains.robinhood import config as robinhood


DEFAULT_RESEARCH_MARKET: dict[str, Any] = {
    "network": robinhood.NETWORK,
    "chainId": robinhood.CHAIN_ID,
    "rpcUrl": (robinhood.DEFAULT_RPC_URLS or ("https://rpc.mainnet.chain.robinhood.com",))[0],
    "factory": "0x1f7d7550b1b028f7571e69a784071F0205FD2EfA",
    "quoter": robinhood.default_v3_quoter_address("0x33e885eD0Ec9bF04EcfB19341582aADCb4c8A9E7"),
    "router": "0xCaf681a66D020601342297493863E78C959E5cb2",
    "tokens": {
        "WETH": {
            "address": robinhood.WETH_ADDRESS,
            "decimals": 18,
            "sourceStatus": "chain_observed_pending_independent_source_verification",
        },
        "USDG": {
            "address": "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168",
            "decimals": 6,
            "sourceStatus": "chain_observed_pending_registry_verification",
        },
    },
    "pools": [
        {
            "name": "uniswap-v3-0.01%",
            "address": "0x52e65B17fB6E5BA00Ed806f37Afcd2DaA50271Ca",
            "fee": 100,
        },
        {
            "name": "uniswap-v3-0.05%",
            "address": "0x69BfaF19C9f377BB306a89aEd9F6B07e2c1a8d9a",
            "fee": 500,
        },
        {
            "name": "uniswap-v3-0.30%",
            "address": "0xa9188730Fe85Be88ad499D7d52B099e800fB0334",
            "fee": 3000,
        },
        {
            "name": "uniswap-v3-1.00%",
            "address": "0x5f009E071F07e92B6C624e83F52F17bBDa34680D",
            "fee": 10000,
        },
    ],
    "addressEvidence": {
        "status": "readonly_chain_observation",
        "sourceUrls": [
            "https://docs.uniswap.org/contracts/v3/reference/deployments",
            "https://robinhoodchain.blockscout.com",
        ],
        "executionConfigWriteAllowed": False,
    },
}


JsonRpcTransport = Callable[[str, dict[str, Any], float], dict[str, Any]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decimal_amount(value: str | int | float | Decimal, decimals: int) -> int:
    amount = Decimal(str(value))
    if amount <= 0:
        raise ValueError("amount must be positive")
    scaled = amount * (Decimal(10) ** int(decimals))
    if scaled != scaled.to_integral_value():
        raise ValueError("amount has more precision than token decimals")
    return int(scaled)


def _human_amount(value: int, decimals: int) -> str:
    amount = Decimal(int(value)) / (Decimal(10) ** int(decimals))
    return format(amount.normalize(), "f")


def _checksum(value: str) -> str:
    return Web3.to_checksum_address(str(value))


def _code_present(w3: Web3, address: str, block_identifier: int | str = "latest") -> bool:
    code = w3.eth.get_code(_checksum(address), block_identifier=block_identifier)
    return str(code or "").lower() not in {"", "0x", "0x0"}


def _gas_price(w3: Web3) -> dict[str, Any]:
    try:
        value = int(w3.eth.gas_price)
        return {
            "ok": True,
            "source": "eth_gasPrice",
            "gasPriceWei": value,
            "gasPriceGwei": value / 1_000_000_000,
        }
    except Exception as exc:
        return {
            "ok": False,
            "source": "eth_gasPrice",
            "gasPriceWei": None,
            "gasPriceGwei": None,
            "error": str(exc),
        }


def _pool_state(w3: Web3, item: dict[str, Any], block_number: int) -> dict[str, Any]:
    address = _checksum(str(item["address"]))
    snapshot = snapshot_v3_pool(w3, address, block_identifier=block_number)
    return {
        "name": item.get("name"),
        "address": snapshot.pool_address,
        "token0": snapshot.token0,
        "token1": snapshot.token1,
        "fee": snapshot.fee,
        "liquidity": str(snapshot.liquidity),
        "sqrtPriceX96": str(snapshot.sqrt_price_x96),
        "tick": snapshot.tick,
        "blockNumber": block_number,
        "codePresent": _code_present(w3, address, block_number),
    }


def _quote(
    w3: Web3,
    quoter: str,
    token_in: str,
    token_out: str,
    fee: int,
    amount_in: int,
    block_number: int,
) -> dict[str, Any]:
    amount_out, sqrt_after, ticks, gas_estimate = quote_v3_exact_input_single(
        w3,
        quoter,
        token_in,
        token_out,
        fee,
        amount_in,
        block_identifier=block_number,
    )
    return {
        "ok": True,
        "tokenIn": _checksum(token_in),
        "tokenOut": _checksum(token_out),
        "fee": int(fee),
        "amountInRaw": str(amount_in),
        "amountOutRaw": str(amount_out),
        "sqrtPriceX96After": str(sqrt_after),
        "initializedTicksCrossed": int(ticks),
        "quoterGasEstimate": int(gas_estimate),
        "quoteBlock": block_number,
        "quoteMethod": "quoter_v2_quoteExactInputSingle_eth_call",
    }


def _round_trip(
    w3: Web3,
    *,
    quoter: str,
    weth: str,
    usdg: str,
    weth_decimals: int,
    usdg_decimals: int,
    first: dict[str, Any],
    second: dict[str, Any],
    amount_usdg_raw: int,
    block_number: int,
) -> dict[str, Any]:
    first_quote = _quote(
        w3,
        quoter,
        usdg,
        weth,
        int(first["fee"]),
        amount_usdg_raw,
        block_number,
    )
    second_quote = _quote(
        w3,
        quoter,
        weth,
        usdg,
        int(second["fee"]),
        int(first_quote["amountOutRaw"]),
        block_number,
    )
    final_raw = int(second_quote["amountOutRaw"])
    gross_raw = final_raw - int(amount_usdg_raw)
    return {
        "route": [
            {"pool": first["address"], "fee": int(first["fee"]), "direction": "USDG->WETH"},
            {"pool": second["address"], "fee": int(second["fee"]), "direction": "WETH->USDG"},
        ],
        "amountIn": _human_amount(amount_usdg_raw, usdg_decimals),
        "amountOut": _human_amount(final_raw, usdg_decimals),
        "grossProfit": _human_amount(gross_raw, usdg_decimals),
        "amountInRaw": str(amount_usdg_raw),
        "amountOutRaw": str(final_raw),
        "grossProfitRaw": str(gross_raw),
        "profitableBeforeCosts": gross_raw > 0,
        "hops": [first_quote, second_quote],
        "quoterGasEstimate": sum(int(item.get("quoterGasEstimate") or 0) for item in (first_quote, second_quote)),
        "wethDecimals": weth_decimals,
        "usdgDecimals": usdg_decimals,
    }


def model_costs(
    *,
    gross_profit_usdg_raw: int,
    amount_in_usdg_raw: int,
    gas_price_wei: int | None,
    gas_units: int | None,
    weth_usdg_raw_per_weth: int | None,
    flashloan_fee_bps: int | None = None,
    mev_buffer_usdg_raw: int = 0,
) -> dict[str, Any]:
    """Model costs without pretending unverified inputs are verified.

    ``weth_usdg_raw_per_weth`` is a quote-derived conversion, not an oracle.
    If any required input is missing, net profit remains ``None``.
    """

    result: dict[str, Any] = {
        "grossProfitUsdgRaw": int(gross_profit_usdg_raw),
        "flashloanFeeUsdgRaw": None,
        "gasCostUsdgRaw": None,
        "mevBufferUsdgRaw": int(mev_buffer_usdg_raw),
        "netProfitUsdgRaw": None,
        "netProfitModelStatus": "incomplete",
        "inputs": {
            "gasPriceWei": gas_price_wei,
            "gasUnits": gas_units,
            "wethUsdgRawPerWeth": weth_usdg_raw_per_weth,
            "flashloanFeeBps": flashloan_fee_bps,
        },
    }
    if flashloan_fee_bps is not None:
        result["flashloanFeeUsdgRaw"] = (
            int(amount_in_usdg_raw) * max(0, int(flashloan_fee_bps)) + 9_999
        ) // 10_000
    if gas_price_wei is not None and gas_units is not None and weth_usdg_raw_per_weth is not None:
        gas_wei = max(0, int(gas_price_wei)) * max(0, int(gas_units))
        result["gasCostUsdgRaw"] = (
            gas_wei * max(0, int(weth_usdg_raw_per_weth)) + (10**18 - 1)
        ) // 10**18
    if result["flashloanFeeUsdgRaw"] is not None and result["gasCostUsdgRaw"] is not None:
        result["netProfitUsdgRaw"] = (
            int(gross_profit_usdg_raw)
            - int(result["flashloanFeeUsdgRaw"])
            - int(result["gasCostUsdgRaw"])
            - int(mev_buffer_usdg_raw)
        )
        result["netProfitModelStatus"] = "complete"
    return result


def build_robinhood_market_snapshot(
    *,
    rpc_url: str | None = None,
    market: dict[str, Any] | None = None,
    amount_usdg: str | int | float | Decimal = "1000",
    timeout_seconds: float = 15,
    flashloan_fee_bps: int | None = None,
    gas_units_override: int | None = None,
) -> dict[str, Any]:
    """Read current pool state and quote every ordered pair of configured pools."""

    profile = dict(DEFAULT_RESEARCH_MARKET)
    if market:
        profile.update(market)
    selected_rpc = str(rpc_url or profile["rpcUrl"])
    w3 = Web3(Web3.HTTPProvider(selected_rpc, request_kwargs={"timeout": timeout_seconds}))
    chain_id = int(w3.eth.chain_id)
    if chain_id != robinhood.CHAIN_ID:
        raise ValueError(f"Robinhood RPC returned chain id {chain_id}, expected {robinhood.CHAIN_ID}")
    block_number = int(w3.eth.block_number)
    tokens = profile["tokens"]
    weth = _checksum(tokens["WETH"]["address"])
    usdg = _checksum(tokens["USDG"]["address"])
    weth_decimals = int(tokens["WETH"]["decimals"])
    usdg_decimals = int(tokens["USDG"]["decimals"])
    amount_usdg_raw = _decimal_amount(amount_usdg, usdg_decimals)

    contracts = {
        key: {
            "address": profile.get(key),
            "codePresent": _code_present(w3, str(profile[key]), block_number),
        }
        for key in ("factory", "quoter", "router")
    }
    contracts["weth"] = {"address": weth, "codePresent": _code_present(w3, weth, block_number)}
    contracts["usdg"] = {"address": usdg, "codePresent": _code_present(w3, usdg, block_number)}

    pool_states: list[dict[str, Any]] = []
    for item in profile.get("pools") or []:
        try:
            pool_states.append(_pool_state(w3, item, block_number))
        except Exception as exc:
            pool_states.append(
                {
                    "name": item.get("name"),
                    "address": item.get("address"),
                    "fee": item.get("fee"),
                    "blockNumber": block_number,
                    "codePresent": False,
                    "error": str(exc),
                }
            )

    valid_pools = [
        item
        for item in pool_states
        if item.get("codePresent")
        and str(item.get("token0") or "").lower() in {weth.lower(), usdg.lower()}
        and str(item.get("token1") or "").lower() in {weth.lower(), usdg.lower()}
        and int(item.get("liquidity") or 0) > 0
    ]
    rounds: list[dict[str, Any]] = []
    for first in valid_pools:
        for second in valid_pools:
            if first["address"].lower() == second["address"].lower():
                continue
            try:
                rounds.append(
                    _round_trip(
                        w3,
                        quoter=str(profile["quoter"]),
                        weth=weth,
                        usdg=usdg,
                        weth_decimals=weth_decimals,
                        usdg_decimals=usdg_decimals,
                        first=first,
                        second=second,
                        amount_usdg_raw=amount_usdg_raw,
                        block_number=block_number,
                    )
                )
            except Exception as exc:
                rounds.append(
                    {
                        "route": [
                            {"pool": first["address"], "fee": first["fee"], "direction": "USDG->WETH"},
                            {"pool": second["address"], "fee": second["fee"], "direction": "WETH->USDG"},
                        ],
                        "profitableBeforeCosts": False,
                        "status": "quote_failed",
                        "error": str(exc),
                    }
                )

    successful_rounds = [item for item in rounds if "amountOutRaw" in item]
    best = max(successful_rounds, key=lambda item: int(item["grossProfitRaw"]), default=None)
    gas = _gas_price(w3)
    weth_to_usdg = None
    if best and best.get("hops"):
        weth_to_usdg = int(best["hops"][1]["amountOutRaw"])
        weth_input = int(best["hops"][1]["amountInRaw"])
        if weth_input:
            weth_to_usdg = (weth_to_usdg * 10**weth_decimals) // weth_input
    gas_units = gas_units_override
    if gas_units is None and best:
        gas_units = int(best.get("quoterGasEstimate") or 0)
    costs = (
        model_costs(
            gross_profit_usdg_raw=int(best["grossProfitRaw"]),
            amount_in_usdg_raw=amount_usdg_raw,
            gas_price_wei=gas.get("gasPriceWei"),
            gas_units=gas_units,
            weth_usdg_raw_per_weth=weth_to_usdg,
            flashloan_fee_bps=flashloan_fee_bps,
        )
        if best
        else {"netProfitModelStatus": "incomplete"}
    )

    return {
        "schemaVersion": "robinhood-market-snapshot-v1",
        "generatedAt": _utc_now(),
        "network": robinhood.NETWORK,
        "chainId": chain_id,
        "rpcUrl": selected_rpc,
        "blockNumber": block_number,
        "mode": "readonly_market_snapshot",
        "safety": {
            "loadsSigner": False,
            "buildsTransaction": False,
            "broadcastsTransaction": False,
            "executionEnabled": False,
            "broadcastEnabled": False,
        },
        "addressEvidence": profile.get("addressEvidence") or {},
        "contracts": contracts,
        "tokens": tokens,
        "gas": gas,
        "amount": {"symbol": "USDG", "decimals": usdg_decimals, "raw": str(amount_usdg_raw), "human": str(amount_usdg)},
        "poolCount": len(pool_states),
        "validPoolCount": len(valid_pools),
        "pools": pool_states,
        "routes": rounds,
        "bestRoute": best,
        "costModel": costs,
        "arbitrageAssessment": {
            "grossProfitableRouteFound": bool(best and best.get("profitableBeforeCosts")),
            "netProfitVerified": False,
            "executionEligible": False,
            "flashloanStatus": "unverified",
            "blockedReasons": [
                "robinhood_flashloan_provider_unverified",
                "robinhood_executor_unverified",
                "robinhood_fork_simulation_unverified",
                "broadcast_disabled_by_design",
            ],
        },
    }
