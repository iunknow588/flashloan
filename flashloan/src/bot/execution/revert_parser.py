from __future__ import annotations

import re
from typing import Any

from core.error_registry import resolve_error_definition


REVERT_PATTERNS: list[tuple[str, str]] = [
    (r"ProfitTooLow\(", "profit_too_low"),
    (r"InvalidRequest\(", "invalid_request"),
    (r"InvalidRequest", "invalid_request"),
    (r"NotPool\(", "not_pool"),
    (r"NotPool", "not_pool"),
    (r"BadInitiator\(", "bad_initiator"),
    (r"BadInitiator", "bad_initiator"),
    (r"NotOwner\(", "not_owner"),
    (r"NotOwner", "not_owner"),
    (r"Paused\(", "paused"),
    (r"Paused", "paused"),
    (r"LiquidationNotConfigured", "liquidation_not_configured"),
    (r"liquidation not allowed", "liquidation_not_allowed"),
    (r"close.?factor", "close_factor_exceeded"),
    (r"AmountOutTooLow", "slippage_exceeded"),
    (r"amountoutmin", "slippage_exceeded"),
    (r"INSUFFICIENT_OUTPUT_AMOUNT", "slippage_exceeded"),
    (r"TRANSFER_FAILED", "transfer_failed"),
    (r"insufficient allowance", "insufficient_allowance"),
    (r"ERC20: transfer amount exceeds balance", "insufficient_balance"),
    (r"DeadlineExpired", "deadline_expired"),
    (r"RateNotSet", "rate_not_set"),
    (r"execution reverted", "execution_reverted"),
    (r"out of gas", "out_of_gas"),
    (r"nonce too low", "nonce_too_low"),
    (r"replacement transaction", "replacement_tx_underpriced"),
]


REVERT_CATEGORY_LABELS: dict[str, str] = {
    "profit_too_low": "利润不足（swap后收益低于minProfitAmount）",
    "invalid_request": "请求参数无效（地址/金额/deadline不合法）",
    "not_pool": "回调来源校验失败（非Pool地址调用executeOperation）",
    "bad_initiator": "发起者校验失败（非合约自身发起回调）",
    "not_owner": "权限校验失败（非owner调用）",
    "paused": "合约已暂停",
    "liquidation_not_configured": "清算配置未找到（账户可能已恢复或参数错误）",
    "liquidation_not_allowed": "清算不被允许（HF已恢复/close factor超限/dust残留）",
    "close_factor_exceeded": "清算金额超过close factor限制",
    "slippage_exceeded": "DEX滑点超限（流动性不足或价格变动）",
    "transfer_failed": "Token转账失败",
    "insufficient_allowance": "授权额度不足",
    "insufficient_balance": "余额不足",
    "deadline_expired": "交易deadline已过期",
    "rate_not_set": "DEX汇率未配置",
    "execution_reverted": "执行回滚（通用原因）",
    "out_of_gas": "Gas不足",
    "nonce_too_low": "nonce过低",
    "replacement_tx_underpriced": "替换交易Gas不足",
}


REVERT_ERROR_KEYS: dict[str, str] = {
    "profit_too_low": "BOT.EXECUTION.SUBMISSION_REVERTED",
    "invalid_request": "CORE.VALIDATION.INVALID",
    "not_pool": "BOT.LIQUIDATION.STATIC_CALL_FAILED",
    "bad_initiator": "BOT.LIQUIDATION.STATIC_CALL_FAILED",
    "not_owner": "BOT.LIQUIDATION.STATIC_CALL_FAILED",
    "paused": "BOT.EXECUTION.BLOCKED",
    "liquidation_not_configured": "BOT.LIQUIDATION.STATIC_CALL_FAILED",
    "liquidation_not_allowed": "BOT.LIQUIDATION.STATIC_CALL_FAILED",
    "close_factor_exceeded": "BOT.LIQUIDATION.STATIC_CALL_FAILED",
    "slippage_exceeded": "BOT.EXECUTION.SUBMISSION_REVERTED",
    "transfer_failed": "BOT.EXECUTION.SUBMISSION_REVERTED",
    "insufficient_allowance": "BOT.EXECUTION.APPROVAL_FAILED",
    "insufficient_balance": "BOT.EXECUTION.SUBMISSION_REVERTED",
    "deadline_expired": "BOT.EXECUTION.SUBMISSION_REVERTED",
    "rate_not_set": "BOT.LIQUIDATION.QUOTE_FAILED",
    "execution_reverted": "BOT.EXECUTION.SUBMISSION_REVERTED",
    "out_of_gas": "BOT.EXECUTION.SUBMISSION_REVERTED",
    "nonce_too_low": "BOT.EXECUTION.SUBMISSION_REVERTED",
    "replacement_tx_underpriced": "BOT.EXECUTION.SUBMISSION_REVERTED",
}


def parse_revert_reason(error_text: str) -> dict[str, Any]:
    """Parse a revert exception message into structured categories.

    Returns a dict with:
      - category: str (e.g. "profit_too_low", "slippage_exceeded")
      - label: str (human-readable Chinese description)
      - raw: str (original error text)
      - confidence: str ("high" if pattern matched, "low" otherwise)
    """
    if not error_text:
        return {
            "category": "unknown",
            "module": "BOT.revert_parser",
            "code": int(resolve_error_definition("BOT.INTERNAL").code),
            "message": "未知错误",
            "label": "未知错误",
            "raw": "",
            "confidence": "low",
        }

    text = str(error_text)
    for pattern, category in REVERT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            label = REVERT_CATEGORY_LABELS.get(category, category)
            error_key = REVERT_ERROR_KEYS.get(category, "BOT.EXECUTION.SUBMISSION_REVERTED")
            return {
                "category": category,
                "error_key": error_key,
                "module": "BOT.revert_parser",
                "code": int(resolve_error_definition(error_key).code),
                "message": label,
                "label": label,
                "raw": text[:500],
                "confidence": "high",
            }

    error_key = "BOT.INTERNAL"
    return {
        "category": "unknown",
        "error_key": error_key,
        "module": "BOT.revert_parser",
        "code": int(resolve_error_definition(error_key).code),
        "message": f"未分类错误: {text[:100]}",
        "label": f"未分类错误: {text[:100]}",
        "raw": text[:500],
        "confidence": "low",
    }


def build_failure_record(
    parsed: dict[str, Any],
    *,
    payload: dict[str, Any] | None = None,
    account: str | None = None,
    block_number: int | None = None,
) -> dict[str, Any]:
    """Build a record suitable for record_liquidation_failure_sample."""
    request = (payload or {}).get("request") or {}
    return {
        "account": account or str(request.get("user") or ""),
        "block_number": block_number,
        "collateral_asset": str(request.get("collateralAsset") or ""),
        "debt_asset": str(request.get("debtAsset") or ""),
        "failure_type": f"static_call_{parsed['category']}",
        "failure_reason": parsed.get("label") or parsed.get("raw", ""),
        "error_key": parsed.get("error_key") or "BOT.INTERNAL",
        "module": parsed.get("module") or "BOT.revert_parser",
        "code": parsed.get("code"),
        "message": parsed.get("message") or parsed.get("label") or parsed.get("raw", ""),
        "payload": payload,
        "source": "static_call_preflight",
    }
