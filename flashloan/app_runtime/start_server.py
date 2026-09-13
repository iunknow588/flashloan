#!/usr/bin/env python3
"""Start the control panel and keep its stdout/stderr inside the canonical log directory."""

import os
import subprocess
import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = Path(os.getenv("FLASHLOAN_RUNTIME_DIR", str(APP_DIR / "runtime"))).resolve()
LOG_DIR = Path(os.getenv("FLASHLOAN_LOG_DIR", str(RUNTIME_DIR / "logs"))).resolve()


def _read_port() -> str:
    if "--port" in sys.argv:
        try:
            return sys.argv[sys.argv.index("--port") + 1]
        except IndexError:
            pass
    return os.getenv("PORT", "5000")


def main() -> int:
    port = _read_port()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stdout_path = LOG_DIR / f"server_{port}_stdout.log"
    stderr_path = LOG_DIR / f"server_{port}_stderr.log"

    with stdout_path.open("ab", buffering=0) as stdout, stderr_path.open("ab", buffering=0) as stderr:
        env = os.environ.copy()
        env.setdefault("FLASHLOAN_RUNTIME_DIR", str(RUNTIME_DIR))
        cmd = [sys.executable, str(APP_DIR / "run.py"), *sys.argv[1:]]
        print(f"server log dir: {LOG_DIR}", flush=True)
        print(f"stdout log: {stdout_path}", flush=True)
        print(f"stderr log: {stderr_path}", flush=True)
        print(f"command: {' '.join(cmd)}", flush=True)
        process = subprocess.Popen(cmd, env=env, stdout=stdout, stderr=stderr)
        print(f"started pid={process.pid}", flush=True)
        return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
