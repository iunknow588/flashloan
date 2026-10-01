"""Generate Foundry RPC/etherscan fragments from flashloan/src/chains chain.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHAINS_ROOT = ROOT / "flashloan" / "src" / "chains"
OUTPUT = ROOT / "runtime" / "external" / "rh-flash-toolkit" / "generated" / "foundry.chain.toml"


def _read_json(path: Path, fallback=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _toml_str(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def chain_directories(registry: dict) -> dict[int, tuple[str, dict]]:
    result: dict[int, tuple[str, dict]] = {}
    chains = registry.get("chains") or {}
    chain_root = (ROOT / "flashloan" / "src" / "chains").resolve()
    for raw_chain_id, entry in chains.items():
        if not isinstance(entry, dict):
            continue
        directory = str(entry.get("directory") or "").strip()
        chain_file = (CHAINS_ROOT / directory / "chain.json").resolve()
        if not chain_file.exists() or chain_root not in chain_file.parents:
            continue
        config = _read_json(chain_file, {})
        if isinstance(config, dict):
            result[int(raw_chain_id)] = (directory, config)
    return result


def render_entries(chains: dict[int, tuple[str, dict]]) -> str:
    lines = ["# GENERATED FILE. DO NOT EDIT."]
    lines.append("# Source: flashloan/src/chains/{chain}/chain.json")
    lines.append("")
    rpc_lines: list[str] = []
    etherscan_lines: list[str] = []
    for chain_id in sorted(chains):
        _, config = chains[chain_id]
        tooling = config.get("tooling") or {}
        alias = str(tooling.get("foundry_alias") or "").strip()
        rpc = config.get("rpc") or {}
        primary = str(rpc.get("primary") or "").strip()
        explorer = config.get("explorer") or {}
        explorer_url = str(explorer.get("url") or "").strip()
        if not alias or not primary:
            continue
        rpc_lines.append(f"{alias} = {_toml_str(primary)}")
        if explorer_url:
            etherscan_lines.append(
                f'{alias} = {{ key = "dummy", url = {_toml_str(explorer_url + "/api")} }}'
            )
    if rpc_lines:
        lines.append("[rpc_endpoints]")
        lines.extend(rpc_lines)
        lines.append("")
    if etherscan_lines:
        lines.append("[etherscan]")
        lines.extend(etherscan_lines)
        lines.append("")
    return "\n".join(lines)


def generate(chain_ids: list[int], output: Path) -> None:
    registry = _read_json(CHAINS_ROOT / "registry.json", {}) or {}
    chains = chain_directories(registry)
    if chain_ids:
        chains = {chain_id: chains[chain_id] for chain_id in chain_ids if chain_id in chains}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_entries(chains), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chain", type=int, action="append", help="Chain IDs to include; repeat or omit for all")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="Generated TOML fragment path")
    parser.add_argument("--check", action="store_true", help="Only verify the existing generated file is current")
    args = parser.parse_args()

    registry = _read_json(CHAINS_ROOT / "registry.json", {}) or {}
    chains = chain_directories(registry)
    if args.chain:
        chains = {chain_id: chains[chain_id] for chain_id in args.chain if chain_id in chains}
    rendered = render_entries(chains)
    target = args.output.expanduser().resolve()
    if args.check:
        stale = not target.exists() or target.read_text(encoding="utf-8") != rendered
        if stale:
            raise SystemExit(f"generated foundry config is stale: {target}")
        print(f"OK: {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")
    print(f"wrote {target}")


if __name__ == "__main__":
    main()

