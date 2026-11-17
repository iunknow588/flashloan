# -*- coding: utf-8 -*-
"""Verify that the deployed pool-manager binary matches the current source.

The healer drives the pool manager through its CLI.  A deployed binary that
predates the current source tree accepts a command and then behaves like an
older tool: the healer sees "success" while the new behaviour never runs.
That is the silent no-op class of defect (AUD-P0-6): the repair path reports
green and the underlying problem is untouched.

This checker compares two independent facts:

``commandSet``
    The subcommands the deployed binary advertises in its usage line against
    the subcommands the current source tree implements.  A command that only
    exists in the source is the clearest proof that the binary is stale.

``sourceDigest``
    A SHA-256 digest over the crate's source files.  The digest is recorded
    in a stamp file next to the binary at build time; when the stamp is
    missing or does not match, the deployment is reported as unverifiable
    rather than silently accepted.

The script is read-only: it never writes the database and never rebuilds
anything unless ``--write-stamp`` is given (which only writes the stamp file
next to the binary).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
POOL_MANAGER_DIR = REPO_ROOT / "flashloan" / "tools" / "pool_manager"
DEFAULT_TOOLS = REPO_ROOT / "flashloan" / "tools" / "target" / "debug"
DEFAULT_BINARY = DEFAULT_TOOLS / "pool-manager.exe"
DEFAULT_STAMP = DEFAULT_BINARY.with_suffix(".source-stamp.json")

# The commands the healer and the plan depend on.  A binary missing one of
# them cannot run the corresponding repair step at all.
REQUIRED_COMMANDS = (
    "audit",
    "baseline-audit",
    "cleanup",
    "materialize-scope",
    "ensure-indexes",
    "ensure-depth-schema",
    "depth",
)

# Files whose content changes the observable behaviour of the binary.  The
# dependency manifests are included because a dependency bump can change the
# SDK math the capacity calculation relies on.
SOURCE_GLOBS = ("src/**/*.rs", "Cargo.toml")

USAGE_COMMAND_RE = re.compile(r"pool-manager\s+<([^>]+)>")
STAMP_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def source_files(crate_dir: Path) -> list[Path]:
    files: set[Path] = set()
    for pattern in SOURCE_GLOBS:
        files.update(crate_dir.glob(pattern))
    return sorted(path for path in files if path.is_file())


def compute_source_digest(crate_dir: Path) -> tuple[str, list[dict[str, Any]]]:
    """Return a stable digest over the crate source plus its file inventory."""
    entries: list[dict[str, Any]] = []
    hasher = hashlib.sha256()
    for path in source_files(crate_dir):
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        relative = path.relative_to(crate_dir).as_posix()
        entries.append({"path": relative, "sha256": digest, "bytes": len(data)})
        hasher.update(relative.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(digest.encode("ascii"))
        hasher.update(b"\0")
    return hasher.hexdigest(), entries


def source_command_set(crate_dir: Path) -> set[str]:
    """Read the subcommands the current source tree implements.

    The main dispatch is a ``match`` over the command string.  Each arm names
    a literal, so the literal set is the command set without having to build
    anything.
    """
    main_rs = crate_dir / "src" / "main.rs"
    text = main_rs.read_text(encoding="utf-8", errors="replace")
    commands: set[str] = set()
    for match in re.finditer(r'"([a-z][a-z0-9-]{2,})"\s*=>\s*\{', text):
        candidate = match.group(1)
        # Only the dispatch arms name a command.  Flags such as ``"scope"``
        # appear in the same shape, so a small blocklist keeps them out.
        if candidate in {"json", "ok", "error", "true", "false", "null", "scope"}:
            continue
        commands.add(candidate)
    usage = USAGE_COMMAND_RE.search(text)
    if usage:
        commands.update(part.strip() for part in usage.group(1).split("|") if part.strip())
    # The dispatch arms may declare aliases (``"refresh-pair" | "update-pair"``).
    # Those aliases are part of the source command set even when the usage
    # line does not spell them out.
    for match in re.finditer(
        r'"([a-z][a-z0-9-]{2,})"\s*\|\s*"([a-z][a-z0-9-]{2,})"\s*=>', text
    ):
        commands.add(match.group(1))
        commands.add(match.group(2))
    return commands


def binary_command_set(binary: Path) -> tuple[set[str] | None, str | None]:
    """Ask the deployed binary which commands it supports."""
    try:
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=60
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as error:
        return None, f"probe failed: {error}"
    usage = f"{result.stdout or ''}\n{result.stderr or ''}"
    match = USAGE_COMMAND_RE.search(usage)
    if not match:
        return None, "usage line did not advertise a command set"
    return {part.strip() for part in match.group(1).split("|") if part.strip()}, None


def read_stamp(stamp_path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(stamp_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def write_stamp(stamp_path: Path, digest: str, entries: list[dict[str, Any]],
                binary: Path) -> None:
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "stampVersion": STAMP_VERSION,
        "sourceDigest": digest,
        "sourceDigestAlgorithm": "sha256",
        "generatedAt": _utc_now(),
        "binary": str(binary),
        "binarySha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "files": entries,
    }
    stamp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def check(binary: Path, crate_dir: Path, stamp_path: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "checkedAt": _utc_now(),
        "binary": str(binary),
        "crateDir": str(crate_dir),
        "stampPath": str(stamp_path),
    }
    problems: list[str] = []

    if not binary.exists():
        report["valid"] = False
        report["problems"] = ["deployed_binary_missing"]
        return report

    source_digest, entries = compute_source_digest(crate_dir)
    source_commands = source_command_set(crate_dir)
    binary_commands, probe_error = binary_command_set(binary)
    report["sourceDigest"] = source_digest
    report["sourceFileCount"] = len(entries)
    report["sourceCommandSet"] = sorted(source_commands)
    report["binaryCommandSet"] = sorted(binary_commands) if binary_commands else None
    report["binaryProbeError"] = probe_error

    if binary_commands is None:
        problems.append("binary_command_set_unknown")
    else:
        missing_in_binary = sorted(source_commands - binary_commands)
        missing_required = sorted(set(REQUIRED_COMMANDS) - binary_commands)
        report["commandsMissingFromBinary"] = missing_in_binary
        report["requiredCommandsMissingFromBinary"] = missing_required
        if missing_required:
            problems.append("required_command_missing_from_binary")
        if missing_in_binary:
            problems.append("binary_is_behind_source")

    stamp = read_stamp(stamp_path)
    report["stampPresent"] = stamp is not None
    if stamp is None:
        problems.append("source_stamp_missing")
    else:
        report["stampSourceDigest"] = stamp.get("sourceDigest")
        report["stampBinarySha256"] = stamp.get("binarySha256")
        actual_binary_digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        report["binarySha256"] = actual_binary_digest
        if stamp.get("sourceDigest") != source_digest:
            problems.append("source_digest_mismatch")
        if stamp.get("binarySha256") and stamp["binarySha256"] != actual_binary_digest:
            problems.append("binary_digest_mismatch")

    report["problems"] = problems
    report["valid"] = not problems
    report["verdict"] = (
        "deployed binary matches the current source" if not problems
        else "deployed binary does NOT match the current source"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the deployed pool-manager binary matches the source tree"
    )
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--crate-dir", type=Path, default=POOL_MANAGER_DIR)
    parser.add_argument("--stamp", type=Path, default=DEFAULT_STAMP)
    parser.add_argument("--write-stamp", action="store_true",
                        help="write the source stamp next to the binary")
    parser.add_argument("--json", action="store_true", help="print the full JSON report")
    args = parser.parse_args()

    if args.write_stamp:
        if not args.binary.exists():
            parser.error(f"binary does not exist: {args.binary}")
        digest, entries = compute_source_digest(args.crate_dir)
        write_stamp(args.stamp, digest, entries, args.binary)
        print(f"stamp={args.stamp} sourceDigest={digest}")

    report = check(args.binary, args.crate_dir, args.stamp)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"binary={report['binary']}")
        print(f"valid={report['valid']} verdict={report.get('verdict')}")
        print(f"sourceDigest={report.get('sourceDigest')}")
        print(f"sourceCommands={report.get('sourceCommandSet')}")
        print(f"binaryCommands={report.get('binaryCommandSet')}")
        if report.get("problems"):
            print(f"problems={','.join(report['problems'])}")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
