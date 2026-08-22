from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _value(query: Mapping[str, object] | None, name: str, default: object = "") -> str:
    if query is None:
        return str(default)
    value = query.get(name, default)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else default
    return str(value if value is not None else default).strip()


def parse_bool(value: object, *, default: bool = False) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return bool(default)
    return text in TRUE_VALUES


def parse_int(
    value: object,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = int(default)
    if minimum is not None:
        parsed = max(int(minimum), parsed)
    if maximum is not None:
        parsed = min(int(maximum), parsed)
    return parsed


def optional_int(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class MarketRequest:
    market_id: str | None = None
    chain_id: int | None = None

    def as_kwargs(self) -> dict[str, object]:
        result: dict[str, object] = {}
        if self.market_id:
            result["market_id"] = self.market_id
        if self.chain_id is not None:
            result["chain_id"] = self.chain_id
        return result


@dataclass(frozen=True)
class ScanPagination:
    page_size: int = 20
    risk_page: int = 1
    high_page: int = 1
    core_page: int = 1

    def as_kwargs(self) -> dict[str, int]:
        return {
            "page_size": self.page_size,
            "risk_page": self.risk_page,
            "high_page": self.high_page,
            "core_page": self.core_page,
        }


@dataclass(frozen=True)
class ScanRequest:
    force: bool
    pagination: ScanPagination
    market: MarketRequest
    pagination_requested: bool

    def as_kwargs(self) -> dict[str, object]:
        return {
            "force": self.force,
            **self.pagination.as_kwargs(),
            **self.market.as_kwargs(),
        }


def parse_market_request(query: Mapping[str, object] | None) -> MarketRequest:
    market_id = _value(query, "market_id") or None
    return MarketRequest(market_id=market_id, chain_id=optional_int(_value(query, "chain_id")))


def parse_scan_request(
    query: Mapping[str, object] | None,
    body: Mapping[str, object] | None = None,
    *,
    default_force: bool = False,
) -> ScanRequest:
    body = body or {}
    force = parse_bool(_value(query, "force"), default=default_force) or parse_bool(body.get("force"))
    pagination_names = ("page_size", "risk_page", "high_page", "core_page", "market_id", "chain_id")
    pagination_requested = any(_value(query, name) != "" for name in pagination_names)
    pagination = ScanPagination(
        page_size=parse_int(_value(query, "page_size", 20), 20, minimum=1, maximum=100),
        risk_page=parse_int(_value(query, "risk_page", 1), 1, minimum=1),
        high_page=parse_int(_value(query, "high_page", 1), 1, minimum=1),
        core_page=parse_int(_value(query, "core_page", 1), 1, minimum=1),
    )
    return ScanRequest(
        force=force,
        pagination=pagination,
        market=parse_market_request(query),
        pagination_requested=pagination_requested,
    )
