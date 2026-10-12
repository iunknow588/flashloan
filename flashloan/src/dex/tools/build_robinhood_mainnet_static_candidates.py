"""Build the bounded Robinhood mainnet static candidate pool cache.

The candidate cache is deliberately static and representative: per token pair
it keeps at most ``--max-per-pair`` pools while preserving the deepest pool,
the highest-priced and lowest-priced pools, and low-fee liquidity when it
exists. Historical pools without liquidity do not flood the page.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dex.intent_trade.chains.robinhood.dex_pool_candidates import (  # noqa: E402
    DEFAULT_ROBINHOOD_DEX_CANDIDATE_PATH,
    build_mainnet_static_candidate_payload,
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--index",
        default="",
        help="Optional V4 pool index path; default reads chain-owned v4_pool_index evidence.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_ROBINHOOD_DEX_CANDIDATE_PATH),
        help="Output static candidate cache path.",
    )
    parser.add_argument(
        "--max-per-pair",
        type=int,
        default=6,
        help="Maximum representative pools kept per token pair; 0 keeps every initialized V4 pool.",
    )
    parser.add_argument(
        "--rpc-url",
        default="",
        help="Optional Robinhood mainnet RPC URL used for live V4 state reads.",
    )
    args = parser.parse_args()

    index_path = Path(args.index or SRC_ROOT / "runtime" / "evidence" / "robinhood" / "v4_pool_index" / "latest.json")
    index_payload = _read_json(index_path)
    rows = [row for row in index_payload.get("pools") or [] if isinstance(row, dict)]
    if not rows:
        print(json.dumps({"ok": False, "reason": "v4_pool_index_empty", "path": str(index_path)}))
        return 1

    payload = build_mainnet_static_candidate_payload(
        index_payload,
        rows,
        max_candidates_per_pair=max(0, args.max_per_pair),
        rpc_url=args.rpc_url or None,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(output),
                "poolCount": len(payload["pools"]),
                "summary": payload.get("summary") or {},
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
