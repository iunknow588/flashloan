"""Error disposition matrix tests for the automatic runtime policy."""

from __future__ import annotations

from core.error_registry import (
    DISPOSITIONS,
    ERROR_REGISTRY,
    LEGACY_ERROR_KEYS,
    resolve_disposition,
)
from dex.runtime.error_disposition import resolve_broadcast_error


def _definition_names() -> list[str]:
    return ["BOT.EXECUTION.SUBMISSION_REVERTED", "BOT.INTERNAL", "DEX.INTERNAL"]


def test_every_registered_error_has_known_disposition():
    for definition in ERROR_REGISTRY.values():
        assert definition.disposition in DISPOSITIONS
        assert definition.action


def test_every_legacy_alias_resolves_to_known_disposition():
    for alias in LEGACY_ERROR_KEYS:
        assert resolve_disposition(alias) in DISPOSITIONS


def test_unknown_errors_fail_closed_to_design_blocked():
    for value in (None, "", "made_up", "unknown"):
        assert resolve_disposition(value) == "DESIGN_BLOCKED"


def test_no_runtime_path_wait_for_manual():
    for name in ("profit_too_low", "slippage_exceeded", "nonce_too_low", "unknown"):
        assert "manual" not in resolve_disposition(name).lower()


def test_common_broadcast_errors_have_expected_disposition():
    expectations = {
        "slippage_exceeded": "REQUOTE_AND_RETRY",
        "already known": "VERIFY_CHAIN_STATE",
        "nonce too low": "VERIFY_CHAIN_STATE",
        "replacement transaction underpriced": "VERIFY_CHAIN_STATE",
        "execution reverted": "DESIGN_BLOCKED",
        "request timed out": "VERIFY_CHAIN_STATE",
    }
    for raw, expected in expectations.items():
        info = resolve_broadcast_error(raw)
        assert info["disposition"] == expected
        assert info["errorKey"]
        assert info["errorCode"]


def test_unknown_broadcast_error_defaults_design_blocked():
    info = resolve_broadcast_error("some completely unknown failure")
    assert info["disposition"] == "DESIGN_BLOCKED"
    assert info["confidence"] == "low"


def test_same_raw_resubmit_defaults_disabled_and_can_be_enabled(monkeypatch):
    from dex.runtime.error_disposition import resubmit_same_raw_enabled

    monkeypatch.delenv("DEX_RESUBMIT_SAME_RAW_ENABLED", raising=False)
    assert resubmit_same_raw_enabled() is False
    monkeypatch.setenv("DEX_RESUBMIT_SAME_RAW_ENABLED", "1")
    assert resubmit_same_raw_enabled() is True


def test_definition_names_sanity():
    assert len(_definition_names()) == 3
