from __future__ import annotations

import logging
import json
import os
from typing import Any

from core.broadcast_failure_audit import append_broadcast_failure_audit
from core.market_identity import canonical_market_id
from dex.runtime.closed_loop_identity import (
    directed_symbol_key_from_symbols,
    physical_loop_key_from_route,
    worker_group_key_from_route,
)
from dex.runtime.dex_opportunity_route_runner import (
    _route_closed_loop_group_key,
    _route_is_closed_loop_quad,
)
from dex.runtime.rust_closed_loop import (
    build_group_task_config,
    database_path_for_chain,
    native_worker_status,
    submit_group,
)

logger = logging.getLogger(__name__)


def _is_broadcast_failure_result(result: dict[str, Any]) -> bool:
    if result.get("broadcastError"):
        return True
    broadcast = result.get("broadcast")
    return (
        isinstance(broadcast, dict)
        and broadcast.get("ok") is False
        and broadcast.get("attempted") is True
    )


def _canonical_market_id(chain_id: int, protocol: str) -> str:
    return canonical_market_id(
        "dex_arbitrage",
        int(chain_id),
        str(protocol or "uniswap_v4").strip().lower(),
    )


def _broadcast_failure_audit_record(
    *,
    route: dict[str, Any],
    result: dict[str, Any],
    group_key: str,
    chain_id: int,
    observed_at: str,
    recorded_at: str,
    fingerprint: str,
) -> dict[str, Any]:
    broadcast = result.get("broadcast") if isinstance(result.get("broadcast"), dict) else {}
    symbols = route.get("routeSymbols") if isinstance(route.get("routeSymbols"), list) else []
    path_text = " -> ".join(str(symbol) for symbol in symbols)
    return {
        "schemaVersion": "robinhood-testnet-broadcast-failure-audit-v1",
        "recordedAt": recorded_at,
        "observedAt": observed_at,
        "groupKey": group_key,
        "chainId": chain_id,
        "network": route.get("network"),
        "routePath": path_text
        or str(route.get("pair") or route.get("routePathKey") or ""),
        "directionSelected": bool(result.get("directionSelected")),
        "status": result.get("status"),
        "broadcastExecuted": bool(result.get("broadcastExecuted")),
        "broadcastError": result.get("broadcastError") or broadcast.get("error"),
        "broadcastStatus": broadcast.get("status"),
        "broadcast": {
            "ok": broadcast.get("ok"),
            "attempted": broadcast.get("attempted"),
            "failed": broadcast.get("failed"),
            "status": broadcast.get("status"),
            "error": broadcast.get("error"),
            "rawError": broadcast.get("rawError"),
            "revertData": broadcast.get("revertData"),
            "revertSelector": broadcast.get("revertSelector"),
            "rpcMethod": broadcast.get("rpcMethod"),
            "broadcastChannel": broadcast.get("broadcastChannel")
            or broadcast.get("broadcast_channel"),
            "relayErrors": broadcast.get("relayErrors")
            or broadcast.get("relay_errors"),
            "submitted": broadcast.get("submitted"),
            "txHash": broadcast.get("txHash"),
        },
        "selectedAmountRaw": result.get("selectedAmountRaw"),
        "selectedProfitRaw": result.get("selectedProfitRaw"),
        "minProfitRaw": result.get("minProfitRaw"),
        "profitSymbol": symbols[0] if symbols else route.get("final_symbol"),
        "attemptFingerprint": fingerprint,
        "source": "rust_worker_broadcast_failure_history",
    }


def _closed_loop_transition_error(exc: Exception) -> str:
    text = str(exc).strip().lower()
    if "closed_loop_group_busy" in text:
        return "closed_loop_group_busy"
    if "closed_loop_group_worker_not_running" in text or "closed_loop_group_not_found" in text:
        return "closed_loop_group_not_found"
    if "execution_task_not_found" in text:
        return "execution_task_not_found"
    if "execution_task_not_waiting" in text:
        return "execution_task_not_waiting"
    if "broadcast executor is not enabled" in text:
        return "broadcast_executor_unavailable"
    return "closed_loop_state_transition_failed"


def _closed_loop_transition_detail(error: str) -> str:
    return {
        "closed_loop_group_busy": "只有空闲等待中的复核线程可以手动唤醒",
        "closed_loop_group_not_found": "未找到对应的常驻复核线程",
        "execution_task_not_found": "未找到对应的等待广播任务",
        "execution_task_not_waiting": "只有等待中的广播任务可以手动唤醒",
        "broadcast_executor_unavailable": "广播执行器当前不可用",
    }.get(error, "闭环状态切换失败")


class DexOpportunityNativeRustMixin:
    def _native_database_url(self, chain_id: int | None = None) -> str | None:
        """Resolve the worker pool database from the active chain scope.

        The chain runtime is the single source of truth for chain/database
        pairing.  Thread-local context and watched-chain heuristics are only
        compatibility fallbacks and cannot override an explicit chain id.
        """
        if chain_id is not None:
            try:
                from core.runtime.chain_runtime import chain_runtime_registry

                return chain_runtime_registry().get(chain_id).database_url
            except Exception:
                return database_path_for_chain(chain_id)
        watched = getattr(self, "_watched", {})
        if isinstance(watched, dict):
            for item in watched.values():
                if isinstance(item, tuple) and item and item[0] is not None:
                    return self._native_database_url(int(item[0]))
        try:
            from core.runtime.chain_runtime import chain_runtime_registry

            return chain_runtime_registry().current().database_url
        except Exception:
            return database_path_for_chain(None)

    def _native_chain_id(self) -> int | None:
        """Return the active chain id through the single runtime registry."""
        try:
            from core.runtime.chain_runtime import chain_runtime_registry

            return chain_runtime_registry().current().chain_id
        except Exception:
            return None

    def _native_template_map(self) -> dict[str, list[dict[str, Any]]]:
        templates = getattr(self, "_native_route_templates", None)
        if templates is None:
            templates = {}
            self._native_route_templates = templates
        return templates

    def _native_record_fingerprints(self) -> set[str]:
        fingerprints = getattr(self, "_native_recorded_fingerprints", None)
        if fingerprints is None:
            fingerprints = set()
            self._native_recorded_fingerprints = fingerprints
        return fingerprints

    def _native_recorder_lock_object(self) -> Any:
        lock = getattr(self, "_native_recorder_lock", None)
        if lock is None:
            import threading

            lock = threading.Lock()
            self._native_recorder_lock = lock
        return lock

    def _enqueue_closed_loop_groups_from_payload(
        self,
        payload: dict[str, Any],
        *,
        chain_id: int | None,
        protocols: list[str] | None,
    ) -> None:
        if self._thread is None or not self._thread.is_alive():
            if not self.auto_execute_enabled:
                return
        if self._native_rust_closed_loop and self.auto_execute_enabled:
            self._submit_native_closed_loop_groups(
                payload=payload,
                chain_id=chain_id,
                protocols=list(protocols or []),
            )
            return
        seen: set[str] = set()
        for route in payload.get("routes") or []:
            if not isinstance(route, dict) or not _route_is_closed_loop_quad(route):
                continue
            symbols = route.get("routeSymbols") if isinstance(route.get("routeSymbols"), list) else route.get("symbols")
            if isinstance(symbols, list) and symbols and str(symbols[0] or "").strip():
                group_key = str(symbols[0]).strip().upper()
            else:
                group_key = _route_closed_loop_group_key(route)
            if group_key in seen:
                continue
            seen.add(group_key)
            current = self.closed_loop_review_queue.get(group_key)
            if current is None or current.state in {"done", "error"}:
                self.closed_loop_review_queue.submit_group(
                    group_key=group_key,
                    chain_id=chain_id,
                    protocols=protocols,
                    mode="deep" if self.auto_execute_enabled else "scan",
                )

    def _native_group_busy(self, group_key: str, chain_id: int | None = None) -> bool:
        try:
            status = native_worker_status(chain_id=chain_id)
        except Exception:
            return False
        workers = status.get("workers") if isinstance(status.get("workers"), list) else []
        normalized_key = str(group_key or "").strip().lower()
        for worker in workers:
            if not isinstance(worker, dict):
                continue
            worker_key = str(worker.get("group_key") or "").lower()
            if (
                worker_key != normalized_key
                and (
                    not normalized_key
                    or not worker_key.startswith(f"{normalized_key}|")
                )
            ):
                continue
            if str(worker.get("state") or "").lower() in {"queued", "running"}:
                return True
        return False

    def _native_resident_group_keys(self, chain_id: int | None = None) -> set[str]:
        try:
            status = native_worker_status(
                include_payloads=False,
                chain_id=chain_id,
            )
        except Exception:
            return set()
        workers = status.get("workers") if isinstance(status.get("workers"), list) else []
        resident: set[str] = set()
        for worker in workers:
            if not isinstance(worker, dict):
                continue
            if str(worker.get("state") or "").strip().lower() == "stopped":
                continue
            key = str(worker.get("group_key") or "").strip().lower()
            if key:
                resident.add(key)
        return resident

    def _native_worker_group_count(self, chain_id: int | None = None) -> int:
        """Count every spawned group thread, including stopped resident ones."""
        try:
            status = native_worker_status(
                include_payloads=False,
                chain_id=chain_id,
            )
        except Exception:
            return 0
        workers = status.get("workers") if isinstance(status.get("workers"), list) else []
        return len(
            {
                str(worker.get("group_key") or "").strip().lower()
                for worker in workers
                if isinstance(worker, dict)
                and str(worker.get("group_key") or "").strip()
            }
        )

    def _submit_native_closed_loop_groups(
        self,
        *,
        payload: dict[str, Any],
        chain_id: int | None,
        protocols: list[str],
    ) -> None:
        if chain_id is None:
            return
        groups: dict[str, list[tuple[dict[str, Any], str]]] = {}

        def _pair_key(route: dict[str, Any]) -> tuple[str, str] | None:
            symbols = (
                route.get("routeSymbols")
                if isinstance(route.get("routeSymbols"), list)
                else route.get("symbols")
            )
            symbols = [str(item).strip().upper() for item in (symbols or []) if str(item).strip()]
            if len(symbols) != 4 or symbols[0] != symbols[-1]:
                return None
            addresses = [
                str(item).strip().lower()
                for item in (route.get("routeAddresses") or [])
                if str(item).strip()
            ]
            if len(addresses) != len(symbols):
                from market.binance_market.dex_route_helpers import (
                    _official_route_addresses,
                    _route_addresses_from_pool_keys,
                )

                addresses = [
                    str(item).strip().lower()
                    for item in _official_route_addresses(symbols, chain_id=chain_id)
                    if str(item).strip()
                ]
                if len(addresses) != len(symbols):
                    addresses = [
                        str(item).strip().lower()
                        for item in _route_addresses_from_pool_keys(
                            route,
                            symbols,
                            chain_id=chain_id,
                        )
                        if str(item).strip()
                    ]
            if len(addresses) == len(symbols):
                from market.binance_market.dex_route_helpers import (
                    trusted_symbol_from_address,
                )

                if not route.get("routeAddresses"):
                    route["routeAddresses"] = addresses
                symbols = [
                    str(
                        trusted_symbol_from_address(address, chain_id=chain_id)
                        or f"UNRESOLVED:{address}"
                    ).upper()
                    for address in addresses
                ]
                route["routeSymbols"] = symbols
            else:
                return None
            loop_key = physical_loop_key_from_route(route)
            worker_group_key = worker_group_key_from_route(route)
            if not loop_key or not worker_group_key:
                return None
            route["loopKey"] = loop_key
            return worker_group_key, loop_key

        for route in payload.get("routes") or []:
            if not isinstance(route, dict) or not _route_is_closed_loop_quad(route):
                continue
            key = _pair_key(route)
            if key is None:
                continue
            worker_group_key, loop_key = key
            groups.setdefault(worker_group_key, []).append((route, loop_key))
        try:
            batch_limit = max(
                1,
                int(
                    os.getenv("DEX_CLOSED_LOOP_MAX_NEW_GROUPS_PER_SCAN", "16")
                    or "16"
                ),
            )
        except (TypeError, ValueError):
            batch_limit = 16
        try:
            total_group_limit = max(
                1,
                min(
                    32,
                    int(
                        os.getenv("DEX_CLOSED_LOOP_MAX_RESIDENT_GROUPS", "16")
                        or "16"
                    ),
                ),
            )
        except (TypeError, ValueError):
            total_group_limit = 16
        resident_keys = self._native_resident_group_keys(chain_id)
        worker_group_count = self._native_worker_group_count(chain_id)
        batch_limit = min(
            batch_limit,
            max(0, total_group_limit - worker_group_count),
        )
        newly_submitted = 0
        for group_key, route_entries in groups.items():
            normalized = str(group_key or "").strip().lower()
            if not normalized or normalized in resident_keys:
                continue
            if newly_submitted >= batch_limit:
                continue
            routes = [route for route, _loop_key in route_entries]
            resolved_group_key, task_config, failed = build_group_task_config(
                chain_id=chain_id,
                protocols=protocols,
                routes=routes,
            )
            from dex.runtime import closed_loop_consumer
            from dex.runtime.closed_loop_state_machine import (
                complete_loop,
                scope_error_isolation_active,
            )
            from dex.runtime.dex_opportunity_helpers import _network_for_chain_id, _now_iso

            protocol = (
                str(protocols[0]).strip().lower()
                if protocols and str(protocols[0] or "").strip()
                else "uniswap_v4"
            )
            network = _network_for_chain_id(chain_id)
            database = self._native_database_url(chain_id)
            if not database:
                database = closed_loop_consumer.resolve_runtime_database(None)
            market_id = _canonical_market_id(chain_id, protocol)

            def _record_config_failure(
                *,
                route: dict[str, Any],
                loop_key: str,
                reason: str,
                detail: dict[str, Any],
            ) -> bool:
                failure_reason = reason or "native_task_config_unavailable"
                retryable = (
                    failure_reason.startswith(
                        "missing_pool_candidates_or_token_metadata"
                    )
                    or "missing_pool_candidates_or_token_metadata"
                    in failure_reason
                    or failure_reason == "pool_manager_refresh_required"
                    or "pool_manager_refresh_required" in failure_reason
                )
                failure_detail = json.dumps(
                    detail,
                    ensure_ascii=False,
                    default=str,
                )[:2000]
                if retryable:
                    logger.warning(
                        "native closed-loop pool metadata not ready; "
                        "skipping %s until candidates exist: %s",
                        loop_key,
                        failure_detail,
                    )
                    return False
                closed_loop_consumer.mark_group_submitted(
                    database,
                    network=network,
                    chain_id=chain_id,
                    protocol=protocol,
                    market_id=market_id,
                    loop_key=loop_key,
                )
                claimed_failure = closed_loop_consumer.claim_review_before_submit(
                    database,
                    network=network,
                    chain_id=chain_id,
                    protocol=protocol,
                    market_id=market_id,
                    loop_key=loop_key,
                )
                completed = None
                if claimed_failure is not None and database:
                    completed = complete_loop(
                        database,
                        network=network,
                        chain_id=chain_id,
                        protocol=protocol,
                        market_id=market_id,
                        loop_key=loop_key,
                        operation="review",
                        outcome="failed",
                        owner_thread=closed_loop_consumer.owner_thread(
                            loop_key,
                            "review",
                        ),
                        error=f"review_failed:{failure_reason}; {failure_detail}",
                    )
                if completed is not None:
                    from dex.runtime.cow_attempt_log import (
                        record_cow_attempt_list_safely,
                    )

                    try:
                        record_cow_attempt_list_safely(
                            [{
                                "network": network,
                                "chain_id": int(chain_id),
                                "market_id": market_id,
                                "protocol": protocol,
                                "pair": loop_key,
                                "loop_key": loop_key,
                                "route_path": [
                                    str(item)
                                    for item in (route.get("routeSymbols") or [])
                                ],
                                "category": "not_executable",
                                "status": "review_failed",
                                "priority_reason": "review_failed",
                                "reviewFailureKind": "native_task_config_unavailable",
                                "reviewFailureReasons": [failure_reason],
                                "reviewFailureDetail": failure_detail,
                                "failureReason": failure_reason,
                                "error": failure_detail,
                                "errorKey": "native_task_config_unavailable",
                                "observed_at": _now_iso(),
                            }],
                            database_url=database,
                            dedupe_market_candidates=False,
                        )
                    except Exception as exc:
                        logger.warning(
                            "native closed-loop review failure attempt log failed for %s: %s",
                            loop_key,
                            exc,
                            exc_info=True,
                        )
                logger.warning(
                    "native closed-loop config unavailable for %s: %s",
                    loop_key,
                    failure_reason,
                )
                return completed is not None

            if not task_config or not resolved_group_key:
                failure = failed[0] if failed and isinstance(failed[0], dict) else {}
                reason = str(
                    failure.get("reason") or "native_task_config_unavailable"
                )
                for route, loop_key in route_entries:
                    _record_config_failure(
                        route=route,
                        loop_key=loop_key,
                        reason=reason,
                        detail=failure or {"reason": reason},
                    )
                continue

            route_configs = (
                task_config.get("routes")
                if isinstance(task_config.get("routes"), list)
                else []
            )
            config_by_loop_key: dict[str, dict[str, Any]] = {}
            for route_config in route_configs:
                if not isinstance(route_config, dict):
                    continue
                route_loop_key = physical_loop_key_from_route(route_config)
                if route_loop_key:
                    config_by_loop_key[route_loop_key] = route_config

            failed_by_directed_key: dict[str, dict[str, Any]] = {}
            for failure in failed:
                if not isinstance(failure, dict):
                    continue
                directed_key = directed_symbol_key_from_symbols(
                    failure.get("routeSymbols")
                )
                if directed_key:
                    failed_by_directed_key[directed_key] = failure

            for route, loop_key in route_entries:
                directed_key = directed_symbol_key_from_symbols(
                    route.get("routeSymbols")
                )
                failure = failed_by_directed_key.get(directed_key or "")
                if failure and loop_key not in config_by_loop_key:
                    _record_config_failure(
                        route=route,
                        loop_key=loop_key,
                        reason=str(
                            failure.get("reason")
                            or "native_task_config_unavailable"
                        ),
                        detail=failure,
                    )
                elif loop_key not in config_by_loop_key:
                    _record_config_failure(
                        route=route,
                        loop_key=loop_key,
                        reason="native_task_config_route_identity_unavailable",
                        detail={
                            "reason": "native_task_config_route_identity_unavailable"
                        },
                    )

            claimed_entries: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
            for route, loop_key in route_entries:
                route_config = config_by_loop_key.get(loop_key)
                if not route_config:
                    continue
                if scope_error_isolation_active(
                    database,
                    network=network,
                    chain_id=chain_id,
                    protocol=protocol,
                    scope_key=loop_key,
                ):
                    logger.info(
                        "native closed-loop direction %s is isolated; skipping submit",
                        loop_key,
                    )
                    continue
                claimed = closed_loop_consumer.claim_review_before_submit(
                    database,
                    network=network,
                    chain_id=chain_id,
                    protocol=protocol,
                    market_id=market_id,
                    loop_key=loop_key,
                )
                if claimed is None:
                    logger.info(
                        "native closed-loop direction %s is not claimable in this cycle; skipping submit",
                        loop_key,
                    )
                    continue
                claimed_entries.append((route, loop_key, route_config))

            if not claimed_entries:
                continue
            try:
                claimed_task_config = dict(task_config)
                claimed_task_config["routes"] = [
                    route_config
                    for _route, _loop_key, route_config in claimed_entries
                ]
                self._native_template_map()[resolved_group_key] = [
                    route for route, _loop_key, _route_config in claimed_entries
                ]
                submit_group(
                    group_key=resolved_group_key,
                    chain_id=chain_id,
                    protocols=protocols,
                    task_config=claimed_task_config,
                    mode="deep" if self.auto_execute_enabled else "scan",
                )
                newly_submitted += 1
                logger.info(
                    "submitted native closed-loop group %s with %s direction(s)",
                    resolved_group_key,
                    len(claimed_entries),
                )
            except Exception as exc:
                for _route, loop_key, _route_config in claimed_entries:
                    try:
                        closed_loop_consumer.release_review_after_submit_failure(
                            database,
                            network=network,
                            chain_id=chain_id,
                            protocol=protocol,
                            market_id=market_id,
                            loop_key=loop_key,
                        )
                    except Exception:
                        logger.warning(
                            "native closed-loop review release failed for %s",
                            loop_key,
                            exc_info=True,
                        )
                logger.error(
                    "native closed-loop submit failed for %s: %s",
                    resolved_group_key,
                    exc,
                    exc_info=True,
                )

    def _refresh_native_closed_loop_group(
        self,
        *,
        group_key: str,
        chain_id: int | None,
        protocols: list[str],
    ) -> dict[str, Any]:
        """Feed one closed-loop group to the Rust resident worker pool."""
        if chain_id is None:
            return {
                "ok": False,
                "mode": "native_closed_loop_group_refresh",
                "error": "chain_id_required",
            }
        if self._native_group_busy(str(group_key or ""), chain_id):
            return {
                "ok": False,
                "mode": "native_closed_loop_group_refresh",
                "chain_id": chain_id,
                "group_key": str(group_key or ""),
                "error": "closed_loop_group_busy",
                "detail": (
                    "该闭环当前正在复核中；手动复核只允许投递到空闲线程，"
                    "忙碌线程等待当前复核完成即可继续"
                ),
            }
        raw_anchor = str(group_key or "").strip().upper()
        anchor = (raw_anchor.split("|")[0] if "|" in raw_anchor else raw_anchor) or None
        from dex.runtime.dex_opportunity_route_runner import _quote_routes_payload_from_facade

        # Static observation only supplies loop topology/pool identity; the Rust
        # worker still re-quotes live via previewBestPoolDetailed eth_call.
        payload = _quote_routes_payload_from_facade(
            chain_id=chain_id,
            protocols=list(protocols),
            use_static_observation=True,
            anchor=anchor,
        )
        routes = payload.get("routes") or []
        if anchor:
            routes = [
                route
                for route in routes
                if str(
                    (
                        (route.get("routeSymbols") or route.get("symbols") or [])
                        if isinstance(
                            route.get("routeSymbols") or route.get("symbols"),
                            list,
                        )
                        else []
                    )[0]
                    or ""
                ).strip().upper() == anchor
            ]
        if not routes:
            return {
                "ok": False,
                "mode": "native_closed_loop_group_refresh",
                "chain_id": chain_id,
                "group_key": str(group_key or ""),
                "error": "closed_loop_group_not_found",
            }
        self._submit_native_closed_loop_groups(
            payload={"routes": routes},
            chain_id=chain_id,
            protocols=protocols,
        )
        status = native_worker_status(chain_id=chain_id)
        workers = [
            worker
            for worker in (status.get("workers") if isinstance(status.get("workers"), list) else [])
            if isinstance(worker, dict)
            and str(worker.get("group_key") or "").lower() == str(group_key or "").lower()
        ]
        return {
            "ok": True,
            "mode": "native_closed_loop_group_refresh",
            "chain_id": chain_id,
            "group_key": str(group_key or ""),
            "accepted": True,
            "worker": workers[0] if workers else None,
        }

    def _manual_native_closed_loop_review(
        self,
        *,
        route_symbols: list[str],
        route_addresses: list[str] | None,
        chain_id: int | None,
        protocols: list[str],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Move one idle review worker from waiting to its review handler."""
        if chain_id is None:
            return {
                "ok": False,
                "mode": "native_closed_loop_group_manual_review",
                "error": "chain_id_required",
            }
        symbols = [
            str(item).strip().upper()
            for item in (route_symbols or [])
            if str(item).strip()
        ]
        addresses = [
            str(item).strip().lower()
            for item in (route_addresses or [])
            if str(item).strip()
        ]
        if len(symbols) != 4 or symbols[0] != symbols[-1]:
            return {
                "ok": False,
                "mode": "native_closed_loop_group_manual_review",
                "error": "closed_loop_route_unsupported",
            }
        middle = sorted([symbols[1], symbols[2]])
        group_key = f"{symbols[0].lower()}|{middle[0].lower()}|{middle[1].lower()}"
        from dex.runtime.rust_closed_loop import wake_waiting_closed_loop_group

        try:
            transition = wake_waiting_closed_loop_group(
                group_key,
                database_url=self._native_database_url(chain_id),
                dry_run=bool(dry_run),
            )
        except Exception as exc:
            error = _closed_loop_transition_error(exc)
            return {
                "ok": False,
                "mode": "native_closed_loop_group_manual_review",
                "chain_id": chain_id,
                "group_key": group_key,
                "routeSymbols": symbols,
                "error": error,
                "detail": _closed_loop_transition_detail(error),
            }
        return {
            **transition,
            "ok": True,
            "mode": "native_closed_loop_group_manual_review",
            "chain_id": chain_id,
            "group_key": group_key,
            "routeSymbols": symbols,
            "accepted": True,
            "processingAsync": not bool(dry_run),
            "stateReadRequired": True,
        }

    def _manual_native_closed_loop_broadcast(
        self,
        *,
        route_symbols: list[str],
        route_addresses: list[str] | None,
        chain_id: int | None,
        protocols: list[str],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Move one waiting execution task to the broadcast queue."""
        if chain_id is None:
            return {
                "ok": False,
                "mode": "native_closed_loop_group_manual_broadcast",
                "error": "chain_id_required",
            }
        symbols = [
            str(item).strip().upper()
            for item in (route_symbols or [])
            if str(item).strip()
        ]
        if len(symbols) != 4 or symbols[0] != symbols[-1]:
            return {
                "ok": False,
                "mode": "native_closed_loop_group_manual_broadcast",
                "error": "closed_loop_route_unsupported",
            }
        middle = sorted([symbols[1], symbols[2]])
        group_key = f"{symbols[0].lower()}|{middle[0].lower()}|{middle[1].lower()}"
        from dex.runtime.rust_closed_loop import wake_waiting_broadcast

        try:
            transition = wake_waiting_broadcast(
                group_key,
                database_url=self._native_database_url(chain_id),
                dry_run=bool(dry_run),
            )
        except Exception as exc:
            error = _closed_loop_transition_error(exc)
            return {
                "ok": False,
                "mode": "native_closed_loop_group_manual_broadcast",
                "chain_id": chain_id,
                "group_key": group_key,
                "routeSymbols": symbols,
                "error": error,
                "detail": _closed_loop_transition_detail(error),
            }
        return {
            **transition,
            "ok": True,
            "mode": "native_closed_loop_group_manual_broadcast",
            "chain_id": chain_id,
            "group_key": group_key,
            "routeSymbols": symbols,
            "accepted": True,
            "processingAsync": not bool(dry_run),
            "stateReadRequired": True,
        }

    def _start_native_closed_loop_group(
        self,
        *,
        group_key: str,
        chain_id: int | None,
        protocols: list[str],
    ) -> dict[str, Any]:
        from dex.runtime.rust_closed_loop import start_closed_loop_group

        try:
            return start_closed_loop_group(
                group_key,
                database_url=self._native_database_url(chain_id),
            )
        except Exception as exc:
            return {
                "ok": False,
                "mode": "native_closed_loop_group_start",
                "error": str(exc)[:300],
            }

    def _stop_native_closed_loop_group(
        self,
        *,
        group_key: str,
        chain_id: int | None,
        protocols: list[str],
    ) -> dict[str, Any]:
        from dex.runtime.rust_closed_loop import stop_closed_loop_group

        try:
            return stop_closed_loop_group(
                group_key,
                database_url=self._native_database_url(chain_id),
            )
        except Exception as exc:
            return {
                "ok": False,
                "mode": "native_closed_loop_group_stop",
                "error": str(exc)[:300],
            }

    def _record_native_worker_results(self) -> dict[str, Any]:
        if not (
            getattr(self, "_native_rust_closed_loop", False)
            and self.auto_execute_enabled
            and self.record_opportunities
        ):
            return {"recorded": 0, "source": "native_rust_disabled", "error": None}
        try:
            status = native_worker_status(chain_id=self._native_chain_id())
        except Exception as exc:
            return {"recorded": 0, "source": "native_rust_status", "error": str(exc)[:300]}
        workers = status.get("workers") if isinstance(status.get("workers"), list) else []
        if not workers:
            return {"recorded": 0, "source": "native_rust_status", "error": None}
        from dex.runtime import rust_closed_loop

        from dex.runtime.dex_opportunity_helpers import _now_iso

        lock = self._native_recorder_lock_object()
        with lock:
            recorded_at = _now_iso()
            templates_by_group = self._native_template_map()
            fingerprints = self._native_record_fingerprints()
            attempts: list[dict[str, Any]] = []
            newly_recorded = 0
            broadcasts: list[dict[str, Any]] = []
            broadcast_failed = 0
            seen_tx_hashes: set[str] = set()

            def _native_result_sets(
                result: dict[str, Any],
            ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
                route_results = result.get("routes")
                if isinstance(route_results, list) and len(route_results) == len(routes):
                    return [
                        (
                            route,
                            self._merge_group_execution_into_route_result(
                                route_result=route_result,
                                group_result=result,
                                selected=(
                                    bool(route_result.get("directionSelected"))
                                    or len(route_results) == len(routes) == 1
                                ),
                            ),
                        )
                        for route, route_result in zip(routes, route_results)
                    ]
                if not isinstance(route_results, list) and len(routes) == 1:
                    return [(routes[0], result)]
                if isinstance(route_results, list) and len(routes) == 1 and route_results:
                    return [(
                        routes[0],
                        self._merge_group_execution_into_route_result(
                            route_result=route_results[0],
                            group_result=result,
                            selected=True,
                        ),
                    )]
                if len(routes) == 1:
                    return [(
                        routes[0],
                        self._merge_group_execution_into_route_result(
                            route_result=result,
                            group_result=result,
                            selected=True,
                        ),
                    )]
                return []

            for worker in workers:
                if not isinstance(worker, dict):
                    continue
                group_key = str(worker.get("group_key") or "").strip()
                routes = templates_by_group.get(group_key) or []
                if not routes:
                    continue
                observed_at = str(worker.get("updated_at") or _now_iso())
                try:
                    chain_id = int(worker.get("chain_id") or 0)
                except (TypeError, ValueError):
                    chain_id = 0
                last_result = worker.get("last_result")
                result_inputs: list[tuple[dict[str, Any], str]] = []
                if isinstance(last_result, dict):
                    result_inputs.append((last_result, observed_at))
                broadcast_history = worker.get("broadcast_history")
                if isinstance(broadcast_history, list):
                    for history_item in broadcast_history:
                        if not isinstance(history_item, dict):
                            continue
                        broadcast = history_item.get("broadcast")
                        if not isinstance(broadcast, dict):
                            continue
                        tx_hash = str(
                            broadcast.get("txHash")
                            or broadcast.get("transactionHash")
                            or broadcast.get("transaction_hash")
                            or ""
                        ).strip()
                        if not tx_hash:
                            continue
                        history_observed_at = str(
                            history_item.get("observedAt")
                            or history_item.get("updatedAt")
                            or broadcast.get("submittedAt")
                            or broadcast.get("observedAt")
                            or broadcast.get("txTime")
                            or observed_at
                        )
                        result_inputs.append((history_item, history_observed_at))
                failure_history = worker.get("broadcast_failure_history")
                if isinstance(failure_history, list):
                    for history_item in failure_history:
                        if not isinstance(history_item, dict):
                            continue
                        broadcast = history_item.get("broadcast")
                        if not isinstance(broadcast, dict) and not history_item.get("broadcastError"):
                            continue
                        history_observed_at = str(
                            history_item.get("observedAt")
                            or history_item.get("updatedAt")
                            or observed_at
                        )
                        result_inputs.append((history_item, history_observed_at))
                if not result_inputs and (
                    str(worker.get("state") or "").lower() == "error"
                    or (worker.get("last_error") and not isinstance(last_result, dict))
                ):
                    error_result = {
                        "status": "rpc_verification_failed",
                        "error": worker.get("last_error") or "native Rust worker failed",
                    }
                    result_inputs.append((error_result, observed_at))
                for result, result_observed_at in result_inputs:
                    result_sets = _native_result_sets(result)
                    for route, merged_result in result_sets:
                        broadcast = merged_result.get("broadcast")
                        if isinstance(broadcast, dict):
                            merged_tx_hash = str(
                                broadcast.get("txHash")
                                or broadcast.get("transactionHash")
                                or broadcast.get("transaction_hash")
                                or ""
                            ).strip()
                            if merged_tx_hash and merged_tx_hash.lower() in seen_tx_hashes:
                                continue
                            if merged_tx_hash:
                                seen_tx_hashes.add(merged_tx_hash.lower())
                        attempt = rust_closed_loop.build_native_worker_attempt(
                            route=route,
                            result=merged_result,
                            chain_id=chain_id,
                            observed_at=result_observed_at,
                        )
                        if attempt is None:
                            continue
                        fingerprint = rust_closed_loop.native_worker_result_fingerprint(
                            group_key=group_key,
                            updated_at=result_observed_at,
                            result=merged_result,
                            route=route,
                        )
                        if fingerprint in fingerprints:
                            continue
                        fingerprints.add(fingerprint)
                        if _is_broadcast_failure_result(merged_result):
                            audit_record = _broadcast_failure_audit_record(
                                route=route,
                                result=merged_result,
                                group_key=group_key,
                                chain_id=chain_id,
                                observed_at=result_observed_at,
                                recorded_at=recorded_at,
                                fingerprint=fingerprint,
                            )
                            append_broadcast_failure_audit(audit_record)
                        attempts.append(attempt)
                        newly_recorded += 1
                        self._apply_fixed_refresh_evidence_to_route(route, attempt)
                        auto_execute = attempt.get("auto_execute") if isinstance(attempt.get("auto_execute"), dict) else {}
                        if bool(auto_execute.get("executed")):
                            broadcasts.append(auto_execute.get("result") if isinstance(auto_execute.get("result"), dict) else {})
                        elif bool(auto_execute.get("attempted")) and not bool(auto_execute.get("executed")):
                            broadcast_failed += 1
                        if str(auto_execute.get("confirmationStatus") or "").lower() == "failed":
                            broadcast_failed += 1
            recording = {"recorded": 0, "source": "native_rust_closed_loop", "error": None}
            if attempts:
                try:
                    recording = self.record_attempts(attempts) or recording
                except Exception as exc:
                    recording = {
                        "recorded": 0,
                        "source": "native_rust_closed_loop",
                        "error": str(exc)[:300],
                    }
            try:
                from dex.runtime import closed_loop_consumer

                database_by_chain = self._native_database_url()
                for worker in workers:
                    if not isinstance(worker, dict):
                        continue
                    try:
                        worker_chain_id = int(worker.get("chain_id") or 0)
                    except (TypeError, ValueError):
                        continue
                    if worker_chain_id <= 0:
                        continue
                    candidate = self._native_database_url(worker_chain_id)
                    if candidate:
                        database_by_chain = candidate
                        break
                closed_loop_consumer.record_state_from_attempts(database_by_chain, attempts)
            except Exception as exc:
                logger.warning(
                    "closed-loop runtime state sync failed: %s",
                    exc,
                    exc_info=True,
                )
            recorded = int(recording.get("recorded") or 0)
            auto_executed = len(broadcasts)
            with self._lock:
                self._recorded_attempt_count += recorded
                self._closed_loop_review_count += newly_recorded
                if auto_executed:
                    self._auto_execute_count += auto_executed
                    self._last_auto_execute_at = _now_iso()
                self._last_recording = {
                    "candidate_count": newly_recorded,
                    "review_count": newly_recorded,
                    "recorded": recorded,
                    "source": "native_rust_closed_loop",
                    "autoExecuteEnabled": bool(self.auto_execute_enabled),
                    "autoExecuted": auto_executed,
                    "autoExecuteFailed": broadcast_failed,
                    "error": recording.get("error"),
                }
                for worker in workers:
                    try:
                        chain_id = int(worker.get("chain_id") or 0)
                    except (TypeError, ValueError):
                        chain_id = None
                    if chain_id is None:
                        continue
                    stats = self._get_chain_stats(chain_id)
                    if stats is not None:
                        stats["recorded_attempt_count"] += recorded
                        stats["closed_loop_review_count"] += newly_recorded
                        if auto_executed:
                            stats["auto_execute_count"] += auto_executed
                            stats["last_auto_execute_at"] = _now_iso()
                        stats["last_recording"] = self._last_recording
            for broadcast in broadcasts:
                chain_ids = {
                    int(worker.get("chain_id") or 0)
                    for worker in workers
                    if worker.get("chain_id") is not None
                }
                for chain_id in chain_ids:
                    self._record_session_executed(broadcast, auto=True, chain_id=chain_id)
            return recording

    def _merge_group_execution_into_route_result(
        self,
        *,
        route_result: dict[str, Any],
        group_result: dict[str, Any],
        selected: bool | None = None,
    ) -> dict[str, Any]:
        """Attach the group-level broadcast payload only to the selected route.

        Rust executes the broadcast once per group after selecting the best
        route. Only that direction may carry the broadcast and selected amount;
        the opposite direction keeps its own no-profit/numeric evidence and
        must not claim the same tx.

        When a broadcast happened for another direction, the unselected route is
        marked as broadcast_not_selected instead of a missing broadcast, so the
        recorder does not invent a broadcast_result_missing error.
        """
        merged = dict(route_result or {})
        if selected:
            for key in (
                "broadcast",
                "broadcastError",
                "broadcastExecuted",
                "deferBroadcast",
                "executionQueued",
                "executionQueueAt",
                "selectedAmountRaw",
                "selectedProfitRaw",
                "minProfitRaw",
            ):
                if key in group_result:
                    merged[key] = group_result[key]
            if "token" in group_result:
                merged["token"] = group_result["token"]
            if (
                merged.get("status") in {None, "route_error"}
                and group_result.get("status") == "profitable_amount_found"
            ):
                merged["status"] = group_result["status"]
        elif isinstance(group_result.get("broadcast"), dict) or bool(
            group_result.get("broadcastError")
        ):
            merged["broadcastNotSelected"] = True
            selected_route_payload = group_result.get("selectedRoutePayload")
            if isinstance(selected_route_payload, dict):
                merged["selectedRouteSymbols"] = selected_route_payload.get(
                    "routeSymbols"
                )
                merged["selectedRouteAddresses"] = selected_route_payload.get(
                    "routeAddresses"
                )
        return merged

    def _rust_worker_status(self) -> dict[str, Any]:
        if not self._native_rust_closed_loop:
            return {
                "enabled": False,
                "engine": "python_fallback",
                "executionBackend": "python",
                "workers": [],
                "summary": {},
            }
        try:
            result = native_worker_status(
                include_payloads=False,
                chain_id=self._native_chain_id(),
            )
            if not isinstance(result, dict):
                result = {}
            result["executionBackend"] = "rust"
            return result
        except Exception as exc:
            return {
                "enabled": True,
                "engine": "rust_native_closed_loop",
                "executionBackend": "rust",
                "error": str(exc)[:300],
                "workers": [],
                "summary": {},
            }
