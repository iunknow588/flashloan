"""Persist DEX quote-route tests into the shared execution attempt log."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.chain_data_registry import chain_data_by_chain_id
from core.db.storage_cow_execution import append_cow_execution_attempts_jsonl
from dex.runtime.cow_attempt_log import cow_execution_attempt_log_path


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
        reasons = []
        if detail:
            reasons.append(detail)
        elif error:
            reasons.append(error)
        if error_detail and error_detail != detail:
            reasons.append(error_detail)
        if quote_status == "not_enough_liquidity":
            reason = "池当前没有 LP 流动性"
        elif quote_status in {"rpc_verification_failed", "rpc_verification_timeout"}:
            reason = "链上复核失败：RPC 不可用"
        elif quote_status in {"live_quote_failed", "live_quote_error"}:
            reason = "链上报价未完成：" + (execution_trust or quote_status)
        elif execution_trust:
            reason = "链上报价状态：" + (quote_status or execution_trust)
        else:
            reason = None
        if reason and reason not in reasons:
            reasons.append(reason)
        if broadcast:
            state = "submitted_success" if ok and tx_hash else ("submission_failed" if not ok else "quote_verified")
            execution_phase = "order_submission" if ok else "submission"
        else:
            state = (
                "quote_verified"
                if ok and str(payload.get("quoteStatus") or "").lower() == "live_verified"
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
            "path": route_symbols,
            "input_amount": amount,
            "input_symbol": route_symbols[0] if route_symbols else None,
            "final_symbol": route_symbols[0] if route_symbols else None,
            "quote_verified": ok and str(payload.get("quoteStatus") or "").lower() == "live_verified",
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
            "checks_passed": ok,
            "can_submit_order": ok,
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
            "route_path": route_symbols,
            "route_addresses": route_addresses,
            "state": state,
            "execution_phase": execution_phase,
            "checks_passed": ok,
            "can_submit_order": ok,
            "order_submission_enabled": True,
            "auto_execute_requested": False,
            "blocked_reasons": reasons,
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
