from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from core.chain_data_registry import default_market_id_for_feature, global_chain_id
from core.market_identity import (
    canonical_market_id_from_legacy,
    canonical_market_id,
    market_key_from_summary,
    normalize_feature,
    normalize_protocol_id,
)
from core.market_config import (
    MARKET_TYPE_EXECUTION_RAIL,
    MARKET_TYPE_NATIVE_CHAIN,
    MARKET_TYPE_SHADOW_CHAIN,
    supported_market_summaries,
)


class ChainSelectionError(ValueError):
    """Raised when a requested market/chain scope cannot be selected."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


def _normalise_market_id(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _normalise_feature(value: object) -> str:
    try:
        return normalize_feature(value)
    except Exception:
        return ""


def _normalise_protocol_id(value: object) -> str:
    try:
        return normalize_protocol_id(value)
    except Exception:
        return ""


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
        try:
            key = market_key_from_summary(row)
        except Exception:
            key = None
        metadata = row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else {}
        market_id = str(row.get("market_id") or "").strip().lower()
        legacy_market_id = str(row.get("legacy_market_id") or "").strip().lower()
        if key is not None:
            market_id = key.market_id
            legacy_market_id = key.legacy_market_id or legacy_market_id or market_id
            feature = key.feature
            protocol = key.protocol_id
        else:
            feature = _normalise_feature(row.get("feature") or "liquidation")
            protocol = _normalise_protocol_id(row.get("protocol") or row.get("protocol_id"))
        market_identifiers = {
            _normalise_market_id(row.get("market_id")),
            _normalise_market_id(market_id),
            _normalise_market_id(legacy_market_id),
        }
        row["market_type"] = str(
            row.get("market_type")
            or metadata.get("market_type")
            or (
                MARKET_TYPE_EXECUTION_RAIL
                if market_identifiers.intersection({"cow-dex-arbitrage", "dex-arbitrage-0-cow-protocol"})
                else MARKET_TYPE_SHADOW_CHAIN
                if market_identifiers.intersection({"robinhood-dex-shadow", "dex-arbitrage-4663-uniswap-v3"})
                else MARKET_TYPE_NATIVE_CHAIN
            )
        ).strip().lower()
        row["display_name"] = str(row.get("display_name") or market_id)
        row["market_id"] = market_id
        row["canonical_market_id"] = market_id
        row["legacy_market_id"] = legacy_market_id or None
        row["feature"] = feature
        row["protocol"] = protocol
        row["protocol_id"] = protocol
        row["enabled"] = bool(row.get("enabled", True))
        row["observation_only"] = bool(row.get("observation_only", False))
        row["protocol_supported"] = bool(
            row.get("protocol_supported", not row["observation_only"])
        )
        row["is_default"] = bool(row.get("is_default", False))
        row["chain_default"] = bool(row.get("chain_default", False))
        if row.get("chain_id") is not None:
            try:
                row["chain_id"] = int(row["chain_id"])
            except (TypeError, ValueError):
                row["chain_id"] = None
        if row.get("network") is not None:
            row["network"] = str(row.get("network") or "").strip().lower()
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
    legacy_market_id: str | None = None
    canonical_market_id: str = ""
    chain_default: bool = False

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
            legacy_market_id=(
                str(row.get("legacy_market_id") or "").strip().lower()
                or None
            ),
            canonical_market_id=str(row.get("canonical_market_id") or row.get("market_id") or ""),
            chain_default=bool(row.get("chain_default", False)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "canonical_market_id": self.canonical_market_id or self.market_id,
            "legacy_market_id": self.legacy_market_id,
            "chain_id": self.chain_id,
            "global_chain_id": self.global_chain_id,
            "chain_key": self.global_chain_id,
            "feature": self.feature,
            "display_name": self.display_name,
            "network": self.network,
            "market_type": self.market_type,
            "protocol": self.protocol,
            "protocol_id": self.protocol,
            "native_symbol": self.native_symbol,
            "observation_only": self.observation_only,
            "enabled": self.enabled,
            "is_default": self.is_default,
            "chain_default": self.chain_default,
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
    canonical_request = canonical_market_id_from_legacy(requested_market)
    normalized_request = _normalise_market_id(canonical_request or requested_market)
    candidates = {
        _normalise_market_id(selection.market_id),
        _normalise_market_id(selection.canonical_market_id or selection.market_id),
        _normalise_market_id(selection.legacy_market_id),
    }
    return normalized_request in candidates


def _find_market(selection_rows: Iterable[ChainSelection], requested_market: str) -> ChainSelection | None:
    return next((item for item in selection_rows if _market_matches(item, requested_market)), None)


def _find_triplet(
    selection_rows: Iterable[ChainSelection],
    *,
    feature: str | None,
    chain_id: int | None,
    protocol_id: str | None,
) -> ChainSelection | None:
    if not feature or chain_id is None or not protocol_id:
        return None
    return next(
        (
            item
            for item in selection_rows
            if item.feature == feature
            and item.chain_id == chain_id
            and _normalise_protocol_id(item.protocol) == protocol_id
        ),
        None,
    )


def _find_requested_scope(
    selection_rows: Iterable[ChainSelection],
    *,
    feature: str | None,
    chain_id: int | None,
    protocol_id: str | None,
) -> ChainSelection | None:
    matches = list(selection_rows)
    if feature:
        matches = [item for item in matches if item.feature == feature]
    if chain_id is not None:
        matches = [item for item in matches if item.chain_id == chain_id]
    if protocol_id:
        matches = [item for item in matches if _normalise_protocol_id(item.protocol) == protocol_id]
    if not matches:
        return None
    return (
        next((item for item in matches if chain_id is not None and item.chain_default), None)
        or next((item for item in matches if item.is_default), None)
        or matches[0]
    )


def _raise_market_scope_mismatch(
    *,
    requested_market: str,
    selected: ChainSelection,
    database_url: str | None,
    selections: Iterable[ChainSelection] | None,
) -> None:
    search_rows = list(selections) if selections is not None else available_chain_selections(
        database_url=database_url,
        feature=None,
    )
    existing = _find_market(search_rows, requested_market)
    if existing is None or existing.market_id == selected.market_id:
        return
    if existing.feature != selected.feature:
        raise ChainSelectionError(
            "feature_mismatch",
            f"market_id {existing.market_id} belongs to feature {existing.feature}, not {selected.feature}",
        )
    if existing.chain_id != selected.chain_id:
        raise ChainSelectionError(
            "chain_mismatch",
            f"market_id {existing.market_id} belongs to chain_id {existing.chain_id}, not {selected.chain_id}",
        )
    if _normalise_protocol_id(existing.protocol) != _normalise_protocol_id(selected.protocol):
        raise ChainSelectionError(
            "scope_mismatch",
            f"market_id {existing.market_id} belongs to protocol_id {existing.protocol}, not {selected.protocol}",
        )
    raise ChainSelectionError(
        "scope_mismatch",
        f"market_id {existing.market_id} does not match selected market_id {selected.market_id}",
    )


def _raise_feature_mismatch_if_market_exists(
    *,
    requested_market: str,
    requested_feature: str,
    database_url: str | None,
    selections: Iterable[ChainSelection] | None,
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

def _default_priority(selection: ChainSelection) -> tuple[int, int, str]:
    market_id = _normalise_market_id(selection.market_id)
    feature_rank = 0 if selection.feature == "liquidation" else 1
    if selection.is_default:
        return (1, feature_rank, market_id)
    return (2, feature_rank, market_id)

def resolve_chain_selection(
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
    feature: str | None = None,
    protocol_id: str | None = None,
    database_url: str | None = None,
    default_market_id: str | None = None,
    selections: Iterable[ChainSelection] | None = None,
) -> ChainSelection:
    requested_market = _normalise_market_id(market_id)
    requested_feature = _normalise_feature(feature)
    requested_protocol_id = _normalise_protocol_id(protocol_id)
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
    if requested_feature and requested_chain_id is not None and requested_protocol_id:
        selected = _find_triplet(
            available,
            feature=requested_feature,
            chain_id=requested_chain_id,
            protocol_id=requested_protocol_id,
        )
        if selected is None:
            expected_market_id = canonical_market_id(
                requested_feature,
                requested_chain_id,
                requested_protocol_id,
            )
            raise ChainSelectionError(
                "unknown_market",
                (
                    "no enabled market is registered for "
                    f"{requested_feature}/{requested_chain_id}/{requested_protocol_id} "
                    f"({expected_market_id})"
                ),
            )
        if requested_market:
            _raise_market_scope_mismatch(
                requested_market=requested_market,
                selected=selected,
                database_url=database_url,
                selections=None,
            )

    if selected is None and requested_market:
        selected = _find_market(available, requested_market)
        if selected is None:
            _raise_feature_mismatch_if_market_exists(
                requested_market=requested_market,
                requested_feature=requested_feature,
                database_url=database_url,
                selections=None,
            )
            raise ChainSelectionError("unknown_market", f"unknown market_id: {market_id}")

    if selected is None and requested_chain_id is not None:
        selected = _find_requested_scope(
            available,
            feature=requested_feature,
            chain_id=requested_chain_id,
            protocol_id=requested_protocol_id,
        )
        if selected is None:
            raise ChainSelectionError(
                "unknown_chain",
                f"no {requested_feature or 'all'} market is registered for chain_id {requested_chain_id}",
            )

    effective_default_market_id = default_market_id
    if selected is None and not effective_default_market_id and requested_feature:
        try:
            effective_default_market_id = default_market_id_for_feature(requested_feature)
        except Exception:
            effective_default_market_id = None

    if selected is None and effective_default_market_id:
        requested_default = _normalise_market_id(
            canonical_market_id_from_legacy(effective_default_market_id)
            or effective_default_market_id
        )
        selected = next(
            (item for item in available if _market_matches(item, requested_default)),
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
    if requested_protocol_id and _normalise_protocol_id(selected.protocol) != requested_protocol_id:
        raise ChainSelectionError(
            "scope_mismatch",
            f"market_id {selected.market_id} belongs to protocol_id {selected.protocol}, not {requested_protocol_id}",
        )
    return selected
