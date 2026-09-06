from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from web3 import Web3

from bot.execution.venus_abis import (
    COMPTROLLER_ABI,
    ERC20_ABI,
    NATIVE_BNB_PLACEHOLDER,
    VENUS_CORE_COMPTROLLER_ADDRESS,
    VENUS_CORE_MARKET_ADDRESSES,
    VENUS_OFFICIAL_MARKETS_SOURCE,
    VENUS_ORACLE_ABI,
    VTOKEN_ABI,
    WBNB_ADDRESS,
)


DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR = Path(__file__).resolve().parents[3] / "data_info" / "BNB" / "liquidation"
MANTISSA = 10**18
STABLE_SYMBOLS = {"USDT", "USDC", "BUSD", "DAI", "FDUSD"}
HIGH_LIQUIDITY_SYMBOLS = {"BNB", "WBNB", "USDT", "USDC", "BTCB", "BTC", "ETH", "FDUSD"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def venus_core_config_from_env() -> dict[str, Any]:
    return {
        "network": "bnb",
        "chain_id": 56,
        "protocol": "venus_core",
        "comptroller_address": os.getenv("VENUS_CORE_COMPTROLLER_ADDRESS", VENUS_CORE_COMPTROLLER_ADDRESS).strip()
        or VENUS_CORE_COMPTROLLER_ADDRESS,
        "oracle_address": os.getenv("VENUS_CORE_ORACLE_ADDRESS", "").strip(),
        "source": VENUS_OFFICIAL_MARKETS_SOURCE,
    }


def _contract(w3: Web3, address: str, abi: list[dict]):
    return w3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)


def _call(function: Any, default: Any = None) -> Any:
    try:
        return function.call()
    except Exception:
        return default


def _contract_call(contract: Any, name: str, *args: Any, default: Any = None) -> Any:
    try:
        fn = getattr(contract.functions, name)(*args)
    except Exception:
        return default
    return _call(fn, default=default)


def _checksum_or_empty(value: Any) -> str:
    try:
        return Web3.to_checksum_address(str(value))
    except Exception:
        return ""


def _mantissa_float(value: Any) -> float:
    try:
        return float(int(value or 0)) / float(MANTISSA)
    except Exception:
        return 0.0


def _protocol_share_mantissa(comptroller: Any) -> int:
    raw = _contract_call(comptroller, "protocolSeizeShareMantissa", default=None)
    if raw is not None:
        try:
            return int(raw)
        except Exception:
            return 0
    fallback = os.getenv("VENUS_PROTOCOL_SHARE_MANTISSA", "50000000000000000").strip()
    try:
        return int(fallback)
    except ValueError:
        return 0


def _liquidation_incentive_mantissa(comptroller: Any) -> tuple[int, str]:
    raw = _contract_call(comptroller, "liquidationIncentiveMantissa", default=None)
    try:
        value = int(raw or 0)
    except Exception:
        value = 0
    if value > 0:
        return value, "comptroller"
    fallback = os.getenv("VENUS_LIQUIDATION_INCENTIVE_MANTISSA", "1100000000000000000").strip()
    try:
        return int(fallback), "fallback_env_or_venus_default"
    except ValueError:
        return 1_100_000_000_000_000_000, "fallback_venus_default"


def _token_symbol_from_vtoken(vtoken_symbol: str) -> str:
    symbol = str(vtoken_symbol or "").strip()
    if symbol.lower() == "vbnb":
        return "BNB"
    if symbol.lower().startswith("v") and len(symbol) > 1:
        return symbol[1:].upper()
    return symbol.upper()


def _market_label(symbol: str) -> dict[str, bool]:
    normalized = str(symbol or "").upper()
    return {
        "stable": normalized in STABLE_SYMBOLS,
        "high_liquidity": normalized in HIGH_LIQUIDITY_SYMBOLS,
        "long_tail": normalized not in HIGH_LIQUIDITY_SYMBOLS,
    }


def _read_oracle_address(comptroller: Any, configured_oracle: str = "") -> str:
    checksum = _checksum_or_empty(configured_oracle)
    if checksum:
        return checksum
    return _checksum_or_empty(_contract_call(comptroller, "oracle", default=""))


def _read_vtoken_underlying(w3: Web3, vtoken: Any, vtoken_symbol: str) -> dict[str, Any]:
    if str(vtoken_symbol or "").lower() == "vbnb":
        return {
            "underlying_address": NATIVE_BNB_PLACEHOLDER,
            "wrapped_underlying_address": Web3.to_checksum_address(WBNB_ADDRESS),
            "underlying_symbol": "BNB",
            "underlying_decimals": 18,
            "underlying_source": "native_bnb",
        }
    underlying_address = _checksum_or_empty(_contract_call(vtoken, "underlying", default=""))
    if not underlying_address:
        return {
            "underlying_address": "",
            "wrapped_underlying_address": "",
            "underlying_symbol": _token_symbol_from_vtoken(vtoken_symbol),
            "underlying_decimals": 18,
            "underlying_source": "vtoken_symbol_fallback",
        }
    token = _contract(w3, underlying_address, ERC20_ABI)
    symbol = _contract_call(token, "symbol", default="") or _token_symbol_from_vtoken(vtoken_symbol)
    decimals = int(_contract_call(token, "decimals", default=18) or 18)
    return {
        "underlying_address": underlying_address,
        "wrapped_underlying_address": underlying_address,
        "underlying_symbol": str(symbol).upper(),
        "underlying_decimals": decimals,
        "underlying_source": "erc20_underlying",
    }


def _read_vtoken_market(
    w3: Web3,
    comptroller: Any,
    oracle: Any | None,
    address: str,
    *,
    close_factor_mantissa: int,
    liquidation_incentive_mantissa: int,
) -> dict[str, Any]:
    checksum_address = Web3.to_checksum_address(address)
    vtoken = _contract(w3, checksum_address, VTOKEN_ABI)
    vtoken_symbol = str(_contract_call(vtoken, "symbol", default="")).strip() or "vUNKNOWN"
    market_info = _contract_call(comptroller, "markets", checksum_address, default=())
    underlying = _read_vtoken_underlying(w3, vtoken, vtoken_symbol)
    oracle_price = _contract_call(oracle, "getUnderlyingPrice", checksum_address, default=0) if oracle is not None else 0
    collateral_factor_mantissa = int(market_info[1]) if isinstance(market_info, (list, tuple)) and len(market_info) > 1 else 0
    is_listed = bool(market_info[0]) if isinstance(market_info, (list, tuple)) and len(market_info) > 0 else False
    labels = _market_label(str(underlying.get("underlying_symbol") or ""))
    return {
        "vtoken_address": checksum_address,
        "vtoken_symbol": vtoken_symbol,
        "vtoken_name": _contract_call(vtoken, "name", default="") or "",
        "vtoken_decimals": int(_contract_call(vtoken, "decimals", default=8) or 8),
        **underlying,
        "is_listed": is_listed,
        "collateral_factor_mantissa": collateral_factor_mantissa,
        "collateral_factor": _mantissa_float(collateral_factor_mantissa),
        "close_factor_mantissa": int(close_factor_mantissa),
        "close_factor": _mantissa_float(close_factor_mantissa),
        "liquidation_incentive_mantissa": int(liquidation_incentive_mantissa),
        "liquidation_incentive": _mantissa_float(liquidation_incentive_mantissa),
        "exchange_rate_stored": int(_contract_call(vtoken, "exchangeRateStored", default=0) or 0),
        "borrow_index": int(_contract_call(vtoken, "borrowIndex", default=0) or 0),
        "total_supply": int(_contract_call(vtoken, "totalSupply", default=0) or 0),
        "total_borrows": int(_contract_call(vtoken, "totalBorrows", default=0) or 0),
        "cash": int(_contract_call(vtoken, "getCash", default=0) or 0),
        "oracle_price": int(oracle_price or 0),
        "oracle_price_usd": (
            float(oracle_price or 0) / float(10 ** max(0, 36 - int(underlying.get("underlying_decimals") or 18)))
            if int(oracle_price or 0) > 0
            else 0.0
        ),
        "stable": labels["stable"],
        "high_liquidity": labels["high_liquidity"],
        "long_tail": labels["long_tail"],
        "paused": None,
        "borrow_disabled": None,
        "collateral_disabled": not is_listed or collateral_factor_mantissa <= 0,
        "protocol_share_mantissa": None,
    }


def build_venus_market_static_table(
    w3: Web3,
    *,
    comptroller_address: str = VENUS_CORE_COMPTROLLER_ADDRESS,
    oracle_address: str = "",
    market_addresses: Iterable[str] | None = None,
    rpc_url: str = "",
    limit: int = 0,
) -> dict[str, Any]:
    comptroller = _contract(w3, comptroller_address, COMPTROLLER_ABI)
    close_factor_mantissa = int(_contract_call(comptroller, "closeFactorMantissa", default=0) or 0)
    liquidation_incentive_mantissa, liquidation_incentive_source = _liquidation_incentive_mantissa(comptroller)
    protocol_share_mantissa = int(_protocol_share_mantissa(comptroller))
    resolved_oracle_address = _read_oracle_address(comptroller, oracle_address)
    oracle = _contract(w3, resolved_oracle_address, VENUS_ORACLE_ABI) if resolved_oracle_address else None
    addresses = list(market_addresses or _contract_call(comptroller, "getAllMarkets", default=[]) or [])
    if not addresses:
        addresses = list(VENUS_CORE_MARKET_ADDRESSES.values())
    if limit and limit > 0:
        addresses = addresses[: int(limit)]
    markets = [
        _read_vtoken_market(
            w3,
            comptroller,
            oracle,
            address,
            close_factor_mantissa=close_factor_mantissa,
            liquidation_incentive_mantissa=liquidation_incentive_mantissa,
        )
        for address in addresses
    ]
    return {
        "network": "bnb",
        "chain_id": 56,
        "protocol": "venus_core",
        "source": VENUS_OFFICIAL_MARKETS_SOURCE,
        "generated_at": utc_now(),
        "block_number": int(getattr(w3.eth, "block_number", 0) or 0),
        "rpc_url": rpc_url,
        "comptroller_address": Web3.to_checksum_address(comptroller_address),
        "oracle_address": resolved_oracle_address,
        "close_factor_mantissa": close_factor_mantissa,
        "liquidation_incentive_mantissa": liquidation_incentive_mantissa,
        "liquidation_incentive_source": liquidation_incentive_source,
        "protocol_share_mantissa": protocol_share_mantissa,
        "market_count": len(markets),
        "markets": markets,
    }


def market_static_table_markdown(table: dict[str, Any]) -> str:
    lines = [
        "# Venus Core Pool Market Static Table",
        "",
        f"- Network: `{table.get('network')}`",
        f"- Chain ID: `{table.get('chain_id')}`",
        f"- Protocol: `{table.get('protocol')}`",
        f"- Comptroller: `{table.get('comptroller_address')}`",
        f"- Oracle: `{table.get('oracle_address') or '-'}`",
        f"- Block: `{table.get('block_number')}`",
        f"- Generated: `{table.get('generated_at')}`",
        f"- Protocol share: `{float(table.get('protocol_share_mantissa') or 0) / 1e18:.4f}`",
        f"- Source: {table.get('source')}",
        "",
        "| vToken | Underlying | Collateral Factor | Total Borrows | Cash | Flags |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in table.get("markets") or []:
        flags = []
        if row.get("stable"):
            flags.append("stable")
        if row.get("high_liquidity"):
            flags.append("high_liquidity")
        if row.get("long_tail"):
            flags.append("long_tail")
        if row.get("collateral_disabled"):
            flags.append("collateral_disabled")
        lines.append(
            "| {vtoken} | {underlying} | {factor:.4f} | {borrows} | {cash} | {flags} |".format(
                vtoken=row.get("vtoken_symbol") or row.get("vtoken_address"),
                underlying=row.get("underlying_symbol") or row.get("underlying_address") or "-",
                factor=float(row.get("collateral_factor") or 0.0),
                borrows=row.get("total_borrows") or 0,
                cash=row.get("cash") or 0,
                flags=", ".join(flags) or "-",
            )
        )
    return "\n".join(lines) + "\n"


def write_venus_market_static_table(table: dict[str, Any], output_dir: str | Path = DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR) -> dict[str, str]:
    raw_dir = Path(output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    json_path = raw_dir / "venus_market_static_table.json"
    md_path = raw_dir / "venus_market_static_table.md"
    json_path.write_text(json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(market_static_table_markdown(table), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}
