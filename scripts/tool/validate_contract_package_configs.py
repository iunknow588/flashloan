"""Validate that Robinhood DEX contract package configs share one schema.

The two Hardhat project packages are different deployments of the same
contract-package template. Chain-specific values are allowed, but the stable
keys used by scripts/tests must exist in both ``chain.json`` and
``contracts.json`` so callers no longer branch on missing fields.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]

CONFIG_PAIRS: dict[int, tuple[Path, Path]] = {
    4663: (
        ROOT / "contract" / "robinhood" / "DEX" / "config" / "chain.json",
        ROOT / "contract" / "robinhood" / "DEX" / "config" / "contracts.json",
    ),
    46630: (
        ROOT / "contract" / "robinhood-testnet" / "DEX" / "config" / "chain.json",
        ROOT / "contract" / "robinhood-testnet" / "DEX" / "config" / "contracts.json",
    ),
}

CHAIN_COMMON_KEYS = [
    "schemaVersion",
    "chainId",
    "network",
    "chainKey",
    "displayName",
    "nativeSymbol",
    "module",
    "projectRoot",
    "sourceContract",
    "executorContract",
    "sourceChainConfig",
    "explorerUrl",
    "marketId",
    "feature",
    "mode",
    "broadcastEnabled",
    "readyForPausedDeploy",
    "defaultDexChainSelector",
    "routePreflightStatus",
    "deploymentGate",
    "deploymentStatus",
    "deploymentAddress",
    "lastPreflightEvidence",
    "lastReadinessEvidence",
    "requiredAdapters",
    "optionalAdapters",
    "defaultFlashSourceKind",
    "defaultFlashSource",
    "defaultPermit2",
    "defaultV4PoolManager",
    "defaultV4Quoter",
    "defaultV4UniversalRouter",
    "defaultPositionManager",
    "defaultWrappedNative",
    "defaultMulticall3",
    "v3SwapRouter",
    "flashSource",
    "officialV4",
]

CONTRACTS_COMMON_KEYS = [
    "schemaVersion",
    "chainId",
    "network",
    "chainKey",
    "marketId",
    "module",
    "projectRoot",
    "sourceContract",
    "executorContract",
    "sourceChainConfig",
    "mode",
    "broadcastEnabled",
    "readyForPausedDeploy",
    "routePreflightStatus",
    "deploymentGate",
    "deploymentStatus",
    "deploymentAddress",
    "deploymentFile",
    "lastPreflightEvidence",
    "lastReadinessEvidence",
    "requiredAdapters",
    "optionalAdapters",
    "defaultFlashSourceKind",
    "defaultFlashSource",
    "defaultPermit2",
    "defaultV4PoolManager",
    "defaultV4Quoter",
    "defaultV4UniversalRouter",
    "defaultPositionManager",
    "defaultWrappedNative",
    "defaultMulticall3",
    "v3SwapRouter",
    "v3Router",
    "flashSource",
    "officialV4",
    "wrappedNative",
    "multicall3",
    "permit2",
    "v4PoolManager",
    "v4Quoter",
    "v4UniversalRouter",
]

ADDRESS_KEYS = [
    "defaultFlashSource",
    "defaultPermit2",
    "defaultV4PoolManager",
    "defaultV4Quoter",
    "defaultV4UniversalRouter",
    "defaultPositionManager",
    "defaultWrappedNative",
    "defaultMulticall3",
    "v3SwapRouter",
    "flashSource",
]

EXPECTED_BY_CHAIN = {
    4663: {
        "chainId": 4663,
        "network": "robinhood",
        "chainKey": "chain:4663",
        "marketId": "robinhood-dex-shadow",
    },
    46630: {
        "chainId": 46630,
        "network": "robinhood-testnet",
        "chainKey": "chain:46630",
        "marketId": "robinhood-testnet-dex-paused",
    },
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _missing(keys: list[str], payload: dict[str, Any]) -> list[str]:
    return [key for key in keys if key not in payload]


def validate_pair(chain_id: int) -> tuple[bool, list[str]]:
    chain_path, contracts_path = CONFIG_PAIRS[chain_id]
    errors: list[str] = []
    try:
        chain = _read_json(chain_path)
    except Exception as exc:
        return False, [f"{chain_path}: invalid JSON: {exc}"]
    try:
        contracts = _read_json(contracts_path)
    except Exception as exc:
        return False, [f"{contracts_path}: invalid JSON: {exc}"]

    for key in _missing(CHAIN_COMMON_KEYS, chain):
        errors.append(f"{chain_path}: missing chain key {key}")
    for key in _missing(CONTRACTS_COMMON_KEYS, contracts):
        errors.append(f"{contracts_path}: missing contracts key {key}")

    expected = EXPECTED_BY_CHAIN[chain_id]
    for key, value in expected.items():
        if chain.get(key) != value:
            errors.append(
                f"{chain_path}: expected {key}={value!r}, got {chain.get(key)!r}"
            )
        if contracts.get(key) != value:
            errors.append(
                f"{contracts_path}: expected {key}={value!r}, got {contracts.get(key)!r}"
            )

    for payload, name in ((chain, "chain"), (contracts, "contracts")):
        official = payload.get("officialV4")
        if not isinstance(official, dict):
            errors.append(f"{name} officialV4 must be an object")
            continue
        for nested_key in ("arb", "composer"):
            value = str((official.get(nested_key) or "").strip())
            if not value:
                errors.append(f"{name} officialV4.{nested_key} must be non-empty")
            elif not value.startswith("0x"):
                errors.append(f"{name} officialV4.{nested_key} must be an address")

    for key in ADDRESS_KEYS:
        if key not in chain:
            continue
        value = str(chain.get(key) or "").strip()
        if value and not value.startswith("0x"):
            errors.append(f"{chain_path}: {key} must be an address")

    return not errors, errors


def main() -> int:
    all_errors: list[str] = []
    for chain_id in CONFIG_PAIRS:
        ok, errors = validate_pair(chain_id)
        if not ok:
            all_errors.extend(errors)
    if all_errors:
        print("\n".join(all_errors))
        return 1
    print("OK: Robinhood DEX contract package configs share the common schema")
    return 0


if __name__ == "__main__":
    sys.exit(main())
