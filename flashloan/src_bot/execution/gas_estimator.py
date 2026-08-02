from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from web3 import Web3

from core.config_schema import parse_env_float


@dataclass(frozen=True)
class GasEstimate:
    base_fee: int
    priority_fee: int
    max_fee: int
    strategy: str
    gas_price_gwei: float
    base_fee_percentiles: dict[str, int] | None = None
    priority_fee_percentiles: dict[str, int] | None = None
    mempool_gas_price_percentiles: dict[str, int] | None = None
    sample_source: str = "fallback"


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _percentiles(values: list[int]) -> dict[str, int]:
    cleaned = sorted(int(item) for item in values if int(item or 0) >= 0)
    if not cleaned:
        return {"p50": 0, "p75": 0, "p90": 0}
    last = len(cleaned) - 1
    return {
        "p50": cleaned[min(last, int(last * 0.50))],
        "p75": cleaned[min(last, int(last * 0.75))],
        "p90": cleaned[min(last, int(last * 0.90))],
    }


def _urgency_percentile(urgency: str) -> str:
    return {"low": "p50", "normal": "p75", "high": "p90"}.get(urgency, "p75")


def _tx_priority_fee(tx: Any) -> int | None:
    value = _field(tx, "maxPriorityFeePerGas")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _tx_gas_price(tx: Any) -> int | None:
    value = _field(tx, "gasPrice")
    if value is None:
        value = _field(tx, "maxFeePerGas")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def recent_fee_distribution(
    w3: Web3,
    *,
    block_count: int = 20,
    latest_block: Any | None = None,
) -> dict[str, Any]:
    latest = latest_block if latest_block is not None else w3.eth.get_block("latest")
    latest_number = int(_field(latest, "number", 0) or 0)
    sample_count = max(1, min(int(block_count), max(1, latest_number)))

    try:
        history = w3.eth.fee_history(sample_count, "latest", [50, 75, 90])
        base_fees = [int(item) for item in history.get("baseFeePerGas", [])[:sample_count]]
        rewards = history.get("reward", []) or []
        priority_fees = [int(row[1]) for row in rewards if isinstance(row, (list, tuple)) and len(row) >= 2]
        if base_fees or priority_fees:
            return {
                "source": "fee_history",
                "base_fee_percentiles": _percentiles(base_fees),
                "priority_fee_percentiles": _percentiles(priority_fees),
                "base_fee_samples": base_fees,
                "priority_fee_samples": priority_fees,
            }
    except Exception:
        pass

    base_fees: list[int] = []
    priority_fees: list[int] = []
    for i in range(sample_count):
        try:
            block = w3.eth.get_block(latest_number - i, full_transactions=True)
        except Exception:
            continue
        base_fee = _field(block, "baseFeePerGas")
        if base_fee is not None:
            base_fees.append(int(base_fee))
        for tx in _field(block, "transactions", []) or []:
            priority_fee = _tx_priority_fee(tx)
            if priority_fee is not None:
                priority_fees.append(priority_fee)

    return {
        "source": "block_transactions",
        "base_fee_percentiles": _percentiles(base_fees),
        "priority_fee_percentiles": _percentiles(priority_fees),
        "base_fee_samples": base_fees,
        "priority_fee_samples": priority_fees,
    }


def mempool_gas_price_distribution(w3: Web3) -> dict[str, Any]:
    try:
        pending = w3.eth.get_block("pending", full_transactions=True)
    except Exception:
        return {"source": "unavailable", "gas_price_percentiles": _percentiles([]), "gas_price_samples": []}

    gas_prices: list[int] = []
    for tx in _field(pending, "transactions", []) or []:
        gas_price = _tx_gas_price(tx)
        if gas_price is not None:
            gas_prices.append(gas_price)
    return {
        "source": "pending_block",
        "gas_price_percentiles": _percentiles(gas_prices),
        "gas_price_samples": gas_prices,
    }


def estimate_gas_price(
    w3: Web3,
    *,
    urgency: str = "normal",
    max_gas_price_gwei: float | None = None,
    history_blocks: int = 20,
    include_mempool: bool = True,
) -> GasEstimate:
    """Estimate EIP-1559 gas params from recent fee history and pending txs."""
    if max_gas_price_gwei is None:
        max_gas_price_gwei, _ = parse_env_float("LIQUIDATION_MAX_GAS_PRICE_GWEI", 100, minimum=0)

    latest = w3.eth.get_block("latest")
    distribution = recent_fee_distribution(w3, block_count=history_blocks, latest_block=latest)
    base_fee_percentiles = distribution["base_fee_percentiles"]
    priority_fee_percentiles = distribution["priority_fee_percentiles"]
    key = _urgency_percentile(urgency)

    latest_base_fee = int(_field(latest, "baseFeePerGas", 0) or 0)
    base_fee = int(base_fee_percentiles.get(key) or latest_base_fee)
    if latest_base_fee > 0:
        base_fee = max(base_fee, latest_base_fee)

    priority_fee = int(priority_fee_percentiles.get(key) or w3.to_wei(1.5, "gwei"))
    if base_fee > 0:
        max_fee = base_fee * 2 + priority_fee
    else:
        max_fee = int(w3.eth.gas_price)
        priority_fee = 0

    mempool_percentiles: dict[str, int] | None = None
    sample_source = str(distribution.get("source") or "fallback")
    if include_mempool:
        mempool = mempool_gas_price_distribution(w3)
        mempool_percentiles = mempool["gas_price_percentiles"]
        mempool_bid = int(mempool_percentiles.get(key) or 0)
        if mempool_bid > 0:
            max_fee = max(max_fee, mempool_bid)
            sample_source = f"{sample_source}+{mempool['source']}"

    gas_price_gwei = float(Web3.from_wei(max_fee, "gwei"))
    if gas_price_gwei > max_gas_price_gwei:
        return GasEstimate(
            base_fee=base_fee,
            priority_fee=priority_fee,
            max_fee=0,
            strategy="blocked",
            gas_price_gwei=gas_price_gwei,
            base_fee_percentiles=base_fee_percentiles,
            priority_fee_percentiles=priority_fee_percentiles,
            mempool_gas_price_percentiles=mempool_percentiles,
            sample_source=sample_source,
        )

    return GasEstimate(
        base_fee=base_fee,
        priority_fee=priority_fee,
        max_fee=max_fee,
        strategy=urgency,
        gas_price_gwei=gas_price_gwei,
        base_fee_percentiles=base_fee_percentiles,
        priority_fee_percentiles=priority_fee_percentiles,
        mempool_gas_price_percentiles=mempool_percentiles,
        sample_source=sample_source,
    )


def build_gas_params(estimate: GasEstimate) -> dict[str, Any]:
    """Convert GasEstimate to web3 transaction params."""
    if estimate.strategy == "blocked":
        return {}
    if estimate.base_fee > 0:
        return {
            "maxFeePerGas": estimate.max_fee,
            "maxPriorityFeePerGas": estimate.priority_fee,
        }
    return {"gasPrice": estimate.max_fee}


def compare_gas_strategy_costs(
    *,
    gas_used: int,
    baseline_gas_price: int,
    optimized: GasEstimate,
) -> dict[str, Any]:
    gas_units = max(0, int(gas_used or 0))
    baseline_cost = gas_units * max(0, int(baseline_gas_price or 0))
    optimized_cost = gas_units * max(0, int(optimized.max_fee or 0))
    savings = baseline_cost - optimized_cost
    savings_percent = 0.0
    if baseline_cost > 0:
        savings_percent = savings / baseline_cost * 100.0
    return {
        "gas_used": gas_units,
        "baseline_cost_wei": baseline_cost,
        "optimized_cost_wei": optimized_cost,
        "savings_wei": savings,
        "savings_percent": savings_percent,
        "optimized_strategy": optimized.strategy,
        "optimized_sample_source": optimized.sample_source,
    }
