from __future__ import annotations

import os
from pathlib import Path
from typing import Literal


ModuleKind = Literal["BOT", "DEX"]

CHAIN_PROJECTS: dict[int, tuple[str, frozenset[ModuleKind]]] = {
    43114: ("avalanche", frozenset({"BOT", "DEX"})),
    43113: ("avalanche-testnet", frozenset({"BOT", "DEX"})),
    4663: ("robinhood", frozenset({"DEX"})),
    46630: ("robinhood-testnet", frozenset({"DEX"})),
}

LEGACY_PROJECT_NAMES = frozenset({"contracts-bot", "contracts-dex"})


def workspace_root(start: Path | None = None) -> Path:
    current = (start or Path(__file__)).resolve()
    for parent in (current, *current.parents):
        if (parent / "contract").is_dir() and (parent / "flashloan").is_dir():
            return parent
    raise RuntimeError(f"unable to locate workspace root from {current}")


def resolve_contract_project(
    chain_id: int,
    module: ModuleKind,
    *,
    root: Path | None = None,
) -> Path:
    try:
        normalized_chain_id = int(chain_id)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unsupported contract chain id: {chain_id!r}") from exc
    normalized_module = str(module or "").strip().upper()
    if normalized_module not in {"BOT", "DEX"}:
        raise ValueError(f"unsupported contract module: {module!r}")
    chain_entry = CHAIN_PROJECTS.get(normalized_chain_id)
    if chain_entry is None:
        raise ValueError(f"unsupported contract chain id: {normalized_chain_id}")
    chain_name, modules = chain_entry
    if normalized_module not in modules:
        raise ValueError(f"module {normalized_module} is not configured for chain {normalized_chain_id}")
    project_root = (root or workspace_root())
    project = project_root / "contract" / chain_name / normalized_module
    resolved = project.resolve()
    contract_root = (project_root / "contract" / chain_name).resolve()
    if resolved.parent != contract_root or resolved.name in LEGACY_PROJECT_NAMES:
        raise ValueError(f"contract project escaped chain scope: {resolved}")
    return resolved


def resolve_contract_project_from_env(
    chain_id: int,
    module: ModuleKind,
    *,
    env_name: str,
    root: Path | None = None,
) -> Path:
    configured = os.getenv(env_name, "").strip()
    if not configured:
        return resolve_contract_project(chain_id, module, root=root)
    path = Path(configured).expanduser().resolve()
    if path.name in LEGACY_PROJECT_NAMES or any(part in LEGACY_PROJECT_NAMES for part in path.parts):
        raise ValueError(f"legacy contract project path is forbidden: {path}")
    try:
        normalized_chain_id = int(chain_id)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unsupported contract chain id: {chain_id!r}") from exc
    chain_entry = CHAIN_PROJECTS.get(normalized_chain_id)
    if chain_entry is None:
        raise ValueError(f"unsupported contract chain id: {normalized_chain_id}")
    chain_name, modules = chain_entry
    normalized_module = str(module or "").strip().upper()
    if normalized_module not in modules:
        raise ValueError(f"module {normalized_module} is not configured for chain {normalized_chain_id}")
    expected_suffix = Path("contract") / chain_name / normalized_module
    if path.name != normalized_module or path.parent.name != chain_name or path.parent.parent.name != "contract":
        raise ValueError(f"configured contract project must match {expected_suffix}, got {path}")
    return path


__all__ = [
    "CHAIN_PROJECTS",
    "LEGACY_PROJECT_NAMES",
    "ModuleKind",
    "resolve_contract_project",
    "resolve_contract_project_from_env",
    "workspace_root",
]
