from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from web3 import Web3


AVALANCHE_C_CHAIN_ID = 43114


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
    raw = _env(name) or default
    try:
        value = float(raw)
    except ValueError:
        return ConfigCheck(name, False, "error", f"{name} must be numeric")
    if minimum is not None and value < minimum:
        return ConfigCheck(name, False, "error", f"{name} must be >= {minimum:g}")
    return ConfigCheck(name, True, "info", f"{name} is {raw}")


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
    execution_enabled = _env("LIQUIDATION_EXECUTION_ENABLED").lower() in {"1", "true", "yes", "on"}
    checks = [
        ConfigCheck(
            "DATABASE_URL",
            bool(_env("DATABASE_URL")),
            "error" if execution_enabled else "warning",
            "DATABASE_URL is configured" if _env("DATABASE_URL") else "DATABASE_URL is missing",
        ),
        _address_check("AAVE_POOL_ADDRESS"),
        _address_check("AAVE_PROTOCOL_DATA_PROVIDER_ADDRESS", required_for_execution=False),
        _address_check("AAVE_LIQUIDATION_DATA_PROVIDER_ADDRESS", required_for_execution=False),
        _address_check("DEX_ROUTER_ADDRESS", required_for_execution=False),
        _address_check("LIQUIDATION_EXECUTOR_ADDRESS"),
        _address_check("LIQUIDATION_EXECUTOR_OWNER_ADDRESS"),
        _numeric_check("LIQUIDATION_SWAP_SLIPPAGE_BPS", "50", minimum=0),
        _numeric_check("LIQUIDATION_MIN_PROFIT_BASE", "0", minimum=0),
        _numeric_check("LIQUIDATION_MAX_DEBT_TO_COVER", "0", minimum=0),
        _numeric_check("LIQUIDATION_MAX_GAS_COST_USD", "0", minimum=0),
        _numeric_check("LIQUIDATION_MEV_BUFFER_USD", "0", minimum=0),
        _numeric_check("LIQUIDATION_RETRY_BUFFER_USD", "0", minimum=0),
        _numeric_check("LIQUIDATION_MIN_OPERATOR_NET_PROFIT_USD", "0", minimum=0),
        _private_key_owner_check(),
    ]

    expected_chain_id = int(_env("CHAIN_ID") or AVALANCHE_C_CHAIN_ID)
    actual_chain_id = chain_id if chain_id is not None else expected_chain_id
    chain_ok = int(actual_chain_id) == expected_chain_id == AVALANCHE_C_CHAIN_ID
    checks.append(
        ConfigCheck(
            "CHAIN_ID",
            chain_ok,
            "error",
            f"chain id is {actual_chain_id}, expected {AVALANCHE_C_CHAIN_ID}",
        )
    )

    errors = [check.message for check in checks if not check.ok and check.severity == "error"]
    warnings = [check.message for check in checks if not check.ok and check.severity == "warning"]
    execution_blocked = execution_enabled and bool(errors)
    return {
        "valid": not errors,
        "execution_enabled": execution_enabled,
        "execution_blocked": execution_blocked,
        "errors": errors,
        "warnings": warnings,
        "checks": [check.as_dict() for check in checks],
        "expected_chain_id": AVALANCHE_C_CHAIN_ID,
        "chain_id": actual_chain_id,
    }
