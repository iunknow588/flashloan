from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from typing import Any, Callable

from dex.runtime.dex_opportunity_helpers import (
    _network_for_chain_id,
    _now_iso,
    _structured_dex_error,
)
from dex.runtime.dex_opportunity_auto_execute import _order_routes_for_review
from dex.runtime.dex_opportunity_route_runner import (
    _closed_loop_workers,
    _quote_routes_payload_from_facade,
)


def _first_number(*values: Any) -> float | None:
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        return number
    return None


class DexOpportunityReviewEngineMixin:
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

            workers = _closed_loop_workers()
            if workers <= 1 or len(routes) <= 1:
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
            else:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = [
                        executor.submit(
                            self._closed_loop_review_one_route,
                            route=route,
                            chain_id=task.chain_id,
                            protocols=list(task.protocols),
                            recommended_route_test_amount=recommended_route_test_amount,
                            run_official_v4_arb_route_test=run_official_v4_arb_route_test,
                            estimate_route_live_quote=estimate_route_live_quote,
                            anchor=anchor,
                        )
                        for route in routes
                    ]
                    for future in as_completed(futures):
                        _consume_result(future.result())
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
                            "executed": bool(broadcast_payload.get("ok")) and bool(broadcast_payload.get("txHash")),
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
        preflight = run_official_v4_arb_route_test(
            route_symbols=route_symbols,
            amount=amount,
            broadcast=False,
            chain_id=chain_id,
            route_addresses=route_addresses or None,
        )
        if (
            not isinstance(preflight, dict)
            or not preflight.get("ok")
            or not preflight.get("bestAmountConfirmed")
            or not self._manual_review_is_profitable(preflight)
        ):
            return {"preflight": preflight, "broadcast": None, "result": preflight}
        broadcast_amount = preflight.get("bestAmount") or amount
        amount_is_best = bool(preflight.get("bestAmountConfirmed"))
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
        return {"preflight": preflight, "broadcast": broadcast, "result": broadcast}

    @staticmethod
    def _manual_review_is_profitable(payload: dict[str, Any] | None) -> bool:
        if not isinstance(payload, dict):
            return False
        if bool(payload.get("profitable")):
            return True
        analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
        for key in (
            "profit",
            "netProfitAmount",
            "referenceNetProfitAmount",
            "pureProfitAmount",
            "referencePureProfitAmount",
        ):
            try:
                if float(payload.get(key) or analysis.get(key) or 0) > 0:
                    return True
            except (TypeError, ValueError):
                continue
        evidence = payload.get("live_onchain_evidence") if isinstance(payload.get("live_onchain_evidence"), dict) else {}
        costs = evidence.get("costModel") if isinstance(evidence.get("costModel"), dict) else {}
        try:
            return float(costs.get("netProfitUsd") or costs.get("netProfitAmount") or 0) > 0
        except (TypeError, ValueError):
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
                broadcast_payload = manual_result["broadcast"]
                if broadcast_payload is not None:
                    if bool(broadcast_payload.get("ok")) and broadcast_payload.get("txHash"):
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
        quote_status = str(estimate.get("quoteStatus") or "").strip().lower()
        execution_trust = str(estimate.get("executionTrust") or "").strip().lower()
        if not quote_status:
            quote_status = str(estimate.get("evidenceStatus") or "").strip().lower()
        if not quote_status:
            quote_status = "live_quote_failed" if not estimate.get("ok") else "static_reference_pending_live_review"
        if not execution_trust:
            execution_trust = quote_status
        if best_amount_missing and (quote_status == "live_verified" or execution_trust == "live_quoter_verified_readonly"):
            quote_status = "static_reference_pending_live_review"
            execution_trust = "static_reference_pending_live_review"
        failure_detail = ""
        if not estimate.get("ok"):
            failure_detail = str(estimate.get("detail") or estimate.get("error") or "").strip()[:300]
        blockers = [
            "chain hops passed for current amount, but best amount is not confirmed; cannot enter transaction preflight"
        ] if best_amount_missing else []
        if failure_detail and failure_detail not in blockers:
            blockers.append(failure_detail)
        error_stack = [
            {
                "module": "DEX",
                "code": 3006,
                "message": "chain hops passed for current amount, but best amount is not confirmed; cannot enter transaction preflight",
            }
        ] if best_amount_missing else []
        if not estimate.get("ok") and not error_stack:
            error_stack = [
                {
                    "module": "DEX",
                    "code": 3007,
                    "message": failure_detail or "on-chain review did not complete",
                }
            ]
        source_evidence = estimate.get("evidence") if isinstance(estimate.get("evidence"), dict) else {}
        source_costs = source_evidence.get("costModel") if isinstance(source_evidence.get("costModel"), dict) else {}
        analysis = estimate.get("analysis") if isinstance(estimate.get("analysis"), dict) else {}
        source_best_amount = _first_number(
            source_costs.get("selectedBorrowInputAmount"),
            source_costs.get("borrowInputAmount"),
            estimate.get("bestAmount"),
            source_evidence.get("bestAmount"),
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
        tx_hash = str(
            estimate.get("txHash")
            or source_evidence.get("txHash")
            or source_evidence.get("transactionHash")
            or ""
        ).strip() or None
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
                "selectedBorrowInputAmount": source_best_amount,
                "selectedBorrowInputAmountConfirmedMax": best_amount_confirmed,
                "selectedBorrowInputAmountRaw": source_costs.get("selectedBorrowInputAmountRaw"),
                "borrowInputAmount": source_best_amount,
                "requestedBorrowInputAmount": estimate.get("amount") or source_costs.get("requestedBorrowInputAmount"),
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
            },
            "referenceNetProfitAmount": source_net_profit,
            "referenceNetProfitAmountBps": source_net_bps,
            "referenceFinalOutputAmount": source_final_output,
            "referenceFinalOutputSymbol": source_final_symbol,
            "validation": {
                "profitableAfterCosts": bool(estimate.get("profitable")) or bool(source_costs.get("profitable")),
            },
            "offchainInitialProbeReason": (
                estimate.get("offchainInitialProbeReason")
                or source_evidence.get("offchainInitialProbeReason")
                or "requested_borrow_amount_fallback"
            ),
            "blockers": blockers,
            "conclusion": "閾句笂鎶ヤ环宸查獙璇佸綋鍓嶉噾棰濓紝浣嗘渶浣抽噺鏈绠楁垚鍔燂紱澶嶆牳鏈€氳繃" if best_amount_missing else None,
            "errorStack": error_stack,
        }
        quote = dict(route.get("quote") or {})
        quote["live_onchain_evidence"] = evidence
        enriched_analysis = dict(analysis)
        enriched_analysis.setdefault("referenceInputSymbol", route_symbols[0] if route_symbols else None)
        if source_best_amount is not None:
            enriched_analysis.setdefault("bestAmount", source_best_amount)
            enriched_analysis.setdefault("referenceInputAmount", source_best_amount)
            enriched_analysis.setdefault("initialProbeAmount", estimate.get("amount") or source_best_amount)
        if source_net_profit is not None:
            enriched_analysis.setdefault("referenceNetProfitAmount", source_net_profit)
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
        executed = bool(broadcast_payload.get("ok")) and bool(tx_hash)
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
