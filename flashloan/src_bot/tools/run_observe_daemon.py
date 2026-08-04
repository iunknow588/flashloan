"""Start the liquidation daemon without enabling transaction submission."""

import os
import sys
from pathlib import Path


os.environ["LIQUIDATION_AUTO_EXECUTE"] = "false"
os.environ["LIQUIDATION_MANUAL_TEST_COMPLETED"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime import liquidation_daemon


raise SystemExit(liquidation_daemon.main())
