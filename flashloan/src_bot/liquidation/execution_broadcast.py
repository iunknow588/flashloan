from __future__ import annotations

from typing import Any, Callable


def build_transaction_params(
    w3,
    tx_builder,
    *,
    sender: str,
    nonce: int,
    controls: dict[str, Any],
    gas_floor: int,
    gas_fallback: int,
) -> dict[str, Any]:
    tx_params = {
        "from": sender,
        "nonce": nonce,
        "chainId": int(w3.eth.chain_id),
    }
    try:
        estimated_gas = tx_builder.estimate_gas({"from": sender})
        tx_params["gas"] = max(gas_floor, int(estimated_gas * 12 // 10))
    except Exception:
        tx_params["gas"] = gas_fallback

    latest_block = w3.eth.get_block("latest")
    base_fee = int(getattr(latest_block, "baseFeePerGas", 0) or 0)
    priority_fee = w3.to_wei(max(0.0, float(controls["priority_fee_gwei"])), "gwei")
    if base_fee > 0:
        tx_params["maxPriorityFeePerGas"] = int(priority_fee)
        tx_params["maxFeePerGas"] = int(base_fee * 2 + priority_fee)
    else:
        tx_params["gasPrice"] = int(w3.eth.gas_price)
    return tx_params


def sign_and_broadcast_transaction(
    w3,
    tx_builder,
    tx_params: dict[str, Any],
    *,
    private_key: str,
    send_raw_transaction: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    built_tx = tx_builder.build_transaction(tx_params)
    signed = w3.eth.account.sign_transaction(built_tx, private_key=private_key)
    raw_tx = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    return send_raw_transaction(raw_tx, public_w3=w3)
