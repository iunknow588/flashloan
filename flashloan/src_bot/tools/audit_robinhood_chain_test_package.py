from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.robinhood_chain_test_audit import audit_robinhood_chain_test_plan


def _read_plan(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a Robinhood chain-test plan before fork/static-call work.")
    parser.add_argument(
        "--plan-json",
        type=Path,
        default=SRC_ROOT / "runtime" / "evidence" / "robinhood" / "chain_test" / "latest" / "plan.json",
    )
    args = parser.parse_args()

    audit = audit_robinhood_chain_test_plan(_read_plan(args.plan_json))
    print(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if audit["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
