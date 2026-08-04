from datetime import datetime, timedelta, timezone

from web.liquidation_discovery_service import build_discovery_window_result


def test_discovery_window_result_marks_historical_backfill_complete():
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    scan_start_at = now - timedelta(days=10)
    scan_end_at = now - timedelta(days=20)

    result = build_discovery_window_result(
        force_full=True,
        scan_start_at=scan_start_at,
        scan_end_at=scan_end_at,
        interval_seconds=3600,
        registry={"discovery_cursor": "0x123"},
        mode="full",
        from_block=1,
        to_block=2,
        lookback_blocks=100,
    )

    assert result["skipped"] is True
    assert result["reason"] == "historical backfill complete"
    assert result["mode"] == "full"


def test_discovery_window_result_marks_interval_not_reached():
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    scan_start_at = now - timedelta(minutes=30)
    scan_end_at = now

    result = build_discovery_window_result(
        force_full=False,
        scan_start_at=scan_start_at,
        scan_end_at=scan_end_at,
        interval_seconds=3600,
        registry={"discovery_cursor": "0x456"},
        mode="incremental",
        from_block=1,
        to_block=2,
        lookback_blocks=100,
    )

    assert result["skipped"] is True
    assert result["reason"] == "discovery interval not reached"
    assert result["mode"] == "incremental"
