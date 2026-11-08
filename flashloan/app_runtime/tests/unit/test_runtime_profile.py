from __future__ import annotations

from app_runtime import run as run_module


def test_runtime_profile_defaults_to_v4_dex(monkeypatch) -> None:
    monkeypatch.delenv("FLASHLOAN_RUNTIME_PROFILE", raising=False)

    assert run_module.runtime_profile() == run_module.RUNTIME_PROFILE_V4_DEX
    assert run_module.dex_only_runtime_enabled() is True


def test_runtime_profile_unsupported_value_falls_back_safely(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("FLASHLOAN_RUNTIME_PROFILE", "unexpected")

    assert run_module.runtime_profile() == run_module.RUNTIME_PROFILE_V4_DEX
    assert "falling back to v4_dex" in capsys.readouterr().err


def test_v4_dex_profile_overrides_ambient_non_dex_switches(monkeypatch) -> None:
    monkeypatch.setenv("FLASHLOAN_RUNTIME_PROFILE", run_module.RUNTIME_PROFILE_V4_DEX)
    monkeypatch.setenv("COW_REALTIME_QUOTE_ENABLED", "true")
    monkeypatch.setenv("COW_ORDER_SUBMISSION_ENABLED", "true")
    monkeypatch.setenv("LIQUIDATION_AUTO_EXECUTE", "true")
    monkeypatch.setenv("FLASHLOAN_BNB_SHARED_DATABASE", "1")
    monkeypatch.setattr(run_module, "load_env_files", lambda *args, **kwargs: None)

    with run_module._runtime_environment():
        assert run_module.os.getenv("COW_REALTIME_QUOTE_ENABLED") == "false"
        assert run_module.os.getenv("COW_ORDER_SUBMISSION_ENABLED") == "false"
        assert run_module.os.getenv("LIQUIDATION_AUTO_EXECUTE") == "false"
        assert run_module.os.getenv("FLASHLOAN_BNB_SHARED_DATABASE") == "0"


def test_full_profile_preserves_explicit_feature_switches(monkeypatch) -> None:
    monkeypatch.setenv("FLASHLOAN_RUNTIME_PROFILE", run_module.RUNTIME_PROFILE_FULL)
    monkeypatch.setenv("COW_REALTIME_QUOTE_ENABLED", "false")
    monkeypatch.setenv("COW_ORDER_SUBMISSION_ENABLED", "false")
    monkeypatch.setattr(run_module, "load_env_files", lambda *args, **kwargs: None)

    with run_module._runtime_environment():
        assert run_module.os.getenv("COW_REALTIME_QUOTE_ENABLED") == "false"
        assert run_module.os.getenv("COW_ORDER_SUBMISSION_ENABLED") == "false"
        assert run_module.dex_only_runtime_enabled() is False


def test_non_dex_runtime_background_is_skipped_for_v4_dex(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("FLASHLOAN_RUNTIME_PROFILE", run_module.RUNTIME_PROFILE_V4_DEX)
    calls: list[str] = []

    initialized = run_module.initialize_non_dex_runtime_background(
        lambda: calls.append("liquidation"),
        lambda: calls.append("cow"),
    )

    assert initialized is False
    assert calls == []
    assert "non-DEX background disabled" in capsys.readouterr().err


def test_non_dex_runtime_background_is_opt_in_for_full_profile(monkeypatch) -> None:
    monkeypatch.setenv("FLASHLOAN_RUNTIME_PROFILE", run_module.RUNTIME_PROFILE_FULL)
    calls: list[str] = []

    initialized = run_module.initialize_non_dex_runtime_background(
        lambda: calls.append("liquidation"),
        lambda: calls.append("cow"),
    )

    assert initialized is True
    assert calls == ["liquidation", "cow"]


def test_runtime_background_thread_name_is_scope_neutral() -> None:
    assert run_module.RUNTIME_BACKGROUND_THREAD_NAME == "runtime-background-init"
