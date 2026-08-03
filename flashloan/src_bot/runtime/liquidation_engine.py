from __future__ import annotations

import asyncio
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from core.sensitive_data import redact_sensitive_text
from core.config_schema import parse_env_float, parse_env_int


AccountLoader = Callable[[], list[str]]
PayloadBuilder = Callable[[str], dict[str, Any]]
StaticCallRunner = Callable[[dict[str, Any]], dict[str, Any]]
Submitter = Callable[[dict[str, Any]], dict[str, Any]]
AttemptRecorder = Callable[..., Any]
ControlsLoader = Callable[[], dict[str, Any]]
PriceSnapshotLoader = Callable[[], dict[str, float]]
PriceEventLoader = Callable[[], list[dict[str, Any]]]
AffectedAccountLoader = Callable[[list[str]], list[str]]


@dataclass(frozen=True)
class LiquidationEngineConfig:
    poll_interval_seconds: float = 30.0
    event_poll_interval_seconds: float = 5.0
    auto_execute: bool = False
    auto_execute_requested: bool = False
    manual_test_completed: bool = True
    price_change_threshold_bps: int = 100
    max_accounts_per_tick: int = 100

    @property
    def mode(self) -> str:
        return "auto" if self.auto_execute else "observe"

    @classmethod
    def from_env(cls) -> "LiquidationEngineConfig":
        poll_interval_seconds, _ = parse_env_float("LIQUIDATION_ENGINE_POLL_SECONDS", 30)
        event_poll_interval_seconds, _ = parse_env_float("LIQUIDATION_EVENT_POLL_SECONDS", 5)
        price_change_threshold_bps, _ = parse_env_int("LIQUIDATION_PRICE_TRIGGER_BPS", 100)
        max_accounts_per_tick, _ = parse_env_int("LIQUIDATION_ENGINE_MAX_ACCOUNTS", 100)
        auto_execute_requested = os.getenv("LIQUIDATION_AUTO_EXECUTE", "true").strip().lower() in {"1", "true", "yes", "on"}
        manual_test_completed = os.getenv("LIQUIDATION_MANUAL_TEST_COMPLETED", "true").strip().lower() in {"1", "true", "yes", "on"}
        return cls(
            poll_interval_seconds=max(1.0, poll_interval_seconds),
            event_poll_interval_seconds=max(1.0, event_poll_interval_seconds),
            auto_execute=auto_execute_requested and manual_test_completed,
            auto_execute_requested=auto_execute_requested,
            manual_test_completed=manual_test_completed,
            price_change_threshold_bps=max(1, price_change_threshold_bps),
            max_accounts_per_tick=max(1, max_accounts_per_tick),
        )


@dataclass(frozen=True)
class LiquidationEngineDependencies:
    load_accounts: AccountLoader
    build_payload: PayloadBuilder
    simulate_static_call: StaticCallRunner
    submit: Submitter
    record_attempt: AttemptRecorder
    load_controls: ControlsLoader
    load_price_snapshot: PriceSnapshotLoader | None = None
    load_price_events: PriceEventLoader | None = None
    load_affected_accounts: AffectedAccountLoader | None = None


class LiquidationEngine:
    def __init__(
        self,
        dependencies: LiquidationEngineDependencies,
        config: LiquidationEngineConfig | None = None,
    ) -> None:
        self.dependencies = dependencies
        self.config = config or LiquidationEngineConfig.from_env()
        self._stop = threading.Event()
        self._last_prices: dict[str, float] = {}
        self._last_full_poll_at = 0.0

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            full_poll_due = now - self._last_full_poll_at >= self.config.poll_interval_seconds
            self.run_once(allow_poll=full_poll_due)
            if full_poll_due:
                self._last_full_poll_at = now
            sleep_seconds = self.config.event_poll_interval_seconds if self.dependencies.load_price_events else self.config.poll_interval_seconds
            await asyncio.sleep(max(1.0, min(self.config.poll_interval_seconds, sleep_seconds)))

    def run_in_thread(self, *, name: str = "liquidation-engine") -> threading.Thread:
        thread = threading.Thread(target=lambda: asyncio.run(self.run_forever()), name=name, daemon=True)
        thread.start()
        return thread

    def price_triggered_accounts(self) -> tuple[list[str], list[str], list[dict[str, Any]]]:
        loader = self.dependencies.load_price_snapshot
        event_loader = self.dependencies.load_price_events
        affected_loader = self.dependencies.load_affected_accounts
        if affected_loader is None:
            return [], [], []

        current = loader() if loader is not None else {}
        current = current or {}
        changed_assets: list[str] = []
        oracle_events = event_loader() if event_loader is not None else []
        for event in oracle_events or []:
            asset = str(event.get("asset") or "").strip()
            if asset and asset not in changed_assets:
                changed_assets.append(asset)
        for asset, price in current.items():
            previous = self._last_prices.get(asset)
            self._last_prices[asset] = float(price)
            if previous is None or previous <= 0:
                continue
            change_bps = abs(float(price) - previous) / previous * 10000
            if change_bps >= self.config.price_change_threshold_bps and asset not in changed_assets:
                changed_assets.append(asset)
        if not changed_assets:
            return [], [], oracle_events
        return changed_assets, affected_loader(changed_assets), oracle_events

    def run_once(self, accounts: list[str] | None = None, *, allow_poll: bool = True) -> dict[str, Any]:
        controls = self.dependencies.load_controls()
        if controls.get("auto_pause_active") or int(controls.get("circuit_breaker_level") or 0) >= 3:
            return {
                "mode": self.config.mode,
                "state": "paused",
                "processed": [],
                "blocked_reasons": ["auto_pause_active"],
                "controls": controls,
            }

        changed_assets, event_accounts, oracle_events = self.price_triggered_accounts()
        candidate_accounts = accounts or event_accounts or (self.dependencies.load_accounts() if allow_poll else [])
        candidate_accounts = list(dict.fromkeys(candidate_accounts))[: self.config.max_accounts_per_tick]
        processed = [self.process_account(account) for account in candidate_accounts]
        trigger = "oracle_event" if oracle_events else ("price_event" if event_accounts else ("poll" if allow_poll else "idle"))
        return {
            "mode": self.config.mode,
            "state": "completed",
            "trigger": trigger,
            "changed_assets": changed_assets,
            "oracle_events": oracle_events,
            "processed": processed,
            "controls": controls,
        }

    def process_account(self, account: str) -> dict[str, Any]:
        payload: dict[str, Any] | None = None
        try:
            payload = self.dependencies.build_payload(account)
            static_result = self.dependencies.simulate_static_call(payload)
            preflight = static_result.get("preflight") or {}
            static_state = "static_call_passed" if preflight.get("static_call_passed") else "static_call_failed"
            self._record(
                account,
                state=static_state,
                payload=static_result,
                error=preflight.get("static_call_error"),
            )

            blocked = list(static_result.get("blocked_reasons") or [])
            if not preflight.get("static_call_passed"):
                return {"account": account, "state": "static_call_failed", "blocked_reasons": blocked}
            deferred_submit_blockers = {"fork_simulation_required"} if self.config.auto_execute else set()
            remaining_blockers = [reason for reason in blocked if reason not in deferred_submit_blockers]
            if remaining_blockers:
                self._record(account, state="submission_blocked", payload=static_result)
                return {"account": account, "state": "submission_blocked", "blocked_reasons": remaining_blockers}
            if not self.config.auto_execute:
                return {"account": account, "state": "observed", "preflight": preflight}

            result = self.dependencies.submit(static_result)
            receipt = result.get("receipt") or {}
            state = "confirmed_success" if int(receipt.get("status") or 0) == 1 else "confirmed_failed"
            self._record(
                account,
                state=state,
                payload=result,
                tx_hash=result.get("tx_hash"),
                receipt=receipt,
            )
            return {"account": account, "state": state, "tx_hash": result.get("tx_hash"), "receipt": receipt}
        except Exception as exc:
            message = redact_sensitive_text(exc)
            if "execution plan is not ready" in message.lower():
                blocked_reasons = ["no_liquidation_candidate"]
                self._record(
                    account,
                    state="submission_blocked",
                    payload={"blocked_reasons": blocked_reasons},
                    error=message,
                )
                return {
                    "account": account,
                    "state": "submission_blocked",
                    "blocked_reasons": blocked_reasons,
                    "error": message,
                }
            self._record(account, state="submission_failed", payload=payload or {}, error=message)
            return {"account": account, "state": "submission_failed", "error": message}

    def _record(
        self,
        account: str,
        *,
        state: str,
        payload: dict[str, Any],
        error: str | None = None,
        tx_hash: str | None = None,
        receipt: dict[str, Any] | None = None,
    ) -> None:
        self.dependencies.record_attempt(
            account=account,
            mode=f"engine_{self.config.mode}",
            state=state,
            blocked_reasons=payload.get("blocked_reasons") or [],
            request_payload=payload.get("request") or {},
            quote=payload.get("dex_quote") or {},
            preflight=payload.get("preflight") or {},
            tx_hash=tx_hash,
            receipt=receipt,
            error=error,
        )
