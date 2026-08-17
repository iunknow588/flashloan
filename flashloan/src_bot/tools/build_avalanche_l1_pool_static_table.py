"""Build an Avalanche L1 pool static lookup table.

The input discovery cache lists possible pools. This tool keeps every discovered
pool in the long-lived lookup table and annotates each one with current chain
balances plus Quoter quality checks. Runtime callers can then choose the best
5-pool window for a UI signal without losing the full pool universe.

It is read-only: no signer, no deployment, no transaction broadcast.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, getcontext
from pathlib import Path
from typing import Any

from web3 import Web3

SRC_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DISCOVERY_CACHE = SRC_ROOT / "runtime" / "cache" / "avalanche_v3_pools.json"
DEFAULT_OUTPUT = SRC_ROOT / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"
DEFAULT_LATEST_OUTPUT = SRC_ROOT / "runtime" / "state" / "latest_avalanche_l1_pool_static_table.json"
DEFAULT_PRICE_STATE = SRC_ROOT / "runtime" / "state" / "latest_extremes.json"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from core.sensitive_data import redact_sensitive_text
from execution.uniswap_v3 import quote_v3_exact_input_single, snapshot_v3_pool
from market.observer_common import DEFAULT_RPC, parse_rpc_urls

getcontext().prec = 60

ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
]

STABLE_SYMBOLS = {
    "USDC",
    "USDC.E",
    "USDT",
    "USDT.E",
    "USDT",
    "USDE",
    "SUSDE",
    "DAI",
    "DAI.E",
    "FRAX",
    "EURC",
    "USDt".upper(),
}

PRICE_SYMBOL_ALIASES = {
    "AVAX": "WAVAX",
    "WAVAX": "WAVAX",
    "BTC": "BTC.B",
    "BTC.B": "BTC.B",
    "BTC.B".replace(".", "").upper(): "BTC.B",
    "BTCB": "BTC.B",
    "WBTC.E": "WBTC.E",
    "WBTC": "WBTC.E",
    "ETH": "WETH.E",
    "WETH": "WETH.E",
    "WETH.E": "WETH.E",
    "AAVE": "AAVE.E",
    "AAVE.E": "AAVE.E",
    "LINK": "LINK.E",
    "LINK.E": "LINK.E",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _decimal_text(value: Decimal | None, places: int = 12) -> str | None:
    if value is None:
        return None
    quant = Decimal(10) ** -places
    if value == 0:
        return "0"
    if abs(value) >= Decimal("1000"):
        return format(value.quantize(Decimal("0.000001")), "f").rstrip("0").rstrip(".")
    return format(value.quantize(quant), "f").rstrip("0").rstrip(".")


def _norm_symbol(symbol: Any) -> str:
    return str(symbol or "").strip().upper()


def _canonical_price_symbol(symbol: Any) -> str:
    text = _norm_symbol(symbol)
    if text in STABLE_SYMBOLS:
        return text
    return PRICE_SYMBOL_ALIASES.get(text, text)


def _is_stable(symbol: Any) -> bool:
    return _norm_symbol(symbol) in STABLE_SYMBOLS


def _token_price_key(symbol: Any) -> str:
    text = _canonical_price_symbol(symbol)
    if text in STABLE_SYMBOLS:
        return text
    return text


def _load_price_refs(path: Path) -> dict[str, Decimal]:
    prices: dict[str, Decimal] = {symbol: Decimal("1") for symbol in STABLE_SYMBOLS}
    if not path.exists():
        return prices
    try:
        payload = _read_json(path)
    except Exception:
        return prices
    rows = []
    for key in ("basket", "top", "bottom"):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        base = symbol
        for suffix in ("USDT", "USDC", "FDUSD", "BUSD", "TUSD"):
            if base.endswith(suffix) and base not in {"USDT", "USDC"}:
                base = base[: -len(suffix)]
                break
        price = _decimal(row.get("current_price") if row.get("current_price") is not None else row.get("end_price"))
        if price is None or price <= 0:
            continue
        key = _token_price_key(base)
        prices[key] = price
        if key == "BTC.B":
            prices["WBTC.E"] = price
        elif key == "WETH.E":
            prices["ETH"] = price
        elif key == "WAVAX":
            prices["AVAX"] = price
    return prices


def _rpc_candidates(rpc_url: str = "") -> list[str]:
    candidates = [rpc_url.strip()] if rpc_url.strip() else []
    for name in ("AVALANCHE_RPC_URL", "AVALANCHE_RPC"):
        value = os.getenv(name, "").strip()
        if value:
            candidates.append(value)
    candidates.extend(parse_rpc_urls(os.getenv("AVALANCHE_RPCS", "")))
    candidates.append(DEFAULT_RPC)
    unique: list[str] = []
    for item in candidates:
        if item and item not in unique:
            unique.append(item)
    return unique


def _connect(rpc_url: str = "") -> tuple[Web3, str]:
    errors: list[str] = []
    for candidate in _rpc_candidates(rpc_url):
        try:
            w3 = Web3(Web3.HTTPProvider(candidate, request_kwargs={"timeout": 20}))
            if int(w3.eth.chain_id) != 43114:
                errors.append("not_avalanche_chain")
                continue
            _ = int(w3.eth.block_number)
            return w3, candidate
        except Exception as exc:
            errors.append(redact_sensitive_text(exc))
    raise RuntimeError("no usable Avalanche RPC: " + "; ".join(errors[:3]))


def _pool_address(row: dict[str, Any]) -> str:
    return str(row.get("pool") or row.get("pool_address") or row.get("address") or "").strip()


def _effective_rows(entry: dict[str, Any]) -> list[dict[str, Any]]:
    rows = entry.get("pools") if isinstance(entry.get("pools"), list) else [entry]
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if int(row.get("adapterKind", row.get("adapter_kind", 1)) or 0) != 1:
            continue
        if not _pool_address(row):
            continue
        result.append(row)
    return result


def _token_balance(w3: Web3, token: str, pool: str) -> int:
    contract = w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20_ABI)
    return int(contract.functions.balanceOf(Web3.to_checksum_address(pool)).call())


def _amount_decimal(raw: int, decimals: int) -> Decimal:
    return Decimal(raw) / (Decimal(10) ** int(decimals))


def _raw_from_decimal(amount: Decimal, decimals: int) -> int:
    return max(1, int((amount * (Decimal(10) ** int(decimals))).to_integral_value()))


def _price_for(prices: dict[str, Decimal], symbol: str) -> Decimal | None:
    if _is_stable(symbol):
        return Decimal("1")
    return prices.get(_token_price_key(symbol))


def _quote_direction(
    *,
    w3: Web3,
    quoter: str,
    token_in: str,
    token_out: str,
    token_in_symbol: str,
    token_out_symbol: str,
    token_in_decimals: int,
    token_out_decimals: int,
    fee: int,
    prices: dict[str, Decimal],
    probe_usd: Decimal,
) -> dict[str, Any]:
    price_in = _price_for(prices, token_in_symbol)
    price_out = _price_for(prices, token_out_symbol)
    if price_in is None or price_out is None or price_in <= 0 or price_out <= 0:
        return {
            "ok": False,
            "reason": "missing_reference_price",
            "tokenInSymbol": token_in_symbol,
            "tokenOutSymbol": token_out_symbol,
        }
    amount_in_units = probe_usd / price_in
    amount_in_raw = _raw_from_decimal(amount_in_units, token_in_decimals)
    try:
        amount_out_raw, _, _, gas = quote_v3_exact_input_single(
            w3,
            quoter,
            token_in,
            token_out,
            fee,
            amount_in_raw,
        )
    except Exception as exc:
        return {
            "ok": False,
            "reason": "quote_failed",
            "error": redact_sensitive_text(exc),
            "tokenInSymbol": token_in_symbol,
            "tokenOutSymbol": token_out_symbol,
            "amountInUsd": _decimal_text(probe_usd, 6),
        }
    amount_in = _amount_decimal(amount_in_raw, token_in_decimals)
    amount_out = _amount_decimal(amount_out_raw, token_out_decimals)
    input_usd = amount_in * price_in
    output_usd = amount_out * price_out
    output_loss_pct = (Decimal("1") - (output_usd / input_usd)) * Decimal("100") if input_usd > 0 else None
    reference_rate = price_in / price_out
    dex_rate = amount_out / amount_in if amount_in > 0 else None
    deviation_pct = ((dex_rate / reference_rate) - Decimal("1")) * Decimal("100") if dex_rate and reference_rate > 0 else None
    return {
        "ok": True,
        "tokenInSymbol": token_in_symbol,
        "tokenOutSymbol": token_out_symbol,
        "tokenIn": Web3.to_checksum_address(token_in),
        "tokenOut": Web3.to_checksum_address(token_out),
        "amountInRaw": str(amount_in_raw),
        "amountOutRaw": str(amount_out_raw),
        "amountIn": _decimal_text(amount_in, 12),
        "amountOut": _decimal_text(amount_out, 12),
        "amountInUsd": _decimal_text(input_usd, 6),
        "amountOutUsd": _decimal_text(output_usd, 6),
        "dexRateOutPerIn": _decimal_text(dex_rate, 18),
        "referenceRateOutPerIn": _decimal_text(reference_rate, 18),
        "deviationPct": _decimal_text(deviation_pct, 6),
        "outputLossPct": _decimal_text(output_loss_pct, 6),
        "gas": str(gas),
    }


def _pool_report(
    *,
    w3: Web3,
    quoter: str,
    entry: dict[str, Any],
    row: dict[str, Any],
    prices: dict[str, Decimal],
    probe_usd: Decimal,
    min_pool_tvl_usd: Decimal,
    min_side_usd: Decimal,
    max_probe_loss_pct: Decimal,
) -> dict[str, Any]:
    pool = _pool_address(row)
    token0 = Web3.to_checksum_address(str(row.get("token0") or entry.get("token0") or entry.get("tokenX") or ""))
    token1 = Web3.to_checksum_address(str(row.get("token1") or entry.get("token1") or entry.get("tokenY") or ""))
    token0_symbol = str(row.get("token0_symbol") or entry.get("token0_symbol") or entry.get("tokenX_symbol") or "")
    token1_symbol = str(row.get("token1_symbol") or entry.get("token1_symbol") or entry.get("tokenY_symbol") or "")
    token0_decimals = int(row.get("token0_decimals") or 18)
    token1_decimals = int(row.get("token1_decimals") or 18)
    fee = int(row.get("fee") or 0)
    snapshot = snapshot_v3_pool(w3, pool)
    balance0_raw = _token_balance(w3, token0, pool)
    balance1_raw = _token_balance(w3, token1, pool)
    balance0 = _amount_decimal(balance0_raw, token0_decimals)
    balance1 = _amount_decimal(balance1_raw, token1_decimals)
    price0 = _price_for(prices, token0_symbol)
    price1 = _price_for(prices, token1_symbol)
    balance0_usd = balance0 * price0 if price0 is not None else None
    balance1_usd = balance1 * price1 if price1 is not None else None
    tvl_usd = balance0_usd + balance1_usd if balance0_usd is not None and balance1_usd is not None else None
    quote01 = _quote_direction(
        w3=w3,
        quoter=quoter,
        token_in=token0,
        token_out=token1,
        token_in_symbol=token0_symbol,
        token_out_symbol=token1_symbol,
        token_in_decimals=token0_decimals,
        token_out_decimals=token1_decimals,
        fee=fee,
        prices=prices,
        probe_usd=probe_usd,
    )
    quote10 = _quote_direction(
        w3=w3,
        quoter=quoter,
        token_in=token1,
        token_out=token0,
        token_in_symbol=token1_symbol,
        token_out_symbol=token0_symbol,
        token_in_decimals=token1_decimals,
        token_out_decimals=token0_decimals,
        fee=fee,
        prices=prices,
        probe_usd=probe_usd,
    )
    reasons = []
    if tvl_usd is None:
        reasons.append("missing_tvl_reference_price")
    elif tvl_usd < min_pool_tvl_usd:
        reasons.append("pool_tvl_below_threshold")
    if balance0_usd is None or balance1_usd is None:
        reasons.append("missing_side_reference_price")
    else:
        if balance0_usd < min_side_usd:
            reasons.append("token0_side_balance_below_threshold")
        if balance1_usd < min_side_usd:
            reasons.append("token1_side_balance_below_threshold")
    for label, quote in (("token0_to_token1", quote01), ("token1_to_token0", quote10)):
        if not quote.get("ok"):
            reasons.append(f"{label}_{quote.get('reason') or 'quote_not_ok'}")
            continue
        loss = _decimal(quote.get("outputLossPct"))
        if loss is None:
            reasons.append(f"{label}_missing_probe_loss")
        elif loss > max_probe_loss_pct:
            reasons.append(f"{label}_probe_loss_above_threshold")
    quality = "pass" if not reasons else "reject"
    return {
        "pool": Web3.to_checksum_address(pool),
        "adapterKind": 1,
        "fee": str(fee),
        "quality": quality,
        "rejectReasons": reasons,
        "token0": token0,
        "token1": token1,
        "token0Symbol": token0_symbol,
        "token1Symbol": token1_symbol,
        "token0Decimals": str(token0_decimals),
        "token1Decimals": str(token1_decimals),
        "liquidity": str(snapshot.liquidity),
        "tick": str(snapshot.tick),
        "sqrtPriceX96": str(snapshot.sqrt_price_x96),
        "balances": {
            "token0Raw": str(balance0_raw),
            "token1Raw": str(balance1_raw),
            "token0": _decimal_text(balance0, 12),
            "token1": _decimal_text(balance1, 12),
            "token0Usd": _decimal_text(balance0_usd, 6),
            "token1Usd": _decimal_text(balance1_usd, 6),
            "tvlUsd": _decimal_text(tvl_usd, 6),
        },
        "quotes": {
            "probeUsd": _decimal_text(probe_usd, 6),
            "token0ToToken1": quote01,
            "token1ToToken0": quote10,
        },
    }


def _quality_sort_key(item: dict[str, Any]) -> tuple[int, Decimal, int]:
    tvl = _decimal(((item.get("balances") or {}).get("tvlUsd"))) or Decimal(0)
    fee = int(item.get("fee") or 0)
    return (0 if item.get("quality") == "pass" else 1, -tvl, fee)


def _compatible_pool_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "adapterKind": 1,
        "pool": item.get("pool"),
        "fee": int(item.get("fee") or 0),
        "liquidity": item.get("liquidity"),
        "tick": int(item.get("tick") or 0),
        "token0": item.get("token0"),
        "token1": item.get("token1"),
        "token0_symbol": item.get("token0Symbol"),
        "token1_symbol": item.get("token1Symbol"),
        "token0_decimals": int(item.get("token0Decimals") or 18),
        "token1_decimals": int(item.get("token1Decimals") or 18),
        "quality": item.get("quality"),
        "rejectReasons": item.get("rejectReasons") or [],
        "tvlUsd": (item.get("balances") or {}).get("tvlUsd"),
        "token0Usd": (item.get("balances") or {}).get("token0Usd"),
        "token1Usd": (item.get("balances") or {}).get("token1Usd"),
        "probeUsd": ((item.get("quotes") or {}).get("probeUsd")),
        "qualitySource": "avalanche_l1_pool_static_table_current_snapshot",
    }


def _pair_key(token_a: str, token_b: str) -> str:
    items = sorted([Web3.to_checksum_address(token_a).lower(), Web3.to_checksum_address(token_b).lower()])
    return f"{items[0]}:{items[1]}"


def _symbol_pair_key(symbol_a: str, symbol_b: str) -> str:
    return ":".join(sorted([_norm_symbol(symbol_a), _norm_symbol(symbol_b)]))


def build_table(
    *,
    discovery_cache_path: Path = DEFAULT_DISCOVERY_CACHE,
    price_state_path: Path = DEFAULT_PRICE_STATE,
    rpc_url: str = "",
    min_pool_tvl_usd: Decimal = Decimal("50000"),
    min_side_usd: Decimal = Decimal("5000"),
    max_probe_loss_pct: Decimal = Decimal("5"),
    probe_usd: Decimal = Decimal("1000"),
) -> dict[str, Any]:
    load_env_files(__file__)
    cache = _read_json(discovery_cache_path)
    prices = _load_price_refs(price_state_path)
    w3, selected_rpc = _connect(rpc_url)
    quoter = Web3.to_checksum_address(str(cache.get("quoter") or "0xbe0F5544EC67e9B3b2D979aaA43f18Fd87E6257F"))
    block_number = int(w3.eth.block_number)
    pair_reports = []
    lookup_by_address_pair: dict[str, Any] = {}
    lookup_by_symbol_pair: dict[str, Any] = {}
    compatible_entries: list[dict[str, Any]] = []
    pool_count = 0
    passed_count = 0
    rejected_count = 0
    started = time.time()
    for entry in cache.get("pools") or []:
        if not isinstance(entry, dict):
            continue
        token_x = str(entry.get("tokenX") or entry.get("token_x") or "").strip()
        token_y = str(entry.get("tokenY") or entry.get("token_y") or "").strip()
        token_x_symbol = str(entry.get("tokenX_symbol") or entry.get("token_x_symbol") or "").strip()
        token_y_symbol = str(entry.get("tokenY_symbol") or entry.get("token_y_symbol") or "").strip()
        rows = _effective_rows(entry)
        pool_reports = []
        for row in rows:
            pool_count += 1
            try:
                report = _pool_report(
                    w3=w3,
                    quoter=quoter,
                    entry=entry,
                    row=row,
                    prices=prices,
                    probe_usd=probe_usd,
                    min_pool_tvl_usd=min_pool_tvl_usd,
                    min_side_usd=min_side_usd,
                    max_probe_loss_pct=max_probe_loss_pct,
                )
            except Exception as exc:
                report = {
                    "pool": _pool_address(row),
                    "adapterKind": 1,
                    "fee": str(row.get("fee") or ""),
                    "quality": "reject",
                    "rejectReasons": ["pool_quality_probe_failed"],
                    "error": redact_sensitive_text(exc),
                }
            if report.get("quality") == "pass":
                passed_count += 1
            else:
                rejected_count += 1
            pool_reports.append(report)
        pool_reports.sort(key=_quality_sort_key)
        accepted = [item for item in pool_reports if item.get("quality") == "pass"]
        preferred = pool_reports[:4]
        execution = pool_reports[:5]
        strict_quality_rows = [_compatible_pool_row(item) for item in accepted]
        preferred_rows = [_compatible_pool_row(item) for item in preferred]
        execution_rows = [_compatible_pool_row(item) for item in execution]
        pair_report = {
            "symbolPair": f"{token_x_symbol}:{token_y_symbol}",
            "addressPair": _pair_key(token_x, token_y) if token_x and token_y else "",
            "tokenX": Web3.to_checksum_address(token_x) if token_x else "",
            "tokenY": Web3.to_checksum_address(token_y) if token_y else "",
            "tokenXSymbol": token_x_symbol,
            "tokenYSymbol": token_y_symbol,
            "discoveredPoolCount": str(len(pool_reports)),
            "strictQualityPoolCount": str(len(strict_quality_rows)),
            "preferredPoolCount": str(len(preferred_rows)),
            "executionPoolCount": str(len(execution_rows)),
            "acceptedPoolCount": str(len(strict_quality_rows)),
            "preferredPoolShortfall": str(max(0, 4 - len(preferred_rows))),
            "strictQualityPoolShortfall": str(max(0, 4 - len(strict_quality_rows))),
            "hasFourPreferredPools": len(preferred_rows) >= 4,
            "hasFourStrictQualityPools": len(strict_quality_rows) >= 4,
            "acceptedPools": [
                {
                    "adapterKind": 1,
                    "pool": item.get("pool"),
                    "fee": item.get("fee"),
                    "tvlUsd": (item.get("balances") or {}).get("tvlUsd"),
                    "token0Usd": (item.get("balances") or {}).get("token0Usd"),
                    "token1Usd": (item.get("balances") or {}).get("token1Usd"),
                    "liquidity": item.get("liquidity"),
                    "tick": item.get("tick"),
                }
                for item in accepted
            ],
            "strictQualityPools": strict_quality_rows,
            "preferredPools": preferred_rows,
            "executionPools": execution_rows,
            "allPools": pool_reports,
        }
        pair_reports.append(pair_report)
        if token_x and token_y and pool_reports:
            compatible_rows = [_compatible_pool_row(item) for item in pool_reports]
            compatible_entries.append(
                {
                    "tokenX_symbol": token_x_symbol,
                    "tokenY_symbol": token_y_symbol,
                    "tokenX": Web3.to_checksum_address(token_x),
                    "tokenY": Web3.to_checksum_address(token_y),
                    "factory": str(cache.get("factory") or ""),
                    "pools": compatible_rows,
                    "qualityPools": strict_quality_rows,
                    "preferredPools": preferred_rows,
                    "executionPools": execution_rows,
                    "qualityFiltered": False,
                    "allPoolsIncluded": True,
                    "runtimeSelection": "select up to 4 preferred pools for UI slot preview; select up to 5 execution pools for contract window",
                    "sourceDiscoveryCache": str(discovery_cache_path),
                }
            )
            lookup_item = {
                "symbolPair": pair_report["symbolPair"],
                "addressPair": pair_report["addressPair"],
                "pairKey": f"{token_x_symbol}:{token_y_symbol}",
                "pairKeyReversed": f"{token_y_symbol}:{token_x_symbol}",
                "tokenX": pair_report["tokenX"],
                "tokenY": pair_report["tokenY"],
                "tokenXSymbol": token_x_symbol,
                "tokenYSymbol": token_y_symbol,
                "pools": compatible_rows,
                "qualityPools": strict_quality_rows,
                "preferredPools": preferred_rows,
                "executionPools": execution_rows,
                "poolCount": str(len(compatible_rows)),
                "qualityPoolCount": str(len(strict_quality_rows)),
                "preferredPoolCount": str(len(preferred_rows)),
                "executionPoolCount": str(len(execution_rows)),
                "source": "avalanche_l1_pool_static_table_full_with_quality_annotations",
            }
            lookup_by_address_pair[pair_report["addressPair"]] = lookup_item
            lookup_by_symbol_pair[_symbol_pair_key(token_x_symbol, token_y_symbol)] = lookup_item
            lookup_by_symbol_pair[f"{_norm_symbol(token_x_symbol)}:{_norm_symbol(token_y_symbol)}"] = lookup_item
            lookup_by_symbol_pair[f"{_norm_symbol(token_y_symbol)}:{_norm_symbol(token_x_symbol)}"] = lookup_item
    pair_reports.sort(key=lambda item: item.get("symbolPair") or "")
    return {
        "schemaVersion": 1,
        "mode": "avalanche_l1_full_pool_static_table_with_quality_annotations",
        "generatedAt": _utc_now_iso(),
        "chainId": 43114,
        "network": "avalanche",
        "l1": "Avalanche C-Chain",
        "sourceDiscoveryCache": str(discovery_cache_path),
        "sourceDiscoveryBlockNumber": str(cache.get("block_number") or ""),
        "sourceDiscoveryFetchedAt": str(cache.get("fetched_at") or ""),
        "generatedBlockNumber": str(block_number),
        "rpcUrl": selected_rpc,
        "factory": str(cache.get("factory") or ""),
        "router": str(cache.get("router") or ""),
        "quoter": quoter,
        "usdc": str(cache.get("usdc") or ""),
        "feeTiers": cache.get("fee_tiers") or [],
        "filters": {
            "minPoolTvlUsd": _decimal_text(min_pool_tvl_usd, 6),
            "minSideUsd": _decimal_text(min_side_usd, 6),
            "maxProbeLossPct": _decimal_text(max_probe_loss_pct, 6),
            "probeUsd": _decimal_text(probe_usd, 6),
            "stableSymbols": sorted(STABLE_SYMBOLS),
            "notes": [
                "TVL uses current pool token balances multiplied by local reference prices.",
                "Probe loss uses Quoter exactInput in both directions for a probe-sized USD notional.",
                "Lookup keys use exact token addresses to avoid BTC.b/WBTC.e alias confusion.",
                "All discovered pools are retained; quality filters only annotate and rank runtime candidates.",
            ],
        },
        "priceReferences": {key: _decimal_text(value, 12) for key, value in sorted(prices.items())},
        "summary": {
            "discoveredPairCount": str(len(pair_reports)),
            "discoveredPoolCount": str(pool_count),
            "staticPairCount": str(len(compatible_entries)),
            "staticPoolCount": str(pool_count),
            "preferredPoolCount": str(sum(int(item.get("preferredPoolCount") or 0) for item in pair_reports)),
            "executionPoolCount": str(sum(int(item.get("executionPoolCount") or 0) for item in pair_reports)),
            "strictQualityPoolCount": str(sum(int(item.get("strictQualityPoolCount") or 0) for item in pair_reports)),
            "preferredPairCount": str(sum(1 for item in pair_reports if int(item.get("preferredPoolCount") or 0) >= 4)),
            "strictQualityPairCount": str(sum(1 for item in pair_reports if int(item.get("strictQualityPoolCount") or 0) >= 4)),
            "preferredPoolShortfallPairCount": str(sum(1 for item in pair_reports if int(item.get("preferredPoolShortfall") or 0) > 0)),
            "qualityPairCount": str(sum(1 for item in pair_reports if int(item.get("acceptedPoolCount") or 0) > 0)),
            "qualityPoolCount": str(passed_count),
            "qualityRejectedPoolCount": str(rejected_count),
            "acceptedPairCount": str(sum(1 for item in pair_reports if int(item.get("acceptedPoolCount") or 0) > 0)),
            "acceptedPoolCount": str(sum(int(item.get("acceptedPoolCount") or 0) for item in pair_reports)),
            "rejectedPoolCount": str(rejected_count),
            "elapsedSeconds": _decimal_text(Decimal(str(time.time() - started)), 3),
        },
        "lookup": {
            "byAddressPair": lookup_by_address_pair,
            "bySymbolPair": lookup_by_symbol_pair,
        },
        "pools": compatible_entries,
        "pairs": pair_reports,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discovery-cache", type=Path, default=DEFAULT_DISCOVERY_CACHE)
    parser.add_argument("--price-state", type=Path, default=DEFAULT_PRICE_STATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--latest-output", type=Path, default=DEFAULT_LATEST_OUTPUT)
    parser.add_argument("--rpc-url", default="")
    parser.add_argument("--min-pool-tvl-usd", default="50000")
    parser.add_argument("--min-side-usd", default="5000")
    parser.add_argument("--max-probe-loss-pct", default="5")
    parser.add_argument("--probe-usd", default="1000")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_table(
        discovery_cache_path=args.discovery_cache,
        price_state_path=args.price_state,
        rpc_url=args.rpc_url,
        min_pool_tvl_usd=Decimal(str(args.min_pool_tvl_usd)),
        min_side_usd=Decimal(str(args.min_side_usd)),
        max_probe_loss_pct=Decimal(str(args.max_probe_loss_pct)),
        probe_usd=Decimal(str(args.probe_usd)),
    )
    _write_json_atomic(args.output, report)
    _write_json_atomic(args.latest_output, report)
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output),
                "latestOutput": str(args.latest_output),
                "summary": report["summary"],
                "filters": report["filters"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
