import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
TESTNET_MANIFEST = REPO_ROOT / "test-token_create" / "config" / "testnet-v4-amm" / "manifest.json"
TESTNET_CHAIN_JSON = REPO_ROOT / "flashloan" / "src" / "chains" / "robinhood-testnet" / "chain.json"
TESTNET_CONTRACT_MANIFEST = (
    REPO_ROOT / "contract" / "robinhood-testnet" / "DEX" / "config" / "testnet-v4-amm" / "manifest.json"
)
SHARED_ENV_FILES = [
    REPO_ROOT / "flashloan" / "app_runtime" / ".env",
    REPO_ROOT / "flashloan" / "app_runtime" / ".env.test",
    REPO_ROOT / "contract" / "robinhood-testnet" / "DEX" / ".env",
]
FORBIDDEN_BROADCAST_ENV_MARKERS = (
    "ROBINHOOD_MAINNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST",
    "ALLOW_OFFICIAL_V4_COMPOSER_BROADCAST",
    "ALLOW_OFFICIAL_V4_MAKE_ARB_BROADCAST",
    "ALLOW_OFFICIAL_V4_REPLENISH_BROADCAST",
)
TESTNET_ARB_BROADCAST_ENV_KEY = "ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST"
OPERATOR_RUNTIME_ENV = REPO_ROOT / "flashloan" / "app_runtime" / ".env"

# Only one usable OfficialV4FlashArb is maintained per chain.  The runtime
# chain config and the authoritative manifests must point at that single
# latest contract; historical live-test addresses are never fallback
# candidates and obsolete deployments are intentionally out of scope.
LATEST_TESTNET_OFFICIAL_V4_ARB = "0x1A8Cc533EaBA93Ec7f68Ac6c428cDa948D7bB8DE"
OBSOLETE_TESTNET_OFFICIAL_V4_ARB = {
    "0x9b4feb127297a4f827522d57c2a9b3d01229d0c0",
    "0xd46327b50f3a16253ff16f7f657dede7ab19b808",
    "0xce9221c1c54bd6a52d66a8c5003d773d6774b383",
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_testnet_chain_official_v4_matches_authoritative_manifest():
    manifest = _load_json(TESTNET_MANIFEST)
    chain = _load_json(TESTNET_CHAIN_JSON)
    official = manifest["officialV4"]
    expected = {
        "arb": official["arb"],
        "composer": official["composer"],
    }
    assert {"arb": chain["officialV4"]["arb"], "composer": chain["officialV4"]["composer"]} == expected


def test_testnet_contract_manifest_matches_authoritative_manifest():
    authoritative = _load_json(TESTNET_MANIFEST)
    contract_manifest = _load_json(TESTNET_CONTRACT_MANIFEST)
    assert contract_manifest["officialV4"] == authoritative["officialV4"]
    official = authoritative["officialV4"]
    assert official["arb"].lower() == LATEST_TESTNET_OFFICIAL_V4_ARB.lower()
    assert official["arb"].lower() not in OBSOLETE_TESTNET_OFFICIAL_V4_ARB
    if official["composer"] == official["lastComposerLiveTest"]["composer"]:
        return
    authority = official.get("composerAuthority") or {}
    assert authority.get("address", "").lower() == official["composer"].lower()
    assert authority.get("evidenceType") == "official_v4_custom_contracts_code_check"
    assert authority.get("checkedAt")


def test_testnet_arb_live_script_only_uses_canonical_address():
    script = (
        REPO_ROOT
        / "test-token_create"
        / "scripts"
        / "tool"
        / "testnet-v4-official-arb-live.js"
    ).read_text(encoding="utf-8")
    assert "lastArbLiveTest?.arb" not in script
    assert "manifest.officialV4.arb" in script


def test_shared_runtime_env_never_enables_forbidden_official_v4_broadcast_gates():
    offenders = []
    for env_path in SHARED_ENV_FILES:
        if not env_path.exists():
            continue
        for line_number, line in enumerate(env_path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            key, separator, value = stripped.partition("=")
            if not separator:
                continue
            enabled = str(value).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            if key.strip() in FORBIDDEN_BROADCAST_ENV_MARKERS and enabled:
                offenders.append(f"{env_path}:{line_number}:{key}={value}")
            if (
                key.strip() == TESTNET_ARB_BROADCAST_ENV_KEY
                and enabled
                and env_path != OPERATOR_RUNTIME_ENV
            ):
                offenders.append(
                    f"{env_path}:{line_number}:{key}={value} "
                    "(testnet arb broadcast may only be enabled in the operator runtime .env)"
                )
    assert not offenders, (
        "shared runtime env must never enable forbidden official V4 broadcast gates "
        "and testnet arb broadcast only belongs in the operator runtime .env:\n"
        + "\n".join(offenders)
    )
