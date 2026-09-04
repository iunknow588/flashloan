from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any


FEATURE_LIQUIDATION = "liquidation"
FEATURE_DEX_ARBITRAGE = "dex_arbitrage"
SUPPORTED_FEATURES = {FEATURE_LIQUIDATION, FEATURE_DEX_ARBITRAGE}


def _slugify(value: object, *, separator: str = "-") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", separator, text)
    text = re.sub(rf"{re.escape(separator)}+", separator, text).strip(separator)
    return text


def _normalize_market_identifier(value: object) -> str:
    return _slugify(value, separator="-")


def _normalize_aliases(values: object) -> tuple[str, ...]:
    if not values:
        return ()
    if isinstance(values, (str, bytes)):
        items = [values]
    elif isinstance(values, Iterable):
        items = list(values)
    else:
        items = [values]
    result: list[str] = []
    for item in items:
        alias = _normalize_market_identifier(item)
        if alias and alias not in result:
            result.append(alias)
    return tuple(result)


@dataclass(frozen=True)
class MarketKey:
    feature: str
    chain_id: int | None
    protocol_id: str
    market_id: str
    legacy_market_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketInfo:
    key: MarketKey
    display_name: str
    network: str
    market_type: str
    native_symbol: str = ""
    observation_only: bool = True
    enabled: bool = True
    is_default: bool = False
    chain_default: bool = False
    protocol_supported: bool = False
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def market_id(self) -> str:
        return self.key.market_id

    @property
    def canonical_market_id(self) -> str:
        return self.key.market_id

    @property
    def legacy_market_id(self) -> str | None:
        return self.key.legacy_market_id

    @property
    def feature(self) -> str:
        return self.key.feature

    @property
    def chain_id(self) -> int | None:
        return self.key.chain_id

    @property
    def protocol_id(self) -> str:
        return self.key.protocol_id

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "market_id": self.market_id,
            "canonical_market_id": self.canonical_market_id,
            "legacy_market_id": self.legacy_market_id,
            "feature": self.feature,
            "chain_id": self.chain_id,
            "protocol_id": self.protocol_id,
            "protocol": self.protocol_id,
            "display_name": self.display_name,
            "network": self.network,
            "market_type": self.market_type,
            "native_symbol": self.native_symbol,
            "observation_only": self.observation_only,
            "enabled": self.enabled,
            "is_default": self.is_default,
            "chain_default": self.chain_default,
            "protocol_supported": self.protocol_supported,
            "aliases": list(self.aliases),
        }
        return payload


@dataclass(frozen=True)
class MarketRegistry:
    items: tuple[MarketInfo, ...]
    by_key: dict[MarketKey, MarketInfo]
    by_id: dict[str, MarketKey]
    alias_map: dict[str, MarketKey]

    def key_for_market_id(self, market_id: object) -> MarketKey | None:
        normalized = _normalize_market_identifier(market_id)
        if not normalized:
            return None
        return self.alias_map.get(normalized) or self.by_id.get(normalized)

    def info_for_market_id(self, market_id: object) -> MarketInfo | None:
        key = self.key_for_market_id(market_id)
        if key is None:
            return None
        return self.by_key.get(key)

    def info_for_key(self, key: MarketKey) -> MarketInfo | None:
        return self.by_key.get(key)


def normalize_feature(value: object) -> str:
    slug = _slugify(value, separator="_")
    if slug in {"bot", FEATURE_LIQUIDATION}:
        return FEATURE_LIQUIDATION
    if slug in {"dex", FEATURE_DEX_ARBITRAGE}:
        return FEATURE_DEX_ARBITRAGE
    raise ValueError(f"unknown feature: {value}")


def normalize_protocol_id(value: object) -> str:
    slug = _slugify(value, separator="_")
    if not slug:
        raise ValueError("protocol_id is required")
    return slug


def _normalize_chain_component(chain_id: int | None, network: object | None = None) -> str:
    if chain_id is not None:
        return str(int(chain_id))
    fallback = _slugify(network, separator="-")
    return fallback or "unknown"


def canonical_market_id(
    feature: str,
    chain_id: int | None,
    protocol_id: str,
    *,
    network: object | None = None,
) -> str:
    feature_slug = normalize_feature(feature).replace("_", "-")
    protocol_slug = normalize_protocol_id(protocol_id).replace("_", "-")
    chain_component = _normalize_chain_component(chain_id, network)
    return f"{feature_slug}-{chain_component}-{protocol_slug}"


def _looks_canonical_market_id(value: str) -> bool:
    return value.startswith(("liquidation-", "dex-arbitrage-")) and value.count("-") >= 2


def canonical_market_id_from_legacy(market_id: object) -> str | None:
    normalized = _normalize_market_identifier(market_id)
    if not normalized:
        return None
    aliases = legacy_market_aliases()
    if normalized in aliases:
        return aliases[normalized]
    if _looks_canonical_market_id(normalized):
        return normalized
    return None


def _coerce_row(row: Mapping[str, Any] | MarketInfo | MarketKey) -> Mapping[str, Any]:
    if isinstance(row, MarketInfo):
        return row.as_dict()
    if isinstance(row, MarketKey):
        return row.as_dict()
    if not isinstance(row, Mapping):
        raise TypeError("row must be a mapping")
    return row


def _row_feature(row: Mapping[str, Any]) -> str:
    value = row.get("feature") or row.get("workflow") or FEATURE_LIQUIDATION
    return normalize_feature(value)


def _row_protocol(row: Mapping[str, Any]) -> str:
    value = row.get("protocol_id") or row.get("protocol")
    return normalize_protocol_id(value)


def _row_chain_id(row: Mapping[str, Any]) -> int | None:
    value = row.get("chain_id")
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_network(row: Mapping[str, Any]) -> str:
    return _slugify(row.get("network"), separator="-")


def market_key_from_summary(row: Mapping[str, Any] | MarketInfo | MarketKey) -> MarketKey:
    if isinstance(row, MarketKey):
        return row
    if isinstance(row, MarketInfo):
        return row.key
    mapping = _coerce_row(row)
    feature = _row_feature(mapping)
    protocol_id = _row_protocol(mapping)
    chain_id = _row_chain_id(mapping)
    network = _row_network(mapping)
    canonical_id = canonical_market_id(feature, chain_id, protocol_id, network=network)
    market_id = str(mapping.get("canonical_market_id") or mapping.get("market_id") or canonical_id).strip().lower()
    if market_id != canonical_id:
        market_id = canonical_id
    legacy_market_id = str(mapping.get("legacy_market_id") or "").strip().lower() or None
    raw_market_id = _normalize_market_identifier(mapping.get("market_id"))
    if legacy_market_id is None and raw_market_id and raw_market_id != market_id:
        legacy_market_id = raw_market_id
    return MarketKey(
        feature=feature,
        chain_id=chain_id,
        protocol_id=protocol_id,
        market_id=market_id,
        legacy_market_id=legacy_market_id,
    )


def market_info_from_summary(row: Mapping[str, Any] | MarketInfo | MarketKey) -> MarketInfo:
    if isinstance(row, MarketInfo):
        return row
    key = market_key_from_summary(row)
    mapping = _coerce_row(row)
    metadata = mapping.get("metadata_json") if isinstance(mapping.get("metadata_json"), Mapping) else {}
    aliases = list(_normalize_aliases(mapping.get("aliases")))
    legacy_market_id = str(mapping.get("legacy_market_id") or key.legacy_market_id or "").strip().lower()
    if legacy_market_id and legacy_market_id not in aliases and legacy_market_id != key.market_id:
        aliases.append(legacy_market_id)
    raw_market_id = _normalize_market_identifier(mapping.get("market_id"))
    if raw_market_id and raw_market_id not in aliases and raw_market_id != key.market_id:
        aliases.append(raw_market_id)
    return MarketInfo(
        key=key,
        display_name=str(mapping.get("display_name") or key.market_id),
        network=str(mapping.get("network") or "").strip().lower(),
        market_type=str(mapping.get("market_type") or metadata.get("market_type") or "native_chain").strip().lower(),
        native_symbol=str(mapping.get("native_symbol") or ""),
        observation_only=bool(mapping.get("observation_only", False)),
        enabled=bool(mapping.get("enabled", True)),
        is_default=bool(mapping.get("is_default", False)),
        chain_default=bool(mapping.get("chain_default", False)),
        protocol_supported=bool(mapping.get("protocol_supported", not mapping.get("observation_only", False))),
        aliases=tuple(aliases),
    )


def market_registry_from_summaries(
    rows: Iterable[Mapping[str, Any] | MarketInfo | MarketKey],
) -> MarketRegistry:
    items: list[MarketInfo] = []
    by_key: dict[MarketKey, MarketInfo] = {}
    by_id: dict[str, MarketKey] = {}
    alias_map: dict[str, MarketKey] = {}
    triplet_index: dict[tuple[str, int | None, str], MarketKey] = {}
    for row in rows:
        info = market_info_from_summary(row)
        items.append(info)
        key = info.key
        triplet = (key.feature, key.chain_id, key.protocol_id)
        previous_triplet_key = triplet_index.get(triplet)
        if previous_triplet_key is not None and previous_triplet_key != key:
            raise ValueError(
                f"duplicate market key: {key.feature}/{key.chain_id}/{key.protocol_id}"
            )
        triplet_index[triplet] = key
        if key in by_key and by_key[key].market_id != info.market_id:
            raise ValueError(f"duplicate canonical market id: {key.market_id}")
        by_key[key] = info
        canonical_id = _normalize_market_identifier(info.market_id)
        if canonical_id:
            previous = by_id.get(canonical_id)
            if previous is not None and previous != key:
                raise ValueError(
                    f"duplicate canonical market id: {canonical_id} resolves to multiple market keys"
                )
            by_id[canonical_id] = key
            alias_map[canonical_id] = key
        all_aliases = [key.legacy_market_id, *info.aliases]
        for alias in all_aliases:
            normalized_alias = _normalize_market_identifier(alias)
            if not normalized_alias or normalized_alias == canonical_id:
                continue
            previous = alias_map.get(normalized_alias)
            if previous is not None and previous != key:
                raise ValueError(
                    f"alias {normalized_alias} resolves to multiple market keys"
                )
            alias_map[normalized_alias] = key
    return MarketRegistry(
        items=tuple(items),
        by_key=by_key,
        by_id=by_id,
        alias_map=alias_map,
    )


def legacy_market_aliases() -> dict[str, str]:
    try:
        from core.market_config import KNOWN_EVM_MARKETS
    except Exception:
        return {}
    registry = market_registry_from_summaries(
        {"market_id": legacy_market_id, **dict(profile)}
        for legacy_market_id, profile in KNOWN_EVM_MARKETS.items()
    )
    aliases = {
        alias: registry.by_key[key].market_id
        for alias, key in registry.alias_map.items()
        if alias and key in registry.by_key
    }
    aliases.setdefault(
        "hyperliquid-native",
        canonical_market_id(
            FEATURE_DEX_ARBITRAGE,
            None,
            "hyperliquid_native",
            network="hyperliquid",
        ),
    )
    return aliases


def _default_market_key_by_priority(
    registry: MarketRegistry,
    *,
    feature: str | None,
    chain_id: int | None,
    protocol_id: str | None,
) -> MarketKey | None:
    matching = [
        info
        for info in registry.items
        if (feature is None or info.feature == feature)
        and (chain_id is None or info.chain_id == chain_id)
        and (protocol_id is None or info.protocol_id == protocol_id)
    ]
    if not matching and feature is not None:
        matching = [info for info in registry.items if info.feature == feature]
    if not matching and chain_id is not None:
        matching = [info for info in registry.items if info.chain_id == chain_id]
    if not matching:
        matching = list(registry.items)
    if not matching:
        return None
    preferred = (
        next((info for info in matching if chain_id is not None and info.chain_default), None)
        or next((info for info in matching if info.is_default), None)
        or matching[0]
    )
    return preferred.key


def resolve_market_key(
    *,
    feature: str | None = None,
    chain_id: int | None = None,
    protocol_id: str | None = None,
    market_id: str | None = None,
    summaries: list[Mapping[str, Any]] | None = None,
    network: str | None = None,
) -> MarketKey:
    resolved_feature = normalize_feature(feature) if feature is not None else None
    resolved_protocol_id = (
        normalize_protocol_id(protocol_id)
        if protocol_id is not None and str(protocol_id).strip()
        else None
    )
    resolved_chain_id = None if chain_id is None else int(chain_id)
    market_rows = list(summaries or [])
    if not market_rows:
        try:
            from core.market_config import supported_market_summaries

            market_rows = list(supported_market_summaries())
        except Exception:
            market_rows = []
    registry = market_registry_from_summaries(market_rows) if market_rows else MarketRegistry(
        items=(),
        by_key={},
        by_id={},
        alias_map={},
    )
    normalized_market_id = _normalize_market_identifier(market_id)
    canonical_from_triplet = None
    if resolved_feature is not None and resolved_protocol_id is not None:
        canonical_from_triplet = canonical_market_id(
            resolved_feature,
            resolved_chain_id,
            resolved_protocol_id,
            network=network,
        )
    if normalized_market_id:
        candidate = registry.key_for_market_id(normalized_market_id)
        if candidate is not None:
            if resolved_feature is not None and candidate.feature != resolved_feature:
                raise ValueError(
                    f"feature_mismatch: market_id {candidate.market_id} belongs to feature {candidate.feature}, not {resolved_feature}"
                )
            if resolved_chain_id is not None and candidate.chain_id != resolved_chain_id:
                raise ValueError(
                    f"chain_mismatch: market_id {candidate.market_id} belongs to chain_id {candidate.chain_id}, not {resolved_chain_id}"
                )
            if resolved_protocol_id is not None and candidate.protocol_id != resolved_protocol_id:
                raise ValueError(
                    f"scope_mismatch: market_id {candidate.market_id} belongs to protocol_id {candidate.protocol_id}, not {resolved_protocol_id}"
                )
            if canonical_from_triplet and candidate.market_id != canonical_from_triplet:
                raise ValueError(
                    f"scope_mismatch: market_id {candidate.market_id} resolves to {canonical_from_triplet}"
                )
            return candidate
        if canonical_from_triplet:
            return MarketKey(
                feature=resolved_feature or FEATURE_LIQUIDATION,
                chain_id=resolved_chain_id,
                protocol_id=resolved_protocol_id or normalize_protocol_id("unknown"),
                market_id=canonical_from_triplet,
                legacy_market_id=normalized_market_id,
            )
        if _looks_canonical_market_id(normalized_market_id):
            return MarketKey(
                feature=resolved_feature or FEATURE_LIQUIDATION,
                chain_id=resolved_chain_id,
                protocol_id=resolved_protocol_id or normalize_protocol_id("unknown"),
                market_id=normalized_market_id,
                legacy_market_id=None,
            )
        return MarketKey(
            feature=resolved_feature or FEATURE_LIQUIDATION,
            chain_id=resolved_chain_id,
            protocol_id=resolved_protocol_id or normalize_protocol_id("unknown"),
            market_id=normalized_market_id,
            legacy_market_id=normalized_market_id,
        )

    if canonical_from_triplet:
        existing = registry.key_for_market_id(canonical_from_triplet)
        if existing is not None:
            return existing
        return MarketKey(
            feature=resolved_feature or FEATURE_LIQUIDATION,
            chain_id=resolved_chain_id,
            protocol_id=resolved_protocol_id or normalize_protocol_id("unknown"),
            market_id=canonical_from_triplet,
            legacy_market_id=None,
        )

    default_key = _default_market_key_by_priority(
        registry,
        feature=resolved_feature,
        chain_id=resolved_chain_id,
        protocol_id=resolved_protocol_id,
    )
    if default_key is not None:
        return default_key

    raise ValueError("no market identity rows are available")


def validate_market_identity_registry(rows: Iterable[Mapping[str, Any] | MarketInfo | MarketKey]) -> None:
    market_registry_from_summaries(rows)


__all__ = [
    "FEATURE_DEX_ARBITRAGE",
    "FEATURE_LIQUIDATION",
    "MarketInfo",
    "MarketKey",
    "MarketRegistry",
    "SUPPORTED_FEATURES",
    "canonical_market_id",
    "canonical_market_id_from_legacy",
    "legacy_market_aliases",
    "market_info_from_summary",
    "market_key_from_summary",
    "market_registry_from_summaries",
    "normalize_feature",
    "normalize_protocol_id",
    "resolve_market_key",
    "validate_market_identity_registry",
]
