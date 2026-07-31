from pathlib import Path

from execution.liquidation_samples import (
    build_liquidation_sample_manifest,
    classify_liquidation_sample_label,
    serialize_liquidation_failure_samples,
    write_liquidation_sample_library,
)


def _failure_report(label: str, account: str) -> dict:
    base = {
        "account": account,
        "summary": {"status": "liquidatable", "health_factor": 0.98},
        "recommended_candidate": {
            "collateral_asset": "0xcoll",
            "debt_asset": "0xdebt",
            "amount_to_pass_to_liquidation_call": 500,
            "estimated_profit": {"net_profit_base": 10.0},
        },
    }
    if label == "close_factor_failure":
        base["recommended_candidate"]["estimated_profit"]["repay_base_source"] = "close_factor_fallback"
        base["execution_plan"] = {"reason": "close factor exceeds current Aave limit"}
    elif label == "dust_leftover":
        base["recommended_candidate"]["amount_to_pass_to_liquidation_call"] = 20
    elif label == "low_profit":
        base["recommended_candidate"]["estimated_profit"]["net_profit_base"] = 3.5
    elif label == "high_slippage_failure":
        base["preflight"] = {"static_call_status": "error", "static_call_error": "AmountOutMin slippage exceeded"}
    return base


def test_classify_liquidation_sample_label_prefers_status():
    assert classify_liquidation_sample_label({"summary": {"status": "healthy"}}) == "healthy"
    assert classify_liquidation_sample_label({"summary": {"status": "warning"}}) == "warning"


def test_classify_liquidation_sample_label_uses_profit_and_fallbacks():
    assert (
        classify_liquidation_sample_label(
            {
                "summary": {"status": "liquidatable", "health_factor": 0.98},
                "recommended_candidate": {
                    "amount_to_pass_to_liquidation_call": 80,
                    "estimated_profit": {"net_profit_base": 2.0, "repay_base_source": "close_factor_fallback"},
                },
            }
        )
        == "close_factor_failure"
    )
    assert (
        classify_liquidation_sample_label(
            {
                "summary": {"status": "liquidatable", "health_factor": 0.98},
                "recommended_candidate": {
                    "amount_to_pass_to_liquidation_call": 20,
                    "estimated_profit": {"net_profit_base": 10.0},
                },
            }
        )
        == "dust_leftover"
    )
    assert (
        classify_liquidation_sample_label(
            {
                "summary": {"status": "liquidatable", "health_factor": 0.98},
                "recommended_candidate": {
                    "amount_to_pass_to_liquidation_call": 500,
                    "estimated_profit": {"net_profit_base": 3.5},
                },
            }
        )
        == "low_profit"
    )
    assert classify_liquidation_sample_label(_failure_report("high_slippage_failure", "0x4")) == "high_slippage_failure"


def test_build_liquidation_sample_manifest_keeps_placeholders():
    manifest = build_liquidation_sample_manifest(
        [
            {"account": "0x1", "summary": {"status": "healthy", "health_factor": 1.2}},
            {"account": "0x2", "summary": {"status": "warning", "health_factor": 1.04}},
        ]
    )

    labels = [item["label"] for item in manifest["samples"]]
    assert labels == [
        "healthy",
        "warning",
        "liquidatable",
        "close_factor_failure",
        "dust_leftover",
        "low_profit",
        "high_slippage_failure",
    ]
    assert manifest["samples"][0]["status"] == "ready"
    assert manifest["samples"][2]["status"] == "pending_real_sample"


def test_write_liquidation_sample_library_writes_index(tmp_path: Path):
    output_dir = tmp_path / "samples"
    index = write_liquidation_sample_library(
        [
            {"account": "0x1", "summary": {"status": "healthy", "health_factor": 1.2}},
            {"account": "0x2", "summary": {"status": "warning", "health_factor": 1.04}},
        ],
        output_dir,
    )

    assert (output_dir / "index.json").exists()
    assert index["schema_version"] == 1
    assert index["samples"][0]["status"] == "ready"


def test_serialize_liquidation_failure_samples_records_four_failure_classes():
    calls = []

    def recorder(database_url, **record):
        calls.append((database_url, record))
        return len(calls)

    reports = [
        _failure_report("close_factor_failure", "0x1"),
        _failure_report("dust_leftover", "0x2"),
        _failure_report("low_profit", "0x3"),
        _failure_report("high_slippage_failure", "0x4"),
    ]

    result = serialize_liquidation_failure_samples(
        "postgresql://example",
        reports,
        recorder=recorder,
    )

    assert result["inserted_count"] == 4
    assert result["pending_labels"] == []
    assert [call[1]["failure_type"] for call in calls] == [
        "close_factor_failure",
        "dust_leftover",
        "low_profit",
        "high_slippage_failure",
    ]
    assert calls[0][0] == "postgresql://example"
    assert calls[0][1]["source"] == "liquidation_sample_library"
    assert calls[0][1]["collateral_asset"] == "0xcoll"
