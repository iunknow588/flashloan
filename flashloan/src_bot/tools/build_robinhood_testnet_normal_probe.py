from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from intent_trade.chains.robinhood_testnet.normal_flash_probe import resolve_robinhood_testnet_normal_flash_probe


REPO_ROOT = SRC_ROOT.parents[1]


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Robinhood Testnet normal flash probe evidence from internal chain data.")
    parser.add_argument(
        "--chain-json",
        type=Path,
        default=REPO_ROOT / "contract" / "robinhood" / "testnet" / "chain.json",
    )
    parser.add_argument(
        "--contracts-json",
        type=Path,
        default=REPO_ROOT / "contract" / "robinhood" / "testnet" / "contracts.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=SRC_ROOT / "runtime" / "evidence" / "robinhood-testnet" / "normal_testnet_probe",
    )
    args = parser.parse_args()

    try:
        chain_metadata = _read_json(args.chain_json)
        contracts_metadata = _read_json(args.contracts_json)
        report = resolve_robinhood_testnet_normal_flash_probe(
            chain_metadata=chain_metadata,
            contracts_metadata=contracts_metadata,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    evidence_dir = args.output_dir / _stamp()
    _write_json(evidence_dir / "report.json", report)
    latest_payload = report | {"reportPath": str(evidence_dir / "report.json")}
    _write_json(args.output_dir / "latest.json", latest_payload)
    print(json.dumps(latest_payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("measurementReady") else 1


if __name__ == "__main__":
    raise SystemExit(main())
