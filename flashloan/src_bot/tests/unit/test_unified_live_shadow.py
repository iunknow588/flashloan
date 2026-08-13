from tools import run_unified_live_shadow as shadow


def test_window_report_counts_invalid_samples_and_positive_signals(tmp_path):
    report = shadow._write_window_report(
        path=tmp_path / "report.json",
        run_id="run-1",
        input_path=tmp_path / "input.json",
        started_at="2026-08-13T00:00:00+00:00",
        stopped_at="2026-08-13T00:00:01+00:00",
        input_refresh_count=2,
        live_signal_only=True,
        samples=[
            {
                "file": "signals/000000.json",
                "result": {
                    "status": "static_call_passed",
                    "statusFamily": "shadow_static_success",
                    "selectedStrategyStatus": "4",
                    "selectedExecutionKind": "1",
                },
                "validation": {"ok": True, "errors": [], "disposition": "manual_review_required", "manualReviewThresholdBps": "7000"},
                "evidenceHash": "sha256:" + "1" * 64,
                "manualReviewThresholdBps": "7000",
            },
            {
                "file": "signals/000001.json",
                "result": {"status": "static_call_passed", "statusFamily": "shadow_static_success"},
                "validation": {"ok": False, "errors": ["runtime_trades_empty"]},
                "evidenceHash": "sha256:" + "2" * 64,
            },
        ],
    )

    assert report["validSampleCount"] == 1
    assert report["invalidSampleCount"] == 1
    assert report["manualReviewSampleCount"] == 1
    assert report["manualReviewRate"] == 0.5
    assert report["manualReviewThresholdBps"] == 7000
    assert report["manualReviewThresholdRationale"]
    assert report["manualReviewThresholdAdjustmentHistory"] == []
    assert report["positiveSignalCount"] == 1
    assert report["statusCounts"] == {"static_call_passed": 2}
    assert report["statusFamilyCounts"] == {"shadow_static_success": 2}
    assert report["strategyStatusCounts"] == {"4": 1}
    assert report["executionKindCounts"] == {"1": 1}
    assert report["orderOptimization"]["currentWindowSignal"] is False
    assert report["orderOptimization"]["independenceDefinition"].startswith(
        "non_overlapping_completed_runs"
    )
    assert report["orderOptimizationRecommended"] is False
    assert report["marketFeasibility"]["conclusion"] == "observe_longer"
    assert report["marketFeasibility"]["competitorPressure"]["confidence"] == "insufficient_data"
    assert report["minCapturableWindowMs"] == {"p50": None, "p95": None, "max": None}
    assert report["evidenceSemantics"]["evidenceEligible"] is False
    assert report["runClassification"] == "live_shadow_window_contains_invalid_samples"


def test_window_report_marks_all_valid_samples_evidence_eligible(tmp_path):
    report = shadow._write_window_report(
        path=tmp_path / "report.json",
        run_id="run-valid",
        input_path=tmp_path / "input.json",
        started_at="2026-08-13T00:00:00+00:00",
        stopped_at="2026-08-13T00:00:01+00:00",
        input_refresh_count=1,
        live_signal_only=True,
        samples=[
            {
                "file": "signals/000000.json",
                "result": {"status": "static_call_passed", "statusFamily": "shadow_static_success"},
                "validation": {"ok": True, "errors": []},
                "evidenceHash": "sha256:" + "1" * 64,
            }
        ],
    )

    assert report["evidenceSemantics"]["evidenceEligible"] is True
    assert report["runClassification"] == "live_shadow_candidate_evidence"


def test_window_report_groups_blocked_and_diagnostic_statuses(tmp_path):
    report = shadow._write_window_report(
        path=tmp_path / "report.json",
        run_id="run-families",
        input_path=tmp_path / "input.json",
        started_at="2026-08-13T00:00:00+00:00",
        stopped_at="2026-08-13T00:00:01+00:00",
        input_refresh_count=4,
        live_signal_only=True,
        samples=[
            {"file": "signals/000000.json", "result": {"status": "broadcast_disabled", "statusFamily": "broadcast_blocked"}, "validation": {"ok": False}},
            {"file": "signals/000001.json", "result": {"status": "gas_price_cap_exceeded", "statusFamily": "broadcast_blocked"}, "validation": {"ok": False}},
            {"file": "signals/000002.json", "result": {"status": "direct_protocol_incomplete", "statusFamily": "diagnostic"}, "validation": {"ok": False}},
            {"file": "signals/000003.json", "result": {"status": "unknown", "statusFamily": "unknown"}, "validation": {"ok": False}},
        ],
    )

    assert report["statusFamilyCounts"] == {
        "broadcast_blocked": 2,
        "diagnostic": 1,
        "unknown": 1,
    }


def test_window_report_marks_live_input_without_valid_samples_ineligible(tmp_path):
    report = shadow._write_window_report(
        path=tmp_path / "report.json",
        run_id="run-invalid",
        input_path=tmp_path / "input.json",
        started_at="2026-08-13T00:00:00+00:00",
        stopped_at="2026-08-13T00:00:01+00:00",
        input_refresh_count=1,
        live_signal_only=True,
        samples=[
            {
                "file": "signals/000000.json",
                "result": {"status": "direct_protocol_incomplete", "statusFamily": "diagnostic"},
                "validation": {"ok": False, "errors": ["runtime_trades_empty"]},
                "evidenceHash": "sha256:" + "3" * 64,
            }
        ],
    )

    assert report["evidenceSemantics"]["evidenceEligible"] is False
    assert report["runClassification"] == "live_input_without_valid_shadow_samples"
    assert report["zeroSignalReason"] == "shadow_samples_failed_schema_validation"


def test_window_report_keeps_order_optimization_as_multi_window_evidence(tmp_path):
    report = shadow._write_window_report(
        path=tmp_path / "report.json",
        run_id="run-order",
        input_path=tmp_path / "input.json",
        started_at="2026-08-13T00:00:00+00:00",
        stopped_at="2026-08-13T00:00:01+00:00",
        input_refresh_count=4,
        live_signal_only=True,
        samples=[
            {
                "file": f"signals/{index:06d}.json",
                "result": {"status": "static_call_passed", "selectedStrategyStatus": "4"},
                "validation": {"ok": True, "errors": []},
                "evidenceHash": "sha256:" + str(index + 1) * 64,
            }
            for index in range(3)
        ],
    )

    assert report["orderOptimization"]["currentWindowSignal"] is True
    assert report["orderOptimization"]["requiredIndependentWindows"] == 3
    assert report["orderOptimization"]["independentWindowsObserved"] == 1
    assert report["orderOptimizationRecommended"] is False


def test_window_report_carries_market_feasibility_from_samples(tmp_path):
    report = shadow._write_window_report(
        path=tmp_path / "report.json",
        run_id="run-market",
        input_path=tmp_path / "input.json",
        started_at="2026-08-13T00:00:00+00:00",
        stopped_at="2026-08-13T00:00:01+00:00",
        input_refresh_count=1,
        live_signal_only=True,
        samples=[
            {
                "file": "signals/000000.json",
                "result": {"status": "static_call_passed"},
                "validation": {"ok": True, "errors": []},
                "evidenceHash": "sha256:" + "4" * 64,
                "marketFeasibility": {
                    "targetPairLiquidity": {"BTC.b/WAVAX": {"min": "1"}},
                    "volatilitySummary": {"windowSeconds": 300},
                    "competitorPressure": {
                        "observations": [],
                        "inferenceOnly": True,
                        "confidence": "low_confidence",
                        "emptyObservationMeaning": "no_sufficient_data_to_infer_competitor_pressure",
                    },
                    "expectedProfitDistribution": {"positiveCount": 1},
                    "gasPriceRegime": {"p50Gwei": "25"},
                    "strategyDifferentiation": ["baseline=public_mempool_triangular_bot_without_multi_state_redundancy"],
                    "conclusion": "positive_signal_requires_review",
                },
            }
        ],
    )

    assert report["marketFeasibility"]["conclusion"] == "positive_signal_requires_review"
    assert report["marketFeasibility"]["source"] == "last_sample_market_feasibility"


def test_window_market_feasibility_does_not_infer_low_frequency_from_candidate_count():
    report = shadow._window_market_feasibility(
        [
            {
                "candidateCount": 2,
                "validation": {"ok": True},
                "marketFeasibility": {
                    "expectedProfitDistribution": {
                        "sampleSufficiency": "low_frequency_market",
                        "candidateGenerationRatePerHour": 2.0,
                    },
                    "candidateGeneration": {
                        "listenerHealth": "healthy",
                        "rpcHealth": "healthy",
                        "cacheHealth": "healthy",
                    },
                },
            }
        ],
        True,
        started_at="2026-08-13T00:00:00+00:00",
        stopped_at="2026-08-13T01:00:00+00:00",
    )

    assert report["expectedProfitDistribution"]["sampleSufficiency"] == "insufficient"
    assert report["expectedProfitDistribution"]["lowFrequencyConfirmation"] is None
