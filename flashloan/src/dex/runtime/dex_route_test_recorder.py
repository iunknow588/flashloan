"""Persist DEX quote-route tests into the shared execution attempt log."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.chain_data_registry import chain_data_by_chain_id
from core.db.storage_cow_execution import append_cow_execution_attempts_jsonl
from dex.runtime.cow_attempt_log import cow_execution_attempt_log_path


def _token_units(value: object) -> float | None:
    try:
        return int(str(value)) / 1e18
    except (TypeError, ValueError):
        return None


def _number_value(value: object) -> float | None:
    try:
        text = str(value).strip()
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            return int(text) / 1e18
        return float(text)
    except (TypeError, ValueError):
        return _token_units(value)


def record_dex_route_test_attempt(
    payload: dict[str, Any],
    *,
    chain_id: int | None,
    route_symbols: list[str],
    amount: float,
    amount_source: str,
    mode: str,
    broadcast: bool,
    fallback_networks: list[str] | None = None,
) -> dict[str, Any]:
    """Persist a DEX quote-route test so the submissions page can replay it."""
    try:
        row = chain_data_by_chain_id(int(chain_id)) if chain_id is not None else None
        network = str((row or {}).get("network") or "").strip().lower()
        if not network:
            networks = [str(item).strip().lower() for item in fallback_networks or [] if str(item).strip()]
            network = networks[0] if networks else "dex"
        observed_at = datetime.now(timezone.utc).isoformat()
        ok = bool(payload.get("ok"))
        tx_hash = str(payload.get("txHash") or payload.get("transactionHash") or payload.get("transaction_hash") or "")
        error = str(payload.get("error") or payload.get("detail") or "").strip()
        detail = str(payload.get("detail") or "").strip()
        error_detail = str(payload.get("errorDetail") or "").strip()
        quote_status = str(payload.get("quoteStatus") or "").strip()
        execution_trust = str(payload.get("executionTrust") or "").strip()
        analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
        profit_amount = _number_value(
            payload.get("profitHuman")
            if payload.get("profitHuman") is not None
            else payload.get("profit")
            if payload.get("profit") is not None
            else payload.get("netProfit")
        )
        final_amount = _number_value(
            payload.get("outAmountHuman")
            if payload.get("outAmountHuman") is not None
            else payload.get("outAmount")
            if payload.get("outAmount") is not None
            else payload.get("finalOutputAmount")
        )
        best_amount = _number_value(payload.get("bestAmount"))
        initial_probe_amount = _number_value(
            payload.get("initialProbeAmount")
            if payload.get("initialProbeAmount") is not None
            else payload.get("requestedAmount")
        )
        if initial_probe_amount is None:
            initial_probe_amount = _number_value(amount)
        initial_probe_symbol = (
            payload.get("initialProbeSymbol")
            or analysis.get("initialProbeSymbol")
            or (route_symbols[0] if route_symbols else None)
        )
        direction = (
            payload.get("direction")
            or analysis.get("direction")
            or payload.get("selectedRouteSymbols")
            or route_symbols
            or ""
        )
        if isinstance(direction, (list, tuple)):
            direction = " -> ".join(str(item) for item in direction)
        elif not isinstance(direction, str):
            direction = str(direction)
        if profit_amount is None:
            profit_amount = _number_value(analysis.get("referenceNetProfitAmount"))
        if final_amount is None:
            final_amount = _number_value(analysis.get("referenceFinalOutputAmount"))
        if best_amount is None:
            best_amount = _number_value(analysis.get("bestAmount"))
        best_calculation = (
            payload.get("bestAmountCalculation")
            if isinstance(payload.get("bestAmountCalculation"), dict)
            else analysis.get("bestAmountCalculation")
            if isinstance(analysis.get("bestAmountCalculation"), dict)
            else {}
        )
        best_amount_confirmed = bool(
            payload.get("bestAmountConfirmed")
            or analysis.get("bestAmountConfirmed")
            or best_calculation.get("bestAmountConfirmed")
        )
        best_amount_selection_basis = (
            payload.get("bestAmountSelectionBasis")
            or analysis.get("bestAmountSelectionBasis")
            or best_calculation.get("bestAmountSelectionBasis")
            or "initial_probe_unconfirmed"
        )
        best_amount_confirmation_reason = (
            payload.get("bestAmountConfirmationReason")
            or analysis.get("bestAmountConfirmationReason")
            or best_calculation.get("bestAmountConfirmationReason")
            or "no confirmed best amount"
        )
        if best_amount is None:
            best_amount = _number_value(best_calculation.get("bestAmount"))
        if best_amount is None:
            best_amount = _number_value(best_calculation.get("recommendedBorrowInputAmount"))
        if best_amount is None:
            best_amount = _number_value(best_calculation.get("selectedBorrowInputAmount"))
        size_analysis = payload.get("sizeAnalysis")
        if not isinstance(size_analysis, dict):
            size_analysis = best_calculation.get("sizeAnalysis")
        if not isinstance(size_analysis, dict):
            size_analysis = {}
        probe_records = [
            dict(item) for item in (
                payload.get("probeRecords")
                or analysis.get("probeRecords")
                or analysis.get("probe_records")
                or analysis.get("rows")
                or analysis.get("probeRows")
                or best_calculation.get("probeRecords")
                or best_calculation.get("probe_records")
                or best_calculation.get("rows")
                or size_analysis.get("rows")
                or []
            )
            if isinstance(item, dict)
        ]
        if not probe_records:
            token_steps = size_analysis.get("tokenAmountSteps")
            if isinstance(token_steps, list) and token_steps:
                probe_records = []
                for step in token_steps:
                    step_amount = _number_value(step)
                    if step_amount is None:
                        continue
                    probe_records.append(
                        {
                            "amount": step_amount,
                            "borrowAmount": step_amount,
                            "borrowSymbol": initial_probe_symbol,
                            "symbol": initial_probe_symbol,
                            "referenceInputSymbol": initial_probe_symbol,
                            "referenceNetProfitAmount": None,
                            "referenceNetReturnBps": None,
                            "executableCandidate": False,
                            "staticDepthStatus": None,
                        }
                    )
        if not probe_records and isinstance(payload.get("steps"), list):
            probe_records = []
        input_amount = float(amount) if isinstance(amount, (int, float)) and amount > 0 else amount
        input_symbol = route_symbols[0] if route_symbols else None
        final_symbol = route_symbols[0] if route_symbols else None
        profit_percent = (
            (float(profit_amount) / float(best_amount or input_amount) * 100)
            if profit_amount is not None and (best_amount or input_amount) and float(best_amount or input_amount) > 0
            else None
        )
        if isinstance(analysis, dict):
            analysis = {
                **analysis,
                "bestAmountConfirmed": best_amount_confirmed,
                "bestAmountSelectionBasis": best_amount_selection_basis,
                "bestAmountConfirmationReason": best_amount_confirmation_reason,
                "referenceInputSymbol": analysis.get("referenceInputSymbol") or input_symbol,
                "referenceFinalOutputSymbol": analysis.get("referenceFinalOutputSymbol") or final_symbol,
                "referenceNetProfitAmount": analysis.get("referenceNetProfitAmount", profit_amount),
                "referenceFinalOutputAmount": analysis.get("referenceFinalOutputAmount", final_amount),
                "referenceNetReturnBps": analysis.get("referenceNetReturnBps", (
                    float(profit_percent) * 100 if profit_percent is not None else None
                )),
                "profitable": bool(profit_amount is not None and float(profit_amount) > 0),
                "profit_amount": profit_amount,
                "final_amount": final_amount,
                "profit_percent": profit_percent,
                "best_amount": best_amount,
                "initial_probe_amount": initial_probe_amount,
                "initial_probe_symbol": initial_probe_symbol,
                "direction": direction,
                "probe_records": probe_records,
                "best_amount_calculation": best_calculation,
                "bestAmountCalculation": best_calculation,
            }
        recommendation = analysis.get("recommendation") if isinstance(analysis.get("recommendation"), dict) else {}
        analysis_ready = bool(
            payload.get("readyForTransactionPreflight")
            or analysis.get("readyForTransactionPreflight")
            or recommendation.get("readyForTransactionPreflight")
        )
        broadcast_completed = bool(broadcast and tx_hash)
        safe_for_submission = bool(
            broadcast_completed
            or (
                ok
                and quote_status.lower() == "live_verified"
                and analysis_ready
            )
        )
        reasons = []
        if detail:
            reasons.append(detail)
        elif error:
            reasons.append(error)
        if error_detail and error_detail != detail:
            reasons.append(error_detail)
        if quote_status == "not_enough_liquidity":
            reason = "池当前没有 LP 流动性"
        elif quote_status == "rpc_verification_failed":
            reason = "链上复核失败：RPC 不可用"
        elif quote_status == "rpc_verification_timeout":
            reason = "链上复核超时：RPC 未在期限内返回"
        elif quote_status == "pool_not_initialized":
            reason = "V4 池未初始化"
        elif quote_status in {"live_quote_failed", "live_quote_error"}:
            reason = "链上报价未完成：" + (execution_trust or quote_status)
        elif execution_trust:
            reason = "链上报价状态：" + (quote_status or execution_trust)
        else:
            reason = None
        if reason and reason not in reasons:
            reasons.append(reason)
        if broadcast:
            state = (
                "submitted_success"
                if broadcast_completed
                else ("submission_failed" if not safe_for_submission else "quote_verified")
            )
            execution_phase = "order_submission" if ok else "submission"
        else:
            state = (
                "quote_verified"
                if safe_for_submission
                else ("quote_failed" if not ok else "quote_required")
            )
            execution_phase = "quote_precheck"
        route_addresses = [
            str(item).strip()
            for item in payload.get("routeAddresses") or payload.get("route_addresses") or []
            if str(item).strip()
        ]
        steps = [dict(step) for step in payload.get("steps") or [] if isinstance(step, dict)]
        raw_evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
        live_onchain_evidence = {
            **raw_evidence,
            "status": quote_status or raw_evidence.get("status"),
            "executionTrust": execution_trust or raw_evidence.get("executionTrust"),
            "routeSymbols": route_symbols or raw_evidence.get("routeSymbols"),
            "routeAddresses": route_addresses or raw_evidence.get("routeAddresses") or [],
            "hops": steps or raw_evidence.get("hops") or [],
        }
        quote = {
            "pair": " -> ".join(route_symbols),
            "direction": direction,
            "path": route_symbols,
            "input_amount": amount,
            "best_amount": best_amount,
            "best_amount_confirmed": best_amount_confirmed,
            "best_amount_selection_basis": best_amount_selection_basis,
            "best_amount_confirmation_reason": best_amount_confirmation_reason,
            "initial_probe_amount": initial_probe_amount,
            "initial_probe_symbol": initial_probe_symbol,
            "input_symbol": input_symbol,
            "final_symbol": final_symbol,
            "final_amount": final_amount,
            "final_delta_amount": profit_amount,
            "profit_amount": profit_amount,
            "profit_percent": profit_percent,
            "probe_records": probe_records,
            "best_amount_calculation": best_calculation,
            "bestAmountCalculation": best_calculation,
            "quote_verified": safe_for_submission,
            "quote_status": payload.get("quoteStatus"),
            "execution_trust": payload.get("executionTrust"),
            "routeAddresses": route_addresses,
            "route_addresses": route_addresses,
            "hops": steps,
            "steps": steps,
            "analysis": payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {},
            "live_onchain_evidence": live_onchain_evidence,
            "tx_hash": tx_hash,
            "error": error or None,
            "detail": detail or None,
            "errorDetail": error_detail or None,
        }
        precheck = {
            "status": state,
            "checks_passed": safe_for_submission,
            "can_submit_order": safe_for_submission,
            "order_submission_enabled": True,
            "auto_execute_requested": False,
            "mode": mode,
            "broadcast": broadcast,
            "amount": amount,
            "amount_source": amount_source,
            "reasons": reasons,
        }
        attempt = {
            "observed_at": observed_at,
            "network": network,
            "chain_id": chain_id,
            "pair": " -> ".join(route_symbols),
            "direction": direction,
            "route_path": route_symbols,
            "route_addresses": route_addresses,
            "state": state,
            "execution_phase": execution_phase,
            "broadcast": bool(broadcast),
            "broadcastAttempted": bool(broadcast),
            "broadcastPerformed": bool(broadcast_completed),
            "checks_passed": safe_for_submission,
            "can_submit_order": safe_for_submission,
            "order_submission_enabled": True,
            "auto_execute_requested": False,
            "blocked_reasons": reasons,
            "profit_amount": profit_amount,
            "final_amount": final_amount,
            "profit_percent": profit_percent,
            "best_amount": best_amount,
            "best_amount_confirmed": best_amount_confirmed,
            "best_amount_selection_basis": best_amount_selection_basis,
            "best_amount_confirmation_reason": best_amount_confirmation_reason,
            "initial_probe_amount": initial_probe_amount,
            "initial_probe_symbol": initial_probe_symbol,
            "probe_records": probe_records,
            "best_amount_calculation": best_calculation,
            "bestAmountCalculation": best_calculation,
            "quote": quote,
            "precheck": precheck,
            "tx_hash": tx_hash,
            "error": error or None,
            "source": "dex_quote_route_test",
        }
        log_path = cow_execution_attempt_log_path(network=network, chain_id=chain_id)
        appended = append_cow_execution_attempts_jsonl(
            log_path,
            [attempt],
            dedupe_market_candidates=False,
        )
        return {"recorded": appended, "log_path": str(log_path), "state": state, "ok": True}
    except Exception as exc:  # noqa: BLE001 - the user-facing route test still succeeds.
        return {"recorded": 0, "state": "record_failed", "error": str(exc)[:200], "ok": False}


__all__ = ["record_dex_route_test_attempt"]
