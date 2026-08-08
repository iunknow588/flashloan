#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent

REPO_ROOT_OVERRIDE = ""
REPO_HTTPS_URL = ""
PREFERRED_REMOTE_URL = ""


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.lstrip("\ufeff").split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_env_values(repo_root: Path | None = None) -> None:
    global REPO_ROOT_OVERRIDE, REPO_HTTPS_URL, PREFERRED_REMOTE_URL
    paths = [SCRIPT_DIR / ".env"]
    if repo_root is not None:
        paths.append(repo_root / ".env")
    for path in dict.fromkeys(paths):
        for key, value in read_env_file(path).items():
            if key == "REPO_ROOT_OVERRIDE":
                REPO_ROOT_OVERRIDE = value
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


def get_git_dir(root: Path) -> Path:
    result = run_git(root, "rev-parse", "--absolute-git-dir", capture=True, check=False)
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip())
    return root / ".git"


def has_rebase_in_progress(root: Path) -> bool:
    git_dir = get_git_dir(root)
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


def has_unmerged_paths(root: Path) -> bool:
    result = run_git(root, "diff", "--name-only", "--diff-filter=U", capture=True, check=False)
    return bool(result.stdout.strip())


def unmerged_paths(root: Path) -> list[str]:
    result = run_git(root, "diff", "--name-only", "--diff-filter=U", capture=True, check=False)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def current_rebase_subject(root: Path) -> str:
    result = run_git(root, "show", "-s", "--format=%s", "REBASE_HEAD", capture=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def is_bootstrap_initial_subject(subject: str) -> bool:
    return subject.strip().lower() in {"initial commit", "# initial commit"}


def remove_index_lock_if_requested(root: Path, cleanup_stale_lock: bool) -> None:
    lock_path = get_git_dir(root) / "index.lock"
    if not lock_path.exists():
        return
    if cleanup_stale_lock:
        lock_path.unlink()
        print(f"Removed stale git lock: {lock_path}")
        return
    raise RuntimeError(
        f"Git index lock exists: {lock_path}\n"
        "If no other git command is running, rerun with --cleanup-stale-lock."
    )


def continue_rebase_if_possible(root: Path) -> None:
    if not has_rebase_in_progress(root):
        return
    if has_unmerged_paths(root):
        subject = current_rebase_subject(root)
        if is_bootstrap_initial_subject(subject):
            print("Bootstrap initial commit rebase has conflicts. Aborting it before syncing from origin.")
            run_git(root, "rebase", "--abort")
            return
        raise RuntimeError(
            "A rebase is in progress and has conflicts. Resolve them, run `git add ...`, then rerun this script."
        )
    print("A rebase is in progress. Continuing it before syncing.")
    result = run_git(root, "rebase", "--continue", check=False)
    if result.returncode != 0:
        raise RuntimeError(
            "Could not continue the existing rebase automatically.\n"
            f"Run `git -C {root} rebase --continue` or `git -C {root} rebase --abort` and retry."
        )


def resolve_repo_root(create_if_missing: bool = False) -> Path:
    if REPO_ROOT_OVERRIDE:
        root = Path(REPO_ROOT_OVERRIDE).expanduser().resolve()
        if not root.exists():
            raise RuntimeError(f"Configured REPO_ROOT_OVERRIDE does not exist: {root}")
        return root

    intended_root = SCRIPT_DIR.parent if SCRIPT_DIR.name.lower() == "git" else SCRIPT_DIR
    if (intended_root / ".git").exists():
        return intended_root.resolve()
    if create_if_missing and intended_root.exists():
        return intended_root.resolve()

    candidate = SCRIPT_DIR
    while True:
        if (candidate / ".git").exists():
            return candidate
        if candidate.parent == candidate:
            break
        candidate = candidate.parent

    raise RuntimeError("Could not find a .git directory. Set REPO_ROOT_OVERRIDE or initialize the repository first.")


def head_exists(root: Path) -> bool:
    return run_git(root, "rev-parse", "--verify", "HEAD", capture=True, check=False).returncode == 0


def working_tree_status(root: Path) -> str:
    return run_git(root, "status", "--porcelain", capture=True).stdout.strip()


def local_commit_count(root: Path, branch: str) -> int:
    result = run_git(root, "rev-list", "--count", branch, capture=True, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return 0
    return int(result.stdout.strip())


def commit_subject(root: Path, ref: str) -> str:
    result = run_git(root, "log", "-1", "--format=%s", ref, capture=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def is_bootstrap_initial_branch(root: Path, branch: str, *, ahead: int, behind: int) -> bool:
    if ahead != 1 or behind <= 0:
        return False
    if local_commit_count(root, branch) != 1:
        return False
    return is_bootstrap_initial_subject(commit_subject(root, branch))


def stash_worktree_if_needed(root: Path, message: str) -> bool:
    if not working_tree_status(root):
        return False
    result = run_git(root, "stash", "push", "-u", "-m", message, capture=True, check=False)
    output = f"{result.stdout}\n{result.stderr}".lower()
    if result.returncode != 0:
        raise RuntimeError(
            "Could not stash local working-tree changes before bootstrap sync.\n"
            f"{result.stderr or result.stdout}"
        )
    return "no local changes" not in output


def restore_stash_after_sync(root: Path) -> None:
    result = run_git(root, "stash", "pop", capture=True, check=False)
    if result.returncode == 0:
        print("Reapplied local working-tree changes after sync.")
        return
    detail = f"{result.stderr or ''}\n{result.stdout or ''}".strip()
    untracked_restore_failed = (
        "could not restore untracked files from stash" in detail
        or "already exists, no checkout" in detail
    )
    if untracked_restore_failed and not has_unmerged_paths(root):
        print(
            "Synced to origin, but some stashed untracked files were not restored because "
            "origin now contains files with the same paths."
        )
        print("The stash was kept by Git. Inspect it with: git stash show --include-untracked stash@{0}")
        print("If those old local files are not needed, remove the saved stash with: git stash drop stash@{0}")
        return
    raise RuntimeError(
        "Synced to origin, but reapplying stashed local changes needs manual resolution.\n"
        f"{detail}"
    )


def remote_tracked_paths(root: Path, ref: str) -> list[str]:
    result = run_git(root, "ls-tree", "-r", "--name-only", ref, capture=True)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def backup_initial_checkout_conflicts(root: Path, ref: str, timestamp: str) -> dict[str, object]:
    moved: list[str] = []
    backup_root = get_git_dir(root) / f"bootstrap-untracked-backup-{timestamp}"
    for path_text in remote_tracked_paths(root, ref):
        local_path = root.joinpath(*path_text.split("/"))
        if not local_path.exists() and not local_path.is_symlink():
            continue
        backup_path = backup_root.joinpath(*path_text.split("/"))
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.rename(backup_path)
        moved.append(path_text)
    if moved:
        print(f"Backed up {len(moved)} bootstrap file(s) that overlap origin/{ref.split('/')[-1]} to {backup_root}")
    return {"path": str(backup_root) if moved else None, "moved": moved}


def checkout_initial_branch_from_origin(root: Path, branch: str, behind: int) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup = backup_initial_checkout_conflicts(root, f"origin/{branch}", timestamp)
    run_git(root, "checkout", "-f", "-B", branch, f"origin/{branch}")
    print(f"Initialised local branch {branch} from origin/{branch} ({behind} commits)")
    if backup.get("moved"):
        print(
            "Local bootstrap files that conflicted with origin were kept in the backup directory above. "
            "The checked out origin files are now active."
        )


def reset_bootstrap_initial_branch(root: Path, branch: str, behind: int) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup_branch = f"backup/bootstrap-initial-{timestamp}"
    stash_created = stash_worktree_if_needed(root, f"get.py bootstrap sync {timestamp}")
    run_git(root, "branch", backup_branch, branch)
    run_git(root, "reset", "--hard", f"origin/{branch}")
    print(f"Replaced bootstrap initial commit with origin/{branch} ({behind} commits). Backup branch: {backup_branch}")
    if stash_created:
        restore_stash_after_sync(root)


def reset_conflicted_worktree_to_origin(root: Path, branch: str) -> None:
    run_git(root, "rebase", "--abort", capture=True, check=False)
    run_git(root, "merge", "--abort", capture=True, check=False)
    fetch_origin(root, branch)
    run_git(root, "reset", "--hard", f"origin/{branch}")
    print(f"Discarded conflicted local state and reset {branch} to origin/{branch}.")


def unresolved_conflict_message(root: Path) -> str:
    paths = unmerged_paths(root)
    path_text = "\n".join(f"  - {path}" for path in paths[:20]) or "  - unknown"
    if len(paths) > 20:
        path_text += f"\n  - ... {len(paths) - 20} more"
    return (
        "Git has unresolved merge conflicts, so pulling the latest GitHub version is blocked.\n"
        f"Repository: {root}\n"
        f"Conflicted files:\n{path_text}\n\n"
        "If these Replit-local conflicted edits are not needed, rerun:\n"
        "  python git/get.py --discard-local-conflicts\n\n"
        "If they must be preserved, resolve each file, run `git add ...`, commit, then rerun this script."
    )


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


def fetch_origin(root: Path, *args: str, quiet: bool = False) -> subprocess.CompletedProcess[str]:
    command = ["fetch", "origin", *args]
    if quiet:
        command.append("--quiet")
    result = run_git(root, *command, capture=True, check=False)
    detail = f"{result.stderr}\n{result.stdout}"
    if result.returncode == 0 or "Permission denied (publickey)" not in detail or not REPO_HTTPS_URL:
        if result.returncode != 0:
            raise RuntimeError(f"git command failed: git -C {root} {' '.join(command)}\n{detail.strip()}")
        return result

    if get_origin_remote_url(root) != REPO_HTTPS_URL:
        print(f"SSH authentication failed. Switching origin to HTTPS -> {REPO_HTTPS_URL}")
        run_git(root, "remote", "set-url", "origin", REPO_HTTPS_URL)
        run_git(root, "remote", "set-url", "--push", "origin", REPO_HTTPS_URL)

    result = run_git(root, *command, capture=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"git command failed: git -C {root} {' '.join(command)}\n{detail}")
    return result


def current_or_default_branch(root: Path) -> str:
    branch = run_git(root, "branch", "--show-current", capture=True).stdout.strip()
    if branch and head_exists(root):
        return branch

    fetch_origin(root)
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
    fetch_origin(root, branch, quiet=True)
    if not head_exists(root):
        # Freshly initialised repo — local branch has no commits yet, so the
        # three-dot comparison fails with "ambiguous argument".  Report how
        # many commits the remote has; local is 0 ahead.
        count_str = run_git(root, "rev-list", "--count", f"origin/{branch}", capture=True).stdout.strip()
        return int(count_str), 0
    counts = run_git(root, "rev-list", "--left-right", "--count", f"origin/{branch}...{branch}", capture=True).stdout.split()
    if len(counts) < 2:
        raise RuntimeError(f"Unexpected branch comparison result: {' '.join(counts)}")
    return int(counts[0]), int(counts[1])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cross-platform git pull helper.")
    parser.add_argument("--init", action="store_true", help="Initialize the local repo if needed, then sync from origin.")
    parser.add_argument("--rebase", action="store_true", help="Use git pull --rebase.")
    parser.add_argument("--merge", action="store_true", help="Use git pull --no-ff when local and remote branches diverged.")
    parser.add_argument("--allow-dirty", action="store_true", help="Allow pulling with a dirty working tree.")
    parser.add_argument("--no-autostash", action="store_true", help="Do not pass --autostash to git pull --rebase/--merge.")
    parser.add_argument(
        "--discard-local-conflicts",
        action="store_true",
        help="Abort any unfinished merge/rebase and reset the local worktree to origin/<branch>.",
    )
    parser.add_argument(
        "--cleanup-stale-lock",
        action="store_true",
        help="Remove .git/index.lock before syncing. Use only after confirming no other git process is running.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        load_env_values()
        root = resolve_repo_root(create_if_missing=True)
        load_env_values(root)
        initialize_repository_if_missing(root)
        remove_index_lock_if_requested(root, args.cleanup_stale_lock)
        ensure_origin_remote(root)
        branch = current_or_default_branch(root)
        ensure_local_branch_name(root, branch)
        if args.discard_local_conflicts:
            reset_conflicted_worktree_to_origin(root, branch)
            return 0
        continue_rebase_if_possible(root)
        if has_unmerged_paths(root):
            raise RuntimeError(unresolved_conflict_message(root))

        status = working_tree_status(root)
        if status and not args.allow_dirty and args.no_autostash:
            raise RuntimeError("Working tree is dirty. Commit/stash changes first, or rerun with --allow-dirty.")

        behind, ahead = branch_sync_state(root, branch)
        if behind == 0 and ahead == 0:
            print(f"Branch {branch} is already up to date")
            return 0

        # Freshly initialized repo: local branch has no commits at all.
        # Existing bootstrap files such as git/get.py are untracked at this
        # point, so stash them before checking out origin and restore only the
        # files that do not conflict with tracked files from origin.
        if not head_exists(root) and behind > 0:
            checkout_initial_branch_from_origin(root, branch, behind)
            return 0

        if is_bootstrap_initial_branch(root, branch, ahead=ahead, behind=behind):
            reset_bootstrap_initial_branch(root, branch, behind)
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
