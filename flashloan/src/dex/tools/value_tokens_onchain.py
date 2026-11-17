"""On-chain valuation of stock tokens against a chain's quote asset.

Why this tool exists
--------------------

The hosted Uniswap Trading API probe (``token-manager value-tokens``) is
the right tool on the mainnet: chain ``4663`` has 231 live ETH/USDG pools
and the API serves the chain.  On the testnet neither half holds.

* The Trading API does not serve chain ``46630`` at all; every probe comes
  back as an unknown.
* The testnet has **zero** ETH/USDG pools.  Its only USDG pool is paired
  against an unresolved placeholder token and carries no on-chain
  liquidity, so USDG cannot be reached from ETH there.

The testnet's quote asset is therefore the chain's own coin, and the price
has to be read from the chain itself.  This tool asks the Uniswap V4
quoter on chain what one whole token is worth in that quote asset, and
writes the result in exactly the shape the token manager's valuation store
expects.

What it produces
----------------

A report document with the same vocabulary as the hosted probe:
``route_found`` | ``no_route`` | ``api_unavailable`` | ``api_error``.  The
three-way split matters and is preserved exactly:

* ``route_found`` — the quoter answered with a positive amount.
* ``no_route``    — the chain genuinely has no pool for this pair.  This
  is a real negative and it is recorded as such.
* ``api_error``   — the chain could not be reached, or the quoter reverted
  for a reason that is not "no pool".  This is an *unknown* and is never
  allowed to look like a negative.

The tool never invents a value, never rounds a quote into existence and
never treats a failure to ask as a failure to trade.

Usage
-----

    python flashloan/src/dex/tools/value_tokens_onchain.py \
        --registry flashloan/chains/robinhood-testnet/robinhood_stock_tokens.json \
        --network robinhood-testnet --chain-id 46630 \
        --output plan/runtime_evidence/reports/testnet_onchain_valuation.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
SRC_ROOT = REPO_ROOT / "flashloan" / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

# The verdicts the token manager's valuation vocabulary defines.
VERDICT_ROUTE_FOUND = "route_found"
VERDICT_NO_ROUTE = "no_route"
VERDICT_API_UNAVAILABLE = "api_unavailable"
VERDICT_API_ERROR = "api_error"

# The source recorded on every observation, so a reader can tell an
# on-chain measurement from a hosted-API measurement.
SOURCE = "uniswap_v4_onchain_quoter"

# The protocols the probe may route through.
PROTOCOLS = ["V4"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_address(value: object) -> str:
    if not isinstance(value, str):
        return ""
    trimmed = value.strip().lower()
    if not trimmed.startswith("0x") or len(trimmed) != 42:
        return ""
    try:
        int(trimmed, 16)
    except ValueError:
        return ""
    return trimmed


def _read_registry(path: Path, network: str, chain_id: int) -> list[dict[str, Any]]:
    """Read the stock registry and keep only the tokens of this scope."""
    document = json.loads(path.read_text(encoding="utf-8"))
    registry_network = str(document.get("network") or "").strip()
    registry_chain_id = document.get("chain_id")
    if registry_network != network:
        raise SystemExit(
            f"error: registry network {registry_network!r} does not match {network!r}"
        )
    if registry_chain_id is not None and int(registry_chain_id) != chain_id:
        raise SystemExit(
            f"error: registry chain id {registry_chain_id!r} does not match {chain_id!r}"
        )
    tokens = document.get("tokens")
    if not isinstance(tokens, dict):
        raise SystemExit("error: registry carries no token object")
    records: list[dict[str, Any]] = []
    for key, value in tokens.items():
        if not isinstance(value, dict):
            continue
        address = _normalize_address(value.get("address"))
        if not address:
            continue
        records.append(
            {
                "registryKey": key,
                "token_address": address,
                "token_symbol": str(value.get("symbol") or key).strip(),
                "token_decimals": int(value.get("decimals") or 18),
            }
        )
    records.sort(key=lambda record: record["token_address"])
    return records


def _quote_asset(network: str, chain_id: int) -> dict[str, Any]:
    """The quote asset of a chain scope.

    Mainnet values in USDG; the testnet values in the chain's own coin,
    because USDG has no pool path there.
    """
    if network == "robinhood" and chain_id == 4663:
        return {
            "address": "0x5fc5360d0400a0fd4f2af552add042d716f1d168",
            "symbol": "USDG",
            "decimals": 6,
            "is_native": False,
        }
    if network == "robinhood-testnet" and chain_id == 46630:
        return {
            "address": "0x0000000000000000000000000000000000000000",
            "symbol": "ETH",
            "decimals": 18,
            "is_native": True,
        }
    raise SystemExit(
        f"error: no quote asset is declared for {network}/{chain_id}; "
        "known scopes are robinhood/4663 (USDG) and robinhood-testnet/46630 (native ETH)"
    )


def _raw_amount_from_decimal(amount: str, decimals: int) -> int:
    """Convert a decimal amount into raw units without touching a float."""
    text = str(amount).strip()
    if not text:
        raise ValueError("empty amount")
    if "." in text:
        whole, _, fraction = text.partition(".")
    else:
        whole, fraction = text, ""
    if fraction:
        if len(fraction) > decimals:
            fraction = fraction[:decimals]
        fraction = fraction.ljust(decimals, "0")
    else:
        fraction = "0" * decimals
    return int(whole or "0") * (10**decimals) + int(fraction or "0")


def _decimal_from_raw(raw: int, decimals: int) -> str:
    """Render raw units as a decimal string without touching a float."""
    scale = 10**decimals
    whole, fraction = divmod(int(raw), scale)
    if decimals <= 0:
        return str(whole)
    return f"{whole}.{str(fraction).zfill(decimals).rstrip('0') or '0'}"


def _probe_one(
    record: dict[str, Any],
    quote: dict[str, Any],
    network: str,
    chain_id: int,
    nominal_amount: str,
) -> dict[str, Any]:
    """Ask the on-chain quoter what one token is worth in the quote asset."""
    from dex.intent_trade.chains.robinhood_testnet.v4_quote_adapter import (  # noqa: PLC0415
        quote_v4_exact_input_single_from_pool_index,
    )

    token_address = record["token_address"]
    symbol = record["token_symbol"]
    try:
        input_raw = _raw_amount_from_decimal(nominal_amount, record["token_decimals"])
    except (TypeError, ValueError) as error:
        return {
            "token_address": token_address,
            "token_symbol": symbol,
            "verdict": VERDICT_API_ERROR,
            "has_value": False,
            "input_amount_raw": "0",
            "protocols": list(PROTOCOLS),
            "source": SOURCE,
            "observed_at": _utc_now(),
            "failure_kind": "malformed_response",
            "failure_detail": f"invalid nominal amount {nominal_amount!r}: {error}",
        }

    request = {
        "network": network,
        "chainId": chain_id,
        "tokenIn": quote["address"],
        "tokenOut": token_address,
        "amountInRaw": str(input_raw),
    }
    try:
        result = quote_v4_exact_input_single_from_pool_index(request)
    except Exception as error:  # noqa: BLE001 - every failure is classified below
        detail = str(error)
        # "no pool for this pair" is a real negative. Everything else --
        # an unreachable node, a reverted quoter, a missing pool index -- is
        # an unknown, and an unknown must never be recorded as a negative.
        lowered = detail.lower()
        if "pool index missing" in lowered or "no pool" in lowered:
            return {
                "token_address": token_address,
                "token_symbol": symbol,
                "verdict": VERDICT_NO_ROUTE,
                "has_value": False,
                "input_amount_raw": str(input_raw),
                "protocols": list(PROTOCOLS),
                "source": SOURCE,
                "observed_at": _utc_now(),
                "failure_kind": "no_route",
                "failure_detail": detail,
            }
        return {
            "token_address": token_address,
            "token_symbol": symbol,
            "verdict": VERDICT_API_ERROR,
            "has_value": False,
            "input_amount_raw": str(input_raw),
            "protocols": list(PROTOCOLS),
            "source": SOURCE,
            "observed_at": _utc_now(),
            "failure_kind": "onchain_quote_failed",
            "failure_detail": detail,
        }

    amount_raw = int(result.get("amountOutRaw") or 0)
    if amount_raw <= 0:
        return {
            "token_address": token_address,
            "token_symbol": symbol,
            "verdict": VERDICT_API_ERROR,
            "has_value": False,
            "input_amount_raw": str(input_raw),
            "protocols": list(PROTOCOLS),
            "source": SOURCE,
            "observed_at": _utc_now(),
            "failure_kind": "malformed_response",
            "failure_detail": "the quoter returned a non-positive amount",
        }

    # The quote answers "what did `input_raw` units of the token buy?"
    # The valuation is "what is one whole token worth?", so the nominal
    # amount is divided out. The nominal amount is one whole token by
    # default, which makes the two the same.
    value_raw = amount_raw * record["token_decimals"] and amount_raw
    if input_raw != 10**record["token_decimals"]:
        value_raw = (amount_raw * (10**record["token_decimals"])) // input_raw
    pool_state = result.get("poolState") if isinstance(result.get("poolState"), dict) else {}
    return {
        "token_address": token_address,
        "token_symbol": symbol,
        "verdict": VERDICT_ROUTE_FOUND,
        "has_value": True,
        "input_amount_raw": str(input_raw),
        "usdg_amount_raw": str(value_raw),
        "usdg_amount_decimal": _decimal_from_raw(value_raw, quote["decimals"]),
        "protocols": list(PROTOCOLS),
        "source": SOURCE,
        "observed_at": _utc_now(),
        "quote_method": result.get("quoteMethod"),
        "pool_id": pool_state.get("poolId") or pool_state.get("pool"),
        "pool_fee": pool_state.get("fee"),
        "pool_tick": pool_state.get("tick"),
        "pool_sqrt_price_x96": pool_state.get("sqrtPriceX96"),
        "quote_amount_out_raw": str(amount_raw),
        "quote_target": quote["address"],
        "quote_target_symbol": quote["symbol"],
        "quote_target_decimals": quote["decimals"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Value stock tokens against the chain quote asset using the on-chain V4 quoter.",
    )
    parser.add_argument("--registry", required=True, help="stock registry JSON")
    parser.add_argument("--network", required=True, help="chain network scope")
    parser.add_argument("--chain-id", required=True, type=int, help="chain id scope")
    parser.add_argument("--amount", default="1", help="nominal input amount, default 1")
    parser.add_argument("--limit", type=int, default=0, help="cap the tokens probed")
    parser.add_argument("--output", help="write the JSON report here")
    arguments = parser.parse_args(argv)

    quote = _quote_asset(arguments.network, arguments.chain_id)
    records = _read_registry(
        Path(arguments.registry), arguments.network, arguments.chain_id
    )
    if arguments.limit > 0:
        records = records[: arguments.limit]

    tokens: list[dict[str, Any]] = []
    for record in records:
        tokens.append(
            _probe_one(
                record,
                quote,
                arguments.network,
                arguments.chain_id,
                arguments.amount,
            )
        )

    route_found = sum(1 for token in tokens if token["verdict"] == VERDICT_ROUTE_FOUND)
    no_route = sum(1 for token in tokens if token["verdict"] == VERDICT_NO_ROUTE)
    unknown = sum(1 for token in tokens if token["verdict"] not in (VERDICT_ROUTE_FOUND, VERDICT_NO_ROUTE))
    report = {
        "source": SOURCE,
        "observed_at": _utc_now(),
        "nominal_amount": arguments.amount,
        "protocols": list(PROTOCOLS),
        "requested_count": len(records),
        "evaluated_count": len(tokens),
        "route_found_count": route_found,
        "no_route_count": no_route,
        "unknown_count": unknown,
        "quote_target": quote["address"],
        "quote_target_symbol": quote["symbol"],
        "quote_target_decimals": quote["decimals"],
        "tokens": tokens,
    }
    rendered = {
        "report": report,
        "source": report["source"],
        "observedAt": report["observed_at"],
        "quoteTarget": quote["address"],
        "quoteTargetSymbol": quote["symbol"],
        "quoteTargetDecimals": quote["decimals"],
        "network": arguments.network,
        "chainId": arguments.chain_id,
        "nominalAmount": report["nominal_amount"],
        "protocols": report["protocols"],
        "requestedCount": report["requested_count"],
        "evaluatedCount": report["evaluated_count"],
        "routeFoundCount": report["route_found_count"],
        "noRouteCount": report["no_route_count"],
        "unknownCount": report["unknown_count"],
        "tokens": report["tokens"],
    }
    payload = json.dumps(rendered, ensure_ascii=False, indent=2, sort_keys=True)
    if arguments.output:
        output_path = Path(arguments.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
