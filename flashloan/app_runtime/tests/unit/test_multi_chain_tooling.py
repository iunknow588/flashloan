from __future__ import annotations

import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in (current, *current.parents):
        if (parent / "scripts" / "tool" / "chain_config.py").is_file():
            return parent
    raise AssertionError("unable to locate repo root")


ROOT = _repo_root()
TOOL_DIR = ROOT / "scripts" / "tool"
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

from chain_config import chain_config, known_chain_ids, set_chain_env, supported_chain_configs  # noqa: E402


def test_mainnet_chain_config_resolves_project_and_rpc():
    cfg = chain_config(4663)

    assert cfg["chain_id"] == 4663
    assert cfg["chain_key"] == "chain:4663"
    assert cfg["network"] == "robinhood"
    assert cfg["hardhat_network"] == "robinhood"
    assert cfg["contract_project"].is_dir()
    assert cfg["chain_json"].is_file()
    assert cfg["contracts_json"].is_file()
    assert cfg["rpc_url"].startswith("https://")


def test_testnet_chain_config_resolves_manifest_and_network():
    cfg = chain_config(46630)

    assert cfg["chain_id"] == 46630
    assert cfg["chain_key"] == "chain:46630"
    assert cfg["network"] == "robinhood-testnet"
    assert cfg["hardhat_network"] == "robinhoodTestnet"
    assert cfg["manifest_path"].is_file()
    assert cfg["contract_project"].is_dir()
    assert cfg["contracts_json"].is_file()


def test_set_chain_env_injects_chain_context():
    env = set_chain_env(46630, rpc_url_override="https://example.invalid")

    assert env["FLASHLOAN_CHAIN_ID"] == "46630"
    assert env["FLASHLOAN_CHAIN_KEY"] == "chain:46630"
    assert env["HARDHAT_NETWORK"] == "robinhoodTestnet"
    assert env["FLASHLOAN_RPC_URL"] == "https://example.invalid"
    assert env["ROBINHOOD_TESTNET_RPC_URL"] == "https://example.invalid"


def test_set_chain_env_does_not_mutate_current_process():
    before = os.environ.get("FLASHLOAN_CHAIN_ID")
    env = set_chain_env(4663, rpc_url_override="https://example.invalid")

    assert env["FLASHLOAN_CHAIN_ID"] == "4663"
    assert os.environ.get("FLASHLOAN_CHAIN_ID") == before


def test_supported_chains_include_local_chain_and_use_testnet_project():
    configs = supported_chain_configs()

    assert 31337 in configs
    local_cfg = configs[31337]
    assert local_cfg["hardhat_network"] == "localhost"
    assert local_cfg["manifest_path"].is_file()
    assert local_cfg["contract_project"].is_dir()


def test_supported_chains_are_registry_driven():
    configs = supported_chain_configs()
    registry_ids = set(known_chain_ids())

    assert set(configs) == registry_ids
    assert {1, 56, 137, 4663, 46630, 43113, 43114, 31337} <= registry_ids


def test_manual_menu_belongs_to_repo_root_and_accepts_chain_flag():
    source = (ROOT / "test-token_create" / "scripts" / "tool" / "manual_official_v4_menu.py").read_text(
        encoding="utf-8"
    )

    assert "parents[3]" in source
    assert '"--chain"' in source
    assert "chain_config" in source
    assert "FLASHLOAN_MANIFEST_PATH" in source


def test_multi_chain_cli_exposes_direct_actions():
    source = (TOOL_DIR / "multi_chain_cli.py").read_text(encoding="utf-8")

    assert '"testnet-menu"' in source
    assert '"mainnet-preflight"' in source
    assert '"mainnet-bridge-pairs"' in source
    assert '"mainnet-refresh-v4-index"' in source
    assert '"local-menu"' in source
    assert '"chain-info"' in source
    assert 'str(ROOT / "test-token_create"' in source
    assert 'testnet-menu requires chain 46630' in source
    assert 'local-menu requires chain 31337' in source
    assert "run_mainnet_readiness" in source
    assert "3.1 主网预检（只读 / 不广播）" in source
    assert "enter 6.x to run a testnet V4 secondary operation directly" in source
    assert "enter 7.x to run a local V4 secondary operation directly" in source


def test_mainnet_v4_pool_index_tool_defaults_to_observation_scope():
    tool = ROOT / "flashloan" / "src" / "dex" / "tools" / "build_robinhood_mainnet_v4_pool_index.py"
    source = tool.read_text(encoding="utf-8")

    assert tool.is_file()
    assert "--scope" in source
    assert 'choices=["observation", "full"]' in source
    assert 'default="observation"' in source
    assert "robinhood.v4_pool_index" in source
    assert "--no-write" in source


def test_periodic_composer_uses_shared_chain_config():
    source = (ROOT / "test-token_create" / "scripts" / "tool" / "periodic_official_v4_composer.py").read_text(
        encoding="utf-8"
    )

    assert "parents[3]" in source
    assert "from chain_config import chain_config, set_chain_env" in source
    assert '"--chain"' in source
    assert "cfg[\"hardhat_network\"]" in source
    assert "chain_config(" in source


def test_test_token_create_root_has_main_entry():
    main_entry = ROOT / "test-token_create" / "main.py"

    assert main_entry.is_file()
    source = main_entry.read_text(encoding="utf-8")
    assert "parents[1]" in source
    assert "from multi_chain_cli import main as cli_main" in source
