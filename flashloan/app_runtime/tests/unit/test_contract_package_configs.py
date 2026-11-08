"""Regression tests for the shared DEX contract-package config schema."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
TOOL_PATH = ROOT / "scripts" / "tool" / "validate_contract_package_configs.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_contract_package_configs",
        TOOL_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_chain_and_contracts_share_common_package_schema() -> None:
    validator = _load_validator()
    for chain_id in validator.CONFIG_PAIRS:
        ok, errors = validator.validate_pair(chain_id)
        assert ok, errors


def test_chain_specific_package_values_and_official_v4() -> None:
    validator = _load_validator()
    expected_by_chain = {
        4663: {
            "chainId": 4663,
            "network": "robinhood",
            "officialV4": {
                "arb": "0x63743AB0f08a1ea8F7078893a88E943d6BEAbcFB",
                "composer": "0x1e1Fa70ca5af51d7F04716c7247b88c846703282",
            },
        },
        46630: {
            "chainId": 46630,
            "network": "robinhood-testnet",
            "officialV4": {
                "arb": "0x1A8Cc533EaBA93Ec7f68Ac6c428cDa948D7bB8DE",
                "composer": "0x3a9636AfD016C01d18e81AF8B4F52F7b01562956",
            },
        },
    }
    for chain_id, (chain_path, contracts_path) in validator.CONFIG_PAIRS.items():
        chain = json.loads(chain_path.read_text(encoding="utf-8-sig"))
        contracts = json.loads(contracts_path.read_text(encoding="utf-8-sig"))
        expected = expected_by_chain[chain_id]
        assert chain["chainId"] == expected["chainId"]
        assert contracts["chainId"] == expected["chainId"]
        assert chain["network"] == expected["network"]
        assert contracts["network"] == expected["network"]
        assert chain["officialV4"] == expected["officialV4"]
        assert contracts["officialV4"] == expected["officialV4"]
