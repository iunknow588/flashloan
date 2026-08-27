from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from itertools import combinations
from typing import Any

from intent_trade.chains.common.routes import best_edges_by_pair, triangle_routes
from intent_trade.chains.robinhood import config as robinhood


def _address(value: Any) -> str:
    return str(value or "").strip()


def _address_key(value: Any) -> str:
    return _address(value).lower()


def _json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_discovery_report(report: dict[str, Any]) -> None:
    if str(report.get("network") or "").lower() != robinhood.NETWORK:
        raise ValueError("discovery report is not for Robinhood mainnet")
    if int(report.get("chainId") or 0) != robinhood.CHAIN_ID:
        raise ValueError("discovery report has the wrong chain id")
    if not report.get("factoryCodeVerified"):
        raise ValueError("factory code was not verified")
    if not str(report.get("sourceUrl") or "").startswith(("https://", "http://")):
        raise ValueError("discovery report is missing factory source evidence")


def build_robinhood_pool_records(
    report: dict[str, Any],
    *,
    token_registry: dict[str, dict[str, Any]] | None = None,
    token_allowlist: set[str] | None = None,
    token_blacklist: set[str] | None = None,
) -> list[dict[str, Any]]:
    validate_discovery_report(report)
    registry = {
        _address_key(address): dict(metadata)
        for address, metadata in (token_registry or {}).items()
        if _address(address)
    }
    allowlist = {_address_key(item) for item in (token_allowlist or set()) if _address(item)}
    blacklist = {_address_key(item) for item in (token_blacklist or set()) if _address(item)}
    if allowlist & blacklist:
        raise ValueError("token allowlist and blacklist overlap")

    rows: list[dict[str, Any]] = []
    for item in report.get("pools") or []:
        token0 = _address(item.get("token0"))
        token1 = _address(item.get("token1"))
        token0_meta = registry.get(_address_key(token0), {})
        token1_meta = registry.get(_address_key(token1), {})
        reasons: list[str] = []
        if not item.get("poolCodeVerified"):
            reasons.append("pool_code_missing")
        if not item.get("eventVerified") and not item.get("factoryQueryVerified"):
            reasons.append("pool_discovery_proof_missing")
        if not token0_meta or not token1_meta:
            reasons.append("token_metadata_missing")
        if allowlist and (_address_key(token0) not in allowlist or _address_key(token1) not in allowlist):
            reasons.append("token_not_allowlisted")
        if _address_key(token0) in blacklist or _address_key(token1) in blacklist:
            reasons.append("token_blacklisted")
        symbol0 = str(token0_meta.get("symbol") or "").upper()
        symbol1 = str(token1_meta.get("symbol") or "").upper()
        status = "observable" if not reasons else "rejected"
        rows.append(
            {
                "network": robinhood.NETWORK,
                "chainId": robinhood.CHAIN_ID,
                "dex": str(report.get("dex") or "unverified_robinhood_dex"),
                "protocol": "uniswap_v3",
                "factory": report.get("factory"),
                "factorySourceUrl": report.get("sourceUrl"),
                "pool": item.get("pool"),
                "token0": token0,
                "token1": token1,
                "token0Symbol": symbol0,
                "token1Symbol": symbol1,
                "symbolPair": f"{symbol0}:{symbol1}" if symbol0 and symbol1 else "",
                "fee": str(item.get("fee") or ""),
                "tickSpacing": item.get("tickSpacing"),
                "status": status,
                "rejectReasons": reasons,
                "tvlUsd": item.get("tvlUsd"),
                "liquidity": item.get("liquidity"),
                "slot0": {
                    "sqrtPriceX96": item.get("sqrtPriceX96"),
                    "tick": item.get("tick"),
                }
                if item.get("sqrtPriceX96") is not None or item.get("tick") is not None
                else None,
                "evidence": {
                    "eventVerified": bool(item.get("eventVerified")),
                    "factoryQueryVerified": bool(item.get("factoryQueryVerified")),
                    "poolCodeVerified": bool(item.get("poolCodeVerified")),
                    "factoryCodeVerified": bool(report.get("factoryCodeVerified")),
                    "poolStateVerified": bool(item.get("poolStateVerified")),
                    "liquidity": item.get("liquidity"),
                    "discoveryMethod": report.get("discoveryMethod") or report.get("evidenceType"),
                    "sourceUrl": report.get("sourceUrl"),
                    "transactionHash": item.get("transactionHash"),
                    "blockNumber": item.get("blockNumber"),
                    "logIndex": item.get("logIndex"),
                },
            }
        )
    return rows


def build_current_robinhood_candidates(
    pool_rows: list[dict[str, Any]],
    *,
    market_id: str = "robinhood-dex-shadow",
) -> list[dict[str, Any]]:
    observable_rows = [row for row in pool_rows if row.get("status") == "observable"]
    candidates: list[dict[str, Any]] = []

    # Keep every pool in a pair.  Selecting one "best" edge is insufficient
    # for a two-pool fee-tier arbitrage route.
    pair_groups: dict[str, list[dict[str, Any]]] = {}
    for row in observable_rows:
        symbols = [part.strip() for part in str(row.get("symbolPair") or "").split(":") if part.strip()]
        if len(symbols) != 2 or not row.get("pool"):
            continue
        pair_key = ":".join(sorted(symbols))
        pair_groups.setdefault(pair_key, []).append(row)
    for pair_key, rows in sorted(pair_groups.items()):
        symbols = pair_key.split(":")
        for first, second in combinations(rows, 2):
            if str(first.get("pool") or "").lower() == str(second.get("pool") or "").lower():
                continue
            ordered = sorted(
                (first, second),
                key=lambda row: (int(row.get("fee") or 0), str(row.get("pool") or "")),
            )
            observed_block = max(
                int((row.get("evidence") or {}).get("blockNumber") or 0)
                for row in ordered
            )
            candidates.append(
                {
                    "market_id": market_id,
                    "chain_id": robinhood.CHAIN_ID,
                    "network": robinhood.NETWORK,
                    "route_key": f"{pair_key}:{ordered[0].get('fee')}->{ordered[1].get('fee')}",
                    "route_symbols": [symbols[0], symbols[1], symbols[0]],
                    "pool_addresses": [ordered[0].get("pool"), ordered[1].get("pool")],
                    "route_kind": "two_pool_same_pair",
                    "status": "observed",
                    "gross_profit_usd": None,
                    "estimated_cost_usd": None,
                    "net_profit_usd": None,
                    "observed_block": observed_block,
                    "evidence": {
                        "candidateStatus": "observed",
                        "routeKind": "two_pool_same_pair",
                        "executionEnabled": False,
                        "broadcastEnabled": False,
                        "quoteStatus": "not_started",
                        "pools": [row.get("evidence") or {} for row in ordered],
                    },
                }
            )

    edges = best_edges_by_pair(observable_rows, {"observable"})
    for route in triangle_routes(edges):
        route_symbols = [part.strip() for part in route["path"].split("->")]
        pools = [edge.get("pool") for edge in route.get("edges") or []]
        evidence = {
            "candidateStatus": "observed",
            "executionEnabled": False,
            "broadcastEnabled": False,
            "quoteStatus": "not_started",
            "pools": [edge.get("evidence") or {} for edge in route.get("edges") or []],
        }
        candidates.append(
            {
                "market_id": market_id,
                "chain_id": robinhood.CHAIN_ID,
                "network": robinhood.NETWORK,
                "route_key": ":".join(route_symbols),
                "route_symbols": route_symbols,
                "pool_addresses": pools,
                "status": "observed",
                "route_kind": "triangle",
                "gross_profit_usd": None,
                "estimated_cost_usd": None,
                "net_profit_usd": None,
                "observed_block": max(
                    int((edge.get("evidence") or {}).get("blockNumber") or 0)
                    for edge in route.get("edges") or []
                ),
                "evidence": evidence,
            }
        )
    return candidates


def write_robinhood_evidence_package(
    output_dir: Path,
    *,
    discovery_report: dict[str, Any],
    pool_rows: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    token_registry: dict[str, dict[str, Any]],
    token_allowlist: set[str],
    token_blacklist: set[str],
) -> Path:
    validate_discovery_report(discovery_report)
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = str(discovery_report.get("generatedAt") or _utc_now_iso())
    _json_write(output_dir / "source_urls.json", {"factory": discovery_report.get("sourceUrl")})
    _json_write(
        output_dir / "contracts.json",
        {
            "network": robinhood.NETWORK,
            "chainId": robinhood.CHAIN_ID,
            "factory": discovery_report.get("factory"),
            "router": None,
            "quoter": None,
            "multicall": robinhood.MULTICALL_ADDRESS,
        },
    )
    _json_write(
        output_dir / "code_hashes.json",
        {
            "factory": {"codePresent": bool(discovery_report.get("factoryCodeVerified"))},
            "pools": [
                {"address": row.get("pool"), "codePresent": bool((row.get("evidence") or {}).get("poolCodeVerified"))}
                for row in pool_rows
            ],
        },
    )
    _json_write(output_dir / "factory_events.json", discovery_report)
    _json_write(output_dir / "token_allowlist.json", sorted(token_allowlist))
    _json_write(
        output_dir / "token_risk_report.json",
        {
            "allowlist": sorted(token_allowlist),
            "blacklist": sorted(token_blacklist),
            "registry": token_registry,
            "manualReviewRequired": any(row.get("status") != "observable" for row in pool_rows),
        },
    )
    _json_write(
        output_dir / "pool_static_table.json",
        {
            "network": robinhood.NETWORK,
            "chainId": robinhood.CHAIN_ID,
            "generatedAt": discovery_report.get("generatedAt"),
            "factory": discovery_report.get("factory"),
            "pools": pool_rows,
            "candidates": candidates,
        },
    )
    _json_write(
        output_dir / "quote_smoke_report.json",
        {
            "status": "not_started",
            "executionEnabled": False,
            "broadcastEnabled": False,
            "reason": "live quote is not enabled before verified router/quoter addresses",
        },
    )
    _json_write(
        output_dir / "manifest.json",
        {
            "schemaVersion": "robinhood-dex-evidence-v1",
            "network": robinhood.NETWORK,
            "chainId": robinhood.CHAIN_ID,
            "generatedAt": generated_at,
            "executionEnabled": False,
            "broadcastEnabled": False,
            "files": [
                "source_urls.json",
                "contracts.json",
                "code_hashes.json",
                "factory_events.json",
                "token_allowlist.json",
                "token_risk_report.json",
                "pool_static_table.json",
                "quote_smoke_report.json",
                "summary.md",
            ],
        },
    )
    (output_dir / "summary.md").write_text(
        "\n".join(
            [
                "# Robinhood DEX Discovery Evidence",
                "",
                f"- Network: `{robinhood.NETWORK}`",
                f"- Chain ID: `{robinhood.CHAIN_ID}`",
                f"- Factory source: `{discovery_report.get('sourceUrl')}`",
                f"- Pool records: `{len(pool_rows)}`",
                f"- Current observed candidates: `{len(candidates)}`",
                "- Execution: disabled",
                "- Broadcast: disabled",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return output_dir
