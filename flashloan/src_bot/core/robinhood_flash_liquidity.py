from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests


ROBINHOOD_CHAIN_ID = 4663
ROBINHOOD_RPC_URL = "https://rpc.mainnet.chain.robinhood.com"
MORPHO_BLUE_CORE = "0x9D53d5E3bd5E8d4Cbfa6DB1ca238AEA02E651010"
UNISWAP_V2_FACTORY = "0x8bCeAA40b9ACdFAEDF85Adf4FF01f5aD6517937f"
USDG = "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"
WETH = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"

TOKENS = {
    "USDG": {"symbol": "USDG", "address": USDG, "decimals": 6, "price_hint_usd": 1.0},
    "WETH": {"symbol": "WETH", "address": WETH, "decimals": 18, "price_hint_usd": 2450.0},
}

BALANCE_OF_SELECTOR = "0x70a08231"
GET_PAIR_SELECTOR = "0xe6a43905"
TOKEN0_SELECTOR = "0x0dfe1681"
TOKEN1_SELECTOR = "0xd21220a7"
GET_RESERVES_SELECTOR = "0x0902f1ac"


def _normalize_address(address: str) -> str:
    text = str(address or "").strip()
    if not text.startswith("0x") or len(text) != 42:
        raise ValueError(f"invalid address: {address}")
    return "0x" + text[2:]


def _address_word(address: str) -> str:
    return _normalize_address(address)[2:].lower().rjust(64, "0")


def _word_to_address(result: str | None) -> str:
    if not result or result == "0x":
        return "0x0000000000000000000000000000000000000000"
    return "0x" + result[2:].rjust(64, "0")[-40:]


def _word_to_int(result: str | None) -> int:
    if not result or result == "0x":
        return 0
    return int(result[2:] or "0", 16)


def _human(raw: int, decimals: int) -> float:
    return float(raw) / float(10 ** int(decimals))


def _eth_call(*, rpc_url: str, to: str, data: str, timeout: float) -> str:
    response = requests.post(
        rpc_url,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": [{"to": _normalize_address(to), "data": data}, "latest"],
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    return str(payload.get("result") or "0x")


def _erc20_balance(*, rpc_url: str, token: str, owner: str, timeout: float) -> int:
    return _word_to_int(
        _eth_call(
            rpc_url=rpc_url,
            to=token,
            data=BALANCE_OF_SELECTOR + _address_word(owner),
            timeout=timeout,
        )
    )


def _uniswap_v2_pair(*, rpc_url: str, factory: str, token_a: str, token_b: str, timeout: float) -> str:
    return _word_to_address(
        _eth_call(
            rpc_url=rpc_url,
            to=factory,
            data=GET_PAIR_SELECTOR + _address_word(token_a) + _address_word(token_b),
            timeout=timeout,
        )
    )


def _pair_token(*, rpc_url: str, pair: str, selector: str, timeout: float) -> str:
    return _word_to_address(_eth_call(rpc_url=rpc_url, to=pair, data=selector, timeout=timeout))


def _pair_reserves(*, rpc_url: str, pair: str, timeout: float) -> tuple[int, int]:
    result = _eth_call(rpc_url=rpc_url, to=pair, data=GET_RESERVES_SELECTOR, timeout=timeout)
    data = result[2:].rjust(64 * 3, "0")
    return int(data[0:64], 16), int(data[64:128], 16)


def _morpho_source(*, token: dict[str, Any], rpc_url: str, timeout: float) -> dict[str, Any]:
    raw = _erc20_balance(
        rpc_url=rpc_url,
        token=str(token["address"]),
        owner=MORPHO_BLUE_CORE,
        timeout=timeout,
    )
    amount = _human(raw, int(token["decimals"]))
    return {
        "protocol": "morpho_blue",
        "source_kind": "flash_loan",
        "display_name": f"Morpho Blue {token['symbol']}",
        "provider": "Morpho Blue Core",
        "provider_address": MORPHO_BLUE_CORE,
        "token_symbol": token["symbol"],
        "token_address": token["address"],
        "decimals": token["decimals"],
        "available_liquidity_raw": str(raw),
        "available_liquidity": amount,
        "depth_score_usd": amount * float(token.get("price_hint_usd") or 0),
        "fee_bps": 0,
        "flash_capable": True,
        "flash_borrowable": raw > 0,
        "borrowable": raw > 0,
        "repayment_model": "exact_amount_no_protocol_fee",
        "adapter_hint": "morpho.flashLoan(token, amount, data) -> onMorphoFlashLoan",
        "source": "rh_flash_toolkit_morpho_blue",
    }


def _uniswap_v2_source(*, rpc_url: str, timeout: float) -> dict[str, Any]:
    pair = _uniswap_v2_pair(
        rpc_url=rpc_url,
        factory=UNISWAP_V2_FACTORY,
        token_a=USDG,
        token_b=WETH,
        timeout=timeout,
    )
    if pair.lower() == "0x0000000000000000000000000000000000000000":
        raise RuntimeError("Robinhood Uniswap V2 USDG/WETH pair not found")
    token0 = _pair_token(rpc_url=rpc_url, pair=pair, selector=TOKEN0_SELECTOR, timeout=timeout)
    token1 = _pair_token(rpc_url=rpc_url, pair=pair, selector=TOKEN1_SELECTOR, timeout=timeout)
    reserve0, reserve1 = _pair_reserves(rpc_url=rpc_url, pair=pair, timeout=timeout)
    token_by_address = {str(item["address"]).lower(): item for item in TOKENS.values()}
    token0_meta = token_by_address.get(token0.lower(), {"symbol": "TOKEN0", "decimals": 18, "price_hint_usd": 0})
    token1_meta = token_by_address.get(token1.lower(), {"symbol": "TOKEN1", "decimals": 18, "price_hint_usd": 0})
    token0_amount = _human(reserve0, int(token0_meta["decimals"]))
    token1_amount = _human(reserve1, int(token1_meta["decimals"]))
    return {
        "protocol": "uniswap_v2",
        "source_kind": "flash_swap",
        "display_name": f"Uniswap V2 {token0_meta['symbol']}:{token1_meta['symbol']}",
        "provider": "Uniswap V2 Pair",
        "provider_address": pair,
        "factory_address": UNISWAP_V2_FACTORY,
        "token_symbol": f"{token0_meta['symbol']}:{token1_meta['symbol']}",
        "token_address": pair,
        "token0_symbol": token0_meta["symbol"],
        "token1_symbol": token1_meta["symbol"],
        "token0_address": token0,
        "token1_address": token1,
        "reserve0_raw": str(reserve0),
        "reserve1_raw": str(reserve1),
        "reserve0": token0_amount,
        "reserve1": token1_amount,
        "available_liquidity": max(token0_amount, token1_amount),
        "depth_score_usd": token0_amount * float(token0_meta.get("price_hint_usd") or 0)
        + token1_amount * float(token1_meta.get("price_hint_usd") or 0),
        "fee_bps": 30,
        "flash_capable": True,
        "flash_borrowable": reserve0 > 0 or reserve1 > 0,
        "borrowable": reserve0 > 0 or reserve1 > 0,
        "repayment_model": "uniswap_v2_repay_amount_out_times_1000_div_997_plus_one",
        "adapter_hint": "pair.swap(amount0Out, amount1Out, receiver, data) -> uniswapV2Call",
        "source": "rh_flash_toolkit_uniswap_v2_flash_swap",
    }


def load_robinhood_flash_liquidity_snapshot(
    *,
    rpc_url: str = ROBINHOOD_RPC_URL,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Read Robinhood flash liquidity sources without building or broadcasting transactions."""

    sources: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for symbol in ("USDG", "WETH"):
        try:
            sources.append(_morpho_source(token=TOKENS[symbol], rpc_url=rpc_url, timeout=timeout))
        except Exception as exc:
            errors.append({"source": f"morpho_blue:{symbol}", "error": str(exc)[:240]})
    try:
        sources.append(_uniswap_v2_source(rpc_url=rpc_url, timeout=timeout))
    except Exception as exc:
        errors.append({"source": "uniswap_v2:USDG:WETH", "error": str(exc)[:240]})
    return {
        "ok": not errors or bool(sources),
        "network": "robinhood",
        "chainId": ROBINHOOD_CHAIN_ID,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "morphoBlueCore": MORPHO_BLUE_CORE,
        "uniswapV2Factory": UNISWAP_V2_FACTORY,
        "rpcUrl": rpc_url,
        "sources": sources,
        "errors": errors,
        "evidence": [
            "rh-flash-toolkit",
            "morpho_balanceOf_core",
            "uniswap_v2_getPair_getReserves",
        ],
    }
