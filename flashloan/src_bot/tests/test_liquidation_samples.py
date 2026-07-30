from pathlib import Path

from execution.liquidation_samples import (
    build_liquidation_sample_manifest,
    classify_liquidation_sample_label,
    write_liquidation_sample_library,
)


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
