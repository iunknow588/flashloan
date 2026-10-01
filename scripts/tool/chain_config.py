"""Shared chain configuration helpers for flashLoan CLI/tool scripts.

The helpers keep chain-level choices in one place: registry lookup, chain.json,
contracts.json, manifest path, RPC URL and Hardhat network name. Command line
entrypoints call :func:`set_chain_env` to build an environment dictionary for
spawned child tools without mutating the current process.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CHAIN_DATA_ROOT = ROOT / "flashloan" / "src" / "chains"
REGISTRY_PATH = CHAIN_DATA_ROOT / "registry.json"

MANIFEST_BY_CHAIN = {
    31337: ROOT / "test-token_create" / "config" / "local-v4-amm" / "manifest.json",
    46630: ROOT / "test-token_create" / "config" / "testnet-v4-amm" / "manifest.json",
}

HARDHAT_NETWORK_BY_CHAIN = {
    31337: "localhost",
    4663: "robinhood",
    46630: "robinhoodTestnet",
}


def _read_json(path: Path, fallback: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def workspace_root() -> Path:
    return ROOT


def chain_data_root() -> Path:
    return CHAIN_DATA_ROOT


def registry() -> dict[str, Any]:
    return _read_json(REGISTRY_PATH, {}) or {}


def known_chain_ids() -> list[int]:
    chains = registry().get("chains") or {}
    result = []
    for raw in chains:
        try:
            result.append(int(raw))
        except (TypeError, ValueError):
            continue
    return sorted(result)


def chain_registry_entry(chain_id: int) -> dict[str, Any]:
    chains = registry().get("chains") or {}
    raw = chains.get(str(chain_id))
    if not isinstance(raw, dict):
        return {}
    return raw


def _chain_data_path(chain_id: int) -> Path:
    directory = str(chain_registry_entry(chain_id).get("directory") or "").strip()
    if not directory:
        return CHAIN_DATA_ROOT / str(chain_id) / "chain.json"
    return CHAIN_DATA_ROOT / directory / "chain.json"


def chain_json_path(chain_id: int) -> Path:
    """Prefer the src-runtime chain.json, then the contract config chain.json."""

    src_path = _chain_data_path(chain_id)
    if src_path.exists():
        return src_path
    for candidate in _contract_config_files(chain_id, "chain.json"):
        if candidate.exists():
            return candidate
    return src_path


def contracts_json_path(chain_id: int) -> Path:
    """Resolve contracts.json, preferring the Hardhat project config."""

    for candidate in _contract_config_files(chain_id, "contracts.json"):
        if candidate.exists():
            return candidate
    fallback = chain_json_path(chain_id).parent / "contracts.json"
    return fallback


def _contract_config_files(chain_id: int, name: str) -> list[Path]:
    chain_name = str(chain_registry_entry(chain_id).get("directory") or "").strip()
    if chain_name:
        candidates = [
            ROOT / "contract" / chain_name / "DEX" / "config" / name,
            ROOT / "contract" / chain_name / "BOT" / "config" / name,
        ]
    else:
        candidates = []
    candidates.append(CHAIN_DATA_ROOT / str(chain_id) / name)
    return candidates


def contract_project_path(chain_id: int, module: str = "DEX") -> Path:
    if chain_id == 31337:
        return ROOT / "contract" / "robinhood-testnet" / module
    chain_name = str(chain_registry_entry(chain_id).get("directory") or "").strip()
    if not chain_name:
        chain_name = str(chain_id)
    return ROOT / "contract" / chain_name / module


def manifest_path(chain_id: int, configured: str | os.PathLike[str] | None = None) -> Path:
    if configured:
        return Path(configured).expanduser().resolve()
    env_configured = os.getenv("FLASHLOAN_MANIFEST_PATH", "").strip()
    if env_configured:
        return Path(env_configured).expanduser().resolve()
    return MANIFEST_BY_CHAIN.get(
        int(chain_id), ROOT / "test-token_create" / "config" / f"chain-{chain_id}" / "manifest.json"
    )


def rpc_url(chain_id: int, configured: str | None = None) -> str:
    if configured:
        return configured
    env_configured = os.getenv("FLASHLOAN_RPC_URL", "").strip()
    if env_configured:
        return env_configured
    chain = _read_json(chain_json_path(chain_id), {}) or {}
    rpc = chain.get("rpc") or {}
    if isinstance(rpc, dict):
        value = str(rpc.get("primary") or "").strip()
        if value:
            return value
    for key in ("rpc_url", "officialRpc"):
        value = str(chain.get(key) or "").strip()
        if value:
            return value
    for key in ("rpc_urls", "officialRpcUrls"):
        values = chain.get(key) or []
        if isinstance(values, list) and values:
            return str(values[0])
    return ""


def rpc_urls(chain_id: int) -> list[str]:
    chain = _read_json(chain_json_path(chain_id), {}) or {}
    rpc = chain.get("rpc") or {}
    if isinstance(rpc, dict):
        primary = str(rpc.get("primary") or "").strip()
        fallbacks = rpc.get("fallbacks") or []
        values = [str(value).strip() for value in fallbacks if str(value).strip()]
        return list(dict.fromkeys([value for value in [primary, *values] if value]))
    values = chain.get("rpc_urls") or chain.get("officialRpcUrls") or []
    if isinstance(values, list):
        return list(dict.fromkeys([str(value).strip() for value in values if str(value).strip()]))
    value = rpc_url(chain_id)
    return [value] if value else []


def hardhat_network(chain_id: int, configured: str | None = None) -> str:
    if configured:
        return configured
    env_configured = os.getenv("FLASHLOAN_HARDHAT_NETWORK", "").strip()
    if env_configured:
        return env_configured
    chain = _read_json(chain_json_path(chain_id), {}) or {}
    tooling = chain.get("tooling") or {}
    tooling_network = str(tooling.get("hardhat_network") or "").strip()
    if tooling_network:
        return tooling_network
    return HARDHAT_NETWORK_BY_CHAIN.get(int(chain_id), str(chain_id))


def chain_config(
    chain_id: int,
    *,
    rpc_url_override: str | None = None,
    manifest_override: str | os.PathLike[str] | None = None,
    hardhat_network_override: str | None = None,
) -> dict[str, Any]:
    chain_id = int(chain_id)
    chain = _read_json(chain_json_path(chain_id), {}) or {}
    contracts = _read_json(contracts_json_path(chain_id), {}) or {}
    project_root = contract_project_path(chain_id, "DEX")
    display_name = str(chain.get("display_name") or chain.get("displayName") or f"Chain {chain_id}")
    network = str(chain.get("network") or chain_registry_entry(chain_id).get("network") or f"chain-{chain_id}")
    tooling = chain.get("tooling") or {}
    explorer = chain.get("explorer") or {}
    foundry_alias = str(tooling.get("foundry_alias") or "").strip()
    return {
        "chain_id": chain_id,
        "chain_key": f"chain:{chain_id}",
        "network": network,
        "display_name": display_name,
        "rpc_url": rpc_url(chain_id, rpc_url_override),
        "rpc_urls": rpc_urls(chain_id) or [rpc_url(chain_id, rpc_url_override)],
        "rpc_source": str(chain_json_path(chain_id)),
        "foundry_alias": foundry_alias or f"chain-{chain_id}",
        "explorer_url": str(explorer.get("url") or chain.get("explorer_url") or "").strip(),
        "manifest_path": manifest_path(chain_id, manifest_override),
        "chain_json": chain_json_path(chain_id),
        "contracts_json": contracts_json_path(chain_id),
        "contract_project": project_root,
        "hardhat_network": hardhat_network(chain_id, hardhat_network_override),
        "broadcast_enabled": bool(chain.get("broadcastEnabled", bool(contracts.get("broadcastEnabled", False)))),
        "raw_chain": chain,
        "raw_contracts": contracts,
    }


def set_chain_env(
    chain_id: int,
    *,
    rpc_url_override: str | None = None,
    manifest_override: str | os.PathLike[str] | None = None,
    hardhat_network_override: str | None = None,
) -> dict[str, str]:
    cfg = chain_config(
        chain_id,
        rpc_url_override=rpc_url_override,
        manifest_override=manifest_override,
        hardhat_network_override=hardhat_network_override,
    )
    env = os.environ.copy()
    env["FLASHLOAN_DEFAULT_CHAIN_ID"] = str(chain_id)
    env["FLASHLOAN_CHAIN_ID"] = str(chain_id)
    env["FLASHLOAN_CHAIN_KEY"] = cfg["chain_key"]
    env["FLASHLOAN_CHAIN_NETWORK"] = cfg["network"]
    env["FLASHLOAN_RPC_URL"] = cfg["rpc_url"]
    env["FLASHLOAN_HARDHAT_NETWORK"] = cfg["hardhat_network"]
    env["HARDHAT_NETWORK"] = cfg["hardhat_network"]
    env["FLASHLOAN_MANIFEST_PATH"] = str(cfg["manifest_path"])
    env["FLASHLOAN_CHAIN_JSON"] = str(cfg["chain_json"])
    env["FLASHLOAN_CONTRACTS_JSON"] = str(cfg["contracts_json"])
    env["FLASHLOAN_CONTRACT_PROJECT"] = str(cfg["contract_project"])
    if cfg["rpc_url"] and chain_id in {4663, 46630}:
        env[f"ROBINHOOD_{'MAINNET' if chain_id == 4663 else 'TESTNET'}_RPC_URL"] = cfg["rpc_url"]
    return env


def supported_chain_configs() -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for chain_id in known_chain_ids():
        try:
            result[chain_id] = chain_config(chain_id)
        except Exception:
            continue
    return result


__all__ = [
    "ROOT",
    "CHAIN_DATA_ROOT",
    "REGISTRY_PATH",
    "chain_config",
    "chain_json_path",
    "contract_project_path",
    "contracts_json_path",
    "hardhat_network",
    "known_chain_ids",
    "manifest_path",
    "registry",
    "rpc_url",
    "rpc_urls",
    "set_chain_env",
    "supported_chain_configs",
    "workspace_root",
]
