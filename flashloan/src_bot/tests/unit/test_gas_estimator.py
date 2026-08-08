from web3 import Web3

from execution.gas_estimator import (
    build_gas_params,
    compare_gas_strategy_costs,
    estimate_gas_price,
    mempool_gas_price_distribution,
    recent_fee_distribution,
)


GWEI = 10**9


class FeeHistoryEth:
    gas_price = 25 * GWEI

    @staticmethod
    def get_block(block_id, full_transactions=False):
        if block_id == "latest":
            return {"number": 4, "baseFeePerGas": 35 * GWEI}
        if block_id == "pending":
            return {
                "transactions": [
                    {"gasPrice": 40 * GWEI},
                    {"maxFeePerGas": 90 * GWEI},
                    {"gasPrice": 120 * GWEI},
                ]
            }
        return {"baseFeePerGas": 30 * GWEI, "transactions": []}

    @staticmethod
    def fee_history(block_count, newest_block, reward_percentiles):
        return {
            "baseFeePerGas": [10 * GWEI, 20 * GWEI, 30 * GWEI, 40 * GWEI, 50 * GWEI],
            "reward": [
                [1 * GWEI, 2 * GWEI, 3 * GWEI],
                [2 * GWEI, 3 * GWEI, 4 * GWEI],
                [3 * GWEI, 4 * GWEI, 5 * GWEI],
                [4 * GWEI, 5 * GWEI, 6 * GWEI],
            ],
        }


class BlockFallbackEth:
    gas_price = 20 * GWEI

    @staticmethod
    def get_block(block_id, full_transactions=False):
        if block_id == "latest":
            return {"number": 3, "baseFeePerGas": 0}
        if block_id == "pending":
            raise RuntimeError("pending unsupported")
        return {
            "baseFeePerGas": 7 * GWEI,
            "transactions": [
                {"maxPriorityFeePerGas": 1 * GWEI},
                {"maxPriorityFeePerGas": 4 * GWEI},
            ],
        }

    @staticmethod
    def fee_history(block_count, newest_block, reward_percentiles):
        raise RuntimeError("fee history unsupported")


class FakeWeb3:
    def __init__(self, eth):
        self.eth = eth

    @staticmethod
    def to_wei(value, unit):
        return Web3.to_wei(value, unit)


def test_recent_fee_distribution_reads_fee_history_percentiles():
    distribution = recent_fee_distribution(FakeWeb3(FeeHistoryEth()), block_count=4)

    assert distribution["source"] == "fee_history"
    assert distribution["base_fee_percentiles"]["p75"] == 30 * GWEI
    assert distribution["priority_fee_percentiles"]["p75"] == 4 * GWEI


def test_estimate_gas_price_uses_pending_mempool_bid_when_higher():
    estimate = estimate_gas_price(
        FakeWeb3(FeeHistoryEth()),
        urgency="normal",
        max_gas_price_gwei=100,
        history_blocks=4,
        include_mempool=True,
    )

    assert estimate.strategy == "normal"
    assert estimate.max_fee == 90 * GWEI
    assert estimate.gas_price_gwei == 90.0
    assert estimate.mempool_gas_price_percentiles["p75"] == 90 * GWEI
    assert build_gas_params(estimate)["maxFeePerGas"] == 90 * GWEI


def test_estimate_gas_price_blocks_above_hard_cap():
    estimate = estimate_gas_price(
        FakeWeb3(FeeHistoryEth()),
        urgency="high",
        max_gas_price_gwei=50,
        history_blocks=4,
        include_mempool=True,
    )

    assert estimate.strategy == "blocked"
    assert estimate.max_fee == 0
    assert build_gas_params(estimate) == {}


def test_estimate_gas_price_uses_default_when_env_cap_is_invalid(monkeypatch):
    monkeypatch.setenv("LIQUIDATION_MAX_GAS_PRICE_GWEI", "bad")

    estimate = estimate_gas_price(
        FakeWeb3(FeeHistoryEth()),
        urgency="normal",
        history_blocks=4,
        include_mempool=True,
    )

    assert estimate.strategy == "normal"
    assert estimate.gas_price_gwei == 90.0


def test_recent_fee_distribution_falls_back_to_block_transactions():
    estimate = estimate_gas_price(
        FakeWeb3(BlockFallbackEth()),
        urgency="high",
        max_gas_price_gwei=100,
        history_blocks=3,
        include_mempool=True,
    )

    assert estimate.sample_source == "block_transactions"
    assert estimate.base_fee == 7 * GWEI
    assert estimate.priority_fee == 4 * GWEI
    assert estimate.max_fee == 18 * GWEI
    assert estimate.priority_fee_percentiles["p90"] == 4 * GWEI


def test_mempool_distribution_reports_unavailable_when_pending_unsupported():
    distribution = mempool_gas_price_distribution(FakeWeb3(BlockFallbackEth()))

    assert distribution["source"] == "unavailable"
    assert distribution["gas_price_percentiles"] == {"p50": 0, "p75": 0, "p90": 0}


def test_compare_gas_strategy_costs_reports_savings_against_baseline():
    estimate = estimate_gas_price(
        FakeWeb3(BlockFallbackEth()),
        urgency="normal",
        max_gas_price_gwei=100,
        history_blocks=3,
        include_mempool=False,
    )

    comparison = compare_gas_strategy_costs(
        gas_used=500_000,
        baseline_gas_price=30 * GWEI,
        optimized=estimate,
    )

    assert comparison["baseline_cost_wei"] == 15_000_000 * GWEI
    assert comparison["optimized_cost_wei"] == 9_000_000 * GWEI
    assert comparison["savings_wei"] == 6_000_000 * GWEI
    assert comparison["savings_percent"] == 40.0
