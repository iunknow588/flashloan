from __future__ import annotations


def format_tx_receipt(receipt) -> dict:
    tx_hash = getattr(receipt, "transactionHash", "")
    return {
        "transaction_hash": tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash),
        "block_number": int(getattr(receipt, "blockNumber", 0) or 0),
        "gas_used": int(getattr(receipt, "gasUsed", 0) or 0),
        "effective_gas_price": int(getattr(receipt, "effectiveGasPrice", 0) or 0),
        "status": int(getattr(receipt, "status", 0) or 0),
    }
