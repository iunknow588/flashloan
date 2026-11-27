"""A/B the USDG valuation snapshot source on the live store (read-only).

The closed loop layer publishes the USDG value of the borrow token.  That
value is derived from the pool tick states, and the two candidate sources
are:

  A ("current")  the market columns of the base pool tables
                 (v4_exchange_pools_candidate_exchange / _core4)
  B ("proposed") the market columns of v4_exchange_pool_liquidity_snapshots
                 -- the table the liquidity worker refreshes -- while the
                 static facts (fee / tick_spacing / token key ids) still come
                 from the base pool tables.

The script replicates the manager's own valuation rule (the first pool in
pool id order that pairs the token with USDG wins) and compares the two
sources at three levels: pool state, token valuation, published loop row.

The replication is validated against the values the live closed loop rows
already publish, so the "current" side is not an assumption.

The store is opened read-only: mode=ro plus PRAGMA query_only.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

STORE = Path(r"E:\2026OPC大赛\flashLoan\flashloan\db\local_store_4663.sqlite3")
REPORT = Path(
    r"E:\2026OPC大赛\flashLoan\plan\runtime_evidence\reports"
    r"\ab_valuation_snapshot_source_20261125.json"
)
NETWORK = "robinhood"
CHAIN_ID = 4663
BASE_TABLES = ("v4_exchange_pools_candidate_exchange", "v4_exchange_pools_core4")
SNAPSHOT_TABLE = "v4_exchange_pool_liquidity_snapshots"
Q96 = 1 << 96
Q192 = Q96 * Q96


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{STORE}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
        (name,),
    ).fetchone()
    return bool(row and row[0])


def read_decimals(connection: sqlite3.Connection) -> dict[str, int]:
    decimals: dict[str, int] = {}
    for key_id, value in connection.execute(
        "SELECT token_key_id, token_decimals FROM token_library_catalog"
        " WHERE network = ? AND chain_id = ? AND admission_status = 'admitted'"
        " ORDER BY token_key_id",
        (NETWORK, CHAIN_ID),
    ):
        if value is None or value < 0 or value > 255:
            continue
        decimals[str(key_id)] = int(value)
    return decimals


def read_usdg_token(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        "SELECT token_key_id FROM token_library_catalog"
        " WHERE network = ? AND chain_id = ? AND token_symbol = 'USDG'"
        "   AND admission_status = 'admitted'"
        " ORDER BY CASE library_key"
        "   WHEN 'core' THEN 0 WHEN 'stocks' THEN 1 WHEN 'test' THEN 2 ELSE 3 END"
        " LIMIT 1",
        (NETWORK, CHAIN_ID),
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def read_static_facts(connection: sqlite3.Connection) -> dict[str, dict]:
    """The static facts of every base pool, plus the market columns."""
    facts: dict[str, dict] = {}
    for table in BASE_TABLES:
        if not table_exists(connection, table):
            continue
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        required = {
            "sqrt_price_x96",
            "tick",
            "liquidity",
            "fee",
            "tick_spacing",
            "token0_key_id",
            "token1_key_id",
        }
        if not required.issubset(columns):
            continue
        for row in connection.execute(
            f"SELECT pool_id, sqrt_price_x96, tick, liquidity, fee, tick_spacing,"
            "       token0_key_id, token1_key_id"
            f"  FROM {table} WHERE network = ? AND chain_id = ?",
            (NETWORK, CHAIN_ID),
        ):
            pool_id = str(row[0])
            facts.setdefault(
                pool_id,
                {
                    "pool_id": pool_id,
                    "base_sqrt_price_x96": row[1],
                    "base_tick": row[2],
                    "base_liquidity": row[3],
                    "fee": row[4],
                    "tick_spacing": row[5],
                    "token0_key_id": row[6],
                    "token1_key_id": row[7],
                    "first_table": table,
                },
            )
    return facts


def read_snapshot_market(connection: sqlite3.Connection) -> dict[str, dict]:
    if not table_exists(connection, SNAPSHOT_TABLE):
        return {}
    market: dict[str, dict] = {}
    for row in connection.execute(
        "SELECT pool_id, sqrt_price_x96, tick, liquidity FROM "
        f"{SNAPSHOT_TABLE} WHERE network = ? AND chain_id = ? AND protocol = 'uniswap_v4'",
        (NETWORK, CHAIN_ID),
    ):
        market[str(row[0])] = {
            "snapshot_sqrt_price_x96": row[1],
            "snapshot_tick": row[2],
            "snapshot_liquidity": row[3],
        }
    return market


def usable(fact: dict, market: dict | None) -> dict | None:
    """Apply the manager's own publication gates to one pool."""
    if market is None:
        raw_price = fact.get("base_sqrt_price_x96")
        raw_tick = fact.get("base_tick")
        raw_liquidity = fact.get("base_liquidity")
    else:
        raw_price = market.get("snapshot_sqrt_price_x96")
        raw_tick = market.get("snapshot_tick")
        raw_liquidity = market.get("snapshot_liquidity")
    try:
        sqrt_price_x96 = int(str(raw_price).strip())
    except (TypeError, ValueError):
        return None
    if sqrt_price_x96 <= 0:
        return None
    try:
        liquidity = int(str(raw_liquidity).strip())
    except (TypeError, ValueError):
        return None
    if liquidity <= 0:
        return None
    if raw_tick is None:
        return None
    fee = fact["fee"]
    if fee is None or int(fee) <= 0:
        return None
    tick_spacing = fact["tick_spacing"]
    if tick_spacing is None or int(tick_spacing) <= 0:
        return None
    token0 = str(fact["token0_key_id"] or "").strip()
    token1 = str(fact["token1_key_id"] or "").strip()
    if not token0 or not token1:
        return None
    return {
        "sqrt_price_x96": sqrt_price_x96,
        "token0_key_id": token0,
        "token1_key_id": token1,
    }


def build_states(
    facts: dict[str, dict], market: dict[str, dict], use_snapshot: bool
) -> dict[str, dict]:
    states: dict[str, dict] = {}
    for pool_id in sorted(facts):
        fact = facts[pool_id]
        snapshot = market.get(pool_id) if use_snapshot else None
        state = usable(fact, snapshot)
        if state is None:
            continue
        states.setdefault(pool_id, state)
    return states


def value_tokens_in_usdg(
    states: dict[str, dict], usdg_token_key_id: str, decimals: dict[str, int]
) -> dict[str, dict]:
    valuations: dict[str, dict] = {}
    # The quote asset itself is worth one whole unit by definition.
    usdg_decimals = decimals.get(usdg_token_key_id)
    if usdg_decimals is not None:
        valuations[usdg_token_key_id] = {
            "token_key_id": usdg_token_key_id,
            "value_usdg_raw": 10**usdg_decimals,
            "pool_id": "stablecoin_unit_identity",
        }
    for pool_id in sorted(states):
        state = states[pool_id]
        token0 = state["token0_key_id"]
        token1 = state["token1_key_id"]
        token0_is_usdg = token0 == usdg_token_key_id
        token1_is_usdg = token1 == usdg_token_key_id
        if token0_is_usdg == token1_is_usdg:
            continue
        token_key_id = token1 if token0_is_usdg else token0
        token_decimals = decimals.get(token_key_id)
        if token_decimals is None:
            continue
        squared = state["sqrt_price_x96"] ** 2
        if squared == 0:
            continue
        token_scale = 10**token_decimals
        if token0_is_usdg:
            rate = (Q192 * token_scale) // squared
        else:
            rate = (squared * token_scale) // Q192
        if rate == 0:
            continue
        valuations.setdefault(
            token_key_id,
            {
                "token_key_id": token_key_id,
                "value_usdg_raw": rate,
                "pool_id": pool_id,
            },
        )
    return valuations


def main() -> None:
    connection = connect()
    try:
        decimals = read_decimals(connection)
        usdg_token_key_id = read_usdg_token(connection)
        facts = read_static_facts(connection)
        market = read_snapshot_market(connection)
    finally:
        connection.close()
    if not usdg_token_key_id:
        raise SystemExit("the store has no admitted USDG token")

    states_a = build_states(facts, market, use_snapshot=False)
    states_b = build_states(facts, market, use_snapshot=True)
    valuations_a = value_tokens_in_usdg(states_a, usdg_token_key_id, decimals)
    valuations_b = value_tokens_in_usdg(states_b, usdg_token_key_id, decimals)

    only_a = sorted(set(states_a) - set(states_b))
    only_b = sorted(set(states_b) - set(states_a))
    changed_price = sorted(
        pool_id
        for pool_id in set(states_a) & set(states_b)
        if states_a[pool_id]["sqrt_price_x96"] != states_b[pool_id]["sqrt_price_x96"]
    )

    valuation_changed = []
    for token_key_id in sorted(set(valuations_a) | set(valuations_b)):
        left = valuations_a.get(token_key_id)
        right = valuations_b.get(token_key_id)
        if left is None:
            valuation_changed.append(
                {"token_key_id": token_key_id, "kind": "added", "after": right}
            )
            continue
        if right is None:
            valuation_changed.append(
                {"token_key_id": token_key_id, "kind": "removed", "before": left}
            )
            continue
        if left["value_usdg_raw"] != right["value_usdg_raw"]:
            valuation_changed.append(
                {
                    "token_key_id": token_key_id,
                    "kind": "value_changed",
                    "before": left,
                    "after": right,
                    "ratio": (
                        right["value_usdg_raw"] / left["value_usdg_raw"]
                        if left["value_usdg_raw"]
                        else None
                    ),
                }
            )
        elif left["pool_id"] != right["pool_id"]:
            valuation_changed.append(
                {
                    "token_key_id": token_key_id,
                    "kind": "pool_changed",
                    "before": left,
                    "after": right,
                }
            )

    # Validate the replication against what the live rows already publish.
    connection = connect()
    try:
        published_rows = connection.execute(
            "SELECT anchor_token_key_id, borrow_token_usdg_value_raw FROM manager_v4_closed_loops"
            " WHERE network = ? AND chain_id = ?",
            (NETWORK, CHAIN_ID),
        ).fetchall()
    finally:
        connection.close()
    published: dict[str, int] = {}
    for token_key_id, value in published_rows:
        if value is None:
            continue
        published.setdefault(str(token_key_id), int(value))
    matched = 0
    mismatched = []
    for token_key_id, value in sorted(published.items()):
        computed = valuations_a.get(token_key_id)
        if computed is None:
            mismatched.append(
                {"token_key_id": token_key_id, "published": value, "replicated": None}
            )
            continue
        if computed["value_usdg_raw"] == value:
            matched += 1
        else:
            mismatched.append(
                {
                    "token_key_id": token_key_id,
                    "published": value,
                    "replicated": computed["value_usdg_raw"],
                    "replicated_pool_id": computed["pool_id"],
                }
            )

    loop_impact = []
    for token_key_id, value in sorted(published.items()):
        after = valuations_b.get(token_key_id)
        loop_impact.append(
            {
                "anchor_token_key_id": token_key_id,
                "published_value_usdg_raw": value,
                "after_value_usdg_raw": (
                    after["value_usdg_raw"] if after else None
                ),
                "after_pool_id": after["pool_id"] if after else None,
            }
        )

    # How fresh is each source?  The base pool tables carry the price
    # columns the valuation reads today; the snapshot table is the one the
    # liquidity worker refreshes.
    connection = connect()
    try:
        freshness: dict[str, object] = {}
        for table in BASE_TABLES:
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if "updated_at" not in columns:
                continue
            row = connection.execute(
                f"SELECT MAX(updated_at) FROM {table} WHERE network = ? AND chain_id = ?",
                (NETWORK, CHAIN_ID),
            ).fetchone()
            freshness[table] = row[0] if row else None
        if table_exists(connection, SNAPSHOT_TABLE):
            columns = {
                row[1] for row in connection.execute(f"PRAGMA table_info({SNAPSHOT_TABLE})")
            }
            for column in ("refreshed_at", "liquidity_read_at", "observed_at"):
                if column not in columns:
                    continue
                row = connection.execute(
                    f"SELECT MAX({column}) FROM {SNAPSHOT_TABLE}"
                    " WHERE network = ? AND chain_id = ? AND protocol = 'uniswap_v4'",
                    (NETWORK, CHAIN_ID),
                ).fetchone()
                freshness[f"{SNAPSHOT_TABLE}.{column}"] = row[0] if row else None
    finally:
        connection.close()

    report = {
        "generatedAt": "2026-11-25",
        "store": str(STORE),
        "network": NETWORK,
        "chainId": CHAIN_ID,
        "usdgTokenKeyId": usdg_token_key_id,
        "poolState": {
            "basePoolRows": len(facts),
            "snapshotMarketRows": len(market),
            "statesA_base_tables": len(states_a),
            "statesB_snapshot_tables": len(states_b),
            "onlyInA": len(only_a),
            "onlyInB": len(only_b),
            "changedPrice": len(changed_price),
            "onlyInA_sample": only_a[:10],
            "onlyInB_sample": only_b[:10],
            "changedPrice_sample": [
                {
                    "pool_id": pool_id,
                    "a_sqrt_price_x96": states_a[pool_id]["sqrt_price_x96"],
                    "b_sqrt_price_x96": states_b[pool_id]["sqrt_price_x96"],
                }
                for pool_id in changed_price[:10]
            ],
            "lostDetail": [
                {
                    "pool_id": pool_id,
                    "base_sqrt_price_x96": facts[pool_id]["base_sqrt_price_x96"],
                    "base_tick": facts[pool_id]["base_tick"],
                    "base_liquidity": facts[pool_id]["base_liquidity"],
                    "snapshot_row": market.get(pool_id),
                    "reason": (
                        "no snapshot row"
                        if pool_id not in market
                        else "snapshot row has no usable price or liquidity"
                    ),
                }
                for pool_id in only_a
            ],
        },
        "valuation": {
            "valuedTokensA": len(valuations_a),
            "valuedTokensB": len(valuations_b),
            "changed": len(valuation_changed),
            "detail": valuation_changed,
        },
        "replicationCheck": {
            "publishedAnchors": len(published),
            "matched": matched,
            "mismatched": len(mismatched),
            "mismatchSample": mismatched[:10],
        },
        "publishedLoopImpact": {
            "rows": len(loop_impact),
            "valueChanged": sum(
                1
                for item in loop_impact
                if item["published_value_usdg_raw"] != item["after_value_usdg_raw"]
            ),
            "valueLost": sum(
                1 for item in loop_impact if item["after_value_usdg_raw"] is None
            ),
            "detail": loop_impact,
        },
        "freshness": freshness,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "poolState": report["poolState"],
                "valuation": {
                    "valuedTokensA": report["valuation"]["valuedTokensA"],
                    "valuedTokensB": report["valuation"]["valuedTokensB"],
                    "changed": report["valuation"]["changed"],
                },
                "replicationCheck": report["replicationCheck"],
                "publishedLoopImpact": {
                    "rows": report["publishedLoopImpact"]["rows"],
                    "valueChanged": report["publishedLoopImpact"]["valueChanged"],
                    "valueLost": report["publishedLoopImpact"]["valueLost"],
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
