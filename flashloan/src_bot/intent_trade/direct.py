"""Public compatibility module for the direct intent trade implementation.

The implementation lives in ``direct_impl`` so the public module stays small
and the package architecture guard can keep this boundary easy to inspect.
"""

import sys

from intent_trade import direct_impl as _implementation

sys.modules[__name__] = _implementation
