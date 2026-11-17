"""Tests for the ARCH-2b off-chain parity bridge.

The bridge compares the off-chain three-hop simulation against the value
the chain reported, so the team can see whether the off-chain computation
still matches the chain before the chain-side preview is ever closed.

The tests lock down four things:

* the **architecture**: the bridge is a thin forwarder. It reaches the Rust
  managers through the in-memory ``RuntimeCore`` and never reads a
  manager-owned table, never performs the simulation and never owns the
  Uniswap arithmetic. This is the project's governing rule: the functional
  work lives in the Rust managers and Python only carries the request and
  shapes the answer;
* the **verdict classification** is correct: a matching pair, an
  executability disagreement, an amount disagreement and missing evidence
  are four different things;
* an **unexplained difference is an anomaly, not a blocker**. The 2026-11-12
  ruling is explicit: the chain stays authoritative, execution continues on
  the chain result, and the anomaly is a defect to repair. A regression that
  made the anomaly block would silently change the safety model, so the
  tests assert the opposite;
* the bridge **never fails the review** that asked for the measurement: a
  missing runtime, a missing pool state or a malformed route all produce a
  report with a reason, not an exception.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from dex.runtime.v4_offchain_parity_bridge import (
    ParityReport,
    ParityVerdict,
    bridge_enabled,
    evaluate_route_parity,
    resolve_pool_ids,
    summarize_reports,
)


BRIDGE = (
    pathlib.Path(__file__).resolve().parents[3]
    / "src"
    / "dex"
    / "runtime"
    / "v4_offchain_parity_bridge.py"
)


TOKEN_A = "0x" + "11" * 20
TOKEN_B = "0x" + "22" * 20
POOL_IDS = ("0xpool1",)


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
        "v4_closed_loop_hops",
    ):
        assert table not in source, f"the bridge must not reference {table}"


def test_the_bridge_does_not_implement_the_uniswap_arithmetic():
    """The simulation belongs to Review Manager, not to Python."""
    source = BRIDGE.read_text(encoding="utf-8")
    # The sqrt-price and liquidity deltas are the Uniswap maths. If they
    # appear here, Python has grown a second implementation of the chain's
    # settlement arithmetic.
    for forbidden in (
        "sqrt_price",
        "get_next_sqrt_price",
        "amount_0_delta",
        "amount_1_delta",
        "liquidity_q96",
        "Q96",
        "Q192",
    ):
        assert forbidden not in source, f"the bridge must not compute {forbidden}"


def test_the_bridge_reaches_the_managers_only_through_the_runtime_core():
    """Every manager call goes through the in-memory RuntimeCore."""
    source = BRIDGE.read_text(encoding="utf-8")
    assert "chain_runtime_registry" in source
    assert "execute_command" in source
    # The two commands the bridge relies on are the manager-owned entry
    # points: Review Manager compares, Pool Manager resolves.
    assert "review_parity_compare" in source
    assert "pool_resolve_keys" in source


def test_the_bridge_never_opens_a_database_path():
    """The database is the managers' concern, not the bridge's."""
    source = BRIDGE.read_text(encoding="utf-8")
    assert "connect(" not in source
    assert "open(" not in source


# --------------------------------------------------------------- verdicts


def _verdict(
    state: str,
    *,
    executable_offchain: bool | None = None,
    executable_onchain: bool | None = None,
    profit_relative_delta: float | None = None,
) -> ParityVerdict:
    return ParityVerdict(
        state=state,
        executable_offchain=executable_offchain,
        executable_onchain=executable_onchain,
        profit_offchain_raw=1000,
        profit_onchain_raw=1000,
        profit_relative_delta=profit_relative_delta,
        reason="",
    )


def test_a_matching_pair_is_not_an_anomaly():
    verdict = _verdict(
        "match",
        executable_offchain=True,
        executable_onchain=True,
        profit_relative_delta=0.0,
    )
    assert verdict.state == "match"
    assert not verdict.is_anomaly
    assert verdict.is_comparable
    assert verdict.profit_relative_delta == 0.0


def test_executability_disagreement_is_flagged_as_an_anomaly():
    verdict = _verdict(
        "executable_mismatch", executable_offchain=True, executable_onchain=False
    )
    assert verdict.state == "executable_mismatch"
    assert verdict.is_anomaly
    assert verdict.executable_offchain is True
    assert verdict.executable_onchain is False


def test_amount_disagreement_without_an_executability_flip_is_separate():
    verdict = _verdict(
        "amount_mismatch",
        executable_offchain=True,
        executable_onchain=True,
        profit_relative_delta=0.1,
    )
    assert verdict.state == "amount_mismatch"
    assert not verdict.is_anomaly
    assert verdict.is_comparable
    assert verdict.profit_relative_delta == pytest.approx(0.1)


def test_missing_evidence_is_not_a_disagreement():
    verdict = _verdict("not_comparable")
    assert verdict.state == "not_comparable"
    assert not verdict.is_anomaly
    assert not verdict.is_comparable


# --------------------------------------------------------------- policy


def test_an_anomaly_is_never_enforced():
    """The 2026-11-12 ruling: an unexplained difference must not block.

    If this test ever fails, the bridge has started overriding the chain,
    which would mean the off-chain result could stop a live trade.
    """
    verdict = _verdict(
        "executable_mismatch", executable_offchain=True, executable_onchain=False
    )
    assert verdict.is_anomaly
    assert verdict.to_dict()["anomaly"] is True
    # The bridge never reports itself as enforcing anything.
    assert "blocking" not in verdict.to_dict()


def test_summary_reports_anomalies_without_enforcing_them():
    report = ParityReport()
    report.verdicts.append(
        _verdict("executable_mismatch", executable_offchain=True, executable_onchain=False)
    )
    summary = summarize_reports([report])
    assert summary["anomalyCount"] == 1
    assert summary["anomaly"] is True
    assert summary["enforced"] is False
    assert report.has_anomaly is True


def test_a_clean_run_reports_no_anomaly():
    report = ParityReport()
    report.verdicts.append(
        _verdict("match", executable_offchain=True, executable_onchain=True)
    )
    summary = summarize_reports([report])
    assert summary["anomalyCount"] == 0
    assert summary["anomaly"] is False


# --------------------------------------------------------------- forwarding


class _StubRuntime:
    """A stand-in for the in-memory RuntimeCore."""

    def __init__(self, payload):
        self.payload = payload
        self.calls: list[dict] = []

    def execute_command(self, command, *, request_id, idempotency_key, dry_run, options=None):
        self.calls.append(
            {
                "command": command,
                "requestId": request_id,
                "idempotencyKey": idempotency_key,
                "dryRun": dry_run,
                "options": options or {},
            }
        )
        return self.payload


@pytest.fixture
def stub_runtime(monkeypatch):
    """Installs a stub RuntimeCore for the bridge to reach."""

    def _install(payload):
        runtime = _StubRuntime(payload)
        import dex.runtime.v4_offchain_parity_bridge as bridge

        monkeypatch.setattr(bridge, "_runtime_core", lambda chain_id: runtime)
        return runtime

    return _install


def test_the_bridge_forwards_the_route_to_review_manager(stub_runtime):
    runtime = stub_runtime(
        {
            "data": {
                "state": "match",
                "executableOffchain": True,
                "executableOnchain": True,
                "profitOffchainRaw": 1000,
                "profitOnchainRaw": 1000,
                "profitRelativeDelta": 0.0,
                "reason": "",
            }
        }
    )
    report = evaluate_route_parity(
        database_path="unused.sqlite3",
        chain_id=4663,
        network="robinhood",
        route_addresses=[TOKEN_A, TOKEN_B],
        pool_ids=[POOL_IDS[0]],
        amount_raw=10 ** 18,
        onchain_profit_raw=1000,
        onchain_executable=True,
    )
    assert report.skipped_reason == ""
    assert report.verdicts[0].state == "match"

    assert len(runtime.calls) == 1
    call = runtime.calls[0]
    assert call["command"] == "review_parity_compare"
    assert call["dryRun"] is True
    options = call["options"]
    assert options["routeAddresses"] == [TOKEN_A, TOKEN_B]
    assert options["poolIds"] == [POOL_IDS[0]]
    assert options["amountRaw"] == str(10 ** 18)
    assert options["onchainProfitRaw"] == "1000"
    assert options["onchainExecutable"] is True


def test_a_negative_chain_profit_keeps_its_sign(stub_runtime):
    """The chain reports a losing route as a negative profit."""
    runtime = stub_runtime({"data": {"state": "not_comparable", "reason": "x"}})
    evaluate_route_parity(
        database_path="unused.sqlite3",
        chain_id=4663,
        network="robinhood",
        route_addresses=[TOKEN_A, TOKEN_B],
        pool_ids=[POOL_IDS[0]],
        amount_raw=10 ** 18,
        onchain_profit_raw=-500,
        onchain_executable=False,
    )
    assert runtime.calls[0]["options"]["onchainProfitRaw"] == "-500"


def test_a_missing_runtime_is_not_an_exception(stub_runtime, monkeypatch):
    import dex.runtime.v4_offchain_parity_bridge as bridge

    def _boom(chain_id):
        raise RuntimeError("no runtime")

    monkeypatch.setattr(bridge, "_runtime_core", _boom)
    report = evaluate_route_parity(
        database_path="unused.sqlite3",
        chain_id=4663,
        network="robinhood",
        route_addresses=[TOKEN_A, TOKEN_B],
        pool_ids=[POOL_IDS[0]],
        amount_raw=10 ** 18,
        onchain_profit_raw=1000,
        onchain_executable=True,
    )
    assert report.skipped_reason
    assert report.verdicts == []
    assert report.has_anomaly is False


def test_a_non_positive_amount_is_skipped(stub_runtime):
    runtime = stub_runtime({"data": {}})
    report = evaluate_route_parity(
        database_path="unused.sqlite3",
        chain_id=4663,
        network="robinhood",
        route_addresses=[TOKEN_A, TOKEN_B],
        pool_ids=[POOL_IDS[0]],
        amount_raw=0,
        onchain_profit_raw=1000,
        onchain_executable=True,
    )
    assert "probe amount" in report.skipped_reason
    # A skipped measurement must not call the managers at all.
    assert runtime.calls == []


def test_pool_keys_are_resolved_through_pool_manager(stub_runtime):
    runtime = stub_runtime({"data": {"poolIds": [POOL_IDS[0]]}})
    resolved = resolve_pool_ids(
        "unused.sqlite3",
        chain_id=4663,
        network="robinhood",
        pool_keys=[
            {
                "currency0": TOKEN_A,
                "currency1": TOKEN_B,
                "fee": 3000,
                "tickSpacing": 60,
                "hooks": "0x0000000000000000000000000000000000000000",
            }
        ],
    )
    assert resolved == [POOL_IDS[0]]
    assert runtime.calls[0]["command"] == "pool_resolve_keys"
    assert runtime.calls[0]["options"]["poolKeys"][0]["currency0"] == TOKEN_A


def test_an_unresolved_pool_key_resolves_to_nothing(stub_runtime):
    stub_runtime({"data": {"poolIds": [""]}})
    resolved = resolve_pool_ids(
        "unused.sqlite3",
        chain_id=4663,
        network="robinhood",
        pool_keys=[
            {
                "currency0": TOKEN_A,
                "currency1": TOKEN_B,
                "fee": 3000,
                "tickSpacing": 60,
            }
        ],
    )
    assert resolved == [""]


def test_a_failed_resolution_is_not_an_exception(monkeypatch):
    import dex.runtime.v4_offchain_parity_bridge as bridge

    def _boom(chain_id):
        raise RuntimeError("no runtime")

    monkeypatch.setattr(bridge, "_runtime_core", _boom)
    resolved = resolve_pool_ids(
        "unused.sqlite3",
        chain_id=4663,
        network="robinhood",
        pool_keys=[{"currency0": TOKEN_A, "currency1": TOKEN_B, "fee": 3000}],
    )
    assert resolved == []


def test_an_empty_pool_key_list_short_circuits(stub_runtime):
    runtime = stub_runtime({"data": {"poolIds": []}})
    assert resolve_pool_ids("unused.sqlite3", chain_id=4663, network="robinhood", pool_keys=[]) == []
    assert runtime.calls == []


# --------------------------------------------------------------- enablement


def test_the_bridge_is_off_by_default(monkeypatch):
    monkeypatch.delenv("DEX_V4_OFFCHAIN_PARITY_BRIDGE", raising=False)
    assert bridge_enabled() is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
def test_the_bridge_can_be_switched_on(monkeypatch, raw):
    monkeypatch.setenv("DEX_V4_OFFCHAIN_PARITY_BRIDGE", raw)
    assert bridge_enabled() is True


def test_the_bridge_stays_off_for_an_unrecognised_value(monkeypatch):
    monkeypatch.setenv("DEX_V4_OFFCHAIN_PARITY_BRIDGE", "maybe")
    assert bridge_enabled() is False
