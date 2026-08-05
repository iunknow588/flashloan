from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from db.storage_common import db_connection

DEFAULT_COW_EXECUTION_RETENTION_DAYS = 7


def _json_text(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _json_loads(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _market_state_summary(market_state: dict[str, Any]) -> dict[str, Any]:
    return {
        "observed_at": market_state.get("observed_at"),
        "window_seconds": market_state.get("window_seconds"),
        "price_source": market_state.get("price_source"),
        "market_state_source": market_state.get("market_state_source"),
        "fallback_reason": market_state.get("fallback_reason"),
        "cow_filter": market_state.get("cow_filter"),
    }


def ensure_cow_execution_attempts_table(database_url: str) -> None:
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS cow_execution_attempts (
                    id BIGSERIAL PRIMARY KEY,
                    observed_at TIMESTAMPTZ,
                    network TEXT NOT NULL,
                    chain_id INTEGER,
                    owner_address TEXT,
                    pair TEXT,
                    pair_rank INTEGER,
                    priority_reason TEXT,
                    route_path_json TEXT,
                    state TEXT NOT NULL,
                    execution_phase TEXT NOT NULL,
                    checks_passed BOOLEAN NOT NULL DEFAULT FALSE,
                    can_submit_order BOOLEAN NOT NULL DEFAULT FALSE,
                    order_submission_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    auto_execute_requested BOOLEAN NOT NULL DEFAULT FALSE,
                    final_delta_amount TEXT,
                    final_symbol TEXT,
                    blocked_reasons_json TEXT,
                    quote_json TEXT,
                    precheck_json TEXT,
                    market_state_json TEXT,
                    error TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_cow_execution_attempts_network_time "
                "ON cow_execution_attempts(network, created_at DESC)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_cow_execution_attempts_state_time "
                "ON cow_execution_attempts(state, created_at DESC)"
            )


def _attempt_from_quote(
    quote: dict[str, Any],
    *,
    market_state: dict[str, Any],
    cow_network: str,
    cow_chain_id: int | None,
    owner: str | None,
) -> dict[str, Any]:
    precheck = quote.get("execution_precheck") or {}
    reasons = precheck.get("reasons") if isinstance(precheck, dict) else []
    state = str(precheck.get("status") or ("quote_failed" if quote.get("error") else "quoted"))
    return {
        "observed_at": market_state.get("observed_at"),
        "network": cow_network,
        "chain_id": cow_chain_id,
        "owner_address": owner,
        "pair": quote.get("pair"),
        "pair_rank": quote.get("pair_rank"),
        "priority_reason": quote.get("priority_reason"),
        "route_path": quote.get("path") or [],
        "state": state,
        "execution_phase": "quote_precheck",
        "checks_passed": bool(precheck.get("checks_passed")),
        "can_submit_order": bool(precheck.get("can_submit_order")),
        "order_submission_enabled": bool(precheck.get("order_submission_enabled")),
        "auto_execute_requested": bool(precheck.get("auto_execute_requested")),
        "final_delta_amount": quote.get("final_delta_amount"),
        "final_symbol": quote.get("final_symbol"),
        "blocked_reasons": reasons if isinstance(reasons, list) else [str(reasons)],
        "quote": quote,
        "precheck": precheck,
        "market_state": _market_state_summary(market_state),
        "error": quote.get("error"),
    }


def _attempt_from_market_route(
    pair: dict[str, Any],
    route: dict[str, Any],
    *,
    market_state: dict[str, Any],
    cow_network: str,
    cow_chain_id: int | None,
) -> dict[str, Any]:
    route_path = route.get("route") or pair.get("route") or []
    reasons = []
    for item in [*(pair.get("blocked_reasons") or []), *(route.get("blocked_reasons") or [])]:
        if item and item not in reasons:
            reasons.append(item)
    if not reasons:
        reasons.append("requires_cow_or_dex_quote")
    precheck = {
        "status": "quote_required",
        "checks_passed": False,
        "can_submit_order": False,
        "order_submission_enabled": False,
        "auto_execute_requested": False,
        "reasons": reasons,
        "quote_required": bool(pair.get("quote_required", True) or route.get("quote_required", True)),
        "estimation_available": bool(pair.get("estimation_available") or route.get("estimation_available")),
        "edge_hint_percent": route.get("edge_hint_percent", pair.get("edge_hint_percent")),
        "window_spread_percent": pair.get("window_spread_percent"),
    }
    quote = {
        "pair": pair.get("pair"),
        "pair_rank": pair.get("rank") or pair.get("grid_rank"),
        "priority_reason": route.get("priority_reason"),
        "path": route_path,
        "input_amount": route.get("initial_amount"),
        "input_symbol": route.get("initial_symbol"),
        "final_symbol": route.get("initial_symbol"),
        "final_delta_amount": route.get("net_after_flashloan_amount") or route.get("profit_amount"),
        "quote_verified": False,
        "quote_required": True,
        "candidate_basis": pair.get("candidate_basis") or route.get("candidate_basis"),
        "trigger_source": pair.get("trigger_source"),
        "edge_hint_percent": route.get("edge_hint_percent", pair.get("edge_hint_percent")),
        "window_spread_percent": pair.get("window_spread_percent"),
        "x_symbol": pair.get("x_symbol"),
        "y_symbol": pair.get("y_symbol"),
        "x_base_symbol": pair.get("x_base_symbol"),
        "y_base_symbol": pair.get("y_base_symbol"),
        "x_change_percent": pair.get("x_change_percent"),
        "y_change_percent": pair.get("y_change_percent"),
        "x_start_price": pair.get("x_start_price"),
        "x_current_price": pair.get("x_current_price"),
        "y_start_price": pair.get("y_start_price"),
        "y_current_price": pair.get("y_current_price"),
        "route": route,
    }
    return {
        "observed_at": market_state.get("observed_at"),
        "network": cow_network,
        "chain_id": cow_chain_id,
        "owner_address": None,
        "pair": pair.get("pair"),
        "pair_rank": pair.get("rank") or pair.get("grid_rank"),
        "priority_reason": route.get("priority_reason"),
        "route_path": route_path,
        "state": "quote_required",
        "execution_phase": "market_candidate",
        "checks_passed": False,
        "can_submit_order": False,
        "order_submission_enabled": False,
        "auto_execute_requested": False,
        "final_delta_amount": quote["final_delta_amount"],
        "final_symbol": quote["final_symbol"],
        "blocked_reasons": reasons,
        "quote": quote,
        "precheck": precheck,
        "market_state": _market_state_summary(market_state),
        "error": None,
    }


def build_cow_execution_attempts(
    payload: dict[str, Any],
    *,
    market_state: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _attempt_from_quote(
            quote,
            market_state=market_state,
            cow_network=str(payload.get("cow_network") or ""),
            cow_chain_id=payload.get("cow_chain_id"),
            owner=payload.get("owner"),
        )
        for quote in payload.get("ranking") or []
        if isinstance(quote, dict)
    ]


def build_cow_market_candidate_attempts(market_state: dict[str, Any]) -> list[dict[str, Any]]:
    cow_filter = market_state.get("cow_filter") if isinstance(market_state, dict) else {}
    cow_network = str((cow_filter or {}).get("network") or market_state.get("cow_network") or "")
    cow_chain_id = (cow_filter or {}).get("chain_id") or market_state.get("cow_chain_id")
    attempts = []
    for pair in market_state.get("pairs") or []:
        if not isinstance(pair, dict):
            continue
        route_results = pair.get("route_results") or []
        if not route_results:
            route_results = [{"route": pair.get("route") or [], "priority_reason": pair.get("priority_reason")}]
        for route in route_results:
            if isinstance(route, dict):
                attempts.append(
                    _attempt_from_market_route(
                        pair,
                        route,
                        market_state=market_state,
                        cow_network=cow_network,
                        cow_chain_id=cow_chain_id,
                    )
                )
    return attempts


def _claim_route_results(x: dict[str, Any], y: dict[str, Any], amount: Any) -> list[dict[str, Any]]:
    x_base = str(x.get("base_symbol") or x.get("symbol") or "").removesuffix("USDT")
    y_base = str(y.get("base_symbol") or y.get("symbol") or "").removesuffix("USDT")
    return [
        {
            "route_no": 1,
            "route": ["USDC", y_base, x_base, "USDC"],
            "initial_amount": str(amount) if amount is not None else None,
            "initial_symbol": "USDC",
            "priority_reason": "buy_loser_then_gainer",
            "quote_required": True,
        },
        {
            "route_no": 2,
            "route": ["USDC", x_base, y_base, "USDC"],
            "initial_amount": str(amount) if amount is not None else None,
            "initial_symbol": "USDC",
            "priority_reason": "reverse_check",
            "quote_required": True,
        },
    ]


def _claim_pair_row(x: dict[str, Any], y: dict[str, Any], rank: int, *, amount: Any) -> dict[str, Any] | None:
    x_symbol = str(x.get("symbol") or "").strip().upper()
    y_symbol = str(y.get("symbol") or "").strip().upper()
    if not x_symbol or not y_symbol or x_symbol == y_symbol:
        return None
    try:
        x_change = float(x.get("change_percent"))
        y_change = float(y.get("change_percent"))
    except (TypeError, ValueError):
        return None
    x_base = str(x.get("base_symbol") or x_symbol).strip().upper()
    y_base = str(y.get("base_symbol") or y_symbol).strip().upper()
    return {
        "rank": rank,
        "pair": f"{x_symbol} / {y_symbol}",
        "x_symbol": x_symbol,
        "y_symbol": y_symbol,
        "x_base_symbol": x_base,
        "y_base_symbol": y_base,
        "x_change_percent": x_change,
        "y_change_percent": y_change,
        "x_start_price": x.get("start_price"),
        "x_current_price": x.get("current_price"),
        "y_start_price": y.get("start_price"),
        "y_current_price": y.get("current_price"),
        "window_spread_percent": x_change - y_change,
        "candidate_basis": "cow_network_claim_top_bottom",
        "trigger_source": "cow_network_claim",
        "quote_required": True,
        "estimation_available": False,
        "blocked_reasons": ["requires_cow_or_dex_quote"],
        "route_results": _claim_route_results(x, y, amount),
    }


def build_cow_market_claim_candidate_attempts(
    network_claims: list[dict[str, Any]],
    *,
    observed_at: Any = None,
    window_seconds: Any = None,
    price_source: Any = None,
    market_state_source: Any = None,
    fallback_reason: Any = None,
) -> list[dict[str, Any]]:
    attempts = []
    for claim in network_claims or []:
        if not isinstance(claim, dict):
            continue
        threshold_detail = claim.get("threshold_detail") if isinstance(claim.get("threshold_detail"), dict) else {}
        min_spread = float(claim.get("min_spread_percent") or threshold_detail.get("adjusted_min_spread_percent") or 0)
        amount = threshold_detail.get("amount")
        pairs = []
        for x in claim.get("top") or []:
            if not isinstance(x, dict):
                continue
            for y in claim.get("bottom") or []:
                if not isinstance(y, dict):
                    continue
                pair = _claim_pair_row(x, y, len(pairs) + 1, amount=amount)
                if not pair:
                    continue
                if float(pair.get("window_spread_percent") or 0) <= min_spread:
                    continue
                pairs.append(pair)
        if not pairs:
            continue
        market_state = {
            "observed_at": observed_at,
            "window_seconds": window_seconds,
            "price_source": price_source,
            "market_state_source": market_state_source,
            "fallback_reason": fallback_reason,
            "cow_filter": {
                "network": claim.get("network"),
                "chain_id": claim.get("chain_id"),
                "source": "cow_network_claim",
                "token_cache_source": claim.get("token_cache_source"),
                "token_cache_count": claim.get("token_cache_count"),
                "threshold_detail": threshold_detail,
            },
            "pairs": pairs,
        }
        attempts.extend(build_cow_market_candidate_attempts(market_state))
    return attempts


def _is_market_candidate(item: dict[str, Any]) -> bool:
    return str(item.get("execution_phase") or "") == "market_candidate"


def _attempt_signature(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("observed_at"),
        item.get("network"),
        item.get("pair"),
        item.get("pair_rank"),
        item.get("priority_reason"),
        _json_text(item.get("route_path") or []),
        item.get("execution_phase") or "quote_precheck",
    )


def prune_cow_execution_attempts(database_url: str, retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS) -> int:
    ensure_cow_execution_attempts_table(database_url)
    days = max(1, int(retention_days))
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM cow_execution_attempts WHERE created_at < NOW() - (%s * INTERVAL '1 day')",
                (days,),
            )
            return int(cursor.rowcount or 0)


def record_cow_execution_attempts(
    database_url: str,
    attempts: list[dict[str, Any]],
    *,
    retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS,
    dedupe_market_candidates: bool = True,
) -> list[int]:
    if not attempts:
        return []
    ensure_cow_execution_attempts_table(database_url)
    if retention_days:
        prune_cow_execution_attempts(database_url, retention_days=retention_days)
    ids = []
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            for item in attempts:
                route_path_json = _json_text(item.get("route_path") or [])
                if dedupe_market_candidates and _is_market_candidate(item):
                    cursor.execute(
                        """
                        SELECT id
                        FROM cow_execution_attempts
                        WHERE observed_at IS NOT DISTINCT FROM %s
                          AND network IS NOT DISTINCT FROM %s
                          AND pair IS NOT DISTINCT FROM %s
                          AND pair_rank IS NOT DISTINCT FROM %s
                          AND priority_reason IS NOT DISTINCT FROM %s
                          AND route_path_json IS NOT DISTINCT FROM %s
                          AND execution_phase IS NOT DISTINCT FROM %s
                        LIMIT 1
                        """,
                        (
                            item.get("observed_at"),
                            item.get("network"),
                            item.get("pair"),
                            item.get("pair_rank"),
                            item.get("priority_reason"),
                            route_path_json,
                            item.get("execution_phase") or "market_candidate",
                        ),
                    )
                    if cursor.fetchone():
                        continue
                cursor.execute(
                    """
                    INSERT INTO cow_execution_attempts (
                        observed_at, network, chain_id, owner_address,
                        pair, pair_rank, priority_reason, route_path_json,
                        state, execution_phase, checks_passed, can_submit_order,
                        order_submission_enabled, auto_execute_requested,
                        final_delta_amount, final_symbol, blocked_reasons_json,
                        quote_json, precheck_json, market_state_json, error, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    RETURNING id
                    """,
                    (
                        item.get("observed_at"),
                        item.get("network"),
                        item.get("chain_id"),
                        item.get("owner_address"),
                        item.get("pair"),
                        item.get("pair_rank"),
                        item.get("priority_reason"),
                        route_path_json,
                        item.get("state") or "unknown",
                        item.get("execution_phase") or "quote_precheck",
                        bool(item.get("checks_passed")),
                        bool(item.get("can_submit_order")),
                        bool(item.get("order_submission_enabled")),
                        bool(item.get("auto_execute_requested")),
                        item.get("final_delta_amount"),
                        item.get("final_symbol"),
                        _json_text(item.get("blocked_reasons") or []),
                        _json_text(item.get("quote") or {}),
                        _json_text(item.get("precheck") or {}),
                        _json_text(item.get("market_state") or {}),
                        item.get("error"),
                    ),
                )
                row = cursor.fetchone()
                if row:
                    ids.append(int(row[0]))
    return ids


def _jsonl_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return _attempt_signature(row)


def _within_retention(row: dict[str, Any], cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    created_at = _parse_datetime(row.get("created_at") or row.get("observed_at"))
    return created_at is None or created_at >= cutoff


def append_cow_execution_attempts_jsonl(
    path: Path,
    attempts: list[dict[str, Any]],
    *,
    retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS,
    dedupe_market_candidates: bool = True,
) -> int:
    if not attempts:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc).isoformat()
    cutoff = None
    if retention_days:
        cutoff = datetime.now(timezone.utc).timestamp() - (max(1, int(retention_days)) * 86400)
        cutoff = datetime.fromtimestamp(cutoff, tz=timezone.utc)
    existing_rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and _within_retention(row, cutoff):
                existing_rows.append(row)
    market_signatures = {
        _jsonl_signature(row)
        for row in existing_rows
        if _is_market_candidate(row)
    }
    additions = []
    for item in attempts:
        row = {**item, "created_at": created_at}
        if dedupe_market_candidates and _is_market_candidate(row):
            signature = _jsonl_signature(row)
            if signature in market_signatures:
                continue
            market_signatures.add(signature)
        additions.append(row)
    rows = [*existing_rows, *additions]
    with path.open("w", encoding="utf-8") as handle:
        for item in rows:
            handle.write(_json_text(item) + "\n")
    return len(additions)


def load_recent_cow_execution_attempts_jsonl(
    path: Path,
    limit: int = 50,
    *,
    networks: list[str] | None = None,
    retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS,
) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    wanted = {str(item).strip().lower() for item in networks or [] if str(item).strip()}
    cutoff = None
    if retention_days:
        cutoff = datetime.now(timezone.utc).timestamp() - (max(1, int(retention_days)) * 86400)
        cutoff = datetime.fromtimestamp(cutoff, tz=timezone.utc)
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = []
    network_counts: dict[str, int] = {}
    global_limit = max(1, int(limit))
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if wanted and str(payload.get("network") or "").lower() not in wanted:
            continue
        if not _within_retention(payload, cutoff):
            continue
        network = str(payload.get("network") or "").lower()
        if wanted:
            if network_counts.get(network, 0) >= global_limit:
                continue
            network_counts[network] = network_counts.get(network, 0) + 1
        rows.append(payload)
        if wanted and wanted.issubset({key for key, count in network_counts.items() if count >= global_limit}):
            break
        if not wanted and len(rows) >= global_limit:
            break
    return rows


def load_recent_cow_execution_attempts(
    database_url: str,
    limit: int = 50,
    *,
    networks: list[str] | None = None,
    retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS,
) -> list[dict[str, Any]]:
    ensure_cow_execution_attempts_table(database_url)
    wanted = [str(item).strip().lower() for item in networks or [] if str(item).strip()]
    where = ["created_at >= NOW() - (%s * INTERVAL '1 day')"]
    params: list[Any] = [max(1, int(retention_days))]
    if wanted:
        where.append("LOWER(network) = ANY(%s)")
        params.append(wanted)
    params.append(max(1, int(limit)))
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            if wanted:
                cursor.execute(
                    f"""
                    SELECT
                        id, observed_at, network, chain_id, owner_address,
                        pair, pair_rank, priority_reason, route_path_json,
                        state, execution_phase, checks_passed, can_submit_order,
                        order_submission_enabled, auto_execute_requested,
                        final_delta_amount, final_symbol, blocked_reasons_json,
                        error, created_at, quote_json, precheck_json, market_state_json
                    FROM (
                        SELECT
                            id, observed_at, network, chain_id, owner_address,
                            pair, pair_rank, priority_reason, route_path_json,
                            state, execution_phase, checks_passed, can_submit_order,
                            order_submission_enabled, auto_execute_requested,
                            final_delta_amount, final_symbol, blocked_reasons_json,
                            error, created_at, quote_json, precheck_json, market_state_json,
                            ROW_NUMBER() OVER (
                                PARTITION BY LOWER(network)
                                ORDER BY created_at DESC, id DESC
                            ) AS network_row_number
                        FROM cow_execution_attempts
                        WHERE {" AND ".join(where)}
                    ) scoped
                    WHERE network_row_number <= %s
                    ORDER BY created_at DESC, id DESC
                    """,
                    tuple(params),
                )
            else:
                cursor.execute(
                    f"""
                    SELECT
                        id, observed_at, network, chain_id, owner_address,
                        pair, pair_rank, priority_reason, route_path_json,
                        state, execution_phase, checks_passed, can_submit_order,
                        order_submission_enabled, auto_execute_requested,
                        final_delta_amount, final_symbol, blocked_reasons_json,
                        error, created_at, quote_json, precheck_json, market_state_json
                    FROM cow_execution_attempts
                    WHERE {" AND ".join(where)}
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    tuple(params),
                )
            rows = cursor.fetchall()
    result = []
    for row in rows:
        result.append(
            {
                "id": int(row[0]),
                "observed_at": row[1].isoformat() if row[1] else None,
                "network": row[2],
                "chain_id": row[3],
                "owner_address": row[4],
                "pair": row[5],
                "pair_rank": row[6],
                "priority_reason": row[7],
                "route_path": json.loads(row[8] or "[]"),
                "state": row[9],
                "execution_phase": row[10],
                "checks_passed": bool(row[11]),
                "can_submit_order": bool(row[12]),
                "order_submission_enabled": bool(row[13]),
                "auto_execute_requested": bool(row[14]),
                "final_delta_amount": row[15],
                "final_symbol": row[16],
                "blocked_reasons": _json_loads(row[17], []),
                "error": row[18],
                "created_at": row[19].isoformat() if row[19] else None,
                "quote": _json_loads(row[20], {}),
                "precheck": _json_loads(row[21], {}),
                "market_state": _json_loads(row[22], {}),
            }
        )
    return result
