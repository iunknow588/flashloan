from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any


SRC_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
COW_CONTRACTS_DX_DIR = REPO_ROOT / "contract" / "contracts-dex"
COW_SUBMISSION_SCRIPT = COW_CONTRACTS_DX_DIR / "scripts" / "submit-cow-flashloan-order.js"
LIVE_COW_SUBMISSION_NETWORKS = {"ethereum", "avalanche", "bnb", "polygon", "base"}
REQUIRED_COW_SUBMISSION_PACKAGES = (
    "@cowprotocol/sdk-common",
    "@cowprotocol/sdk-viem-adapter",
    "@cowprotocol/sdk-config",
    "@cowprotocol/sdk-order-book",
    "@cowprotocol/sdk-trading",
    "@cowprotocol/sdk-flash-loans",
    "viem",
)
COW_ORDER_SIGNER_ENV_NAMES = (
    "COW_ORDER_SIGNER_PRIVATE_KEY",
    "COW_FLASHLOAN_PROBE_PRIVATE_KEY",
    "LIQUIDATION_EXECUTION_PRIVATE_KEY",
    "LIQUIDATION_SELF_FUNDED_PRIVATE_KEY",
)


def cow_order_submission_requested() -> bool:
    try:
        from web.control_panel_cow_pause import cow_submission_pause_guard_status

        guard = cow_submission_pause_guard_status()
        source = str(guard.get("source") or "")
        if bool(guard.get("database_configured")) or source in {
            "database",
            "database_migrated_from_file",
            "database_initialized",
            "file_fallback",
            "file",
        }:
            requested = guard.get("order_submission_enabled")
            if requested is None:
                requested = not bool(guard.get("paused"))
            return bool(requested) and not bool(guard.get("paused"))
    except Exception:
        pass
    raw_requested = os.getenv("COW_ORDER_SUBMISSION_ENABLED")
    if raw_requested is not None and str(raw_requested).strip() != "":
        return str(raw_requested).strip().lower() in {"1", "true", "yes", "on"}
    return False


def cow_order_submission_adapter_available() -> bool:
    raw = os.getenv("COW_ORDER_SUBMISSION_ADAPTER_ENABLED", "").strip().lower()
    if raw:
        return raw in {"1", "true", "yes", "on"}
    return bool(shutil.which("node") or shutil.which("node.exe")) and COW_SUBMISSION_SCRIPT.exists()


def _node_module_package_path(package_name: str) -> Path:
    path = COW_CONTRACTS_DX_DIR / "node_modules"
    for part in str(package_name or "").split("/"):
        if part:
            path /= part
    return path


def cow_order_submission_sdk_status() -> dict[str, Any]:
    package_json = COW_CONTRACTS_DX_DIR / "package.json"
    package_lock = COW_CONTRACTS_DX_DIR / "package-lock.json"
    node_modules = COW_CONTRACTS_DX_DIR / "node_modules"
    installed = [
        package
        for package in REQUIRED_COW_SUBMISSION_PACKAGES
        if _node_module_package_path(package).exists()
    ]
    missing = [
        package
        for package in REQUIRED_COW_SUBMISSION_PACKAGES
        if package not in installed
    ]
    ready = package_json.exists() and node_modules.exists() and not missing
    return {
        "ready": ready,
        "reason": "cow_flashloan_sdk_ready" if ready else "cow_flashloan_sdk_install_required",
        "contracts_dir": str(COW_CONTRACTS_DX_DIR),
        "package_json_exists": package_json.exists(),
        "package_lock_exists": package_lock.exists(),
        "node_modules_exists": node_modules.exists(),
        "required_packages": list(REQUIRED_COW_SUBMISSION_PACKAGES),
        "installed_packages": installed,
        "missing_packages": missing,
        "install_command": f"cd {COW_CONTRACTS_DX_DIR} && npm install",
    }


def cow_order_submission_sdk_ready() -> bool:
    return bool(cow_order_submission_sdk_status()["ready"])


def cow_order_submission_enabled() -> bool:
    return (
        cow_order_submission_requested()
        and cow_order_submission_adapter_available()
        and cow_order_submission_sdk_ready()
    )


def cow_order_submission_network_supported(network: str | None) -> bool:
    return str(network or "").strip().lower() in LIVE_COW_SUBMISSION_NETWORKS


def _normalized_private_key(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw or raw == "0x...":
        return ""
    key = raw if raw.startswith("0x") else f"0x{raw}"
    if len(key) != 66 or not key.startswith("0x"):
        return ""
    try:
        int(key[2:], 16)
    except ValueError:
        return ""
    return key


def cow_order_submission_signer_status() -> dict[str, Any]:
    invalid_sources: list[str] = []
    for name in COW_ORDER_SIGNER_ENV_NAMES:
        raw = os.getenv(name, "")
        if not str(raw or "").strip():
            continue
        if _normalized_private_key(raw):
            return {
                "ready": True,
                "source": name,
                "reason": "signer_private_key_configured",
                "invalid_sources": invalid_sources,
            }
        invalid_sources.append(name)
    reason = "signer_private_key_invalid" if invalid_sources else "signer_private_key_missing"
    return {
        "ready": False,
        "source": None,
        "reason": reason,
        "invalid_sources": invalid_sources,
    }


def cow_order_submission_signer_ready() -> bool:
    return bool(cow_order_submission_signer_status()["ready"])


def submission_script_ready() -> dict[str, Any]:
    node = shutil.which("node") or shutil.which("node.exe")
    signer = cow_order_submission_signer_status()
    sdk_status = cow_order_submission_sdk_status()
    return {
        "requested": cow_order_submission_requested(),
        "adapter_available": cow_order_submission_adapter_available(),
        "enabled": cow_order_submission_enabled(),
        "sdk_ready": sdk_status["ready"],
        "sdk_status": sdk_status,
        "signer_ready": signer["ready"],
        "signer_status": signer,
        "node": node,
        "script": str(COW_SUBMISSION_SCRIPT),
        "script_exists": COW_SUBMISSION_SCRIPT.exists(),
    }
