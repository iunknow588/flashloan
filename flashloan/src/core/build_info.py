from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any


SRC_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

COMMIT_ENV_NAMES = (
    "FLASHLOAN_GIT_COMMIT",
    "REPLIT_GIT_COMMIT",
    "GITHUB_SHA",
    "COMMIT_SHA",
    "RENDER_GIT_COMMIT",
    "RAILWAY_GIT_COMMIT_SHA",
    "VERCEL_GIT_COMMIT_SHA",
)
REPLIT_ENV_NAMES = (
    "REPL_ID",
    "REPL_SLUG",
    "REPL_OWNER",
    "REPLIT_DEPLOYMENT_ID",
    "REPLIT_DEPLOYMENT",
)


def _git_value(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            timeout=2,
            check=False,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def _first_env(names: tuple[str, ...]) -> tuple[str | None, str | None]:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return name, value
    return None, None


def build_info_payload() -> dict[str, Any]:
    env_commit_name, env_commit = _first_env(COMMIT_ENV_NAMES)
    git_commit = _git_value("rev-parse", "HEAD")
    commit = env_commit or git_commit
    branch = _git_value("rev-parse", "--abbrev-ref", "HEAD")
    commit_time = _git_value("show", "-s", "--format=%cI", "HEAD") if git_commit else None
    replit = {
        name: os.getenv(name, "").strip()
        for name in REPLIT_ENV_NAMES
        if os.getenv(name, "").strip()
    }
    return {
        "app": "flashloan-src-bot",
        "git_commit": commit,
        "git_commit_short": commit[:7] if commit else None,
        "git_commit_source": env_commit_name or ("git" if git_commit else None),
        "git_branch": branch,
        "git_commit_time": commit_time,
        "entrypoint": str(Path(sys.argv[0]).resolve()) if sys.argv and sys.argv[0] else None,
        "cwd": str(Path.cwd().resolve()),
        "repo_root": str(REPO_ROOT),
        "src_root": str(SRC_ROOT),
        "replit": replit,
    }
