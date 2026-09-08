from __future__ import annotations

import pytest

def test_liquidation_contracts_bot_dir_uses_chain_scoped_contract_root(monkeypatch, tmp_path):
    from web import control_panel_liquidation_execute as execute

    fake_module_path = tmp_path / "flashloan" / "app_runtime" / "web" / "control_panel_liquidation_execute.py"
    contract_dir = tmp_path / "contract" / "avalanche" / "BOT"
    contract_dir.mkdir(parents=True)
    monkeypatch.setattr(execute, "__file__", str(fake_module_path))

    assert execute.liquidation_contracts_bot_dir() == contract_dir


def test_liquidation_contracts_bot_dir_keeps_env_override(monkeypatch, tmp_path):
    from web import control_panel_liquidation_execute as execute

    configured_dir = tmp_path / "contract" / "avalanche" / "BOT"
    configured_dir.mkdir(parents=True)
    monkeypatch.setenv("LIQUIDATION_CONTRACTS_BOT_DIR", str(configured_dir))

    assert execute.liquidation_contracts_bot_dir() == configured_dir


def test_contract_project_resolver_maps_chain_and_module(tmp_path):
    from core.contract_project import resolve_contract_project

    assert resolve_contract_project(43114, "BOT", root=tmp_path) == tmp_path / "contract" / "avalanche" / "BOT"
    assert resolve_contract_project(43113, "DEX", root=tmp_path) == tmp_path / "contract" / "avalanche-testnet" / "DEX"
    assert resolve_contract_project(4663, "DEX", root=tmp_path) == tmp_path / "contract" / "robinhood" / "DEX"
    assert resolve_contract_project(46630, "DEX", root=tmp_path) == tmp_path / "contract" / "robinhood-testnet" / "DEX"


def test_contract_project_resolver_rejects_legacy_or_unconfigured_modules(tmp_path):
    from core.contract_project import resolve_contract_project, resolve_contract_project_from_env

    with pytest.raises(ValueError):
        resolve_contract_project(46630, "BOT", root=tmp_path)
    with pytest.raises(ValueError):
        resolve_contract_project(99999, "DEX", root=tmp_path)

    legacy = tmp_path / "contract" / "contracts-dex"
    legacy.mkdir(parents=True)
    import os

    previous = os.environ.get("TEST_CONTRACT_PROJECT")
    os.environ["TEST_CONTRACT_PROJECT"] = str(legacy)
    try:
        with pytest.raises(ValueError):
            resolve_contract_project_from_env(43114, "DEX", env_name="TEST_CONTRACT_PROJECT", root=tmp_path)
    finally:
        if previous is None:
            os.environ.pop("TEST_CONTRACT_PROJECT", None)
        else:
            os.environ["TEST_CONTRACT_PROJECT"] = previous
