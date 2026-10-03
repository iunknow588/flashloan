from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from eth_abi import decode
from web3 import Web3


SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.chain_data_registry import chain_data_file_path  # noqa: E402
from dex.intent_trade.chains.robinhood_common.eth_pair_tokens_core import ZERO_ADDRESS  # noqa: E402


NETWORK = "robinhood"
CHAIN_ID = 4663
RPC_URL = "https://rpc.mainnet.chain.robinhood.com"
WETH_ADDRESS = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
USDG_ADDRESS = "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"
KNOWN_SYMBOLS = {
    ZERO_ADDRESS: "ETH",
    WETH_ADDRESS: "WETH",
    USDG_ADDRESS: "USDG",
}
KNOWN_DECIMALS = {
    ZERO_ADDRESS: 18,
    WETH_ADDRESS: 18,
    USDG_ADDRESS: 6,
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _default_token_metadata_path() -> Path:
    try:
        path = chain_data_file_path(NETWORK, "v4_token_metadata", chain_id=CHAIN_ID)
        if path is not None:
            return Path(path)
    except (TypeError, ValueError, OSError):
        pass
    return SRC_ROOT / "runtime" / "evidence" / NETWORK / "v4_token_metadata.json"


def _default_pool_index_path() -> Path:
    try:
        path = chain_data_file_path(NETWORK, "v4_pool_index", chain_id=CHAIN_ID)
        if path is not None:
            return Path(path)
    except (TypeError, ValueError, OSError):
        pass
    return SRC_ROOT / "runtime" / "evidence" / NETWORK / "v4_pool_index" / "latest.json"


def _default_eth_pair_tokens_path() -> Path:
    try:
        path = chain_data_file_path(NETWORK, "eth_pair_tokens", chain_id=CHAIN_ID)
        if path is not None:
            return Path(path)
    except (TypeError, ValueError, OSError):
        pass
    return SRC_ROOT / "runtime" / "evidence" / NETWORK / "eth_pair_tokens.json"


def _normalized_address(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text.startswith("0x") and len(text) == 42 else ""


def _selector(w3: Web3, signature: str) -> str:
    return w3.keccak(text=signature)[:4].hex()


def _decode_string(raw: bytes, fallback: Any = None) -> Any:
    try:
        return str(decode(["string"], raw)[0]).strip()
    except Exception:
        return fallback


def _decode_uint(raw: bytes, fallback: Any = None) -> Any:
    try:
        return int(decode(["uint256"], raw)[0])
    except Exception:
        return fallback


def _call_one(w3: Web3, address: str, signature: str, decode_fn) -> Any:
    try:
        raw = w3.eth.call(
            {
                "to": Web3.to_checksum_address(address),
                "data": _selector(w3, signature),
            }
        )
        return decode_fn(raw)
    except Exception:
        return None


def _metadata_for_address(w3: Web3, address: str) -> dict[str, Any]:
    key = _normalized_address(address)
    if key == ZERO_ADDRESS:
        return {
            "address": ZERO_ADDRESS,
            "symbol": KNOWN_SYMBOLS[ZERO_ADDRESS],
            "decimals": KNOWN_DECIMALS[ZERO_ADDRESS],
            "metadataOk": True,
            "source": "known",
        }
    checksum = Web3.to_checksum_address(key)
    symbol = KNOWN_SYMBOLS.get(key) or _call_one(w3, checksum, "symbol()", lambda raw: _decode_string(raw))
    decimals = KNOWN_DECIMALS.get(key) or _call_one(w3, checksum, "decimals()", lambda raw: _decode_uint(raw))
    ok = bool(symbol and decimals is not None)
    return {
        "address": checksum,
        "symbol": symbol or checkmarks_prefix(key),
        "decimals": decimals,
        "metadataOk": ok,
        "source": "chain_live_erc20" if (symbol or decimals is not None) else "unavailable",
    }


def checkmarks_prefix(address: str) -> str:
    return address[:10].upper()


def _unique_pool_token_addresses(index: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    for pool in index.get("pools") or []:
        if not isinstance(pool, dict):
            continue
        for key in ("token0", "token1"):
            address = _normalized_address(pool.get(key))
            if address:
                seen.add(address)
    return list(seen)


def _build_metadata(
    *,
    rpc_url: str,
    index: dict[str, Any],
    worker_count: int = 8,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    addresses = _unique_pool_token_addresses(index)
    providers = [
        Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout_seconds}))
        for _ in range(max(1, worker_count))
    ]
    rows: dict[str, Any] = {}
    errors: list[str] = []

    def _worker(provider, address):
        try:
            return address, _metadata_for_address(provider, address)
        except Exception as exc:
            return address, {
                "address": Web3.to_checksum_address(address),
                "symbol": checkmarks_prefix(address),
                "decimals": None,
                "metadataOk": False,
                "source": "fetch_error",
                "error": str(exc)[:160],
            }

    with ThreadPoolExecutor(max_workers=max(1, worker_count)) as executor:
        futures = [
            executor.submit(_worker, providers[index % len(providers)], address)
            for index, address in enumerate(addresses)
        ]
        for future in as_completed(futures):
            address, row = future.result()
            rows[address] = row
            if row.get("metadataOk") is False:
                errors.append(f"{address}:{row.get('error') or row.get('source') or 'unavailable'}")
    symbol_ok = sum(1 for row in rows.values() if row.get("metadataOk"))
    return {
        "schemaVersion": "robinhood-v4-token-metadata-v1",
        "network": NETWORK,
        "chainId": CHAIN_ID,
        "rpcUrl": rpc_url,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tokenAddressCount": len(rows),
        "metadataOkCount": symbol_ok,
        "metadataMissingCount": len(rows) - symbol_ok,
        "errors": errors[:100],
        "tokens": rows,
    }


def _merge_metadata_into_pool_index(index: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    tokens = metadata.get("tokens") if isinstance(metadata.get("tokens"), dict) else {}
    updated_pool_count = 0
    for pool in index.get("pools") or []:
        if not isinstance(pool, dict):
            continue
        pool_key = pool.get("poolKey") if isinstance(pool.get("poolKey"), dict) else {}
        changed = False
        for side in (0, 1):
            address = _normalized_address(pool.get(f"token{side}") or pool_key.get(f"currency{side}") or pool_key.get(f"token{side}"))
            row = tokens.get(address)
            if not row:
                continue
            symbol = str(row.get("symbol") or "").strip()
            decimals = row.get("decimals")
            if symbol or decimals is not None:
                changed = True
                pool[f"token{side}Symbol"] = symbol or pool.get(f"token{side}Symbol")
                pool[f"token{side}Decimals"] = decimals
                pool_key[f"token{side}Symbol"] = symbol or pool_key.get(f"token{side}Symbol")
                pool_key[f"token{side}Decimals"] = decimals
        if changed:
            symbol0 = str(pool.get("token0Symbol") or pool_key.get("token0Symbol") or "")
            symbol1 = str(pool.get("token1Symbol") or pool_key.get("token1Symbol") or "")
            pool["symbolPair"] = f"{symbol0}:{symbol1}".strip(":")
            updated_pool_count += 1
    index["symbolPairsUpdatedFromMetadata"] = True
    index["tokenMetadataSource"] = metadata.get("rpcUrl") or RPC_URL
    index["tokenMetadataOkCount"] = metadata.get("metadataOkCount") or 0
    index["tokenMetadataPoolCount"] = updated_pool_count
    return index


def _merge_metadata_into_eth_pair_tokens(payload: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    tokens = metadata.get("tokens") if isinstance(metadata.get("tokens"), dict) else {}
    updated = 0
    rows = payload.get("ycZc") if isinstance(payload.get("ycZc"), list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        address = _normalized_address(row.get("address"))
        meta = tokens.get(address)
        if not meta:
            continue
        symbol = str(meta.get("symbol") or "").strip()
        if symbol:
            old = str(row.get("symbol") or "")
            if old != symbol:
                row["symbol"] = symbol
                updated += 1
    payload["metadataUpdatedFrom"] = metadata.get("rpcUrl") or RPC_URL
    payload["metadataUpdatedTokenCount"] = updated
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh Robinhood mainnet V4 token metadata and apply it to evidence files.")
    parser.add_argument("--rpc-url", default=RPC_URL)
    parser.add_argument("--idx", default="")
    parser.add_argument("--metadata-output", default="")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    pool_index_path = Path(args.idx) if args.idx else _default_pool_index_path()
    index = _read_json(pool_index_path)
    metadata_output = Path(args.metadata_output) if args.metadata_output else _default_token_metadata_path()
    metadata = _build_metadata(
        rpc_url=args.rpc_url,
        index=index,
        worker_count=max(1, args.workers),
    )
    merged_index = _merge_metadata_into_pool_index(index, metadata)
    eth_pair_path = _default_eth_pair_tokens_path()
    eth_pair = _read_json(eth_pair_path)
    merged_eth_pair = _merge_metadata_into_eth_pair_tokens(eth_pair, metadata) if eth_pair else {"error": "eth_pair_tokens_missing"}
    if not args.no_write:
        _write_json(metadata_output, metadata)
        _write_json(pool_index_path, merged_index)
        if eth_pair:
            _write_json(eth_pair_path, merged_eth_pair)
    print(json.dumps({
        "poolIndex": str(pool_index_path),
        "metadataOutput": str(metadata_output),
        "ethPairTokens": str(eth_pair_path) if eth_pair else None,
        "uniqueTokenAddresses": metadata.get("tokenAddressCount"),
        "metadataOkCount": metadata.get("metadataOkCount"),
        "poolIndexUpdatedPoolCount": merged_index.get("tokenMetadataPoolCount"),
        "ethPairUpdatedSymbolCount": merged_eth_pair.get("metadataUpdatedTokenCount") if isinstance(merged_eth_pair, dict) else 0,
        "errors": metadata.get("errors")[:20],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
