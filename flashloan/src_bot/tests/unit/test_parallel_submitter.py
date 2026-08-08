from execution.parallel_submitter import SubmissionAttempt, run_parallel_submissions


def test_run_parallel_submissions_returns_first_success_with_attempt_summary():
    calls = []

    def fail():
        calls.append("fail")
        raise RuntimeError("boom")

    def success():
        calls.append("success")
        return {"tx_hash": "0xabc", "sender": "0x1"}

    result = run_parallel_submissions(
        [
            SubmissionAttempt("wallet1", fail),
            SubmissionAttempt("wallet2", success),
        ],
        max_workers=2,
    )

    assert result["tx_hash"] == "0xabc"
    assert result["parallel_submission"]["enabled"] is True
    assert result["parallel_submission"]["success_count"] == 1
    assert result["parallel_submission"]["failure_count"] == 1
    assert {item["name"] for item in result["parallel_submission"]["attempts"]} == {"wallet1", "wallet2"}
    assert "success" in calls


def test_run_parallel_submissions_raises_when_all_fail():
    def fail_one():
        raise RuntimeError("one")

    def fail_two():
        raise RuntimeError("two")

    try:
        run_parallel_submissions(
            [
                SubmissionAttempt("wallet1", fail_one),
                SubmissionAttempt("wallet2", fail_two),
            ],
            max_workers=2,
        )
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "all parallel submissions failed" in str(exc)


def test_run_parallel_submissions_redacts_attempt_errors(monkeypatch):
    database_url = "postgresql://user:secret-pass@example.com:5432/db?token=abc123"
    private_key = "0x" + "2" * 64
    monkeypatch.setenv("DATABASE_URL", database_url)

    def fail():
        raise RuntimeError(f"submit failed: {database_url} private_key={private_key}")

    try:
        run_parallel_submissions([SubmissionAttempt("wallet1", fail)], max_workers=1)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        message = str(exc)
        assert database_url not in message
        assert private_key not in message
        assert "secret-pass" not in message
        assert "abc123" not in message
        assert "[REDACTED]" in message
