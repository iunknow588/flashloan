"""Liquidation runtime implementations owned by the bot domain.

The runtime compatibility namespace is shared by the split bot, DEX and
market runtime packages while legacy imports are being retired.
"""

from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[2]
for _MOVED in (
    _SRC_ROOT / "bot" / "runtime",
    _SRC_ROOT / "dex" / "runtime",
    _SRC_ROOT / "market" / "runtime",
):
    if _MOVED.is_dir() and str(_MOVED) not in __path__:
        __path__.append(str(_MOVED))
