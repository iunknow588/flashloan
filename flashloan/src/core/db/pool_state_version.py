"""ARCH-1 pool-state version fingerprint.

ARCH-1 (2026-11-12) replaces the blind "wait for the next 60 second tick"
with an **immediate recomputation trigger** whenever the on-chain pool state
has actually changed. The measured production symptom was
``reviewAgeSeconds=66130``: a review sat for more than eighteen hours
because nothing woke the scanner when the pool moved.

What counts as a version
------------------------

The version of a candidate pool is the pair

    (liquidity_block_number, sqrt_price_x96)

``liquidity_block_number`` says *when* the pool state was observed and
``sqrt_price_x96`` says *what* the price was. Together they change exactly
when the state that the recommendation depends on has moved.

What deliberately does **not** count as a version
-------------------------------------------------

* **No TTL.** A time-to-live would wake the scanner on a timer, which is
  precisely the behaviour ARCH-1 replaces.
* **No ``recent_swap_active``.** Every ``recent_swap_active`` value in the
  production snapshot table is ``NULL`` (4669/4669), so using it would make
  the trigger permanently silent.
* **No wall-clock timestamp.** ``liquidity_read_at`` moves whenever a
  refresh runs, even when the observed state is byte-identical; treating it
  as a version would turn the trigger back into a timer.

The fingerprint
---------------

``pool_state_fingerprint`` reduces a set of pool rows to one comparable
value. Two fingerprints are equal exactly when no pool changed its version
pair. The fingerprint is **order independent** (it sorts) so that a
re-read of the same rows in a different order does not look like a change.

Boundaries
----------

* A pool whose version fields are both missing contributes a stable
  ``unmeasured`` marker. It never contributes a fabricated version, and it
  never makes a measured pool look changed.
* A pool that disappears from the table changes the fingerprint, because
  the set of pools changed.
* The fingerprint is a **read-only observation**. It never writes, never
  mutates the database and never decides admission.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable


__all__ = [
    "PoolStateVersion",
    "pool_state_version",
    "pool_state_fingerprint",
    "pool_state_changed",
]


# The marker used for a pool whose version pair was never observed. It is a
# constant, so an unmeasured pool neither fabricates a version nor makes
# the fingerprint change between two reads.
UNMEASURED_VERSION = "unmeasured"


class PoolStateVersion(tuple):
    """The immutable version pair of one pool.

    It behaves as ``(block_number, sqrt_price_x96)`` with both members
    normalised to strings so that the value is directly comparable and
    directly serialisable.
    """

    __slots__ = ()

    def __new__(cls, block_number: Any = None, sqrt_price_x96: Any = None) -> "PoolStateVersion":
        return super().__new__(cls, (_version_text(block_number), _version_text(sqrt_price_x96)))

    @property
    def block_number(self) -> str:
        return self[0]

    @property
    def sqrt_price_x96(self) -> str:
        return self[1]

    @property
    def measured(self) -> bool:
        """Whether this version came from an actual observation."""
        return UNMEASURED_VERSION not in self

    def as_dict(self) -> dict[str, Any]:
        return {
            "blockNumber": None if self.block_number == UNMEASURED_VERSION else self.block_number,
            "sqrtPriceX96": None if self.sqrt_price_x96 == UNMEASURED_VERSION else self.sqrt_price_x96,
            "measured": self.measured,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"PoolStateVersion(block_number={self.block_number!r}, sqrt_price_x96={self.sqrt_price_x96!r})"


def _version_text(value: Any) -> str:
    """Normalise one version member to a stable comparable string."""
    if value is None:
        return UNMEASURED_VERSION
    if isinstance(value, bool):
        # A boolean is never a valid block number or price; treating it as
        # such would fabricate a version out of a type confusion.
        return UNMEASURED_VERSION
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # ``repr`` keeps the value round-trippable and avoids the locale and
        # precision surprises of ``str`` on very large magnitudes.
        return repr(value)
    text = str(value).strip()
    return text if text else UNMEASURED_VERSION


def pool_state_version(pool: dict[str, Any] | None) -> PoolStateVersion:
    """Return the version pair of one pool row.

    The row may use either the snake_case database column names or the
    camelCase names used by the page payloads.
    """
    if not isinstance(pool, dict):
        return PoolStateVersion()
    block_number = pool.get("liquidity_block_number")
    if block_number is None:
        block_number = pool.get("liquidityBlockNumber")
    sqrt_price = pool.get("sqrt_price_x96")
    if sqrt_price is None:
        sqrt_price = pool.get("sqrtPriceX96")
    return PoolStateVersion(block_number, sqrt_price)


def pool_state_fingerprint(pools: Iterable[dict[str, Any]] | None) -> str:
    """Reduce pool rows to one order-independent fingerprint string.

    The fingerprint is stable across re-reads of the same rows and changes
    whenever a pool appears, disappears, or changes its version pair.

    The result is a SHA-256 digest rather than the joined rows. The
    production snapshot table holds several thousand pools, and the joined
    form is half a megabyte of string that would then be kept in memory
    for the lifetime of the scanner. The digest is fixed size, compares
    just as cheaply, and carries the same change detection property.

    The digest is over a **sorted** entry list, so it is order independent
    by construction rather than by a second comparison step.
    """
    if not pools:
        return ""
    entries: list[str] = []
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        pool_id = str(pool.get("pool_id") or pool.get("poolId") or "").strip().lower()
        version = pool_state_version(pool)
        entries.append(f"{pool_id}|{version.block_number}|{version.sqrt_price_x96}")
    if not entries:
        return ""
    entries.sort()
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(entry.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def pool_state_changed(
    previous_fingerprint: str | None,
    pools: Iterable[dict[str, Any]] | None,
) -> bool:
    """Whether the pool state moved since ``previous_fingerprint``.

    The first observation always counts as a change: there is no previous
    state to compare against, so the scanner has never computed a
    recommendation from this state. Returning ``False`` there would mean
    the very first scan never establishes a baseline.
    """
    if previous_fingerprint is None:
        return True
    return pool_state_fingerprint(pools) != previous_fingerprint
