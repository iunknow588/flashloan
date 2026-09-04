import sys
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1]
FLASHLOAN_ROOT = Path(__file__).resolve().parents[2]
if str(FLASHLOAN_ROOT) not in sys.path:
    sys.path.insert(0, str(FLASHLOAN_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.append(str(SRC_ROOT))
