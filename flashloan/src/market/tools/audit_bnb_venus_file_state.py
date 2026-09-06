from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from web3 import Web3


SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from bot.execution.venus_accounts import (
    dedupe_venus_events,
    load_venus_event_journal,
    validate_venus_borrower_registry,
)
from bot.execution.venus_markets import DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR


DEFAULT_AUDIT_JSON = DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR / "venus_file_state_audit.json"
DEFAULT_AUDIT_MD = DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR / "venus_file_state_audit.md"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _load_accounts(path: Path) -> list[str]:
    if not path.exists():
        return []
    result: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value:
            continue
        try:
            account = Web3.to_checksum_address(value)
        except Exception:
            continue
        if account not in result:
            result.append(account)
    return result


def audit_file_state(output_dir: Path) -> dict[str, Any]:
    registry_path = output_dir / "venus_borrower_registry.json"
    accounts_path = output_dir / "venus_borrower_accounts.txt"
    progress_path = output_dir / "venus_two_month_market_scan_progress.json"
    registry = _load_json(registry_path)
    validation = validate_venus_borrower_registry(registry)
    account_only_events = str(registry.get("event_storage_mode") or "") == "account_only"
    errors = list(validation.get("errors") or [])
    warnings = list(validation.get("warnings") or [])

    registry_accounts = []
    for row in registry.get("accounts") or []:
        if isinstance(row, dict) and row.get("account"):
            try:
                registry_accounts.append(Web3.to_checksum_address(str(row["account"])))
            except Exception:
                warnings.append("invalid_registry_account")
    registry_accounts = sorted(set(registry_accounts))
    file_accounts = sorted(set(_load_accounts(accounts_path)))
    if registry_accounts != file_accounts:
        errors.append("registry_accounts_file_mismatch")

    events_path = Path(str(registry.get("events_jsonl") or output_dir / "venus_borrower_events_2mo.jsonl"))
    if not events_path.is_absolute():
        events_path = Path.cwd() / events_path
        if not events_path.exists():
            events_path = output_dir / Path(str(registry.get("events_jsonl") or "venus_borrower_events_2mo.jsonl")).name
    events = dedupe_venus_events(load_venus_event_journal(events_path))
    event_accounts = sorted(
        {
            Web3.to_checksum_address(str(row.get("account")))
            for row in events
            if row.get("account")
        }
    )
    if not account_only_events and event_accounts != registry_accounts:
        errors.append("event_accounts_registry_mismatch")
    declared_event_count = registry.get("event_count")
    if not account_only_events and declared_event_count is not None and int(declared_event_count or 0) != len(events):
        errors.append("event_count_mismatch")

    progress = _load_json(progress_path)
    market_count = int(progress.get("market_count") or 0)
    selected_market_count = int(progress.get("selected_market_count") or 0)
    if market_count and selected_market_count < market_count:
        warnings.append("market_scan_incomplete")
    if int(progress.get("recent_blocks") or 0) > 0:
        warnings.append("recent_blocks_window_only")
    if not registry_path.exists():
        errors.append("registry_file_missing")
    if not accounts_path.exists():
        errors.append("accounts_file_missing")
    if not account_only_events and not events_path.exists():
        errors.append("events_file_missing")

    status = "error" if errors else "warning" if warnings else "ok"
    return {
        "status": status,
        "ok": not errors,
        "storage_mode": registry.get("storage_mode") or "unknown",
        "registry_validation": validation,
        "registry": {
            "path": str(registry_path),
            "account_count": len(registry_accounts),
            "declared_account_count": int(registry.get("account_count") or 0),
            "scan_complete": bool(registry.get("scan_complete", True)),
            "from_block": registry.get("from_block"),
            "to_block": registry.get("to_block"),
            "discovery_mode": registry.get("discovery_mode"),
            "event_storage_mode": registry.get("event_storage_mode"),
        },
        "events": {
            "path": str(events_path),
            "event_count": len(events),
            "account_count": len(event_accounts),
        },
        "market_progress": {
            "path": str(progress_path),
            "status": progress.get("status"),
            "market_count": market_count,
            "selected_market_count": selected_market_count,
            "lookback_from_block": progress.get("lookback_from_block"),
            "from_block": progress.get("from_block"),
            "to_block": progress.get("to_block"),
            "recent_blocks": int(progress.get("recent_blocks") or 0),
        },
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
    }


def _write_audit_markdown(path: Path, result: dict[str, Any]) -> None:
    registry = result.get("registry") or {}
    events = result.get("events") or {}
    market_progress = result.get("market_progress") or {}
    lines = [
        "# BNB Venus File State Audit",
        "",
        f"- Status: `{result.get('status')}`",
        f"- OK: `{result.get('ok')}`",
        f"- Storage mode: `{result.get('storage_mode')}`",
        f"- Registry accounts: `{registry.get('account_count', 0)}`",
        f"- Registry scan complete: `{registry.get('scan_complete')}`",
        f"- Discovery mode: `{registry.get('discovery_mode') or 'unknown'}`",
        f"- Event count: `{events.get('event_count', 0)}`",
        f"- Event account count: `{events.get('account_count', 0)}`",
        f"- Market count: `{market_progress.get('market_count', 0)}`",
        f"- Selected market count: `{market_progress.get('selected_market_count', 0)}`",
        f"- Recent blocks: `{market_progress.get('recent_blocks', 0)}`",
    ]
    if result.get("errors"):
        lines.extend(["", "## Errors"])
        lines.extend(f"- `{item}`" for item in result.get("errors") or [])
    if result.get("warnings"):
        lines.extend(["", "## Warnings"])
        lines.extend(f"- `{item}`" for item in result.get("warnings") or [])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_audit_report(output_dir: Path, result: dict[str, Any], *, json_path: Path | None = None, markdown_path: Path | None = None) -> dict[str, str]:
    json_target = json_path or (output_dir / DEFAULT_AUDIT_JSON.name)
    markdown_target = markdown_path or (output_dir / DEFAULT_AUDIT_MD.name)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_audit_markdown(markdown_target, result)
    return {"json": str(json_target), "markdown": str(markdown_target)}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit BNB Venus file-only discovery and health-scan state.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_BNB_LIQUIDATION_OUTPUT_DIR)
    parser.add_argument("--fail-on-warning", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_AUDIT_JSON)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_AUDIT_MD)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    result = audit_file_state(args.output_dir)
    paths = write_audit_report(args.output_dir, result, json_path=args.output, markdown_path=args.markdown_output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"ok": not result["errors"], "paths": paths}, ensure_ascii=False), file=sys.stderr)
    if result["errors"]:
        return 2
    if args.fail_on_warning and result["warnings"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
