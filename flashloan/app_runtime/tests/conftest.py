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
