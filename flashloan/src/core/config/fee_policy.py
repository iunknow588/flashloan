"""Shared fixed-fee ceiling for the candidate pool library.

The fee ceiling is one number, and it has to be the *same* number on every
side of the system:

* the Rust ``pool_manager`` compiles it into
  ``depth_config::MAX_CANDIDATE_FIXED_FEE`` and asserts it against
  ``flashloan/config/depth_policy.json`` at start-up;
* the Rust ``token_manager`` applies it when it decides whether a pool is
  liquidity qualified;
* the Rust ``rs-src`` evidence writer uses it to decide whether a pool is
  allowed into the library at all;
* the Python orchestrator applies it when it builds the candidate surface
  and when it queries the candidate-eligible pool scope.

This module is the Python reader of that shared file. It deliberately owns
no second copy of the value: it reads ``maxCandidateFixedFee`` from the
policy file, so there is no way for the Python and the Rust sides to drift
apart.

The ceiling was raised from 3000 (0.30%) to 30000 (3.00%) on 2026-11-13 so
that more pools can enter the observation scope. The dynamic-fee flag is a
separate concern and is excluded everywhere regardless of the ceiling.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


# Location of the shared depth policy, relative to the repository root.
DEPTH_POLICY_RELATIVE_PATH = Path("flashloan/config/depth_policy.json")

# Uniswap V4 encodes a dynamic-fee pool in the top bit of the fee field.
# A pool with this bit set is excluded from the library regardless of the
# numeric ceiling.
DYNAMIC_FEE_FLAG = 8388608

# Fallback used only when the shared policy file cannot be located. It
# matches the compiled Rust constant so a missing file degrades to the same
# behaviour instead of silently widening the scope.
FALLBACK_MAX_CANDIDATE_FIXED_FEE = 30000


def _resolve_depth_policy_path() -> Path:
    """Return the absolute path of the shared depth policy file."""
    override = os.environ.get("FLASHLOAN_DEPTH_POLICY_PATH")
    if override:
        return Path(override).expanduser().resolve()
    for parent in [Path(__file__).resolve(), *Path(__file__).resolve().parents]:
        candidate = parent / DEPTH_POLICY_RELATIVE_PATH
        if candidate.is_file():
            return candidate
        nested = parent / "config" / "depth_policy.json"
        if nested.is_file() and parent.name == "flashloan":
            return nested
    raise FileNotFoundError(
        "shared depth policy file not found: "
        f"{DEPTH_POLICY_RELATIVE_PATH} (set FLASHLOAN_DEPTH_POLICY_PATH to override)"
    )


def load_max_candidate_fixed_fee() -> int:
    """Read the fixed-fee ceiling from the shared depth policy."""
    path = _resolve_depth_policy_path()
    with path.open("r", encoding="utf-8") as handle:
        payload: Any = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"depth policy must be a JSON object: {path}")
    value = payload.get("maxCandidateFixedFee")
    if not isinstance(value, int):
        raise ValueError(f"depth policy is missing an integer maxCandidateFixedFee: {path}")
    if value <= 0:
        raise ValueError(f"depth policy maxCandidateFixedFee must be positive: {value}")
    return value


# The ceiling every Python-side fee gate uses.
MAX_CANDIDATE_FIXED_FEE = load_max_candidate_fixed_fee()


def is_dynamic_fee(fee: int | None) -> bool:
    """Return whether the fee field encodes a dynamic-fee pool."""
    return bool(fee is not None and fee & DYNAMIC_FEE_FLAG)


def fee_is_library_eligible(fee: int | None) -> bool:
    """Return whether a pool with this fee may enter the pool library.

    A pool qualifies when its fee is a positive fixed fee at or below the
    ceiling. A dynamic-fee pool never qualifies, whatever its numeric
    value is.
    """
    if fee is None:
        return False
    fee = int(fee)
    if fee <= 0 or fee > MAX_CANDIDATE_FIXED_FEE:
        return False
    return not is_dynamic_fee(fee)
