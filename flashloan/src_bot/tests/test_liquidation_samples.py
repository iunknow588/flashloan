import json
import sys
from pathlib import Path

from execution.liquidation_samples import (
    build_liquidation_sample_manifest,
    build_liquidation_sample_record,
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
            "collateral_symbol": "WAVAX",
            "debt_asset": "0xdebt",
            "debt_symbol": "USDC",
            "amount_to_pass_to_liquidation_call": 500,
            "estimated_profit": {"net_profit_base": 10.0},
        },
        "context": {"block_number": 12345},
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
    assert manifest["samples"][2]["replay"]["replayable"] is False
    assert "block_number" in manifest["samples"][2]["required_fields"]


def test_sample_manifest_ready_failure_includes_replay_metadata():
    manifest = build_liquidation_sample_manifest(
        [
            _failure_report("high_slippage_failure", "0x4"),
        ]
    )
    sample = manifest["samples"][-1]

    assert sample["label"] == "high_slippage_failure"
    assert sample["status"] == "ready"
    assert sample["block_number"] == 12345
    assert sample["candidate_params"]["collateral_asset"] == "0xcoll"
    assert sample["preflight_result"]["static_call_status"] == "error"
    assert sample["revert_classification"]["category"] == "high_slippage"
    assert sample["replay"]["replayable"] is True
    assert sample["replay"]["missing_fields"] == []


def test_sample_record_includes_replayable_fields_without_payload_build(monkeypatch):
    from execution import liquidation_samples

    monkeypatch.setattr(
        liquidation_samples,
        "build_liquidation_execution_payload",
        lambda *args, **kwargs: {
            "preflight": {
                "static_call_status": "error",
                "static_call_error": "AmountOutMin slippage exceeded",
                "static_call_passed": False,
            }
        },
    )

    record = build_liquidation_sample_record(
        _failure_report("high_slippage_failure", "0x4"),
        label="high_slippage_failure",
        executor_address="0x0000000000000000000000000000000000000001",
        router_address="0x0000000000000000000000000000000000000002",
    )

    assert record["schema_version"] == 2
    assert record["block_number"] == 12345
    assert record["candidate_params"]["debt_asset"] == "0xdebt"
    assert record["preflight_result"]["static_call_passed"] is False
    assert record["revert_classification"]["category"] == "high_slippage"
    assert record["replay"]["replayable"] is True


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
    assert index["schema_version"] == 2
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


def test_liquidation_sample_payload_error_is_redacted(monkeypatch):
    from execution import liquidation_samples

    database_url = "postgresql://user:secret-pass@example.com:5432/db?token=abc123"
    private_key = "0x" + "b" * 64
    monkeypatch.setenv("DATABASE_URL", database_url)

    def fail_payload(*args, **kwargs):
        raise RuntimeError(f"payload failed: {database_url} private_key={private_key}")

    monkeypatch.setattr(liquidation_samples, "build_liquidation_execution_payload", fail_payload)

    record = build_liquidation_sample_record(
        _failure_report("low_profit", "0x3"),
        label="low_profit",
        executor_address="0x0000000000000000000000000000000000000001",
        router_address="0x0000000000000000000000000000000000000002",
    )

    assert database_url not in record["payload_error"]
    assert private_key not in record["payload_error"]
    assert "secret-pass" not in record["payload_error"]
    assert "abc123" not in record["payload_error"]
    assert "[REDACTED]" in record["payload_error"]


def test_export_liquidation_samples_prints_replay_summary(monkeypatch, tmp_path: Path, capsys):
    from tools import export_liquidation_samples as exporter

    monkeypatch.setenv("DATABASE_URL", "postgresql://example")
    monkeypatch.setattr(exporter, "ensure_database_schema", lambda database_url: None)
    monkeypatch.setattr(
        exporter,
        "load_latest_liquidation_account_reports",
        lambda database_url, limit=500: [
            {
                "account": "0x4",
                "source": "test",
                "summary": {"status": "liquidatable", "health_factor": 0.98},
                "report": _failure_report("high_slippage_failure", "0x4"),
            }
        ],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "export_liquidation_samples.py",
            "--output",
            str(tmp_path / "samples"),
        ],
    )

    assert exporter.main() == 0
    output = json.loads(capsys.readouterr().out)

    assert output["schema_version"] == 2
    assert output["ready_labels"] == ["high_slippage_failure"]
    assert output["replayable_labels"] == ["high_slippage_failure"]
    assert "healthy" in output["pending_labels"]
    assert "healthy" in output["missing_replay_fields"]
