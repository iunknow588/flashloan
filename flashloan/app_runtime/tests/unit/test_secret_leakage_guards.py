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
CONTRACT_SOURCE_DIRS = (
    REPO_ROOT / "contract" / "avalanche" / "BOT",
    REPO_ROOT / "contract" / "avalanche" / "DEX",
    REPO_ROOT / "contract" / "avalanche-testnet" / "BOT",
    REPO_ROOT / "contract" / "avalanche-testnet" / "DEX",
    REPO_ROOT / "contract" / "robinhood" / "DEX",
    REPO_ROOT / "contract" / "robinhood-testnet" / "DEX",
)
LOCAL_ENV_FILES = (
    REPO_ROOT / "flashloan" / "app_runtime" / ".env",
    REPO_ROOT / "contract" / "avalanche" / "BOT" / ".env",
    REPO_ROOT / "contract" / "avalanche" / "DEX" / ".env",
    REPO_ROOT / "contract" / "robinhood-testnet" / "DEX" / ".env",
)
NON_SECRET_HEX_FIXTURES = (
    # Test-only V4 pool ids and deterministic addresses; not key material.
    "0xe992d4e9041b58b363a83c1863a664a7c07e46728ee6788a38f789102bff83a0",
    "0x0000000000000000000000001111111111111111111111111111111111111111",
    "0x2222222222222222222222222222222222222222222222222222222222222222",
    "0x6dff37dd1a996412e30723462231d2a67bc7d66702b498aa739746797c4302a3",
    # Uniswap V4 pool ids taken from the Swap event topic of the recorded
    # testnet trades; a pool id is a keccak of the pool key, not key
    # material.
    "0xcf6cbc2e370cbe2d78a33232f0b1ecf960b01e7745dddec34f3e838768d9d94b",
    "0x1965da48bc986ea0f6197b65521da2f668c22ccc5830f628d0f8f920bef8bc66",
    "0xcd5b37560fdaf7cabd475c51d4e5127d7321d6eb45e3d5bacf61f59b91b6a069",
)
STOCK_REGISTRY_ID_SUFFIX = "flashloan/chains/robinhood/robinhood_stock_tokens.json"

# Public chain identifiers that are 32 bytes wide and therefore match the
# private-key literal pattern without being key material:
#
# * a Uniswap V4 pool id is the keccak of the pool key,
# * a transaction hash is the keccak of a signed transaction,
# * ``0x7fff...ff`` is the max-uint256 sentinel used by the amount ladder.
#
# The guard has to recognise them by the field that carries them, otherwise
# every recorded runtime evidence report would look like a leaked key.
PUBLIC_IDENTIFIER_FIELDS = (
    "poolId",
    "pool_id",
    "txHash",
    "tx_hash",
    "transactionHash",
    "transaction_hash",
    "lastReplenishTx",
    "lastMakeArbTx",
    "anomalyId",
    "arbBytecode",
    "composerCode",
)
MAX_UINT256_HEX = "0x7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"


def _sanitize_public_chain_identifiers(text: str) -> str:
    """Blank the public 32-byte identifiers a record carries by field name."""
    for field in PUBLIC_IDENTIFIER_FIELDS:
        text = re.sub(
            r'("' + re.escape(field) + r'"\s*:\s*")0x[a-fA-F0-9]{64}',
            r"\g<1>0x" + "n" * 64,
            text,
        )
    return re.sub(
        re.escape(MAX_UINT256_HEX),
        "0x" + "n" * 64,
        text,
        flags=re.IGNORECASE,
    )


def _sanitize_markdown_transaction_hashes(text: str) -> str:
    """Blank a transaction hash that a markdown report names in prose.

    The recorded reports describe a transaction as ``txHash : 0x...``. The
    hash is public chain data, not key material, so the prose form has to
    be recognised as well.
    """
    return re.sub(
        r"(?i)(txHash\s*[:=]\s*)0x[a-fA-F0-9]{64}",
        r"\g<1>0x" + "n" * 64,
        text,
    )


def _tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    )
    files: list[Path] = []
    seen: set[Path] = set()
    for raw in result.stdout.splitlines():
        path = Path(raw)
        if "deployments" in path.parts or "runtime" in path.parts or "cache" in path.parts:
            continue
        if path.suffix.lower() not in TRACKED_SUFFIXES and path.name not in {".env.example", ".env.testnet.example"}:
            continue
        resolved = REPO_ROOT / path
        if resolved.exists() and resolved not in seen:
            files.append(resolved)
            seen.add(resolved)
    for root in CONTRACT_SOURCE_DIRS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if {"node_modules", "artifacts", "cache", "deployments"} & set(path.parts):
                continue
            if path.suffix.lower() not in TRACKED_SUFFIXES and path.name not in {".env.example", ".env.testnet.example"}:
                continue
            if path not in seen:
                files.append(path)
                seen.add(path)
    return files


def _sanitize_public_registry_ids(text: str, path: Path) -> str:
    """Blank the public ERC-1155 asset ids in the Robinhood stock registry.

    The assets API returns 64-hex token ids that would otherwise match the
    private-key literal pattern even though they are not key material.
    """
    if not str(path).replace("\\", "/").endswith(STOCK_REGISTRY_ID_SUFFIX):
        return text
    return re.sub(
        r'(\n\s*"id"\s*:\s*")0x[a-fA-F0-9]{64}(")',
        r"\g<1>0x" + "n" * 64 + r"\g<2>",
        text,
    )


def test_tracked_source_does_not_contain_private_key_literals():
    offenders: list[str] = []
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        sanitized = text
        sanitized = _sanitize_public_registry_ids(sanitized, path)
        sanitized = _sanitize_public_chain_identifiers(sanitized)
        sanitized = _sanitize_markdown_transaction_hashes(sanitized)
        for fixture in NON_SECRET_HEX_FIXTURES:
            sanitized = re.sub(re.escape(fixture), "0x" + "n" * 64, sanitized, flags=re.IGNORECASE)
        for pattern in SECRET_PATTERNS:
            if pattern.search(sanitized):
                offenders.append(str(path.relative_to(REPO_ROOT)))
                break

    assert offenders == []


def test_example_env_files_do_not_embed_secret_material():
    example_files = [
        REPO_ROOT / "flashloan" / "app_runtime" / ".env.example",
        REPO_ROOT / "contract" / "avalanche" / "BOT" / ".env.example",
        REPO_ROOT / "contract" / "avalanche" / "DEX" / ".env.example",
        REPO_ROOT / "contract" / "avalanche" / "DEX" / ".env.testnet.example",
        REPO_ROOT / "contract" / "robinhood-testnet" / "DEX" / "config" / ".env.testnet.example",
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
