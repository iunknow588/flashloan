import os

from core.env_loader import load_env_files


def test_load_env_files_prefers_process_environment(monkeypatch, tmp_path):
    child = tmp_path / "src_bot"
    child.mkdir()
    (tmp_path / ".env").write_text("SETTING=parent\n", encoding="utf-8")
    (child / ".env").write_text("SETTING=local\n", encoding="utf-8")
    monkeypatch.setenv("SETTING", "process")

    loaded = load_env_files(child / "run.py")

    assert loaded == [child / ".env", tmp_path / ".env"]
    assert os.environ["SETTING"] == "process"


def test_load_env_files_prefers_nearest_env_file_when_process_value_is_missing(monkeypatch, tmp_path):
    child = tmp_path / "src_bot"
    child.mkdir()
    (tmp_path / ".env").write_text("SETTING=parent\n", encoding="utf-8")
    (child / ".env").write_text("SETTING=local\n", encoding="utf-8")
    monkeypatch.delenv("SETTING", raising=False)

    load_env_files(child / "run.py")

    assert os.environ["SETTING"] == "local"


def test_load_env_files_applies_non_sensitive_test_env_overrides(monkeypatch, tmp_path):
    child = tmp_path / "src_bot"
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
