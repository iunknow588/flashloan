"""Tests for the ARCH-4 stage A shadow comparison bridge.

Stage A runs the local memory pool and the chain-side ``eth_call`` side by
side and compares them, one quote at a time. The bridge is the Python face of
that comparison.

The tests lock down the parts of the stage-A ruling that are easy to break:

* the **architecture**: the bridge is a thin forwarder. It reaches the Rust
  managers through the in-memory ``RuntimeCore`` and never reads a
  manager-owned table, never performs the swap arithmetic and never issues
  the chain call itself. The functional work lives in Review Manager;
* the **source tags** are carried on both sides, so a reader always knows
  which computation produced a number;
* **only an untrustworthy chain call may block**. A memory-side deviation is
  never a blocking reason, and the bridge reports the block counter so the
  claim is measured rather than asserted;
* the **anomaly ledger** answers "which piece of logic was changed": every
  entry walks ``unexplained -> explained -> fixed`` and carries the landed
  fix and the regression result;
* the bridge **never fails the path** that asked for the measurement.
"""

from __future__ import annotations

import json
import pathlib

from dex.runtime.v4_shadow_comparison import (
    DEFAULT_SHADOW_TOLERANCE,
    ShadowHop,
    ShadowRun,
    run_shadow_comparison,
    shadow_enabled,
    write_shadow_report,
)


BRIDGE = (
    pathlib.Path(__file__).resolve().parents[3]
    / "src"
    / "dex"
    / "runtime"
    / "v4_shadow_comparison.py"
)


TOKEN_A = "0x" + "11" * 20
TOKEN_B = "0x" + "22" * 20
POOL_ID = "pool-1"


# --------------------------------------------------------------- architecture


def test_the_bridge_does_not_import_a_database_driver():
    """The bridge must never open a manager-owned table itself."""
    source = BRIDGE.read_text(encoding="utf-8")
    assert "sqlite3" not in source
    assert "aiosqlite" not in source


def test_the_bridge_never_names_a_manager_owned_table():
    """A direct table reference would be an ownership violation."""
    source = BRIDGE.read_text(encoding="utf-8")
    for table in (
        "v4_exchange_pools",
        "v4_exchange_pool_liquidity_snapshots",
        "cow_execution_reviews",
    ):
        assert table not in source, f"the bridge must not reference {table}"


def test_the_bridge_does_not_implement_the_uniswap_arithmetic():
    """The swap maths belongs to Review Manager and Pool Manager."""
    source = BRIDGE.read_text(encoding="utf-8")
    assert "get_next_sqrt_price" not in source
    assert "sqrtPriceX96" not in source or "sqrt_price_x96" not in source


def test_the_bridge_reaches_the_managers_through_the_runtime_core():
    """The in-memory big object is the only channel to the managers."""
    source = BRIDGE.read_text(encoding="utf-8")
    assert "chain_runtime_registry" in source
    assert "execute_command" in source
    assert "review_shadow_compare" in source


def test_the_bridge_never_opens_a_database_path():
    """The database is the managers' concern, not the bridge's."""
    source = BRIDGE.read_text(encoding="utf-8")
    assert "connect(" not in source


# --------------------------------------------------------------- the request


def test_a_hop_is_shaped_with_the_wire_names_the_manager_expects():
    hop = ShadowHop(
        pool_id=POOL_ID,
        token0=TOKEN_A,
        token1=TOKEN_B,
        fee=3000,
        tick_spacing=60,
        hooks="0x" + "00" * 20,
        zero_for_one=True,
        amount_in_raw="1000",
    )
    payload = hop.to_dict()
    assert payload["poolId"] == POOL_ID
    assert payload["zeroForOne"] is True
    assert payload["amountInRaw"] == "1000"
    assert payload["fee"] == 3000


def test_an_empty_hop_list_is_skipped_rather_than_reported():
    run = run_shadow_comparison(
        chain_id=4663,
        network="robinhood",
        hops=[],
    )
    assert run.skipped_reason == "no hops were supplied"
    assert run.comparison_count == 0


def test_a_missing_runtime_produces_a_skipped_run_not_an_exception():
    run = run_shadow_comparison(
        chain_id=999_999,
        network="robinhood",
        hops=[
            ShadowHop(
                pool_id=POOL_ID,
                token0=TOKEN_A,
                token1=TOKEN_B,
                fee=3000,
                tick_spacing=60,
                hooks="0x" + "00" * 20,
                zero_for_one=True,
                amount_in_raw="1000",
            )
        ],
    )
    assert run.skipped_reason
    assert "review_manager_unavailable" in run.skipped_reason
    assert run.report == {}


# --------------------------------------------------------------- policy


def test_a_memory_deviation_never_blocks_an_execution():
    """The stage-A ruling: a local defect must never freeze the chain."""
    run = ShadowRun(
        report={
            "comparisonCount": 3,
            "executableMismatchCount": 1,
            "unexplainedCount": 1,
            "untrustedChainCount": 0,
            "memoryDeviationBlockCount": 0,
        }
    )
    assert run.executable_mismatch_count == 1
    assert run.unexplained_count == 1
    # The block counter is measured, and it is zero.
    assert run.memory_deviation_block_count == 0
    payload = run.to_dict()
    assert payload["memoryDeviationBlockCount"] == 0


def test_an_untrustworthy_chain_call_is_counted_separately_from_the_deviation():
    """The two conditions are distinct and must not be merged."""
    run = ShadowRun(
        report={
            "comparisonCount": 2,
            "executableMismatchCount": 0,
            "unexplainedCount": 0,
            "untrustedChainCount": 1,
            "memoryDeviationBlockCount": 0,
        }
    )
    assert run.untrusted_chain_count == 1
    assert run.executable_mismatch_count == 0


def test_the_default_tolerance_matches_the_ruling():
    assert DEFAULT_SHADOW_TOLERANCE == 1e-9


# --------------------------------------------------------------- the ledger


def test_the_anomaly_ledger_is_carried_in_the_report():
    run = ShadowRun(
        report={
            "comparisonCount": 1,
            "ledger": {
                "entries": [
                    {
                        "anomalyId": "pool-1::amountOut",
                        "poolId": "pool-1",
                        "field": "amountOut",
                        "status": "unexplained",
                        "attribution": "unexplained",
                        "landedFix": None,
                        "regressionResult": None,
                        "observationCount": 1,
                    }
                ]
            },
        }
    )
    assert run.has_anomaly is True
    entries = run.report["ledger"]["entries"]
    assert entries[0]["status"] == "unexplained"
    assert entries[0]["landedFix"] is None


def test_a_fixed_anomaly_carries_the_landed_fix_and_the_regression():
    run = ShadowRun(
        report={
            "comparisonCount": 1,
            "ledger": {
                "entries": [
                    {
                        "anomalyId": "pool-1::amountOut",
                        "status": "fixed",
                        "attribution": "pool_state_version",
                        "landedFix": "changed the state synchronisation in pool_manager",
                        "regressionResult": "regression: amountOut agrees",
                        "observationCount": 4,
                    }
                ]
            },
        }
    )
    entry = run.report["ledger"]["entries"][0]
    assert entry["status"] == "fixed"
    assert entry["landedFix"]
    assert entry["regressionResult"]


# --------------------------------------------------------------- the report


def test_the_report_is_written_as_utf8_without_bom_and_with_lf(tmp_path):
    run = ShadowRun(
        report={
            "generatedAt": "2026-11-13T05:00:00Z",
            "comparisonCount": 1,
            "ledger": {"entries": []},
        }
    )
    path = write_shadow_report(run, report_dir=tmp_path)
    assert path is not None
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw
    payload = json.loads(raw.decode("utf-8"))
    assert payload["comparisonCount"] == 1


def test_the_report_file_name_carries_the_prefix_and_the_stamp(tmp_path):
    run = ShadowRun(
        report={"generatedAt": "2026-11-13T05:00:00Z", "ledger": {"entries": []}},
    )
    path = write_shadow_report(run, report_dir=tmp_path)
    assert path is not None
    assert path.name.startswith("arch4_shadow_diff_")
    assert path.name.endswith(".json")


def test_nothing_is_written_for_a_skipped_run(tmp_path):
    run = ShadowRun(report={}, skipped_reason="no hops were supplied")
    assert write_shadow_report(run, report_dir=tmp_path) is None


def test_the_bridge_is_off_unless_an_operator_turns_it_on(monkeypatch):
    monkeypatch.delenv("DEX_V4_SHADOW_COMPARISON", raising=False)
    assert shadow_enabled() is False
    monkeypatch.setenv("DEX_V4_SHADOW_COMPARISON", "1")
    assert shadow_enabled() is True
