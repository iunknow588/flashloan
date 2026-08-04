from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class _TextEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class PageName(_TextEnum):
    DEBT_POOL = "debt_pool"
    ACCOUNT_SCAN = "account_scan"
    MARKET_OBSERVATION = "market_observation"
    EXECUTION = "execution"
    AUDIT = "audit"
    CONFIG = "config"


class DebtPoolStatus(_TextEnum):
    ENTER = "ENTER"
    CHECKING_DATA = "CHECKING_DATA"
    CHECKING_ACCOUNT_POOL = "CHECKING_ACCOUNT_POOL"
    NEED_ACCOUNT_POOL = "NEED_ACCOUNT_POOL"
    MARKET_ALERT_RECEIVED = "MARKET_ALERT_RECEIVED"
    SCANNING_CORE_POOL = "SCANNING_CORE_POOL"
    CORE_LIQUIDATION_DECISION = "CORE_LIQUIDATION_DECISION"
    SCANNING_HIGH_FREQUENCY_POOL = "SCANNING_HIGH_FREQUENCY_POOL"
    SCANNING_NORMAL_POOL = "SCANNING_NORMAL_POOL"
    SYNCING_CORE_POOL = "SYNCING_CORE_POOL"
    COMPLETED = "COMPLETED"
    IDLE_FRESH = "IDLE_FRESH"
    IDLE_STALE = "IDLE_STALE"
    ERROR = "ERROR"


class AccountScanStatus(_TextEnum):
    ENTER = "ENTER"
    LOADING_ACCOUNT_POOL = "LOADING_ACCOUNT_POOL"
    SHOWING_ACCOUNT_POOL = "SHOWING_ACCOUNT_POOL"
    PREPARING = "PREPARING"
    RESOLVING_WINDOW = "RESOLVING_WINDOW"
    RESOLVING_BLOCKS = "RESOLVING_BLOCKS"
    SCANNING_EVENTS = "SCANNING_EVENTS"
    SAVING_ACCOUNTS = "SAVING_ACCOUNTS"
    PREPARING_BACKFILL = "PREPARING_BACKFILL"
    RESOLVING_BACKFILL_RANGE = "RESOLVING_BACKFILL_RANGE"
    SCANNING_HISTORICAL_EVENTS = "SCANNING_HISTORICAL_EVENTS"
    VERIFYING_ACCOUNT_POOL = "VERIFYING_ACCOUNT_POOL"
    AUTO_COMPLETED = "AUTO_COMPLETED"
    MANUAL_COMPLETED = "MANUAL_COMPLETED"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


class MarketObservationStatus(_TextEnum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    OBSERVING = "OBSERVING"
    VOLATILITY_DETECTED = "VOLATILITY_DETECTED"
    ALERTING_DEBT_POOL = "ALERTING_DEBT_POOL"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


class ExecutionStatus(_TextEnum):
    IDLE = "IDLE"
    CONTEXT_RECEIVED = "CONTEXT_RECEIVED"
    LOADING_ACCOUNT = "LOADING_ACCOUNT"
    BUILDING_PREDICTION = "BUILDING_PREDICTION"
    BUILDING_QUOTE = "BUILDING_QUOTE"
    BUILDING_PAYLOAD = "BUILDING_PAYLOAD"
    READY_FOR_PREFLIGHT = "READY_FOR_PREFLIGHT"
    PREFLIGHTING = "PREFLIGHTING"
    READY_TO_SUBMIT = "READY_TO_SUBMIT"
    SUBMITTING = "SUBMITTING"
    SUBMITTING_FORCE = "SUBMITTING_FORCE"
    SUBMITTING_AUTO = "SUBMITTING_AUTO"
    WAITING_RECEIPT = "WAITING_RECEIPT"
    SOFT_BLOCKED = "SOFT_BLOCKED"
    HARD_BLOCKED = "HARD_BLOCKED"
    SUCCESS = "SUCCESS"
    CONFIRMED_FAILED = "CONFIRMED_FAILED"
    AUTO_SCAN_STARTING = "AUTO_SCAN_STARTING"
    SCANNING_OPPORTUNITIES = "SCANNING_OPPORTUNITIES"
    OPPORTUNITY_FOUND = "OPPORTUNITY_FOUND"
    NO_OPPORTUNITY = "NO_OPPORTUNITY"
    SCAN_ERROR = "SCAN_ERROR"
    ERROR = "ERROR"


class AccountPoolResult(_TextEnum):
    MISSING = "ACCOUNT_POOL_MISSING"
    EMPTY = "ACCOUNT_POOL_EMPTY"
    INCOMPLETE = "ACCOUNT_POOL_INCOMPLETE"
    READY = "ACCOUNT_POOL_READY"


class DebtPoolScanResult(_TextEnum):
    CORE_POOL_LIQUIDATABLE = "CORE_POOL_LIQUIDATABLE"
    CORE_POOL_NOT_LIQUIDATABLE = "CORE_POOL_NOT_LIQUIDATABLE"
    HIGH_FREQUENCY_RISK_FOUND = "HIGH_FREQUENCY_RISK_FOUND"
    NO_HIGH_FREQUENCY_RISK = "NO_HIGH_FREQUENCY_RISK"
    NORMAL_POOL_RISK_FOUND = "NORMAL_POOL_RISK_FOUND"
    NO_NORMAL_POOL_RISK = "NO_NORMAL_POOL_RISK"
    SCAN_FAILED = "SCAN_FAILED"


class ExecutionBlockReason(_TextEnum):
    EXECUTION_DISABLED = "execution_disabled"
    AUTO_PAUSE_ACTIVE = "auto_pause_active"
    CONFIG_INVALID = "config_invalid"
    CHAIN_ID_MISMATCH = "chain_id_mismatch"
    PRIVATE_KEY_MISMATCH = "private_key_mismatch"
    MISSING_EXECUTOR = "missing_executor"
    MISSING_OWNER = "missing_owner"
    MISSING_SELF_FUNDED_KEY = "missing_self_funded_key"
    ACCOUNT_NOT_LIQUIDATABLE = "account_not_liquidatable"
    NO_LIQUIDATION_CANDIDATE = "no_liquidation_candidate"
    INVALID_DEBT_TO_COVER = "invalid_debt_to_cover"
    DEBT_EXCEEDS_LIMIT = "debt_exceeds_limit"
    PAYLOAD_EXPIRED = "payload_expired"
    DEADLINE_TOO_CLOSE = "deadline_too_close"
    STATIC_CALL_REQUIRED = "static_call_required"
    STATIC_CALL_FAILED = "static_call_failed"
    FORK_SIMULATION_REQUIRED = "fork_simulation_required"
    FORK_SIMULATION_FAILED = "fork_simulation_failed"
    PROFIT_BELOW_MINIMUM = "profit_below_minimum"
    GAS_COST_TOO_HIGH = "gas_cost_too_high"
    QUOTE_EXPIRED = "quote_expired"
    QUOTE_FAILED = "quote_failed"
    FALLBACK_CLOSE_FACTOR = "fallback_close_factor"
    FALLBACK_FLASHLOAN_PREMIUM = "fallback_flashloan_premium"


class BlockLevel(_TextEnum):
    NONE = "none"
    SOFT = "soft"
    HARD = "hard"


def receipt_status(receipt: dict | None) -> int | None:
    try:
        if receipt and receipt.get("status") is not None:
            return int(receipt.get("status"))
    except (TypeError, ValueError):
        return None
    return None


def _first_text_value(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        if hasattr(value, "hex"):
            value = value.hex()
        text = str(value).strip()
        if text:
            return text
    return None


def normalize_tx_hash(row: dict | None) -> str | None:
    row = row or {}
    preflight = row.get("preflight") if isinstance(row.get("preflight"), dict) else {}
    context = row.get("context") if isinstance(row.get("context"), dict) else {}
    preflight_context = preflight.get("context") if isinstance(preflight.get("context"), dict) else {}
    receipt = row.get("receipt") if isinstance(row.get("receipt"), dict) else {}
    return _first_text_value(
        row.get("tx_hash"),
        row.get("txHash"),
        preflight.get("tx_hash"),
        preflight.get("txHash"),
        context.get("tx_hash"),
        context.get("txHash"),
        preflight_context.get("tx_hash"),
        preflight_context.get("txHash"),
        receipt.get("transaction_hash"),
        receipt.get("transactionHash"),
        receipt.get("tx_hash"),
        receipt.get("txHash"),
    )


def normalize_execution_phase(row: dict | None, fallback_state: str | None = None) -> str | None:
    row = row or {}
    preflight = row.get("preflight") if isinstance(row.get("preflight"), dict) else {}
    context = row.get("context") if isinstance(row.get("context"), dict) else {}
    preflight_context = preflight.get("context") if isinstance(preflight.get("context"), dict) else {}
    phase = (
        row.get("execution_phase")
        or row.get("phase")
        or preflight.get("execution_phase")
        or context.get("execution_phase")
        or context.get("phase")
        or preflight_context.get("execution_phase")
        or preflight_context.get("phase")
    )
    if phase:
        return str(phase)
    status = receipt_status(row.get("receipt") if isinstance(row.get("receipt"), dict) else None)
    if status == 1:
        return "confirmed_success"
    if status == 0:
        return "confirmed_failed"
    state = row.get("state") or fallback_state
    return str(state) if state else None


@dataclass(frozen=True)
class RouteIntent:
    source_page: str
    target_page: str
    reason: str
    event_id: str | None = None
    context_version: str | None = None
    created_at: str | None = None
    context: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_page": self.source_page,
            "target_page": self.target_page,
            "reason": self.reason,
            "event_id": self.event_id,
            "context_version": self.context_version,
            "created_at": self.created_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "context": dict(self.context or {}),
        }
