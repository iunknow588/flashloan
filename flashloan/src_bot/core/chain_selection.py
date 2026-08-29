from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from core.chain_data_registry import default_market_id_for_feature, global_chain_id
from core.market_config import (
    MARKET_TYPE_EXECUTION_RAIL,
    MARKET_TYPE_NATIVE_CHAIN,
    MARKET_TYPE_SHADOW_CHAIN,
    market_namespace,
    supported_market_summaries,
)


class ChainSelectionError(ValueError):
    """Raised when a requested market/chain scope cannot be selected."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


def _normalise_market_id(value: object) -> str:
    return str(value or "").strip().lower()


def _normalise_feature(value: object) -> str:
    return str(value or "").strip().lower()


def _selection_global_chain_id(row: dict[str, Any]) -> str:
    if row.get("global_chain_id"):
        return str(row["global_chain_id"])
    if row.get("chain_key"):
        return str(row["chain_key"])
    return global_chain_id(
        chain_id=row.get("chain_id") if isinstance(row.get("chain_id"), int) else None,
        network=str(row.get("network") or ""),
        market_id=str(row.get("market_id") or ""),
    )


def _normalise_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalised: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        row["market_id"] = str(row.get("market_id") or "").strip()
        row["feature"] = _normalise_feature(row.get("feature") or "liquidation")
        row["network"] = str(row.get("network") or "").strip().lower()
        row["protocol"] = str(row.get("protocol") or "").strip().lower()
        metadata = row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else {}
        row["market_type"] = str(
            row.get("market_type")
            or metadata.get("market_type")
            or (
                MARKET_TYPE_EXECUTION_RAIL
                if row["market_id"] == "cow-dex-arbitrage"
                else MARKET_TYPE_SHADOW_CHAIN
                if row["market_id"] == "robinhood-dex-shadow"
                else MARKET_TYPE_NATIVE_CHAIN
            )
        ).strip().lower()
        row["display_name"] = str(row.get("display_name") or row["market_id"])
        row["enabled"] = bool(row.get("enabled", True))
        row["observation_only"] = bool(row.get("observation_only", False))
        row["protocol_supported"] = bool(
            row.get("protocol_supported", not row["observation_only"])
        )
        row["is_default"] = bool(row.get("is_default", False))
        if row.get("chain_id") is not None:
            try:
                row["chain_id"] = int(row["chain_id"])
            except (TypeError, ValueError):
                row["chain_id"] = None
        row["global_chain_id"] = _selection_global_chain_id(row)
        row["chain_key"] = row["global_chain_id"]
        normalised.append(row)
    return normalised


def _capabilities(row: dict[str, Any]) -> dict[str, bool]:
    market_id = _normalise_market_id(row.get("market_id"))
    feature = _normalise_feature(row.get("feature"))
    protocol_supported = bool(row.get("protocol_supported", False))
    observation_only = bool(row.get("observation_only", False))
    enabled = bool(row.get("enabled", True))
    return {
        "liquidation": feature == "liquidation",
        "dex_arbitrage": feature == "dex_arbitrage",
        "execution": enabled and protocol_supported and not observation_only,
        "observation": enabled,
        "shadow": observation_only,
        "robinhood_shadow": row.get("network") == "robinhood" and observation_only,
    }


@dataclass(frozen=True)
class ChainSelection:
    market_id: str
    chain_id: int | None
    global_chain_id: str
    feature: str
    display_name: str
    network: str
    market_type: str
    protocol: str
    native_symbol: str
    observation_only: bool
    enabled: bool
    is_default: bool
    protocol_supported: bool
    capabilities: dict[str, bool]

    @classmethod
    def from_summary(cls, row: dict[str, Any]) -> "ChainSelection":
        return cls(
            market_id=str(row.get("market_id") or ""),
            chain_id=row.get("chain_id"),
            global_chain_id=_selection_global_chain_id(row),
            feature=str(row.get("feature") or "liquidation"),
            display_name=str(row.get("display_name") or row.get("market_id") or ""),
            network=str(row.get("network") or ""),
            market_type=str(row.get("market_type") or MARKET_TYPE_NATIVE_CHAIN),
            protocol=str(row.get("protocol") or ""),
            native_symbol=str(row.get("native_symbol") or ""),
            observation_only=bool(row.get("observation_only", False)),
            enabled=bool(row.get("enabled", True)),
            is_default=bool(row.get("is_default", False)),
            protocol_supported=bool(row.get("protocol_supported", False)),
            capabilities=_capabilities(row),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "chain_id": self.chain_id,
            "global_chain_id": self.global_chain_id,
            "chain_key": self.global_chain_id,
            "feature": self.feature,
            "display_name": self.display_name,
            "network": self.network,
            "market_type": self.market_type,
            "protocol": self.protocol,
            "native_symbol": self.native_symbol,
            "observation_only": self.observation_only,
            "enabled": self.enabled,
            "is_default": self.is_default,
            "protocol_supported": self.protocol_supported,
            "capabilities": dict(self.capabilities),
        }


def available_chain_selections(
    *,
    database_url: str | None = None,
    feature: str | None = None,
) -> list[ChainSelection]:
    requested_feature = _normalise_feature(feature)
    rows = _normalise_rows(supported_market_summaries(database_url))
    if requested_feature:
        rows = [row for row in rows if row["feature"] == requested_feature]
    return [
        ChainSelection.from_summary(row)
        for row in rows
        if row["market_id"] and row["enabled"]
    ]


def _market_matches(selection: ChainSelection, requested_market: str) -> bool:
    return (
        _normalise_market_id(selection.market_id) == requested_market
        or _normalise_market_id(market_namespace(selection.market_id)) == requested_market
    )


def _find_market(selection_rows: Iterable[ChainSelection], requested_market: str) -> ChainSelection | None:
    return next((item for item in selection_rows if _market_matches(item, requested_market)), None)


def _raise_feature_mismatch_if_market_exists(
    *,
    requested_market: str,
    requested_feature: str,
    database_url: str | None,
    selections: Iterable[ChainSelection] | None,
    original_market_id: str | None,
 ) -> None:
    if not requested_market or not requested_feature:
        return
    search_rows = list(selections) if selections is not None else available_chain_selections(
        database_url=database_url,
        feature=None,
    )
    existing = _find_market(search_rows, requested_market)
    if existing is not None and existing.feature != requested_feature:
        raise ChainSelectionError(
            "feature_mismatch",
            f"market_id {existing.market_id} belongs to feature {existing.feature}, not {requested_feature}",
        )

def _default_priority(selection: ChainSelection) -> tuple[int, str]:
    market_id = _normalise_market_id(selection.market_id)
    if selection.is_default:
        return (1, market_id)
    return (2, market_id)

def resolve_chain_selection(
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
    feature: str | None = None,
    database_url: str | None = None,
    default_market_id: str | None = None,
    selections: Iterable[ChainSelection] | None = None,
) -> ChainSelection:
    requested_market = _normalise_market_id(market_id)
    requested_feature = _normalise_feature(feature)
    requested_chain_id: int | None = None
    if chain_id is not None:
        try:
            requested_chain_id = int(chain_id)
        except (TypeError, ValueError):
            raise ChainSelectionError("invalid_chain_id", f"invalid chain_id: {chain_id}") from None
    available = list(selections) if selections is not None else available_chain_selections(
        database_url=database_url,
        feature=requested_feature,
    )
    if not available:
        raise ChainSelectionError(
            "no_markets",
            f"no enabled market is registered for feature {requested_feature or 'all'}",
        )

    selected: ChainSelection | None = None
    if requested_market:
        selected = _find_market(available, requested_market)
        if selected is None:
            _raise_feature_mismatch_if_market_exists(
                requested_market=requested_market,
                requested_feature=requested_feature,
                database_url=database_url,
                selections=None if selections is None else available,
                original_market_id=market_id,
            )
            raise ChainSelectionError("unknown_market", f"unknown market_id: {market_id}")

    if selected is None and requested_chain_id is not None:
        chain_matches = [item for item in available if item.chain_id == requested_chain_id]
        if not chain_matches:
            raise ChainSelectionError(
                "unknown_chain",
                f"no {requested_feature or 'all'} market is registered for chain_id {requested_chain_id}",
            )
        selected = next((item for item in chain_matches if item.is_default), chain_matches[0])

    effective_default_market_id = default_market_id
    if selected is None and not effective_default_market_id and requested_feature:
        try:
            effective_default_market_id = default_market_id_for_feature(requested_feature)
        except Exception:
            effective_default_market_id = None

    if selected is None and effective_default_market_id:
        requested_default = _normalise_market_id(effective_default_market_id)
        selected = next(
            (item for item in available if _normalise_market_id(item.market_id) == requested_default),
            None,
        )

    if selected is None:
        selected = sorted(available, key=_default_priority)[0]

    if requested_chain_id is not None and selected.chain_id != requested_chain_id:
        raise ChainSelectionError(
            "chain_mismatch",
            f"market_id {selected.market_id} belongs to chain_id {selected.chain_id}, not {requested_chain_id}",
        )
    if requested_feature and selected.feature != requested_feature:
        raise ChainSelectionError(
            "feature_mismatch",
            f"market_id {selected.market_id} belongs to feature {selected.feature}, not {requested_feature}",
        )
    return selected
