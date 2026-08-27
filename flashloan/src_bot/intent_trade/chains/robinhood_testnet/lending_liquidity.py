from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from core.chain_flow_dispatcher import ChainFlowRequest
from core.chain_data_registry import chain_contracts, chain_data_by_network
from intent_trade.chains.robinhood.chain_health import JsonRpcTransport, _rpc_call
from intent_trade.chains.robinhood.shared import ROBINHOOD_TESTNET_CHAIN_ID, ROBINHOOD_TESTNET_NETWORK
from core.market_config import DEFAULT_ROBINHOOD_TESTNET_RPC_URL


POOL_MANAGER = "0x8366a39CC670B4001A1121B8F6A443A643e40951"
POSITION_MANAGER = "0x58DaEC3116AAe6D93017BaaEA7749052E8a04fa7"
PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3"
UNIVERSAL_ROUTER = "0x8876789976dEcBfCbBbe364623C63652db8C0904"
TESTNET_WETH = "0x7943e237c7F95DA44E0301572D358911207852Fa"
TESTNET_MULTICALL3 = "0xa432504b6F04Cafe775b09D8AA92e8dbe41Ec7a8"
MAINNET_MORPHO_BLUE_NOT_TESTNET = "0x9D53d5E3bd5E8d4Cbfa6DB1ca238AEA02E651010"
AAVE_POOL_ENV_NAMES = (
    "ROBINHOOD_TESTNET_AAVE_POOL",
    "ROBINHOOD_TESTNET_AAVE_V3_POOL",
    "ROBINHOOD_TESTNET_AAVE_POOL_ADDRESS",
)
MORPHO_ENV_NAMES = (
    "ROBINHOOD_TESTNET_MORPHO_BLUE",
    "ROBINHOOD_TESTNET_MORPHO_BLUE_ADDRESS",
    "ROBINHOOD_TESTNET_MORPHO",
    "ROBINHOOD_TESTNET_MORPHO_ADDRESS",
)
TOKEN_ENV_NAMES = (
    "ROBINHOOD_TESTNET_BORROW_TOKEN_ADDRESSES",
    "ROBINHOOD_TESTNET_TOKEN_ADDRESSES",
    "ROBINHOOD_TESTNET_USDC_ADDRESS",
    "ROBINHOOD_TESTNET_WETH_ADDRESS",
)


def _testnet_contracts() -> dict[str, str]:
    try:
        configured = chain_contracts(ROBINHOOD_TESTNET_NETWORK)
    except Exception:
        configured = {}
    return {
        "v4PoolManager": configured.get("v4_pool_manager") or POOL_MANAGER,
        "positionManager": configured.get("position_manager") or POSITION_MANAGER,
        "permit2": configured.get("permit2") or PERMIT2,
        "universalRouter": configured.get("universal_router") or UNIVERSAL_ROUTER,
        "wrappedNative": configured.get("wrapped_native") or TESTNET_WETH,
        "multicall3": configured.get("multicall3") or TESTNET_MULTICALL3,
    }


def _testnet_rpc_url() -> str:
    try:
        row = chain_data_by_network(ROBINHOOD_TESTNET_NETWORK) or {}
    except Exception:
        row = {}
    return _text(row.get("rpc_url")) or DEFAULT_ROBINHOOD_TESTNET_RPC_URL


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hex_int(value: Any) -> int:
    raw = str(value or "0").strip()
    return int(raw, 16) if raw.startswith(("0x", "0X")) else int(raw or "0")


def _text(value: object) -> str:
    return str(value or "").strip()


def _is_address(value: object) -> bool:
    text = _text(value)
    return text.startswith("0x") and len(text) == 42 and all(char in "0123456789abcdefABCDEF" for char in text[2:])


def _has_code(value: object) -> bool:
    code = _text(value).lower()
    return code not in {"", "0x", "0x0"}


def _split_addresses(value: object) -> list[str]:
    result: list[str] = []
    for item in _text(value).replace(";", ",").replace("|", ",").split(","):
        address = item.strip()
        if _is_address(address) and address.lower() not in {existing.lower() for existing in result}:
            result.append(address)
    return result


def _addresses_from_env(names: tuple[str, ...]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for name in names:
        for address in _split_addresses(os.getenv(name, "")):
            key = address.lower()
            if key in seen:
                continue
            seen.add(key)
            rows.append({"source": name, "address": address})
    return rows


def _erc20_balance_call(token: str, owner: str) -> str:
    return "0x70a08231" + owner.removeprefix("0x").rjust(64, "0")


def _code_status(
    rpc_url: str,
    address: str,
    *,
    timeout_seconds: float,
    transport: JsonRpcTransport | None,
) -> dict[str, Any]:
    code = _rpc_call(
        rpc_url,
        "eth_getCode",
        [address, "latest"],
        timeout_seconds=timeout_seconds,
        transport=transport,
    )
    return {
        "address": address,
        "hasCode": _has_code(code),
        "codeBytes": max(0, (len(_text(code)) - 2) // 2) if _has_code(code) else 0,
    }


def _named_code_statuses(
    rpc_url: str,
    addresses: dict[str, str],
    *,
    timeout_seconds: float,
    transport: JsonRpcTransport | None,
) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    for name, address in addresses.items():
        try:
            statuses[name] = _code_status(
                rpc_url,
                address,
                timeout_seconds=timeout_seconds,
                transport=transport,
            )
        except Exception as exc:
            statuses[name] = {
                "address": address,
                "hasCode": False,
                "codeBytes": 0,
                "error": str(exc),
            }
    return statuses


def _token_balance_status(
    rpc_url: str,
    token: str,
    owner: str,
    *,
    timeout_seconds: float,
    transport: JsonRpcTransport | None,
) -> dict[str, Any]:
    try:
        raw = _rpc_call(
            rpc_url,
            "eth_call",
            [{"to": token, "data": _erc20_balance_call(token, owner)}, "latest"],
            timeout_seconds=timeout_seconds,
            transport=transport,
        )
        balance = _hex_int(raw)
        return {"token": token, "owner": owner, "ok": True, "balanceRaw": str(balance), "nonzero": balance > 0}
    except Exception as exc:
        return {"token": token, "owner": owner, "ok": False, "balanceRaw": "0", "nonzero": False, "error": str(exc)}


def _protocol_liquidity_report(
    *,
    protocol: str,
    candidates: list[dict[str, str]],
    token_addresses: list[str],
    rpc_url: str,
    timeout_seconds: float,
    transport: JsonRpcTransport | None,
) -> dict[str, Any]:
    contract_checks = []
    token_balance_checks = []
    for candidate in candidates:
        address = candidate["address"]
        status = _code_status(rpc_url, address, timeout_seconds=timeout_seconds, transport=transport)
        status["source"] = candidate["source"]
        contract_checks.append(status)
        if status["hasCode"]:
            for token in token_addresses:
                token_balance_checks.append(
                    _token_balance_status(
                        rpc_url,
                        token,
                        address,
                        timeout_seconds=timeout_seconds,
                        transport=transport,
                    )
                )
    code_present = [item for item in contract_checks if item.get("hasCode")]
    nonzero_tokens = [item for item in token_balance_checks if item.get("nonzero")]
    return {
        "protocol": protocol,
        "configuredAddressCount": len(candidates),
        "codePresentCount": len(code_present),
        "tokenLiquidityCheckCount": len(token_balance_checks),
        "verifiedBorrowableAssetCount": len(nonzero_tokens),
        "verifiedNonzeroTokenBalance": bool(nonzero_tokens),
        "status": (
            "verified_nonzero_lending_liquidity"
            if nonzero_tokens
            else "contract_code_present_but_token_liquidity_unverified"
            if code_present
            else "no_candidate_address_configured"
            if not candidates
            else "candidate_address_has_no_code"
        ),
        "contracts": contract_checks,
        "tokenBalances": token_balance_checks,
    }


def summarize_robinhood_testnet_lending_liquidity(
    *,
    rpc_url: str | None = None,
    timeout_seconds: float = 10,
    transport: JsonRpcTransport | None = None,
) -> dict[str, Any]:
    rpc_url = rpc_url or _testnet_rpc_url()
    checks: list[dict[str, Any]] = []
    blockers: list[str] = []
    configured_contracts = _testnet_contracts()
    pool_manager = configured_contracts["v4PoolManager"]
    chain_id = 0
    try:
        chain_id = _hex_int(_rpc_call(rpc_url, "eth_chainId", timeout_seconds=timeout_seconds, transport=transport))
        checks.append({"name": "eth_chainId", "ok": chain_id == ROBINHOOD_TESTNET_CHAIN_ID, "value": chain_id})
    except Exception as exc:
        checks.append({"name": "eth_chainId", "ok": False, "error": str(exc)})
        blockers.append("rpc_chain_id_check_failed")
    if chain_id and chain_id != ROBINHOOD_TESTNET_CHAIN_ID:
        blockers.append("rpc_not_robinhood_testnet_46630")

    pool_manager_status: dict[str, Any] = {"address": pool_manager, "hasCode": False}
    testnet_contracts: dict[str, dict[str, Any]] = {
        name: {"address": address, "hasCode": False, "codeBytes": 0}
        for name, address in configured_contracts.items()
    }
    mainnet_morpho_negative_control: dict[str, Any] = {
        "address": MAINNET_MORPHO_BLUE_NOT_TESTNET,
        "expected": "no_code_on_robinhood_testnet",
        "hasCode": False,
    }
    if chain_id == ROBINHOOD_TESTNET_CHAIN_ID:
        testnet_contracts = _named_code_statuses(
            rpc_url,
            configured_contracts,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )
        pool_manager_status = dict(testnet_contracts["v4PoolManager"])
        try:
            balance_raw = _rpc_call(
                rpc_url,
                "eth_getBalance",
                [pool_manager, "latest"],
                timeout_seconds=timeout_seconds,
                transport=transport,
            )
            pool_manager_status["nativeBalanceWei"] = str(_hex_int(balance_raw))
        except Exception as exc:
            pool_manager_status["error"] = str(exc)
            blockers.append("v4_poolmanager_code_check_failed")
        try:
            mainnet_morpho_negative_control = {
                **mainnet_morpho_negative_control,
                **_code_status(
                    rpc_url,
                    MAINNET_MORPHO_BLUE_NOT_TESTNET,
                    timeout_seconds=timeout_seconds,
                    transport=transport,
                ),
            }
        except Exception as exc:
            mainnet_morpho_negative_control["error"] = str(exc)

    token_addresses = [item["address"] for item in _addresses_from_env(TOKEN_ENV_NAMES)]
    aave = _protocol_liquidity_report(
        protocol="aave_v3",
        candidates=_addresses_from_env(AAVE_POOL_ENV_NAMES),
        token_addresses=token_addresses,
        rpc_url=rpc_url,
        timeout_seconds=timeout_seconds,
        transport=transport,
    )
    morpho = _protocol_liquidity_report(
        protocol="morpho_blue",
        candidates=_addresses_from_env(MORPHO_ENV_NAMES),
        token_addresses=token_addresses,
        rpc_url=rpc_url,
        timeout_seconds=timeout_seconds,
        transport=transport,
    )

    verified_protocols = [item for item in (aave, morpho) if item["verifiedNonzeroTokenBalance"]]
    if not verified_protocols:
        blockers.append("no_aave_or_morpho_lending_liquidity_verified_on_testnet")
    if not token_addresses:
        blockers.append("no_robinhood_testnet_borrow_token_addresses_configured_for_lending_liquidity_check")
    if mainnet_morpho_negative_control.get("hasCode"):
        blockers.append("mainnet_morpho_address_has_code_on_testnet_requires_manual_review")

    return {
        "schemaVersion": "robinhood-testnet-lending-liquidity-v1",
        "checkedAt": _utc_now_iso(),
        "network": ROBINHOOD_TESTNET_NETWORK,
        "expectedChainId": ROBINHOOD_TESTNET_CHAIN_ID,
        "chainId": chain_id,
        "chainDataSource": "chains/robinhood-testnet/chain.json",
        "rpcUrl": rpc_url,
        "rpcOk": bool(checks) and all(bool(item.get("ok")) for item in checks),
        "checks": checks,
        "testnetV4PoolManager": pool_manager_status,
        "testnetContracts": testnet_contracts,
        "mainnetMorphoBlueAddressNegativeControl": mainnet_morpho_negative_control,
        "tokenAddressCount": len(token_addresses),
        "tokenAddresses": token_addresses,
        "protocols": {
            "aave_v3": aave,
            "morpho_blue": morpho,
        },
        "hasVerifiedLendingPool": bool(verified_protocols),
        "hasVerifiedBorrowableLiquidity": bool(verified_protocols),
        "supportsAaveOrMorphoFlashLoans": bool(verified_protocols),
        "supportsV4FlashAccountingTests": bool(testnet_contracts["v4PoolManager"].get("hasCode")),
        "broadcastEnabled": False,
        "blockingReasons": list(dict.fromkeys(blockers)),
    }


def lending_liquidity_flow(
    *,
    request: ChainFlowRequest | None = None,
    rpc_url: str | None = None,
    timeout_seconds: float = 10,
    transport: JsonRpcTransport | None = None,
    **_: Any,
) -> dict[str, Any]:
    return summarize_robinhood_testnet_lending_liquidity(
        rpc_url=rpc_url,
        timeout_seconds=timeout_seconds,
        transport=transport,
    )


__all__ = ["lending_liquidity_flow", "summarize_robinhood_testnet_lending_liquidity"]
