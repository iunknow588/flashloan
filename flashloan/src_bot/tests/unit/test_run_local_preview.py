import run_local_preview as preview


def test_open_preview_url_clears_browser_env_and_uses_webbrowser(monkeypatch):
    monkeypatch.setenv("BROWSER", "broken-browser")
    calls = []

    monkeypatch.setattr(
        preview.webbrowser,
        "open",
        lambda url, new=0, autoraise=True: calls.append((url, new, autoraise)) or True,
    )

    assert preview._open_preview_url("http://127.0.0.1:5055/") is True
    assert "BROWSER" not in preview.os.environ
    assert calls == [("http://127.0.0.1:5055/", 2, True)]


def test_launch_browser_when_ready_respects_disable_flag(monkeypatch):
    monkeypatch.setenv("FLASHLOAN_DISABLE_BROWSER_AUTO_OPEN", "1")
    launched = []

    monkeypatch.setattr(preview, "_wait_for_port", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not wait")))
    monkeypatch.setattr(preview, "_open_preview_url", lambda url: launched.append(url) or True)

    preview._launch_browser_when_ready("127.0.0.1", 5055)

    assert launched == []


def test_launch_browser_when_ready_opens_preview_url(monkeypatch):
    monkeypatch.delenv("FLASHLOAN_DISABLE_BROWSER_AUTO_OPEN", raising=False)
    launched = []

    monkeypatch.setattr(preview, "_wait_for_port", lambda *args, **kwargs: True)
    monkeypatch.setattr(preview, "_open_preview_url", lambda url: launched.append(url) or True)

    preview._launch_browser_when_ready("127.0.0.1", 5055)

    assert launched == ["http://127.0.0.1:5055/"]
