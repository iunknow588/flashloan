from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from web3 import Web3

from core.market_config import DEFAULT_AVALANCHE_CHAIN_ID, liquidation_market_config

AVALANCHE_C_CHAIN_ID = DEFAULT_AVALANCHE_CHAIN_ID


@dataclass(frozen=True)
class ConfigCheck:
    name: str
    ok: bool
    severity: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "severity": self.severity,
            "message": self.message,
        }


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _is_address(value: str) -> bool:
    return bool(value) and Web3.is_address(value)


def _address_check(name: str, *, required_for_execution: bool = True) -> ConfigCheck:
    value = _env(name)
    if not value:
        severity = "error" if required_for_execution else "warning"
        return ConfigCheck(name, False, severity, f"{name} is missing")
    if not _is_address(value):
        return ConfigCheck(name, False, "error", f"{name} is not a valid address")
    return ConfigCheck(name, True, "info", f"{name} is configured")


def _numeric_check(name: str, default: str, *, minimum: float | None = None) -> ConfigCheck:
    value, error = parse_env_float(name, default, minimum=minimum)
    if error:
        return ConfigCheck(name, False, "error", error)
    return ConfigCheck(name, True, "info", f"{name} is {_env(name) or default}")


def parse_env_float(name: str, default: float | str, *, minimum: float | None = None) -> tuple[float, str | None]:
    raw = _env(name) or str(default)
    try:
        fallback = float(default)
    except (TypeError, ValueError):
        fallback = 0.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback, f"{name} must be numeric"
    if minimum is not None and value < minimum:
        return fallback, f"{name} must be >= {minimum:g}"
    return value, None


def parse_env_int(name: str, default: int | str, *, minimum: int | None = None) -> tuple[int, str | None]:
    raw = _env(name) or str(default)
    try:
        fallback = int(default)
    except (TypeError, ValueError):
        fallback = 0
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback, f"{name} must be an integer"
    if minimum is not None and value < minimum:
        return fallback, f"{name} must be >= {minimum:d}"
    return value, None


def _chain_id_check(chain_id: int | None, expected_chain_id: int) -> tuple[ConfigCheck, int]:
    configured_chain_id, expected_error = parse_env_int("CHAIN_ID", expected_chain_id)
    if _env("LIQUIDATION_CHAIN_ID"):
        configured_chain_id, expected_error = parse_env_int("LIQUIDATION_CHAIN_ID", expected_chain_id)
    actual_chain_id = chain_id if chain_id is not None else configured_chain_id
    if expected_error:
        return (
            ConfigCheck("CHAIN_ID", False, "error", expected_error),
            actual_chain_id,
        )
    chain_ok = int(actual_chain_id) == int(expected_chain_id) == int(configured_chain_id)
    return (
        ConfigCheck(
            "CHAIN_ID",
            chain_ok,
            "error",
            f"chain id is {actual_chain_id}, expected {expected_chain_id}",
        ),
        actual_chain_id,
    )


def _private_key_owner_check() -> ConfigCheck:
    owner = _env("LIQUIDATION_EXECUTOR_OWNER_ADDRESS")
    private_key = _env("LIQUIDATION_EXECUTION_PRIVATE_KEY") or _env("DEPLOYER_PRIVATE_KEY")
    if not owner or not private_key:
        return ConfigCheck(
            "LIQUIDATION_EXECUTION_PRIVATE_KEY",
            False,
            "warning",
            "execution owner or private key is missing",
        )
    if not _is_address(owner):
        return ConfigCheck(
            "LIQUIDATION_EXECUTOR_OWNER_ADDRESS",
            False,
            "error",
            "LIQUIDATION_EXECUTOR_OWNER_ADDRESS is not a valid address",
        )
    try:
        from eth_account import Account

        signer = Account.from_key(private_key).address
    except Exception:
        return ConfigCheck(
            "LIQUIDATION_EXECUTION_PRIVATE_KEY",
            False,
            "error",
            "execution private key cannot be parsed",
        )
    if Web3.to_checksum_address(signer).lower() != Web3.to_checksum_address(owner).lower():
        return ConfigCheck(
            "LIQUIDATION_EXECUTION_PRIVATE_KEY",
            False,
            "error",
            "execution private key does not match LIQUIDATION_EXECUTOR_OWNER_ADDRESS",
        )
    return ConfigCheck(
        "LIQUIDATION_EXECUTION_PRIVATE_KEY",
        True,
        "info",
        "execution private key matches owner address",
    )


def liquidation_config_health(chain_id: int | None = None) -> dict[str, Any]:
    market = liquidation_market_config()
    execution_enabled = _env_bool("LIQUIDATION_EXECUTION_ENABLED", True)
    auto_execute_requested = _env_bool("LIQUIDATION_AUTO_EXECUTE", True)
    manual_test_completed = _env_bool("LIQUIDATION_MANUAL_TEST_COMPLETED", True)
    checks = [
        ConfigCheck(
            "DATABASE_URL",
            bool(_env("DATABASE_URL")),
            "error" if execution_enabled else "warning",
            "DATABASE_URL is configured" if _env("DATABASE_URL") else "DATABASE_URL is missing",
        ),
        _address_check("AAVE_POOL_ADDRESS" if not _env("LIQUIDATION_POOL_ADDRESS") else "LIQUIDATION_POOL_ADDRESS"),
        _address_check(
            "AAVE_PROTOCOL_DATA_PROVIDER_ADDRESS"
            if not _env("LIQUIDATION_PROTOCOL_DATA_PROVIDER_ADDRESS")
            else "LIQUIDATION_PROTOCOL_DATA_PROVIDER_ADDRESS",
            required_for_execution=False,
        ),
        _address_check(
            "AAVE_LIQUIDATION_DATA_PROVIDER_ADDRESS"
            if not _env("LIQUIDATION_DATA_PROVIDER_ADDRESS")
            else "LIQUIDATION_DATA_PROVIDER_ADDRESS",
            required_for_execution=False,
        ),
        _address_check("DEX_ROUTER_ADDRESS" if not _env("LIQUIDATION_DEX_ROUTER_ADDRESS") else "LIQUIDATION_DEX_ROUTER_ADDRESS", required_for_execution=False),
        _address_check("LIQUIDATION_EXECUTOR_ADDRESS"),
        _address_check("LIQUIDATION_EXECUTOR_OWNER_ADDRESS"),
        _numeric_check("LIQUIDATION_SWAP_SLIPPAGE_BPS", "50", minimum=0),
        _numeric_check("LIQUIDATION_MIN_PROFIT_BASE", "0", minimum=0),
        _numeric_check("LIQUIDATION_MAX_DEBT_TO_COVER", "0", minimum=0),
        _numeric_check("LIQUIDATION_MAX_GAS_COST_USD", "0", minimum=0),
        _numeric_check("LIQUIDATION_MEV_BUFFER_USD", "0", minimum=0),
        _numeric_check("LIQUIDATION_RETRY_BUFFER_USD", "0", minimum=0),
        _numeric_check("LIQUIDATION_MIN_OPERATOR_NET_PROFIT_USD", "1.0", minimum=0),
        _private_key_owner_check(),
    ]
    if auto_execute_requested:
        checks.append(
            ConfigCheck(
                "LIQUIDATION_MANUAL_TEST_COMPLETED",
                manual_test_completed,
                "warning",
                "manual liquidation test is complete"
                if manual_test_completed
                else "LIQUIDATION_AUTO_EXECUTE requested but manual liquidation test is not complete",
            )
        )

    if not market.protocol_supported:
        checks.append(
            ConfigCheck(
                "LIQUIDATION_PROTOCOL",
                False,
                "error" if execution_enabled else "warning",
                f"{market.protocol} is not executable by the current protocol adapter",
            )
        )
    if not market.evm_compatible:
        checks.append(
            ConfigCheck(
                "LIQUIDATION_PROTOCOL_EXECUTION",
                False,
                "error" if execution_enabled else "warning",
                f"{market.protocol} is not EVM liquidation-call compatible",
            )
        )

    chain_check, actual_chain_id = _chain_id_check(chain_id, market.chain_id)
    checks.append(chain_check)

    errors = [check.message for check in checks if not check.ok and check.severity == "error"]
    warnings = [check.message for check in checks if not check.ok and check.severity == "warning"]
    execution_blocked = execution_enabled and bool(errors)
    return {
        "valid": not errors,
        "execution_enabled": execution_enabled,
        "auto_execute_requested": auto_execute_requested,
        "manual_test_completed": manual_test_completed,
        "execution_blocked": execution_blocked,
        "errors": errors,
        "warnings": warnings,
        "checks": [check.as_dict() for check in checks],
        "expected_chain_id": market.chain_id,
        "chain_id": actual_chain_id,
        "market": market.as_dict(),
    }
