from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_contracts, chain_data_by_network, chain_data_file_path
from intent_trade.chains.robinhood.shared import (
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
)
from intent_trade.chains.robinhood_testnet.v4_flash_accounting_sources import (
    ZERO_ADDRESS,
    v4_flash_accounting_source_rows,
)
DEFAULT_PROBE_DEPLOYMENT_FILE = (
    Path("contract") / "contracts-dex" / "deployments" / "robinhood-v4-flash-normal-probe-robinhoodTestnet.json"
)


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: object) -> str:
    return str(value or "").strip()


def _normalize_address(value: object) -> str:
    text = _text(value)
    if text.startswith("0x") and len(text) == 42:
        return text.lower()
    return ""


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except Exception:
        return None
    if not parsed.is_finite():
        return None
    return parsed


def _int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _source_rows(liquidity: dict[str, Any], *, pool_manager: str) -> list[dict[str, Any]]:
    try:
        return v4_flash_accounting_source_rows(liquidity, pool_manager=pool_manager)
    except Exception:
        return []


def _best_source_row(
    source_rows: list[dict[str, Any]],
    *,
    token_address: str,
    token_symbol: str,
) -> dict[str, Any]:
    if token_address:
        for row in source_rows:
            row_address = _normalize_address(row.get("currency_address") or row.get("token_address"))
            if row_address == token_address:
                return row
    if token_symbol:
        normalized = token_symbol.upper()
        for row in source_rows:
            if _text(row.get("token_symbol")).upper() == normalized:
                return row
    stable_rows = [row for row in source_rows if row.get("is_stable")]
    if stable_rows:
        return stable_rows[0]
    return source_rows[0] if source_rows else {}


def _source_row_balance_raw(source_row: dict[str, Any]) -> int:
    for key in ("poolmanager_balance_raw", "available_liquidity_raw", "balance_raw"):
        balance = _int(source_row.get(key))
        if balance is not None and balance > 0:
            return balance
    return 0


def _load_plan() -> dict[str, Any]:
    return _read_json(chain_data_file_path(ROBINHOOD_TESTNET_NETWORK, "chain_test"))


def _load_liquidity() -> dict[str, Any]:
    return _read_json(chain_data_file_path(ROBINHOOD_TESTNET_NETWORK, "v4_pool_liquidity"))


def _plan_route_hops(plan: dict[str, Any]) -> list[dict[str, Any]]:
    preflight = _as_dict(plan.get("preflightSpec"))
    return [item for item in _as_list(preflight.get("routeHops")) if isinstance(item, dict)]


def _resolve_probe_address(
    *,
    chain_metadata: dict[str, Any],
    contracts_metadata: dict[str, Any],
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    probe = _as_dict(contracts_metadata.get("normalTestnetProbe"))
    if probe:
        candidates.append(
            {
                "address": _normalize_address(probe.get("address")),
                "source": "contracts_metadata.normalTestnetProbe.address",
                "deploymentFile": _text(probe.get("deploymentFile")),
            }
        )
    probe = _as_dict(chain_metadata.get("normalTestnetProbe"))
    if probe:
        candidates.append(
            {
                "address": _normalize_address(probe.get("address")),
                "source": "chain_metadata.normalTestnetProbe.address",
                "deploymentFile": _text(probe.get("deploymentFile")),
            }
        )
    for address_key in (
        "normal_testnet_probe",
        "normalTestnetProbe",
        "normal_testnet_probe_address",
        "normalTestnetProbeAddress",
    ):
        address = _normalize_address(chain_contracts(ROBINHOOD_TESTNET_NETWORK).get(address_key))
        if address:
            candidates.append(
                {
                    "address": address,
                    "source": f"chain_contracts.{address_key}",
                    "deploymentFile": _text(chain_contracts(ROBINHOOD_TESTNET_NETWORK).get("normal_testnet_probe_deployment_file")),
                }
            )

    deployment_path = _resolve_probe_deployment_file(chain_metadata=chain_metadata, contracts_metadata=contracts_metadata)
    if deployment_path is not None:
        deployment = _read_json(deployment_path)
        address = _normalize_address(
            deployment.get("probeAddress")
            or deployment.get("address")
            or _as_dict(deployment.get("probe")).get("address")
        )
        if address:
            candidates.append(
                {
                    "address": address,
                    "source": "deployment_file",
                    "deploymentFile": str(deployment_path),
                }
            )

    for candidate in candidates:
        if candidate.get("address"):
            return candidate

    return {
        "address": "",
        "source": "probe_address_missing",
        "deploymentFile": str(deployment_path) if deployment_path is not None else "",
    }


def _resolve_probe_deployment_file(
    *,
    chain_metadata: dict[str, Any],
    contracts_metadata: dict[str, Any],
) -> Path | None:
    for metadata in (contracts_metadata, chain_metadata):
        probe = _as_dict(metadata.get("normalTestnetProbe"))
        deployment_file = _text(probe.get("deploymentFile"))
        if deployment_file:
            path = Path(deployment_file)
            if not path.is_absolute():
                path = (Path(__file__).resolve().parents[5] / deployment_file).resolve()
            return path
    default_path = (Path(__file__).resolve().parents[5] / DEFAULT_PROBE_DEPLOYMENT_FILE).resolve()
    return default_path


def _resolve_probe_token_and_amount(
    *,
    chain_test_plan: dict[str, Any],
    liquidity: dict[str, Any],
    pool_manager: str,
) -> dict[str, Any]:
    route_hops = _plan_route_hops(chain_test_plan)
    repayment = _as_dict(_as_dict(chain_test_plan.get("preflightSpec")).get("repaymentConstraint"))
    first_hop = route_hops[0] if route_hops else {}
    token_address = _normalize_address(repayment.get("borrowToken")) or _normalize_address(first_hop.get("tokenIn"))
    token_symbol = _text(repayment.get("borrowSymbol")) or _text(first_hop.get("fromSymbol"))
    amount_raw = _int(repayment.get("borrowAmountRaw"))
    if amount_raw is None:
        amount_raw = _int(first_hop.get("amountInRaw"))
    if amount_raw is None:
        amount_raw = 0
    decimals = _int(first_hop.get("inputDecimals")) or _int(first_hop.get("outputDecimals")) or 18
    source_rows = _source_rows(liquidity, pool_manager=pool_manager)
    source_row = _best_source_row(source_rows, token_address=token_address, token_symbol=token_symbol)
    if not token_address:
        token_address = _normalize_address(source_row.get("currency_address") or source_row.get("token_address"))
    if not token_symbol:
        token_symbol = _text(source_row.get("token_symbol"))
    if _int(source_row.get("decimals")):
        decimals = int(source_row["decimals"])
    balance_raw = _source_row_balance_raw(source_row)
    balance_human = None
    if balance_raw > 0:
        try:
            balance_human = format(Decimal(balance_raw) / (Decimal(10) ** int(decimals)), "f")
        except Exception:
            balance_human = None
    human = None
    try:
        human = format(Decimal(amount_raw) / (Decimal(10) ** int(decimals)), "f") if amount_raw else "0"
    except Exception:
        human = None
    return {
        "token": {
            "address": token_address,
            "symbol": token_symbol,
            "decimals": decimals,
            "source": "chain_test_plan.preflightSpec.repaymentConstraint",
            "poolManagerBalanceRaw": str(balance_raw) if balance_raw > 0 else "",
            "poolManagerBalance": balance_human or source_row.get("poolmanager_balance") or source_row.get("available_liquidity") or None,
            "poolManagerSource": source_row.get("source") or "",
        },
        "amount": {
            "amount": str(amount_raw),
            "human": human or str(amount_raw),
            "decimals": decimals,
            "source": "chain_test_plan.preflightSpec.repaymentConstraint.borrowAmountRaw",
        },
        "borrowableLiquidity": {
            "raw": str(balance_raw),
            "human": balance_human,
            "ready": bool(balance_raw > 0 and amount_raw > 0 and balance_raw >= amount_raw),
            "source": source_row.get("source") or "",
        },
        "selectedSource": source_row,
    }


def resolve_robinhood_testnet_normal_flash_probe(
    *,
    chain_metadata: dict[str, Any] | None = None,
    contracts_metadata: dict[str, Any] | None = None,
    chain_test_plan: dict[str, Any] | None = None,
    v4_pool_liquidity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    chain_row = chain_data_by_network(ROBINHOOD_TESTNET_NETWORK) or {}
    chain_metadata = chain_metadata or {}
    contracts_metadata = contracts_metadata or {}
    chain_test_plan = chain_test_plan or _load_plan()
    v4_pool_liquidity = v4_pool_liquidity or _load_liquidity()
    pool_manager = (
        _normalize_address(_as_dict(chain_row.get("contracts")).get("v4_pool_manager"))
        or _normalize_address(_as_dict(chain_metadata.get("contracts")).get("v4_pool_manager"))
        or _normalize_address(_as_dict(contracts_metadata.get("contracts")).get("v4_pool_manager"))
    )
    if not pool_manager:
        pool_manager = _normalize_address(chain_metadata.get("defaultV4PoolManager"))
    if not pool_manager:
        pool_manager = _normalize_address(chain_metadata.get("defaultFlashSource"))
    if not pool_manager:
        pool_manager = _normalize_address(chain_row.get("contracts", {}).get("v4_pool_manager")) if isinstance(chain_row.get("contracts"), dict) else ""

    probe = _resolve_probe_address(chain_metadata=chain_metadata, contracts_metadata=contracts_metadata)
    resolved = _resolve_probe_token_and_amount(
        chain_test_plan=chain_test_plan,
        liquidity=v4_pool_liquidity,
        pool_manager=pool_manager or _normalize_address(v4_pool_liquidity.get("poolManager")),
    )
    token = resolved["token"]
    amount = resolved["amount"]
    borrowable_liquidity = _as_dict(resolved.get("borrowableLiquidity"))
    route_hops = _plan_route_hops(chain_test_plan)
    ready = bool(
        probe.get("address")
        and token.get("address")
        and _int(amount.get("amount") or 0)
        and route_hops
        and borrowable_liquidity.get("ready") is True
    )
    blockers = []
    if not probe.get("address"):
        blockers.append("probe_address_missing")
    if not token.get("address"):
        blockers.append("token_address_missing")
    if _int(amount.get("amount") or 0) in (None, 0):
        blockers.append("normal_flash_amount_missing_or_zero")
    if not borrowable_liquidity.get("ready"):
        available_raw = _int(borrowable_liquidity.get("raw")) or 0
        requested_raw = _int(amount.get("amount")) or 0
        if available_raw <= 0:
            blockers.append("borrowable_liquidity_not_verified")
        elif requested_raw > available_raw:
            blockers.append("borrow_amount_exceeds_poolmanager_balance")
    return {
        "schemaVersion": "robinhood-normal-test-probe-config-v1",
        "network": ROBINHOOD_TESTNET_NETWORK,
        "chainId": ROBINHOOD_TESTNET_CHAIN_ID,
        "poolManager": pool_manager,
        "probe": {
            "address": probe.get("address") or "",
            "source": probe.get("source") or "",
            "deploymentFile": probe.get("deploymentFile") or "",
        },
        "token": token,
        "amount": amount,
        "borrowableLiquidity": borrowable_liquidity,
        "route": {
            "pathText": _text(chain_test_plan.get("route", {}).get("pathText")),
            "closed": bool(_as_dict(chain_test_plan.get("route")).get("closed")),
            "hopCount": len(route_hops),
        },
        "measurementReady": bool(token.get("address") and _int(amount.get("amount") or 0)),
        "readyForNormalBroadcast": ready,
        "blockingReasons": blockers,
        "sourceFiles": {
            "chainTestPlan": _text(chain_data_file_path(ROBINHOOD_TESTNET_NETWORK, "chain_test")),
            "v4PoolLiquidity": _text(chain_data_file_path(ROBINHOOD_TESTNET_NETWORK, "v4_pool_liquidity")),
            "probeDeployment": probe.get("deploymentFile") or str(_resolve_probe_deployment_file(chain_metadata=chain_metadata, contracts_metadata=contracts_metadata)),
        },
    }


__all__ = ["resolve_robinhood_testnet_normal_flash_probe"]
