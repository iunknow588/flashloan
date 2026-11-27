#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clone the slice of the chain store the **direction layer** reads.

The closed-loop slice of 2026-11-25 does not carry the two tables the
directional manager needs: ``pool_scope_config`` (which scopes exist and
which base table each scope reads) and ``token_library_catalog`` (the token
decimals and identities the projection copies).  Without them the
materialization has no scope to materialize, so the rehearsal of the
directional slot needs its own slice.

The live store is opened **read-only** and the clone is the only file that
is written.  The schema of every table is copied verbatim; only the rows of
the tables the layer reads are copied.

Usage:
    python clone_direction_layer_slice_20261126.py <source.sqlite3> <target.sqlite3>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import clone_closed_loop_slice_20261125 as base  # noqa: E402


EXTRA_TABLES = (
    # The scopes of the chain: which base table each scope reads.
    "pool_scope_config",
    # The token identities and decimals the projection copies.
    "token_library_catalog",
)

# ``manager_market_observation_yz_pairs`` is a **view** (migration 017) over
# ``manager_market_observation_yz_pairs_base``.  Copying the view's rows
# does not copy the observation rows: the view is created over an empty base
# table, so the closed-loop layer finds no pair and closes no loop.  The base
# table is therefore copied instead of the view.
VIEW_TABLES_REPLACED_BY_BASE = (
    ("manager_market_observation_yz_pairs", "manager_market_observation_yz_pairs_base"),
)


def main() -> int:
    tables = list(base.SLICE_TABLES)
    for view, underlying in VIEW_TABLES_REPLACED_BY_BASE:
        tables = [name for name in tables if name != view]
        if underlying not in tables:
            tables.append(underlying)
    base.SLICE_TABLES = tuple(tables) + EXTRA_TABLES
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
