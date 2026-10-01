from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import importlib
import os
import sys
from typing import Any

from web3 import Web3

from core.error_stack import ErrorStack, ModuleError
from core.error_registry import ErrorCode
from bot.execution.uniswap_v4_sdk_adapter import load_v4_sdk_adapter
from dex.intent_trade.chains.robinhood import config as robinhood
from dex.intent_trade.chains.robinhood.chain_test_policy import ROBINHOOD_TESTNET_CHAIN_ID, ROBINHOOD_TESTNET_NETWORK

def _evidence_module_or_none() -> Any | None:
    for module_name in ("runtime.dex_onchain_quote_evidence", "dex.runtime.dex_onchain_quote_evidence"):
        if sys.modules.get(module_name) is not None:
            return sys.modules[module_name]
    return None

def _evidence_attr_or_default(name: str, default: Any) -> Any:
    module = _evidence_module_or_none()
    if module is not None and hasattr(module, name):
        return getattr(module, name)
    return default

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _norm_symbol(value: object) -> str:
    return str(value or "").strip().upper()

def _text(value: object) -> str:
    return str(value or "").strip()

def _robinhood_network_for_chain_id(chain_id: int | None, network: object = "") -> str:
    """返回给定链/网络参数对应的 Robinhood 规范网络名称。

    Robinhood 家族内以 chain_id 为准，因为旧路由元数据可能写着
    ``robinhood``，但实际数据可能来自 Robinhood 测试网 46630。
    """
    normalized = _text(network).lower()
    parsed_chain_id = int(chain_id) if chain_id is not None and str(chain_id).strip() else None
    if parsed_chain_id == ROBINHOOD_TESTNET_CHAIN_ID:
        return ROBINHOOD_TESTNET_NETWORK
    if parsed_chain_id == robinhood.CHAIN_ID:
        return robinhood.NETWORK
    if normalized in {
        ROBINHOOD_TESTNET_NETWORK,
        "robinhood_testnet",
        "robinhood-sepolia",
        "chain_46630",
    }:
        return ROBINHOOD_TESTNET_NETWORK
    if normalized in {
        robinhood.NETWORK,
        "robinhood-mainnet",
        "robinhood_mainnet",
        "chain_4663",
    }:
        return robinhood.NETWORK
    return normalized

def _is_missing_historical_state_error(exc: BaseException) -> bool:
    message = _text(exc).lower()
    return (
        "missing trie node" in message
        or "metadata is not found" in message
        or ("historical state" in message and "is not available" in message)
        or "state is not available" in message
        or ("state is not" in message and "available" in message)
        or "state .* not found" in message
    )

def _is_pool_not_initialized_error(exc: BaseException) -> bool:
    message = _text(exc).lower()
    if (
        "poolnotinitialized" in message.replace("_", "").replace("(", "").replace(")", "")
        or "pool not initialized" in message
        or "slot0 missing" in message
        or "missing sqrtpricex96" in message
        or "live slot0" in message
    ):
        return True
    cause = getattr(exc, "__cause__", None)
    return bool(cause is not None and _is_pool_not_initialized_error(cause))

def _is_missing_v4_adapter_error(exc: BaseException) -> bool:
    message = _text(exc).lower()
    if any(
        marker in message
        for marker in (
            "quote adapter is required",
            "adapter_required",
            "adapter required",
            "v4 pool manager address missing",
            "v4 quoter address missing",
            "v4 pool index missing",
            "pool index missing requested pool",
            "pool index data file",
            "pool key decode",
            "requested hop tokens do not match",
        )
    ):
        return True
    cause = getattr(exc, "__cause__", None)
    return bool(cause is not None and _is_missing_v4_adapter_error(cause))

def _is_not_enough_liquidity_error(exc: BaseException) -> bool:
    message = _text(exc).lower()
    if (
        "notenoughliquidity" in message.replace("_", "").replace("(", "").replace(")", "")
        or "not enough liquidity" in message
        or "insufficient liquidity" in message
        or "live liquidity is zero" in message
        or "liquidity is zero" in message
        or "no lp liquidity" in message
    ):
        return True
    cause = getattr(exc, "__cause__", None)
    return bool(cause is not None and _is_not_enough_liquidity_error(cause))

def _pool_not_initialized_error(exc: BaseException) -> str | None:
    if _is_pool_not_initialized_error(exc):
        return str(exc)
    return None

def _is_rpc_verification_error(exc: BaseException) -> str | None:
    """返回 Robinhood RPC 失败状态；如果错误与 RPC 无关则返回 None。"""
    message = _text(exc).lower()
    # 连接层已经用尽显式的 10/20/30 秒尝试机会。
    # 即使每次尝试最终都是 socket 超时，这也是终端连接失败。
    # 仍在运行的复核通道由独立的 evidence 看门狗负责 rpc_verification_timeout。
    if "rpc" in message and "unavailable after" in message and "connect attempt" in message:
        return "rpc_verification_failed"
    if "rpc" in message and any(
        marker in message
        for marker in (
            "unavailable",
            "failed",
            "connect",
            "timeout",
            "timed out",
            "not available",
            "cannot be reached",
        )
    ):
        return "rpc_verification_timeout" if ("timeout" in message or "timed out" in message) else "rpc_verification_failed"
    if (
        "timeout" in message
        or "timed out" in message
    ) and any(marker in message for marker in ("web3", "rpc", "http", "provider", "endpoint")):
        return "rpc_verification_timeout"
    cause = getattr(exc, "__cause__", None)
    if cause is not None:
        nested = _is_rpc_verification_error(cause)
        if nested:
            return nested
    return None

DEX_STATUS_ERROR_KEYS = {
    "live_quote_failed": "DEX.QUOTE.LIVE_QUOTE_FAILED",
    "pool_not_initialized": "DEX.ONCHAIN.HOP_QUOTE.POOL_NOT_INITIALIZED",
    "not_enough_liquidity": "DEX.ONCHAIN.HOP_QUOTE.NOT_ENOUGH_LIQUIDITY",
    "rpc_verification_failed": "DEX.ONCHAIN.RPC_FAILED",
    "rpc_verification_timeout": "DEX.ONCHAIN.RPC_TIMEOUT",
    "adapter_required": "DEX.ONCHAIN.ADAPTER_REQUIRED",
    "missing_trigger_block": "DEX.ONCHAIN.TRIGGER_BLOCK_MISSING",
    "not_profitable_after_costs": "DEX.SIZE_PROBE.NOT_PROFITABLE_AFTER_COSTS",
    "route_unprofitable": "DEX.SIZE_PROBE.NOT_PROFITABLE_AFTER_COSTS",
    "pool_insufficient_for_profitable_execution": "DEX.GATE.NOT_READY",
    "slippage_limit_exceeded": "DEX.GATE.NOT_READY",
    "live_evidence_deferred": "DEX.GATE.NOT_READY",
    "live_quote_reference_only": "DEX.GATE.NOT_READY",
    "static_reference_pending_live_review": "DEX.GATE.NOT_READY",
    "missing_effective_pool": "DEX.GATE.NOT_READY",
    "depth_insufficient": "DEX.GATE.NOT_READY",
}

DEX_ERROR_KEY_CODES = {
    "DEX.QUOTE.LIVE_QUOTE_FAILED": ErrorCode.DEX_LIVE_QUOTE_FAILED,
    "DEX.ONCHAIN.HOP_QUOTE.POOL_NOT_INITIALIZED": ErrorCode.DEX_POOL_NOT_INITIALIZED,
    "DEX.ONCHAIN.HOP_QUOTE.NOT_ENOUGH_LIQUIDITY": ErrorCode.DEX_NOT_ENOUGH_LIQUIDITY,
    "DEX.ONCHAIN.RPC_FAILED": ErrorCode.DEX_RPC_FAILED,
    "DEX.ONCHAIN.RPC_TIMEOUT": ErrorCode.DEX_RPC_TIMEOUT,
    "DEX.ONCHAIN.ADAPTER_REQUIRED": ErrorCode.DEX_GATE_NOT_READY,
    "DEX.ONCHAIN.TRIGGER_BLOCK_MISSING": ErrorCode.DEX_GATE_NOT_READY,
    "DEX.SIZE_PROBE.POOL_NOT_INITIALIZED": ErrorCode.DEX_SIZE_POOL_NOT_INITIALIZED,
    "DEX.SIZE_PROBE.NOT_PROFITABLE_AFTER_COSTS": ErrorCode.DEX_SIZE_NOT_PROFITABLE,
    "DEX.GATE.NOT_READY": ErrorCode.DEX_GATE_NOT_READY,
    "DEX.ROUTE.INVALID": ErrorCode.DEX_ROUTE_INVALID,
    "DEX.RPC.FAILED": ErrorCode.DEX_RPC_FAILED,
    "DEX.RPC.TIMEOUT": ErrorCode.DEX_RPC_TIMEOUT,
    "DEX.INTERNAL": ErrorCode.DEX_INTERNAL,
}

def dex_error_stack_from_state(
    status: object,
    *,
    message: object = None,
    details: dict[str, Any] | None = None,
    flow: str = "dex_route",
) -> list[dict[str, Any]]:
    return _dex_error_stack_from_status(
        status,
        message=message,
        raw_error=message,
        details=details,
        flow=flow,
        node_id=str((details or {}).get("hop") or ""),
    )

def _dex_error_stack_from_status(
    status: object,
    *,
    message: object = None,
    raw_error: object = None,
    details: dict[str, Any] | None = None,
    flow: str = "onchain_quote",
    node_id: str = "evidence",
) -> list[dict[str, Any]]:
    status_text = str(status or "").strip().lower()
    detail_values = dict(details or {})
    error_key = DEX_STATUS_ERROR_KEYS.get(status_text, "DEX.GATE.NOT_READY" if status_text and status_text != "live_verified" else "CORE.OK")
    stack = ErrorStack()
    if status_text and status_text != "live_verified":
        node_label = str(detail_values.get("hop") or node_id or "")
        reason = str(message or error_key)
        if node_label and node_label not in reason:
            reason = f"[{flow}:{node_label}] {reason}"
        stack.push(
            ModuleError(
                module="DEX",
                code=int(DEX_ERROR_KEY_CODES.get(error_key, ErrorCode.DEX_INTERNAL)),
                message=reason,
            )
        )
    else:
        stack.mark_ok(ModuleError(module="DEX", code=0, message="OK"))
    return stack.as_list()

def _dex_error_stack_from_hop(hop: dict[str, Any], *, index: int = 0, flow: str = "onchain_quote") -> list[dict[str, Any]]:
    status = str(hop.get("status") or "").strip().lower()
    raw = hop.get("liveQuoteError")
    message = raw or status
    raw_as_exc = ValueError(str(raw))
    key = DEX_STATUS_ERROR_KEYS.get(
        _is_rpc_verification_error(raw_as_exc)
        or (
            "not_enough_liquidity"
            if _is_not_enough_liquidity_error(raw_as_exc)
            else "pool_not_initialized"
            if _is_pool_not_initialized_error(raw_as_exc)
            else "adapter_required"
            if _is_missing_v4_adapter_error(raw_as_exc)
            else status
        ),
        "DEX.QUOTE.LIVE_QUOTE_FAILED" if status in {"live_quote_failed", "quote_failed"} else "CORE.OK",
    )
    stack = None
    if status in {
        "live_quote_failed",
        "quote_failed",
        "live_evidence_deferred",
        "pool_not_initialized",
        "not_enough_liquidity",
        "adapter_required",
        "rpc_verification_failed",
        "rpc_verification_timeout",
    }:
        stack = ErrorStack()
        hop_num = hop.get("hop") or index
        symbols = f"{_norm_symbol(hop.get('fromSymbol'))}->{_norm_symbol(hop.get('toSymbol'))}"
        code = int(DEX_ERROR_KEY_CODES.get(key, ErrorCode.DEX_LIVE_QUOTE_FAILED))
        stack.push(
            ModuleError(
                module="DEX",
                code=code,
                message=f"hop {hop_num} {symbols} verification failed",
            )
        )
        raw_text = str(message or "").strip()
        if raw_text.startswith(f"hop {hop_num} {symbols}:"):
            raw_text = raw_text.split(":", 1)[1].strip()
        layers: list[str] = []
        for segment in raw_text.split(";"):
            segment = segment.strip()
            if not segment:
                continue
            lowered = segment.lower()
            if "reverted" in lowered and not segment.endswith("reverted"):
                before, after = segment.split("reverted", 1)
                layers.append((before + "reverted").strip())
                if after.lstrip(": ").strip():
                    layers.extend(part.strip() for part in after.lstrip(": ").split("->") if part.strip())
            elif "exact-input call failed" in lowered or "reverted" in lowered:
                layers.append(segment)
            else:
                layers.extend(part.strip() for part in segment.split("->") if part.strip())
        for layer in layers:
            if layer.startswith("hop ") and "verification failed" in layer.lower():
                continue
            stack.push(
                ModuleError(
                    module="DEX",
                    code=code,
                    message=layer,
                )
            )
    return stack.as_list() if stack is not None else []

def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}

def _has_not_enough_liquidity_blocker(evidence: dict[str, Any]) -> bool:
    hops = evidence.get("hops") or []
    if any(_text(hop.get("status")).lower() == "not_enough_liquidity" for hop in hops if isinstance(hop, dict)):
        return True
    blockers = [str(item) for item in evidence.get("blockers") or [] if str(item).strip()]
    if not blockers:
        return False
    return _is_not_enough_liquidity_error(ValueError(" ".join(blockers)))

def _number(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed

def _int_or_none(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal amount: {value!r}") from exc

def _raw_from_human(value: object, decimals: int) -> int:
    amount = _decimal(value)
    if amount <= 0:
        raise ValueError("amount must be positive")
    return int((amount * (Decimal(10) ** int(decimals))).to_integral_value())

def _human_from_raw(value: int, decimals: int) -> str:
    return format((Decimal(int(value)) / (Decimal(10) ** int(decimals))).normalize(), "f")

def _positive_decimal_or_none(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    parsed = _decimal(value)
    return parsed if parsed > 0 else None

def _checksum(value: object) -> str:
    return Web3.to_checksum_address(str(value))

def _protocol_key(*values: object) -> str:
    text = " ".join(_text(value).lower() for value in values)
    if "v4" in text:
        return "uniswap_v4"
    if "v3" in text:
        return "uniswap_v3"
    if "v2" in text:
        return "uniswap_v2"
    return text.strip() or "unknown"

def _unsupported_step_reason(step: dict[str, Any], pool: dict[str, Any]) -> str | None:
    protocol = _protocol_key(step.get("protocol"), step.get("dex"), step.get("fee"), pool.get("protocol"), pool.get("dex"))
    if protocol not in {"uniswap_v2", "uniswap_v3", "uniswap_v4"}:
        return (
            f"hop {step.get('hop') or '?'} protocol "
            f"{step.get('protocol') or step.get('dex') or step.get('fee')} is not wired to on-chain review"
        )
    return None

def _fee_bps(value: object) -> int:
    text = _text(value).lower().replace("bps", "")
    try:
        parsed = float(text)
    except ValueError:
        return 30
    if parsed >= 100:
        return int(parsed / 100)
    return int(parsed)

def _pool_map(route: dict[str, Any]) -> dict[str, dict[str, Any]]:
    quote_inputs = route.get("quoteInputs") if isinstance(route.get("quoteInputs"), dict) else {}
    pools = []
    for key in ("routePools", "candidatePools"):
        if isinstance(quote_inputs.get(key), list):
            pools.extend(quote_inputs.get(key) or [])
    for key in ("executionPools", "preferredPools", "strictQualityPools"):
        if isinstance(route.get(key), list):
            pools.extend(route.get(key) or [])
    mapping: dict[str, dict[str, Any]] = {}
    for pool in pools:
        if isinstance(pool, dict) and _text(pool.get("pool")):
            mapping[_text(pool.get("pool")).lower()] = pool
    return mapping

def _pool_for_step(step: dict[str, Any], pools: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return pools.get(_text(step.get("pool")).lower(), {})

def _load_v4_quote_adapter(*, network: str | None = None, chain_id: int | None = None) -> Any | None:
    normalized_network = _robinhood_network_for_chain_id(chain_id, network)
    is_testnet = bool(
        normalized_network in {ROBINHOOD_TESTNET_NETWORK, "robinhood_sepolia", "robinhood_testnet"}
        or int(chain_id or 0) == ROBINHOOD_TESTNET_CHAIN_ID
    )
    is_mainnet = bool(
        normalized_network in {"robinhood", "robinhood-mainnet", "robinhood_mainnet"}
        or int(chain_id or 0) == robinhood.CHAIN_ID
    )
    if is_testnet:
        preferred_names = (
            "ROBINHOOD_TESTNET_V4_QUOTE_ADAPTER",
            "ROBINHOOD_V4_QUOTE_ADAPTER",
            "UNISWAP_V4_QUOTE_ADAPTER",
            "DEX_V4_QUOTE_ADAPTER",
        )
    elif is_mainnet:
        preferred_names = (
            "ROBINHOOD_MAINNET_V4_QUOTE_ADAPTER",
            "ROBINHOOD_V4_QUOTE_ADAPTER",
            "UNISWAP_V4_QUOTE_ADAPTER",
            "DEX_V4_QUOTE_ADAPTER",
        )
    else:
        preferred_names = (
            "ROBINHOOD_V4_QUOTE_ADAPTER",
            "UNISWAP_V4_QUOTE_ADAPTER",
            "DEX_V4_QUOTE_ADAPTER",
        )
    reference = ""
    for name in preferred_names:
        candidate = str(os.getenv(name) or "").strip()
        if candidate:
            reference = candidate
            break
    if reference:
        module_name, separator, attr_path = str(reference).strip().partition(":")
        if not module_name or not separator or not attr_path:
            raise ValueError("UNISWAP_V4_QUOTE_ADAPTER must use module:callable format")
        target: Any = importlib.import_module(module_name)
        for part in attr_path.split("."):
            target = getattr(target, part)
        return target
    return load_v4_sdk_adapter(network=network, chain_id=chain_id)
