"""CLI entry point for the BNB niche-token layered stable lookup table builder."""

from __future__ import annotations

import sys
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dex.intent_trade.chains.bnb.niche_static_table import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
