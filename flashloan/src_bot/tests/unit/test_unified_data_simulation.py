from tools import run_unified_data_simulation as simulation


def _row(symbol: str, start: float, end: float) -> dict:
    return {
        "symbol": symbol,
        "start_price": start,
        "end_price": end,
        "current_price": end,
        "change_percent": (end / start - 1) * 100,
        "window_ready": True,
    }


def test_build_data_simulation_creates_local_only_profitable_paper_report():
    report = simulation.build_data_simulation(
        {
            "observed_at": "2026-08-13T00:00:00+00:00",
            "window_seconds": 10,
            "sample_count": 2,
            "price_source": "ws",
            "top": [_row("XUSDT", 10, 12)],
            "bottom": [_row("YUSDT", 10, 8)],
        },
        notional_usd=100,
        trade_fee_percent=0,
        flashloan_fee_percent=0,
        fee_reserve_percent=0,
        min_window_spread_percent=0,
        basket_size=1,
        gas_reserve_usdc=1,
        public_mempool_penalty_usdc=2,
        slippage_penalty_usdc=3,
        other_known_costs_usdc=4,
    )

    assert report["mode"] == "local_data_simulation"
    assert report["paperSimulation"]["signal"] is True
    assert report["profitEstimate"]["estimatedNetProfitUsdc"] > 0
    assert report["intentTradeDraft"]["direct_onchain_protocol"]["kind"] == (
        "unified_flashloan_mev_executor_runtime_v1"
    )
    assert report["intentTradeDraft"]["direct_onchain_ready"] is False
    assert report["deploymentEligible"] is False
    assert report["broadcastEligible"] is False
    assert "fork_static_call_not_verified" in report["blockedReasons"]
    assert report["nextRequiredStage"] == "export_runtime_trade_specs_then_run_avalanche_fork_static_call"


def test_build_data_simulation_preserves_block_when_observer_has_no_candidate():
    report = simulation.build_data_simulation(
        {
            "observed_at": "2026-08-13T00:00:00+00:00",
            "window_seconds": 1,
            "sample_count": 0,
            "price_source": "mixed",
            "top": [],
            "bottom": [],
        },
        notional_usd=100,
        trade_fee_percent=0.1,
        flashloan_fee_percent=0.05,
        fee_reserve_percent=0.1,
        min_window_spread_percent=0.3,
        basket_size=1,
    )

    assert report["paperSimulation"] is None
    assert report["intentTradeDraft"] is None
    assert report["profitEstimate"]["profitableOnPaper"] is False
    assert report["nextRequiredStage"] == "continue_observer_data_collection_and_quote_verification"
    assert "no_paper_route_from_current_snapshot" in report["blockedReasons"]
