import sys
import os
from pathlib import Path

import pytest


SRC_ROOT = Path(__file__).resolve().parents[1]
FLASHLOAN_ROOT = Path(__file__).resolve().parents[2]
SPLIT_SRC_ROOT = FLASHLOAN_ROOT / "src"
if str(FLASHLOAN_ROOT) not in sys.path:
    sys.path.insert(0, str(FLASHLOAN_ROOT))
if str(SPLIT_SRC_ROOT) not in sys.path:
    sys.path.insert(1, str(SPLIT_SRC_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.append(str(SRC_ROOT))


@pytest.fixture(autouse=True)
def _clear_dex_auto_execute_env():
    key = "DEX_AUTO_EXECUTE_VERIFIED_OPPORTUNITIES"
    previous = os.environ.get(key)
    os.environ.pop(key, None)
    yield
    if previous is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = previous


@pytest.fixture(autouse=True)
def _clear_rust_closed_loop_flag():
    key = "DEX_CLOSED_LOOP_NATIVE_RUST"
    previous = os.environ.get(key)
    os.environ[key] = "0"
    yield
    if previous is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = previous


@pytest.fixture(autouse=True)
def _disable_background_dex_opportunity_threads():
    key = "DEX_OPPORTUNITY_SCANNER_BACKGROUND_ENABLED"
    previous = os.environ.get(key)
    os.environ[key] = "0"
    yield
    if previous is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = previous


@pytest.fixture(autouse=True)
def _disable_dex_rpc_rate_limit():
    key = "DEX_RPC_MIN_INTERVAL_SECONDS"
    previous = os.environ.get(key)
    os.environ[key] = "0"
    try:
        from dex.intent_trade.chains.robinhood_common.rpc_rate_limit import (
            reset_dex_rpc_rate_limit_for_tests,
        )

        reset_dex_rpc_rate_limit_for_tests()
        yield
    finally:
        reset_dex_rpc_rate_limit_for_tests()
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


@pytest.fixture(autouse=True)
def _stop_global_dex_opportunity_scanner():
    yield
    try:
        from dex.runtime.dex_opportunity_scanner import (
            _reset_dex_opportunity_scanner_for_tests,
        )

        _reset_dex_opportunity_scanner_for_tests()
    except Exception:
        pass
