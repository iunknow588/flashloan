"""Refresh the official Robinhood stock-token registry from /rhj/assets."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import urllib.request
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dex.intent_trade.chains.robinhood_common.stock_token_registry import (  # noqa: E402
    CHAIN_ID,
    NETWORK,
    STOCK_FACTORY,
    default_stock_token_registry_path,
)


ASSETS_API_URL = "https://api.robinhood.com/rhj/assets"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _fetch_assets(api_url: str) -> list[dict[str, Any]]:
    with urllib.request.urlopen(api_url, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assets = payload.get("assets") if isinstance(payload, dict) else payload
    if not isinstance(assets, list):
        raise ValueError(f"{api_url} did not return an assets list")
    return assets


def _to_checksum(address: str) -> str:
    try:
        from web3 import Web3

        return Web3.to_checksum_address(str(address or "").strip())
    except Exception:
        return str(address or "").strip()


def build_registry(assets: list[dict[str, Any]]) -> dict[str, Any]:
    tokens: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    address_symbols: dict[str, str] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        deployments = asset.get("deployments") if isinstance(asset.get("deployments"), list) else []
        deployment = next(
            (row for row in deployments if int(row.get("chainId") or 0) == CHAIN_ID),
            None,
        )
        if not isinstance(deployment, dict):
            continue
        symbol = str(asset.get("tokenSymbol") or "").strip().upper()
        address = _to_checksum(str(deployment.get("contractAddress") or "").strip())
        if not symbol or not address.lower().startswith("0x") or len(address) != 42:
            errors.append(f"{symbol or 'unknown'}:{address or 'missing-address'}")
            continue
        key = symbol.lower()
        if key in tokens:
            errors.append(f"{symbol}:duplicate-symbol")
            continue
        existing_symbol = address_symbols.get(address.lower())
        if existing_symbol is not None:
            errors.append(f"{symbol}:duplicate-address:{address}->{existing_symbol}")
            continue
        try:
            decimals = int(asset.get("tokenDecimals") or 18)
        except (TypeError, ValueError):
            decimals = 18
        address_symbols[address.lower()] = symbol
        tokens[key] = {
            "symbol": symbol,
            "address": address,
            "name": str(asset.get("tokenName") or symbol),
            "decimals": decimals,
            "category": "robinhood_stock",
            "source": "robinhood_assets_api",
            "status": str(asset.get("status") or "ASSET_STATUS_ACTIVE"),
            "isin": str(asset.get("isin") or ""),
            "id": str(asset.get("id") or ""),
        }
    return {
        "schema_version": "robinhood-stock-token-registry-v1",
        "network": NETWORK,
        "chain_id": CHAIN_ID,
        "factory": STOCK_FACTORY,
        "source": ASSETS_API_URL,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "token_count": len(tokens),
        "errors": errors,
        "tokens": tokens,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=ASSETS_API_URL)
    parser.add_argument("--output", default="")
    parser.add_argument("--cache", default="")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    assets = _fetch_assets(args.api_url)
    registry = build_registry(assets)
    if registry.get("errors"):
        raise SystemExit("stock token registry has errors:\n" + "\n".join(registry["errors"]))
    output_path = Path(args.output) if args.output else default_stock_token_registry_path()
    if not args.no_write:
        _write_json(output_path, registry)
        if args.cache:
            cache_path = Path(args.cache)
            _write_json(cache_path, {"source": args.api_url, "assets": assets})
    print(
        json.dumps(
            {
                "output": str(output_path),
                "tokenCount": registry["token_count"],
                "errors": registry["errors"],
                "source": args.api_url,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
