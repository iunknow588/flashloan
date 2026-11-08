"""Rust execution component bridge for the closed-loop pipeline.

Python only builds the per-YZ task config, submits it to the Rust pool, and
reads ``status_json()``/results back. All on-chain preview, adaptive amount
ladder, and broadcast logic stays inside ``flashloan_rs``.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import sqlite3
import threading
from typing import Any


_POOL_LOCK = threading.Lock()
# Legacy fallback only. The native Rust registry owns pool lifecycle when the
# current flashloan_rs extension exposes native_pool_get_or_create.
_POOLS_BY_DATABASE: dict[str, Any] = {}
_POOLS_BY_CHAIN: dict[int, str] = {}
# Compatibility aliases retained for callers that inspect the old globals.
_POOL: Any = None
_POOL_DATABASE_PATH: str | None = None

_GAS_ESTIMATION_FAILURE_MARKERS = (
    "out of gas",
    "outofgas",
    "gas required exceeds",
    "intrinsic gas too low",
    "gas estimation",
)

_RPC_FAILURE_STATUSES = {
    "rpc_verification_failed",
    "rpc_verification_timeout",
    "error",
    "live_quote_failed",
    "route_error",
    "rpc_unavailable",
}

_RPC_BUSY_MARKERS = (
    "network is busy",
    "please try again in a moment",
    "too many requests",
    "rate limit",
    "rate limited",
    "request rate",
    "429",
    "service unavailable",
    "temporarily unavailable",
    "overloaded",
    "server busy",
    "busy, please",
)

_OFFICIAL_V4_CALL_SITES = (
    "previewBestPool",
    "previewArbitrageBestAmount",
    "runArbitrageBestPoolsWithSelectedAmount",
    "previewBestPoolDetailed",
)


def _rust_runtime_module() -> Any:
    """Return the Rust extension module, or None when unavailable."""
    try:
        import flashloan_rs  # type: ignore

        return flashloan_rs
    except Exception:
        return None


def sync_rust_active_runtime(
    *,
    chain_id: int,
    network: str,
    database_path: str,
) -> dict[str, Any]:
    """Push the Python chain-runtime active scope into the Rust global config."""
    module = _rust_runtime_module()
    if module is None or not hasattr(module, "set_active_runtime"):
        return {
            "available": False,
            "synced": False,
            "reason": "rust_runtime_config_unavailable",
        }
    try:
        payload = module.set_active_runtime(
            int(chain_id),
            str(network or ""),
            str(database_path or ""),
        )
        result = dict(payload) if isinstance(payload, dict) else {}
        return {
            "available": True,
            "synced": True,
            **result,
        }
    except Exception as exc:
        return {
            "available": True,
            "synced": False,
            "error": str(exc)[:200],
        }


def clear_rust_active_runtime() -> dict[str, Any]:
    """Clear the Rust global active runtime (best-effort)."""
    module = _rust_runtime_module()
    if module is None or not hasattr(module, "clear_active_runtime"):
        return {
            "available": False,
            "cleared": False,
            "reason": "rust_runtime_config_unavailable",
        }
    try:
        payload = module.clear_active_runtime()
        result = dict(payload) if isinstance(payload, dict) else {}
        return {"available": True, "cleared": True, **result}
    except Exception as exc:
        return {
            "available": True,
            "cleared": False,
            "error": str(exc)[:200],
        }


def rust_active_runtime_status() -> dict[str, Any]:
    """Return the Rust global runtime config snapshot."""
    module = _rust_runtime_module()
    if module is None or not hasattr(module, "active_runtime"):
        return {
            "available": False,
            "active": False,
            "reason": "rust_runtime_config_unavailable",
        }
    try:
        payload = module.active_runtime()
        result = dict(payload) if isinstance(payload, dict) else {}
        return {"available": True, **result}
    except Exception as exc:
        return {
            "available": True,
            "active": False,
            "error": str(exc)[:200],
        }


def resolve_runtime(
    chain_id: int | None = None,
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    """Resolve the canonical database path for one explicit or active chain.

    ``database_url`` is the explicit override.  Otherwise the chain runtime
    registry is the only source; missing scope raises instead of silently
    falling back to a non-chain database.
    """
    if database_url:
        from pathlib import Path

        return {
            "ok": True,
            "chainId": chain_id,
            "databasePath": str(Path(database_url).expanduser().resolve()),
            "source": "explicit",
        }
    from core.runtime.chain_runtime import (
        DatabaseScopeRequired,
        chain_runtime_registry,
    )

    try:
        runtime = (
            chain_runtime_registry().get(int(chain_id))
            if chain_id is not None
            else chain_runtime_registry().current()
        )
    except (DatabaseScopeRequired, KeyError) as exc:
        raise RuntimeError(f"database_scope_required: {exc}") from exc
    return {
        "ok": True,
        "chainId": runtime.chain_id,
        "network": runtime.network,
        "databasePath": runtime.database_url,
        "source": "active_runtime",
    }


def _broadcast_receipt(broadcast: dict[str, Any]) -> dict[str, Any]:
    """Return the normalized execution receipt from a native broadcast result.

    The Rust result can carry the receipt either at ``broadcast.receipt`` or at
    ``broadcast.receipt.receipt`` depending on the source snapshot.
    """
    if not isinstance(broadcast, dict):
        return {}
    receipt = broadcast.get("receipt")
    if isinstance(receipt, dict) and isinstance(receipt.get("receipt"), dict):
        receipt = receipt["receipt"]
    if not isinstance(receipt, dict):
        return {}
    return receipt


def _gas_metric(raw: Any) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    try:
        return int(text, 0)
    except (TypeError, ValueError):
        return None


def rust_execution_component_enabled() -> bool:
    """Return whether the Rust execution component is available and active."""
    raw = str(os.getenv("DEX_CLOSED_LOOP_NATIVE_RUST", "") or "").strip().lower()
    if raw:
        return raw in {"1", "true", "yes", "on"}
    from dex.runtime.dex_opportunity_auto_execute import auto_execute_env_enabled

    try:
        import flashloan_rs  # noqa: F401
    except Exception:
        return False
    return auto_execute_env_enabled()


def native_closed_loop_enabled() -> bool:
    """Deprecated compatibility alias for ``rust_execution_component_enabled``."""
    return rust_execution_component_enabled()


def _database_path() -> str:
    from core.local_kv import store_path

    return str(store_path())


def _default_pool_database_url() -> str:
    """Resolve the default Rust pool database without switching chains.

    With per-chain databases enabled the active chain runtime is mandatory;
    without it this raises instead of silently reusing the legacy default
    store.  Non-chain/legacy callers keep the old default path.
    """
    from core.db.chain_database_context import chain_databases_enabled

    if chain_databases_enabled():
        try:
            from core.runtime.chain_runtime import (
                DatabaseScopeRequired,
                chain_runtime_registry,
            )

            return chain_runtime_registry().current().database_url
        except Exception as exc:
            if isinstance(exc, DatabaseScopeRequired):
                raise
            raise RuntimeError(
                f"database_scope_required: {exc}"
            ) from exc
    return _database_path()


def database_path_for_chain(chain_id: int | None) -> str | None:
    """Resolve the Rust pool database without relying on thread-local scope."""
    if chain_id is None:
        return None
    try:
        from core.runtime.chain_runtime import chain_runtime_registry

        return chain_runtime_registry().get(int(chain_id)).database_url
    except Exception:
        pass
    try:
        from core.db.chain_database_context import chain_database_path, chain_databases_enabled
        if chain_databases_enabled():
            from dex.runtime.dex_opportunity_helpers import _network_for_chain_id
            return str(chain_database_path(int(chain_id), _network_for_chain_id(int(chain_id))).resolve())
    except Exception:
        return None
    return None


def execution_queue_env_enabled() -> bool:
    raw = str(os.getenv("DEX_CLOSED_LOOP_EXECUTION_QUEUE_ENABLED", "") or "").strip().lower()
    if raw:
        return raw in {"1", "true", "yes", "on"}
    return True


def execution_workers_env() -> int:
    default = 2
    try:
        return max(1, min(8, int(os.getenv("DEX_CLOSED_LOOP_EXECUTION_WORKERS", str(default)) or str(default))))
    except (TypeError, ValueError):
        return default


def get_native_pool(
    max_workers: int | None = None,
    *,
    chain_id: int | None = None,
    network: str | None = None,
    protocol: str | None = None,
    database_url: str | None = None,
    maintenance_handler: Any | None = None,
    maintenance_interval_seconds: int = 600,
    binance_interval_seconds: int = 300,
    binance_enabled: bool = False,
    execution_workers: int | None = None,
    execution_queue_enabled: bool | None = None,
) -> Any:
    global _POOL, _POOL_DATABASE_PATH
    with _POOL_LOCK:
        resolved_database_url = database_url or _default_pool_database_url()
        requested_path = os.path.normcase(os.path.abspath(str(resolved_database_url)))
        native_pool = _native_pool_get_or_create(
            database_path=requested_path,
            chain_id=chain_id,
            network=network,
            protocol=protocol,
            max_workers=max_workers,
            maintenance_handler=maintenance_handler,
            maintenance_interval_seconds=maintenance_interval_seconds,
            binance_interval_seconds=binance_interval_seconds,
            binance_enabled=binance_enabled,
            execution_workers=execution_workers,
            execution_queue_enabled=execution_queue_enabled,
        )
        if native_pool is not None:
            _POOL = native_pool
            _POOL_DATABASE_PATH = requested_path
            return native_pool

        pool = _POOLS_BY_DATABASE.get(requested_path)
        if pool is None:
            if max_workers is None:
                try:
                    max_workers = max(
                        1,
                        min(
                            32,
                            int(
                                os.getenv(
                                    "DEX_CLOSED_LOOP_MAX_WORKERS",
                                    os.getenv(
                                        "DEX_CLOSED_LOOP_WORKERS",
                                        os.getenv("DEX_CLOSED_LOOP_MAX_CONCURRENCY", "16"),
                                    ),
                                )
                                or "16"
                            ),
                        ),
                    )
                except (TypeError, ValueError):
                    max_workers = 16
            resolved_handler = maintenance_handler
            if resolved_handler is None:
                from dex.runtime.robinhood_v4_idle_maintenance import run_idle_maintenance

                resolved_handler = run_idle_maintenance
            resolved_execution_workers = execution_workers
            if resolved_execution_workers is None:
                resolved_execution_workers = execution_workers_env()
            resolved_execution_queue_enabled = (
                execution_queue_env_enabled()
                if execution_queue_enabled is None
                else bool(execution_queue_enabled)
            )
            import flashloan_rs

            pool = flashloan_rs.ClosedLoopWorkerPool(
                max_workers=max(1, min(32, int(max_workers))),
                database_path=requested_path,
                maintenance_handler=resolved_handler,
                maintenance_interval_seconds=max(1, int(maintenance_interval_seconds)),
                binance_interval_seconds=max(60, int(binance_interval_seconds)),
                binance_enabled=bool(binance_enabled),
                execution_workers=max(1, min(8, int(resolved_execution_workers))),
                execution_queue_enabled=bool(resolved_execution_queue_enabled),
            )
            _POOLS_BY_DATABASE[requested_path] = pool
        _POOL = pool
        _POOL_DATABASE_PATH = requested_path
        return pool


def _native_pool_get_or_create(
    *,
    database_path: str,
    chain_id: int | None,
    network: str | None,
    protocol: str | None,
    max_workers: int | None,
    maintenance_handler: Any | None,
    maintenance_interval_seconds: int,
    binance_interval_seconds: int,
    binance_enabled: bool,
    execution_workers: int | None,
    execution_queue_enabled: bool | None,
) -> Any | None:
    """Return a Rust-registry pool, or None when the extension predates it."""
    module = _rust_runtime_module()
    if module is None or not hasattr(module, "native_pool_get_or_create"):
        return None
    if max_workers is None:
        max_workers = max(
            1,
            min(
                32,
                int(
                    os.getenv(
                        "DEX_CLOSED_LOOP_MAX_WORKERS",
                        os.getenv(
                            "DEX_CLOSED_LOOP_WORKERS",
                            os.getenv("DEX_CLOSED_LOOP_MAX_CONCURRENCY", "16"),
                        ),
                    )
                    or "16"
                ),
            ),
        )
    resolved_handler = maintenance_handler
    if resolved_handler is None:
        from dex.runtime.robinhood_v4_idle_maintenance import run_idle_maintenance

        resolved_handler = run_idle_maintenance
    resolved_execution_workers = execution_workers
    if resolved_execution_workers is None:
        resolved_execution_workers = execution_workers_env()
    resolved_execution_queue_enabled = (
        execution_queue_env_enabled()
        if execution_queue_enabled is None
        else bool(execution_queue_enabled)
    )
    return module.native_pool_get_or_create(
        database_path=database_path,
        chain_id=int(chain_id) if chain_id is not None else None,
        network=str(network) if network else None,
        protocol=str(protocol or "uniswap_v4"),
        max_workers=max(1, min(32, int(max_workers))),
        maintenance_handler=resolved_handler,
        maintenance_interval_seconds=max(1, int(maintenance_interval_seconds)),
        binance_interval_seconds=max(60, int(binance_interval_seconds)),
        binance_enabled=bool(binance_enabled),
        execution_workers=max(1, min(8, int(resolved_execution_workers))),
        execution_queue_enabled=bool(resolved_execution_queue_enabled),
    )


def pool_for_chain(chain_id: int) -> Any:
    """Return the Rust pool owned by ``chain_id`` without changing another chain."""
    resolved = database_path_for_chain(int(chain_id))
    if not resolved:
        raise RuntimeError(f"database_scope_required: chain_id={int(chain_id)}")
    requested_path = os.path.normcase(os.path.abspath(str(resolved)))
    module = _rust_runtime_module()
    if module is not None and hasattr(module, "native_pool_get_or_create"):
        return get_native_pool(
            chain_id=int(chain_id),
            network=_network_hint_for_chain(int(chain_id)),
            database_url=requested_path,
        )
    with _POOL_LOCK:
        _POOLS_BY_CHAIN[int(chain_id)] = requested_path
    return get_native_pool(database_url=requested_path)


def _network_hint_for_chain(chain_id: int) -> str:
    try:
        from dex.runtime.dex_opportunity_helpers import _network_for_chain_id

        return _network_for_chain_id(int(chain_id))
    except Exception:
        return ""


def reset_native_pool() -> None:
    global _POOL, _POOL_DATABASE_PATH
    with _POOL_LOCK:
        _native_pool_registry_manage(stop=True)
        for pool in list(_POOLS_BY_DATABASE.values()):
            try:
                pool.stop()
            except Exception:
                pass
        _POOLS_BY_DATABASE.clear()
        _POOLS_BY_CHAIN.clear()
        _POOL = None
        _POOL_DATABASE_PATH = None


def stop_native_pool() -> None:
    global _POOL, _POOL_DATABASE_PATH
    with _POOL_LOCK:
        _native_pool_registry_manage(stop=True)
        for pool in list(_POOLS_BY_DATABASE.values()):
            try:
                pool.stop()
            except Exception:
                pass
        _POOLS_BY_DATABASE.clear()
        _POOLS_BY_CHAIN.clear()
        _POOL = None
        _POOL_DATABASE_PATH = None


def _native_pool_registry_manage(
    *,
    stop: bool,
    chain_id: int | None = None,
    database_path: str | None = None,
) -> dict[str, Any]:
    module = _rust_runtime_module()
    if module is None or not hasattr(module, "native_pool_registry_manage"):
        return {
            "available": False,
            "stopped": False,
            "reason": "native_pool_registry_unavailable",
        }
    kwargs: dict[str, Any] = {"stop": bool(stop)}
    if chain_id is not None:
        kwargs["chain_id"] = int(chain_id)
    if database_path is not None:
        kwargs["database_path"] = str(database_path)
    payload = module.native_pool_registry_manage(**kwargs)
    if not isinstance(payload, dict):
        return {
            "available": True,
            "stopped": False,
            "reason": "native_pool_registry_returned_non_object",
        }
    return {
        "available": True,
        **dict(payload),
    }


def stop_native_pool_for_chain(
    *,
    chain_id: int,
    database_path: str | None = None,
) -> dict[str, Any]:
    """Stop only one chain's pool; Rust registry owns native pool lifecycle."""
    global _POOL, _POOL_DATABASE_PATH
    chain_id = int(chain_id)
    requested_path = (
        os.path.normcase(os.path.abspath(str(database_path)))
        if database_path
        else None
    )
    with _POOL_LOCK:
        native = _native_pool_registry_manage(
            stop=True,
            chain_id=chain_id,
            database_path=requested_path,
        )
        legacy_paths: set[str] = set()
        if requested_path:
            legacy_paths.add(requested_path)
        for existing_chain in list(_POOLS_BY_CHAIN):
            if existing_chain == chain_id:
                path = _POOLS_BY_CHAIN.pop(existing_chain)
                if path:
                    legacy_paths.add(path)
        errors: list[str] = []
        stopped_paths: list[str] = []
        for path in sorted(legacy_paths):
            pool = _POOLS_BY_DATABASE.pop(path, None)
            if pool is None:
                continue
            try:
                pool.stop()
            except Exception as exc:
                errors.append(str(exc)[:200])
            stopped_paths.append(path)
        if _POOL_DATABASE_PATH in legacy_paths:
            _POOL = None
            _POOL_DATABASE_PATH = None
    native_ok = (
        not bool(native.get("available", False))
        or bool(native.get("ok", True))
    )
    return {
        "ok": native_ok and not errors,
        "chainId": chain_id,
        "native": native,
        "legacyStopped": stopped_paths,
        "errors": errors,
    }


def start_closed_loop_group(group_key: str, *, database_url: str | None = None) -> dict[str, Any]:
    pool = get_native_pool(database_url=database_url)
    normalized = str(group_key or "").strip().lower()
    payload = pool.start_group(group_key=normalized)
    return payload if isinstance(payload, dict) else {"ok": bool(payload)}


def stop_closed_loop_group(group_key: str, *, database_url: str | None = None) -> dict[str, Any]:
    pool = get_native_pool(database_url=database_url)
    normalized = str(group_key or "").strip().lower()
    payload = pool.stop_group(group_key=normalized)
    return payload if isinstance(payload, dict) else {"ok": bool(payload)}


def wake_waiting_closed_loop_group(
    group_key: str,
    *,
    database_url: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Move an idle review worker from waiting to its review handler.

    The worker keeps its last submitted review task. This call only clears the
    wait deadline; it must never rebuild or resubmit route business data.
    """
    pool = get_native_pool(database_url=database_url)
    normalized = str(group_key or "").strip().lower()
    waker = getattr(pool, "wake_waiting_for_review", None)
    if waker is None:
        raise RuntimeError("native review state transition is unavailable")
    payload = waker(
        group_key=normalized,
        wait_until=None,
        dry_run=bool(dry_run),
    )
    return payload if isinstance(payload, dict) else {"accepted": bool(payload)}


def wake_waiting_broadcast(
    group_key: str,
    *,
    database_url: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Move one waiting execution task to the broadcast queue.

    The existing reviewed task is the only payload consumed by the broadcast
    worker. This transition does not re-run review or rebuild signed PnL.
    """
    pool = get_native_pool(database_url=database_url)
    normalized = str(group_key or "").strip().lower()
    waker = getattr(pool, "wake_waiting_for_broadcast", None)
    if waker is None:
        raise RuntimeError("native broadcast state transition is unavailable")
    payload = waker(
        group_key=normalized,
        wait_until=None,
        dry_run=bool(dry_run),
    )
    return payload if isinstance(payload, dict) else {"accepted": bool(payload)}


def register_maintenance_scope(
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocol: str | None = None,
) -> dict[str, Any]:
    """Register a chain scope so idle maintenance runs even without workers."""
    if not native_closed_loop_enabled():
        return {"enabled": False, "accepted": False}
    pool = get_native_pool(database_url=database_path_for_chain(chain_id))
    payload = pool.register_maintenance_scope(
        network=network,
        chain_id=chain_id,
        protocol=protocol,
    )
    if not isinstance(payload, dict):
        payload = {"accepted": bool(payload)}
    payload["enabled"] = True
    return payload


def native_worker_status(
    *,
    include_payloads: bool = True,
    include_workers: bool = True,
    database_url: str | None = None,
    chain_id: int | None = None,
) -> dict[str, Any]:
    if not native_closed_loop_enabled():
        return {
            "enabled": False,
            "engine": "python_fallback",
            "executionBackend": "python",
            "workers": [],
            "summary": {},
        }
    try:
        pool = pool_for_chain(chain_id) if chain_id is not None else get_native_pool(database_url=database_url)
        if not include_workers:
            summary_getter = getattr(pool, "summary_status_json", None)
            payload = summary_getter() if summary_getter is not None else pool.compact_status_json()
        elif include_payloads:
            payload = pool.status_json()
        else:
            compact_getter = getattr(pool, "compact_status_json", None)
            payload = compact_getter() if compact_getter is not None else pool.status_json()
        status_database = getattr(pool, "database_path", None) or database_url or None
    except Exception as exc:
        return {
            "enabled": True,
            "engine": "rust_native_closed_loop",
            "executionBackend": "rust",
            "error": str(exc)[:300],
            "workers": [],
            "summary": {},
        }
    if not isinstance(payload, dict):
        payload = {}
    payload["enabled"] = True
    payload["executionBackend"] = "rust"
    if include_workers:
        _enrich_native_worker_direction_results(payload)
    for worker in payload.get("workers") or []:
        if not isinstance(worker, dict):
            continue
        try:
            chain_id = int(worker.get("chain_id") or 0)
        except (TypeError, ValueError):
            continue
        if chain_id:
            worker["broadcastGate"] = broadcast_gate_status(chain_id=chain_id)
    try:
        from dex.runtime.v4_maintenance_workers import v4_maintenance_worker_status

        payload["maintenanceWorkers"] = v4_maintenance_worker_status(
            database_url=status_database,
            include_details=include_payloads,
        )
    except Exception as exc:
        payload["maintenanceWorkers"] = {
            "started": False,
            "error": str(exc)[:200],
        }
    return payload


def _enrich_native_worker_direction_results(payload: dict[str, Any]) -> None:
    """Add human-readable per-direction review values for the read-only UI."""
    for worker in payload.get("workers") or []:
        if not isinstance(worker, dict):
            continue
        chain_id = int(worker.get("chain_id") or 0)
        updated_at = worker.get("updated_at")
        for item in worker.get("direction_results") or []:
            if not isinstance(item, dict):
                continue
            route_payload = item.get("route") if isinstance(item.get("route"), dict) else item
            route_symbols = [
                str(value).strip().upper()
                for value in (item.get("routeSymbols") or route_payload.get("routeSymbols") or [])
                if str(value).strip()
            ]
            route_addresses = [
                str(value).strip().lower()
                for value in (item.get("routeAddresses") or route_payload.get("routeAddresses") or [])
                if str(value).strip()
            ]
            borrow_symbol = route_symbols[0] if route_symbols else None
            borrow_address = route_addresses[0] if route_addresses else None
            item["netProfitAmount"] = _signed_native_profit_value(
                route_payload,
                chain_id,
                borrow_address or "",
            )
            item["netProfitAmountSymbol"] = borrow_symbol
            item["referenceRateProductDelta"] = _native_rate_product_delta(route_payload)
            item["lastReviewAt"] = item.get("checkedAt") or updated_at
            broadcast = item.get("broadcast") if isinstance(item.get("broadcast"), dict) else {}
            if broadcast:
                item["lastTradeAt"] = (
                    broadcast.get("submittedAt")
                    or broadcast.get("submitted_at")
                    or broadcast.get("createdAt")
                    or broadcast.get("observedAt")
                    or broadcast.get("txTime")
                    or updated_at
                )
                item["lastTradeTxHash"] = (
                    broadcast.get("txHash")
                    or broadcast.get("transactionHash")
                    or broadcast.get("transaction_hash")
                    or ""
                )
            else:
                item["lastTradeAt"] = None
                item["lastTradeTxHash"] = None


def _decimals_for_borrow(chain_id: int, borrow_address: str) -> int | None:
    from market.binance_market.dex_route_test import _borrow_token_decimals_from_registry

    return _borrow_token_decimals_from_registry(
        chain_id=int(chain_id or 0),
        borrow_address=str(borrow_address or "").strip().lower(),
    )


def _search_mode() -> str:
    raw = str(os.getenv("DEX_OFFICIAL_V4_SEARCH_MODE", "") or "").strip().lower()
    if raw in {"inner", "inner_single_call", "single_call", "contract_internal"}:
        return "inner_single_call"
    return "external_ladder"


def _hex_or_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        negative = text.startswith("-")
        unsigned = text[1:] if negative else text
        if unsigned.lower().startswith("0x"):
            parsed = int(unsigned, 16)
        else:
            parsed = int(unsigned)
        return -parsed if negative else parsed
    except (TypeError, ValueError):
        return None


def _human_native_amount(chain_id: int, borrow_address: str, raw_value: Any) -> float | None:
    raw = _hex_or_int(raw_value)
    if raw is None:
        return None
    decimals = _decimals_for_borrow(int(chain_id or 0), str(borrow_address or "").lower())
    if decimals is None or decimals <= 0:
        return None
    return float(raw) / (10 ** int(decimals))


def _float_amount_to_raw(value: Any, decimals: int) -> str | None:
    """Encode a signed human amount as a raw hex string for storage/display.

    The signed raw form is used only for borrowing-token estimates and review
    records; it is never sent to the on-chain contract.
    """
    try:
        number = Decimal(str(value))
    except Exception:
        return None
    if not number.is_finite():
        return None
    try:
        scale = Decimal(10) ** int(decimals or 0)
    except (TypeError, ValueError):
        return None
    raw_int = int(number * scale)
    raw_hex = hex(abs(raw_int))
    return raw_hex if raw_int >= 0 else f"-{raw_hex}"


def _native_probe_rows(result: dict[str, Any], chain_id: int, borrow_address: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in result.get("attempts") or []:
        if not isinstance(item, dict):
            continue
        amount = _human_native_amount(chain_id, borrow_address, item.get("amountRaw"))
        profit = _human_native_amount(chain_id, borrow_address, item.get("profitRaw"))
        profitable = bool(item.get("profitable")) or bool(item.get("ok"))
        rows.append(
            {
                "amount": amount,
                "amountRaw": item.get("amountRaw"),
                "borrowAmount": amount,
                "requestedAmount": amount,
                "referenceNetProfitAmount": profit,
                "profitAmount": profit,
                "profit": profit,
                "ok": bool(item.get("ok")),
                "profitable": profitable,
                "status": "live_verified" if profitable else "not_profitable",
                "error": item.get("error"),
                "revertData": item.get("revertData"),
                "routeIndex": item.get("routeIndex"),
            }
        )
    return rows


def _native_route_error_kind(result: dict[str, Any]) -> str:
    raw = str(result.get("errorKind") or "").strip().lower()
    if raw in {
        "gas_estimation_failed",
        "rpc_verification_timeout",
        "rpc_verification_failed",
        "rpc_busy",
    }:
        return raw
    detail = str(result.get("error") or "").strip().lower()
    if any(marker in detail for marker in _GAS_ESTIMATION_FAILURE_MARKERS):
        return "gas_estimation_failed"
    if "timeout" in detail or "timed out" in detail:
        return "rpc_verification_timeout"
    if any(marker in detail for marker in _RPC_BUSY_MARKERS):
        return "rpc_busy"
    for route_error in result.get("routeErrors") or []:
        if not isinstance(route_error, dict):
            continue
        nested = _native_route_error_kind(route_error)
        if nested == "rpc_busy":
            return "rpc_busy"
    for route in result.get("routes") or []:
        if not isinstance(route, dict):
            continue
        nested = _native_route_error_kind(route)
        if nested == "rpc_busy":
            return "rpc_busy"
    return "rpc_verification_failed"


def _native_values_unavailable(result: dict[str, Any]) -> bool:
    """Return true when the native result says preview numeric values are unavailable.

    ``previewBestPoolDetailed unavailable`` and preview selector reverts do not
    prove the route is unprofitable; they only mean the quote could not tell us
    the signed profit.  They must never be classified as ``confirmed_no_profit``.
    """
    if bool(result.get("numericValuesUnavailable")):
        return True
    if any(
        str(value or "").strip().lower()
        in {"previewbestpooldetailed unavailable", "previewbestpool unavailable"}
        for value in (
            result.get("error"),
            (result.get("poolPreview") or {}).get("error"),
            (result.get("poolPreviewDetailed") or {}).get("error"),
        )
    ):
        return True
    return (
        isinstance(result.get("poolPreviewDetailed"), dict)
        and bool((result.get("poolPreviewDetailed") or {}).get("numericValuesUnavailable"))
        is True
    )


def _native_quote_unavailable_reasons(result: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    pool = result.get("poolPreview") if isinstance(result.get("poolPreview"), dict) else {}
    detailed = (
        result.get("poolPreviewDetailed")
        if isinstance(result.get("poolPreviewDetailed"), dict)
        else {}
    )
    for container, label in (
        (pool, "previewBestPool"),
        (detailed, "previewBestPoolDetailed"),
    ):
        if not container:
            continue
        if str(container.get("error") or "").strip().lower() in {
            "previewbestpool unavailable",
            "previewbestpooldetailed unavailable",
        }:
            reasons.append(str(container.get("error")).strip())
            detail = str(container.get("message") or "").strip()
            if detail:
                reasons.append(detail)
            if str(container.get("revertData") or "").strip().lower() not in {"", "0x"}:
                reasons.append(f"revertData={container.get('revertData')}")
        elif bool(container.get("numericValuesUnavailable")):
            reasons.append(f"{label} numeric values unavailable")
    if not reasons and str(result.get("error") or "").strip():
        reasons.append(str(result.get("error")).strip())
    return reasons


def _native_review_failure_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Construct the machine-readable review failure block for the page."""
    reasons = _native_quote_unavailable_reasons(result)
    detail = str(result.get("error") or "").strip()
    if detail and detail not in reasons:
        reasons.append(detail)
    if not reasons:
        reasons = ["native closed-loop preview did not return signed profit values"]
    return {
        "kind": "quote_unavailable",
        "reasons": [str(item).strip() for item in reasons if str(item).strip()],
        "detail": " | ".join(reasons),
    }


def _native_error_location(error_detail: str) -> dict[str, Any]:
    text = str(error_detail or "")
    call_site = next(
        (site for site in _OFFICIAL_V4_CALL_SITES if f"official_v4:{site}" in text),
        "unknown",
    )
    attempt: int | None = None
    for chunk in text.split(":"):
        chunk = chunk.strip()
        if chunk.startswith("attempt="):
            try:
                attempt = int(chunk.split("=", 1)[1])
            except (TypeError, ValueError):
                attempt = None
    probe_amount_raw: str | None = None
    if "probeAmount=0x" in text:
        probe_amount_raw = "0x" + text.split("probeAmount=0x", 1)[1].split(":", 1)[0].strip()
    elif "probeAmount=" in text:
        probe_amount_raw = text.split("probeAmount=", 1)[1].split(":", 1)[0].strip()
    return {
        "flow": "official_v4_closed_loop",
        "callSite": call_site,
        "rpcMethod": "eth_call" if "eth_call" in text else "unknown",
        "attempt": attempt,
        "probeAmountRaw": probe_amount_raw,
        "gasLimit": 50_000_000 if call_site != "unknown" else None,
    }


def _signed_native_profit_value(
    result: dict[str, Any],
    chain_id: int,
    borrow_address: str | None = None,
) -> float | None:
    """Return the most concrete signed borrow-token profit from a native result.

    Ladder rows carry their own signed ``profitRaw`` when the contract can
    return one.  A NoProfitableRoute run also produces
    ``arbitrageNotProfitable.profitDeltaRaw`` from
    ``ArbitrageNotProfitable(received, required)``, which is the concrete
    borrow-token loss for that amount.  Unsigned zero placeholders are not
    treated as proof of a zero/negative value and are skipped here.
    """
    zero_raw = {"", "0", "0x0", "0x00"}
    for item in result.get("attempts") or []:
        if not isinstance(item, dict):
            continue
        raw = item.get("profitRaw")
        if raw is None or str(raw).strip().lower() in zero_raw:
            continue
        value = _human_native_amount(chain_id, borrow_address or "", raw)
        if value is not None:
            return value
    for route in result.get("routes") or []:
        if not isinstance(route, dict):
            continue
        route_addresses = [str(item) for item in (route.get("routeAddresses") or [])]
        route_address = route_addresses[0] if route_addresses else borrow_address
        value = _signed_native_profit_value(route, chain_id, route_address)
        if value is not None:
            return value
        raw = route.get("selectedProfitRaw")
        if raw is None or str(raw).strip().lower() in zero_raw:
            continue
        value = _human_native_amount(chain_id, borrow_address or "", raw)
        if value is not None:
            return value
    for key in ("selectedProfitRaw", "profitRaw", "reportedNetProfitRaw"):
        raw = result.get(key)
        if raw is None or str(raw).strip().lower() in zero_raw:
            continue
        value = _human_native_amount(chain_id, borrow_address or "", raw)
        if value is not None:
            return value
    run_arbitrage = result.get("runArbitrage") if isinstance(result.get("runArbitrage"), dict) else {}
    not_profitable = run_arbitrage.get("arbitrageNotProfitable") if isinstance(
        run_arbitrage.get("arbitrageNotProfitable"), dict
    ) else {}
    raw = (
        not_profitable.get("profitDeltaRaw")
        or not_profitable.get("profitDelta")
        or run_arbitrage.get("profit")
        or run_arbitrage.get("profitRaw")
    )
    if raw is None or str(raw).strip().lower() in zero_raw:
        detailed = result.get("poolPreviewDetailed") if isinstance(result.get("poolPreviewDetailed"), dict) else {}
        for container in (detailed, result):
            arbitrage = container.get("arbitrageNotProfitable") if isinstance(
                container.get("arbitrageNotProfitable"), dict
            ) else {}
            nested_raw = (
                arbitrage.get("profitDeltaRaw")
                or arbitrage.get("profitDelta")
            )
            if nested_raw is None or str(nested_raw).strip().lower() in zero_raw:
                continue
            value = _human_native_amount(chain_id, borrow_address or "", nested_raw)
            if value is not None:
                return value
        return None
    value = _human_native_amount(chain_id, borrow_address or "", raw)
    return value


def _native_rate_product_delta(result: dict[str, Any]) -> float | None:
    """Signed ``netProduct - 1e18`` from the detailed pool preview."""
    detailed = result.get("poolPreviewDetailed") if isinstance(result.get("poolPreviewDetailed"), dict) else {}
    raw = (
        result.get("reportedNetProductDeltaRaw")
        or result.get("netProductDeltaRaw")
        or detailed.get("netProductDeltaRaw")
    )
    value = _hex_or_int(raw)
    if value is None:
        return None
    return float(value) / 1e18


def _native_detailed_values_present(result: dict[str, Any]) -> bool:
    detailed = result.get("poolPreviewDetailed") if isinstance(result.get("poolPreviewDetailed"), dict) else {}
    if bool(detailed.get("ok")) and bool(detailed.get("numericValuesAvailable")):
        return True
    return (
        result.get("poolPreviewDetailed") is not None
        and bool(detailed.get("numericValuesAvailable"))
    )


def _native_run_arbitrage_info(result: dict[str, Any]) -> dict[str, Any]:
    run = result.get("runArbitrage") if isinstance(result.get("runArbitrage"), dict) else {}
    if not run:
        return {}
    raw_error = str(run.get("rawError") or "").strip()
    error = str(run.get("error") or raw_error or "").strip()
    revert_data = str(run.get("revertData") or "").strip()
    revert_selector = str(run.get("revertSelector") or "").strip()
    if not revert_selector and revert_data.lower().startswith("0x") and len(revert_data) >= 10:
        revert_selector = revert_data[:10].lower()
    return {
        "ok": bool(run.get("ok")),
        "error": error,
        "rawError": raw_error,
        "revertData": revert_data,
        "revertSelector": revert_selector,
    }


def _native_run_arbitrage_error_kind(info: dict[str, Any]) -> str | None:
    if not info:
        return None
    selector = str(info.get("revertSelector") or "").strip().lower()
    text = " ".join(
        str(value).lower()
        for value in (info.get("error"), info.get("rawError"), info.get("revertData"))
        if str(value or "").strip()
    )
    if selector == "0xb20c2204" or "swapnotresolved" in text:
        return "swap_not_resolved"
    if selector in {"0x4f422847", "0x16301234"} or "noprofitable" in text:
        return "no_profitable_route"
    if str(info.get("revertData") or "").strip().lower() not in {"", "0x"}:
        return "contract_revert"
    if info.get("error") or info.get("rawError"):
        return "run_arbitrage_error"
    return None


def _native_run_arbitrage_diagnostic(result: dict[str, Any]) -> str:
    info = _native_run_arbitrage_info(result)
    if not info:
        return ""
    kind = _native_run_arbitrage_error_kind(info)
    parts: list[str] = []
    if kind == "swap_not_resolved":
        parts.append("SwapNotResolved()")
    elif kind == "no_profitable_route":
        parts.append("NoProfitableRoute()")
    elif kind == "contract_revert":
        parts.append("runArbitrage contract revert")
    elif kind == "run_arbitrage_error":
        parts.append("runArbitrage failed")
    for value in (info.get("rawError"), info.get("error")):
        text = str(value or "").strip()
        if text and text not in parts:
            parts.append(text)
    if info.get("revertData"):
        text = f"revertData={info['revertData']}"
        if text not in parts:
            parts.append(text)
    if info.get("revertSelector"):
        text = f"revertSelector={info['revertSelector']}"
        if text not in parts:
            parts.append(text)
    return " | ".join(parts)


def _native_run_arbitrage_confirmed(result: dict[str, Any]) -> bool:
    return bool(_native_run_arbitrage_info(result).get("ok"))


def _native_confirmed_broadcast(result: dict[str, Any]) -> bool:
    broadcast = result.get("broadcast") if isinstance(result.get("broadcast"), dict) else {}
    if not broadcast or not _broadcast_payload_represents_attempt(broadcast):
        return False
    tx_hash = str(
        broadcast.get("txHash")
        or broadcast.get("transactionHash")
        or broadcast.get("transaction_hash")
        or ""
    ).strip()
    if not tx_hash:
        return False
    receipt = _broadcast_receipt(broadcast)
    status = str(receipt.get("status") or broadcast.get("status") or "").strip().lower()
    return status in {"0x1", "1", "confirmed", "success"}


def _identity_address(value: Any) -> str | None:
    """Return a canonical lower-case address when the value is a valid 20-byte address."""
    text = str(value or "").strip()
    lowered = text.lower()
    if lowered.startswith("0x"):
        lowered = lowered[2:]
    if len(lowered) != 40:
        return None
    try:
        bytes.fromhex(lowered)
    except ValueError:
        return None
    return "0x" + lowered


def _bool_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def _pool_key_view(pool: dict[str, Any]) -> dict[str, Any]:
    fee = pool.get("fee")
    tick_spacing = pool.get("tickSpacing", pool.get("tick_spacing"))
    currency0 = _identity_address(pool.get("currency0"))
    currency1 = _identity_address(pool.get("currency1"))
    hooks = _identity_address(pool.get("hooks")) or str(pool.get("hooks") or "").strip()
    return {
        "currency0": currency0 or str(pool.get("currency0") or "").strip(),
        "currency1": currency1 or str(pool.get("currency1") or "").strip(),
        "fee": fee,
        "tickSpacing": tick_spacing,
        "hooks": hooks,
        "identity": "|".join(
            str(value).strip().lower()
            for value in (
                currency0 or pool.get("currency0"),
                currency1 or pool.get("currency1"),
                fee,
                tick_spacing,
                hooks,
            )
            if str(value or "").strip()
        ),
    }


def _fallback_route_pool_for_hop(
    route: dict[str, Any],
    hop_index: int,
) -> dict[str, Any]:
    route_pools = route.get("routePools")
    if isinstance(route_pools, list) and hop_index < len(route_pools):
        candidate = route_pools[hop_index]
        return candidate if isinstance(candidate, dict) else {}
    hop_candidates = route.get("hopCandidates")
    if isinstance(hop_candidates, list) and hop_index < len(hop_candidates):
        candidate = hop_candidates[hop_index]
        if not isinstance(candidate, dict):
            return {}
        pools = candidate.get("pools")
        if isinstance(pools, list) and pools:
            first = pools[0]
            if isinstance(first, dict):
                return first.get("poolKey") if isinstance(first.get("poolKey"), dict) else first
    return {}


def _native_route_stage_hops(
    route: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Expand the selected three-hop route into per-hop PoolKey diagnostics.

    ``SwapNotResolved`` itself does not carry a hop index, so this helper cannot
    claim that the contract pinpointed one hop.  It can, however, identify the
    far more common configuration failure: a selected PoolKey whose currencies
    do not line up with the route addresses.
    """
    route_symbols = [
        str(item).strip().upper()
        for item in (route.get("routeSymbols") or [])
        if str(item).strip()
    ]
    route_addresses = [
        _identity_address(item)
        for item in (route.get("routeAddresses") or [])
        if str(item).strip()
    ]
    route_addresses = [item for item in route_addresses if item is not None]
    if len(route_addresses) != 4:
        return {
            "available": False,
            "reason": "route_addresses_unavailable",
            "hopCount": 0,
            "hops": [],
        }
    selected_keys = result.get("selectedKeys")
    selected_directions = result.get("selectedDirections")
    if not isinstance(selected_keys, list) or len(selected_keys) != 3:
        selected_keys = []
    if not isinstance(selected_directions, list) or len(selected_directions) != 3:
        selected_directions = []
    hops: list[dict[str, Any]] = []
    reasons: list[str] = []
    for hop_index in range(3):
        label = ("entry", "bridge", "exit")[hop_index]
        expected_input = route_addresses[hop_index]
        expected_output = route_addresses[hop_index + 1]
        pool_source = (
            selected_keys[hop_index]
            if isinstance(selected_keys, list) and hop_index < len(selected_keys)
            else _fallback_route_pool_for_hop(route, hop_index)
        )
        pool = pool_source if isinstance(pool_source, dict) else {}
        view = _pool_key_view(pool)
        direction = (
            _bool_value(selected_directions[hop_index])
            if isinstance(selected_directions, list) and hop_index < len(selected_directions)
            else None
        )
        if direction is None:
            if _identity_address(view["currency0"]) == expected_input:
                direction = True
            elif _identity_address(view["currency1"]) == expected_input:
                direction = False
        input_currency = (
            _identity_address(view["currency0"]) if direction is True else
            _identity_address(view["currency1"]) if direction is False else None
        )
        output_currency = (
            _identity_address(view["currency1"]) if direction is True else
            _identity_address(view["currency0"]) if direction is False else None
        )
        hop_reasons: list[str] = []
        if not view.get("currency0") or not view.get("currency1"):
            hop_reasons.append("pool_key_currencies_unavailable")
        if direction is None:
            hop_reasons.append("route_direction_not_resolvable")
        else:
            if input_currency != expected_input:
                hop_reasons.append("pool_currency_mismatch_input")
            if output_currency != expected_output:
                hop_reasons.append("pool_currency_mismatch_output")
        reasons.extend(hop_reasons)
        hops.append(
            {
                "hopIndex": hop_index,
                "hopLabel": label,
                "expectedInputAddress": expected_input,
                "expectedOutputAddress": expected_output,
                "expectedInputSymbol": route_symbols[hop_index] if hop_index < len(route_symbols) else None,
                "expectedOutputSymbol": route_symbols[hop_index + 1] if hop_index + 1 < len(route_symbols) else None,
                "direction": direction,
                "inputCurrency": input_currency,
                "outputCurrency": output_currency,
                "poolKey": view,
                "reasons": hop_reasons,
            }
        )
    return {
        "available": True,
        "reason": "ok" if not reasons else "; ".join(sorted(set(reasons))),
        "hopCount": len(hops),
        "routeClosed": route_addresses[0] == route_addresses[3],
        "selectedKeysAvailable": bool(selected_keys),
        "contractCanLocalizeRevert": False,
        "localizationNote": (
            "SwapNotResolved revert data does not include a hop index; "
            "off-chain route stage diagnostics mark expected-currency mismatches."
        ),
        "hops": hops,
        "reasons": sorted(set(reasons)),
    }


def _native_worker_evidence(
    *,
    route: dict[str, Any],
    result: dict[str, Any],
    chain_id: int,
    observed_at: str,
) -> dict[str, Any] | None:
    route_symbols = [str(item).strip().upper() for item in (route.get("routeSymbols") or []) if str(item).strip()]
    route_addresses = [
        str(item).strip().lower()
        for item in (route.get("routeAddresses") or [])
        if str(item).strip()
    ]
    if not route_symbols or not route_addresses:
        return None
    borrow_symbol = route_symbols[0]
    borrow_address = route_addresses[0]
    selected_amount_raw = result.get("selectedAmountRaw")
    selected_profit_raw = result.get("selectedProfitRaw")
    selected_amount = _human_native_amount(chain_id, borrow_address, selected_amount_raw)
    selected_profit = _human_native_amount(chain_id, borrow_address, selected_profit_raw)
    initial_raw = result.get("initialAmountRaw")
    if initial_raw is None or _hex_or_int(initial_raw) in (None, 0):
        initial_raw = (
            selected_amount_raw
            if selected_amount is not None and selected_amount > 0
            else None
        )
    initial_amount = _human_native_amount(chain_id, borrow_address, initial_raw)
    status = str(result.get("status") or "").strip().lower()
    run_arbitrage_confirmed = _native_run_arbitrage_confirmed(result)
    confirmed_by_broadcast = _native_confirmed_broadcast(result)
    profitable = (
        (run_arbitrage_confirmed or confirmed_by_broadcast)
        and status == "profitable_amount_found"
        and selected_profit is not None
        and selected_profit > 0
    )
    signed_native_profit = (
        None
        if profitable
        else _signed_native_profit_value(result, chain_id, borrow_address)
    )
    rate_product_delta = _native_rate_product_delta(result)
    rate_delta_raw = (
        result.get("reportedNetProductDeltaRaw")
        or (result.get("poolPreviewDetailed") or {}).get("netProductDeltaRaw")
    )
    rate_delta_estimate_used = False
    positive_rate_delta_unconfirmed = (
        not profitable
        and rate_product_delta is not None
        and rate_product_delta >= 0
    )
    final_output = None
    if profitable and selected_amount is not None and selected_profit is not None:
        final_output = selected_amount + selected_profit
    net_profit_bps = (
        selected_profit / selected_amount * 10000.0
        if profitable and selected_amount not in (None, 0)
        else None
    )
    broadcast = result.get("broadcast") if isinstance(result.get("broadcast"), dict) else {}
    tx_hash = str(
        broadcast.get("txHash")
        or broadcast.get("transactionHash")
        or broadcast.get("transaction_hash")
        or ""
    ).strip() or None
    receipt = _broadcast_receipt(broadcast)
    gas_used = _gas_metric(receipt.get("gasUsed"))
    effective_gas_price = _gas_metric(receipt.get("effectiveGasPrice"))
    receipt_status = receipt.get("status")
    block_number = receipt.get("blockNumber")
    gas_cost_eth = None
    if gas_used is not None and effective_gas_price is not None:
        gas_cost_eth = gas_used * effective_gas_price / 10**18
    gas_cost_token = gas_cost_eth if borrow_symbol == "ETH" else None
    net_after_gas_token = None
    error_detail = str(result.get("error") or "").strip()
    run_arbitrage_info = _native_run_arbitrage_info(result)
    run_arbitrage_error_kind = _native_run_arbitrage_error_kind(run_arbitrage_info)
    run_arbitrage_diagnostic = _native_run_arbitrage_diagnostic(result)
    route_stage_diagnostic = _native_route_stage_hops(route, result)
    route_stage_reasons = route_stage_diagnostic.get("reasons") or []
    rpc_failed = status in _RPC_FAILURE_STATUSES
    rpc_busy = bool(
        not profitable
        and _native_route_error_kind(result) == "rpc_busy"
    )
    if rpc_busy:
        rpc_failed = True
    quote_unavailable = (
        not profitable
        and not rpc_failed
        and signed_native_profit is None
        and _native_values_unavailable(result)
    )
    # The native worker returns an aggregate contract preview only.  It does
    # not return per-hop quote amounts, so this result cannot be treated as
    # live price evidence or as transaction-preflight input.
    live_verified = bool(run_arbitrage_confirmed or confirmed_by_broadcast)
    evidence_status = (
        "live_verified"
        if live_verified and profitable
        else "static_reference_pending_live_review"
        if profitable
        else "live_evidence_deferred"
        if rpc_busy
        else "rpc_verification_failed"
        if rpc_failed
        else "review_failed"
        if quote_unavailable
        else "route_unprofitable"
    )
    error_kind = _native_route_error_kind(result) if rpc_failed else None
    blockers: list[str] = []
    error_stack: list[dict[str, Any]] = []
    error_location: dict[str, Any] | None = None
    review_failure = None
    if profitable:
        blockers = []
        review_failure = None
    elif rpc_busy:
        detail = error_detail or "RPC is busy or rate limited; on-chain review deferred"
        blockers.append(detail)
        blockers.append(
            "on-chain review deferred due to transient RPC busy/rate limit; "
            "will retry on the next scan"
        )
        review_failure = {
            "kind": "rpc_busy",
            "reasons": [detail],
            "detail": detail,
        }
    elif rpc_failed:
        review_failure = None
        retry_error_text = str(result.get("retryError") or "").strip()
        location_text = " ; ".join(part for part in (error_detail, retry_error_text) if part)
        error_location = _native_error_location(location_text)
        if error_kind == "gas_estimation_failed":
            detail = f"gas estimation failed: {error_detail or 'out of gas'}"
            if retry_error_text:
                detail = f"{detail}; external_ladder retry also failed: {retry_error_text}"
            error_stack = [
                {
                    "module": "DEX",
                    "code": 3008,
                    "kind": error_kind,
                    "flow": error_location["flow"],
                    "nodeId": "|".join(route_symbols),
                    "callSite": error_location["callSite"],
                    "rpcMethod": error_location["rpcMethod"],
                    "attempt": error_location["attempt"],
                    "probeAmountRaw": error_location["probeAmountRaw"],
                    "gasLimit": error_location["gasLimit"],
                    "message": detail,
                }
            ]
        else:
            detail = error_detail or "native Rust worker review failed"
            error_stack = [
                {
                    "module": "DEX",
                    "code": 3008,
                    "kind": error_kind,
                    "flow": error_location["flow"],
                    "nodeId": "|".join(route_symbols),
                    "callSite": error_location["callSite"],
                    "rpcMethod": error_location["rpcMethod"],
                    "attempt": error_location["attempt"],
                    "probeAmountRaw": error_location["probeAmountRaw"],
                    "gasLimit": error_location["gasLimit"],
                    "message": detail,
                }
            ]
        blockers.append(detail)
    elif quote_unavailable:
        review_failure = _native_review_failure_payload(result)
        detail = str(review_failure.get("detail") or "").strip() or (
            "native Rust worker quote unavailable"
        )
        blockers.append(f"chain review failed: {detail}")
        blockers.append(
            "closed loop quote values are unavailable; automatic execution is not justified"
        )
        error_stack = [
            {
                "module": "DEX",
                "code": 3009,
                "kind": "quote_unavailable",
                "flow": "official_v4_closed_loop",
                "nodeId": "|".join(route_symbols),
                "message": detail,
            }
        ]
    else:
        detail = run_arbitrage_diagnostic or error_detail or "NoProfitableRoute()"
        blockers.append(f"chain review failed: {detail}")
        if run_arbitrage_error_kind == "swap_not_resolved" and route_stage_reasons:
            blockers.append(
                "route stage: "
                + "; ".join(str(item) for item in route_stage_reasons)
            )
        blockers.append("closed loop currently has no positive pool-token profit; automatic execution is not justified")
        if rate_product_delta is not None:
            if rate_product_delta < 0:
                blockers.append(
                    f"contract detailed preview concrete value: netProductDelta={rate_product_delta:.18g} "
                    f"({rate_delta_raw or 'raw missing'}); netProduct below 1e18 confirms the route is not profitable"
                )
            else:
                run_revert_text = run_arbitrage_diagnostic or "NoProfitableRoute()"
                blockers.append(
                    f"contract detailed preview concrete value: netProductDelta={rate_product_delta:.18g} "
                    f"({rate_delta_raw or 'raw missing'}); {run_revert_text} and the positive rate "
                    "delta is not execution-confirmed; automatic execution is not justified"
                )
        error_stack = [{
            "module": "DEX",
            "code": 3007,
            "kind": run_arbitrage_error_kind or "no_profitable_route",
            "message": blockers[0],
            "revertData": run_arbitrage_info.get("revertData") or "",
            "revertSelector": run_arbitrage_info.get("revertSelector") or "",
            "routeStages": route_stage_diagnostic.get("hops") or [],
        }]
    probe_rows = _native_probe_rows(result, chain_id, borrow_address)
    best_amount_confirmed = profitable
    confirmed_best_amount = (
        selected_amount
        if best_amount_confirmed and selected_amount not in (None, 0)
        else None
    )
    explored_amount = (
        selected_amount
        if selected_amount not in (None, 0)
        else initial_amount
    )
    best_amount_missing = confirmed_best_amount is None or not best_amount_confirmed
    expected_profit = (
        selected_profit
        if profitable
        else signed_native_profit
    )
    if expected_profit is not None and gas_cost_token is not None:
        net_after_gas_token = expected_profit - gas_cost_token
    sqlite_reference_pnl: dict[str, Any] = {}
    quick_reference_pnl: dict[str, Any] = {}
    pool_reference_pnl: dict[str, Any] = {}
    if (
        not profitable
        and expected_profit is None
        and explored_amount not in (None, 0)
    ):
        try:
            from market.binance_market.dex_route_test import (
                _offline_v4_route_sqlite_pnl_reference,
            )

            sqlite_reference_pnl = _offline_v4_route_sqlite_pnl_reference(
                chain_id=chain_id,
                route_symbols=route_symbols,
                route_addresses=route_addresses,
                amount=float(explored_amount),
            )
        except Exception:
            sqlite_reference_pnl = {}
        if not isinstance(sqlite_reference_pnl, dict):
            sqlite_reference_pnl = {}
        if sqlite_reference_pnl.get("referenceNetProfitAmount") is None:
            try:
                from market.binance_market.dex_route_test import (
                    estimate_fixed_amount_pnl_quick,
                )

                quick_reference_pnl = estimate_fixed_amount_pnl_quick(
                    chain_id=chain_id,
                    route_symbols=route_symbols,
                    route_addresses=route_addresses,
                    amount=float(explored_amount),
                    protocols=["uniswap_v4"],
                    prefer_sqlite_snapshot=True,
                )
            except Exception:
                quick_reference_pnl = {}
            if not isinstance(quick_reference_pnl, dict):
                quick_reference_pnl = {}
        if (
            sqlite_reference_pnl.get("referenceNetProfitAmount") is None
            and quick_reference_pnl.get("referenceNetProfitAmount") is None
        ):
            try:
                from market.binance_market.dex_route_test import (
                    _offline_pool_token_pnl_reference,
                )

                pool_reference_pnl = _offline_pool_token_pnl_reference(
                    chain_id=chain_id,
                    route_symbols=route_symbols,
                    route_addresses=route_addresses,
                    amount=float(explored_amount),
                    protocols=["uniswap_v4"],
                    route=route,
                )
            except Exception:
                pool_reference_pnl = {}
            if not isinstance(pool_reference_pnl, dict):
                pool_reference_pnl = {}
    reference_pnl = (
        sqlite_reference_pnl
        if sqlite_reference_pnl.get("referenceNetProfitAmount") is not None
        else quick_reference_pnl
        if quick_reference_pnl.get("referenceNetProfitAmount") is not None
        else pool_reference_pnl
    )
    reference_pnl_source = (
        "offline_v4_sqlite_route_snapshot"
        if sqlite_reference_pnl.get("referenceNetProfitAmount") is not None
        else "quick_observation_route_snapshot"
        if quick_reference_pnl.get("referenceNetProfitAmount") is not None
        else "offline_pool_token_snapshot"
        if pool_reference_pnl.get("referenceNetProfitAmount") is not None
        else None
    )
    if (
        expected_profit is None
        and reference_pnl.get("referenceNetProfitAmount") is not None
    ):
        expected_profit = float(reference_pnl["referenceNetProfitAmount"])
        final_output = reference_pnl.get("referenceFinalOutputAmount")
        if final_output is not None:
            try:
                final_output = float(final_output)
            except (TypeError, ValueError):
                final_output = None
        net_profit_bps = reference_pnl.get("referenceNetReturnBps")
        if net_profit_bps is not None:
            try:
                net_profit_bps = float(net_profit_bps)
            except (TypeError, ValueError):
                net_profit_bps = None
    if (
        expected_profit is None
        and rate_product_delta is not None
        and explored_amount not in (None, 0)
    ):
        expected_profit = rate_product_delta * float(explored_amount)
        try:
            final_output = float(explored_amount) + float(expected_profit)
        except (TypeError, ValueError):
            final_output = None
        net_profit_bps = (
            float(expected_profit) / float(explored_amount) * 10000.0
            if explored_amount not in (None, 0)
            else None
        )
        rate_delta_estimate_used = True

    missing_concrete_profit = expected_profit is None
    selected_profit_source = (
        "native_rust_contract_preview"
        if not missing_concrete_profit
        else "concrete_pnl_unavailable"
    )
    if missing_concrete_profit:
        selected_profit = None
    if (
        expected_profit is not None
        and selected_profit_raw in (None, "", "0", "0x0", "0x00")
    ):
        borrow_decimals = _decimals_for_borrow(chain_id, borrow_address) or 18
        concrete_raw = _float_amount_to_raw(expected_profit, borrow_decimals)
        if concrete_raw is not None:
            selected_profit_raw = concrete_raw
            selected_profit = float(expected_profit)
            if rate_delta_estimate_used:
                selected_profit_source = "native_contract_rate_delta_estimate"
            elif reference_pnl_source is not None and not profitable:
                selected_profit_source = reference_pnl_source
    if (
        quote_unavailable
        and expected_profit is not None
        and reference_pnl.get("referenceNetProfitAmount") is not None
    ):
        quote_unavailable = False
        review_failure = None
        evidence_status = (
            "static_reference_pending_live_review"
            if float(expected_profit) > 0
            else "route_unprofitable"
        )
        blockers = [
            "chain review reference estimate is available; native preview numeric values were unavailable",
            (
                "route_unprofitable"
                if float(expected_profit) <= 0
                else "static_reference_pending_live_review"
            ),
        ]
        error_stack = [
            {
                "module": "DEX",
                "code": 3007,
                "message": (
                    "chain review reference estimate "
                    f"{expected_profit}; native preview values unavailable"
                ),
            }
        ]
    net_profit_missing_reason = (
        None
        if expected_profit is not None
        else (
            "native result has no concrete borrow-token P&L; a zero value would "
            "indicate a code/data resolution failure and is never accepted as a conclusion"
        )
    )
    if (
        expected_profit is None
        and positive_rate_delta_unconfirmed
    ):
        run_revert_text = run_arbitrage_diagnostic or "NoProfitableRoute()"
        net_profit_missing_reason = (
            "native contract preview returned positive netProductDelta "
            f"({rate_product_delta:.18g}, {rate_delta_raw or 'raw missing'}) but {run_revert_text}; "
            "positive rate delta is not a confirmed borrow-token profit"
        )
    if quote_unavailable and net_profit_missing_reason is not None:
        net_profit_missing_reason = (
            f"{net_profit_missing_reason}; preview unavailable: "
            f"{review_failure.get('detail') if review_failure else error_detail or 'unknown'}"
        )
    net_profit_reference_source = (
        "native_contract_rate_delta_estimate"
        if rate_delta_estimate_used
        else reference_pnl_source
        if expected_profit is not None and not profitable
        else None
    )
    if missing_concrete_profit:
        if evidence_status not in {
            "live_evidence_deferred",
            "live_quote_failed",
            "quote_failed",
            "review_failed",
            "rpc_unavailable",
            "rpc_verification_failed",
            "rpc_verification_timeout",
        }:
            evidence_status = "review_failed_missing_concrete_pnl"
            detail = "concrete P&L could not be resolved; 0.0 is not an acceptable review result"
            blockers.append(detail)
            review_failure = {
                "kind": "missing_concrete_pnl",
                "reasons": [detail],
                "detail": detail,
            }
            error_stack = [
                {
                    "module": "DEX",
                    "code": 3010,
                    "kind": "missing_concrete_pnl",
                    "flow": "official_v4_closed_loop",
                    "nodeId": "|".join(route_symbols),
                    "message": detail,
                }
            ]
    if (
        not profitable
        and expected_profit is not None
        and float(expected_profit) > 0
        and not run_arbitrage_info
        and evidence_status == "route_unprofitable"
    ):
        evidence_status = "static_reference_pending_live_review"
    detailed_values_present = _native_detailed_values_present(result)
    worker_context = (
        result.get("workerContext")
        if isinstance(result.get("workerContext"), dict)
        else {}
    )
    rpc_metadata = (
        result.get("rpcMetadata")
        if isinstance(result.get("rpcMetadata"), dict)
        else None
    )
    cache_evidence = {
        "source": "native_rust_worker_pool",
        "cacheHit": bool(worker_context.get("cacheHit")),
        "loadedAt": worker_context.get("loadedAt"),
        "registryScope": worker_context.get("registryScope"),
        "network": worker_context.get("network"),
        "protocol": worker_context.get("protocol"),
        "databasePath": worker_context.get("databasePath"),
        "cacheTtlSeconds": worker_context.get("cacheTtlSeconds"),
    }
    costs = {
        "borrowInputAmount": initial_amount,
        "borrowInputSymbol": borrow_symbol,
        "finalOutputAmount": final_output,
        "finalOutputSymbol": borrow_symbol,
        "gasUsed": gas_used,
        "effectiveGasPrice": effective_gas_price,
        "gasCostEth": gas_cost_eth,
        "gasCostToken": gas_cost_token,
        "gasSymbol": "ETH",
        "netAfterGasToken": net_after_gas_token,
        "netAfterGasEth": net_after_gas_token,
        "receiptStatus": receipt_status,
        "blockNumber": block_number,
        "initialProbeAmount": initial_amount,
        "initialProbeSymbol": borrow_symbol,
        "netProfitAmount": expected_profit,
        "netProfitAmountMissingReason": net_profit_missing_reason,
        "netProfitReferenceSource": net_profit_reference_source,
        "netProfitAmountSymbol": borrow_symbol,
        "netProfitAmountBps": net_profit_bps,
        "netProfitBps": net_profit_bps,
        "contractNetProductDelta": rate_product_delta,
        "contractNetProductDeltaRaw": rate_delta_raw,
        "contractNetProductDeltaSource": (
            "previewBestPoolDetailed" if rate_delta_raw is not None else None
        ),
        "positiveRateDeltaUnconfirmed": positive_rate_delta_unconfirmed,
        "profitBasis": "borrow_token",
        "profitable": profitable,
        "readyForTransactionPreflight": profitable,
        "requestedBorrowInputAmount": initial_amount,
        "selectedBorrowInputAmount": confirmed_best_amount,
        "selectedBorrowInputAmountConfirmedMax": best_amount_confirmed,
        "selectedBorrowInputAmountRaw": (
            selected_amount_raw
            if confirmed_best_amount is not None
            else None
        ),
        "selectedProfitRaw": selected_profit_raw,
        "selectedProfitAmount": selected_profit,
        "selectedProfitAmountSymbol": borrow_symbol,
        "bestAmountConfirmed": best_amount_confirmed,
        "bestAmountMissing": best_amount_missing,
        "bestAmountSelectionBasis": "native_contract_preview",
        "bestAmountConfirmationReason": (
            "contract preview returned a profitable closed-loop amount"
            if profitable
            else "contract preview did not return a confirmed best amount; current value is initial probe"
        ),
    }
    from dex.runtime.dex_opportunity_helpers import _network_for_chain_id

    return {
        "status": evidence_status,
        "executionTrust": (
            "live_verified"
            if live_verified and profitable
            else "static_reference_pending_live_review"
            if profitable or evidence_status == "static_reference_pending_live_review"
            else "live_evidence_deferred"
            if rpc_busy
            else "rpc_verification_failed"
            if rpc_failed
            else "review_failed"
            if quote_unavailable
            else "route_unprofitable"
        ),
        "liveVerified": bool(live_verified),
        "livePriceVerified": bool(live_verified),
        "readyForTransactionPreflight": profitable,
        "profitable": profitable,
        "trustedForExecution": False,
        "executionEligible": profitable,
        "bestAmountConfirmed": best_amount_confirmed,
        "bestAmount": confirmed_best_amount,
        "bestAmountRaw": (
            selected_amount_raw
            if confirmed_best_amount is not None
            else None
        ),
        "selectedAmount": selected_amount,
        "selectedAmountRaw": selected_amount_raw,
        "selectedProfit": selected_profit,
        "selectedProfitRaw": selected_profit_raw,
        "selectedProfitAmount": selected_profit,
        "selectedProfitAmountSymbol": borrow_symbol,
        "selectedProfitSource": selected_profit_source,
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "hops": [],
        "blockNumber": None,
        "txHash": tx_hash,
        "network": _network_for_chain_id(chain_id),
        "chainId": chain_id,
        "observedAt": observed_at,
        "receiptStatus": receipt_status,
        "blockNumber": block_number,
        "method": result.get("method") or "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": result.get("searchMode"),
        "cacheEvidence": cache_evidence,
        "rpcMetadata": rpc_metadata,
        "broadcastRpcMetadata": (
            result.get("broadcastRpcMetadata")
            if isinstance(result.get("broadcastRpcMetadata"), dict)
            else None
        ),
        "runArbitrage": run_arbitrage_info,
        "runArbitrageErrorKind": run_arbitrage_error_kind,
        "runArbitrageDiagnostic": run_arbitrage_diagnostic,
        "runArbitrageRouteDiagnostic": route_stage_diagnostic,
        "attemptedRpcUrls": (rpc_metadata or {}).get("attemptedRpcUrls"),
        "selectedRpcUrl": (rpc_metadata or {}).get("selectedRpcUrl"),
        "rpcFailoverCount": (rpc_metadata or {}).get("rpcFailoverCount"),
        "errorKind": error_kind,
        "errorLocation": error_location,
        "retriedFrom": result.get("retriedFrom"),
        "attemptedSearchModes": result.get("attemptedSearchModes"),
        "retrySearchMode": result.get("retrySearchMode"),
        "retryError": result.get("retryError"),
        "costModel": costs,
        "validation": {
            "profitableAfterCosts": profitable,
            "allHopsQuoted": bool(live_verified),
            "routeClosed": True,
            "tokenContinuity": False,
        },
        "referenceNetProfitAmount": expected_profit,
        "referenceNetProfitAmountMissingReason": net_profit_missing_reason,
        "referenceNetProfitMethod": net_profit_reference_source or "native_rust_ladder",
        "referenceNetProfitAmountBps": net_profit_bps,
        "referenceRateProductDelta": rate_product_delta,
        "referenceRateProductDeltaRaw": rate_delta_raw,
        "contractNetProductDelta": rate_product_delta,
        "contractNetProductDeltaRaw": rate_delta_raw,
        "contractNetProductDeltaSource": (
            "previewBestPoolDetailed" if rate_delta_raw is not None else None
        ),
        "positiveRateDeltaUnconfirmed": positive_rate_delta_unconfirmed,
        "detailedPreviewValuesAvailable": detailed_values_present,
        "referenceFinalOutputAmount": final_output,
        "referenceFinalOutputSymbol": borrow_symbol,
        "offchainInitialProbeReason": "onchain_native_ladder",
        "executionSizeProbe": {
            "status": "native_rust_closed_loop",
            "searchStatus": "profitable" if profitable else "not_profitable",
            "attempts": probe_rows,
            "probeRecords": probe_rows,
        },
        "blockers": blockers,
        "reviewFailureKind": (review_failure or {}).get("kind"),
        "reviewFailureReasons": (review_failure or {}).get("reasons") or [],
        "reviewFailureDetail": (review_failure or {}).get("detail"),
        "conclusion": (
            "chain review passed; closed-loop borrow-token profit is positive and automation is allowed"
            if profitable
            else "chain review deferred; RPC is busy or rate limited, will retry on the next scan"
            if rpc_busy
            else "chain review failed; closed loop quote values are unavailable"
            if quote_unavailable
            else (
                f"chain review blocked; {run_arbitrage_diagnostic or 'NoProfitableRoute()'}; "
                "contract detailed positive netProductDelta is not execution-confirmed"
                if positive_rate_delta_unconfirmed
                else (
                    "chain review blocked; closed loop currently has no positive pool-token profit"
                    + (
                        f"; contract netProductDelta={rate_product_delta:.18g}"
                        if rate_product_delta is not None
                        else ""
                    )
                )
            )
        ),
        "errorStack": error_stack,
    }


def build_native_worker_attempt(
    *,
    route: dict[str, Any],
    result: dict[str, Any],
    chain_id: int,
    observed_at: str,
) -> dict[str, Any] | None:
    route_symbols = [str(item).strip().upper() for item in (route.get("routeSymbols") or []) if str(item).strip()]
    route_addresses = [
        str(item).strip().lower()
        for item in (route.get("routeAddresses") or [])
        if str(item).strip()
    ]
    if len(route_symbols) != 4 or len(route_addresses) != 4:
        return None
    evidence = _native_worker_evidence(
        route=route,
        result=result,
        chain_id=chain_id,
        observed_at=observed_at,
    )
    if evidence is None:
        return None
    costs = evidence.get("costModel") if isinstance(evidence.get("costModel"), dict) else {}
    probe_rows = _native_probe_rows(result, chain_id, route_addresses[0])
    analysis = deepcopy(route.get("quoteAnalysis") or {})
    enriched_analysis = {
        **analysis,
        "livePriceVerified": bool(evidence.get("livePriceVerified")),
        "profitable": bool(evidence.get("profitable")),
        "bestAmount": costs.get("selectedBorrowInputAmount"),
        "bestAmountConfirmed": bool(costs.get("bestAmountConfirmed")),
        "bestAmountMissing": bool(costs.get("bestAmountMissing")),
        "initialProbeAmount": costs.get("initialProbeAmount"),
        "referenceInputAmount": costs.get("selectedBorrowInputAmount"),
        "referenceInputSymbol": costs.get("borrowInputSymbol"),
        "referenceFinalOutputAmount": costs.get("finalOutputAmount"),
        "referenceFinalOutputSymbol": costs.get("finalOutputSymbol"),
        "referenceNetProfitAmount": costs.get("netProfitAmount"),
        "referenceNetReturnBps": costs.get("netProfitAmountBps"),
        "profitBasis": costs.get("profitBasis") or "borrow_token",
        "method": "native_closed_loop_rust_ladder",
        "probeRecords": probe_rows,
        "probe_records": probe_rows,
        "sizeAnalysis": {
            "status": "native_rust_closed_loop",
            "searchStatus": "profitable" if evidence.get("profitable") else "not_profitable",
            "selectedAmountRaw": result.get("selectedAmountRaw"),
            "selectedProfitRaw": result.get("selectedProfitRaw"),
            "probeRecords": probe_rows,
            "rows": probe_rows,
        },
    }
    enriched_route = {
        **route,
        "symbolPair": str(route.get("symbolPair") or " -> ".join(route_symbols)),
        "quoteAnalysis": enriched_analysis,
    }
    from dex.runtime.dex_opportunity_helpers import (
        _apply_onchain_evidence_to_attempt,
        _route_attempt,
    )

    attempt = _route_attempt(
        enriched_route,
        payload={},
        chain_id=chain_id,
        pair_rank=1,
        observed_at=observed_at,
    )
    attempt = _apply_onchain_evidence_to_attempt(attempt, evidence)
    pair = " -> ".join(route_symbols)
    quote = attempt.get("quote") if isinstance(attempt.get("quote"), dict) else {}
    quote.update(
        {
            "pair": pair,
            "input_amount": costs.get("requestedBorrowInputAmount"),
            "input_symbol": costs.get("borrowInputSymbol"),
            "final_amount": costs.get("finalOutputAmount"),
            "final_symbol": costs.get("finalOutputSymbol"),
            "final_delta_amount": costs.get("netProfitAmount"),
            "best_amount": costs.get("selectedBorrowInputAmount"),
            "bestAmount": costs.get("selectedBorrowInputAmount"),
            "best_amount_confirmed": bool(costs.get("bestAmountConfirmed")),
            "bestAmountConfirmed": bool(costs.get("bestAmountConfirmed")),
            "probe_records": probe_rows,
            "probeRecords": probe_rows,
            "quote_analysis": enriched_analysis,
            "quoteAnalysis": enriched_analysis,
        }
    )
    best_amount_calculation = {
        "method": "native_closed_loop_rust_ladder",
        "bestAmount": costs.get("selectedBorrowInputAmount"),
        "bestAmountConfirmed": bool(costs.get("bestAmountConfirmed")),
        "bestAmountMissing": bool(costs.get("bestAmountMissing")),
        "recommendedBorrowInputAmount": costs.get("selectedBorrowInputAmount"),
        "recommendedSelectionBasis": "native_contract_preview",
        "recommendedSelectionMetric": "borrow_token_profit",
        "initialProbeAmount": costs.get("initialProbeAmount"),
        "requestedAmount": costs.get("requestedBorrowInputAmount"),
        "requestedSymbol": costs.get("borrowInputSymbol"),
        "probeRecords": probe_rows,
        "probe_records": probe_rows,
        "rows": probe_rows,
    }
    selected_profit_raw_value = evidence.get("selectedProfitRaw")
    selected_profit_missing_reason = None
    if selected_profit_raw_value in (None, "", "0", "0x0", "0x00"):
        evidence_status = str(evidence.get("status") or "").strip()
        costs_missing_reason = (
            costs.get("netProfitAmountMissingReason")
            if isinstance(costs, dict)
            else None
        )
        selected_profit_missing_reason = costs_missing_reason
        if (
            selected_profit_missing_reason is None
            and evidence.get("costModel", {}).get("netProfitReferenceSource")
            != "native_contract_conservative_zero"
        ):
            selected_profit_missing_reason = (
                f"no_native_signed_profit:{evidence_status}"
                if evidence_status
                else "no_native_signed_profit"
            )
        blocked_sources = list(
            dict.fromkeys(
                str(item).strip()
                for item in (evidence.get("blockers") or [evidence.get("conclusion")])
                if isinstance(item, str) and str(item).strip()
            )
        )
        blocked_text = "; ".join(blocked_sources)
        if (
            blocked_text
            and selected_profit_missing_reason
            and blocked_text not in selected_profit_missing_reason
        ):
            selected_profit_missing_reason = (
                f"{selected_profit_missing_reason} | {blocked_text}"
            )
    attempt.update(
        {
            "pair": pair,
            "pair_rank": 1,
            "route_path": route_symbols,
            "route_addresses": route_addresses,
            "routeAddresses": route_addresses,
            "priority_reason": "closed_loop_native_rust",
            "final_delta_amount": costs.get("netProfitAmount"),
            "final_symbol": costs.get("finalOutputSymbol"),
            "quote": quote,
            "quoteAnalysis": enriched_analysis,
            "quote_analysis": enriched_analysis,
            "bestAmountCalculation": best_amount_calculation,
            "best_amount_calculation": best_amount_calculation,
            "best_amount": costs.get("selectedBorrowInputAmount"),
            "probe_records": probe_rows,
            "probeRecords": probe_rows,
            "blocked_reasons": evidence.get("blockers") or attempt.get("blocked_reasons") or [],
            "selectedProfitRaw": evidence.get("selectedProfitRaw"),
            "selectedProfitAmount": evidence.get("selectedProfitAmount"),
            "selectedProfitAmountSymbol": evidence.get("selectedProfitAmountSymbol"),
            "selectedProfitSource": evidence.get("selectedProfitSource"),
            "selectedProfitMissingReason": selected_profit_missing_reason,
        }
    )
    if attempt.get("precheck") is not None and isinstance(attempt["precheck"], dict):
        attempt["precheck"]["best_amount_calculation"] = best_amount_calculation
        attempt["precheck"]["probe_records"] = probe_rows
    broadcast = result.get("broadcast") if isinstance(result.get("broadcast"), dict) else {}
    if broadcast and not _broadcast_payload_represents_attempt(broadcast):
        _attach_review_broadcast_skip(
            attempt,
            chain_id=chain_id,
            reason=_review_broadcast_skip_reason(attempt),
            broadcast=broadcast,
        )
        return attempt
    if broadcast:
        _attach_native_auto_execute_to_attempt(attempt, broadcast, observed_at)
        if not _review_preflight_ready(attempt):
            attempt["broadcastAttemptedDuringReview"] = True
    elif bool(evidence.get("profitable")):
        if bool(result.get("broadcastNotSelected")):
            attempt["auto_execute"] = {
                "skipped": True,
                "reason": "broadcast_not_selected",
                "broadcast": False,
                "attempted": False,
                "broadcastGate": broadcast_gate_status(chain_id=chain_id),
            }
            attempt["auto_execute_requested"] = False
            attempt["broadcastNotSelected"] = True
            if isinstance(attempt.get("precheck"), dict):
                attempt["precheck"]["auto_execute_requested"] = False
            return attempt
        broadcast_skipped = str(result.get("broadcastSkipped") or "").strip().lower()
        if broadcast_skipped:
            reason = (
                "broadcast_group_stopped"
                if broadcast_skipped == "group_stopped"
                else f"broadcast_skipped_{broadcast_skipped}"
            )
            attempt["auto_execute"] = {
                "skipped": True,
                "reason": reason,
                "broadcast": False,
                "attempted": False,
                "broadcastGate": broadcast_gate_status(chain_id=chain_id),
            }
            # A stopped group is an intentional control decision, not a
            # missing broadcast result and not a submission failure.
            attempt["auto_execute_requested"] = False
            attempt["broadcastSkipped"] = broadcast_skipped
            if isinstance(attempt.get("precheck"), dict):
                attempt["precheck"]["auto_execute_requested"] = False
            return attempt
        if bool(result.get("executionQueued")):
            attempt["auto_execute"] = {
                "skipped": True,
                "queued": True,
                "reason": "broadcast_queued",
                "broadcast": False,
                "attempted": False,
                "broadcastResultMissing": False,
                "executionQueueAt": result.get("executionQueueAt"),
                "broadcastGate": broadcast_gate_status(chain_id=chain_id),
            }
            attempt["auto_execute_requested"] = True
            attempt["executionQueued"] = True
            attempt["executionQueueAt"] = result.get("executionQueueAt")
            if isinstance(attempt.get("precheck"), dict):
                attempt["precheck"]["auto_execute_requested"] = True
            return attempt
        broadcast_error = result.get("broadcastError")
        if broadcast_error:
            if isinstance(broadcast_error, dict):
                failure_payload = {
                    **broadcast_error,
                    "ok": False,
                    "attempted": True,
                    "failed": True,
                    "status": "broadcast_failed",
                    "txHash": broadcast_error.get("txHash"),
                }
            else:
                failure_payload = {
                    "ok": False,
                    "attempted": True,
                    "failed": True,
                    "status": "broadcast_failed",
                    "error": str(broadcast_error).strip(),
                    "txHash": None,
                }
            _attach_native_auto_execute_to_attempt(
                attempt,
                failure_payload,
                observed_at,
            )
            if not _review_preflight_ready(attempt):
                attempt["broadcastAttemptedDuringReview"] = True
        else:
            broadcast_gate = broadcast_gate_status(chain_id=chain_id)
            gate_open = bool(broadcast_gate.get("broadcastEnabled"))
            attempt["auto_execute"] = {
                "skipped": True,
                "reason": (
                    "broadcast_result_missing"
                    if gate_open
                    else "native_broadcast_disabled"
                ),
                "broadcastResultMissing": gate_open,
                "broadcast": False,
                "attempted": False,
                "broadcastGate": broadcast_gate,
            }
            if bool(broadcast_gate.get("autoExecuteEnabled")):
                attempt["auto_execute_requested"] = True
    else:
        error_kind = str(evidence.get("errorKind") or "").strip().lower()
        status = str(evidence.get("status") or "").strip().lower()
        if error_kind == "gas_estimation_failed":
            reason = "gas_estimation_failed"
        elif error_kind == "rpc_busy" or status == "live_evidence_deferred":
            reason = "rpc_busy"
        elif status in _RPC_FAILURE_STATUSES:
            reason = "rpc_verification_failed"
        else:
            review_failure_kind = str(evidence.get("reviewFailureKind") or "").strip().lower()
            if status == "static_reference_pending_live_review":
                reason = "manual_review_requires_per_hop_live_quotes"
            elif status == "review_failed" or review_failure_kind == "quote_unavailable":
                reason = "review_failed_quote_unavailable"
            else:
                reason = "manual_review_not_profitable"
        attempt["auto_execute"] = {"skipped": True, "reason": reason}
    if evidence.get("rpcMetadata") is not None:
        attempt["rpcMetadata"] = evidence["rpcMetadata"]
    if isinstance(result.get("broadcastRpcMetadata"), dict):
        attempt["broadcastRpcMetadata"] = result["broadcastRpcMetadata"]
    return attempt


def _review_preflight_ready(attempt: dict[str, Any]) -> bool:
    """Return whether the attempt passed the ready-for-preflight gate.

    Broadcast is only legal after live-verified on-chain evidence.  A stale
    broadcast payload attached to a static-reference review must never turn
    the review row into an execution terminal.
    """
    precheck = attempt.get("precheck") if isinstance(attempt.get("precheck"), dict) else {}
    quote = attempt.get("quote") if isinstance(attempt.get("quote"), dict) else {}
    evidence = quote.get("live_onchain_evidence") if isinstance(quote.get("live_onchain_evidence"), dict) else {}
    return bool(
        precheck.get("ready_for_transaction_preflight")
        or quote.get("ready_for_transaction_preflight")
        or evidence.get("readyForTransactionPreflight")
    )


def _broadcast_payload_represents_attempt(broadcast: dict[str, Any]) -> bool:
    """Return whether a native broadcast payload is a genuine submission attempt.

    Payloads from ``record_broadcast_failure`` and
    ``broadcast_v4_best_pools_inner`` carry ``attempted`` or ``submitted``
    markers.  Review-only signals such as ``broadcastNotSelected`` or plain
    queue/skip payloads never mark an attempt and must not become an
    execution terminal.
    """
    tx_hash = str(
        broadcast.get("txHash")
        or broadcast.get("transactionHash")
        or broadcast.get("transaction_hash")
        or ""
    ).strip()
    if tx_hash:
        return True
    if str(broadcast.get("submitted") or "").strip().lower() in {"1", "true"}:
        return True
    attempted = str(broadcast.get("attempted") or "").strip().lower()
    failed = str(broadcast.get("failed") or "").strip().lower()
    status = str(broadcast.get("status") or "").strip().lower()
    if attempted in {"1", "true"} and failed in {"1", "true"}:
        return True
    return attempted in {"1", "true"} and status in {
        "broadcast_failed",
        "broadcast_not_accepted",
        "public_fallback_failed",
        "submission_failed",
    }


def _review_broadcast_skip_reason(attempt: dict[str, Any]) -> str:
    quote = attempt.get("quote") if isinstance(attempt.get("quote"), dict) else {}
    evidence = quote.get("live_onchain_evidence")
    if not isinstance(evidence, dict):
        evidence = {}
    return str(
        evidence.get("status")
        or attempt.get("state")
        or "review_broadcast_not_ready"
    ).strip().lower()


def _attach_review_broadcast_skip(
    attempt: dict[str, Any],
    *,
    chain_id: int,
    reason: str,
    broadcast: dict[str, Any] | None = None,
    broadcast_error: Any = None,
) -> dict[str, Any]:
    """Mark a review-phase broadcast payload as ignored, never as submitted."""
    payload: dict[str, Any] = {
        "skipped": True,
        "reason": reason,
        "broadcast": False,
        "attempted": False,
        "broadcastGate": broadcast_gate_status(chain_id=chain_id),
    }
    if isinstance(broadcast, dict):
        payload["reviewBroadcastResult"] = broadcast
    if broadcast_error is not None:
        payload["reviewBroadcastError"] = broadcast_error
    attempt["auto_execute"] = payload
    attempt["auto_execute_requested"] = False
    attempt["reviewBroadcastSkipped"] = True
    attempt["reviewBroadcastReason"] = reason
    precheck = attempt.get("precheck") if isinstance(attempt.get("precheck"), dict) else {}
    if isinstance(precheck, dict):
        precheck["auto_execute_requested"] = False
    return attempt


def _attach_native_auto_execute_to_attempt(
    attempt: dict[str, Any],
    broadcast: dict[str, Any],
    observed_at: str,
) -> None:
    tx_hash = str(
        broadcast.get("txHash")
        or broadcast.get("transactionHash")
        or broadcast.get("transaction_hash")
        or ""
    ).strip()
    submitted = bool(tx_hash)
    executed = submitted
    route_symbols = [
        str(item).strip().upper()
        for item in (attempt.get("route_path") or attempt.get("route_addresses") or [])
        if str(item).strip() and not str(item).strip().startswith("0x")
    ] or [
        str(item).strip()
        for item in (attempt.get("route_path") or [])
        if str(item).strip()
    ]
    receipt = _broadcast_receipt(broadcast)
    receipt_status = str(receipt.get("status") or broadcast.get("status") or "").strip().lower() or None
    confirmed = submitted and receipt_status in {"0x1", "1", "confirmed", "success"}
    reverted = submitted and receipt_status in {"0x0", "0", "failed", "reverted"}
    confirmation_status = (
        "confirmed"
        if confirmed
        else "failed"
        if reverted
        else "pending"
        if submitted
        else "none"
    )
    raw_error = broadcast.get("rawError") or broadcast.get("error") or broadcast.get("detail")
    if isinstance(raw_error, dict):
        raw_error = raw_error.get("rawError") or raw_error.get("error") or raw_error
    error_text_from_broadcast = str(raw_error or "").strip()
    revert_data = str(broadcast.get("revertData") or "").strip()
    failure_kind = None
    if not submitted and error_text_from_broadcast:
        failure_kind = "submission_rejected_before_acceptance"
    elif submitted and reverted:
        failure_kind = "execution_reverted_onchain"
    gas_used = _gas_metric(receipt.get("gasUsed"))
    effective_gas_price = _gas_metric(receipt.get("effectiveGasPrice"))
    gas_cost_eth = None
    if gas_used is not None and effective_gas_price is not None:
        gas_cost_eth = gas_used * effective_gas_price / 10**18
    broadcast_result = {
        **dict(broadcast),
        "gasUsed": receipt.get("gasUsed") if gas_used is not None else broadcast.get("gasUsed"),
        "gasUsedInt": gas_used,
        "effectiveGasPrice": (
            receipt.get("effectiveGasPrice")
            if effective_gas_price is not None
            else broadcast.get("effectiveGasPrice")
        ),
        "effectiveGasPriceInt": effective_gas_price,
        "gasCostEth": gas_cost_eth,
        "blockNumber": receipt.get("blockNumber") or broadcast.get("blockNumber"),
        "receiptStatus": receipt_status,
        "submitted": submitted,
        "confirmed": confirmed,
        "confirmationStatus": confirmation_status,
        "rawError": broadcast.get("rawError") or broadcast.get("error"),
        "revertData": revert_data,
        "revertSelector": broadcast.get("revertSelector") or (revert_data[:10] if revert_data.startswith("0x") and len(revert_data) >= 10 else ""),
        "rpcMethod": broadcast.get("rpcMethod") or "eth_sendRawTransaction",
        "broadcastChannel": broadcast.get("broadcastChannel") or broadcast.get("broadcast_channel"),
        "relayErrors": broadcast.get("relayErrors") or broadcast.get("relay_errors") or [],
        "failureKind": failure_kind,
    }
    auto_execute_payload = {
        "observed_at": observed_at,
        "route_symbols": route_symbols,
        "broadcast": True,
        "attempted": True,
        "amount": attempt.get("best_amount"),
        "result": broadcast_result,
        "executed": executed,
        "submitted": submitted,
        "confirmed": confirmed,
        "confirmationStatus": confirmation_status,
        "failureKind": failure_kind,
    }
    if not submitted:
        auto_execute_payload["reason"] = "broadcast_failed"
    elif reverted:
        auto_execute_payload["reason"] = "broadcast_reverted"
    elif not confirmed:
        auto_execute_payload["reason"] = "broadcast_pending_confirmation"
    attempt["auto_execute"] = auto_execute_payload
    attempt["broadcast"] = True
    attempt["broadcastAttempted"] = True
    attempt["broadcastPerformed"] = submitted
    attempt["auto_execute_requested"] = True
    quote = attempt.get("quote") if isinstance(attempt.get("quote"), dict) else {}
    precheck = attempt.get("precheck") if isinstance(attempt.get("precheck"), dict) else {}
    if submitted and confirmed:
        state = "execution_success"
        execution_phase = "order_submission"
        checks_passed = True
        can_submit_order = True
        blocked_reasons: list[str] = []
        error_text: str | None = None
    elif submitted and reverted:
        error_text = (
            f"chain receipt status {receipt_status}: "
            "transaction mined but execution reverted"
        )
        state = "execution_failed"
        execution_phase = "submission"
        checks_passed = False
        can_submit_order = False
        blocked_reasons = [error_text]
    elif submitted:
        state = "submitted_success"
        execution_phase = "order_submission"
        checks_passed = True
        can_submit_order = True
        blocked_reasons = []
        error_text = None
    else:
        error_text = error_text_from_broadcast or "native_auto_execute_failed"
        state = "execution_failed"
        execution_phase = "submission"
        checks_passed = False
        can_submit_order = False
        blocked_reasons = [error_text]
    attempt["state"] = state
    attempt["execution_phase"] = execution_phase
    attempt["checks_passed"] = checks_passed
    attempt["can_submit_order"] = can_submit_order
    attempt["order_submission_enabled"] = True
    attempt["blocked_reasons"] = blocked_reasons
    attempt["error"] = error_text
    if failure_kind:
        attempt["failureKind"] = failure_kind
    if revert_data:
        attempt["revertData"] = revert_data
        attempt["revertReasonVerifiable"] = True
    elif error_text:
        attempt["revertReasonVerifiable"] = False
    if error_text:
        from dex.runtime.error_disposition import resolve_broadcast_error

        info = resolve_broadcast_error(error_text)
        attempt["errorKey"] = info["errorKey"]
        attempt["errorCode"] = info["errorCode"]
        attempt["errorName"] = info["errorName"]
        attempt["disposition"] = info["disposition"]
    if submitted:
        attempt["tx_hash"] = tx_hash
        quote["tx_hash"] = tx_hash
    precheck.update(
        {
            "status": state,
            "checks_passed": checks_passed,
            "can_submit_order": can_submit_order,
            "order_submission_enabled": True,
            "auto_execute_requested": True,
            "broadcast": True,
            "reasons": blocked_reasons,
        }
    )


def native_worker_result_fingerprint(
    *,
    group_key: str,
    updated_at: str | None,
    result: dict[str, Any],
    route: dict[str, Any] | None = None,
) -> str:
    route_symbols = []
    route_addresses = []
    if isinstance(route, dict):
        route_symbols = [str(item).strip().upper() for item in (route.get("routeSymbols") or []) if str(item).strip()]
        route_addresses = [
            str(item).strip().lower()
            for item in (route.get("routeAddresses") or [])
            if str(item).strip()
        ]
    broadcast = result.get("broadcast") if isinstance(result.get("broadcast"), dict) else {}
    tx_hash = str(
        broadcast.get("txHash")
        or broadcast.get("transactionHash")
        or broadcast.get("transaction_hash")
        or ""
    ).strip()
    return "|".join(
        [
            str(group_key or "").lower(),
            str(updated_at or ""),
            str(result.get("status") or ""),
            str(result.get("selectedAmountRaw") or ""),
            str(result.get("selectedProfitRaw") or ""),
            tx_hash,
            json.dumps(route_symbols, sort_keys=True),
            json.dumps(route_addresses, sort_keys=True),
        ]
    )


def _broadcast_allowed(chain_id: int) -> bool:
    from market.binance_market.dex_route_test import _env_flag, _robinhood_env_prefix

    prefix = _robinhood_env_prefix(int(chain_id))
    return _env_flag(
        f"{prefix}_ALLOW_OFFICIAL_V4_ARB_BROADCAST",
        "OFFICIAL_V4_ALLOW_ARB_BROADCAST",
    )


def broadcast_gate_status(*, chain_id: int) -> dict[str, Any]:
    """Return the current pre-broadcast gate and the missing reasons."""
    from market.binance_market.dex_route_test import (
        _env_flag,
        _official_v4_signer_for_chain,
        _robinhood_env_prefix,
    )
    from dex.runtime.dex_opportunity_auto_execute import auto_execute_env_enabled

    prefix = _robinhood_env_prefix(int(chain_id))
    auto_execute_enabled = auto_execute_env_enabled()
    broadcast_allowed = _env_flag(
        f"{prefix}_ALLOW_OFFICIAL_V4_ARB_BROADCAST",
        "OFFICIAL_V4_ALLOW_ARB_BROADCAST",
    )
    private_key, key_name = _official_v4_signer_for_chain(int(chain_id))
    reasons: list[str] = []
    if not auto_execute_enabled:
        reasons.append("DEX_AUTO_EXECUTE_VERIFIED_OPPORTUNITIES is not true")
    if not broadcast_allowed:
        reasons.append(f"{prefix}_ALLOW_OFFICIAL_V4_ARB_BROADCAST is not true")
    if not private_key:
        reasons.append("no official V4 broadcast signer key is configured")
    return {
        "autoExecuteEnabled": bool(auto_execute_enabled),
        "broadcastAllowed": bool(broadcast_allowed),
        "privateKeyConfigured": bool(private_key),
        "privateKeySource": key_name,
        "broadcastEnabled": bool(auto_execute_enabled and broadcast_allowed and private_key),
        "reasons": reasons,
    }


def _candidate_pool_key_groups_from_database(
    *,
    chain_id: int,
    route_addresses: list[str],
    network: str,
    protocols: list[str],
    return_details: bool = False,
) -> list[list[dict[str, Any]]] | dict[str, Any]:
    """Build PoolKey groups from the candidate and observation surface.

    ``dex_candidate_pools`` is the downstream-visible candidate result.  The
    closed-loop builder does not re-apply fee, hook, or depth quality checks
    here; rows already placed in this table are considered candidate-authorised.
    The middle Y-Z hop is not anchor-adjacent, so it is also authorized by
    ``market_observation_intermediate_pools`` and resolved back to
    ``v4_exchange_pools`` when the observation row does not carry a PoolKey.
    """
    normalized_route_addresses = [
        str(item).strip().lower()
        for item in route_addresses
        if str(item).strip()
    ]
    if (
        len(normalized_route_addresses) != 4
        or normalized_route_addresses[0] != normalized_route_addresses[-1]
    ):
        return {
            "ok": False,
            "reason": "invalid_closed_loop_route",
            "routeAddresses": normalized_route_addresses,
            "groups": [],
            "keyIds": [],
            "missingEdges": [],
        } if return_details else []
    protocol = str(protocols[0] if protocols else "uniswap_v4").strip().lower()
    edges: list[tuple[str, str]] = []
    for index in range(3):
        left = normalized_route_addresses[index]
        right = normalized_route_addresses[index + 1]
        edges.append((left, right) if left <= right else (right, left))
    key_ids = [f"{left}:{right}" for left, right in edges]
    try:
        from core.db.storage_business_io import query

        database_path = resolve_runtime(chain_id)["databasePath"]
        rows = query(
            str(database_path),
            """
            SELECT token0, token1, fee, tick_spacing, hooks, pool_key_json,
                   usable_for_observation
            FROM dex_candidate_pools
            WHERE network = ? AND chain_id = ? AND protocol = ?
              AND usable_for_observation = 1
            ORDER BY pool_id
            """,
            (network, int(chain_id), protocol),
        )
        try:
            observation_rows = query(
                str(database_path),
                """
                SELECT m.token0, m.token1, m.fee, m.tick_spacing, m.hooks,
                       CASE WHEN TRIM(COALESCE(m.pool_key_json, '')) IN ('', '{}', 'null')
                            THEN p.pool_key_json
                            ELSE m.pool_key_json END AS pool_key_json,
                       1 AS usable_for_observation
                FROM market_observation_intermediate_pools m
                LEFT JOIN v4_exchange_pools p
                  ON p.network = m.network AND p.chain_id = m.chain_id
                 AND p.protocol = m.protocol AND p.pool_id = m.pool_id
                WHERE m.network = ? AND m.chain_id = ? AND m.protocol = ?
                ORDER BY m.pool_id
                """,
                (network, int(chain_id), protocol),
            )
            rows = [*rows, *observation_rows]
        except Exception:
            # Older database fixtures may not yet expose the observation
            # surface; candidate-only resolution remains valid when it does.
            pass
    except Exception as exc:
        return {
            "ok": False,
            "reason": "candidate_surface_unavailable",
            "detail": str(exc)[:200],
            "routeAddresses": normalized_route_addresses,
            "groups": [],
            "keyIds": key_ids,
            "missingEdges": [],
        } if return_details else []

    def _normalize_key(row: dict[str, Any]) -> dict[str, Any] | None:
        raw_key = row.get("pool_key_json")
        if isinstance(raw_key, str):
            try:
                raw_key = json.loads(raw_key)
            except (TypeError, ValueError, json.JSONDecodeError):
                raw_key = None
        if not isinstance(raw_key, dict):
            raw_key = {}
        currency0 = str(raw_key.get("currency0") or row.get("token0") or "").strip()
        currency1 = str(raw_key.get("currency1") or row.get("token1") or "").strip()
        try:
            fee = int(raw_key.get("fee", row.get("fee") if raw_key.get("fee") is None else raw_key.get("fee")))
            tick_spacing = int(raw_key.get("tickSpacing", row.get("tick_spacing")))
        except (TypeError, ValueError):
            return None
        hooks = str(raw_key.get("hooks") or row.get("hooks") or "0x0000000000000000000000000000000000000000").strip()
        if not currency0 or not currency1 or fee < 0 or tick_spacing < 0:
            return None
        return {
            "currency0": currency0,
            "currency1": currency1,
            "fee": fee,
            "tickSpacing": tick_spacing,
            "hooks": hooks,
        }

    keys_by_edge: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        token0 = str(row.get("token0") or "").strip().lower()
        token1 = str(row.get("token1") or "").strip().lower()
        if not token0 or not token1:
            continue
        key = _normalize_key(row)
        if key is None:
            continue
        if token0 <= token1:
            edge = (token0, token1)
        else:
            edge = (token1, token0)
        keys_by_edge.setdefault(edge, []).append(key)

    missing_edges = [
        {
            "index": index,
            "from": left,
            "to": right,
            "keyId": f"{left}:{right}",
        }
        for index, (left, right) in enumerate(edges)
        if not keys_by_edge.get((left, right))
    ]
    groups: list[list[dict[str, Any]]] = [] if missing_edges else [[]]
    for edge in edges:
        candidates = keys_by_edge.get(edge) or []
        if not candidates:
            groups = []
            break
        groups = [
            group + [key]
            for group in groups
            for key in candidates[:8]
        ]
        if len(groups) > 64:
            groups = groups[:64]
    details: dict[str, Any] = {
        "ok": not missing_edges,
        "network": network,
        "chainId": int(chain_id),
        "protocol": protocol,
        "routeAddresses": normalized_route_addresses,
        "groups": groups,
        "keyIds": key_ids,
        "missingEdges": missing_edges,
    }
    if missing_edges:
        details["reason"] = "candidate_pool_key_missing"
    return details if return_details else groups


def _candidate_pool_key_tuple(key: dict[str, Any]) -> tuple[Any, ...] | None:
    """Canonical key identity used to validate route-supplied PoolKeys."""
    currency0 = str(key.get("currency0") or "").strip().lower()
    currency1 = str(key.get("currency1") or "").strip().lower()
    try:
        fee = int(key.get("fee"))
        tick_spacing = int(key.get("tickSpacing"))
    except (TypeError, ValueError):
        return None
    hooks = str(
        key.get("hooks")
        or "0x0000000000000000000000000000000000000000"
    ).strip().lower()
    if not currency0 or not currency1:
        return None
    return (currency0, currency1, fee, tick_spacing, hooks)


def _fill_refresh_required_diagnostics(
    diagnostics: dict[str, Any],
    *,
    route: dict[str, Any],
    candidate_details: dict[str, Any],
    refresh_cause: str | None = None,
) -> None:
    route_addresses = candidate_details.get("routeAddresses")
    if not route_addresses:
        route_addresses = [
            str(item).strip().lower()
            for item in (route.get("routeAddresses") or [])
            if str(item).strip()
        ]
    diagnostics.update(
        {
            "kind": "pool_manager_refresh_required",
            "reason": "pool_manager_refresh_required",
            "retryable": True,
            "routeSymbols": route.get("routeSymbols") or [],
            "routeAddresses": route_addresses,
            "keyIds": candidate_details.get("keyIds") or [],
            "missingEdges": candidate_details.get("missingEdges") or [],
            "requestedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
    )
    cause = (
        refresh_cause
        or candidate_details.get("reason")
        or candidate_details.get("detail")
    )
    if cause:
        diagnostics["refreshCause"] = str(cause)


def _normalize_route_pool_key_group(
    group: list[Any],
) -> list[dict[str, Any]] | None:
    normalized: list[dict[str, Any]] = []
    for value in group:
        if isinstance(value, dict):
            item = {
                "currency0": value.get("currency0"),
                "currency1": value.get("currency1"),
                "fee": value.get("fee"),
                "tickSpacing": value.get("tickSpacing"),
                "hooks": value.get("hooks")
                or "0x0000000000000000000000000000000000000000",
            }
        elif isinstance(value, (list, tuple)) and len(value) == 5:
            item = {
                "currency0": value[0],
                "currency1": value[1],
                "fee": value[2],
                "tickSpacing": value[3],
                "hooks": value[4]
                or "0x0000000000000000000000000000000000000000",
            }
        else:
            return None
        normalized.append(item)
    return normalized


def _route_task_config(
    *,
    route: dict[str, Any],
    chain_id: int,
    protocols: list[str],
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    from dex.runtime.dex_opportunity_helpers import _network_for_chain_id
    from market.binance_market.dex_route_helpers import (
        _official_route_addresses,
        _official_v4_route_pool_key_groups,
        _route_addresses_from_pool_keys,
        _route_addresses_from_route,
        normalize_route_symbols_from_addresses,
    )

    raw_route_symbols = [
        str(item).strip().upper()
        for item in (route.get("routeSymbols") or [])
        if str(item).strip()
    ]
    route_addresses = [
        str(item).strip().lower()
        for item in _route_addresses_from_route(route)
        if str(item).strip()
    ]

    if len(route_addresses) != len(raw_route_symbols):
        route_addresses = [
            str(item).strip().lower()
            for item in _official_route_addresses(
                raw_route_symbols,
                chain_id=int(chain_id),
            )
            if str(item).strip()
        ]
    if len(route_addresses) != len(raw_route_symbols):
        route_addresses = [
            str(item).strip().lower()
            for item in _route_addresses_from_pool_keys(
                route,
                raw_route_symbols,
                chain_id=int(chain_id),
            )
            if str(item).strip()
        ]

    route_symbols = normalize_route_symbols_from_addresses(
        route_symbols=raw_route_symbols,
        route_addresses=route_addresses,
        chain_id=int(chain_id),
        route=route,
    )
    if len(route_symbols) != 4 or len(route_addresses) != 4:
        if diagnostics is not None:
            _fill_refresh_required_diagnostics(
                diagnostics,
                route=route,
                candidate_details={"routeAddresses": route_addresses},
                refresh_cause="route_addresses_unresolved",
            )
        return None
    from market.binance_market.dex_route_test import _official_v4_hop_candidate_sets

    candidate_details = _candidate_pool_key_groups_from_database(
        chain_id=int(chain_id),
        route_addresses=route_addresses,
        network=_network_for_chain_id(chain_id),
        protocols=protocols,
        return_details=True,
    )
    candidate_groups = candidate_details.get("groups") if isinstance(candidate_details, dict) else []
    if not candidate_groups:
        if diagnostics is not None:
            _fill_refresh_required_diagnostics(
                diagnostics,
                route=route,
                candidate_details=candidate_details if isinstance(candidate_details, dict) else {},
            )
        return None
    candidate_key_set: set[tuple[Any, ...]] = set()
    for group in candidate_groups:
        for key in group:
            identity = _candidate_pool_key_tuple(key)
            if identity is not None:
                candidate_key_set.add(identity)

    route_groups = _official_v4_route_pool_key_groups(
        route_symbols=route_symbols,
        chain_id=int(chain_id),
        protocols=protocols,
        route_addresses=route_addresses,
    )
    groups: list[list[dict[str, Any]]] = []
    if route_groups:
        for raw_group in route_groups:
            normalized_group = _normalize_route_pool_key_group(raw_group)
            if (
                normalized_group
                and all(
                    _candidate_pool_key_tuple(key) in candidate_key_set
                    for key in normalized_group
                )
            ):
                groups.append(normalized_group)
        if not groups:
            groups = candidate_groups
    else:
        groups = candidate_groups
    if not groups:
        if diagnostics is not None:
            _fill_refresh_required_diagnostics(
                diagnostics,
                route=route,
                candidate_details=candidate_details if isinstance(candidate_details, dict) else {},
            )
        return None

    def _normalize_official_v4_key(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            currency0 = str(value.get("currency0") or "").strip()
            currency1 = str(value.get("currency1") or "").strip()
            fee = value.get("fee")
            tick_spacing = value.get("tickSpacing")
            hooks = str(value.get("hooks") or "0x0000000000000000000000000000000000000000").strip()
        elif isinstance(value, (list, tuple)) and len(value) == 5:
            currency0 = str(value[0] or "").strip()
            currency1 = str(value[1] or "").strip()
            fee = value[2]
            tick_spacing = value[3]
            hooks = str(value[4] or "0x0000000000000000000000000000000000000000").strip()
        else:
            return None
        try:
            fee_int = int(fee)
            tick_spacing_int = int(tick_spacing)
        except (TypeError, ValueError):
            return None
        if not currency0 or not currency1 or fee_int < 0:
            return None
        return {
            "currency0": currency0,
            "currency1": currency1,
            "fee": fee_int,
            "tickSpacing": tick_spacing_int,
            "hooks": hooks,
        }

    try:
        (
            entry_keys,
            entry_directions,
            bridge_keys,
            bridge_directions,
            exit_keys,
            exit_directions,
        ) = _official_v4_hop_candidate_sets(groups, route_addresses)
    except (ValueError, TypeError) as exc:
        if diagnostics is not None:
            _fill_refresh_required_diagnostics(
                diagnostics,
                route=route,
                candidate_details=candidate_details if isinstance(candidate_details, dict) else {},
                refresh_cause=f"candidate_hop_sets_unavailable: {exc}",
            )
        return None
    entry_keys = [_normalize_official_v4_key(key) for key in entry_keys]
    bridge_keys = [_normalize_official_v4_key(key) for key in bridge_keys]
    exit_keys = [_normalize_official_v4_key(key) for key in exit_keys]
    if (
        not entry_keys
        or not bridge_keys
        or not exit_keys
        or any(key is None for key in entry_keys + bridge_keys + exit_keys)
    ):
        if diagnostics is not None:
            _fill_refresh_required_diagnostics(
                diagnostics,
                route=route,
                candidate_details=candidate_details if isinstance(candidate_details, dict) else {},
                refresh_cause="candidate_pool_key_normalization_failed",
            )
        return None
    borrow_decimals = _decimals_for_borrow(chain_id, route_addresses[0])
    if borrow_decimals is None or borrow_decimals <= 0:
        if diagnostics is not None:
            _fill_refresh_required_diagnostics(
                diagnostics,
                route=route,
                candidate_details=candidate_details if isinstance(candidate_details, dict) else {},
                refresh_cause="borrow_token_metadata_unavailable",
            )
        return None
    initial_amount_raw = max(1, int(Decimal(10) ** int(borrow_decimals)))
    min_probe_amount_raw = max(
        1,
        int(Decimal("0.001") * (Decimal(10) ** int(borrow_decimals))),
    )
    from dex.runtime.dex_opportunity_helpers import _liquidity_cap_raw, _pool_map

    pool_map = _pool_map(route)
    cap_raw, _cap_basis = _liquidity_cap_raw(pool_map, route_symbols[0], int(borrow_decimals))
    if cap_raw is None or cap_raw <= 0:
        cap_raw = initial_amount_raw * 64
    try:
        max_probe_attempts = max(4, min(64, int(os.getenv("DEX_V4_MANUAL_REVIEW_PROBE_ATTEMPTS", "24") or 24)))
    except (TypeError, ValueError):
        max_probe_attempts = 24
    return {
        "token": route_addresses[0],
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "entryKeys": entry_keys,
        "entryDirections": [bool(item) for item in entry_directions],
        "bridgeKeys": bridge_keys,
        "bridgeDirections": [bool(item) for item in bridge_directions],
        "exitKeys": exit_keys,
        "exitDirections": [bool(item) for item in exit_directions],
        "initialAmountRaw": str(initial_amount_raw),
        "minProfitRaw": "1",
        "minProbeAmountRaw": str(min_probe_amount_raw),
        "maxProbeAmountRaw": str(cap_raw),
        "maxProbeAttempts": max_probe_attempts,
        "searchMode": _search_mode(),
    }


def build_group_task_config(
    *,
    chain_id: int,
    protocols: list[str],
    routes: list[dict[str, Any]],
) -> tuple[str | None, dict[str, Any] | None, list[dict[str, Any]]]:
    route_configs: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for route in routes:
        route_diagnostics: dict[str, Any] = {}
        config = _route_task_config(
            route=route,
            chain_id=chain_id,
            protocols=protocols,
            diagnostics=route_diagnostics,
        )
        if config is None:
            failure: dict[str, Any] = {
                "routeSymbols": route.get("routeSymbols") or [],
                "reason": (
                    route_diagnostics.get("reason")
                    or "pool_manager_refresh_required"
                ),
            }
            failure.update(
                {
                    key: value
                    for key, value in route_diagnostics.items()
                    if key not in failure
                }
            )
            if failure["reason"] == "pool_manager_refresh_required":
                failure.setdefault("kind", "pool_manager_refresh_required")
                failure.setdefault("retryable", True)
                failure.setdefault(
                    "requestedAt",
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                )
            failed.append(
                failure
            )
            continue
        route_configs.append(config)
    if not route_configs:
        return None, None, failed
    from dex.runtime.dex_opportunity_helpers import _network_for_chain_id
    from dex.runtime.dex_onchain_quote_evidence_official_v4 import _official_v4_arb_address
    from dex.runtime.dex_route_rpc import (
        _probe_robinhood_family_rpc,
        robinhood_family_rpc_urls,
    )
    from market.binance_market.dex_route_test import _official_v4_signer_for_chain
    from dex.runtime.dex_opportunity_auto_execute import auto_execute_env_enabled

    first = route_configs[0]
    try:
        rpc_url, _block_number = _probe_robinhood_family_rpc(int(chain_id))
    except Exception as exc:
        return None, None, [{"reason": f"rpc_probe_failed: {str(exc)[:200]}"}]
    rpc_urls = [rpc_url] + [
        url
        for url in robinhood_family_rpc_urls(int(chain_id))
        if str(url).strip() and str(url).strip() != str(rpc_url).strip()
    ]
    deduped_rpc_urls: list[str] = []
    for url in rpc_urls:
        value = str(url).strip()
        if value and value not in deduped_rpc_urls:
            deduped_rpc_urls.append(value)
    rpc_urls = deduped_rpc_urls
    arb_address = _official_v4_arb_address(chain_id=int(chain_id))
    if not arb_address:
        return None, None, [{"reason": "missing_official_v4_arb_address"}]
    broadcast_gate = broadcast_gate_status(chain_id=int(chain_id))
    broadcast_enabled = bool(broadcast_gate.get("broadcastEnabled"))
    group_key = "|".join(
        [
            str(first["routeSymbols"][0]).lower(),
            "|".join(sorted([str(first["routeSymbols"][1]).lower(), str(first["routeSymbols"][2]).lower()])),
        ]
    )
    first_addresses = [str(item).strip().lower() for item in (first.get("routeAddresses") or [])]
    pool_group_key = ""
    if len(first_addresses) == 4:
        pool_group_key = "|".join(
            [
                first_addresses[0],
                "|".join(sorted([first_addresses[1], first_addresses[2]])),
            ]
        )
    task = {
        "rpcUrl": rpc_url,
        "rpcUrls": rpc_urls,
        "arbAddress": arb_address,
        "poolGroupKey": pool_group_key,
        "timeoutSeconds": max(10, int(os.getenv("DEX_V4_NATIVE_WORKER_TIMEOUT_SECONDS", "30") or 30)),
        "scanIntervalSeconds": max(
            5,
            min(
                600,
                int(
                    os.getenv(
                        "DEX_OPPORTUNITY_SCAN_INTERVAL_SECONDS",
                        "60",
                    )
                    or 60
                ),
            ),
        ),
        "chainId": int(chain_id),
        "broadcastEnabled": broadcast_enabled,
        "deferBroadcast": bool(broadcast_enabled and execution_queue_env_enabled()),
        "broadcastPrivateKey": (
            _official_v4_signer_for_chain(int(chain_id))[0]
            if broadcast_enabled
            else None
        ),
        "broadcastGate": broadcast_gate,
        "relayUrls": os.getenv("OFFICIAL_V4_BROADCAST_RELAY_URLS", "").strip() or None,
        "allow_public_fallback": True,
        "routes": route_configs,
        "network": _network_for_chain_id(chain_id),
    }
    return group_key, task, failed


def submit_group(
    *,
    group_key: str,
    chain_id: int,
    protocols: list[str],
    task_config: dict[str, Any],
    mode: str = "deep",
) -> dict[str, Any]:
    pool = pool_for_chain(chain_id)
    network = str(task_config.pop("network", "") or "")
    return pool.submit(
        group_key=group_key,
        config_json=json.dumps(task_config, ensure_ascii=False),
        chain_id=int(chain_id),
        protocols=list(protocols),
        mode=mode,
        priority=0,
        database_path=resolve_runtime(chain_id)["databasePath"],
        network=network,
        protocol="uniswap_v4",
    )


__all__ = [
    "build_native_worker_attempt",
    "build_group_task_config",
    "clear_rust_active_runtime",
    "database_path_for_chain",
    "execution_queue_env_enabled",
    "execution_workers_env",
    "get_native_pool",
    "pool_for_chain",
    "native_closed_loop_enabled",
    "rust_execution_component_enabled",
    "rust_active_runtime_status",
    "resolve_runtime",
    "native_worker_result_fingerprint",
    "native_worker_status",
    "reset_native_pool",
    "start_closed_loop_group",
    "stop_closed_loop_group",
    "stop_native_pool",
    "stop_native_pool_for_chain",
    "submit_group",
    "sync_rust_active_runtime",
    "wake_waiting_broadcast",
    "wake_waiting_closed_loop_group",
]
