from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.sensitive_data import redact_sensitive_text
from cow_flashloan.submission_readiness import (
    COW_NODE_ADAPTER_DIR,
    COW_SUBMISSION_SCRIPT,
    cow_order_submission_adapter_available,
    cow_order_submission_enabled,
    cow_order_submission_sdk_install_hint,
    cow_order_submission_network_supported,
    cow_order_submission_requested,
    cow_order_submission_sdk_ready,
    cow_order_submission_sdk_status,
    cow_order_submission_signer_ready,
    cow_order_submission_signer_status,
    submission_script_ready,
)


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
    from web.control_panel_cow_pause import cow_submission_pause_guard_status

    pause_guard = cow_submission_pause_guard_status()
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
        "pause_guard": pause_guard,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
    }
    if pause_guard.get("paused"):
        base.update(
            {
                "status": "submission_paused",
                "blocked_reason": "cow_submission_paused",
                "error": pause_guard.get("pause_reason"),
            }
        )
        return base
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
    if not readiness.get("sdk_ready"):
        sdk_status = readiness.get("sdk_status") or {}
        base["blocked_reason"] = "cow_flashloan_sdk_install_required"
        base["status"] = "cow_flashloan_sdk_install_required"
        base["error"] = cow_order_submission_sdk_install_hint(sdk_status)
        return base
    if not cow_order_submission_network_supported(quote_payload.get("cow_network") or quote_payload.get("network")):
        base["blocked_reason"] = "order_submission_network_unsupported"
        base["status"] = "order_submission_network_unsupported"
        base["error"] = f"unsupported live CoW submission network: {quote_payload.get('cow_network') or quote_payload.get('network') or '-'}"
        return base
    if not readiness.get("signer_ready"):
        signer_status = readiness.get("signer_status") or {}
        base["blocked_reason"] = "order_submission_signer_not_ready"
        base["status"] = "order_submission_signer_not_ready"
        base["error"] = signer_status.get("reason") or "signer_private_key_missing"
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
                cwd=str(COW_NODE_ADAPTER_DIR),
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
