"""Compatibility package for legacy ``intent_trade`` handler paths.

Runtime code lives under ``dex.intent_trade``; chain-flow configuration still
uses the historical top-level package name.  Extending ``__path__`` keeps
those handler paths importable without duplicating the implementation.
"""

from pathlib import Path

_LEGACY_PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "dex" / "intent_trade"
__path__ = [str(_LEGACY_PACKAGE_ROOT)]
