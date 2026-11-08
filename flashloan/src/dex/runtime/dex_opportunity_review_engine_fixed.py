from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import os
from typing import Any, Callable

from dex.runtime.dex_opportunity_helpers import (
    _liquidity_cap_raw,
    _network_for_chain_id,
    _now_iso,
    _pool_map,
    _structured_dex_error,
)
from dex.runtime.dex_opportunity_auto_execute import _order_routes_for_review
from dex.runtime.dex_opportunity_route_runner import (
    _quote_routes_payload_from_facade,
)
from dex.runtime.dex_opportunity_scanner_rust_mixin import DexOpportunityNativeRustMixin


def _first_number(*values: Any) -> float | None:
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        return number
    return None


class DexOpportunityFixedRefreshMixin(DexOpportunityNativeRustMixin):
    def _fixed_refresh_group(self, task) -> dict[str, Any]:
        anchor = str(task.group_key or "").strip().upper()
        payload = _quote_routes_payload_from_facade(
            chain_id=task.chain_id,
            protocols=list(task.protocols),
            use_static_observation=True,
            anchor=anchor,
        )
        if task.mode in {"deep", "broadcast"}:
            from market.binance_market.dex_route_test import (
                estimate_route_live_quote,
                recommended_route_test_amount,
                run_official_v4_arb_route_test,
            )

            routes = _order_routes_for_review(payload.get("routes") or [])
            refreshed = []
            refresh_attempt_pairs = []
            manual_review_count = 0
            auto_broadcast_count = 0
            auto_broadcast_failed = 0
            auto_broadcast_errors: list[str] = []
            per_route_recorded = 0
            per_route_error: str | None = None

            def _consume_result(work: dict[str, Any]) -> None:
                nonlocal manual_review_count, auto_broadcast_count, auto_broadcast_failed, auto_broadcast_errors
                nonlocal per_route_recorded, per_route_error
                route = work["route"]
                refreshed.append(route)
                if work["manual_reviewed"]:
                    manual_review_count += 1
                if work["broadcast_executed"]:
                    auto_broadcast_count += 1
                    self._record_session_executed(work["broadcast_payload"], auto=True, chain_id=task.chain_id)
                if work["broadcast_failed"]:
                    auto_broadcast_failed += 1
                    auto_broadcast_errors.extend(work["broadcast_errors"])
                attempt = work.get("attempt")
                if attempt is not None:
                    refresh_attempt_pairs.append((route, attempt))
                if attempt is not None and self.record_opportunities:
                    broadcast_payload = work.get("broadcast_payload")
                    if isinstance(broadcast_payload, dict):
                        self._attach_auto_execute_to_attempt(attempt, route, broadcast_payload)
                    elif work.get("manual_reviewed"):
                        attempt["auto_execute"] = {
                            "skipped": True,
                            "reason": "manual_review_not_profitable",
                        }
                    try:
                        route_recording = self.record_attempts([attempt]) or {}
                    except Exception as exc:
                        route_recording = {
                            "recorded": 0,
                            "source": "closed_loop_deep_review",
                            "error": str(exc)[:300],
                        }
                    per_route_recorded += int(route_recording.get("recorded") or 0)
                    if route_recording.get("error"):
                        per_route_error = str(route_recording.get("error") or "")[:300]
                    with self._lock:
                        self._recorded_attempt_count += int(route_recording.get("recorded") or 0)
                        self._closed_loop_review_count += 1
                        self._last_recording = {
                            "candidate_count": 1,
                            "review_count": 1,
                            "recorded": int(route_recording.get("recorded") or 0),
                            "source": "closed_loop_deep_review",
                            "error": route_recording.get("error"),
                        }
                        if task.chain_id is not None:
                            chain_stats = self._get_chain_stats(task.chain_id)
                            if chain_stats is not None:
                                chain_stats["recorded_attempt_count"] += int(route_recording.get("recorded") or 0)
                                chain_stats["closed_loop_review_count"] += 1
                                chain_stats["last_recording"] = {
                                    "candidate_count": 1,
                                    "review_count": 1,
                                    "recorded": int(route_recording.get("recorded") or 0),
                                    "source": "closed_loop_deep_review",
                                    "error": route_recording.get("error"),
                                }

            for route in routes:
                _consume_result(
                    self._closed_loop_review_one_route(
                        route=route,
                        chain_id=task.chain_id,
                        protocols=list(task.protocols),
                        recommended_route_test_amount=recommended_route_test_amount,
                        run_official_v4_arb_route_test=run_official_v4_arb_route_test,
                        estimate_route_live_quote=estimate_route_live_quote,
                        anchor=anchor,
                    )
                )
            payload["routes"] = refreshed
            if self.auto_execute_enabled and manual_review_count:
                payload["auto_execute"] = {
                    "autoExecuteEnabled": True,
                    "manualReviewCount": manual_review_count,
                    "autoExecuted": auto_broadcast_count,
                    "autoExecuteSkipped": manual_review_count - auto_broadcast_count - auto_broadcast_failed,
                    "autoExecuteFailed": auto_broadcast_failed,
                    "autoExecuteErrors": auto_broadcast_errors,
                }
                if auto_broadcast_count:
                    with self._lock:
                        self._auto_execute_count += auto_broadcast_count
                        self._last_auto_execute_at = _now_iso()
                        if task.chain_id is not None:
                            chain_stats = self._get_chain_stats(task.chain_id)
                            if chain_stats is not None:
                                chain_stats["auto_execute_count"] += auto_broadcast_count
                                chain_stats["last_auto_execute_at"] = _now_iso()
                for route in refreshed:
                    broadcast_payload = route.get("__manualBroadcast")
                    auto_execute_payload = {
                        "observed_at": _now_iso(),
                        "route_symbols": route.get("routeSymbols") or [],
                        "broadcast": broadcast_payload is not None,
                        "attempted": isinstance(broadcast_payload, dict),
                    }
                    if isinstance(broadcast_payload, dict):
                        auto_execute_payload.update({
                            "amount": broadcast_payload.get("amount"),
                            "result": broadcast_payload,
                            "executed": bool(broadcast_payload.get("txHash")),
                        })
                    else:
                        auto_execute_payload["skipped"] = True
                        auto_execute_payload["reason"] = "manual_review_not_profitable"
                    route["auto_execute"] = auto_execute_payload
                    for _, attempt in refresh_attempt_pairs:
                        if attempt.get("route_path") == route.get("routeSymbols"):
                            attempt["auto_execute"] = auto_execute_payload
                            self._apply_fixed_refresh_evidence_to_route(route, attempt)
            attempts_to_record = [] if self.record_opportunities else [attempt for _, attempt in refresh_attempt_pairs]
            recorded_result = {
                "recorded": per_route_recorded if self.record_opportunities else 0,
                "source": "closed_loop_deep_review",
                "error": per_route_error,
            }
            if self.record_opportunities and attempts_to_record:
                try:
                    recorded_result = self.record_attempts(attempts_to_record) or {}
                except Exception as exc:
                    recorded_result = {
                        "recorded": 0,
                        "source": "closed_loop_deep_review",
                        "error": str(exc)[:300],
                    }
            recording = {
                "candidate_count": len(refresh_attempt_pairs),
                "review_count": len(refresh_attempt_pairs),
                "recorded": int(recorded_result.get("recorded") or 0),
                "source": "closed_loop_deep_review",
                "error": recorded_result.get("error"),
                "autoExecuteEnabled": self.auto_execute_enabled,
                "autoExecuted": auto_broadcast_count,
                "autoExecuteSkipped": max(0, manual_review_count - auto_broadcast_count - auto_broadcast_failed),
                "autoExecuteFailed": auto_broadcast_failed,
                "autoExecuteErrors": auto_broadcast_errors,
            }
            with self._lock:
                self._last_recording = recording
                self._closed_loop_scan_count += 1
                if not self.record_opportunities:
                    self._recorded_attempt_count += int(recording.get("recorded") or 0)
                    self._closed_loop_review_count += len(refresh_attempt_pairs)
                if task.chain_id is not None:
                    chain_stats = self._get_chain_stats(task.chain_id)
                    if chain_stats is not None:
                        chain_stats["last_recording"] = recording
                        chain_stats["closed_loop_scan_count"] += 1
                        if not self.record_opportunities:
                            chain_stats["recorded_attempt_count"] += int(recording.get("recorded") or 0)
                            chain_stats["closed_loop_review_count"] += len(refresh_attempt_pairs)
            payload["recording"] = recording
        self._merge_fixed_refresh_into_snapshot(task, payload)
        return {
            "ok": True,
            "mode": task.mode,
            "anchor": anchor,
            "routeCount": len(payload.get("routes") or []),
            "routes": payload.get("routes") or [],
            "auto_execute": payload.get("auto_execute"),
            "recording": payload.get("recording"),
        }

    def _manual_pool_depth_max_human(
        self,
        *,
        route: dict[str, Any],
        route_symbols: list[str],
        chain_id: int | None,
    ) -> tuple[float | None, str | None, int | None]:
        route_addresses = [
            str(item).strip().lower()
            for item in (route.get("routeAddresses") or [])
            if str(item).strip()
        ]
        borrow_address = route_addresses[0] if route_addresses else None
        if not borrow_address:
            return None, None, None
        try:
            from market.binance_market.dex_route_test import _borrow_token_decimals_from_registry

            borrow_decimals = _borrow_token_decimals_from_registry(
                chain_id=int(chain_id or 0),
                borrow_address=borrow_address,
            )
        except Exception:
            borrow_decimals = None
        if borrow_decimals is None or borrow_decimals <= 0:
            return None, None, None
        pools = _pool_map(route)
        input_symbol = route_symbols[0] if route_symbols else None
        if not pools or not input_symbol:
            return None, None, borrow_decimals
        cap_raw, cap_basis = _liquidity_cap_raw(pools, input_symbol, borrow_decimals)
        if not cap_raw or cap_raw <= 0:
            return None, cap_basis, borrow_decimals
        return float(cap_raw) / (10 ** int(borrow_decimals)), cap_basis, borrow_decimals

    @staticmethod
    def _manual_profit_amount(payload: dict[str, Any] | None) -> float | None:
        if not isinstance(payload, dict):
            return None
        analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
        for key in (
            "profit",
            "profitHuman",
            "netProfitAmount",
            "referenceNetProfitAmount",
            "pureProfitAmount",
            "referencePureProfitAmount",
        ):
            number = _first_number(payload.get(key), analysis.get(key))
            if number is not None:
                return number
        evidence = payload.get("live_onchain_evidence")
        if not isinstance(evidence, dict):
            evidence = payload.get("evidence")
        if isinstance(evidence, dict):
            costs = evidence.get("costModel") if isinstance(evidence.get("costModel"), dict) else {}
            number = _first_number(
                costs.get("netProfitAmount"),
                evidence.get("referenceNetProfitAmount"),
            )
            if number is not None:
                return number
        return None

    def _run_manual_route_review_and_broadcast(
        self,
        *,
        route: dict[str, Any],
        route_symbols: list[str],
        amount: float,
        chain_id: int | None,
        protocols: list[str],
        run_official_v4_arb_route_test: Callable[..., dict[str, Any]],
    ) -> dict[str, Any]:
        route_addresses = [
            str(item).strip()
            for item in (route.get("routeAddresses") or [])
            if str(item).strip()
        ]
        max_human, cap_basis, borrow_decimals = self._manual_pool_depth_max_human(
            route=route,
            route_symbols=route_symbols,
            chain_id=chain_id,
        )
        min_human = 0.001
        size_probe: dict[str, Any] = {
            "status": "official_v4_manual_review_size_probe",
            "searchStatus": "pending",
            "requestedAmount": float(amount),
            "minProbeAmountHuman": "0.001 borrow token unit",
            "maxProbeAmountHuman": max_human,
            "poolDepthCapBasis": cap_basis,
            "borrowTokenDecimals": borrow_decimals,
            "attempts": [],
        }
        if max_human is not None and max_human < min_human:
            size_probe["searchStatus"] = "pool_depth_below_minimum"
            preflight = {
                "ok": False,
                "error": "official_arb_pool_depth_below_minimum",
                "detail": "50 percent pool depth estimate is below the 0.001 borrow-token minimum; broadcast skipped",
                "quoteStatus": "pool_insufficient_for_profitable_execution",
                "evidenceStatus": "pool_insufficient_for_profitable_execution",
                "executionTrust": "pool_insufficient_for_profitable_execution",
                "routeSymbols": route_symbols,
                "routeAddresses": route_addresses,
                "amount": amount,
                "sizeProbe": size_probe,
            }
            return {"preflight": preflight, "broadcast": None, "result": preflight, "size_probe": size_probe}

        start_amount = float(amount)
        if max_human is not None and max_human > 0:
            start_amount = min(start_amount, float(max_human))
        if start_amount < min_human:
            start_amount = min_human
        if max_human is None:
            safety_max_human = max(start_amount, min_human) * 64.0
            max_human = safety_max_human
            size_probe["maxProbeAmountHuman"] = safety_max_human
            size_probe["maxBasis"] = "safety_max_64x_initial"
        max_probe_attempts = 24
        try:
            max_probe_attempts = max(4, min(64, int(os.getenv("DEX_V4_MANUAL_REVIEW_PROBE_ATTEMPTS", "24") or 24)))
        except (TypeError, ValueError):
            max_probe_attempts = 24
        size_probe["maxProbeAttempts"] = max_probe_attempts

        def _record_probe(candidate_amount: float) -> dict[str, Any]:
            payload = run_official_v4_arb_route_test(
                route_symbols=route_symbols,
                amount=candidate_amount,
                broadcast=False,
                chain_id=chain_id,
                route_addresses=route_addresses or None,
            )
            if not isinstance(payload, dict):
                payload = {"ok": False, "error": "official_arb_manual_review_preflight_non_dict"}
            size_probe["attempts"].append(
                {
                    "amount": candidate_amount,
                    "ok": bool(payload.get("ok")),
                    "profitable": self._manual_review_is_profitable(payload),
                    "profit": self._manual_profit_amount(payload),
                    "quoteStatus": payload.get("quoteStatus") if isinstance(payload, dict) else None,
                }
            )
            return payload

        def _hard_failure(payload: dict[str, Any] | None) -> str | None:
            if not isinstance(payload, dict) or payload.get("ok") is True:
                return None
            status = str(
                payload.get("quoteStatus")
                or payload.get("evidenceStatus")
                or payload.get("error")
                or ""
            ).lower()
            if any(
                marker in status
                for marker in (
                    "rpc_verification_failed",
                    "rpc_verification_timeout",
                    "rpc_failed",
                    "rpc_timeout",
                    "rpc_unavailable",
                    "live_quote_disabled",
                    "missing_configuration",
                    "official_arb_broadcast_config_missing",
                    "official_v4_readonly_preview_unavailable",
                )
            ):
                return str(payload.get("detail") or payload.get("error") or status)[:300]
            return None

        preflight = _record_probe(start_amount)
        initial_hard_failure = _hard_failure(preflight)
        if initial_hard_failure:
            size_probe["searchStatus"] = "preflight_failed"
            size_probe["failureReason"] = initial_hard_failure
            return {"preflight": preflight, "broadcast": None, "result": preflight, "size_probe": size_probe}

        selected_amount: float | None = None
        selected_preflight: dict[str, Any] | None = None
        selected_profit = None
        base_amount: float | None = None
        lower_amount: float | None = None
        upper_amount: float | None = None
        has_upper_amount = False

        if self._manual_review_is_profitable(preflight):
            selected_amount = start_amount
            selected_preflight = preflight
            selected_profit = self._manual_profit_amount(preflight)
            base_amount = start_amount
            lower_amount = max(min_human, start_amount / 2.0)
            candidate = min(max_human, base_amount * 2.0)
            while len(size_probe["attempts"]) < max_probe_attempts and candidate > base_amount:
                payload = _record_probe(candidate)
                hard_failure = _hard_failure(payload)
                if hard_failure:
                    size_probe["searchStatus"] = "preflight_failed"
                    size_probe["failureReason"] = hard_failure
                    return {
                        "preflight": selected_preflight,
                        "broadcast": None,
                        "result": selected_preflight,
                        "size_probe": size_probe,
                    }
                if self._manual_review_is_profitable(payload):
                    payload_profit = self._manual_profit_amount(payload)
                    if payload_profit is not None and (
                        selected_profit is None or payload_profit > selected_profit
                    ):
                        selected_amount = candidate
                        selected_preflight = payload
                        selected_profit = payload_profit
                        base_amount = candidate
                        lower_amount = max(min_human, candidate / 2.0)
                    else:
                        upper_amount = candidate
                        # 涓庡叡浜嚜閫傚簲闃舵涓€鑷达細鏇村ぇ鐨勯噺鍗充娇浠嶇泩鍒╋紝鍙鍒╂鼎鏈?                        # 瓒呰繃褰撳墠鏈€浼橈紝灏变互瀹冧綔涓轰笂鐣岃繘鍏ヤ腑鐐圭粏鍖栥€?                        upper_amount = candidate
                        has_upper_amount = True
                        break
                else:
                    upper_amount = candidate
                    has_upper_amount = True
                    break
                candidate = min(max_human, candidate * 2.0)
            if not has_upper_amount and selected_amount is not None:
                upper_amount = min(max_human, base_amount * 2.0) if base_amount is not None else None
                if upper_amount is not None and upper_amount > base_amount:
                    has_upper_amount = True
        else:
            upper_amount = start_amount
            candidate = max(min_human, start_amount / 2.0)
            while (
                len(size_probe["attempts"]) < max_probe_attempts
                and candidate >= min_human - 1e-12
                and candidate != upper_amount
            ):
                payload = _record_probe(candidate)
                hard_failure = _hard_failure(payload)
                if hard_failure:
                    size_probe["searchStatus"] = "preflight_failed"
                    size_probe["failureReason"] = hard_failure
                    return {"preflight": preflight, "broadcast": None, "result": preflight, "size_probe": size_probe}
                if self._manual_review_is_profitable(payload):
                    selected_amount = candidate
                    selected_preflight = payload
                    selected_profit = self._manual_profit_amount(payload)
                    base_amount = candidate
                    lower_amount = max(min_human, candidate / 2.0)
                    has_upper_amount = True
                    break
                upper_amount = candidate
                next_candidate = max(min_human, candidate / 2.0)
                if next_candidate == candidate:
                    break
                candidate = next_candidate

        if selected_amount is None or selected_preflight is None:
            size_probe["searchStatus"] = "not_profitable_after_costs"
            return {"preflight": preflight, "broadcast": None, "result": preflight, "size_probe": size_probe}

        if selected_profit is None:
            size_probe["searchStatus"] = "missing_concrete_pnl"
            size_probe["failureReason"] = (
                "concrete P&L could not be resolved; 0.0 is not an "
                "acceptable review result and broadcast is not justified"
            )
            missing_result = deepcopy(selected_preflight)
            missing_result.update(
                {
                    "ok": False,
                    "quoteStatus": "review_failed_missing_concrete_pnl",
                    "evidenceStatus": "review_failed_missing_concrete_pnl",
                    "executionTrust": "review_failed_missing_concrete_pnl",
                    "error": size_probe["failureReason"],
                }
            )
            return {
                "preflight": selected_preflight,
                "broadcast": None,
                "result": missing_result,
                "size_probe": size_probe,
            }

        if has_upper_amount and upper_amount is not None and base_amount is not None and lower_amount is not None:
            while (
                len(size_probe["attempts"]) < max_probe_attempts
                and upper_amount > base_amount + 1e-12
                and lower_amount < base_amount - 1e-12
                and upper_amount > lower_amount
            ):
                left_mid = lower_amount + (base_amount - lower_amount) / 2.0
                right_mid = base_amount + (upper_amount - base_amount) / 2.0
                if (
                    left_mid <= lower_amount
                    or left_mid >= base_amount
                    or right_mid <= base_amount
                    or right_mid >= upper_amount
                ):
                    break
                left_payload = _record_probe(left_mid)
                right_payload = _record_probe(right_mid)
                left_hard = _hard_failure(left_payload)
                right_hard = _hard_failure(right_payload)
                if left_hard or right_hard:
                    size_probe["searchStatus"] = "preflight_failed"
                    size_probe["failureReason"] = left_hard or right_hard
                    break
                left_ok = self._manual_review_is_profitable(left_payload)
                right_ok = self._manual_review_is_profitable(right_payload)
                if not left_ok and not right_ok:
                    break
                left_profit = self._manual_profit_amount(left_payload)
                right_profit = self._manual_profit_amount(right_payload)
                left_improves = left_ok and left_profit is not None and (
                    selected_profit is None or left_profit > selected_profit
                )
                right_improves = right_ok and right_profit is not None and (
                    selected_profit is None or right_profit > selected_profit
                )
                if not left_improves and not right_improves:
                    break
                old_base = base_amount
                if left_ok and right_ok:
                    if (left_profit or 0.0) >= (right_profit or 0.0):
                        if left_improves:
                            selected_amount = left_mid
                            selected_preflight = left_payload
                            selected_profit = left_profit
                        upper_amount = old_base
                        base_amount = left_mid
                    else:
                        if right_improves:
                            selected_amount = right_mid
                            selected_preflight = right_payload
                            selected_profit = right_profit
                        lower_amount = old_base
                        base_amount = right_mid
                elif left_ok:
                    if left_improves:
                        selected_amount = left_mid
                        selected_preflight = left_payload
                        selected_profit = left_profit
                    upper_amount = old_base
                    base_amount = left_mid
                else:
                    if right_improves:
                        selected_amount = right_mid
                        selected_preflight = right_payload
                        selected_profit = right_profit
                    lower_amount = old_base
                    base_amount = right_mid

        size_probe["searchStatus"] = "profitable_amount_found"
        size_probe["selectedProfitableAmount"] = selected_amount
        size_probe["selectedProfitAmount"] = selected_profit
        size_probe["baseAmount"] = base_amount
        size_probe["lowerAmount"] = lower_amount
        size_probe["upperAmount"] = upper_amount
        size_probe["hasUpperBound"] = has_upper_amount
        if upper_amount is not None:
            size_probe["firstFailureAmount"] = upper_amount
        broadcast_amount = selected_preflight.get("bestAmount") or selected_amount
        amount_is_best = bool(selected_preflight.get("bestAmountConfirmed"))
        broadcast = run_official_v4_arb_route_test(
            route_symbols=route_symbols,
            amount=broadcast_amount,
            broadcast=True,
            chain_id=chain_id,
            route_addresses=route_addresses or None,
            amount_is_best=amount_is_best,
        )
        if not isinstance(broadcast, dict):
            broadcast = {"ok": False, "error": "official_arb_auto_broadcast_failed"}
        return {
            "preflight": selected_preflight,
            "broadcast": broadcast,
            "result": broadcast,
            "size_probe": size_probe,
        }

    @staticmethod
    def _manual_review_is_profitable(payload: dict[str, Any] | None) -> bool:
        """Return whether a route review is positive and executable.

        A signed positive estimate is not executable by itself: the same
        payload must also have completed a successful on-chain review.  This
        prevents an offline SQLite reference or static positive profit from
        being sent to the broadcast path when the live preview failed.
        """
        if not isinstance(payload, dict):
            return False
        if payload.get("ok") is not True:
            return False
        analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
        for key in (
            "profit",
            "profitHuman",
            "netProfitAmount",
            "referenceNetProfitAmount",
            "pureProfitAmount",
            "referencePureProfitAmount",
        ):
            number = _first_number(payload.get(key), analysis.get(key))
            if number is not None:
                return number > 0
        evidence = payload.get("live_onchain_evidence")
        if not isinstance(evidence, dict):
            evidence = payload.get("evidence")
        if isinstance(evidence, dict):
            costs = evidence.get("costModel") if isinstance(evidence.get("costModel"), dict) else {}
            number = _first_number(
                costs.get("netProfitAmount"),
                evidence.get("referenceNetProfitAmount"),
            )
            if number is not None:
                return number > 0
        return False

    def _closed_loop_review_one_route(
        self,
        *,
        route: dict[str, Any],
        chain_id: int | None,
        protocols: list[str],
        recommended_route_test_amount: Callable[..., float | None],
        run_official_v4_arb_route_test: Callable[..., dict[str, Any]],
        estimate_route_live_quote: Callable[..., dict[str, Any]],
        anchor: str,
    ) -> dict[str, Any]:
        route_symbols = [str(item) for item in (route.get("routeSymbols") or []) if str(item).strip()]
        result: dict[str, Any] = {
            "route": route,
            "attempt": None,
            "manual_reviewed": False,
            "broadcast_executed": False,
            "broadcast_failed": False,
            "broadcast_payload": None,
            "broadcast_errors": [],
            "result_payload": None,
        }
        if not route_symbols:
            return result
        try:
            amount = recommended_route_test_amount(
                route_symbols=route_symbols,
                chain_id=chain_id,
                protocols=protocols,
            )
            if amount is None:
                return result
            if self.auto_execute_enabled:
                manual_result = self._run_manual_route_review_and_broadcast(
                    route=route,
                    route_symbols=route_symbols,
                    amount=amount,
                    chain_id=chain_id,
                    protocols=protocols,
                    run_official_v4_arb_route_test=run_official_v4_arb_route_test,
                )
                route["__manualPreflight"] = manual_result["preflight"]
                route["__manualBroadcast"] = manual_result["broadcast"]
                result["manual_reviewed"] = True
                result["result_payload"] = manual_result["result"]
                if isinstance(result["result_payload"], dict) and manual_result.get("size_probe"):
                    result["result_payload"]["sizeProbe"] = deepcopy(manual_result["size_probe"])
                broadcast_payload = manual_result["broadcast"]
                if broadcast_payload is not None:
                    if broadcast_payload.get("txHash"):
                        result["broadcast_executed"] = True
                        result["broadcast_payload"] = broadcast_payload
                    else:
                        result["broadcast_failed"] = True
                        result["broadcast_errors"] = [
                            str(broadcast_payload.get("error") or "official_arb_auto_broadcast_failed")[:300]
                        ]
            else:
                result["result_payload"] = estimate_route_live_quote(
                    chain_id=chain_id,
                    protocols=protocols,
                    route_symbols=route_symbols,
                    amount=amount,
                    route=route,
                )
            if isinstance(result["result_payload"], dict):
                route["__liveEstimate"] = result["result_payload"]
                attempt = self._fixed_refresh_attempt(route, chain_id=chain_id, observed_at=_now_iso())
                if attempt is not None:
                    self._apply_fixed_refresh_evidence_to_route(route, attempt)
                    result["attempt"] = attempt
        except Exception as exc:
            detail = str(exc)[:300]
            route["__liveEstimate"] = {
                "ok": False,
                "error": detail,
                "quoteStatus": "live_quote_failed",
                "errorStack": _structured_dex_error(
                    detail,
                    flow="closed_loop_refresh",
                    node_id=anchor,
                ),
            }
            attempt = self._fixed_refresh_attempt(route, chain_id=chain_id, observed_at=_now_iso())
            if attempt is not None:
                self._apply_fixed_refresh_evidence_to_route(route, attempt)
                result["attempt"] = attempt
        return result

    def _fixed_refresh_attempt(self, route: dict[str, Any], *, chain_id: int | None, observed_at: str) -> dict[str, Any] | None:
        estimate = route.get("__liveEstimate")
        if not isinstance(estimate, dict):
            return None
        route_symbols = [str(item) for item in (estimate.get("routeSymbols") or route.get("routeSymbols") or []) if str(item).strip()]
        if not route_symbols:
            return None
        route_addresses = [
            str(item).strip().lower()
            for item in (estimate.get("routeAddresses") or route.get("routeAddresses") or [])
            if str(item).strip()
        ]
        from market.binance_market.dex_route_helpers import (
            normalize_route_symbols_from_addresses,
        )

        if len(route_addresses) == len(route_symbols):
            route_symbols = normalize_route_symbols_from_addresses(
                route_symbols=route_symbols,
                route_addresses=route_addresses,
                chain_id=chain_id,
                route=route,
            )
        quote_status = str(estimate.get("quoteStatus") or "").strip().lower()
        execution_trust = str(estimate.get("executionTrust") or "").strip().lower()
        if not quote_status:
            quote_status = str(estimate.get("evidenceStatus") or "").strip().lower()
        if not quote_status:
            quote_status = "live_quote_failed" if not estimate.get("ok") else "static_reference_pending_live_review"
        if not execution_trust:
            execution_trust = quote_status
        failure_detail = ""
        if not estimate.get("ok"):
            failure_detail = str(estimate.get("detail") or estimate.get("error") or "").strip()[:300]
        blockers: list[str] = []
        if failure_detail and failure_detail not in blockers:
            blockers.append(failure_detail)
        error_stack: list[dict[str, Any]] = []
        if not estimate.get("ok") and not error_stack:
            error_stack = [
                {
                    "module": "DEX",
                    "code": 3007,
                    "message": failure_detail or "on-chain review did not complete",
                }
            ]
        source_evidence = estimate.get("evidence") if isinstance(estimate.get("evidence"), dict) else {}
        size_probe = estimate.get("sizeProbe") if isinstance(estimate.get("sizeProbe"), dict) else None
        source_costs = source_evidence.get("costModel") if isinstance(source_evidence.get("costModel"), dict) else {}
        analysis = estimate.get("analysis") if isinstance(estimate.get("analysis"), dict) else {}
        source_best_amount = _first_number(
            source_costs.get("selectedBorrowInputAmount"),
            source_costs.get("borrowInputAmount"),
            estimate.get("bestAmount"),
            source_evidence.get("bestAmount"),
        )
        best_amount_confirmed = bool(
            estimate.get("bestAmountConfirmed")
            or (estimate.get("analysis") or {}).get("bestAmountConfirmed")
            or (estimate.get("bestAmountCalculation") or {}).get("bestAmountConfirmed")
        )
        best_amount_missing = bool(
            estimate.get("bestAmountMissing") is True
            or (estimate.get("analysis") or {}).get("bestAmountMissing") is True
            or (estimate.get("bestAmountCalculation") or {}).get("bestAmountMissing") is True
            or estimate.get("bestAmount") in (None, "")
            or not best_amount_confirmed
        )
        confirmed_best_amount = (
            source_best_amount
            if best_amount_confirmed
            and source_best_amount is not None
            and source_best_amount > 0
            else None
        )
        reference_probe_amount = (
            source_costs.get("borrowInputAmount")
            or source_costs.get("requestedBorrowInputAmount")
            or source_costs.get("initialProbeAmount")
            or (source_best_amount if source_best_amount and source_best_amount > 0 else None)
        )
        source_net_profit = _first_number(
            analysis.get("referenceNetProfitAmount"),
            analysis.get("netProfitAmount"),
            estimate.get("referenceNetProfitAmount"),
            estimate.get("profitHuman"),
            estimate.get("profit"),
            source_costs.get("netProfitAmount"),
            source_evidence.get("referenceNetProfitAmount"),
        )
        source_net_bps = _first_number(
            analysis.get("referenceNetReturnBps"),
            analysis.get("netProfitBps"),
            estimate.get("referenceNetReturnBps"),
            source_costs.get("netProfitAmountBps"),
            source_costs.get("netProfitBps"),
        )
        source_final_output = _first_number(
            analysis.get("referenceFinalOutputAmount"),
            source_costs.get("finalOutputAmount"),
            estimate.get("outAmount"),
            estimate.get("outAmountHuman"),
        )
        source_final_symbol = (
            analysis.get("referenceFinalOutputSymbol")
            or source_costs.get("finalOutputSymbol")
            or (route_symbols[0] if route_symbols else None)
        )
        source_profit_source = "native_rust_contract_preview"
        if source_net_profit is None and reference_probe_amount not in (None, 0):
            from market.binance_market.dex_route_test import (
                _offline_v4_route_sqlite_pnl_reference,
                estimate_fixed_amount_pnl_quick,
            )

            reference_pnl: dict[str, Any] = {}
            try:
                reference_pnl = _offline_v4_route_sqlite_pnl_reference(
                    chain_id=chain_id,
                    route_symbols=route_symbols,
                    route_addresses=route_addresses,
                    amount=float(reference_probe_amount),
                )
            except Exception:
                reference_pnl = {}
            if not isinstance(reference_pnl, dict) or reference_pnl.get("referenceNetProfitAmount") is None:
                try:
                    reference_pnl = estimate_fixed_amount_pnl_quick(
                        chain_id=chain_id,
                        route_symbols=route_symbols,
                        route_addresses=route_addresses,
                        amount=float(reference_probe_amount),
                        protocols=["uniswap_v4"],
                        prefer_sqlite_snapshot=True,
                    )
                except Exception:
                    reference_pnl = {}
            if isinstance(reference_pnl, dict) and reference_pnl.get("referenceNetProfitAmount") is not None:
                source_net_profit = float(reference_pnl["referenceNetProfitAmount"])
                source_net_bps = _first_number(
                    reference_pnl.get("referenceNetReturnBps"),
                    source_net_bps,
                )
                source_final_output = _first_number(
                    reference_pnl.get("referenceFinalOutputAmount"),
                    source_final_output,
                )
                source_profit_source = "offline_v4_sqlite_route_snapshot"
            else:
                source_profit_source = "concrete_pnl_unavailable"
        tx_hash = str(
            estimate.get("txHash")
            or source_evidence.get("txHash")
            or source_evidence.get("transactionHash")
            or ""
        ).strip() or None
        if source_net_profit is None or source_net_profit <= 0:
            blocker_text = "closed loop currently has no positive pool-token profit; automatic execution is not justified"
            if blocker_text not in blockers:
                blockers.append(blocker_text)
        if source_net_profit is None:
            detail = "concrete P&L could not be resolved; 0.0 is not an acceptable review result"
            if detail not in blockers:
                blockers.append(detail)
            if not error_stack:
                error_stack = [
                    {
                        "module": "DEX",
                        "code": 3010,
                        "kind": "missing_concrete_pnl",
                        "message": detail,
                    }
                ]
            quote_status = "review_failed_missing_concrete_pnl"
            execution_trust = quote_status
        evidence = {
            "status": quote_status,
            "executionTrust": execution_trust,
            "liveVerified": bool(estimate.get("liveVerified")) and not best_amount_missing,
            "livePriceVerified": bool(estimate.get("liveVerified")) and not best_amount_missing,
            "readyForTransactionPreflight": bool(estimate.get("readyForTransactionPreflight")) and not best_amount_missing,
            "profitable": bool(estimate.get("profitable")),
            "trustedForExecution": bool(estimate.get("trustedForExecution")) and not best_amount_missing,
            "bestAmountConfirmed": best_amount_confirmed,
            "routeSymbols": route_symbols,
            "routeAddresses": route_addresses,
            "hops": list(estimate.get("steps") or source_evidence.get("hops") or []),
            "blockNumber": estimate.get("blockNumber") or source_evidence.get("blockNumber"),
            "txHash": tx_hash,
            "costModel": {
                "borrowInputSymbol": route_symbols[0] if route_symbols else None,
                "finalOutputSymbol": source_final_symbol,
                "borrowInputAmount": reference_probe_amount,
                "requestedBorrowInputAmount": (
                    source_costs.get("requestedBorrowInputAmount")
                    or reference_probe_amount
                ),
                "initialProbeAmount": (
                    source_costs.get("initialProbeAmount")
                    or reference_probe_amount
                ),
                "selectedBorrowInputAmount": confirmed_best_amount,
                "selectedBorrowInputAmountConfirmedMax": best_amount_confirmed,
                "selectedBorrowInputAmountRaw": (
                    source_costs.get("selectedBorrowInputAmountRaw")
                    if confirmed_best_amount is not None
                    else None
                ),
                "requestedBorrowInputAmountRaw": source_costs.get("requestedBorrowInputAmountRaw"),
                "bestAmountConfirmed": best_amount_confirmed,
                "bestAmountMissing": best_amount_missing,
                "netProfitAmount": source_net_profit,
                "netProfitBps": source_net_bps,
                "netProfitAmountBps": source_net_bps,
                "finalOutputAmount": source_final_output,
                "profitBasis": analysis.get("profitBasis") or source_costs.get("profitBasis") or "borrow_token",
                "profitable": bool(estimate.get("profitable")) or bool(source_costs.get("profitable")),
                "readyForTransactionPreflight": bool(estimate.get("readyForTransactionPreflight")) and not best_amount_missing,
                "bestAmountSelectionBasis": estimate.get("bestAmountSelectionBasis") or source_costs.get("bestAmountSelectionBasis"),
                "bestAmountConfirmationReason": estimate.get("bestAmountConfirmationReason") or source_costs.get("bestAmountConfirmationReason"),
                "selectedProfitRaw": (
                    source_costs.get("selectedProfitRaw")
                    if source_net_profit is not None
                    else None
                ),
                "selectedProfitAmount": source_net_profit,
                "selectedProfitAmountSymbol": route_symbols[0] if route_symbols else None,
                "selectedProfitSource": source_profit_source,
                "selectedProfitMissingReason": (
                    None
                    if source_net_profit is not None
                    else (
                        "concrete P&L could not be resolved from native review, "
                        "SQLite snapshot or observation quote; zero is not accepted"
                    )
                ),
            },
            "referenceNetProfitAmount": source_net_profit,
            "referenceNetProfitAmountBps": source_net_bps,
            "referenceFinalOutputAmount": source_final_output,
            "referenceFinalOutputSymbol": source_final_symbol,
            "selectedProfitAmount": source_net_profit,
            "selectedProfitAmountSymbol": route_symbols[0] if route_symbols else None,
            "selectedProfitSource": source_profit_source,
            "selectedProfitMissingReason": (
                None
                if source_net_profit is not None
                else (
                    "concrete P&L could not be resolved from native review, "
                    "SQLite snapshot or observation quote; zero is not accepted"
                )
            ),
            "bestAmount": confirmed_best_amount,
            "bestAmountRaw": (
                source_costs.get("selectedBorrowInputAmountRaw")
                if confirmed_best_amount is not None
                else None
            ),
            "validation": {
                "profitableAfterCosts": bool(estimate.get("profitable")) or bool(source_costs.get("profitable")),
            },
            "offchainInitialProbeReason": (
                estimate.get("offchainInitialProbeReason")
                or source_evidence.get("offchainInitialProbeReason")
                or "requested_borrow_amount_fallback"
            ),
            "executionSizeProbe": size_probe,
            "blockers": blockers,
            "conclusion": "閾句笂鎶ヤ环宸查獙璇佸綋鍓嶉噾棰濓紝浣嗘渶浣充氦鏄撻噺鏈绠楁垚鍔燂紱澶嶆牳鏈€氳繃" if best_amount_missing else None,
            "errorStack": error_stack,
        }
        quote = dict(route.get("quote") or {})
        quote["live_onchain_evidence"] = evidence
        enriched_analysis = dict(analysis)
        enriched_analysis["sizeAnalysis"] = {
            **(enriched_analysis.get("sizeAnalysis") if isinstance(enriched_analysis.get("sizeAnalysis"), dict) else {}),
            "executionSizeProbe": size_probe,
        }
        enriched_analysis.setdefault("referenceInputSymbol", route_symbols[0] if route_symbols else None)
        if source_best_amount is not None:
            enriched_analysis.setdefault("bestAmount", confirmed_best_amount)
            enriched_analysis.setdefault("referenceInputAmount", confirmed_best_amount or reference_probe_amount)
            enriched_analysis.setdefault("initialProbeAmount", estimate.get("amount") or source_best_amount)
        if source_net_profit is not None:
            enriched_analysis.setdefault("referenceNetProfitAmount", source_net_profit)
            enriched_analysis.setdefault("selectedProfitAmount", source_net_profit)
            enriched_analysis.setdefault("selectedProfitAmountSymbol", route_symbols[0] if route_symbols else None)
            enriched_analysis.setdefault("selectedProfitSource", source_profit_source)
        if source_net_bps is not None:
            enriched_analysis.setdefault("referenceNetReturnBps", source_net_bps)
        if source_final_output is not None:
            enriched_analysis.setdefault("referenceFinalOutputAmount", source_final_output)
        quote["quote_analysis"] = enriched_analysis
        return {
            "network": _network_for_chain_id(chain_id),
            "chain_id": chain_id,
            "pair": " -> ".join(route_symbols),
            "route_path": route_symbols,
            "route_addresses": route_addresses,
            "routeAddresses": route_addresses,
            "priority_reason": "closed_loop_fixed_refresh",
            "quote": quote,
            "quoteAnalysis": enriched_analysis,
            "observed_at": observed_at,
        }

    def _apply_fixed_refresh_evidence_to_route(self, route: dict[str, Any], attempt: dict[str, Any]) -> None:
        quote = attempt.get("quote") if isinstance(attempt.get("quote"), dict) else {}
        evidence = quote.get("live_onchain_evidence") if isinstance(quote, dict) else {}
        if not isinstance(evidence, dict):
            return
        route["live_onchain_evidence"] = deepcopy(evidence)
        existing_analysis = route.get("quoteAnalysis") if isinstance(route.get("quoteAnalysis"), dict) else {}
        route["quoteAnalysis"] = {
            **deepcopy(existing_analysis),
            **deepcopy(attempt.get("quoteAnalysis") or {}),
        }
        if isinstance(evidence.get("costModel"), dict):
            route_costs = evidence["costModel"]
            route["quoteAnalysis"].setdefault("bestAmount", route_costs.get("selectedBorrowInputAmount"))
            route["quoteAnalysis"].setdefault("referenceInputAmount", route_costs.get("selectedBorrowInputAmount"))
            route["quoteAnalysis"].setdefault("referenceInputSymbol", route_costs.get("borrowInputSymbol"))
            route["quoteAnalysis"].setdefault("referenceNetProfitAmount", route_costs.get("netProfitAmount"))
            route["quoteAnalysis"].setdefault("referenceNetReturnBps", route_costs.get("netProfitAmountBps"))
            route["quoteAnalysis"].setdefault("bestAmountConfirmed", bool(route_costs.get("bestAmountConfirmed")))
            route["quoteAnalysis"].setdefault("bestAmountMissing", bool(route_costs.get("bestAmountMissing")))
        route["onchainEvidenceStatus"] = evidence.get("status")
        route["quoteStatus"] = evidence.get("status")
        route["quoteVerification"] = {
            "status": evidence.get("status"),
            "executionTrust": evidence.get("executionTrust"),
            "trustedForExecution": evidence.get("trustedForExecution"),
            "readyForTransactionPreflight": evidence.get("readyForTransactionPreflight"),
            "livePriceVerified": evidence.get("livePriceVerified"),
            "bestAmountConfirmed": evidence.get("bestAmountConfirmed"),
            "blockers": list(evidence.get("blockers") or []),
        }
        costs = evidence.get("costModel") if isinstance(evidence.get("costModel"), dict) else {}
        route["bestAmountConfirmed"] = bool(evidence.get("bestAmountConfirmed")) or bool(costs.get("bestAmountConfirmed"))
        route["bestAmountMissing"] = bool(evidence.get("bestAmountMissing")) or bool(costs.get("bestAmountMissing")) or not route["bestAmountConfirmed"]
        if "auto_execute" in attempt:
            route["auto_execute"] = deepcopy(attempt.get("auto_execute") or {})

    def _attach_auto_execute_to_attempt(
        self,
        attempt: dict[str, Any],
        route: dict[str, Any],
        broadcast_payload: dict[str, Any],
    ) -> None:
        route_symbols = [
            str(item)
            for item in (route.get("routeSymbols") or attempt.get("route_path") or [])
            if str(item).strip()
        ]
        tx_hash = str(
            broadcast_payload.get("txHash")
            or broadcast_payload.get("transactionHash")
            or broadcast_payload.get("transaction_hash")
            or ""
        ).strip()
        executed = bool(tx_hash)
        auto_execute_payload = {
            "observed_at": attempt.get("observed_at") or _now_iso(),
            "route_symbols": route_symbols,
            "broadcast": True,
            "attempted": True,
            "amount": broadcast_payload.get("amount") or attempt.get("best_amount"),
            "result": dict(broadcast_payload),
            "executed": executed,
        }
        attempt["auto_execute"] = auto_execute_payload
        quote = attempt.get("quote") if isinstance(attempt.get("quote"), dict) else {}
        precheck = attempt.get("precheck") if isinstance(attempt.get("precheck"), dict) else {}
        attempt["broadcast"] = True
        attempt["broadcastAttempted"] = True
        attempt["broadcastPerformed"] = executed
        attempt["auto_execute_requested"] = True
        if executed:
            attempt["state"] = "execution_success"
            attempt["execution_phase"] = "order_submission"
            attempt["checks_passed"] = True
            attempt["can_submit_order"] = True
            attempt["order_submission_enabled"] = True
            attempt["blocked_reasons"] = []
            attempt["error"] = None
            attempt["tx_hash"] = tx_hash
            quote["tx_hash"] = tx_hash
            precheck.update(
                {
                    "status": "execution_success",
                    "checks_passed": True,
                    "can_submit_order": True,
                    "order_submission_enabled": True,
                    "auto_execute_requested": True,
                    "broadcast": True,
                }
            )
        else:
            error = str(broadcast_payload.get("error") or broadcast_payload.get("detail") or "auto_execute_failed")
            attempt["state"] = "broadcast_failed"
            attempt["execution_phase"] = "submission"
            attempt["checks_passed"] = False
            attempt["can_submit_order"] = False
            attempt["order_submission_enabled"] = True
            attempt["blocked_reasons"] = [error]
            attempt["error"] = error
            precheck.update(
                {
                    "status": "broadcast_failed",
                    "checks_passed": False,
                    "can_submit_order": False,
                    "order_submission_enabled": True,
                    "auto_execute_requested": True,
                    "broadcast": True,
                    "reasons": [error],
                }
            )

