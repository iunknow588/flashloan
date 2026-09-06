from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiquidationRefreshRuntime:
    get_thread: Callable[[], Any]
    set_thread: Callable[[Any], None]
    thread_factory: Callable[..., Any]
    stop_event: Any
    enabled: Callable[[], bool]
    discover_accounts: Callable[..., dict]
    scan_borrow_pool: Callable[..., dict]
    discovery_cache: dict[str, Any]
    backfill_interval_seconds: Callable[[], float]
    cooldown_seconds: Callable[[], float]


def start_liquidation_refresh_loop(runtime: LiquidationRefreshRuntime) -> None:
    current_thread = runtime.get_thread()
    if current_thread is not None and current_thread.is_alive():
        return
    if not runtime.enabled():
        return

    def runner() -> None:
        while not runtime.stop_event.is_set():
            try:
                runtime.discover_accounts(force_full=False)
                runtime.scan_borrow_pool(force=False)
                last_backfill_at = float(
                    runtime.discovery_cache.get("last_backfill_monotonic") or 0.0
                )
                if (
                    time.monotonic() - last_backfill_at
                    >= runtime.backfill_interval_seconds()
                ):
                    runtime.discover_accounts(force_full=True)
            except Exception:
                logger.warning("liquidation refresh loop iteration failed", exc_info=True)
                pass
            runtime.stop_event.wait(runtime.cooldown_seconds())

    thread = runtime.thread_factory(
        target=runner,
        name="liquidation-refresh",
        daemon=True,
    )
    runtime.set_thread(thread)
    thread.start()
