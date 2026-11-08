from __future__ import annotations

from unittest.mock import patch

from app_runtime import run as run_module


def test_stop_previous_worktree_instances_skips_missing_windows() -> None:
    with patch.object(run_module.os, "name", "nt"):
        with patch.object(
            run_module.subprocess,
            "run",
            side_effect=FileNotFoundError("wmic not available"),
        ):
            assert run_module.stop_previous_worktree_instances() == []


def test_stop_previous_worktree_instances_kills_matching_pids() -> None:
    with patch.object(run_module.os, "name", "nt"):
        fake_output = (
            "CommandLine ProcessId\r\n"
            "C:\\\\python.exe flashloan/app_runtime/run.py --port 8088 4242\r\n"
            "C:\\\\python.exe unrelated/tool.py 9999\r\n"
        )
        kill_calls: list[list[str]] = []

        def fake_run(cmd, *args, **kwargs):
            if cmd[:2] == ["wmic", "process"]:
                if kill_calls:
                    return type("Proc", (), {"stdout": b"CommandLine ProcessId\r\n"})()
                return type("Proc", (), {"stdout": fake_output.encode("utf-8")})()
            kill_calls.append(cmd)
            return type("Proc", (), {"returncode": 0})()

        with patch.object(run_module.subprocess, "run", side_effect=fake_run):
            with patch.object(run_module.time, "monotonic", side_effect=[0.0, 9.0]):
                stopped = run_module.stop_previous_worktree_instances()

    assert stopped == ["4242"]
    assert kill_calls and kill_calls[0][:3] == ["taskkill", "/F", "/PID"]


def test_stop_previous_worktree_instances_parses_powershell_json() -> None:
    with patch.object(run_module.os, "name", "nt"):
        calls: list[list[str]] = []
        fake_output = (
            '[{"ProcessId": 4321, "CommandLine": "C:\\\\python.exe flashloan/app_runtime/run.py --port 8088"}]'
        )

        def fake_run(cmd, *args, **kwargs):
            calls.append(cmd)
            if cmd[:2] == ["wmic", "process"]:
                return type("Proc", (), {"stdout": b""})()
            if cmd[0] == "powershell":
                return type("Proc", (), {"stdout": fake_output.encode("utf-8")})()
            return type("Proc", (), {"returncode": 0})()

        with patch.object(run_module.subprocess, "run", side_effect=fake_run):
            with patch.object(run_module.time, "monotonic", side_effect=[0.0, 9.0]):
                stopped = run_module.stop_previous_worktree_instances()

    assert stopped == ["4321"]
    assert any(cmd[0] == "powershell" for cmd in calls)
