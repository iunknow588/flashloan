import json
import sys
from pathlib import Path

from execution.liquidation_evidence_audit import audit_attempt_failure_sample_pair, audit_sample_manifest


def _attempt() -> dict:
    return {
        "id": 10,
        "account": "0x0000000000000000000000000000000000000001",
        "mode": "static_call",
        "state": "static_call_failed",
        "quote": {"quote_block": 12345},
        "preflight": {
            "execution_phase": "ready_to_submit",
            "block_number": 12345,
            "static_call_status": "error",
            "static_call_error": "AmountOutMin slippage exceeded",
        },
        "error": "AmountOutMin slippage exceeded",
    }


def _failure_sample() -> dict:
    return {
        "id": 20,
        "account": "0x0000000000000000000000000000000000000001",
        "block_number": 12345,
        "failure_type": "static_call_failed",
        "failure_reason": "AmountOutMin slippage exceeded",
        "payload": {
            "mode": "static_call",
            "state": "static_call_failed",
            "execution_phase": "ready_to_submit",
            "preflight": {
                "execution_phase": "ready_to_submit",
                "static_call_status": "error",
                "static_call_error": "AmountOutMin slippage exceeded",
            },
            "retryable": True,
        },
    }


def _manifest(status: str = "ready", replayable: bool = True) -> dict:
    samples = [
        {
            "label": label,
            "status": "ready",
            "replay": {"replayable": True, "missing_fields": []},
        }
        for label in ("healthy", "warning", "liquidatable")
    ]
    for label in ("close_factor_failure", "dust_leftover", "low_profit", "high_slippage_failure"):
        samples.append(
            {
                "label": label,
                "status": status,
                "replay": {
                    "replayable": replayable,
                    "missing_fields": [] if replayable else ["preflight_result"],
                },
            }
        )
    return {"schema_version": 2, "source_count": len(samples), "samples": samples}


def test_attempt_failure_pair_audit_accepts_matching_static_call_failure():
    report = audit_attempt_failure_sample_pair(_attempt(), _failure_sample())

    assert report["ok"] is True
    assert report["errors"] == []
    assert report["attempt"]["phase"] == "ready_to_submit"
    assert report["sample"]["retryable"] is True


def test_attempt_failure_pair_audit_detects_mismatched_account_and_phase():
    sample = _failure_sample()
    sample["account"] = "0x0000000000000000000000000000000000000002"
    sample["payload"]["execution_phase"] = "confirmed_failed"

    report = audit_attempt_failure_sample_pair(_attempt(), sample)

    assert report["ok"] is False
    assert "account_mismatch" in report["errors"]
    assert "execution_phase_mismatch" in report["errors"]


def test_attempt_failure_pair_audit_rejects_unredacted_sensitive_values():
    attempt = _attempt()
    attempt["error"] = "failed private_key=0x" + "a" * 64

    report = audit_attempt_failure_sample_pair(attempt, _failure_sample())

    assert report["ok"] is False
    assert "unredacted_sensitive_value" in report["errors"]


def test_sample_manifest_audit_requires_failure_replayability_by_default():
    report = audit_sample_manifest(_manifest(status="pending_real_sample", replayable=False))

    assert report["ok"] is False
    assert "failure_sample_not_ready:close_factor_failure" in report["errors"]
    assert "failure_sample_pending:low_profit" in report["warnings"]


def test_sample_manifest_audit_can_allow_pending_failure_samples():
    report = audit_sample_manifest(
        _manifest(status="pending_real_sample", replayable=False),
        require_failure_replayable=False,
    )

    assert report["ok"] is True
    assert "close_factor_failure" in report["pending_labels"]


def test_audit_liquidation_evidence_cli_writes_report(tmp_path: Path, monkeypatch, capsys):
    from tools import audit_liquidation_evidence as cli

    attempt_path = tmp_path / "attempt.json"
    sample_path = tmp_path / "sample.json"
    output_path = tmp_path / "report.json"
    attempt_path.write_text(json.dumps(_attempt()), encoding="utf-8")
    sample_path.write_text(json.dumps(_failure_sample()), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit_liquidation_evidence.py",
            "--attempt-json",
            str(attempt_path),
            "--failure-sample-json",
            str(sample_path),
            "--output",
            str(output_path),
        ],
    )

    assert cli.main() == 0
    output = json.loads(capsys.readouterr().out)
    saved = json.loads(output_path.read_text(encoding="utf-8"))

    assert output["ok"] is True
    assert saved["reports"]["attempt_failure_pair"]["sample"]["retryable"] is True
