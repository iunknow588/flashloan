from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any
from urllib import request as urllib_request

from web3 import Web3

from core.chain_data_registry import chain_contracts, chain_data_file_path


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
MULTICALL3_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "target", "type": "address"},
                    {"internalType": "bool", "name": "allowFailure", "type": "bool"},
                    {"internalType": "bytes", "name": "callData", "type": "bytes"},
                ],
                "internalType": "struct Multicall3.Call3[]",
                "name": "calls",
                "type": "tuple[]",
            }
        ],
        "name": "aggregate3",
        "outputs": [
            {
                "components": [
                    {"internalType": "bool", "name": "success", "type": "bool"},
                    {"internalType": "bytes", "name": "returnData", "type": "bytes"},
                ],
                "internalType": "struct Multicall3.Result[]",
                "name": "returnData",
                "type": "tuple[]",
            }
        ],
        "stateMutability": "payable",
        "type": "function",
    }
]
TOKEN_BALANCE_SCAN_CACHE_SCHEMA = "robinhood-testnet-v4-poolmanager-token-balance-scan-v1"


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _is_address(value: object) -> bool:
    text = str(value or "").strip()
    return text.startswith("0x") and len(text) == 42 and all(char in "0123456789abcdefABCDEF" for char in text[2:])


def _configured_contract(key: str, fallback: str = "") -> str:
    try:
        configured = chain_contracts(ROBINHOOD_TESTNET_NETWORK)
    except Exception:
        configured = {}
    return str(configured.get(key) or fallback).strip()


def _multicall3_address() -> str:
    return _configured_contract("multicall3", "0xa432504b6F04Cafe775b09D8AA92e8dbe41Ec7a8")


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


def _pool_key_currency_address(pool_key: dict[str, Any], currency_key: str) -> str:
    token_key = "token0" if currency_key == "currency0" else "token1"
    metadata_key = "currency0Metadata" if currency_key == "currency0" else "currency1Metadata"
    metadata = pool_key.get(metadata_key) if isinstance(pool_key.get(metadata_key), dict) else {}
    return str(pool_key.get(currency_key) or pool_key.get(token_key) or metadata.get("address") or "").strip()


def _index_pool_keys(active_pool_ids: list[str] | None = None) -> tuple[list[dict[str, Any]], str | None]:
    try:
        from intent_trade.chains.robinhood_testnet.v4_pool_index import load_robinhood_testnet_v4_pool_index

        index = load_robinhood_testnet_v4_pool_index()
    except Exception:
        return [], None
    if not index.get("ok"):
        return [], index.get("source")
    active = {str(pool_id or "").lower() for pool_id in active_pool_ids or []}
    rows = []
    for pool in _as_list(index.get("pools")):
        pool_id = str(pool.get("poolId") or pool.get("pool_id") or "").strip()
        if active and pool_id.lower() not in active:
            continue
        pool_key = pool.get("poolKey") if isinstance(pool.get("poolKey"), dict) else pool
        rows.append({**pool_key, "poolId": pool_id})
    return rows, index.get("source")


def _known_token_pool_ids() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for key in ("chain_test", "normal_testnet_probe", "predeploy_readiness"):
        path = chain_data_file_path(ROBINHOOD_TESTNET_NETWORK, key)
        payload = _read_json(path) if path is not None else {}
        candidates: list[tuple[str, str]] = []
        if key == "chain_test":
            preflight = payload.get("preflightSpec") if isinstance(payload.get("preflightSpec"), dict) else {}
            repayment = preflight.get("repaymentConstraint") if isinstance(preflight.get("repaymentConstraint"), dict) else {}
            candidates.append((str(repayment.get("borrowToken") or ""), "chain_test.repaymentConstraint"))
            for hop in preflight.get("routeHops") or []:
                if isinstance(hop, dict):
                    candidates.append((str(hop.get("tokenIn") or ""), "chain_test.routeHops"))
                    candidates.append((str(hop.get("tokenOut") or ""), "chain_test.routeHops"))
        elif key == "normal_testnet_probe":
            token = payload.get("token") if isinstance(payload.get("token"), dict) else {}
            candidates.append((str(token.get("address") or ""), "normal_testnet_probe.token"))
        else:
            evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
            probe = evidence.get("resolvedNormalTestnetProbe") if isinstance(evidence.get("resolvedNormalTestnetProbe"), dict) else {}
            token = probe.get("token") if isinstance(probe.get("token"), dict) else {}
            candidates.append((str(token.get("address") or ""), "predeploy_readiness.resolvedNormalTestnetProbe.token"))
        for address, source in candidates:
            if _is_address(address) and address.lower() != ZERO_ADDRESS.lower():
                bucket = result.setdefault(address.lower(), [])
                if source not in bucket:
                    bucket.append(source)
    return result


def _token_balance_scan_cache_path() -> Path | None:
    latest_path = chain_data_file_path(ROBINHOOD_TESTNET_NETWORK, "v4_pool_liquidity")
    if latest_path is None:
        return None
    return latest_path.parent / "token_balance_scan_cache.json"


def _load_token_balance_scan_cache(*, pool_manager: str) -> dict[str, Any]:
    path = _token_balance_scan_cache_path()
    if path is None:
        return {"entries": {}}
    payload = _read_json(path)
    if not payload or payload.get("_error"):
        return {"path": str(path), "entries": {}}
    if str(payload.get("schemaVersion") or "") != TOKEN_BALANCE_SCAN_CACHE_SCHEMA:
        return {"path": str(path), "entries": {}}
    if str(payload.get("poolManager") or "").lower() != str(pool_manager or "").lower():
        return {"path": str(path), "entries": {}}
    entries = payload.get("entries") if isinstance(payload.get("entries"), dict) else {}
    return {**payload, "path": str(path), "entries": entries}


def _write_token_balance_scan_cache(
    *,
    pool_manager: str,
    pool_index_source: str | None,
    entries: dict[str, dict[str, Any]],
    scan_scope: str,
) -> str | None:
    path = _token_balance_scan_cache_path()
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": TOKEN_BALANCE_SCAN_CACHE_SCHEMA,
        "network": ROBINHOOD_TESTNET_NETWORK,
        "chainId": ROBINHOOD_TESTNET_CHAIN_ID,
        "poolManager": pool_manager,
        "poolIndexSource": pool_index_source,
        "lastScanScope": scan_scope,
        "refreshedAt": _stamp(),
        "entryCount": len(entries),
        "entries": entries,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)


def _decode_multicall_value(w3: Web3, return_data: object, output_types: list[str]) -> Any:
    raw = bytes(return_data)
    if not raw:
        return None
    decoded = w3.codec.decode(output_types, raw)
    return decoded[0] if len(decoded) == 1 else decoded


def _multicall_values(
    w3: Web3,
    calls: list[tuple[str, str, str, list[str]]],
    *,
    chunk_size: int,
) -> tuple[dict[tuple[str, str], Any], list[str]]:
    values: dict[tuple[str, str], Any] = {}
    errors: list[str] = []
    multicall_address = _multicall3_address()
    if not _is_address(multicall_address):
        return values, ["multicall3_address_not_configured"]
    multicall = w3.eth.contract(address=Web3.to_checksum_address(multicall_address), abi=MULTICALL3_ABI)
    step = max(1, int(chunk_size))
    for offset in range(0, len(calls), step):
        chunk = calls[offset : offset + step]
        aggregate_calls = [
            {
                "target": Web3.to_checksum_address(address),
                "allowFailure": True,
                "callData": call_data,
            }
            for address, _, call_data, _ in chunk
        ]
        try:
            results = multicall.functions.aggregate3(aggregate_calls).call()
        except Exception as exc:
            errors.append(f"multicall3_chunk_{offset}:{str(exc)[:160]}")
            continue
        for (address, name, _, output_types), result in zip(chunk, results):
            success = bool(result[0])
            return_data = result[1]
            if not success:
                continue
            try:
                values[(address.lower(), name)] = _decode_multicall_value(w3, return_data, output_types)
            except Exception as exc:
                errors.append(f"{name}:{address}:{str(exc)[:120]}")
    return values, errors


def _batch_eth_call_values(
    w3: Web3,
    calls: list[tuple[str, str, str, list[str]]],
    *,
    rpc_url: str,
    chunk_size: int,
) -> tuple[dict[tuple[str, str], Any], list[str]]:
    values: dict[tuple[str, str], Any] = {}
    errors: list[str] = []
    step = max(1, int(chunk_size))
    next_id = 1
    for offset in range(0, len(calls), step):
        chunk = calls[offset : offset + step]
        id_map: dict[int, tuple[str, str, list[str]]] = {}
        payload = []
        for address, name, call_data, output_types in chunk:
            request_id = next_id
            next_id += 1
            id_map[request_id] = (address, name, output_types)
            payload.append(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "eth_call",
                    "params": [{"to": Web3.to_checksum_address(address), "data": call_data}, "latest"],
                }
            )
        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib_request.Request(
                rpc_url,
                data=body,
                headers={
                    "content-type": "application/json",
                    "user-agent": "flashloan-robinhood-testnet-v4-batch/1.0",
                },
                method="POST",
            )
            with urllib_request.urlopen(req, timeout=60) as response:
                decoded_response = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            errors.append(f"jsonrpc_batch_chunk_{offset}:{str(exc)[:160]}")
            continue
        rows = decoded_response if isinstance(decoded_response, list) else [decoded_response]
        for row in rows:
            if not isinstance(row, dict):
                continue
            request_id = row.get("id")
            if request_id not in id_map:
                continue
            address, name, output_types = id_map[int(request_id)]
            if row.get("error"):
                continue
            result = str(row.get("result") or "")
            if not result.startswith("0x") or result == "0x":
                continue
            try:
                values[(address.lower(), name)] = _decode_multicall_value(
                    w3,
                    bytes.fromhex(result.removeprefix("0x")),
                    output_types,
                )
            except Exception as exc:
                errors.append(f"{name}:{address}:{str(exc)[:120]}")
    return values, errors


def _individual_balance_values(w3: Web3, addresses: list[str], *, pool_manager: str) -> tuple[dict[str, int], list[str]]:
    values: dict[str, int] = {}
    errors: list[str] = []
    pm = Web3.to_checksum_address(pool_manager)
    for address in addresses:
        try:
            contract = w3.eth.contract(address=Web3.to_checksum_address(address), abi=ERC20_ABI)
            values[address.lower()] = int(contract.functions.balanceOf(pm).call())
        except Exception as exc:
            errors.append(f"{address}:{str(exc)[:160]}")
    return values, errors


def _individual_metadata_values(w3: Web3, addresses: list[str]) -> tuple[dict[tuple[str, str], Any], list[str]]:
    values: dict[tuple[str, str], Any] = {}
    errors: list[str] = []
    for address in addresses:
        try:
            contract = w3.eth.contract(address=Web3.to_checksum_address(address), abi=ERC20_ABI)
            values[(address.lower(), "symbol")] = str(contract.functions.symbol().call()).strip()
        except Exception as exc:
            errors.append(f"symbol:{address}:{str(exc)[:120]}")
        try:
            contract = w3.eth.contract(address=Web3.to_checksum_address(address), abi=ERC20_ABI)
            values[(address.lower(), "decimals")] = int(contract.functions.decimals().call())
        except Exception as exc:
            errors.append(f"decimals:{address}:{str(exc)[:120]}")
    return values, errors


def _pool_index_token_balance_sources(
    *,
    active_pool_ids: list[str],
    pool_manager: str,
    rpc_url: str,
    scan_scope: str,
    multicall_chunk_size: int,
    max_scan_seconds: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    normalized_scope = str(scan_scope or "active_plus_known").strip().lower()
    full_index = normalized_scope == "full_index"
    pool_keys, pool_index_source = _index_pool_keys(None if full_index else active_pool_ids)
    token_pool_ids: dict[str, list[str]] = {}
    for pool_key in pool_keys:
        pool_id = str(pool_key.get("poolId") or "").strip()
        for currency_key in ("currency0", "currency1"):
            address = _pool_key_currency_address(pool_key, currency_key)
            if not _is_address(address) or address.lower() == ZERO_ADDRESS.lower():
                continue
            bucket = token_pool_ids.setdefault(address.lower(), [])
            if pool_id and pool_id not in bucket:
                bucket.append(pool_id)
    if normalized_scope in {"active_plus_known", "full_index"}:
        for address, source_keys in _known_token_pool_ids().items():
            bucket = token_pool_ids.setdefault(address, [])
            for source_key in source_keys:
                if source_key not in bucket:
                    bucket.append(source_key)
    if not token_pool_ids:
        return [], {
            "poolIndexSource": pool_index_source,
            "indexedActivePoolKeyCount": len(pool_keys),
            "tokenBalanceScanScope": normalized_scope,
            "tokenBalanceScanCount": 0,
            "tokenBalanceSourceCount": 0,
            "tokenBalanceScanErrors": [],
        }

    w3 = _web3(rpc_url)
    if int(w3.eth.chain_id) != ROBINHOOD_TESTNET_CHAIN_ID:
        raise ValueError(f"RPC returned chainId {int(w3.eth.chain_id)}, expected {ROBINHOOD_TESTNET_CHAIN_ID}")
    token_addresses = sorted(token_pool_ids)
    balance_contract = w3.eth.contract(address=Web3.to_checksum_address("0x" + "1" * 40), abi=ERC20_ABI)
    errors: list[str] = []
    cache = _load_token_balance_scan_cache(pool_manager=pool_manager)
    cache_entries: dict[str, dict[str, Any]] = {
        str(address).lower(): dict(entry)
        for address, entry in (cache.get("entries") or {}).items()
        if isinstance(entry, dict)
    }
    balances: dict[str, int] = {}
    cached_addresses: list[str] = []
    for address in token_addresses:
        entry = cache_entries.get(address)
        if not isinstance(entry, dict) or entry.get("balanceRaw") is None:
            continue
        try:
            balances[address] = int(str(entry.get("balanceRaw") or "0"))
            cached_addresses.append(address)
        except (TypeError, ValueError):
            continue

    pending_balance_addresses = [address for address in token_addresses if address not in balances]
    deadline = None
    if max_scan_seconds is not None and int(max_scan_seconds or 0) > 0:
        deadline = time.monotonic() + max(1, int(max_scan_seconds))
    scanned_balance_count = 0
    # Robinhood testnet RPC currently rejects larger JSON-RPC batches with 429s.
    balance_chunk_size = max(1, min(int(multicall_chunk_size or 50), 50 if full_index else 200))
    for offset in range(0, len(pending_balance_addresses), balance_chunk_size):
        if deadline is not None and time.monotonic() >= deadline:
            break
        chunk_addresses = pending_balance_addresses[offset : offset + balance_chunk_size]
        balance_calls = [
            (
                address,
                "balanceOf",
                balance_contract.functions.balanceOf(Web3.to_checksum_address(pool_manager))._encode_transaction_data(),
                ["uint256"],
            )
            for address in chunk_addresses
        ]
        balance_values, chunk_errors = _batch_eth_call_values(
            w3,
            balance_calls,
            rpc_url=rpc_url,
            chunk_size=balance_chunk_size,
        )
        errors.extend(chunk_errors)
        if not balance_values and len(chunk_addresses) <= 50:
            individual_values, individual_errors = _individual_balance_values(
                w3,
                chunk_addresses,
                pool_manager=pool_manager,
            )
            for address, value in individual_values.items():
                balance_values[(address.lower(), "balanceOf")] = int(value)
            errors.extend(individual_errors)
        scanned_at = _stamp()
        for address in chunk_addresses:
            value = int(balance_values.get((address, "balanceOf")) or 0)
            balances[address] = value
            scanned_balance_count += 1
            entry = cache_entries.setdefault(address, {})
            entry.update(
                {
                    "address": Web3.to_checksum_address(address),
                    "balanceRaw": str(value),
                    "poolIds": token_pool_ids.get(address) or [],
                    "scannedAt": scanned_at,
                    "scanScope": normalized_scope,
                }
            )

    balance_pending_count = sum(1 for address in token_addresses if address not in balances)
    positive_addresses = [address for address, balance in balances.items() if int(balance or 0) > 0]
    metadata_contract = w3.eth.contract(address=Web3.to_checksum_address("0x" + "1" * 40), abi=ERC20_ABI)
    metadata_values: dict[tuple[str, str], Any] = {}
    for address in positive_addresses:
        entry = cache_entries.get(address) or {}
        if entry.get("symbol"):
            metadata_values[(address, "symbol")] = entry.get("symbol")
        if entry.get("decimals") is not None:
            metadata_values[(address, "decimals")] = entry.get("decimals")
    metadata_calls: list[tuple[str, str, str, list[str]]] = []
    for address in positive_addresses:
        if (address, "symbol") in metadata_values and (address, "decimals") in metadata_values:
            continue
        metadata_calls.append((address, "symbol", metadata_contract.functions.symbol()._encode_transaction_data(), ["string"]))
        metadata_calls.append((address, "decimals", metadata_contract.functions.decimals()._encode_transaction_data(), ["uint8"]))
    metadata_errors: list[str] = []
    metadata_chunk_size = max(1, min(int(multicall_chunk_size or 50), 50 if full_index else 200))
    for offset in range(0, len(metadata_calls), metadata_chunk_size):
        if deadline is not None and time.monotonic() >= deadline:
            break
        chunk_calls = metadata_calls[offset : offset + metadata_chunk_size]
        chunk_values, chunk_errors = _batch_eth_call_values(
            w3,
            chunk_calls,
            rpc_url=rpc_url,
            chunk_size=metadata_chunk_size,
        )
        metadata_values.update(chunk_values)
        metadata_errors.extend(chunk_errors)
        for address in {str(call[0]).lower() for call in chunk_calls}:
            entry = cache_entries.setdefault(address, {})
            if (address, "symbol") in metadata_values:
                entry["symbol"] = str(metadata_values[(address, "symbol")] or "").strip()
            if (address, "decimals") in metadata_values:
                try:
                    entry["decimals"] = int(metadata_values[(address, "decimals")])
                except (TypeError, ValueError):
                    pass
            entry["metadataScannedAt"] = _stamp()
    missing_metadata = [
        address
        for address in positive_addresses
        if (address, "symbol") not in metadata_values or (address, "decimals") not in metadata_values
    ]
    if missing_metadata and (not full_index or len(missing_metadata) <= 25) and (deadline is None or time.monotonic() < deadline):
        individual_metadata, individual_metadata_errors = _individual_metadata_values(w3, missing_metadata)
        metadata_values.update(individual_metadata)
        metadata_errors.extend(individual_metadata_errors)
        for address in missing_metadata:
            entry = cache_entries.setdefault(address, {})
            if (address, "symbol") in individual_metadata:
                entry["symbol"] = str(individual_metadata[(address, "symbol")] or "").strip()
            if (address, "decimals") in individual_metadata:
                entry["decimals"] = int(individual_metadata[(address, "decimals")])
            entry["metadataScannedAt"] = _stamp()
    errors.extend(metadata_errors)
    cache_path = _write_token_balance_scan_cache(
        pool_manager=pool_manager,
        pool_index_source=pool_index_source,
        entries=cache_entries,
        scan_scope=normalized_scope,
    )

    sources: list[dict[str, Any]] = []
    for normalized in positive_addresses:
        raw_balance = int(balances.get(normalized) or 0)
        symbol = str(metadata_values.get((normalized, "symbol")) or "").strip()
        decimals = metadata_values.get((normalized, "decimals"))
        metadata_ok = bool(symbol and decimals is not None)
        if decimals is None:
            decimals = 18
        try:
            decimals_int = int(decimals)
        except (TypeError, ValueError):
            decimals_int = 18
            metadata_ok = False
        if not symbol:
            symbol = Web3.to_checksum_address(normalized)[:8]
        try:
            balance_units = float(raw_balance) / 10**decimals_int
        except OverflowError:
            balance_units = None
        try:
            sources.append(
                {
                    "address": Web3.to_checksum_address(normalized),
                    "symbol": symbol,
                    "decimals": decimals_int,
                    "poolManagerBalanceRaw": str(raw_balance),
                    "poolManagerBalance": balance_units,
                    "metadataOk": metadata_ok,
                    "poolIds": token_pool_ids.get(normalized) or [],
                    "source": f"v4_pool_index_{normalized_scope}_token_balanceOf_poolmanager",
                }
            )
        except Exception as exc:
            errors.append(f"{normalized}:{str(exc)[:160]}")
    return sources, {
        "poolIndexSource": pool_index_source,
        "indexedActivePoolKeyCount": sum(1 for pool_key in pool_keys if str(pool_key.get("poolId") or "").lower() in {str(pool_id or "").lower() for pool_id in active_pool_ids}),
        "indexedPoolKeyScanCount": len(pool_keys),
        "tokenBalanceScanScope": normalized_scope,
        "tokenBalanceScanStrategy": "poolmanager_balance_of_active_pool_tokens_plus_known_assets"
        if normalized_scope == "active_plus_known"
        else "poolmanager_balance_of_full_initialized_pool_index",
        "tokenBalanceScanAuthority": "erc20_balanceOf_poolmanager",
        "tokenBalanceScanCount": len(token_pool_ids),
        "tokenBalanceScanComplete": balance_pending_count == 0,
        "tokenBalanceScanCachePath": cache_path or cache.get("path"),
        "tokenBalanceCachedCount": len(cached_addresses),
        "tokenBalanceScannedCount": scanned_balance_count,
        "tokenBalancePendingCount": balance_pending_count,
        "tokenBalanceSourceCount": len(sources),
        "tokenBalancePositiveAddressCount": len(positive_addresses),
        "tokenMetadataPendingCount": sum(
            1
            for address in positive_addresses
            if (address, "symbol") not in metadata_values or (address, "decimals") not in metadata_values
        ),
        "tokenBalanceScanErrors": errors,
    }


def _merge_flash_accounting_source_rows(*source_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for sources in source_lists:
        for source in sources:
            address = str(source.get("address") or source.get("currency") or "").strip()
            if not address:
                continue
            key = address.lower()
            current = merged.setdefault(key, dict(source))
            if _balance_raw(source) > _balance_raw(current):
                current.update(source)
            pool_ids = list(current.get("poolIds") or current.get("pool_ids") or [])
            for pool_id in source.get("poolIds") or source.get("pool_ids") or []:
                pool_id_text = str(pool_id or "").strip()
                if pool_id_text and pool_id_text not in pool_ids:
                    pool_ids.append(pool_id_text)
            current["poolIds"] = pool_ids
            sources_text = [item for item in str(current.get("source") or "").split(";") if item]
            source_text = str(source.get("source") or "").strip()
            if source_text and source_text not in sources_text:
                sources_text.append(source_text)
            current["source"] = ";".join(sources_text)
    return sorted(merged.values(), key=lambda item: (str(item.get("symbol") or ""), str(item.get("address") or "")))


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
    token_balance_scan_scope: str = "active_plus_known",
    multicall_chunk_size: int = 400,
    max_token_balance_scan_seconds: int | None = None,
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
    token_balance_scan: dict[str, Any] = {}
    if not decode_pool_keys and active_pool_ids and str(token_balance_scan_scope or "").strip().lower() != "none":
        try:
            indexed_sources, token_balance_scan = _pool_index_token_balance_sources(
                active_pool_ids=active_pool_ids,
                pool_manager=pool_manager,
                rpc_url=resolved_rpc_url,
                scan_scope=token_balance_scan_scope,
                multicall_chunk_size=multicall_chunk_size,
                max_scan_seconds=max_token_balance_scan_seconds,
            )
            flash_accounting_sources = _merge_flash_accounting_source_rows(flash_accounting_sources, indexed_sources)
        except Exception as exc:
            token_balance_scan = {
                "tokenBalanceScanScope": str(token_balance_scan_scope or "").strip().lower(),
                "tokenBalanceScanErrors": [str(exc)[:200]],
            }
    token_balance_errors = [str(item) for item in token_balance_scan.get("tokenBalanceScanErrors") or []]
    if token_balance_errors:
        blocking_reasons.append("v4_poolmanager_token_balance_scan_errors_present")
    if token_balance_scan and token_balance_scan.get("tokenBalanceScanComplete") is False:
        blocking_reasons.append("v4_poolmanager_token_balance_scan_incomplete")
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
        "borrowAssetDiscoveryStrategy": "known_stable_assets_plus_active_non_stable_pool_assets",
        "rpcUrl": resolved_rpc_url if decode_pool_keys or token_balance_scan else None,
        "lookbackBlocks": flash.get("lookbackBlocks"),
        "swapLogCount": flash.get("swapLogCount", 0),
        "activePoolIdCount": len(active_pool_ids),
        "decodedPoolKeyCount": decoded_count,
        "tokenAddressCount": len(token_addresses),
        "tokenAddresses": token_addresses,
        "usableFlashAccountingPoolCount": flash.get("usableFlashAccountingPoolCount", len(active_pool_ids)),
        **token_balance_scan,
        "nativePoolManagerBalanceWei": native_balance_wei,
        "flashAccountingSourceCount": len(flash_accounting_sources),
        "flashAccountingSources": flash_accounting_sources,
        "poolKeys": pool_keys,
        "pools": pools,
        "decodeErrors": decode_errors,
        "blockingReasons": blocking_reasons,
        "notes": [
            "Borrowable V4 flash-accounting assets are currencies with positive PoolManager balance.",
            "Default balance scan is strategic: known configured stable assets plus active PoolKey currencies; full_index is reserved for offline diagnostics.",
            "PoolKey rows are trading-pool evidence and do not by themselves prove flash-accounting borrowable funds.",
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
