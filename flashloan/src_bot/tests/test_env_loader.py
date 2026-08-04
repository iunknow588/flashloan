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
