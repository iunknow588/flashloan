"""Regression tests for the ARCH-2b production wiring of the parity bridge.

The bridge module itself is covered by ``test_v4_offchain_parity_bridge``.
What these tests lock down is the *other* half of ARCH-2b: the bridge has to
be reachable from the review path, and it has to reach the end of that path
without raising.

Why this file exists
--------------------

``dex.runtime.rust_closed_loop`` calls the bridge from
``_offchain_parity_observation``. The bridge is guarded so that it can never
raise into a review, but that guard only covers what happens *inside* the
bridge call. A mistake in the calling code -- a name that is not imported
into the local scope, a typo in a keyword argument -- is raised by Python
*before* the bridge is ever entered, so the bridge's own error handling never
sees it.

Such a mistake is invisible until the code path that triggers it actually
runs. The early-return branches of ``_offchain_parity_observation`` are
ordered so that the most common reasons for doing nothing (the bridge is
disabled, the pool keys are missing, the database path cannot be resolved)
all happen *before* the interesting work. A defect that sits after those
returns therefore stays dormant for every ordinary review and only fires
during the exact measurement run the bridge exists for.

These tests call the wiring function directly with every precondition
satisfied, so the defect cannot hide behind an early return.
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3

import pytest


TOKEN_A = "0x" + "11" * 20
TOKEN_B = "0x" + "22" * 20
POOL_ID = "0xpool1"


def _seed_database(path: str) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE v4_exchange_pools (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            protocol TEXT NOT NULL,
            pool_id TEXT NOT NULL,
            pool_key_json TEXT,
            token0 TEXT,
            token1 TEXT,
            fee INTEGER,
            tick_spacing INTEGER,
            hooks TEXT,
            PRIMARY KEY (network, chain_id, protocol, pool_id)
        );
        CREATE TABLE v4_exchange_pool_liquidity_snapshots (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            protocol TEXT NOT NULL,
            pool_id TEXT NOT NULL,
            liquidity TEXT,
            sqrt_price_x96 TEXT,
            liquidity_read_at TEXT,
            evidence_source TEXT,
            PRIMARY KEY (network, chain_id, protocol, pool_id)
        );
        CREATE TABLE chain_tokens (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            token_address TEXT NOT NULL,
            token_decimals INTEGER,
            PRIMARY KEY (network, chain_id, token_address)
        );
        """
    )
    pool_key = {
        "currency0": TOKEN_A,
        "currency1": TOKEN_B,
        "fee": 3000,
        "tickSpacing": 60,
        "hooks": "0x0000000000000000000000000000000000000000",
        "poolId": POOL_ID,
    }
    connection.execute(
        "INSERT INTO v4_exchange_pools VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "robinhood",
            4663,
            "uniswap_v4",
            POOL_ID,
            json.dumps(pool_key),
            TOKEN_A,
            TOKEN_B,
            3000,
            60,
            "0x0000000000000000000000000000000000000000",
        ),
    )
    connection.execute(
        "INSERT INTO v4_exchange_pool_liquidity_snapshots VALUES (?,?,?,?,?,?,?,?)",
        (
            "robinhood",
            4663,
            "uniswap_v4",
            POOL_ID,
            str(10 ** 18),
            str(1 << 96),
            "2026-11-12T00:00:00Z",
            "unit-test",
        ),
    )
    connection.commit()
    connection.close()


def _pool_key() -> dict:
    return {
        "currency0": TOKEN_A,
        "currency1": TOKEN_B,
        "fee": 3000,
        "tickSpacing": 60,
        "hooks": "0x0000000000000000000000000000000000000000",
    }


class _StubRuntime:
    """A stand-in for the in-memory RuntimeCore.

    The bridge reaches the Rust managers through the RuntimeCore, so a unit
    test that exercises the review path stubs that object rather than
    standing up a real Rust runtime.
    """

    def __init__(self, payload=None, pool_ids=None):
        self.payload = payload
        self.pool_ids = pool_ids or []
        self.calls: list[dict] = []

    def execute_command(self, command, *, request_id, idempotency_key, dry_run, options=None):
        self.calls.append(
            {
                "command": command,
                "dryRun": dry_run,
                "options": options or {},
            }
        )
        if command == "pool_resolve_keys":
            return {"data": {"poolIds": list(self.pool_ids)}}
        return {"data": self.payload or {}}


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    """A review-path call with every bridge precondition satisfied."""
    from dex.runtime import rust_closed_loop
    from dex.runtime import v4_offchain_parity_bridge as bridge

    database = str(tmp_path / "state.sqlite3")
    _seed_database(database)
    monkeypatch.setenv("DEX_V4_OFFCHAIN_PARITY_BRIDGE", "1")
    monkeypatch.setattr(
        rust_closed_loop,
        "resolve_runtime",
        lambda chain_id: {"databasePath": database},
    )
    runtime = _StubRuntime(
        payload={
            "state": "match",
            "executableOffchain": True,
            "executableOnchain": True,
            "profitOffchainRaw": 1000,
            "profitOnchainRaw": 1000,
            "profitRelativeDelta": 0.0,
            "reason": "",
        },
        pool_ids=[POOL_ID],
    )
    monkeypatch.setattr(bridge, "_runtime_core", lambda chain_id: runtime)
    return rust_closed_loop, database, runtime


def test_the_wiring_reaches_the_bridge_instead_of_raising(wired):
    """The production path must run to completion, not raise.

    Every precondition is satisfied here: the bridge is enabled, the route
    has addresses, the selected pool keys are present, the database path
    resolves, the probe amount is positive and the pool key resolves to a
    live pool. A defect in the calling code therefore cannot hide behind an
    early return.
    """
    rust_closed_loop, _database, _runtime = wired
    payload = rust_closed_loop._offchain_parity_observation(
        route={"routeAddresses": [TOKEN_A, TOKEN_B]},
        result={
            "selectedKeys": [_pool_key()],
            "selectedAmountRaw": str(10 ** 18),
            "selectedProfitRaw": "1000",
        },
        chain_id=4663,
        evidence={},
    )
    assert payload["enabled"] is True
    assert payload["authority"] == "onchain_eth_call"
    assert payload["enforced"] is False
    assert payload["bridge"] == "offchain_v4_multihop_parity"


def test_the_wiring_never_reports_an_enforced_anomaly(wired):
    """An anomaly is reported, never enforced -- on the production path too."""
    rust_closed_loop, _database, runtime = wired
    payload = rust_closed_loop._offchain_parity_observation(
        route={"routeAddresses": [TOKEN_A, TOKEN_B]},
        result={
            "selectedKeys": [_pool_key()],
            "selectedAmountRaw": str(10 ** 18),
            # A profit that cannot be reproduced off-chain is an anomaly.
            "selectedProfitRaw": str(10 ** 30),
        },
        chain_id=4663,
        evidence={},
    )
    assert payload["enabled"] is True
    assert payload["enforced"] is False
    assert payload["verdicts"], "the bridge must produce a verdict"


def test_a_disabled_bridge_costs_nothing(wired, monkeypatch):
    """With the bridge off, the wiring returns immediately and cheaply."""
    rust_closed_loop, _database, _runtime = wired
    monkeypatch.delenv("DEX_V4_OFFCHAIN_PARITY_BRIDGE", raising=False)
    payload = rust_closed_loop._offchain_parity_observation(
        route={"routeAddresses": [TOKEN_A, TOKEN_B]},
        result={"selectedKeys": [_pool_key()]},
        chain_id=4663,
        evidence={},
    )
    assert payload == {
        "enabled": False,
        "anomaly": False,
        "enforced": False,
        "reason": "bridge_disabled",
    }


def test_the_network_is_resolved_from_the_chain_id(wired):
    """The network argument must be resolvable, not an unbound name.

    This is the regression for the defect where the wiring referenced a
    helper that was only imported inside other functions: the name was not
    in scope at the call site, so the bridge raised ``NameError`` on the
    real production path while every early-return branch stayed green.
    """
    rust_closed_loop, _database, _runtime = wired
    assert rust_closed_loop._network_hint_for_chain(4663) == "robinhood"


def test_the_wiring_uses_the_resolved_network(wired, monkeypatch):
    """The resolved network is what reaches the bridge."""
    rust_closed_loop, _database, runtime = wired
    rust_closed_loop._offchain_parity_observation(
        route={"routeAddresses": [TOKEN_A, TOKEN_B]},
        result={
            "selectedKeys": [_pool_key()],
            "selectedAmountRaw": str(10 ** 18),
            "selectedProfitRaw": "1000",
        },
        chain_id=4663,
        evidence={},
    )
    networks = [
        call["options"].get("network")
        for call in runtime.calls
        if call["command"] == "pool_resolve_keys"
    ]
    assert networks == ["robinhood"]


def test_the_wiring_reaches_the_managers_through_the_runtime_core(wired):
    """The review path must reach the managers only through the RuntimeCore."""
    rust_closed_loop, _database, runtime = wired
    rust_closed_loop._offchain_parity_observation(
        route={"routeAddresses": [TOKEN_A, TOKEN_B]},
        result={
            "selectedKeys": [_pool_key()],
            "selectedAmountRaw": str(10 ** 18),
            "selectedProfitRaw": "1000",
        },
        chain_id=4663,
        evidence={},
    )
    commands = [call["command"] for call in runtime.calls]
    assert commands == ["pool_resolve_keys", "review_parity_compare"]
    # Every manager call is read-only.
    assert all(call["dryRun"] is True for call in runtime.calls)
