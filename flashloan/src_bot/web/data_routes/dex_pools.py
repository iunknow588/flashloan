from __future__ import annotations

import json
from pathlib import Path

from core.contracts import source_metadata

SRC_ROOT = Path(__file__).resolve().parents[2]
DEX_POOL_STATIC_TABLE_PATH = SRC_ROOT / "runtime" / "cache" / "bnb_arbitrage_pool_static_table.json"


def _read_json_object(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _string_number(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else None


def _pool_address(row: dict) -> str:
    return str(row.get("pool") or row.get("pool_address") or row.get("address") or "").strip()


def _reject_reason_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for reason in row.get("rejectReasons") or row.get("reject_reasons") or []:
            text = str(reason or "").strip()
            if text:
                counts[text] = counts.get(text, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _pool_quote_summary(row: dict) -> dict:
    quotes = row.get("quotes") if isinstance(row.get("quotes"), dict) else {}
    quote01 = quotes.get("token0ToToken1") if isinstance(quotes.get("token0ToToken1"), dict) else {}
    quote10 = quotes.get("token1ToToken0") if isinstance(quotes.get("token1ToToken0"), dict) else {}
    return {
        "probeUsd": quotes.get("probeUsd"),
        "token0ToToken1": {
            "ok": bool(quote01.get("ok")),
            "amountIn": quote01.get("amountIn"),
            "amountOut": quote01.get("amountOut"),
            "amountInUsd": quote01.get("amountInUsd"),
            "amountOutUsd": quote01.get("amountOutUsd"),
            "dexRateOutPerIn": quote01.get("dexRateOutPerIn"),
            "referenceRateOutPerIn": quote01.get("referenceRateOutPerIn"),
            "deviationPct": quote01.get("deviationPct"),
            "outputLossPct": quote01.get("outputLossPct"),
            "gas": quote01.get("gas"),
            "reason": quote01.get("reason"),
        },
        "token1ToToken0": {
            "ok": bool(quote10.get("ok")),
            "amountIn": quote10.get("amountIn"),
            "amountOut": quote10.get("amountOut"),
            "amountInUsd": quote10.get("amountInUsd"),
            "amountOutUsd": quote10.get("amountOutUsd"),
            "dexRateOutPerIn": quote10.get("dexRateOutPerIn"),
            "referenceRateOutPerIn": quote10.get("referenceRateOutPerIn"),
            "deviationPct": quote10.get("deviationPct"),
            "outputLossPct": quote10.get("outputLossPct"),
            "gas": quote10.get("gas"),
            "reason": quote10.get("reason"),
        },
    }


def _pool_candidate_row(row: dict) -> dict:
    balances = row.get("balances") if isinstance(row.get("balances"), dict) else {}
    tvl = _string_number(balances.get("tvlUsd"))
    return {
        "pool": _pool_address(row),
        "fee": str(row.get("fee") or ""),
        "quality": str(row.get("quality") or ""),
        "rejectReasons": row.get("rejectReasons") or [],
        "token0": row.get("token0"),
        "token1": row.get("token1"),
        "token0Symbol": row.get("token0Symbol") or row.get("token0_symbol"),
        "token1Symbol": row.get("token1Symbol") or row.get("token1_symbol"),
        "liquidity": str(row.get("liquidity") or ""),
        "tick": str(row.get("tick") or ""),
        "sqrtPriceX96": str(row.get("sqrtPriceX96") or ""),
        "balances": {
            "token0": balances.get("token0"),
            "token1": balances.get("token1"),
            "token0Usd": balances.get("token0Usd"),
            "token1Usd": balances.get("token1Usd"),
            "tvlUsd": balances.get("tvlUsd"),
        },
        "quoteSummary": _pool_quote_summary(row),
        "_sortTvlUsd": tvl if tvl is not None else -1.0,
    }


def _bnb_pool_candidate_row(row: dict) -> dict:
    status = str(row.get("status") or "rejected").lower()
    quality = "pass" if status == "recommended" else status
    tvl = _string_number(row.get("tvlUsd"))
    return {
        "pool": _pool_address(row),
        "fee": str(row.get("fee") or ""),
        "quality": quality,
        "rejectReasons": [],
        "token0": row.get("token0"),
        "token1": row.get("token1"),
        "token0Symbol": row.get("token0Symbol"),
        "token1Symbol": row.get("token1Symbol"),
        "liquidity": str(row.get("liquidity") or ""),
        "tick": str(row.get("tick") or ""),
        "sqrtPriceX96": str(row.get("sqrtPriceX96") or ""),
        "balances": {
            "token0": row.get("token0Amount"),
            "token1": row.get("token1Amount"),
            "token0Usd": row.get("token0Usd"),
            "token1Usd": row.get("token1Usd"),
            "tvlUsd": row.get("tvlUsd"),
        },
        "quoteSummary": {},
        "_sortTvlUsd": tvl if tvl is not None else -1.0,
    }


def _bnb_dex_pool_candidates_payload(table: dict, pool_cache_path: Path) -> dict:
    groups: dict[str, list[dict]] = {}
    for row in table.get("pools") or []:
        if not isinstance(row, dict) or not _pool_address(row):
            continue
        groups.setdefault(str(row.get("symbolPair") or "unknown"), []).append(row)
    pair_reports = []
    for pair, rows in groups.items():
        all_rows = [_bnb_pool_candidate_row(row) for row in rows]
        strict_rows = [row for row in all_rows if str(row.get("quality") or "").lower() == "pass"]
        preferred_rows = list(strict_rows)
        rejected_rows = [row for row in all_rows if row not in strict_rows]
        for row in all_rows:
            row.pop("_sortTvlUsd", None)
        pair_reports.append(
            {
                "symbolPair": pair,
                "addressPair": "",
                "tokenX": "",
                "tokenY": "",
                "tokenXSymbol": rows[0].get("token0Symbol") if rows else "",
                "tokenYSymbol": rows[0].get("token1Symbol") if rows else "",
                "allPoolCount": str(len(all_rows)),
                "strictQualityPoolCount": str(len(strict_rows)),
                "preferredPoolCount": str(len(preferred_rows)),
                "executionPoolCount": str(min(5, len(preferred_rows))),
                "rejectedPoolCount": str(len(rejected_rows)),
                "hasStrictQualityPools": bool(strict_rows),
                "hasPreferredPools": bool(preferred_rows),
                "strictQualityPools": strict_rows,
                "preferredPools": preferred_rows,
                "executionPools": preferred_rows[:5],
                "rejectedPools": rejected_rows[:5],
                "rejectReasonCounts": {},
            }
        )
    pair_reports.sort(key=lambda item: (-int(item["preferredPoolCount"]), str(item["symbolPair"])))
    summary = table.get("summary") if isinstance(table.get("summary"), dict) else {}
    return {
        "mode": "dex_pool_candidates",
        "poolStaticTable": {
            "path": str(pool_cache_path),
            "exists": pool_cache_path.exists(),
            "mode": "bnb_static_pool_table",
            "network": table.get("network"),
            "chainId": table.get("chainId"),
            "generatedAt": table.get("generatedAt"),
            "generatedBlockNumber": table.get("generatedBlockNumber"),
            "sourceDiscoveryBlockNumber": None,
            "sourceDiscoveryFetchedAt": None,
            "factory": None,
            "router": None,
            "quoter": None,
        },
        "filters": {"status": "recommended pools are high quality candidates"},
        "summary": {
            **summary,
            "pairTableCount": str(len(pair_reports)),
            "pairsWithStrictQualityPools": str(sum(1 for item in pair_reports if item["hasStrictQualityPools"])),
            "pairsWithPreferredPools": str(sum(1 for item in pair_reports if item["hasPreferredPools"])),
        },
        "pairs": pair_reports,
    }


def dex_pool_candidates_payload(pool_cache_path: Path = DEX_POOL_STATIC_TABLE_PATH) -> dict:
    table = _read_json_object(pool_cache_path)
    source = "runtime_cache" if table else "builtin_fallback"
    generated_at = table.get("generatedAt") if table else None
    fallback_reason = None if table else "dex_pool_static_table_unavailable"
    if str(table.get("network") or "").lower() == "bnb":
        payload = _bnb_dex_pool_candidates_payload(table, pool_cache_path)
        payload.update(source_metadata(source, freshness=generated_at, fallback_reason=fallback_reason))
        return payload
    pair_reports = []
    for pair in table.get("pairs") or []:
        if not isinstance(pair, dict):
            continue
        all_rows = [row for row in pair.get("allPools") or [] if isinstance(row, dict) and _pool_address(row)]
        strict_rows = [_pool_candidate_row(row) for row in all_rows if str(row.get("quality") or "").lower() == "pass"]
        preferred_rows = [_pool_candidate_row(row) for row in (pair.get("preferredPools") or []) if isinstance(row, dict) and _pool_address(row)]
        execution_rows = [_pool_candidate_row(row) for row in (pair.get("executionPools") or []) if isinstance(row, dict) and _pool_address(row)]
        rejected_rows = [_pool_candidate_row(row) for row in all_rows if str(row.get("quality") or "").lower() != "pass"]
        strict_rows.sort(key=lambda row: (-row["_sortTvlUsd"], int(row.get("fee") or 0)))
        preferred_rows.sort(key=lambda row: (-row["_sortTvlUsd"], int(row.get("fee") or 0)))
        execution_rows.sort(key=lambda row: (-row["_sortTvlUsd"], int(row.get("fee") or 0)))
        rejected_rows.sort(key=lambda row: (-row["_sortTvlUsd"], int(row.get("fee") or 0)))
        for row in strict_rows + preferred_rows + execution_rows + rejected_rows:
            row.pop("_sortTvlUsd", None)
        pair_reports.append(
            {
                "symbolPair": pair.get("symbolPair"),
                "addressPair": pair.get("addressPair"),
                "tokenX": pair.get("tokenX"),
                "tokenY": pair.get("tokenY"),
                "tokenXSymbol": pair.get("tokenXSymbol"),
                "tokenYSymbol": pair.get("tokenYSymbol"),
                "allPoolCount": str(len(all_rows)),
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
    pair_reports.sort(
        key=lambda item: (
            -int(item.get("preferredPoolCount") or 0),
            str(item.get("symbolPair") or ""),
        )
    )
    return {
        "mode": "dex_pool_candidates",
        **source_metadata(source, freshness=generated_at, fallback_reason=fallback_reason),
        "poolStaticTable": {
            "path": str(pool_cache_path),
            "exists": pool_cache_path.exists(),
            "mode": table.get("mode"),
            "network": table.get("network"),
            "chainId": table.get("chainId") or table.get("chain_id"),
            "generatedAt": table.get("generatedAt"),
            "generatedBlockNumber": table.get("generatedBlockNumber"),
            "sourceDiscoveryBlockNumber": table.get("sourceDiscoveryBlockNumber"),
            "sourceDiscoveryFetchedAt": table.get("sourceDiscoveryFetchedAt"),
            "factory": table.get("factory"),
            "router": table.get("router"),
            "quoter": table.get("quoter"),
        },
        "filters": table.get("filters") or {},
        "summary": {
            **(table.get("summary") if isinstance(table.get("summary"), dict) else {}),
            "pairTableCount": str(len(pair_reports)),
            "pairsWithStrictQualityPools": str(sum(1 for item in pair_reports if item["hasStrictQualityPools"])),
            "pairsWithPreferredPools": str(sum(1 for item in pair_reports if item["hasPreferredPools"])),
        },
        "pairs": pair_reports,
    }


