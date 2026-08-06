from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.sensitive_data import redact_sensitive_text


SRC_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
COW_CONTRACTS_DX_DIR = REPO_ROOT / "contract" / "contracts-dex"
COW_SUBMISSION_SCRIPT = COW_CONTRACTS_DX_DIR / "scripts" / "submit-cow-flashloan-order.js"
LIVE_COW_SUBMISSION_NETWORKS = {"ethereum", "avalanche", "bnb", "polygon", "base"}
COW_ORDER_SIGNER_ENV_NAMES = (
    "COW_ORDER_SIGNER_PRIVATE_KEY",
    "COW_FLASHLOAN_PROBE_PRIVATE_KEY",
    "LIQUIDATION_EXECUTION_PRIVATE_KEY",
    "LIQUIDATION_SELF_FUNDED_PRIVATE_KEY",
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def cow_order_submission_requested() -> bool:
    return _env_bool("COW_ORDER_SUBMISSION_ENABLED", False)


def cow_order_submission_adapter_available() -> bool:
    raw = os.getenv("COW_ORDER_SUBMISSION_ADAPTER_ENABLED", "").strip().lower()
    if raw:
        return raw in {"1", "true", "yes", "on"}
    return cow_order_submission_requested() and bool(shutil.which("node") or shutil.which("node.exe")) and COW_SUBMISSION_SCRIPT.exists()


def cow_order_submission_enabled() -> bool:
    return cow_order_submission_requested() and cow_order_submission_adapter_available()


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
    return {
        "requested": cow_order_submission_requested(),
        "adapter_available": cow_order_submission_adapter_available(),
        "enabled": cow_order_submission_enabled(),
        "signer_ready": signer["ready"],
        "signer_status": signer,
        "node": node,
        "script": str(COW_SUBMISSION_SCRIPT),
        "script_exists": COW_SUBMISSION_SCRIPT.exists(),
    }


def _json_from_output(stdout: str) -> dict[str, Any]:
    text = str(stdout or "").strip()
    if not text:
        return {}
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("submission helper did not return JSON")


def submit_cow_flashloan_order(
    *,
    quote_payload: dict[str, Any],
    opportunity: dict[str, Any],
    timeout_seconds: int | float | None = None,
) -> dict[str, Any]:
    readiness = submission_script_ready()
    base: dict[str, Any] = {
        "ok": False,
        "submitted": False,
        "status": "order_submission_disabled",
        "blocked_reason": "order_submission_disabled",
        "error": None,
        "network": quote_payload.get("cow_network"),
        "chain_id": quote_payload.get("cow_chain_id"),
        "owner": quote_payload.get("owner"),
        "order_id": None,
        "tx_hash": None,
        "quote_call": None,
        "posting_call": None,
        "submit_call": None,
        "submission": None,
        "readiness": readiness,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
    }
    if not readiness["enabled"]:
        if not readiness["requested"]:
            base["blocked_reason"] = "order_submission_disabled"
        elif not readiness["adapter_available"]:
            base["blocked_reason"] = "order_submission_adapter_unavailable"
            base["status"] = "adapter_unavailable"
        return base
    if not readiness["script_exists"]:
        base["blocked_reason"] = "cow_submission_script_missing"
        base["status"] = "adapter_unavailable"
        return base
    node = readiness["node"]
    if not node:
        base["blocked_reason"] = "node_runtime_missing"
        base["status"] = "adapter_unavailable"
        return base

    payload = {
        "quote_payload": quote_payload,
        "opportunity": opportunity,
        "requested_at": base["started_at"],
    }
    with tempfile.TemporaryDirectory(prefix="cow-order-submit-") as temp_dir:
        input_path = Path(temp_dir) / "submission-input.json"
        input_path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
        env = os.environ.copy()
        env["COW_SUBMISSION_INPUT_PATH"] = str(input_path)
        env["COW_SUBMISSION_MODE"] = "live"
        try:
            completed = subprocess.run(
                [node, str(COW_SUBMISSION_SCRIPT), str(input_path)],
                cwd=str(COW_CONTRACTS_DX_DIR),
                env=env,
                text=True,
                capture_output=True,
                timeout=max(1, int(timeout_seconds or int(os.getenv("COW_ORDER_SUBMISSION_TIMEOUT_SECONDS", "180")))),
            )
        except Exception as exc:
            message = redact_sensitive_text(exc)
            base.update(
                {
                    "status": "submission_failed",
                    "blocked_reason": "submission_failed",
                    "error": message,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return base

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    parsed: dict[str, Any] = {}
    if stdout.strip():
        try:
            parsed = _json_from_output(stdout)
        except Exception:
            parsed = {}

    if completed.returncode != 0:
        message = (stderr or stdout or f"cow submission failed with exit code {completed.returncode}").strip()
        base.update(
            {
                "status": "submission_failed",
                "blocked_reason": "submission_failed",
                "error": redact_sensitive_text(message[-4000:]),
                "quote_call": parsed.get("quoteCall") or parsed.get("quote_call"),
                "posting_call": parsed.get("postingCall") or parsed.get("posting_call"),
                "submit_call": parsed.get("submitCall") or parsed.get("submit_call"),
                "submission": parsed or None,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return base

    submission = parsed if parsed else {"raw_stdout": stdout.strip()}
    base.update(
        {
            "ok": bool(submission.get("ok", True)),
            "submitted": bool(submission.get("submitted") or submission.get("orderId")),
            "status": str(submission.get("status") or ("submitted_success" if submission.get("submitted") or submission.get("orderId") else "submission_failed")),
            "blocked_reason": submission.get("blockedReason") or submission.get("blocked_reason"),
            "error": submission.get("error"),
            "order_id": submission.get("orderId") or submission.get("order_id"),
            "tx_hash": submission.get("txHash") or submission.get("tx_hash"),
            "quote_call": submission.get("quoteCall") or submission.get("quote_call"),
            "posting_call": submission.get("postingCall") or submission.get("posting_call"),
            "submit_call": submission.get("submitCall") or submission.get("submit_call"),
            "submission": submission,
            "finished_at": submission.get("finishedAt") or datetime.now(timezone.utc).isoformat(),
        }
    )
    if not base["submitted"] and not base["blocked_reason"]:
        base["blocked_reason"] = "submission_returned_no_order_id"
    if base["submitted"] and base["status"] != "submitted_success":
        base["status"] = "submitted_success"
    return base
