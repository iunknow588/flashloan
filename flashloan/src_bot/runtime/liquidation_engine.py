from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable


AccountLoader = Callable[[], list[str]]
PayloadBuilder = Callable[[str], dict[str, Any]]
StaticCallRunner = Callable[[dict[str, Any]], dict[str, Any]]
Submitter = Callable[[dict[str, Any]], dict[str, Any]]
AttemptRecorder = Callable[..., Any]
ControlsLoader = Callable[[], dict[str, Any]]
PriceSnapshotLoader = Callable[[], dict[str, float]]
AffectedAccountLoader = Callable[[list[str]], list[str]]


@dataclass(frozen=True)
class LiquidationEngineConfig:
    poll_interval_seconds: float = 30.0
    auto_execute: bool = False
    price_change_threshold_bps: int = 100
    max_accounts_per_tick: int = 100

    @property
    def mode(self) -> str:
        return "auto" if self.auto_execute else "observe"

    @classmethod
    def from_env(cls) -> "LiquidationEngineConfig":
        return cls(
            poll_interval_seconds=max(1.0, float(os.getenv("LIQUIDATION_ENGINE_POLL_SECONDS", "30"))),
            auto_execute=os.getenv("LIQUIDATION_AUTO_EXECUTE", "false").strip().lower() in {"1", "true", "yes", "on"},
            price_change_threshold_bps=max(1, int(os.getenv("LIQUIDATION_PRICE_TRIGGER_BPS", "100") or 100)),
            max_accounts_per_tick=max(1, int(os.getenv("LIQUIDATION_ENGINE_MAX_ACCOUNTS", "100") or 100)),
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

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            await asyncio.sleep(self.config.poll_interval_seconds)

    def run_in_thread(self, *, name: str = "liquidation-engine") -> threading.Thread:
        thread = threading.Thread(target=lambda: asyncio.run(self.run_forever()), name=name, daemon=True)
        thread.start()
        return thread

    def price_triggered_accounts(self) -> tuple[list[str], list[str]]:
        loader = self.dependencies.load_price_snapshot
        affected_loader = self.dependencies.load_affected_accounts
        if loader is None or affected_loader is None:
            return [], []

        current = loader() or {}
        changed_assets: list[str] = []
        for asset, price in current.items():
            previous = self._last_prices.get(asset)
            self._last_prices[asset] = float(price)
            if previous is None or previous <= 0:
                continue
            change_bps = abs(float(price) - previous) / previous * 10000
            if change_bps >= self.config.price_change_threshold_bps:
                changed_assets.append(asset)
        if not changed_assets:
            return [], []
        return changed_assets, affected_loader(changed_assets)

    def run_once(self, accounts: list[str] | None = None) -> dict[str, Any]:
        controls = self.dependencies.load_controls()
        if controls.get("auto_pause_active") or int(controls.get("circuit_breaker_level") or 0) >= 3:
            return {
                "mode": self.config.mode,
                "state": "paused",
                "processed": [],
                "blocked_reasons": ["auto_pause_active"],
                "controls": controls,
            }

        changed_assets, event_accounts = self.price_triggered_accounts()
        candidate_accounts = accounts or event_accounts or self.dependencies.load_accounts()
        candidate_accounts = list(dict.fromkeys(candidate_accounts))[: self.config.max_accounts_per_tick]
        processed = [self.process_account(account) for account in candidate_accounts]
        return {
            "mode": self.config.mode,
            "state": "completed",
            "trigger": "price_event" if event_accounts else "poll",
            "changed_assets": changed_assets,
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
            if blocked:
                self._record(account, state="submission_blocked", payload=static_result)
                return {"account": account, "state": "submission_blocked", "blocked_reasons": blocked}
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
            self._record(account, state="submission_failed", payload=payload or {}, error=str(exc))
            return {"account": account, "state": "submission_failed", "error": str(exc)}

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
