from runtime import dex_opportunity_scanner as scanner_module


def test_worker_scan_interval_defaults_to_sixty_seconds(monkeypatch) -> None:
    monkeypatch.delenv("DEX_OPPORTUNITY_SCAN_INTERVAL_SECONDS", raising=False)

    scanner = scanner_module._build_scanner()
    try:
        assert scanner.interval_seconds == 60.0
    finally:
        scanner.stop()


def test_worker_scan_interval_does_not_read_ui_refresh_interval(monkeypatch) -> None:
    monkeypatch.setenv("DEX_OPPORTUNITY_SCAN_INTERVAL_SECONDS", "61")
    monkeypatch.setenv("DEX_QUOTES_UI_REFRESH_INTERVAL_SECONDS", "75")

    scanner = scanner_module._build_scanner()
    try:
        assert scanner.interval_seconds == 61.0
    finally:
        scanner.stop()
