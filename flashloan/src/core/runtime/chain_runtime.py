"""Thin Python adapter for the Rust chain RuntimeCore.

The authoritative per-chain RuntimeCore, Manager DTO cache and consistency
state live in ``flashloan_rs``.  ``ChainRuntimeContext`` only resolves the
chain identity and forwards PyO3 calls for Flask/page integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import threading
from typing import Any


class DatabaseScopeRequired(RuntimeError):
    """Raised when a chain-aware operation cannot resolve a chain scope."""


def _chain_database_path(chain_id: int) -> str:
    """Resolve the canonical SQLite path for a chain or fail loudly."""
    from core.db.chain_database_context import (
        chain_database_path,
        chain_databases_enabled,
    )
    from dex.runtime.dex_opportunity_helpers import _network_for_chain_id

    chain_id = int(chain_id)
    if not chain_databases_enabled():
        raise DatabaseScopeRequired(
            "chain_databases_required: FLASHLOAN_CHAIN_DATABASES must be enabled"
        )
    path = chain_database_path(chain_id, _network_for_chain_id(chain_id))
    return str(Path(path).resolve())


def _native_pool_for_chain(chain_id: int) -> Any:
    from dex.runtime.rust_closed_loop import pool_for_chain

    return pool_for_chain(chain_id)


def _sync_rust_runtime(runtime: "ChainRuntimeContext", *, active: bool) -> dict[str, Any]:
    from dex.runtime.rust_closed_loop import (
        clear_rust_active_runtime,
        sync_rust_active_runtime,
    )

    if not active:
        return clear_rust_active_runtime()
    return sync_rust_active_runtime(
        chain_id=runtime.chain_id,
        network=runtime.network,
        database_path=runtime.database_url,
    )


def _rust_runtime_core_module() -> Any | None:
    from core.rust_extension import load_rust_extension

    return load_rust_extension()


def _register_rust_runtime_core(
    *,
    chain_id: int,
    network: str,
    protocol: str,
    database_path: str,
) -> dict[str, Any]:
    candidate_authority = (
        str(os.getenv("CANDIDATE_MANAGER_AUTHORITY") or "legacy").strip().lower()
        or "legacy"
    )
    broadcast_authority = (
        str(os.getenv("BROADCAST_MANAGER_AUTHORITY") or "legacy").strip().lower()
        or "legacy"
    )
    candidate_exchange_authority = (
        str(os.getenv("CANDIDATE_EXCHANGE_MANAGER_AUTHORITY") or "legacy")
        .strip()
        .lower()
        or "legacy"
    )
    module = _rust_runtime_core_module()
    if module is None or not hasattr(module, "runtime_core_register"):
        return {
            "available": False,
            "registered": False,
            "reason": "rust_runtime_core_unavailable",
            "dataSource": "rust_runtime_core",
        }
    if hasattr(module, "runtime_core_status"):
        try:
            existing = module.runtime_core_status(int(chain_id))
            if (
                isinstance(existing, dict)
                and existing.get("databasePath") == str(database_path)
                and existing.get("broadcastAuthority") == broadcast_authority
                and existing.get("candidateAuthority") == candidate_authority
                and existing.get("candidateExchangeAuthority")
                == candidate_exchange_authority
            ):
                return {
                    "available": True,
                    "registered": True,
                    "reused": True,
                    **dict(existing),
                }
            if isinstance(existing, dict) and hasattr(module, "runtime_core_close"):
                try:
                    module.runtime_core_close(int(chain_id))
                except Exception:
                    pass
        except Exception:
            pass
    try:
        payload = module.runtime_core_register(
            int(chain_id),
            str(network or ""),
            str(database_path or ""),
            str(protocol or "uniswap_v4"),
            broadcast_authority,
            candidate_authority,
            candidate_exchange_authority,
        )
        result = dict(payload) if isinstance(payload, dict) else {}
        return {
            "available": True,
            "registered": True,
            **result,
        }
    except Exception as exc:
        return {
            "available": True,
            "registered": False,
            "error": str(exc)[:500],
            "dataSource": "rust_runtime_core",
        }


def request_chain_database_url(database_url: str | None = None) -> str | None:
    """Resolve the current page database from the bound runtime object.

    The Flask ``g.chain_runtime`` is the single request-scoped source.  The
    explicit ``database_url`` argument and legacy fallback are only for
    non-page background callers and tests.
    """
    if database_url:
        return str(database_url)
    try:
        from flask import g

        runtime = getattr(g, "chain_runtime", None)
        if runtime is not None:
            return str(runtime.database_url)
        page_context = getattr(g, "page_context", None)
        if page_context is not None:
            source = page_context.to_dict().get("sourceDatabase")
            if source:
                return str(source)
    except Exception:
        pass
    try:
        from core.runtime.chain_runtime import chain_runtime_registry

        return str(chain_runtime_registry().current().database_url)
    except Exception:
        return database_url


def _scanner_facade_for_chain(chain_id: int) -> Any:
    from dex.runtime.dex_opportunity_scanner import get_dex_opportunity_scanner

    return get_dex_opportunity_scanner(chain_id=chain_id)


@dataclass
class ChainRuntimeIdentity:
    chain_id: int
    network: str
    market_id: str
    protocol: str = "uniswap_v4"


@dataclass
class PageContext:
    """Read-only page snapshot bound to one chain runtime."""

    runtime: "ChainRuntimeContext"
    chain: dict[str, Any] = field(default_factory=dict)
    market: dict[str, Any] = field(default_factory=dict)
    scanner: dict[str, Any] = field(default_factory=dict)
    closed_loops: dict[str, Any] = field(default_factory=dict)
    broadcast: dict[str, Any] = field(default_factory=dict)
    manager_snapshot: dict[str, Any] = field(default_factory=dict)
    manager_snapshot_error: str | None = None
    permissions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime": {
                "chainId": self.runtime.identity.chain_id,
                "network": self.runtime.identity.network,
                "marketId": self.runtime.identity.market_id,
                "protocol": self.runtime.identity.protocol,
                "databaseUrl": self.runtime.database_url,
                "rustRuntimeConfig": self.runtime.rust_runtime_config(),
                "runtimeCore": self.runtime.runtime_core_status(),
            },
            "chain": self.chain,
            "market": self.market,
            "scanner": self.scanner,
            "closedLoops": self.closed_loops,
            "broadcast": self.broadcast,
            "managerSnapshot": self.manager_snapshot,
            "managerSnapshotError": self.manager_snapshot_error,
            "permissions": self.permissions,
            "sourceDatabase": self.runtime.database_url,
        }


class ChainRuntimeContext:
    """One chain's complete in-memory runtime.

    The object is the only runtime entrypoint for this chain.  It holds stable
    lazy facades so callers never resolve chain configuration themselves.
    """

    def __init__(
        self,
        *,
        chain_id: int,
        network: str,
        protocol: str = "uniswap_v4",
        database_url: str | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self.chain_id = int(chain_id)
        self.network = str(network or "")
        self.protocol = str(protocol or "uniswap_v4")
        self.market_id = f"dex-arbitrage-{self.chain_id}-{self.protocol}"
        self.identity = ChainRuntimeIdentity(
            chain_id=self.chain_id,
            network=self.network,
            market_id=self.market_id,
            protocol=self.protocol,
        )
        self.database_url = str(database_url or _chain_database_path(self.chain_id))
        self._scanner: Any | None = None
        self._maintenance: Any | None = None
        self._closed_loops: Any | None = None
        self._broadcast: Any | None = None
        self._closed = False
        self._runtime_core_registration = _register_rust_runtime_core(
            chain_id=self.chain_id,
            network=self.network,
            protocol=self.protocol,
            database_path=self.database_url,
        )

    @property
    def native_pool(self) -> Any:
        with self._lock:
            if self._closed:
                raise RuntimeError(f"chain runtime closed: chain_id={self.chain_id}")
            return _native_pool_for_chain(self.chain_id)

    @property
    def scanner(self) -> Any:
        with self._lock:
            if self._closed:
                raise RuntimeError(f"chain runtime closed: chain_id={self.chain_id}")
            if self._scanner is None:
                self._scanner = _scanner_facade_for_chain(self.chain_id)
            return self._scanner

    @property
    def maintenance(self) -> Any:
        with self._lock:
            if self._closed:
                raise RuntimeError(f"chain runtime closed: chain_id={self.chain_id}")
            if self._maintenance is None:
                from dex.runtime.v4_maintenance_workers import get_v4_maintenance_manager

                self._maintenance = get_v4_maintenance_manager(
                    database_url=self.database_url,
                    network=self.network,
                    chain_id=self.chain_id,
                )
            return self._maintenance

    @property
    def closed_loop_state_machine(self) -> Any:
        with self._lock:
            if self._closed:
                raise RuntimeError(f"chain runtime closed: chain_id={self.chain_id}")
            if self._closed_loops is None:
                from dex.runtime.closed_loop_object import ClosedLoopStateMachine

                self._closed_loops = ClosedLoopStateMachine(database_url=self.database_url)
            return self._closed_loops

    @property
    def broadcast_gate(self) -> dict[str, Any]:
        from dex.runtime.rust_closed_loop import broadcast_gate_status

        return broadcast_gate_status(chain_id=self.chain_id)

    def closed_loop_control_status(self) -> dict[str, Any]:
        """Return review/broadcast worker state owned by the native manager."""
        from dex.runtime.rust_closed_loop import native_worker_status

        return native_worker_status(
            include_payloads=False,
            include_workers=False,
            database_url=self.database_url,
            chain_id=self.chain_id,
        )

    def wake_waiting_review(
        self,
        group_key: str,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Transition one idle review worker from waiting to reviewing."""
        from dex.runtime.rust_closed_loop import wake_waiting_closed_loop_group

        return wake_waiting_closed_loop_group(
            group_key,
            database_url=self.database_url,
            dry_run=dry_run,
        )

    def wake_waiting_broadcast(
        self,
        group_key: str,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Transition one waiting execution task from waiting to broadcasting."""
        from dex.runtime.rust_closed_loop import wake_waiting_broadcast

        return wake_waiting_broadcast(
            group_key,
            database_url=self.database_url,
            dry_run=dry_run,
        )

    def page_context(self) -> PageContext:
        """Build a page snapshot through the Rust RuntimeCore boundary."""
        broadcast = self.broadcast_gate
        manager_snapshot: dict[str, Any] = {}
        manager_snapshot_error: str | None = None
        try:
            manager_snapshot = self.manager_snapshot(refresh=True)
        except Exception as exc:
            manager_snapshot_error = str(exc)[:500]
        components = manager_snapshot.get("components", {})
        manager_broadcast = (
            components.get("broadcast", {})
            if isinstance(components, dict)
            else {}
        )
        broadcast.update(manager_broadcast)
        context = PageContext(
            runtime=self,
            chain={
                "chainId": self.chain_id,
                "network": self.network,
                "marketId": self.market_id,
                "protocol": self.protocol,
                "databaseUrl": self.database_url,
                "databaseResolved": True,
            },
            market={
                "chainId": self.chain_id,
                "network": self.network,
                "marketId": self.market_id,
                "protocol": self.protocol,
                "active": True,
            },
            scanner={
                "dataSource": manager_snapshot.get(
                    "dataSource",
                    "rust_runtime_core",
                ),
                "candidateExchange": components.get("candidateExchange", {}),
            },
            closed_loops={
                "dataSource": manager_snapshot.get(
                    "dataSource",
                    "rust_runtime_core",
                ),
                "closedLoop": components.get("closedLoop", {}),
            },
            broadcast=broadcast,
            manager_snapshot=manager_snapshot,
            manager_snapshot_error=manager_snapshot_error,
            permissions={
                "readOnlyUi": True,
                "broadcastEnabled": bool(broadcast.get("broadcastEnabled")),
                "requiresLiveReview": not bool(broadcast.get("autoExecuteEnabled")),
            },
        )
        return context

    def rust_runtime_config(self) -> dict[str, Any]:
        try:
            from dex.runtime.rust_closed_loop import rust_active_runtime_status

            return rust_active_runtime_status()
        except Exception as exc:
            return {
                "available": False,
                "active": False,
                "error": str(exc)[:200],
            }

    def runtime_core_status(self) -> dict[str, Any]:
        """Return Rust RuntimeCore lifecycle state without database access."""
        module = _rust_runtime_core_module()
        if module is None or not hasattr(module, "runtime_core_status"):
            return {
                **self._runtime_core_registration,
                "available": False,
                "reason": "rust_runtime_core_unavailable",
                "dataSource": "rust_runtime_core",
            }
        try:
            payload = module.runtime_core_status(self.chain_id)
            result = dict(payload) if isinstance(payload, dict) else {}
            return {
                "available": True,
                "registered": bool(result.get("instanceId")),
                **result,
            }
        except Exception as exc:
            return {
                "available": True,
                "registered": False,
                "error": str(exc)[:500],
                "registration": self._runtime_core_registration,
                "dataSource": "rust_runtime_core",
            }

    def manager_snapshot(self, *, refresh: bool = False) -> dict[str, Any]:
        """Return the latest Rust Manager snapshot or refresh it on demand."""
        with self._lock:
            if self._closed:
                raise RuntimeError(f"chain runtime closed: chain_id={self.chain_id}")
        module = _rust_runtime_core_module()
        if module is None or not hasattr(module, "runtime_core_manager_snapshot"):
            raise RuntimeError("rust_runtime_core_unavailable")
        payload = module.runtime_core_manager_snapshot(
            self.chain_id,
            bool(refresh),
        )
        if not isinstance(payload, dict):
            raise RuntimeError("rust_runtime_core returned a non-object snapshot")
        return dict(payload)

    def manager_view(
        self,
        view: str,
        limit: int = 1000,
        *,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a read-only Manager view computed by Rust RuntimeCore."""
        with self._lock:
            if self._closed:
                raise RuntimeError(f"chain runtime closed: chain_id={self.chain_id}")
        module = _rust_runtime_core_module()
        if module is None or not hasattr(module, "runtime_core_manager_view"):
            raise RuntimeError("rust_runtime_core_manager_view_unavailable")
        arguments = [self.chain_id, str(view or "").strip(), int(limit)]
        if options:
            arguments.append(
                json.dumps(
                    options,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        payload = module.runtime_core_manager_view(*arguments)
        if not isinstance(payload, dict):
            raise RuntimeError("rust_runtime_core returned a non-object manager view")
        return dict(payload)

    def execute_command(
        self,
        command: str,
        *,
        request_id: str,
        idempotency_key: str,
        dry_run: bool,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute one explicit Rust RuntimeCore command."""
        with self._lock:
            if self._closed:
                raise RuntimeError(f"chain runtime closed: chain_id={self.chain_id}")
        module = _rust_runtime_core_module()
        if module is None or not hasattr(module, "runtime_core_command"):
            raise RuntimeError("rust_runtime_core_command_unavailable")
        arguments: list[Any] = [
            self.chain_id,
            str(command or "").strip(),
            str(request_id or "").strip(),
            str(idempotency_key or "").strip(),
            bool(dry_run),
        ]
        if options is not None:
            if not isinstance(options, dict):
                raise ValueError("RuntimeCore command options must be an object")
            arguments.append(
                json.dumps(
                    options,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        payload = module.runtime_core_command(*arguments)
        if not isinstance(payload, dict):
            raise RuntimeError("rust_runtime_core returned a non-object command result")
        return dict(payload)

    def status(self) -> dict[str, Any]:
        """Return one chain's runtime status without page-level extra fields."""
        broadcast = self.broadcast_gate
        runtime_core = self.runtime_core_status()
        return {
            "chainId": self.chain_id,
            "network": self.network,
            "marketId": self.market_id,
            "protocol": self.protocol,
            "databasePath": self.database_url,
            "databaseResolved": True,
            "rustRuntimeConfig": self.rust_runtime_config(),
            "runtimeCore": runtime_core,
            "broadcastGate": broadcast,
            "nativeEnabled": bool(runtime_core.get("available")),
            "executionBackend": "rust_runtime_core",
            "dataSource": "rust_runtime_core",
        }

    def close(self) -> dict[str, Any]:
        """Stop the chain's runtime-owned resources.

        ``close`` only affects this chain; the registry does not stop other
        registered runtimes.  It returns after best-effort cleanup.
        """
        with self._lock:
            self._closed = True
            errors: list[str] = []
            try:
                from dex.runtime.rust_closed_loop import stop_native_pool_for_chain

                stop_native_pool_for_chain(
                    chain_id=self.chain_id,
                    database_path=self.database_url,
                )
            except Exception as exc:
                errors.append(str(exc)[:200])
            try:
                module = _rust_runtime_core_module()
                if module is not None and hasattr(module, "runtime_core_close"):
                    module.runtime_core_close(self.chain_id)
            except Exception as exc:
                errors.append(str(exc)[:200])
            return {
                "ok": not errors,
                "chainId": self.chain_id,
                "errors": errors,
            }

    def __repr__(self) -> str:
        return (
            f"ChainRuntimeContext(chain_id={self.chain_id}, "
            f"network={self.network!r}, database_url={self.database_url!r})"
        )


class ChainRuntimeRegistry:
    """Global registry owning all chain runtime contexts."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._runtimes: dict[int, ChainRuntimeContext] = {}
        self._active_chain_id: int | None = None

    def register_chain(
        self,
        chain_id: int,
        *,
        network: str | None = None,
        protocol: str = "uniswap_v4",
        database_url: str | None = None,
        make_active: bool = False,
    ) -> ChainRuntimeContext:
        chain_id = int(chain_id)
        with self._lock:
            runtime = self._runtimes.get(chain_id)
            if runtime is not None:
                requested_database = (
                    str(Path(database_url).expanduser().resolve())
                    if database_url
                    else runtime.database_url
                )
                requested_network = str(network or runtime.network)
                requested_protocol = str(protocol or runtime.protocol)
                if (
                    requested_database != runtime.database_url
                    or requested_network != runtime.network
                    or requested_protocol != runtime.protocol
                ):
                    raise ValueError(
                        "chain runtime identity already registered with different scope: "
                        f"chain_id={chain_id}"
                    )
                return runtime
            if runtime is None:
                from dex.runtime.dex_opportunity_helpers import _network_for_chain_id

                resolved_network = str(network or _network_for_chain_id(chain_id))
                runtime = ChainRuntimeContext(
                    chain_id=chain_id,
                    network=resolved_network,
                    protocol=protocol,
                    database_url=database_url,
                )
                self._runtimes[chain_id] = runtime
            if make_active or self._active_chain_id is None:
                self._active_chain_id = chain_id
                _sync_rust_runtime(runtime, active=True)
            return runtime

    def get(self, chain_id: int | None = None) -> ChainRuntimeContext:
        if chain_id is None:
            return self.current()
        chain_id = int(chain_id)
        with self._lock:
            runtime = self._runtimes.get(chain_id)
        if runtime is None:
            raise KeyError(f"chain not registered: chain_id={chain_id}")
        return runtime

    def current(self) -> ChainRuntimeContext:
        with self._lock:
            active = self._active_chain_id
        if active is None:
            raise DatabaseScopeRequired("database_scope_required: no active chain runtime")
        return self.get(active)

    def switch_chain(self, chain_id: int) -> ChainRuntimeContext:
        """Atomically switch active runtime after target is ready."""
        target = self.register_chain(int(chain_id))
        with self._lock:
            self._active_chain_id = int(chain_id)
        _sync_rust_runtime(target, active=True)
        return target

    def active_chain_id(self) -> int | None:
        with self._lock:
            return self._active_chain_id

    def registered_chain_ids(self) -> list[int]:
        with self._lock:
            return sorted(self._runtimes)

    def snapshot(self) -> dict[str, Any]:
        active = self.active_chain_id()
        with self._lock:
            ids = sorted(self._runtimes)
        return {
            "activeChainId": active,
            "registeredChainIds": ids,
            "count": len(ids),
        }

    def close(self, chain_id: int | None = None) -> dict[str, Any]:
        """Close one chain or all runtimes.  Registry remains usable."""
        results: dict[str, list[Any]] = {"closed": [], "errors": []}
        with self._lock:
            targets = [int(chain_id)] if chain_id is not None else list(self._runtimes)
            for target in targets:
                runtime = self._runtimes.get(target)
                if runtime is None:
                    continue
                outcome = runtime.close()
                results["closed"].append(target)
                if not outcome.get("ok"):
                    results["errors"].append(outcome)
                self._runtimes.pop(target, None)
                if self._active_chain_id == target:
                    self._active_chain_id = None
                    _sync_rust_runtime(runtime, active=False)
        return results

    def reset(self) -> dict[str, Any]:
        return self.close()


def _default_chain_id() -> int | None:
    raw = str(os.getenv("DEX_ACTIVE_CHAIN_ID", "") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


_REGISTRY_LOCK = threading.Lock()
_REGISTRY: ChainRuntimeRegistry | None = None


def chain_runtime_registry() -> ChainRuntimeRegistry:
    """Return the process-global chain runtime registry."""
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            _REGISTRY = ChainRuntimeRegistry()
            default_chain = _default_chain_id()
            if default_chain is not None:
                try:
                    _REGISTRY.register_chain(default_chain, make_active=True)
                except (DatabaseScopeRequired, ImportError, KeyError, OSError):
                    # The registry remains valid but inactive; production
                    # startup registers the explicit active chain.
                    pass
        return _REGISTRY


def reset_chain_runtime_registry() -> None:
    """Reset the global registry; intended for tests and explicit shutdown."""
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is not None:
            _REGISTRY.reset()
        _REGISTRY = None


__all__ = [
    "ChainRuntimeContext",
    "ChainRuntimeRegistry",
    "DatabaseScopeRequired",
    "PageContext",
    "chain_runtime_registry",
    "request_chain_database_url",
    "reset_chain_runtime_registry",
]
