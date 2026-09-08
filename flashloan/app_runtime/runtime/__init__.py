"""Compatibility package for runtime modules moved into split packages."""

from __future__ import annotations

from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
for _MOVED in (
    _SRC_ROOT / "bot" / "runtime",
    _SRC_ROOT / "dex" / "runtime",
    _SRC_ROOT / "market" / "runtime",
):
    if _MOVED.is_dir() and str(_MOVED) not in __path__:
        __path__.append(str(_MOVED))
