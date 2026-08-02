from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any, Callable, Iterable, Mapping
from urllib import request as urllib_request

from web3 import Web3


DEFAULT_GRAPH_BORROWERS_QUERY = """
query LiquidationBorrowers($first: Int!) {
  borrows(first: $first, orderBy: timestamp, orderDirection: desc) {
    user {
      id
    }
    borrower {
      id
    }
    onBehalfOf {
      id
    }
  }
}
""".strip()

FetchJson = Callable[[str, dict[str, Any] | None, float], Any]


@dataclass(frozen=True)
class ExternalIndexConfig:
    enabled: bool = False
    url: str = ""
    timeout_seconds: float = 8.0
    limit: int = 5000
    method: str = "POST"
    graph_query: str = DEFAULT_GRAPH_BORROWERS_QUERY
    source: str = "external-index-coarse"


def external_index_config_from_env(env: Mapping[str, str] | None = None) -> ExternalIndexConfig:
    values = env or os.environ
    enabled = _env_bool(values.get("LIQUIDATION_EXTERNAL_INDEX_ENABLED"), False)
    url = str(values.get("LIQUIDATION_EXTERNAL_INDEX_URL") or "").strip()
    limit = _safe_int(
        values.get("LIQUIDATION_EXTERNAL_INDEX_LIMIT")
        or values.get("LIQUIDATION_BORROW_DISCOVERY_LIMIT")
        or values.get("LIQUIDATION_MAX_CANDIDATES"),
        5000,
    )
    timeout_seconds = _safe_float(values.get("LIQUIDATION_EXTERNAL_INDEX_TIMEOUT_SECONDS"), 8.0)
    graph_query = str(values.get("LIQUIDATION_EXTERNAL_INDEX_GRAPH_QUERY") or DEFAULT_GRAPH_BORROWERS_QUERY).strip()
    raw_method = str(values.get("LIQUIDATION_EXTERNAL_INDEX_METHOD") or "").strip().upper()
    method = raw_method or ("POST" if graph_query else "GET")
    if method not in {"GET", "POST"}:
        method = "POST"
    return ExternalIndexConfig(
        enabled=enabled,
        url=url,
        timeout_seconds=max(0.1, timeout_seconds),
        limit=max(0, limit),
        method=method,
        graph_query=graph_query,
    )


def fetch_external_borrower_accounts(
    *,
    pool_address: str = "",
    from_block: int | None = None,
    to_block: int | None = None,
    config: ExternalIndexConfig | None = None,
    fetch_json: FetchJson | None = None,
) -> dict[str, Any]:
    index_config = config or external_index_config_from_env()
    result: dict[str, Any] = {
        "enabled": index_config.enabled,
        "configured": bool(index_config.url),
        "source": index_config.source,
        "count": 0,
        "accounts": [],
        "error": None,
        "requires_onchain_verification": True,
    }
    if not index_config.enabled:
        return result
    if not index_config.url:
        result["error"] = "LIQUIDATION_EXTERNAL_INDEX_URL is required when external index is enabled"
        return result

    try:
        body = _request_body(index_config, pool_address, from_block, to_block)
        payload = (fetch_json or _http_json)(index_config.url, body, index_config.timeout_seconds)
        accounts = normalize_external_accounts(payload, index_config.limit)
        result["accounts"] = accounts
        result["count"] = len(accounts)
        if isinstance(payload, dict) and payload.get("errors"):
            result["error"] = _graph_error_message(payload.get("errors"))
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result


def normalize_external_accounts(payload: Any, limit: int = 5000) -> list[str]:
    return normalize_account_candidates(_iter_external_account_values(payload), limit=limit)


def normalize_account_candidates(accounts: Iterable[Any], limit: int = 5000) -> list[str]:
    normalized: list[str] = []
    max_count = max(0, int(limit or 0))
    for account in accounts:
        try:
            checksum = Web3.to_checksum_address(str(account).strip())
        except ValueError:
            continue
        if checksum in normalized:
            continue
        normalized.append(checksum)
        if max_count > 0 and len(normalized) >= max_count:
            break
    return normalized


def merge_candidate_accounts(
    onchain_accounts: Iterable[Any],
    external_accounts: Iterable[Any],
    *,
    limit: int = 5000,
) -> list[str]:
    return normalize_account_candidates([*onchain_accounts, *external_accounts], limit=limit)


def _request_body(
    config: ExternalIndexConfig,
    pool_address: str,
    from_block: int | None,
    to_block: int | None,
) -> dict[str, Any] | None:
    if config.method == "GET":
        return None
    variables = {
        "first": min(config.limit or 1000, 1000),
        "pool": str(pool_address or "").lower(),
        "fromBlock": from_block,
        "toBlock": to_block,
    }
    if config.graph_query:
        return {"query": config.graph_query, "variables": variables}
    return {"limit": config.limit, "pool": pool_address, "fromBlock": from_block, "toBlock": to_block}


def _http_json(url: str, body: dict[str, Any] | None, timeout_seconds: float) -> Any:
    headers = {"Accept": "application/json"}
    data = None
    method = "GET"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    req = urllib_request.Request(url, data=data, headers=headers, method=method)
    with urllib_request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310 - URL is operator configured.
        return json.loads(response.read().decode("utf-8"))


def _iter_external_account_values(value: Any, parent_key: str = ""):
    parent = _normalize_key(parent_key)
    if isinstance(value, str):
        if parent in _ACCOUNT_VALUE_KEYS:
            yield value
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_external_account_values(item, parent_key)
        return
    if not isinstance(value, dict):
        return

    for key, item in value.items():
        normalized_key = _normalize_key(key)
        if normalized_key in _DIRECT_ACCOUNT_KEYS:
            if isinstance(item, str):
                yield item
            elif isinstance(item, dict):
                yield from _iter_external_account_values(item, normalized_key)
            elif isinstance(item, (list, tuple)):
                for child in item:
                    yield from _iter_external_account_values(child, normalized_key)
        elif normalized_key == "id" and parent in _ACCOUNT_OBJECT_KEYS:
            yield item
        elif normalized_key == "address" and parent in _GENERIC_ADDRESS_PARENT_KEYS:
            yield item
        elif isinstance(item, (dict, list, tuple)):
            yield from _iter_external_account_values(item, normalized_key)


def _normalize_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _env_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _graph_error_message(errors: Any) -> str:
    if not isinstance(errors, list):
        return "external index returned errors"
    messages = [str(item.get("message")) for item in errors if isinstance(item, dict) and item.get("message")]
    return "; ".join(messages[:3]) or "external index returned errors"


_DIRECT_ACCOUNT_KEYS = {
    "account",
    "accountaddress",
    "user",
    "useraddress",
    "borrower",
    "borroweraddress",
    "onbehalfof",
    "onbehalfofaddress",
    "owner",
    "owneraddress",
}
_ACCOUNT_OBJECT_KEYS = {
    "account",
    "accounts",
    "user",
    "users",
    "borrower",
    "borrowers",
    "onbehalfof",
    "owners",
    "owner",
}
_ACCOUNT_VALUE_KEYS = _ACCOUNT_OBJECT_KEYS | {
    "address",
    "addresses",
    "accountaddress",
    "useraddress",
    "borroweraddress",
    "onbehalfofaddress",
    "owneraddress",
}
_GENERIC_ADDRESS_PARENT_KEYS = {
    "",
    "account",
    "accounts",
    "address",
    "addresses",
    "user",
    "users",
    "borrower",
    "borrowers",
    "owner",
    "owners",
    "node",
    "nodes",
    "item",
    "items",
    "result",
    "results",
}
