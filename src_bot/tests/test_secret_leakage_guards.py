from __future__ import annotations

import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SECRET_PATTERNS = (
    re.compile(r"0x[a-fA-F0-9]{64}"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
    re.compile(
        r"""(?ix)
        \b(?:private[_-]?key|privatekey)\b
        \s*[:=]\s*["']?
        (?:0x)?[a-f0-9]{64}\b
        """
    ),
    re.compile(
        r"""(?ix)
        \b(?:mnemonic|seed(?:[_-]?phrase)?)\b
        \s*[:=]\s*["']
        (?:[a-z]+\s+){11,}[a-z]+
        """
    ),
)
TRACKED_SUFFIXES = {".py", ".js", ".mjs", ".sol", ".json", ".md", ".yml", ".yaml", ".toml", ".html"}
LOCAL_ENV_FILES = (
    REPO_ROOT / "flashloan" / "src_bot" / ".env",
    REPO_ROOT / "contracts-bot" / ".env",
    REPO_ROOT / "contracts-dex" / ".env",
)


def _tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    )
    files: list[Path] = []
    for raw in result.stdout.splitlines():
        path = Path(raw)
        if "deployments" in path.parts or "runtime" in path.parts or "cache" in path.parts:
            continue
        if path.suffix.lower() not in TRACKED_SUFFIXES and path.name not in {".env.example", ".env.testnet.example"}:
            continue
        files.append(REPO_ROOT / path)
    return files


def test_tracked_source_does_not_contain_private_key_literals():
    offenders: list[str] = []
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                offenders.append(str(path.relative_to(REPO_ROOT)))
                break

    assert offenders == []


def test_example_env_files_do_not_embed_secret_material():
    example_files = [
        REPO_ROOT / "flashloan" / "src_bot" / ".env.example",
        REPO_ROOT / "contracts-bot" / ".env.example",
        REPO_ROOT / "contracts-dex" / ".env.example",
        REPO_ROOT / "contracts-dex" / ".env.testnet.example",
    ]

    offenders: list[str] = []
    for path in example_files:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                offenders.append(str(path.relative_to(REPO_ROOT)))
                break

    assert offenders == []


def test_local_env_files_are_ignored_and_untracked():
    for path in LOCAL_ENV_FILES:
        relative_path = path.relative_to(REPO_ROOT)
        ignored = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", "--", str(relative_path)],
            check=False,
        )
        tracked = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "--error-unmatch", "--", str(relative_path)],
            check=False,
            capture_output=True,
            text=True,
        )

        assert ignored.returncode == 0, f"{relative_path} must be ignored by Git"
        assert tracked.returncode != 0, f"{relative_path} must not be tracked by Git"
