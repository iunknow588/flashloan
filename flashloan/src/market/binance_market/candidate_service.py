"""Pure candidate-pair assembly for the Binance/CoW market workflow."""

from __future__ import annotations

from typing import Any, Callable


def build_pair_candidates(
    top_rows: list[dict[str, Any]],
    bottom_rows: list[dict[str, Any]],
    *,
    min_spread_percent: float,
    network: str | None,
    pool_filter: dict[str, Any] | None,
    route_support: Callable[[str, str, str | None, dict[str, Any] | None], dict[str, Any]],
    pair_builder: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build ranked pair candidates and retain structured pool rejections."""

    pairs: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    minimum = max(0.0, float(min_spread_percent))
    for x in top_rows:
        for y in bottom_rows:
            if x.get("symbol") == y.get("symbol"):
                continue
            spread = (
                float(x["change_percent"]) - float(y["change_percent"])
                if x.get("change_percent") is not None and y.get("change_percent") is not None
                else None
            )
            if spread is None or spread <= minimum:
                continue
            support = route_support(
                str(x.get("base_symbol") or ""),
                str(y.get("base_symbol") or ""),
                network,
                pool_filter,
            ) if network else {"supported": True}
            if network and not support.get("supported"):
                rejected.append(
                    {
                        "pair": f"{x.get('base_symbol')}:{y.get('base_symbol')}",
                        "missingPairs": support.get("missingPairs") or [],
                        "reason": support.get("reason"),
                    }
                )
                continue
            row = pair_builder(x, y)
            if network:
                row["pool_assembly_supported"] = True
                row["pool_assembly"] = support
            row["grid_rank"] = len(pairs) + 1
            row["rank"] = row["grid_rank"]
            pairs.append(row)
    return pairs, rejected
