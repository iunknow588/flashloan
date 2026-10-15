"""Shared Robinhood ETH-pair token evidence helpers.

The mainnet/testnet chain modules can use these helpers when they need to
extract Uniswap V4 ETH-paired token candidates from a local pool index or from
PoolManager Initialize logs. No chain-specific pool files are written here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_by_network, chain_data_file_path
from eth_utils import event_signature_to_log_topic
from web3 import Web3


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
INITIALIZE_SIGNATURE = "Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)"
NETWORK = "robinhood"
MAINNET_CHAIN_ID = 4663

STABLE_SYMBOLS = {
    "AUSD", "BUSD", "DAI", "DAI.E", "EURC", "FDUSD", "FRAX", "FRAX.E",
    "GHO", "PYUSD", "RLUSD", "TUSD", "USDC", "USDC.E", "USDD", "USDE",
    "USDG", "USDF", "USD1", "USDT", "USDT.E",
}

_TOKEN_METADATA_CACHE: dict[str, dict[str, Any]] | None = None


def _load_v4_token_metadata(
    *,
    network: str = NETWORK,
    chain_id: int = MAINNET_CHAIN_ID,
) -> dict[str, dict[str, Any]]:
    global _TOKEN_METADATA_CACHE
    if _TOKEN_METADATA_CACHE is not None:
        return _TOKEN_METADATA_CACHE
    path = default_v4_token_metadata_path(network=network, chain_id=chain_id)
    payload = _read_json(path)
    tokens = payload.get("tokens") if isinstance(payload.get("tokens"), dict) else {}
    _TOKEN_METADATA_CACHE = {
        str(address or "").strip().lower(): row
        for address, row in tokens.items()
        if isinstance(row, dict)
    }
    return _TOKEN_METADATA_CACHE


def _metadata_lookup(address: str, *, network: str = NETWORK, chain_id: int = MAINNET_CHAIN_ID) -> dict[str, Any]:
    normalized = str(address or "").strip().lower()
    row = _load_v4_token_metadata(network=network, chain_id=chain_id).get(normalized)
    return row if isinstance(row, dict) else {}


def _is_hex_prefix_symbol(symbol: object) -> bool:
    text = str(symbol or "").strip()
    return text.upper().startswith("0X")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def default_pool_index_path(
    *,
    network: str = NETWORK,
    chain_id: int = MAINNET_CHAIN_ID,
) -> Path:
    try:
        path = chain_data_file_path(network, "v4_pool_index", chain_id=chain_id)
        if path is not None:
            return Path(path)
    except (TypeError, ValueError, OSError):
        pass
    return Path(__file__).resolve().parents[4] / "runtime" / "evidence" / network / "v4_pool_index" / "latest.json"


def default_eth_pair_tokens_path(
    *,
    network: str = NETWORK,
    chain_id: int = MAINNET_CHAIN_ID,
) -> Path:
    try:
        path = chain_data_file_path(network, "eth_pair_tokens", chain_id=chain_id)
        if path is not None:
            return Path(path)
    except (TypeError, ValueError, OSError):
        pass
    return Path(__file__).resolve().parents[4] / "runtime" / "evidence" / network / "eth_pair_tokens.json"


def default_v4_token_metadata_path(
    *,
    network: str = NETWORK,
    chain_id: int = MAINNET_CHAIN_ID,
) -> Path:
    try:
        path = chain_data_file_path(network, "v4_token_metadata", chain_id=chain_id)
        if path is not None:
            return Path(path)
    except (TypeError, ValueError, OSError):
        pass
    return Path(__file__).resolve().parents[4] / "runtime" / "evidence" / network / "v4_token_metadata.json"


def _is_zero_address(value: str | None) -> bool:
    text = str(value or "").strip().lower()
    if not text.startswith("0x"):
        return False
    try:
        return int(text, 16) == 0
    except ValueError:
        return False


def _token_side(
    pool: dict[str, Any],
    *,
    network: str = NETWORK,
    chain_id: int = MAINNET_CHAIN_ID,
) -> tuple[str, str] | None:
    """Return the non-zero token address and label for an ETH V4 pool."""
    pool_key = pool.get("poolKey") if isinstance(pool.get("poolKey"), dict) else {}
    currency0 = pool.get("token0") or pool_key.get("currency0") or pool_key.get("token0") or ""
    currency1 = pool.get("token1") or pool_key.get("currency1") or pool_key.get("token1") or ""
    symbol0 = pool.get("token0Symbol") or pool_key.get("token0Symbol") or ""
    symbol1 = pool.get("token1Symbol") or pool_key.get("token1Symbol") or ""
    if _is_zero_address(currency0):
        metadata1 = _metadata_lookup(
            str(currency1),
            network=network,
            chain_id=chain_id,
        )
        raw_symbol1 = str(symbol1 or "").strip()
        if raw_symbol1 and not _is_hex_prefix_symbol(raw_symbol1):
            symbol = raw_symbol1
        else:
            symbol = str(metadata1.get("symbol") or raw_symbol1 or currency1[:10]).strip()
        return str(currency1), symbol
    if _is_zero_address(currency1):
        metadata0 = _metadata_lookup(
            str(currency0),
            network=network,
            chain_id=chain_id,
        )
        raw_symbol0 = str(symbol0 or "").strip()
        if raw_symbol0 and not _is_hex_prefix_symbol(raw_symbol0):
            symbol = raw_symbol0
        else:
            symbol = str(metadata0.get("symbol") or raw_symbol0 or currency0[:10]).strip()
        return str(currency0), symbol
    return None


def _known_symbol(address: str, *, network: str = NETWORK) -> str | None:
    try:
        row = chain_data_by_network(network) or {}
        contracts = row.get("contracts") if isinstance(row.get("contracts"), dict) else {}
        normalized = str(address or "").strip().lower()
        if normalized == str(contracts.get("wrapped_native") or "").strip().lower():
            return "WETH"
        if normalized == str(contracts.get("usdg") or "").strip().lower():
            return "USDG"
    except Exception:
        return None
    return None


def _is_stable_symbol(symbol: object) -> bool:
    text = str(symbol or "").strip().upper()
    if text in STABLE_SYMBOLS:
        return True
    return text.endswith("USD")


def _filter_tokens(tokens: list[dict[str, Any]], *, stable_only: bool) -> list[dict[str, Any]]:
    if not stable_only:
        return tokens
    return [token for token in tokens if _is_stable_symbol(token.get("symbol"))]


def eth_tokens_from_index(
    index: dict[str, Any],
    *,
    stable_only: bool = False,
    network: str = NETWORK,
    chain_id: int = MAINNET_CHAIN_ID,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pool in index.get("pools") or []:
        if not isinstance(pool, dict):
            continue
        side = _token_side(
            pool,
            network=network,
            chain_id=chain_id,
        )
        if side is None:
            continue
        address, symbol = side
        key = str(address).strip().lower()
        if key == ZERO_ADDRESS or key in seen:
            continue
        seen.add(key)
        metadata = _metadata_lookup(str(address), network=network, chain_id=chain_id)
        metadata_symbol = str(metadata.get("symbol") or "").strip()
        if metadata_symbol and (_is_hex_prefix_symbol(symbol) or not symbol):
            symbol = metadata_symbol
        symbol = _known_symbol(str(address), network=network) or str(symbol or address[:10])
        rows.append(
            {
                "address": address,
                "symbol": symbol,
                "poolId": pool.get("poolId") or pool.get("pool") or "",
                "source": "v4_pool_index",
            }
        )
    return _filter_tokens(rows, stable_only=stable_only)


def _topic_address(topic: Any) -> str:
    raw = bytes(topic) if not isinstance(topic, str) else bytes.fromhex(topic[2:] if topic.startswith("0x") else topic)
    return Web3.to_checksum_address("0x" + raw[-20:].hex())


def _erc20_symbol(w3: Web3, address: str) -> str:
    try:
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(address),
            abi=[{"inputs": [], "name": "symbol", "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"}],
        )
        return str(contract.functions.symbol().call()).strip()
    except Exception:
        return address[:10]


def _get_logs_chunked_eth_pair(
    w3: Web3,
    *,
    pool_manager: str,
    initialize_topic: str,
    zero_topic: str,
    from_block: int,
    to_block: int,
    chunk_size: int = 10_000_000,
    min_chunk_size: int = 100_000,
) -> list[Any]:
    logs: list[Any] = []
    cursor = max(0, int(from_block))
    step = max(min_chunk_size, int(chunk_size))
    while cursor <= int(to_block):
        end = min(int(to_block), cursor + step - 1)
        try:
            logs.extend(
                w3.eth.get_logs(
                    {
                        "address": pool_manager,
                        "fromBlock": cursor,
                        "toBlock": end,
                        "topics": [initialize_topic, None, zero_topic, None],
                    }
                )
            )
            cursor = end + 1
        except Exception:
            if step <= min_chunk_size:
                raise
            step = max(min_chunk_size, step // 2)
    return logs


def eth_tokens_from_rpc(
    *,
    rpc_url: str,
    pool_manager: str,
    from_block: int = 0,
    to_block: int | None = None,
    include_metadata: bool = True,
    stable_only: bool = False,
    network: str = NETWORK,
    chain_id: int = MAINNET_CHAIN_ID,
) -> dict[str, Any]:
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        raise RuntimeError(f"{network} RPC unavailable")
    actual_chain_id = int(w3.eth.chain_id)
    if actual_chain_id != int(chain_id):
        raise RuntimeError(f"RPC returned chainId {actual_chain_id}, expected {chain_id}")
    initialize_topic = "0x" + event_signature_to_log_topic(INITIALIZE_SIGNATURE).hex()
    zero_topic = "0x" + ("00" * 32)
    latest_block = int(w3.eth.block_number)
    resolved_to_block = latest_block if to_block is None else int(to_block)
    logs = _get_logs_chunked_eth_pair(
        w3,
        pool_manager=Web3.to_checksum_address(pool_manager),
        initialize_topic=initialize_topic,
        zero_topic=zero_topic,
        from_block=max(0, int(from_block)),
        to_block=resolved_to_block,
    )
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for log in logs:
        currencies = list(log.get("topics") or [])
        if len(currencies) < 4:
            continue
        currency1 = _topic_address(currencies[3])
        key = str(currency1).lower()
        if key == ZERO_ADDRESS or key in seen:
            continue
        seen.add(key)
        symbol = _known_symbol(currency1, network=network) or (_erc20_symbol(w3, currency1) if include_metadata else currency1[:10])
        if stable_only and not _is_stable_symbol(symbol):
            continue
        rows.append(
            {
                "address": currency1,
                "symbol": symbol,
                "poolId": currencies[1].hex() if hasattr(currencies[1], "hex") else str(currencies[1]),
                "source": "poolmanager_initialize_topic",
            }
        )
    return {
        "network": network,
        "chainId": actual_chain_id,
        "poolManager": pool_manager,
        "fromBlock": max(0, int(from_block)),
        "toBlock": resolved_to_block,
        "initializeLogCount": len(logs),
        "ethPairTokenCount": len(rows),
        "ycZc": rows,
    }


__all__ = [
    "INITIALIZE_SIGNATURE",
    "MAINNET_CHAIN_ID",
    "NETWORK",
    "STABLE_SYMBOLS",
    "ZERO_ADDRESS",
    "default_eth_pair_tokens_path",
    "default_v4_token_metadata_path",
    "default_pool_index_path",
    "eth_tokens_from_index",
    "eth_tokens_from_rpc",
]
