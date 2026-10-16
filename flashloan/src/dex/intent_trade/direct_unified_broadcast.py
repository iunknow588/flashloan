"""Rust-first broadcast and receipt handling for the unified direct execution path."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from bot.execution.gas_estimator import build_gas_params, estimate_gas_price
from core.rs_bridge import (
    get_v4_pending_nonce,
    load_v4_receipt,
    resolve_private_key_hex,
    rust_available,
    sign_typed_transaction_json,
    submit_v4_route,
)


def _rust_jsonable(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex()
    if isinstance(value, dict):
        return {str(key): _rust_jsonable(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [_rust_jsonable(item) for item in value]
    return value


def _rust_transaction_json(transaction: dict[str, Any]) -> str:
    return json.dumps(
        {key: _rust_jsonable(value) for key, value in transaction.items() if value is not None},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _public_rpc_url(context: dict[str, Any]) -> str:
    provider = getattr(context.get("w3"), "provider", None)
    url = str(getattr(provider, "endpoint_uri", "") or "").strip()
    if url:
        return url
    from dex.intent_trade import direct_impl as impl

    try:
        return str(impl._direct_rpc_url(str(context.get("network") or ""))[0] or "").strip()
    except Exception:
        return ""


def broadcast_unified_direct_trade(context: dict[str, Any]) -> dict[str, Any]:
    from dex.intent_trade import direct_impl as impl

    if not rust_available():
        from dex.intent_trade.direct_unified_broadcast_py_fallback import (
            broadcast_unified_direct_trade_py_fallback,
        )

        return broadcast_unified_direct_trade_py_fallback(context)

    executor = context["executor"]
    signer_address = context["signer_address"]
    signer_key = context["signer_key"]
    chain_id = int(context["chain_id"])
    network = str(context["network"])
    onchain_owner = context["onchain_owner"]
    executor_address = context["executor_address"]
    preview_report = context["preview_report"]
    request_payload = context["request_payload"]
    selected_group = context["selected_group"]
    runtime_trade_args = context["runtime_trade_args"]
    usdc_params_arg = context["usdc_params_arg"]
    token_params_arg = context["token_params_arg"]
    broadcast_enabled = bool(context["broadcast_enabled"])
    execute_runtime_trade = bool(context["execute_runtime_trade"])
    gas_estimate_info = context.get("gas_estimate_info")
    current_block = int(context["current_block"])
    timeout_seconds = context.get("timeout_seconds")
    allow_non_usdc_cross_pool = bool(context["allow_non_usdc_cross_pool"])

    if not execute_runtime_trade:
        return {
            "ok": True,
            "submitted": False,
            "status": "preview_passed",
            "blocked_reason": "execution_disabled",
            "error": None,
            "network": network,
            "chain_id": chain_id,
            "owner": onchain_owner,
            "signer": signer_address,
            "executor_address": executor_address,
            "preflight": preview_report,
            "request": request_payload,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

    tx_builder = executor.functions.runOrderedRuntimeTradesAndExecuteAuto(
        runtime_trade_args,
        usdc_params_arg,
        token_params_arg,
        allow_non_usdc_cross_pool,
    )
    static_return = tx_builder.call({"from": signer_address})
    gas_estimate = tx_builder.estimate_gas({"from": signer_address})
    static_report = {
        "ok": True,
        "gasEstimate": str(gas_estimate),
        "runResult": impl._runtime_run_result_report(static_return),
        "executionPreview": preview_report["executionPreview"],
    }

    def _finish(**fields: Any) -> dict[str, Any]:
        payload = {
            "network": network,
            "chain_id": chain_id,
            "owner": onchain_owner,
            "signer": signer_address,
            "executor_address": executor_address,
            "preflight": preview_report,
            "static_call": static_report,
            "strategy_status": preview_report["strategyStatus"],
            "route_direction": preview_report["executionPreview"]["routeDirection"],
            "request": request_payload,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        payload.update(fields)
        return impl._with_direct_circuit_record(
            payload,
            network=network,
            route_key=selected_group["routeKey"],
        )

    if not broadcast_enabled:
        return _finish(
            ok=True,
            submitted=False,
            status="static_call_passed",
            blocked_reason="broadcast_disabled",
            error=None,
        )

    if gas_estimate_info is None:
        gas_estimate_info = estimate_gas_price(
            context["w3"],
            max_gas_price_gwei=(
                impl._max_broadcast_gas_price_wei() / 1_000_000_000
                if impl._max_broadcast_gas_price_wei()
                else None
            ),
        )
    gas_guard = impl._broadcast_gas_guard(gas_estimate, gas_estimate_info)
    static_report["gasPricing"] = gas_guard["report"]
    if not gas_guard["ok"]:
        return _finish(
            ok=False,
            submitted=False,
            status=gas_guard["status"],
            blocked_reason=gas_guard["blocked_reason"],
            error=gas_guard["blocked_reason"],
        )

    rpc_url = _public_rpc_url(context)
    if not rpc_url:
        return _finish(
            ok=False,
            submitted=False,
            status="rpc_url_unavailable",
            blocked_reason="rpc_url_unavailable",
            error="Rust broadcast requires a public RPC URL",
        )

    tx_params = {
        "from": signer_address,
        "nonce": int(
            get_v4_pending_nonce(
                rpc_url,
                signer_address,
                timeout_seconds=max(1, int(timeout_seconds or 20)),
            )["nonce"]
        ),
        "chainId": chain_id,
        "gas": gas_estimate,
        **build_gas_params(gas_estimate_info),
    }
    built_tx = tx_builder.build_transaction(tx_params)
    resolved_signer = resolve_private_key_hex(signer_key)
    if str(resolved_signer.get("address") or "").lower() != str(signer_address or "").lower():
        raise RuntimeError("signer private key resolves to a different address than signer_address")
    signed_tx = sign_typed_transaction_json(_rust_transaction_json(built_tx), signer_key)
    raw_signed_tx = str(signed_tx.get("raw") or "")
    if not raw_signed_tx:
        return _finish(
            ok=False,
            submitted=False,
            status="signing_failed",
            blocked_reason="signing_failed",
            error="Rust signing returned no raw transaction",
        )

    relay_research = impl._private_relay_research_enabled(
        context["protocol"],
        context["opportunity_context"],
    )
    broadcast = submit_v4_route(
        {
            "rpc_url": rpc_url,
            "timeout_seconds": max(1, int(timeout_seconds or 15)),
            "allow_public_fallback": False if relay_research else True,
            "target_block": current_block + 1,
        },
        raw_signed_tx,
        relay_urls=None if relay_research else "",
    )
    tx_hash = broadcast.get("tx_hash")
    if not tx_hash:
        return _finish(
            ok=False,
            submitted=False,
            status=broadcast.get("status") or "broadcast_failed",
            blocked_reason=broadcast.get("status") or "broadcast_failed",
            error="broadcast failed; discard calldata and re-preview",
            broadcast_channel=broadcast.get("broadcast_channel"),
            deliveryPolicy=request_payload.get("deliveryPolicy"),
            relay_errors=broadcast.get("relay_errors"),
            privateRelayMetrics=broadcast.get("private_relay_metrics"),
        )

    receipt_payload = load_v4_receipt(
        [rpc_url],
        str(tx_hash).lower(),
        wait=True,
        timeout_seconds=max(1, int(timeout_seconds or 180)),
    )
    receipt = receipt_payload.get("receipt") if isinstance(receipt_payload.get("receipt"), dict) else {}
    status = "submitted_success" if int(receipt.get("status") or 0) == 1 else "submitted_failed"
    events = impl._unified_receipt_events(executor, receipt) if receipt else {}
    private_relay_metrics = list(broadcast.get("private_relay_metrics") or [])
    if private_relay_metrics:
        included_block = int(str(receipt.get("blockNumber") or "0x0"), 16)
        for metric in private_relay_metrics:
            if not isinstance(metric, dict):
                continue
            metric["firstVisibleBlock"] = str(included_block or "")
            metric["finalIncludedBlock"] = str(included_block or "")
            try:
                target_block = int(metric.get("targetBlock") or 0)
            except (TypeError, ValueError):
                target_block = 0
            metric["targetBlockDelta"] = str(included_block - target_block) if target_block else ""
            metric["finalStatus"] = "included" if status == "submitted_success" else "included_reverted"
    return _finish(
        ok=status == "submitted_success",
        submitted=True,
        status=status,
        blocked_reason=None if status == "submitted_success" else "submission_failed",
        error=None if status == "submitted_success" else "transaction reverted",
        tx_hash=broadcast.get("tx_hash"),
        broadcast_channel=broadcast.get("broadcast_channel"),
        deliveryPolicy=request_payload.get("deliveryPolicy"),
        relay=broadcast.get("relay"),
        privateRelayMetrics=private_relay_metrics,
        receipt={
            "hash": str(receipt.get("hash") or receipt.get("transactionHash") or tx_hash),
            "status": receipt.get("status"),
            "gasUsed": str(receipt.get("gasUsed")) if receipt.get("gasUsed") is not None else None,
            "blockNumber": str(receipt.get("blockNumber")) if receipt.get("blockNumber") is not None else None,
            "events": events,
        },
    )


__all__ = ["broadcast_unified_direct_trade"]
