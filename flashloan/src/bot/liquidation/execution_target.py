from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from web3 import Web3


@dataclass(frozen=True)
class LiquidationExecutionTarget:
    account: str
    borrow_index: int | None = None
    candidate_index: int | None = None
    market_id: str | None = None




def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_execution_target(
    account: str,
    *,
    borrow_index: Any = None,
    candidate_index: Any = None,
    market_id: Any = None,
) -> LiquidationExecutionTarget:
    normalized_borrow_index = _int_or_none(borrow_index)
    normalized_candidate_index = _int_or_none(candidate_index)
    if normalized_borrow_index is not None and normalized_borrow_index < 1:
        raise ValueError("borrow_index must be greater than zero")
    if normalized_candidate_index is not None and normalized_candidate_index < 0:
        raise ValueError("candidate_index must be zero or greater")
    if normalized_borrow_index is not None and normalized_candidate_index is not None:
        expected_candidate_index = normalized_borrow_index - 1
        if normalized_candidate_index != expected_candidate_index:
            raise ValueError("borrow_index and candidate_index refer to different liquidation candidates")
    return LiquidationExecutionTarget(
        account=Web3.to_checksum_address(str(account or "").strip()),
        borrow_index=normalized_borrow_index,
        candidate_index=normalized_candidate_index,
        market_id=str(market_id or "").strip().lower() or None,
    )


def _candidate_index(target: LiquidationExecutionTarget) -> int | None:
    if target.borrow_index is not None:
        return target.borrow_index - 1
    return target.candidate_index


def _target_hash(
    target: LiquidationExecutionTarget,
    *,
    candidate: dict[str, Any],
    candidate_index: int,
) -> str:
    debt_to_cover = (
        candidate.get("amount_to_pass_to_liquidation_call")
        or candidate.get("max_debt_to_liquidate")
        or ""
    )
    parts = (
        target.account.lower(),
        target.market_id or "",
        str(candidate_index),
        str(candidate.get("collateral_asset") or "").lower(),
        str(candidate.get("debt_asset") or "").lower(),
        str(debt_to_cover),
    )
    return sha256("|".join(parts).encode("utf-8")).hexdigest()


def select_execution_candidate(
    report: dict[str, Any],
    *,
    target: LiquidationExecutionTarget,
) -> tuple[dict[str, Any], dict[str, Any]]:
    candidates = report.get("liquidation_candidates") or []
    index = _candidate_index(target)
    if index is None:
        candidate = report.get("recommended_candidate") or (candidates[0] if candidates else None)
        if not candidate:
            raise ValueError("no executable liquidation candidate; a debt/collateral pair is required")
        index = next((item_index for item_index, item in enumerate(candidates) if item is candidate), 0)
    else:
        if not isinstance(candidates, list) or index >= len(candidates):
            raise ValueError(f"liquidation candidate index {index} is out of range")
        candidate = candidates[index]
    return dict(candidate), {
        "account": target.account,
        "market_id": target.market_id,
        "borrow_index": index + 1,
        "candidate_index": index,
        "target_hash": _target_hash(target, candidate=candidate, candidate_index=index),
        "collateral_asset": candidate.get("collateral_asset"),
        "debt_asset": candidate.get("debt_asset"),
        "debt_to_cover": str(
            candidate.get("amount_to_pass_to_liquidation_call")
            or candidate.get("max_debt_to_liquidate")
            or ""
        ),
    }
