from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from core.contracts import source_metadata


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _is_zero_address(value: object) -> bool:
    text = str(value or "").strip().lower()
    if not text.startswith("0x"):
        return False
    try:
        return int(text, 16) == 0
    except ValueError:
        return False


def _v4_zero_address_allowed(row: dict[str, Any]) -> bool:
    """V4 PoolKeys legitimately use the zero address as plain-ETH sentinel."""
    protocol = str(row.get("protocol") or row.get("dex") or "").strip().lower()
    return protocol in {"uniswap_v4", "v4"}


STABLE_SYMBOLS = {
    "AUSD",
    "BUSD",
    "DAI",
    "DAI.E",
    "EURC",
    "FDUSD",
    "FRAX",
    "FRAX.E",
    "GHO",
    "MIM",
    "PYUSD",
    "RLUSD",
    "SUSDE",
    "TUSD",
    "USDC",
    "USDC.E",
    "USDD",
    "USDE",
    "USDG",
    "USDF",
    "USD1",
    "USDT",
    "USDT.E",
}
READABLE_SYMBOL_RE = re.compile(r"^[A-Z0-9.$_\-\u4e00-\u9fff()]+$")
MAX_READABLE_SYMBOL_LENGTH = 24


def read_json_object(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _number(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else None


def _int_sort_value(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _norm_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _stable_symbol(symbol: object) -> bool:
    normalized = str(symbol or "").strip().upper()
    bridge_base = normalized[:-2] if normalized.endswith(".E") else normalized
    return normalized in STABLE_SYMBOLS or bridge_base in STABLE_SYMBOLS or normalized.endswith("USD")


def _readable_symbol(symbol: object) -> bool:
    text = str(symbol or "").strip().upper()
    return bool(text) and len(text) <= MAX_READABLE_SYMBOL_LENGTH and bool(READABLE_SYMBOL_RE.fullmatch(text))


def pair_classification(token0_symbol: object, token1_symbol: object) -> dict[str, Any]:
    token0 = str(token0_symbol or "").strip().upper()
    token1 = str(token1_symbol or "").strip().upper()
    token0_stable = _stable_symbol(token0)
    token1_stable = _stable_symbol(token1)
    stable_symbols = [symbol for symbol, stable in ((token0, token0_stable), (token1, token1_stable)) if symbol and stable]
    non_stable_symbols = [symbol for symbol, stable in ((token0, token0_stable), (token1, token1_stable)) if symbol and not stable]
    if token0_stable and token1_stable:
        pair_class = "stable_pair"
        label = "stable pair"
        route_role = "stable_anchor_pool"
    elif token0_stable or token1_stable:
        pair_class = "stable_to_non_stable_pair"
        label = "stable to non-stable pool"
        route_role = "flashloan_entry_or_exit_leg"
    else:
        pair_class = "non_stable_pair"
        label = "non-stable pool"
        route_role = "middle_volatility_leg"
    return {
        "pairClass": pair_class,
        "pairClassLabel": label,
        "routeRole": route_role,
        "token0IsStable": token0_stable,
        "token1IsStable": token1_stable,
        "stableSymbols": stable_symbols,
        "nonStableSymbols": non_stable_symbols,
    }


def pair_symbol_universe(pair_reports: list[dict[str, Any]]) -> list[str]:
    symbols: set[str] = set()
    for pair in pair_reports:
        if not isinstance(pair, dict):
            continue
        for key in ("tokenXSymbol", "tokenYSymbol"):
            symbol = _norm_symbol(pair.get(key))
            if symbol:
                symbols.add(symbol)
        for pool_key in ("preferredPools", "strictQualityPools", "executionPools"):
            for pool in pair.get(pool_key) or []:
                if not isinstance(pool, dict):
                    continue
                for key in ("token0Symbol", "token1Symbol"):
                    symbol = _norm_symbol(pool.get(key))
                    if symbol:
                        symbols.add(symbol)
    return sorted(symbols)


def _depth_band(tvl_usd: object, *, observed_liquidity: object = None) -> dict[str, str]:
    tvl = _number(tvl_usd)
    if tvl is not None and tvl >= 1_000_000:
        return {"level": "deep", "label": "deep"}
    if tvl is not None and tvl >= 100_000:
        return {"level": "medium", "label": "medium"}
    if tvl is not None and tvl >= 10_000:
        return {"level": "shallow", "label": "shallow"}
    if tvl is not None and tvl > 0:
        return {"level": "dust", "label": "dust"}
    if str(observed_liquidity or "").strip():
        return {"level": "observed", "label": "on-chain liquidity observed"}
    return {"level": "unavailable", "label": "unavailable"}


def _pool_address(row: dict[str, Any]) -> str:
    return str(row.get("pool") or row.get("pool_address") or row.get("address") or "").strip()


def _token_address(value: object) -> str:
    text = str(value or "").strip()
    return text.lower() if text.startswith("0x") and len(text) == 42 else ""


def _unordered_address_pair(token0: object, token1: object) -> str:
    left = _token_address(token0)
    right = _token_address(token1)
    if not left or not right:
        return ""
    return ":".join(sorted((left, right)))


def _reject_reason_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for reason in row.get("rejectReasons") or row.get("reject_reasons") or []:
            text = str(reason or "").strip()
            if text:
                counts[text] = counts.get(text, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _pool_identity_key(row: dict[str, Any]) -> str:
    pool_id = _pool_address(row).lower()
    if pool_id:
        return f"pool:{pool_id}"
    token0 = _token_address(row.get("token0"))
    token1 = _token_address(row.get("token1"))
    if token0 and token1:
        return f"addrpair:{_unordered_address_pair(token0, token1)}"
    token0_symbol = str(row.get("token0Symbol") or row.get("token0_symbol") or "").strip().upper()
    token1_symbol = str(row.get("token1Symbol") or row.get("token1_symbol") or "").strip().upper()
    if token0_symbol and token1_symbol:
        return f"symbolpair:{':'.join(sorted((token0_symbol, token1_symbol)))}"
    return f"row:{id(row)}"


def _unique_pool_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _pool_identity_key(row)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _pair_all_pool_rows(pair: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("allPools", "preferredPools", "strictQualityPools", "executionPools", "rejectedPools"):
        for row in pair.get(key) or []:
            if isinstance(row, dict) and _pool_address(row):
                rows.append(row)
    if rows:
        return _unique_pool_rows(rows)
    return []


def _anchor_queue_signature(
    *,
    anchor_symbol: str,
    direction: str,
    row: dict[str, Any],
    counter_symbol: str,
    counter_address: str,
) -> str:
    pool_id = _pool_address(row).lower()
    token0 = _token_address(row.get("token0"))
    token1 = _token_address(row.get("token1"))
    token_pair = _unordered_address_pair(token0, token1) or ":".join(
        sorted(
            [
                str(anchor_symbol or "").strip().upper(),
                str(counter_symbol or "").strip().upper(),
            ]
        )
    )
    protocol = str(row.get("protocol") or row.get("dex") or "").strip().lower()
    fee = str(row.get("fee") or "").strip().lower()
    return ":".join(
        [
            str(direction or "").strip().lower() or "yc",
            str(anchor_symbol or "").strip().upper(),
            str(counter_symbol or "").strip().upper(),
            counter_address.lower() if counter_address else "",
            token_pair,
            protocol,
            fee,
            pool_id,
        ]
    )


def _pool_quote_summary(row: dict[str, Any]) -> dict[str, Any]:
    quotes = row.get("quotes") if isinstance(row.get("quotes"), dict) else {}
    quote01 = quotes.get("token0ToToken1") if isinstance(quotes.get("token0ToToken1"), dict) else {}
    quote10 = quotes.get("token1ToToken0") if isinstance(quotes.get("token1ToToken0"), dict) else {}
    return {
        "probeUsd": quotes.get("probeUsd"),
        "token0ToToken1": {"ok": bool(quote01.get("ok")), "reason": quote01.get("reason")},
        "token1ToToken0": {"ok": bool(quote10.get("ok")), "reason": quote10.get("reason")},
    }


def _protocol_key(value: object) -> str:
    text = str(value or "").strip().lower()
    return {"1": "uniswap_v3", "v2": "uniswap_v2", "v3": "uniswap_v3", "v4": "uniswap_v4"}.get(text, text)


def _candidate_pool_has_depth(row: dict[str, Any]) -> bool:
    balances = row.get("balances") if isinstance(row.get("balances"), dict) else {}
    tvl = _number(balances.get("tvlUsd") if balances else row.get("tvlUsd"))
    if tvl is not None and tvl > 0:
        return True
    liquidity = str(row.get("liquidity") or "").strip()
    if liquidity:
        try:
            return int(liquidity) > 0
        except ValueError:
            return True
    return False


def _candidate_pool_available(row: dict[str, Any]) -> bool:
    quality = str(row.get("quality") or row.get("status") or "").strip().lower()
    return quality in {"pass", "recommended", "observable"} and _candidate_pool_has_depth(row)


def _candidate_row(row: dict[str, Any], *, default_protocol: str = "uniswap_v3", bnb_status: bool = False) -> dict[str, Any]:
    balances = row.get("balances") if isinstance(row.get("balances"), dict) else {}
    tvl = _number(balances.get("tvlUsd") if balances else row.get("tvlUsd"))
    status = str(row.get("status") or "").strip().lower()
    quality = str(row.get("quality") or "").strip().lower()
    if bnb_status:
        quality = "pass" if status == "recommended" else status or quality
    elif not quality:
        quality = "pass" if status in {"observable", "recommended", "active"} else status or "observed"
    raw_protocol = str(row.get("protocol") or "").strip().lower()
    raw_dex = str(row.get("dex") or "").strip().lower()
    if bnb_status and raw_dex and raw_protocol in {"v2", "v3", ""}:
        protocol = _protocol_key(raw_dex)
    else:
        protocol = _protocol_key(row.get("protocol") or row.get("adapterKind") or row.get("dex") or default_protocol)
    result = {
        "pool": _pool_address(row),
        "dex": protocol,
        "protocol": protocol,
        "fee": str(row.get("fee") or ""),
        "quality": quality,
        "rejectReasons": row.get("rejectReasons") or row.get("reject_reasons") or [],
        "token0": row.get("token0") or row.get("baseToken"),
        "token1": row.get("token1") or row.get("quoteToken"),
        "token0Symbol": row.get("token0Symbol") or row.get("token0_symbol") or row.get("baseTokenSymbol"),
        "token1Symbol": row.get("token1Symbol") or row.get("token1_symbol") or row.get("quoteTokenSymbol"),
        "liquidity": str(row.get("liquidity") or ""),
        "tick": str(row.get("tick") or ((row.get("slot0") or {}).get("tick") if isinstance(row.get("slot0"), dict) else "")),
        "sqrtPriceX96": str(
            row.get("sqrtPriceX96") or ((row.get("slot0") or {}).get("sqrtPriceX96") if isinstance(row.get("slot0"), dict) else "")
        ),
        "balances": {
            "token0": balances.get("token0") if balances else row.get("token0Amount"),
            "token1": balances.get("token1") if balances else row.get("token1Amount"),
            "token0Usd": balances.get("token0Usd") if balances else row.get("token0Usd"),
            "token1Usd": balances.get("token1Usd") if balances else row.get("token1Usd"),
            "tvlUsd": balances.get("tvlUsd") if balances else row.get("tvlUsd"),
        },
        "quoteSummary": _pool_quote_summary(row),
        "market": row.get("market") or {},
        "evidence": row.get("evidence") or {},
        "_sortTvlUsd": tvl if tvl is not None else -1.0,
    }
    result.update(pair_classification(result.get("token0Symbol"), result.get("token1Symbol")))
    result["depthBand"] = _depth_band(result["balances"].get("tvlUsd"), observed_liquidity=result.get("liquidity"))
    return result


def _anchor_symbol_set(pair_reports: list[dict[str, Any]], anchor_symbols: list[str] | None = None) -> list[str]:
    if anchor_symbols:
        symbols = {str(symbol or "").strip().upper() for symbol in anchor_symbols if str(symbol or "").strip()}
        return sorted(symbols)
    stable_symbols: set[str] = set()
    fallback_symbols: set[str] = set()
    for pair in pair_reports:
        classification = pair.get("classification") if isinstance(pair.get("classification"), dict) else {}
        for symbol in classification.get("stableSymbols") or []:
            text = str(symbol or "").strip().upper()
            if text:
                stable_symbols.add(text)
        for key in ("tokenXSymbol", "tokenYSymbol"):
            text = str(pair.get(key) or "").strip().upper()
            if text:
                fallback_symbols.add(text)
    ordered = sorted(stable_symbols)
    ordered.extend(symbol for symbol in sorted(fallback_symbols) if symbol not in stable_symbols)
    return ordered


def _borrowable_symbols_from_discovery(chain_id: int | None) -> list[str]:
    try:
        snapshot = query_flash_loan_pool_discovery(chain_id=chain_id)
    except Exception:
        return []
    symbols: list[str] = []
    for raw in (snapshot.get("raw_snapshots") or {}).values():
        if not isinstance(raw, dict):
            continue
        raw_chain_id = raw.get("chainId") or raw.get("chain_id")
        try:
            raw_chain_id = int(raw_chain_id) if raw_chain_id is not None else None
        except (TypeError, ValueError):
            raw_chain_id = None
        if chain_id is not None and raw_chain_id and raw_chain_id != int(chain_id):
            continue
        for source in raw.get("sources") or []:
            if not isinstance(source, dict):
                continue
            symbol = str(source.get("token_symbol") or "").strip().upper()
            if symbol and bool(source.get("flash_borrowable", True)):
                symbols.append(symbol)
    if symbols:
        return sorted(set(symbols))
    fallback: set[str] = set()
    for protocol in snapshot.get("protocols") or []:
        if not isinstance(protocol, dict):
            continue
        for key in ("token_symbol", "asset_symbol", "symbol"):
            symbol = str(protocol.get(key) or "").strip().upper()
            if symbol:
                fallback.add(symbol)
    return sorted(fallback)


def _anchor_directional_row(
    row: dict[str, Any],
    *,
    anchor_symbol: str,
    anchor_address: str,
    counter_symbol: str,
    counter_address: str,
    direction: str,
) -> dict[str, Any]:
    pool_id = _pool_address(row)
    route_symbols = [anchor_symbol, counter_symbol] if direction == "yc" else [counter_symbol, anchor_symbol]
    route_addresses = [anchor_address, counter_address] if direction == "yc" else [counter_address, anchor_address]
    return {
        "pool": pool_id,
        "poolId": row.get("poolId") or pool_id,
        "poolManager": row.get("poolManager") or row.get("pool_manager"),
        "poolKey": row.get("poolKey") or row.get("pool_key"),
        "direction": direction,
        "anchorSymbol": anchor_symbol,
        "anchorAddress": anchor_address,
        "counterSymbol": counter_symbol,
        "counterAddress": counter_address,
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "routeText": " -> ".join(route_symbols),
        "poolIdentityMode": row.get("poolIdentityMode") or "",
        "protocol": row.get("protocol") or row.get("dex") or "",
        "dex": row.get("dex") or row.get("protocol") or "",
        "fee": row.get("fee"),
        "dynamicFee": row.get("dynamicFee"),
        "specialFee": row.get("specialFee"),
        "feeLabel": row.get("feeLabel"),
        "usableForObservation": row.get("usableForObservation"),
        "hasLiquidity": row.get("hasLiquidity"),
        "liquidityIndex": row.get("liquidityIndex"),
        "estimatedTvlUsd": row.get("estimatedTvlUsd"),
        "depthStatus": row.get("depthStatus") or "",
        "quality": row.get("quality"),
        "classification": row.get("classification") or {},
        "balances": row.get("balances") or {},
        "depthBand": row.get("depthBand") or {},
        "quoteSummary": row.get("quoteSummary") or {},
        "tick": row.get("tick"),
        "tickSpacing": row.get("tickSpacing") or row.get("tick_spacing"),
        "hooks": row.get("hooks") or "",
        "sqrtPriceX96": row.get("sqrtPriceX96"),
        "liquidity": row.get("liquidity"),
        "token0": row.get("token0"),
        "token1": row.get("token1"),
        "token0Symbol": row.get("token0Symbol"),
        "token1Symbol": row.get("token1Symbol"),
        "token0Decimals": row.get("token0Decimals") or row.get("token0_decimals"),
        "token1Decimals": row.get("token1Decimals") or row.get("token1_decimals"),
        "pairSymbol": row.get("symbolPair") or "",
        "pairAddress": row.get("addressPair") or "",
        "evidence": row.get("evidence") or {},
    }


def build_anchor_candidate_groups(
    pair_reports: list[dict[str, Any]],
    *,
    anchor_symbols: list[str] | None = None,
) -> list[dict[str, Any]]:
    symbols = _anchor_symbol_set(pair_reports, anchor_symbols)
    if not symbols:
        return []
    symbol_set = set(symbols)
    groups: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        groups[symbol] = {
            "anchorSymbol": symbol,
            "anchorAddresses": set(),
            "ycPools": [],
            "zcPools": [],
            "ycTokensMap": {},
            "zcTokensMap": {},
            "uniquePoolIds": set(),
        }

    def _add_record(group: dict[str, Any], section: str, record: dict[str, Any]) -> None:
        pools_key = "ycPools" if section == "yc" else "zcPools"
        tokens_key = "ycTokensMap" if section == "yc" else "zcTokensMap"
        token_symbol = str(record.get("counterSymbol") or "").strip().upper()
        token_address = str(record.get("counterAddress") or "").strip()
        pool_id = str(record.get("pool") or "").strip()
        if not token_symbol or not pool_id:
            return
        if _is_zero_address(token_address) and not _v4_zero_address_allowed(record):
            return
        seen_key = _anchor_queue_signature(
            anchor_symbol=str(group["anchorSymbol"]),
            direction=section,
            row=record,
            counter_symbol=token_symbol,
            counter_address=token_address,
        )
        seen_records = group.setdefault(f"_{section}Seen", set())
        if seen_key in seen_records:
            return
        seen_records.add(seen_key)
        record["queueKey"] = seen_key
        record["routeKey"] = seen_key
        group[pools_key].append(record)
        group["uniquePoolIds"].add(pool_id)
        token_bucket = group[tokens_key].setdefault(
            token_symbol,
            {
                "symbol": token_symbol,
                "address": token_address,
                "pools": [],
                "poolIds": set(),
            },
        )
        if token_address and not token_bucket.get("address"):
            token_bucket["address"] = token_address
        if pool_id not in token_bucket["poolIds"]:
            token_bucket["poolIds"].add(pool_id)
            token_bucket["pools"].append(record)

    for pair in pair_reports:
        pools = _pair_all_pool_rows(pair)
        if not pools:
            pools = pair.get("preferredPools") or pair.get("strictQualityPools") or pair.get("executionPools") or []
        for pool in pools:
            if not isinstance(pool, dict) or not _pool_address(pool):
                continue
            token0_symbol = str(pool.get("token0Symbol") or "").strip().upper()
            token1_symbol = str(pool.get("token1Symbol") or "").strip().upper()
            token0_address = _token_address(pool.get("token0"))
            token1_address = _token_address(pool.get("token1"))
            if (
                not token0_symbol
                or not token1_symbol
                or not token0_address
                or not token1_address
            ):
                continue
            if _is_zero_address(token0_address) and not _v4_zero_address_allowed(pool):
                continue
            if _is_zero_address(token1_address) and not _v4_zero_address_allowed(pool):
                continue
            for anchor_symbol, anchor_address, counter_symbol, counter_address in (
                (token0_symbol, token0_address, token1_symbol, token1_address),
                (token1_symbol, token1_address, token0_symbol, token0_address),
            ):
                if anchor_symbol not in symbol_set:
                    continue
                group = groups.setdefault(
                    anchor_symbol,
                    {
                        "anchorSymbol": anchor_symbol,
                        "anchorAddresses": set(),
                        "ycPools": [],
                        "zcPools": [],
                        "ycTokensMap": {},
                        "zcTokensMap": {},
                        "uniquePoolIds": set(),
                    },
                )
                if anchor_address:
                    group["anchorAddresses"].add(anchor_address)
                yc_record = _anchor_directional_row(
                    pool,
                    anchor_symbol=anchor_symbol,
                    anchor_address=anchor_address,
                    counter_symbol=counter_symbol,
                    counter_address=counter_address,
                    direction="yc",
                )
                zc_record = _anchor_directional_row(
                    pool,
                    anchor_symbol=anchor_symbol,
                    anchor_address=anchor_address,
                    counter_symbol=counter_symbol,
                    counter_address=counter_address,
                    direction="zc",
                )
                _add_record(group, "yc", yc_record)
                _add_record(group, "zc", zc_record)

    anchor_groups: list[dict[str, Any]] = []
    for symbol in symbols:
        group = groups.get(symbol)
        if not group:
            continue
        yc_tokens = []
        for token in sorted(group["ycTokensMap"].values(), key=lambda item: (-len(item["pools"]), str(item["symbol"]))):
            if not token.get("address"):
                continue
            if _is_zero_address(token.get("address")) and not any(
                _v4_zero_address_allowed(row) for row in token.get("pools") or []
            ):
                continue
            yc_tokens.append(
                {
                    "symbol": token["symbol"],
                    "address": token.get("address") or "",
                    "poolCount": str(len(token["pools"])),
                    "pools": token["pools"][:10],
                }
            )
        zc_tokens = []
        for token in sorted(group["zcTokensMap"].values(), key=lambda item: (-len(item["pools"]), str(item["symbol"]))):
            if not token.get("address"):
                continue
            if _is_zero_address(token.get("address")) and not any(
                _v4_zero_address_allowed(row) for row in token.get("pools") or []
            ):
                continue
            zc_tokens.append(
                {
                    "symbol": token["symbol"],
                    "address": token.get("address") or "",
                    "poolCount": str(len(token["pools"])),
                    "pools": token["pools"][:10],
                }
            )
        if not group["ycPools"] and not group["zcPools"]:
            continue
        anchor_groups.append(
            {
                "anchorSymbol": symbol,
                "anchorAddresses": sorted(group["anchorAddresses"]),
                "ycPools": group["ycPools"],
                "zcPools": group["zcPools"],
                "ycTokens": yc_tokens,
                "zcTokens": zc_tokens,
                "ycPoolCount": str(len(group["ycPools"])),
                "zcPoolCount": str(len(group["zcPools"])),
                "ycTokenCount": str(len(yc_tokens)),
                "zcTokenCount": str(len(zc_tokens)),
                "candidatePoolCount": str(len(group["uniquePoolIds"])),
                "candidateQueueCount": str(len(group["ycPools"]) + len(group["zcPools"])),
            }
        )
    return anchor_groups


def build_bnb_pool_candidates(
    table: dict[str, Any],
    pool_cache_path: Path,
    *,
    source: str,
    fallback_reason: str | None,
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in table.get("pools") or []:
        if isinstance(row, dict) and _pool_address(row):
            groups.setdefault(str(row.get("symbolPair") or "unknown"), []).append(row)
    pair_reports = []
    for pair, rows in groups.items():
        all_rows = _unique_pool_rows([_candidate_row(row, default_protocol="pancakeswap_v3", bnb_status=True) for row in rows])
        strict_rows = [row for row in all_rows if _candidate_pool_available(row)]
        rejected_rows = [row for row in all_rows if row not in strict_rows]
        for row in all_rows:
            row.pop("_sortTvlUsd", None)
        first = rows[0] if rows else {}
        pair_reports.append(
            {
                "symbolPair": pair,
                "addressPair": "",
                "tokenX": "",
                "tokenY": "",
                "tokenXSymbol": first.get("token0Symbol"),
                "tokenYSymbol": first.get("token1Symbol"),
                "classification": pair_classification(first.get("token0Symbol"), first.get("token1Symbol")),
                "allPoolCount": str(len(all_rows)),
                "allPools": all_rows,
                "strictQualityPoolCount": str(len(strict_rows)),
                "preferredPoolCount": str(len(strict_rows)),
                "executionPoolCount": str(min(5, len(strict_rows))),
                "rejectedPoolCount": str(len(rejected_rows)),
                "hasStrictQualityPools": bool(strict_rows),
                "hasPreferredPools": bool(strict_rows),
                "strictQualityPools": strict_rows,
                "preferredPools": strict_rows,
                "executionPools": strict_rows[:5],
                "rejectedPools": rejected_rows[:5],
                "rejectReasonCounts": {},
            }
        )
    chain_id = table.get("chainId") or table.get("chain_id")
    try:
        chain_id = int(chain_id) if chain_id is not None else None
    except (TypeError, ValueError):
        chain_id = None
    anchor_groups = build_anchor_candidate_groups(
        pair_reports,
        anchor_symbols=_borrowable_symbols_from_discovery(chain_id) or None,
    )
    return _candidate_payload(
        table,
        pool_cache_path,
        pair_reports,
        source=source,
        fallback_reason=fallback_reason,
        mode="bnb_static_pool_table",
        filters={"status": "high quality pools with positive depth"},
        anchor_groups=anchor_groups,
    )


def build_pair_table_candidates(
    table: dict[str, Any],
    pool_cache_path: Path,
    *,
    source: str,
    fallback_reason: str | None,
) -> dict[str, Any]:
    pair_reports = []
    for pair in table.get("pairs") or []:
        if not isinstance(pair, dict):
            continue
        all_rows = _unique_pool_rows([row for row in _pair_all_pool_rows(pair) if isinstance(row, dict) and _pool_address(row)])
        strict_rows = [_candidate_row(row) for row in all_rows if str(row.get("quality") or "").lower() == "pass" and _candidate_pool_has_depth(row)]
        preferred_rows = [_candidate_row(row) for row in pair.get("preferredPools") or [] if isinstance(row, dict) and _pool_address(row) and _candidate_pool_available(row)]
        if not preferred_rows:
            preferred_rows = list(strict_rows)
        execution_rows = [_candidate_row(row) for row in pair.get("executionPools") or [] if isinstance(row, dict) and _pool_address(row) and _candidate_pool_available(row)]
        if not execution_rows:
            execution_rows = preferred_rows[:5]
        rejected_rows = [_candidate_row(row) for row in all_rows if str(row.get("quality") or "").lower() != "pass"]
        for rows in (strict_rows, preferred_rows, execution_rows, rejected_rows):
            rows.sort(key=lambda item: (-item["_sortTvlUsd"], _int_sort_value(item.get("fee"))))
            for row in rows:
                row.pop("_sortTvlUsd", None)
        pair_reports.append(
            {
                "symbolPair": pair.get("symbolPair"),
                "addressPair": pair.get("addressPair"),
                "tokenX": pair.get("tokenX"),
                "tokenY": pair.get("tokenY"),
                "tokenXSymbol": pair.get("tokenXSymbol"),
                "tokenYSymbol": pair.get("tokenYSymbol"),
                "classification": pair_classification(pair.get("tokenXSymbol"), pair.get("tokenYSymbol")),
                "allPoolCount": str(len(all_rows)),
                "allPools": all_rows,
                "strictQualityPoolCount": str(len(strict_rows)),
                "preferredPoolCount": str(len(preferred_rows)),
                "executionPoolCount": str(len(execution_rows)),
                "rejectedPoolCount": str(len(rejected_rows)),
                "hasStrictQualityPools": bool(strict_rows),
                "hasPreferredPools": bool(preferred_rows),
                "strictQualityPools": strict_rows,
                "preferredPools": preferred_rows,
                "executionPools": execution_rows,
                "rejectedPools": rejected_rows[:5],
                "rejectReasonCounts": _reject_reason_counts(all_rows),
            }
        )
    chain_id = table.get("chainId") or table.get("chain_id")
    try:
        chain_id = int(chain_id) if chain_id is not None else None
    except (TypeError, ValueError):
        chain_id = None
    anchor_groups = build_anchor_candidate_groups(
        pair_reports,
        anchor_symbols=_borrowable_symbols_from_discovery(chain_id) or None,
    )
    return _candidate_payload(
        table,
        pool_cache_path,
        pair_reports,
        source=source,
        fallback_reason=fallback_reason,
        mode=table.get("mode"),
        filters=table.get("filters") if isinstance(table.get("filters"), dict) else {},
        anchor_groups=anchor_groups,
    )


def build_robinhood_pool_candidates(
    table: dict[str, Any],
    pool_cache_path: Path,
    *,
    market_pool_path: Path,
    evidence_root: Path,
    source: str,
    fallback_reason: str | None,
) -> dict[str, Any]:
    rows, effective_path, pool_source = _robinhood_pool_rows_and_source(table, pool_cache_path, market_pool_path, evidence_root)
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not _pool_address(row):
            continue
        candidate = _candidate_row(row)
        if not _readable_symbol(candidate.get("token0Symbol")) or not _readable_symbol(candidate.get("token1Symbol")):
            continue
        symbols = [candidate.get("token0Symbol"), candidate.get("token1Symbol")]
        symbol_pair = str(row.get("symbolPair") or ":".join(str(item or "").upper() for item in symbols if item)).strip(":")
        address_pair = _unordered_address_pair(candidate.get("token0"), candidate.get("token1"))
        bucket = grouped.setdefault(
            address_pair or f"symbol:{symbol_pair}",
            {"symbolPair": symbol_pair, "addressPair": address_pair, "identityMode": "address" if address_pair else "symbol_fallback", "rows": []},
        )
        bucket["rows"].append(candidate)
    pair_reports = []
    for bucket in grouped.values():
        pool_rows = bucket["rows"]
        pool_rows.sort(key=lambda row: (-row["_sortTvlUsd"], _int_sort_value(row.get("fee"))))
        strict_rows = [row for row in pool_rows if str(row.get("quality") or "").lower() == "pass"]
        rejected_rows = [row for row in pool_rows if row not in strict_rows]
        for row in pool_rows:
            row.pop("_sortTvlUsd", None)
        first = pool_rows[0]
        pair_reports.append(
            {
                "symbolPair": bucket["symbolPair"],
                "addressPair": bucket["addressPair"],
                "identityMode": bucket["identityMode"],
                "tokenX": first.get("token0"),
                "tokenY": first.get("token1"),
                "tokenXSymbol": first.get("token0Symbol"),
                "tokenYSymbol": first.get("token1Symbol"),
                "classification": pair_classification(first.get("token0Symbol"), first.get("token1Symbol")),
                "allPoolCount": str(len(pool_rows)),
                "allPools": pool_rows,
                "strictQualityPoolCount": str(len(strict_rows)),
                "preferredPoolCount": str(len(strict_rows)),
                "executionPoolCount": str(min(5, len(strict_rows))),
                "rejectedPoolCount": str(len(rejected_rows)),
                "hasStrictQualityPools": bool(strict_rows),
                "hasPreferredPools": bool(strict_rows),
                "strictQualityPools": strict_rows,
                "preferredPools": strict_rows,
                "executionPools": strict_rows[:5],
                "rejectedPools": rejected_rows[:5],
                "rejectReasonCounts": _reject_reason_counts(rejected_rows),
            }
        )
    anchor_groups = build_anchor_candidate_groups(
        pair_reports,
        anchor_symbols=("ETH", "WETH", "USDG", "USDE", "TZ"),
    )
    # Keep every initialized V4 PoolKey by token address, including different
    # fee/tickSpacing/hook variants.  Liquidity is dynamic and belongs to a
    # later refresh; the pool-address registry is the fixed on-chain surface.
    all_pools_by_address_pair: dict[str, list[dict[str, Any]]] = {}
    for pair in pair_reports:
        address_pair = str(pair.get("addressPair") or "").strip()
        if not address_pair:
            continue
        rows = list(pair.get("allPools") or [])
        if rows:
            all_pools_by_address_pair[address_pair] = rows
    return _candidate_payload(
        table,
        effective_path,
        pair_reports,
        source=source,
        fallback_reason=fallback_reason,
        mode=table.get("schemaVersion") or "robinhood_pool_static_table",
        filters={"source": pool_source, "status": "observable DEX pools with market depth when available"},
        network="robinhood",
        chain_id=4663,
        anchor_groups=anchor_groups,
        all_pools_by_address_pair=all_pools_by_address_pair,
    )


def _robinhood_pool_rows_and_source(
    table: dict[str, Any],
    pool_cache_path: Path,
    market_pool_path: Path,
    evidence_root: Path,
) -> tuple[list[dict[str, Any]], Path, str]:
    if isinstance(table.get("pools"), list) and table.get("pools"):
        return list(table.get("pools") or []), pool_cache_path, "robinhood_pool_cache"
    market_table = read_json_object(market_pool_path)
    if isinstance(market_table.get("pools"), list) and market_table.get("pools"):
        return list(market_table.get("pools") or []), market_pool_path, "robinhood_dexscreener_market_cache"
    evidence_table, evidence_path = _latest_robinhood_pool_static_table(evidence_root)
    if isinstance(evidence_table.get("pools"), list) and evidence_table.get("pools") and evidence_path is not None:
        return list(evidence_table.get("pools") or []), evidence_path, "robinhood_evidence_pool_static_table"
    return _robinhood_route_candidate_rows(table), pool_cache_path, "robinhood_route_candidate_cache_legacy_fallback"


def _latest_robinhood_pool_static_table(evidence_root: Path) -> tuple[dict[str, Any], Path | None]:
    try:
        paths = sorted(evidence_root.glob("*/pool_static_table.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        return {}, None
    for path in paths:
        table = read_json_object(path)
        if str(table.get("network") or "").lower() == "robinhood" and int(table.get("chainId") or 0) == 4663:
            return table, path
    return {}, None


def _robinhood_route_candidate_rows(table: dict[str, Any]) -> list[dict[str, Any]]:
    fallback_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in table.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        symbols = [str(item or "").strip().upper() for item in candidate.get("route_symbols") or [] if item]
        if len(symbols) < 2:
            continue
        route_fees = str(candidate.get("route_key") or "").rsplit(":", 1)[-1].split("->")
        evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {}
        evidence_pools = evidence.get("pools") or []
        for index, pool in enumerate(candidate.get("pool_addresses") or []):
            pool_address = str(pool or "").strip()
            if not pool_address or pool_address.lower() in seen:
                continue
            seen.add(pool_address.lower())
            evidence_pool = evidence_pools[index] if index < len(evidence_pools) and isinstance(evidence_pools[index], dict) else {}
            fallback_rows.append(
                {
                    "network": "robinhood",
                    "chainId": 4663,
                    "dex": "uniswap_v3",
                    "protocol": "uniswap_v3",
                    "pool": pool_address,
                    "token0Symbol": symbols[0],
                    "token1Symbol": symbols[1],
                    "symbolPair": f"{symbols[0]}:{symbols[1]}",
                    "fee": route_fees[index] if index < len(route_fees) else "",
                    "quality": "pass",
                    "status": "observable",
                    "liquidity": evidence_pool.get("liquidity"),
                    "evidence": evidence_pool,
                }
            )
    return fallback_rows


def _candidate_payload(
    table: dict[str, Any],
    path: Path,
    pair_reports: list[dict[str, Any]],
    *,
    source: str,
    fallback_reason: str | None,
    mode: object,
    filters: dict[str, Any],
    network: str | None = None,
    chain_id: int | None = None,
    anchor_groups: list[dict[str, Any]] | None = None,
    all_pools_by_address_pair: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    anchor_groups = anchor_groups or build_anchor_candidate_groups(pair_reports)
    pair_reports.sort(key=lambda item: (-int(item.get("preferredPoolCount") or 0), str(item.get("symbolPair") or "")))
    summary = table.get("summary") if isinstance(table.get("summary"), dict) else {}
    all_pool_count = sum(int(item.get("allPoolCount") or len(item.get("allPools") or [])) for item in pair_reports)
    yc_pool_count = sum(int(group.get("ycPoolCount") or 0) for group in anchor_groups)
    zc_pool_count = sum(int(group.get("zcPoolCount") or 0) for group in anchor_groups)
    yc_token_count = sum(int(group.get("ycTokenCount") or 0) for group in anchor_groups)
    zc_token_count = sum(int(group.get("zcTokenCount") or 0) for group in anchor_groups)
    return {
        "mode": "dex_pool_candidates",
        **source_metadata(source, freshness=table.get("generatedAt"), fallback_reason=fallback_reason),
        "poolStaticTable": {
            "path": str(path),
            "exists": path.exists(),
            "mode": mode,
            "network": network or table.get("network"),
            "chainId": chain_id or table.get("chainId") or table.get("chain_id"),
            "generatedAt": table.get("generatedAt"),
            "generatedBlockNumber": table.get("generatedBlockNumber") or table.get("blockNumber"),
            "sourceDiscoveryBlockNumber": table.get("sourceDiscoveryBlockNumber"),
            "sourceDiscoveryFetchedAt": table.get("sourceDiscoveryFetchedAt"),
            "factory": table.get("factory"),
            "router": table.get("router"),
            "quoter": table.get("quoter"),
        },
        "filters": filters,
        "summary": {
            **summary,
            "staticPoolCount": str(all_pool_count),
            "staticPairCount": str(len(pair_reports)),
            "pairTableCount": str(len(pair_reports)),
            "anchorGroupCount": str(len(anchor_groups)),
            "ycPoolCount": str(yc_pool_count),
            "zcPoolCount": str(zc_pool_count),
            "ycTokenCount": str(yc_token_count),
            "zcTokenCount": str(zc_token_count),
            "pairsWithStrictQualityPools": str(sum(1 for item in pair_reports if item["hasStrictQualityPools"])),
            "pairsWithPreferredPools": str(sum(1 for item in pair_reports if item["hasPreferredPools"])),
            "preferredPoolCount": str(sum(len(item["preferredPools"]) for item in pair_reports)),
            "executionPoolCount": str(sum(len(item["executionPools"]) for item in pair_reports)),
            "strictQualityPoolCount": str(sum(len(item["strictQualityPools"]) for item in pair_reports)),
            "candidateRouteCount": str(all_pool_count),
        },
        "pairs": pair_reports,
        "anchorGroups": anchor_groups,
        "allPoolsByAddressPair": all_pools_by_address_pair or {},
    }


__all__ = [
    "build_bnb_pool_candidates",
    "build_anchor_candidate_groups",
    "build_pair_table_candidates",
    "build_robinhood_pool_candidates",
    "pair_classification",
    "read_json_object",
]
