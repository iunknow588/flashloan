import os

from core.env_loader import (
    load_chain_env_files,
    load_env_files,
)


def test_load_env_files_prefers_process_environment(monkeypatch, tmp_path):
    child = tmp_path / "app_runtime"
    child.mkdir()
    (tmp_path / ".env").write_text("SETTING=parent\n", encoding="utf-8")
    (child / ".env").write_text("SETTING=local\n", encoding="utf-8")
    monkeypatch.setenv("SETTING", "process")

    loaded = load_env_files(child / "run.py")

    assert loaded == [child / ".env", tmp_path / ".env"]
    assert os.environ["SETTING"] == "process"


def test_load_env_files_prefers_nearest_env_file_when_process_value_is_missing(monkeypatch, tmp_path):
    child = tmp_path / "app_runtime"
    child.mkdir()
    (tmp_path / ".env").write_text("SETTING=parent\n", encoding="utf-8")
    (child / ".env").write_text("SETTING=local\n", encoding="utf-8")
    monkeypatch.delenv("SETTING", raising=False)

    load_env_files(child / "run.py")

    assert os.environ["SETTING"] == "local"


def test_load_env_files_applies_non_sensitive_test_env_overrides(monkeypatch, tmp_path):
    child = tmp_path / "app_runtime"
    child.mkdir()
    (child / ".env").write_text(
        "TRIANGULAR_ROUTE_CONTROLLER_ADDRESS=0x1111111111111111111111111111111111111111\n"
        "DEPLOYER_PRIVATE_KEY=from-env\n",
        encoding="utf-8",
    )
    (child / ".env.test").write_text(
        "TRIANGULAR_ROUTE_CONTROLLER_ADDRESS=0x2222222222222222222222222222222222222222\n"
        "DEPLOYER_PRIVATE_KEY=from-test\n"
        "EMPTY_TEST_VALUE=\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TRIANGULAR_ROUTE_CONTROLLER_ADDRESS", raising=False)
    monkeypatch.delenv("DEPLOYER_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("EMPTY_TEST_VALUE", raising=False)

    loaded = load_env_files(child / "run.py")

    assert loaded == [child / ".env", child / ".env.test"]
    assert os.environ["TRIANGULAR_ROUTE_CONTROLLER_ADDRESS"] == "0x2222222222222222222222222222222222222222"
    assert os.environ["DEPLOYER_PRIVATE_KEY"] == "from-env"
    assert "EMPTY_TEST_VALUE" not in os.environ


def test_load_env_files_loads_service_url_from_env_file(monkeypatch, tmp_path):
    child = tmp_path / "app_runtime"
    child.mkdir()
    (child / ".env").write_text(
        "SERVICE_URL=https://remote.example/db\nSETTING=local\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("SERVICE_URL", raising=False)
    monkeypatch.delenv("SETTING", raising=False)

    load_env_files(child / "run.py")

    assert os.environ["SERVICE_URL"] == "https://remote.example/db"
    assert os.environ["SETTING"] == "local"


def test_load_env_files_loads_service_url_without_legacy_alias(monkeypatch, tmp_path):
    child = tmp_path / "app_runtime"
    child.mkdir()
    (child / ".env").write_text(
        "SERVICE_URL=https://remote.example/db\nSETTING=alias\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("SERVICE_URL", raising=False)
    monkeypatch.delenv("SETTING", raising=False)

    load_env_files(child / "run.py")

    assert os.environ["SERVICE_URL"] == "https://remote.example/db"
    assert os.environ["SETTING"] == "alias"


def test_load_chain_env_files_prefers_chain_env_then_global_env(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    chain_env = root / "contract" / "robinhood" / "testnet" / ".env"
    global_env = root / "flashloan" / "app_runtime" / ".env"
    tool = root / "flashloan" / "src" / "dex" / "tools" / "run.py"
    chain_env.parent.mkdir(parents=True)
    global_env.parent.mkdir(parents=True)
    tool.parent.mkdir(parents=True)
    tool.write_text("", encoding="utf-8")
    chain_env.write_text(
        "ROBINHOOD_TESTNET_FOO=chain\nGLOBAL_SHARED=chain\n",
        encoding="utf-8",
    )
    global_env.write_text(
        "GLOBAL_SHARED=global\nCROSS_CHAIN=global-only\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("ROBINHOOD_TESTNET_FOO", raising=False)
    monkeypatch.delenv("GLOBAL_SHARED", raising=False)
    monkeypatch.delenv("CROSS_CHAIN", raising=False)

    loaded = load_chain_env_files(tool, [chain_env])

    assert chain_env in loaded
    assert global_env in loaded
    assert os.environ["ROBINHOOD_TESTNET_FOO"] == "chain"
    assert os.environ["GLOBAL_SHARED"] == "chain"
    assert os.environ["CROSS_CHAIN"] == "global-only"
