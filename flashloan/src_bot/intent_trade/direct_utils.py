from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from cow_flashloan.routes import build_token_registry
from eth_utils import keccak


ROUTE_FAILURE_NAMES = {
    0: "none",
    1: "first_hop_quote_failed",
    2: "direct_comparison_quote_failed",
    3: "middle_hop_quote_failed",
    4: "edge_below_required",
    5: "full_route_quote_failed",
    6: "quoted_final_below_required",
    7: "slippage_adjusted_final_below_required",
}
RUNTIME_FAILURE_NAMES = {
    0: "none",
    101: "not_enough_valid_pools",
    102: "no_price_spread",
}
EXECUTOR_FAILURE_NAMES = {
    1: "post_swap_balance_below_actual_repayment",
}
CONTROLLER_ERROR_SELECTOR = keccak(
    text="NoViableRoute(uint256,uint256,uint256,uint256,uint256,uint256)"
)[:4].hex()
RUNTIME_CONTROLLER_ERROR_SELECTOR = keccak(text="NoRuntimeOpportunity(uint256)")[:4].hex()
EXECUTOR_ERROR_SELECTOR = keccak(
    text="ExecutionConstraintFailed(uint256,uint256,uint256,uint256,uint256,uint256)"
)[:4].hex()
ROUTER_SWAP_ERROR_SELECTOR = keccak(text="RouterSwapFailed(bytes4)")[:4].hex()
ROUTER_SWAP_RESULT_INVALID_SELECTOR = keccak(text="RouterSwapResultInvalid(uint256)")[:4].hex()


def _bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _raw_signed_transaction(signed_tx: Any) -> bytes:
    raw = getattr(signed_tx, "raw_transaction", None) or getattr(signed_tx, "rawTransaction", None)
    if raw is None:
        raise AttributeError("signed transaction does not expose raw transaction bytes")
    return raw


def _build_direct_token_registry(*, aave_cache_path: Path, network: str) -> dict[str, Any]:
    local_cache = aave_cache_path if aave_cache_path.exists() else None
    try:
        return build_token_registry(aave_cache_path=local_cache, cow_network=network)
    except Exception:
        if local_cache is not None:
            return build_token_registry(aave_cache_path=local_cache, cow_network=network, include_cow_token_list=False)
        raise


def _route_failure_name(code: Any) -> str:
    try:
        return ROUTE_FAILURE_NAMES.get(int(code), f"unknown_failure_{code}")
    except (TypeError, ValueError):
        return "unknown_failure"


def _runtime_failure_name(code: Any) -> str:
    try:
        return RUNTIME_FAILURE_NAMES.get(int(code), f"unknown_failure_{code}")
    except (TypeError, ValueError):
        return "unknown_failure"


def _route_decision_report(decision: Any) -> dict[str, Any]:
    failure_code = int(decision[9]) if len(decision) > 9 else 0
    return {
        "viable": bool(decision[0]),
        "reverse": bool(decision[1]),
        "quotedFinalUsdc": str(decision[2]),
        "profitUsdc": str(decision[3]),
        "path": decision[4],
        "edgeBps": str(decision[5]),
        "requiredEdgeBps": str(decision[6]),
        "directComparableAmount": str(decision[7]),
        "viaComparableAmount": str(decision[8]),
        "failureCode": str(failure_code),
        "failureReason": _route_failure_name(failure_code),
        "requiredFinalUsdc": str(decision[10]) if len(decision) > 10 else "0",
        "minAfterSlippageUsdc": str(decision[11]) if len(decision) > 11 else "0",
        "amountOutMinUsdc": str(decision[12]) if len(decision) > 12 else "0",
        "selectedAmount": str(decision[13]) if len(decision) > 13 else "0",
        "routeMaxBorrow": str(decision[14]) if len(decision) > 14 else "0",
        "probeAmount": str(decision[15]) if len(decision) > 15 else "0",
        "probeProfitUsdc": str(decision[16]) if len(decision) > 16 else "0",
        "fundingCostUsdc": str(decision[17]) if len(decision) > 17 else "0",
        "mBps": str(decision[18]) if len(decision) > 18 else "0",
    }


def _execution_failure_report(error: Any) -> dict[str, Any] | None:
    raw = _revert_data(error)
    if not raw or len(raw) < 2 + 8:
        return None
    selector = raw[2:10].lower()
    if selector == RUNTIME_CONTROLLER_ERROR_SELECTOR and len(raw) == 2 + 8 + 64:
        failure_code = int(raw[10:74], 16)
        return {
            "source": "triangular_route_controller",
            "failureCode": str(failure_code),
            "failureReason": _runtime_failure_name(failure_code),
        }
    if selector == ROUTER_SWAP_ERROR_SELECTOR and len(raw) == 2 + 8 + 64:
        return {
            "source": "aave_triangular_executor",
            "failureCode": "router_swap_failed",
            "failureReason": f"router_swap_reverted_selector_0x{raw[10:18]}",
        }
    if selector == ROUTER_SWAP_RESULT_INVALID_SELECTOR and len(raw) == 2 + 8 + 64:
        result_length = int(raw[10:74], 16)
        return {
            "source": "aave_triangular_executor",
            "failureCode": "router_swap_result_invalid",
            "failureReason": "router_swap_result_length_invalid",
            "resultLength": str(result_length),
        }
    if len(raw) != 2 + 8 + 64 * 6:
        return None
    values = [int(raw[index:index + 64], 16) for index in range(10, len(raw), 64)]
    if selector == CONTROLLER_ERROR_SELECTOR:
        return {
            "source": "triangular_route_controller",
            "failureCode": str(values[0]),
            "failureReason": _route_failure_name(values[0]),
            "edgeBps": str(values[1]),
            "requiredEdgeBps": str(values[2]),
            "quotedFinalUsdc": str(values[3]),
            "requiredFinalUsdc": str(values[4]),
            "minAfterSlippageUsdc": str(values[5]),
        }
    if selector == EXECUTOR_ERROR_SELECTOR:
        return {
            "source": "aave_triangular_executor",
            "failureCode": str(values[0]),
            "failureReason": EXECUTOR_FAILURE_NAMES.get(values[0], f"unknown_failure_{values[0]}"),
            "amountOutMinUsdc": str(values[1]),
            "repaymentRequiredUsdc": str(values[2]),
            "finalUsdc": str(values[3]),
            "actualBalanceUsdc": str(values[4]),
            "requiredBalanceUsdc": str(values[5]),
        }
    return None


def _revert_data(error: Any) -> str:
    for value in _nested_error_values(error):
        if isinstance(value, str):
            match = re.search(r"0x[a-fA-F0-9]{8,}", value)
            if match:
                return match.group(0)
    return ""


def _nested_error_values(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from _nested_error_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _nested_error_values(item)
    else:
        data = getattr(value, "data", None)
        if data is not None:
            yield from _nested_error_values(data)
        args = getattr(value, "args", None)
        if args:
            yield from _nested_error_values(args)
        yield value
