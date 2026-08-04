from tools.check_manual_prereqs import check_item, summarize_checks


def test_summarize_checks_separates_required_and_manual_items():
    summary = summarize_checks(
        [
            check_item("required ok", True),
            check_item("required missing", False),
            check_item("nonblocking missing", False, blocking=False),
            check_item("manual pending", False, manual=True),
        ]
    )

    assert summary["required_count"] == 2
    assert summary["missing_required_count"] == 1
    assert summary["nonblocking_count"] == 1
    assert summary["missing_nonblocking_count"] == 1
    assert summary["manual_count"] == 1
    assert summary["pending_manual_count"] == 1
    assert summary["ready_for_static_simulation"] is False
