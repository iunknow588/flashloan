from __future__ import annotations

import os
import time
from typing import Any, Callable


AutoExecuteCallback = Callable[..., dict[str, Any]]


def auto_execute_env_enabled() -> bool:
    raw = os.getenv("DEX_AUTO_EXECUTE_VERIFIED_OPPORTUNITIES", "")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def auto_execute_cooldown_seconds() -> float:
    raw = os.getenv("DEX_AUTO_EXECUTE_COOLDOWN_SECONDS", "600")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 600.0


def auto_execute_callback_from_env() -> AutoExecuteCallback | None:
    if not auto_execute_env_enabled():
        return None
    from market.binance_market.dex_route_test import run_official_v4_arb_route_test

    return run_official_v4_arb_route_test


def _auto_execute_skip_reason(attempt: dict[str, Any], *, chain_id: int | None) -> str | None:
    del chain_id
    quote = attempt.get("quote") if isinstance(attempt.get("quote"), dict) else {}
    evidence = quote.get("live_onchain_evidence") if isinstance(quote.get("live_onchain_evidence"), dict) else {}
    if not evidence:
        return "missing_live_onchain_evidence"
    if str(evidence.get("status") or "").lower() != "live_verified":
        return "not_live_verified"
    if not bool(evidence.get("readyForTransactionPreflight")):
        return "not_ready_for_transaction_preflight"
    costs = evidence.get("costModel") if isinstance(evidence.get("costModel"), dict) else {}
    best_amount_confirmed = bool(evidence.get("bestAmountConfirmed")) or bool(costs.get("bestAmountConfirmed"))
    if not best_amount_confirmed:
        return "best_amount_not_confirmed"
    net_profit = costs.get("netProfitUsd")
    if net_profit is None:
        net_profit = costs.get("netProfitAmount")
    try:
        profitable = bool(evidence.get("profitable")) or bool(
            (evidence.get("validation") or {}).get("profitableAfterCosts")
        ) or float(net_profit or 0) > 0
    except (TypeError, ValueError):
        profitable = bool(evidence.get("profitable")) or bool(
            (evidence.get("validation") or {}).get("profitableAfterCosts")
        )
    if not profitable:
        return "not_profitable"
    return None


def _auto_execute_amount(attempt: dict[str, Any]) -> float | None:
    quote = attempt.get("quote") if isinstance(attempt.get("quote"), dict) else {}
    evidence = quote.get("live_onchain_evidence") if isinstance(quote.get("live_onchain_evidence"), dict) else {}
    costs = evidence.get("costModel") if isinstance(evidence.get("costModel"), dict) else {}
    for key in (
        "selectedBorrowInputAmount",
        "borrowInputAmount",
        "requestedBorrowInputAmount",
    ):
        value = costs.get(key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    analysis = quote.get("quote_analysis") if isinstance(quote.get("quote_analysis"), dict) else {}
    try:
        number = float(analysis.get("referenceInputAmount") or analysis.get("inputAmount") or 0)
    except (TypeError, ValueError):
        number = 0.0
    return number if number > 0 else None


class DexAutoExecuteRunner:
    def __init__(self, *, callback: AutoExecuteCallback | None, enabled: bool, cooldown_seconds: float) -> None:
        self.callback = callback
        self.enabled = bool(enabled)
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._executed_keys: dict[tuple[Any, ...], float] = {}

    def run_ready_attempts(
        self,
        attempts: list[dict[str, Any]],
        *,
        chain_id: int | None,
        observed_at: str,
    ) -> dict[str, Any]:
        if not self.enabled or self.callback is None:
            return {
                "autoExecuteEnabled": False,
                "autoExecuted": 0,
                "autoExecuteSkipped": len(attempts),
                "autoExecuteFailed": 0,
                "autoExecuteErrors": [],
            }
        executed = 0
        skipped = 0
        failed = 0
        errors: list[str] = []
        now = time.monotonic()
        for attempt in attempts:
            skip_reason = _auto_execute_skip_reason(attempt, chain_id=chain_id)
            if skip_reason:
                attempt["auto_execute"] = {"skipped": True, "reason": skip_reason}
                skipped += 1
                continue
            amount = _auto_execute_amount(attempt)
            if amount is None or amount <= 0:
                skip_reason = "missing_executable_amount"
                attempt["auto_execute"] = {"skipped": True, "reason": skip_reason}
                skipped += 1
                continue
            route_symbols = [str(item) for item in (attempt.get("route_path") or []) if str(item).strip()]
            evidence = attempt.get("quote", {}).get("live_onchain_evidence", {}) if isinstance(attempt.get("quote"), dict) else {}
            route_addresses = [
                str(item).strip().lower()
                for item in (attempt.get("route_addresses") or attempt.get("routeAddresses") or [])
                if str(item).strip()
            ]
            route_identity = tuple(route_addresses) if route_addresses else tuple(route_symbols)
            dedupe_key = (
                "auto_execute",
                chain_id,
                route_identity,
                round(float(amount), 10),
                evidence.get("blockNumber"),
            )
            if self._on_cooldown(dedupe_key, now):
                attempt["auto_execute"] = {"skipped": True, "reason": "auto_execute_cooldown"}
                skipped += 1
                continue
            try:
                result = self.callback(
                    route_symbols=route_symbols,
                    amount=amount,
                    broadcast=True,
                    chain_id=chain_id,
                )
            except Exception as exc:
                result = {"ok": False, "error": "auto_execute_callback_exception", "detail": str(exc)}
            auto_execute_payload = {
                "observed_at": observed_at,
                "amount": amount,
                "route_symbols": route_symbols,
                "broadcast": True,
                "result": result if isinstance(result, dict) else {"ok": False, "error": "auto_execute_invalid_result"},
                "executed": bool(isinstance(result, dict) and result.get("ok") and result.get("txHash")),
                "attempted": True,
            }
            attempt["auto_execute"] = auto_execute_payload
            quote = attempt.get("quote") if isinstance(attempt.get("quote"), dict) else {}
            if isinstance(quote, dict):
                quote["auto_execute"] = auto_execute_payload
            if auto_execute_payload["executed"]:
                executed += 1
            else:
                failed += 1
                errors.append(str((result or {}).get("error") or "auto_execute_failed")[:300])
        return {
            "autoExecuteEnabled": True,
            "autoExecuted": executed,
            "autoExecuteSkipped": skipped,
            "autoExecuteFailed": failed,
            "autoExecuteErrors": errors,
        }

    def _on_cooldown(self, dedupe_key: tuple[Any, ...], now: float) -> bool:
        last_executed_at = self._executed_keys.get(dedupe_key)
        if last_executed_at is not None and now - last_executed_at < self.cooldown_seconds:
            return True
        self._executed_keys[dedupe_key] = now
        return False

    def reset_cooldowns(self) -> None:
        self._executed_keys.clear()
