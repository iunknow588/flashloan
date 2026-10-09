from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any
from urllib import request as urllib_request

import eth_abi
from eth_utils import keccak as _keccak
from web3 import Web3

from core.chain_data_registry import chain_contracts, chain_data_file_path
from dex.intent_trade.chains.robinhood_testnet.v4_pool_rpc import (
    ERC20_ABI,
    MULTICALL3_ABI,
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
    ROBINHOOD_TESTNET_V4_POOL_MANAGER,
    TOKEN_BALANCE_SCAN_CACHE_SCHEMA,
    V4_INITIALIZE_TOPIC,
    V4_SWAP_TOPIC,
    ZERO_ADDRESS,
    _as_list,
    _configured_contract,
    _currency_metadata,
    _decode_initialize_log,
    _enrich_pool_ids_with_rpc,
    _is_address,
    _latest_swap_liquidity,
    _multicall3_address,
    _read_json,
    _rpc_url_from_env_or_default,
    _stamp,
    _topic_address,
    _web3,
)


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
    return _workspace_root() / "contract" / "robinhood-testnet" / "DEX" / "config" / "contracts.json"


def _preflight_report_candidates(base: Path | None = None) -> list[Path]:
    evidence_root = (base or _workspace_root()) / "contract" / "robinhood-testnet" / "DEX" / "deployments" / "evidence"
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
        from dex.intent_trade.chains.robinhood_testnet.v4_pool_index import load_robinhood_testnet_v4_pool_index

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
        row = {**pool_key, "poolId": pool_id}
        for extra_key in (
            "recentSwapActive",
            "status",
            "latestSwapAmount0",
            "latestSwapAmount1",
            "latestSwapBlockNumber",
            "latestSwapFee",
            "latestSwapLiquidity",
            "latestSwapSqrtPriceX96",
            "latestSwapTick",
            "latestSwapTransactionHash",
        ):
            if pool.get(extra_key) is not None:
                row[extra_key] = pool[extra_key]
        rows.append(row)
    return rows, index.get("source")


def _known_token_pool_ids() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    allowlist_path = chain_data_file_path(ROBINHOOD_TESTNET_NETWORK, "v4_token_allowlist")
    allowlist = _read_json(allowlist_path) if allowlist_path is not None else {}
    allowlist_tokens = allowlist.get("tokens") if isinstance(allowlist.get("tokens"), dict) else {}
    for symbol, entry in allowlist_tokens.items():
        if not isinstance(entry, dict):
            continue
        address = str(entry.get("address") or "")
        if not _is_address(address) or address.lower() == ZERO_ADDRESS.lower():
            continue
        bucket = result.setdefault(address.lower(), [])
        source = f"v4-token-allowlist.{symbol}"
        if source not in bucket:
            bucket.append(source)

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

    # Local Robinhood testnet V4 AMM manifest: tz/ta/tb and official V4 hook pools.
    manifest_path = _workspace_root() / "test-token_create" / "config" / "testnet-v4-amm" / "manifest.json"
    manifest = _read_json(manifest_path)
    for symbol in ("tz", "ta", "tb"):
        address = str(manifest.get(symbol) or "")
        if _is_address(address) and address.lower() != ZERO_ADDRESS.lower():
            bucket = result.setdefault(address.lower(), [])
            source = f"testnet-v4-amm-manifest.{symbol}"
            if source not in bucket:
                bucket.append(source)
    official = manifest.get("officialV4") if isinstance(manifest.get("officialV4"), dict) else {}
    route = official.get("lastArbLiveTest") if isinstance(official.get("lastArbLiveTest"), dict) else {}
    route = route.get("route") if isinstance(route.get("route"), dict) else {}
    for key in route.get("keys") or []:
        if not isinstance(key, dict):
            continue
        c0 = str(key.get("currency0") or "").strip()
        c1 = str(key.get("currency1") or "").strip()
        fee = int(key.get("fee") or 0)
        tick_spacing = int(key.get("tickSpacing") or 0)
        hooks = str(key.get("hooks") or ZERO_ADDRESS).strip()
        if not (_is_address(c0) and _is_address(c1)):
            continue
        try:
            encoded = eth_abi.encode(
                ["address", "address", "uint24", "int24", "address"],
                [
                    Web3.to_checksum_address(c0),
                    Web3.to_checksum_address(c1),
                    fee,
                    tick_spacing,
                    Web3.to_checksum_address(hooks),
                ],
            )
            pool_id = f"0x{_keccak(encoded).hex()}"
        except Exception:
            pool_id = ""
        if not pool_id:
            continue
        for currency in (c0, c1):
            normalized = currency.lower()
            bucket = result.setdefault(normalized, [])
            if pool_id not in bucket:
                bucket.append(pool_id)
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
        decoded_response = None
        last_error = None
        for attempt in range(1, 4):
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
                with urllib_request.urlopen(req, timeout=20) as response:
                    decoded_response = json.loads(response.read().decode("utf-8"))
                    break
            except Exception as exc:
                last_error = exc
                if attempt >= 3:
                    errors.append(f"jsonrpc_batch_chunk_{offset}:{str(last_error)[:160]}")
                    break
                time.sleep(0.4 * attempt)
        if decoded_response is None:
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
    recent_swaps_only = normalized_scope in {"full_index_recent_swaps", "recent_swaps"}
    pool_keys, pool_index_source = _index_pool_keys(None if (full_index or recent_swaps_only) else active_pool_ids)
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
    cache = _load_token_balance_scan_cache(pool_manager=pool_manager)
    cache_entries: dict[str, dict[str, Any]] = {
        str(address).lower(): dict(entry)
        for address, entry in (cache.get("entries") or {}).items()
        if isinstance(entry, dict)
    }
    if normalized_scope == "active_plus_known":
        for address, source_keys in _known_token_pool_ids().items():
            if _is_address(address) and address.lower() != ZERO_ADDRESS.lower():
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
    balance_chunk_size = max(1, min(int(multicall_chunk_size or 50), 25 if (full_index or recent_swaps_only) else 200))
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
        else:
            missing_chunk_addresses = [
                address for address in chunk_addresses if (address, "balanceOf") not in balance_values
            ]
            if missing_chunk_addresses:
                individual_values, individual_errors = _individual_balance_values(
                    w3,
                    missing_chunk_addresses,
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
    metadata_chunk_size = max(1, min(int(multicall_chunk_size or 50), 25 if (full_index or recent_swaps_only) else 200))
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
        decimals_int = None
        if decimals is not None:
            try:
                decimals_int = int(decimals)
            except (TypeError, ValueError):
                decimals_int = None
                metadata_ok = False
        if not symbol:
            symbol = Web3.to_checksum_address(normalized)[:8]
        balance_units = None
        if decimals_int is not None:
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
        else "poolmanager_balance_of_recent_swap_pool_index_tokens"
        if recent_swaps_only
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

    normalized_scan_scope = str(token_balance_scan_scope or "active_plus_known").strip().lower()
    full_index_scope = normalized_scan_scope in {
        "full_index",
        "full_index_recent_swaps",
        "recent_swaps",
    }
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
    if normalized_scan_scope != "none" and (active_pool_ids or full_index_scope):
        try:
            indexed_sources, token_balance_scan = _pool_index_token_balance_sources(
                active_pool_ids=active_pool_ids,
                pool_manager=pool_manager,
                rpc_url=resolved_rpc_url,
                scan_scope=normalized_scan_scope,
                multicall_chunk_size=multicall_chunk_size,
                max_scan_seconds=max_token_balance_scan_seconds,
            )
            flash_accounting_sources = _merge_flash_accounting_source_rows(flash_accounting_sources, indexed_sources)
            if full_index_scope and not decode_pool_keys:
                full_index_rows, full_index_source = _index_pool_keys(None)
                full_source_pool_ids: list[str] = []
                for source in indexed_sources:
                    for pool_id in source.get("poolIds") or []:
                        text = str(pool_id or "").strip()
                        if text.startswith("0x") and len(text) == 66 and text not in full_source_pool_ids:
                            full_source_pool_ids.append(text)
                if full_source_pool_ids:
                    active_pool_ids = full_source_pool_ids
                    active_id_set = {str(pool_id).lower() for pool_id in active_pool_ids}
                    indexed_pool_rows = [
                        row
                        for row in full_index_rows
                        if str(row.get("poolId") or "").strip().lower() in active_id_set
                    ]
                    if indexed_pool_rows:
                        pool_keys = indexed_pool_rows
                        pools = indexed_pool_rows
                        decode_errors = []
                        token_balance_scan["indexedFullSourcePoolIdCount"] = len(full_source_pool_ids)
                        token_balance_scan["indexedFullSourcePoolKeyCount"] = len(indexed_pool_rows)
                        token_balance_scan["poolIndexSource"] = full_index_source or token_balance_scan.get("poolIndexSource")
        except Exception as exc:
            token_balance_scan = {
                "tokenBalanceScanScope": normalized_scan_scope,
                "tokenBalanceScanErrors": [str(exc)[:200]],
            }
    token_balance_errors = [str(item) for item in token_balance_scan.get("tokenBalanceScanErrors") or []]
    if token_balance_errors:
        if token_balance_scan.get("tokenBalanceScanComplete") is not True:
            blocking_reasons.append("v4_poolmanager_token_balance_scan_errors_present")
    if full_index_scope and token_balance_scan.get("indexedFullSourcePoolIdCount"):
        blocking_reasons = [
            reason
            for reason in blocking_reasons
            if reason != "active_pool_id_list_truncated_by_preflight_report"
        ]
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
            "Default balance scan is strategic: known configured stable assets plus active PoolKey currencies; full_index expands discovery to every initialized PoolManager currency.",
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
            "status_message": "Robinhood Testnet Uniswap V4 concrete PoolKey/poolId liquidity cache not configured",
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
            "status_message": "Robinhood Testnet Uniswap V4 concrete PoolKey/poolId liquidity cache not generated",
            "source": str(resolved),
            "pools": [],
            "pool_keys": [],
            "blocking_reasons": ["v4_pool_liquidity_data_file_missing"],
        }
    if payload.get("_error"):
        return {
            "ok": False,
            "status": "v4_pool_liquidity_cache_invalid",
            "status_message": f"Robinhood Testnet Uniswap V4 liquidity cache unreadable: {payload['_error']}",
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
            "status_message": "Robinhood Testnet Uniswap V4 liquidity cache network/chainId does not match",
            "source": str(resolved),
            "pools": [],
            "pool_keys": [],
            "blocking_reasons": blocking_reasons or ["v4_pool_liquidity_scope_mismatch"],
        }
    if not pools and not pool_keys:
        return {
            "ok": False,
            "status": "v4_pool_liquidity_cache_not_available",
            "status_message": "Robinhood Testnet Uniswap V4 concrete PoolKey/poolId liquidity cache not generated",
            "source": str(resolved),
            "payload": payload,
            "pools": [],
            "pool_keys": [],
            "blocking_reasons": blocking_reasons or ["no_uniswap_v4_poolkey_or_poolid_liquidity_cache_generated"],
        }
    return {
        "ok": True,
        "status": "v4_pool_liquidity_cache_available",
        "status_message": "Robinhood Testnet Uniswap V4 local poolId cache accepted; PoolKey decode and USD depth estimation remain independently generated",
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
