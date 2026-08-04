from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from execution.liquidation_evidence_audit import audit_attempt_failure_sample_pair, audit_sample_manifest


def _read_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit liquidation attempt/failure-sample and sample-library evidence.")
    parser.add_argument("--attempt-json", help="JSON file containing one liquidation execution attempt row.")
    parser.add_argument("--failure-sample-json", help="JSON file containing one liquidation failure sample row.")
    parser.add_argument("--manifest-json", help="Sample-library index.json to audit.")
    parser.add_argument("--allow-pending-failure-samples", action="store_true", help="Do not fail when failure sample labels are still pending.")
    parser.add_argument("--output", help="Optional output JSON report path.")
    args = parser.parse_args()

    reports: dict[str, dict] = {}
    if args.attempt_json or args.failure_sample_json:
        if not (args.attempt_json and args.failure_sample_json):
            raise SystemExit("--attempt-json and --failure-sample-json must be provided together")
        reports["attempt_failure_pair"] = audit_attempt_failure_sample_pair(
            _read_json(args.attempt_json),
            _read_json(args.failure_sample_json),
        )
    if args.manifest_json:
        reports["sample_manifest"] = audit_sample_manifest(
            _read_json(args.manifest_json),
            require_failure_replayable=not args.allow_pending_failure_samples,
        )
    if not reports:
        raise SystemExit("provide --manifest-json or both --attempt-json and --failure-sample-json")

    output = {
        "ok": all(item.get("ok") for item in reports.values()),
        "reports": reports,
    }
    text = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(f"{text}\n", encoding="utf-8")
    print(text)
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
