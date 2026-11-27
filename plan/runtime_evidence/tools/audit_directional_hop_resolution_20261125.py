#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit that every closed-loop hop resolves in the directional layer.

The runtime resolves a hop by its **base pool** and its **direction**:
the base pool names the row, the direction names which of the two rows
of that base pool the hop walks.  This probe replays that resolution on
the **live** store, so the data the new resolution depends on is measured
before it is relied upon.

For every hop it answers:

* does a row exist for `(base pool, direction)`?
* does that row's own `direction` column agree with the hop's direction?
* do the row's own input/output tokens agree with the direction?
* does the row's `zero_for_one` agree with the hop's direction?

The store is opened **read-only** (`mode=ro` + `PRAGMA query_only = ON`).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO / "flashloan" / "db" / "local_store_4663.sqlite3"


def open_ro(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def address_of_key_id(key_id: str | None) -> str | None:
    if not key_id:
        return None
    text = key_id.strip()
    if not text:
        return None
    return text.rsplit(":", 1)[-1].strip().lower() or None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--network", default="robinhood")
    parser.add_argument("--chain-id", type=int, default=4663)
    parser.add_argument("--protocol", default="uniswap_v4")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    connection = open_ro(args.db)

    rows = connection.execute(
        "SELECT directional_pool_id, base_pool_id, direction, scope_key, "
        "token0, token1, input_token_key_id, output_token_key_id, zero_for_one, "
        "pair_direction_rank "
        "FROM manager_v4_directional_exchange_pools "
        "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3",
        (args.network, args.chain_id, args.protocol),
    ).fetchall()

    # The seat of a `(base pool, direction)` pair goes to the best ranked
    # row, exactly like the runtime registry does.
    seat: dict[tuple[str, int], dict] = {}
    for row in rows:
        key = (str(row[1]).lower(), int(row[2]))
        rank = row[9]
        current = seat.get(key)
        if current is None:
            seat[key] = {
                "directional_pool_id": row[0],
                "scope_key": row[3],
                "token0": (row[4] or "").strip().lower(),
                "token1": (row[5] or "").strip().lower(),
                "input": address_of_key_id(row[6]),
                "output": address_of_key_id(row[7]),
                "zero_for_one": int(row[8]),
                "rank": rank,
            }
            continue
        current_rank = current["rank"]
        if (current_rank is None and rank is not None) or (
            current_rank is not None and rank is not None and rank < current_rank
        ):
            seat[key] = {
                "directional_pool_id": row[0],
                "scope_key": row[3],
                "token0": (row[4] or "").strip().lower(),
                "token1": (row[5] or "").strip().lower(),
                "input": address_of_key_id(row[6]),
                "output": address_of_key_id(row[7]),
                "zero_for_one": int(row[8]),
                "rank": rank,
            }

    hops = connection.execute(
        "SELECT hop_direction, base_pool_id, directional_pool_id, directional_scope_key "
        "FROM manager_v4_closed_loop_hops "
        "WHERE network = ?1 AND chain_id = ?2 AND protocol = ?3",
        (args.network, args.chain_id, args.protocol),
    ).fetchall()

    report = {
        "database": str(args.db),
        "network": args.network,
        "chain_id": args.chain_id,
        "protocol": args.protocol,
        "read_only": True,
        "directional_rows": len(rows),
        "seat_entries": len(seat),
        "hop_rows": len(hops),
    }

    resolved = 0
    missing: list[str] = []
    direction_mismatch: list[str] = []
    token_mismatch: list[str] = []
    flag_mismatch: list[str] = []
    id_mismatch: list[str] = []
    for hop_direction, base_pool_id, hop_pool_id, hop_scope in hops:
        key = (str(base_pool_id).lower(), int(hop_direction))
        row = seat.get(key)
        if row is None:
            missing.append(f"{base_pool_id}:{hop_direction}")
            continue
        resolved += 1
        if str(hop_pool_id).lower() != str(row["directional_pool_id"]).lower():
            id_mismatch.append(f"{base_pool_id}:{hop_direction}")
        if int(row["zero_for_one"]) != int(hop_direction):
            flag_mismatch.append(f"{base_pool_id}:{hop_direction}")
        token0 = row["token0"]
        token1 = row["token1"]
        if token0 and token1:
            expected_input = token0 if int(hop_direction) == 1 else token1
            expected_output = token1 if int(hop_direction) == 1 else token0
            if row["input"] and row["output"]:
                if row["input"] != expected_input or row["output"] != expected_output:
                    token_mismatch.append(f"{base_pool_id}:{hop_direction}")

    report.update(
        {
            "hops_resolved": resolved,
            "hops_missing_from_directional_layer": len(missing),
            "missing_samples": missing[:10],
            "hops_whose_direction_flag_disagrees": len(flag_mismatch),
            "hops_whose_row_tokens_disagree_with_direction": len(token_mismatch),
            "hops_whose_seat_differs_from_published_pool_id": len(id_mismatch),
            "id_mismatch_samples": id_mismatch[:10],
        }
    )

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
