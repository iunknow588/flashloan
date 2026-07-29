#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent

REPO_ROOT_OVERRIDE = ""
PREFERRED_REMOTE_URL = ""


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_env_values(repo_root: Path | None = None) -> None:
    global REPO_ROOT_OVERRIDE, PREFERRED_REMOTE_URL
    paths = [SCRIPT_DIR / ".env"]
    if repo_root is not None:
        paths.append(repo_root / ".env")
    for path in dict.fromkeys(paths):
        for key, value in read_env_file(path).items():
            if key == "REPO_ROOT_OVERRIDE":
                REPO_ROOT_OVERRIDE = value
            elif key == "PREFERRED_REMOTE_URL":
                PREFERRED_REMOTE_URL = value


def run_git(root: Path, *args: str, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = ["git", "-C", str(root), *args]
    result = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"git command failed: {' '.join(command)}\n{detail}")
    return result


def resolve_repo_root(create_if_missing: bool = False) -> Path:
    if REPO_ROOT_OVERRIDE:
        root = Path(REPO_ROOT_OVERRIDE).expanduser().resolve()
        if not root.exists():
            raise RuntimeError(f"Configured REPO_ROOT_OVERRIDE does not exist: {root}")
        return root

    candidate = SCRIPT_DIR
    while True:
        if (candidate / ".git").exists():
            return candidate
        if candidate.parent == candidate:
            break
        candidate = candidate.parent

    default_root = SCRIPT_DIR.parent
    if create_if_missing and default_root.exists():
        return default_root.resolve()
    raise RuntimeError("Could not find a .git directory. Set REPO_ROOT_OVERRIDE or initialize the repository first.")


def head_exists(root: Path) -> bool:
    return run_git(root, "rev-parse", "--verify", "HEAD", capture=True, check=False).returncode == 0


def initialize_repository_if_missing(root: Path) -> bool:
    if (root / ".git").exists():
        return False
    print(f"No .git directory found. Initializing a repository in {root}")
    run_git(root, "init")
    run_git(root, "branch", "-M", "main", check=False)
    return True


def get_origin_remote_url(root: Path) -> str:
    result = run_git(root, "remote", "get-url", "origin", capture=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def ensure_origin_remote(root: Path) -> None:
    global PREFERRED_REMOTE_URL
    if not PREFERRED_REMOTE_URL:
        PREFERRED_REMOTE_URL = get_origin_remote_url(root)
    if not PREFERRED_REMOTE_URL:
        raise RuntimeError("No remote URL configured. Set PREFERRED_REMOTE_URL in git/.env or configure git origin first.")

    remotes = run_git(root, "remote", capture=True).stdout.splitlines()
    if "origin" in remotes:
        run_git(root, "remote", "set-url", "origin", PREFERRED_REMOTE_URL)
        run_git(root, "remote", "set-url", "--push", "origin", PREFERRED_REMOTE_URL)
        print(f"Updated origin -> {PREFERRED_REMOTE_URL}")
    else:
        run_git(root, "remote", "add", "origin", PREFERRED_REMOTE_URL)
        print(f"Added origin -> {PREFERRED_REMOTE_URL}")


def current_or_default_branch(root: Path) -> str:
    branch = run_git(root, "branch", "--show-current", capture=True).stdout.strip()
    if branch and head_exists(root):
        return branch

    run_git(root, "fetch", "origin")
    origin_head = run_git(root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", capture=True, check=False)
    value = origin_head.stdout.strip()
    if value.startswith("origin/"):
        return value.removeprefix("origin/")

    branches = set(run_git(root, "branch", "-r", "--format", "%(refname:short)", capture=True).stdout.splitlines())
    if "origin/main" in branches:
        return "main"
    if "origin/master" in branches:
        return "master"
    raise RuntimeError("Detached HEAD or unborn branch detected, and could not determine origin's default branch.")


def ensure_local_branch_name(root: Path, branch: str) -> None:
    current = run_git(root, "branch", "--show-current", capture=True).stdout.strip()
    if not current or current == branch or head_exists(root):
        return
    run_git(root, "branch", "-m", branch)
    print(f"Renamed unborn branch {current} -> {branch}")


def branch_sync_state(root: Path, branch: str) -> tuple[int, int]:
    run_git(root, "fetch", "origin", branch, "--quiet")
    counts = run_git(root, "rev-list", "--left-right", "--count", f"origin/{branch}...{branch}", capture=True).stdout.split()
    if len(counts) < 2:
        raise RuntimeError(f"Unexpected branch comparison result: {' '.join(counts)}")
    return int(counts[0]), int(counts[1])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cross-platform git pull helper.")
    parser.add_argument("--rebase", action="store_true", help="Use git pull --rebase.")
    parser.add_argument("--merge", action="store_true", help="Use git pull --no-ff when local and remote branches diverged.")
    parser.add_argument("--allow-dirty", action="store_true", help="Allow pulling with a dirty working tree.")
    parser.add_argument("--no-autostash", action="store_true", help="Do not pass --autostash to git pull --rebase/--merge.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        load_env_values()
        root = resolve_repo_root(create_if_missing=True)
        load_env_values(root)
        initialize_repository_if_missing(root)
        ensure_origin_remote(root)
        branch = current_or_default_branch(root)
        ensure_local_branch_name(root, branch)

        status = run_git(root, "status", "--porcelain", capture=True).stdout.strip()
        if status and not args.allow_dirty and args.no_autostash:
            raise RuntimeError("Working tree is dirty. Commit/stash changes first, or rerun with --allow-dirty.")

        behind, ahead = branch_sync_state(root, branch)
        if behind == 0 and ahead == 0:
            print(f"Branch {branch} is already up to date")
            return 0
        if ahead > 0 and behind > 0 and not args.rebase and not args.merge:
            print(
                f"Local branch and origin/{branch} diverged: local is ahead by {ahead}, behind by {behind}.\n"
                f"Rebasing local commits on top of origin/{branch}."
            )
            args.rebase = True

        if args.rebase:
            command = ["pull", "--rebase"]
            if not args.no_autostash:
                command.append("--autostash")
            run_git(root, *command, "origin", branch)
        elif args.merge:
            command = ["pull", "--no-ff"]
            if not args.no_autostash:
                command.append("--autostash")
            run_git(root, *command, "origin", branch)
        else:
            run_git(root, "pull", "--ff-only", "origin", branch)
        print(f"Updated branch {branch}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
