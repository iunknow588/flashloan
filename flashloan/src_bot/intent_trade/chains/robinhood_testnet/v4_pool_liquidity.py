from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from web3 import Web3

from core.chain_data_registry import chain_data_file_path


ROBINHOOD_TESTNET_CHAIN_ID = 46630
ROBINHOOD_TESTNET_NETWORK = "robinhood-testnet"
ROBINHOOD_TESTNET_V4_POOL_MANAGER = "0x8366a39CC670B4001A1121B8F6A443A643e40951"
ROBINHOOD_TESTNET_RPC_URL = "https://rpc.testnet.chain.robinhood.com"
V4_INITIALIZE_TOPIC = Web3.keccak(
    text="Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)"
).hex()
V4_SWAP_TOPIC = Web3.keccak(text="Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)").hex()
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"type": "string"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"type": "uint8"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [{"type": "address"}],
        "name": "balanceOf",
        "outputs": [{"type": "uint256"}],
        "type": "function",
    },
]


def _as_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        return {"_error": f"invalid_json:{exc.msg}"}
    return payload if isinstance(payload, dict) else {"_error": "invalid_json_root"}


def _rpc_url_from_env_or_default() -> str:
    import os

    for name in (
        "ROBINHOOD_TESTNET_RPC_URL",
        "ROBINHOOD_TESTNET_RPC",
        "ROBINHOOD_TESTNET_RPCS",
        "ROBINHOOD_TESTNET_RPC_URLS",
        "ROBINHOOD_TESTNET_ALCHEMY_RPC_URL",
        "ROBINHOOD_TESTNET_ALCHEMY_RPC",
    ):
        value = str(os.getenv(name) or "").split(",")[0].strip()
        if value:
            return value
    return ROBINHOOD_TESTNET_RPC_URL


def _web3(rpc_url: str) -> Web3:
    return Web3(
        Web3.HTTPProvider(
            rpc_url,
            request_kwargs={
                "timeout": 30,
                "headers": {
                    "content-type": "application/json",
                    "user-agent": "flashloan-robinhood-testnet-v4/1.0",
                },
            },
        )
    )


def _topic_address(topic: object) -> str:
    raw = topic.hex() if hasattr(topic, "hex") else str(topic or "")
    address = "0x" + raw.removeprefix("0x")[-40:]
    if int(address, 16) == 0:
        return ZERO_ADDRESS
    return Web3.to_checksum_address(address)


def _currency_metadata(w3: Web3, currency: str, pool_manager: str) -> dict[str, Any]:
    if str(currency).lower() == ZERO_ADDRESS.lower():
        balance = w3.eth.get_balance(Web3.to_checksum_address(pool_manager))
        return {
            "address": ZERO_ADDRESS,
            "symbol": "ETH",
            "decimals": 18,
            "poolManagerBalanceRaw": str(balance),
            "poolManagerBalance": float(balance) / 10**18,
            "metadataOk": True,
        }
    checksum = Web3.to_checksum_address(currency)
    contract = w3.eth.contract(address=checksum, abi=ERC20_ABI)
    symbol = ""
    decimals = None
    balance = None
    metadata_ok = True
    try:
        symbol = str(contract.functions.symbol().call()).strip()
    except Exception:
        symbol = checksum[:8]
        metadata_ok = False
    try:
        decimals = int(contract.functions.decimals().call())
    except Exception:
        metadata_ok = False
    try:
        balance = int(contract.functions.balanceOf(Web3.to_checksum_address(pool_manager)).call())
    except Exception:
        balance = None
    return {
        "address": checksum,
        "symbol": symbol or checksum[:8],
        "decimals": decimals,
        "poolManagerBalanceRaw": str(balance) if balance is not None else None,
        "poolManagerBalance": (float(balance) / 10**decimals) if balance is not None and decimals is not None else None,
        "metadataOk": metadata_ok,
    }


def _latest_swap_liquidity(
    w3: Web3,
    *,
    pool_manager: str,
    pool_id: str,
    from_block: int,
    to_block: int | str,
) -> dict[str, Any]:
    logs = w3.eth.get_logs(
        {
            "address": Web3.to_checksum_address(pool_manager),
            "fromBlock": max(0, int(from_block)),
            "toBlock": to_block,
            "topics": [V4_SWAP_TOPIC, pool_id],
        }
    )
    for log in reversed(logs):
        amount0, amount1, sqrt_price_x96, liquidity, tick, fee = w3.codec.decode(
            ["int128", "int128", "uint160", "uint128", "int24", "uint24"],
            bytes(log["data"]),
        )
        if int(liquidity) > 0:
            return {
                "latestSwapBlockNumber": int(log["blockNumber"]),
                "latestSwapTransactionHash": log["transactionHash"].hex()
                if hasattr(log["transactionHash"], "hex")
                else str(log["transactionHash"]),
                "latestSwapLiquidity": str(liquidity),
                "latestSwapAmount0": str(amount0),
                "latestSwapAmount1": str(amount1),
                "latestSwapSqrtPriceX96": str(sqrt_price_x96),
                "latestSwapTick": int(tick),
                "latestSwapFee": int(fee),
            }
    return {}


def _decode_initialize_log(
    w3: Web3,
    *,
    pool_manager: str,
    pool_id: str,
    from_block: int = 0,
    to_block: int | str = "latest",
) -> dict[str, Any] | None:
    logs = w3.eth.get_logs(
        {
            "address": Web3.to_checksum_address(pool_manager),
            "fromBlock": max(0, int(from_block)),
            "toBlock": to_block,
            "topics": [V4_INITIALIZE_TOPIC, pool_id],
        }
    )
    if not logs:
        return None
    log = logs[0]
    topics = list(log["topics"])
    fee, tick_spacing, hooks, sqrt_price_x96, tick = w3.codec.decode(
        ["uint24", "int24", "address", "uint160", "int24"],
        bytes(log["data"]),
    )
    currency0 = _currency_metadata(w3, _topic_address(topics[2]), pool_manager)
    currency1 = _currency_metadata(w3, _topic_address(topics[3]), pool_manager)
    return {
        "poolId": pool_id,
        "poolKeyDecoded": True,
        "currency0": currency0["address"],
        "currency1": currency1["address"],
        "token0": currency0["address"],
        "token1": currency1["address"],
        "token0Symbol": currency0["symbol"],
        "token1Symbol": currency1["symbol"],
        "token0Decimals": currency0["decimals"],
        "token1Decimals": currency1["decimals"],
        "symbolPair": f"{currency0['symbol']}:{currency1['symbol']}",
        "fee": int(fee),
        "tickSpacing": int(tick_spacing),
        "hooks": Web3.to_checksum_address(hooks) if int(str(hooks), 16) else ZERO_ADDRESS,
        "sqrtPriceX96": str(sqrt_price_x96),
        "tick": int(tick),
        "initializeBlockNumber": int(log["blockNumber"]),
        "initializeTransactionHash": log["transactionHash"].hex()
        if hasattr(log["transactionHash"], "hex")
        else str(log["transactionHash"]),
        "currency0Metadata": currency0,
        "currency1Metadata": currency1,
    }


def _enrich_pool_ids_with_rpc(
    pool_ids: list[str],
    *,
    pool_manager: str,
    rpc_url: str,
    swap_from_block: int,
    to_block: int | str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    w3 = _web3(rpc_url)
    chain_id = int(w3.eth.chain_id)
    if chain_id != ROBINHOOD_TESTNET_CHAIN_ID:
        raise ValueError(f"RPC returned chainId {chain_id}, expected {ROBINHOOD_TESTNET_CHAIN_ID}")
    pool_keys: list[dict[str, Any]] = []
    pools: list[dict[str, Any]] = []
    errors: list[str] = []
    for pool_id in pool_ids:
        try:
            decoded = _decode_initialize_log(w3, pool_manager=pool_manager, pool_id=pool_id)
            if decoded is None:
                decoded = {"poolId": pool_id, "poolKeyDecoded": False}
            swap_state = _latest_swap_liquidity(
                w3,
                pool_manager=pool_manager,
                pool_id=pool_id,
                from_block=swap_from_block,
                to_block=to_block,
            )
            pool_keys.append(decoded)
            symbol_pair = decoded.get("symbolPair") or f"poolId:{pool_id[2:10]}"
            pools.append(
                {
                    "protocol": "uniswap_v4",
                    "poolId": pool_id,
                    "poolKey": decoded,
                    "poolManager": pool_manager,
                    "symbolPair": symbol_pair,
                    "token0": decoded.get("token0") or decoded.get("currency0"),
                    "token1": decoded.get("token1") or decoded.get("currency1"),
                    "token0Symbol": decoded.get("token0Symbol") or "",
                    "token1Symbol": decoded.get("token1Symbol") or "",
                    "token0Decimals": decoded.get("token0Decimals"),
                    "token1Decimals": decoded.get("token1Decimals"),
                    "fee": decoded.get("fee"),
                    "tickSpacing": decoded.get("tickSpacing"),
                    "hooks": decoded.get("hooks"),
                    "liquidity": swap_state.get("latestSwapLiquidity"),
                    "tvlUsd": 0,
                    "status": "observable_token_pair_nonzero_liquidity_swap"
                    if decoded.get("poolKeyDecoded") and swap_state.get("latestSwapLiquidity")
                    else "observable_poolid_nonzero_liquidity_swap",
                    "depthStatus": "unpriced_token_pair"
                    if decoded.get("poolKeyDecoded")
                    else "unpriced_poolid_only",
                    "source": "poolmanager_initialize_and_swap_logs",
                    **swap_state,
                }
            )
        except Exception as exc:
            errors.append(f"{pool_id}:{str(exc)[:160]}")
            pool_keys.append({"poolId": pool_id, "poolKeyDecoded": False, "error": str(exc)[:200]})
    return pool_keys, pools, errors


def _workspace_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "contract").exists() and (parent / "flashloan").exists():
            return parent
    return current.parents[5]


def _resolve_workspace_path(value: object, *, base: Path | None = None) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    if path.is_absolute():
        return path
    return (base or _workspace_root()) / path


def default_contract_metadata_path() -> Path:
    return _workspace_root() / "contract" / "robinhood" / "testnet" / "contracts.json"


def _preflight_report_candidates(base: Path | None = None) -> list[Path]:
    evidence_root = (base or _workspace_root()) / "contract" / "contracts-dex" / "deployments" / "evidence"
    if not evidence_root.exists():
        return []
    return sorted(evidence_root.glob("*robinhoodTestnet-robinhood-testnet-preflight/report.json"))


def _flash_pool_ids(report: dict[str, Any]) -> list[str]:
    flash = report.get("flashAccountingLiquidity") if isinstance(report.get("flashAccountingLiquidity"), dict) else {}
    return [
        str(pool_id)
        for pool_id in flash.get("activePoolIds") or []
        if isinstance(pool_id, str) and pool_id.startswith("0x") and len(pool_id) == 66
    ]


def _report_chain_id(report: dict[str, Any]) -> int:
    rpc = report.get("rpc") if isinstance(report.get("rpc"), dict) else {}
    return int(rpc.get("chainId") or report.get("chainId") or report.get("expectedChainId") or 0)


def _balance_raw(metadata: dict[str, Any]) -> int:
    try:
        return int(str(metadata.get("poolManagerBalanceRaw") or "0"))
    except (TypeError, ValueError):
        return 0


def _flash_accounting_sources(
    pool_keys: list[dict[str, Any]],
    *,
    native_balance_wei: object,
) -> list[dict[str, Any]]:
    by_address: dict[str, dict[str, Any]] = {}
    try:
        native_raw = int(str(native_balance_wei or "0"))
    except (TypeError, ValueError):
        native_raw = 0
    if native_raw > 0:
        by_address[ZERO_ADDRESS] = {
            "address": ZERO_ADDRESS,
            "symbol": "ETH",
            "decimals": 18,
            "poolManagerBalanceRaw": str(native_raw),
            "poolManagerBalance": native_raw / 10**18,
            "poolIds": [],
            "source": "nativePoolManagerBalanceWei",
        }
    for pool_key in pool_keys:
        pool_id = str(pool_key.get("poolId") or "").strip()
        for currency_key, metadata_key in (("currency0", "currency0Metadata"), ("currency1", "currency1Metadata")):
            metadata = pool_key.get(metadata_key) if isinstance(pool_key.get(metadata_key), dict) else {}
            address = str(pool_key.get(currency_key) or metadata.get("address") or "").strip()
            if not address:
                continue
            raw = _balance_raw(metadata)
            if raw <= 0:
                continue
            current = by_address.setdefault(
                address.lower(),
                {
                    "address": address,
                    "symbol": str(metadata.get("symbol") or address[:8]).strip(),
                    "decimals": metadata.get("decimals"),
                    "poolManagerBalanceRaw": str(raw),
                    "poolManagerBalance": metadata.get("poolManagerBalance"),
                    "poolIds": [],
                    "source": "decoded_pool_key_metadata",
                },
            )
            if raw > _balance_raw(current):
                current["poolManagerBalanceRaw"] = str(raw)
                current["poolManagerBalance"] = metadata.get("poolManagerBalance")
                current["symbol"] = str(metadata.get("symbol") or current.get("symbol") or address[:8]).strip()
                current["decimals"] = metadata.get("decimals", current.get("decimals"))
            if pool_id and pool_id not in current["poolIds"]:
                current["poolIds"].append(pool_id)
    return sorted(
        by_address.values(),
        key=lambda item: (
            str(item.get("symbol") or ""),
            str(item.get("address") or ""),
        ),
    )


def _load_best_preflight_report(seed_path: Path, *, allow_fallback: bool) -> tuple[Path, dict[str, Any]]:
    seed = _read_json(seed_path)
    if seed.get("_error"):
        raise ValueError(f"cannot read preflight report: {seed['_error']}")
    if not seed:
        raise FileNotFoundError(str(seed_path))
    if _report_chain_id(seed) == ROBINHOOD_TESTNET_CHAIN_ID and _flash_pool_ids(seed):
        return seed_path, seed
    if not allow_fallback:
        return seed_path, seed

    best_path = seed_path
    best_report = seed
    best_score = (len(_flash_pool_ids(seed)), 0, str(seed.get("runAt") or ""))
    for candidate in _preflight_report_candidates():
        payload = _read_json(candidate)
        if not payload or payload.get("_error") or _report_chain_id(payload) != ROBINHOOD_TESTNET_CHAIN_ID:
            continue
        rpc = payload.get("rpc") if isinstance(payload.get("rpc"), dict) else {}
        flash = payload.get("flashAccountingLiquidity") if isinstance(payload.get("flashAccountingLiquidity"), dict) else {}
        score = (
            len(_flash_pool_ids(payload)),
            int(rpc.get("blockNumber") or flash.get("toBlock") or 0),
            str(payload.get("runAt") or ""),
        )
        if score > best_score:
            best_path = candidate
            best_report = payload
            best_score = score
    return best_path, best_report


def build_v4_pool_liquidity_from_contract_evidence(
    *,
    contract_metadata_path: Path | None = None,
    preflight_report_path: Path | None = None,
    pool_limit: int = 80,
    decode_pool_keys: bool = False,
    rpc_url: str | None = None,
) -> dict[str, Any]:
    """Build a page-consumable V4 poolId cache from local Robinhood testnet evidence."""

    metadata_path = contract_metadata_path or default_contract_metadata_path()
    metadata = _read_json(metadata_path)
    if metadata.get("_error"):
        raise ValueError(f"cannot read contract metadata: {metadata['_error']}")
    if not metadata:
        raise FileNotFoundError(str(metadata_path))

    report_path = preflight_report_path or _resolve_workspace_path(metadata.get("lastReadinessEvidence"))
    if report_path is None:
        raise ValueError("lastReadinessEvidence is not configured")
    report_path, report = _load_best_preflight_report(report_path, allow_fallback=preflight_report_path is None)

    rpc = report.get("rpc") if isinstance(report.get("rpc"), dict) else {}
    chain_id = _report_chain_id(report)
    if chain_id != ROBINHOOD_TESTNET_CHAIN_ID:
        raise ValueError(f"preflight report chainId {chain_id} does not match {ROBINHOOD_TESTNET_CHAIN_ID}")

    flash = report.get("flashAccountingLiquidity") if isinstance(report.get("flashAccountingLiquidity"), dict) else {}
    pool_manager = str(
        flash.get("address")
        or metadata.get("defaultV4PoolManager")
        or metadata.get("defaultFlashSource")
        or ROBINHOOD_TESTNET_V4_POOL_MANAGER
    )
    active_pool_ids = _flash_pool_ids(report)[: max(0, int(pool_limit))]
    pools = []
    pool_keys = [{"poolId": pool_id, "poolKeyDecoded": False} for pool_id in active_pool_ids]
    decode_errors: list[str] = []
    resolved_rpc_url = rpc_url or _rpc_url_from_env_or_default()
    if active_pool_ids and decode_pool_keys:
        to_block = int(rpc.get("blockNumber") or flash.get("toBlock") or 0) or "latest"
        pool_keys, pools, decode_errors = _enrich_pool_ids_with_rpc(
            active_pool_ids,
            pool_manager=pool_manager,
            rpc_url=resolved_rpc_url,
            swap_from_block=int(flash.get("fromBlock") or 0),
            to_block=to_block,
        )
    else:
        pools = [
            {
                "protocol": "uniswap_v4",
                "poolId": pool_id,
                "symbolPair": f"poolId:{pool_id[2:10]}",
                "poolManager": pool_manager,
                "status": "observable_poolid_nonzero_liquidity_swap",
                "depthStatus": "unpriced_poolid_only",
                "tvlUsd": 0,
                "source": "preflight_flashAccountingLiquidity.activePoolIds",
            }
            for pool_id in active_pool_ids
        ]
    blocking_reasons = []
    if not active_pool_ids:
        blocking_reasons.append("no_active_v4_pool_ids_in_local_preflight_evidence")
    if int(flash.get("usableFlashAccountingPoolCount") or 0) > len(active_pool_ids):
        blocking_reasons.append("active_pool_id_list_truncated_by_preflight_report")
    if decode_errors:
        blocking_reasons.append("v4_poolkey_decode_errors_present")
    decoded_count = sum(1 for item in pool_keys if item.get("poolKeyDecoded"))
    token_addresses = sorted(
        {
            str(value)
            for item in pool_keys
            for value in (item.get("currency0"), item.get("currency1"))
            if value and str(value).lower() != ZERO_ADDRESS.lower()
        }
    )
    native_balance_wei = flash.get("nativeBalanceWei")
    flash_accounting_sources = _flash_accounting_sources(pool_keys, native_balance_wei=native_balance_wei)
    return {
        "schemaVersion": "robinhood-testnet-v4-pool-liquidity-v1",
        "network": ROBINHOOD_TESTNET_NETWORK,
        "chainId": ROBINHOOD_TESTNET_CHAIN_ID,
        "poolManager": pool_manager,
        "generatedAt": report.get("runAt"),
        "generatedBlockNumber": rpc.get("blockNumber") or flash.get("toBlock"),
        "sourceEvidence": str(report_path),
        "sourceContractMetadata": str(metadata_path),
        "discoveryMethod": "local_preflight_poolmanager_swap_logs",
        "rpcUrl": resolved_rpc_url if decode_pool_keys else None,
        "lookbackBlocks": flash.get("lookbackBlocks"),
        "swapLogCount": flash.get("swapLogCount", 0),
        "activePoolIdCount": len(active_pool_ids),
        "decodedPoolKeyCount": decoded_count,
        "tokenAddressCount": len(token_addresses),
        "tokenAddresses": token_addresses,
        "usableFlashAccountingPoolCount": flash.get("usableFlashAccountingPoolCount", len(active_pool_ids)),
        "nativePoolManagerBalanceWei": native_balance_wei,
        "flashAccountingSourceCount": len(flash_accounting_sources),
        "flashAccountingSources": flash_accounting_sources,
        "poolKeys": pool_keys,
        "pools": pools,
        "decodeErrors": decode_errors,
        "blockingReasons": blocking_reasons,
        "notes": [
            "These rows are poolId-only observations from local Robinhood Testnet preflight evidence.",
            "Token addresses, fee, tickSpacing, hooks, and USD depth require a decoded V4 PoolKey cache.",
        ],
    }


def write_v4_pool_liquidity_from_contract_evidence(
    output_path: Path | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    payload = build_v4_pool_liquidity_from_contract_evidence(**kwargs)
    resolved = output_path or chain_data_file_path(ROBINHOOD_TESTNET_NETWORK, "v4_pool_liquidity")
    if resolved is None:
        raise ValueError("v4_pool_liquidity data file is not configured")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**payload, "outputPath": str(resolved)}


def load_robinhood_testnet_v4_pool_liquidity(path: Path | None = None) -> dict[str, Any]:
    resolved = path or chain_data_file_path(ROBINHOOD_TESTNET_NETWORK, "v4_pool_liquidity")
    if resolved is None:
        return {
            "ok": False,
            "status": "v4_pool_liquidity_cache_not_available",
            "status_message": "Robinhood Testnet Uniswap V4 具体 PoolKey/poolId 流动性缓存尚未配置。",
            "source": None,
            "pools": [],
            "pool_keys": [],
            "blocking_reasons": ["v4_pool_liquidity_data_file_not_configured"],
        }

    payload = _read_json(resolved)
    if not payload:
        return {
            "ok": False,
            "status": "v4_pool_liquidity_cache_not_available",
            "status_message": "Robinhood Testnet Uniswap V4 具体 PoolKey/poolId 流动性缓存尚未生成。",
            "source": str(resolved),
            "pools": [],
            "pool_keys": [],
            "blocking_reasons": ["v4_pool_liquidity_data_file_missing"],
        }
    if payload.get("_error"):
        return {
            "ok": False,
            "status": "v4_pool_liquidity_cache_invalid",
            "status_message": f"Robinhood Testnet Uniswap V4 流动性缓存无法读取：{payload['_error']}",
            "source": str(resolved),
            "pools": [],
            "pool_keys": [],
            "blocking_reasons": [str(payload["_error"])],
        }

    chain_id = payload.get("chainId", payload.get("chain_id"))
    network = str(payload.get("network") or "").strip().lower()
    scope_ok = network == ROBINHOOD_TESTNET_NETWORK and int(chain_id or 0) == ROBINHOOD_TESTNET_CHAIN_ID
    pools = _as_list(payload.get("pools"))
    pool_keys = _as_list(payload.get("poolKeys") or payload.get("pool_keys"))
    blocking_reasons = [str(item) for item in payload.get("blockingReasons") or payload.get("blocking_reasons") or []]
    if not scope_ok:
        return {
            "ok": False,
            "status": "v4_pool_liquidity_scope_mismatch",
            "status_message": "Robinhood Testnet Uniswap V4 流动性缓存的 network/chainId 与链路不匹配。",
            "source": str(resolved),
            "pools": [],
            "pool_keys": [],
            "blocking_reasons": blocking_reasons or ["v4_pool_liquidity_scope_mismatch"],
        }
    if not pools and not pool_keys:
        return {
            "ok": False,
            "status": "v4_pool_liquidity_cache_not_available",
            "status_message": "Robinhood Testnet Uniswap V4 尚未生成具体 PoolKey/poolId 流动性缓存。",
            "source": str(resolved),
            "payload": payload,
            "pools": [],
            "pool_keys": [],
            "blocking_reasons": blocking_reasons or ["no_uniswap_v4_poolkey_or_poolid_liquidity_cache_generated"],
        }
    return {
        "ok": True,
        "status": "v4_pool_liquidity_cache_available",
        "status_message": "Robinhood Testnet Uniswap V4 本地 poolId 缓存已接入；PoolKey 解码和 USD 深度估值仍保持独立生成。",
        "source": str(resolved),
        "payload": payload,
        "pools": pools,
        "pool_keys": pool_keys,
        "pool_manager": payload.get("poolManager") or payload.get("pool_manager"),
        "block_number": payload.get("generatedBlockNumber") or payload.get("block_number"),
        "refreshed_at": payload.get("generatedAt") or payload.get("fetched_at"),
        "blocking_reasons": blocking_reasons,
    }


__all__ = [
    "build_v4_pool_liquidity_from_contract_evidence",
    "default_contract_metadata_path",
    "load_robinhood_testnet_v4_pool_liquidity",
    "write_v4_pool_liquidity_from_contract_evidence",
]
