"""Read-only Morpho Blue provider probe for Robinhood mainnet.

This module never broadcasts transactions and never writes business tables. It
records chain identity, contract code, ABI selector presence and, when market
ids and accounts are supplied, reads market aggregates and user positions
through view calls.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

from eth_utils import event_signature_to_log_topic, function_signature_to_4byte_selector
from web3 import Web3

from dex.intent_trade.chains.robinhood import config as robinhood
from dex.intent_trade.chains.robinhood.chain_health import (
    JsonRpcTransport,
    _has_code,
    _hex_int,
    _rpc_call,
)


MORPHO_BLUE_CORE = robinhood.MORPHO_BLUE_CORE_ADDRESS
CORE_SELECTOR_PROBES = (
    "market(bytes32)",
    "position(bytes32,address)",
    "flashLoan(address,uint256,bytes)",
    "owner()",
)
REQUIRED_READONLY_PROBES = {"market(bytes32)", "position(bytes32,address)"}
REQUIRED_FLASHLOAN_PROBES = {"flashLoan(address,uint256,bytes)"}
KNOWN_EVENT_SIGNATURES = (
    "FlashLoan(address,address,uint256)",
    "AccrueInterest(bytes32,uint256,uint256,uint256)",
    "Supply(bytes32,address,address,uint256,uint256)",
    "SupplyCollateral(bytes32,address,address,uint256)",
    "Borrow(bytes32,address,address,address,uint256,uint256)",
)
EVENT_TOPIC_BY_SIGNATURE = {
    signature: "0x" + event_signature_to_log_topic(signature).hex()
    for signature in KNOWN_EVENT_SIGNATURES
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _word(data: str, index: int) -> str:
    raw = str(data or "").removeprefix("0x")
    start = index * 64
    return raw[start : start + 64].rjust(64, "0")


def _word_int(data: str, index: int) -> int:
    return int(_word(data, index) or "0", 16)


def _address_word(address: str) -> str:
    text = str(address or "").strip()
    if not text.startswith("0x") or len(text) != 42:
        raise ValueError(f"invalid address: {address}")
    return text[2:].lower().rjust(64, "0")


def _selector_hex(signature: str) -> str:
    return "0x" + function_signature_to_4byte_selector(signature).hex()


def _rpc_call_retry(
    rpc_url: str,
    method: str,
    params: list[Any] | None = None,
    *,
    timeout_seconds: float,
    transport: JsonRpcTransport | None,
    attempts: int = 3,
) -> Any:
    last_error: Exception | None = None
    for _ in range(max(1, int(attempts))):
        try:
            return _rpc_call(
                rpc_url,
                method,
                params,
                timeout_seconds=timeout_seconds,
                transport=transport,
            )
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"{method} failed after {attempts} attempts: {last_error}")


def _block_int(value: Any) -> int:
    raw = str(value or "0x0")
    return int(raw, 16) if raw.startswith(("0x", "0X")) else int(raw or "0")


def scan_recent_morpho_blue_events(
    rpc_url: str,
    *,
    lookback_blocks: int = 10_000,
    from_block: int | None = None,
    to_block: int | None = None,
    timeout_seconds: float = 12,
    transport: JsonRpcTransport | None = None,
) -> dict[str, Any]:
    """Read recent Morpho-compatible provider logs without touching state."""

    try:
        latest = _block_int(
            _rpc_call_retry(
                rpc_url,
                "eth_blockNumber",
                timeout_seconds=timeout_seconds,
                transport=transport,
                attempts=3,
            )
        )
        end_block = int(to_block if to_block is not None else latest)
        start_block = int(
            from_block if from_block is not None else max(0, end_block - max(0, int(lookback_blocks)) + 1)
        )
        raw_logs = _rpc_call_retry(
            rpc_url,
            "eth_getLogs",
            [
                {
                    "address": MORPHO_BLUE_CORE,
                    "fromBlock": hex(start_block),
                    "toBlock": hex(end_block),
                }
            ],
            timeout_seconds=timeout_seconds,
            transport=transport,
            attempts=3,
        )
        logs = [log for log in (raw_logs or []) if isinstance(log, dict)]
        topic_counts = Counter(
            str(next(iter(log.get("topics") or []), None) or "") for log in logs
        )
        signature_counts = {signature: 0 for signature in KNOWN_EVENT_SIGNATURES}
        for log in logs:
            topic = str(next(iter(log.get("topics") or []), None) or "")
            for signature, event_topic in EVENT_TOPIC_BY_SIGNATURE.items():
                if topic.lower() == event_topic.lower():
                    signature_counts[signature] += 1
        samples = []
        for log in logs[:3]:
            samples.append(
                {
                    "blockNumber": _block_int(log.get("blockNumber")),
                    "transactionHash": log.get("transactionHash"),
                    "logIndex": _block_int(log.get("logIndex")),
                    "topics": log.get("topics") or [],
                    "data": str(log.get("data") or "")[:520],
                }
            )
        return {
            "ok": True,
            "blockRange": {"fromBlock": start_block, "toBlock": end_block},
            "logCount": len(logs),
            "uniqueTopicCount": len(topic_counts),
            "eventCountsBySignature": signature_counts,
            "topicCounts": [{"topic": topic, "count": count} for topic, count in topic_counts.most_common()],
            "sampleLogs": samples,
            "observedActivity": bool(logs),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc)[:240],
            "logCount": 0,
            "eventCountsBySignature": {signature: 0 for signature in KNOWN_EVENT_SIGNATURES},
        }


def _selector_probes(code_hex: str) -> list[dict[str, Any]]:
    body = str(code_hex or "").removeprefix("0x").lower()
    probes = []
    for signature in CORE_SELECTOR_PROBES:
        selector = _selector_hex(signature)
        probes.append(
            {
                "signature": signature,
                "selector": selector,
                "presentInBytecode": "63" + selector.removeprefix("0x") in body,
            }
        )
    return probes


def _code_digest(code_hex: str) -> str:
    code = str(code_hex or "")
    if code.lower() in {"", "0x", "0x0"}:
        return "0x"
    return Web3.keccak(hexstr=code).hex()


def _market_id_word(market_id: str) -> str:
    raw = str(market_id or "").strip()
    if not raw.startswith("0x") or len(raw) != 66:
        raise ValueError("market_id must be a 32-byte hex id")
    return raw[2:].lower()


def read_morpho_blue_market(
    rpc_url: str,
    market_id: str,
    *,
    timeout_seconds: float = 12,
    transport: JsonRpcTransport | None = None,
) -> dict[str, Any]:
    """Read Morpho Blue market aggregates with the verified view selector."""

    try:
        data = _rpc_call_retry(
            rpc_url,
            "eth_call",
            [{"to": MORPHO_BLUE_CORE, "data": _selector_hex("market(bytes32)") + _market_id_word(market_id)}, "latest"],
            timeout_seconds=timeout_seconds,
            transport=transport,
            attempts=3,
        )
        return {
            "ok": True,
            "marketId": market_id,
            "totalSupplyAssets": str(_word_int(data, 0)),
            "totalSupplyShares": str(_word_int(data, 1)),
            "totalBorrowAssets": str(_word_int(data, 2)),
            "totalBorrowShares": str(_word_int(data, 3)),
            "lastUpdate": str(_word_int(data, 4)),
            "fee": str(_word_int(data, 5)),
            "hasBorrowLiquidity": _word_int(data, 2) > 0,
            "hasSupplyLiquidity": _word_int(data, 0) > 0,
        }
    except Exception as exc:
        return {
            "ok": False,
            "marketId": market_id,
            "error": str(exc)[:240],
        }


def read_morpho_blue_position(
    rpc_url: str,
    market_id: str,
    account: str,
    *,
    timeout_seconds: float = 12,
    transport: JsonRpcTransport | None = None,
) -> dict[str, Any]:
    """Read one Morpho Blue borrower position with the verified view selector."""

    try:
        data = _rpc_call_retry(
            rpc_url,
            "eth_call",
            [
                {
                    "to": MORPHO_BLUE_CORE,
                    "data": _selector_hex("position(bytes32,address)")
                    + _market_id_word(market_id)
                    + _address_word(account),
                },
                "latest",
            ],
            timeout_seconds=timeout_seconds,
            transport=transport,
            attempts=3,
        )
        supply_shares = _word_int(data, 0)
        borrow_shares = _word_int(data, 1)
        return {
            "ok": True,
            "marketId": market_id,
            "account": account,
            "supplyShares": str(supply_shares),
            "borrowShares": str(borrow_shares),
            "hasSupply": supply_shares > 0,
            "hasBorrow": borrow_shares > 0,
            "healthFactorUsable": False,
            "reasonHealthFactorUnavailable": "oracle prices and Morpho market parameters are not yet verified",
        }
    except Exception as exc:
        return {
            "ok": False,
            "marketId": market_id,
            "account": account,
            "error": str(exc)[:240],
        }


def summarize_robinhood_morpho_blue_readonly(
    *,
    rpc_url: str | None = None,
    timeout_seconds: float = 12,
    transport: JsonRpcTransport | None = None,
    market_ids: Iterable[str] = (),
    accounts: Iterable[str] = (),
    recent_blocks: int = 0,
) -> dict[str, Any]:
    urls = robinhood.rpc_urls_from_env()
    selected_rpc = str(rpc_url or (urls[0] if urls else "")).strip()
    checks: list[dict[str, Any]] = []
    blockers: list[str] = ["robinhood_morpho_blue_core_source_unverified"]
    chain_id = 0
    block_number = 0
    code_hex = ""

    if not selected_rpc:
        checks.append({"name": "rpc_url", "ok": False, "message": "Robinhood mainnet RPC URL is missing"})
        blockers.append("robinhood_morpho_blue_rpc_missing")
    else:
        try:
            chain_id = _hex_int(
                _rpc_call_retry(
                    selected_rpc,
                    "eth_chainId",
                    timeout_seconds=timeout_seconds,
                    transport=transport,
                    attempts=3,
                )
            )
            checks.append(
                {
                    "name": "eth_chainId",
                    "ok": chain_id == robinhood.CHAIN_ID,
                    "value": chain_id,
                    "message": f"chain id is {chain_id}, expected {robinhood.CHAIN_ID}",
                }
            )
            if chain_id != robinhood.CHAIN_ID:
                blockers.append("robinhood_morpho_blue_wrong_chain")
        except Exception as exc:
            checks.append({"name": "eth_chainId", "ok": False, "error": str(exc)})
            blockers.append("robinhood_morpho_blue_rpc_chain_check_failed")

        try:
            block_number = _hex_int(
                _rpc_call_retry(
                    selected_rpc,
                    "eth_blockNumber",
                    timeout_seconds=timeout_seconds,
                    transport=transport,
                    attempts=3,
                )
            )
            checks.append(
                {
                    "name": "eth_blockNumber",
                    "ok": block_number > 0,
                    "value": block_number,
                    "message": f"latest block is {block_number}",
                }
            )
        except Exception as exc:
            checks.append({"name": "eth_blockNumber", "ok": False, "error": str(exc)})
            blockers.append("robinhood_morpho_blue_rpc_block_check_failed")

        try:
            code_hex = _rpc_call_retry(
                selected_rpc,
                "eth_getCode",
                [MORPHO_BLUE_CORE, "latest"],
                timeout_seconds=timeout_seconds,
                transport=transport,
                attempts=3,
            )
        except Exception as exc:
            checks.append({"name": "morpho_blue_core_code", "ok": False, "error": str(exc)})
            blockers.append("robinhood_morpho_blue_core_code_read_failed")

    has_code = _has_code(code_hex)
    selectors = _selector_probes(code_hex) if has_code else []
    probe_by_signature = {item["signature"]: item for item in selectors}
    readonly_probes_present = all(
        probe_by_signature.get(signature, {}).get("presentInBytecode")
        for signature in REQUIRED_READONLY_PROBES
    )
    flashloan_probe_present = bool(
        probe_by_signature.get("flashLoan(address,uint256,bytes)", {}).get("presentInBytecode")
    )
    checks.append(
        {
            "name": "morpho_blue_core_code",
            "ok": has_code,
            "address": MORPHO_BLUE_CORE,
            "codeBytes": max(0, (len(code_hex) - 2) // 2) if has_code else 0,
            "codeHash": _code_digest(code_hex) if has_code else "0x",
            "message": "contract code is present" if has_code else "contract code is missing",
        }
    )
    checks.append(
        {
            "name": "morpho_blue_required_readonly_selectors",
            "ok": readonly_probes_present,
            "required": sorted(REQUIRED_READONLY_PROBES),
            "probes": selectors,
        }
    )
    checks.append(
        {
            "name": "morpho_blue_flashloan_selector",
            "ok": flashloan_probe_present,
            "probe": probe_by_signature.get("flashLoan(address,uint256,bytes)"),
        }
    )

    if not has_code:
        blockers.append("robinhood_morpho_blue_core_code_missing")
    if not readonly_probes_present:
        blockers.append("robinhood_morpho_blue_readonly_abi_signature_requires_review")
    if not flashloan_probe_present:
        blockers.append("robinhood_morpho_blue_flashloan_selector_unverified")
    blockers.extend(
        [
            "robinhood_morpho_blue_market_events_unverified",
            "robinhood_morpho_blue_oracle_verification_pending",
            "robinhood_morpho_blue_health_factor_unavailable_without_oracle_prices",
            "robinhood_morpho_blue_account_pool_empty",
        ]
    )

    markets: list[dict[str, Any]] = []
    positions: list[dict[str, Any]] = []
    account_list = list(dict.fromkeys(str(account).strip() for account in accounts if str(account).strip()))
    market_list = list(dict.fromkeys(str(market).strip() for market in market_ids if str(market).strip()))
    if selected_rpc and has_code and readonly_probes_present:
        for market_id in market_list:
            market = read_morpho_blue_market(
                selected_rpc,
                market_id,
                timeout_seconds=timeout_seconds,
                transport=transport,
            )
            markets.append(market)
            if market.get("ok"):
                for account in account_list:
                    positions.append(
                        read_morpho_blue_position(
                            selected_rpc,
                            market_id,
                            account,
                            timeout_seconds=timeout_seconds,
                            transport=transport,
                        )
                    )
        if account_list:
            blockers.remove("robinhood_morpho_blue_account_pool_empty")

    recent_activity: dict[str, Any] = {}
    if selected_rpc and recent_blocks > 0:
        recent_activity = scan_recent_morpho_blue_events(
            selected_rpc,
            lookback_blocks=max(0, int(recent_blocks)),
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    rpc_ok = bool(checks) and all(bool(item.get("ok")) for item in checks)
    return {
        "schemaVersion": "robinhood-morpho-blue-readonly-v1",
        "checkedAt": _utc_now_iso(),
        "network": robinhood.NETWORK,
        "chainId": chain_id or robinhood.CHAIN_ID,
        "rpcUrl": selected_rpc,
        "rpcOk": rpc_ok,
        "checks": checks,
        "morphoBlueCore": MORPHO_BLUE_CORE,
        "addressSource": "chains/robinhood/chain.json without independent source URL evidence",
        "blockNumber": block_number,
        "code": {
            "hasCode": has_code,
            "codeBytes": max(0, (len(code_hex) - 2) // 2) if has_code else 0,
            "codeHash": _code_digest(code_hex) if has_code else "0x",
        },
        "selectorProbes": selectors,
        "requiredReadonlySelectorProbesPresent": readonly_probes_present,
        "flashLoanSelectorPresent": flashloan_probe_present,
        "marketReadCount": len(markets),
        "markets": markets,
        "accountCount": len(account_list),
        "positionReadCount": len(positions),
        "positions": positions,
        "recentActivity": recent_activity,
        "observationOnly": True,
        "executionMode": "readonly_probe",
        "executionEnabled": False,
        "broadcastEnabled": False,
        "blockingReasons": list(dict.fromkeys(blockers)),
    }


__all__ = [
    "CORE_SELECTOR_PROBES",
    "MORPHO_BLUE_CORE",
    "REQUIRED_FLASHLOAN_PROBES",
    "REQUIRED_READONLY_PROBES",
    "read_morpho_blue_market",
    "read_morpho_blue_position",
    "scan_recent_morpho_blue_events",
    "summarize_robinhood_morpho_blue_readonly",
]
