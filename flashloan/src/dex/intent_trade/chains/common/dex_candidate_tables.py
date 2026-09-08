"""DEX candidate table builders facade, backed by dex_candidate_tables_impl."""

from __future__ import annotations

import dex.intent_trade.chains.common.dex_candidate_tables_impl as _impl

_MODULE_METADATA = {"__builtins__", "__cached__", "__doc__", "__file__", "__loader__", "__name__", "__package__", "__spec__"}

globals().update({name: value for name, value in vars(_impl).items() if name not in _MODULE_METADATA})
