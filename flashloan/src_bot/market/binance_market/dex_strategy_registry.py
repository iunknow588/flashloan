from __future__ import annotations

from dataclasses import asdict, dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class DexStrategyDefinition:
    key: str
    display_name: str
    description: str
    default_path: str
    aliases: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


_DEX_STRATEGIES = (
    DexStrategyDefinition(
        key="binance_velocity",
        display_name="Binance 速度策略",
        description="每 200ms 以币安行情涨跌幅筛选差异最大的代币池。",
        default_path="/binance-market",
        aliases=(
            "/dex-arbitrage",
        ),
    ),
    DexStrategyDefinition(
        key="closed_loop_routes",
        display_name="闭环扫描策略",
        description="直接扫描所有闭环链路，实时复核可套利机会。",
        default_path="/dex",
        aliases=(
            "/dex/overview",
            "/dex/quotes",
            "/dex/market-observation",
            "/dex/candidates",
            "/candidate-pools",
            "/dex/submissions",
            "/market-observation",
            "/robinhood-dex",
            "/robinhood-dex/candidates",
        ),
    ),
)


def _path_key(path: str | None) -> str:
    return urlsplit(str(path or "/")).path.rstrip("/") or "/"


def default_dex_strategy_key() -> str:
    return "closed_loop_routes"


def dex_strategy_definitions() -> list[dict[str, object]]:
    return [definition.as_dict() for definition in _DEX_STRATEGIES]


def dex_strategy_keys() -> list[str]:
    return [definition.key for definition in _DEX_STRATEGIES]


def dex_strategy_definition(strategy_key: str | None) -> dict[str, object] | None:
    resolved = resolve_dex_strategy_key(strategy_key)
    for definition in _DEX_STRATEGIES:
        if definition.key == resolved:
            return definition.as_dict()
    return None


def resolve_dex_strategy_key(strategy_key: str | None) -> str:
    normalized = str(strategy_key or "").strip().lower()
    if not normalized:
        return default_dex_strategy_key()
    for definition in _DEX_STRATEGIES:
        if normalized == definition.key:
            return definition.key
        if normalized == definition.default_path.strip().lower():
            return definition.key
        if normalized in {alias.strip().lower() for alias in definition.aliases}:
            return definition.key
    return default_dex_strategy_key()


def dex_strategy_key_for_path(path: str | None) -> str:
    normalized = _path_key(path)
    for definition in _DEX_STRATEGIES:
        if normalized == definition.default_path.rstrip("/"):
            return definition.key
        if normalized in {alias.rstrip("/") for alias in definition.aliases}:
            return definition.key
    if normalized.startswith("/binance-market"):
        return "binance_velocity"
    if normalized.startswith("/dex"):
        return "closed_loop_routes"
    return default_dex_strategy_key()


def dex_strategy_path_for_key(strategy_key: str | None) -> str:
    definition = dex_strategy_definition(strategy_key)
    if definition is None:
        return _DEX_STRATEGIES[0].default_path
    return str(definition.get("default_path") or _DEX_STRATEGIES[0].default_path)

