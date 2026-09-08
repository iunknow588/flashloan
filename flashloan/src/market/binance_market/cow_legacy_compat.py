"""Legacy CoW compatibility facade, backed by cow_legacy_compat_impl."""

from __future__ import annotations

import market.binance_market.cow_legacy_compat_impl as _impl

_MODULE_METADATA = {"__builtins__", "__cached__", "__doc__", "__file__", "__loader__", "__name__", "__package__", "__spec__"}

globals().update({name: value for name, value in vars(_impl).items() if name not in _MODULE_METADATA})
