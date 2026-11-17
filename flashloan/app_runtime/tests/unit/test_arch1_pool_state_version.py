"""ARCH-1 version-triggered recomputation.

ARCH-1 (2026-11-12) replaces the blind "wait for the next fixed tick" with
an immediate recomputation whenever the on-chain pool state has moved.

These tests pin the two halves of that claim:

* the **version fingerprint** changes exactly when the state it describes
  changed, and never because of a timer, a TTL or a refresh timestamp; and
* the **scanner** queues a recomputation when the version moved, and stays
  quiet when it did not.
"""

from __future__ import annotations

import pytest


# --------------------------------------------------------------------------
# The fingerprint
# --------------------------------------------------------------------------


def _pool(pool_id: str, block: int | None, sqrt_price: str | None) -> dict:
    return {
        "pool_id": pool_id,
        "liquidity_block_number": block,
        "sqrt_price_x96": sqrt_price,
    }


def test_the_fingerprint_is_empty_for_no_pools():
    from core.db.pool_state_version import pool_state_fingerprint

    assert pool_state_fingerprint([]) == ""
    assert pool_state_fingerprint(None) == ""


def test_the_fingerprint_is_order_independent():
    from core.db.pool_state_version import pool_state_fingerprint

    rows = [_pool("0xb", 10, "100"), _pool("0xa", 11, "200")]
    assert pool_state_fingerprint(rows) == pool_state_fingerprint(list(reversed(rows)))


def test_a_new_block_number_changes_the_fingerprint():
    from core.db.pool_state_version import pool_state_fingerprint

    before = pool_state_fingerprint([_pool("0xa", 10, "100")])
    after = pool_state_fingerprint([_pool("0xa", 11, "100")])
    assert before != after


def test_a_new_sqrt_price_changes_the_fingerprint():
    from core.db.pool_state_version import pool_state_fingerprint

    before = pool_state_fingerprint([_pool("0xa", 10, "100")])
    after = pool_state_fingerprint([_pool("0xa", 10, "101")])
    assert before != after


def test_an_identical_state_keeps_the_fingerprint():
    from core.db.pool_state_version import pool_state_fingerprint

    rows = [_pool("0xa", 10, "100")]
    assert pool_state_fingerprint(rows) == pool_state_fingerprint(list(rows))


def test_a_refresh_timestamp_alone_does_not_change_the_fingerprint():
    """A refresh that observed the same state is not a version change.

    This is the property that keeps ARCH-1 from degenerating back into a
    timer: ``liquidity_read_at`` moves on every refresh even when the
    observed state is byte-identical.
    """
    from core.db.pool_state_version import pool_state_fingerprint

    before = {
        "pool_id": "0xa",
        "liquidity_block_number": 10,
        "sqrt_price_x96": "100",
        "liquidity_read_at": "2026-11-12T00:00:00Z",
        "recent_swap_active": None,
    }
    after = dict(before)
    after["liquidity_read_at"] = "2026-11-12T00:05:00Z"
    after["recent_swap_active"] = 1
    assert pool_state_fingerprint([before]) == pool_state_fingerprint([after])


def test_a_disappeared_pool_changes_the_fingerprint():
    from core.db.pool_state_version import pool_state_fingerprint

    before = pool_state_fingerprint([_pool("0xa", 10, "100"), _pool("0xb", 10, "100")])
    after = pool_state_fingerprint([_pool("0xa", 10, "100")])
    assert before != after


def test_an_unmeasured_pool_contributes_a_stable_marker():
    from core.db.pool_state_version import (
        UNMEASURED_VERSION,
        pool_state_fingerprint,
        pool_state_version,
    )

    version = pool_state_version({"pool_id": "0xa"})
    assert version.block_number == UNMEASURED_VERSION
    assert version.sqrt_price_x96 == UNMEASURED_VERSION
    assert version.measured is False
    # Two reads of the same unmeasured row agree, so an unmeasured pool
    # never fabricates a change.
    assert pool_state_fingerprint([{"pool_id": "0xa"}]) == pool_state_fingerprint(
        [{"pool_id": "0xa"}]
    )


def test_a_measured_pool_is_reported_as_measured():
    from core.db.pool_state_version import pool_state_version

    version = pool_state_version({"liquidity_block_number": 10, "sqrt_price_x96": "100"})
    assert version.measured is True
    assert version.block_number == "10"
    assert version.sqrt_price_x96 == "100"


def test_the_camel_case_column_names_are_accepted():
    from core.db.pool_state_version import pool_state_version

    version = pool_state_version(
        {"liquidityBlockNumber": 42, "sqrtPriceX96": "999"}
    )
    assert version.block_number == "42"
    assert version.sqrt_price_x96 == "999"


def test_a_boolean_block_number_is_not_a_version():
    """A type confusion must not fabricate a version."""
    from core.db.pool_state_version import UNMEASURED_VERSION, pool_state_version

    version = pool_state_version({"liquidity_block_number": True, "sqrt_price_x96": "1"})
    assert version.block_number == UNMEASURED_VERSION


def test_the_first_observation_counts_as_a_change():
    from core.db.pool_state_version import pool_state_changed

    assert pool_state_changed(None, [_pool("0xa", 10, "100")]) is True


def test_an_unchanged_state_does_not_count_as_a_change():
    from core.db.pool_state_version import pool_state_changed, pool_state_fingerprint

    rows = [_pool("0xa", 10, "100")]
    fingerprint = pool_state_fingerprint(rows)
    assert pool_state_changed(fingerprint, rows) is False


# --------------------------------------------------------------------------
# The scanner trigger
# --------------------------------------------------------------------------


def _scanner(monkeypatch, rows: list[dict], *, error: str | None = None):
    """Build a scanner whose pool-state reader returns fixed rows."""
    from dex.runtime.dex_opportunity_scanner import DexOpportunityScanner

    scanner = DexOpportunityScanner(
        quote_payload_provider=lambda *, chain_id=None, protocols=None: {"routes": []},
        periodic_scan_enabled=False,
        record_opportunities=False,
    )
    monkeypatch.setattr(
        scanner,
        "_pool_state_rows",
        lambda chain_id: (list(rows), error),
    )
    return scanner


def test_the_first_trigger_queues_a_scan(monkeypatch):
    scanner = _scanner(monkeypatch, [_pool("0xa", 10, "100")])

    report = scanner.trigger_scan_on_pool_state_change(chain_id=4663)

    assert report["changed"] is True
    assert report["queued"] is True
    assert len(scanner._queued_scans) == 1


def test_an_unchanged_state_does_not_queue_a_scan(monkeypatch):
    scanner = _scanner(monkeypatch, [_pool("0xa", 10, "100")])
    scanner.trigger_scan_on_pool_state_change(chain_id=4663)
    scanner._queued_scans.clear()

    report = scanner.trigger_scan_on_pool_state_change(chain_id=4663)

    assert report["changed"] is False
    assert report["queued"] is False
    assert len(scanner._queued_scans) == 0


def test_a_changed_block_number_queues_a_second_scan(monkeypatch):
    rows = [_pool("0xa", 10, "100")]
    scanner = _scanner(monkeypatch, rows)
    scanner.trigger_scan_on_pool_state_change(chain_id=4663)
    scanner._queued_scans.clear()

    rows[0]["liquidity_block_number"] = 11
    report = scanner.trigger_scan_on_pool_state_change(chain_id=4663)

    assert report["changed"] is True
    assert report["queued"] is True
    assert len(scanner._queued_scans) == 1


def test_a_refresh_timestamp_alone_does_not_queue_a_scan(monkeypatch):
    rows = [
        {
            "pool_id": "0xa",
            "liquidity_block_number": 10,
            "sqrt_price_x96": "100",
            "liquidity_read_at": "2026-11-12T00:00:00Z",
        }
    ]
    scanner = _scanner(monkeypatch, rows)
    scanner.trigger_scan_on_pool_state_change(chain_id=4663)
    scanner._queued_scans.clear()

    rows[0]["liquidity_read_at"] = "2026-11-12T00:05:00Z"
    report = scanner.trigger_scan_on_pool_state_change(chain_id=4663)

    assert report["changed"] is False
    assert len(scanner._queued_scans) == 0


def test_a_burst_of_changes_queues_only_one_scan(monkeypatch):
    """One pending scan is enough; the queue must not multiply work."""
    scanner = _scanner(monkeypatch, [_pool("0xa", 10, "100")])

    first = scanner.trigger_scan_on_pool_state_change(chain_id=4663)
    second = scanner.trigger_scan_on_pool_state_change(chain_id=4663)

    assert first["queued"] is True
    # The baseline was already refreshed, so the second call sees no change.
    assert second["changed"] is False
    assert len(scanner._queued_scans) == 1


def test_the_trigger_does_not_start_a_worker_thread(monkeypatch):
    """The trigger runs inside the run loop, so it must not join threads."""
    scanner = _scanner(monkeypatch, [_pool("0xa", 10, "100")])

    started: list[bool] = []
    monkeypatch.setattr(scanner, "start", lambda **kwargs: started.append(True))

    scanner.trigger_scan_on_pool_state_change(chain_id=4663)

    assert started == []
    assert len(scanner._queued_scans) == 1


def test_the_run_loop_checks_the_version_before_the_periodic_tick(monkeypatch):
    """The version trigger is independent of the periodic interval."""
    scanner = _scanner(monkeypatch, [_pool("0xa", 10, "100")])
    scanner._watched[(4663, ())] = (4663, ())

    calls: list[str] = []
    monkeypatch.setattr(
        scanner,
        "_trigger_scans_on_pool_state_change",
        lambda watched: calls.append("version") or [],
    )
    monkeypatch.setattr(scanner, "_stop", _StopAfterOneIteration())
    monkeypatch.setattr(scanner, "_scan_one", lambda **kwargs: calls.append("scan"))

    scanner.run_forever()

    assert calls[0] == "version"


class _StopAfterOneIteration:
    """A stop event that lets exactly one loop iteration run."""

    def __init__(self) -> None:
        self._calls = 0

    def is_set(self) -> bool:
        self._calls += 1
        return self._calls > 1

    def wait(self, timeout: float | None = None) -> None:
        return None


# --------------------------------------------------------------------------
# Failures must be visible
# --------------------------------------------------------------------------


def test_a_read_failure_is_reported_and_never_looks_like_no_change(monkeypatch):
    """A broken read must not masquerade as an idle trigger.

    If a read failure silently returned "no change", the trigger would
    look healthy while doing nothing, which is exactly the failure mode
    this module exists to avoid.
    """
    scanner = _scanner(monkeypatch, [], error="disk unavailable")

    report = scanner.trigger_scan_on_pool_state_change(chain_id=4663)

    assert report["available"] is False
    assert report["error"] == "disk unavailable"
    assert report["changed"] is False
    assert report["queued"] is False


def test_a_read_failure_is_visible_in_the_status(monkeypatch):
    scanner = _scanner(monkeypatch, [], error="disk unavailable")

    scanner.trigger_scan_on_pool_state_change(chain_id=4663)
    status = scanner.pool_state_status(chain_id=4663)

    assert status["available"] is False
    assert status["error"] == "disk unavailable"


def test_a_successful_read_clears_a_previous_error(monkeypatch):
    scanner = _scanner(monkeypatch, [], error="disk unavailable")
    scanner.trigger_scan_on_pool_state_change(chain_id=4663)

    monkeypatch.setattr(
        scanner,
        "_pool_state_rows",
        lambda chain_id: ([_pool("0xa", 10, "100")], None),
    )
    report = scanner.trigger_scan_on_pool_state_change(chain_id=4663)

    assert report["available"] is True
    assert scanner.pool_state_status(chain_id=4663)["error"] is None


# --------------------------------------------------------------------------
# The version check is throttled
# --------------------------------------------------------------------------


def test_the_version_check_is_throttled(monkeypatch):
    """The check reads the whole snapshot table, so it must not run on
    every loop iteration."""
    scanner = _scanner(monkeypatch, [_pool("0xa", 10, "100")])
    scanner._watched[(4663, ())] = (4663, ())

    calls: list[int] = []
    monkeypatch.setattr(
        scanner,
        "_pool_state_rows",
        lambda chain_id: (calls.append(1) or ([_pool("0xa", 10, "100")], None)),
    )

    scanner._trigger_scans_on_pool_state_change([(4663, ())])
    scanner._trigger_scans_on_pool_state_change([(4663, ())])

    # The first call establishes the baseline; the second is throttled.
    assert len(calls) == 1


def test_the_throttle_window_expires(monkeypatch):
    scanner = _scanner(monkeypatch, [_pool("0xa", 10, "100")])
    scanner._watched[(4663, ())] = (4663, ())

    calls: list[int] = []
    monkeypatch.setattr(
        scanner,
        "_pool_state_rows",
        lambda chain_id: (calls.append(1) or ([_pool("0xa", 10, "100")], None)),
    )

    scanner._trigger_scans_on_pool_state_change([(4663, ())])
    with scanner._lock:
        scanner._pool_state_last_checked_at[scanner._pool_state_scope_key(4663, ())] = 0.0
    scanner._trigger_scans_on_pool_state_change([(4663, ())])

    assert len(calls) == 2
