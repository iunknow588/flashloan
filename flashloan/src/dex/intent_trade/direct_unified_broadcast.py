"""Broadcast and receipt handling for the unified direct execution path."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from eth_account import Account
from bot.execution.gas_estimator import build_gas_params, estimate_gas_price
from bot.execution.private_tx import send_raw_transaction_private_first


def broadcast_unified_direct_trade(context: dict[str, Any]) -> dict[str, Any]:
    from dex.intent_trade import direct_impl as impl

    w3 = context["w3"]
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
    gas_price_for_selection = int(context["gas_price_for_selection"])
    relay_cost_for_selection = int(context["relay_cost_for_selection"])
    cache_penalty_for_selection = int(context["cache_penalty_for_selection"])
    public_mempool_penalty_for_selection = int(context["public_mempool_penalty_for_selection"])
    public_mempool_penalty_bps_for_selection = int(context["public_mempool_penalty_bps_for_selection"])
    avax_usdc_price_for_selection = int(context["avax_usdc_price_for_selection"])
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
    if not broadcast_enabled:
        return {
            "ok": True,
            "submitted": False,
            "status": "static_call_passed",
            "blocked_reason": "broadcast_disabled",
            "error": None,
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

    if gas_estimate_info is None:
        gas_estimate_info = estimate_gas_price(
            w3,
            max_gas_price_gwei=(
                impl._max_broadcast_gas_price_wei() / 1_000_000_000
                if impl._max_broadcast_gas_price_wei()
                else None
            ),
        )
    gas_guard = impl._broadcast_gas_guard(gas_estimate, gas_estimate_info)
    static_report["gasPricing"] = gas_guard["report"]
    if not gas_guard["ok"]:
        return {
            "ok": False,
            "submitted": False,
            "status": gas_guard["status"],
            "blocked_reason": gas_guard["blocked_reason"],
            "error": gas_guard["blocked_reason"],
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

    tx_params = {
        "from": signer_address,
        "nonce": w3.eth.get_transaction_count(signer_address, "pending"),
        "chainId": chain_id,
        "gas": gas_estimate,
        **build_gas_params(gas_estimate_info),
    }
    built_tx = tx_builder.build_transaction(tx_params)
    signed_tx = Account.sign_transaction(built_tx, signer_key)
    raw_signed_tx = impl._raw_signed_transaction(signed_tx)
    if impl._private_relay_research_enabled(
        context["protocol"],
        context["opportunity_context"],
    ):
        broadcast = send_raw_transaction_private_first(
            raw_signed_tx,
            public_w3=w3,
            allow_public_fallback=impl._public_fallback_enabled(
                context["protocol"],
                context["opportunity_context"],
            ),
            target_block=current_block + 1,
            defer_public_fallback=True,
        )
    else:
        public_tx_hash = w3.eth.send_raw_transaction(raw_signed_tx)
        broadcast = {
            "tx_hash": public_tx_hash.hex() if hasattr(public_tx_hash, "hex") else str(public_tx_hash),
            "broadcast_channel": "public_rpc",
            "relay": "",
            "private_relay_metrics": [],
            "delivery_mode": "public_rpc_direct_after_fresh_gates",
        }
    tx_hash = broadcast.get("tx_hash")
    if not tx_hash:
        return impl._with_direct_circuit_record({
            "ok": False,
            "submitted": False,
            "status": broadcast.get("status") or "broadcast_failed",
            "blocked_reason": broadcast.get("status") or "broadcast_failed",
            "error": "broadcast failed; discard calldata and re-preview",
            "network": network,
            "chain_id": chain_id,
            "owner": onchain_owner,
            "signer": signer_address,
            "executor_address": executor_address,
            "broadcast_channel": broadcast.get("broadcast_channel"),
            "deliveryPolicy": request_payload.get("deliveryPolicy"),
            "relay_errors": broadcast.get("relay_errors"),
            "privateRelayMetrics": broadcast.get("private_relay_metrics"),
            "preflight": preview_report,
            "static_call": static_report,
            "strategy_status": preview_report["strategyStatus"],
            "route_direction": preview_report["executionPreview"]["routeDirection"],
            "request": request_payload,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }, network=network, route_key=selected_group["routeKey"])
    receipt_hash = tx_hash.hex() if hasattr(tx_hash, "hex") else tx_hash
    receipt = w3.eth.wait_for_transaction_receipt(
        receipt_hash,
        timeout=max(1, int(timeout_seconds or 180)),
    )
    status = "submitted_success" if receipt and int(receipt.status or 0) == 1 else "submitted_failed"
    events = impl._unified_receipt_events(receipt and executor or executor, receipt) if receipt else {}
    private_relay_metrics = list(broadcast.get("private_relay_metrics") or [])
    if receipt and private_relay_metrics:
        included_block = int(receipt.blockNumber) if receipt.blockNumber is not None else 0
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
    return impl._with_direct_circuit_record({
        "ok": status == "submitted_success",
        "submitted": True,
        "status": status,
        "blocked_reason": None if status == "submitted_success" else "submission_failed",
        "error": None if status == "submitted_success" else "transaction reverted",
        "network": network,
        "chain_id": chain_id,
        "owner": onchain_owner,
        "signer": signer_address,
        "executor_address": executor_address,
        "tx_hash": broadcast.get("tx_hash"),
        "broadcast_channel": broadcast.get("broadcast_channel"),
        "deliveryPolicy": request_payload.get("deliveryPolicy"),
        "relay": broadcast.get("relay"),
        "privateRelayMetrics": private_relay_metrics,
        "preflight": preview_report,
        "static_call": static_report,
        "strategy_status": preview_report["strategyStatus"],
        "route_direction": preview_report["executionPreview"]["routeDirection"],
        "request": request_payload,
        "receipt": {
            "hash": receipt.hash.hex() if hasattr(receipt.hash, "hex") else str(receipt.hash),
            "status": receipt.status,
            "gasUsed": str(receipt.gasUsed) if receipt and receipt.gasUsed is not None else None,
            "blockNumber": str(receipt.blockNumber) if receipt and receipt.blockNumber is not None else None,
            "events": events,
        },
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }, network=network, route_key=selected_group["routeKey"])
