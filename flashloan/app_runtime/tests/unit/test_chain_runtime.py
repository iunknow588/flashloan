from __future__ import annotations

import sys
from typing import Any

import pytest

from core.runtime import chain_runtime as cr


def _fake_scanner_factory(registry: dict[int, Any]) -> Any:
    def factory(chain_id: int) -> Any:
        if chain_id not in registry:
            registry[chain_id] = object()
        return registry[chain_id]

    return factory


def test_registry_switches_complete_runtime_without_destroying_other_chain(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        cr,
        "_chain_database_path",
        lambda chain_id: str(tmp_path / f"chain_{int(chain_id)}.sqlite3"),
    )
    registry = cr.ChainRuntimeRegistry()
    testnet = registry.register_chain(
        46630,
        network="robinhood-testnet",
        make_active=True,
    )
    mainnet = registry.register_chain(
        4663,
        network="robinhood",
        make_active=False,
    )

    assert registry.active_chain_id() == 46630
    assert testnet.database_url.endswith("chain_46630.sqlite3")
    assert mainnet.database_url.endswith("chain_4663.sqlite3")

    switched = registry.switch_chain(4663)
    assert switched is mainnet
    assert registry.current() is mainnet
    # Switching the active chain must not evict or stop the other runtime.
    assert registry.get(46630) is testnet


def test_switch_failure_keeps_previous_active_runtime(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        cr,
        "_chain_database_path",
        lambda chain_id: str(tmp_path / f"chain_{int(chain_id)}.sqlite3"),
    )
    registry = cr.ChainRuntimeRegistry()
    testnet = registry.register_chain(
        46630,
        network="robinhood-testnet",
        make_active=True,
    )

    def fail_for_missing(chain_id: int) -> str:
        raise cr.DatabaseScopeRequired(f"missing chain: {chain_id}")

    monkeypatch.setattr(cr, "_chain_database_path", fail_for_missing)
    with pytest.raises(cr.DatabaseScopeRequired):
        registry.switch_chain(4663)

    assert registry.active_chain_id() == 46630
    assert registry.current() is testnet


def test_registry_rejects_conflicting_identity_for_same_chain(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        cr,
        "_chain_database_path",
        lambda chain_id: str(tmp_path / f"chain_{int(chain_id)}.sqlite3"),
    )
    registry = cr.ChainRuntimeRegistry()
    runtime = registry.register_chain(
        4663,
        network="robinhood",
        database_url=str(tmp_path / "mainnet.sqlite3"),
        make_active=True,
    )

    with pytest.raises(ValueError, match="different scope"):
        registry.register_chain(
            4663,
            network="robinhood",
            database_url=str(tmp_path / "other.sqlite3"),
        )

    assert registry.get(4663) is runtime


def test_runtime_owns_chain_bound_facades(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        cr,
        "_chain_database_path",
        lambda chain_id: str(tmp_path / f"chain_{int(chain_id)}.sqlite3"),
    )
    pools: dict[int, Any] = {}
    pool_calls: list[int] = []
    scanners: dict[int, Any] = {}

    def pool_factory(chain_id: int) -> Any:
        pool_calls.append(chain_id)
        if chain_id not in pools:
            pools[chain_id] = object()
        return pools[chain_id]

    monkeypatch.setattr(cr, "_native_pool_for_chain", pool_factory)
    monkeypatch.setattr(
        cr,
        "_scanner_facade_for_chain",
        _fake_scanner_factory(scanners),
    )

    runtime = cr.ChainRuntimeContext(
        chain_id=46630,
        network="robinhood-testnet",
    )

    assert runtime.native_pool is pools[46630]
    assert runtime.native_pool is pools[46630]
    assert pool_calls == [46630, 46630]
    assert "_pool" not in runtime.__dict__
    assert runtime.scanner is scanners[46630]
    assert runtime.closed_loop_state_machine.database_url == runtime.database_url
    context = runtime.page_context()
    assert context.runtime is runtime
    assert context.chain["chainId"] == 46630
    assert context.broadcast["broadcastEnabled"] is False


def test_native_pool_registry_keeps_multiple_databases_isolated(monkeypatch) -> None:
    from dex.runtime import rust_closed_loop

    class FakePool:
        def __init__(self) -> None:
            self.stopped = False

        def stop(self) -> None:
            self.stopped = True

    pools: list[FakePool] = []

    class FakeModule:
        @staticmethod
        def ClosedLoopWorkerPool(**kwargs: Any) -> FakePool:
            pool = FakePool()
            pools.append(pool)
            return pool

    monkeypatch.setitem(sys.modules, "flashloan_rs", FakeModule())
    monkeypatch.setattr(
        rust_closed_loop,
        "_database_path",
        lambda: "sqlite:///default.sqlite3",
    )
    monkeypatch.setattr(rust_closed_loop, "_POOLS_BY_DATABASE", {})
    monkeypatch.setattr(rust_closed_loop, "_POOLS_BY_CHAIN", {})

    first = rust_closed_loop.get_native_pool(database_url="a.sqlite3")
    same = rust_closed_loop.get_native_pool(database_url="a.sqlite3")
    second = rust_closed_loop.get_native_pool(database_url="b.sqlite3")

    assert first is same
    assert second is not first
    assert len(pools) == 2
    rust_closed_loop.stop_native_pool()
    assert pools[0].stopped is True
    assert pools[1].stopped is True


def test_rust_runtime_config_sync_and_resolve(tmp_path, monkeypatch) -> None:
    from dex.runtime import rust_closed_loop

    captured: list[dict[str, Any]] = []

    class FakeRustModule:
        @staticmethod
        def set_active_runtime(chain_id, network, database_path) -> dict:
            captured.append(
                {
                    "chain_id": chain_id,
                    "network": network,
                    "database_path": database_path,
                }
            )
            return {
                "ok": True,
                "active": True,
                "chainId": chain_id,
                "network": network,
                "databasePath": database_path,
            }

    monkeypatch.setattr(rust_closed_loop, "_rust_runtime_module", lambda: FakeRustModule())

    registry = cr.ChainRuntimeRegistry()
    runtime = registry.register_chain(
        4663,
        network="robinhood",
        database_url=str(tmp_path / "mainnet.sqlite3"),
        make_active=True,
    )
    monkeypatch.setattr(cr, "chain_runtime_registry", lambda: registry)

    assert captured[-1]["chain_id"] == 4663
    assert captured[-1]["database_path"] == str(tmp_path / "mainnet.sqlite3")

    resolved = rust_closed_loop.resolve_runtime(4663)
    assert resolved["source"] == "active_runtime"
    assert resolved["databasePath"] == runtime.database_url

    explicit = rust_closed_loop.resolve_runtime(
        4663,
        database_url=str(tmp_path / "explicit.sqlite3"),
    )
    assert explicit["source"] == "explicit"
    assert explicit["databasePath"] == str(tmp_path / "explicit.sqlite3")


def test_resolve_runtime_requires_scope(monkeypatch) -> None:
    from dex.runtime import rust_closed_loop

    class FakeRegistry:
        def current(self) -> Any:
            raise cr.DatabaseScopeRequired("no active chain runtime")

        def get(self, chain_id: int) -> Any:
            raise cr.DatabaseScopeRequired("chain not registered")

    monkeypatch.setattr(cr, "chain_runtime_registry", lambda: FakeRegistry())

    with pytest.raises(RuntimeError, match="database_scope_required"):
        rust_closed_loop.resolve_runtime()


def test_request_chain_database_url_falls_back_to_active_registry(monkeypatch) -> None:
    class FakeRuntime:
        database_url = "/active/mainnet.sqlite3"

    class FakeRegistry:
        def current(self) -> Any:
            return FakeRuntime()

    monkeypatch.setattr(cr, "chain_runtime_registry", lambda: FakeRegistry())
    assert cr.request_chain_database_url() == "/active/mainnet.sqlite3"


def test_chain_runtime_is_a_thin_rust_runtime_core_adapter(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str, tuple[Any, ...]]] = []
    monkeypatch.delenv("BROADCAST_MANAGER_AUTHORITY", raising=False)
    monkeypatch.setenv("CANDIDATE_MANAGER_AUTHORITY", "manager")
    monkeypatch.setenv("CANDIDATE_EXCHANGE_MANAGER_AUTHORITY", "manager")

    class FakeRuntimeCore:
        def __init__(self) -> None:
            self.registered = False

        def runtime_core_register(
            self,
            chain_id: int,
            network: str,
            database_path: str,
            protocol: str,
            broadcast_authority: str,
            candidate_authority: str,
            candidate_exchange_authority: str,
        ) -> dict[str, Any]:
            calls.append(
                (
                    "register",
                    (
                        chain_id,
                        network,
                        database_path,
                        protocol,
                        broadcast_authority,
                        candidate_authority,
                        candidate_exchange_authority,
                    ),
                )
            )
            self.registered = True
            return {
                "instanceId": "runtime-core-1",
                "chainId": chain_id,
                "state": "registered",
                "broadcastAuthority": broadcast_authority,
                "candidateAuthority": candidate_authority,
                "candidateExchangeAuthority": candidate_exchange_authority,
                "dataSource": "rust_runtime_core",
            }

        def runtime_core_status(self, chain_id: int) -> dict[str, Any]:
            if not self.registered:
                raise RuntimeError("runtime core not registered")
            calls.append(("status", (chain_id,)))
            return {
                "instanceId": "runtime-core-1",
                "chainId": chain_id,
                "state": "partial",
                "snapshotAvailable": True,
                "broadcastAuthority": "legacy",
                "candidateAuthority": "manager",
                "candidateExchangeAuthority": "manager",
                "dataSource": "rust_runtime_core",
            }

        def runtime_core_manager_snapshot(
            self,
            chain_id: int,
            refresh: bool,
        ) -> dict[str, Any]:
            calls.append(("snapshot", (chain_id, refresh)))
            return {
                "chainId": chain_id,
                "generation": 7,
                "dataSource": "rust_runtime_core",
                "components": {
                    "candidateExchange": {"observationVersion": 11},
                    "closedLoop": {"closedLoopVersion": 13},
                    "broadcast": {"broadcastVersion": 17, "pendingCount": 0},
                },
            }

        def runtime_core_manager_view(
            self,
            chain_id: int,
            view: str,
            limit: int,
            options_json: str | None = None,
        ) -> dict[str, Any]:
            calls.append(("manager_view", (chain_id, view, limit, options_json)))
            return {
                "ok": True,
                "view": view,
                "dataSource": "rust_runtime_core",
                "runtimeCore": {"chainId": chain_id, "generation": 7},
                "data": {"chainId": chain_id, "summary": {"arbitragePathCount": 3}},
            }

        def runtime_core_command(
            self,
            chain_id: int,
            command: str,
            request_id: str,
            idempotency_key: str,
            dry_run: bool,
            options_json: str | None = None,
        ) -> dict[str, Any]:
            arguments = [chain_id, command, request_id, idempotency_key, dry_run]
            if options_json is not None:
                arguments.append(options_json)
            calls.append(
                (
                    "command",
                    tuple(arguments),
                )
            )
            return {
                "ok": True,
                "accepted": True,
                "command": command,
                "chainId": chain_id,
                "dataSource": "rust_runtime_core",
                "dryRun": dry_run,
                "clearedAttempts": 2,
                "remainingAttempts": 0,
                "idempotentReplay": False,
            }

        def runtime_core_close(self, chain_id: int) -> dict[str, Any]:
            calls.append(("close", (chain_id,)))
            return {"ok": True, "chainId": chain_id, "closed": True}

    monkeypatch.setattr(cr, "_chain_database_path", lambda chain_id: str(tmp_path / "db.sqlite3"))
    fake_runtime_core = FakeRuntimeCore()
    monkeypatch.setattr(cr, "_rust_runtime_core_module", lambda: fake_runtime_core)

    runtime = cr.ChainRuntimeContext(
        chain_id=4663,
        network="robinhood",
    )
    assert calls[0] == (
        "register",
        (
            4663,
            "robinhood",
            str(tmp_path / "db.sqlite3"),
            "uniswap_v4",
            "legacy",
            "manager",
            "manager",
        ),
    )
    assert runtime.runtime_core_status()["state"] == "partial"
    assert "_manager_snapshot" not in runtime.__dict__

    snapshot = runtime.manager_snapshot()
    assert snapshot["generation"] == 7
    assert calls[-1] == ("snapshot", (4663, False))

    overview = runtime.manager_view("overview", 100)
    assert overview["dataSource"] == "rust_runtime_core"
    assert overview["data"]["summary"]["arbitragePathCount"] == 3
    assert calls[-1] == ("manager_view", (4663, "overview", 100, None))

    reviews = runtime.manager_view("reviews", 20, options={"offset": 40})
    assert reviews["view"] == "reviews"
    assert calls[-1] == (
        "manager_view",
        (4663, "reviews", 20, '{"offset":40}'),
    )

    command = runtime.execute_command(
        "clear_broadcasts",
        request_id="clear-request-1",
        idempotency_key="clear-idem-1",
        dry_run=False,
    )
    assert command["accepted"] is True
    assert command["clearedAttempts"] == 2
    assert calls[-1] == (
        "command",
        (
            4663,
            "clear_broadcasts",
            "clear-request-1",
            "clear-idem-1",
            False,
        ),
    )

    review = runtime.execute_command(
        "manual_review",
        request_id="review-request-1",
        idempotency_key="review-idem-1",
        dry_run=True,
        options={"groupKey": "aaa|bbb|ccc"},
    )
    assert review["accepted"] is True
    assert calls[-1] == (
        "command",
        (
            4663,
            "manual_review",
            "review-request-1",
            "review-idem-1",
            True,
            '{"groupKey":"aaa|bbb|ccc"}',
        ),
    )

    context = runtime.page_context()
    assert context.scanner["candidateExchange"]["observationVersion"] == 11
    assert context.closed_loops["closedLoop"]["closedLoopVersion"] == 13
    assert context.broadcast["broadcastVersion"] == 17
    assert context.manager_snapshot_error is None
    assert calls[-1] == ("snapshot", (4663, True))

    closed = runtime.close()
    assert closed["ok"] is True
    assert calls[-1] == ("close", (4663,))
