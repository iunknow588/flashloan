"""Unit tests for the Rust native closed-loop worker pool bridge."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from typing import Any

import pytest

from dex.runtime import dex_opportunity_scanner_rust_mixin as rust_mixin
from dex.runtime import rust_closed_loop


ANCHOR_ADDRESS = "0x" + ("1" * 40)
Y_ADDRESS = "0x" + ("2" * 40)
Z_ADDRESS = "0x" + ("3" * 40)
FORWARD_LOOP_KEY = f"{ANCHOR_ADDRESS}:{Y_ADDRESS}:{Z_ADDRESS}"
REVERSE_LOOP_KEY = f"{ANCHOR_ADDRESS}:{Z_ADDRESS}:{Y_ADDRESS}"


def _route(symbols: list[str]) -> dict[str, Any]:
    addresses_by_symbol = {
        "AAA": "0x" + ("1" * 40),
        "BBB": "0x" + ("2" * 40),
        "CCC": "0x" + ("3" * 40),
    }
    dynamic_by_symbol: dict[str, str] = {}
    addresses = []
    for symbol in symbols:
        normalized = str(symbol).upper()
        if normalized in addresses_by_symbol:
            addresses.append(addresses_by_symbol[normalized])
            continue
        if normalized.startswith("UNRESOLVED:0X"):
            nibble = normalized.rsplit("0X", 1)[1][0]
            addresses.append("0x" + (nibble * 40))
            continue
        if normalized not in dynamic_by_symbol:
            nibble = "456789abcdef"[len(dynamic_by_symbol) % 12]
            dynamic_by_symbol[normalized] = "0x" + (nibble * 40)
        addresses.append(dynamic_by_symbol[normalized])
    return {
        "routeSymbols": symbols,
        "routeAddresses": addresses,
        "tokenSymbols": symbols,
        "quoteStatus": "requires_live_quoter",
        "poolPathKey": "a|b|c",
        "pools": [],
    }


def _mock_pool_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    pools = [
        {
            "currency0": "0x" + ("1" * 40),
            "currency1": "0x" + ("2" * 40),
            "fee": 3000,
            "tickSpacing": 60,
            "hooks": "0x0000000000000000000000000000000000000000",
        },
        {
            "currency0": "0x" + ("2" * 40),
            "currency1": "0x" + ("3" * 40),
            "fee": 3000,
            "tickSpacing": 60,
            "hooks": "0x0000000000000000000000000000000000000000",
        },
        {
            "currency0": "0x" + ("1" * 40),
            "currency1": "0x" + ("3" * 40),
            "fee": 3000,
            "tickSpacing": 60,
            "hooks": "0x0000000000000000000000000000000000000000",
        },
    ]

    def fake_pool_key_groups(*, route_symbols: list[str], chain_id: int, protocols: list[str], route_addresses: list[str] | None = None) -> list[list[dict[str, Any]]]:
        return [deepcopy(pools)]

    def fake_candidate_sets(
        pool_key_groups: list[list[dict[str, Any]]],
        route_addresses: list[str],
    ) -> tuple[list[Any], list[bool], list[Any], list[bool], list[Any], list[bool]]:
        keys: list[Any] = []
        directions: list[bool] = []
        for pool_key in pool_key_groups[0]:
            keys.append(
                [
                    pool_key["currency0"],
                    pool_key["currency1"],
                    pool_key["fee"],
                    pool_key["tickSpacing"],
                    pool_key["hooks"],
                ]
            )
            direction = str(route_addresses[keys.index(keys[-1])]).lower() == pool_key["currency0"].lower()
            directions.append(direction)
        return keys, directions, keys, directions, keys, directions

    monkeypatch.setattr(
        "market.binance_market.dex_route_helpers._official_v4_route_pool_key_groups",
        fake_pool_key_groups,
    )
    monkeypatch.setattr(
        "market.binance_market.dex_route_test._official_v4_hop_candidate_sets",
        fake_candidate_sets,
    )
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    monkeypatch.setattr(
        "dex.runtime.dex_opportunity_helpers._liquidity_cap_raw",
        lambda pool_map, input_symbol, borrow_decimals: (3_000_000, "pool_depth_50pct"),
    )


def _candidate_pool_details() -> dict[str, Any]:
    a = "0x" + ("1" * 40)
    b = "0x" + ("2" * 40)
    c = "0x" + ("3" * 40)
    zero_hook = "0x0000000000000000000000000000000000000000"
    keys = [
        {"currency0": a, "currency1": b, "fee": 3000, "tickSpacing": 60, "hooks": zero_hook},
        {"currency0": b, "currency1": c, "fee": 3000, "tickSpacing": 60, "hooks": zero_hook},
        {"currency0": c, "currency1": a, "fee": 3000, "tickSpacing": 60, "hooks": zero_hook},
    ]
    return {
        "ok": True,
        "groups": [keys],
        "keyIds": [f"{a}:{b}", f"{b}:{c}", f"{c}:{a}"],
        "missingEdges": [],
        "routeAddresses": [a, b, c, a],
    }


def _mock_sqlite_reference_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "market.binance_market.dex_route_test._offline_v4_route_sqlite_pnl_reference",
        lambda **kwargs: {
            "ok": False,
            "error": "offline_sqlite_route_pnl_unavailable",
        },
    )
    monkeypatch.setattr(
        "market.binance_market.dex_route_test.estimate_fixed_amount_pnl_quick",
        lambda **kwargs: {
            "ok": False,
            "error": "quick_route_pnl_unavailable",
        },
    )
    monkeypatch.setattr(
        "market.binance_market.dex_route_test._offline_pool_token_pnl_reference",
        lambda **kwargs: {
            "ok": False,
            "error": "offline_pool_token_pnl_unavailable",
        },
    )


def _mock_pair_key_trusted_symbols(monkeypatch: pytest.MonkeyPatch) -> None:
    trusted_by_address = {
        "0x" + ("1" * 40): "AAA",
        "0x" + ("2" * 40): "BBB",
        "0x" + ("3" * 40): "CCC",
    }

    def fake_trusted_symbol_from_address(address, chain_id=None):
        return trusted_by_address.get(str(address or "").strip().lower())

    monkeypatch.setattr(
        "market.binance_market.dex_route_helpers.trusted_symbol_from_address",
        fake_trusted_symbol_from_address,
    )


def test_build_group_task_config_builds_both_directions(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEX_OPPORTUNITY_SCAN_INTERVAL_SECONDS", raising=False)
    monkeypatch.setenv("DEX_CLOSED_LOOP_NATIVE_RUST", "1")
    monkeypatch.setenv("ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST", "true")
    _mock_pool_helpers(monkeypatch)
    monkeypatch.setattr(
        rust_closed_loop,
        "_candidate_pool_key_groups_from_database",
        lambda **kwargs: _candidate_pool_details(),
    )
    monkeypatch.setattr(
        "dex.runtime.dex_route_rpc._probe_robinhood_family_rpc",
        lambda chain_id: ("http://rpc.test", 123456),
    )
    monkeypatch.setattr(
        "dex.runtime.dex_route_rpc.robinhood_family_rpc_urls",
        lambda chain_id: ["http://rpc.test", "http://backup.test", "http://rpc.test"],
    )
    monkeypatch.setattr(
        "dex.runtime.dex_onchain_quote_evidence_official_v4._official_v4_arb_address",
        lambda chain_id: "0x" + ("a" * 40),
    )
    monkeypatch.setattr(
        "market.binance_market.dex_route_test._official_v4_signer_for_chain",
        lambda chain_id: ("0x" + ("b" * 32), "testnet-arb"),
    )
    monkeypatch.setattr(
        "dex.runtime.dex_opportunity_auto_execute.auto_execute_env_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "dex.runtime.dex_opportunity_helpers._network_for_chain_id",
        lambda chain_id: "robinhood-testnet",
    )

    group_key, task, failed = rust_closed_loop.build_group_task_config(
        chain_id=46630,
        protocols=["uniswap_v4"],
        routes=[
            _route(["AAA", "BBB", "CCC", "AAA"]),
            _route(["AAA", "CCC", "BBB", "AAA"]),
        ],
    )

    assert failed == []
    assert group_key == "aaa|bbb|ccc"
    assert task is not None
    assert task["network"] == "robinhood-testnet"
    assert task["rpcUrl"] == "http://rpc.test"
    assert task["rpcUrls"] == ["http://rpc.test", "http://backup.test"]
    assert task["arbAddress"] == "0x" + ("a" * 40)
    assert (
        task["poolGroupKey"]
        == "0x" + ("1" * 40) + "|0x" + ("2" * 40) + "|0x" + ("3" * 40)
    )
    assert task["broadcastEnabled"] is True
    assert task["deferBroadcast"] is True
    assert task["broadcastPrivateKey"] == "0x" + ("b" * 32)
    assert task["allow_public_fallback"] is True
    assert task.get("relayUrls") is None
    assert task["scanIntervalSeconds"] == 60
    assert len(task["routes"]) == 2
    for route_config in task["routes"]:
        assert route_config["token"] == "0x" + ("1" * 40)
        assert route_config["initialAmountRaw"] == str(10**6)
        assert route_config["minProbeAmountRaw"] == str(10**3)
        assert route_config["maxProbeAmountRaw"] == "3000000"


def test_build_group_task_config_passes_relay_urls_when_configured(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEX_CLOSED_LOOP_NATIVE_RUST", "1")
    monkeypatch.setenv("ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST", "true")
    monkeypatch.setenv("DEX_CLOSED_LOOP_EXECUTION_QUEUE_ENABLED", "0")
    monkeypatch.setenv("OFFICIAL_V4_BROADCAST_RELAY_URLS", "alpha=https://relay.example")
    _mock_pool_helpers(monkeypatch)
    monkeypatch.setattr(
        rust_closed_loop,
        "_candidate_pool_key_groups_from_database",
        lambda **kwargs: _candidate_pool_details(),
    )
    monkeypatch.setattr(
        "dex.runtime.dex_route_rpc._probe_robinhood_family_rpc",
        lambda chain_id: ("http://rpc.test", 123456),
    )
    monkeypatch.setattr(
        "dex.runtime.dex_onchain_quote_evidence_official_v4._official_v4_arb_address",
        lambda chain_id: "0x" + ("a" * 40),
    )
    monkeypatch.setattr(
        "market.binance_market.dex_route_test._official_v4_signer_for_chain",
        lambda chain_id: ("0x" + ("b" * 32), "testnet-arb"),
    )
    monkeypatch.setattr(
        "dex.runtime.dex_opportunity_auto_execute.auto_execute_env_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "dex.runtime.dex_opportunity_helpers._network_for_chain_id",
        lambda chain_id: "robinhood-testnet",
    )

    _, task, failed = rust_closed_loop.build_group_task_config(
        chain_id=46630,
        protocols=["uniswap_v4"],
        routes=[_route(["AAA", "BBB", "CCC", "AAA"])],
    )

    assert failed == []
    assert task is not None
    assert task["allow_public_fallback"] is True
    assert task["relayUrls"] == "alpha=https://relay.example"
    assert task["deferBroadcast"] is False


def test_execution_queue_env_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEX_CLOSED_LOOP_EXECUTION_QUEUE_ENABLED", raising=False)
    monkeypatch.delenv("DEX_CLOSED_LOOP_EXECUTION_WORKERS", raising=False)
    assert rust_closed_loop.execution_queue_env_enabled() is True
    assert rust_closed_loop.execution_workers_env() == 2

    monkeypatch.setenv("DEX_CLOSED_LOOP_EXECUTION_QUEUE_ENABLED", "false")
    monkeypatch.setenv("DEX_CLOSED_LOOP_EXECUTION_WORKERS", "99")
    assert rust_closed_loop.execution_queue_env_enabled() is False
    assert rust_closed_loop.execution_workers_env() == 8

    monkeypatch.setenv("DEX_CLOSED_LOOP_EXECUTION_WORKERS", "not-a-number")
    assert rust_closed_loop.execution_workers_env() == 2


def test_get_native_pool_forwards_execution_queue_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys
    from types import SimpleNamespace

    captured: dict[str, Any] = {}

    class _FakePool:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    fake_module = SimpleNamespace(ClosedLoopWorkerPool=_FakePool)
    monkeypatch.setitem(sys.modules, "flashloan_rs", fake_module)
    monkeypatch.setattr(rust_closed_loop, "_POOL", None)
    monkeypatch.setattr(rust_closed_loop, "_database_path", lambda: "sqlite:///tmp/test.sqlite3")

    pool = rust_closed_loop.get_native_pool(execution_workers=4, execution_queue_enabled=False)

    assert isinstance(pool, _FakePool)
    assert captured["execution_workers"] == 4
    assert captured["execution_queue_enabled"] is False


def test_native_pool_registry_replaces_python_pool_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pools: dict[str, Any] = {}
    stopped: list[str] = []

    class FakePool:
        def __init__(self, database_path: str) -> None:
            self.database_path = database_path

        def stop(self) -> None:
            stopped.append(self.database_path)

    class FakeModule:
        def native_pool_get_or_create(self, **kwargs: Any) -> Any:
            database_path = str(kwargs["database_path"])
            if database_path not in pools:
                pools[database_path] = FakePool(database_path)
            return pools[database_path]

        def native_pool_registry_manage(self, **kwargs: Any) -> dict[str, Any]:
            if kwargs.get("stop"):
                for pool in list(pools.values()):
                    pool.stop()
                pools.clear()
            return {
                "ok": True,
                "stopped": bool(kwargs.get("stop")),
                "removed": 0,
                "remaining": len(pools),
                "errors": [],
            }

        def native_pool_registry_status(self) -> dict[str, Any]:
            return {"count": len(pools), "entries": [], "registry": "fake"}

    fake_module = FakeModule()
    monkeypatch.setattr(rust_closed_loop, "_rust_runtime_module", lambda: fake_module)
    monkeypatch.setattr(rust_closed_loop, "_POOLS_BY_DATABASE", {})
    monkeypatch.setattr(rust_closed_loop, "_POOLS_BY_CHAIN", {})
    monkeypatch.setattr(rust_closed_loop, "_database_path", lambda: "sqlite:///tmp/native.sqlite3")

    first = rust_closed_loop.get_native_pool()
    same = rust_closed_loop.get_native_pool()

    assert first is same
    assert rust_closed_loop._POOLS_BY_DATABASE == {}
    rust_closed_loop.reset_native_pool()
    assert len(stopped) == 1
    assert stopped[0].lower().replace("\\", "/").endswith("native.sqlite3")


def test_stop_native_pool_for_chain_only_stops_target_scope(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped: list[str] = []
    registry_calls: list[dict[str, Any]] = []

    class FakePool:
        def __init__(self, database_path: str) -> None:
            self.database_path = database_path

        def stop(self) -> None:
            stopped.append(self.database_path)

    class FakeModule:
        def native_pool_registry_manage(self, **kwargs: Any) -> dict[str, Any]:
            registry_calls.append(dict(kwargs))
            return {
                "available": True,
                "ok": True,
                "stopped": bool(kwargs.get("stop")),
                "removed": 1,
                "remaining": 1,
                "errors": [],
            }

    target_path = os.path.normcase(os.path.abspath(str(tmp_path / "target.sqlite3")))
    other_path = os.path.normcase(os.path.abspath(str(tmp_path / "other.sqlite3")))
    target_pool = FakePool(target_path)
    other_pool = FakePool(other_path)
    monkeypatch.setattr(rust_closed_loop, "_rust_runtime_module", lambda: FakeModule())
    monkeypatch.setattr(
        rust_closed_loop,
        "_POOLS_BY_DATABASE",
        {target_path: target_pool, other_path: other_pool},
    )
    monkeypatch.setattr(
        rust_closed_loop,
        "_POOLS_BY_CHAIN",
        {4663: target_path, 46630: other_path},
    )
    monkeypatch.setattr(rust_closed_loop, "_POOL", target_pool)
    monkeypatch.setattr(rust_closed_loop, "_POOL_DATABASE_PATH", target_path)

    result = rust_closed_loop.stop_native_pool_for_chain(
        chain_id=4663,
        database_path=str(tmp_path / "target.sqlite3"),
    )

    assert result["ok"] is True
    assert registry_calls == [
        {
            "stop": True,
            "chain_id": 4663,
            "database_path": target_path,
        }
    ]
    assert stopped == [target_path]
    assert rust_closed_loop._POOLS_BY_DATABASE == {other_path: other_pool}
    assert rust_closed_loop._POOLS_BY_CHAIN == {46630: other_path}
    assert rust_closed_loop._POOL is None
    assert rust_closed_loop._POOL_DATABASE_PATH is None


def test_route_task_config_normalizes_array_pool_keys_for_rust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real Python candidate helpers return 5-item arrays; Rust wants objects."""
    from dex.runtime import rust_closed_loop

    def fake_pool_key_groups(
        *,
        route_symbols: list[str],
        chain_id: int,
        protocols: list[str],
        route_addresses: list[str] | None = None,
    ) -> list[list[dict[str, Any]]]:
        return [
            [
                {
                    "currency0": "0x" + ("1" * 40),
                    "currency1": "0x" + ("2" * 40),
                    "fee": 3000,
                    "tickSpacing": 60,
                    "hooks": "0x0000000000000000000000000000000000000000",
                },
                {
                    "currency0": "0x" + ("2" * 40),
                    "currency1": "0x" + ("3" * 40),
                    "fee": 3000,
                    "tickSpacing": 60,
                    "hooks": "0x0000000000000000000000000000000000000000",
                },
                {
                    "currency0": "0x" + ("1" * 40),
                    "currency1": "0x" + ("3" * 40),
                    "fee": 3000,
                    "tickSpacing": 60,
                    "hooks": "0x0000000000000000000000000000000000000000",
                },
            ]
        ]

    def fake_candidate_sets(
        pool_key_groups: list[list[dict[str, Any]]],
        route_addresses: list[str],
    ) -> tuple[list[Any], list[bool], list[Any], list[bool], list[Any], list[bool]]:
        keys: list[Any] = []
        directions: list[bool] = []
        for pool_key in pool_key_groups[0]:
            keys.append(
                [
                    pool_key["currency0"],
                    pool_key["currency1"],
                    pool_key["fee"],
                    pool_key["tickSpacing"],
                    pool_key["hooks"],
                ]
            )
            direction = str(route_addresses[keys.index(keys[-1])]).lower() == pool_key["currency0"].lower()
            directions.append(direction)
        return keys, directions, keys, directions, keys, directions

    monkeypatch.setattr(
        "market.binance_market.dex_route_helpers._official_v4_route_pool_key_groups",
        fake_pool_key_groups,
    )
    monkeypatch.setattr(
        rust_closed_loop,
        "_candidate_pool_key_groups_from_database",
        lambda **kwargs: _candidate_pool_details(),
    )
    monkeypatch.setattr(
        "market.binance_market.dex_route_test._official_v4_hop_candidate_sets",
        fake_candidate_sets,
    )
    monkeypatch.setattr(rust_closed_loop, "_decimals_for_borrow", lambda chain_id, borrow_address: 6)
    monkeypatch.setattr(
        "dex.runtime.dex_opportunity_helpers._liquidity_cap_raw",
        lambda pool_map, input_symbol, borrow_decimals: (3_000_000, "pool_depth_50pct"),
    )

    config = rust_closed_loop._route_task_config(
        route=_route(["AAA", "BBB", "CCC", "AAA"]),
        chain_id=46630,
        protocols=["uniswap_v4"],
    )

    assert config is not None
    for key_name in ("entryKeys", "bridgeKeys", "exitKeys"):
        keys = config[key_name]
        assert keys and all(isinstance(key, dict) for key in keys)
        assert all("currency0" in key and "currency1" in key for key in keys)
    assert config["entryDirections"] == config["bridgeDirections"] == config["exitDirections"]


def test_route_task_config_does_not_use_spoofed_pool_metadata_symbols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dex.runtime import rust_closed_loop

    def fake_pool_key_groups(
        *,
        route_symbols: list[str],
        chain_id: int,
        protocols: list[str],
        route_addresses: list[str] | None = None,
    ) -> list[list[dict[str, Any]]]:
        assert route_symbols == ["UNRESOLVED:0X11111111",
                                 "UNRESOLVED:0X22222222",
                                 "UNRESOLVED:0X33333333",
                                 "UNRESOLVED:0X11111111"]
        return [
            [
                {
                    "currency0": "0x" + ("1" * 40),
                    "currency1": "0x" + ("2" * 40),
                    "fee": 3000,
                    "tickSpacing": 60,
                    "hooks": "0x0000000000000000000000000000000000000000",
                },
                {
                    "currency0": "0x" + ("2" * 40),
                    "currency1": "0x" + ("3" * 40),
                    "fee": 3000,
                    "tickSpacing": 60,
                    "hooks": "0x0000000000000000000000000000000000000000",
                },
                {
                    "currency0": "0x" + ("1" * 40),
                    "currency1": "0x" + ("3" * 40),
                    "fee": 3000,
                    "tickSpacing": 60,
                    "hooks": "0x0000000000000000000000000000000000000000",
                },
            ]
        ]

    def fake_candidate_sets(
        pool_key_groups: list[list[dict[str, Any]]],
        route_addresses: list[str],
    ) -> tuple[list[Any], list[bool], list[Any], list[bool], list[Any], list[bool]]:
        keys = []
        for pool_key in pool_key_groups[0]:
            keys.append(
                [
                    pool_key["currency0"],
                    pool_key["currency1"],
                    pool_key["fee"],
                    pool_key["tickSpacing"],
                    pool_key["hooks"],
                ]
            )
        directions = [True] * len(keys)
        return keys, directions, keys, directions, keys, directions

    monkeypatch.setattr(
        "market.binance_market.dex_route_helpers._official_v4_route_pool_key_groups",
        fake_pool_key_groups,
    )
    monkeypatch.setattr(
        rust_closed_loop,
        "_candidate_pool_key_groups_from_database",
        lambda **kwargs: _candidate_pool_details(),
    )
    monkeypatch.setattr(
        "market.binance_market.dex_route_test._official_v4_hop_candidate_sets",
        fake_candidate_sets,
    )
    monkeypatch.setattr(rust_closed_loop, "_decimals_for_borrow", lambda chain_id, borrow_address: 6)
    monkeypatch.setattr(
        "dex.runtime.dex_opportunity_helpers._liquidity_cap_raw",
        lambda pool_map, input_symbol, borrow_decimals: (3_000_000, "pool_depth_50pct"),
    )

    route_symbols = [
        "UNRESOLVED:0x11111111",
        "UNRESOLVED:0x22222222",
        "UNRESOLVED:0x33333333",
        "UNRESOLVED:0x11111111",
    ]
    route = _route(route_symbols)
    route["routePools"] = [
        {
            "poolKey": {
                "currency0": "0x" + ("1" * 40),
                "currency1": "0x" + ("2" * 40),
                "currency0Metadata": {"symbol": "AAA"},
                "currency1Metadata": {"symbol": "BBB"},
            }
        },
        {
            "poolKey": {
                "currency0": "0x" + ("2" * 40),
                "currency1": "0x" + ("3" * 40),
                "currency0Metadata": {"symbol": "BBB"},
                "currency1Metadata": {"symbol": "CCC"},
            }
        },
    ]

    config = rust_closed_loop._route_task_config(
        route=route,
        chain_id=46630,
        protocols=["uniswap_v4"],
    )

    assert config is not None
    assert config["routeSymbols"][0] == "UNRESOLVED:0X11111111"
    assert config["routeAddresses"] == [
        "0x" + ("1" * 40),
        "0x" + ("2" * 40),
        "0x" + ("3" * 40),
        "0x" + ("1" * 40),
    ]
    assert "AAA" not in str(config["routeSymbols"])


def test_route_task_config_builds_from_pool_manager_candidate_database(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sqlite3

    database = tmp_path / "deep_candidate_pools.sqlite3"
    connection = sqlite3.connect(str(database))
    connection.executescript(
        """
        CREATE TABLE dex_candidate_pools (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            protocol TEXT NOT NULL,
            pool_id TEXT NOT NULL,
            token0 TEXT NOT NULL,
            token1 TEXT NOT NULL,
            fee INTEGER NOT NULL,
            tick_spacing INTEGER NOT NULL,
            hooks TEXT NOT NULL,
            pool_key_json TEXT NOT NULL,
            depth_status TEXT NOT NULL,
            has_liquidity INTEGER NOT NULL,
            usable_for_observation INTEGER NOT NULL,
            PRIMARY KEY (network, chain_id, protocol, pool_id)
        )
        """
    )
    a = "0x" + ("1" * 40)
    b = "0x" + ("2" * 40)
    c = "0x" + ("3" * 40)
    zero_hook = "0x0000000000000000000000000000000000000000"
    rows = [
        ("p-a-b", a, b),
        ("p-b-c", b, c),
        ("p-c-a", c, a),
    ]
    for index, (pool_id, token0, token1) in enumerate(rows, start=1):
        depth_status = ("thin", "unpriced", "deep")[index - 1]
        pool_key = {
            "currency0": token0,
            "currency1": token1,
            "fee": 3000,
            "tickSpacing": 60,
            "hooks": zero_hook,
        }
        connection.execute(
            """
            INSERT INTO dex_candidate_pools (
                network, chain_id, protocol, pool_id, token0, token1, fee,
                tick_spacing, hooks, pool_key_json, depth_status,
                has_liquidity, usable_for_observation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "robinhood",
                4663,
                "uniswap_v4",
                pool_id,
                token0,
                token1,
                3000,
                60,
                zero_hook,
                json.dumps(pool_key),
                depth_status,
                1,
                1,
            ),
        )
    connection.commit()
    connection.close()

    monkeypatch.setattr(
        rust_closed_loop,
        "resolve_runtime",
        lambda chain_id=None, **kwargs: {
            "databasePath": str(database),
            "source": "explicit",
        },
    )
    monkeypatch.setattr(
        "market.binance_market.dex_route_helpers._official_v4_route_pool_key_groups",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    monkeypatch.setattr(
        "dex.runtime.dex_opportunity_helpers._liquidity_cap_raw",
        lambda pool_map, input_symbol, borrow_decimals: (3_000_000, "pool_depth_50pct"),
    )

    route = _route(["AAA", "BBB", "CCC", "AAA"])
    assert route["routeAddresses"] == [a, b, c, a]

    config = rust_closed_loop._route_task_config(
        route=route,
        chain_id=4663,
        protocols=["uniswap_v4"],
    )

    assert config is not None
    assert len(config["entryKeys"]) == 1
    assert len(config["bridgeKeys"]) == 1
    assert len(config["exitKeys"]) == 1
    assert config["entryKeys"][0] == {
        "currency0": a,
        "currency1": b,
        "fee": 3000,
        "tickSpacing": 60,
        "hooks": zero_hook,
    }
    assert config["bridgeKeys"][0] == {
        "currency0": b,
        "currency1": c,
        "fee": 3000,
        "tickSpacing": 60,
        "hooks": zero_hook,
    }
    assert config["exitKeys"][0] == {
        "currency0": c,
        "currency1": a,
        "fee": 3000,
        "tickSpacing": 60,
        "hooks": zero_hook,
    }
    assert config["entryDirections"] == [True]
    assert config["bridgeDirections"] == [True]
    assert config["exitDirections"] == [True]


def test_candidate_pool_key_groups_reports_missing_edge_and_refreshes(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sqlite3

    database = tmp_path / "missing_candidate_edge.sqlite3"
    connection = sqlite3.connect(str(database))
    connection.executescript(
        """
        CREATE TABLE dex_candidate_pools (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            protocol TEXT NOT NULL,
            pool_id TEXT NOT NULL,
            token0 TEXT NOT NULL,
            token1 TEXT NOT NULL,
            fee INTEGER NOT NULL,
            tick_spacing INTEGER NOT NULL,
            hooks TEXT NOT NULL,
            pool_key_json TEXT NOT NULL,
            depth_status TEXT NOT NULL,
            has_liquidity INTEGER NOT NULL,
            usable_for_observation INTEGER NOT NULL,
            PRIMARY KEY (network, chain_id, protocol, pool_id)
        )
        """
    )
    a = "0x" + ("1" * 40)
    b = "0x" + ("2" * 40)
    c = "0x" + ("3" * 40)
    zero_hook = "0x0000000000000000000000000000000000000000"

    def _insert(pool_id: str, token0: str, token1: str) -> None:
        connection.execute(
            """
            INSERT INTO dex_candidate_pools (
                network, chain_id, protocol, pool_id, token0, token1, fee,
                tick_spacing, hooks, pool_key_json, depth_status,
                has_liquidity, usable_for_observation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "robinhood",
                4663,
                "uniswap_v4",
                pool_id,
                token0,
                token1,
                3000,
                60,
                zero_hook,
                json.dumps(
                    {
                        "currency0": token0,
                        "currency1": token1,
                        "fee": 3000,
                        "tickSpacing": 60,
                        "hooks": zero_hook,
                    }
                ),
                "deep",
                1,
                1,
            ),
        )

    _insert("p-a-b", a, b)
    _insert("p-c-a", c, a)
    connection.commit()
    connection.close()

    monkeypatch.setattr(
        rust_closed_loop,
        "resolve_runtime",
        lambda chain_id=None, **kwargs: {
            "databasePath": str(database),
            "source": "explicit",
        },
    )

    details = rust_closed_loop._candidate_pool_key_groups_from_database(
        chain_id=4663,
        route_addresses=[a, b, c, a],
        network="robinhood",
        protocols=["uniswap_v4"],
        return_details=True,
    )

    assert isinstance(details, dict)
    assert details["ok"] is False
    assert details["groups"] == []
    assert details["keyIds"] == [f"{a}:{b}", f"{b}:{c}", f"{a}:{c}"]
    assert details["missingEdges"] == [
        {"index": 1, "from": b, "to": c, "keyId": f"{b}:{c}"}
    ]

    connection = sqlite3.connect(str(database))
    connection.execute(
        """
        INSERT INTO dex_candidate_pools (
            network, chain_id, protocol, pool_id, token0, token1, fee,
            tick_spacing, hooks, pool_key_json, depth_status,
            has_liquidity, usable_for_observation
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "robinhood",
            4663,
            "uniswap_v4",
            "p-b-c",
            b,
            c,
            3000,
            60,
            zero_hook,
            json.dumps(
                {
                    "currency0": b,
                    "currency1": c,
                    "fee": 3000,
                    "tickSpacing": 60,
                    "hooks": zero_hook,
                }
            ),
            "thin",
            1,
            1,
        ),
    )
    connection.commit()
    connection.close()

    refreshed = rust_closed_loop._candidate_pool_key_groups_from_database(
        chain_id=4663,
        route_addresses=[a, b, c, a],
        network="robinhood",
        protocols=["uniswap_v4"],
        return_details=True,
    )

    assert isinstance(refreshed, dict)
    assert refreshed["ok"] is True
    assert len(refreshed["groups"]) == 1
    assert len(refreshed["groups"][0]) == 3
    assert refreshed["missingEdges"] == []


def test_candidate_pool_key_groups_resolves_middle_edge_from_observation_surface(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sqlite3

    database = tmp_path / "observation_middle_edge.sqlite3"
    connection = sqlite3.connect(str(database))
    connection.executescript(
        """
        CREATE TABLE dex_candidate_pools (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            protocol TEXT NOT NULL,
            pool_id TEXT NOT NULL,
            token0 TEXT NOT NULL,
            token1 TEXT NOT NULL,
            fee INTEGER NOT NULL,
            tick_spacing INTEGER NOT NULL,
            hooks TEXT NOT NULL,
            pool_key_json TEXT NOT NULL,
            depth_status TEXT NOT NULL,
            has_liquidity INTEGER NOT NULL,
            usable_for_observation INTEGER NOT NULL,
            PRIMARY KEY (network, chain_id, protocol, pool_id)
        );
        CREATE TABLE market_observation_intermediate_pools (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            protocol TEXT NOT NULL,
            pool_id TEXT NOT NULL,
            token0 TEXT NOT NULL,
            token1 TEXT NOT NULL,
            fee INTEGER NOT NULL,
            tick_spacing INTEGER NOT NULL,
            hooks TEXT NOT NULL,
            pool_key_json TEXT,
            PRIMARY KEY (network, chain_id, protocol, pool_id)
        );
        CREATE TABLE v4_exchange_pools (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            protocol TEXT NOT NULL,
            pool_id TEXT NOT NULL,
            pool_key_json TEXT,
            PRIMARY KEY (network, chain_id, protocol, pool_id)
        )
        """
    )
    a = "0x" + ("1" * 40)
    b = "0x" + ("2" * 40)
    c = "0x" + ("3" * 40)
    zero_hook = "0x0000000000000000000000000000000000000000"
    rows = [
        ("p-a-b", a, b),
        ("p-c-a", c, a),
    ]
    for pool_id, token0, token1 in rows:
        connection.execute(
            """
            INSERT INTO dex_candidate_pools (
                network, chain_id, protocol, pool_id, token0, token1, fee,
                tick_spacing, hooks, pool_key_json, depth_status,
                has_liquidity, usable_for_observation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "robinhood",
                4663,
                "uniswap_v4",
                pool_id,
                token0,
                token1,
                3000,
                60,
                zero_hook,
                json.dumps(
                    {
                        "currency0": token0,
                        "currency1": token1,
                        "fee": 3000,
                        "tickSpacing": 60,
                        "hooks": zero_hook,
                    }
                ),
                "deep",
                1,
                1,
            ),
        )
    connection.execute(
        """
        INSERT INTO v4_exchange_pools (
            network, chain_id, protocol, pool_id, pool_key_json
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            "robinhood",
            4663,
            "uniswap_v4",
            "p-b-c",
            json.dumps(
                {
                    "currency0": b,
                    "currency1": c,
                    "fee": 3000,
                    "tickSpacing": 60,
                    "hooks": zero_hook,
                }
            ),
        ),
    )
    connection.execute(
        """
        INSERT INTO market_observation_intermediate_pools (
            network, chain_id, protocol, pool_id, token0, token1, fee,
            tick_spacing, hooks, pool_key_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "robinhood",
            4663,
            "uniswap_v4",
            "p-b-c",
            b,
            c,
            3000,
            60,
            zero_hook,
            "{}",
        ),
    )
    connection.commit()
    connection.close()

    monkeypatch.setattr(
        rust_closed_loop,
        "resolve_runtime",
        lambda chain_id=None, **kwargs: {
            "databasePath": str(database),
            "source": "explicit",
        },
    )

    details = rust_closed_loop._candidate_pool_key_groups_from_database(
        chain_id=4663,
        route_addresses=[a, b, c, a],
        network="robinhood",
        protocols=["uniswap_v4"],
        return_details=True,
    )

    assert isinstance(details, dict)
    assert details["ok"] is True
    assert details["missingEdges"] == []
    assert len(details["groups"]) == 1
    assert len(details["groups"][0]) == 3
    assert details["groups"][0][1]["currency0"] == b
    assert details["groups"][0][1]["currency1"] == c
    assert details["groups"][0][1]["tickSpacing"] == 60


def test_route_task_config_replaces_stale_route_pool_keys_with_candidate_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_pool_helpers(monkeypatch)
    details = _candidate_pool_details()
    for key in details["groups"][0]:
        key["fee"] = 500
        key["tickSpacing"] = 10
    monkeypatch.setattr(
        rust_closed_loop,
        "_candidate_pool_key_groups_from_database",
        lambda **kwargs: details,
    )

    config = rust_closed_loop._route_task_config(
        route=_route(["AAA", "BBB", "CCC", "AAA"]),
        chain_id=4663,
        protocols=["uniswap_v4"],
    )

    assert config is not None
    assert config["entryKeys"][0]["fee"] == 500
    assert config["bridgeKeys"][0]["fee"] == 500
    assert config["exitKeys"][0]["fee"] == 500


def test_route_task_config_reports_pool_manager_refresh_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_pool_helpers(monkeypatch)
    a = "0x" + ("1" * 40)
    b = "0x" + ("2" * 40)
    c = "0x" + ("3" * 40)
    details = {
        "ok": False,
        "groups": [],
        "routeAddresses": [a, b, c, a],
        "keyIds": [f"{a}:{b}", f"{b}:{c}", f"{c}:{a}"],
        "missingEdges": [
            {"index": 1, "from": b, "to": c, "keyId": f"{b}:{c}"},
        ],
    }
    monkeypatch.setattr(
        rust_closed_loop,
        "_candidate_pool_key_groups_from_database",
        lambda **kwargs: details,
    )
    diagnostics: dict[str, Any] = {}

    config = rust_closed_loop._route_task_config(
        route=_route(["AAA", "BBB", "CCC", "AAA"]),
        chain_id=4663,
        protocols=["uniswap_v4"],
        diagnostics=diagnostics,
    )

    assert config is None
    assert diagnostics["reason"] == "pool_manager_refresh_required"
    assert diagnostics["retryable"] is True
    assert diagnostics["keyIds"] == [f"{a}:{b}", f"{b}:{c}", f"{c}:{a}"]
    assert diagnostics["missingEdges"][0]["keyId"] == f"{b}:{c}"


def test_route_task_config_recovers_after_candidate_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_pool_helpers(monkeypatch)
    a = "0x" + ("1" * 40)
    b = "0x" + ("2" * 40)
    c = "0x" + ("3" * 40)
    state = {
        "details": {
            "ok": False,
            "groups": [],
            "routeAddresses": [a, b, c, a],
            "keyIds": [f"{a}:{b}", f"{b}:{c}", f"{c}:{a}"],
            "missingEdges": [
                {"index": 1, "from": b, "to": c, "keyId": f"{b}:{c}"},
            ],
        }
    }

    def _candidate_details(**kwargs: Any) -> dict[str, Any]:
        return state["details"]

    monkeypatch.setattr(
        rust_closed_loop,
        "_candidate_pool_key_groups_from_database",
        _candidate_details,
    )

    diagnostics: dict[str, Any] = {}
    config = rust_closed_loop._route_task_config(
        route=_route(["AAA", "BBB", "CCC", "AAA"]),
        chain_id=4663,
        protocols=["uniswap_v4"],
        diagnostics=diagnostics,
    )

    assert config is None
    assert diagnostics["reason"] == "pool_manager_refresh_required"

    state["details"] = _candidate_pool_details()
    refreshed = rust_closed_loop._route_task_config(
        route=_route(["AAA", "BBB", "CCC", "AAA"]),
        chain_id=4663,
        protocols=["uniswap_v4"],
    )

    assert refreshed is not None
    assert refreshed["entryKeys"]
    assert refreshed["bridgeKeys"]
    assert refreshed["exitKeys"]


def test_build_group_task_config_uses_refresh_required_for_unclassified_pool_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_route_task_config",
        lambda **kwargs: None,
    )

    _resolved, _config, failed = rust_closed_loop.build_group_task_config(
        chain_id=4663,
        protocols=["uniswap_v4"],
        routes=[_route(["AAA", "BBB", "CCC", "AAA"])],
    )

    assert _resolved is None
    assert _config is None
    assert failed[0]["reason"] == "pool_manager_refresh_required"
    assert failed[0]["kind"] == "pool_manager_refresh_required"
    assert failed[0]["retryable"] is True
    assert failed[0]["requestedAt"]


def test_submit_group_forwards_network_and_config_to_rust_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakePool:
        def __init__(self) -> None:
            self.submitted: list[dict[str, Any]] = []

        def submit(self, **kwargs: Any) -> dict[str, Any]:
            self.submitted.append(deepcopy(kwargs))
            return {"accepted": True, "group_key": kwargs["group_key"]}

    fake_pool = _FakePool()
    monkeypatch.setattr(rust_closed_loop, "pool_for_chain", lambda chain_id: fake_pool)
    monkeypatch.setattr(
        rust_closed_loop,
        "resolve_runtime",
        lambda chain_id, **kwargs: {
            "databasePath": "/tmp/testnet.sqlite3",
            "source": "explicit",
        },
    )
    task_config = {
        "network": "robinhood-testnet",
        "routes": [{"token": "0x" + ("1" * 40)}],
        "broadcastEnabled": False,
    }

    result = rust_closed_loop.submit_group(
        group_key="aaa|bbb|ccc",
        chain_id=46630,
        protocols=["uniswap_v4"],
        task_config=task_config,
    )

    assert result["accepted"] is True
    assert len(fake_pool.submitted) == 1
    call = fake_pool.submitted[0]
    assert call["group_key"] == "aaa|bbb|ccc"
    assert call["chain_id"] == 46630
    assert call["network"] == "robinhood-testnet"
    assert call["protocol"] == "uniswap_v4"
    assert "network" not in call["config_json"]
    assert "0x" + ("1" * 40) in call["config_json"]


def test_native_pool_stop_returns_while_maintenance_handler_is_blocked() -> None:
    import json
    import threading
    import time

    import flashloan_rs

    started = threading.Event()
    release = threading.Event()

    def blocking_handler(payload_json: str) -> str:
        started.set()
        release.wait(5)
        return json.dumps({"ok": True})

    pool = flashloan_rs.ClosedLoopWorkerPool(
        max_workers=1,
        database_path="",
        maintenance_handler=blocking_handler,
        maintenance_interval_seconds=1,
    )
    try:
        pool.submit(
            group_key="stop-blocked-maintenance",
            config_json=json.dumps({"rpcUrl": "http://127.0.0.1:9", "arbAddress": "0x"}),
            chain_id=46630,
            protocols=["uniswap_v4"],
            mode="scan",
            priority=0,
            database_path="",
            network="robinhood-testnet",
            protocol="uniswap_v4",
        )
        assert started.wait(5), "maintenance handler did not start"
        started_at = time.monotonic()
        pool.stop()
        assert time.monotonic() - started_at < 2.0
    finally:
        release.set()
        time.sleep(0.2)


def test_scanner_submits_native_groups_without_python_closed_loop_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dex.runtime.dex_opportunity_scanner_engine import DexOpportunityScanner
    from dex.runtime import closed_loop_consumer

    submitted: list[dict[str, Any]] = []
    monkeypatch.setattr(
        closed_loop_consumer,
        "resolve_runtime_database",
        lambda database_url=None: None,
    )
    monkeypatch.setattr(
        rust_mixin,
        "native_worker_status",
        lambda **kwargs: {"workers": []},
    )
    monkeypatch.setattr(
        rust_mixin,
        "build_group_task_config",
        lambda *, chain_id, protocols, routes: (
            "aaa|bbb|ccc",
            {"routes": routes},
            [],
        ),
    )
    monkeypatch.setattr(
        rust_mixin,
        "submit_group",
        lambda **kwargs: submitted.append(kwargs) or {"accepted": True},
    )
    scanner = object.__new__(DexOpportunityScanner)
    scanner.auto_execute_enabled = True
    payload = {
        "routes": [
            _route(["AAA", "BBB", "CCC", "AAA"]),
            _route(["AAA", "CCC", "BBB", "AAA"]),
        ]
    }

    rust_mixin.DexOpportunityNativeRustMixin._submit_native_closed_loop_groups(
        scanner,
        payload=payload,
        chain_id=46630,
        protocols=["uniswap_v4"],
    )

    assert len(submitted) == 1
    assert submitted[0]["group_key"] == "aaa|bbb|ccc"
    assert submitted[0]["mode"] == "deep"
    # Both directions are carried inside one Rust worker task; no Python
    # ThreadPoolExecutor is created for closed-loop scheduling.
    assert len(submitted[0]["task_config"]["routes"]) == 2


def test_scanner_claims_each_direction_with_physical_loop_key(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dex.runtime import closed_loop_consumer
    from dex.runtime.closed_loop_state_machine import load_loop_state
    from dex.runtime.dex_opportunity_scanner_engine import DexOpportunityScanner

    database = tmp_path / "native_direction_claims.sqlite3"
    submitted: list[dict[str, Any]] = []
    monkeypatch.setattr(
        rust_mixin,
        "native_worker_status",
        lambda **kwargs: {"workers": []},
    )
    monkeypatch.setattr(
        rust_mixin,
        "build_group_task_config",
        lambda *, chain_id, protocols, routes: (
            "aaa|bbb|ccc",
            {"routes": routes},
            [],
        ),
    )
    monkeypatch.setattr(
        rust_mixin,
        "submit_group",
        lambda **kwargs: submitted.append(kwargs) or {"accepted": True},
    )
    scanner = object.__new__(DexOpportunityScanner)
    scanner.auto_execute_enabled = True
    scanner._native_database_url = lambda chain_id=None: str(database)

    rust_mixin.DexOpportunityNativeRustMixin._submit_native_closed_loop_groups(
        scanner,
        payload={
            "routes": [
                _route(["AAA", "BBB", "CCC", "AAA"]),
                _route(["AAA", "CCC", "BBB", "AAA"]),
            ]
        },
        chain_id=46630,
        protocols=["uniswap_v4"],
    )

    assert len(submitted) == 1
    assert len(submitted[0]["task_config"]["routes"]) == 2
    common = {
        "network": "robinhood-testnet",
        "chain_id": 46630,
        "protocol": "uniswap_v4",
        "market_id": "dex-arbitrage-46630-uniswap-v4",
    }
    forward = load_loop_state(str(database), **common, loop_key=FORWARD_LOOP_KEY)
    reverse = load_loop_state(str(database), **common, loop_key=REVERSE_LOOP_KEY)
    assert forward is not None
    assert reverse is not None
    assert forward["state"] == "preflight_running"
    assert reverse["state"] == "preflight_running"
    assert forward["owner_thread"] == closed_loop_consumer.owner_thread(
        FORWARD_LOOP_KEY,
        "review",
    )
    assert reverse["owner_thread"] == closed_loop_consumer.owner_thread(
        REVERSE_LOOP_KEY,
        "review",
    )


def test_scanner_records_task_config_failure_per_direction(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dex.runtime.closed_loop_state_machine import load_loop_state
    from dex.runtime.dex_opportunity_scanner_engine import DexOpportunityScanner

    _mock_pair_key_trusted_symbols(monkeypatch)
    database = tmp_path / "native_direction_failures.sqlite3"
    monkeypatch.setattr(
        rust_mixin,
        "native_worker_status",
        lambda **kwargs: {"workers": []},
    )
    monkeypatch.setattr(
        rust_mixin,
        "build_group_task_config",
        lambda *, chain_id, protocols, routes: (
            None,
            None,
            [{"reason": "native_task_config_unavailable"}],
        ),
    )
    monkeypatch.setattr(
        rust_mixin,
        "submit_group",
        lambda **kwargs: pytest.fail("failed configs must not be submitted"),
    )
    scanner = object.__new__(DexOpportunityScanner)
    scanner.auto_execute_enabled = True
    scanner._native_database_url = lambda chain_id=None: str(database)

    rust_mixin.DexOpportunityNativeRustMixin._submit_native_closed_loop_groups(
        scanner,
        payload={
            "routes": [
                _route(["AAA", "BBB", "CCC", "AAA"]),
                _route(["AAA", "CCC", "BBB", "AAA"]),
            ]
        },
        chain_id=46630,
        protocols=["uniswap_v4"],
    )

    common = {
        "network": "robinhood-testnet",
        "chain_id": 46630,
        "protocol": "uniswap_v4",
        "market_id": "dex-arbitrage-46630-uniswap-v4",
    }
    forward = load_loop_state(str(database), **common, loop_key=FORWARD_LOOP_KEY)
    reverse = load_loop_state(str(database), **common, loop_key=REVERSE_LOOP_KEY)
    assert forward is not None
    assert reverse is not None
    assert forward["state"] == "preflight_failed"
    assert reverse["state"] == "preflight_failed"
    assert "native_task_config_unavailable" in str(forward["last_error"])
    assert "native_task_config_unavailable" in str(reverse["last_error"])


def test_rust_native_path_never_starts_python_review_pipeline(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dex.runtime import dex_opportunity_scanner_engine as engine
    from dex.runtime.dex_opportunity_scanner_engine import DexOpportunityScanner

    started: list[str] = []

    class _Pipeline:
        def start(self) -> None:
            started.append("python_closed_loop_review_pipeline")

        def stop(self) -> None:
            pass

    monkeypatch.setattr(engine, "get_native_pool", lambda: object())
    monkeypatch.setattr(
        rust_closed_loop,
        "register_maintenance_scope",
        lambda **kwargs: {"accepted": True},
    )
    scanner = DexOpportunityScanner(
        quote_payload_provider=lambda **kwargs: {"routes": []},
        auto_execute_enabled=True,
        cache_path=tmp_path / "dex_opportunity_snapshot_p2.json",
        periodic_scan_enabled=False,
    )
    scanner._native_rust_closed_loop = True
    scanner.closed_loop_review_pipeline = _Pipeline()
    scanner.run_forever = lambda: None

    # start() with native Rust enabled must not launch the Python pipeline.
    scanner.start()

    assert started == []
    scanner.stop()


def test_scanner_skips_native_group_when_worker_is_idle_resident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_pair_key_trusted_symbols(monkeypatch)
    from dex.runtime.dex_opportunity_scanner_engine import DexOpportunityScanner

    submitted: list[dict[str, Any]] = []
    monkeypatch.setattr(
        rust_mixin,
        "native_worker_status",
        lambda **kwargs: {
            "workers": [
                {
                    "group_key": "aaa|bbb|ccc",
                    "state": "idle",
                }
            ]
        },
    )
    monkeypatch.setattr(
        rust_mixin,
        "build_group_task_config",
        lambda *, chain_id, protocols, routes: (
            "aaa|bbb|ccc",
            {"routes": routes},
            [],
        ),
    )
    monkeypatch.setattr(
        rust_mixin,
        "submit_group",
        lambda **kwargs: submitted.append(kwargs) or {"accepted": True},
    )
    scanner = object.__new__(DexOpportunityScanner)
    scanner.auto_execute_enabled = True

    rust_mixin.DexOpportunityNativeRustMixin._submit_native_closed_loop_groups(
        scanner,
        payload={"routes": [_route(["AAA", "BBB", "CCC", "AAA"])]},
        chain_id=46630,
        protocols=["uniswap_v4"],
    )

    assert len(submitted) == 0


def test_scanner_caps_total_native_group_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_pair_key_trusted_symbols(monkeypatch)
    from dex.runtime.dex_opportunity_scanner_engine import DexOpportunityScanner

    submitted: list[dict[str, Any]] = []
    monkeypatch.setenv("DEX_CLOSED_LOOP_MAX_RESIDENT_GROUPS", "2")
    monkeypatch.setattr(
        rust_mixin,
        "native_worker_status",
        lambda **kwargs: {
            "workers": [
                {"group_key": "eth|aaa|bbb", "state": "idle"},
                {"group_key": "eth|ccc|ddd", "state": "stopped"},
            ]
        },
    )
    monkeypatch.setattr(
        rust_mixin,
        "build_group_task_config",
        lambda *, chain_id, protocols, routes: (
            "aaa|bbb|ccc",
            {"routes": routes},
            [],
        ),
    )
    monkeypatch.setattr(
        rust_mixin,
        "submit_group",
        lambda **kwargs: submitted.append(kwargs) or {"accepted": True},
    )
    scanner = object.__new__(DexOpportunityScanner)
    scanner.auto_execute_enabled = True

    rust_mixin.DexOpportunityNativeRustMixin._submit_native_closed_loop_groups(
        scanner,
        payload={"routes": [_route(["AAA", "BBB", "CCC", "AAA"])]},
        chain_id=46630,
        protocols=["uniswap_v4"],
    )

    assert submitted == []


def test_scanner_skips_closed_loop_when_pool_candidates_missing(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_pair_key_trusted_symbols(monkeypatch)
    from dex.runtime import closed_loop_consumer
    from dex.runtime.closed_loop_state_machine import load_loop_state
    from dex.runtime.dex_opportunity_scanner_engine import DexOpportunityScanner

    database = tmp_path / "native_cfg_failure.sqlite3"
    monkeypatch.setattr(
        closed_loop_consumer,
        "resolve_runtime_database",
        lambda database_url=None: str(database),
    )
    monkeypatch.setattr(
        rust_mixin,
        "native_worker_status",
        lambda **kwargs: {"workers": []},
    )
    monkeypatch.setattr(
        rust_mixin,
        "build_group_task_config",
        lambda *, chain_id, protocols, routes: (
            None,
            None,
            [
                {
                    "reason": "pool_manager_refresh_required",
                    "kind": "pool_manager_refresh_required",
                    "retryable": True,
                }
            ],
        ),
    )
    scanner = object.__new__(DexOpportunityScanner)
    scanner.auto_execute_enabled = True

    rust_mixin.DexOpportunityNativeRustMixin._submit_native_closed_loop_groups(
        scanner,
        payload={"routes": [_route(["AAA", "BBB", "CCC", "AAA"])]},
        chain_id=46630,
        protocols=["uniswap_v4"],
    )

    state = load_loop_state(
        str(database),
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-46630-uniswap-v4",
        loop_key=FORWARD_LOOP_KEY,
    )
    assert state is None


def test_scanner_records_preflight_failed_when_native_rpc_config_unavailable(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_pair_key_trusted_symbols(monkeypatch)
    from dex.runtime import closed_loop_consumer
    from dex.runtime.closed_loop_state_machine import load_loop_state
    from dex.runtime.dex_opportunity_scanner_engine import DexOpportunityScanner

    database = tmp_path / "native_cfg_failure.sqlite3"
    monkeypatch.setattr(
        closed_loop_consumer,
        "resolve_runtime_database",
        lambda database_url=None: str(database),
    )
    monkeypatch.setattr(
        rust_mixin,
        "native_worker_status",
        lambda **kwargs: {"workers": []},
    )
    monkeypatch.setattr(
        rust_mixin,
        "build_group_task_config",
        lambda *, chain_id, protocols, routes: (
            None,
            None,
            [{"reason": "native_task_config_unavailable"}],
        ),
    )
    scanner = object.__new__(DexOpportunityScanner)
    scanner.auto_execute_enabled = True

    rust_mixin.DexOpportunityNativeRustMixin._submit_native_closed_loop_groups(
        scanner,
        payload={"routes": [_route(["AAA", "BBB", "CCC", "AAA"])]},
        chain_id=46630,
        protocols=["uniswap_v4"],
    )

    state = load_loop_state(
        str(database),
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-46630-uniswap-v4",
        loop_key=FORWARD_LOOP_KEY,
    )
    assert state is not None
    assert state["state"] == "preflight_failed"
    assert "native_task_config_unavailable" in str(state.get("last_error") or "")


def test_scanner_status_exposes_closed_loop_rust_workers(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.dex_opportunity_scanner import DexOpportunityScanner

    monkeypatch.setenv("DEX_CLOSED_LOOP_NATIVE_RUST", "1")
    calls: list[dict[str, Any]] = []

    def _compact_worker_status(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "engine": "rust_native_closed_loop",
            "maxWorkers": 1,
            "residentWorkers": 1,
            "workers": [
                {
                    "group_key": "aaa|bbb|ccc",
                    "state": "running",
                    "probe_count": 3,
                    "broadcast_count": 0,
                }
            ],
            "summary": {"running": 1},
        }

    monkeypatch.setattr(
        rust_mixin,
        "native_worker_status",
        _compact_worker_status,
    )
    scanner = DexOpportunityScanner(
        quote_payload_provider=lambda **kwargs: {"routes": []},
        auto_execute_enabled=True,
        cache_path=tmp_path / "dex_opportunity_snapshot.json",
        periodic_scan_enabled=False,
    )

    workers = scanner.status(chain_id=46630)["closed_loop_rust_workers"]

    assert workers["engine"] == "rust_native_closed_loop"
    assert workers["maxWorkers"] == workers["residentWorkers"] == 1
    assert workers["workers"][0]["group_key"] == "aaa|bbb|ccc"
    assert workers["workers"][0]["broadcast_count"] == 0
    assert calls and all(call.get("include_payloads") is False for call in calls)


def test_scanner_status_does_not_copy_full_snapshot_payload(
    tmp_path: Any,
) -> None:
    from dex.runtime.dex_opportunity_scanner_engine import DexOpportunityScanner

    class _PayloadMustNotBeCopied:
        def __deepcopy__(self, memo: dict[int, Any]) -> Any:
            raise AssertionError("status must not deep-copy the snapshot payload")

    scanner = DexOpportunityScanner(
        quote_payload_provider=lambda **kwargs: {"routes": []},
        auto_execute_enabled=False,
        cache_path=tmp_path / "status_snapshot_copy.json",
        periodic_scan_enabled=False,
    )
    scanner._native_rust_closed_loop = False
    scanner._snapshots["46630"] = {
        "chain_id": 46630,
        "payload": {"routes": [_PayloadMustNotBeCopied()]},
        "summary": {"routeCount": 1},
        "updated_at": "2026-11-07T00:00:00+00:00",
        "error": None,
    }

    status = scanner.status(chain_id=46630)

    assert status["snapshot_updated_at"] == "2026-11-07T00:00:00+00:00"


def test_native_worker_status_uses_compact_getter_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dex.runtime import rust_closed_loop

    class _FakePool:
        def __init__(self) -> None:
            self.status_calls = 0
            self.compact_calls = 0
            self.summary_calls = 0

        def status_json(self) -> dict[str, Any]:
            self.status_calls += 1
            return {"engine": "rust_native_closed_loop", "workers": [], "summary": {}}

        def compact_status_json(self) -> dict[str, Any]:
            self.compact_calls += 1
            return {"engine": "rust_native_closed_loop", "workers": [], "summary": {}}

        def summary_status_json(self) -> dict[str, Any]:
            self.summary_calls += 1
            return {
                "engine": "rust_native_closed_loop",
                "residentWorkers": 16,
                "workers": [],
                "summary": {"idle": 16},
            }

    pool = _FakePool()
    maintenance_detail_flags: list[bool] = []
    monkeypatch.setenv("DEX_CLOSED_LOOP_NATIVE_RUST", "1")
    monkeypatch.setattr(rust_closed_loop, "get_native_pool", lambda **kwargs: pool)
    monkeypatch.setattr(
        "dex.runtime.v4_maintenance_workers.v4_maintenance_worker_status",
        lambda **kwargs: maintenance_detail_flags.append(
            bool(kwargs.get("include_details"))
        )
        or {"started": False, "workers": [], "state": {}},
    )

    summary = rust_closed_loop.native_worker_status(
        include_payloads=False,
        include_workers=False,
    )
    compact = rust_closed_loop.native_worker_status(include_payloads=False)
    full = rust_closed_loop.native_worker_status()

    assert summary["residentWorkers"] == 16
    assert compact["enabled"] is True
    assert full["enabled"] is True
    assert pool.summary_calls == 1
    assert pool.compact_calls == 1
    assert pool.status_calls == 1
    assert maintenance_detail_flags == [False, False, True]


def test_native_worker_status_compact_falls_back_to_full_when_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dex.runtime import rust_closed_loop

    class _LegacyPool:
        def __init__(self) -> None:
            self.status_calls = 0

        def status_json(self) -> dict[str, Any]:
            self.status_calls += 1
            return {"engine": "rust_native_closed_loop", "workers": [], "summary": {}}

    pool = _LegacyPool()
    monkeypatch.setenv("DEX_CLOSED_LOOP_NATIVE_RUST", "1")
    monkeypatch.setattr(rust_closed_loop, "get_native_pool", lambda **kwargs: pool)

    payload = rust_closed_loop.native_worker_status(include_payloads=False)

    assert payload["enabled"] is True
    assert pool.status_calls == 1


def test_build_native_worker_attempt_records_profitable_broadcast_tx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "profitable_amount_found",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0493e0",
        "initialAmountRaw": "0x0f4240",
        "runArbitrage": {
            "ok": True,
            "profit": "0x493e0",
        },
        "attempts": [
            {
                "amountRaw": "0x0f4240",
                "profitRaw": "0x0493e0",
                "ok": True,
                "profitable": True,
                "routeIndex": "0x0",
                "attemptedCount": "0x1",
            }
        ],
        "broadcast": {
            "ok": True,
            "txHash": "0xabc123",
            "receipt": {
                "status": "0x1",
                "blockNumber": "0x2a",
                "gasUsed": "0x4a950",
                "effectiveGasPrice": "0x989680",
            },
        },
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    assert evidence["status"] == "live_verified"
    assert evidence["liveVerified"] is True
    assert evidence["livePriceVerified"] is True
    assert evidence["readyForTransactionPreflight"] is True
    assert evidence["trustedForExecution"] is False
    assert evidence["validation"]["allHopsQuoted"] is True
    assert evidence["costModel"]["selectedBorrowInputAmount"] == 1.0
    assert evidence["costModel"]["netProfitAmount"] == 0.3
    assert evidence["costModel"]["gasUsed"] == 305488
    assert evidence["costModel"]["effectiveGasPrice"] == 10000000
    assert evidence["costModel"]["gasCostEth"] == pytest.approx(0.00000305488)
    assert evidence["costModel"]["receiptStatus"] == "0x1"
    assert evidence["costModel"]["blockNumber"] == "0x2a"
    assert attempt["auto_execute"]["executed"] is True
    assert attempt["auto_execute"]["result"]["txHash"] == "0xabc123"
    assert attempt["auto_execute"]["result"]["gasUsedInt"] == 305488
    assert attempt["auto_execute"]["result"]["effectiveGasPriceInt"] == 10000000
    assert attempt["auto_execute"]["result"]["gasCostEth"] == pytest.approx(0.00000305488)
    assert attempt["auto_execute"]["result"]["receiptStatus"] == "0x1"
    assert attempt["auto_execute"]["result"]["blockNumber"] == "0x2a"
    assert attempt["state"] == "execution_success"
    assert attempt["tx_hash"] == "0xabc123"


def test_build_native_worker_attempt_keeps_aggregate_preview_out_of_live_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "profitable_amount_found",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0493e0",
        "initialAmountRaw": "0x0f4240",
        "attempts": [
            {
                "amountRaw": "0x0f4240",
                "profitRaw": "0x0493e0",
                "ok": True,
                "profitable": True,
                "routeIndex": "0x0",
                "attemptedCount": "0x1",
            }
        ],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    assert evidence["status"] == "static_reference_pending_live_review"
    assert evidence["liveVerified"] is False
    assert evidence["livePriceVerified"] is False
    assert evidence["readyForTransactionPreflight"] is False
    assert evidence["executionEligible"] is False
    assert evidence["trustedForExecution"] is False
    assert evidence["hops"] == []
    assert evidence["validation"]["allHopsQuoted"] is False


def test_build_native_worker_attempt_marks_pending_broadcast_as_submitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "profitable_amount_found",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0493e0",
        "initialAmountRaw": "0x0f4240",
        "attempts": [
            {
                "amountRaw": "0x0f4240",
                "profitRaw": "0x0493e0",
                "ok": True,
                "profitable": True,
                "routeIndex": "0x0",
                "attemptedCount": "0x1",
            }
        ],
        "broadcastExecuted": True,
        "broadcast": {
            "ok": True,
            "txHash": "0xabcPending",
            "receipt": {"status": "receipt_pending"},
        },
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    auto_execute = attempt["auto_execute"]
    assert auto_execute["submitted"] is True
    assert auto_execute["executed"] is True
    assert auto_execute["confirmed"] is False
    assert auto_execute["confirmationStatus"] == "pending"
    assert auto_execute["reason"] == "broadcast_pending_confirmation"
    assert attempt["state"] == "submitted_success"
    assert attempt["checks_passed"] is True
    assert attempt["tx_hash"] == "0xabcPending"


def test_build_native_worker_attempt_marks_reverted_broadcast_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "profitable_amount_found",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0493e0",
        "initialAmountRaw": "0x0f4240",
        "attempts": [
            {
                "amountRaw": "0x0f4240",
                "profitRaw": "0x0493e0",
                "ok": True,
                "profitable": True,
                "routeIndex": "0x0",
                "attemptedCount": "0x1",
            }
        ],
        "broadcastExecuted": True,
        "broadcast": {
            "ok": True,
            "txHash": "0xabcReverted",
            "receipt": {"status": "0x0"},
        },
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    auto_execute = attempt["auto_execute"]
    assert auto_execute["submitted"] is True
    assert auto_execute["executed"] is True
    assert auto_execute["confirmed"] is False
    assert auto_execute["confirmationStatus"] == "failed"
    assert auto_execute["reason"] == "broadcast_reverted"
    assert attempt["state"] == "execution_failed"
    assert attempt["checks_passed"] is False
    assert attempt["tx_hash"] == "0xabcReverted"
    assert "0x0" in attempt["error"]
    assert "reverted" in attempt["blocked_reasons"][0]


def test_build_native_worker_attempt_records_failed_broadcast_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "profitable_amount_found",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0493e0",
        "initialAmountRaw": "0x0f4240",
        "attempts": [
            {
                "amountRaw": "0x0f4240",
                "profitRaw": "0x0493e0",
                "ok": True,
                "profitable": True,
                "routeIndex": "0x0",
                "attemptedCount": "0x1",
            }
        ],
        "broadcastExecuted": False,
        "broadcast": {
            "ok": False,
            "attempted": True,
            "failed": True,
            "status": "broadcast_failed",
            "error": "eth_sendRawTransaction RPC request failed",
            "txHash": None,
        },
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    auto_execute = attempt["auto_execute"]
    assert auto_execute["attempted"] is True
    assert auto_execute["executed"] is False
    assert auto_execute["reason"] == "broadcast_failed"
    assert auto_execute["result"]["error"] == "eth_sendRawTransaction RPC request failed"
    assert attempt["state"] == "execution_failed"
    assert attempt["error"] == "eth_sendRawTransaction RPC request failed"
    assert attempt["quote"]["live_onchain_evidence"]["costModel"]["netProfitAmount"] == 0.3


def test_build_native_worker_attempt_reports_missing_broadcast_when_gate_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    monkeypatch.setattr(
        rust_closed_loop,
        "broadcast_gate_status",
        lambda *, chain_id: {
            "autoExecuteEnabled": True,
            "broadcastAllowed": True,
            "broadcastEnabled": True,
            "privateKeyConfigured": True,
            "privateKeySource": "LIQUIDATION_EXECUTION_PRIVATE_KEY",
            "reasons": [],
        },
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "profitable_amount_found",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0493e0",
        "initialAmountRaw": "0x0f4240",
        "runArbitrage": {
            "ok": True,
            "profit": "0x493e0",
        },
        "attempts": [
            {
                "amountRaw": "0x0f4240",
                "profitRaw": "0x0493e0",
                "ok": True,
                "profitable": True,
                "routeIndex": "0x0",
                "attemptedCount": "0x1",
            }
        ],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    auto_execute = attempt["auto_execute"]
    assert auto_execute["skipped"] is True
    assert auto_execute["reason"] == "broadcast_result_missing"
    assert auto_execute["broadcastResultMissing"] is True
    assert auto_execute["broadcastGate"]["broadcastEnabled"] is True
    assert auto_execute["broadcastGate"]["reasons"] == []
    assert attempt["auto_execute_requested"] is True


def test_build_native_worker_attempt_marks_execution_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    monkeypatch.setattr(
        rust_closed_loop,
        "broadcast_gate_status",
        lambda *, chain_id: {
            "autoExecuteEnabled": True,
            "broadcastAllowed": True,
            "broadcastEnabled": True,
            "privateKeyConfigured": True,
            "privateKeySource": "LIQUIDATION_EXECUTION_PRIVATE_KEY",
            "reasons": [],
        },
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "profitable_amount_found",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0493e0",
        "initialAmountRaw": "0x0f4240",
        "executionQueued": True,
        "executionQueueAt": "2026-10-20T00:01:00+00:00",
        "runArbitrage": {
            "ok": True,
            "profit": "0x493e0",
        },
        "attempts": [
            {
                "amountRaw": "0x0f4240",
                "profitRaw": "0x0493e0",
                "ok": True,
                "profitable": True,
                "routeIndex": "0x0",
                "attemptedCount": "0x1",
            }
        ],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    auto_execute = attempt["auto_execute"]
    assert auto_execute["skipped"] is True
    assert auto_execute["queued"] is True
    assert auto_execute["reason"] == "broadcast_queued"
    assert auto_execute["broadcastResultMissing"] is False
    assert auto_execute["executionQueueAt"] == "2026-10-20T00:01:00+00:00"
    assert attempt["executionQueued"] is True
    assert attempt["executionQueueAt"] == "2026-10-20T00:01:00+00:00"
    assert attempt["auto_execute_requested"] is True


def test_build_native_worker_attempt_marks_group_stopped_broadcast_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    monkeypatch.setattr(
        rust_closed_loop,
        "broadcast_gate_status",
        lambda *, chain_id: {
            "autoExecuteEnabled": True,
            "broadcastAllowed": True,
            "broadcastEnabled": True,
            "privateKeyConfigured": True,
            "privateKeySource": "LIQUIDATION_EXECUTION_PRIVATE_KEY",
            "reasons": [],
        },
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "profitable_amount_found",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0493e0",
        "initialAmountRaw": "0x0f4240",
        "broadcastSkipped": "group_stopped",
        "stopRequested": True,
        "runArbitrage": {
            "ok": True,
            "profit": "0x493e0",
        },
        "attempts": [
            {
                "amountRaw": "0x0f4240",
                "profitRaw": "0x0493e0",
                "ok": True,
                "profitable": True,
                "routeIndex": "0x0",
                "attemptedCount": "0x1",
            }
        ],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    auto_execute = attempt["auto_execute"]
    assert auto_execute["skipped"] is True
    assert auto_execute["reason"] == "broadcast_group_stopped"
    assert auto_execute["attempted"] is False
    assert auto_execute["broadcastGate"]["broadcastEnabled"] is True
    assert attempt["broadcastSkipped"] == "group_stopped"
    assert attempt["auto_execute_requested"] is False
    assert attempt["precheck"]["auto_execute_requested"] is False
    assert attempt["quote"]["live_onchain_evidence"]["profitable"] is True


def test_build_native_worker_attempt_clears_static_blockers_when_profitable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    route["quoteAnalysis"] = {
        "requiresLiveQuoter": True,
        "adapterRequired": True,
        "executableCandidate": False,
        "chainSubmissionCandidate": False,
        "recommendation": {
            "level": "当前仅公开静态路由框架",
            "reasons": ["需要链上实时 Quoter"],
        },
    }
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "profitable_amount_found",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0493e0",
        "initialAmountRaw": "0x0f4240",
        "runArbitrage": {
            "ok": True,
            "profit": "0x493e0",
        },
        "attempts": [
            {
                "amountRaw": "0x0f4240",
                "profitRaw": "0x0493e0",
                "ok": True,
                "profitable": True,
                "routeIndex": "0x0",
                "attemptedCount": "0x1",
            }
        ],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    assert evidence["profitable"] is True
    assert evidence["executionEligible"] is True
    assert attempt["blocked_reasons"] == []
    assert attempt["precheck"]["reasons"] == []
    assert attempt["precheck"]["ready_for_transaction_preflight"] is True


def test_build_native_worker_attempt_reads_nested_receipt_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = _route(["ETH", "AMZN", "AMD", "ETH"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "status": "profitable_amount_found",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x186a0",
        "initialAmountRaw": "0x0f4240",
        "attempts": [
            {
                "amountRaw": "0x0f4240",
                "profitRaw": "0x186a0",
                "ok": True,
                "profitable": True,
                "routeIndex": "0x0",
                "attemptedCount": "0x1",
            }
        ],
        "broadcast": {
            "ok": True,
            "txHash": "0xabc999",
            "receipt": {
                "receipt": {
                    "status": "0x1",
                    "blockNumber": "0x1234",
                    "gasUsed": "0x5208",
                    "effectiveGasPrice": "0x3b9aca00",
                }
            },
        },
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    assert evidence["costModel"]["gasUsed"] == 21000
    assert evidence["costModel"]["effectiveGasPrice"] == 1000000000
    assert evidence["costModel"]["gasCostEth"] == pytest.approx(0.000021)
    assert evidence["costModel"]["netAfterGasEth"] == pytest.approx(0.099979)
    auto_result = attempt["auto_execute"]["result"]
    assert auto_result["gasUsedInt"] == 21000
    assert auto_result["effectiveGasPriceInt"] == 1000000000
    assert auto_result["receiptStatus"] == "0x1"
    assert auto_result["blockNumber"] == "0x1234"


def test_build_native_worker_attempt_keeps_broadcast_disabled_when_gate_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    monkeypatch.setattr(
        rust_closed_loop,
        "broadcast_gate_status",
        lambda *, chain_id: {
            "autoExecuteEnabled": True,
            "broadcastAllowed": False,
            "broadcastEnabled": False,
            "privateKeyConfigured": True,
            "privateKeySource": "LIQUIDATION_EXECUTION_PRIVATE_KEY",
            "reasons": ["ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST is not true"],
        },
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "status": "profitable_amount_found",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0493e0",
        "initialAmountRaw": "0x0f4240",
        "runArbitrage": {
            "ok": True,
            "profit": "0x493e0",
        },
        "attempts": [
            {
                "amountRaw": "0x0f4240",
                "profitRaw": "0x0493e0",
                "ok": True,
                "profitable": True,
                "routeIndex": "0x0",
                "attemptedCount": "0x1",
            }
        ],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    auto_execute = attempt["auto_execute"]
    assert auto_execute["skipped"] is True
    assert auto_execute["reason"] == "native_broadcast_disabled"
    assert auto_execute["broadcastResultMissing"] is False
    assert auto_execute["broadcastGate"]["broadcastEnabled"] is False


def test_build_native_worker_attempt_records_unprofitable_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "not_profitable_after_costs",
        "initialAmountRaw": "0x0f4240",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0",
        "reportedNetProductDeltaRaw": "-0x0de0b6b3a7640000",
        "poolPreviewDetailed": {
            "ok": True,
            "numericValuesAvailable": True,
            "netProduct": "0xde0b6b3a7640000",
            "netProductDeltaRaw": "-0x0de0b6b3a7640000",
            "profitable": False,
        },
        "attempts": [],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    assert evidence["status"] == "route_unprofitable"
    assert evidence["costModel"]["profitable"] is False
    assert evidence["costModel"]["netProfitAmount"] == pytest.approx(-1.0)
    assert evidence["costModel"]["netProfitAmountMissingReason"] is None
    assert (
        evidence["costModel"]["netProfitReferenceSource"]
        == "native_contract_rate_delta_estimate"
    )
    assert evidence["referenceNetProfitAmount"] == pytest.approx(-1.0)
    assert attempt["auto_execute"]["skipped"] is True
    assert attempt["auto_execute"]["reason"] == "manual_review_not_profitable"
    assert "NoProfitableRoute" in " ".join(evidence["blockers"])


def test_build_native_worker_attempt_keeps_initial_amount_null_when_only_zero_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount",
        "searchMode": "inner_single_call",
        "status": "not_profitable_after_costs",
        "selectedAmountRaw": "0x0",
        "selectedProfitRaw": "0x0",
        "error": "previewBestPoolDetailed unavailable",
        "poolPreview": {"ok": False, "error": "previewBestPool unavailable"},
        "poolPreviewDetailed": {
            "ok": False,
            "numericValuesUnavailable": True,
            "error": "previewBestPoolDetailed unavailable",
        },
        "attempts": [],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    assert evidence["costModel"]["initialProbeAmount"] is None
    assert evidence["costModel"]["requestedBorrowInputAmount"] is None
    assert evidence["costModel"]["borrowInputAmount"] is None


def test_build_native_worker_attempt_preserves_rust_initial_amount_when_quote_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount",
        "searchMode": "inner_single_call",
        "status": "not_profitable_after_costs",
        "initialAmountRaw": "0xf4240",
        "minProfitRaw": "0x1",
        "selectedAmountRaw": "0x0",
        "selectedProfitRaw": "0x0",
        "error": "previewBestPoolDetailed unavailable",
        "poolPreview": {"ok": False, "error": "previewBestPool unavailable"},
        "poolPreviewDetailed": {
            "ok": False,
            "numericValuesUnavailable": True,
            "error": "previewBestPoolDetailed unavailable",
        },
        "attempts": [],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    assert evidence["costModel"]["initialProbeAmount"] == 1.0
    assert evidence["costModel"]["requestedBorrowInputAmount"] == 1.0
    assert evidence["costModel"]["borrowInputAmount"] == 1.0


def test_build_native_worker_attempt_records_signed_negative_net_profit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "not_profitable_after_costs",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0",
        "attempts": [
            {
                "amountRaw": "0x0f4240",
                "profitRaw": "-123",
                "ok": False,
                "profitable": False,
                "routeIndex": "0x0",
                "attemptedCount": "0x1",
            }
        ],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    assert evidence["costModel"]["netProfitAmount"] == pytest.approx(-0.000123)
    assert evidence["referenceNetProfitAmount"] == pytest.approx(-0.000123)


def test_build_native_worker_attempt_records_concrete_no_profit_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "not_profitable_after_costs",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0",
        "reportedNetProductDeltaRaw": "-0x14",
        "poolPreviewDetailed": {
            "ok": True,
            "numericValuesAvailable": True,
            "netProduct": "0x0f",
            "netProductDeltaRaw": "-0x14",
            "profitable": False,
        },
        "runArbitrage": {
            "ok": False,
            "noProfitableRoute": True,
            "arbitrageNotProfitable": {
                "receivedRaw": "0x0f",
                "requiredRaw": "0x14",
                "profitDeltaRaw": "-0x7a120",
            },
        },
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    assert evidence["costModel"]["netProfitAmount"] == pytest.approx(-0.5)
    assert evidence["costModel"]["netProfitAmountMissingReason"] is None
    assert evidence["referenceRateProductDelta"] == pytest.approx(-0x14 / 1e18)
    assert evidence["detailedPreviewValuesAvailable"] is True
    assert evidence["selectedProfitRaw"] == "-0x7a120"
    assert evidence["selectedProfitAmount"] == pytest.approx(-0.5)
    assert evidence["selectedProfitSource"] == "native_rust_contract_preview"
    assert evidence["costModel"]["selectedProfitRaw"] == "-0x7a120"
    assert evidence["costModel"]["selectedProfitAmount"] == pytest.approx(-0.5)
    assert attempt["selectedProfitMissingReason"] is None


def test_build_native_worker_attempt_records_contract_rate_delta_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 18,
    )
    _mock_sqlite_reference_unavailable(monkeypatch)
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "not_profitable_after_costs",
        "initialAmountRaw": "0x0de0b6b3a7640000",
        "selectedAmountRaw": "0x0",
        "selectedProfitRaw": "0x0",
        "reportedNetProductDeltaRaw": "-0xf322b272f3d06b",
        "poolPreviewDetailed": {
            "ok": True,
            "numericValuesAvailable": True,
            "netProduct": "0xde0cf324534aad55",
            "netProductDeltaRaw": "-0xf322b272f3d06b",
            "profitable": False,
        },
        "attempts": [],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    costs = evidence["costModel"]
    assert evidence["status"] == "route_unprofitable"
    assert costs["netProfitAmount"] == pytest.approx(-0.0684365691687937)
    assert costs["netProfitAmountMissingReason"] is None
    assert costs["netProfitReferenceSource"] == "native_contract_rate_delta_estimate"
    assert costs["contractNetProductDelta"] == pytest.approx(-0.0684365691687937)
    assert costs["contractNetProductDeltaRaw"] == "-0xf322b272f3d06b"
    assert evidence["selectedProfitRaw"].startswith("-0xf322b272f3d06")
    assert evidence["selectedProfitAmount"] == pytest.approx(-0.0684365691687937)
    assert evidence["selectedProfitSource"] == "native_contract_rate_delta_estimate"
    assert any("netProductDelta=-0.0684365691687937" in blocker for blocker in evidence["blockers"])
    assert attempt["selectedProfitMissingReason"] is None


def test_build_native_worker_attempt_keeps_positive_rate_delta_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 18,
    )
    _mock_sqlite_reference_unavailable(monkeypatch)
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "not_profitable_after_costs",
        "initialAmountRaw": "0x0de0b6b3a7640000",
        "selectedAmountRaw": "0x0",
        "selectedProfitRaw": "0x0",
        "reportedNetProductDeltaRaw": "0x4fb199a426ec68",
        "poolPreviewDetailed": {
            "ok": True,
            "numericValuesAvailable": True,
            "netProduct": "0xde0b6b49a7b426ec68",
            "netProductDeltaRaw": "0x4fb199a426ec68",
            "profitable": False,
        },
        "attempts": [],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    costs = evidence["costModel"]
    assert evidence["status"] == "static_reference_pending_live_review"
    assert evidence["executionEligible"] is False
    assert evidence["readyForTransactionPreflight"] is False
    assert costs["netProfitAmount"] == pytest.approx(0.022431796602268777)
    assert costs["positiveRateDeltaUnconfirmed"] is True
    assert costs["contractNetProductDelta"] == pytest.approx(0.022431796602268777)
    assert costs["netProfitReferenceSource"] == "native_contract_rate_delta_estimate"
    assert "not execution-confirmed" in " ".join(evidence["blockers"])
    assert attempt["selectedProfitMissingReason"] is None
    assert evidence["selectedProfitAmount"] == pytest.approx(0.022431796602268777)
    assert evidence["selectedProfitSource"] == "native_contract_rate_delta_estimate"


def test_build_native_worker_attempt_keeps_swap_not_resolved_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 18,
    )
    _mock_sqlite_reference_unavailable(monkeypatch)
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "not_profitable_after_costs",
        "initialAmountRaw": "0x0de0b6b3a7640000",
        "selectedAmountRaw": "0x0",
        "selectedProfitRaw": "0x0",
        "reportedNetProductDeltaRaw": "0x4fb199a426ec68",
        "selectedKeys": [
            {
                "currency0": "0x" + ("1" * 40),
                "currency1": "0x" + ("2" * 40),
                "fee": 3000,
                "tickSpacing": 60,
                "hooks": "0x0000000000000000000000000000000000000000",
            },
            {
                "currency0": "0x" + ("2" * 40),
                "currency1": "0x" + ("3" * 40),
                "fee": 3000,
                "tickSpacing": 60,
                "hooks": "0x0000000000000000000000000000000000000000",
            },
            {
                "currency0": "0x" + ("1" * 40),
                "currency1": "0x" + ("3" * 40),
                "fee": 3000,
                "tickSpacing": 60,
                "hooks": "0x0000000000000000000000000000000000000000",
            },
        ],
        "selectedDirections": [True, True, False],
        "poolPreviewDetailed": {
            "ok": True,
            "numericValuesAvailable": True,
            "netProduct": "0xde0b6b49a7b426ec68",
            "netProductDeltaRaw": "0x4fb199a426ec68",
            "profitable": False,
        },
        "runArbitrage": {
            "ok": False,
            "noProfitableRoute": False,
            "error": "execution reverted: SwapNotResolved()",
            "rawError": "execution reverted: SwapNotResolved()",
            "revertData": "0xb20c2204",
            "revertSelector": "0xb20c2204",
        },
        "attempts": [],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    costs = evidence["costModel"]
    assert evidence["status"] == "route_unprofitable"
    assert evidence["executionEligible"] is False
    assert evidence["profitable"] is False
    assert evidence["runArbitrageErrorKind"] == "swap_not_resolved"
    assert evidence["runArbitrage"]["revertSelector"] == "0xb20c2204"
    route_diag = evidence["runArbitrageRouteDiagnostic"]
    assert route_diag["available"] is True
    assert route_diag["routeClosed"] is True
    assert route_diag["reasons"] == []
    assert [item["hopLabel"] for item in route_diag["hops"]] == [
        "entry",
        "bridge",
        "exit",
    ]
    assert costs["positiveRateDeltaUnconfirmed"] is True
    assert costs["netProfitAmount"] == pytest.approx(0.022431796602268777)
    assert costs["netProfitReferenceSource"] == "native_contract_rate_delta_estimate"
    assert "SwapNotResolved" in " ".join(evidence["blockers"])
    assert "0xb20c2204" in " ".join(evidence["blockers"])
    assert attempt["auto_execute"]["skipped"] is True
    assert attempt["auto_execute"]["reason"] == "manual_review_not_profitable"


def test_build_native_worker_attempt_route_stage_locates_pool_currency_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 18,
    )
    _mock_sqlite_reference_unavailable(monkeypatch)
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "not_profitable_after_costs",
        "initialAmountRaw": "0x0de0b6b3a7640000",
        "selectedAmountRaw": "0x0",
        "selectedProfitRaw": "0x0",
        "selectedKeys": [
            {
                "currency0": "0x" + ("1" * 40),
                "currency1": "0x" + ("2" * 40),
                "fee": 3000,
                "tickSpacing": 60,
                "hooks": "0x0000000000000000000000000000000000000000",
            },
            {
                "currency0": "0x" + ("2" * 40),
                "currency1": "0x" + ("9" * 40),
                "fee": 3000,
                "tickSpacing": 60,
                "hooks": "0x0000000000000000000000000000000000000000",
            },
            {
                "currency0": "0x" + ("1" * 40),
                "currency1": "0x" + ("3" * 40),
                "fee": 3000,
                "tickSpacing": 60,
                "hooks": "0x0000000000000000000000000000000000000000",
            },
        ],
        "selectedDirections": [True, True, False],
        "runArbitrage": {
            "ok": False,
            "noProfitableRoute": False,
            "error": "execution reverted: SwapNotResolved()",
            "rawError": "execution reverted: SwapNotResolved()",
            "revertData": "0xb20c2204",
            "revertSelector": "0xb20c2204",
        },
        "attempts": [],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    route_diag = evidence["runArbitrageRouteDiagnostic"]
    assert route_diag["available"] is True
    assert route_diag["hops"][0]["reasons"] == []
    assert route_diag["hops"][1]["hopLabel"] == "bridge"
    assert route_diag["hops"][1]["reasons"] == [
        "pool_currency_mismatch_output"
    ]
    assert route_diag["reasons"] == ["pool_currency_mismatch_output"]
    assert "route stage: pool_currency_mismatch_output" in " ".join(
        evidence["blockers"]
    )
    assert evidence["runArbitrageRouteDiagnostic"]["contractCanLocalizeRevert"] is False


def test_build_native_worker_attempt_run_arbitrage_ok_is_execution_eligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "profitable_amount_found",
        "initialAmountRaw": "0x0f4240",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0493e0",
        "runArbitrage": {
            "ok": True,
            "outTb": "0x2",
            "outTa": "0x3",
            "outTz": "0x4",
            "profit": "0x493e0",
        },
        "attempts": [
            {
                "amountRaw": "0x0f4240",
                "profitRaw": "0x0493e0",
                "ok": True,
                "profitable": True,
                "routeIndex": "0x0",
                "attemptedCount": "0x1",
            }
        ],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    assert evidence["status"] == "live_verified"
    assert evidence["executionEligible"] is True
    assert evidence["readyForTransactionPreflight"] is True
    assert evidence["profitable"] is True
    assert evidence["validation"]["allHopsQuoted"] is True
    assert evidence["blockers"] == []
    assert attempt["precheck"]["ready_for_transaction_preflight"] is True


def test_signed_native_profit_reads_detailed_arbitrage_not_profitable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    _mock_sqlite_reference_unavailable(monkeypatch)
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "status": "not_profitable_after_costs",
        "initialAmountRaw": "0x0f4240",
        "selectedAmountRaw": "0x0",
        "selectedProfitRaw": "0x0",
        "poolPreviewDetailed": {
            "ok": False,
            "noProfitableRoute": True,
            "numericValuesUnavailable": True,
            "error": "NoProfitableRoute",
            "revertData": "0x",
            "arbitrageNotProfitable": {
                "receivedRaw": "0x0f",
                "requiredRaw": "0x14",
                "profitDeltaRaw": "-0x05",
            },
        },
        "attempts": [],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    costs = evidence["costModel"]
    assert costs["netProfitAmount"] == pytest.approx(-0.000005)
    assert costs["netProfitAmountMissingReason"] is None
    assert evidence["selectedProfitRaw"] == "-0x5"
    assert evidence["selectedProfitAmount"] == pytest.approx(-0.000005)


def test_build_native_worker_attempt_skips_non_attempt_broadcast_during_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "profitable_amount_found",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0493e0",
        "initialAmountRaw": "0x0f4240",
        "attempts": [
            {
                "amountRaw": "0x0f4240",
                "profitRaw": "0x0493e0",
                "ok": True,
                "profitable": True,
                "routeIndex": "0x0",
                "attemptedCount": "0x1",
            }
        ],
        "broadcast": {
            "ok": False,
            "attempted": False,
            "failed": False,
            "status": "requires_live_quoter",
            "error": "static review cannot broadcast before live quote verification",
            "submitted": False,
            "txHash": None,
        },
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    auto_execute = attempt["auto_execute"]
    assert auto_execute["skipped"] is True
    assert auto_execute["attempted"] is False
    assert auto_execute["reason"] == "static_reference_pending_live_review"
    assert attempt["reviewBroadcastSkipped"] is True
    assert attempt["state"] == "quote_required"
    assert attempt["auto_execute_requested"] is False


def test_build_native_worker_attempt_marks_review_phase_broadcast_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    _mock_sqlite_reference_unavailable(monkeypatch)
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "profitable_amount_found",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0493e0",
        "initialAmountRaw": "0x0f4240",
        "attempts": [
            {
                "amountRaw": "0x0f4240",
                "profitRaw": "0x0493e0",
                "ok": True,
                "profitable": True,
                "routeIndex": "0x0",
                "attemptedCount": "0x1",
            }
        ],
        "broadcast": {
            "ok": False,
            "attempted": True,
            "failed": True,
            "status": "broadcast_failed",
            "error": "execution reverted",
            "submitted": False,
            "txHash": None,
        },
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    assert attempt["state"] == "execution_failed"
    assert attempt["broadcastAttemptedDuringReview"] is True
    assert attempt["auto_execute"]["attempted"] is True


def test_build_native_worker_attempt_uses_sqlite_reference_when_no_signed_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )

    def fake_sqlite_reference(**kwargs):
        return {
            "referenceNetProfitAmount": -0.75,
            "referenceFinalOutputAmount": 0.25,
            "referenceNetReturnBps": -7500.0,
        }

    monkeypatch.setattr(
        "market.binance_market.dex_route_test._offline_v4_route_sqlite_pnl_reference",
        fake_sqlite_reference,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "status": "not_profitable_after_costs",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0",
        "poolPreviewDetailed": {
            "ok": False,
            "numericValuesUnavailable": True,
            "error": "NoProfitableRoute",
        },
        "attempts": [],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    costs = evidence["costModel"]
    assert evidence["status"] == "route_unprofitable"
    assert costs["netProfitAmount"] == pytest.approx(-0.75)
    assert costs["finalOutputAmount"] == pytest.approx(0.25)
    assert costs["netProfitAmountBps"] == pytest.approx(-7500.0)
    assert costs["netProfitAmountMissingReason"] is None
    assert costs["netProfitReferenceSource"] == "offline_v4_sqlite_route_snapshot"
    assert evidence["referenceNetProfitMethod"] == "offline_v4_sqlite_route_snapshot"


def test_build_native_worker_attempt_does_not_accept_zero_when_no_signed_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    _mock_sqlite_reference_unavailable(monkeypatch)
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "status": "not_profitable_after_costs",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0",
        "poolPreviewDetailed": {
            "ok": False,
            "numericValuesUnavailable": True,
            "error": "NoProfitableRoute",
        },
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    assert evidence["costModel"]["netProfitAmount"] is None
    assert "concrete" in str(evidence["costModel"]["netProfitAmountMissingReason"])
    assert evidence["costModel"]["netProfitReferenceSource"] is None
    assert attempt["selectedProfitAmount"] is None
    assert attempt["selectedProfitSource"] == "concrete_pnl_unavailable"
    assert attempt["selectedProfitMissingReason"] is not None
    assert evidence["referenceRateProductDelta"] is None
    assert evidence["detailedPreviewValuesAvailable"] is False


def test_build_native_worker_attempt_falls_back_when_detailed_selector_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    _mock_sqlite_reference_unavailable(monkeypatch)
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "status": "not_profitable_after_costs",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0",
        "error": "previewBestPoolDetailed unavailable",
        "poolPreview": {"ok": True, "netProduct": "0x0f"},
        "poolPreviewDetailed": {
            "ok": False,
            "numericValuesUnavailable": True,
            "error": "previewBestPoolDetailed unavailable",
            "message": "execution reverted",
            "revertData": "0x",
            "fallback": "not_profitable_after_costs_without_numeric_values",
        },
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    assert evidence["status"] == "review_failed"
    assert evidence["reviewFailureKind"] == "quote_unavailable"
    assert evidence["costModel"]["profitable"] is False
    assert evidence["costModel"]["netProfitAmount"] is None
    assert "concrete" in str(evidence["costModel"]["netProfitAmountMissingReason"])
    assert evidence["costModel"]["netProfitReferenceSource"] is None
    assert attempt["selectedProfitAmount"] is None
    assert attempt["selectedProfitSource"] == "concrete_pnl_unavailable"
    assert attempt["selectedProfitMissingReason"] is not None
    assert evidence["detailedPreviewValuesAvailable"] is False
    assert "previewBestPoolDetailed unavailable" in " ".join(evidence["blockers"])
    assert attempt["auto_execute"]["skipped"] is True
    assert attempt["auto_execute"]["reason"] == "review_failed_quote_unavailable"


def test_build_native_worker_attempt_falls_back_when_preview_selector_reverts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    _mock_sqlite_reference_unavailable(monkeypatch)
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "status": "not_profitable_after_costs",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0",
        "error": "previewBestPool unavailable; previewBestPoolDetailed unavailable",
        "poolPreview": {
            "ok": False,
            "numericValuesUnavailable": True,
            "error": "previewBestPool unavailable",
            "message": "execution reverted: arithmetic underflow or overflow",
            "revertData": "0x",
            "fallback": "not_profitable_after_costs_without_numeric_values",
        },
        "poolPreviewDetailed": {
            "ok": False,
            "numericValuesUnavailable": True,
            "error": "previewBestPoolDetailed unavailable",
            "message": "execution reverted",
            "revertData": "0x",
            "fallback": "not_profitable_after_costs_without_numeric_values",
        },
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    assert evidence["status"] == "review_failed"
    assert evidence["reviewFailureKind"] == "quote_unavailable"
    assert evidence["costModel"]["profitable"] is False
    assert evidence["costModel"]["netProfitAmount"] is None
    assert "concrete" in str(evidence["costModel"]["netProfitAmountMissingReason"])
    assert evidence["costModel"]["netProfitReferenceSource"] is None
    assert attempt["selectedProfitAmount"] is None
    assert attempt["selectedProfitSource"] == "concrete_pnl_unavailable"
    assert attempt["selectedProfitMissingReason"] is not None
    assert evidence["detailedPreviewValuesAvailable"] is False
    assert "previewBestPool unavailable" in " ".join(evidence["blockers"])
    assert "previewBestPoolDetailed unavailable" in " ".join(evidence["blockers"])
    assert attempt["auto_execute"]["skipped"] is True
    assert attempt["auto_execute"]["reason"] == "review_failed_quote_unavailable"


def test_enrich_native_worker_direction_results_keeps_each_direction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    payload = {
        "workers": [
            {
                "group_key": "aaa|bbb|ccc",
                "chain_id": 46630,
                "updated_at": "2026-10-20T01:00:00+00:00",
                "direction_results": [
                    {
                        "direction": "aaa|bbb|ccc|aaa",
                        "routeSymbols": ["AAA", "BBB", "CCC", "AAA"],
                        "routeAddresses": ["0x" + ("1" * 40), "0x" + ("2" * 40), "0x" + ("3" * 40), "0x" + ("1" * 40)],
                        "checkedAt": "2026-10-20T01:00:00+00:00",
                        "selected": True,
                        "status": "profitable_amount_found",
                        "broadcast": {"txHash": "0xabc", "submittedAt": "2026-10-20T01:00:05+00:00"},
                        "route": {
                            "selectedAmountRaw": "0x0f4240",
                            "selectedProfitRaw": "0x0493e0",
                        },
                    },
                    {
                        "direction": "aaa|ccc|bbb|aaa",
                        "routeSymbols": ["AAA", "CCC", "BBB", "AAA"],
                        "routeAddresses": ["0x" + ("1" * 40), "0x" + ("3" * 40), "0x" + ("2" * 40), "0x" + ("1" * 40)],
                        "checkedAt": "2026-10-20T01:00:00+00:00",
                        "selected": False,
                        "status": "not_profitable_after_costs",
                        "route": {
                            "reportedNetProfitRaw": "-0x7a120",
                            "runArbitrage": {
                                "arbitrageNotProfitable": {
                                    "profitDeltaRaw": "-0x7a120",
                                }
                            },
                        },
                    },
                ],
            }
        ]
    }

    rust_closed_loop._enrich_native_worker_direction_results(payload)

    directions = payload["workers"][0]["direction_results"]
    assert directions[0]["netProfitAmount"] == pytest.approx(0.3)
    assert directions[0]["lastReviewAt"] == "2026-10-20T01:00:00+00:00"
    assert directions[0]["lastTradeAt"] == "2026-10-20T01:00:05+00:00"
    assert directions[0]["lastTradeTxHash"] == "0xabc"
    assert directions[1]["netProfitAmount"] == pytest.approx(-0.5)
    assert directions[1]["lastTradeAt"] is None


def test_build_native_worker_attempt_records_route_error_as_rpc_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "status": "route_error",
        "error": "official_v4:probeAmount=0x0f4240:official_v4:previewArbitrageBestAmount:eth_call:attempt=3: out of gas",
        "selectedAmountRaw": "0x0",
        "selectedProfitRaw": "0x0",
        "attempts": [],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    assert evidence["status"] == "rpc_verification_failed"
    assert evidence["errorKind"] == "gas_estimation_failed"
    assert evidence["errorLocation"]["callSite"] == "previewArbitrageBestAmount"
    assert evidence["errorLocation"]["rpcMethod"] == "eth_call"
    assert evidence["errorLocation"]["attempt"] == 3
    assert evidence["errorLocation"]["probeAmountRaw"] == "0x0f4240"
    assert evidence["errorLocation"]["gasLimit"] == 50_000_000
    assert evidence["errorStack"][0]["nodeId"] == "AAA|BBB|CCC|AAA"
    assert evidence["profitable"] is False
    assert "gas estimation failed" in evidence["blockers"][0]
    assert attempt["auto_execute"]["skipped"] is True
    assert attempt["auto_execute"]["reason"] == "gas_estimation_failed"


def test_build_native_worker_attempt_keeps_generic_route_error_as_rpc_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "status": "route_error",
        "error": "eth_call RPC request failed",
        "errorKind": "rpc_verification_failed",
        "selectedAmountRaw": "0x0",
        "selectedProfitRaw": "0x0",
        "attempts": [],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    assert evidence["status"] == "rpc_verification_failed"
    assert evidence["errorKind"] == "rpc_verification_failed"
    assert evidence["blockers"] == ["eth_call RPC request failed"]
    assert attempt["auto_execute"]["skipped"] is True
    assert attempt["auto_execute"]["reason"] == "rpc_verification_failed"


def test_build_native_worker_attempt_defers_server_busy_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "status": "route_error",
        "error": "official_v4:previewBestPool:eth_call: the network is busy, please try again in a moment",
        "errorKind": "rpc_busy",
        "selectedAmountRaw": "0x0",
        "selectedProfitRaw": "0x0",
        "attempts": [],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    assert evidence["status"] == "live_evidence_deferred"
    assert evidence["executionTrust"] == "live_evidence_deferred"
    assert evidence["errorKind"] == "rpc_busy"
    assert evidence["blockers"][0] == result["error"]
    assert evidence["reviewFailureKind"] == "rpc_busy"
    assert attempt["auto_execute"]["skipped"] is True
    assert attempt["auto_execute"]["reason"] == "rpc_busy"


def test_build_native_worker_attempt_preserves_rpc_failover_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    rpc_metadata = {
        "attemptedRpcUrls": ["https://primary.test", "https://backup.test"],
        "selectedRpcUrl": "https://backup.test",
        "rpcFailoverCount": 1,
    }
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "status": "not_profitable_after_costs",
        "error": "previewBestPoolDetailed unavailable",
        "selectedAmountRaw": "0x0",
        "selectedProfitRaw": "0x0",
        "initialAmountRaw": "0xf4240",
        "attempts": [],
        "rpcMetadata": rpc_metadata,
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    assert attempt["rpcMetadata"] == rpc_metadata
    evidence = attempt["quote"]["live_onchain_evidence"]
    assert evidence["rpcMetadata"] == rpc_metadata
    assert evidence["attemptedRpcUrls"] == ["https://primary.test", "https://backup.test"]
    assert evidence["selectedRpcUrl"] == "https://backup.test"
    assert evidence["rpcFailoverCount"] == 1
    assert "previewBestPoolDetailed unavailable" in evidence["blockers"][0]


def test_build_native_worker_attempt_preserves_gas_retry_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "status": "route_error",
        "error": "official_v4:previewArbitrageBestAmount:eth_call:attempt=2: out of gas",
        "errorKind": "gas_estimation_failed",
        "retrySearchMode": "external_ladder",
        "attemptedSearchModes": ["inner_single_call", "external_ladder"],
        "retryError": "official_v4:probeAmount=0x0f4240:official_v4:previewArbitrageBestAmount:eth_call:attempt=1: out of gas",
        "selectedAmountRaw": "0x0",
        "selectedProfitRaw": "0x0",
        "attempts": [],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    evidence = attempt["quote"]["live_onchain_evidence"]
    assert evidence["errorKind"] == "gas_estimation_failed"
    assert evidence["errorLocation"]["probeAmountRaw"] == "0x0f4240"
    assert evidence["attemptedSearchModes"] == ["inner_single_call", "external_ladder"]
    assert evidence["retrySearchMode"] == "external_ladder"
    assert "external_ladder retry also failed" in evidence["blockers"][0]
    assert attempt["auto_execute"]["reason"] == "gas_estimation_failed"


def test_native_worker_result_fingerprint_is_stable_and_distinct() -> None:
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    result_a = {
        "status": "profitable_amount_found",
        "selectedAmountRaw": "0x1",
        "selectedProfitRaw": "0x2",
    }
    result_b = {
        "status": "profitable_amount_found",
        "selectedAmountRaw": "0x1",
        "selectedProfitRaw": "0x3",
    }
    first = rust_closed_loop.native_worker_result_fingerprint(
        group_key="aaa|bbb|ccc",
        updated_at="2026-10-20T00:00:00+00:00",
        result=result_a,
        route=route,
    )
    same = rust_closed_loop.native_worker_result_fingerprint(
        group_key="aaa|bbb|ccc",
        updated_at="2026-10-20T00:00:00+00:00",
        result=result_a,
        route=route,
    )
    different = rust_closed_loop.native_worker_result_fingerprint(
        group_key="aaa|bbb|ccc",
        updated_at="2026-10-20T00:00:00+00:00",
        result=result_b,
        route=route,
    )
    assert first == same
    assert first != different


def test_scanner_records_native_worker_results_once(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.dex_opportunity_scanner import DexOpportunityScanner

    monkeypatch.setenv("DEX_CLOSED_LOOP_NATIVE_RUST", "1")
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    scanner = DexOpportunityScanner(
        quote_payload_provider=lambda **kwargs: {"routes": []},
        auto_execute_enabled=True,
        cache_path=tmp_path / "dex_opportunity_snapshot.json",
        periodic_scan_enabled=False,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    scanner._native_route_templates = {"aaa|bbb|ccc": [route]}
    recorded: list[dict[str, Any]] = []
    scanner.record_attempts = lambda attempts, **kwargs: recorded.extend(attempts) or {
        "recorded": len(attempts),
        "source": "test",
        "error": None,
    }
    monkeypatch.setattr(
        rust_mixin,
        "native_worker_status",
        lambda **kwargs: {
            "workers": [
                {
                    "group_key": "aaa|bbb|ccc",
                    "chain_id": 46630,
                    "state": "done",
                    "updated_at": "2026-10-20T00:00:00+00:00",
                    "last_result": {
                        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
                        "searchMode": "inner_single_call",
                        "status": "profitable_amount_found",
                        "selectedAmountRaw": "0x0f4240",
                        "selectedProfitRaw": "0x0493e0",
                        "initialAmountRaw": "0x0f4240",
                        "attempts": [],
                        "broadcast": {
                            "ok": True,
                            "txHash": "0xabc123",
                            "receipt": {"status": "0x1"},
                        },
                    },
                }
            ]
        },
    )

    first = scanner._record_native_worker_results()
    second = scanner._record_native_worker_results()

    assert int(first.get("recorded") or 0) == 1
    assert len(recorded) == 1
    assert int(second.get("recorded") or 0) == 0
    assert len(recorded) == 1
    assert recorded[0]["auto_execute"]["executed"] is True


def test_scanner_records_broadcast_history_after_last_result_overwritten(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.dex_opportunity_scanner import DexOpportunityScanner

    monkeypatch.setenv("DEX_CLOSED_LOOP_NATIVE_RUST", "1")
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    scanner = DexOpportunityScanner(
        quote_payload_provider=lambda **kwargs: {"routes": []},
        auto_execute_enabled=True,
        cache_path=tmp_path / "dex_opportunity_snapshot.json",
        periodic_scan_enabled=False,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    scanner._native_route_templates = {"aaa|bbb|ccc": [route]}
    recorded: list[dict[str, Any]] = []
    scanner.record_attempts = lambda attempts, **kwargs: recorded.extend(attempts) or {
        "recorded": len(attempts),
        "source": "test",
        "error": None,
    }
    broadcast_result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "profitable_amount_found",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0493e0",
        "initialAmountRaw": "0x0f4240",
        "attempts": [],
        "broadcastExecuted": True,
        "broadcast": {
            "ok": True,
            "txHash": "0xabc456",
            "receipt": {"status": "0x1"},
        },
        "observedAt": "2026-10-20T00:00:01+00:00",
    }
    monkeypatch.setattr(
        rust_mixin,
        "native_worker_status",
        lambda **kwargs: {
            "workers": [
                {
                    "group_key": "aaa|bbb|ccc",
                    "chain_id": 46630,
                    "state": "idle",
                    "updated_at": "2026-10-20T00:01:00+00:00",
                    "last_result": {
                        "status": "not_profitable_after_costs",
                        "selectedAmountRaw": "0x0",
                        "selectedProfitRaw": "0x0",
                        "attempts": [],
                    },
                    "broadcast_history": [broadcast_result],
                }
            ]
        },
    )

    recording = scanner._record_native_worker_results()

    assert int(recording.get("recorded") or 0) == 2
    success = [
        attempt
        for attempt in recorded
        if (attempt.get("auto_execute") or {}).get("executed")
    ]
    assert len(success) == 1
    assert success[0]["tx_hash"] == "0xabc456"
    assert success[0]["state"] == "execution_success"
    assert success[0]["observed_at"] == "2026-10-20T00:00:01+00:00"


def test_scanner_records_broadcast_failure_history_after_last_result_overwritten(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.dex_opportunity_scanner import DexOpportunityScanner

    monkeypatch.setenv("DEX_CLOSED_LOOP_NATIVE_RUST", "1")
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    scanner = DexOpportunityScanner(
        quote_payload_provider=lambda **kwargs: {"routes": []},
        auto_execute_enabled=True,
        cache_path=tmp_path / "dex_opportunity_snapshot.json",
        periodic_scan_enabled=False,
    )
    audit_path = tmp_path / "broadcast_failures.jsonl"
    from core import broadcast_failure_audit as broadcast_failure_audit_module

    monkeypatch.setattr(
        broadcast_failure_audit_module,
        "broadcast_failure_audit_path",
        lambda: audit_path,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    scanner._native_route_templates = {"aaa|bbb|ccc": [route]}
    recorded: list[dict[str, Any]] = []
    scanner.record_attempts = lambda attempts, **kwargs: recorded.extend(attempts) or {
        "recorded": len(attempts),
        "source": "test",
        "error": None,
    }
    failed_payload = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "profitable_amount_found",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0493e0",
        "initialAmountRaw": "0x0f4240",
        "attempts": [],
        "broadcastExecuted": False,
        "broadcastError": "eth_sendRawTransaction RPC request failed",
        "broadcast": {
            "ok": False,
            "attempted": True,
            "failed": True,
            "status": "broadcast_failed",
            "error": "eth_sendRawTransaction RPC request failed",
            "txHash": None,
        },
        "observedAt": "2026-10-20T00:00:01+00:00",
    }
    monkeypatch.setattr(
        rust_mixin,
        "native_worker_status",
        lambda **kwargs: {
            "workers": [
                {
                    "group_key": "aaa|bbb|ccc",
                    "chain_id": 46630,
                    "state": "idle",
                    "updated_at": "2026-10-20T00:01:00+00:00",
                    "last_result": {
                        "status": "not_profitable_after_costs",
                        "selectedAmountRaw": "0x0",
                        "selectedProfitRaw": "0x0",
                        "attempts": [],
                    },
                    "broadcast_failure_history": [failed_payload],
                }
            ]
        },
    )

    recording = scanner._record_native_worker_results()

    assert int(recording.get("recorded") or 0) == 2
    failed = [
        attempt
        for attempt in recorded
        if attempt["state"] == "execution_failed"
    ]
    assert len(failed) == 1
    assert failed[0]["auto_execute"]["reason"] == "broadcast_failed"
    assert failed[0]["auto_execute"]["submitted"] is False
    assert failed[0]["error"] == "eth_sendRawTransaction RPC request failed"
    assert failed[0]["quote"]["live_onchain_evidence"]["costModel"]["netProfitAmount"] == 0.3
    assert scanner._last_recording["autoExecuteFailed"] == 1
    assert audit_path.exists()
    audit_rows = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(audit_rows) == 1
    assert audit_rows[0]["groupKey"] == "aaa|bbb|ccc"
    assert audit_rows[0]["broadcastError"] == "eth_sendRawTransaction RPC request failed"
    assert audit_rows[0]["selectedProfitRaw"] == "0x0493e0"
    assert audit_rows[0]["status"] == "profitable_amount_found"
    assert audit_rows[0]["observedAt"] == "2026-10-20T00:00:01+00:00"


def test_scanner_dedupes_broadcast_history_with_same_tx_hash(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.dex_opportunity_scanner import DexOpportunityScanner

    monkeypatch.setenv("DEX_CLOSED_LOOP_NATIVE_RUST", "1")
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    scanner = DexOpportunityScanner(
        quote_payload_provider=lambda **kwargs: {"routes": []},
        auto_execute_enabled=True,
        cache_path=tmp_path / "dex_opportunity_snapshot.json",
        periodic_scan_enabled=False,
    )
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    scanner._native_route_templates = {"aaa|bbb|ccc": [route]}
    recorded: list[dict[str, Any]] = []
    scanner.record_attempts = lambda attempts, **kwargs: recorded.extend(attempts) or {
        "recorded": len(attempts),
        "source": "test",
        "error": None,
    }
    broadcast_result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "profitable_amount_found",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0493e0",
        "initialAmountRaw": "0x0f4240",
        "attempts": [],
        "broadcastExecuted": True,
        "broadcast": {
            "ok": True,
            "txHash": "0xabc789",
            "receipt": {"status": "0x1"},
        },
        "observedAt": "2026-10-20T00:00:01+00:00",
    }
    monkeypatch.setattr(
        rust_mixin,
        "native_worker_status",
        lambda **kwargs: {
            "workers": [
                {
                    "group_key": "aaa|bbb|ccc",
                    "chain_id": 46630,
                    "state": "idle",
                    "updated_at": "2026-10-20T00:00:02+00:00",
                    "last_result": broadcast_result,
                    "broadcast_history": [broadcast_result],
                }
            ]
        },
    )

    scanner._record_native_worker_results()

    success = [
        attempt
        for attempt in recorded
        if (attempt.get("auto_execute") or {}).get("executed")
    ]
    assert len(success) == 1
    assert success[0]["tx_hash"] == "0xabc789"
    assert success[0]["state"] == "execution_success"


def test_start_and_stop_closed_loop_group_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    from dex.runtime import rust_closed_loop

    calls: list[tuple[str, str, bool | None]] = []

    class _FakePool:
        def start_group(self, group_key: str) -> dict[str, Any]:
            calls.append(("start", group_key, None))
            return {"accepted": True, "group_key": group_key, "action": "start"}

        def stop_group(self, group_key: str) -> dict[str, Any]:
            calls.append(("stop", group_key, None))
            return {"accepted": True, "group_key": group_key, "action": "stop"}

        def wake_waiting_for_review(
            self,
            *,
            group_key: str,
            wait_until: str | None,
            dry_run: bool,
        ) -> dict[str, Any]:
            calls.append(("review", group_key, dry_run))
            return {
                "accepted": True,
                "group_key": group_key,
                "waitUntil": wait_until,
                "transitionQueued": not dry_run,
                "dryRun": dry_run,
            }

        def wake_waiting_for_broadcast(
            self,
            *,
            group_key: str,
            wait_until: str | None,
            dry_run: bool,
        ) -> dict[str, Any]:
            calls.append(("broadcast", group_key, dry_run))
            return {
                "accepted": True,
                "group_key": group_key,
                "waitUntil": wait_until,
                "state": "waiting" if dry_run else "queued",
                "dryRun": dry_run,
            }

    monkeypatch.setattr(rust_closed_loop, "get_native_pool", lambda **kwargs: _FakePool())

    started = rust_closed_loop.start_closed_loop_group("  AAA|BBB|CCC  ")
    stopped = rust_closed_loop.stop_closed_loop_group("AAA|BBB|CCC")
    review = rust_closed_loop.wake_waiting_closed_loop_group("AAA|BBB|CCC")
    broadcast = rust_closed_loop.wake_waiting_broadcast(
        "AAA|BBB|CCC",
        dry_run=True,
    )

    assert started["accepted"] is True
    assert stopped["accepted"] is True
    assert review["transitionQueued"] is True
    assert broadcast["state"] == "waiting"
    assert calls == [
        ("start", "aaa|bbb|ccc", None),
        ("stop", "aaa|bbb|ccc", None),
        ("review", "aaa|bbb|ccc", False),
        ("broadcast", "aaa|bbb|ccc", True),
    ]


def test_merge_group_execution_only_attaches_broadcast_to_selected_direction() -> None:
    scanner = object.__new__(rust_mixin.DexOpportunityNativeRustMixin)
    group_result = {
        "status": "profitable_amount_found",
        "broadcast": {"ok": True, "txHash": "0xabc"},
        "broadcastError": "broadcast_config_missing",
        "selectedAmountRaw": "0x10",
        "selectedProfitRaw": "0x02",
    }
    selected_route = {"status": "profitable_amount_found", "directionSelected": True}
    opposite_route = {"status": "not_profitable_after_costs"}

    selected_merged = scanner._merge_group_execution_into_route_result(
        route_result=selected_route,
        group_result=group_result,
        selected=True,
    )
    opposite_merged = scanner._merge_group_execution_into_route_result(
        route_result=opposite_route,
        group_result=group_result,
        selected=False,
    )

    assert selected_merged["broadcast"]["txHash"] == "0xabc"
    assert selected_merged["broadcastError"] == "broadcast_config_missing"
    assert selected_merged["selectedAmountRaw"] == "0x10"
    assert "broadcast" not in opposite_merged
    assert opposite_merged.get("selectedAmountRaw") is None


def test_merge_group_execution_preserves_execution_queue_fields_when_selected() -> None:
    scanner = object.__new__(rust_mixin.DexOpportunityNativeRustMixin)
    group_result = {
        "status": "profitable_amount_found",
        "executionQueued": True,
        "executionQueueAt": "2026-10-20T00:01:00+00:00",
        "selectedAmountRaw": "0x10",
        "selectedProfitRaw": "0x02",
    }
    selected_route = {"status": "profitable_amount_found", "directionSelected": True}

    selected_merged = scanner._merge_group_execution_into_route_result(
        route_result=selected_route,
        group_result=group_result,
        selected=True,
    )

    assert selected_merged["executionQueued"] is True
    assert selected_merged["executionQueueAt"] == "2026-10-20T00:01:00+00:00"
    assert selected_merged["selectedAmountRaw"] == "0x10"


def test_merge_group_execution_marks_unselected_broadcast_direction() -> None:
    scanner = object.__new__(rust_mixin.DexOpportunityNativeRustMixin)
    group_result = {
        "status": "profitable_amount_found",
        "broadcast": {"ok": True, "txHash": "0xabc"},
        "selectedAmountRaw": "0x10",
        "selectedProfitRaw": "0x02",
        "selectedRoutePayload": {
            "routeSymbols": ["AAA", "BBB", "CCC", "AAA"],
            "routeAddresses": ["0x1", "0x2", "0x3", "0x1"],
        },
    }
    opposite_route = {"status": "profitable_amount_found"}

    opposite_merged = scanner._merge_group_execution_into_route_result(
        route_result=opposite_route,
        group_result=group_result,
        selected=False,
    )

    assert opposite_merged["broadcastNotSelected"] is True
    assert "broadcast" not in opposite_merged
    assert opposite_merged["selectedRouteSymbols"] == [
        "AAA",
        "BBB",
        "CCC",
        "AAA",
    ]


def test_build_native_worker_attempt_marks_unselected_direction_not_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    monkeypatch.setattr(
        rust_closed_loop,
        "broadcast_gate_status",
        lambda *, chain_id: {
            "autoExecuteEnabled": True,
            "broadcastAllowed": True,
            "broadcastEnabled": True,
            "privateKeyConfigured": True,
            "privateKeySource": "LIQUIDATION_EXECUTION_PRIVATE_KEY",
            "reasons": [],
        },
    )
    route = _route(["AAA", "CCC", "BBB", "AAA"])
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "profitable_amount_found",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0493e0",
        "initialAmountRaw": "0x0f4240",
        "broadcastNotSelected": True,
        "selectedRouteSymbols": ["AAA", "BBB", "CCC", "AAA"],
        "runArbitrage": {
            "ok": True,
            "profit": "0x493e0",
        },
        "attempts": [
            {
                "amountRaw": "0x0f4240",
                "profitRaw": "0x0493e0",
                "ok": True,
                "profitable": True,
                "routeIndex": "0x0",
                "attemptedCount": "0x1",
            }
        ],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-20T00:00:00+00:00",
    )

    assert attempt is not None
    auto_execute = attempt["auto_execute"]
    assert auto_execute["skipped"] is True
    assert auto_execute["reason"] == "broadcast_not_selected"
    assert auto_execute.get("broadcastResultMissing") is not True
    assert attempt["broadcastNotSelected"] is True
    assert attempt["auto_execute_requested"] is False


def test_scanner_rust_refresh_feeds_group_without_python_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dex.runtime.dex_opportunity_scanner_engine import DexOpportunityScanner

    submitted: list[dict[str, Any]] = []
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    other_route = _route(["XXX", "YYY", "ZZZ", "XXX"])
    import dex.runtime.dex_opportunity_route_runner as route_runner

    monkeypatch.setattr(
        rust_mixin.DexOpportunityNativeRustMixin,
        "_submit_native_closed_loop_groups",
        lambda self, **kwargs: submitted.append(kwargs) or None,
    )
    monkeypatch.setattr(
        rust_mixin,
        "native_worker_status",
        lambda **kwargs: {
            "workers": [
                {
                    "group_key": "aaa|bbb|ccc",
                    "state": "idle",
                    "chain_id": 46630,
                    "last_result": {"status": "not_profitable_after_costs"},
                }
            ]
        },
    )
    scanner = object.__new__(DexOpportunityScanner)

    monkeypatch.setattr(
        route_runner,
        "_quote_routes_payload_from_facade",
        lambda **_kwargs: {"routes": [route, other_route]},
    )

    result = rust_mixin.DexOpportunityNativeRustMixin._refresh_native_closed_loop_group(
        scanner,
        group_key="AAA|BBB|CCC",
        chain_id=46630,
        protocols=["uniswap_v4"],
    )

    assert result["ok"] is True
    assert result["mode"] == "native_closed_loop_group_refresh"
    assert submitted[0]["payload"]["routes"] == [route]


def test_scanner_rust_refresh_rejects_busy_group_without_resubmitting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dex.runtime.dex_opportunity_scanner_engine import DexOpportunityScanner

    called = []
    monkeypatch.setattr(
        rust_mixin.DexOpportunityNativeRustMixin,
        "_submit_native_closed_loop_groups",
        lambda self, **kwargs: called.append(kwargs) or None,
    )
    monkeypatch.setattr(
        rust_mixin,
        "native_worker_status",
        lambda **kwargs: {
            "workers": [
                {
                    "group_key": "aaa|bbb|ccc",
                    "state": "running",
                    "chain_id": 46630,
                }
            ]
        },
    )
    scanner = object.__new__(DexOpportunityScanner)

    result = rust_mixin.DexOpportunityNativeRustMixin._refresh_native_closed_loop_group(
        scanner,
        group_key="AAA|BBB|CCC",
        chain_id=46630,
        protocols=["uniswap_v4"],
    )

    assert result["error"] == "closed_loop_group_busy"
    assert result["ok"] is False
    assert called == []


def test_manual_native_review_feeds_exact_idle_route_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dex.runtime.dex_opportunity_scanner_engine import DexOpportunityScanner

    woken: list[tuple[str, bool]] = []
    route = _route(["AAA", "BBB", "CCC", "AAA"])
    monkeypatch.setattr(
        rust_mixin.DexOpportunityNativeRustMixin,
        "_submit_native_closed_loop_groups",
        lambda self, **kwargs: (_ for _ in ()).throw(
            AssertionError("manual review must not resubmit routes")
        ),
    )
    monkeypatch.setattr(
        rust_closed_loop,
        "wake_waiting_closed_loop_group",
        lambda group_key, **kwargs: woken.append((group_key, kwargs.get("dry_run", False)))
        or {
            "accepted": True,
            "group_key": group_key,
            "transitionQueued": True,
            "dryRun": bool(kwargs.get("dry_run")),
        },
    )
    scanner = object.__new__(DexOpportunityScanner)

    result = rust_mixin.DexOpportunityNativeRustMixin._manual_native_closed_loop_review(
        scanner,
        route_symbols=["AAA", "BBB", "CCC", "AAA"],
        route_addresses=route.get("routeAddresses"),
        chain_id=46630,
        protocols=["uniswap_v4"],
    )

    assert result["ok"] is True
    assert result["group_key"] == "aaa|bbb|ccc"
    assert result["processingAsync"] is True
    assert result["stateReadRequired"] is True
    assert woken == [("aaa|bbb|ccc", False)]


def test_manual_native_review_rejects_busy_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dex.runtime.dex_opportunity_scanner_engine import DexOpportunityScanner

    called = []
    monkeypatch.setattr(
        rust_mixin.DexOpportunityNativeRustMixin,
        "_submit_native_closed_loop_groups",
        lambda self, **kwargs: called.append(kwargs) or None,
    )
    monkeypatch.setattr(
        rust_closed_loop,
        "wake_waiting_closed_loop_group",
        lambda _group_key, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("closed_loop_group_busy:aaa|bbb|ccc:running")
        ),
    )
    monkeypatch.setattr(
        rust_mixin.DexOpportunityNativeRustMixin,
        "_native_database_url",
        lambda self, chain_id=None: None,
    )
    scanner = object.__new__(DexOpportunityScanner)

    result = rust_mixin.DexOpportunityNativeRustMixin._manual_native_closed_loop_review(
        scanner,
        route_symbols=["AAA", "BBB", "CCC", "AAA"],
        route_addresses=_route(["AAA", "BBB", "CCC", "AAA"]).get("routeAddresses"),
        chain_id=46630,
        protocols=["uniswap_v4"],
    )

    assert result["error"] == "closed_loop_group_busy"
    assert result["ok"] is False
    assert called == []


def test_broadcast_gate_status_reports_missing_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    import market.binance_market.dex_route_test as route_test

    monkeypatch.delenv("DEX_AUTO_EXECUTE_VERIFIED_OPPORTUNITIES", raising=False)
    monkeypatch.delenv("ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST", raising=False)
    monkeypatch.delenv("LIQUIDATION_EXECUTION_PRIVATE_KEY", raising=False)
    monkeypatch.setattr(
        route_test,
        "_official_v4_signer_for_chain",
        lambda chain_id: (None, None),
    )

    gate = rust_closed_loop.broadcast_gate_status(chain_id=46630)

    assert gate["broadcastEnabled"] is False
    assert gate["autoExecuteEnabled"] is False
    assert gate["broadcastAllowed"] is False
    assert gate["privateKeyConfigured"] is False
    assert "DEX_AUTO_EXECUTE_VERIFIED_OPPORTUNITIES is not true" in gate["reasons"]
    assert "ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST is not true" in gate["reasons"]
    assert "no official V4 broadcast signer key is configured" in gate["reasons"]


def test_broadcast_gate_status_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import market.binance_market.dex_route_test as route_test

    monkeypatch.setenv("DEX_AUTO_EXECUTE_VERIFIED_OPPORTUNITIES", "true")
    monkeypatch.setenv("ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST", "true")
    monkeypatch.setattr(
        route_test,
        "_official_v4_signer_for_chain",
        lambda chain_id: ("0x" + "1" * 64, "TEST_KEY"),
    )

    gate = rust_closed_loop.broadcast_gate_status(chain_id=46630)

    assert gate["broadcastEnabled"] is True
    assert gate["broadcastAllowed"] is True
    assert gate["privateKeyConfigured"] is True
    assert gate["privateKeySource"] == "TEST_KEY"
    assert gate["reasons"] == []


def test_rust_worker_summary_carries_broadcast_failure_recent() -> None:
    from web.data_routes.dex_surface_routes_ops import _summarize_rust_workers

    payload = {
        "engine": "rust_native_closed_loop",
        "workers": [
            {
                "group_key": "aaa|bbb|ccc",
                "state": "idle",
                "probe_count": 3,
                "broadcast_count": 1,
                "broadcast_failure_count": 2,
                "last_broadcast_failure_at": "2026-10-26T01:00:00+00:00",
                "last_result": {
                    "status": "profitable_amount_found",
                    "broadcast": {"txHash": "0x" + "a" * 64},
                },
                "broadcast_history": [],
                "broadcast_failure_history": [],
                "broadcast_failure_recent": [
                    {
                        "observedAt": "2026-10-26T01:00:00+00:00",
                        "error": "eth_sendRawTransaction RPC request failed",
                        "profitable": True,
                        "selectedProfitRaw": "0x0493e0",
                    }
                ],
            }
        ],
    }

    summarized = _summarize_rust_workers(payload)
    worker = summarized["workers"][0]

    assert worker["broadcast_failure_count"] == 2
    assert worker["last_broadcast_failure_at"] == "2026-10-26T01:00:00+00:00"
    assert worker["broadcast_failure_recent"][0]["error"] == "eth_sendRawTransaction RPC request failed"
    assert worker["broadcast_failure_recent"][0]["selectedProfitRaw"] == "0x0493e0"
    assert worker["tx_hash"] == "0x" + "a" * 64
    assert worker["broadcast"] is True
    assert "last_result" not in worker
    assert "broadcast_history" not in worker
    assert "broadcast_failure_history" not in worker
