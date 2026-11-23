"""V4 depth-snapshot write path (ARCH-1a / R-05) — **no longer a writer**.

TASK-172 / AC-221.  ``v4_exchange_pool_liquidity_snapshots`` is the
**materialised boundary** of the pool manager's in-memory pool state:

    chain read -> pool manager in-memory state -> this table -> consumers

The table has exactly **one** writer: the Rust pool manager.  It writes the
price columns (``sqrt_price_x96`` / ``tick`` / ``liquidity``) **and** the
single-tick bounds (``single_tick_max_token0_raw`` /
``single_tick_max_token1_raw``) in one statement, so a row always describes
exactly one on-chain moment.

This module used to be the second writer.  It wrote the price columns only,
which left the bounds of an older moment sitting next to a newer price.  The
row then carried two different observations at once, every consumer of the
bound read a stale number, and writing to the row again could not repair it
because this writer never carried the bound.

The evidence rows are therefore **discarded** rather than merged.  The pool
manager measures the same on-chain state and is the only writer that can
materialise a complete row, so nothing is lost.

The public name and signature are kept because this module is re-exported by
:mod:`core.db.storage_v4_exchange` and called by
:mod:`core.db.import_v4_evidence`; it simply no longer writes.  It returns the
number of rows it refused to write so the caller reports the drop instead of
swallowing it silently.

The **read** helpers live in :mod:`core.db.storage_v4_exchange` and are
unaffected: consumers still read the table the pool manager wrote.
"""

from __future__ import annotations

from typing import Any


__all__ = ["upsert_v4_liquidity_snapshots"]


def upsert_v4_liquidity_snapshots(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    rows: list[dict[str, Any]],
) -> int:
    """Drop the evidence rows; the depth snapshot has another writer.

    See the module docstring for the full rationale (TASK-172 / AC-221).

    The parameters are kept so the call sites stay unchanged.  The return
    value is the number of rows that were **not** written.
    """
    return len(rows)
