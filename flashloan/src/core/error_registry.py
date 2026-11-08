"""Project-local BOT/DEX/CORE error code tables.

These codes are owned by the flashloan project, not by the reusable
``errorstack`` package.  The generic stack never reads this registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class ErrorCode(IntEnum):
    OK = 0

    CORE_VALIDATION = 1001
    CORE_SCOPE_MISMATCH = 1002
    CORE_FLOW_MISSING = 1003
    CORE_INTERNAL = 1099

    BOT_ACCOUNT_NOT_LIQUIDATABLE = 2001
    BOT_NO_EXECUTABLE_CANDIDATE = 2002
    BOT_QUOTE_FAILED = 2003
    BOT_STATIC_CALL_FAILED = 2004
    BOT_APPROVAL_FAILED = 2005
    BOT_SUBMISSION_REVERTED = 2006
    BOT_RECEIPT_FAILED = 2007
    BOT_EXECUTION_BLOCKED = 2008
    BOT_INTERNAL = 2099

    DEX_LIVE_QUOTE_FAILED = 3001
    DEX_POOL_NOT_INITIALIZED = 3002
    DEX_NOT_ENOUGH_LIQUIDITY = 3003
    DEX_SIZE_POOL_NOT_INITIALIZED = 3004
    DEX_SIZE_NOT_PROFITABLE = 3005
    DEX_GATE_NOT_READY = 3006
    DEX_ROUTE_INVALID = 3007
    DEX_RPC_FAILED = 3008
    DEX_RPC_TIMEOUT = 3009
    DEX_RPC_ALREADY_KNOWN = 3010
    DEX_RPC_NONCE_TOO_LOW = 3011
    DEX_RPC_REPLACEMENT_UNDERPRICED = 3012
    DEX_RPC_TRANSPORT_TIMEOUT = 3013
    DEX_RPC_BROADCAST_NOT_ACCEPTED = 3014
    DEX_RPC_RECEIPT_FAILED = 3015
    DEX_INTERNAL = 3099


@dataclass(frozen=True)
class ErrorDefinition:
    key: str
    code: int
    domain: str
    flow: str
    node_id: str
    retryable: bool
    max_attempts: int
    backoff_seconds: float
    manual_review: bool
    disposition: str
    action: str


DISPOSITIONS = frozenset(
    {
        "CLOSE_NO_OPPORTUNITY",
        "REQUOTE_AND_RETRY",
        "REFRESH_POOL_SCOPE",
        "RETRY_READONLY",
        "VERIFY_CHAIN_STATE",
        "RESUBMIT_SAME_RAW_ON_TRANSPORT",
        "CLOSE_AND_QUARANTINE",
        "STOP_BROADCAST_SCOPE",
        "DESIGN_BLOCKED",
        "DEFER_QUEUE",
    }
)


def _definition(
    key: str,
    code: int | ErrorCode,
    domain: str,
    flow: str,
    node_id: str,
    *,
    retryable: bool = False,
    max_attempts: int = 1,
    backoff_seconds: float = 0.0,
    manual_review: bool = False,
    disposition: str = "DESIGN_BLOCKED",
    action: str = "",
) -> ErrorDefinition:
    return ErrorDefinition(
        key=key,
        code=int(code),
        domain=domain,
        flow=flow,
        node_id=node_id,
        retryable=retryable,
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
        manual_review=manual_review,
        disposition=disposition,
        action=action,
    )


CONTRACT_DEX_MODULE_OFFSET = 40_000_000

CONTRACT_DEX_FLOW_CODES: dict[str, int] = {
    "validation": 1,
    "quote": 2,
    "execution": 3,
    "repayment": 4,
    "callback": 5,
    "sweep": 6,
    "internal": 9,
}

CONTRACT_DEX_NODE_CODES: dict[str, int] = {
    "request_validate": 1,
    "flash_source_resolve": 2,
    "adapter_resolve": 3,
    "route_validate": 4,
    "hop_quote": 5,
    "swap_execute": 6,
    "callback_verify": 7,
    "repayment_check": 8,
    "transfer": 9,
    "approve": 10,
    "reentrancy_guard": 11,
    "owner_guard": 12,
    "pool_guard": 13,
    "initiator_guard": 14,
    "paused_guard": 15,
    "internal": 99,
}


def _contract_definition(
    key: str,
    flow: str,
    node_id: str,
    detail_code: int = 1,
    *,
    retryable: bool = False,
    max_attempts: int = 1,
    backoff_seconds: float = 0.0,
    manual_review: bool = False,
    disposition: str = "DESIGN_BLOCKED",
    action: str = "",
) -> ErrorDefinition:
    flow_code = CONTRACT_DEX_FLOW_CODES[flow]
    node_code = CONTRACT_DEX_NODE_CODES[node_id]
    code = CONTRACT_DEX_MODULE_OFFSET + flow_code * 10_000 + node_code * 100 + detail_code
    return _definition(
        key,
        code,
        "CONTRACT.DEX",
        flow,
        node_id,
        retryable=retryable,
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
        manual_review=manual_review,
        disposition=disposition,
        action=action,
    )


ERROR_REGISTRY: dict[str, ErrorDefinition] = {
    "CORE.OK": _definition("CORE.OK", ErrorCode.OK, "CORE", "system", "complete", disposition="CLOSE_NO_OPPORTUNITY", action="机会正常结束，不记为系统故障"),
    "CORE.VALIDATION.INVALID": _definition("CORE.VALIDATION.INVALID", ErrorCode.CORE_VALIDATION, "CORE", "validation", "validate_request", disposition="CLOSE_AND_QUARANTINE", action="拒绝当前请求，保留输入快照，不重试原请求"),
    "CORE.SCOPE.MISMATCH": _definition("CORE.SCOPE.MISMATCH", ErrorCode.CORE_SCOPE_MISMATCH, "CORE", "scope", "validate_scope", disposition="DESIGN_BLOCKED", action="阻断错误作用域并生成配置/设计事件"),
    "CORE.FLOW.MISSING": _definition("CORE.FLOW.MISSING", ErrorCode.CORE_FLOW_MISSING, "CORE", "dispatch", "resolve_flow", disposition="DESIGN_BLOCKED", action="fail-closed 并记录完整错误，交由设计阶段补齐"),
    "CORE.INTERNAL": _definition("CORE.INTERNAL", ErrorCode.CORE_INTERNAL, "CORE", "system", "internal", manual_review=True, disposition="DESIGN_BLOCKED", action="fail-closed、隔离并记录完整错误堆栈"),
    "BOT.LIQUIDATION.ACCOUNT_NOT_LIQUIDATABLE": _definition("BOT.LIQUIDATION.ACCOUNT_NOT_LIQUIDATABLE", ErrorCode.BOT_ACCOUNT_NOT_LIQUIDATABLE, "BOT", "liquidation", "account_check", disposition="CLOSE_NO_OPPORTUNITY", action="当前账户无清算机会，正常关闭"),
    "BOT.LIQUIDATION.NO_EXECUTABLE_CANDIDATE": _definition("BOT.LIQUIDATION.NO_EXECUTABLE_CANDIDATE", ErrorCode.BOT_NO_EXECUTABLE_CANDIDATE, "BOT", "liquidation", "candidate_selection", disposition="CLOSE_NO_OPPORTUNITY", action="当前轮次无可执行候选，关闭当前机会"),
    "BOT.LIQUIDATION.QUOTE_FAILED": _definition("BOT.LIQUIDATION.QUOTE_FAILED", ErrorCode.BOT_QUOTE_FAILED, "BOT", "liquidation", "quote", retryable=True, max_attempts=2, backoff_seconds=1.0, disposition="RETRY_READONLY", action="按 RPC 错误预算只读重试，禁止进入广播"),
    "BOT.LIQUIDATION.STATIC_CALL_FAILED": _definition("BOT.LIQUIDATION.STATIC_CALL_FAILED", ErrorCode.BOT_STATIC_CALL_FAILED, "BOT", "liquidation", "static_call", manual_review=True, disposition="DESIGN_BLOCKED", action="先解析子错误；无法解析时 fail-closed 并生成设计事件"),
    "BOT.EXECUTION.APPROVAL_FAILED": _definition("BOT.EXECUTION.APPROVAL_FAILED", ErrorCode.BOT_APPROVAL_FAILED, "BOT", "execution", "approval", retryable=True, max_attempts=2, backoff_seconds=0.5, disposition="CLOSE_AND_QUARANTINE", action="按授权策略自动刷新后仍失败则隔离路由，不重发同一交易"),
    "BOT.EXECUTION.SUBMISSION_REVERTED": _definition("BOT.EXECUTION.SUBMISSION_REVERTED", ErrorCode.BOT_SUBMISSION_REVERTED, "BOT", "execution", "submission", manual_review=True, disposition="DESIGN_BLOCKED", action="必须先解析 revert 子错误；无法解析时 fail-closed"),
    "BOT.EXECUTION.RECEIPT_FAILED": _definition("BOT.EXECUTION.RECEIPT_FAILED", ErrorCode.BOT_RECEIPT_FAILED, "BOT", "execution", "receipt", retryable=True, max_attempts=2, backoff_seconds=1.0, disposition="VERIFY_CHAIN_STATE", action="跨 RPC 只读确认回执与交易，不能确认时自动隔离"),
    "BOT.EXECUTION.BLOCKED": _definition("BOT.EXECUTION.BLOCKED", ErrorCode.BOT_EXECUTION_BLOCKED, "BOT", "execution", "submission_gate", manual_review=True, disposition="STOP_BROADCAST_SCOPE", action="当前广播门关闭，只读继续，不提交"),
    "BOT.INTERNAL": _definition("BOT.INTERNAL", ErrorCode.BOT_INTERNAL, "BOT", "system", "internal", manual_review=True, disposition="DESIGN_BLOCKED", action="fail-closed、隔离并审计完整错误"),
    "DEX.QUOTE.LIVE_QUOTE_FAILED": _definition("DEX.QUOTE.LIVE_QUOTE_FAILED", ErrorCode.DEX_LIVE_QUOTE_FAILED, "DEX", "quote", "live_quote", retryable=True, max_attempts=2, backoff_seconds=1.0, disposition="RETRY_READONLY", action="只读重试实时报价，预算耗尽后关闭并隔离"),
    "DEX.ONCHAIN.HOP_QUOTE.POOL_NOT_INITIALIZED": _definition("DEX.ONCHAIN.HOP_QUOTE.POOL_NOT_INITIALIZED", ErrorCode.DEX_POOL_NOT_INITIALIZED, "DEX", "onchain_quote", "hop_quote", manual_review=True, disposition="REFRESH_POOL_SCOPE", action="标记池未初始化并刷新索引，不广播"),
    "DEX.ONCHAIN.HOP_QUOTE.NOT_ENOUGH_LIQUIDITY": _definition("DEX.ONCHAIN.HOP_QUOTE.NOT_ENOUGH_LIQUIDITY", ErrorCode.DEX_NOT_ENOUGH_LIQUIDITY, "DEX", "onchain_quote", "hop_quote", manual_review=True, disposition="REFRESH_POOL_SCOPE", action="排除当前池/方向并刷新流动性，不立即重复同一请求"),
    "DEX.SIZE_PROBE.POOL_NOT_INITIALIZED": _definition("DEX.SIZE_PROBE.POOL_NOT_INITIALIZED", ErrorCode.DEX_SIZE_POOL_NOT_INITIALIZED, "DEX", "size_probe", "probe_quote", manual_review=True, disposition="REFRESH_POOL_SCOPE", action="刷新池作用域后重新探测金额"),
    "DEX.SIZE_PROBE.NOT_PROFITABLE_AFTER_COSTS": _definition("DEX.SIZE_PROBE.NOT_PROFITABLE_AFTER_COSTS", ErrorCode.DEX_SIZE_NOT_PROFITABLE, "DEX", "size_probe", "profit_gate", disposition="CLOSE_NO_OPPORTUNITY", action="无利润正常关闭，下一轮扫描生成新报价"),
    "DEX.GATE.NOT_READY": _definition("DEX.GATE.NOT_READY", ErrorCode.DEX_GATE_NOT_READY, "DEX", "preflight", "execution_gate", manual_review=True, disposition="STOP_BROADCAST_SCOPE", action="广播门未就绪，只读继续且不提交"),
    "DEX.ROUTE.INVALID": _definition("DEX.ROUTE.INVALID", ErrorCode.DEX_ROUTE_INVALID, "DEX", "route", "route_validation", disposition="CLOSE_AND_QUARANTINE", action="拒绝当前路由并隔离，不重试原请求"),
    "DEX.RPC.FAILED": _definition("DEX.RPC.FAILED", ErrorCode.DEX_RPC_FAILED, "DEX", "rpc", "rpc_call", retryable=True, max_attempts=3, backoff_seconds=0.5, disposition="RETRY_READONLY", action="只读请求可换 RPC 重试；广播结果未知时先核链"),
    "DEX.RPC.TIMEOUT": _definition("DEX.RPC.TIMEOUT", ErrorCode.DEX_RPC_TIMEOUT, "DEX", "rpc", "rpc_timeout", retryable=True, max_attempts=3, backoff_seconds=1.0, disposition="RETRY_READONLY", action="按 RPC 错误预算和退避只读重试"),
    "DEX.RPC.ALREADY_KNOWN": _definition("DEX.RPC.ALREADY_KNOWN", ErrorCode.DEX_RPC_ALREADY_KNOWN, "DEX", "rpc", "broadcast", disposition="VERIFY_CHAIN_STATE", action="raw tx 已进入节点，查回执与 pending，不重新签名"),
    "DEX.RPC.NONCE_TOO_LOW": _definition("DEX.RPC.NONCE_TOO_LOW", ErrorCode.DEX_RPC_NONCE_TOO_LOW, "DEX", "rpc", "broadcast", disposition="VERIFY_CHAIN_STATE", action="查询该 nonce 的交易和回执，不得抬 gas 重发"),
    "DEX.RPC.REPLACEMENT_UNDERPRICED": _definition("DEX.RPC.REPLACEMENT_UNDERPRICED", ErrorCode.DEX_RPC_REPLACEMENT_UNDERPRICED, "DEX", "rpc", "broadcast", disposition="VERIFY_CHAIN_STATE", action="同 nonce 冲突，核对后自动隔离，不替换不重签"),
    "DEX.RPC.TRANSPORT_TIMEOUT": _definition("DEX.RPC.TRANSPORT_TIMEOUT", ErrorCode.DEX_RPC_TRANSPORT_TIMEOUT, "DEX", "rpc", "broadcast", disposition="VERIFY_CHAIN_STATE", action="结果未知先核链，再按 confirmed/pending/absent 迁移"),
    "DEX.RPC.BROADCAST_NOT_ACCEPTED": _definition("DEX.RPC.BROADCAST_NOT_ACCEPTED", ErrorCode.DEX_RPC_BROADCAST_NOT_ACCEPTED, "DEX", "rpc", "broadcast", disposition="VERIFY_CHAIN_STATE", action="保存 raw 指纹与错误后核链，不直接重发"),
    "DEX.RPC.RECEIPT_FAILED": _definition("DEX.RPC.RECEIPT_FAILED", ErrorCode.DEX_RPC_RECEIPT_FAILED, "DEX", "rpc", "receipt", disposition="VERIFY_CHAIN_STATE", action="跨 RPC 只读确认，不能确认时自动隔离"),
    "DEX.INTERNAL": _definition("DEX.INTERNAL", ErrorCode.DEX_INTERNAL, "DEX", "system", "internal", manual_review=True, disposition="DESIGN_BLOCKED", action="fail-closed 并审计完整内部错误"),
    "CONTRACT.DEX.VALIDATION.REQUEST_INVALID": _contract_definition("CONTRACT.DEX.VALIDATION.REQUEST_INVALID", "validation", "request_validate", disposition="CLOSE_AND_QUARANTINE", action="拒绝当前请求，保留输入快照"),
    "CONTRACT.DEX.VALIDATION.FLASH_SOURCE_MISSING": _contract_definition("CONTRACT.DEX.VALIDATION.FLASH_SOURCE_MISSING", "validation", "flash_source_resolve", disposition="STOP_BROADCAST_SCOPE", action="配置缺失，阻断广播并记录设计配置事件"),
    "CONTRACT.DEX.VALIDATION.ROUTE_INVALID": _contract_definition("CONTRACT.DEX.VALIDATION.ROUTE_INVALID", "validation", "route_validate", disposition="CLOSE_AND_QUARANTINE", action="拒绝当前路由并隔离，不重试原请求"),
    "CONTRACT.DEX.QUOTE.ADAPTER_MISSING": _contract_definition("CONTRACT.DEX.QUOTE.ADAPTER_MISSING", "quote", "adapter_resolve", disposition="STOP_BROADCAST_SCOPE", action="适配器配置缺失，阻断广播并记录设计事件"),
    "CONTRACT.DEX.QUOTE.HOP_QUOTE.POOL_NOT_INITIALIZED": _contract_definition("CONTRACT.DEX.QUOTE.HOP_QUOTE.POOL_NOT_INITIALIZED", "quote", "hop_quote", 1, manual_review=True, disposition="REFRESH_POOL_SCOPE", action="标记池未初始化并刷新索引"),
    "CONTRACT.DEX.QUOTE.HOP_QUOTE.NOT_ENOUGH_LIQUIDITY": _contract_definition("CONTRACT.DEX.QUOTE.HOP_QUOTE.NOT_ENOUGH_LIQUIDITY", "quote", "hop_quote", 2, manual_review=True, disposition="REFRESH_POOL_SCOPE", action="排除池/方向并刷新流动性"),
    "CONTRACT.DEX.QUOTE.HOP_QUOTE.QUOTER_FAILED": _contract_definition("CONTRACT.DEX.QUOTE.HOP_QUOTE.QUOTER_FAILED", "quote", "hop_quote", 3, retryable=True, max_attempts=2, backoff_seconds=1.0, disposition="RETRY_READONLY", action="只读重试 Quoter，禁止进入广播"),
    "CONTRACT.DEX.EXECUTION.ROUTER_CALL_FAILED": _contract_definition("CONTRACT.DEX.EXECUTION.ROUTER_CALL_FAILED", "execution", "swap_execute", manual_review=True, disposition="CLOSE_AND_QUARANTINE", action="当前路由失败，重新报价前不重发"),
    "CONTRACT.DEX.EXECUTION.APPROVE_FAILED": _contract_definition("CONTRACT.DEX.EXECUTION.APPROVE_FAILED", "execution", "approve", retryable=True, max_attempts=2, backoff_seconds=0.5, disposition="CLOSE_AND_QUARANTINE", action="刷新授权后仍失败则隔离路由"),
    "CONTRACT.DEX.EXECUTION.TRANSFER_FAILED": _contract_definition("CONTRACT.DEX.EXECUTION.TRANSFER_FAILED", "execution", "transfer", manual_review=True, disposition="CLOSE_AND_QUARANTINE", action="按余额/授权策略刷新后仍失败则隔离，不重复广播同一 raw"),
    "CONTRACT.DEX.EXECUTION.REENTRANCY": _contract_definition("CONTRACT.DEX.EXECUTION.REENTRANCY", "execution", "reentrancy_guard", disposition="STOP_BROADCAST_SCOPE", action="安全错误，自动关闭广播，不重试"),
    "CONTRACT.DEX.EXECUTION.NOT_OWNER": _contract_definition("CONTRACT.DEX.EXECUTION.NOT_OWNER", "execution", "owner_guard", disposition="STOP_BROADCAST_SCOPE", action="调用身份错误，自动阻断当前 scope"),
    "CONTRACT.DEX.EXECUTION.PAUSED": _contract_definition("CONTRACT.DEX.EXECUTION.PAUSED", "execution", "paused_guard", disposition="STOP_BROADCAST_SCOPE", action="合约广播关闭，只读保留"),
    "CONTRACT.DEX.CALLBACK.BAD_FLASH_CALLBACK": _contract_definition("CONTRACT.DEX.CALLBACK.BAD_FLASH_CALLBACK", "callback", "callback_verify", disposition="CLOSE_AND_QUARANTINE", action="隔离路由与合约配置，不重试同一交易"),
    "CONTRACT.DEX.CALLBACK.NOT_POOL": _contract_definition("CONTRACT.DEX.CALLBACK.NOT_POOL", "callback", "pool_guard", disposition="CLOSE_AND_QUARANTINE", action="隔离路由与合约配置，不重试同一交易"),
    "CONTRACT.DEX.CALLBACK.BAD_INITIATOR": _contract_definition("CONTRACT.DEX.CALLBACK.BAD_INITIATOR", "callback", "initiator_guard", disposition="STOP_BROADCAST_SCOPE", action="调用身份错误，自动阻断当前 scope"),
    "CONTRACT.DEX.REPAYMENT.INSUFFICIENT": _contract_definition("CONTRACT.DEX.REPAYMENT.INSUFFICIENT", "repayment", "repayment_check", manual_review=True, disposition="STOP_BROADCAST_SCOPE", action="资金安全错误，自动关闭广播进入设计修正"),
    "CONTRACT.DEX.INTERNAL": _contract_definition("CONTRACT.DEX.INTERNAL", "internal", "internal", manual_review=True, disposition="DESIGN_BLOCKED", action="fail-closed、隔离并记录完整错误"),
}

LEGACY_ERROR_KEYS: dict[str, str] = {
    "validation": "CORE.VALIDATION.INVALID",
    "scope_mismatch": "CORE.SCOPE.MISMATCH",
    "chain_scope_required": "CORE.SCOPE.MISMATCH",
    "invalid_chain_flow_handler": "CORE.FLOW.MISSING",
    "account_not_liquidatable": "BOT.LIQUIDATION.ACCOUNT_NOT_LIQUIDATABLE",
    "no_liquidation_candidate": "BOT.LIQUIDATION.NO_EXECUTABLE_CANDIDATE",
    "quote_failed": "BOT.LIQUIDATION.QUOTE_FAILED",
    "static_call_failed": "BOT.LIQUIDATION.STATIC_CALL_FAILED",
    "approval_failed": "BOT.EXECUTION.APPROVAL_FAILED",
    "submission_reverted": "BOT.EXECUTION.SUBMISSION_REVERTED",
    "live_quote_failed": "DEX.QUOTE.LIVE_QUOTE_FAILED",
    "pool_not_initialized": "DEX.ONCHAIN.HOP_QUOTE.POOL_NOT_INITIALIZED",
    "not_enough_liquidity": "DEX.ONCHAIN.HOP_QUOTE.NOT_ENOUGH_LIQUIDITY",
    "not_profitable_after_costs": "DEX.SIZE_PROBE.NOT_PROFITABLE_AFTER_COSTS",
    "route_unprofitable": "DEX.SIZE_PROBE.NOT_PROFITABLE_AFTER_COSTS",
    "execution_reverted": "BOT.EXECUTION.SUBMISSION_REVERTED",
    "rpc": "DEX.RPC.FAILED",
    "rpc_timeout": "DEX.RPC.TIMEOUT",
    "risk_blocked": "DEX.GATE.NOT_READY",
    "already_known": "DEX.RPC.ALREADY_KNOWN",
    "nonce_too_low": "DEX.RPC.NONCE_TOO_LOW",
    "replacement_tx_underpriced": "DEX.RPC.REPLACEMENT_UNDERPRICED",
    "broadcast_transport_timeout": "DEX.RPC.TRANSPORT_TIMEOUT",
    "broadcast_timeout": "DEX.RPC.TRANSPORT_TIMEOUT",
    "broadcast_not_accepted": "DEX.RPC.BROADCAST_NOT_ACCEPTED",
    "receipt_failed": "DEX.RPC.RECEIPT_FAILED",
}


DISPOSITION_BY_NAME: dict[str, str] = {
    # 3.1 报价、收益和流动性
    "profit_too_low": "CLOSE_NO_OPPORTUNITY",
    "not_profitable_after_costs": "CLOSE_NO_OPPORTUNITY",
    "route_unprofitable": "CLOSE_NO_OPPORTUNITY",
    "slippage_exceeded": "REQUOTE_AND_RETRY",
    "not_enough_liquidity": "REFRESH_POOL_SCOPE",
    "pool_not_initialized": "REFRESH_POOL_SCOPE",
    "live_quote_failed": "RETRY_READONLY",
    "quoter_failed": "RETRY_READONLY",
    "size_probe": "REQUOTE_AND_RETRY",
    # 3.2 RPC、提交和回执
    "connection_refused": "RETRY_READONLY",
    "connection_reset": "RETRY_READONLY",
    "rpc_timeout": "RETRY_READONLY",
    "http_429": "RETRY_READONLY",
    "http_502": "RETRY_READONLY",
    "http_503": "RETRY_READONLY",
    "http_504": "RETRY_READONLY",
    "broadcast_transport_timeout": "VERIFY_CHAIN_STATE",
    "already_known": "VERIFY_CHAIN_STATE",
    "nonce_too_low": "VERIFY_CHAIN_STATE",
    "replacement_tx_underpriced": "VERIFY_CHAIN_STATE",
    "receipt_failed": "VERIFY_CHAIN_STATE",
    "broadcast_not_accepted": "VERIFY_CHAIN_STATE",
    # 3.3 合约和策略错误
    "execution_reverted": "DESIGN_BLOCKED",
    "paused": "STOP_BROADCAST_SCOPE",
    "not_owner": "STOP_BROADCAST_SCOPE",
    "bad_initiator": "STOP_BROADCAST_SCOPE",
    "adapter_missing": "STOP_BROADCAST_SCOPE",
    "flash_source_missing": "STOP_BROADCAST_SCOPE",
    "bad_flash_callback": "CLOSE_AND_QUARANTINE",
    "not_pool": "CLOSE_AND_QUARANTINE",
    "router_call_failed": "CLOSE_AND_QUARANTINE",
    "transfer_failed": "CLOSE_AND_QUARANTINE",
    "insufficient_balance": "CLOSE_AND_QUARANTINE",
    "insufficient_allowance": "CLOSE_AND_QUARANTINE",
    "bad_flash_repayment": "STOP_BROADCAST_SCOPE",
    "repayment_insufficient": "STOP_BROADCAST_SCOPE",
    "reentrancy": "STOP_BROADCAST_SCOPE",
    "out_of_gas": "CLOSE_AND_QUARANTINE",
    "deadline_expired": "REQUOTE_AND_RETRY",
    # 3.4 校验、作用域和内部错误
    "invalid_request": "CLOSE_AND_QUARANTINE",
    "route_invalid": "CLOSE_AND_QUARANTINE",
    "scope_mismatch": "DESIGN_BLOCKED",
    "chain_scope_required": "DESIGN_BLOCKED",
    "broadcast_disabled": "STOP_BROADCAST_SCOPE",
    "execution_queued": "DEFER_QUEUE",
    "broadcast_deferred": "DEFER_QUEUE",
    "internal": "DESIGN_BLOCKED",
    "unknown": "DESIGN_BLOCKED",
}


def resolve_error_definition(error_key: str | None) -> ErrorDefinition:
    key = str(error_key or "").strip()
    if key in ERROR_REGISTRY:
        return ERROR_REGISTRY[key]
    mapped = LEGACY_ERROR_KEYS.get(key.lower(), "CORE.INTERNAL")
    return ERROR_REGISTRY[mapped]


def resolve_disposition(value: str | None, *, fallback: str = "DESIGN_BLOCKED") -> str:
    """Resolve a structured automatic disposition for an error key/name.

    Resolution order: registry key -> legacy alias -> strategy matrix name.
    Anything unknown fails closed to ``DESIGN_BLOCKED``; no runtime path may
    block on human review.
    """
    text = str(value or "").strip().lower()
    if not text:
        return fallback
    upper = text.upper()
    if upper in ERROR_REGISTRY:
        return str(ERROR_REGISTRY[upper].disposition or fallback)
    if text in LEGACY_ERROR_KEYS:
        return str(resolve_error_definition(text).disposition or fallback)
    return str(DISPOSITION_BY_NAME.get(text, fallback))


def contract_dex_error_codes() -> dict[str, int]:
    return {
        key: definition.code
        for key, definition in ERROR_REGISTRY.items()
        if definition.domain == "CONTRACT.DEX"
    }


__all__ = [
    "CONTRACT_DEX_MODULE_OFFSET",
    "CONTRACT_DEX_FLOW_CODES",
    "CONTRACT_DEX_NODE_CODES",
    "ERROR_REGISTRY",
    "ErrorCode",
    "ErrorDefinition",
    "LEGACY_ERROR_KEYS",
    "contract_dex_error_codes",
    "DISPOSITIONS",
    "DISPOSITION_BY_NAME",
    "resolve_error_definition",
    "resolve_disposition",
]
