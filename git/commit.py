#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent

REPO_ROOT_OVERRIDE = ""
REPO_SSH_URL = ""
REPO_HTTPS_URL = ""
PREFERRED_REMOTE_URL = ""
COMMIT_PATHSPEC = "."


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
    global REPO_ROOT_OVERRIDE, REPO_SSH_URL, REPO_HTTPS_URL, PREFERRED_REMOTE_URL
    paths = [SCRIPT_DIR / ".env"]
    if repo_root is not None:
        paths.append(repo_root / ".env")
    for path in dict.fromkeys(paths):
        for key, value in read_env_file(path).items():
            if key == "REPO_ROOT_OVERRIDE":
                REPO_ROOT_OVERRIDE = value
            elif key == "REPO_SSH_URL":
                REPO_SSH_URL = value
            elif key == "REPO_HTTPS_URL":
                REPO_HTTPS_URL = value
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


def run_command(cwd: Path, *args: str) -> None:
    result = subprocess.run([*args], cwd=str(cwd), text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"command failed in {cwd}: {' '.join(args)}")


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


def current_branch(root: Path) -> str:
    branch = run_git(root, "branch", "--show-current", capture=True).stdout.strip()
    if not branch:
        raise RuntimeError("Detached HEAD detected. Cannot auto-commit and push.")
    return branch


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


def invoke_selftests(root: Path) -> None:
    for package in ("skills/local-story-access", "skills/remote-gateway", "engine"):
        package_root = root / package
        if not (package_root / "package.json").exists():
            continue
        print(f"Running selftest: {package}")
        run_command(package_root, "npm", "run", "selftest")


def remove_staged_ignored_pathspecs(root: Path) -> None:
    ignored = (
        ".vercel/**",
        "target/**",
        ":(glob)common/**/target/**",
        ":(glob)host/**/target/**",
        ":(glob)plugins/**/target/**",
        ":(glob)storylock/**/target/**",
        ":(glob)tools/**/target/**",
        ":(glob)yian-web/**/node_modules/**",
        ":(glob)**/node_modules/**",
    )
    for pathspec in ignored:
        matches = run_git(root, "diff", "--cached", "--name-only", "--", pathspec, capture=True).stdout.splitlines()
        if matches:
            run_git(root, "restore", "--staged", "--", pathspec)


def stage_changes(root: Path, pathspec: str) -> None:
    if head_exists(root):
        run_git(root, "restore", "--staged", "--", ".")
    status = run_git(root, "status", "--porcelain", "--", pathspec, capture=True).stdout.splitlines()
    if not status:
        return
    run_git(root, "add", "--all", "--", pathspec)
    remove_staged_ignored_pathspecs(root)


def has_staged_changes(root: Path, pathspec: str) -> bool:
    result = run_git(root, "diff", "--cached", "--quiet", "--", pathspec, check=False)
    if result.returncode == 0:
        return False
    if result.returncode == 1:
        return True
    raise RuntimeError(f"Failed to inspect staged changes under {pathspec}.")


def branch_sync_state(root: Path, branch: str) -> tuple[int, int]:
    run_git(root, "fetch", "origin", branch, "--quiet")
    counts = run_git(root, "rev-list", "--left-right", "--count", f"origin/{branch}...{branch}", capture=True).stdout.split()
    if len(counts) < 2:
        raise RuntimeError(f"Unexpected branch comparison result: {' '.join(counts)}")
    return int(counts[0]), int(counts[1])


def command_commit(args: argparse.Namespace) -> int:
    load_env_values()
    root = resolve_repo_root(create_if_missing=True)
    load_env_values(root)
    created = initialize_repository_if_missing(root)
    ensure_origin_remote(root)
    if created:
        print("Repository initialized on branch main")

    if not args.skip_self_test:
        invoke_selftests(root)

    branch = current_branch(root)
    stage_changes(root, COMMIT_PATHSPEC)
    if has_staged_changes(root, COMMIT_PATHSPEC):
        message = args.message or f"Auto commit {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        run_git(root, "commit", "-m", message)
        print(f"Committed on branch {branch}")
    else:
        print("No changes to commit")

    if args.no_push:
        print("Skip push because --no-push was specified")
        return 0

    behind, ahead = branch_sync_state(root, branch)
    if ahead == 0:
        if behind > 0:
            print(f"Local branch is behind origin/{branch} by {behind} commit(s). Nothing new to push.")
            print(f"Run `git -C {root} pull --rebase origin {branch}` first.")
            return 0
        print(f"Local branch is already up to date with origin/{branch}. Nothing to push.")
        return 0

    result = run_git(root, "push", "-u", "origin", branch, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Push failed. The remote branch is likely ahead. Run `git -C {root} pull --rebase origin {branch}` and retry.")
    print(f"Pushed to origin/{branch}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cross-platform git commit helper.")
    parser.add_argument("-m", "--message", default="", help="Commit message.")
    parser.add_argument("--no-push", action="store_true", help="Commit locally without pushing.")
    parser.add_argument("--skip-self-test", action="store_true", help="Skip package selftests.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(command_commit(args))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
