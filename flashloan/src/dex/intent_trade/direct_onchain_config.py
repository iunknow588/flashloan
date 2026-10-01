"""Configuration, network, and address helpers for direct on-chain execution."""
from __future__ import annotations
import json
import os
import re
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Any
from dex.intent_trade.direct_chain_addresses import (
    chain_contracts_for_network,
    owner_address_for_network,
    unified_executor_address_for_network,
)

_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$", re.IGNORECASE)


def _impl():
    from dex.intent_trade import direct_impl

    return direct_impl
def env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value and value != "0x...":
            return value
    return default


def env_bool(*names: str) -> bool:
    for name in names:
        value = os.getenv(name, "").strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
    return False


def first_value(*objects: Any, names: tuple[str, ...]) -> Any:
    for source in objects:
        if not isinstance(source, dict):
            continue
        for name in names:
            value = source.get(name)
            if value not in (None, ""):
                return value
    return None


def positive_int_value(value: Any, *, default: int = 0) -> int:
    raw = str(value if value is not None else "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw, 10)
    except ValueError as exc:
        raise ValueError(f"expected a positive integer, got {value!r}") from exc
    if parsed < 0:
        raise ValueError(f"expected a positive integer, got {parsed}")
    return parsed


def usdc_decimal_to_base_units(value: Any) -> int:
    raw = str(value if value is not None else "").strip()
    if not raw:
        return 0
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"expected a USDC decimal amount, got {value!r}") from exc
    if amount < 0:
        raise ValueError(f"expected a non-negative USDC amount, got {value!r}")
    return int((amount * Decimal("1000000")).to_integral_value(rounding=ROUND_CEILING))


def normalize_pair_address(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower() == "0x..." or not _ADDRESS_RE.match(text):
        return ""
    return "0x" + text[2:].lower()
def max_broadcast_gas_price_wei() -> int:
    return positive_int_value(
        env(
            "TRIANGULAR_MAX_GAS_PRICE_WEI",
            "UNIFIED_EXECUTOR_MAX_GAS_PRICE_WEI",
            default="0",
        )
    )


def broadcast_gas_guard(gas_units: Any, gas_estimate: Any) -> dict[str, Any]:
    gas = positive_int_value(gas_units)
    max_fee = positive_int_value(getattr(gas_estimate, "max_fee", 0))
    strategy = str(getattr(gas_estimate, "strategy", "") or "")
    cap = max_broadcast_gas_price_wei()
    report = {
        "gasUnits": str(gas),
        "gasPriceWei": str(max_fee),
        "estimatedCostWei": str(gas * max_fee),
        "strategy": strategy,
        "configuredMaxGasPriceWei": str(cap),
    }
    if strategy == "blocked" or max_fee == 0:
        return {
            "ok": False,
            "status": "gas_price_unavailable",
            "blocked_reason": "gas_price_unavailable",
            "report": report,
        }
    if cap and max_fee > cap:
        return {
            "ok": False,
            "status": "gas_price_cap_exceeded",
            "blocked_reason": "gas_price_cap_exceeded",
            "report": report,
        }
    return {
        "ok": True,
        "status": "gas_price_accepted",
        "blocked_reason": None,
        "report": report,
    }


def normalize_direct_onchain_network(value: Any) -> str:
    key = str(value or "").strip().lower().replace("_", "-")
    if not key:
        return ""
    return _impl().DIRECT_ONCHAIN_NETWORK_ALIASES.get(key, key)


def chain_id_value(value: Any) -> int | None:
    raw = str(value if value is not None else "").strip()
    if not raw:
        return None
    try:
        return int(raw, 10)
    except ValueError:
        return None


def resolve_direct_onchain_network(protocol: dict[str, Any] | None = None) -> tuple[str, int, bool]:
    impl = _impl()
    protocol = protocol if isinstance(protocol, dict) else {}
    explicit_chain_id = chain_id_value(
        impl._first_value(protocol, names=("direct_chain_id", "onchain_chain_id", "chain_id", "chainId"))
    )
    if explicit_chain_id is not None:
        by_chain = normalize_direct_onchain_network(str(explicit_chain_id))
        if by_chain in impl.DIRECT_ONCHAIN_NETWORKS:
            config = impl.DIRECT_ONCHAIN_NETWORKS[by_chain]
            return by_chain, int(config["chain_id"]), bool(config["testnet"])

    configured = (
        env("TRIANGULAR_DIRECT_NETWORK", "TRIANGULAR_ONCHAIN_NETWORK")
        or env("TRIANGULAR_TESTNET_NAME", "TRIANGULAR_NETWORK")
        or impl._first_value(protocol, names=("direct_network", "onchain_network", "network"))
        or "avalanche"
    )
    network = normalize_direct_onchain_network(configured)
    if network not in impl.DIRECT_ONCHAIN_NETWORKS:
        supported = ", ".join(sorted(impl.DIRECT_ONCHAIN_NETWORKS))
        raise ValueError(f"unsupported direct on-chain network: {network}; supported networks: {supported}")
    config = impl.DIRECT_ONCHAIN_NETWORKS[network]
    return network, int(config["chain_id"]), bool(config["testnet"])


def direct_rpc_url(network: str) -> tuple[str, tuple[str, ...]]:
    networks = _impl().DIRECT_ONCHAIN_NETWORKS
    config = networks.get(network, networks["avalanche"])
    names = tuple(config["rpc_env"])
    return env(*names), names


def unified_executor_supported_for_network(network: str) -> bool:
    normalized = normalize_direct_onchain_network(network)
    return bool(_impl().DIRECT_ONCHAIN_NETWORKS.get(normalized, {}).get("unified_executor_supported", False))


def executor_adapter_unverified_status(network: str) -> str:
    normalized = normalize_direct_onchain_network(network)
    if normalized == "bnb":
        return "bnb_executor_adapter_unverified"
    if normalized in {"robinhood", "robinhood-testnet"}:
        return "robinhood_executor_adapter_unverified"
    return f"{normalized or 'direct'}_executor_adapter_unverified"


def executor_adapter_unverified_error(network: str) -> str:
    normalized = normalize_direct_onchain_network(network)
    label = {
        "bnb": "BNB",
        "robinhood": "Robinhood",
        "robinhood-testnet": "Robinhood testnet",
    }.get(normalized, normalized or "direct")
    return (
        f"{label} direct execution is disabled until a chain-specific "
        "flash-loan executor adapter is verified"
    )


def parse_pair_id(raw: Any) -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        pair_id = int(str(raw).strip(), 10)
    except ValueError as exc:
        raise ValueError(f"pair_id must be a non-negative integer, got {raw!r}") from exc
    if pair_id < 0:
        raise ValueError(f"pair_id must be a non-negative integer, got {pair_id}")
    return pair_id


def explicit_pair_id(protocol: dict[str, Any]) -> int | None:
    raw = protocol.get("pair_id")
    if raw is None:
        raw = protocol.get("usdc_pair_id")
    return parse_pair_id(raw)


def env_pair_id() -> int | None:
    return parse_pair_id(env("TRIANGULAR_USDC_PAIR_ID", "TRIANGULAR_PAIR_ID"))


def normalize_usdc_pair_table(pairs: Any) -> list[dict[str, str]]:
    if not isinstance(pairs, (list, tuple)):
        raise ValueError("USDC pair table must be a list")
    normalized = []
    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            raise ValueError(f"USDC pair table entry {index} must be an object")
        token_x = normalize_pair_address(pair.get("tokenX") or pair.get("token_x") or pair.get("tokex"))
        token_y = normalize_pair_address(pair.get("tokenY") or pair.get("token_y") or pair.get("tokey"))
        router = normalize_pair_address(pair.get("router"))
        if not token_x:
            raise ValueError(f"USDC pair table entry {index} tokenX must be a valid address")
        if not token_y:
            raise ValueError(f"USDC pair table entry {index} tokenY must be a valid address")
        if token_x == token_y:
            raise ValueError(f"USDC pair table entry {index} tokenX and tokenY must be different")
        entry = {"tokenX": token_x, "tokenY": token_y}
        if router:
            entry["router"] = router
        normalized.append(entry)
    return normalized


def env_usdc_pair_table() -> list[dict[str, str]]:
    value = env("TRIANGULAR_USDC_PAIRS_JSON")
    if value:
        return normalize_usdc_pair_table(json.loads(value))
    token_x = env("TRIANGULAR_TOKEN_X")
    token_y = env("TRIANGULAR_TOKEN_Y")
    router = env("TRIANGULAR_DEX_ROUTER", "DEX_ROUTER_ADDRESS", "FUJI_DEX_ROUTER")
    if token_x and token_y:
        return normalize_usdc_pair_table([{"tokenX": token_x, "tokenY": token_y, "router": router}])
    return []


def local_usdc_pair_memory_table(protocol: dict[str, Any] | None = None) -> list[dict[str, str]]:
    impl = _impl()
    if protocol and isinstance(protocol.get("usdc_pairs"), list):
        return normalize_usdc_pair_table(protocol["usdc_pairs"])
    if impl._USDC_PAIR_MEMORY_TABLE:
        return list(impl._USDC_PAIR_MEMORY_TABLE)
    return env_usdc_pair_table()


def runtime_candidate_pairs(
    protocol: dict[str, Any],
    opportunity: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    impl = _impl()
    pairs_source = None
    for source in (protocol, opportunity):
        if not isinstance(source, dict):
            continue
        for key in ("candidate_pairs", "usdc_candidate_pairs", "usdc_pairs"):
            if isinstance(source.get(key), list):
                pairs_source = source[key]
                break
        if pairs_source is not None:
            break
    if pairs_source is None:
        return []

    default_router = normalize_pair_address(
        impl._first_value(protocol, opportunity, names=("router", "dex_router", "router_address"))
        or env("TRIANGULAR_DEX_ROUTER", "DEX_ROUTER_ADDRESS", "FUJI_DEX_ROUTER")
    )
    candidates = []
    for index, pair in enumerate(normalize_usdc_pair_table(pairs_source)):
        router = normalize_pair_address(pair.get("router")) or default_router
        if not router:
            raise ValueError(f"runtime candidate pair {index} router must be a valid address")
        candidates.append({"tokenX": pair["tokenX"], "tokenY": pair["tokenY"], "router": router})
    return candidates
