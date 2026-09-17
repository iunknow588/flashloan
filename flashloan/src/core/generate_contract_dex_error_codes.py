"""Generate ContractDexErrorCodes.sol from the CONTRACT.DEX registry.

Run from the repository root:

    python flashloan/src/core/generate_contract_dex_error_codes.py
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)
sys.path = [p for p in sys.path if os.path.normcase(p) != os.path.normcase(HERE)]

from pathlib import Path

from core.error_registry import (
    CONTRACT_DEX_FLOW_CODES,
    CONTRACT_DEX_NODE_CODES,
    CONTRACT_DEX_MODULE_OFFSET,
    contract_dex_error_codes,
)


CONTRACT_DEX_PREFIX = "CONTRACT.DEX."


def _sol_constant_name(key: str) -> str:
    suffix = key[len(CONTRACT_DEX_PREFIX):] if key.startswith(CONTRACT_DEX_PREFIX) else key
    return suffix.replace(".", "_")


def _sol_literal(value: int) -> str:
    return f"{value:,}".replace(",", "_")


def build_solidity_source() -> str:
    lines: list[str] = []
    lines.append("// SPDX-License-Identifier: MIT")
    lines.append("pragma solidity ^0.8.24;")
    lines.append("")
    lines.append("// Generated from flashloan/src/core/error_registry.py.")
    lines.append("// Do not edit by hand: regenerate with flashloan/src/core/generate_contract_dex_error_codes.py.")
    lines.append("")
    lines.append("library ContractDexErrorCodes {")
    lines.append(f"    uint256 internal constant MODULE_OFFSET = {_sol_literal(CONTRACT_DEX_MODULE_OFFSET)};")
    lines.append("")
    lines.append("    // flow id")
    for flow, flow_code in CONTRACT_DEX_FLOW_CODES.items():
        lines.append(f"    uint256 internal constant FLOW_{flow.upper()} = {_sol_literal(flow_code)};")
    lines.append("")
    lines.append("    // node id")
    for node, node_code in CONTRACT_DEX_NODE_CODES.items():
        lines.append(f"    uint256 internal constant NODE_{node.upper()} = {_sol_literal(node_code)};")
    lines.append("")
    lines.append("    // error codes")
    for key, code in contract_dex_error_codes().items():
        constant = _sol_constant_name(key)
        lines.append(f"    uint256 internal constant {constant} = {_sol_literal(code)};")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def default_output_paths() -> list[Path]:
    root = Path(__file__).resolve().parents[3]
    return [
        root / "contract" / "robinhood" / "DEX" / "src" / "ContractDexErrorCodes.sol",
        root / "contract" / "robinhood-testnet" / "DEX" / "src" / "ContractDexErrorCodes.sol",
    ]


def write_solidity_file(output_path: Path | None = None) -> Path:
    target = Path(output_path or default_output_paths()[0]).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_solidity_source(), encoding="utf-8")
    return target


def main() -> None:
    if len(sys.argv) > 1:
        paths = [Path(sys.argv[1]).resolve()]
    else:
        paths = default_output_paths()
    for path in paths:
        written = write_solidity_file(path)
        print(written)


if __name__ == "__main__":
    main()
