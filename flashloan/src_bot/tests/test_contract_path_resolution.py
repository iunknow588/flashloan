from __future__ import annotations

def test_liquidation_contracts_bot_dir_uses_contract_root(monkeypatch, tmp_path):
    from web import control_panel_liquidation_execute as execute

    fake_module_path = tmp_path / "flashloan" / "src_bot" / "web" / "control_panel_liquidation_execute.py"
    contract_dir = tmp_path / "contract" / "contracts-bot"
    contract_dir.mkdir(parents=True)
    monkeypatch.setattr(execute, "__file__", str(fake_module_path))

    assert execute.liquidation_contracts_bot_dir() == contract_dir


def test_liquidation_contracts_bot_dir_keeps_env_override(monkeypatch, tmp_path):
    from web import control_panel_liquidation_execute as execute

    configured_dir = tmp_path / "custom-contracts-bot"
    monkeypatch.setenv("LIQUIDATION_CONTRACTS_BOT_DIR", str(configured_dir))

    assert execute.liquidation_contracts_bot_dir() == configured_dir
