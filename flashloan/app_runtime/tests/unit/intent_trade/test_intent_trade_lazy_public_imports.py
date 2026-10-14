"""The intent-trade package must stay light for read-only page handlers."""

from __future__ import annotations

import os
import subprocess
import sys


def test_robinhood_pool_view_import_does_not_load_web3():
    code = """
import sys
import dex.intent_trade.chains.robinhood.flash_loan_pool_views as views
assert "web3" not in sys.modules, "web3 must not be loaded for the pool view"
assert "requests" not in sys.modules, "requests must not be loaded for the pool view"
from dex.intent_trade import build_cow_intent_trade
assert callable(build_cow_intent_trade)
assert "web3" not in sys.modules, "web3 must not be loaded by lazy public names"
"""
    env = dict(os.environ)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
