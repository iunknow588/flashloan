"""Validate that RPC/chain config is centralized in flashloan/src/chains/*/chain.json."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHAINS_ROOT = ROOT / "flashloan" / "src" / "chains"
FOUNDRY_GENERATED = ROOT / "runtime" / "external" / "rh-flash-toolkit" / "generated" / "foundry.chain.toml"

FORBIDDEN_FIELDS = ("officialRpc", "officialRpcUrls", "rpc_url", "rpc_urls")
RPC_RE = re.compile(r"https?://")


def iter_chain_files():
    registry = json.loads((CHAINS_ROOT / "registry.json").read_text(encoding="utf-8"))
    for raw, entry in (registry.get("chains") or {}).items():
        directory = entry.get("directory") if isinstance(entry, dict) else None
        if not directory:
            continue
        yield int(raw), CHAINS_ROOT / directory / "chain.json"


def main() -> int:
    errors: list[str] = []
    seen_rpc: dict[str, str] = {}
    for chain_id, chain_file in iter_chain_files():
        if not chain_file.exists():
            errors.append(f"missing chain config: {chain_file}")
            continue
        config = json.loads(chain_file.read_text(encoding="utf-8"))
        if config.get("chain_id") != chain_id:
            errors.append(f"{chain_file}: chain_id mismatch ({config.get('chain_id')} != {chain_id})")
        rpc = config.get("rpc") if isinstance(config.get("rpc"), dict) else {}
        primary = str(rpc.get("primary") or config.get("rpc_url") or "").strip()
        if not primary:
            errors.append(f"{chain_file}: missing rpc.primary")
        if not RPC_RE.match(primary):
            errors.append(f"{chain_file}: invalid rpc.primary {primary!r}")
        if rpc.get("primary") and rpc.get("primary") != primary:
            errors.append(f"{chain_file}: rpc.primary duplicates old rpc_url")
        if not isinstance(rpc.get("fallbacks"), list):
            errors.append(f"{chain_file}: rpc.fallbacks must be a list")
        if primary and primary in seen_rpc:
            errors.append(f"{chain_file}: duplicate RPC {primary!r} also in {seen_rpc[primary]}")
        if primary:
            seen_rpc[primary] = str(chain_file)
    for path in [
        ROOT / "contract" / "robinhood" / "DEX" / "config" / "chain.json",
        ROOT / "contract" / "robinhood-testnet" / "DEX" / "config" / "chain.json",
    ]:
        if not path.exists():
            continue
        config = json.loads(path.read_text(encoding="utf-8"))
        for field in FORBIDDEN_FIELDS:
            if field in config:
                errors.append(f"{path}: still contains deprecated field {field}")
    foundry_toml = ROOT / "runtime" / "external" / "rh-flash-toolkit" / "foundry.toml"
    text = foundry_toml.read_text(encoding="utf-8")
    if "[rpc_endpoints]" in text:
        errors.append(f"{foundry_toml}: still contains hardcoded [rpc_endpoints]")
    if not FOUNDRY_GENERATED.exists():
        errors.append(f"{FOUNDRY_GENERATED}: missing generated foundry config; run generate-foundry-config.py")
    if errors:
        print("\n".join(errors))
        return 1
    print("OK: all chain RPC configs are centralized")
    return 0


if __name__ == "__main__":
    sys.exit(main())

