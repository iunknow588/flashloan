import json
import os
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SRC_ROOT.parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files


load_env_files(__file__)


def present_env(name: str) -> bool:
    value = os.getenv(name, "").strip()
    return bool(value and value != "0x...")


def file_exists(path: Path) -> bool:
    return path.exists() and path.is_file()


def check_item(name: str, ok: bool, manual: bool = False, blocking: bool = True, detail: str = "") -> dict:
    return {
        "name": name,
        "ok": bool(ok),
        "manual": bool(manual),
        "blocking": bool(blocking),
        "detail": detail,
    }


def build_checks() -> list[dict]:
    signal_file = SRC_ROOT / "runtime" / "state" / "latest_executable_signal.json"
    aave_cache = SRC_ROOT / "runtime" / "cache" / "aave_reserve_assets.json"
    trade_log = PROJECT_ROOT / "contracts" / "deployments" / "fuji-trades.jsonl"
    return [
        check_item("DATABASE_URL configured", present_env("DATABASE_URL")),
        check_item("FUJI_RPC_URL configured", present_env("FUJI_RPC_URL")),
        check_item("DEPLOYER_PRIVATE_KEY configured", present_env("DEPLOYER_PRIVATE_KEY"), manual=True),
        check_item("AAVE_POOL_ADDRESS configured", present_env("AAVE_POOL_ADDRESS")),
        check_item(
            "ONCHAIN_DYNAMIC_AAVE_EXECUTOR_ADDRESS configured",
            present_env("ONCHAIN_DYNAMIC_AAVE_EXECUTOR_ADDRESS"),
            manual=True,
        ),
        check_item(
            "DEX router configured",
            present_env("DYNAMIC_DEX_ROUTER") or present_env("FUJI_DEX_ROUTER"),
        ),
        check_item("USDC configured", present_env("DYNAMIC_USDC") or present_env("FUJI_USDC")),
        check_item("latest executable signal file exists", file_exists(signal_file), detail=str(signal_file)),
        check_item("Aave reserve cache exists", file_exists(aave_cache), detail=str(aave_cache)),
        check_item("Fuji trade log exists", file_exists(trade_log), blocking=False, detail=str(trade_log)),
        check_item("manual review: token address mapping confirmed", False, manual=True),
        check_item("manual review: small-test wallet balance confirmed", False, manual=True),
    ]


def summarize_checks(checks: list[dict]) -> dict:
    required = [item for item in checks if not item["manual"] and item.get("blocking", True)]
    nonblocking = [item for item in checks if not item["manual"] and not item.get("blocking", True)]
    manual = [item for item in checks if item["manual"]]
    missing_required = [item for item in required if not item["ok"]]
    missing_nonblocking = [item for item in nonblocking if not item["ok"]]
    pending_manual = [item for item in manual if not item["ok"]]
    return {
        "required_count": len(required),
        "missing_required_count": len(missing_required),
        "manual_count": len(manual),
        "nonblocking_count": len(nonblocking),
        "missing_nonblocking_count": len(missing_nonblocking),
        "pending_manual_count": len(pending_manual),
        "ready_for_static_simulation": not missing_required,
        "missing_required": missing_required,
        "missing_nonblocking": missing_nonblocking,
        "pending_manual": pending_manual,
        "checks": checks,
    }


def main() -> int:
    summary = summarize_checks(build_checks())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ready_for_static_simulation"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
