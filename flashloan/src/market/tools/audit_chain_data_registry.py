from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.chain_data_registry import CHAIN_DATA_ROOT, chain_data_audit


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit chain-owned data files and processing handlers.")
    parser.add_argument("--root", type=Path, default=CHAIN_DATA_ROOT)
    args = parser.parse_args()

    try:
        audit = chain_data_audit(root=args.root)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2, sort_keys=True))
        return 1

    print(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if audit["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
