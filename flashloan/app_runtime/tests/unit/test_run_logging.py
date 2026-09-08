import run
from core.sensitive_data import _is_sensitive_environment_name


def test_redact_sensitive_text_masks_credentials_and_private_key_shapes():
    private_key = "0x" + ("1" * 64)
    message = (
        "DATABASE_URL=postgresql://bot:demo-password@db.example/app?token=demo-token "
        f"private_key={private_key}"
    )

    redacted = run.redact_sensitive_text(message)

    assert "demo-password" not in redacted
    assert "demo-token" not in redacted
    assert private_key not in redacted
    assert "postgresql://[REDACTED]@db.example/app" in redacted


def test_main_redacts_sensitive_startup_error(monkeypatch, capsys):
    monkeypatch.setattr(
        run,
        "require_dependencies",
        lambda: (_ for _ in ()).throw(RuntimeError("connect postgresql://bot:demo-password@db.example/app failed")),
    )

    assert run.main() == 1

    captured = capsys.readouterr()
    assert "demo-password" not in captured.err
    assert "postgresql://[REDACTED]@db.example/app" in captured.err


def test_public_token_configuration_is_not_treated_as_a_secret_environment_value():
    assert _is_sensitive_environment_name("TRIANGULAR_TOKEN_X") is False
    assert _is_sensitive_environment_name("DEX_TARGET_STABLE_TOKENS") is False
    assert _is_sensitive_environment_name("ACCESS_TOKEN") is True
    assert _is_sensitive_environment_name("LIQUIDATION_EXECUTION_PRIVATE_KEY") is True
